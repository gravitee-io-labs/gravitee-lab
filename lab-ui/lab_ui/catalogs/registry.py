"""The catalogs this machine has: what the person asked for (`.lab/catalogs.json`, the intent), what is installed
(`.lab/catalogs.lock`, the result, and the manifest inside each git install), their namespaces, and the units each one
vouches for. Every other module of the package reads and writes these files through this one.

Reads are tolerant and never raise: a file that is missing, damaged or from a newer Lab reads as empty and the reason is
kept for the Catalogs page (`config_problem`), never a crash. The only read that tells absence from damage is
`read_config`, which answers None for a file that does not exist (a first run) and an empty list for one it cannot
read, so that a damaged file is never mistaken for a first run. Writes are atomic (`paths.write_atomic`, mode 0600), and
a file the Lab cannot read is kept aside (`<name>.bad-<utc>`) before it is replaced, never overwritten.

Every function reads `paths.ROOT` at call time and keeps no path of its own. Two locks serialize the writers: `LOCK` for
the threads of this process and an advisory lock on `.lab/catalogs.flock` for the other processes, the command line and
a second Lab. `flock()` takes both, LOCK first, so that whoever holds the advisory lock holds LOCK as well and no two
threads can wait for each other. The order is always LOCK, then the advisory lock, then the project lock. A reader takes
neither: it reads whole files and tolerates a pair that is half written. What a read accepts, a write can store back.

A git install is trusted only through its manifest: a unit is a unit of the catalog only if
`.installed.json` lists it, and the manifest is valid only for the commit the lock pins, or for the one an operation
under way names."""

import contextlib
import copy
import json
import math
import os
import re
import shutil
import stat
import threading
import time
import unicodedata
from pathlib import Path
from typing import Iterable, Optional

try:
    import fcntl
except ImportError:   # no advisory locks on this system: the lock of this process is all there is
    fcntl = None

from .. import paths

LOCK = threading.RLock()

SCHEMA = 1
LOCAL = paths.LOCAL
KINDS = paths.KINDS
CONFIG_FILE = f"{paths.LAB_DIR}/catalogs.json"
LOCK_FILE = f"{paths.LAB_DIR}/catalogs.lock"
FLOCK_FILE = f"{paths.LAB_DIR}/catalogs.flock"
STORE_DIR = f"{paths.LAB_DIR}/cache/git"
CATALOG_FILE = "catalog.yml"

# Seconds a writer waits for the lock of another process before the change is `busy`
FLOCK_WAIT = 20.0
# What is read at most: the two small files of the Lab, a manifest (a catalog may hold 10000 files), the catalog.yml of
# a catalog (shown, so a bound is enough), and the optional suggestions file
FILE_LIMIT, MANIFEST_LIMIT, SUMMARY_LIMIT, SUGGESTIONS_LIMIT = 1 << 20, 32 << 20, 256 << 10, 256 << 10
# A catalog list longer than this is not a person's: the rest is ignored
ENTRIES_LIMIT, SUGGESTIONS_MAX = 200, 50
# A file changed this recently is read again whatever its stamp says: two writes within one tick of the clock can leave
# the same modification time, size and inode number
RACY = 2.0
# How deep a JSON document of the Lab may nest: the formats of are 5 deep, and a newer Lab's keys are kept
DEPTH_LIMIT = 64
# A temporary file of a write that was killed is removed after this long
LEFTOVER_AGE = 86400.0

# The keys of an entry of catalogs.json that this Lab reads; any other is a newer Lab's and is kept as it is
ENTRY_KEYS = ("id", "kind", "url", "path", "branch", "auto", "host", "added", "shadows")

_NS = re.compile(paths.NS)
_NAME = re.compile(paths.NAME)
_SHA = re.compile(r"[0-9a-f]{40}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")

# The state of a catalog that its last failure names (the keys of), by the code of the catalogue of errors
FAILURES = {
    "git_missing": "needs_git", "git_old": "needs_git",
    "no_access": "noaccess", "not_found": "noaccess", "ssh_denied": "noaccess", "host_key": "noaccess",
    "unreachable": "unreachable", "timeout": "unreachable", "certificate": "unreachable",
    "wrong_catalog": "wrong_catalog", "history_changed": "rewritten",
    "lab_new": "lab_new", "no_readable_version": "lab_new", "lab_old": "lab_old", "damaged": "damaged",
}

LISTING_PROBLEM = ("The file that lists your catalogs could not be read ({why}), so none is shown. Fix it, or add a "
                   "catalog to start a new list: the old file is kept.")
ENTRIES_PROBLEM = "{count} of the catalogs in the file that lists them could not be read ({why}) and {are} not shown."
RECORD_PROBLEM = ("The file that records installed catalogs could not be read ({why}), so every catalog shows as not "
                  "downloaded. Check them again to download them.")


class CatalogError(Exception):
    """A refusal with a code (the catalogue of errors), the line for the person, the HTTP status it answers with and the facts behind
    it. `detail` is for the log and the dialog's "Show output", never part of the line."""

    def __init__(self, code, message, status=400, **detail):
        super().__init__(message)
        self.code, self.message, self.status, self.detail = code, message, status, detail


# ---------------------------------------------------------------- locks

# The advisory locks this thread holds, by file, with a depth: flock(2) would block a thread on a second descriptor of
# a file it already holds
_held = threading.local()


def busy() -> CatalogError:
    """`busy` (the catalogue of errors): a change of catalogs is under way in another thread or process. Raised by the locks, and by a
    caller that finds the change it wanted to make is not the only one."""
    return CatalogError("busy", "Another catalog change is running. Try again in a moment.", 409)


def _acquire(path, wait):
    """The descriptor that holds the advisory lock of `path`, taken within `wait` seconds (the change is `busy` after);
    None where there is nothing to lock with (no fcntl, a file that cannot be made, a file system that cannot lock)."""
    if fcntl is None:
        return None
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        descriptor = os.open(path, flags, 0o600)
    except OSError:
        return None
    deadline = time.monotonic() + wait
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held, descriptor = descriptor, None
                return held
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise busy() from None
                time.sleep(0.05)
            except OSError:
                return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


@contextlib.contextmanager
def flock(wait: Optional[float] = None):
    """Holds LOCK and then the advisory lock on `.lab/catalogs.flock` (the other processes of this machine are kept
    out), for up to `wait` seconds of waiting for both (FLOCK_WAIT when not given, 0 to try once), else the change is
    `busy`. LOCK is taken here, first: a thread that holds the advisory lock holds LOCK too, so it can go on to call a
    writer while another thread of this process waits for the lock, and neither waits for the other. A thread that
    holds it takes it again at no cost, so a function may take it whether its caller did or not (`with LOCK, flock()`
    works as well). Without fcntl, and where the lock file cannot be made, LOCK is all there is."""
    path = str(Path(paths.ROOT) / FLOCK_FILE)
    held = _held.__dict__.setdefault("held", {})
    if path in held:
        held[path] += 1
        try:
            yield
        finally:
            held[path] -= 1
        return
    deadline = time.monotonic() + (FLOCK_WAIT if wait is None else wait)
    if not LOCK.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise busy()
    try:
        descriptor = _acquire(path, max(0.0, deadline - time.monotonic()))
        held[path] = 1
        try:
            yield
        finally:
            del held[path]
            if descriptor is not None:
                os.close(descriptor)   # closing the descriptor releases the lock
    finally:
        LOCK.release()


# ---------------------------------------------------------------- reading files

_MISSING = object()   # a file that is not there: not a damaged one


def _root(*parts):
    return os.path.join(paths.ROOT, *parts)


def _read(path, limit, follow=True):
    """(content, None), (None, _MISSING) for a file that does not exist, or (None, why) for one that cannot be used: not
    a regular file, over `limit`, unreadable. Never blocks on a pipe. `follow` False refuses a link as the file."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    if not follow:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except (FileNotFoundError, NotADirectoryError):
        return None, _MISSING
    except OSError:
        return None, "it cannot be opened"
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return None, "it is not a regular file"
        if info.st_size > limit:
            return None, f"it is larger than {limit >> 10} KB"
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            data = source.read(limit + 1)
    except OSError:
        return None, "it cannot be read"
    finally:
        os.close(descriptor)
    return (data, None) if len(data) <= limit else (None, f"it is larger than {limit >> 10} KB")


def _constant(name):
    raise ValueError(name)


def _integer(digits):
    if len(digits) > 18:   # parsing a number of a million digits takes seconds, and no file of the Lab holds one
        raise ValueError("a number too long to be one")
    return int(digits)


def _number(digits):
    value = float(digits)
    if not math.isfinite(value):   # 1e999 is a number to the parser and no number to a writer
        raise ValueError("a number out of range")
    return value


def _nested(value, limit=DEPTH_LIMIT):
    """Whether the parsed JSON `value` nests deeper than `limit` levels of list or mapping. Walked with a stack of its
    own, so that no depth can exhaust the one of the interpreter (a copy or a dump of such a value would)."""
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if not isinstance(item, (dict, list)):
            continue
        if depth > limit:
            return True
        pending.extend((child, depth + 1) for child in (item.values() if isinstance(item, dict) else item)
                       if isinstance(child, (dict, list)))
    return False


def _json(data):
    """(value, None) or (None, why) for the bytes of a JSON file: strict JSON (no NaN, no Infinity), numbers of
    reasonable length and in range, nested at most DEPTH_LIMIT deep."""
    try:
        value = json.loads(data.decode("utf-8-sig"), parse_constant=_constant, parse_int=_integer, parse_float=_number)
    except UnicodeDecodeError:
        return None, "it is not text"
    except RecursionError:
        return None, "it is nested too deeply"
    except ValueError:
        return None, "it is not valid JSON"
    return (None, "it is nested too deeply") if _nested(value) else (value, None)


_cache = {}   # path -> (stamp, value), the parsed form of the files that are big or read often
_cache_lock = threading.Lock()


def _cached(path, load, limit, follow=False):
    """`load(bytes)` of a regular file, kept until the file changes: a manifest is read by every state, and parsing one
    would cost more than the budget of the fingerprint. None when the file is missing or `load` refuses it. A file
    modified within RACY seconds is read again each time."""
    try:
        info = os.lstat(path) if not follow else os.stat(path)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    stamp = (info.st_mtime_ns, info.st_size, info.st_ino, info.st_dev)
    with _cache_lock:
        found = _cache.get(path)
    if found and found[0] == stamp and time.time() - info.st_mtime > RACY:
        return found[1]
    data, why = _read(path, limit, follow)
    value = load(data) if why is None else None
    with _cache_lock:
        if len(_cache) > 256:
            _cache.clear()
        _cache[path] = (stamp, value)
    return value


def shown(text, limit):
    """An author's text for a person: normalized, control, format and surrogate characters out, white space collapsed,
    cut. Public: the other modules of the package show an author's text the same way."""
    text = unicodedata.normalize("NFKC", str(text))
    text = "".join(" " if unicodedata.category(c) in ("Cc", "Zl", "Zp") else c
                   for c in text if unicodedata.category(c) not in ("Cf", "Cs"))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


_shown = shown   # the name the first modules of the package import


def _plain(text, limit=4096):
    """Whether `text` is a text a path or an address can be: not empty, not long, no control, format or surrogate
    character (a lone surrogate is no text: no file stores it as UTF-8)."""
    return (isinstance(text, str) and 0 < len(text) <= limit
            and not any(unicodedata.category(c) in ("Cc", "Cf", "Cs", "Zl", "Zp") for c in text))


def _address(text):
    """Whether `text` can be the address of a repository: plain, and without a space of any kind in it."""
    return _plain(text) and not any(c.isspace() for c in text)


def _namespace(ns):
    return isinstance(ns, str) and _NS.fullmatch(ns) is not None


def _clean_url(url):
    """The address without the user name (and password) of an https address, and without the password of an ssh one: an
    address is credential-free by construction, and one that is not is never used or shown as it is. The user
    information is everything between the scheme and the first slash, up to the last @ there: a space in it does not
    keep it."""
    found = re.match(r"(?i)https?://([^/]*)@", url)
    if found:
        return url[:found.start(1)] + url[found.end():]
    found = re.match(r"(?i)ssh://([^/@:]*):[^/]*@", url)
    return url[:found.start(1)] + found.group(1) + "@" + url[found.end():] if found else url


# ---------------------------------------------------------------- the default configuration (another module's)

def _defaults():
    """The module that reads the tracked default file. Imported when it is first needed: it reads this one."""
    from . import defaults
    return defaults


def _tracked():
    """({id: row} in the order of the tracked file, the set of the listed ids). Tolerant: a default module that is not
    there or fails lists nothing, except the first entry's id that `paths` reads itself."""
    rows = {}
    try:
        for row in _defaults().read():
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                rows.setdefault(row["id"], row)
    except Exception:  # noqa: BLE001 - the default file is another module's: whatever it fails with, nothing is listed
        rows = {}
    first = paths.default_ns()
    ids = set(rows) | ({first} if first else set())
    return rows, ids


def _effective(entry):
    """(url, branch) a git entry is read at: the tracked ones for a listed entry, its own for any other."""
    if entry.get("kind") == "git":
        try:
            url, branch = _defaults().effective(entry)
            if isinstance(url, str) and url:
                return url, branch if isinstance(branch, str) and branch else None
        except Exception:  # noqa: BLE001 - the default module is another module's: the stored address is the fallback
            pass
    return entry.get("url"), entry.get("branch")


# ---------------------------------------------------------------- catalogs.json

def _entry(raw, listed):
    """(entry, None, cleaned) for an entry of catalogs.json read as the Lab uses it, or (None, why, False) for one that
    cannot be: not an object, no valid id, a reserved or personal id (reserved ones are for the listed catalogs only),
    no address. `listed` is a function that answers the set of listed ids, called only when it matters. `cleaned` says a
    user name was taken out of the address. Unknown keys are kept: a newer Lab's keys survive a round trip."""
    if not isinstance(raw, dict):
        return None, "an entry is not an object", False
    ns = raw.get("id")
    if not _namespace(ns):
        return None, "an entry has no valid id", False
    if ns == LOCAL or (ns in paths.RESERVED_NS and ns not in listed()):
        return None, f"{ns} is a reserved name", False
    kind = raw.get("kind")
    if kind not in ("git", "folder"):
        kind = "folder" if "path" in raw and "url" not in raw else "git" if "url" in raw else None
    if kind is None:
        return None, f"{ns} has no address", False
    entry, cleaned = {"id": ns, "kind": kind}, False
    if kind == "git":
        url = _clean_url(raw["url"]) if _plain(raw.get("url")) else None   # cleaned first: the secret is named as one
        if not _address(url):
            return None, f"{ns} has no valid address", False
        entry["url"] = url
        cleaned = url != raw["url"]
    else:
        if not (_plain(raw.get("path")) and os.path.isabs(raw["path"])):
            return None, f"{ns} has no valid folder", False
        entry["path"] = raw["path"]
    if isinstance(raw.get("branch"), str) and 0 < len(raw["branch"]) <= 200 and _plain(raw["branch"], 200):
        entry["branch"] = raw["branch"]
    if isinstance(raw.get("auto"), bool):
        entry["auto"] = raw["auto"]
    if raw.get("host") is True and ns not in listed():   # a listed catalog's allowance is implied, and dropped
        entry["host"] = True
    if isinstance(raw.get("added"), str) and _plain(raw["added"], 40):
        entry["added"] = raw["added"]
    shadows = raw.get("shadows")
    if kind == "folder" and isinstance(shadows, dict) and _plain(shadows.get("url")) \
            and _address(_clean_url(shadows["url"])):
        entry["shadows"] = {"url": _clean_url(shadows["url"])}
        cleaned = cleaned or entry["shadows"]["url"] != shadows["url"]
        for key in ("branch", "added"):
            if _plain(shadows.get(key), 200):
                entry["shadows"][key] = shadows[key]
    entry.update((key, value) for key, value in raw.items() if key not in ENTRY_KEYS)
    return entry, None, cleaned


def _lazy(function):
    """`function` called once, when first asked."""
    box = []

    def asked():
        if not box:
            box.append(function())
        return box[0]
    return asked


def _load_config():
    """(config, problems): the parsed catalogs.json and the lines that say what could not be read of it. The config is
    None for a file that does not exist; an empty list for one that cannot be read at all."""
    blank = {"schema": SCHEMA, "catalogs": []}
    data, why = _read(_root(CONFIG_FILE), FILE_LIMIT)
    if why is _MISSING:
        return None, []
    if why is None:
        value, why = _json(data)
    if why is None and not isinstance(value, dict):
        why = "it is not a JSON object"
    if why is None:
        schema = value.get("schema", SCHEMA)
        if type(schema) is not int or schema < 1:
            why = "it does not say which format it is"
        elif schema > SCHEMA:
            why = "its format is newer than this Lab reads"
        elif not isinstance(value.get("catalogs"), list):
            why = "it has no list of catalogs"
    if why is not None:
        return blank, [LISTING_PROBLEM.format(why=why)]
    config = {"schema": SCHEMA, **{key: item for key, item in value.items() if key not in ("schema", "catalogs")},
              "catalogs": []}
    listed = _lazy(lambda: _tracked()[1])
    seen, dropped = set(), []
    for number, raw in enumerate(value["catalogs"]):
        entry, reason, _ = _entry(raw, listed) if number < ENTRIES_LIMIT else (None, "there are too many", False)
        if entry is not None and entry["id"] in seen:
            entry, reason = None, f"{entry['id']} is in the list twice"
        if entry is None:
            dropped.append(reason)
        else:
            seen.add(entry["id"])
            config["catalogs"].append(entry)
    problems = [ENTRIES_PROBLEM.format(count=len(dropped), why=dropped[0], are="is" if len(dropped) == 1 else "are")] \
        if dropped else []
    return config, problems


def read_config() -> Optional[dict]:
    """.lab/catalogs.json as {"schema": 1, "catalogs": [entries]}, entries in the order of the file, which is the order
    among the catalogs the person added. None only when the file does not exist, which is a first run. A
    file that cannot be read reads as an empty list and sets `config_problem()`, so that it is never taken for a first
    run; one entry that cannot be read is left out and said there too. A new copy each time."""
    return _load_config()[0]


def _load_lock():
    """(lock, problems) of catalogs.lock: {"schema": 1, "catalogs": {ns: entry}}, empty when it does not exist or cannot
    be read."""
    blank = {"schema": SCHEMA, "catalogs": {}}
    data, why = _read(_root(LOCK_FILE), FILE_LIMIT)
    if why is _MISSING:
        return blank, []
    if why is None:
        value, why = _json(data)
    if why is None and not (isinstance(value, dict) and isinstance(value.get("catalogs", {}), dict)):
        why = "it is not a record of catalogs"
    if why is None and type(value.get("schema", SCHEMA)) is int and value.get("schema", SCHEMA) > SCHEMA:
        why = "its format is newer than this Lab reads"
    if why is not None:
        return blank, [RECORD_PROBLEM.format(why=why)]
    lock = {"schema": SCHEMA, **{key: item for key, item in value.items() if key not in ("schema", "catalogs")},
            "catalogs": {}}
    dropped = []
    for ns, raw in list(value.get("catalogs", {}).items())[:ENTRIES_LIMIT]:
        if _namespace(ns) and isinstance(raw, dict):
            lock["catalogs"][ns] = _lock_entry(raw, strict=False)
        else:
            dropped.append(ns)
    problems = [RECORD_PROBLEM.format(why="a record in it is not one")] if dropped else []
    return lock, problems


def config_problem() -> Optional[str]:
    """Why catalogs.json or catalogs.lock could not be read, as a line for the Catalogs page, else None. The lines of
    both files if both are damaged. Read from the files at each call, so it is gone as soon as the file is fixed."""
    lines = _load_config()[1] + _load_lock()[1]
    return " ".join(lines) or None


def _dump(document):
    """The text of a document of the Lab: indented JSON, in UTF-8 as it is, or with every character escaped when it
    holds a lone surrogate that a newer Lab's key kept (UTF-8 has no bytes for it). A ValueError for what no read would
    accept back: nested deeper than DEPTH_LIMIT, a number out of range."""
    if _nested(document):
        raise ValueError(f"nested deeper than {DEPTH_LIMIT} levels")
    text = json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = json.dumps(document, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    return text


def _set_aside(file):
    """Keeps the file `file` as `<name>.bad-<utc>` (with a number if that name is taken) before a write replaces it. A
    regular file is linked, not moved (copied where links are not allowed): until the write succeeds the file is still
    where it was, so a write that fails never turns a damaged file into a missing one, which would read as a first run.
    Anything else (a folder, a link, a pipe) is moved. The person's file is never overwritten: when it cannot be kept,
    the write fails."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    number = 0
    while True:
        target = file.with_name(f"{file.name}.bad-{stamp}" + (f"-{number}" if number else ""))
        if not os.path.lexists(target):
            break
        number += 1
    try:
        regular = stat.S_ISREG(os.lstat(file).st_mode)
    except FileNotFoundError:
        return None
    if not regular:
        os.rename(file, target)
        return target
    try:
        os.link(file, target)
    except OSError:
        shutil.copyfile(file, target)
    return target


_LEFTOVER = re.compile(r"\.catalogs\.(?:json|lock)\.[a-z0-9_]{1,16}\.tmp")


def _sweep(folder):
    """Removes from `folder` the temporary files of a write that was killed half way (`paths.write_atomic` makes
    `.catalogs.json.<random>.tmp` and `.catalogs.lock.<random>.tmp` next to the file and renames it): the regular files
    of those names that are older than LEFTOVER_AGE. Called by the writers under the locks, so none is a live
    write's."""
    try:
        with os.scandir(folder) as listing:
            found = [one for one in listing if _LEFTOVER.fullmatch(one.name)]
    except OSError:
        return
    for one in found:
        try:
            info = one.stat(follow_symlinks=False)
            if stat.S_ISREG(info.st_mode) and time.time() - info.st_mtime > LEFTOVER_AGE:
                os.unlink(one.path)
        except OSError:
            pass


def _write(relative, text, problems):
    """`text` written to a file of .lab replaced whole with mode 0600, after a file that has `problems` (it cannot be
    read: a person's file, or a newer Lab's) is kept aside. The leftovers of a write that was killed are swept."""
    file = Path(paths.ROOT) / relative
    file.parent.mkdir(parents=True, exist_ok=True)
    if problems:
        _set_aside(file)
    paths.write_atomic(file, text, mode=0o600)
    _sweep(file.parent)


def _checked_config(config):
    """The document write_config writes, or a ValueError for one that would not read back as it is given: a programmer's
    error, never a person's typing (CatalogError bad_address_secret for an address with a user name in it)."""
    if not isinstance(config, dict) or not isinstance(config.get("catalogs"), list):
        raise ValueError('a catalog configuration is {"schema": 1, "catalogs": [...]}')
    if len(config["catalogs"]) > ENTRIES_LIMIT:
        raise ValueError(f"at most {ENTRIES_LIMIT} catalogs")
    listed = _lazy(lambda: _tracked()[1])
    document = {"schema": SCHEMA, **{key: item for key, item in config.items() if key not in ("schema", "catalogs")},
                "catalogs": []}
    for raw in config["catalogs"]:
        entry, why, cleaned = _entry(raw, listed)
        if entry is None:
            raise ValueError(why)
        if cleaned:
            from . import gitx   # here and not above: it reads this module
            raise gitx.fail("bad_address_secret")
        if any(one["id"] == entry["id"] for one in document["catalogs"]):
            raise ValueError(f"{entry['id']} is in the list twice")
        document["catalogs"].append(entry)
    return document


def write_config(config: dict) -> None:
    """Writes .lab/catalogs.json: replaced whole, mode 0600. A file that cannot be read (or has an entry that cannot) is
    first kept as `catalogs.json.bad-<utc>`, never overwritten, so a hand edit that went wrong is kept for the person.
    Refuses (ValueError) what would not read back as given: an entry with no valid id, a reserved id that is not listed,
    a repeated id; and an address with a user name (bad_address_secret). The allowance of a listed catalog is not
    written (it is implied). Reading the file again is the caller's, under LOCK and flock, as any change of catalogs."""
    text = _dump(_checked_config(config))
    with flock():
        _write(CONFIG_FILE, text, _load_config()[1])


def entries() -> list:
    """The entries of catalogs.json, in the order of the file (the order the catalogs were added, which is the order of
    precedence among them); empty for no file or one that cannot be read."""
    return (_load_config()[0] or {"catalogs": []})["catalogs"]


def entry(ns: str) -> Optional[dict]:
    """The entry of catalog `ns`, or None."""
    return next((one for one in entries() if one["id"] == ns), None)


def is_git(ns: str) -> bool:
    """Whether catalog `ns` is a git catalog of this machine: its entry says kind git. For `paths`, which lists a
    catalog by its folder: a git catalog is never listed that way, whatever its folder holds, and its units are those
    of `units_of`, which are none without a valid manifest (a Damaged catalog, a half installed one)."""
    one = entry(ns) if _namespace(ns) else None
    return one is not None and one["kind"] == "git"


def precedence() -> list:
    """The namespaces in the order they claim names: the default catalog if the person has it, `local` (the
    personal area, always), then the other catalogs in the order they were added."""
    ids = [one["id"] for one in entries()]
    first = paths.default_ns()
    return ([first] if first in ids else []) + [LOCAL] + [ns for ns in ids if ns != first]


# ---------------------------------------------------------------- catalogs.lock

_SHAS = ("pin", "previous", "tip")
_ORDER = ("pin", "previous", "tip", "roots", "audience", "name", "version", "lab", "installed", "checked", "error",
          "pending", "via", "shadowed")


def _blank():
    """A lock entry before anything was downloaded."""
    return {"pin": None, "previous": None, "tip": None, "roots": [], "audience": "private", "name": None,
            "version": None, "lab": None, "installed": None, "checked": None, "error": None, "pending": None}


def _pending(value):
    """Whether `value` is the operation a lock entry says is under way: {op: add, update or undo, from: a commit or
    None, to: a commit}."""
    return (isinstance(value, dict) and value.get("op") in ("add", "update", "undo")
            and (value.get("from") is None or (isinstance(value["from"], str) and _SHA.fullmatch(value["from"])))
            and isinstance(value.get("to"), str) and _SHA.fullmatch(value["to"]))


def _lock_entry(raw, strict):
    """A lock entry with every field of of the type it has. A field that is not (a file edited by hand, a
    newer Lab's) reads as its blank when `strict` is False, and is a ValueError when it is True (a write). Keys this Lab
    does not know are kept."""
    found = _blank()

    def refuse(field, why):
        if strict:
            raise ValueError(f"lock field {field}: {why}")

    for field in _SHAS:
        value = raw.get(field)
        if value is None or (isinstance(value, str) and _SHA.fullmatch(value)):
            found[field] = value
        else:
            refuse(field, "a commit id of 40 hexadecimal digits or null")
    roots = raw.get("roots")
    if isinstance(roots, list) and len(roots) <= 20 \
            and all(isinstance(one, str) and _SHA.fullmatch(one) for one in roots):
        found["roots"] = list(roots)
    elif roots is not None:
        refuse("roots", "a list of commit ids")
    audience = raw.get("audience")
    if audience in ("public", "private"):
        found["audience"] = audience
    elif audience is not None:
        refuse("audience", "public or private")
    for field, pattern, limit in (("version", _VERSION, 20), ("installed", _STAMP, 40), ("checked", _STAMP, 40),
                                  ("error", _CODE, 64)):
        value = raw.get(field)
        if value is None or (isinstance(value, str) and len(value) <= limit and pattern.fullmatch(value)):
            found[field] = value
        else:
            refuse(field, "text of the form it has")
    name = raw.get("name")
    if isinstance(name, str) and name.strip():
        found["name"] = shown(name, 40)
    elif name is not None:
        refuse("name", "text")
    lab = raw.get("lab")
    if type(lab) is int and lab >= 0:
        found["lab"] = lab
    elif lab is not None:
        refuse("lab", "a whole number")
    pending = raw.get("pending")
    if _pending(pending):
        found["pending"] = {"op": pending["op"], "from": pending.get("from"), "to": pending["to"]}
    elif pending is not None:
        refuse("pending", "{op, from, to}")
    if raw.get("via") in ("https", "ssh"):
        found["via"] = raw["via"]
    elif raw.get("via") is not None:
        refuse("via", "https or ssh")
    if isinstance(raw.get("shadowed"), bool):
        found["shadowed"] = raw["shadowed"]
    elif raw.get("shadowed") is not None:
        refuse("shadowed", "true or false")
    ordered = {field: found[field] for field in _ORDER if field in found}
    ordered.update((key, value) for key, value in raw.items() if key not in ordered and key not in _ORDER)
    return ordered


def read_lock() -> dict:
    """.lab/catalogs.lock as {"schema": 1, "catalogs": {ns: entry}}, every entry with all the fields of (a
    field that cannot be read is its blank). Empty when the file does not exist, and empty with `config_problem()`
    saying why when it cannot be read."""
    return _load_lock()[0]


def _checked_lock(lock):
    if not isinstance(lock, dict) or not isinstance(lock.get("catalogs"), dict):
        raise ValueError('a lock is {"schema": 1, "catalogs": {namespace: entry}}')
    document = {"schema": SCHEMA, **{key: item for key, item in lock.items() if key not in ("schema", "catalogs")},
                "catalogs": {}}
    for ns, raw in lock["catalogs"].items():
        if not _namespace(ns) or not isinstance(raw, dict):
            raise ValueError(f"not a namespace with a record: {ns!r}")
        document["catalogs"][ns] = _lock_entry(raw, strict=True)
    return document


def write_lock(lock: dict) -> None:
    """Writes .lab/catalogs.lock: replaced whole, mode 0600; a file that cannot be read is first set aside as
    `catalogs.lock.bad-<utc>`. A field of the wrong type is a ValueError: the Lab writes only what it reads."""
    text = _dump(_checked_lock(lock))
    with flock():
        _write(LOCK_FILE, text, _load_lock()[1])


def lock_of(ns: str) -> dict:
    """The lock entry of catalog `ns`, a new copy; the blank entry (no pin, private) for one that has none."""
    return read_lock()["catalogs"].get(ns) or _blank()


def set_lock(ns: str, **fields) -> dict:
    """Sets fields of the lock entry of `ns` (made if it has none) and writes the file: read, change and write under
    LOCK and flock, so that two changes never lose one another, in this process or in another. A field set to None is
    its blank (`via` and `shadowed`: removed). Answers the new entry."""
    if not _namespace(ns):
        raise ValueError(f"not a namespace: {ns!r}")
    with flock():
        lock, problems = _load_lock()
        current = lock["catalogs"].get(ns) or _blank()
        merged = {**current, **fields}
        for optional in ("via", "shadowed"):
            if merged.get(optional) is None:
                merged.pop(optional, None)
        lock["catalogs"][ns] = _lock_entry(merged, strict=True)
        _write(LOCK_FILE, _dump(_checked_lock(lock)), problems)
        return copy.deepcopy(lock["catalogs"][ns])


def drop_lock(ns: str) -> None:
    """Removes the lock entry of `ns`; nothing to do when it has none."""
    with flock():
        lock, problems = _load_lock()
        if ns in lock["catalogs"]:
            del lock["catalogs"][ns]
            _write(LOCK_FILE, _dump(_checked_lock(lock)), problems)


# ---------------------------------------------------------------- places

def install_dir(ns) -> Path:
    """Where catalog `ns` is: .lab/catalogs/<ns>, a real directory for a git catalog, a link for a folder one."""
    if not _namespace(ns):
        raise ValueError(f"not a namespace: {ns!r}")
    return Path(paths.ROOT) / paths.CATALOGS_DIR / ns


def stage_dir(ns) -> Path:
    """Where a candidate is judged before it is put in place: .lab/catalogs/.stage-<ns>. A name that starts with a dot
    is never a namespace, so a staged unit is never a unit. For an Add, the placeholder `new-<ticket>` stands for the
    namespace the catalog will have."""
    if not _namespace(ns):
        raise ValueError(f"not a namespace: {ns!r}")
    return Path(paths.ROOT) / paths.CATALOGS_DIR / f".stage-{ns}"


def store_dir(ns) -> Path:
    """The bare git store of catalog `ns`: .lab/cache/git/<ns>.git."""
    if not _namespace(ns):
        raise ValueError(f"not a namespace: {ns!r}")
    return Path(paths.ROOT) / STORE_DIR / f"{ns}.git"


def new_store_dir(ticket: str) -> Path:
    """The store of a catalog being added, before its name is known: .lab/cache/git/.new-<ticket>.git."""
    if not (isinstance(ticket, str) and re.fullmatch(r"[0-9a-f]{1,32}", ticket)):
        raise ValueError(f"not a ticket: {ticket!r}")
    return Path(paths.ROOT) / STORE_DIR / f".new-{ticket}.git"


def check_namespace(ns: str, taken: Iterable[str] = (), listed: Iterable[str] = ()) -> None:
    """Accepts the namespace a catalog declares, or raises the CatalogError that says why not:
    `catalog_invalid` when it is not 2 to 24 lowercase letters, digits and dashes starting with a letter;
    `name_reserved` for a reserved word (`RESERVED_NS`), which only an id in `listed` may be: the caller passes the ids
    the tracked file lists when this catalog is at the listed address; `name_taken` when another catalog has it
    (`taken`)."""
    if not _namespace(ns):
        raise CatalogError("catalog_invalid",
                           "catalog.yml: id must be 2 to 24 lowercase letters, digits and dashes.", 400)
    if ns == LOCAL or (ns in paths.RESERVED_NS and ns not in set(listed)):
        raise CatalogError("name_reserved",
                           f"{ns} is a reserved name. Ask the owner of this catalog to choose another.", 400)
    if ns in set(taken):
        other = entry(ns)
        where = ""
        if other:
            where = display_address(_effective(other)[0] if other["kind"] == "git" else other["path"])
        raise CatalogError("name_taken", f"Another catalog already uses the name {ns}{f' ({where})' if where else ''}. "
                           "Remove it first, or ask the owner of this one to rename it.", 409, id=ns)


# ---------------------------------------------------------------- the manifest and the units

def _load_manifest(ns):
    """The loader of a manifest for `_cached`: the parsed document when it has the shape of and names this
    catalog, else None."""
    def load(data):
        value, why = _json(data)
        if why is not None or not isinstance(value, dict):
            return None
        schema = value.get("schema", SCHEMA)
        if type(schema) is not int or schema != SCHEMA or value.get("catalog") != ns:
            return None
        if not (isinstance(value.get("commit"), str) and _SHA.fullmatch(value["commit"])):
            return None
        if not (isinstance(value.get("files"), dict) and isinstance(value.get("units"), dict)):
            return None
        ignored = value.get("ignored", 0)
        if type(ignored) is not int or ignored < 0:
            return None
        return value
    return load


def _installed(ns):
    """The manifest of the install of `ns` as it is cached (read only), whatever commit it is for: it names this
    catalog, sits in a real directory (a link would lead anywhere) and is a regular file of the shape of.
    None otherwise."""
    if not _namespace(ns):
        return None
    base = _root(paths.CATALOGS_DIR, ns)
    if os.path.islink(base) or not os.path.isdir(base):
        return None
    return _cached(os.path.join(base, paths.MANIFEST), _load_manifest(ns), MANIFEST_LIMIT)


def _manifest(ns, held):
    """_installed, when it is valid for the lock entry `held`: its commit is the pin, or the one an operation under way
    names. None otherwise."""
    found = _installed(ns)
    if found is None:
        return None
    pending = held.get("pending")
    commit = found["commit"]
    return found if commit == held.get("pin") or (isinstance(pending, dict) and pending.get("to") == commit) else None


def read_manifest(ns: str) -> Optional[dict]:
    """.installed.json of the install `ns` for whatever commit it was written for, a new copy, or None when the install
    has none the Lab can use (see `_installed`). For the code that finishes or repairs an operation and must tell a
    manifest that is of the next commit from one that is of no commit the lock knows; a reader of the units uses
    `manifest`, which is valid only for the pin."""
    found = _installed(ns)
    return copy.deepcopy(found) if found is not None else None


def manifest(ns: str) -> Optional[dict]:
    """.installed.json of the git install `ns`, a new copy, when it is valid: the document of that names
    this catalog, in a real directory, whose `commit` is the pin of the lock (or, while the operation that is installing
    it is not finished, the commit that operation names: a reader never sees a catalog flash as damaged between two
    writes). None otherwise: that is a Damaged catalog, not an error."""
    found = _manifest(ns, read_lock()["catalogs"].get(ns) or {})
    return copy.deepcopy(found) if found is not None else None


def _listing(ns):
    return {kind: paths.names(ns, kind) for kind in KINDS}


def _units(ns, one, held):
    """{kind: [names]} the catalog vouches for, given its entry `one` and lock entry `held`."""
    found = {kind: [] for kind in KINDS}
    if ns == LOCAL:
        return _listing(LOCAL)
    if one is None:
        return found
    if one["kind"] == "folder":
        return _listing(ns)
    found_manifest = _manifest(ns, held)
    for key in found_manifest["units"] if found_manifest else ():
        kind, _, name = key.partition(":")
        if kind in found and _NAME.fullmatch(name):
            found[kind].append(name)
    return {kind: sorted(names) for kind, names in found.items()}


def units_of(ns: str) -> dict:
    """{kind: [names]} the catalog `ns` vouches for, sorted, with the three kinds always there. A git catalog only from
    a valid manifest (manifest()): never a file that is in its folder and not in the manifest, and nothing at all
    without one (a catalog that is Damaged, half installed or not downloaded vouches for nothing). A folder catalog, and
    the personal area `local`, by listing the folders. A namespace that is not a catalog of this machine: nothing."""
    if ns != LOCAL and not _namespace(ns):
        return {kind: [] for kind in KINDS}
    one = entry(ns) if ns != LOCAL else None
    return _units(ns, one, read_lock()["catalogs"].get(ns) or {})


def units(kind: Optional[str] = None) -> list:
    """The ids of the units of every installed catalog, of one kind or of all (kind by kind): bare for the default
    catalog's, which is the only form of them that is an id, `@ns/name` for the others. The catalogs in the order
    of precedence, each one's names sorted. The personal area is not a catalog and is not listed."""
    if kind is not None and kind not in KINDS:
        raise ValueError(f"not a kind of unit: {kind!r}")
    default = paths.default_ns()
    vouched = [(ns, _units(ns, one, held)) for ns, one, held in _catalogs() if ns != LOCAL]
    return [("" if ns == default else f"@{ns}/") + name
            for wanted in (KINDS if kind is None else (kind,)) for ns, found in vouched for name in found[wanted]]


# ---------------------------------------------------------------- catalog.yml, read for what it says of itself

def _uncomment(line):
    """The line without its comment: a # that starts it or follows a space, outside quotes."""
    quote, escaped = None, False
    for at, char in enumerate(line):
        if escaped:
            escaped = False
        elif quote:
            if char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                quote = None
        elif char in "\"'" and (at == 0 or line[at - 1] in " \t[{,:"):
            quote = char
        elif char == "#" and (at == 0 or line[at - 1] in " \t"):
            return line[:at]
    return line


def _scalar(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            return str(json.loads(text))
        except ValueError:
            return text[1:-1]
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text


def _outside(text, char):
    """The places of `char` in `text` that are outside quotes and brackets."""
    quote, depth, found = None, 0, []
    for at, one in enumerate(text):
        if quote:
            quote = None if one == quote else quote
        elif one in "\"'":
            quote = one
        elif one in "[{":
            depth += 1
        elif one in "]}":
            depth -= 1
        elif one == char and depth == 0:
            found.append(at)
    return found


def _closed(text):
    """Whether the flow `text`, which starts with a bracket, ends where that bracket closes (outside quotes)."""
    quote, depth = None, 0
    for at, one in enumerate(text):
        if quote:
            quote = None if one == quote else quote
        elif one in "\"'":
            quote = one
        elif one in "[{":
            depth += 1
        elif one in "]}":
            depth -= 1
            if depth <= 0:
                return depth == 0 and at == len(text) - 1
    return False


def _item(text, depth, lost):
    """A value inside a flow: another flow while there is room for one, else a scalar."""
    text = text.strip()
    if depth < 8 and len(text) >= 2 and (text[0], text[-1]) in (("[", "]"), ("{", "}")):
        return _flow(text, depth + 1, lost)
    return _scalar(text)


def _flow(text, depth=0, lost=None):
    """A one-line YAML flow list or mapping: [a, b] or {key: value, key: "value"}, its values scalars or flows of the
    same kind. A part of a mapping that is no `key: value` is left out and noted in the list `lost`."""
    lost = [] if lost is None else lost
    inner = text[1:-1]
    cuts = [-1] + _outside(inner, ",") + [len(inner)]
    parts = [inner[a + 1:b] for a, b in zip(cuts, cuts[1:]) if inner[a + 1:b].strip()]
    if text[0] == "[":
        return [_item(part, depth, lost) for part in parts]
    result = {}
    for part in parts:
        colon = next((at for at in _outside(part, ":") if part[at + 1:at + 2] in ("", " ", "\t")), None)
        if colon is None:
            lost.append(part.strip()[:20])
        else:
            result[_scalar(part[:colon])] = _item(part[colon + 1:], depth, lost)
    return result


# A key of the block: plain, or in quotes, of the characters a key of the Lab has
_NAME_OF_KEY = r"[A-Za-z0-9_][A-Za-z0-9_.-]*"
_KEY = re.compile(rf"(?:({_NAME_OF_KEY})|\"({_NAME_OF_KEY})\"|'({_NAME_OF_KEY})'):(?:[ \t]+(.*))?")
_TOP = re.compile(r"(?:x-gravitee-catalog|\"x-gravitee-catalog\"|'x-gravitee-catalog'):(?:[ \t]+(.*))?")
# What this Lab reads of the block; a key of any other name that the scan cannot read is nothing to it
_WANTED = ("id", "name", "description", "version", "lab", "requires", "host_access")
# A number in a spelling that YAML knows and the scan does not read (0x2, +2, 1_0, 2.0, 2e0)
_NUMBER = re.compile(r"[+-]?(?:0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+"
                     r"|[0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][+-]?[0-9]+)?)")


def _block(lines, at, indent, skipped, trail=()):
    """({key: value}, next line) of the block of mapping keys at `indent` of `lines`, [(indent, text)]: a value is a
    plain or quoted scalar, a one-line flow list or mapping, or the block below the key. What it cannot read it leaves
    out, and appends to `skipped` the path of the key it belongs to: a list in block form, a scalar on several lines, an
    anchor, an alias, a tag, a line that is no key (path `?`)."""
    result, last = {}, None
    while at < len(lines):
        here, content = lines[at]
        if here < indent:
            break
        at += 1
        found = _KEY.fullmatch(content) if here == indent else None
        if not found:
            if here == indent:   # a list item level with its key (YAML allows it), or a line this does not know
                skipped.append(trail + ((last if content.startswith("- ") and last else "?"),))
            continue
        name, value = next(one for one in found.groups()[:3] if one), (found.group(4) or "").strip()
        last, below = name, False
        if not value and at < len(lines) and lines[at][0] > indent and not lines[at][1].startswith("- ") \
                and len(trail) < 8:
            result[name], at = _block(lines, at, lines[at][0], skipped, trail + (name,))
            continue
        while at < len(lines) and lines[at][0] > indent:
            at, below = at + 1, True
        lost = []
        if below:   # a list in block form below the key, or a scalar that goes on over several lines
            skipped.append(trail + (name,))
        elif value[:1] in ("{", "[") and _closed(value):
            result[name] = _flow(value, 0, lost)
            if lost:
                skipped.append(trail + (name,))
        elif value and value[0] in "|>&*!{[":
            skipped.append(trail + (name,))
        elif value:
            result[name] = _scalar(value)
    return result, at


def _git_url(text):
    """The credential-free address of a git repository that `text` names, as the Lab spells it, else None: an address
    the Add dialog would take as a git repository (not a folder, not another scheme)."""
    if not _plain(text):
        return None
    from . import gitx   # here and not above: it reads this module
    try:
        address = gitx.parse_address(_clean_url(text))
    except CatalogError:
        return None
    return address.url if address.kind == "git" else None


def scan(data):
    """What the x-gravitee-catalog block of a catalog.yml says of the catalog, for display and for dependencies: {id,
    name, description, version, lab, host_access (only when true), requires_catalogs: {ns: {name, url, min}}}, a requirement's url being the address
    of a git repository or None. The block is the one shows (a mapping, two spaces deep, or one line of
    flow); this is not a YAML parser, it reads that shape and nothing else, and what it does not understand it leaves
    out.
    `partial` is True, and there only then, when something it could not read may have been a key the Lab reads (an
    anchor, a scalar on several lines, a number spelled 0x2, a block it cannot find): the answer is then not the
    file's, and the strict reading of a catalog (manifest.read_catalog, at Add) is. A file with no block, or one that
    is not YAML, is {}."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return {}
    every = text.splitlines()
    lines = []
    for line in every[:4000]:
        stripped = _uncomment(line).rstrip()
        if stripped.strip():
            content = stripped.lstrip(" \t")
            lead = stripped[:len(stripped) - len(content)]
            if "\t" in lead:
                return {}   # a tab in an indent is not YAML
            lines.append((len(lead), content))
    start = next((at for at, (indent, content) in enumerate(lines) if indent == 0 and _TOP.fullmatch(content)), None)
    if start is None:
        return {"partial": True} if len(every) > 4000 else {}
    skipped, lost, partial = [], [], False
    inline = (_TOP.fullmatch(lines[start][1]).group(1) or "").strip()
    if inline:   # the block on the line of its key: a flow mapping, else something this does not read
        if not (inline.startswith("{") and _closed(inline)):
            return {"partial": True}
        block = _flow(inline, 0, lost)
        partial = bool(lost)
    else:
        end = next((at for at in range(start + 1, len(lines)) if lines[at][0] == 0), len(lines))
        inside = lines[start + 1:end]
        if not inside or inside[0][0] == 0:
            return {}
        block, stop = _block(inside, 0, inside[0][0], skipped)
        partial = stop < len(inside) or any(path[0] in _WANTED or path[0] == "?" for path in skipped)
    found = {}
    if isinstance(block.get("id"), str) and _namespace(block["id"]):
        found["id"] = block["id"]
    if isinstance(block.get("name"), str) and shown(block["name"], 40):
        found["name"] = shown(block["name"], 40)
    if isinstance(block.get("description"), str):
        found["description"] = shown(block["description"], 90)
    if isinstance(block.get("version"), str) and _VERSION.fullmatch(block["version"]):
        found["version"] = block["version"]
    if isinstance(block.get("lab"), str) and re.fullmatch(r"[0-9]{1,6}", block["lab"]):
        found["lab"] = int(block["lab"])
    elif isinstance(block.get("lab"), str) and _NUMBER.fullmatch(block["lab"]):
        partial = True
    if block.get("host_access") == "true":
        found["host_access"] = True
    needs = {}
    requires = block.get("requires")
    declared = requires.get("catalogs") if isinstance(requires, dict) else None
    for ns, want in (declared.items() if isinstance(declared, dict) else ()):
        if _namespace(ns) and isinstance(want, dict):
            floor = want.get("min")
            needs[ns] = {"name": shown(want["name"], 40) if isinstance(want.get("name"), str) else None,
                         "url": _git_url(want.get("url")),
                         "min": floor if isinstance(floor, str) and _VERSION.fullmatch(floor) else None}
    found["requires_catalogs"] = needs
    if partial:
        found["partial"] = True
    return found


_scan = scan   # the name the first modules of the package import


def _summary(ns):
    """_scan of the catalog.yml of the install of `ns` (read only, cached); {} when it cannot be read. The file must be
    a regular file: a link in its place leads anywhere."""
    return _cached(_root(paths.CATALOGS_DIR, ns, CATALOG_FILE), scan, SUMMARY_LIMIT) or {}


# ---------------------------------------------------------------- addresses

def display_address(url_or_path: str) -> str:
    """The address as a person reads it, the same for every spelling of one repository: `github.com/acme/lab-hub` for
    https://user@github.com/acme/lab-hub.git and git@github.com:acme/lab-hub.git; the host in lowercase, the port kept,
    the scheme, the user name and a final `.git` or slash taken off. A folder under the home folder is `~/code/x`. A
    text that is none of these is cleaned of its control characters and cut. Never raises, never holds a credential."""
    if not isinstance(url_or_path, str):
        return ""
    text = "".join(c for c in url_or_path.strip()
                   if unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Zl", "Zp"))[:4096]
    if not text:
        return ""
    if text in (".", "..", "~") or text.startswith(("/", "~", "./", "../")):
        return _tilde(text.rstrip("/") or "/")
    scheme = re.fullmatch(r"([A-Za-z][A-Za-z0-9+.-]*)://([^/]*)(/.*)?", text, re.S)
    if scheme:
        host = scheme.group(2).rpartition("@")[2].lower()
        body = _bare_repo(scheme.group(3) or "")
        return f"/{body}" if scheme.group(1).lower() == "file" else f"{host}/{body}".rstrip("/")
    scp = re.fullmatch(r"[^@:/\s]+@([^:/@\s]+):(.+)", text, re.S)
    if scp:
        return f"{scp.group(1).lower()}/{_bare_repo(scp.group(2))}"
    short = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9._-]+)", text)
    if short:
        return f"github.com/{short.group(1)}/{_bare_repo(short.group(2))}"
    return re.sub(r"://[^/@\s]*@", "://", text)[:200]


def _bare_repo(path):
    path = path.strip("/")
    return path[:-4].rstrip("/") if path.lower().endswith(".git") and len(path) > 4 else path


def _tilde(path):
    """A folder with the home folder as ~."""
    for base in (os.path.expanduser("~"), os.path.realpath(os.path.expanduser("~"))):
        base = base.rstrip(os.sep)
        if base and (path == base or path.startswith(base + os.sep)):
            return "~" + path[len(base):]
    return path


def web_url(entry: dict) -> Optional[str]:
    """The https address of the repository a git entry stands for, to open it in a browser: the listed address for a
    listed catalog, without user name, port kept, `.git` and final slash off. None for every other entry: ssh, a folder,
    and any address that is not https."""
    if not isinstance(entry, dict) or entry.get("kind", "git") != "git":
        return None
    url = _effective(entry)[0] if isinstance(entry.get("id"), str) else entry.get("url")
    found = re.fullmatch(r"(?i)https://(?:[^/@\s]*@)?([A-Za-z0-9.-]+(?::[0-9]{1,5})?|\[[0-9A-Fa-f:]+\](?::[0-9]{1,5})?)"
                         r"/([A-Za-z0-9._~+/-]+)", url) if isinstance(url, str) else None
    if not found:
        return None
    path = _bare_repo(found.group(2))
    if not path or any(part in ("", ".", "..") for part in path.split("/")):
        return None
    return f"https://{found.group(1).lower()}/{path}"


# ---------------------------------------------------------------- suggestions

def suggestions_file() -> Optional[str]:
    """Where the optional suggestions file is: $LAB_CATALOGS_FILE, else
    $XDG_CONFIG_HOME/gravitee-lab/catalogs.json, else ~/.config/gravitee-lab/catalogs.json. It is outside the repository
    and never written by the Lab."""
    named = os.environ.get("LAB_CATALOGS_FILE")
    if named:
        return os.path.abspath(os.path.expanduser(named))
    config = os.environ.get("XDG_CONFIG_HOME")
    base = config if config and os.path.isabs(config) else os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "gravitee-lab", "catalogs.json")


def _suggested(raw):
    """A row of the suggestions file as the Lab shows it, or None for one that is not an address the Lab could add."""
    if not isinstance(raw, dict) or not isinstance(raw.get("url"), str):
        return None
    from . import gitx   # here and not above: it reads this module
    try:
        address = gitx.parse_address(raw["url"])
        branch = gitx.asked_branch(raw.get("branch")) if raw.get("branch") is not None else None
    except CatalogError:
        return None
    if address.kind != "git":
        return None
    name = shown(raw["name"], 40) if isinstance(raw.get("name"), str) and shown(raw["name"], 40) else address.display
    return {"id": None, "kind": "suggested", "name": name,
            "description": shown(raw["description"], 90) if isinstance(raw.get("description"), str) else "",
            "url": address.url, "branch": branch, "host_access": raw.get("host_access") is True, "listed": False}


def suggestions() -> list:
    """The "Not added" rows: first the listed catalogs the person does not have (removed, or newly listed by a newer
    engine), then the rows of the suggestions file, minus every catalog already added (the same repository in any
    spelling). A row is {id (the listed id, else None), kind (always suggested), name, description, url, branch,
    host_access, listed}. `host_access` only preselects a choice in the Add dialog, it never allows anything. The
    file is read, never written; a file that is missing, damaged or hostile, or a row that is, gives nothing."""
    added = {display_address(_effective(one)[0]) for one in entries() if one["kind"] == "git"}
    rows = []
    try:
        for row in _defaults().not_added():
            if isinstance(row, dict) and isinstance(row.get("id"), str) and isinstance(row.get("url"), str):
                rows.append({"id": row["id"], "kind": "suggested", "name": shown(row.get("name") or row["id"], 40),
                             "description": shown(row.get("description") or "", 90), "url": row["url"],
                             "branch": row["branch"] if isinstance(row.get("branch"), str) and row["branch"] else None,
                             "host_access": False, "listed": True})
    except Exception:  # noqa: BLE001 - the default module is another module's: failing, it adds no row
        rows = []
    where = suggestions_file()
    data, why = _read(where, SUGGESTIONS_LIMIT) if where else (None, _MISSING)
    value = _json(data)[0] if why is None else None
    if isinstance(value, dict) and type(value.get("schema")) is int and value["schema"] == SCHEMA \
            and isinstance(value.get("catalogs"), list):
        rows += [row for row in map(_suggested, value["catalogs"][:SUGGESTIONS_MAX]) if row]
    seen, kept = set(added), []
    for row in rows:
        key = display_address(row["url"])
        if key not in seen:
            seen.add(key)
            kept.append(row)
    return kept


# ---------------------------------------------------------------- what the resolver reads

def _facts(ns, one, held):
    """(kind, the manifest when the install has a valid one, whether the folder of a folder catalog is there): what the
    disk says of a catalog, read once for its state and its row."""
    if ns == LOCAL:
        return "machine", None, False
    if one["kind"] == "folder":
        return "folder", None, os.path.isdir(_root(paths.CATALOGS_DIR, ns))
    return "git", _manifest(ns, held), False


def _state(kind, one, held, found, folder_here):
    """The state key a catalog is in as far as the disk says it, without the network: `machine` for the
    personal area; for a folder `linked` or `missing_folder`; for a git catalog `updating` or `downloading` while an
    operation is under way, `damaged` when its manifest is not valid for the pin, the state its last failure names
    (FAILURES, `failed` for a code that has none), `downloading` when it has no pin and no failure yet, else `uptodate`.
    Whether the newest version differs and which Lab it needs are the check's to say (describe), not the disk's."""
    if kind != "git":
        return "machine" if kind == "machine" else "linked" if folder_here else "missing_folder"
    pending = held.get("pending")
    if pending:
        return "downloading" if pending["op"] == "add" and not held.get("pin") else "updating"
    if held.get("pin") and found is None:
        return "damaged"
    if held.get("error"):
        return FAILURES.get(held["error"], "failed")
    return "uptodate" if held.get("pin") else "downloading"


def _row(ns, one, held, tracked, listed_ids):
    """The row of `inputs()` for catalog `ns`."""
    kind, found, folder_here = _facts(ns, one, held)
    has_copy = {"git": bool(held.get("pin")) or found is not None, "folder": folder_here, "machine": False}[kind]
    summary = _summary(ns) if (found is not None or folder_here) else {}
    # A folder is read live, a git install from the lock
    sources = [summary, held] if kind == "folder" else [held, summary]
    listed_row = tracked.get(ns) or {}
    named = next((one_of["name"] for one_of in sources if one_of.get("name")), None)
    version = next((one_of["version"] for one_of in sources if one_of.get("version")), None) if has_copy else None
    lab = next((one_of["lab"] for one_of in sources if isinstance(one_of.get("lab"), int)), None) if has_copy else None
    # The listed name and description are shown until the catalog.yml of the install is read, and no longer
    description = summary.get("description", "") if summary else shown(listed_row.get("description") or "", 90)
    listed = kind != "machine" and ns in listed_ids
    host = "listed" if listed else "allowed" if kind != "machine" and one.get("host") is True else "no"
    return {"name": "Personal" if kind == "machine" else named or shown(listed_row.get("name") or ns, 40),
            "description": "" if kind == "machine" else description, "version": version, "lab": lab,
            "audience": held["audience"] if kind != "machine" else "private", "kind": kind,
            "requires_catalogs": copy.deepcopy(summary.get("requires_catalogs") or {}),
            "state": _state(kind, one, held, found, folder_here), "units": _units(ns, one, held),
            "ignored": found["ignored"] if found else 0, "error": held.get("error"),
            "pending": copy.deepcopy(held.get("pending")), "listed": listed, "host": host}


def _catalogs():
    """[(namespace, entry or None, lock entry)] in the order of precedence, from one read of each file."""
    config = _load_config()[0] or {"catalogs": []}
    by_id = {one["id"]: one for one in config["catalogs"]}
    held = _load_lock()[0]["catalogs"]
    first = paths.default_ns()
    ids = ([first] if first in by_id else []) + [LOCAL] + [ns for ns in by_id if ns != first]
    return [(ns, by_id.get(ns), held.get(ns) or _blank()) for ns in ids]


def sources() -> list:
    """The catalogs to read, in the order they claim names: the default catalog, `local`, then the
    others as added. Each {id, kind (git, folder or machine), path (where its units are read, relative to the project:
    .lab/catalogs/<id>), state (the key of inputs)}. A catalog whose state is `downloading`, `missing_folder` or
    `damaged` has nothing to read."""
    found = []
    for ns, one, held in _catalogs():
        kind, manifest_found, folder_here = _facts(ns, one, held)
        found.append({"id": ns, "kind": kind, "path": f"{paths.CATALOGS_DIR}/{ns}",
                      "state": _state(kind, one, held, manifest_found, folder_here)})
    return found


def inputs() -> dict:
    """What the resolver and the state read of every catalog, from the disk only: {ns: {name, description,
    version, lab, audience, kind, requires_catalogs, state, units, ignored, error, pending, listed, host}}, the default
    catalog first, then `local`, then the others as added. `version` and `lab` are None while no copy is installed (a
    catalog with a version has a copy: no `pin` is given), and `units` is {kind: [names]} as units_of says it. The name
    and version come from the lock for a git install and from the live catalog.yml for a folder; `requires_catalogs` is
    {ns: {name, url, min}} from the catalog.yml of an install that is valid. `audience` is the lock's, private for a
    catalog it does not know. `host` is `listed`, `allowed` (the person allowed the items that use this machine) or
    `no`. Every value is plain JSON and none is a credential."""
    tracked, listed_ids = _tracked()
    return {ns: _row(ns, one, held, tracked, listed_ids) for ns, one, held in _catalogs()}
