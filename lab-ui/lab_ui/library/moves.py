"""The primitives that move things on this machine, shared by the shadow link of a working copy, Share and the renames of a
catalog: `relocate` moves a file or a folder and never overwrites, `repoint` writes again the paths of a compose file that
moved, `rekey` follows an item to its new id in the stars and the tested sets.

State based and idempotent: every function reads the disk and does what is left to do, so a run that crashed is run
again, never undone. Nothing here runs Docker, git or the network: whether git tracks a file is read from the index itself.
`STEP` is the hook a test sets to crash a run between two steps.

One folder of `.lab` is only for the person (0700, and so is whatever is made in it): `migration/`, with what was kept
aside."""

import errno
import filecmp
import hashlib
import json
import os
import posixpath
import re
import shutil
import stat
import struct
import unicodedata

from .. import paths

# A test sets it to a callable that takes the name of the step it is about to take and raises to stop the run there:
# rekey:starred and :tested, and merge:entry for each entry `merge` moves
STEP = None

MIGRATION = f"{paths.LAB_DIR}/migration"   # what was kept aside
STARRED = f"{paths.LAB_DIR}/starred.json"
TESTED = f"{paths.LAB_DIR}/tested.json"
TESTED_KEPT = 20   # per playground: the latest sets
# What a whole entry is at the top of a folder that is merged: a folder of seed records or of credential files moves in one
# piece and is never merged into another, since records are not meant to be mixed. Any other folder is a container.
WHOLE = (".state", "secrets")
DEEP = 16   # how far down a folder is merged

_COPY_MODE = 0o700   # what a copy of a folder is made with, until it is renamed and gets the mode of the original


class Refused(Exception):
    """A move the Lab will not make: a path is empty, the source is the project folder or one above it, git tracks the
    source, or a path is inside itself."""


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
    top = _abs(MIGRATION)
    private = _inside(target, top)
    for folder in reversed(missing):
        try:
            os.mkdir(folder, 0o700 if private else 0o777)
        except FileExistsError:   # another run made it between the look and the mkdir: fine when it is a real folder
            if os.path.islink(folder) or not os.path.isdir(folder):
                raise
    if private and os.path.isdir(top) and not os.path.islink(top):
        os.chmod(top, 0o700)


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


# ---------------------------------------------------------------------------------------------------------------------
# What git tracks, read from its index
# ---------------------------------------------------------------------------------------------------------------------

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
    """The keys of what an index file lists, None when it cannot be read; nothing when there is none yet (a repository where
    nothing was added). A sparse index lists a folder whole, with a slash at the end."""
    try:
        with open(index, "rb") as f:
            names = _parse_index(f.read())
    except FileNotFoundError:
        return []
    except OSError:
        return None
    return None if names is None else [_key(name.decode("utf-8", "surrogateescape")) for name in names]


def tracked(path):
    """Whether git tracks a file, or anything below a folder, in the checkout that holds it. It is the answer of
    `git ls-files`, read from the index file and not asked of git: a move may start in a folder a container wrote, and the
    configuration of a `.git` it planted there can make even `ls-files` run a command. The checkout is the nearest folder above
    that has a `.git`, found from the real place of the parent (a catalog linked in place is judged in its own checkout). True
    when it cannot tell: a checkout whose index is not readable is treated as tracked. False outside any checkout."""
    absolute = _abs(path).rstrip(os.sep) or os.sep
    parent, name = os.path.split(absolute)
    real = os.path.realpath(parent)
    top = _checkout(real)
    if top is None:
        return False
    git = _git_dir(top)
    keys = _listed(os.path.join(git, "index")) if git else None
    if keys is None:
        return True
    key = _key(os.path.relpath(os.path.join(real, name), top))
    return any(one == key or one.startswith(key + "/") or (one.endswith("/") and key.startswith(one)) for one in keys)


# ---------------------------------------------------------------------------------------------------------------------
# Moving
# ---------------------------------------------------------------------------------------------------------------------

def _copy_readable(source, target):
    """Copies what can be read of a file, a link or a folder to `target`, which is not there: links stay links and are
    never followed, modes and times are kept (the mode of `target` itself too: see `_sealed`), what cannot be read or is
    not a file (a socket, a device) is left out."""
    folders, work = [], [(source, target)]
    while work:
        origin, copy = work.pop()
        try:
            info = os.lstat(origin)
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode):
            try:
                os.symlink(os.readlink(origin), copy)
            except OSError:
                pass
        elif stat.S_ISDIR(info.st_mode):
            os.mkdir(copy, _COPY_MODE)   # its own mode comes last, once what is inside is in
            folders.append((copy, info))
            try:
                names = sorted(os.listdir(origin))
            except OSError:
                continue
            work.extend((os.path.join(origin, name), os.path.join(copy, name)) for name in reversed(names))
        elif stat.S_ISREG(info.st_mode):
            _copy_file(origin, copy, info)
    for copy, info in reversed(folders[1:]):   # the top one waits until it is renamed: a read-only folder cannot be
        _sealed(copy, info)


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


def _kept_copy(source, target):
    """The rename was refused for want of a right (a folder a container made as root, a read-only folder): the readable
    part is copied next to `target` and renamed to it, and the original stays."""
    partial = target + ".partial"
    _discard(partial)
    _copy_readable(source, partial)
    os.rename(partial, target)
    if os.path.isdir(target) and not os.path.islink(target):
        _sealed(target, os.lstat(source))
    return "copied"


def _across_devices(source, target):
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
            return "stayed"
        if os.path.getsize(partial) != info.st_size or not filecmp.cmp(source, partial, shallow=False):
            _discard(partial)
            raise OSError(errno.EIO, f"The copy of {_rel(source)} differs from it")
    else:
        return "stayed"
    os.rename(partial, target)
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


def relocate(src, dst) -> str:
    """Moves a file, a link or a folder to a place that is free, whole and with its mode, owner and unreadable children,
    by one rename. A path is relative to the project or absolute. Answers "gone" when `src` is not there (already moved),
    "conflict" when `dst` is (nothing is overwritten or merged), "moved", "copied" when the rename was refused for a right
    and the readable part was copied while the original stays, and "stayed" for a folder on another filesystem or one that
    is busy. A link moves as a link, never followed. Refused (raises) when a path is empty, when `src` is the
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
        return "conflict"
    _parents(target)
    try:
        os.rename(source, target)
    except PermissionError:
        return _kept_copy(source, target)
    except OSError as error:
        if error.errno == errno.EXDEV:
            return _across_devices(source, target)
        if error.errno in (errno.EBUSY, errno.ETXTBSY):
            return "stayed"
        raise
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
    to the project: a file or a folder; the longest old path that holds a target wins, whatever the order. A path that
    stays in the folder is left as written: what it names moves
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
    """starred.json with every old id of the pairs replaced by the new one."""
    found = _object(STARRED)
    changed = False
    for kind, old, new in pairs:
        ids = found.get(kind) if found else None
        if isinstance(ids, list) and old in ids:
            found[kind] = sorted({uid for uid in ids if isinstance(uid, str) and uid != old} | {new})
            changed = True
    if changed:
        _step("rekey:starred")
        _replaced(STARRED, json.dumps(found, indent=2) + "\n")


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
    """tested.json with the sets of every old playground id under the new one, joined with the ones it has."""
    found = _object(TESTED)
    changed = False
    for kind, old, new in pairs:
        if kind == "playground" and found and isinstance(found.get(old), list):
            found[new] = _union(found.pop(old), found.get(new))
            changed = True
    if changed:
        _step("rekey:tested")
        _replaced(TESTED, json.dumps(found, indent=2, sort_keys=True))


def rekey(mapping: dict) -> None:
    """Follows items to their new ids in `.lab/starred.json` and `.lab/tested.json`: the mapping is {"kind:old id": new
    id} ("playground:bank": "local/bank"). A star and a set of tested versions move to the new id, and where it has some
    already the two are joined (the union of stars; the sets of both, the same versions once, the latest twenty). Each
    file is replaced whole, and only when it changes; a file that does not read is not touched; an id nothing refers
    to is not an error. Repeating it changes nothing."""
    pairs = _pairs(mapping)
    _stars(pairs)
    _tested(pairs)


# ---------------------------------------------------------------------------------------------------------------------
# Folders merged
# ---------------------------------------------------------------------------------------------------------------------

def _plain(path):
    """Whether a path is a real folder, not a link nor a file."""
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _merge(origin, home, inside, found, depth=0, everywhere=False):
    """Moves what is in the folder `origin` into `home`, a folder that exists. An entry that is a container (a folder
    `home` has too, other than the whole entries at the top, or at any depth with `everywhere`) is merged; every other entry
    is moved whole and never over what is there. Adds {"path", "result"} to `found` for each."""
    for name in sorted(os.listdir(origin)):
        here, there = os.path.join(origin, name), os.path.join(home, name)
        whole = (depth == 0 or everywhere) and name in WHOLE
        if _plain(here) and _plain(there) and depth < DEEP and not whole:
            _merge(here, there, f"{inside}{name}/", found, depth + 1, everywhere)
            continue
        _step("merge:entry")
        found.append({"path": f"{inside}{name}", "result": relocate(here, there)})


def merge(origin: str, home: str, everywhere: bool = False) -> list:
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
    _merge(_abs(origin), _abs(home), "", found, everywhere=everywhere)
    return found


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
