"""The migration of an existing machine to the layout of the catalogs. A clone that ran the Lab before the content
left the repository holds its settings where the units were: `.env`, `secrets/`, certificates and `.state/` beside the
`compose.yml` that git has removed, the person's own items in `addons/local`, `setups/local` and `playgrounds/local`, and
an on file and platform copies that name those folders. This module moves all of it to `.lab/catalogs/` and loses none.

State based: every step looks at the disk, never at what an earlier run did, so a run that crashed is run again and a
second run changes nothing. It needs no Docker and no network, deletes only what is byte identical to the installed
catalog's copy (or junk: caches, `.DS_Store`, folders Docker made empty), and puts everything else aside, in the place
for its class. The moving primitives are `moves.py`'s (every move is journaled before it is made, which is what
`undo.sh` is generated from); `moves.STEP` crashes a run between any two steps, and this module adds its own steps to it.

Two parts. Part A (`migrate_personal`) is offline and needs no catalog: the person's own items move at once, so they are
listed while the default catalog downloads, and keep being listed if it never does. Part B (`migrate`) needs the default
catalog: it classifies what the old layout left, freezes the plan, moves the machine files into the install, translates
the on file and the platform copies (the switch) and writes the commit point, `.lab/layout.json`. Until that file exists
the layout is waiting and Compose reads the core alone. It waits for the default catalog, unless the person has none:
their catalogs file reads whole and does not list it, and Part B then runs against a catalog with no items. Nothing
happens while git tracks `addons/`, `setups/` or `playgrounds/` (the engine before the catalogs, a fork that keeps its
content), or while there is no default file: the legacy paths are then the current layout.

`status()` and `waiting()` are read by every state poll and every Compose command: they read the disk and take no lock."""

import contextlib
import hashlib
import json
import os
import posixpath
import re
import shutil
import stat
import sys
import time
from pathlib import Path
from typing import Optional

from ..docker import compose
from . import moves
from .. import paths
from ..catalogs import registry, store

LAYOUT = 2   # the layout this Lab keeps: .lab/catalogs/<namespace>/ for every catalog, this machine's own in `local`
FOLDER = {"addon": "addons", "setup": "setups", "playground": "playgrounds"}
FOLDERS = tuple(FOLDER.values())
MARKER = f"{paths.LAB_DIR}/layout.json"   # the commit point
MIGRATION = moves.MIGRATION
STARTED = f"{MIGRATION}/started.json"   # a migration was started: written before the first change, read as S0 with the plan
PLAN = f"{MIGRATION}/plan.json"   # the classification, the table of what moved and the id map, frozen when Part B starts
REPORT = f"{MIGRATION}/report.json"   # what the notice says
BACKUP = f"{MIGRATION}/backup"
KEPT = f"{MIGRATION}/kept"   # modified copies, strays and conflicts, mirrored by their old path
ON_FILE = paths.ON_FILE
TARGETS = compose.TARGETS
TITLE = "Lab updated"
FAILED_LINE = "Could not move your settings. Nothing was deleted."
READ_LIMIT = 16 << 20   # the largest file of its own this module reads back
SCAN_LIMIT = 100_000   # entries read in one legacy unit folder; more is not a unit the Lab dares to sort

GUIDES = ("README.md", "AGENTS.md", "CLAUDE.md")   # the guide files of a folder: not signals, not units
# What the old .gitignore called junk: deleted where the move leaves it behind, never counted in "Kept aside"
SCRATCH_DIRS = ("__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules")
SCRATCH_FILES = (".DS_Store", "Thumbs.db")
SCRATCH_PATTERN = re.compile(r"\..*\.(?:check\.yml|effects\.yml|tmp)")
# A machine entry, frozen from the old .gitignore: the settings, keys and state of a unit
MACHINE_DIRS = (".state", "secrets")   # at the top of a unit folder, whole
KEY_SUFFIXES = store.KEY_SUFFIXES   # the catalog standard's list (ST06), the one the Lab drops from a git install

_NAME = re.compile(paths.NAME)
# A line of the on file the Lab writes for a unit: `- path`, with quotes or a comment. The long form it writes for an item
# that runs on another platform is compose.TARGET_ITEM. The first line of a long form a person wrote is not one of these:
# it is theirs, and stays as written (layers.current already locks such a file)
_PLAIN = re.compile(r"(\s*-\s*)([\"']?)([^\s\"'#]+)\2(\s*(?:#.*)?)")
_ENV_FILE = re.compile(r"\s*(?:export\s+)?COMPOSE_FILE\s*=(.*)")

_failed = None   # why the last run of this process failed, for status(); a run that ends clears it
_backed = None   # the compose texts of the backup while a run keeps count of them (_backed_up); None: not known yet
_said = set()   # the reasons already printed once in this process

# The copy of the notice. One place for each string; `restart` is built by whoever knows what runs (restart_row)
ROWS = {
    "personal": ("Now personal", "Not in a catalog"),
    "kept": ("Kept aside", "Your changes, in .lab/migration/kept"),   # the path is shown: it is where the fix is
    "turned_off": ("Turned off", "Needs a catalog"),
    "restart": ("Restart needed", "Their folders moved"),
    "not_moved": ("Not moved", "Files stay where they were"),
}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _report():
    return {key: [] for key in ("moved", "personal", "promoted", "kept", "left", "held", "turned_off", "problems")}


def _say(reason, text):
    """One line on standard error for a reason, the first time in this process: what the Lab did not do and why."""
    if reason not in _said:
        _said.add(reason)
        print(text, file=sys.stderr)


# ---------------------------------------------------------------------------------------------------------------------
# What the disk says: the marker, the signals, the phase. No lock, no write, no network.
# ---------------------------------------------------------------------------------------------------------------------

def _path(rel):
    return os.path.join(str(paths.ROOT), rel)


def _read(path, limit=READ_LIMIT):
    """The bytes of a regular file, at most `limit` and one more, None when it is not one or cannot be read. A pipe or a
    link is none: opening a pipe waits for a writer that never comes, and a doubt must not stop the Lab."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return None
        with os.fdopen(descriptor, "rb", closefd=False) as f:
            return f.read(min(limit, info.st_size) + 1)   # one more than the limit says the file is over it
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _object(rel):
    """The JSON object a file of this machine holds, None when it is not there, is too big, does not read or is not one."""
    data = _read(_path(rel))
    try:
        found = json.loads(data.decode("utf-8-sig")) if data is not None and len(data) <= READ_LIMIT else None
    except (ValueError, RecursionError):
        return None
    return found if isinstance(found, dict) else None


def _marker():
    """None while `.lab/layout.json` does not exist; its object when it does (an empty one when it does not read: it is
    the commit point, and a file that is there says the move was made, whatever else is in it)."""
    if not os.path.isfile(_path(MARKER)):
        return None
    return _object(MARKER) or {}


def _tracked():
    """Whether git tracks anything in `addons/`, `setups/` or `playgrounds/`, read from its index: no git runs."""
    return any(moves.tracked(paths.ROOT / FOLDER[kind]) for kind in paths.KINDS)


def _inactive():
    """Why nothing is done on this tree, or None. `untouched`: there is no default file, so the legacy folders are the
    layout (the one before catalogs). `tracked`: git tracks the content, so they are the layout too."""
    if paths.default_ns() is None:
        return "untouched"
    return "tracked" if _tracked() else None


def _inactive_said():
    """_inactive, and the one line that says why when the content is tracked."""
    why = _inactive()
    if why == "tracked":
        _say("tracked", "The Lab did not move any settings: git tracks addons, setups or playgrounds here, so they are the "
                        "current layout.")
    return why


def _scratch_name(name, isdir):
    """Whether a name is junk by itself. A file that is a machine file by its name is never junk, whatever else its name
    says: `.env.tmp`, what `sed -i .tmp` leaves of an `.env`, matches both, and a doubt keeps a secret."""
    if isdir:
        return name in SCRATCH_DIRS
    return (name in SCRATCH_FILES or bool(SCRATCH_PATTERN.fullmatch(name))) and not _machine_name(name)


def _machine_name(name):
    """Whether a file is a machine file by its name: `.env` and `.env.*` but the example, and the keys. Case is
    folded: git on a Mac ignored `Server.KEY` too, and a doubt keeps a secret."""
    low = name.lower()
    return low == ".env" or (low.startswith(".env.") and low != ".env.example") or low.endswith(KEY_SUFFIXES)


def _ghost(path, budget=200):
    """Whether `path` is a folder with nothing in it but folders with nothing in them: what Docker makes, as root, for a
    bind source that git removed. A file, a link, a folder that cannot be read or one with a lot in it is not."""
    try:
        if not stat.S_ISDIR(os.lstat(path).st_mode):
            return False
        pending = [path]
        while pending:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    budget -= 1
                    if budget < 0 or not entry.is_dir(follow_symlinks=False):
                        return False
                    pending.append(entry.path)
    except OSError:
        return False
    return True


def _entries(folder):
    """[(name, is a folder and no link)] of a folder, sorted; links are never followed."""
    with os.scandir(folder) as found:
        return sorted((entry.name, entry.is_dir(follow_symlinks=False)) for entry in found)


def _counts(folder, name, isdir, own=False):
    """Whether an entry of a legacy kind folder is something to move: not `local` (the person's own, S3, unless `own`:
    the entry is in that folder already), not a guide file, not junk, not a ghost folder."""
    return not ((name == "local" and not own) or (name in GUIDES and not isdir) or _scratch_name(name, isdir)
                or (isdir and _ghost(os.path.join(folder, name))))


def _signal_s1(text):
    """Whether the on file names a unit by a legacy path, in the forms the Lab writes."""
    for line in text.splitlines():
        found = compose.TARGET_ITEM.fullmatch(line)
        if found:
            if _legacy(compose._original(found.group(1))) or _legacy(found.group(3)):
                return True
        else:
            plain = _PLAIN.fullmatch(line)
            if plain and _legacy(plain.group(3)):
                return True
    return False


def _legacy(path):
    """A path of the on file as a legacy project path (`addons/<name>/compose.yml`), or None when it is not one."""
    if not isinstance(path, str) or not path or path.startswith("/") or "\0" in path:
        return None
    norm = posixpath.normpath(path)
    return norm if norm.split("/")[0] in FOLDERS else None


def _stuff(folder, test):
    """Whether a legacy folder holds an entry for which `test(name, is a folder)` is true. A folder that exists and cannot
    be read counts as holding one: a doubt is a signal, since the move meets the folder again and says what it is, while
    a clone taken for fresh would never be looked at again."""
    try:
        return any(test(name, isdir) for name, isdir in _entries(folder))
    except OSError:
        return True


def _signals(first=True):
    """The signals of an old layout that are on the disk: S0 a migration was started and not finished, S1 the on
    file names a legacy path, S2 a legacy folder holds something, S3 a `local` folder does, S4 a platform copy has a
    legacy name. Once the marker exists only S1 and S4 count (`first` is False): the others would keep calling a
    directory the Lab could not move a signal for ever."""
    found = []
    if first and any(os.path.isfile(_path(rel)) for rel in (PLAN, STARTED)):
        found.append("S0")
    text = (_read(_path(ON_FILE)) or b"").decode("utf-8", "replace")
    if _signal_s1(text):
        found.append("S1")
    if first:
        for kind in paths.KINDS:
            folder = _path(FOLDER[kind])
            if not os.path.isdir(folder):
                continue
            if "S2" not in found and _stuff(folder, lambda name, isdir: _counts(folder, name, isdir)):
                found.append("S2")
            local = os.path.join(folder, "local")
            if "S3" not in found and os.path.isdir(local) and _stuff(
                    local, lambda name, isdir: _counts(local, name, isdir, own=True)):
                found.append("S3")
    targets = _path(TARGETS)
    prefixes = tuple(f"{folder}__" for folder in FOLDERS)
    if os.path.isdir(targets) and _stuff(targets, lambda platform, isdir: isdir and _stuff(
            os.path.join(targets, platform), lambda name, _: name.startswith(prefixes))):
        found.append("S4")
    return found


def _phase():
    """ready (nothing to move, or moved), waiting (an old layout is still being moved) or failed (the move could not be
    made in this process; the layout is still the old one)."""
    if _marker() is not None or _inactive():
        return "ready"
    if _signals(first=True):
        return "failed" if _failed else "waiting"
    return "ready"


def waiting() -> bool:
    """Whether the old layout is still being moved, so that only the core and the person's own items are usable. Also while a move has failed: the on file is still the old one. Never raises: a doubt is not waiting."""
    try:
        return _phase() != "ready"
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------------------------------------------------
# The notice
# ---------------------------------------------------------------------------------------------------------------------

def _shown(item):
    """What a person reads of a key `kind:id` of the report: the name of the item, never its place."""
    name = item.split(":", 1)[1] if item.split(":", 1)[0] in (*FOLDER, "path") and ":" in item else item
    return name[len("local/"):] if name.startswith("local/") else name


def _row(key, items):
    names = sorted({_shown(item) for item in items})
    return {"key": key, "label": ROWS[key][0], "items": names, "hint": ROWS[key][1]} if names else None


def _kept_names():
    """The items that have something under `kept/`, by what the disk says: `kind:name`, `kind:local/name` for a personal
    one, `path:addons/README.md` for a file of a legacy folder. Read from the disk, so a run that was stopped and run again
    says the same."""
    base, found = _path(KEPT), []
    for kind, folder in FOLDER.items():
        try:
            listing = _entries(os.path.join(base, folder))
        except OSError:
            continue   # nothing of this kind was put aside
        for name, isdir in listing:
            here = os.path.join(base, folder, name)
            if name == "local" and isdir:
                found += [f"{kind}:local/{n[:-4] if kind == 'setup' and n.endswith('.yml') else n}"
                          for n, _ in _entries(here) if _has_file(os.path.join(here, n))]
            elif not _has_file(here):
                continue   # a folder that was emptied again (an undone move) says nothing
            elif kind == "setup" and name.endswith(".yml"):
                found.append(f"setup:{name[:-4]}")
            elif isdir:
                found.append(f"{kind}:{name}")
            else:
                found.append(f"path:{folder}/{name}")
    return found


def _notice(marker):
    """The notice of a finished move, from the report that was written with the marker: the rows with something in them, and
    the items whose folders moved (the page adds the row `Restart needed` from the ones that run: restart_row). None when
    there is nothing to say or the person closed it."""
    if marker.get("closed"):
        return None
    found = (_object(REPORT) or {}).get("report")
    if not isinstance(found, dict):
        return None
    lists = {key: [item for item in found.get(key, []) if isinstance(item, str)] for key in _report()}
    rows = [row for row in (_row("personal", lists["promoted"]), _row("kept", lists["kept"]),
                            _row("turned_off", lists["turned_off"]), _row("not_moved", lists["left"])) if row]
    moved = [{"kind": kind, "id": name} for kind, _, name in (item.partition(":") for item in lists["moved"])
             if kind in FOLDER and name]
    if not rows and not moved:
        return None
    return {"id": marker.get("migrated"), "title": TITLE, "text": "", "rows": rows, "moved": moved, "button": "Close"}


def restart_row(notice, late) -> Optional[dict]:
    """The row `Restart needed` for a notice: the names of the items whose folders moved that are running and
    behind their configuration. `late` holds their keys `kind:id` (what model.lateness reports). None when none is."""
    late = set(late)
    return _row("restart", [item["id"] for item in (notice or {}).get("moved", []) if f"{item['kind']}:{item['id']}" in late])


def status() -> dict:
    """{"phase": "ready" | "waiting" | "failed", "notice": the 'Lab updated' notice or None}, from the disk only.
    Waiting says the settings are not changed yet; failed says the move could not be made and offers to try again."""
    try:
        marker = _marker()
        if marker is not None:
            return {"phase": "ready", "notice": _notice(marker)}
        phase = _phase()
    except (OSError, ValueError):
        return {"phase": "ready", "notice": None}   # a doubt is not a move under way, as for waiting()
    if phase == "waiting":
        row = {"key": "settings", "label": "Settings", "items": ["Not changed"], "hint": ""}
        return {"phase": phase, "notice": {"id": None, "title": TITLE, "text": "", "rows": [row], "moved": [],
                                           "button": "Close"}}
    if phase == "failed":
        return {"phase": phase, "notice": {"id": None, "title": TITLE, "text": FAILED_LINE, "rows": [], "moved": [],
                                           "button": "Try again"}}
    return {"phase": "ready", "notice": None}


# ---------------------------------------------------------------------------------------------------------------------
# Writing: private folders, atomic files, the locks
# ---------------------------------------------------------------------------------------------------------------------

def _put(rel, text, mode=0o600):
    """Writes a file of this machine whole, in a folder only the person reads when it is one of the Lab's."""
    file = paths.ROOT / rel
    moves._parents(str(file))   # the one maker of private folders (0700 under .lab/migration and .lab/held)
    paths.write_atomic(file, text, mode=mode)


def _json(value):
    return json.dumps(value, indent=1, sort_keys=True) + "\n"   # ASCII: a name that is not UTF-8 survives


def _text(file):
    """The text of a regular file, None when it is not there, is not one or is not UTF-8 (it is then left as it is)."""
    data = _read(file, 1 << 28)
    try:
        return data.decode("utf-8") if data is not None else None
    except ValueError:
        return None


def _overwrite(file, text):
    """Replaces a text file whole and keeps its mode: a file that was 0600 does not become 0644."""
    paths.write_atomic(Path(file), text, mode=stat.S_IMODE(os.stat(file).st_mode))


@contextlib.contextmanager
def _locked():
    """The locks of every change to the catalogs: the catalogs' (in this process and in the others), then the
    project's. Taken here, never by status() or waiting(), which the project holds its own lock to call."""
    from . import project   # here: project imports this module
    with registry.flock(), project.exclusive():
        yield


def _step(label):
    moves._step(f"layout:{label}")   # the hook a test sets to stop a run between two steps


def _default_ready(default):
    """Whether the default catalog is installed and valid: a git install whose manifest holds for the pin, or a folder
    that is linked in its place."""
    one = registry.entry(default)
    if one is None:
        return False
    if one["kind"] == "git":
        return registry.manifest(default) is not None
    return os.path.isdir(os.path.join(str(paths.ROOT), paths.CATALOGS_DIR, default))


def _opted_out(default):
    """Whether the person decided against the default catalog: `.lab/catalogs.json` is there, reads whole and does not list
    it (`catalog.py reset --empty`, or a Remove). Nothing will ever install it unasked, so the move does not wait
    for it: Part B runs against a catalog with no items. What the pull left of an item with a compose file is the person's
    now, the settings of the others are held for the catalog that comes, and a line that names nothing goes. A file that
    does not read is a doubt, not a decision."""
    return registry.read_config() is not None and registry.config_problem() is None and registry.entry(default) is None


def _settled(default):
    """Whether Part B can run: the default catalog is installed and valid, or the person decided to have none."""
    return _default_ready(default) or _opted_out(default)


# ---------------------------------------------------------------------------------------------------------------------
# What to move where: tables, scans, copies
# ---------------------------------------------------------------------------------------------------------------------

def _install(default):
    return f"{paths.CATALOGS_DIR}/{default}"


def _local(kind, name=None):
    """Where the person's own items of a kind are: the folder, or the item in it (`name`, a setup's with its `.yml`)."""
    folder = f"{paths.CATALOGS_DIR}/{paths.LOCAL}/{FOLDER[kind]}"
    return folder if name is None else f"{folder}/{name}{'.yml' if kind == 'setup' else ''}"


def _table_a(default):
    """What Part A moves by: the kind folders of the person's own items, and the kinds of the default catalog (the unit is
    not needed, only its kind: a folder that is not there any more still has a place to be pointed at)."""
    return [(f"{FOLDER[kind]}/local", _local(kind)) for kind in paths.KINDS] \
        + [(FOLDER[kind], f"{_install(default)}/{FOLDER[kind]}") for kind in paths.KINDS]


def _sorted(table):
    """A table as pairs, the longest old path first: the one that holds a path most closely wins."""
    return sorted(((old, new) for old, new in table), key=lambda pair: (-len(pair[0]), pair[0]))


def _mapped(path, pairs):
    """Where a legacy project path is now, by the longest old path that holds it; None when no entry does."""
    for old, new in pairs:
        if path == old or path.startswith(old + "/"):
            return new + path[len(old):]
    return None


def _files_under(base):
    """The regular files below a folder, as POSIX paths relative to it, sorted; links are never followed."""
    found = []
    for here, _, files in os.walk(base):
        for name in files:
            full = os.path.join(here, name)
            if stat.S_ISREG(os.lstat(full).st_mode):
                found.append(os.path.relpath(full, base).replace(os.sep, "/"))
    return sorted(found)


def _has_file(base):
    """Whether a path is a file or a link, or has one below it (a folder that was emptied says nothing)."""
    if not os.path.isdir(base) or os.path.islink(base):
        return os.path.lexists(base)
    for here, folders, files in os.walk(base):
        if files or any(os.path.islink(os.path.join(here, name)) for name in folders):
            return True
    return False


def _sha(path):
    """sha256 of a regular file that is not a link; None when it is not one or cannot be read."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))   # a pipe does not wait
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as f:
            for block in iter(lambda: f.read(1 << 16), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _copy_once(source, target):
    """Copies a file or a folder to the backup unless the backup has it already: the first copy is the one that counts.
    Through a partial that is renamed when whole, so that a crash never leaves half a copy that reads as whole."""
    if os.path.lexists(target) or not os.path.lexists(source):
        return
    partial = target + ".partial"
    moves._discard(partial)
    moves._parents(target)
    if os.path.isdir(source) and not os.path.islink(source):
        shutil.copytree(source, partial, symlinks=True)
    else:
        shutil.copy2(source, partial)
    os.replace(partial, target)
    if _backed is not None and target.startswith(os.path.join(_path(BACKUP), "units") + os.sep):
        _backed.append(os.path.relpath(target, os.path.join(_path(BACKUP), "units")).replace(os.sep, "/"))


def _backed_up():
    """The compose texts of the backup, as the paths they were copied from. Walked once for a run, then kept by
    `_copy_once`, which writes there and nowhere else while the run holds the locks: the undo script is written after
    each move, and a walk each time would cost more than the moves."""
    global _backed
    if _backed is None:
        units = os.path.join(_path(BACKUP), "units")
        _backed = _files_under(units) if os.path.isdir(units) else []
    return sorted(set(_backed))


def _begin():
    """A1: the migration starts. Its witness is written before the first change, so that a run that stops anywhere after
    it is found again, after a line `start` of the journal, which is where the report and the undo script begin; then
    the first copies of what it will change are made, and the undo script written. A backup with no witness is what an
    undone migration left (undo.sh removes the witness): it is not this one's baseline, and it is set aside, not deleted."""
    global _backed
    _backed = None   # under the lock now: the backup is looked at again, whatever another thread of this process did
    if not os.path.isfile(_path(STARTED)) and not os.path.isfile(_path(PLAN)):
        old = _path(BACKUP)
        if os.path.lexists(old):
            aside, number = f"{old}.{_now().replace(':', '')}", 1
            while os.path.lexists(aside):
                number += 1
                aside = f"{old}.{_now().replace(':', '')}-{number}"
            os.rename(old, aside)
        _step("start")
        moves.journal("start", default=paths.default_ns())   # first: the report and the undo script read from the last one
        _put(STARTED, _json({"schema": 1, "from": 1, "default": paths.default_ns(), "at": _now()}))
    _step("backup")
    for name in ("on.yml", "starred.json", "tested.json"):
        _copy_once(_path(f"{paths.LAB_DIR}/{name}"), _path(f"{BACKUP}/{name}"))
    _copy_once(_path(TARGETS), _path(f"{BACKUP}/targets"))
    _write_undo()   # from here a person who goes back has a script, whatever stops the run


def _transfer(source, target, why, found):
    """relocate, and a conflict (the place is taken) puts the old one aside under `kept/` at its old path: both are kept,
    nothing is merged or overwritten."""
    result = moves.relocate(source, target, why)
    if result == "conflict":
        result = moves.relocate(source, f"{KEPT}/{source}", "conflict")
        if result == "conflict":
            found["problems"].append(f"{source} was kept where it is: its place under kept is taken")
    return result


# ---------------------------------------------------------------------------------------------------------------------
# The compose texts of the items that moved
# ---------------------------------------------------------------------------------------------------------------------

def _texts(table_a, table_b, fixups, found):
    """Writes again the paths of each compose file the migration moved, from the copy of its original text, never from
    what an earlier run wrote. `table_a` is Part A's (what it wrote is the provisional text), `table_b` the full one.
    The file holds the original or the provisional text: it gets the final one. It holds the final one: it stays. It
    holds something else, because the person edited it while the catalog downloaded: their text stays, and only the
    paths to an item that Part B made personal (`fixups`: the place it was pointed at, the place it is) are written
    again. A file whose item was put aside, that is gone or that is a link is not touched."""
    base = os.path.join(_path(BACKUP), "units")
    pairs = _sorted(table_b)
    for legacy in _files_under(base):
        new = _mapped(legacy, pairs)
        unit = legacy if legacy.startswith("setups/") else posixpath.dirname(legacy)
        if new is None or os.path.lexists(_path(f"{KEPT}/{unit}")):   # the item was put aside: its place is another's
            continue
        try:
            info = os.lstat(_path(new))
        except OSError:
            continue   # the person deleted it
        if not stat.S_ISREG(info.st_mode):
            found["problems"].append(f"{new} is a link: its paths were not written again")
            continue
        before, current = _text(os.path.join(base, legacy)), _text(_path(new))
        if before is None or current is None:
            found["problems"].append(f"{new} is not UTF-8 text: its paths were not written again")
            continue
        was, now = posixpath.dirname(legacy), posixpath.dirname(new)
        provisional = moves.repoint(before, was, now, table_a)
        final = moves.repoint(before, was, now, table_b)
        if current == final:
            continue
        text = final if current in (before, provisional) else moves.repoint(current, now, now, fixups)
        if text != current:
            _step("text")
            _overwrite(_path(new), text)
            moves.journal("rewrote", path=new)


# ---------------------------------------------------------------------------------------------------------------------
# Part A: the person's own items, offline
# ---------------------------------------------------------------------------------------------------------------------

def _unit_name(kind, name, isdir):
    """The name of an item of a `local` folder, None for what is not one: a setup is a `.yml` file, the others are
    folders, and a name is lowercase letters, digits and dashes. A link is not one: its target may be relative."""
    if kind == "setup":
        return name[:-4] if not isdir and name.endswith(".yml") and _NAME.fullmatch(name[:-4]) else None
    return name if isdir and _NAME.fullmatch(name) else None


def _relink(kind, folder, found):
    """A `local` folder that is a link (a synced folder): the same link is made in `.lab/catalogs/local`, relative as it was
    when it was, and the folder is left as it is. The items are reached through the new link; their texts are written in
    place."""
    target, place = _local(kind), _path(_local(kind))
    link = os.readlink(_path(folder))
    if not os.path.isdir(_path(folder)):
        found["problems"].append(f"{folder} is a link to something that is not a folder: it was left as it is")
        return
    if not os.path.lexists(place):
        absolute = os.path.normpath(os.path.join(os.path.dirname(_path(folder)), link))
        fresh = link if os.path.isabs(link) else os.path.relpath(absolute, os.path.dirname(place))
        moves._parents(place)
        moves.journal("link", path=target, target=fresh, of=folder)
        _step("link")
        os.symlink(fresh, place)
    elif not (os.path.islink(place) and os.path.realpath(place) == os.path.realpath(_path(folder))):
        found["problems"].append(f"{folder} is a link and {target} is something else: they were left as they are")
    if os.path.isfile(_path(PLAN)):
        return   # Part B has moved items into the linked folder since: what is in it now is not what Part A found
    try:
        listing = _entries(_path(folder))
    except OSError:
        found["problems"].append(f"{folder} could not be read: its items were left as they are")
        return
    for name, isdir in listing:
        if _unit_name(kind, name, isdir):
            file = f"{folder}/{name}" if kind == "setup" else f"{folder}/{name}/compose.yml"
            _copy_once(_path(file), _path(f"{BACKUP}/units/{file}"))


def _part_a(found):
    """The items of `addons/local`, `setups/local` and `playgrounds/local` move to `.lab/catalogs/local`
    one at a time, each with the copy of its compose text first and its provisional text after. No catalog is needed:
    an include of an add-on of the default catalog is written to where the catalog will put it. The on file is not
    touched, and nothing else moves."""
    default = paths.default_ns()
    _begin()
    for kind in paths.KINDS:
        folder = f"{FOLDER[kind]}/local"
        if os.path.islink(_path(folder)):
            _relink(kind, folder, found)
            continue
        if not os.path.isdir(_path(folder)):
            continue
        try:
            listing = _entries(_path(folder))
        except OSError:
            found["problems"].append(f"{folder} could not be read: its items were left as they are")
            continue   # Part B's plan leaves it as it is and says so
        for name, isdir in listing:
            unit = _unit_name(kind, name, isdir)
            if unit is None:
                continue   # junk, a guide file, a link, a name the grammar refuses: Part B's plan sorts it
            source, target = f"{folder}/{name}", _local(kind, unit)
            file = source if kind == "setup" else f"{source}/compose.yml"
            _copy_once(_path(file), _path(f"{BACKUP}/units/{file}"))
            _transfer(source, target, "personal", found)
            _write_undo()
    table = _table_a(default)
    _texts(table, table, [], found)
    _step("undo-personal")
    _write_undo()


# ---------------------------------------------------------------------------------------------------------------------
# Part B: the plan
# ---------------------------------------------------------------------------------------------------------------------

class _Big(Exception):
    """A legacy folder with more in it than the Lab dares to sort."""


def _scan(folder):
    """What a legacy unit folder holds, below the top: `machine` (the entries that move to the install, each whole: a
    top-level `.state` or `secrets`, a `.env` or a key at any depth, a folder that cannot be read), `files` (anything
    else, a person's or a stray), `scratch` (junk and ghost folders) and whether it has a `compose.yml`. Paths are
    relative to the folder; a link is a file, never followed."""
    found = {"machine": [], "files": [], "scratch": [], "compose": False}
    pending, count = [("", folder)], 0
    while pending:
        above, here = pending.pop()
        try:
            listing = _entries(here)
        except OSError:
            if not above:
                raise _Big(folder) from None   # the unit's own folder: it is left, and said
            found["machine"].append(above)   # a folder a container made and the person cannot read: whole, with its secrets
            continue
        for name, isdir in listing:
            count += 1
            if count > SCAN_LIMIT:
                raise _Big(folder)
            rel, path = f"{above}/{name}" if above else name, os.path.join(here, name)
            if not above and name in MACHINE_DIRS:
                found["machine"].append(rel)
            elif isdir:
                if name in SCRATCH_DIRS or _ghost(path):
                    found["scratch"].append(rel)
                else:
                    pending.append((rel, path))
            elif _scratch_name(name, False) or (name in SCRATCH_DIRS and os.path.islink(path)):
                found["scratch"].append(rel)   # a link named like a cache goes as a link, never followed
            elif _machine_name(name):
                found["machine"].append(rel)
            else:
                found["files"].append(rel)
                found["compose"] = found["compose"] or (not above and name == "compose.yml")
    for key in found:
        if isinstance(found[key], list):
            found[key].sort()
    return found


def _free(taken, kind, name):
    """The name an item takes among the person's own: its own, else `-2`, `-3`, ...."""
    chosen, number = name, 2
    while chosen in taken[kind]:
        chosen, number = f"{name}-{number}", number + 1
    taken[kind].add(chosen)
    return chosen


def _taken():
    """{kind: names} the person's own items already have in `.lab/catalogs/local`."""
    found = {}
    for kind in paths.KINDS:
        try:
            names = [name for name, _ in _entries(_path(_local(kind)))]
        except OSError:
            names = []
        found[kind] = {name[:-4] if kind == "setup" and name.endswith(".yml") else name for name in names}
    return found


class _Plan:
    """The classification of what the old layout left, as it is built: the operations to make (each a move, a drop or a piece of junk, with the
    step of the move that makes it), the items by class, what is left, and the table of what moved where."""

    def __init__(self, default, names, manifest):
        self.default, self.names, self.manifest = default, names, manifest
        self.ops, self.units, self.left, self.ids, self.promoted = [], [], [], {}, []
        self.taken = _taken()

    def op(self, step, **fields):
        self.ops.append({"step": step, **fields})

    def unit(self, kind, name, klass, **fields):
        self.units.append({"kind": kind, "name": name, "class": klass, **fields})

    def sha(self, relative):
        """sha256 of the installed copy of a file of the default catalog, None when the catalog has none."""
        if self.manifest is not None:
            record = (self.manifest.get("files") or {}).get(relative)
            return record.get("sha256") if isinstance(record, dict) else None
        return _sha(_path(f"{_install(self.default)}/{relative}"))

    def kept(self, relative):
        self.op(4, op="move", **{"from": relative, "to": f"{KEPT}/{relative}", "why": "kept"})

    def junk(self, relative):
        self.op(5, op="scratch", path=relative)


def _plan_setup(plan, name):
    """A file of `setups/`: an identical copy of the catalog's goes; a modified one is kept aside; one the catalog does
    not have is promoted."""
    stem, relative = name[:-4], f"setups/{name}"
    if stem in plan.names["setup"]:
        mine = _sha(_path(relative))
        if mine is not None and mine == plan.sha(relative):
            plan.unit("setup", stem, "identical", moved=False)
            plan.op(4, op="drop", path=relative, sha=plan.sha(relative))
        else:
            plan.unit("setup", stem, "modified", moved=False)
            plan.kept(relative)
        return
    chosen = _free(plan.taken, "setup", stem)
    target = _local("setup", chosen)
    plan.unit("setup", stem, "promoted", moved=False, to=target, id=f"local/{chosen}")
    plan.ids[f"setup:{stem}"] = f"local/{chosen}"
    plan.promoted.append([relative, target])
    plan.op(3, op="move", **{"from": relative, "to": target, "why": "promoted", "text": relative})


def _plan_folder(plan, kind, name):
    """A folder of `addons/` or `playgrounds/` that is not `local`: the class it is, and what each of its parts
    does."""
    relative = f"{FOLDER[kind]}/{name}"
    try:
        scan = _scan(_path(relative))
    except _Big:
        plan.left.append(relative)
        return
    if name not in plan.names[kind]:
        if scan["compose"]:   # promoted: a whole folder that is the person's now
            chosen = _free(plan.taken, kind, name)
            target = _local(kind, chosen)
            plan.unit(kind, name, "promoted", moved=False, to=target, id=f"local/{chosen}")
            plan.ids[f"{kind}:{name}"] = f"local/{chosen}"
            plan.promoted.append([relative, target])
            plan.op(3, op="move", **{"from": relative, "to": target, "why": "promoted", "text": f"{relative}/compose.yml"})
            return
        home = f"{moves.HELD}/{FOLDER[kind]}/{name}"   # held: its settings wait for the catalog that gets the item
        plan.unit(kind, name, "held" if scan["machine"] else "stray", moved=False)
        why, identical = "held", False
    else:
        home = f"{_install(plan.default)}/{FOLDER[kind]}/{name}"
        # A copy is identical when each of its files is the catalog's, byte for byte: then it is dropped, and the
        # catalog has it. One that differs, or has a file the catalog does not, is kept aside whole
        identical = scan["compose"] and all(
            plan.sha(f"{FOLDER[kind]}/{name}/{file}") is not None
            and _sha(_path(f"{relative}/{file}")) == plan.sha(f"{FOLDER[kind]}/{name}/{file}")
            for file in scan["files"])
        plan.unit(kind, name, "identical" if identical else "modified" if scan["compose"] else "settings",
                  moved=bool(scan["machine"]))
        why = "machine"
    for entry in scan["machine"]:
        plan.op(4, op="move", **{"from": f"{relative}/{entry}", "to": f"{home}/{entry}", "why": why})
    for file in scan["files"]:
        if identical:
            plan.op(4, op="drop", path=f"{relative}/{file}", sha=plan.sha(f"{FOLDER[kind]}/{name}/{file}"))
        else:
            plan.kept(f"{relative}/{file}")
    for entry in scan["scratch"]:
        plan.junk(f"{relative}/{entry}")


def _plan_local(plan, kind, folder):
    """What Part A left in a `local` folder: junk goes, a guide file is kept aside, an item it could not move stays and is
    reported by the journal, a link was re-linked (and stays), anything else is left."""
    if os.path.islink(_path(folder)):
        plan.left.append(folder)
        return
    try:
        listing = _entries(_path(folder))
    except OSError:   # a folder the person cannot read: left whole, as a unit folder that cannot be read is
        plan.left.append(folder)
        return
    for name, isdir in listing:
        relative = f"{folder}/{name}"
        if _unit_name(kind, name, isdir):
            continue
        if name in GUIDES and not isdir:
            plan.kept(relative)
        elif _scratch_name(name, isdir) or (isdir and _ghost(_path(relative))):
            plan.junk(relative)
        else:
            plan.left.append(relative)


def _make_plan(default):
    """The classification of what the old layout left, from the folders on the disk and from the
    catalog's unit list, which is where the table comes from: a unit that git removed with no machine file left has no
    folder at all, and the on file and the personal items still name it."""
    plan = _Plan(default, registry.units_of(default), registry.manifest(default))
    for kind in paths.KINDS:
        folder = FOLDER[kind]
        if not os.path.lexists(_path(folder)):
            continue
        if os.path.islink(_path(folder)) or not os.path.isdir(_path(folder)):
            plan.left.append(folder)
            continue
        for name, isdir in _entries(_path(folder)):   # a kind folder that cannot be read fails the move: it is tried again
            relative = f"{folder}/{name}"
            if name == "local" and (isdir or os.path.islink(_path(relative))):
                _plan_local(plan, kind, relative)   # a file called `local` is any other file that is not a unit: left
            elif name in GUIDES and not isdir:
                plan.kept(relative)
            elif _scratch_name(name, isdir) or (isdir and _ghost(_path(relative))):
                plan.junk(relative)
            elif kind == "setup" and not isdir and name.endswith(".yml") and _NAME.fullmatch(name[:-4]):
                _plan_setup(plan, name)
            elif kind != "setup" and isdir and _NAME.fullmatch(name):
                _plan_folder(plan, kind, name)
            else:
                plan.left.append(relative)
    rows = list(_table_a(default)) + [tuple(pair) for pair in plan.promoted]
    for kind in paths.KINDS:   # every unit of the catalog, so that one with no folder left on the disk is still mapped
        suffix = ".yml" if kind == "setup" else ""
        rows += [(f"{FOLDER[kind]}/{name}{suffix}", f"{_install(default)}/{FOLDER[kind]}/{name}{suffix}")
                 for name in plan.names[kind]]
    return {"schema": 1, "default": default, "ops": plan.ops, "units": plan.units, "left": plan.left,
            "ids": plan.ids, "promoted": plan.promoted, "table": [list(pair) for pair in _sorted(set(rows))]}


def _inside(path, folders):
    """Whether a path of a plan is a plain relative path below one of these folders: it names no `..`, is not absolute and
    is not written two ways, so that a plan edited by hand or damaged moves nothing outside what it is for."""
    return (isinstance(path, str) and path == posixpath.normpath(path) and not path.startswith(("/", "../"))
            and path.split("/")[0] in folders and "\0" not in path)


def _valid_op(op):
    if not (isinstance(op, dict) and op.get("step") in (3, 4, 5) and op.get("op") in ("move", "drop", "scratch")):
        return False
    if op["op"] == "move":
        return (_inside(op.get("from"), FOLDERS) and _inside(op.get("to"), (paths.LAB_DIR,))
                and op.get("why") in ("promoted", "machine", "held", "kept")
                and (op.get("text") is None or _inside(op["text"], FOLDERS)))
    return _inside(op.get("path"), FOLDERS) and (op["op"] == "scratch" or isinstance(op.get("sha"), str))


def _valid_plan(plan, default):
    """Whether a plan read back is one this module wrote for this default catalog: its shape, and that every operation names
    a legacy path to move from and a place under `.lab` to move to."""
    return (isinstance(plan, dict) and plan.get("schema") == 1 and plan.get("default") == default
            and all(isinstance(plan.get(key), list) for key in ("ops", "units", "left", "promoted", "table"))
            and isinstance(plan.get("ids"), dict) and all(_valid_op(op) for op in plan["ops"])
            and all(isinstance(unit, dict) and isinstance(unit.get("kind"), str) and isinstance(unit.get("name"), str)
                    and isinstance(unit.get("class"), str) for unit in plan["units"])
            and all(isinstance(pair, list) and len(pair) == 2 and all(isinstance(one, str) for one in pair)
                    for pair in plan["table"] + plan["promoted"]))


def _plan(default):
    """The frozen plan when there is one (S0: a run that stopped after it was written resumes it, whatever the folders look
    like now), else a new one, written whole before anything is moved by it. A plan that does not read, or is for
    another default catalog, is set aside and made again."""
    found = _object(PLAN)
    if found is not None and _valid_plan(found, default):
        return found
    if os.path.lexists(_path(PLAN)):
        os.rename(_path(PLAN), _path(f"{PLAN}.bad-{_now().replace(':', '')}"))
    _step("plan")
    plan = _make_plan(default)
    _put(PLAN, _json(plan))
    return plan


# ---------------------------------------------------------------------------------------------------------------------
# Part B: making the plan's moves
# ---------------------------------------------------------------------------------------------------------------------

def _drop(op, found):
    """An identical copy goes (the catalog has it). It is looked at again first: a file that changed since the plan is
    not the catalog's any more, and is put aside."""
    path = _path(op["path"])
    current = _sha(path)
    if current is None:
        if os.path.lexists(path):   # a link or a folder now: not what was planned
            _transfer(op["path"], f"{KEPT}/{op['path']}", "kept", found)
        return
    if current != op["sha"]:
        _transfer(op["path"], f"{KEPT}/{op['path']}", "kept", found)
        return
    moves.journal("dropped", path=op["path"], why="identical")
    _step("drop")
    os.unlink(path)


def _scratch(op, found):
    """Junk goes: a cache, a `.DS_Store`, a folder Docker made empty. Only what the name says is junk, and a folder that
    is not empty any more is not a ghost: it stays. A link is removed as a link, never followed."""
    path = _path(op["path"])
    if not os.path.lexists(path):
        return
    name, isdir = os.path.basename(path), stat.S_ISDIR(os.lstat(path).st_mode)
    if not (_scratch_name(name, isdir) or (isdir and _ghost(path)) or (os.path.islink(path) and name in SCRATCH_DIRS)):
        return
    moves.journal("scratch", path=op["path"])
    _step("scratch")
    try:
        moves._discard(path)
    except OSError as error:
        found["problems"].append(f"{op['path']} could not be removed ({error.strerror or type(error).__name__})")


def _do(op, found):
    """One operation of the plan, from the disk as it is: it does what is left to do."""
    if op["op"] == "move":
        if op.get("text"):   # the compose text is copied before the item moves, once
            _copy_once(_path(op["text"]), _path(f"{BACKUP}/units/{op['text']}"))
        if op["why"] == "kept":
            if moves.relocate(op["from"], op["to"], "kept") == "conflict":
                found["problems"].append(f"{op['from']} was kept where it is: its place under kept is taken")
        else:
            _transfer(op["from"], op["to"], op["why"], found)
        _write_undo()   # a run that stops after this move leaves a script that puts it back
    elif op["op"] == "drop":
        _drop(op, found)
    elif op["op"] == "scratch":
        _scratch(op, found)


def _remove_empty():
    """The legacy folders that are empty now go, from the bottom, with `rmdir` and nothing else: a folder with
    something in it stays, a link is never touched."""
    for folder in FOLDERS:
        base = _path(folder)
        if os.path.islink(base) or not os.path.isdir(base):
            continue
        for here, _, _ in os.walk(base, topdown=False):
            if not os.path.islink(here):
                try:
                    os.rmdir(here)
                except OSError:
                    pass   # something is in it


# ---------------------------------------------------------------------------------------------------------------------
# Part B: the switch (step 7), the undo script, the report and the marker
# ---------------------------------------------------------------------------------------------------------------------

_LEGACY_UNIT = re.compile(r"(addons|playgrounds)/((?:local/)?[a-z0-9-]+)/compose\.yml|setups/((?:local/)?[a-z0-9-]+)\.yml")


def _named(legacy):
    """`kind:id` of the item a legacy file path stands for; `path:` and the path when it is none."""
    found = _LEGACY_UNIT.fullmatch(legacy)
    if not found:
        return f"path:{legacy}"
    if found.group(3):
        return f"setup:{found.group(3)}"
    return f"{'addon' if found.group(1) == 'addons' else 'playground'}:{found.group(2)}"


def _turned_off(legacy):
    """The item has no file after the move (it is in no installed catalog): its line goes, said once in the journal and in
    the notice. The backup of the on file has it. An item whose file is still where it was (a link, a name the grammar
    refuses, a folder that could not be moved) needs no catalog: it is told as not moved, and not here."""
    if not os.path.lexists(_path(legacy)):
        moves.journal("turned_off", path=legacy, item=_named(legacy))


def _aside(legacy, new):
    """Whether the item a legacy file stands for is one of the person's own that was put aside whole under `kept/` (its
    place in `.lab/catalogs/local` was taken by another item of theirs): the line must not turn the other one on. A copy of a
    catalog's item that was put aside is another case: the catalog still has the item, and the line goes to it."""
    return new.startswith(f"{paths.CATALOGS_DIR}/{paths.LOCAL}/") and os.path.lexists(_path(f"{KEPT}/{legacy}"))


def _translate(body, pairs, found):
    """One line of the on file after the move: the line as it is, the new line, or None when the item has no file
    any more. Only the two forms the Lab writes are touched; any other line is the person's and stays."""
    target = compose.TARGET_ITEM.fullmatch(body)
    if target:
        copy, platform, _ = target.groups()
        original = _legacy(compose._original(copy))
        if original is None:
            return body
        new = _mapped(original, pairs)
        if new is None or not os.path.isfile(_path(new)) or _aside(original, new):
            _turned_off(original)
            return None
        names = compose.target_names(copy) or compose.target_names(f"{BACKUP}/targets/{platform}/{os.path.basename(copy)}")
        _step("copy")
        fresh = compose.write_target(new, platform, names)
        line = body[:target.start(3)] + posixpath.dirname(new) + body[target.end(3):]
        return line[:target.start(1)] + fresh + line[target.end(1):]
    plain = _PLAIN.fullmatch(body)
    legacy = _legacy(plain.group(3)) if plain else None
    if legacy is None:
        return body
    new = _mapped(legacy, pairs)
    if new is None or not os.path.isfile(_path(new)) or _aside(legacy, new):
        _turned_off(legacy)
        return None
    return f"{plain.group(1)}{plain.group(2)}{new}{plain.group(2)}{plain.group(4)}"


def _clear_copies(on_text):
    """The platform copies with a legacy name go once nothing names them, each after a copy of it is in the backup (a copy
    made by a stale Lab after the first backup is not in it). The new copies were written first, so a run that stops
    anywhere here is finished by the next."""
    base = _path(TARGETS)
    if not os.path.isdir(base):
        return
    for platform, isdir in _entries(base):
        if not isdir:
            continue
        for name, folder in _entries(os.path.join(base, platform)):
            relative = f"{TARGETS}/{platform}/{name}"
            if folder or not name.startswith(tuple(f"{one}__" for one in FOLDERS)) or relative in on_text:
                continue
            kept = f"{BACKUP}/targets/{platform}/{name}"
            if not os.path.lexists(_path(kept)):
                _copy_once(_path(relative), _path(kept))
            elif _sha(_path(kept)) != _sha(_path(relative)):   # a later copy than the one the backup has: both are kept
                _copy_once(_path(relative), _path(f"{kept}.{(_sha(_path(relative)) or 'link')[:8]}"))
            _step("clear")
            os.unlink(_path(relative))


def _switch(pairs, found):
    """The switch: the lines of the on file that name a legacy path name where the item is now, and the platform
    copies are written for the new place. A line whose item has no file there is removed. The file is replaced whole,
    once, after the copies; an old copy is removed after that."""
    file = _path(ON_FILE)
    text = _text(file) if os.path.lexists(file) else ""
    if text is None:
        found["problems"].append("The file that says what is on is not UTF-8 text: it was left as it is")
        return
    out = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        new = _translate(body, pairs, found)
        if new is not None:
            out.append(new + line[len(body):])
    result = "".join(out)
    if result != text:
        _step("on")
        _overwrite(file, result)
    _clear_copies(result)


def _check_env(found):
    """`COMPOSE_FILE` in `.env` that a person edited to name a legacy path is not touched (a file of secrets), and is said. Only that line is read."""
    for line in (_text(_path(paths.ENV_FILE)) or "").splitlines():
        found_line = _ENV_FILE.fullmatch(line)
        if found_line and any(_legacy(part.strip().strip("\"'")) for part in found_line.group(1).split(":")):
            found["problems"].append("COMPOSE_FILE in .env names a file that moved: edit it by hand")


def _local_item(destination):
    """`kind:local/name` of the item that moved to a place of the person's own, None for any other place."""
    folder = f"{paths.CATALOGS_DIR}/{paths.LOCAL}/"
    if not isinstance(destination, str) or not destination.startswith(folder):
        return None
    parts = destination[len(folder):].split("/")
    kind = next((one for one in FOLDER if FOLDER[one] == parts[0]), None)
    if kind is None or len(parts) != 2:
        return None
    return f"{kind}:local/{parts[1][:-4] if kind == 'setup' and parts[1].endswith('.yml') else parts[1]}"


def _journal():
    """The lines of the journal that belong to the migration under way: those after the last `start`. A migration that was
    undone and made again must not report, or put back, what the first one did."""
    entries = moves.read_journal()
    cut = max((index for index, entry in enumerate(entries) if entry.get("op") == "start"), default=-1)
    return entries[cut + 1:]


def _remaining(reported):
    """What the move leaves in `addons/`, `setups/` and `playgrounds/` by the disk and no path of `reported` holds: the
    entries of these folders and, one by one, those of a `local` folder. A file that came after the plan was frozen, a
    place under `kept/` that was taken, a folder that cannot be read: all of them stay where they are, and are told."""
    found = []
    for folder in FOLDERS:
        base = _path(folder)
        if os.path.islink(base) or not os.path.isdir(base):
            continue   # a link or a file called like the folder: the plan told it
        try:
            listing = _entries(base)
        except OSError:
            found.append(folder)
            continue
        for name, isdir in listing:
            relative = f"{folder}/{name}"
            if name == "local" and isdir:
                try:
                    found += [f"{relative}/{one}" for one, _ in _entries(os.path.join(base, name))]
                except OSError:
                    found.append(relative)
            else:
                found.append(relative)
    return {path for path in found if not any(one == path or one.startswith(f"{path}/") for one in reported)}


def _final_report(plan, found):
    """The report, from what was written down and what the disk says, never from what this run did: a run
    that stopped and was run again says what a run that did not stop says. `problems` are this run's. `moved` is every item
    whose folder is now another, which is what makes a running service late: the person's own items, the promoted
    ones and each item of the default catalog (its files are under the install now, whatever the pull left of it). With a
    plan (Part B has run) `left` is also what the disk still holds in the legacy folders."""
    report, personal, left, off = _report(), set(), set(), set()
    for entry in _journal():
        kind = entry.get("op")
        if kind == "move" and entry.get("why") == "personal":
            personal.add(_local_item(entry.get("dst")))
        elif kind in ("copied", "stayed") and isinstance(entry.get("src"), str):
            left.add(entry["src"])   # the original stays where it was
        elif kind == "link" and isinstance(entry.get("of"), str):
            left.add(entry["of"])
        elif kind == "turned_off" and isinstance(entry.get("item"), str):
            off.add(entry["item"])
    left |= set(plan["left"]) if plan else set()
    left = {path for path in left if path.split("/")[0] in FOLDERS and os.path.lexists(_path(path))}
    report["personal"] = sorted(item for item in personal if item)
    report["turned_off"] = sorted(off)
    report["kept"] = sorted(_kept_names())
    moved = set(report["personal"])
    if plan:
        units = plan["units"]
        report["promoted"] = sorted(f"{unit['kind']}:{unit['id']}" for unit in units if unit["class"] == "promoted")
        report["held"] = sorted(f"{unit['kind']}:{unit['name']}" for unit in units if unit["class"] == "held")
        names = registry.units_of(plan["default"])
        moved |= {f"{unit['kind']}:{unit['name']}" for unit in units if unit.get("moved")} | set(report["promoted"])
        moved |= {f"{kind}:{name}" for kind in paths.KINDS for name in names.get(kind, [])}
        left |= _remaining(left)
    report["moved"] = sorted(moved)
    report["left"] = sorted(left)
    report["problems"] = sorted(set(found["problems"]))
    return report


def _write_undo():
    """`undo.sh` from the journal of this migration: the moves back in the reverse order, then the originals from the
    backup (the on file, the stars, the tested sets, the platform copies, the compose texts that were written again), then
    the files that only the migration made: the marker, the witness, the plan, the new platform copies and the new links.
    Written whole at each point where the disk changes (the start, each move, the end), so that a person who goes back
    after a stop or a failure has a script that puts back what was moved up to the last move."""
    restores = [(f"{BACKUP}/{name}", f"{paths.LAB_DIR}/{name}") for name in ("on.yml", "starred.json", "tested.json")
                if os.path.lexists(_path(f"{BACKUP}/{name}"))]
    if os.path.isdir(_path(f"{BACKUP}/targets")):
        restores.append((f"{BACKUP}/targets", TARGETS))
    restores += [(f"{BACKUP}/units/{rel}", rel) for rel in _backed_up()]
    removes = [MARKER, STARTED, PLAN]
    base = _path(TARGETS)
    if os.path.isdir(base):
        removes += [f"{TARGETS}/{platform}/{name}" for platform, isdir in _entries(base) if isdir
                    for name, folder in _entries(os.path.join(base, platform))
                    if not folder and name.startswith(f"{paths.LAB_DIR}__")]
    entries = _journal()
    removes += [entry["path"] for entry in entries if entry.get("op") == "link" and isinstance(entry.get("path"), str)]
    file = paths.ROOT / moves.UNDO
    moves._parents(str(file))
    paths.write_atomic(file, moves.undo_script(entries=entries, restores=restores, removes=removes), mode=0o700)


def _write_marker(origin):
    """The commit point: `.lab/layout.json`, atomic, written last."""
    marker = {"layout": LAYOUT, "from": origin}
    if origin:
        marker["migrated"] = _now()
    _step("marker")
    _put(MARKER, _json(marker), mode=0o644)


def _part_b(default, plan, found):
    """The second part of the move, on the plan: the promoted items move, then the machine files, the junk and the empty
    folders; the stars follow; the on file is translated; the undo script, the report and the marker are written."""
    pairs = _sorted(plan["table"])
    fixups = [(f"{_install(default)}/{old}", new) for old, new in plan["promoted"]]
    _check_env(found)
    for step in (3, 4, 5):
        for op in plan["ops"]:
            if op["step"] == step:
                _do(op, found)
        if step == 3:
            _texts(_table_a(default), pairs, fixups, found)
    _step("empty")
    _remove_empty()
    _step("rekey")
    moves.rekey(plan["ids"])
    _switch(pairs, found)
    _step("undo")
    _write_undo()
    return _finish(plan, found)


def _finish(plan, found):
    """The report is written down, then the marker: a run that stops between them writes both again."""
    report = _final_report(plan, found)
    _step("report")
    _put(REPORT, _json({"schema": 1, "report": report}))
    _write_marker(1)
    return report


def _again(default, found):
    """The marker is there and a stale Lab wrote a legacy line, or a platform copy with a legacy name, after the move: the lines are translated again by the table the move froze, or by the catalog's units when it has
    none (a clone that had nothing to move). The on file as it was found is kept in the backup first."""
    plan = _object(PLAN)
    plan = plan if _valid_plan(plan, default) else None
    names = registry.units_of(default)
    rows = list(_table_a(default)) + [(f"{FOLDER[kind]}/{name}{'.yml' if kind == 'setup' else ''}",
                                       f"{_install(default)}/{FOLDER[kind]}/{name}{'.yml' if kind == 'setup' else ''}")
                                      for kind in paths.KINDS for name in names[kind]]
    pairs = _sorted(set(map(tuple, plan["table"])) | set(rows) if plan else rows)
    stale = _sha(_path(ON_FILE))
    if stale:
        _copy_once(_path(ON_FILE), _path(f"{BACKUP}/on.yml.{stale[:8]}"))
    _switch(pairs, found)
    _step("undo")
    _write_undo()
    report = _final_report(plan, found)
    _put(REPORT, _json({"schema": 1, "report": report}))
    return report


# ---------------------------------------------------------------------------------------------------------------------
# The entry points
# ---------------------------------------------------------------------------------------------------------------------

def _guarded(work):
    """Runs `work` and records in this process whether the move failed (status() says so). A failure of the disk, a
    refusal of a primitive or of the registry is a failed move that can be tried again: the report says so and nothing was
    deleted. Anything else is a bug or a test stopping the run: it is recorded, then raised."""
    global _failed, _backed
    _backed = None   # the backup is looked at again for each run: it may be another folder, or one a person undid
    try:
        found = work()
    except (OSError, ValueError, moves.Refused, registry.CatalogError) as error:
        _failed = getattr(error, "message", None) or str(error) or type(error).__name__
        _say("failed", f"The Lab could not move your settings: {_failed}. Nothing was deleted.")
        found = _report()
        found["problems"].append(FAILED_LINE)
        _keep_undo()
        return found
    except BaseException:
        _failed = "stopped"
        raise
    _failed = None
    return found


def _keep_undo():
    """After a failure, the script for a person who goes back before the next try, as far as the journal goes. Best effort:
    a disk that failed once may fail again, and a tree where no migration started has none to keep."""
    if os.path.isfile(_path(STARTED)):
        with contextlib.suppress(Exception):
            _write_undo()


def _run():
    """What ensure() and migrate() do, under the locks of every change to the catalogs: nothing on a
    tree where git tracks the content; the marker alone for a clone with nothing to move; the marker re-checked for a stale
    line; else Part A, and Part B when the default catalog is installed and valid."""
    if _inactive_said() or (_marker() is not None and not _signals(first=False)):
        return _report()   # nothing to do, and no lock taken, no file made, to do it
    with _locked():
        found = _report()
        if _inactive():   # looked at again: another process may have finished while this one waited for the lock
            return found
        default = paths.default_ns()
        marker = _marker()
        signals = _signals(first=marker is None)
        if marker is not None:
            if signals and _settled(default):
                return _again(default, found)
            return found
        if not signals:
            _write_marker(0)
            return found
        _part_a(found)
        if not _settled(default):
            return _final_report(None, found)
        return _part_b(default, _plan(default), found)


def migrate_personal() -> dict:
    """Part A, offline: the person's own items move at once. Answers the report of `migrate`, with what it did
    only: nothing is moved when the content is tracked, the marker is there, or there is nothing to move."""
    def part():
        if _inactive_said() or _marker() is not None or not _signals(first=True):
            return _report()
        with _locked():
            found = _report()
            if _inactive() or _marker() is not None or not _signals(first=True):
                return found
            _part_a(found)
            return _final_report(None, found)
    return _guarded(part)


def migrate() -> dict:
    """Part B, once the default catalog is installed (it runs Part A first), idempotently: the rest moves. Answers
    {moved, personal, promoted, kept, left, held, turned_off, problems}: items by `kind:id`, paths of the old layout for
    `left`. Empty when there is nothing to do; a catalog that is not installed leaves Part A done and the layout waiting.
    A failure of the disk is recorded (status() says failed) and answered as a problem; the next call tries again."""
    return _guarded(_run)


def ensure() -> None:
    """Detects an old layout and moves what can be moved at once; it never touches the network. Called once by
    the server after it binds its port, before it serves. Starting the server comes first: a failure is recorded for
    status() and printed once, never raised. So is each problem the move reports (a `COMPOSE_FILE` to edit by hand, a
    place that was taken): the report is on disk, and this is where a person who starts the Lab from a terminal sees it."""
    try:
        for line in _guarded(_run)["problems"]:
            if line != FAILED_LINE:   # `_guarded` has said that one, with its reason
                _say(line, line)
    except Exception as error:  # noqa: BLE001 - a bug here must not keep the Lab from starting; status() says failed
        _say("ensure", f"The Lab could not look at the layout of this folder ({type(error).__name__}).")


def dismiss() -> None:
    """The person closed the notice: it is not shown again. The marker is written again with the time, which also changes
    what the project reads (it stats the marker), so the next state does not carry the notice."""
    if not _marker():
        return
    with _locked():
        marker = _marker()
        if marker and not marker.get("closed"):
            _put(MARKER, _json({**marker, "closed": _now()}), mode=0o644)


# What the Makefile prints and looks for (agreed with the Makefile's own tests): the guard that keeps a hand typed
# `docker compose` from failing on an on file that names folders that moved. The pattern is an ERE for `grep -E`; it is
# what _signal_s1 means for the lines the Lab writes, and a test holds the two together
GUARD = "This Lab was updated: run make update to move your settings."
