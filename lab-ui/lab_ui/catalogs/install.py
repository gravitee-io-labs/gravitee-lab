"""The files of the catalogs on this machine: staging a candidate, putting it in place, updating, going back, repairing,
removing, linking a working copy in place of a git install, and bringing all of it back to a whole state after a crash.
The only code that writes content files under `.lab/catalogs/<ns>`.

What a reader may rely on. A git install is complete or absent: it is built in a staging folder, `.stage-<ns>`, beside
it, that holds exactly the files judged, and is renamed whole to `<ns>`. An update deletes the files the new version
drops and replaces the ones that changed, one by one (each replacement atomic) while it holds the project lock, so a
reader that holds it never sees a mix, and writes `.installed.json` last: a manifest is the list of what the folder
holds, and the units of a catalog are only those it lists. Nothing is ever written, read for content or deleted that the
manifest does not list, except "Delete them too": a file under an install is content if and only if it is listed, and
anything else is the machine's (`.env`, `.state/`, `secrets/`, keys, certificates, whatever a container or a person put
there).

How a crash is survived. Every operation is a short list of steps that are each atomic, ordered so that a stop between
two of them leaves a state `recover()` can finish or undo, and each step is idempotent, judged by the digest of what is
on disk and not by what a previous run did. `lock.pending` ({op, from, to}) is set before the first file of an Add or an
Update is touched and cleared by the write that commits it. An Add is committed by `catalogs.json`, an Update, a Go
back, a Repair and the first download of a listed catalog by `catalogs.lock`, a Remove by `catalogs.json`. A pending
Update, Go back or Repair is finished (the store holds every object), a pending Add is finished when the install and the
store are whole and otherwise undone, a Remove is finished by the sweeper. `STEP` is the hook a test sets to stop a run
between two steps: it is called with the label of the step about to be taken, and what it raises ends the run there.
A folder that holds no manifest is never judged by the names of its files (a name the patterns do not know, `data/` or
`certs/ca.txt`, would read as content that nobody keeps): the marker `.kept` is written before the manifest goes (a Remove,
a download undone, a working copy linked) and into a folder that has no manifest before it is moved aside or restored
from, so whatever a stop leaves is all the machine's. A start without git goes on: an operation under way keeps its
record for a start that has git.

Labels, in order. stage: begin, written. add: begin, manifest, store, pending, install, file (for each file of a merge),
placed, lock, config, adopt, done. update, undo and repair: pending, apply, removed (each), file (each), manifest, lock,
refs, adopt, done. remove: config, store, files, marker, vacate, lock, stars, done. shadow: record, move (each), install,
link, config, lock, done. unshadow: record, link, install, then apply, file, removed and manifest of the repair it makes,
move (each), config, lock, done. recover: begin, pending, sweep, adopt, done.

The locks are the catalogs lock and the advisory lock of the other processes (`registry.LOCK`, `registry.flock`), then
`project.exclusive()` around the steps that change what a reader reads, in that order (, rule 11)."""

import contextlib
import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

from ..docker import compose
from ..library import moves, starred
from .. import paths
from . import defaults, gitx, manifest, registry, resolve, understood
from . import store as bare
from .registry import CatalogError

# A test sets it to a callable that takes the label of the step about to be taken and raises to stop the run there
STEP = None

MARKER = ".kept"   # left by a Remove that kept the settings (rule 12): exactly the machine files and this
SHADOW = ".shadow-{ns}.json"   # beside the installs: the machine entries a working copy took
LAB_FILES = (MARKER, paths.MANIFEST)   # the Lab's own files at the top of an install: neither content nor machine
AGE = 24 * 3600.0   # a stage, a store being added, a partial or a share folder older than this is a leftover (rule 12)
MAX_FILE = 4 * 1024 * 1024   # a file is read whole to be hashed: the filter keeps content under 2 MB
MANIFEST_LIMIT = 32 * 1024 * 1024   # as the registry reads one
DEEP = 40   # how far down an install is looked into for its machine files
WALK_LIMIT = 50000   # entries looked at in a folder catalog before it is `too_large`
ROOTS_LIMIT = 20   # the root commits a lock entry holds (registry): more are not recorded
WHATSNEW = 30   # the lines of What's new an update says: five for each item, as the notes of a release, up to this
READ_MEMORY = 256   # the versions whose reading `unknown_at` keeps: each is a commit, and a Lab sees a few of them a day

_NS = re.compile(paths.NS)
_REMOVED = re.compile(r"\.removed-({ns})-([0-9]{{8}}T[0-9]{{6}}Z)".format(ns=paths.NS))
_SHA = re.compile(r"[0-9a-f]{40}")
_SECRET_DIRS = (".state", "secrets")
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_UNITS = re.compile(r"(addons|playgrounds)/([a-z0-9-]+)/|setups/([a-z0-9-]+)\.yml")
_KIND = {"addons": "addon", "playgrounds": "playground"}

# What a start survives in one catalog and goes on: a refusal, the disk, a name that is none, a move the Lab will not
# make
TROUBLE = (CatalogError, OSError, ValueError, moves.Refused)

DAMAGED = "{name} changed on disk after it was installed. Repair {name}, then start it again."
LINKED = "This catalog is linked to a folder on this machine. Unlink it from a terminal first."
PARTIAL = ("Some files were created by Docker and could not be deleted. Remove the folder .lab/catalogs/{id} with "
           "administrator rights.")


def _step(label):
    if STEP is not None:
        STEP(label)


def _transient(error) -> bool:
    """Whether a refusal says something about this moment and not about the catalog: there is no usable git (or the
    shim of macOS without its tools), a command ran out of time, or the Lab UI is ending. Such an error never marks a
    catalog damaged and never drops what says an operation is under way: the next start has git, and finishes it."""
    detail = getattr(error, "detail", None) or {}
    return getattr(error, "code", None) in ("git_missing", "git_old", "timeout") \
        or gitx.ENDING in str(detail.get("output") or "")


def _relative(text):
    """A text with the project folder taken off the paths in it: what `detail` holds is for the log and the dialog's
    "Show output", and the place of the project on this machine is for neither."""
    for root in {os.fspath(paths.ROOT), os.path.realpath(paths.ROOT)}:
        text = text.replace(root + os.sep, "")
    return text


class Unsafe(CatalogError):
    """A write the Lab refuses because something other than the Lab changed the folder it was to write in: a link where
    a folder must be, a file where one is to be made, a folder where a file goes. Nothing is written. It is the
    `damaged` of the catalogue of errors (the folder changed on disk); `detail["why"]` says what was met, with paths from the project
    folder."""

    def __init__(self, why, ns=None):
        name = _called(ns) if ns else "This catalog"
        super().__init__("damaged", DAMAGED.format(name=name), 409, why=_relative(why), id=ns)


def _called(ns):
    """The name a person knows catalog `ns` by: what the lock cached of its catalog.yml, else its namespace."""
    try:
        return registry.lock_of(ns).get("name") or ns
    except ValueError:
        return ns


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _compact(offset=0):
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(time.time() + offset))


@contextlib.contextmanager
def _guard():
    """The two locks of an operation, in their order: this process's, then the other processes'."""
    with registry.LOCK, registry.flock():
        yield


@contextlib.contextmanager
def _exclusive():
    """The project lock, for the steps that change what a reader reads. Taken after the catalogs lock, never before.
    Nothing inside may ask the project for anything: `project.loaded()` holds the same lock."""
    from ..library import project   # here and not above: the project reads this package
    held = getattr(project, "exclusive", None)
    with (held() if held else contextlib.nullcontext()):
        yield


# ---------------------------------------------------------------- the disk, without following a link

def _lstat(path):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _real_dir(path) -> bool:
    info = _lstat(path)
    return info is not None and stat.S_ISDIR(info.st_mode)


def _parts(rel) -> List[str]:
    """The components of a relative POSIX path inside an install; ValueError for anything that could leave it."""
    if not isinstance(rel, str) or not rel or "\0" in rel or rel.startswith("/") or "\\" in rel:
        raise ValueError(f"not a path inside a catalog: {rel!r}")
    parts = rel.split("/")
    if any(part in ("", ".", "..") or len(part.encode("utf-8", "surrogateescape")) > 255 for part in parts):
        raise ValueError(f"not a path inside a catalog: {rel!r}")
    return parts


def _owner(path) -> Optional[str]:
    """The namespace a path under .lab/catalogs belongs to (a stage folder is its catalog's), for a message."""
    try:
        first = os.path.relpath(path, os.path.join(paths.ROOT, paths.CATALOGS_DIR)).split(os.sep)[0]
    except ValueError:
        return None
    first = first[len(".stage-"):] if first.startswith(".stage-") else first
    return first if _NS.fullmatch(first) else None


def _open_dir(root, parts, create, follow_root=False) -> Optional[int]:
    """A descriptor for the folder `root/parts`, every component opened without following a link, so that what a
    container planted in a state folder can never redirect a write. The missing ones are made 0755 when `create` (None
    when they are not and it is not). A link or a file where a folder must be is `Unsafe`. The caller closes it."""
    flags = _DIR if not follow_root else _DIR & ~os.O_NOFOLLOW
    try:
        fd = os.open(os.fspath(root), flags)
    except FileNotFoundError:
        if create:
            raise
        return None
    except OSError as error:
        raise Unsafe(f"{root} is not a folder: {error.strerror}", _owner(root)) from None
    try:
        for part in parts:
            try:
                below = os.open(part, _DIR, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    os.close(fd)
                    return None
                try:
                    os.mkdir(part, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
                below = os.open(part, _DIR, dir_fd=fd)
                os.fchmod(below, 0o755)   # whatever the umask: the Lab UI writes a .env beside a unit (rule 7)
            except OSError as error:   # a link (ELOOP) or a file (ENOTDIR)
                raise Unsafe(f"{part} is not a folder: {error.strerror}", _owner(root)) from None
            os.close(fd)
            fd = below
        return fd
    except BaseException:
        os.close(fd)
        raise


def safe_write(root: Path, rel: str, data: bytes, mode: int) -> None:
    """Writes `data` to `root/rel` as a new file that is read-only: installed content is, so the file has `mode` without
    its write bits (0o644 gives 0o444, 0o755, for a file git says is executable, gives 0o555), whatever the umask. Every
    folder between `root` and the file is looked at without following a link and made when it is missing; `root` itself
    must be a real folder. The file is made with `O_CREAT|O_EXCL|O_NOFOLLOW`: one that is already there, a link
    included, is refused (`Unsafe`, which is the `damaged` of the catalogue of errors), and nothing is written through it. A write
    that fails halfway leaves no file."""
    mode &= ~0o222
    parts = _parts(rel)
    directory = _open_dir(root, parts[:-1], True)
    try:
        try:
            fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                         mode, dir_fd=directory)
        except OSError as error:
            raise Unsafe(f"{rel} cannot be made: {error.strerror}", _owner(root)) from None
        try:
            os.fchmod(fd, mode)   # whatever the umask
            with os.fdopen(fd, "wb", closefd=False) as out:
                out.write(data)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(parts[-1], dir_fd=directory)
            raise
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def safe_replace(src: Path, dst: Path) -> None:
    """Moves the file `src` (a regular file, never a link) over `dst`, which is under `.lab/catalogs/`: every folder
    between there and `dst` is looked at without following a link and made when it is missing, then `os.replace` swaps
    the file in one step, so a reader sees the old file or the new one. `dst` may be a file or a link (the link is
    replaced, never followed); a folder in its place is `Unsafe`. A path outside `.lab/catalogs` is a ValueError."""
    info = _lstat(src)
    if info is None or not stat.S_ISREG(info.st_mode):
        raise Unsafe(f"{src} is not a regular file", _owner(src))
    base = os.path.join(os.fspath(paths.ROOT), paths.CATALOGS_DIR)
    rel = os.path.relpath(os.fspath(dst), base)
    if rel.startswith(".."):
        raise ValueError(f"{dst} is not under {paths.CATALOGS_DIR}")
    parts = _parts(rel.replace(os.sep, "/"))
    directory = _open_dir(base, parts[:-1], True, follow_root=True)
    try:
        try:
            os.replace(os.fspath(src), parts[-1], dst_dir_fd=directory)
        except OSError as error:
            raise Unsafe(f"{rel} cannot be replaced: {error.strerror}", parts[0]) from None
    finally:
        os.close(directory)


def _unlink(root, rel) -> bool:
    """Removes the file or link `root/rel` (never what a link points to) when every folder above it is a real folder;
    True when something was removed. A path that cannot be a path of an install is left."""
    try:
        parts = _parts(rel)
        directory = _open_dir(root, parts[:-1], False)
    except (ValueError, Unsafe):
        return False
    if directory is None:
        return False
    try:
        info = os.lstat(parts[-1], dir_fd=directory)
        if stat.S_ISDIR(info.st_mode):
            return False
        os.unlink(parts[-1], dir_fd=directory)
        return True
    except FileNotFoundError:
        return False
    finally:
        os.close(directory)


def _remove_tree(path) -> int:
    """Removes a file, a link or a folder with all it holds, without following a link and without stopping at what it
    cannot remove (a file Docker made as root): answers how many entries are left."""
    left = 0
    info = _lstat(path)
    if info is None:
        return 0
    if not stat.S_ISDIR(info.st_mode):
        try:
            os.unlink(path)
            return 0
        except OSError:
            return 1
    stack = [(os.fspath(path), False)]
    while stack:
        folder, visited = stack.pop()
        if visited:
            try:
                os.rmdir(folder)
            except OSError:
                left += 1
            continue
        stack.append((folder, True))
        try:
            names = os.listdir(folder)
        except OSError:
            left += 1
            continue
        for name in names:
            child = os.path.join(folder, name)
            entry = _lstat(child)
            if entry is None:
                continue
            if stat.S_ISDIR(entry.st_mode):
                stack.append((child, False))
            else:
                try:
                    os.unlink(child)
                except OSError:
                    left += 1
    return left


def _clear(path):
    """What stands at `path` removed, a link as a link; a folder that cannot be removed wholly is left."""
    _remove_tree(path)


def _safe(rel) -> bool:
    try:
        _parts(rel)
        return True
    except ValueError:
        return False


def _prune(root, rels):
    """Removes the folders that held the files `rels` and are empty now, bottom up, never `root` itself, and nothing for
    a path that is not one of an install."""
    folders = {os.path.dirname(rel) for rel in rels if _safe(rel)}
    for rel in sorted({parent for folder in folders for parent in _ancestors(folder)}, key=lambda one: -one.count("/")):
        try:
            os.rmdir(os.path.join(root, rel))
        except OSError:
            pass


def _ancestors(rel):
    """`a/b/c` and its parents `a/b` and `a`, not the empty path."""
    found = []
    while rel:
        found.append(rel)
        rel = os.path.dirname(rel)
    return found


# ---------------------------------------------------------------- what is on disk

def _read(path, limit=MAX_FILE) -> Tuple[bytes, os.stat_result]:
    """The content of a regular file, never through a link; `Unsafe` for anything else or a file over `limit`."""
    try:
        fd = os.open(os.fspath(path), os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
                     | getattr(os, "O_CLOEXEC", 0))
    except OSError as error:
        raise Unsafe(f"{path} cannot be read: {error.strerror}", _owner(path)) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise Unsafe(f"{path} is not a regular file of a size a catalog has", _owner(path))
        with os.fdopen(fd, "rb", closefd=False) as source:
            return source.read(limit + 1), info
    finally:
        os.close(fd)


def _oid(data: bytes) -> str:
    """The id git gives a file of this content."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def _perm(entry) -> int:
    return 0o555 if entry.mode == "100755" else 0o444


def _audit(root, kept: List[bare.Entry]) -> Dict[str, str]:
    """{path: sha256} of the files of a staging folder, after proving that it holds exactly `kept`: every file with the
    id and the mode git gave it, no other file, no link, no other folder. What was judged is what is installed. The
    manifest the stage may hold is not looked at. `Unsafe` for anything else."""
    root = os.fspath(root)
    expected = {entry.path: entry for entry in kept}
    folders = {parent for path in expected for parent in _ancestors(os.path.dirname(path))}
    found = {}
    if not _real_dir(root):
        raise Unsafe("the staging folder is not a folder", _owner(root))
    for here, names, files in os.walk(root, followlinks=False):
        rel = os.path.relpath(here, root)
        for name in names + files:
            if stat.S_ISLNK(os.lstat(os.path.join(here, name)).st_mode):
                raise Unsafe(f"a link in the staging folder: {name}", _owner(root))
        if rel != "." and rel.replace(os.sep, "/") not in folders:
            raise Unsafe(f"a folder that was not judged: {rel}", _owner(root))
        for name in files:
            path = (name if rel == "." else f"{rel}/{name}").replace(os.sep, "/")
            if path == paths.MANIFEST and rel == ".":
                continue
            entry = expected.get(path)
            if entry is None:
                raise Unsafe(f"a file that was not judged: {path}", _owner(root))
            data, info = _read(os.path.join(here, name))
            if _oid(data) != entry.oid or len(data) != entry.size or stat.S_IMODE(info.st_mode) != _perm(entry):
                raise Unsafe(f"{path} is not what was judged", _owner(root))
            found[path] = hashlib.sha256(data).hexdigest()
    gone = sorted(set(expected) - set(found))
    if gone:
        raise Unsafe(f"{gone[0]} is missing from the staging folder", _owner(root))
    return found


def _rec(files, path) -> dict:
    """The record of a manifest for a path: {} when the manifest has none or a thing that is not one (a manifest is a
    file anybody can edit)."""
    record = files.get(path)
    return record if isinstance(record, dict) else {}


def _document(ns, commit, kept, ignored, shas) -> dict:
    """.installed.json: the commit, every file with its git id, mode, size and sha256, the digest of every
    unit and the number of files that were not installed. The digests are `store.unit_digests` of the same files, so
    they can be had for any commit from a listing alone; the id of catalog.yml is in `files`."""
    files = {entry.path: {"mode": entry.mode, "oid": entry.oid, "sha256": shas[entry.path], "size": entry.size}
             for entry in sorted(kept, key=lambda one: one.path)}
    units = {key: digest for key, digest in bare.unit_digests(kept).items() if key != "@catalog"}
    return {"schema": 1, "catalog": ns, "commit": commit, "files": files, "units": units, "ignored": int(ignored)}


def _write_manifest(root, document):
    """Writes .installed.json into `root` (0644, replaced whole)."""
    target = Path(root) / paths.MANIFEST
    try:
        paths.write_atomic(target, json.dumps(document, indent=2, ensure_ascii=False) + "\n", mode=0o644)
    except OSError as error:
        raise Unsafe(f"{paths.MANIFEST} cannot be written: {error.strerror}", _owner(root)) from None


# ---------------------------------------------------------------- staging

class Staged(NamedTuple):
    """A candidate in its staging folder: the namespace it will have (the placeholder `new-<ticket>` until an Add knows
    it), the folder, the commit, the files that were written (git: `store.Entry`), how many files were left out, the
    kind (`git` or `folder`) and, for git, the bare store the commit is in."""
    ns: str
    path: Path
    commit: str
    kept: list
    ignored: int
    kind: str
    store: Optional[Path] = None


def _write_stage(target, store_path, kept) -> Dict[str, str]:
    """Writes the files `kept` of a store into the fresh folder `target` (replacing what stood there), catalog.yml first
    so that the id is known, every file with `safe_write` and read-only. Answers {path: sha256}. A failure removes the
    folder it made; a stop (not an error) leaves it, which is what the sweeper is for."""
    _clear(target)
    os.makedirs(os.path.dirname(os.fspath(target)), exist_ok=True)
    os.mkdir(target, 0o755)
    os.chmod(target, 0o755)
    ordered = sorted(kept, key=lambda entry: (entry.path != manifest.FILE, entry.path))
    shas = {}
    try:
        for entry, (oid, data) in zip(ordered, bare.blobs(store_path, [entry.oid for entry in ordered])):
            if oid != entry.oid or len(data) != entry.size or _oid(data) != entry.oid:
                raise gitx.fail("fsck", output=f"{entry.path} is not the file git listed")
            safe_write(target, entry.path, data, _perm(entry))
            shas[entry.path] = hashlib.sha256(data).hexdigest()
        if len(shas) != len(ordered):   # a store that gave fewer files than it listed is not a store
            raise gitx.fail("fsck", output="fewer files than listed")
    except Exception:
        _clear(target)
        raise
    return shas


def _is_regular(path) -> bool:
    info = _lstat(path)
    return info is not None and stat.S_ISREG(info.st_mode)


def stage(address: gitx.Address, ns: str, store: Optional[Path], commit: str, kept: list, ignored: int = 0) -> Staged:
    """The candidate in `.lab/catalogs/.stage-<ns>/` (`ns` may be the placeholder `new-<ticket>` of an Add
    that does not know its namespace yet; `rename_stage` gives it one). For git, every file of `kept` (what
    `store.selected` kept of the listing of `commit` in `store`) is written with `safe_write`, catalog.yml first,
    read-only; nothing is run, no hook, no filter, no checkout. For a folder the stage is a link to the folder: nothing
    is copied. A stage that an earlier attempt left is replaced, and the leftovers older than a day are swept first
    (rule 12). `ignored` is the number of files the filter left out, kept for the manifest."""
    target = registry.stage_dir(ns)
    _sweep_stale()
    _step("stage:begin")
    if address.kind == "folder":
        _clear(target)
        os.makedirs(os.path.dirname(os.fspath(target)), exist_ok=True)
        if not _real_dir(address.path):
            raise gitx.fail("folder_missing", address, path=address.display)
        if not _is_regular(os.path.join(address.path, manifest.FILE)):
            raise gitx.fail("folder_not_catalog", address, path=address.display)
        try:
            os.symlink(address.path, target)
        except OSError:
            raise CatalogError("symlink_unsupported",
                               "The Lab cannot link folders on this system. Use WSL 2.", 400) from None
        return Staged(ns, target, "", [], 0, "folder", None)
    if store is None or not commit:
        raise ValueError("a git stage needs the store and the commit")
    _write_stage(target, store, kept)
    _step("stage:written")
    return Staged(ns, target, commit, list(kept), int(ignored), "git", Path(store))


def rename_stage(staged: Staged, ns: str) -> Staged:
    """The stage of an Add under the namespace its catalog.yml declares, `.stage-<ns>`. One rename."""
    target = registry.stage_dir(ns)
    if os.path.abspath(staged.path) == os.path.abspath(target):
        return staged._replace(ns=ns)
    _clear(target)
    os.rename(staged.path, target)
    return staged._replace(ns=ns, path=target)


def walk(folder: str) -> list:
    """The files of a folder catalog as `store.Entry` (mode from `lstat`, a link as `120000`, a file that is neither as
    `010000`), only below `catalog.yml`, `addons/`, `setups/` and `playgrounds/`, so that `store.selected` filters them
    with the rules of a git catalog. The object id is zeros: a folder has none. A link is never
    followed. More than WALK_LIMIT entries is `too_large`."""
    folder = os.fspath(folder)
    found, count = [], 0
    todo = [name for name in ("catalog.yml", "addons", "setups", "playgrounds")]
    while todo:
        rel = todo.pop(0)
        info = _lstat(os.path.join(folder, rel))
        if info is None:
            continue
        count += 1
        if count > WALK_LIMIT:
            raise gitx.fail("too_large")
        if stat.S_ISDIR(info.st_mode):
            if rel.count("/") >= 10:   # deeper than a catalog goes: one entry that the filter counts as too deep
                found.append(bare.Entry("100644", "0" * 40, 0, rel + "/" + "d/" * 2 + "x"))
                continue
            try:
                todo[0:0] = sorted(f"{rel}/{name}" for name in os.listdir(os.path.join(folder, rel)))
            except OSError:
                continue
        elif stat.S_ISLNK(info.st_mode):
            found.append(bare.Entry("120000", "0" * 40, info.st_size, rel))
        elif stat.S_ISREG(info.st_mode):
            found.append(bare.Entry("100755" if info.st_mode & 0o111 else "100644", "0" * 40, info.st_size, rel))
        else:
            found.append(bare.Entry("010000", "0" * 40, 0, rel))
    return found


# ---------------------------------------------------------------- reading an install: content or machine

def _machine_pattern(rel) -> bool:
    """Whether a path is a machine file by its name, for a folder that has no manifest to say: `.env`, `.env.*`
    but `.env.example`, a folder `.state` or `secrets` and what is in one, a key or a certificate."""
    parts = rel.lower().split("/")
    name = parts[-1]
    return (any(part in _SECRET_DIRS for part in parts) or name == ".env"
            or (name.startswith(".env.") and name != ".env.example") or name.endswith(bare.KEY_SUFFIXES))


def _scan(root, listed: Optional[Set[str]]) -> Tuple[int, List[str], bool]:
    """(how many content files, the machine entries, whether there are any) of an install folder. The machine entries
    are the top-most paths that hold no content: a file, a link, or a folder whose files are all machine. `listed` is
    the set of content paths (the manifest's); None for a folder with no manifest, where a name tells, and anything that
    is not a machine name counts as content. A `.state` or `secrets` folder is one entry and is not looked into. A link
    is a file."""
    root = os.fspath(root)

    def look(rel, depth):
        content, tops, machine = 0, [], False
        here = os.path.join(root, rel) if rel else root
        try:
            names = sorted(os.listdir(here))
        except OSError:   # what cannot be listed cannot be vouched for
            return 0, [rel] if rel else [], True
        for name in names:
            path = f"{rel}/{name}" if rel else name
            if not rel and name in LAB_FILES:
                continue
            info = _lstat(os.path.join(root, path))
            if info is None:
                continue
            if stat.S_ISDIR(info.st_mode):
                if name.lower() in _SECRET_DIRS or depth >= DEEP:
                    tops.append(path)
                    machine = True
                    continue
                inner, below, any_machine = look(path, depth + 1)
                content += inner
                tops += [path] if inner == 0 and any_machine else below
                machine = machine or any_machine
            elif (path not in listed) if listed is not None else _machine_pattern(path):
                tops.append(path)
                machine = True
            else:
                content += 1
        return content, tops, machine
    return look("", 0)


def _manifest_in(root, ns) -> Optional[dict]:
    """The manifest inside the folder `root` as the registry would accept it for catalog `ns`, whatever
    commit it is for, or None: a folder can be an install, a staging folder or a `.removed-<ns>-*` folder, and each says
    what it holds in its own file."""
    try:
        data, _ = _read(os.path.join(root, paths.MANIFEST), MANIFEST_LIMIT)
        found = json.loads(data.decode("utf-8-sig"))
    except (Unsafe, ValueError, RecursionError):
        return None
    if not (isinstance(found, dict) and found.get("schema") == 1 and found.get("catalog") == ns
            and isinstance(found.get("commit"), str) and isinstance(found.get("files"), dict)
            and isinstance(found.get("units"), dict)):
        return None
    return found


def _listed(ns, root) -> Optional[Set[str]]:
    """The content paths of the folder `root` of catalog `ns`: its manifest's files; nothing is content in a folder that
    holds the `.kept` marker and no manifest; None when nothing says (a name tells)."""
    found = _manifest_in(root, ns)
    if found is not None:
        return set(found["files"])
    return set() if os.path.lexists(os.path.join(root, MARKER)) else None


def _machine(ns, root) -> List[str]:
    """The machine entries of an install folder (`_scan`)."""
    return _scan(root, _listed(ns, root))[1]


def _mark(root) -> None:
    """Writes the marker `.kept` into the folder `root`: from then on every entry of it is the machine's, whatever its
    name. A folder that holds neither a manifest nor the marker is judged by names (`_machine_pattern`), and a name the
    patterns do not know (`data/`, `certs/ca.txt`) reads as content that is nobody's to keep: so the marker is written
    before the manifest that vouched for the folder goes, and a folder that no manifest vouches for gets it before it is
    moved or emptied. Idempotent."""
    paths.write_atomic(Path(root) / MARKER, "", mode=0o600)


def _vouch(ns, root) -> None:
    """Marks a folder that no manifest vouches for and that holds no marker yet: nothing in it is judged by its name
    from then on (`_mark`)."""
    if _manifest_in(root, ns) is None and not os.path.lexists(os.path.join(root, MARKER)):
        _mark(root)


def _vacate(ns, root, keep_marker=False) -> None:
    """Removes the content files an install folder lists, its manifest and, unless `keep_marker`, its marker, and the
    folders that held only them: what is left is the machine's. Idempotent."""
    found = _manifest_in(root, ns)
    rels = [rel for rel in (found["files"] if found else ()) if _safe(rel)]
    for rel in rels:
        _unlink(root, rel)
    _unlink(root, paths.MANIFEST)
    if not keep_marker:
        _unlink(root, MARKER)
    _prune(root, rels)


def _leave(ns, root, label=None, vacating=None) -> bool:
    """Takes the content out of an install folder and leaves what is the machine's, with the marker that says so, which
    is written first (after the step `label`) so that a stop between the manifest going and the marker coming never
    leaves a folder that names must judge. The step `vacating` follows the marker: a stop there leaves a marked folder
    whose content is still in it, which the sweeper empties. A folder with no manifest and no marker is marked and its
    content is not taken out: nothing says what is content in it. The folder goes when nothing is left. Answers whether
    something stays. Idempotent."""
    protect = _listed(ns, root) is None or bool(_machine(ns, root))
    if protect:
        if label:
            _step(label)
        _mark(root)
        if vacating:
            _step(vacating)
    _vacate(ns, root, keep_marker=protect)
    if protect and _machine(ns, root):
        return True
    _clear(root)
    return False


def _aside(ns, root) -> str:
    """Renames an install folder that cannot be merged to `.removed-<ns>-<utc>` beside it, where its machine files wait
    for the next Add, and answers the new path. The newest of these is merged into the install at an Add (rule 12). A
    folder that no manifest vouches for is marked first (`_mark`): nothing in it is judged by its name, so what
    `_restore` cannot place stays where it is and the folder is never deleted with a file of the person's in it."""
    _vouch(ns, root)
    offset = 0
    while True:
        target = os.path.join(os.path.dirname(os.fspath(root)), f".removed-{ns}-{_compact(offset)}")
        if not os.path.lexists(target):
            os.rename(root, target)
            return target
        offset += 1


def _removed_dirs(ns) -> List[str]:
    """The `.removed-<ns>-<utc>` folders of a catalog, oldest first."""
    base = os.path.join(paths.ROOT, paths.CATALOGS_DIR)
    try:
        names = os.listdir(base)
    except OSError:
        return []
    found = [(match.group(2), name) for name in names for match in [_REMOVED.fullmatch(name)]
             if match and match.group(1) == ns and _real_dir(os.path.join(base, name))]
    return [os.path.join(base, name) for _, name in sorted(found)]


def _move_in(source, root, rel, moved):
    """Moves `source/rel` to `root/rel`, never over what is there. A folder that is in both places (a unit's folder that
    holds the new content on one side and the machine's files on the other) is merged entry by entry, except a `.state`
    or `secrets` folder, which only moves whole; a conflict stays where it was. Adds the paths it moved to `moved`."""
    here, target = os.path.join(source, rel), os.path.join(root, rel)
    if not os.path.lexists(target):
        try:
            directory = _open_dir(root, _parts(rel)[:-1], True)
        except (ValueError, Unsafe):
            return
        try:
            os.rename(here, _parts(rel)[-1], dst_dir_fd=directory)
            moved.append(rel)
        except OSError:   # a folder only root can move: it stays, and is not lost
            pass
        finally:
            os.close(directory)
    elif _real_dir(here) and _real_dir(target) and os.path.basename(rel).lower() not in _SECRET_DIRS:
        for child in sorted(os.listdir(here)):
            _move_in(source, root, f"{rel}/{child}", moved)


def _restore(ns, root) -> List[str]:
    """Moves the machine entries of the newest `.removed-<ns>-<utc>` folder into the install `root`, each to the path it
    had, never over what is there (a conflict stays where it was). The folder is removed when nothing of the machine is
    left in it. Idempotent: run again after a stop, it moves what is left. Answers the paths it moved."""
    removed = _removed_dirs(ns)
    if not removed or not _real_dir(root):
        return []
    folder, moved = removed[-1], []
    try:
        _vouch(ns, folder)   # one a person or an older run made: what it holds is theirs, not judged by names
    except OSError:   # a folder the Lab cannot mark is left as it is: nothing is moved out of it or deleted from it
        return []
    for rel in _machine(ns, folder):
        _move_in(folder, root, rel, moved)
    if not _machine(ns, folder):
        _clear(folder)
    return moved


def _collides(root, kept, going) -> bool:
    """Whether a file of `kept` would land on something that is not a file of the old install (`going`): a machine file
    where a content file goes, a file or a link where a folder must be, a folder where a file goes."""
    for entry in kept:
        parts, here = entry.path.split("/"), os.fspath(root)
        for number, part in enumerate(parts):
            here = os.path.join(here, part)
            info = _lstat(here)
            if info is None:
                break
            if number < len(parts) - 1:
                if not stat.S_ISDIR(info.st_mode):
                    return True
            elif stat.S_ISDIR(info.st_mode) or entry.path not in going:
                return True
    return False


def _target(ns, kept) -> str:
    """What the install folder of `ns` allows for a new install, after making it so: "rename" when nothing is there (the
    stage is renamed to it) and "merge" when a folder is there that holds only the machine's files and what the previous
    install listed (a Remove that kept the settings, a half-made install: the stage is merged into it in place, and the
    machine files never move). Anything else a folder holds, or any path in the way, sends the folder aside first
    (`_aside`): nothing is ever deleted or overwritten for lack of knowing what it is. A link or a file is never
    replaced: it is `name_taken`. A stop in the merge leaves a folder with no manifest and no marker, which the sweeper
    sets aside marked, so the units of a half-made install are not seen and the machine's files are not lost."""
    root = registry.install_dir(ns)
    info = _lstat(root)
    if info is None:
        return "rename"
    if not stat.S_ISDIR(info.st_mode):
        raise taken(ns)
    listed = _listed(ns, root)
    content = _scan(root, listed)[0]
    known = _manifest_in(root, ns)
    going = set(known["files"]) if known else set()
    if (listed is None and content) or _collides(root, kept, going):
        _aside(ns, root)
        return "rename"
    _vacate(ns, root)
    return "merge"


# ---------------------------------------------------------------- adding a catalog

def _free(ns, entry, held, address):
    """Whether `ns` can be added now: an entry that is a download that has not ended is the provision of a
    listed catalog and is let through; any other entry is `already_added`; the namespace must pass the registry's rules
    (a listed one is accepted at its listed address only), and no other catalog may have this address."""
    if entry is not None:
        if entry.get("kind") == "git" and not held.get("pin"):
            return
        raise CatalogError("already_added", f"Already added as {_called(ns)}.", 409, id=ns)
    others = registry.entries()
    shown = registry.display_address(address.url if address.kind == "git" else address.path)
    for other in others:
        there = defaults.effective(other)[0] if other["kind"] == "git" else other.get("path")
        if shown and registry.display_address(there or "") == shown:
            raise CatalogError("already_added", f"Already added as {_called(other['id'])}.", 409, id=other["id"])
    listed = {row["id"] for row in defaults.read() if registry.display_address(row["url"]) == shown}
    registry.check_namespace(ns, taken=[one["id"] for one in others], listed=listed)


def taken(ns) -> CatalogError:
    """A link or a file stands at the name of a catalog: it is never replaced."""
    return CatalogError("name_taken", f"Another catalog already uses the name {ns}. Remove it first, or ask the owner "
                        "of this one to rename it.", 409, id=ns)


def not_expected(called=None) -> CatalogError:
    """`wrong_catalog` (the catalogue of errors): `called` is how the address is named in a line (`owner/repo`, or the folder as the
    person wrote it); a catalog the Lab cannot name is "This repository"."""
    return CatalogError("wrong_catalog", f"{called or 'This repository'} is not the catalog this Lab expects. Run make "
                        "update, then try again.", 409)


def _check_meta(ns, meta, called=None):
    if meta.get("id") != ns:
        raise not_expected(called)


def _address_name(address) -> Optional[str]:
    """How the lines of the catalogue of errors name the repository or folder at an address."""
    return (address.repo or address.display or None) if address is not None else None


def _entry_name(entry) -> Optional[str]:
    """The same for a catalog the machine has: from the address its entry holds."""
    try:
        return _address_name(gitx.parse_address(entry["url"] if entry["kind"] == "git" else entry["path"]))
    except (CatalogError, KeyError, TypeError):
        return registry.display_address(str(entry.get("url") or entry.get("path") or "")) or None


def _lock_fields(meta) -> dict:
    return {"name": meta["name"], "version": meta["version"], "lab": meta["lab"]}


def _config_with(entry):
    config = registry.read_config() or {"schema": 1, "catalogs": []}
    config["catalogs"] = [one for one in config["catalogs"] if one["id"] != entry["id"]] + [entry]
    registry.write_config(config)


def commit_add(staged: Staged, address: gitx.Address, branch: Optional[str], audience: str, roots: list,
               meta: Optional[dict] = None, host: bool = False, auto: bool = True) -> dict:
    """The install of a catalog, with the first download of a listed catalog (whose entry exists already) as the same
    pipeline. Under the locks: the namespace is still free and the stage is what was judged (every file has the id and
    the mode git gave it, no other file is there); the manifest is written into the stage; the store is renamed to
    `<ns>.git` with `refs/lab/pin` at the commit (already there for a download that fetched into its own store); the
    lock entry is written with `pin` null and `pending` {add, null, commit} so that a stop from here on is recognized;
    the stage is renamed to the install (or merged into the folder a Remove that kept the settings left, and the machine
    files of the newest `.removed-<ns>-*` are moved in); the lock is written with the pin and `pending` null (the commit
    point of a download); `catalogs.json` gets the entry (the commit point of an Add); the settings that wait for the
    catalog's units are adopted (step 18). `meta` is the validated catalog.yml of the stage (read when not given),
    `branch` the one the remote resolved (None or "" for the default one), `roots` the root commits seen at the fetch.
    A folder stage is renamed to the link and recorded. Answers {entry, lock, adopted}."""
    ns = staged.ns
    if audience not in ("public", "private") or not all(isinstance(one, str) and _SHA.fullmatch(one) for one in roots):
        raise ValueError("an audience is public or private, and the roots are commit ids")   # before anything is made
    with _guard():
        _step("add:begin")
        entry, held = registry.entry(ns), registry.lock_of(ns)
        _free(ns, entry, held, address)
        if not _real_dir(staged.path) and not os.path.islink(staged.path):
            raise CatalogError("ticket_expired", "This took too long. Start again.", 409)
        if staged.kind == "git":
            if staged.store is None or not _SHA.fullmatch(staged.commit or ""):
                raise ValueError("a git stage needs its store and its commit")
            shas = _audit(staged.path, staged.kept)   # what was judged is what is installed
        if meta is None:
            meta = manifest.read_catalog(os.path.relpath(Path(staged.path) / manifest.FILE, paths.ROOT), address)
        _check_meta(ns, meta, _address_name(address))
        if staged.kind == "folder":
            return _commit_folder(staged, address, entry, audience, meta, host)
        tip = bare.resolve(staged.store, "refs/lab/tip")
        if tip is None:
            raise bare.MissingObject("refs/lab/tip")
        _step("add:manifest")
        _write_manifest(staged.path, _document(ns, staged.commit, staged.kept, staged.ignored, shas))
        _step("add:store")
        final = registry.store_dir(ns)
        bare.set_ref(staged.store, "pin", staged.commit)
        if os.path.abspath(staged.store) != os.path.abspath(final):
            _clear(final)   # a store left by an attempt that never became a catalog
            os.rename(staged.store, final)
        _step("add:pending")
        registry.set_lock(ns, pin=None, previous=None, tip=tip, roots=sorted(roots), audience=audience,
                          installed=None, checked=None, error=None,
                          pending={"op": "add", "from": None, "to": staged.commit}, **_lock_fields(meta),
                          release=manifest.release_of(final, staged.commit, meta["version"]))
        with _exclusive():
            _step("add:install")
            _place(ns, staged)
        _step("add:placed")
        now = _utc()
        _step("add:lock")
        registry.set_lock(ns, pin=staged.commit, previous=None, installed=now, checked=now, error=None, pending=None)
        row = entry
        if entry is None:
            _step("add:config")
            row = {"id": ns, "kind": "git", "url": address.url, "auto": bool(auto), "added": now}
            asked = gitx.asked_branch(branch, address)
            if asked:
                row["branch"] = asked
            if host:
                row["host"] = True
            _config_with(row)
        _step("add:adopt")
        adopted = _adopted(ns, meta["moved_from"])
        _step("add:done")
        return {"entry": row, "lock": registry.lock_of(ns), "adopted": adopted}


def _place(ns, staged):
    """The stage becomes the install. Renamed whole when nothing stands there (and the machine files of the
    newest `.removed-<ns>-*` move in after); merged file by file, the manifest last, into a folder of machine files."""
    root, stage_dir = os.fspath(registry.install_dir(ns)), os.fspath(staged.path)
    if _target(ns, staged.kept) == "rename":
        os.rename(stage_dir, root)
        _restore(ns, root)
        return
    for entry in sorted(staged.kept, key=lambda one: one.path):
        _step("add:file")
        safe_replace(Path(stage_dir) / entry.path, Path(root) / entry.path)
    safe_replace(Path(stage_dir) / paths.MANIFEST, Path(root) / paths.MANIFEST)
    _clear(stage_dir)
    _unlink(root, MARKER)
    _restore(ns, root)   # a folder the sweeper renamed, beside the one a Remove left


def _commit_folder(staged, address, entry, audience, meta, host):
    """The Add of a folder catalog: the stage is a link, renamed to `.lab/catalogs/<ns>`, then the lock and the entry.
    """
    ns = staged.ns
    stage_dir = os.fspath(staged.path)
    if not os.path.islink(stage_dir) or os.readlink(stage_dir) != address.path:
        raise CatalogError("ticket_expired", "This took too long. Start again.", 409)
    root = os.fspath(registry.install_dir(ns))
    info = _lstat(root)
    if info is not None and stat.S_ISDIR(info.st_mode):
        _aside(ns, root)   # a Remove that kept the settings of a git catalog of this name: they wait for it
    elif info is not None:
        raise taken(ns)
    with _exclusive():
        _step("add:install")
        os.rename(stage_dir, root)
    _step("add:lock")
    registry.set_lock(ns, pin=None, previous=None, tip=None, roots=[], audience=audience, installed=None, checked=None,
                      error=None, pending=None, **_lock_fields(meta))
    row = entry
    if entry is None:
        _step("add:config")
        row = {"id": ns, "kind": "folder", "path": address.path, "added": _utc()}
        if host:
            row["host"] = True
        _config_with(row)
    _step("add:done")
    return {"entry": row, "lock": registry.lock_of(ns), "adopted": []}


def _declared(ns) -> Set[str]:
    """The `kind:name` a catalog says it received from the Lab repository (`moved_from`), read from its installed
    catalog.yml; none when it cannot be read."""
    try:
        return set(manifest.read_catalog(f"{paths.CATALOGS_DIR}/{ns}/{manifest.FILE}")["moved_from"])
    except CatalogError:
        return set()


def adopt(ns: str, moved_from: Optional[Iterable[str]] = None) -> List[dict]:
    """Gives the units of catalog `ns` the settings that waited for them (`moves.adopt_held`). Every unit
    of the first default catalog qualifies (an item that returns to it recovers its settings), any other catalog only
    the units it declares in `moved_from` (read from its catalog.yml when not given), and only a unit for which settings
    wait (a held folder, a star or a tested set under the bare id). Idempotent: `recover()` repeats it. Answers [{unit,
    files}] for the units that received something."""
    default = paths.default_ns()
    units = registry.units_of(ns)
    declared = None if moved_from is None else set(moved_from)
    found = []
    for kind in paths.KINDS:
        for name in units.get(kind, []):
            if not moves.has_held(kind, name):
                continue
            if ns != default:
                if declared is None:
                    declared = _declared(ns)
                if f"{kind}:{name}" not in declared:
                    continue
            result = moves.adopt_held(ns, kind, name)
            if result:
                found.append({"unit": f"{kind}:{name}", "files": result})
    return found


def _adopted(ns, moved_from) -> List[dict]:
    """`adopt` after a commit point: the catalog is added or updated whatever the settings that wait do, so a refusal of
    the move (a held folder that is a link) or an error of the disk is not the operation's. `recover()` tries again at
    the next start."""
    try:
        return adopt(ns, moved_from)
    except (moves.Refused, OSError):
        return []


# ---------------------------------------------------------------- what is on, and what holds an update back

def on_units(ns: str) -> Set[str]:
    """`kind:name` of the units of catalog `ns` that the on file turns on, directly or through what it includes (a
    playground and the add-ons and setups it includes, an item of another catalog and the unit of this one it includes).
    Read from the on file and the include lists of the files it names: the defence of the hold rule where a caller has
    not asked the running state, never a substitute for it."""
    found, seen, todo = set(), set(), [paths.ON_FILE]
    while todo and len(seen) < 2000:
        file = todo.pop()
        if file in seen:
            continue
        seen.add(file)
        target = resolve.target_of(file)
        if target and target[0] == ns:
            found.add(f"{target[1]}:{target[2]}")
        try:
            todo += compose.includes(file)
        except (OSError, ValueError):
            continue
    return found


def _held(ns, keys, context) -> List[str]:
    """The units among `keys` that are on or up: those the caller says (`context["on"]`, an iterable or a function that
    answers one) and those the on file turns on."""
    asked = (context or {}).get("on")
    asked = asked() if callable(asked) else asked
    return sorted(set(keys) & (set(asked or ()) | on_units(ns)))


def _turn_off(keys):
    name = sorted(keys)[0].partition(":")[2]
    return CatalogError("held", f"Turn off {name} first.", 409, units=sorted(keys))


# ---------------------------------------------------------------- updating and going back

def _git_entry(ns):
    """The entry and the lock entry of a git catalog that is installed and not linked; `linked` for a folder, `damaged`
    for a git catalog whose install does not read."""
    entry = registry.entry(ns)
    if entry is None:
        raise ValueError(f"{ns} is not a catalog of this machine")
    if entry["kind"] != "git" or registry.lock_of(ns).get("shadowed"):
        raise CatalogError("linked", LINKED, 409)
    return entry, registry.lock_of(ns)


def _need(ns, commit):
    """The commit is in the store: else `fsck`, which is what a repair fetches again for."""
    store_path = registry.store_dir(ns)
    if not os.path.isdir(store_path) or bare.resolve(store_path, commit) != commit:
        raise bare.MissingObject(commit)
    return store_path


def _unreadable(meta, contract, entry) -> Optional[CatalogError]:
    """The refusal (`lab_new`, `lab_old`) when the version staged is written for a level this Lab does not read, by the strict
    reading of its catalog.yml: `pick` chooses a commit from a tolerant reading that may be unsure of the level (an anchor,
    a number spelled another way) and takes such a version for one this Lab may read, so this is where it is known. None
    for a Lab whose level the caller did not give."""
    if not isinstance(contract, dict):
        return None
    return manifest.compat_error(meta["lab"], contract.get("contract", 1), contract.get("contract_min", 1),
                                 defaults.listed(entry))


_versions = {}   # (store, commit, what this Lab reads) -> what that version uses that this Lab does not read
_versions_lock = threading.Lock()


@contextlib.contextmanager
def probe(store_path, commit):
    """The version `commit` of a catalog of the store, written in a staging folder of its own for the length of the block
    (`.stage-read-<random>`, the files an install would write, read-only, as `stage` writes them), so that it is read as the
    catalog it would be: its files sit where a catalog's do, its includes and its anchors reach what they would installed.
    Answers the placeholder namespace of the folder, which is removed at the end."""
    ns = f"read-{secrets.token_hex(4)}"
    kept, _ = bare.selected(bare.listing(store_path, commit))
    target = registry.stage_dir(ns)
    try:
        _write_stage(target, store_path, kept)
        yield ns
    finally:
        _clear(target)


def unknown_at(store_path, commit) -> Optional[list]:
    """What the version `commit` of a published catalog uses that this Lab does not read: the keys of its catalog.yml that
    this Lab does not know, a `schema` or a legacy `lab` above the ones it reads, and what its units use (`project.staged_unknown`),
    each an item of `understood`. Read from the files of that version (`probe`), once for each version and each lock: a Lab that
    is updated reads it again. [] for a version whose catalog.yml is not a valid one: the strict reading at Add and at Update
    says why. None when this Lab could not tell (Docker did not answer in time, the version could not be written to be read):
    that is no answer, so it is not kept, and the next check reads the version again."""
    key = (os.fspath(store_path), commit, understood.fingerprint())
    with _versions_lock:
        if key in _versions:
            return list(_versions[key])
    from ..library import project   # here and not above: the project reads this package
    try:
        with probe(store_path, commit) as ns:
            try:
                meta = manifest.read_catalog(os.path.relpath(registry.stage_dir(ns) / manifest.FILE, paths.ROOT), published=True)
            except CatalogError as error:
                meta = None
                if error.code == "schema_new":
                    found = [("catalog.key", "schema")]
                elif compose.missing(str((error.detail or {}).get("output") or "")):
                    return None
                else:
                    found = []   # not a catalog this Lab reads at all: the strict reading refuses it with the reason
            if meta is not None:
                found = manifest.unread(meta)
                held = understood.lock()
                if held and isinstance(meta["lab"], int) and manifest.compat(meta["lab"], held.contract, held.contract_min) == "new":
                    found.append(("catalog.key", "lab"))   # a legacy level the tolerant reading could not be sure of
            units = project.staged_unknown(ns) if meta is not None else []
            if units is None:
                return None
            found += units
    except TROUBLE:
        return None
    with _versions_lock:
        if len(_versions) >= READ_MEMORY:
            _versions.clear()
        _versions[key] = list(found)
    return found


def readable(store_path):
    """What `manifest.pick` asks of a version of the catalog in the store: whether this Lab reads all it uses (`unknown_at`).
    A version it could not tell of is not one it reads, for now."""
    return lambda commit: unknown_at(store_path, commit) == []


def plan_update(ns: str, target: str, context: Optional[dict] = None) -> dict:
    """The plan of an update, for the files: `{"upToDate": True}` when `target` is the pin or changes nothing installable;
    else the plan. The candidate tree is staged in full at `.stage-<ns>` for the caller to judge. The plan: {ns, op
    (update, or undo when `target` is `lock.previous`), from, to, tip, name, version, lab, adds, updates, removes (unit
    keys `kind:name`), files {add, change, remove}, touched (what changed in each unit, `store.changed_parts`), rewritten,
    held (the changed or removed units that are on), release (the version of the release `target` is, or None), whatsnew
    (what is new in the units it changes, adds or removes, unit by unit, as the notes of a release say it: `store.notes`,
    each subject once), ignored, kept (the files written), stage, meta (the validated catalog.yml of the candidate), context}.
    `rewritten`: the history is not the one installed (the roots differ, or the pin is not an ancestor of the target; for a
    Go back, the two are not one history), unless the new roots are the ones in `context["vouched"]`; or `context["moved"]`,
    the release installed is a tag the owners moved to the target since, which an update does not take silently either. `context["on"]`, an iterable or a function: the units that are on or up, which the hold
    rule adds to those the on file turns on. `context["contract"]`, `{contract, contract_min}`: the level this Lab reads; a
    version written for another is refused (`lab_new`, `lab_old`) whatever `pick` made of it, and so is one whose catalog.yml
    has a key this Lab does not read (`lab_new`: it needs a newer Gravitee Lab), the staged folder cleared. The
    plan is applied by `apply_update`, which asks again."""
    context = dict(context or {})
    with _guard():
        _git_entry(ns)
        _settle(ns)
        held, old = registry.lock_of(ns), registry.manifest(ns)
        if not held.get("pin") or old is None:
            raise Unsafe("the install does not read", ns)
        store_path = _need(ns, target)
        kept, ignored = bare.selected(bare.listing(store_path, target))
        digests = bare.unit_digests(kept)
        after = {key: value for key, value in digests.items() if key != "@catalog"}
        same = after == old["units"] and digests.get("@catalog") == _rec(old["files"], manifest.FILE).get("oid")
        if target == held["pin"]:
            return {"upToDate": True}
        if same and not _rewritten(ns, store_path, held, target, context):   # the same files on another history are still news
            return {"upToDate": True}
        op = "undo" if target == held.get("previous") else "update"
        _write_stage(registry.stage_dir(ns), store_path, kept)
        try:
            meta = manifest.read_catalog(os.path.relpath(registry.stage_dir(ns) / manifest.FILE, paths.ROOT), published=True)
            if meta["id"] != ns:
                raise not_expected(_entry_name(registry.entry(ns)))
            refusal = _unreadable(meta, context.get("contract"), registry.entry(ns))
            if refusal is not None:
                raise refusal
            if manifest.unread(meta):   # a published catalog: what this Lab does not read in it needs a newer Gravitee Lab
                raise manifest.not_understood(manifest.unread(meta))
        except CatalogError:
            _clear(registry.stage_dir(ns))   # what was refused is not left to be installed
            raise
        before = old["units"]
        adds = sorted(set(after) - set(before))
        removes = sorted(set(before) - set(after))
        updates = sorted(key for key in set(before) & set(after) if before[key] != after[key])
        now = {entry.path: entry for entry in kept}
        files = {"add": sorted(set(now) - set(old["files"])),
                 "remove": sorted(set(old["files"]) - set(now)),
                 "change": sorted(path for path in set(now) & set(old["files"])
                                  if (now[path].oid, now[path].mode)
                                  != (_rec(old["files"], path).get("oid"), _rec(old["files"], path).get("mode")))}
        rewritten = _rewritten(ns, store_path, held, target, context, op) or (op == "update" and bool(context.get("moved")))
        news = _whatsnew(store_path, held["pin"], target, updates + adds + removes) if op == "update" else []
        touched = bare.changed_parts(files["add"] + files["change"] + files["remove"])
        return {"ns": ns, "op": op, "from": held["pin"], "to": target, "tip": held.get("tip"), "name": meta["name"],
                "version": meta["version"], "lab": meta["lab"], "adds": adds, "updates": updates, "removes": removes,
                "files": files, "touched": touched, "rewritten": rewritten, "held": _held(ns, updates + removes, context),
                "release": manifest.release_of(store_path, target, meta["version"]), "whatsnew": news, "ignored": ignored, "kept": kept, "stage": os.fspath(registry.stage_dir(ns)),
                "meta": meta, "context": context}


def _rewritten(ns, store_path, held, target, context, op="update") -> bool:
    """Whether the history of `target` is not the one installed: the roots differ, or the pin is not an ancestor of the target (for a
    Go back, the two are not one history), unless the new roots are the ones the engine lists for this catalog."""
    roots, vouched = bare.roots(store_path, target), sorted(set(context.get("vouched") or ()))
    joined = bare.is_ancestor(store_path, held["pin"], target)
    if op == "undo":
        joined = joined or bare.is_ancestor(store_path, target, held["pin"])
    return (not joined or roots != sorted(held.get("roots") or [])) and not (vouched and roots == vouched)


def _whatsnew(store_path, old, new, keys, limit=WHATSNEW):
    """What's new of an update: the notes of the units it touches (`store.notes`), their subjects one after the other, each
    once, at most `limit`; with no unit touched, those of catalog.yml."""
    lines = [subject for _, subjects in bare.notes(store_path, old, new, keys) for subject in subjects] if keys \
        else bare.log(store_path, old, new, [manifest.FILE])
    return list(dict.fromkeys(lines))[:limit]


def _only(folder, rel, going) -> bool:
    """Whether every file under `folder` (`rel` inside the install) is one of `going`: a folder that holds nothing else
    is emptied by the update and its place is free."""
    for here, _, files in os.walk(folder, followlinks=False):
        base = os.path.relpath(here, folder)
        for name in files:
            if (f"{rel}/{name}" if base == "." else f"{rel}/{base}/{name}".replace(os.sep, "/")) not in going:
                return False
    return True


def _in_the_way(root, path, going, ours, wanted=None) -> Optional[str]:
    """The path of what stands where the file `path` must go and that the update will not remove or replace: a file or a
    link where a folder is needed, a folder that holds something besides the files that go (the machine's), a file or a
    link that the old manifest does not list (`ours`: a file nobody listed is the machine's, and an update never writes
    over it, as an Add sends such a folder aside), else None. A regular file that is, byte for byte, the file the update
    writes there (`wanted`, {path: store.Entry}) is in no one's way: it is what a stopped or failed update of this very version
    left, and a Repair back to the old pin does not take it away, since the old manifest does not list it. Nothing is lost
    if the update takes it over."""
    here, parts = os.fspath(root), path.split("/")
    for number, part in enumerate(parts):
        here, rel = os.path.join(here, part), "/".join(parts[:number + 1])
        info = _lstat(here)
        if info is None:
            return None
        last = number == len(parts) - 1
        if stat.S_ISDIR(info.st_mode):
            if last and not _only(here, rel, going):
                return rel
        elif last:
            return None if rel in ours or (wanted and rel in wanted and _matches(here, wanted[rel]) is not None) else rel
        else:
            return None if rel in going else rel
    return None


def apply_update(ns: str, plan: dict, confirm_history: bool = False) -> None:
    """The apply of a plan, and Go back with a plan whose op is `undo`. Under the locks: the lock still pins the
    commit the plan starts from (else `ticket_expired`), none of the units it changes is on or up (`held`, asked again
    of the on file and of `plan["context"]["on"]`), and a rewritten history is confirmed (`history_changed`). Then
    `lock.pending` is set, the files that changed are replaced one by one and the ones the new version drops are deleted
    under the project lock, `.installed.json` is written last, and the lock write that moves `pin` (`previous` is the
    commit left) commits it. The refs of the store and the adoption of the settings that wait for the units it added
    follow; a stop before the commit is finished by `recover()`. Something of the machine's where a file of the new
    version goes (a folder with a `.state` in it where the catalog now has a file, or a file or a link that the
    installed version did not list where the new one now has a file) is `damaged` before anything is touched; but for a
    regular file that is the very file the new version writes there (what an update that failed halfway left, and a Repair
    to the old pin did not take away): it is taken over."""
    with _guard():
        _git_entry(ns)
        _settle(ns)
        held = registry.lock_of(ns)
        if plan.get("ns") != ns or held.get("pin") != plan.get("from"):
            raise CatalogError("ticket_expired", "This took too long. Start again.", 409)
        _need(ns, plan["to"])
        busy = _held(ns, list(plan["updates"]) + list(plan["removes"]), plan.get("context"))
        if busy:
            raise _turn_off(busy)
        if plan["rewritten"] and not confirm_history:
            raise CatalogError("history_changed", "This catalog was rewritten or moved. Confirm to update anyway.", 409)
        going, ours = set(plan["files"]["remove"]), set((registry.read_manifest(ns) or {}).get("files") or ())
        wanted = {entry.path: entry for entry in plan.get("kept") or ()}
        for path in list(plan["files"]["add"]) + list(plan["files"]["change"]):
            blocking = _in_the_way(registry.install_dir(ns), path, going, ours, wanted)
            if blocking:   # the machine's file where the catalog now puts one of its own: nothing is started
                raise Unsafe(f"{blocking} is in the way of {path}", ns)
        label = plan["op"]
        _step(f"{label}:pending")
        registry.set_lock(ns, pending={"op": label, "from": plan["from"], "to": plan["to"]})
        _roll(ns, plan["from"], plan["to"], label, plan.get("meta"))


def _roll(ns, frm, to, label, meta=None):
    """Steps 11 to 13b of an update from `frm` to `to` (`frm` equal to `to` is a repair), for a lock that is pending:
    the install is brought to the files of `to`, then the lock commits, with the root commits of `to` when the pin moves:
    a history the person confirmed (`history_changed`) is the history of the catalog from then on, and the next check and
    the next plan compare with it. Idempotent, and what `recover()` runs again for a pending operation: it judges the
    files by what is on disk."""
    store_path = _need(ns, to)
    kept, ignored = bare.selected(bare.listing(store_path, to))
    change = {}
    if frm != to:
        change["previous"] = frm
        roots = bare.roots(store_path, to)   # before any file moves: a git that fails stops the operation clean
        if len(roots) <= ROOTS_LIMIT:
            change["roots"] = roots   # the history the person confirmed is the history now pinned
    _converge(ns, to, kept, ignored, label, examine_all=frm == to)
    if meta is not None:
        fields = _lock_fields(meta)
    else:
        found = registry.scan(bare.read(store_path, to, manifest.FILE) or b"")
        fields = {key: found[key] for key in ("name", "version", "lab") if key in found}
    if frm != to:   # the release a pin is, said when it moves: a repair keeps what was said, even of a tag moved since
        fields["release"] = manifest.release_of(store_path, to, fields.get("version"))
    _step(f"{label}:lock")
    registry.set_lock(ns, pin=to, installed=_utc(), error=None, pending=None, **change, **fields)
    _step(f"{label}:refs")
    if frm != to:
        bare.set_ref(store_path, "previous", frm)
    bare.set_ref(store_path, "pin", to)
    _clear(registry.stage_dir(ns))
    _step(f"{label}:adopt")
    _adopted(ns, meta["moved_from"] if meta else None)
    _step(f"{label}:done")


def _matches(path, entry) -> Optional[os.stat_result]:
    """The stat of the file at `path` when it is, byte for byte, the file `entry` describes (its size and git's id), whatever
    its mode; else None. Never through a link."""
    info = _lstat(path)
    if info is None or not stat.S_ISREG(info.st_mode) or info.st_size != entry.size:
        return None
    try:
        data, _ = _read(path)
    except Unsafe:
        return None
    return info if _oid(data) == entry.oid else None


def _same(path, entry) -> bool:
    """Whether the file at `path` is the file `entry` describes, its mode included (a file that has the content and not
    the mode is given the mode). Never through a link."""
    info = _matches(path, entry)
    if info is None:
        return False
    if stat.S_IMODE(info.st_mode) != _perm(entry):
        os.chmod(path, _perm(entry))
    return True


def _prepared(ns, commit, kept, ignored):
    """The staging folder of `ns` made to hold exactly `kept` (the one the plan made when it is still intact, else it is
    written again from the store, which gives the same bytes) and the manifest for it: (the folder, the document)."""
    stage_dir = registry.stage_dir(ns)
    shas = None
    if os.path.lexists(stage_dir):
        try:
            shas = _audit(stage_dir, kept)
        except CatalogError:
            shas = None
    if shas is None:
        shas = _write_stage(stage_dir, registry.store_dir(ns), kept)
    return stage_dir, _document(ns, commit, kept, ignored, shas)


def _apply(ns, commit, kept, label, examine_all, prepared):
    """Brings the install of `ns` to exactly the files `kept` of `commit` from the staging folder `prepared` made, and
    writes its manifest last. The caller holds the project lock. Only the files that differ are replaced, each in one
    step: those that changed since the manifest the install has, or all of them when `examine_all` (a repair, a manifest
    that already says `commit`, or none). The files the old manifest listed and `kept` does not are deleted first, and
    the folders they leave empty, so that a file that becomes a folder finds its place free. A file that is not listed
    anywhere is the machine's and is not touched."""
    stage_dir, document = prepared
    root, old, wanted = registry.install_dir(ns), registry.read_manifest(ns), {entry.path: entry for entry in kept}
    _step(f"{label}:apply")
    if _lstat(root) is None:
        os.mkdir(root, 0o755)
        os.chmod(root, 0o755)
    elif not _real_dir(root):
        raise Unsafe("the install is not a folder", ns)
    if examine_all or old is None or old["commit"] == commit:
        examine = set(wanted)
    else:
        examine = {path for path, entry in wanted.items()
                   if _rec(old["files"], path).get("oid") != entry.oid
                   or _rec(old["files"], path).get("mode") != entry.mode}
    gone = sorted(set(old["files"]) - set(wanted)) if old else []
    for path in gone:   # first, so that a file that becomes a folder, or a folder a file, finds its place free
        _step(f"{label}:removed")
        _unlink(root, path)
    _prune(root, gone)
    for path in sorted(examine):
        if not _same(os.path.join(root, path), wanted[path]):
            _step(f"{label}:file")
            safe_replace(Path(stage_dir) / path, Path(root) / path)
    _step(f"{label}:manifest")
    _write_manifest(root, document)


def _converge(ns, commit, kept, ignored, label, examine_all):
    """`_prepared`, then `_apply` under the project lock."""
    prepared = _prepared(ns, commit, kept, ignored)
    with _exclusive():
        _apply(ns, commit, kept, label, examine_all, prepared)


# ---------------------------------------------------------------- repairing

def repair(ns: str) -> None:
    """The install brought back to the pin from the local store, whatever was changed, removed or added in it:
    a file whose content or mode differs, or that is missing, is written again; the manifest is written again; `error`
    is cleared. A file the manifest does not list is the machine's and stays. It runs as an update from the pin to the
    pin, so a stop in the middle is finished by `recover()`. When the store lacks an object it raises `fsck`
    (`store.MissingObject`): the caller fetches again and repairs again. Nothing to do for a folder catalog."""
    with _guard():
        entry = registry.entry(ns)
        if entry is None:
            raise ValueError(f"{ns} is not a catalog of this machine")
        if entry["kind"] == "folder" and not registry.lock_of(ns).get("shadowed"):
            return
        entry, held = _git_entry(ns)
        _settle(ns)
        held = registry.lock_of(ns)
        if not held.get("pin"):
            raise ValueError(f"{ns} has nothing installed")
        _need(ns, held["pin"])
        _step("repair:pending")
        registry.set_lock(ns, pending={"op": "update", "from": held["pin"], "to": held["pin"]})
        _roll(ns, held["pin"], held["pin"], "repair")


def verify(ns: str, names: Iterable[str]) -> List[str]:
    """The installed files whose sha256 differs from the manifest's, or that are gone, or are no longer regular files,
    among those of the units `names` (`kind:name`; `catalog.yml` is always checked), sorted. Reads each file once, never
    through a link, and nothing else. A catalog with no valid manifest is `damaged`."""
    found = registry.manifest(ns)
    if found is None:
        raise Unsafe("no manifest the pin accepts", ns)
    wanted = set(names)
    root = os.fspath(registry.install_dir(ns))
    bad = []
    for path, record in found["files"].items():
        unit = unit_key(path)
        if unit is not None and unit not in wanted:
            continue
        info = _lstat(os.path.join(root, path)) if _safe(path) and isinstance(record, dict) else None
        if info is None or not stat.S_ISREG(info.st_mode) or info.st_size != record.get("size"):
            bad.append(path)
            continue
        try:
            data, _ = _read(os.path.join(root, path))
        except Unsafe:
            bad.append(path)
            continue
        if hashlib.sha256(data).hexdigest() != record.get("sha256"):
            bad.append(path)
    return sorted(bad)


def unit_key(path):
    """`kind:name` of the unit a path of a catalog belongs to, None for catalog.yml and what belongs to no unit."""
    found = _UNITS.match(path)
    if not found:
        return None
    return f"{_KIND[found.group(1)]}:{found.group(2)}" if found.group(1) else f"setup:{found.group(3)}"


# ---------------------------------------------------------------- removing

def remove(ns: str, keep_settings: bool = True) -> dict:
    """The removal of a catalog, after the caller has checked `held` and `breaks`: the entry leaves `catalogs.json` (the commit point), the
    store is deleted, then the install: with `keep_settings` the files its manifest lists are deleted with the folders
    they leave empty and the machine files stay, with the marker `.kept` that tells the sweeper to leave them (a folder
    with none is removed); without, the whole folder is removed, and what cannot be removed (files Docker made as root)
    is counted and reported. The lock entry and the stars of its units go. A folder catalog loses its link and nothing
    else: the author's folder is never touched. Answers {"warning": the line of `removed_partial` or None}. Idempotent:
    run again after a stop, it finishes. `linked` for a git catalog that a working copy stands in for; `held` ("Turn off
    {name} first.") when the on file turns on a unit of it, directly or through an item of another catalog: the caller
    asks the same of what is up, which only it can see."""
    if not isinstance(ns, str) or ns == paths.LOCAL or not _NS.fullmatch(ns):
        raise ValueError(f"{ns!r} is not a catalog")
    with _guard():
        entry, held = registry.entry(ns), registry.lock_of(ns)
        if entry is not None and (held.get("shadowed") or (entry["kind"] == "folder" and entry.get("shadows"))):
            raise CatalogError("linked", LINKED, 409)
        if entry is None and registry.config_problem():
            raise Unsafe("the file that lists the catalogs cannot be read", ns)
        if entry is not None:
            try:
                _settle(ns)
            except CatalogError as error:
                if not _transient(error):
                    raise   # a Remove needs no git: what was under way without it goes with the catalog
            busy = on_units(ns)
            if busy:
                raise _turn_off(busy)
        units = [(kind, name) for kind, names in registry.units_of(ns).items() for name in names]
        if entry is not None:
            _step("remove:config")
            config = registry.read_config() or {"schema": 1, "catalogs": []}
            config["catalogs"] = [one for one in config["catalogs"] if one["id"] != ns]
            registry.write_config(config)
        root, warning = os.fspath(registry.install_dir(ns)), None
        with _exclusive():
            _step("remove:store")
            _clear(registry.store_dir(ns))
            _clear(registry.stage_dir(ns))
            _step("remove:files")
            if os.path.islink(root):
                os.unlink(root)   # the link and nothing it points to
            elif _real_dir(root):
                warning = _take_out(ns, root, keep_settings)
        _step("remove:lock")
        registry.drop_lock(ns)
        _step("remove:stars")
        default = paths.default_ns()
        for kind, name in units:
            starred.moved(kind, name if ns == default else f"@{ns}/{name}")
        _step("remove:done")
        return {"warning": warning}


def _take_out(ns, root, keep_settings) -> Optional[str]:
    """The install folder of a catalog that left: its content (and, without `keep_settings`, everything). Answers the
    warning for what could not be removed."""
    if keep_settings:
        # judged while the manifest still says what is content: the rest is the machine's
        _leave(ns, root, "remove:marker", "remove:vacate")
        return None
    if _remove_tree(root):
        with contextlib.suppress(OSError):
            _mark(root)   # what is left waits for an Add, as the line says
        return PARTIAL.format(id=ns)
    return None


# ---------------------------------------------------------------- a working copy in place of a git install

def _record_file(ns) -> Path:
    return Path(paths.ROOT) / paths.CATALOGS_DIR / SHADOW.format(ns=ns)


def _read_record(ns) -> Optional[dict]:
    try:
        data, _ = _read(_record_file(ns))
        found = json.loads(data.decode("utf-8"))
    except (Unsafe, ValueError):
        return None
    return found if isinstance(found, dict) and isinstance(found.get("folder"), str) else None


def _write_record(ns, record):
    paths.write_atomic(_record_file(ns), json.dumps(record, indent=2, ensure_ascii=False) + "\n", mode=0o600)


def _declared_id(folder) -> Optional[str]:
    """The `id` that the catalog.yml of a folder declares, read the way the registry reads an install; None when it is
    not there as a regular file or does not say."""
    path = os.path.join(folder, manifest.FILE)
    if not _is_regular(path):
        return None
    try:
        return registry.scan(_read(path, manifest.FILE_LIMIT)[0]).get("id")
    except Unsafe:
        return None


def shadow(ns: str, folder: str) -> dict:
    """A working copy `folder` (whose catalog.yml declares `ns`) stands in place of the git install of `ns`. The
    machine entries of the install move to the same paths in the folder with `moves.relocate` (never over a file there:
    the install's copy goes to `.lab/migration/kept/` and is reported); the install's content files and folder are
    removed (what could not be moved or removed waits in a `.removed-<ns>-*` folder); the folder is linked at
    `.lab/catalogs/<ns>`; the entry becomes a folder entry that remembers the git one in `shadows`; the lock entry is
    marked `shadowed` (the store and the pin stay). What is done is recorded in `.shadow-<ns>.json` first, so that a
    stop is finished by `recover()` and `unshadow` knows what to take back. The caller refuses while an item is on or
    up; this refuses what the on file turns on. Answers {"moved": [...], "kept": [...], "folder": path}."""
    with _guard():
        entry, held = registry.entry(ns), registry.lock_of(ns)
        if entry is None:
            raise ValueError(f"{ns} is not a catalog of this machine")
        if entry["kind"] != "git" or held.get("shadowed"):
            raise CatalogError("linked", LINKED, 409)
        _settle(ns)
        if not registry.lock_of(ns).get("pin") or registry.manifest(ns) is None:
            raise Unsafe("the install does not read", ns)
        address = gitx.parse_address(folder)
        if address.kind != "folder":
            raise gitx.fail("bad_address")
        declared = _declared_id(address.path)
        if declared != ns:
            raise CatalogError("catalog_invalid", f"catalog.yml: id is {declared or 'missing'}, not {ns}.", 400)
        busy = on_units(ns)
        if busy:
            raise _turn_off(busy)
        record = {"schema": 1, "phase": "linking", "folder": address.path, "moves": [],
                  "git": {key: entry[key] for key in ("url", "branch", "added", "auto", "host") if key in entry}}
        _step("shadow:record")
        _write_record(ns, record)
        return _shadow_run(ns, record)


def _kept_path(ns, rel):
    return os.path.join(paths.ROOT, moves.MIGRATION, "kept", "catalogs", ns, rel)


def _relocate_into(source, target, rel, why, found):
    """`moves.relocate` of `source/rel` to `target/rel`; where a folder is in both places it goes entry by entry (a
    `.state` or `secrets` folder only moves whole). Adds (path, result) to `found` for what was moved or could not be.
    """
    here, there = os.path.join(source, rel), os.path.join(target, rel)
    result = moves.relocate(here, there, why)
    whole = os.path.basename(rel).lower() in _SECRET_DIRS
    if result == "conflict" and _real_dir(here) and _real_dir(there) and not whole:
        for child in sorted(os.listdir(here)):
            _relocate_into(source, target, f"{rel}/{child}", why, found)
    else:
        found.append((rel, result))


def _shadow_run(ns, record) -> dict:
    """The steps of `shadow` after the record is written. Each is judged by the disk, so a run that was stopped is run
    again from the top."""
    root, target = os.fspath(registry.install_dir(ns)), record["folder"]
    done = {one["path"] for one in record["moves"]}
    if _real_dir(root):
        for top in _machine(ns, root):
            if top in done:
                continue
            _step("shadow:move")
            found = []
            _relocate_into(root, target, top, "shadow", found)
            for rel, result in found:
                if result == "conflict":   # the working copy's file wins; the install's is kept, not lost
                    moves.relocate(os.path.join(root, rel), _kept_path(ns, rel), "shadow-conflict")
                    result = "kept"
                record["moves"].append({"path": rel, "result": result})
                done.add(rel)
            _write_record(ns, record)
    with _exclusive():   # the install, the link, the entry and the lock change together for a reader
        if _real_dir(root):
            _step("shadow:install")
            if _leave(ns, root):   # what could not be moved waits aside, marked
                _aside(ns, root)
        _step("shadow:link")
        if _lstat(root) is None:
            os.symlink(target, root)
        elif not (os.path.islink(root) and os.readlink(root) == target):
            raise Unsafe("something other than the link is where the working copy goes", ns)
        _step("shadow:config")
        config = registry.read_config() or {"schema": 1, "catalogs": []}
        row = {"id": ns, "kind": "folder", "path": target,
               "shadows": {key: record["git"][key] for key in ("url", "branch", "added") if key in record["git"]}}
        row["added"] = record["git"].get("added") or _utc()
        if record["git"].get("host"):
            row["host"] = True
        config["catalogs"] = [row if one["id"] == ns else one for one in config["catalogs"]]
        registry.write_config(config)
        _step("shadow:lock")
        registry.set_lock(ns, shadowed=True)
    record["phase"] = "linked"
    _write_record(ns, record)
    _step("shadow:done")
    return {"moved": [one["path"] for one in record["moves"] if one["result"] in ("moved", "copied")],
            "kept": [one["path"] for one in record["moves"] if one["result"] == "kept"], "folder": target}


def unshadow(ns: str) -> dict:
    """The reverse of a link: the link is removed; the install is made again at the pin from the store (a repair); the machine
    entries the record says moved go back to the install, never over a file there; the entry becomes the git entry again
    and the lock entry is no longer `shadowed`; the record goes. A stop is finished by `recover()`. Answers
    {"moved": [...]}. The working copy is never touched but for the entries taken back. A store that does not hold the
    pin (the cache was cleared: it is disposable) is `fsck` before anything is written, with the link in place: the
    caller fetches the address in `entry["shadows"]` into the store of the catalog and unlinks again."""
    with _guard():
        entry = registry.entry(ns)
        if entry is None or entry["kind"] != "folder" or not entry.get("shadows"):
            raise ValueError(f"{ns} is not a git catalog that a working copy stands in for")
        pin = registry.lock_of(ns).get("pin")
        if not pin:
            raise Unsafe("the install does not read", ns)
        _need(ns, pin)   # the store holds the pin or nothing is started: the link stays, and the caller fetches again
        record = _read_record(ns) or {"schema": 1, "folder": entry["path"], "moves": [], "git": dict(entry["shadows"])}
        record["phase"] = "unlinking"
        record["git"] = {**entry["shadows"], **{key: entry[key] for key in ("host",) if key in entry}}
        _step("unshadow:record")
        _write_record(ns, record)
        return _unshadow_run(ns, record)


def _unshadow_run(ns, record) -> dict:
    """The steps of `unshadow` after the record is written, judged by the disk like those of `_shadow_run`. The link,
    the install, the entry and the lock change together for a reader."""
    root, held = os.fspath(registry.install_dir(ns)), registry.lock_of(ns)
    store_path = _need(ns, held["pin"])
    kept, ignored = bare.selected(bare.listing(store_path, held["pin"]))
    prepared = _prepared(ns, held["pin"], kept, ignored)
    moved = []
    with _exclusive():
        _step("unshadow:link")
        if os.path.islink(root):
            os.unlink(root)
        _step("unshadow:install")
        _apply(ns, held["pin"], kept, "unshadow", True, prepared)
        for one in record["moves"]:
            if one["result"] not in ("moved", "copied"):
                continue
            _step("unshadow:move")
            if moves.relocate(os.path.join(record["folder"], one["path"]), os.path.join(root, one["path"]),
                              "unshadow") == "moved":
                moved.append(one["path"])
        _step("unshadow:config")
        config = registry.read_config() or {"schema": 1, "catalogs": []}
        git = record["git"]
        row = {"id": ns, "kind": "git", "url": git["url"], "auto": git.get("auto", True),
               "added": git.get("added") or _utc()}
        if git.get("branch"):
            row["branch"] = git["branch"]
        if git.get("host"):
            row["host"] = True
        config["catalogs"] = [row if one["id"] == ns else one for one in config["catalogs"]]
        registry.write_config(config)
        _step("unshadow:lock")
        registry.set_lock(ns, shadowed=None)
    _clear(registry.stage_dir(ns))
    with contextlib.suppress(FileNotFoundError):
        os.unlink(_record_file(ns))
    _step("unshadow:done")
    return {"moved": moved}


# ---------------------------------------------------------------- after a stop, and at every start

def _settle(ns):
    """Finishes the operation a lock entry says is under way, before another one starts (a run that failed with an
    error, not a stop, leaves one too)."""
    if registry.lock_of(ns).get("pending"):
        _finish(ns)


def _intact(root, found) -> bool:
    """Whether every file a manifest lists is in the folder `root` with the sha256 it says."""
    for path, record in found["files"].items():
        if not (_safe(path) and isinstance(record, dict)):
            return False
        try:
            data, _ = _read(os.path.join(root, path))
        except Unsafe:
            return False
        if hashlib.sha256(data).hexdigest() != record.get("sha256"):
            return False
    return True


def _complete(ns, commit) -> bool:
    """Whether the install of `ns` is whole for `commit`: its manifest says `commit`, every file it lists is there with
    the sha256 it says, and the store holds the commit."""
    root = os.fspath(registry.install_dir(ns))
    found = _manifest_in(root, ns)
    if found is None or found["commit"] != commit or not os.path.isdir(registry.store_dir(ns)):
        return False
    return bare.resolve(registry.store_dir(ns), commit) == commit and _intact(root, found)


def _finish(ns):
    """Finishes or undoes the operation `lock.pending` names. An Update, a Go back or a Repair is finished: the
    store holds every object, so the files are brought to `to` again and the lock commits. A download (an Add whose
    entry exists) is finished when the install and the store are whole, else its files are removed (the machine's stay)
    and the pending flag cleared, so that the download starts again from nothing. An Add with no entry never committed:
    it is garbage, which the sweeper removes. What cannot be finished marks the catalog damaged, which Repair clears."""
    pending = registry.lock_of(ns).get("pending")
    if not pending:
        return
    op, frm, to = pending["op"], pending["from"], pending["to"]
    _step("recover:pending")
    if op == "add":
        if registry.entry(ns) is None:
            return
        root = os.fspath(registry.install_dir(ns))
        if _complete(ns, to):
            _restore(ns, root)
            now = _utc()
            registry.set_lock(ns, pin=to, previous=None, installed=now, checked=now, error=None, pending=None)
            bare.set_ref(registry.store_dir(ns), "pin", to)
            _unlink(root, MARKER)
            _clear(registry.stage_dir(ns))
            _adopted(ns, None)
        else:
            if _real_dir(root):
                _leave(ns, root)
            _clear(registry.stage_dir(ns))
            registry.set_lock(ns, pin=None, pending=None, error=None)
        return
    try:
        _roll(ns, frm, to, "repair" if frm == to else op)
    except CatalogError as error:
        if _transient(error):   # git is not here now: the operation stays under way for a start that has it
            raise
        registry.set_lock(ns, pending=None, error="damaged")
    except OSError:
        registry.set_lock(ns, pending=None, error="damaged")


def _age(path) -> float:
    info = _lstat(path)
    return time.time() - info.st_mtime if info else 0.0


def _namespaces(names):
    """The names in a listing of `.lab/catalogs` that can be a catalog's folder: a namespace, and not `local`."""
    return [name for name in names if _NS.fullmatch(name) and name != paths.LOCAL]


def _sweep_stale() -> List[Tuple[str, str]]:
    """The leftovers of rule 12 that age alone judges, which need no lock because nothing that old is in use: the
    staging folders and the `*.partial` files of `.lab/catalogs`, and the stores being added, older than a day (`AGE`).
    """
    done = []
    base = os.path.join(paths.ROOT, paths.CATALOGS_DIR)
    cache = os.path.join(paths.ROOT, registry.STORE_DIR)
    for folder, wanted in ((base, lambda name: name.startswith(".stage-") or name.endswith(".partial")),
                           (cache, lambda name: name.startswith(".new-") and name.endswith(".git"))):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            if wanted(name) and _age(os.path.join(folder, name)) > AGE:
                _clear(os.path.join(folder, name))
                done.append(("swept", name))
    return done


def sweep() -> List[Tuple[str, str]]:
    """Under the locks: `_sweep_stale`, the `.share-*` folders Share leaves in an install older than
    a day, and the install folders that have no entry in `catalogs.json`, each judged: left alone when it holds the
    marker `.kept` (when it still holds its manifest too, a Remove stopped before it took the content out, and the
    content is taken out: "emptied"), or when its id is listed and it holds only machine files (settings waiting for an
    Add); removed when it holds nothing but what its manifest lists; renamed `.removed-<ns>-<utc>` when it holds
    anything else, marked first if no manifest vouches for it. `local` is never swept, a link is never followed, and no
    install is touched while the file that lists the catalogs cannot be read, so a damaged file never turns every
    install into garbage. Answers [(what was done, the name)]."""
    with _guard():
        done = _sweep_stale()
        if registry.config_problem():
            return done
        base = os.path.join(paths.ROOT, paths.CATALOGS_DIR)
        try:
            names = _namespaces(sorted(os.listdir(base)))
        except OSError:
            return done
        have = {one["id"] for one in registry.entries()}
        for name in names:
            root = os.path.join(base, name)
            if not _real_dir(root):
                continue
            try:
                if name not in have:
                    done += _sweep_install(name, root)
                    continue
                inside = sorted(os.listdir(root))
            except OSError:   # a folder the Lab cannot read or rename is left for the person
                continue
            for one in inside:
                if one.startswith(".share-") and _age(os.path.join(root, one)) > AGE:
                    _clear(os.path.join(root, one))
                    done.append(("swept", f"{name}/{one}"))
        return done


def _sweep_install(ns, root) -> List[Tuple[str, str]]:
    """Rule 12 for one install folder with no entry."""
    if os.path.lexists(os.path.join(root, MARKER)):
        if _manifest_in(root, ns) is None:
            return []
        _leave(ns, root)   # a Remove that kept the settings stopped before it took the content out: it is finished
        return [("emptied", ns)]
    found = _manifest_in(root, ns)
    listed = set(found["files"]) if found else None
    content, _, machine = _scan(root, listed)
    if not machine and (found is not None or content == 0):
        _clear(root)
        return [("removed", ns)]
    if listed is None and content == 0 and defaults.listed({"id": ns, "kind": "git"}):
        return []
    _aside(ns, root)
    return [("renamed", ns)]


def recover() -> None:
    """At the start of the Lab (after the bind), under the locks: finishes the operations the lock
    says are under way, finishes a shadow link or its reversal, removes what a stop left (rule 12: `sweep`, the lock
    entries and the stores that have no entry in `catalogs.json`), writes again the refs of the stores, repeats the
    adoption of the settings that wait and removes the markers of the folders a catalog has been added
    into. Everything is idempotent and judged by the disk, so a stop inside it is finished by the next. The trouble of
    one catalog never ends the start, and neither does a start without git (state `needs_git`): an operation under way
    keeps its record, the refs are written at a start that has git. It raises `busy` when another process holds the lock
    for longer than the wait."""
    with _guard():
        _step("recover:begin")
        problem = registry.config_problem()
        for ns in sorted(registry.read_lock()["catalogs"]):
            if registry.entry(ns) is not None and registry.lock_of(ns).get("pending"):
                try:
                    _finish(ns)
                except TROUBLE as error:   # this catalog is damaged, the others are not
                    if not _transient(error):   # without git it is not damaged, it waits
                        registry.set_lock(ns, pending=None, error="damaged")
        for entry in registry.entries():
            record = _read_record(entry["id"])
            try:
                if record and record.get("phase") == "linking":
                    _shadow_run(entry["id"], record)
                elif record and record.get("phase") == "unlinking":
                    _unshadow_run(entry["id"], record)
            except TROUBLE:   # the record stays: the person can unlink from a terminal
                continue
        _step("recover:sweep")
        sweep()
        if not problem:
            _orphans()
        _step("recover:adopt")
        for entry in registry.entries():
            try:
                _tidy(entry)
            except TROUBLE:   # what waits keeps waiting, and is tried again at the next start
                continue
        _step("recover:done")


def _tidy(entry):
    """The last of `recover()` for one catalog: the refs of its store written again, the marker of a folder a catalog
    has been added into removed, and the settings that wait adopted. Git runs for the refs, and a start without git
    (state `needs_git`: an installed catalog keeps working) goes on: the refs are an optimization, tried again at the
    next start, and the rest needs no git."""
    held = registry.lock_of(entry["id"])
    if entry["kind"] != "git" or not held.get("pin") or held.get("pending"):
        return
    try:
        store_path = registry.store_dir(entry["id"])
        if os.path.isdir(store_path) and bare.resolve(store_path, held["pin"]) == held["pin"]:
            if bare.resolve(store_path, "pin") != held["pin"]:
                bare.set_ref(store_path, "pin", held["pin"])
            if held.get("previous") and bare.resolve(store_path, held["previous"]) == held["previous"] \
                    and bare.resolve(store_path, "previous") != held["previous"]:
                bare.set_ref(store_path, "previous", held["previous"])
    except TROUBLE:
        pass
    if registry.manifest(entry["id"]) is not None:
        _unlink(registry.install_dir(entry["id"]), MARKER)
        adopt(entry["id"])


def _orphans():
    """The lock entries and the stores of catalogs that have no entry in `catalogs.json`: an Add that never committed,
    or a Remove that stopped after its commit point. Their install folders are `sweep`'s."""
    have = {one["id"] for one in registry.entries()}
    for ns in sorted(registry.read_lock()["catalogs"]):
        if ns not in have:
            registry.drop_lock(ns)
    cache = os.path.join(paths.ROOT, registry.STORE_DIR)
    try:
        stores = sorted(os.listdir(cache))
    except OSError:
        return
    for name in stores:
        if name.endswith(".git") and _NS.fullmatch(name[:-4]) and name[:-4] not in have:
            _clear(os.path.join(cache, name))
