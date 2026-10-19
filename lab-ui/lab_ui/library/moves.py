"""The primitives that move things on this machine, shared by the migration of an old layout, the shadow link of a
working copy, Share and the adoption of held settings: `relocate` moves a file or a folder
and never overwrites, `repoint` writes again the paths of a compose file that moved, `rekey` follows an item to its new
id in the stars and the tested sets, `adopt_held` gives an item the settings that waited for it.

State based and idempotent: every function reads the disk and does what is left to do, so a run that crashed is run
again, never undone. Every move is written to the journal (`.lab/migration/journal.jsonl`: paths, never a content)
before it is made, so a crash between the two never hides a move from `undo_script`, which turns the journal into the
shell script that puts everything back. Nothing here runs Docker, git or the network: whether git tracks a file is
read from the index itself. `STEP` is the hook a test sets to crash a run between two steps.

Two folders of `.lab` are only for the person (0700, and so is whatever is made in them): `migration/`, with the
journal, the backup, what was kept aside and the undo script, and `held/<kind>s/<name>/`, where the machine files of an
item that is in no installed catalog wait, at the same relative path they had in its folder, for the catalog that gets
the item."""

import bisect
import errno
import filecmp
import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import stat
import struct
import threading
import time
import unicodedata
from typing import Optional

from .. import paths

# A test sets it to a callable that takes the name of the step it is about to take and raises to stop the run there:
# relocate:before, :journaled, :copy, :copied and :renamed, rekey:starred and :tested, adopt:entry, :cleanup, :rekey
# and :repoint, and <why>:entry for each entry `merge` moves (adopt:entry when a held folder is merged)
STEP = None

MIGRATION = f"{paths.LAB_DIR}/migration"   # the journal, the backup, what was kept aside and the undo script
JOURNAL = f"{MIGRATION}/journal.jsonl"
UNDO = f"{MIGRATION}/undo.sh"
MARKER = f"{paths.LAB_DIR}/layout.json"   # the commit point of the migration, written last: layout.MARKER is this
HELD = f"{paths.LAB_DIR}/held"   # the settings of an item that is in no installed catalog, by kind and name
PRIVATE = (MIGRATION, HELD)   # folders only the person reads: whatever is made inside them is 0700
STARRED = f"{paths.LAB_DIR}/starred.json"
TESTED = f"{paths.LAB_DIR}/tested.json"
TESTED_KEPT = 20   # per playground, as tested.KEPT: the latest sets
# What a whole entry is at the top of a held folder: a folder of seed records or of credential files moves in one piece
# and is never merged into another, since records are not meant to be mixed. Any other folder is a container.
WHOLE = (".state", "secrets")
DEEP = 16   # how far down a held folder is merged into the install

_NAME = re.compile(paths.NAME)
_NS = re.compile(paths.NS)
_COPY_MODE = 0o700   # what a copy of a folder is made with, until it is renamed and gets the mode of the original


class Refused(Exception):
    """A move the Lab will not make: a path is empty, the source is the project folder or one above it, git tracks the
    source, a path is inside itself, or an id is not one."""


def _step(label):
    if STEP is not None:
        STEP(label)


# ---------------------------------------------------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------------------------------------------------

def _abs(path):
    """A path of the project made absolute; an absolute one as it is. No link is resolved."""
    text = os.fspath(path)
    return text if os.path.isabs(text) else os.path.join(str(paths.ROOT), text)


def _rel(path):
    """How the journal names a path: relative to the project and POSIX when it is inside it, else absolute."""
    absolute, root = os.path.normpath(_abs(path)), os.path.normpath(str(paths.ROOT))
    if absolute == root:
        return "."
    return absolute[len(root) + 1:] if absolute.startswith(root + os.sep) else absolute


def _inside(path, folder):
    """Whether `path` is `folder` or below it, by name."""
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def _private(path):
    """Whether a path is inside a folder that only the person reads."""
    return any(_inside(path, _abs(folder)) for folder in PRIVATE)


def _parents(target):
    """Makes the folders above `target` that are missing: 0700 where only the person reads, else as `mkdir` does. A
    folder that another run made in the meantime is used, a link or a file in its place is an error. A private folder
    that exists with a wider mode is closed."""
    missing, current = [], os.path.dirname(target)
    while current and not os.path.lexists(current):
        missing.append(current)
        above = os.path.dirname(current)
        if above == current:
            break
        current = above
    private = _private(target)
    for folder in reversed(missing):
        try:
            os.mkdir(folder, 0o700 if private else 0o777)
        except FileExistsError:   # another run made it between the look and the mkdir: fine when it is a real folder
            if os.path.islink(folder) or not os.path.isdir(folder):
                raise
    if private:
        for folder in PRIVATE:
            if _inside(target, _abs(folder)) and os.path.isdir(_abs(folder)) and not os.path.islink(_abs(folder)):
                os.chmod(_abs(folder), 0o700)


def _discard(path):
    """Removes a file, a link or a folder the Lab made itself (a partial copy), never following a link."""
    if not os.path.lexists(path):
        return
    if os.path.islink(path) or not os.path.isdir(path):
        os.unlink(path)
        return
    for folder, folders, files in os.walk(path, topdown=False):
        os.chmod(folder, 0o700)   # a copy keeps the mode of what it copied: a folder at 0555 cannot be emptied
        for name in files:
            os.unlink(os.path.join(folder, name))
        for name in folders:
            full = os.path.join(folder, name)
            if os.path.islink(full):
                os.unlink(full)
            else:
                os.rmdir(full)
    os.rmdir(path)


# The three helpers other modules of the Lab share with this one (the migration calls them), under names that are not private.
# The private names work as they always did: they are the same functions.
step, parents, discard = _step, _parents, _discard


# ---------------------------------------------------------------------------------------------------------------------
# The journal
# ---------------------------------------------------------------------------------------------------------------------

_JOURNAL = threading.Lock()


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def journal_path():
    return paths.ROOT / JOURNAL


def journal(op, **fields):
    """Adds one line to the journal: the operation, what it names (paths, ids, counts; never a content) and the time.
    Appended whole in one write, after a newline if a crash tore the line before. ASCII only, so a name that is not
    valid UTF-8 survives the round trip."""
    line = json.dumps({"op": op, **fields, "at": _now()}, default=str, separators=(",", ":")) + "\n"
    file = journal_path()
    with _JOURNAL:
        _parents(str(file))
        descriptor = os.open(file, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            size = os.fstat(descriptor).st_size
            torn = size and os.pread(descriptor, 1, size - 1) != b"\n"
            os.write(descriptor, (b"\n" if torn else b"") + line.encode("ascii"))
        finally:
            os.close(descriptor)


def read_journal():
    """The lines of the journal that read, oldest first: a torn or foreign line is skipped, not an error."""
    try:
        raw = journal_path().read_bytes()
    except OSError:
        return []
    found = []
    for line in raw.decode("utf-8", errors="replace").split("\n"):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("op"), str):
            found.append(entry)
    return found


def _copied_before(source, target):
    """Whether the journal says `target` is a copy of `source` that was made here: it was kept, not a conflict."""
    pair = (_rel(source), _rel(target))
    return any(entry.get("op") == "copied" and (entry.get("src"), entry.get("dst")) == pair for entry in read_journal())


# ---------------------------------------------------------------------------------------------------------------------
# What git tracks, read from its index
# ---------------------------------------------------------------------------------------------------------------------

_INDEXES = {}   # index file -> (its stat, what it lists)
_INDEXES_LOCK = threading.Lock()


def _key(name):
    """A path as compared with the index: Unicode composed and case folded, since a Mac volume may answer for either."""
    return unicodedata.normalize("NFC", name).casefold()


def _checkout(folder):
    """The nearest folder at or above `folder` that holds a `.git` (a folder, or a file in a worktree), else None."""
    while True:
        if os.path.lexists(os.path.join(folder, ".git")):
            return folder
        above = os.path.dirname(folder)
        if above == folder:
            return None
        folder = above


def _git_dir(top):
    """The folder that holds a checkout's index: `.git`, or the one a worktree's `.git` file names."""
    dot = os.path.join(top, ".git")
    if os.path.isdir(dot):
        return dot
    try:
        with open(dot, encoding="utf-8") as f:
            first = f.readline().strip()
    except (OSError, ValueError):
        return None
    return os.path.normpath(os.path.join(top, first[len("gitdir:"):].strip())) if first.startswith("gitdir:") else None


def _parse_index(data):
    """The paths a git index lists, as bytes in file order, or None when this is not an index this reader knows: a
    version other than 2, 3 and 4, a split index, a checksum that does not hold, a structure that does not add up. The
    caller treats None as "cannot tell", which is "tracked"."""
    if len(data) < 32 or data[:4] != b"DIRC":
        return None
    version, count = struct.unpack(">II", data[4:12])
    if version not in (2, 3, 4):
        return None
    for size, digest in ((20, hashlib.sha1), (32, hashlib.sha256)):
        if digest(data[:-size]).digest() == data[-size:]:
            break
    else:
        if data[-20:] != bytes(20):   # index.skipHash writes no checksum
            return None
        size = 20
    end, position, previous, names = len(data) - size, 12, b"", []
    try:
        for _ in range(count):
            start = position
            flags = struct.unpack_from(">H", data, position + 40 + size)[0]
            position += 40 + size + 2
            if version >= 3 and flags & 0x4000:   # the extended flags
                position += 2
            if version == 4:   # the name keeps the start of the previous one: a count to cut, then the end
                byte = data[position]
                position += 1
                cut = byte & 0x7F
                while byte & 0x80:
                    byte = data[position]
                    position += 1
                    cut = ((cut + 1) << 7) | (byte & 0x7F)
                stop = data.index(b"\0", position)
                if cut > len(previous):
                    return None
                name = previous[:len(previous) - cut] + data[position:stop]
                position, previous = stop + 1, name
            else:   # the entry is padded with NULs to a multiple of eight bytes
                stop = data.index(b"\0", position)
                name = data[position:stop]
                position = start + ((position - start + len(name) + 8) & ~7)
            if position > end:
                return None
            names.append(name)
        while position + 8 <= end:   # the extensions: a four letter name and a size
            length = struct.unpack_from(">I", data, position + 4)[0]
            if data[position:position + 4] == b"link":   # a split index lists the rest elsewhere
                return None
            position += 8 + length
    except (IndexError, ValueError, struct.error):
        return None
    return names if position == end else None


def _listed(index):
    """(sorted keys, set of keys, sparse folders) of an index file, None when it cannot be read; nothing when there is
    none yet (a repository where nothing was added)."""
    try:
        info = os.stat(index)
    except FileNotFoundError:
        return [], frozenset(), ()
    except OSError:
        return None
    seen = (info.st_mtime_ns, info.st_size, info.st_ino)
    with _INDEXES_LOCK:
        cached = _INDEXES.get(index)
    if cached and cached[0] == seen:
        return cached[1]
    try:
        with open(index, "rb") as f:
            names = _parse_index(f.read())
    except OSError:
        names = None
    found = None
    if names is not None:
        keys = [_key(name.decode("utf-8", "surrogateescape")) for name in names]
        # A sparse index lists a folder whole, with a slash at the end
        found = (sorted(key for key in keys if not key.endswith("/")),
                 frozenset(key for key in keys if not key.endswith("/")),
                 tuple(key for key in keys if key.endswith("/")))
    with _INDEXES_LOCK:
        _INDEXES[index] = (seen, found)
    return found


def tracked(path):
    """Whether git tracks a file, or anything below a folder, in the checkout that holds it. It is the answer of
    `git ls-files`, read from the index file, so no git runs. The checkout is the nearest folder above that has a
    `.git`, found from the real place of the parent (a catalog linked in place is judged in its own checkout). True when
    it cannot tell: a checkout whose index is not readable is treated as tracked. False outside any checkout."""
    absolute = _abs(path).rstrip(os.sep) or os.sep
    parent, name = os.path.split(absolute)
    real = os.path.realpath(parent)
    top = _checkout(real)
    if top is None:
        return False
    git = _git_dir(top)
    found = _listed(os.path.join(git, "index")) if git else None
    if found is None:
        return True
    keys, listed, sparse = found
    key = _key(os.path.relpath(os.path.join(real, name), top))
    if key in listed:
        return True
    where = bisect.bisect_left(keys, key + "/")
    if where < len(keys) and keys[where].startswith(key + "/"):
        return True
    return any((key + "/").startswith(folder) or folder.startswith(key + "/") for folder in sparse)


# ---------------------------------------------------------------------------------------------------------------------
# Moving
# ---------------------------------------------------------------------------------------------------------------------

def _copy_readable(source, target):
    """Copies what can be read of a file, a link or a folder to `target`, which is not there: links stay links and are
    never followed, modes and times are kept (the mode of `target` itself too: see `_sealed`), what cannot be read or is
    not a file (a socket, a device) is left out. Answers how many entries were left out."""
    left, folders, work = 0, [], [(source, target)]
    while work:
        origin, copy = work.pop()
        try:
            info = os.lstat(origin)
        except OSError:
            left += 1
            continue
        if stat.S_ISLNK(info.st_mode):
            try:
                os.symlink(os.readlink(origin), copy)
            except OSError:
                left += 1
        elif stat.S_ISDIR(info.st_mode):
            os.mkdir(copy, _COPY_MODE)   # its own mode comes last, once what is inside is in
            folders.append((copy, info))
            try:
                names = sorted(os.listdir(origin))
            except OSError:
                left += 1
                continue
            work.extend((os.path.join(origin, name), os.path.join(copy, name)) for name in reversed(names))
        elif stat.S_ISREG(info.st_mode):
            left += not _copy_file(origin, copy, info)
        else:
            left += 1
    for copy, info in reversed(folders[1:]):   # the top one waits until it is renamed: a read-only folder cannot be
        _sealed(copy, info)
    return left


def _sealed(copy, info):
    """Gives a copied folder the mode and the times of the one it copies."""
    os.chmod(copy, stat.S_IMODE(info.st_mode))
    os.utime(copy, ns=(info.st_atime_ns, info.st_mtime_ns))


def _copy_file(origin, copy, info):
    """Copies one file with its mode and times, without following a link put in its place; False when it cannot be
    read. A copy that fails half way is removed."""
    try:
        reader = os.open(origin, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        writer = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(reader, "rb", closefd=False) as source, os.fdopen(writer, "wb", closefd=False) as out:
                shutil.copyfileobj(source, out)
            os.fchmod(writer, stat.S_IMODE(info.st_mode))
        except OSError:
            os.close(writer)
            os.unlink(copy)
            return False
        os.close(writer)
        os.utime(copy, ns=(info.st_atime_ns, info.st_mtime_ns))
        return True
    finally:
        os.close(reader)


def _kept_copy(source, target, why):
    """The rename was refused for want of a right (a folder a container made as root, a read-only folder): the readable
    part is copied next to `target` and renamed to it, and the original stays. The journal says so before the last
    rename, so that a crash after it is a copy that was kept, not a conflict."""
    partial = target + ".partial"
    _discard(partial)
    left = _copy_readable(source, partial)
    _step("relocate:copy")
    journal("copied", src=_rel(source), dst=_rel(target), why=why, left=left)
    _step("relocate:copied")
    os.rename(partial, target)
    _step("relocate:renamed")
    if os.path.isdir(target) and not os.path.islink(target):
        _sealed(target, os.lstat(source))
    return "copied"


def _across_devices(source, target, why):
    """The rename crossed a filesystem. A file or a link is copied, checked byte for byte and removed from where it was;
    a folder is left where it is, since a copy of a tree that is then deleted cannot be undone half way."""
    info = os.lstat(source)
    partial = target + ".partial"
    if stat.S_ISLNK(info.st_mode):
        _discard(partial)
        os.symlink(os.readlink(source), partial)
    elif stat.S_ISREG(info.st_mode):
        _discard(partial)
        if not _copy_file(source, partial, info):
            return _stayed(source, target, why)
        if os.path.getsize(partial) != info.st_size or not filecmp.cmp(source, partial, shallow=False):
            _discard(partial)
            raise OSError(errno.EIO, f"The copy of {_rel(source)} differs from it")
    else:
        return _stayed(source, target, why)
    _step("relocate:copy")
    journal("copied", src=_rel(source), dst=_rel(target), why=why, left=0)
    _step("relocate:copied")
    os.rename(partial, target)
    _step("relocate:renamed")
    try:
        os.unlink(source)
    except OSError:
        return "copied"
    return "moved"


def _stayed(source, target, why):
    """The move announced in the journal was not made: a line says so, for whoever reads the journal for what happened.
    `undo_script` ignores it."""
    journal("stayed", src=_rel(source), dst=_rel(target), why=why)
    return "stayed"


def _finished(source, target):
    """A copy made across devices whose original was not removed yet (a crash between the two): the same file or the
    same link, so the original goes. A folder, or a file that differs, is left; a folder copy that a crash left without
    its mode gets it."""
    kinds = [stat.S_IFMT(os.lstat(path).st_mode) for path in (source, target)]
    if kinds == [stat.S_IFDIR, stat.S_IFDIR]:   # the crash may have come before the folder had its mode
        original = os.lstat(source)
        # Only a copy that still has the mode it was made with: one made writable since is left as it is
        if stat.S_IMODE(os.lstat(target).st_mode) == _COPY_MODE and stat.S_IMODE(original.st_mode) != _COPY_MODE:
            _sealed(target, original)
    same = kinds[0] == kinds[1] and (
        (kinds[0] == stat.S_IFLNK and os.readlink(source) == os.readlink(target))
        or (kinds[0] == stat.S_IFREG and filecmp.cmp(source, target, shallow=False)))
    if not same:
        return "copied"
    try:
        os.unlink(source)
    except OSError:
        return "copied"
    return "moved"


def _holds_project(path):
    """Whether `path` is the project folder or a folder above it, by name and by the real place of its parent (the
    project reached through a link is the same project). The last name is not resolved: a link to the project moves as
    a link."""
    path = os.path.normpath(path)
    root, (parent, name) = os.path.normpath(str(paths.ROOT)), os.path.split(path)
    return _inside(root, path) or _inside(os.path.realpath(root), os.path.join(os.path.realpath(parent), name))


def relocate(src, dst, why: str) -> str:
    """Moves a file, a link or a folder to a place that is free, whole and with its mode, owner and unreadable children,
    by one rename, and writes it to the journal first. A path is relative to the project or absolute. Answers
    "gone" when `src` is not there (already moved), "conflict" when `dst` is (nothing is overwritten or merged),
    "moved", "copied" when the rename was refused for a right and the readable part was copied while the original
    stays (a repeat answers "copied" again and copies nothing), and "stayed" for a folder on another filesystem or one
    that is busy. A link moves as a link, never followed. Refused (raises) when a path is empty, when `src` is the
    project folder or one above it, when git tracks `src`, when `dst` is `src` or inside it; any other error of the
    disk is raised, never swallowed. The caller's locks keep another writer away between the check and the rename."""
    if not os.fspath(src).strip() or not os.fspath(dst).strip():   # `_abs` would make of it the project folder
        raise Refused("A path to move is empty")
    source, target = _abs(src).rstrip(os.sep) or os.sep, _abs(dst).rstrip(os.sep) or os.sep
    if _holds_project(source):
        raise Refused("The project folder is not moved")
    if not os.path.lexists(source):
        return "gone"
    if _inside(os.path.normpath(target), os.path.normpath(source)):
        raise Refused(f"{_rel(source)} cannot be moved to {_rel(target)}, which is the same place or inside it")
    if tracked(source):
        raise Refused(f"{_rel(source)} is tracked by git: it is not moved")
    if os.path.lexists(target):
        return _finished(source, target) if _copied_before(source, target) else "conflict"
    _step("relocate:before")
    _parents(target)
    journal("move", src=_rel(source), dst=_rel(target), why=why)
    _step("relocate:journaled")
    try:
        os.rename(source, target)
    except PermissionError:
        return _kept_copy(source, target, why)
    except OSError as error:
        if error.errno == errno.EXDEV:
            return _across_devices(source, target, why)
        if error.errno in (errno.EBUSY, errno.ETXTBSY):
            return _stayed(source, target, why)
        raise
    _step("relocate:renamed")
    return "moved"


# ---------------------------------------------------------------------------------------------------------------------
# Paths written again
# ---------------------------------------------------------------------------------------------------------------------

# A path that leaves the folder of the file it is written in, up to a variable when one ends it: whole segments
UPWARD = re.compile(r"(?<![\w./])\.\.(?:/[\w.-]+)*/?(?![\w./-])")
# The lines that name files of this machine: the keys of a path, and the items of a list of paths. A command and the
# value of a variable are read in the container: they stay as written, as a mapping or as a list.
PATH_KEY = re.compile(r"\s*(?:-\s+)?(?:build|context|env_file|file|path|source):")
PATH_LISTS = {"include", "volumes", "env_file", "path", "additional_contexts", "label_file"}
# A key whose value is the block under it, after a tag (!override, !reset) or an anchor
BLOCK = re.compile(r"(\s*(?:-\s+)?)([\w.-]+):(?:\s+[&!][^\s#]*)*\s*(?:#.*)?$")


def _naming_files(text):
    """The lines of a compose file, each with whether it names files of this machine: a key of a path, or an item of
    a list of paths. An item's list is the nearest key above it that is less indented, or as indented: a list may be
    written flush with its key."""
    keys = []   # the keys whose block the line is in: (column, key)
    for line in text.splitlines(keepends=True):
        if not line.strip() or line.lstrip().startswith("#"):
            yield line, False
            continue
        indent, item = len(line) - len(line.lstrip()), bool(re.match(r"\s*-\s", line))
        keys = [(column, key) for column, key in keys if column < indent or (item and column == indent)]
        yield line, bool(PATH_KEY.match(line) or (item and keys and keys[-1][1] in PATH_LISTS))
        block = BLOCK.match(line)
        if block:
            keys.append((len(block.group(1)), block.group(2)))


def _relative(target, start):
    """`target` seen from the folder `start`, both relative to the project: by name, without the working folder that
    `posixpath.relpath` asks for."""
    there, here = [part for part in target.split("/") if part not in ("", ".")], \
        [part for part in start.split("/") if part not in ("", ".")]
    if ".." in here:
        return posixpath.relpath(target, start)
    common = 0
    while common < min(len(there), len(here)) and there[common] == here[common]:
        common += 1
    return "/".join([".."] * (len(here) - common) + there[common:]) or "."


def repoint(text: str, was: str, now: str, table: list) -> str:
    """A compose file's text with the paths that leave its folder written again. The file went from the folder `was` to
    the folder `now` (the same when it did not move), and `table` lists what moved, as [(old path, new path)] relative
    to the project: a file, a folder, or a whole kind (`addons` to `.lab/catalogs/ns/addons`); the longest old path that
    holds a target wins, whatever the order. A path that stays in the folder is left as written: what it names moves
    with the file. So is, in a file that did not move, every path but the ones to what moved. Only values on path keys
    and in lists of paths are touched (`_naming_files`): a command or a variable's value that holds `..` is read in the
    container and stays."""
    pairs = sorted(((old.strip("/"), new.strip("/")) for old, new in table
                    if isinstance(old, str) and isinstance(new, str) and old.strip("/")),
                   key=lambda pair: -len(pair[0]))
    was, now = posixpath.normpath(was), posixpath.normpath(now)

    def path(found):
        written = found.group()
        target = posixpath.normpath(posixpath.join(was, written))
        moved = False
        for old, new in pairs:
            if target == old or target.startswith(old + "/"):
                target, moved = new + target[len(old):], True
                break
        if was == now and not moved:
            return written
        relative = _relative(target, now)
        relative = relative if relative.startswith("..") else f"./{relative}"   # without it, a volume would read a name
        return relative + ("/" if written.endswith("/") else "")
    return "".join(UPWARD.sub(path, line) if named else line for line, named in _naming_files(text))


# ---------------------------------------------------------------------------------------------------------------------
# Stars and tested sets follow an item
# ---------------------------------------------------------------------------------------------------------------------

def _object(file):
    """The JSON object a file of this machine holds, None when it is not there, is not readable or is not an object:
    a file that does not read is left as it is, never replaced by what the Lab made of it."""
    try:
        found = json.loads((paths.ROOT / file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def _pairs(mapping):
    """[(kind, old id, new id)] of a mapping {"kind:old id": new id}, what is not one left out."""
    found = []
    for key, new in mapping.items() if isinstance(mapping, dict) else ():
        kind, _, old = key.partition(":") if isinstance(key, str) else ("", "", "")
        if kind in paths.KINDS and isinstance(new, str) and old and new and old != new and "\0" not in key + new:
            found.append((kind, old, new))
    return found


def _replaced(file, text):
    """Replaces a file of this machine whole, with the mode it has: a file that was 0600 does not become 0644."""
    path = paths.ROOT / file
    paths.write_atomic(path, text, mode=stat.S_IMODE(path.stat().st_mode))


def _stars(pairs):
    """starred.json with every old id of the pairs replaced by the new one; the ids that changed, [] when none."""
    found = _object(STARRED)
    changed = []
    for kind, old, new in pairs:
        ids = found.get(kind) if found else None
        if isinstance(ids, list) and old in ids:
            found[kind] = sorted({uid for uid in ids if isinstance(uid, str) and uid != old} | {new})
            changed.append((kind, old, new))
    if changed:
        _step("rekey:starred")
        _replaced(STARRED, json.dumps(found, indent=2) + "\n")
    return changed


def _union(old, new):
    """The tested sets of two ids as one list, oldest first: the same versions and images once (the latest), the last
    TESTED_KEPT."""
    both = sorted([*(new if isinstance(new, list) else []), *old],
                  key=lambda one: str(one.get("at", "")) if isinstance(one, dict) else "")   # stable: new stays first
    seen, kept = set(), []
    for one in reversed(both):
        mark = json.dumps([one.get("versions"), one.get("images")], sort_keys=True) if isinstance(one, dict) else None
        if mark is None or mark not in seen:
            kept.append(one)
            seen.add(mark)
    return kept[:TESTED_KEPT][::-1]


def _tested(pairs):
    """tested.json with the sets of every old playground id under the new one, joined with the ones it has; the pairs
    that changed it."""
    found = _object(TESTED)
    changed = []
    for kind, old, new in pairs:
        if kind == "playground" and found and isinstance(found.get(old), list):
            found[new] = _union(found.pop(old), found.get(new))
            changed.append((kind, old, new))
    if changed:
        _step("rekey:tested")
        _replaced(TESTED, json.dumps(found, indent=2, sort_keys=True))
    return changed


def rekey(mapping: dict) -> None:
    """Follows items to their new ids in `.lab/starred.json` and `.lab/tested.json`: the mapping is {"kind:old id": new
    id} ("playground:bank": "local/bank"). A star and a set of tested versions move to the new id, and where it has some
    already the two are joined (the union of stars; the sets of both, the same versions once, the latest twenty). Each
    file is replaced whole, and only when it changes; a file that does not read is not touched; an id nothing refers
    to is not an error. Repeating it changes nothing."""
    pairs = _pairs(mapping)
    changed = _stars(pairs) + _tested(pairs)
    if changed:
        journal("rekey", ids={f"{kind}:{old}": new for kind, old, new in changed})


# ---------------------------------------------------------------------------------------------------------------------
# Settings held for an item that is in no catalog
# ---------------------------------------------------------------------------------------------------------------------

def _item(kind, name):
    """Checks the two words of an item before a path is made of them: a hostile id never reaches the disk."""
    if kind not in paths.KINDS:
        raise Refused(f"{kind} is not a kind of item")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise Refused(f"{name} is not the name of an item")


def _catalog(ns):
    if not isinstance(ns, str) or not _NS.fullmatch(ns) or ns == paths.LOCAL:
        raise Refused(f"{ns} is not the name of a catalog")


def held_dir(kind, name):
    """Where the machine files of an item wait when it is in no installed catalog: `.lab/held/<kind>s/<name>`."""
    _item(kind, name)
    return paths.ROOT / HELD / f"{kind}s" / name


def _plain(path):
    """Whether a path is a real folder, not a link nor a file: what the held area is made of."""
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def has_held(kind, name):
    """Whether settings wait for an item: a file in its held folder, or a star or a tested set under its bare id. The
    caller adds the other half of the rule: that the bare id is not an item of the default catalog. A
    held folder that cannot be trusted counts as settings that wait, so that `adopt_held` says why it refuses."""
    _item(kind, name)
    try:
        folder = _held_chain(kind, name)
    except Refused:
        return True
    if folder:
        with os.scandir(folder) as entries:
            if next(entries, None) is not None:
                return True
    stars, tested = (_object(STARRED) or {}).get(kind), _object(TESTED) or {}
    return (isinstance(stars, list) and name in stars) or (kind == "playground" and name in tested)


def _held_chain(kind, name):
    """The held folder of an item when it is there and nothing on the way is a link: a link there would take the move
    to a place the Lab never meant."""
    folder = held_dir(kind, name)
    chain = [paths.ROOT / HELD, paths.ROOT / HELD / f"{kind}s", folder]
    if not os.path.lexists(folder):
        return None
    if not all(_plain(link) for link in chain):
        raise Refused(f"The held settings of {name} are not in a plain folder")
    return folder


def _merge(origin, home, inside, found, depth=0, why="adopt", everywhere=False):
    """Moves what is in the folder `origin` into `home`, a folder that exists. An entry that is a container (a folder
    `home` has too, other than the whole entries at the top, or at any depth with `everywhere`) is merged; every other entry
    is moved whole and never over what is there. Adds {"path", "result"} to `found` for each."""
    for name in sorted(os.listdir(origin)):
        here, there = os.path.join(origin, name), os.path.join(home, name)
        whole = (depth == 0 or everywhere) and name in WHOLE
        if _plain(here) and _plain(there) and depth < DEEP and not whole:
            _merge(here, there, f"{inside}{name}/", found, depth + 1, why, everywhere)
            continue
        _step(f"{why}:entry")
        found.append({"path": f"{inside}{name}", "result": relocate(here, there, why)})


def merge(origin: str, home: str, why: str, everywhere: bool = False) -> list:
    """Moves what is in the folder `origin` into the folder `home`, which exists, by `relocate` and never over what is there:
    a folder both have is merged, down to `DEEP` levels; every other entry is moved whole, and so is a `.state` or a `secrets`
    folder (records and credentials are not meant to be mixed) at the top of `origin`, or at any depth with `everywhere`.
    Answers [{"path": relative to `origin`, "result": what `relocate` answered}] for each entry, "conflict" for the ones whose
    place was taken (they are still in `origin`, for the caller to put aside), empty when `origin` is empty. A path is relative
    to the project or absolute. Repeating it, or running it after a crash, finishes what is left. Refused (raises) as
    `relocate` is, and when `origin` or `home` is not a plain folder."""
    if not (_plain(_abs(origin)) and _plain(_abs(home))):
        raise Refused(f"{_rel(origin)} and {_rel(home)} have to be folders to be merged")
    found = []
    _merge(_abs(origin), _abs(home), "", found, why=why, everywhere=everywhere)
    return found


def _emptied(folder):
    """Removes the held folder of an item once nothing is left in it: its folders from the bottom, then the kind's
    folder when it is empty too, never `.lab/held` itself and never a folder that holds a file."""
    for here, _, _ in os.walk(folder, topdown=False):
        try:
            os.rmdir(here)
        except OSError:
            continue
    try:
        os.rmdir(os.path.dirname(folder))
    except OSError:
        pass


def _personal_files():
    """The compose files of the items of this machine, which `.lab/catalogs/local` holds: an add-on's and a
    playground's compose.yml, a setup's file. A folder of a kind may be a link to the person's own folder."""
    base, found = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL, []
    for kind in ("addon", "playground", "setup"):
        folder = base / f"{kind}s"
        if not folder.is_dir():
            continue
        for name in sorted(os.listdir(folder)):
            if kind == "setup" and name.endswith(".yml"):
                found.append(folder / name)
            elif kind != "setup":
                found.append(folder / name / "compose.yml")
    return found


def _repoint_personal(old, new):
    """Writes again the includes of the items of this machine that name the folder or file `old` (relative to the
    project) as `new`, and journals each file. A file that is a link, or does not read as UTF-8, is left; a file
    changes only when it names `old`."""
    for file in _personal_files():
        if file.is_symlink() or not file.is_file():
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        folder = _rel(file.parent)
        written = repoint(text, folder, folder, [(old, new)])
        if written != text:
            _step("adopt:repoint")
            _replaced(file, written)
            journal("repoint", path=_rel(file), old=old, new=new)


def _where(ns, kind, name):
    """Where an item is in a catalog, relative to the project: its folder, or its file for a setup."""
    file = paths.CATALOG_FILES[kind].format(ns=ns, name=name)
    return file if kind == "setup" else posixpath.dirname(file)


def adopt_held(ns: str, kind: str, name: str, repoint_from: Optional[str] = None) -> list:
    """Gives a catalog's item the settings that waited for it. The files of
    `.lab/held/<kind>s/<name>` move into the item's folder in the installed catalog `ns`, at the same relative path and
    never over a file that is there (a conflict stays in the held area and is reported). Its star and its tested sets
    move from the bare id to `@ns/<name>` with `rekey` (the default catalog keeps the bare ids). The includes of the
    items of this machine are written again from the catalog `repoint_from` (the default catalog's when absent) to `ns`.
    Answers [{"path": relative to the item's folder, "result": "moved" | "copied" | "conflict" | "stayed"}], empty when
    no file was there. Repeating it, or running it after a crash, finishes what is left. A setup has no folder, so only
    its star moves. Nothing is done when the item is not in `ns`, or when `ns` is not the default catalog and the
    default catalog has the name: that bare id is the default item's. Refused (raises) for an id that is not one and for
    a held folder that is a link. It never changes what is on, nor the on file."""
    _catalog(ns)
    _item(kind, name)
    if repoint_from is not None:
        _catalog(repoint_from)
    default = paths.default_ns()
    if not os.path.isfile(_abs(paths.CATALOG_FILES[kind].format(ns=ns, name=name))):
        return []
    if ns != default and default and os.path.isfile(_abs(paths.CATALOG_FILES[kind].format(ns=default, name=name))):
        return []
    found = []
    folder = _held_chain(kind, name) if kind != "setup" else None
    if folder:
        _merge(str(folder), _abs(_where(ns, kind, name)), "", found)
        _step("adopt:cleanup")
        _emptied(str(folder))
    if ns != default:
        _step("adopt:rekey")
        rekey({f"{kind}:{name}": f"@{ns}/{name}"})
        origin = repoint_from or default
        if origin and origin != ns:
            _repoint_personal(_where(origin, kind, name), _where(ns, kind, name))
    return found


# ---------------------------------------------------------------------------------------------------------------------
# Going back
# ---------------------------------------------------------------------------------------------------------------------

# The shell that decides, for a file the backup is about to be put over, whether to keep what is there. It is defined once and
# evaluated by the script, and handed to the `sh` that `find` starts to look at a folder file by file, so it holds no single quote.
# `counts` says what "written since the update" means (see `_written_since`): `newer` a file newer than the script itself, `any`
# every file that differs from the backup, `none` no file (a migration that did not finish: what is there is its own work).
_HELPERS = """
present() { [ -e "$1" ] || [ -L "$1" ]; }
written() {
  case "$counts" in
    any) return 0 ;;
    none) return 1 ;;
  esac
  [ -e "$ref" ] || return 0
  case "$1" in -*) set -- "./$1" ;; esac
  [ -n "$(find "$1" -newer "$ref" 2>/dev/null)" ]
}
aside() {
  present "$2" || return 0
  cmp -s -- "$1" "$2" 2>/dev/null && return 0
  written "$2" || return 0
  if [ -L "$2" ] || [ ! -f "$2" ]; then
    printf "Left %s: it is not a file\\n" "$2" >&2
    return 1
  fi
  ending=.after-update
  count=1
  while present "$2$ending" || present "$1$ending"; do
    count=$((count + 1))
    ending=.after-update.$count
  done
  kept=$2$ending
  if cp -p -- "$2" "$kept"; then
    printf "Kept %s as %s\\n" "$2" "$kept" >&2
  else
    printf "Could not keep %s\\n" "$2" >&2
    return 1
  fi
}
"""

SCRIPT = """#!/bin/sh
# Puts back what the Lab moved when it updated this folder to the layout of the catalogs. Run it with:
#   sh .lab/migration/undo.sh
# Each file and folder goes back to where it was, in the reverse order, and never over something that is there. The on file, the
# stars, the tested sets, the platform copies and the compose texts come back from the backup; one that was written since the update
# is kept beside it first, as NAME.after-update. What was made in the new places after the update, and is not in the journal, stays
# where it is.
cd "$(dirname "$0")/../.." || exit 1
failed=0
marker=@MARKER@
ref=@UNDO@
counts=any
HELPERS='@HELPERS@'
eval "$HELPERS"
back() {
  if present "$2"; then
    if present "$1"; then
      printf 'Left %s: %s is there\\n' "$2" "$1" >&2
    else
      mkdir -p -- "$(dirname -- "$1")" && mv -- "$2" "$1" || { printf 'Could not move %s back\\n' "$2" >&2; failed=1; }
    fi
  fi
}
keep_tree() {
  case "$1" in -*) set -- "./$1" "$2" ;; esac
  find "$1" ! -type d -exec sh -c "$HELPERS"'
    from=$1 to=$2 counts=$3 ref=$4
    shift 4
    bad=0
    for file; do
      aside "$file" "$to${file#"$from"}" || bad=1
    done
    exit $bad' sh "$1" "$2" "$counts" "$ref" {} +
}
restore() {
  if [ -d "$1" ]; then
    mkdir -p -- "$2" && keep_tree "$1" "$2" && cp -pR -- "$1/." "$2/" || { printf 'Could not restore %s\\n' "$2" >&2; failed=1; }
  elif [ -f "$1" ]; then
    mkdir -p -- "$(dirname -- "$2")" && aside "$1" "$2" && cp -p -- "$1" "$2" || { printf 'Could not restore %s\\n' "$2" >&2; failed=1; }
  else
    printf 'No copy of %s\\n' "$2" >&2
  fi
}
drop() { rm -f -- "$1" || failed=1; }
""".replace("@MARKER@", shlex.quote(MARKER)).replace("@UNDO@", shlex.quote(UNDO)).replace("@HELPERS@", _HELPERS)


def _writable(*names):
    """Whether names can be written to a script, which is UTF-8: a name that is not cannot be quoted in it."""
    try:
        for name in names:
            name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _written_since(removes) -> str:
    """The line of the script that says what "written since the update" means for the files it puts back over (`counts`, in
    `_HELPERS`). The migration is over when its marker is there. A file that differs from the backup is then its own work
    unless it is newer than the script, which the migration writes last (and writes again when it translates a line a stale
    Lab brought back): nothing it writes comes after. The marker is not the measure, since closing the notice writes it again.
    With no marker, but with something the migration left that the script removes (its witness, its plan), it stopped: what is
    there is its own half work and goes back as the backup has it. With nothing of either, the update is already undone:
    whatever differs from the backup was written after it."""
    witnesses = [shlex.quote(path) for path in removes if path != MARKER and _writable(path)]
    middle = f"elif {' || '.join(f'present {one}' for one in witnesses)}; then counts=none; " if witnesses else ""
    return f'if present "$marker"; then counts=newer; {middle}else counts=any; fi'


def undo_script(entries=None, restores=(), removes=()) -> str:
    """The text of the POSIX shell script that undoes the moves of the journal. For each move, the last first, it
    calls `back` with where the item was and where it is, and puts it back only when the place it went to is there and
    the one it came from is free. Then it calls `restore` for each pair of `restores` (a file or a folder of the backup
    copied back over its path: the on file, the stars, the compose texts) and `drop` for each of `removes` (the marker
    of the layout). A restore never loses what was written since the update: a file it would put over that differs from the
    backup and is newer than the script is first copied beside itself as NAME.after-update (NAME.after-update.2
    when that is taken), and one it cannot copy, or that is a link or a folder where a file goes, is left and said (see
    `_written_since` for what "since" is). Every name goes through `shlex.quote`, so a file called `a b$(touch x)` is a name,
    not a command. A move that was repeated after a crash is written once, where it was made; one made again after it was
    undone (a shadow link taken off and put back) is written each time; a name that cannot be written in UTF-8 is left out and
    the script says so. `entries` is the journal when none is given. The script exits 0 when all it could do was done."""
    entries = read_journal() if entries is None else entries
    moves, last = [], {}   # the moves in the order they were made; where each pair was last written
    for entry in entries:
        pair = (entry.get("src"), entry.get("dst"))
        if entry.get("op") == "move" and all(isinstance(part, str) and part for part in pair):
            before = last.get(pair)
            if before is not None and last.get(pair[::-1], -1) < before:   # nothing moved it back since: a repeat
                moves[before] = None   # the rename was made by the later line
            last[pair] = len(moves)
            moves.append(pair)
    lines, skipped = [_written_since(removes)], 0
    for src, dst in filter(None, reversed(moves)):
        if _writable(src, dst):
            lines.append(f"back {shlex.quote(src)} {shlex.quote(dst)}")
        else:
            skipped += 1
    for copy, path in restores:
        if _writable(copy, path):
            lines.append(f"restore {shlex.quote(copy)} {shlex.quote(path)}")
    for path in removes:
        if _writable(path):
            lines.append(f"drop {shlex.quote(path)}")
    if skipped:
        lines.append(f"# {skipped} moved name{'s' if skipped > 1 else ''} not valid UTF-8: put back by hand "
                     f"from {JOURNAL}")
    return SCRIPT + "".join(f"{line}\n" for line in lines) + "exit $failed\n"
