"""The bare git store of a catalog (`.lab/cache/git/<ns>.git`) and the pure filter of what is installed from it. A store
holds objects and the refs `refs/lab/tip`, `pin` and `previous`, and the file a fetch locks (`lab-fetch.flock`); it has
no working tree, no hooks and no automatic garbage collection, and is disposable (a fetch makes it again). `rejections`
is as a pure function of a listing: it decides, from modes, names and sizes alone, which files of a
repository are never written to disk, and names the rule (ST01 to ST15 of the catalog standard) that refuses each.
`selected` is defined by it: what is installed, and how many files were refused. A catalog's CI reports the same list as
errors. Every command goes through `gitx` and names its store with `--git-dir`, so that a store that is missing or
damaged is an error and never a search for a repository in a parent folder."""

import contextlib
import fcntl
import hashlib
import os
import re
import shutil
import threading
import time
import unicodedata
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, NamedTuple, Optional, Tuple

from . import gitx
from .gitx import CatalogError, fail

MIB = 1024 * 1024
# A store over this size is removed and the catalog refused (the fetch is stopped as soon as it is known)
STORE_LIMIT = 300 * MIB
# What a catalog may install: a file over 1 MB (text formats) or 2 MB (others); a unit over 2000 files or 25 MB; a catalog over
# 500 units, 10000 files or 100 MB; a path of more than 8 components or 200 characters
TEXT_FILE, OTHER_FILE = 1 * MIB, 2 * MIB
UNIT_FILES, UNIT_BYTES = 2000, 25 * MIB
CATALOG_UNITS, CATALOG_FILES, CATALOG_BYTES = 500, 10000, 100 * MIB
DEPTH, PATH = 8, 200
# What a command may print: a listing of a catalog, a batch of files, a diff shown to a person
LISTING_LIMIT, BLOBS_LIMIT, DIFF_LIMIT = 64 * MIB, 160 * MIB, 256 * 1024
BATCH = 100
TEXT_SUFFIXES = (".yml", ".yaml", ".md", ".json")
KEY_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".jks", ".keystore", ".crt", ".csr", ".srl")
AGENT_FILES = ("agents.md", "claude.md", "gemini.md")
# GitHub Copilot's instructions: this file in a folder named `.github`, the whole name (`my.github/` is any folder)
COPILOT = "copilot-instructions.md"
# Folders of a machine's own (ST06), and folders of an AI agent's (ST07)
MACHINE_DIRS = ("secrets", ".state", ".git")
AGENT_DIRS = (".claude", ".agents", ".gemini")
RESERVED_PREFIXES = (".cursor", ".windsurf")
# The folders of a catalog that hold units, and the kind of each
AREAS = {"addons": "addon", "setups": "setup", "playgrounds": "playground"}
FOLDERS = {kind: folder for folder, kind in AREAS.items()}
# The guides of a kind folder, directly under it (the listing tables, and the pointers for people and agents that a
# catalog keeps there): documents of the catalog, no unit's file. They are not installed and not a refusal worth counting.
# The same names inside a unit are the unit's files, and the agents' are refused there (ST07)
GUIDES = ("README.md", "AGENTS.md", "CLAUDE.md")
LISTINGS = tuple(f"{folder}/{name}" for folder in AREAS for name in GUIDES)
# What a regular file's git mode is: anything else is a link, a submodule or not a file
REGULAR = ("100644", "100755")

_SEGMENT = re.compile(r"[A-Za-z0-9._+-]+")
_NAME = re.compile(r"[a-z0-9-]+")
_SHA = re.compile(r"[0-9a-f]{40}")
_REF = re.compile(r"refs/lab/[a-z][a-z0-9-]*")
_SHORT = re.compile(r"tip|pin|previous")
_PATHSPEC = re.compile(r"[A-Za-z0-9._+/-]+")
_RECORD = re.compile(rb"(\d{6}) (?:blob|commit|tree) ([0-9a-f]{40}) +(-|\d+)\t(.*)", re.S)
_PROGRESS = re.compile(r"(?:Receiving|Unpacking) objects:\s+(\d+)%")
_RECEIVED = re.compile(r"\),\s+([\d.]+)\s+(bytes|KiB|MiB|GiB|TiB)")
_UNITS = {"bytes": 1, "KiB": 1024, "MiB": MIB, "GiB": 1024 * MIB, "TiB": 1024 * 1024 * MIB}


class Entry(NamedTuple):
    """A file of a commit as `git ls-tree -r --long` lists it: git mode (`100644`, `100755`, `120000` link, `160000`
    submodule), object id, size in bytes (0 for a submodule) and path from the root of the repository."""
    mode: str
    oid: str
    size: int
    path: str


class MissingObject(CatalogError):
    """An object the store should hold is not there: the store is damaged or was pruned. A repair fetches again."""

    def __init__(self, oid):
        status, line = gitx.MESSAGES["fsck"]
        super().__init__("fsck", line, status, oid=oid)


class _TooBig(Exception):
    """The fetch received more than the store may hold."""


# The file in a store that a fetch holds a lock on (`flock`). Not a `*.lock`: `clean` removes those
FETCH_LOCK = "lab-fetch.flock"
_FETCHING = {}
_FETCHING_LOCK = threading.Lock()


def _dir(store) -> str:
    return os.path.abspath(str(store))


def _sha(value) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError(f"not a commit or object id: {value!r}")
    return value


def _ref(value) -> str:
    """A reference of the lab, `refs/lab/<name>` or one of `tip`, `pin` and `previous` alone, as `refs/lab/<name>`."""
    if isinstance(value, str) and _SHORT.fullmatch(value):
        value = f"refs/lab/{value}"
    if isinstance(value, str) and _REF.fullmatch(value):
        return value
    raise ValueError(f"not a reference of the lab: {value!r}")


def _rev(value) -> str:
    """A commit id, or a reference of refs/lab: the only revisions the lab names, so that none starts with a dash or
    holds a range, a path or a revision expression."""
    if isinstance(value, str) and _SHA.fullmatch(value):
        return value
    return _ref(value)


def _pathspec(path) -> str:
    if not isinstance(path, str) or not _PATHSPEC.fullmatch(path) or path.startswith(("-", "/")) \
            or any(part in ("", ".", "..") for part in path.strip("/").split("/")):
        raise ValueError(f"not a path of a catalog: {path!r}")
    return path


def _run(store, *args, **how):
    """A command on a store: its Result. Not checked."""
    return gitx.run(["--git-dir", _dir(store), *args], **how)


def _git(store, *args, **how):
    """A command on a store whose failure is an error: its Result."""
    return gitx.require_ok(_run(store, *args, **how))


# ---------------------------------------------------------------- the store

def init(path) -> None:
    """Makes the bare store, or makes sure an existing one is set as the lab needs it: no templates (so no hooks), no
    automatic garbage collection, no hooks or file system monitor, every object checked on the way in."""
    path = _dir(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    gitx.require_ok(gitx.run(["init", "--bare", "--quiet", "--template=", "--", path]))
    for key, value in (("gc.auto", "0"), ("core.hooksPath", "/dev/null"), ("core.fsmonitor", "false"),
                       ("transfer.fsckObjects", "true")):
        _git(path, "config", key, value)


def clean(store, age: float = 60.0) -> int:
    """Removes what a killed or crashed git left in a store, older than `age` seconds: its lock files (`*.lock`; a lock
    git holds lasts milliseconds, and one that stays refuses every later fetch) and the temporary packs of a download
    that was cut (`tmp_pack_*`, `tmp_idx_*`, `tmp_rev_*`). Answers how many files it removed."""
    removed, limit = 0, time.time() - age
    for folder, _, files in os.walk(_dir(store)):
        for name in files:
            path = os.path.join(folder, name)
            try:
                if (name.endswith(".lock") or name.startswith(("tmp_pack_", "tmp_idx_", "tmp_rev_"))) \
                        and not os.path.islink(path) and os.stat(path).st_mtime < limit:
                    os.unlink(path)
                    removed += 1
            except OSError:
                pass
    return removed


def size(store) -> int:
    """The bytes the store takes: loose objects and packs, as `git count-objects -v` reports them."""
    total = 0
    for line in _git(store, "count-objects", "-v").out.decode("utf-8", "replace").splitlines():
        key, _, value = line.partition(":")
        if key in ("size", "size-pack") and value.strip().isdigit():
            total += int(value) * 1024
    return total


def parse_progress(line: str) -> Optional[Tuple[float, Optional[int]]]:
    """(fraction, bytes received) of a line of git's progress, or None when it is not the download's line. `Receiving
    objects` (a large pack) and `Unpacking objects` (a small one) are the download; the bytes are known once git
    reports them."""
    found = _PROGRESS.search(line)
    if not found:
        return None
    received = _RECEIVED.search(line[found.end():])
    return int(found.group(1)) / 100, int(float(received.group(1)) * _UNITS[received.group(2)]) if received else None


class _Download:
    """Reads the errors of a fetch as they arrive: the fraction goes to `progress`, and a download that grows past the
    size of a store stops the fetch."""

    def __init__(self, progress):
        self.progress, self.fraction, self.pending = progress, 0.0, ""

    def feed(self, text):
        *lines, self.pending = re.split(r"[\r\n]", self.pending + text)
        self.pending = self.pending[-2048:]
        for line in lines:
            parsed = parse_progress(line)
            if parsed is None:
                continue
            fraction, received = parsed
            if received is not None and received > STORE_LIMIT:
                raise _TooBig()
            if fraction > self.fraction:
                self.fraction = fraction
                if self.progress:
                    self.progress(fraction)


def _flock(store, deadline, address):
    """The descriptor of the lock file of a store, with the lock on it (`flock`: released when the descriptor is closed,
    or when this process dies), or None when this file system has no such lock. Waits for the process that holds it until
    `deadline`, then `timeout`. A fetch that fails removes the store, the lock file with it: once the lock is held, the
    file is looked at again, and if it is not the one that was locked the lock is asked for again on the one that is."""
    path = os.path.join(store, FETCH_LOCK)
    while True:
        os.makedirs(store, exist_ok=True)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        keep = False
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise fail("timeout", address) from None
                    time.sleep(0.05)
                except OSError:
                    return None   # a file system that cannot lock: the lock of this process is all there is
            try:
                keep = os.stat(path).st_ino == os.fstat(descriptor).st_ino
            except OSError:
                keep = False
            if keep:
                return descriptor
        finally:
            if not keep:
                os.close(descriptor)


@contextlib.contextmanager
def _exclusive(store, wait, address=None):
    """One fetch at a time into a store, for up to `wait` seconds of waiting, then `timeout`. Two fetches into one store
    at the same time fail one of them (`cannot lock ref refs/lab/tip`) when the remote has news, and the second one
    cleans up what the first is writing. The network steps of the Lab hold no other lock, so a check in the
    background and a Check again by hand can meet: they take turns, here for the threads of this process and by `flock`
    for the other processes (the command line)."""
    key = os.path.realpath(str(store))
    with _FETCHING_LOCK:
        turn = _FETCHING.setdefault(key, threading.Lock())
    deadline = time.monotonic() + max(wait, 0)
    if not turn.acquire(timeout=max(wait, 0)):
        raise fail("timeout", address)
    descriptor = None
    try:
        descriptor = _flock(str(store), deadline, address)
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        turn.release()


def fetch(address: gitx.Address, branch: Optional[str], store, stall: float = 30, cap: float = 600,
          progress: Optional[Callable[[float], None]] = None) -> str:
    """Fetches `branch` (the remote's HEAD when None, or an empty text: `gitx.asked_branch`) into the store as
    `refs/lab/tip` and answers the sha of the tip. Every object is checked (fsck); tags, submodules and hooks are not
    touched. It fails with the classified error after `stall` seconds without a byte or `cap` seconds in all, and with
    `too_large` as soon as the download or the store passes STORE_LIMIT; the store is then removed. A store that did not
    exist before the call is removed when the fetch fails, so that a failed first attempt leaves nothing; one that did
    keeps its objects. Fetches into one store take turns (`_exclusive`); one that waits longer than a fetch may last
    is `timeout`. `progress(fraction)` is given the fraction of the download, growing, and 1.0 at the end."""
    gitx.checked(address)
    branch = gitx.asked_branch(branch, address)
    source = "HEAD" if branch is None else f"refs/heads/{branch}"
    store = Path(_dir(store))
    with _exclusive(store, cap + stall, address):
        return _fetch(address, source, store, stall, cap, progress)


def _fetch(address, source, store, stall, cap, progress):
    fresh = not (store / "HEAD").exists()
    if fresh:
        init(store)
    else:
        clean(store)
    download = _Download(progress)
    args = ["--git-dir", str(store), "-c", "transfer.fsckObjects=true", "-c", "fetch.fsckObjects=true",
            "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
            "fetch", "--progress", "--no-tags", "--prune", "--no-recurse-submodules", "--",
            address.url, f"+{source}:refs/lab/tip"]
    try:
        result = gitx.stream(args, env=gitx.address_env(address), stall=stall, cap=cap, on_stderr=download.feed)
        if result.code != 0:
            raise gitx.classify(result.err, address)
        tip = resolve(store, "refs/lab/tip")
        if tip is None:
            raise fail("git_failed", address)
        if size(store) > STORE_LIMIT:
            raise _TooBig()
    except _TooBig:
        shutil.rmtree(store, ignore_errors=True)
        raise fail("too_large", address) from None
    except BaseException:
        if fresh:
            shutil.rmtree(store, ignore_errors=True)
        raise
    if progress and download.fraction < 1.0:
        progress(1.0)
    return tip


def set_ref(store, name: str, commit: Optional[str]) -> None:
    """Points a reference of the lab (`refs/lab/tip`, `refs/lab/pin`, `refs/lab/previous`; `tip`, `pin` and `previous`
    are taken as those) at a commit the store holds; None deletes it."""
    name = _ref(name)
    if commit is None:
        _git(store, "update-ref", "-d", name)
    else:
        _git(store, "update-ref", name, _sha(commit))


def resolve(store, name: str) -> Optional[str]:
    """The sha of the commit a reference of refs/lab or a sha names, or None when the store has none."""
    result = _run(store, "rev-parse", "--verify", "--quiet", f"{_rev(name)}^{{commit}}")
    sha = result.out.decode("utf-8", "replace").strip()
    return sha if result.code == 0 and _SHA.fullmatch(sha) else None


def has(store, oid: str) -> bool:
    """Whether the store holds an object."""
    return _run(store, "cat-file", "-e", _sha(oid)).code == 0


def roots(store, commit: str) -> List[str]:
    """The root commits of the history of `commit`, sorted: the identity of a catalog."""
    out = _git(store, "rev-list", "--max-parents=0", _rev(commit)).out.decode("utf-8", "replace")
    return sorted(line for line in out.split() if _SHA.fullmatch(line))


def is_ancestor(store, old: str, new: str) -> bool:
    """Whether `old` is in the history of `new`. A commit the store does not hold is not an ancestor of anything."""
    result = _run(store, "merge-base", "--is-ancestor", _rev(old), _rev(new))
    if result.code in (0, 1):
        return result.code == 0
    if result.code == 128 and "not a valid" in result.err.lower():
        return False
    raise gitx.classify(result.err, None)


def listing(store, commit: str) -> List[Entry]:
    """Every file of a commit, as `git ls-tree -r -z --long` lists them (links and submodules included: `selected`
    decides what is kept). A listing too large to read is `too_large`; a record git did not write is `fsck`."""
    try:
        result = _run(store, "ls-tree", "-r", "-z", "--long", _rev(commit), limit=LISTING_LIMIT)
    except gitx.OutputLimit:
        raise fail("too_large") from None
    gitx.require_ok(result)
    entries = []
    for record in result.out.split(b"\0"):
        if not record:
            continue
        found = _RECORD.fullmatch(record)
        if not found:
            raise fail("fsck", output="unreadable listing")
        mode, oid, length, path = found.groups()
        entries.append(Entry(mode.decode(), oid.decode(), 0 if length == b"-" else int(length),
                             path.decode("utf-8", "surrogateescape")))
    return entries


# ---------------------------------------------------------------- the filter

class Overflow(NamedTuple):
    """A limit of that a listing passes (ST10). `scope`: the unit (`addon:echo`), or "" for the whole
    catalog; `measure`: `files`, `bytes` or `units`; `found`: what the listing holds; `limit`: what it may."""
    scope: str
    measure: str
    found: int
    limit: int


def _unit_of(parts):
    """(kind, name) of the unit a path belongs to, or None: `addons/<name>/...` and `playgrounds/<name>/...` below the
    folder, `setups/<name>.yml` for a setup."""
    kind = AREAS.get(parts[0])
    if kind == "setup":
        return (kind, parts[1][:-4]) if len(parts) == 2 and parts[1].endswith(".yml") else None
    if kind and len(parts) >= 3:
        return kind, parts[1]
    return None


def _why(entry, parts):
    """The code of the first rule that refuses a file whatever the unit it is in (), or None. The order is
    the rule's: its mode, the depth and the length of its path, each name in it from the top, the name of the file and
    its size. `.git` is counted with the folders of a machine (ST06): a repository does not hold one."""
    if entry.mode not in REGULAR:
        return "ST02" if entry.mode == "160000" else "ST01"   # a submodule; a link, or anything that is not a file
    if len(parts) > DEPTH or len(entry.path) > PATH:
        return "ST04"
    last = len(parts) - 1
    for index, segment in enumerate(parts):
        low = segment.lower()
        if not _SEGMENT.fullmatch(segment) or segment in (".", ".."):
            return "ST03"
        if low in MACHINE_DIRS:
            return "ST06"
        if low in AGENT_DIRS or low.startswith(RESERVED_PREFIXES):
            return "ST07"
        if low == ".env" or (low.startswith(".env.") and not (index == last and low == ".env.example")):
            return "ST05"
    base = parts[-1].lower()
    if base.endswith(KEY_SUFFIXES):
        return "ST06"
    if base in AGENT_FILES or (base == COPILOT and len(parts) > 1 and parts[-2].lower() == ".github"):
        return "ST07"
    return "ST09" if entry.size > (TEXT_FILE if base.endswith(TEXT_SUFFIXES) else OTHER_FILE) else None


def refusal(entry: Entry) -> Optional[str]:
    """The code (ST01 to ST07, ST09) of the first rule about one file that refuses it, wherever in a repository it
    stands, or None: the test `rejections` makes of the files of the installable places, for the tree of a catalog's CI,
    where a `.env` at the top or a key under `docs/` is not dropped (nothing there is installed) but is no less a problem.
    It knows nothing of the place: the `AGENTS.md` and `CLAUDE.md` of a kind folder (GUIDES) and the agents' files at the
    top of a catalog, which the standard allows, are ST07 here, and what only the place decides (ST10, ST13, ST14, ST15)
    is not given.
    An entry needs only a `mode`, a `path` and a `size`."""
    return _why(entry, entry.path.split("/"))


def _judge(entries):
    """The whole judgment of a listing, made once: (codes, kept, over) of a list of entries, which read only `mode`,
    `path` and `size`. `codes`: {position in the listing: the code} of every file that is refused and counted, in the
    order of the rules; `kept`: [(position, unit)] of the files that are installed; `over`: the limits the listing
    passes (Overflow). A listing over a limit has the code ST10 in `codes` for the files that count against it, and
    `kept` is what the other rules left."""
    codes, pending = {}, []   # [(position, unit)] of the files that the rules about one file let through
    for position, entry in enumerate(entries):
        parts = entry.path.split("/")
        if entry.path != "catalog.yml" and parts[0] not in AREAS:
            if parts[0] in ("", ".", "..") or not _SEGMENT.fullmatch(parts[0]):
                codes[position] = "ST03"   # a top-level name that is not a name
            continue   # README.md, Makefile, .github/, tests/: not installed, not counted
        if entry.path in LISTINGS and entry.mode in REGULAR:
            continue   # a guide of a kind folder: not a unit's file
        unit = None if entry.path == "catalog.yml" else _unit_of(parts)
        code = _why(entry, parts)
        if code is None and entry.path != "catalog.yml":
            code = "ST15" if unit is None else None if _NAME.fullmatch(unit[1]) else "ST14"
        if code:
            codes[position] = code
        else:
            pending.append((position, unit))
    twins = _case_twins([entries[position] for position, _ in pending])
    distinct = []
    for slot, (position, unit) in enumerate(pending):
        if slot in twins:
            codes[position] = "ST13"
        else:
            distinct.append((position, unit))
    heads = {_head(unit) for position, unit in distinct if unit is not None and entries[position].path == _head(unit)}
    kept = []
    for position, unit in distinct:
        if unit is not None and _head(unit) not in heads:
            codes[position] = "ST15"   # a unit without its compose.yml
        else:
            kept.append((position, unit))
    over = _overflows(entries, kept)
    if over:
        whole = any(not found.scope for found in over)
        scopes = {found.scope for found in over}
        for position, unit in kept:
            if whole or (unit is not None and f"{unit[0]}:{unit[1]}" in scopes):
                codes[position] = "ST10"
    return codes, [(position, unit) for position, unit in kept if codes.get(position) != "ST10"], over


def _overflows(entries, kept):
    """The limits of a listing's installable files: [Overflow], the units in the order they appear, then the catalog."""
    units = {}
    for position, unit in kept:
        if unit is not None:
            count, length = units.get(unit, (0, 0))
            units[unit] = (count + 1, length + entries[position].size)
    found = []
    for (kind, name), (count, length) in units.items():
        if count > UNIT_FILES:
            found.append(Overflow(f"{kind}:{name}", "files", count, UNIT_FILES))
        if length > UNIT_BYTES:
            found.append(Overflow(f"{kind}:{name}", "bytes", length, UNIT_BYTES))
    total = sum(entries[position].size for position, _ in kept)
    for measure, value, limit in (("units", len(units), CATALOG_UNITS), ("files", len(kept), CATALOG_FILES),
                                  ("bytes", total, CATALOG_BYTES)):
        if value > limit:
            found.append(Overflow("", measure, value, limit))
    return found


def rejections(entries: Iterable[Entry]) -> List[Tuple[Entry, str]]:
    """[(entry, code)]: every file of a listing that the Lab never installs and counts as dropped, in the order given,
    each once, with the code of the structure rule of the catalog standard that refuses it: ST01 a link
    (and any mode that is not a file), ST02 a submodule, ST03 a name the Lab cannot install, ST04 a path too deep or too
    long, ST05 an environment file, ST06 a key, a certificate or a machine's folder, ST07 an agent instruction file, ST09
    a file too large, ST10 a unit or a catalog over its limits (every file that counts against the limit), ST13 a path
    that differs from another only by case, ST14 a unit not named as a unit, ST15 a file that is in no complete unit.
    The first rule that applies gives the code, and those about one file come before those about its unit and its
    place. Not in the list, and not counted: what is outside `catalog.yml`, `addons/`, `setups/` and `playgrounds/`
    (`README.md`, `Makefile`, `.github/`, `tests/`: they stay in the store), except a top-level name that is not a name
    (ST03), and the guides directly under a kind folder (`README.md`, `AGENTS.md` and `CLAUDE.md`, GUIDES), which belong
    to no unit; the same names inside a unit are its files, and the agents' are ST07. Pure: it reads and writes nothing,
    runs nothing, and needs no more of an entry than its `mode`, `path` and `size`; the entries it answers are the ones it
    was given. `selected` is defined by it."""
    entries = list(entries)
    codes = _judge(entries)[0]
    return [(entries[position], codes[position]) for position in sorted(codes)]


def overflows(entries: Iterable[Entry]) -> List[Overflow]:
    """The limits (: ST10) that the installable files of a listing pass, each with what the listing holds
    and what it may: what a message about a unit or a catalog that is too large says. Empty for a listing that fits."""
    return _judge(list(entries))[2]


def selected(entries: Iterable[Entry]) -> Tuple[List[Entry], int]:
    """(kept, ignored): the files of a listing that are installed, in the order given, and the number of files in the
    installable places of a catalog that are not. It is `rejections` read the other way: `ignored` is the number of
    refused files, `kept` are those that nothing refuses. Pure: it reads nothing and writes nothing.

    Installed: `catalog.yml` at the root and the files of the units below `addons/`, `setups/` and `playgrounds/`
    (a unit is complete or absent). Never installed, counted: a link, a submodule, any file whose mode is not
    `100644` or `100755`; `.env` and `.env.*` (except `.env.example`), `secrets/` and `.state/` at any depth, `.git`;
    keys, certificates and requests (`.key .pem .p12 .pfx .jks .keystore .crt .csr .srl`); the instructions of AI
    agents (`AGENTS.md CLAUDE.md GEMINI.md`, `.claude/ .agents/ .gemini/ .cursor* .windsurf*`,
    `.github/copilot-instructions.md`) in a unit or elsewhere than as a guide of a kind folder; a segment outside
    `[A-Za-z0-9._+-]`, a `.` or `..`, an empty one; a path of more than 8 components or 200 characters; a unit folder
    outside `[a-z0-9-]+`; a file over 1 MB (`.yml .yaml .md .json`) or 2 MB; a unit without its `compose.yml`, a setup
    that is not `setups/<name>.yml`, a file directly in `addons/`, `setups/` or `playgrounds/` that is none of the guides
    (GUIDES; LISTINGS); and, because a Mac would write the second over the first, every file whose path, or the path of a
    folder above it, differs from another's only by case. The rest of the top level (`README.md`, `Makefile`, `.github/`)
    is not installed and not counted. A catalog over the limits of this module is `too_large`, not a quiet subset."""
    entries = list(entries)
    codes, kept, over = _judge(entries)
    if over:
        raise fail("too_large")
    return [entries[position] for position, _ in kept], len(codes)


def _case_twins(entries):
    """The places, in a list of entries, of those that a file system which does not tell `a` from `A` would write as they
    are. A path stands for its files and for every folder above them: two spellings of one lowercase path, in any
    component, are the same place there (a folder that merges with another, a file that is in the way of a folder), so
    every entry below such a place is dropped, not the second one written. A path met twice is dropped too."""
    spelled, files, folders, steps = {}, {}, set(), []
    for entry in entries:
        parts = entry.path.split("/")
        keys = ["/".join(parts[:count]).lower() for count in range(1, len(parts) + 1)]
        steps.append(keys)
        for count, key in enumerate(keys, 1):
            spelled.setdefault(key, set()).add("/".join(parts[:count]))
        folders.update(keys[:-1])
        files[keys[-1]] = files.get(keys[-1], 0) + 1
    twins = {key for key, names in spelled.items() if len(names) > 1}
    twins.update(key for key, count in files.items() if count > 1 or key in folders)
    return {slot for slot, keys in enumerate(steps) if not twins.isdisjoint(keys)}


def _head(unit):
    """The file that makes a unit a unit: the setup's own file, the compose.yml of an add-on or playground."""
    kind, name = unit
    return f"setups/{name}.yml" if kind == "setup" else f"{FOLDERS[kind]}/{name}/compose.yml"


def by_unit(kept: Iterable[Entry]) -> Dict[str, List[Entry]]:
    """The installable files of a listing grouped by unit, `"addon:name"` to its entries, in the order given. A file
    that belongs to no unit (`catalog.yml`) is left out."""
    units = {}
    for entry in kept:
        unit = _unit_of(entry.path.split("/"))
        if unit is not None:
            units.setdefault(f"{unit[0]}:{unit[1]}", []).append(entry)
    return units


def unit_digests(kept: Iterable[Entry]) -> Dict[str, str]:
    """The tree digest of every unit, `"addon:name": "sha256:<hex>"`: sha256 of the sorted lines `<mode> <oid> <path>` of
    the unit's files. Only git's own ids and modes go into it, so the digest of a unit at any commit is known from a
    listing, without reading a file, and two commits give the same digest if and only if the unit's installable files
    are the same files in the same places with the same modes. `"@catalog"` is the object id of `catalog.yml`, when
    kept."""
    kept = list(kept)
    digests = {}
    for unit, files in by_unit(kept).items():
        lines = sorted(f"{e.mode} {e.oid} {e.path}\n" for e in files)
        digests[unit] = "sha256:" + hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
    for entry in kept:
        if entry.path == "catalog.yml":
            digests["@catalog"] = entry.oid
    return digests


# ---------------------------------------------------------------- reading objects and history

def blobs(store, oids: Iterable[str]) -> Iterator[Tuple[str, bytes]]:
    """(oid, content) of each file, in the order asked, read in batches with `git cat-file --batch`. The ids are checked
    now, the files read as the iterator is. An object the store lacks is MissingObject (a repair fetches again); one
    that is not a file is `fsck`."""
    return _blobs(store, [_sha(oid) for oid in oids])


def _blobs(store, wanted):
    for start in range(0, len(wanted), BATCH):
        batch = wanted[start:start + BATCH]
        try:
            result = _run(store, "cat-file", "--batch", "--buffer", input="".join(f"{oid}\n" for oid in batch),
                          timeout=120, limit=BLOBS_LIMIT)
        except gitx.OutputLimit:
            raise fail("too_large") from None
        gitx.require_ok(result)
        data, at = result.out, 0
        for oid in batch:
            end = data.find(b"\n", at)
            header = data[at:end].split() if end >= 0 else []
            if len(header) == 2 and header[0].decode("ascii", "replace") == oid and header[1] == b"missing":
                raise MissingObject(oid)
            if len(header) != 3 or header[0].decode("ascii", "replace") != oid or header[1] != b"blob" \
                    or not header[2].isdigit():
                raise fail("fsck", output="unreadable object")
            length = int(header[2])
            body = data[end + 1:end + 1 + length]
            if len(body) != length or data[end + 1 + length:end + 2 + length] != b"\n":
                raise fail("fsck", output="unreadable object")
            at = end + 2 + length
            yield oid, body


def touching(store, tip: str, path: str, limit: int = 50) -> List[str]:
    """The commits, newest first and following first parents, that changed `path`, as `git log --first-parent` lists
    them: where a file such as `catalog.yml` took each of its forms."""
    if not isinstance(limit, int) or not 0 < limit <= 1000:
        raise ValueError(f"not a number of commits: {limit!r}")
    out = _git(store, "log", "--first-parent", "--format=%H", f"--max-count={limit}", _rev(tip), "--", _pathspec(path)).out
    return [line for line in out.decode("utf-8", "replace").split() if _SHA.fullmatch(line)]


def read(store, commit: str, path: str, limit: int = MIB) -> Optional[bytes]:
    """The content of a regular file at a commit, or None when the commit has no such file, it is a link, a submodule or
    a folder, or it is over `limit` bytes. Never raises for a file that is not there."""
    found = _run(store, "ls-tree", "-z", "--long", _rev(commit), "--", _pathspec(path))
    if found.code != 0:
        gitx.require_ok(found)
    records = [record for record in found.out.split(b"\0") if record]
    if len(records) != 1:
        return None
    match = _RECORD.fullmatch(records[0])
    if not match or match.group(1) not in (b"100644", b"100755") or match.group(4).decode("utf-8", "surrogateescape") != path:
        return None
    if int(match.group(3)) > limit:
        return None
    return _git(store, "cat-file", "blob", match.group(2).decode(), limit=limit + 1).out


def _clip(text: str, length: int) -> str:
    return text if len(text) <= length else text[:length - 1].rstrip() + "…"


def log(store, old: Optional[str], new: str, folders: List[str], limit: int = 5) -> List[str]:
    """The subjects of the commits from `old` (not included; None for all) to `new` that touched `folders`, newest
    first, merges left out, those that start with `Bump `, `Merge ` or `Revert ` dropped (dependency bots and merge
    commits are noise to a person), at most `limit`, each with its control and format characters (escape sequences, bidi
    overrides) removed, its white space collapsed and clipped to 80."""
    span = _rev(new) if not old else f"{_rev(old)}..{_rev(new)}"
    paths = [_pathspec(folder) for folder in folders]
    out = _git(store, "log", "--no-merges", "--max-count=200", "--format=%s", span, "--", *paths).out
    subjects = []
    for line in out.decode("utf-8", "replace").splitlines():
        text = " ".join("".join(" " if unicodedata.category(c) == "Cc" else c for c in line
                                if unicodedata.category(c) != "Cf").split())
        if text and not re.match(r"(Bump|Merge|Revert) ", text):
            subjects.append(_clip(text, 80))
        if len(subjects) >= limit:
            break
    return subjects


def diff(store, old: str, new: str, path: str) -> str:
    """The unified diff of one file between two commits, or an empty string for a file that did not change, is binary or
    whose diff is larger than DIFF_LIMIT (the dialog says "Changed, not shown")."""
    args = ["diff", "--no-color", "--no-ext-diff", "--no-textconv", "--unified=3", _rev(old), _rev(new), "--", _pathspec(path)]
    try:
        out = _git(store, *args, limit=DIFF_LIMIT).out
    except gitx.OutputLimit:
        return ""
    text = out.decode("utf-8", "replace")
    return "" if re.search(r"^Binary files ", text, re.M) else text
