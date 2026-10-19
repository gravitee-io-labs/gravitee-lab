"""What the rules read: a catalog as a list of entries (a git mode, a path, a size) and the content of its regular files,
and the commits a change adds. The rules never open a file or run git themselves; they ask a `Tree`, so the same rule judges
the folder an author works in (`FolderTree`), a commit of a bare repository that no one has checked out (`GitTree`, for
`check --pr`) and the listing of a store or the files Share staged (`ListTree`).

The tree is hostile. It is the input of the rules, written by someone who wants a rule to run their code, to read a file of the
reader's machine, or to stop. So nothing here executes anything of it:

- `FolderTree` asks git only for `ls-files` (the index lists what the history would hold, so a link or a submodule is seen as
  one) and reads a file through directory handles opened with O_NOFOLLOW: a link anywhere in the path ends the read. Without a
  git repository it walks the folder with `lstat`, and a link is a link. It runs git through `gitx`, which turns hooks and
  the file system monitor off and passes no variable of the shell.
- `GitTree` reads a commit's objects with `ls-tree` and `cat-file`: no checkout, so no filter driver, no hook, no
  `.gitattributes` program and no `Makefile` can run.
- `read` answers only for a regular file of the listing: a link, a submodule or a name that is not in it gives None.

`History` is the other thing a change brings: the commits of `BASE..HEAD` (who made them and what they said) and every line
they added, read with `git log -p`. `resolve` turns a name a person gives for a base into the id of a commit, and `git_dir_of`
asks git for the git directory of a work tree (a linked work tree has a file where a clone has a folder). Standard library only."""

import os
import re
import stat
from typing import Callable, Dict, Iterable, Iterator, List, Mapping, NamedTuple, Optional

from . import gitx, store

# What a regular file's git mode is: anything else is a link, a submodule or not a file
REGULAR = ("100644", "100755")
LIMIT = 1024 * 1024
# The most entries a listing may hold and the deepest folder the walk goes to: past them the catalog is refused, and the
# structure rules would have said so (ST04 and ST10 are far below)
MAX_ENTRIES = 1_000_000
MAX_DEPTH = 64
# The largest blob GitTree loads: `cat-file` cannot read a part of one. Anything over it is far past every limit of ST09
BLOB_LIMIT = 8 * 1024 * 1024
# What GitTree reads ahead in one `cat-file --batch`, so that the rules that look at every file do not start a git for each
READ_AHEAD, AHEAD_BYTES, CACHE_BYTES = 100, 8 * 1024 * 1024, 64 * 1024 * 1024
# `git log -p` output may weigh this much and take this long
LOG_LIMIT = 128 * 1024 * 1024
LOG_SECONDS = 300
# Git reads the index and the files; this keeps it from writing the index back
QUIET = {"GIT_OPTIONAL_LOCKS": "0"}


class Entry(NamedTuple):
    """A file of a tree: its git mode (`100644`, `100755`, `120000` a link, `160000` a submodule), its path from the root and
    its size in bytes (0 for a submodule)."""
    mode: str
    path: str
    size: int


class TreeError(Exception):
    """The tree cannot be listed (too many files, a repository git cannot read). The message is for the person."""


class Tree:
    """What the rules read. `entries()` is every path of the catalog, links and submodules included, so that a rule can refuse
    them; `read(path, limit)` is at most `limit` bytes of a regular file, or None when the path is not one of the listing, is
    not a regular file or cannot be read."""

    def entries(self) -> List[Entry]:
        raise NotImplementedError

    def read(self, path: str, limit: int = LIMIT) -> Optional[bytes]:
        raise NotImplementedError

    def entry(self, path: str) -> Optional[Entry]:
        """The entry with this path, or None."""
        index = self.__dict__.get("_by_path")
        if index is None:
            index = self.__dict__["_by_path"] = {each.path: each for each in self.entries()}
        return index.get(path)

    def text(self, path: str, limit: int = LIMIT) -> Optional[str]:
        """The content of a regular file as text (UTF-8, what is not valid replaced), or None."""
        data = self.read(path, limit)
        return None if data is None else data.decode("utf-8", "replace")

    def exists(self, path: str) -> bool:
        return self.entry(path) is not None


def _mode_of(info: os.stat_result) -> str:
    """The git mode of a file as the file system says it: a link, a regular file with or without the execute bit; anything
    else (a pipe, a socket, a device) is the octal number of its type, which is no mode of a file."""
    if stat.S_ISLNK(info.st_mode):
        return "120000"
    if stat.S_ISREG(info.st_mode):
        return "100755" if info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) else "100644"
    if stat.S_ISDIR(info.st_mode):
        return "040000"
    return "%06o" % (stat.S_IFMT(info.st_mode),)


def _clean_path(path: str) -> bool:
    """Whether a path is made of real names: no empty, `.` or `..` segment and no NUL."""
    return "\0" not in path and all(part not in ("", ".", "..") for part in path.split("/"))


def read_inside(root: str, path: str, limit: int = LIMIT) -> Optional[bytes]:
    """At most `limit` bytes of the regular file `path` below `root`, or None. Each folder of the path is opened from the one
    before with O_NOFOLLOW and the file is opened the same way (and without blocking, so a pipe cannot hold the reader), so a
    link anywhere in the path, a file that was swapped for one after the listing and a path with `..` all give None: the read
    never leaves the tree."""
    if not _clean_path(path) or limit < 0 or not hasattr(os, "O_NOFOLLOW"):
        return None
    extra = getattr(os, "O_CLOEXEC", 0)
    parts = path.split("/")
    try:
        handle = os.open(root, os.O_RDONLY | os.O_DIRECTORY | extra)
    except OSError:
        return None
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | extra, dir_fd=handle)
            os.close(handle)
            handle = nxt
        file = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | extra, dir_fd=handle)
        try:
            if not stat.S_ISREG(os.fstat(file).st_mode):
                return None
            chunks, left = [], limit
            while left > 0:
                chunk = os.read(file, min(left, 1 << 20))
                if not chunk:
                    break
                chunks.append(chunk)
                left -= len(chunk)
            return b"".join(chunks)
        finally:
            os.close(file)
    except OSError:
        return None
    finally:
        try:
            os.close(handle)
        except OSError:
            pass


class FolderTree(Tree):
    """A folder as it will be committed. When the folder is a git work tree (it holds `.git`), the entries are the index
    (`ls-files -s`, with the modes git would commit) and the files that are not ignored and not yet added (`ls-files
    --others --exclude-standard`, their mode from `lstat`), so `make check` sees a new file before `git add`; a file the index
    lists and the disk no longer has is left out. Otherwise the folder is walked with `lstat`: a link is a link, a folder that
    holds `.git` is a submodule, and a top-level `.git` is no entry. The root is resolved once; a read never leaves it."""

    def __init__(self, root, untracked: bool = True):
        self.root = os.path.realpath(str(root))
        self.untracked = untracked
        self._entries: Optional[List[Entry]] = None
        self.source = ""   # "git" or "walk": how the entries were listed

    def entries(self) -> List[Entry]:
        if self._entries is None:
            found = self._listed()
            if found is None:
                found, self.source = self._walk(), "walk"
            else:
                self.source = "git"
            self._entries = sorted(found, key=lambda each: each.path)
        return list(self._entries)

    def read(self, path: str, limit: int = LIMIT) -> Optional[bytes]:
        entry = self.entry(path)
        if entry is None or entry.mode not in REGULAR:
            return None
        return read_inside(self.root, path, limit)

    def _git(self, *args):
        result = gitx.run(list(args), cwd=self.root, env=QUIET, timeout=120, limit=gitx.OUTPUT_LIMIT)
        return result if result.code == 0 else None

    def _stat(self, path: str) -> Optional[os.stat_result]:
        try:
            return os.lstat(os.path.join(self.root, *path.split("/")))
        except (OSError, ValueError):
            return None

    def _listed(self) -> Optional[List[Entry]]:
        """The entries according to git, or None when this folder is not a work tree git can read."""
        if not os.path.lexists(os.path.join(self.root, ".git")):
            return None
        try:
            tracked = self._git("ls-files", "-s", "-z")
            extra = self._git("ls-files", "-z", "--others", "--exclude-standard") if tracked and self.untracked else None
        except gitx.CatalogError:
            return None
        except gitx.OutputLimit:
            raise TreeError("This catalog lists more files than the check reads.") from None
        if tracked is None:
            return None
        found: Dict[str, Entry] = {}
        for record in tracked.out.split(b"\0"):
            meta, tab, name = record.partition(b"\t")
            fields = meta.split()
            if not tab or len(fields) != 3:
                continue
            path = os.fsdecode(name)
            info = self._stat(path) if _clean_path(path) else None
            if info is None and _clean_path(path):
                continue   # in the index, gone from the disk: the next commit will not hold it
            mode = fields[0].decode("ascii", "replace")
            found[path] = Entry(mode, path, 0 if mode == "160000" or info is None else info.st_size)
        for record in (extra.out.split(b"\0") if extra else []):
            if not record:
                continue
            path = os.fsdecode(record)
            if path.endswith("/"):   # a folder with a repository of its own: a submodule that was never added
                path = path.rstrip("/")
                found.setdefault(path, Entry("160000", path, 0))
                continue
            info = self._stat(path)
            if info is not None and path not in found:
                found[path] = Entry(_mode_of(info), path, info.st_size)
        if len(found) > MAX_ENTRIES:
            raise TreeError("This catalog lists more files than the check reads.")
        return list(found.values())

    def _walk(self) -> List[Entry]:
        """Every entry below the root by `lstat`, without following a link, in a loop (no recursion)."""
        found: List[Entry] = []
        pending = [(self.root, "", 0)]
        while pending:
            folder, prefix, depth = pending.pop()
            try:
                with os.scandir(folder) as names:
                    listing = sorted(names, key=lambda each: each.name)
            except OSError:
                continue
            for item in listing:
                path = prefix + os.fsdecode(item.name)
                if not prefix and item.name == ".git":
                    continue   # the metadata of the repository, never a file of the catalog
                try:
                    info = item.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if os.path.lexists(os.path.join(item.path, ".git")):
                        found.append(Entry("160000", path, 0))
                    elif depth < MAX_DEPTH:
                        pending.append((item.path, path + "/", depth + 1))
                    continue
                found.append(Entry(_mode_of(info), path, info.st_size))
                if len(found) > MAX_ENTRIES:
                    raise TreeError("This catalog lists more files than the check reads.")
        return found


class GitTree(Tree):
    """A commit of a repository read from its objects: `ls-tree` for the entries and `cat-file --batch` for the files, with
    `--git-dir` and no work tree, so that nothing of the tree can run. This is how a pull request is looked at without
    running any of it. A blob over BLOB_LIMIT is not read (`read` answers None; ST09 already refuses such a file)."""

    def __init__(self, git_dir, commit: str):
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError(f"not a commit id: {commit!r}")
        self.git_dir = os.path.abspath(str(git_dir))
        self.commit = commit
        self._entries: Optional[List[Entry]] = None
        self._oids: Dict[str, str] = {}
        self._cache: Dict[str, bytes] = {}
        self._cached = 0
        self._order: List[str] = []

    def entries(self) -> List[Entry]:
        if self._entries is None:
            try:
                listed = store.listing(self.git_dir, self.commit)
            except gitx.CatalogError as error:
                raise TreeError(error.message if getattr(error, "message", None) else str(error)) from None
            if len(listed) > MAX_ENTRIES:
                raise TreeError("This catalog lists more files than the check reads.")
            self._oids = {each.path: each.oid for each in listed}
            self._entries = [Entry(each.mode, each.path, each.size) for each in listed]
            self._order = [each.path for each in listed if each.mode in REGULAR]
        return list(self._entries)

    def read(self, path: str, limit: int = LIMIT) -> Optional[bytes]:
        entry = self.entry(path)
        if entry is None or entry.mode not in REGULAR or entry.size > BLOB_LIMIT or limit < 0:
            return None
        if path not in self._cache and not self._fetch(path):
            return None
        return self._cache[path][:limit]

    def _fetch(self, path: str) -> bool:
        """Loads `path` and the files that come after it in the listing, in one `cat-file`, up to what is read ahead."""
        wanted, total = [], 0
        names = self._order
        start = names.index(path)
        for name in [path] + names[start + 1:start + 1 + READ_AHEAD * 4]:
            if name in self._cache:
                continue
            size = self._sizes()[name]
            if name != path and (size > AHEAD_BYTES // 8 or total + size > AHEAD_BYTES):
                continue
            wanted.append(name)
            total += size
            if len(wanted) >= READ_AHEAD:
                break
        try:
            loaded = dict(store.blobs(self.git_dir, [self._oids[name] for name in wanted]))
        except (gitx.CatalogError, ValueError):
            return False
        for name in wanted:
            data = loaded.get(self._oids[name])
            if data is None:
                continue
            if self._cached + len(data) > CACHE_BYTES:
                self._cache.clear()
                self._cached = 0
            self._cache[name] = data
            self._cached += len(data)
        return path in self._cache

    def _sizes(self) -> Dict[str, int]:
        sizes = self.__dict__.get("_size_of")
        if sizes is None:
            sizes = self.__dict__["_size_of"] = {each.path: each.size for each in self.entries()}
        return sizes


class ListTree(Tree):
    """A listing and a function that reads its files: the Lab's store listing for Add, the files Share staged. `entries` are
    anything with a `mode`, a `path` and a `size` (a `store.Entry`, an `Entry`) or a `(mode, path, size)` tuple. `reader(path,
    limit)` gives the content of a file, or None; a function of the path alone works too. Nothing is read until a rule asks."""

    def __init__(self, entries: Iterable, reader: Callable):
        self._entries = [self._entry(each) for each in entries]
        self._reader = reader
        try:
            import inspect
            parameters = [p for p in inspect.signature(reader).parameters.values()
                          if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)]
            self._wide = any(p.kind == p.VAR_POSITIONAL for p in parameters) or len(parameters) >= 2
        except (TypeError, ValueError):
            self._wide = True

    @staticmethod
    def _entry(each) -> Entry:
        if hasattr(each, "mode") and hasattr(each, "path"):
            return Entry(str(each.mode), str(each.path), int(getattr(each, "size", 0) or 0))
        mode, path, size = each
        return Entry(str(mode), str(path), int(size or 0))

    @classmethod
    def from_files(cls, files: Mapping) -> "ListTree":
        """A tree of files held in memory: {path: content} where content is bytes or text, or `(mode, content)` for a mode
        other than 100644 (`("100755", b"...")`, `("120000", "target")`). For Share's staged files and for tests."""
        held: Dict[str, bytes] = {}
        listing = []
        for path, value in files.items():
            mode, content = value if isinstance(value, tuple) else ("100644", value)
            data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
            held[path] = data
            listing.append(Entry(mode, path, len(data)))
        return cls(listing, lambda path, limit: held.get(path))

    def entries(self) -> List[Entry]:
        return list(self._entries)

    def read(self, path: str, limit: int = LIMIT) -> Optional[bytes]:
        entry = self.entry(path)
        if entry is None or entry.mode not in REGULAR or limit < 0:
            return None
        data = self._reader(path, limit) if self._wide else self._reader(path)
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            return None   # a reader that answers anything else (a default, a number) read nothing
        return bytes(data)[:limit]


# ---------------------------------------------------------------------------------------------------------------------
# The commits of a change
# ---------------------------------------------------------------------------------------------------------------------

class Commit(NamedTuple):
    """A commit of the range: its id, who wrote it (name and address), who committed it, and its whole message."""
    sha: str
    author: str
    author_email: str
    committer: str
    committer_email: str
    message: str


class Added(NamedTuple):
    """A line that a commit added: the commit, the file, the line number in that commit's version of the file, the text."""
    sha: str
    path: str
    line: int
    text: str


_REVISION = re.compile(r"[0-9a-f]{40}|HEAD|[A-Za-z0-9][A-Za-z0-9._/@+-]{0,200}")
_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34}


def _revision(value: str) -> str:
    """A revision the lab passes to git: a commit id, HEAD or a plain branch name; never one that starts with a dash or holds
    a range, a reflog, a pathspec or a revision expression."""
    if not isinstance(value, str) or not _REVISION.fullmatch(value) or ".." in value or "@{" in value or value.endswith("/"):
        raise ValueError(f"not a revision: {value!r}")
    return value


def unquote(name: str) -> str:
    """A path as git writes it in a diff: plain, or between double quotes with C escapes (`\\n`, `\\"`, `\\303\\251`)."""
    if not (len(name) >= 2 and name.startswith('"') and name.endswith('"')):
        return name
    body, out, at = name[1:-1], bytearray(), 0
    while at < len(body):
        char = body[at]
        if char != "\\" or at + 1 >= len(body):
            out += char.encode("utf-8", "surrogateescape")
            at += 1
            continue
        nxt = body[at + 1]
        if nxt in _ESCAPES:
            out.append(_ESCAPES[nxt])
            at += 2
        elif nxt in "01234567":
            end = at + 2
            while end < len(body) and end < at + 4 and body[end] in "01234567":
                end += 1
            out.append(int(body[at + 1:end], 8) & 255)
            at = end
        else:
            out += nxt.encode("utf-8", "surrogateescape")
            at += 2
    return out.decode("utf-8", "surrogateescape")


def diff_path(header: str) -> Optional[str]:
    """The file a `+++ ` line of a diff names (the text after the three plus signs and the space), without git's `b/`, or
    None for a file that was deleted."""
    header = header.rstrip("\n")
    if header.endswith("\t"):   # git ends a name that holds a blank with a tab; a tab inside a name is written \t
        header = header[:-1]
    name = unquote(header)
    if name == "/dev/null":
        return None
    return name[2:] if name.startswith("b/") else name


class History:
    """The commits of `BASE..HEAD` (the whole history of HEAD when `base` is None) of a repository: `commits()` with who made
    them and what they said, and `added()`, every line they added, read from `git log -p`. The repository is the work tree
    `repo` or the bare `git_dir`. It reads with `--text` (a file marked as binary in `.gitattributes` still shows its lines),
    without external diff programs or text conversions, without rename detection and without color, and runs through `gitx`
    (no hook, no file system monitor). Merges show no diff of their own: the commits they bring do. What cannot be read
    (a base git does not know, a history over LOG_LIMIT) is `note`, never an empty answer taken for a clean one."""

    def __init__(self, repo=None, base: Optional[str] = None, head: str = "HEAD", git_dir=None, limit: int = LOG_LIMIT,
                 timeout: float = LOG_SECONDS):
        if repo is None and git_dir is None:
            raise ValueError("a history needs a repository or a git directory")
        self.repo = os.path.realpath(str(repo)) if repo is not None else None
        self.git_dir = os.path.abspath(str(git_dir)) if git_dir is not None else None
        self.base = _revision(base) if base else None
        self.head = _revision(head)
        self.limit, self.timeout = limit, timeout
        self.note = ""
        self._commits: Optional[List[Commit]] = None
        self._diff: Optional[str] = None

    @property
    def range(self) -> str:
        return f"{self.base}..{self.head}" if self.base else self.head

    def _log(self, *options):
        args = (["--git-dir", self.git_dir] if self.git_dir else []) + [
            "-c", "core.quotePath=false", "log", *options, "--end-of-options", self.range]
        try:
            result = gitx.run(args, cwd=None if self.git_dir else self.repo, env=QUIET, timeout=self.timeout, limit=self.limit)
        except gitx.OutputLimit:
            self.note = "the history is too large to read in full"
            return None
        except gitx.CatalogError as error:
            self.note = "git could not be run" if error.code == "git_missing" else "git could not read the history"
            return None
        if result.code != 0:
            self.note = "the commits could not be read (is the base fetched? CI needs fetch-depth: 0)"
            return None
        return result.out

    def commits(self) -> List[Commit]:
        if self._commits is None:
            self._commits = []
            out = self._log("-z", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B")
            if out is not None:
                parts = out.split(b"\0")
                if parts and parts[-1] == b"":
                    parts.pop()
                if len(parts) % 6:
                    self.note = "the commits could not be read (unexpected output of git)"
                for at in range(0, len(parts) - len(parts) % 6, 6):
                    sha, *rest = [part.decode("utf-8", "replace") for part in parts[at:at + 6]]
                    if re.fullmatch(r"[0-9a-f]{40}", sha):
                        self._commits.append(Commit(sha, *rest))
        return list(self._commits)

    def _text(self) -> str:
        if self._diff is None:
            self._diff = ""
            out = self._log("-p", "-U0", "--inter-hunk-context=0", "--no-merges", "--text", "--no-color", "--no-ext-diff",
                            "--no-textconv", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/", "--format=%x01%H")
            if out is not None:
                self._diff = out.decode("utf-8", "replace")
        return self._diff

    def added(self) -> Iterator[Added]:
        """Every line each commit added, newest commit first. The count of each hunk decides which lines belong to it, so a
        line of a file that looks like a header is still a line of the file."""
        sha, path, new, old_left, new_left = "", None, 0, 0, 0
        for raw in _lines(self._text()):
            if old_left > 0 or new_left > 0:
                if raw.startswith("+"):
                    new_left -= 1
                    if path is not None:
                        yield Added(sha, path, new, raw[1:])
                    new += 1
                    continue
                if raw.startswith("-"):
                    old_left -= 1
                    continue
                if raw.startswith("\\"):
                    continue
                if raw == "" or raw.startswith(" "):
                    # A context line: `-U0` and `--inter-hunk-context=0` ask for none, but a setting of the person's git
                    # (`diff.interHunkContext`) fuses near hunks into one, and `diff.suppressBlankEmpty` prints a blank one bare
                    old_left -= 1
                    new_left -= 1
                    new += 1
                    continue
                old_left = new_left = 0   # the hunk ended earlier than it said: read this line as a header
            if raw.startswith("\x01"):
                sha, path = raw[1:].strip(), None
            elif raw.startswith("diff --git "):
                path = None
            elif raw.startswith("+++ "):
                path = diff_path(raw[4:])
            elif raw.startswith("@@"):
                found = _HUNK.match(raw)
                if found:
                    old_left = int(found.group(2)) if found.group(2) is not None else 1
                    new_left = int(found.group(4)) if found.group(4) is not None else 1
                    new = int(found.group(3))
                    if new_left == 0:
                        new += 1


def _lines(text: str) -> Iterator[str]:
    """The lines of a text split at line feeds only, one at a time (a diff of a hundred megabytes is not copied into a list)."""
    at, size = 0, len(text)
    while at <= size:
        end = text.find("\n", at)
        if end == -1:
            yield text[at:]
            return
        yield text[at:end]
        at = end + 1


def default_base(repo) -> Optional[str]:
    """`git merge-base origin/main HEAD` of a work tree, or None when there is no `origin/main` (or no git, or no repository)."""
    try:
        result = gitx.run(["merge-base", "origin/main", "HEAD"], cwd=os.path.realpath(str(repo)), env=QUIET, timeout=60)
    except gitx.CatalogError:
        return None
    sha = result.out.decode("utf-8", "replace").strip()
    return sha if result.code == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def head_of(repo) -> Optional[str]:
    """The commit HEAD names in a work tree, or None."""
    try:
        result = gitx.run(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], cwd=os.path.realpath(str(repo)), env=QUIET,
                          timeout=60)
    except gitx.CatalogError:
        return None
    sha = result.out.decode("utf-8", "replace").strip()
    return sha if result.code == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else None


_BASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@+~^-]{0,200}")


def resolve(repo, name: str) -> Optional[str]:
    """The commit a name stands for in a work tree (`origin/main`, `HEAD~1`, a branch, a tag, a short or a full id), as its 40
    hexadecimal characters, or None when the text is not a name git could resolve to a commit. The text is a plain revision: it
    starts with a letter or a digit (so it is no option), and holds no range, no reflog and no search; git is asked for a commit
    and nothing else."""
    if not isinstance(name, str) or not _BASE.fullmatch(name) or ".." in name or "@{" in name:
        return None
    try:
        result = gitx.run(["rev-parse", "--verify", "--quiet", name + "^{commit}"], cwd=os.path.realpath(str(repo)), env=QUIET,
                          timeout=60)
    except gitx.CatalogError:
        return None
    sha = result.out.decode("utf-8", "replace").strip()
    return sha if result.code == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def git_dir_of(repo) -> Optional[str]:
    """The git directory of a work tree, absolute, or None. It is a folder `.git` in a clone and a file that names the real one
    in a linked work tree (`git worktree add`) or a submodule's: git is asked, the folder is not guessed. The objects of a
    linked work tree are found from its own git directory."""
    try:
        result = gitx.run(["rev-parse", "--absolute-git-dir"], cwd=os.path.realpath(str(repo)), env=QUIET, timeout=60)
    except gitx.CatalogError:
        return None
    where = os.fsdecode(result.out).strip() if result.code == 0 else ""
    return where if where and os.path.isabs(where) and os.path.isdir(where) else None


def is_repository(path) -> bool:
    """Whether a folder is the top of a git work tree."""
    return os.path.lexists(os.path.join(str(path), ".git"))


__all__ = ["Added", "Commit", "Entry", "FolderTree", "GitTree", "History", "ListTree", "REGULAR", "Tree", "TreeError",
           "default_base", "diff_path", "git_dir_of", "head_of", "is_repository", "read_inside", "resolve", "unquote"]
