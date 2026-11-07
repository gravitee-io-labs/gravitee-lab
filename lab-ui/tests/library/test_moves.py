"""moves: a file or a folder moved without overwriting, the paths of a compose file written again, the stars and tested sets
that follow an item, folders merged. Nothing here runs Docker, git or the network: a crash is injected with `moves.STEP`, and
a run that stopped is repeated until it converges."""


import errno
import hashlib
import json
import os
import random
import shutil
import stat
import struct
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from lab_ui.library import moves
from lab_ui import paths

from .. import Case


class Crash(Exception):
    """What a hook raises to stop a run between two steps."""


def crash_at(number):
    """A `moves.STEP` that raises on its `number`th call; its `calls` says how many there were."""
    calls = []

    def hook(label):
        calls.append(label)
        if len(calls) == number:
            raise Crash(label)
    hook.calls = calls
    return hook


def crash_on(wanted):
    """A `moves.STEP` that raises at the step of this name."""
    def hook(label):
        if label == wanted:
            raise Crash(label)
    return hook


def digest(root):
    """Every entry under `root`, links not followed: {path: (kind, mode[, sha256 or target])}."""
    found = {}
    for folder, folders, files in os.walk(root):
        for name in sorted(folders + files):
            path = os.path.join(folder, name)
            key = os.path.relpath(path, root)
            info = os.lstat(path)
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                found[key] = ("link", os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                found[key] = ("dir", mode)
            else:
                with open(path, "rb") as f:
                    found[key] = ("file", mode, hashlib.sha256(f.read()).hexdigest())
    return found


class Moves(Case):

    def setUp(self):
        self.folder(paths)

    def put(self, path, text="", mode=None):
        self.write(path, text)
        if mode is not None:
            os.chmod(self.root / path, mode)
        return self.root / path

    def text(self, path):
        return (self.root / path).read_text(encoding="utf-8")

    def exists(self, path):
        return os.path.lexists(self.root / path)

    def mode(self, path):
        return stat.S_IMODE(os.lstat(self.root / path).st_mode)

    def wipe(self):
        """Empties the project folder, folders a copy made read-only included."""
        for folder, folders, _ in os.walk(self.root):
            for name in folders:
                if not os.path.islink(os.path.join(folder, name)):
                    os.chmod(os.path.join(folder, name), 0o755)
        for child in list(self.root.iterdir()):
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()


# ---------------------------------------------------------------------------------------------------------------------
# relocate
# ---------------------------------------------------------------------------------------------------------------------

class Relocate(Moves):

    def test_a_file_moves_with_its_mode_and_makes_the_folders_it_needs(self):
        self.put("addons/echo/.env", "TOKEN=1\n", 0o600)
        self.assertEqual(moves.relocate("addons/echo/.env", ".lab/catalogs/fixture/addons/echo/.env"),
                         "moved")
        self.assertFalse(self.exists("addons/echo/.env"))
        self.assertEqual(self.text(".lab/catalogs/fixture/addons/echo/.env"), "TOKEN=1\n")
        self.assertEqual(self.mode(".lab/catalogs/fixture/addons/echo/.env"), 0o600)

    def test_a_folder_moves_whole_by_one_rename(self):
        self.put("playgrounds/shop/.state/cloud/env-1/created.json", '{"a": 1}\n')
        self.put("playgrounds/shop/.state/locked", "closed\n", 0o000)   # a rename does not read what is inside
        before = os.stat(self.root / "playgrounds/shop/.state").st_ino
        self.addCleanup(os.chmod, self.root / ".lab/catalogs/fixture/playgrounds/shop/.state/locked", 0o600)
        self.assertEqual(moves.relocate("playgrounds/shop/.state", ".lab/catalogs/fixture/playgrounds/shop/.state"), "moved")
        moved = self.root / ".lab/catalogs/fixture/playgrounds/shop/.state"
        self.assertEqual((os.stat(moved).st_ino, self.mode(".lab/catalogs/fixture/playgrounds/shop/.state/locked")),
                         (before, 0o000))
        self.assertEqual(self.text(".lab/catalogs/fixture/playgrounds/shop/.state/cloud/env-1/created.json"),
                         '{"a": 1}\n')

    def test_gone_when_the_source_is_not_there_and_after_it_moved(self):
        self.assertEqual(moves.relocate("addons/echo/.env", "elsewhere/.env"), "gone")
        self.put("addons/echo/.env", "TOKEN=1\n")
        self.assertEqual(moves.relocate("addons/echo/.env", "elsewhere/.env"), "moved")
        self.assertEqual(moves.relocate("addons/echo/.env", "elsewhere/.env"), "gone")

    def test_a_destination_that_is_there_is_never_overwritten_or_merged(self):
        self.put("old/.env", "OLD=1\n")
        self.put("new/.env", "NEW=1\n")
        self.put("old/.state/created.json", "old\n")
        self.put("new/.state/other.json", "new\n")
        os.symlink("nowhere", self.root / "new/dangling")
        self.put("old/dangling", "file\n")
        for name in (".env", ".state", "dangling"):
            with self.subTest(name=name):
                self.assertEqual(moves.relocate(f"old/{name}", f"new/{name}"), "conflict")
        self.assertEqual((self.text("old/.env"), self.text("new/.env"), self.exists("new/.state/created.json")),
                         ("OLD=1\n", "NEW=1\n", False))
        self.assertEqual(os.readlink(self.root / "new/dangling"), "nowhere")

    def test_names_with_spaces_and_unicode(self):
        name = "Démo ünï cöde $(x) 'q' ✓"
        self.put(f"old/{name}/compose.yml", "services: {}\n")
        self.assertEqual(moves.relocate(f"old/{name}", f"new/{name}"), "moved")
        self.assertEqual(self.text(f"new/{name}/compose.yml"), "services: {}\n")

    def test_a_link_moves_as_a_link_and_is_never_followed(self):
        outside = self.put("outside/precious.txt", "keep\n", 0o600)
        os.symlink(outside, self.root / "old-link")
        os.symlink("nowhere", self.root / "old-dangling")
        self.assertEqual(moves.relocate("old-link", "new/link"), "moved")
        self.assertEqual(moves.relocate("old-dangling", "new/dangling"), "moved")
        self.assertEqual((os.readlink(self.root / "new/link"), os.readlink(self.root / "new/dangling")),
                         (str(outside), "nowhere"))
        self.assertEqual((outside.read_text(encoding="utf-8"), self.mode("outside/precious.txt")), ("keep\n", 0o600))

    def test_a_folder_cannot_go_into_itself(self):
        self.put("a/b.txt", "x\n")
        for target in ("a", "a/inside", "a/./inside", "a/../a/inside"):
            with self.subTest(target=target), self.assertRaisesRegex(moves.Refused, "same place or inside"):
                moves.relocate("a", target)
        self.assertEqual(self.text("a/b.txt"), "x\n")

    def test_a_path_is_relative_to_the_project_or_absolute(self):
        self.put("one", "1\n")
        self.put("two", "2\n")
        self.assertEqual(moves.relocate(self.root / "one", self.root / "dir/one"), "moved")
        self.assertEqual(moves.relocate("two/", "dir/two"), "moved")   # a slash at the end is a name
        self.assertEqual((self.text("dir/one"), self.text("dir/two")), ("1\n", "2\n"))

    def test_what_is_made_in_the_private_folder_is_0700(self):
        self.put("addons/moved/.env", "TOKEN=1\n", 0o600)
        self.put("addons/moved/secrets/token", "t\n")
        os.makedirs(self.root / ".lab/migration", mode=0o755)
        os.chmod(self.root / ".lab/migration", 0o755)   # a folder that exists with a wider mode is closed
        moves.relocate("addons/moved/.env", ".lab/migration/kept/moved/.env")
        moves.relocate("addons/moved/secrets", ".lab/migration/kept/moved/secrets")
        for folder in (".lab/migration", ".lab/migration/kept", ".lab/migration/kept/moved"):
            with self.subTest(folder=folder):
                self.assertEqual(self.mode(folder), 0o700)
        self.assertEqual(self.mode(".lab/migration/kept/moved/.env"), 0o600)

    def nested(self):
        """The project one folder down in the test's folder, so that what is above it is the test's own and a move that
        should be refused and is not does no harm. Answers the folder above."""
        outer = self.root / "outer"
        project = outer / "project"
        project.mkdir(parents=True)
        self.patch(paths, "ROOT", project)
        return outer

    def test_a_path_that_is_empty_is_refused_and_moves_nothing(self):
        outer = self.nested()
        self.put("outer/project/addons/f.env", "T=1\n")
        for src, dst in (("", "elsewhere"), ("   ", "elsewhere"), ("addons/f.env", ""), ("addons/f.env", "\t"),
                         ("", "")):
            with self.subTest(src=src, dst=dst), self.assertRaisesRegex(moves.Refused, "path to move is empty"):
                moves.relocate(src, dst)
        self.assertEqual((sorted(os.listdir(outer)), self.text("outer/project/addons/f.env")), (["project"], "T=1\n"))

    def test_the_project_folder_and_the_folders_above_it_are_refused_and_move_nothing(self):
        outer = self.nested()
        project = paths.ROOT
        self.put("outer/project/addons/f.env", "T=1\n")
        aside = self.root / "aside"
        for src in (".", "./", "addons/..", project, str(project), "..", outer, outer.parent, project / "addons/../.."):
            with self.subTest(src=str(src)), self.assertRaisesRegex(moves.Refused, "project folder is not moved"):
                moves.relocate(src, aside)
        self.assertEqual((sorted(os.listdir(outer)), self.text("outer/project/addons/f.env"), aside.exists()),
                         (["project"], "T=1\n", False))

    def test_the_project_reached_through_a_link_is_the_same_project(self):
        outer = self.nested()
        project = paths.ROOT
        os.symlink(project, self.root / "via")
        self.patch(paths, "ROOT", self.root / "via")   # the Lab was started from the link
        with self.assertRaisesRegex(moves.Refused, "project folder is not moved"):
            moves.relocate(project, self.root / "aside")
        with self.assertRaisesRegex(moves.Refused, "project folder is not moved"):
            moves.relocate(outer, self.root / "aside")
        self.assertEqual((project.is_dir(), (self.root / "aside").exists()), (True, False))

    def test_a_link_to_the_project_moves_as_a_link(self):
        self.nested()
        os.symlink(paths.ROOT, paths.ROOT / "shortcut")
        self.assertEqual(moves.relocate("shortcut", "elsewhere/shortcut"), "moved")
        self.assertEqual((os.readlink(paths.ROOT / "elsewhere/shortcut"), os.path.lexists(paths.ROOT / "shortcut")),
                         (str(paths.ROOT), False))

    def test_a_folder_that_another_run_made_in_the_meantime_is_used(self):
        self.put("old/.env", "TOKEN=1\n")
        self.put("old/other", "2\n")
        real, raced = os.mkdir, []

        def mkdir(path, mode=0o777, **how):
            if not raced:   # the other run is first at the first folder, as the look and the mkdir are two steps
                raced.append(path)
                real(path, mode)
            return real(path, mode, **how)
        self.patch(moves.os, "mkdir", mkdir)
        self.assertEqual(moves.relocate("old/.env", ".lab/catalogs/fixture/addons/x/.env"), "moved")
        self.assertEqual(moves.relocate("old/other", ".lab/catalogs/fixture/addons/x/other"), "moved")
        self.assertEqual(self.text(".lab/catalogs/fixture/addons/x/.env"), "TOKEN=1\n")
        self.assertEqual(len(raced), 1)

    def test_a_link_where_the_folder_was_to_be_is_an_error_and_the_source_stays(self):
        self.put("old/.env", "TOKEN=1\n")
        os.makedirs(self.root / "elsewhere")
        real, raced = os.mkdir, []

        def mkdir(path, mode=0o777, **how):
            if not raced:   # the other run put a link where the folder goes
                raced.append(path)
                os.symlink(self.root / "elsewhere", path)
            return real(path, mode, **how)
        self.patch(moves.os, "mkdir", mkdir)
        with self.assertRaises(FileExistsError):
            moves.relocate("old/.env", "new/.env")
        self.assertEqual((self.text("old/.env"), os.listdir(self.root / "elsewhere")), ("TOKEN=1\n", []))

    def test_runs_that_need_the_same_new_folder_at_once_all_succeed(self):
        for number in range(120):
            self.put(f"old/{number}", f"{number}\n")
        failed, start = [], threading.Barrier(6)

        def run(first):
            start.wait()
            for number in range(first, 120, 6):
                try:
                    moves.relocate(f"old/{number}", f".lab/catalogs/fixture/addons/x/{number}")
                except (OSError, moves.Refused) as error:   # the test says which
                    failed.append(repr(error))
        threads = [threading.Thread(target=run, args=(first,)) for first in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failed, [])
        self.assertEqual((len(os.listdir(self.root / ".lab/catalogs/fixture/addons/x")), os.listdir(self.root / "old")), (120, []))

    def test_any_other_error_of_the_disk_is_raised_and_the_source_stays(self):
        self.put("old/.env", "TOKEN=1\n")
        self.patch(moves.os, "rename", side_effect=OSError(errno.ENOSPC, "No space left on device"))
        with self.assertRaisesRegex(OSError, "No space left"):
            moves.relocate("old/.env", "new/.env")
        self.assertEqual(self.text("old/.env"), "TOKEN=1\n")

    def test_a_busy_folder_stays_where_it_is(self):
        self.put("old/.state/a", "1\n")
        self.patch(moves.os, "rename", side_effect=OSError(errno.EBUSY, "Device or resource busy"))
        self.assertEqual(moves.relocate("old/.state", "new/.state"), "stayed")
        self.assertEqual(self.text("old/.state/a"), "1\n")


# ---------------------------------------------------------------------------------------------------------------------
# What git tracks
# ---------------------------------------------------------------------------------------------------------------------

def varint(number):
    """A number as git writes it in an index of version 4."""
    out = [number & 0x7F]
    number >>= 7
    while number:
        number -= 1
        out.append(0x80 | (number & 0x7F))
        number >>= 7
    return bytes(reversed(out))


def index_file(names, version=2, extended=False, checksum=True, extension=b"", count=None):
    """The bytes of a git index that lists these names, as git writes it."""
    entries, previous = b"", b""
    for name in sorted(names):
        raw = name.encode("utf-8")
        mode = 0o040000 if name.endswith("/") else 0o100644
        entry = struct.pack(">10I", 0, 0, 0, 0, 0, 0, mode, 0, 0, 0) + bytes([0x11]) * 20
        entry += struct.pack(">H", min(len(raw), 0xFFF) | (0x4000 if extended else 0))
        entry += struct.pack(">H", 0x2000) if extended else b""
        if version == 4:
            common = 0
            while common < min(len(raw), len(previous)) and raw[common] == previous[common]:
                common += 1
            entries += entry + varint(len(previous) - common) + raw[common:] + b"\0"
            previous = raw
        else:
            entry += raw + b"\0"
            entries += entry + b"\0" * (-len(entry) % 8)
    body = b"DIRC" + struct.pack(">II", version, len(names) if count is None else count) + entries + extension
    return body + (hashlib.sha1(body).digest() if checksum else bytes(20))


class Tracked(Moves):

    def checkout(self, names=(), **how):
        """A folder `repo` with a git index that lists `names`; answers its path."""
        repo = self.root / "repo"
        (repo / ".git").mkdir(parents=True, exist_ok=True)
        (repo / ".git/index").write_bytes(index_file(names, **how))
        return repo

    def test_what_the_index_lists_is_tracked_with_what_is_below_it(self):
        repo = self.checkout(["addons/echo/compose.yml", "README.md", "ünï/é.txt"])
        for path, expected in (("addons/echo/compose.yml", True), ("addons/echo", True), ("addons", True),
                               ("README.md", True),
                               ("addons/echo/.env", False), ("addons/echo2", False), ("addon", False),
                               ("Readme.md", True),
                               ("setups", False), ("ünï/é.txt", True), ("ünï", True)):
            with self.subTest(path=path):
                self.assertEqual(moves.tracked(repo / path), expected)

    def test_no_program_runs_to_tell(self):
        # A move may start in a folder a container wrote: a `.git` it planted makes even `git ls-files` run a command
        refuse = AssertionError("this ran a program")
        with mock.patch.object(subprocess, "Popen", side_effect=refuse), mock.patch.object(os, "system", side_effect=refuse):
            self.assertTrue(moves.tracked(self.checkout(["README.md"]) / "README.md"))

    def test_a_name_in_the_other_unicode_form_is_the_same_name(self):
        repo = self.checkout(["caf\u00e9/a.txt"])
        self.assertTrue(moves.tracked(repo / "cafe\u0301"))
        self.assertTrue(moves.tracked(repo / "cafe\u0301/a.txt"))

    def test_tracked_agrees_with_the_definition_on_many_indexes(self):
        # Seeded: random names with shared prefixes, in each version of the index, asked about random paths. The
        # definition is a name that is listed or has a listed name below it, in the spelling the index compares
        alphabet = ["a", "b", "ab", "a-b", "a.b", "a_b", "\u00e9", "e\u0301", "dir", "x y", "0", "A"]
        picker = random.Random(7)

        def name():
            return "/".join(picker.choice(alphabet) for _ in range(picker.randint(1, 4)))
        for trial in range(60):
            listed = {name() for _ in range(picker.randint(0, 25))}
            listed = {one for one in listed if not any(other.startswith(one + "/") for other in listed)}
            version = picker.choice([2, 3, 4])
            repo = self.checkout(sorted(listed), version=version, extended=version == 3)
            keys = {moves._key(one) for one in listed}
            for _ in range(20):
                asked = name()
                key = moves._key(asked)
                wanted = key in keys or any(one.startswith(key + "/") for one in keys)
                self.assertEqual(moves.tracked(repo / asked), wanted, (trial, version, asked, sorted(listed)))

    def test_the_index_of_each_version_reads(self):
        names = [f"dir{index % 7}/file{index}.txt" for index in range(40)] + ["a" * 200 + "/x", "b", "ünï/é.txt"]
        for version, extended in ((2, False), (3, True), (4, False), (4, True)):
            with self.subTest(version=version, extended=extended):
                found = moves._parse_index(index_file(names, version, extended))
                self.assertEqual(sorted(found), sorted(name.encode("utf-8") for name in names))

    def test_an_index_that_is_not_one_this_reader_knows_is_tracked(self):
        for case, data in (("a checksum that does not hold", index_file(["a"], checksum=False)[:-1] + b"\x01"),
                           ("a split index", index_file(["a"], extension=b"link" + struct.pack(">I", 0))),
                           ("a version from the future", index_file(["a"], version=9)),
                           ("more entries than there are", index_file(["a", "b"], count=5)),
                           ("an extension that is longer than the file",
                            index_file(["a"], extension=b"TREE" + struct.pack(">I", 99))),
                           ("a cut in the middle of an entry", index_file(["a", "b"])[:50]),
                           ("a few bytes", b"DIRC"), ("not an index", b"hello" * 20), ("nothing", b"")):
            with self.subTest(case=case):
                repo = self.root / "repo"
                (repo / ".git").mkdir(parents=True, exist_ok=True)
                (repo / ".git/index").write_bytes(data)
                self.assertTrue(moves.tracked(repo / "anything"))

    def test_an_index_with_no_checksum_written_reads(self):
        repo = self.checkout(["a/b.txt"], checksum=False)
        self.assertTrue(moves.tracked(repo / "a"))
        self.assertFalse(moves.tracked(repo / "c"))

    def test_a_checkout_with_nothing_added_tracks_nothing(self):
        repo = self.root / "repo"
        (repo / ".git").mkdir(parents=True)
        self.assertFalse(moves.tracked(repo / "anything"))

    def test_a_download_with_no_git_folder_tracks_nothing(self):
        self.put("addons/echo/compose.yml", "x\n")
        self.assertFalse(moves.tracked(self.root / "addons/echo/compose.yml"))

    def test_a_worktree_has_its_own_index(self):
        main = self.root / "main"
        (main / ".git/worktrees/wt").mkdir(parents=True)
        (main / ".git/worktrees/wt/index").write_bytes(index_file(["tracked.txt"]))
        (self.root / "wt").mkdir()
        (self.root / "wt/.git").write_text(f"gitdir: {main}/.git/worktrees/wt\n", encoding="utf-8")
        self.assertEqual((moves.tracked(self.root / "wt/tracked.txt"), moves.tracked(self.root / "wt/other.txt")),
                         (True, False))
        (self.root / "rel").mkdir()
        (self.root / "rel/.git").write_text("gitdir: ../main/.git/worktrees/wt\n", encoding="utf-8")
        self.assertTrue(moves.tracked(self.root / "rel/tracked.txt"))

    def test_a_git_file_that_names_nothing_is_tracked(self):
        (self.root / "odd").mkdir()
        (self.root / "odd/.git").write_text("hello\n", encoding="utf-8")
        self.assertTrue(moves.tracked(self.root / "odd/anything"))

    def test_a_sparse_index_lists_a_folder_whole(self):
        repo = self.checkout(["addons/", "a/b.txt"], version=3, extended=True)
        for path, expected in (("addons/echo/f.txt", True), ("addons", True), ("addons/echo", True), ("a/b.txt", True),
                               ("a", True), ("a/c.txt", False), ("setups", False)):
            with self.subTest(path=path):
                self.assertEqual(moves.tracked(repo / path), expected)

    def test_a_checkout_is_found_from_the_real_place_of_the_parent(self):
        # A catalog linked in place is judged in its own checkout, not in the one the link sits in
        engine = self.checkout(["engine-only.txt"])
        working = self.root / "working"
        (working / ".git").mkdir(parents=True)
        (working / ".git/index").write_bytes(index_file(["addons/echo/compose.yml"]))
        (working / "addons/echo").mkdir(parents=True)
        os.symlink(working, engine / "linked")
        self.assertEqual((moves.tracked(engine / "linked/addons/echo/compose.yml"),
                          moves.tracked(engine / "linked/addons/echo/.env"),
                          moves.tracked(engine / "linked/engine-only.txt")),
                         (True, False, False))

    def test_a_tracked_source_is_refused_and_nothing_moves(self):
        repo = self.checkout(["addons/echo/compose.yml", "addons/kept/compose.yml"])
        self.put("repo/addons/echo/compose.yml", "services: {}\n")
        self.put("repo/addons/echo/.env", "TOKEN=1\n")
        before = digest(self.root)
        for source in ("addons/echo/compose.yml", "addons/echo", "addons"):
            with self.subTest(source=source), self.assertRaisesRegex(moves.Refused, "tracked by git"):
                moves.relocate(repo / source, self.root / "elsewhere" / source)
        self.assertEqual(digest(self.root), before)
        # What git does not track moves: the machine file beside a tracked one
        self.assertEqual(moves.relocate(repo / "addons/echo/.env", self.root / "elsewhere/.env"), "moved")

    def test_a_checkout_that_cannot_be_read_refuses_every_move(self):
        repo = self.root / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git/index").write_bytes(b"garbage")
        self.put("repo/.env", "TOKEN=1\n")
        with self.assertRaisesRegex(moves.Refused, "tracked by git"):
            moves.relocate(repo / ".env", self.root / "elsewhere/.env")
        self.assertEqual(self.text("repo/.env"), "TOKEN=1\n")

    def test_a_source_that_is_gone_is_gone_whatever_git_says(self):
        repo = self.checkout(["a.txt"])
        self.assertEqual(moves.relocate(repo / "a.txt", self.root / "elsewhere/a.txt"), "gone")


@unittest.skipUnless(shutil.which("git"),
                     "git is not installed: the index reader is checked against its own test files only")
class TrackedByGit(Moves):
    """The index reader against what git itself writes and answers."""

    def setUp(self):
        super().setUp()
        self.repo = self.root / "checkout"
        self.repo.mkdir()
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.root), "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test"}
        self.git("init", "-q")
        for name in ("addons/echo/compose.yml", "addons/echo/sub/deep.txt", "dir with space/a b.txt",
                     "ünï/çode/é.txt",
                     "a/b/c/d.txt", *(f"many/file{index}.txt" for index in range(120))):
            file = self.repo / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(name, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "one")
        (self.repo / "untracked.txt").write_text("x", encoding="utf-8")
        (self.repo / "addons/echo/.env").write_text("TOKEN=1\n", encoding="utf-8")

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.env, check=True,
                              capture_output=True).stdout

    def listed(self):
        return sorted(self.git("ls-files", "-z").split(b"\0")[:-1])

    def test_the_index_reads_as_git_lists_it_in_each_version(self):
        for version in (2, 4, 2):
            with self.subTest(version=version):
                self.git("update-index", f"--index-version={version}")
                data = (self.repo / ".git/index").read_bytes()
                self.assertEqual(struct.unpack(">I", data[4:8])[0], version)
                self.assertEqual(sorted(moves._parse_index(data)), self.listed())
        (self.repo / "intent.txt").write_text("x", encoding="utf-8")
        self.git("add", "-N", "intent.txt")   # an intent to add needs the extended flags: version 3
        self.git("update-index", "--skip-worktree", "many/file1.txt")
        data = (self.repo / ".git/index").read_bytes()
        self.assertEqual(sorted(moves._parse_index(data)), self.listed())

    def test_tracked_answers_what_git_answers(self):
        for path in ("addons/echo/compose.yml", "addons/echo", "addons", "addons/echo/.env", "addons/echo/sub",
                     "untracked.txt",
                     "dir with space/a b.txt", "dir with space", "ünï/çode", "many", "many/file7.txt",
                     "many/file777.txt",
                     "a/b", "nothing"):
            with self.subTest(path=path):
                listed = bool(self.git("ls-files", "-z", "--", f":(literal){path}"))
                self.assertEqual(moves.tracked(self.repo / path), listed)

    def test_a_sparse_index_answers_for_the_folders_it_does_not_expand(self):
        try:
            self.git("sparse-checkout", "init", "--cone", "--sparse-index")
            self.git("sparse-checkout", "set", "a")
        except subprocess.CalledProcessError:
            self.skipTest("this git has no sparse index")
        if b"many/" not in moves._parse_index((self.repo / ".git/index").read_bytes()):
            self.skipTest("this git does not write the folders it leaves out whole")
        self.assertTrue(moves.tracked(self.repo / "many/file7.txt"))
        self.assertTrue(moves.tracked(self.repo / "many"))
        self.assertFalse(moves.tracked(self.repo / "untracked.txt"))

    def test_a_worktree_of_the_checkout(self):
        self.git("branch", "other")
        self.git("worktree", "add", "-q", str(self.root / "worktree"), "other")
        self.assertTrue(moves.tracked(self.root / "worktree/addons/echo/compose.yml"))
        self.assertFalse(moves.tracked(self.root / "worktree/addons/echo/.env"))

    def test_relocate_leaves_git_status_as_it_was(self):
        before = self.git("status", "--porcelain")
        with self.assertRaises(moves.Refused):
            moves.relocate(self.repo / "addons/echo", self.root / "elsewhere/echo")
        self.assertEqual(moves.relocate(self.repo / "addons/echo/.env", self.root / "elsewhere/.env"),
                         "moved")
        self.assertEqual(self.git("status", "--porcelain"), before.replace(b"?? addons/echo/.env\n", b""))


# ---------------------------------------------------------------------------------------------------------------------
# A rename that is refused
# ---------------------------------------------------------------------------------------------------------------------

class Elsewhere(Moves):
    """A rename that fails for a right or a filesystem: only the rename of the source is made to fail, so that the Lab's
    own files are still written."""

    def failing(self, source, error):
        """Has `os.rename` raise `error` for the rename of `source` and for no other."""
        real = os.rename

        def rename(src, dst, *more, **how):
            if str(src) == str(self.root / source):
                raise error
            return real(src, dst, *more, **how)
        self.patch(moves.os, "rename", rename)

    def tree(self):
        self.put("playgrounds/shop/.state/created.json", '{"a": 1}\n', 0o640)
        self.put("playgrounds/shop/.state/cloud/env-1/created.json", '{"b": 2}\n')
        os.symlink("/etc/hosts", self.root / "playgrounds/shop/.state/outside")
        os.symlink("created.json", self.root / "playgrounds/shop/.state/inside")
        os.chmod(self.root / "playgrounds/shop/.state/cloud", 0o750)


class Copied(Elsewhere):

    def test_a_folder_refused_for_a_right_is_copied_and_the_original_stays(self):
        self.tree()
        self.failing("playgrounds/shop/.state", PermissionError(errno.EACCES, "Permission denied"))
        original = digest(self.root / "playgrounds/shop/.state")
        self.assertEqual(moves.relocate("playgrounds/shop/.state", "new/.state"), "copied")
        self.assertEqual(digest(self.root / "new/.state"), original)   # modes and links as they were
        self.assertEqual(digest(self.root / "playgrounds/shop/.state"), original)
        self.assertEqual(os.readlink(self.root / "new/.state/outside"), "/etc/hosts")   # a link stays a link
        self.assertFalse(self.exists("new/.state.partial"))

    def test_a_file_refused_for_a_right_is_copied_with_its_mode(self):
        self.put("old/.env", "TOKEN=1\n", 0o600)
        self.failing("old/.env", PermissionError(errno.EPERM, "Operation not permitted"))
        self.assertEqual(moves.relocate("old/.env", "new/.env"), "copied")
        self.assertEqual((self.text("new/.env"), self.mode("new/.env"), self.text("old/.env")),
                         ("TOKEN=1\n", 0o600, "TOKEN=1\n"))

    def test_what_cannot_be_read_is_left_out(self):
        self.tree()
        self.put("playgrounds/shop/.state/secret", "closed\n", 0o000)
        os.mkfifo(self.root / "playgrounds/shop/.state/pipe")
        self.addCleanup(os.chmod, self.root / "playgrounds/shop/.state/secret", 0o600)
        self.failing("playgrounds/shop/.state", PermissionError(errno.EACCES, "Permission denied"))
        self.assertEqual(moves.relocate("playgrounds/shop/.state", "new/.state"), "copied")
        readable = os.access(self.root / "playgrounds/shop/.state/secret", os.R_OK)   # root reads it
        self.assertFalse(self.exists("new/.state/pipe"))
        self.assertEqual(self.exists("new/.state/secret"), readable)
        self.assertEqual(self.text("new/.state/created.json"), '{"a": 1}\n')

    def test_a_partial_copy_of_an_earlier_crash_is_replaced(self):
        self.tree()
        self.put("new/.state.partial/stale", "half\n")
        os.chmod(self.root / "new/.state.partial", 0o555)   # a copy keeps the mode of what it copied
        self.failing("playgrounds/shop/.state", PermissionError(errno.EACCES, "Permission denied"))
        self.assertEqual(moves.relocate("playgrounds/shop/.state", "new/.state"), "copied")
        self.assertEqual((self.exists("new/.state.partial"), self.exists("new/.state/stale")), (False, False))


class Devices(Elsewhere):

    def test_a_file_is_copied_checked_and_removed(self):
        self.put("old/.env", "TOKEN=1\n", 0o600)
        self.failing("old/.env", OSError(errno.EXDEV, "Invalid cross-device link"))
        self.assertEqual(moves.relocate("old/.env", "new/.env"), "moved")
        self.assertEqual((self.exists("old/.env"), self.text("new/.env"), self.mode("new/.env"),
                          self.exists("new/.env.partial")),
                         (False, "TOKEN=1\n", 0o600, False))

    def test_a_link_is_copied_as_a_link(self):
        os.symlink("../target", self.root / "old-link")
        self.failing("old-link", OSError(errno.EXDEV, "Invalid cross-device link"))
        self.assertEqual(moves.relocate("old-link", "new/link"), "moved")
        self.assertEqual((os.path.lexists(self.root / "old-link"), os.readlink(self.root / "new/link")),
                         (False, "../target"))

    def test_a_folder_is_left_where_it_is(self):
        self.tree()
        self.failing("playgrounds/shop/.state", OSError(errno.EXDEV, "Invalid cross-device link"))
        before = digest(self.root / "playgrounds/shop/.state")
        self.assertEqual(moves.relocate("playgrounds/shop/.state", "new/.state"), "stayed")
        self.assertEqual((digest(self.root / "playgrounds/shop/.state"), self.exists("new/.state"),
                          self.exists("new/.state.partial")),
                         (before, False, False))

    def test_a_destination_that_is_there_is_a_conflict_before_any_copy(self):
        self.put("old/.env", "OLD=1\n")
        self.put("new/.env", "NEW=1\n")
        self.failing("old/.env", OSError(errno.EXDEV, "Invalid cross-device link"))
        self.assertEqual(moves.relocate("old/.env", "new/.env"), "conflict")
        self.assertEqual((self.text("old/.env"), self.text("new/.env")), ("OLD=1\n", "NEW=1\n"))

    def test_a_copy_that_differs_is_an_error_and_leaves_the_original(self):
        self.put("old/.env", "TOKEN=1\n")
        self.failing("old/.env", OSError(errno.EXDEV, "Invalid cross-device link"))
        self.patch(moves.filecmp, "cmp", return_value=False)
        with self.assertRaisesRegex(OSError, "differs"):
            moves.relocate("old/.env", "new/.env")
        self.assertEqual((self.text("old/.env"), self.exists("new/.env"), self.exists("new/.env.partial")),
                         ("TOKEN=1\n", False, False))


# ---------------------------------------------------------------------------------------------------------------------
# repoint
# ---------------------------------------------------------------------------------------------------------------------

DEFAULT_NS = "fixture"
# A folder for each kind and a deeper one in each: the order does not matter, the longest that holds a path wins
PREFIXES = [("addons", f".lab/catalogs/{DEFAULT_NS}/addons"), ("setups", f".lab/catalogs/{DEFAULT_NS}/setups"),
            ("playgrounds", f".lab/catalogs/{DEFAULT_NS}/playgrounds"),
            ("playgrounds/local", ".lab/catalogs/local/playgrounds"), ("setups/local", ".lab/catalogs/local/setups"),
            ("addons/local", ".lab/catalogs/local/addons")]

ADDON = """include:
  - "../../echo/compose.yml"   # shared
  - ../other/compose.yml
services:
  scratch:
    image: busybox
    volumes:
      - ../../../plugins/scratch:/plugins
"""


class Repoint(Case):

    def test_an_addon_of_this_machine_that_includes_both_kinds(self):
        self.assertEqual(
            moves.repoint(ADDON, "addons/local/scratch", ".lab/catalogs/local/addons/scratch", PREFIXES),
            ADDON.replace("../../echo/compose.yml", "../../../fixture/addons/echo/compose.yml")
            .replace("../../../plugins/scratch", "../../../../../plugins/scratch"))

    def test_the_longest_old_path_that_holds_a_target_wins(self):
        text = ("include:\n  - ../../addons/echo/compose.yml\n  - ../../addons/other/compose.yml\n"
                "  - ../../addons-x/a.yml\n")
        table = [("addons", "A/addons"), ("addons/echo", "B/echo")]
        self.assertEqual(moves.repoint(text, "setups/local", "setups/local", table),
                         "include:\n  - ../../B/echo/compose.yml\n  - ../../A/addons/other/compose.yml\n"
                         "  - ../../addons-x/a.yml\n")
        self.assertEqual(moves.repoint(text, "setups/local", "setups/local", table[::-1]),
                         moves.repoint(text, "setups/local", "setups/local", table))

    def test_a_file_of_the_table_is_matched_whole_and_a_folder_by_what_is_below_it(self):
        text = ("include:\n  - ../federation.yml\n  - ../federation.yml.bak\n  - ../../addons/jade/compose.yml\n"
                "  - ../../addons/jade-x/compose.yml\n")
        table = [("setups/federation.yml", ".lab/catalogs/fixture/setups/federation.yml"),
                 ("addons/jade", ".lab/catalogs/fixture/addons/jade")]
        self.assertEqual(moves.repoint(text, "setups/local", "setups/local", table),
                         "include:\n  - ../../.lab/catalogs/fixture/setups/federation.yml\n  - ../federation.yml.bak\n"
                         "  - ../../.lab/catalogs/fixture/addons/jade/compose.yml\n"
                         "  - ../../addons/jade-x/compose.yml\n")

    def test_a_file_that_moved_and_a_table_that_names_nothing_it_uses_only_changes_its_depth(self):
        self.assertEqual(moves.repoint("build: ../../../seeder\n", "addons/local/x", ".lab/catalogs/local/addons/x",
                                       []),
                         "build: ../../../../../seeder\n")

    def test_a_file_that_did_not_move_is_untouched_by_a_table_that_does_not_name_what_it_uses(self):
        text = "include:\n  - ../../../plugins/x.yml\n"
        self.assertEqual(moves.repoint(text, "playgrounds/local/x", "playgrounds/local/x", PREFIXES[:3]), text)

    def test_a_target_that_leaves_the_project_stays_one(self):
        self.assertEqual(moves.repoint("build: ../../../../outside/app\n", "addons/local/x",
                                       ".lab/catalogs/local/addons/x", []),
                         "build: ../../../../../../outside/app\n")

    def test_a_table_with_entries_that_are_not_pairs_of_paths_is_read_as_far_as_it_goes(self):
        text = "include:\n  - ../../addons/echo/compose.yml\n"
        self.assertEqual(moves.repoint(text, "setups/local", "setups/local",
                                       [("", "x"), ("/", "y"), (None, "z"), ("addons", None), ("addons", "A/addons")]),
                         "include:\n  - ../../A/addons/echo/compose.yml\n")

    def one(self, text, was, now, old, new):
        return moves.repoint(text, was, now, [(old, new)])

    def test_a_path_back_into_the_folder_stays_a_path(self):
        text = "volumes:\n  - ../whoami/certs:/certs\n  - ${CERTS:-../../kazoo/certs}:/kazoo\n"
        self.assertEqual(self.one(text, "addons/local/whoami", "addons/whoami", "addons/local/whoami", "addons/whoami"),
                         "volumes:\n  - ./certs:/certs\n  - ${CERTS:-../kazoo/certs}:/kazoo\n")

    def test_a_path_ends_where_a_variable_starts(self):
        text = ("volumes:\n  - ../../../plugins/${PRODUCT}:/opt/plugins\n  - ../../../${DATA_DIR:-data}/x:/data\n"
                "build:\n  context: ..\n")
        self.assertEqual(self.one(text, "playgrounds/local/sandbox", "playgrounds/sandbox", "playgrounds/local/sandbox",
                                  "playgrounds/sandbox"),
                         "volumes:\n  - ../../plugins/${PRODUCT}:/opt/plugins\n  - ../../${DATA_DIR:-data}/x:/data\n"
                         "build:\n  context: ../local\n")

    def test_what_the_container_reads_stays_as_written(self):
        for text in ("command: sh -c 'cd ../lib && ./run'\nenvironment:\n  CONFIG: ../config/app.yml\n",
                     "command:\n  - --config=../conf/app.yml\n  - ../data\nenvironment:\n- CONFIG=../config/app.yml\n"):
            with self.subTest(text=text):
                self.assertEqual(self.one(text, "addons/local/whoami", "addons/whoami", "addons/local/whoami",
                                          "addons/whoami"), text)

    def test_the_items_of_a_list_of_paths_are_paths(self):
        text = ("include:\n  - path:\n      - ../../kazoo/compose.yml\n      - ../extra.yml\n"
                "    env_file: ../../kazoo/.env\nservices:\n  app:\n    environment:\n      - CONF=../conf\n"
                "    volumes:\n      - type: bind\n        source: ../data\n        bind:\n"
                "          create_host_path: true\n      - ../certs:/certs\n    env_file:\n    - ../app.env\n")
        self.assertEqual(self.one(text, "addons/local/whoami", "addons/whoami", "addons/local/whoami", "addons/whoami"),
                         text.replace("../../kazoo", "../kazoo").replace("../extra", "../local/extra")
                         .replace("../data", "../local/data").replace("../certs", "../local/certs")
                         .replace("../app.env", "../local/app.env"))

    def test_a_list_of_paths_after_a_tag_or_an_anchor_and_the_other_lists_of_paths(self):
        text = ("services:\n  app:\n    volumes: !override\n      - ../certs:/certs\n    command:\n      - ../data\n"
                "    env_file: &files   # shared\n      - ../app.env\n    build:\n      context: .\n"
                "      additional_contexts:\n        - lib=../lib\n    label_file:\n      - ../labels\n"
                "    ports: !reset []\n    environment: !reset\n      - CONF=../conf\n")
        self.assertEqual(self.one(text, "addons/local/whoami", "addons/whoami", "addons/local/whoami", "addons/whoami"),
                         text.replace("../certs", "../local/certs").replace("../app.env", "../local/app.env")
                         .replace("../lib", "../local/lib").replace("../labels", "../local/labels"))

    def test_in_a_file_that_did_not_move_only_the_path_of_what_moved_changes(self):
        text = ("include:\n  - ../../../addons/local/whoami/compose.yml\nservices:\n  app:\n    build: ..\n"
                "    volumes:\n      - ../../../plugins/${PRODUCT}:/opt/plugins\n      - ../sandbox/../x:/x\n")
        self.assertEqual(self.one(text, "playgrounds/local/sandbox", "playgrounds/local/sandbox", "addons/local/whoami",
                                  "addons/whoami"), text.replace("addons/local/whoami", "addons/whoami"))

    def test_a_text_with_no_path_is_returned_as_it_is(self):
        for text in ("", "\n", "services: {}\n", "# ../comment\nname: x\n", "no newline at the end: ../x"):
            with self.subTest(text=text):
                self.assertEqual(moves.repoint(text, "a/b", "c/d", PREFIXES), text)

    def test_it_does_not_depend_on_the_working_folder(self):
        folder, before = tempfile.mkdtemp(), os.getcwd()
        os.chdir(folder)
        self.addCleanup(os.chdir, before)
        os.rmdir(folder)
        with self.assertRaises(FileNotFoundError):   # what posixpath.relpath asks for
            os.getcwd()
        self.assertEqual(moves.repoint("build: ../../../seeder\n", "addons/local/x", ".lab/catalogs/local/addons/x",
                                       []),
                         "build: ../../../../../seeder\n")


# ---------------------------------------------------------------------------------------------------------------------
# rekey
# ---------------------------------------------------------------------------------------------------------------------

def sets(*versions):
    """Tested sets as tested.py keeps them, one per version, each a day later than the one before."""
    return [{"versions": {"APIM_VERSION": version}, "images": {"APIM_VERSION": f"sha256:{version}"},
             "at": f"2026-10-{index + 1:02d}T10:00:00Z"} for index, version in enumerate(versions)]


class Rekey(Moves):

    def stars(self, found):
        self.put(".lab/starred.json", json.dumps(found, indent=2) + "\n")

    def versions(self, found):
        self.put(".lab/tested.json", json.dumps(found, indent=2, sort_keys=True))

    def read(self, file):
        return json.loads(self.text(file))

    def test_a_star_follows_its_item(self):
        self.stars({"addon": ["echo", "moved-echo"], "playground": ["mine", "shop"]})
        moves.rekey({"addon:moved-echo": "@acme-hub/moved-echo", "playground:mine": "local/mine"})
        self.assertEqual(self.read(".lab/starred.json"), {"addon": ["@acme-hub/moved-echo", "echo"],
                                                          "playground": ["local/mine", "shop"]})
        self.assertEqual(self.text(".lab/starred.json"), json.dumps(self.read(".lab/starred.json"), indent=2) + "\n")

    def test_two_stars_on_one_id_are_one(self):
        self.stars({"addon": ["@acme-hub/moved-echo", "moved-echo"]})
        moves.rekey({"addon:moved-echo": "@acme-hub/moved-echo"})
        self.assertEqual(self.read(".lab/starred.json"), {"addon": ["@acme-hub/moved-echo"]})

    def test_a_kind_is_named_so_the_same_name_of_another_kind_keeps_its_star(self):
        self.stars({"addon": ["echo"], "setup": ["echo"], "playground": ["echo"]})
        moves.rekey({"setup:echo": "local/echo"})
        self.assertEqual(self.read(".lab/starred.json"),
                         {"addon": ["echo"], "setup": ["local/echo"], "playground": ["echo"]})

    def test_other_kinds_and_keys_of_the_file_are_kept(self):
        self.stars({"addon": ["echo"], "future-kind": ["x"], "setup": []})
        moves.rekey({"addon:echo": "local/echo"})
        self.assertEqual(self.read(".lab/starred.json"), {"addon": ["local/echo"], "future-kind": ["x"], "setup": []})

    def test_the_tested_sets_follow_a_playground(self):
        self.versions({"mine": sets("4.12.1", "4.12.2"), "shop": sets("4.11.0")})
        moves.rekey({"playground:mine": "local/mine"})
        self.assertEqual(self.read(".lab/tested.json"),
                         {"local/mine": sets("4.12.1", "4.12.2"), "shop": sets("4.11.0")})

    def test_sets_that_meet_are_joined_the_same_versions_once_and_the_latest_twenty_kept(self):
        mine = sets(*(f"4.1.{n}" for n in range(15)))
        theirs = [{**one, "at": f"2026-11-{index + 1:02d}T10:00:00Z"} for index,
                  one in enumerate(sets(*(f"4.2.{n}" for n in range(8))))]
        theirs[0] = {**mine[3], "at": "2026-12-01T10:00:00Z"}   # the same versions and images as one of mine, later
        self.versions({"mine": mine, "local/mine": theirs})
        moves.rekey({"playground:mine": "local/mine"})
        joined = self.read(".lab/tested.json")["local/mine"]
        self.assertEqual(len(joined), 20)
        self.assertEqual([one["at"] for one in joined], sorted(one["at"] for one in joined))
        versions = [one["versions"]["APIM_VERSION"] for one in joined]
        self.assertEqual(versions.count("4.1.3"), 1)
        self.assertEqual(joined[-1]["at"], "2026-12-01T10:00:00Z")
        self.assertNotIn("mine", self.read(".lab/tested.json"))

    def test_only_a_playground_has_tested_sets(self):
        self.versions({"echo": sets("1")})
        moves.rekey({"addon:echo": "local/echo", "setup:echo": "local/echo"})
        self.assertEqual(self.read(".lab/tested.json"), {"echo": sets("1")})

    def test_a_file_that_does_not_change_is_not_written(self):
        self.stars({"addon": ["echo"]})
        self.versions({"shop": sets("1")})
        before = [os.stat(self.root / ".lab" / name).st_ino for name in ("starred.json", "tested.json")]
        moves.rekey({"addon:moved": "@acme-hub/moved", "playground:gone": "local/gone", "addon:echo": "echo"})
        moves.rekey({})
        self.assertEqual([os.stat(self.root / ".lab" / name).st_ino for name in ("starred.json", "tested.json")],
                         before)

    def test_a_file_that_is_not_there_is_not_made(self):
        moves.rekey({"addon:echo": "local/echo"})
        self.assertEqual((self.exists(".lab/starred.json"), self.exists(".lab/tested.json")), (False, False))

    def test_a_file_that_does_not_read_is_left_as_it_is(self):
        for text in ("{not json", "[1, 2]", '"text"', ""):
            with self.subTest(text=text):
                self.put(".lab/starred.json", text)
                self.put(".lab/tested.json", text)
                moves.rekey({"addon:echo": "local/echo", "playground:echo": "local/echo"})
                self.assertEqual((self.text(".lab/starred.json"), self.text(".lab/tested.json")), (text, text))

    def test_a_mapping_that_is_not_one_changes_nothing(self):
        self.stars({"addon": ["echo"]})
        for mapping in (None, [], "addon:echo", {"echo": "x"}, {"bogus:echo": "x"}, {"addon:echo": ""}, {"addon:": "x"},
                        {"addon:echo": "echo"}, {"addon:echo": None}, {"addon:echo": "a\0b"}, {1: "x"}):
            with self.subTest(mapping=mapping):
                moves.rekey(mapping)
                self.assertEqual(self.read(".lab/starred.json"), {"addon": ["echo"]})

    def test_a_write_that_fails_leaves_both_files_as_they_were(self):
        self.stars({"addon": ["echo"]})
        self.versions({"mine": sets("1")})
        before = (self.text(".lab/starred.json"), self.text(".lab/tested.json"))
        self.patch(paths.os, "replace", side_effect=OSError(errno.ENOSPC, "No space left on device"))
        with self.assertRaises(OSError):
            moves.rekey({"addon:echo": "local/echo", "playground:mine": "local/mine"})
        self.assertEqual((self.text(".lab/starred.json"), self.text(".lab/tested.json")), before)
        # no copy left
        self.assertEqual(sorted(f.name for f in (self.root / ".lab").iterdir()), ["starred.json", "tested.json"])

    def test_a_crash_between_the_two_files_converges(self):
        def build():
            self.stars({"addon": ["echo"], "playground": ["mine"]})
            self.versions({"mine": sets("1")})
        mapping = {"addon:echo": "local/echo", "playground:mine": "local/mine"}
        build()
        moves.rekey(mapping)
        clean = digest(self.root)
        for number in (1, 2):
            with self.subTest(step=number):
                shutil.rmtree(self.root / ".lab")
                build()
                self.patch(moves, "STEP", crash_at(number))
                with self.assertRaises(Crash):
                    moves.rekey(mapping)
                self.patch(moves, "STEP", None)
                moves.rekey(mapping)
                self.assertEqual(digest(self.root), clean)

    def test_the_files_keep_the_mode_they_have(self):
        self.stars({"addon": ["echo"], "playground": ["mine"]})
        self.versions({"mine": sets("1")})
        for file, mode in ((".lab/starred.json", 0o600), (".lab/tested.json", 0o640)):
            os.chmod(self.root / file, mode)
        moves.rekey({"addon:echo": "local/echo", "playground:mine": "local/mine"})
        self.assertEqual((self.read(".lab/starred.json")["addon"], list(self.read(".lab/tested.json"))),
                         (["local/echo"], ["local/mine"]))
        self.assertEqual((self.mode(".lab/starred.json"), self.mode(".lab/tested.json")), (0o600, 0o640))

class Merge(Moves):
    """`moves.merge`: what is in one folder goes into another that exists, and nothing is put over what is there."""

    def test_what_is_not_there_moves_and_a_folder_both_have_is_entered(self):
        self.put("from/.env", "A=1\n", 0o600)
        self.put("from/apps/web/.env", "W=1\n", 0o600)
        self.put("from/apps/web/key", "k")
        self.put("from/apps/api/.env", "I=1\n")
        self.put("to/apps/web/Dockerfile", "FROM busybox\n")
        found = moves.merge("from", "to")
        self.assertEqual(sorted((one["path"], one["result"]) for one in found),
                         [(".env", "moved"), ("apps/api", "moved"), ("apps/web/.env", "moved"), ("apps/web/key", "moved")])
        self.assertEqual([self.text(path) for path in ("to/.env", "to/apps/web/.env", "to/apps/web/key", "to/apps/api/.env",
                                                       "to/apps/web/Dockerfile")],
                         ["A=1\n", "W=1\n", "k", "I=1\n", "FROM busybox\n"])
        self.assertEqual((self.mode("to/.env"), self.mode("to/apps/web/.env")), (0o600, 0o600))

    def test_a_file_whose_place_is_taken_is_not_put_over_it_and_stays_where_it_is(self):
        self.put("from/.env", "mine\n")
        self.put("from/apps/web/.env", "mine\n")
        self.put("to/.env", "theirs\n")
        self.put("to/apps/web/.env", "theirs\n")
        found = moves.merge("from", "to")
        self.assertEqual(sorted((one["path"], one["result"]) for one in found), [(".env", "conflict"), ("apps/web/.env", "conflict")])
        self.assertEqual([self.text(path) for path in ("to/.env", "to/apps/web/.env", "from/.env", "from/apps/web/.env")],
                         ["theirs\n", "theirs\n", "mine\n", "mine\n"])
        # Repeating it changes nothing, and what was moved is gone
        self.put("from/new", "n")
        again = moves.merge("from", "to")
        self.assertEqual(sorted((one["path"], one["result"]) for one in again),
                         [(".env", "conflict"), ("apps/web/.env", "conflict"), ("new", "moved")])
        self.assertEqual(moves.merge("from", "to")[-1]["result"], "conflict")

    def test_records_and_secrets_are_moved_whole_at_the_top_and_at_any_depth_when_asked(self):
        for base in ("from", "to"):
            self.put(f"{base}/.state/{base}.json", base)
            self.put(f"{base}/secrets/{base}", base)
            self.put(f"{base}/apps/web/.state/{base}.json", base)
        found = moves.merge("from", "to")
        self.assertEqual(sorted((one["path"], one["result"]) for one in found),
                         [(".state", "conflict"), ("apps/web/.state/from.json", "moved"), ("secrets", "conflict")],
                         "at the top a record folder is never mixed; deeper it is a folder like another unless asked")
        self.wipe()
        for base in ("from", "to"):
            self.put(f"{base}/.state/{base}.json", base)
            self.put(f"{base}/secrets/{base}", base)
            self.put(f"{base}/apps/web/.state/{base}.json", base)
            self.put(f"{base}/apps/web/secrets/{base}", base)
        found = moves.merge("from", "to", everywhere=True)
        self.assertEqual(sorted((one["path"], one["result"]) for one in found),
                         [(".state", "conflict"), ("apps/web/.state", "conflict"), ("apps/web/secrets", "conflict"),
                          ("secrets", "conflict")])
        self.assertEqual(sorted(path.name for path in (self.root / "to/apps/web/.state").iterdir()), ["to.json"])

    def test_only_plain_folders_are_merged(self):
        self.put("file", "x")
        os.makedirs(self.root / "to")
        os.makedirs(self.root / "real")
        os.symlink("real", self.root / "link")
        for origin, home in (("file", "to"), ("to", "file"), ("link", "to"), ("to", "link"), ("missing", "to")):
            with self.subTest(origin=origin, home=home):
                with self.assertRaises(moves.Refused):
                    moves.merge(origin, home)

    def test_a_link_inside_moves_as_a_link_and_is_never_followed(self):
        os.makedirs(self.root / "outside/deep")
        self.put("outside/deep/f", "x")
        self.put("from/keep", "k")
        os.symlink("../outside", self.root / "from/shared")
        os.makedirs(self.root / "to")
        found = moves.merge("from", "to")
        self.assertEqual(sorted((one["path"], one["result"]) for one in found), [("keep", "moved"), ("shared", "moved")])
        self.assertTrue((self.root / "to/shared").is_symlink())
        self.assertEqual(self.text("outside/deep/f"), "x")

    def test_a_stop_between_two_entries_is_run_again_to_the_end(self):
        for index in range(4):
            self.put(f"from/f{index}", str(index))
        os.makedirs(self.root / "to")
        self.patch(moves, "STEP", crash_on("merge:entry"))
        with self.assertRaises(Crash):
            moves.merge("from", "to")
        moves.STEP = None
        moves.merge("from", "to")
        self.assertEqual(sorted(path.name for path in (self.root / "to").iterdir()), ["f0", "f1", "f2", "f3"])
        self.assertEqual(os.listdir(self.root / "from"), [])

    def test_an_entry_the_disk_cannot_move_stays_and_is_said(self):
        self.put("from/f", "x")
        os.makedirs(self.root / "to")
        real = os.rename

        def busy(source, target, *args, **kwargs):
            if os.path.basename(os.fspath(source)) == "f":
                raise OSError(errno.EBUSY, "Resource busy")
            return real(source, target, *args, **kwargs)
        with mock.patch.object(os, "rename", busy):
            self.assertEqual(moves.merge("from", "to"), [{"path": "f", "result": "stayed"}])
        self.assertEqual(self.text("from/f"), "x")


if __name__ == "__main__":
    unittest.main()
