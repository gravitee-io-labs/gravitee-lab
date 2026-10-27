"""moves: a file or a folder moved without overwriting and written to a journal first, the paths of a compose file
written again, the stars and tested sets that follow an item, the settings held for an item given to the catalog that
has it, and the script that undoes the journal. Nothing here runs Docker, git or the network: a crash is injected
between any two steps with `moves.STEP`, and every run is repeated until it converges."""

import ast
import errno
import hashlib
import json
import os
import random
import re
import shutil
import socket
import stat
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.docker import compose
from lab_ui.library import layout, moves, starred, tested
from lab_ui import paths

from .. import Case, fixtures


def write_undo(restores=(), removes=()):
    """Writes the undo script of the journal to `.lab/migration/undo.sh` (0700, whole): answers its path."""
    file = paths.ROOT / moves.UNDO
    moves._parents(str(file))
    paths.write_atomic(file, moves.undo_script(restores=restores, removes=removes), mode=0o700)
    return file


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


def digest(root, skip=()):
    """Every entry under `root`, links not followed: {path: (kind, mode[, sha256 or target])}. What `skip` names is left
    out, with what is under it."""
    found = {}
    for folder, folders, files in os.walk(root):
        for name in sorted(folders + files):
            path = os.path.join(folder, name)
            key = os.path.relpath(path, root)
            if any(key == one or key.startswith(one + os.sep) for one in skip):
                continue
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


def contents(root, skip=()):
    """What the files under `root` hold, as a sorted list of their texts: where a secret ended up does not matter, that
    it is somewhere does."""
    found = []
    for folder, _, files in os.walk(root):
        for name in files:
            path = os.path.join(folder, name)
            if not any(os.path.relpath(path, root).startswith(one) for one in skip) and not os.path.islink(path):
                with open(path, "rb") as f:
                    found.append(f.read())
    return sorted(found)


class Moves(Case):

    def setUp(self):
        self.folder(paths)
        moves._INDEXES.clear()
        self.addCleanup(moves._INDEXES.clear)

    def put(self, path, text="", mode=None):
        self.write(path, text)
        if mode is not None:
            os.chmod(self.root / path, mode)
        return self.root / path

    def text(self, path):
        return (self.root / path).read_text(encoding="utf-8")

    def exists(self, path):
        return os.path.lexists(self.root / path)

    def journal(self, op=None):
        return [entry for entry in moves.read_journal() if op in (None, entry["op"])]

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
        self.assertEqual(moves.relocate("addons/echo/.env", ".lab/catalogs/fixture/addons/echo/.env", "machine"),
                         "moved")
        self.assertFalse(self.exists("addons/echo/.env"))
        self.assertEqual(self.text(".lab/catalogs/fixture/addons/echo/.env"), "TOKEN=1\n")
        self.assertEqual(self.mode(".lab/catalogs/fixture/addons/echo/.env"), 0o600)

    def test_a_folder_moves_whole_by_one_rename(self):
        self.put("playgrounds/shop/.state/cloud/env-1/created.json", '{"a": 1}\n')
        self.put("playgrounds/shop/.state/locked", "closed\n", 0o000)   # a rename does not read what is inside
        before = os.stat(self.root / "playgrounds/shop/.state").st_ino
        self.addCleanup(os.chmod, self.root / ".lab/catalogs/fixture/playgrounds/shop/.state/locked", 0o600)
        self.assertEqual(moves.relocate("playgrounds/shop/.state", ".lab/catalogs/fixture/playgrounds/shop/.state",
                                        "machine"), "moved")
        moved = self.root / ".lab/catalogs/fixture/playgrounds/shop/.state"
        self.assertEqual((os.stat(moved).st_ino, self.mode(".lab/catalogs/fixture/playgrounds/shop/.state/locked")),
                         (before, 0o000))
        self.assertEqual(self.text(".lab/catalogs/fixture/playgrounds/shop/.state/cloud/env-1/created.json"),
                         '{"a": 1}\n')

    def test_gone_when_the_source_is_not_there_and_after_it_moved(self):
        self.assertEqual(moves.relocate("addons/echo/.env", "elsewhere/.env", "machine"), "gone")
        self.put("addons/echo/.env", "TOKEN=1\n")
        self.assertEqual(moves.relocate("addons/echo/.env", "elsewhere/.env", "machine"), "moved")
        self.assertEqual(moves.relocate("addons/echo/.env", "elsewhere/.env", "machine"), "gone")
        self.assertEqual(len(self.journal("move")), 1)

    def test_a_destination_that_is_there_is_never_overwritten_or_merged(self):
        self.put("old/.env", "OLD=1\n")
        self.put("new/.env", "NEW=1\n")
        self.put("old/.state/created.json", "old\n")
        self.put("new/.state/other.json", "new\n")
        os.symlink("nowhere", self.root / "new/dangling")
        self.put("old/dangling", "file\n")
        for name in (".env", ".state", "dangling"):
            with self.subTest(name=name):
                self.assertEqual(moves.relocate(f"old/{name}", f"new/{name}", "machine"), "conflict")
        self.assertEqual((self.text("old/.env"), self.text("new/.env"), self.exists("new/.state/created.json")),
                         ("OLD=1\n", "NEW=1\n", False))
        self.assertEqual(os.readlink(self.root / "new/dangling"), "nowhere")
        self.assertEqual(self.journal(), [])   # nothing was done, so nothing is said to have been

    def test_names_with_spaces_and_unicode(self):
        name = "Démo ünï cöde $(x) 'q' ✓"
        self.put(f"playgrounds/local/{name}/compose.yml", "services: {}\n")
        self.assertEqual(moves.relocate(f"playgrounds/local/{name}", f".lab/catalogs/local/playgrounds/{name}",
                                        "personal"),
                         "moved")
        self.assertEqual(self.text(f".lab/catalogs/local/playgrounds/{name}/compose.yml"), "services: {}\n")
        self.assertEqual(self.journal("move")[0]["src"], f"playgrounds/local/{name}")

    def test_a_link_moves_as_a_link_and_is_never_followed(self):
        outside = self.put("outside/precious.txt", "keep\n", 0o600)
        os.symlink(outside, self.root / "old-link")
        os.symlink("nowhere", self.root / "old-dangling")
        self.assertEqual(moves.relocate("old-link", "new/link", "machine"), "moved")
        self.assertEqual(moves.relocate("old-dangling", "new/dangling", "machine"), "moved")
        self.assertEqual((os.readlink(self.root / "new/link"), os.readlink(self.root / "new/dangling")),
                         (str(outside), "nowhere"))
        self.assertEqual((outside.read_text(encoding="utf-8"), self.mode("outside/precious.txt")), ("keep\n", 0o600))

    def test_a_folder_cannot_go_into_itself(self):
        self.put("a/b.txt", "x\n")
        for target in ("a", "a/inside", "a/./inside", "a/../a/inside"):
            with self.subTest(target=target), self.assertRaisesRegex(moves.Refused, "same place or inside"):
                moves.relocate("a", target, "machine")
        self.assertEqual(self.text("a/b.txt"), "x\n")
        self.assertEqual(self.journal(), [])

    def test_a_path_is_relative_to_the_project_or_absolute(self):
        self.put("one", "1\n")
        self.put("two", "2\n")
        self.assertEqual(moves.relocate(self.root / "one", self.root / "dir/one", "machine"), "moved")
        self.assertEqual(moves.relocate("two/", "dir/two", "machine"), "moved")   # a slash at the end is a name
        self.assertEqual((self.text("dir/one"), self.text("dir/two")), ("1\n", "2\n"))

    def test_the_move_is_in_the_journal_before_it_is_made(self):
        self.put("old/.env", "TOKEN=1\n")
        seen = []

        def hook(label):
            if label == "relocate:journaled":
                seen.append((self.journal("move"), self.exists("old/.env"), self.exists("new/.env")))
        self.patch(moves, "STEP", hook)
        moves.relocate("old/.env", "new/.env", "machine")
        (lines, at_source, at_target), = seen
        self.assertEqual([(line["src"], line["dst"], line["why"]) for line in lines],
                         [("old/.env", "new/.env", "machine")])
        self.assertEqual((at_source, at_target), (True, False))

    def test_a_path_outside_the_project_is_journaled_whole(self):
        with tempfile.TemporaryDirectory() as other:
            other = Path(other).resolve()
            (other / "copy").mkdir()
            (other / "copy/.env").write_text("TOKEN=1\n", encoding="utf-8")
            moves.relocate(other / "copy/.env", ".lab/catalogs/fixture/addons/echo/.env", "shadow")
        self.assertEqual(self.journal("move")[0]["src"], f"{other}/copy/.env")
        self.assertEqual(self.journal("move")[0]["dst"], ".lab/catalogs/fixture/addons/echo/.env")

    def test_what_is_made_in_the_private_folders_is_0700(self):
        self.put("addons/moved/.env", "TOKEN=1\n", 0o600)
        self.put("addons/moved/secrets/token", "t\n")
        os.makedirs(self.root / ".lab/held", mode=0o755)
        os.chmod(self.root / ".lab/held", 0o755)   # a folder that exists with a wider mode is closed
        moves.relocate("addons/moved/.env", ".lab/held/addons/moved/.env", "held")
        moves.relocate("addons/moved/secrets", ".lab/held/addons/moved/secrets", "held")
        for folder in (".lab/held", ".lab/held/addons", ".lab/held/addons/moved", ".lab/migration"):
            with self.subTest(folder=folder):
                self.assertEqual(self.mode(folder), 0o700)
        self.assertEqual(self.mode(".lab/migration/journal.jsonl"), 0o600)
        self.assertEqual(self.mode(".lab/held/addons/moved/.env"), 0o600)

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
                moves.relocate(src, dst, "machine")
        self.assertEqual((sorted(os.listdir(outer)), self.text("outer/project/addons/f.env"), self.journal()),
                         (["project"], "T=1\n", []))

    def test_the_project_folder_and_the_folders_above_it_are_refused_and_move_nothing(self):
        outer = self.nested()
        project = paths.ROOT
        self.put("outer/project/addons/f.env", "T=1\n")
        aside = self.root / "aside"
        for src in (".", "./", "addons/..", project, str(project), "..", outer, outer.parent, project / "addons/../.."):
            with self.subTest(src=str(src)), self.assertRaisesRegex(moves.Refused, "project folder is not moved"):
                moves.relocate(src, aside, "machine")
        self.assertEqual((sorted(os.listdir(outer)), self.text("outer/project/addons/f.env"), aside.exists(),
                          self.journal()), (["project"], "T=1\n", False, []))

    def test_the_project_reached_through_a_link_is_the_same_project(self):
        outer = self.nested()
        project = paths.ROOT
        os.symlink(project, self.root / "via")
        self.patch(paths, "ROOT", self.root / "via")   # the Lab was started from the link
        with self.assertRaisesRegex(moves.Refused, "project folder is not moved"):
            moves.relocate(project, self.root / "aside", "machine")
        with self.assertRaisesRegex(moves.Refused, "project folder is not moved"):
            moves.relocate(outer, self.root / "aside", "machine")
        self.assertEqual((project.is_dir(), (self.root / "aside").exists()), (True, False))

    def test_a_link_to_the_project_moves_as_a_link(self):
        self.nested()
        os.symlink(paths.ROOT, paths.ROOT / "shortcut")
        self.assertEqual(moves.relocate("shortcut", "elsewhere/shortcut", "machine"), "moved")
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
        self.assertEqual(moves.relocate("old/.env", ".lab/catalogs/fixture/addons/x/.env", "machine"), "moved")
        self.assertEqual(moves.relocate("old/other", ".lab/catalogs/fixture/addons/x/other", "machine"), "moved")
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
            moves.relocate("old/.env", "new/.env", "machine")
        self.assertEqual((self.text("old/.env"), os.listdir(self.root / "elsewhere"), self.journal()),
                         ("TOKEN=1\n", [], []))

    def test_runs_that_need_the_same_new_folder_at_once_all_succeed(self):
        for number in range(120):
            self.put(f"old/{number}", f"{number}\n")
        failed, start = [], threading.Barrier(6)

        def run(first):
            start.wait()
            for number in range(first, 120, 6):
                try:
                    moves.relocate(f"old/{number}", f".lab/catalogs/fixture/addons/x/{number}", "machine")
                except (OSError, moves.Refused) as error:   # the test says which
                    failed.append(repr(error))
        threads = [threading.Thread(target=run, args=(first,)) for first in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failed, [])
        self.assertEqual((len(os.listdir(self.root / ".lab/catalogs/fixture/addons/x")), os.listdir(self.root / "old"),
                          len(self.journal("move"))), (120, [], 120))

    def test_any_other_error_of_the_disk_is_raised_and_the_source_stays(self):
        self.put("old/.env", "TOKEN=1\n")
        self.patch(moves.os, "rename", side_effect=OSError(errno.ENOSPC, "No space left on device"))
        with self.assertRaisesRegex(OSError, "No space left"):
            moves.relocate("old/.env", "new/.env", "machine")
        self.assertEqual(self.text("old/.env"), "TOKEN=1\n")

    def test_a_busy_folder_stays_where_it_is(self):
        self.put("old/.state/a", "1\n")
        self.patch(moves.os, "rename", side_effect=OSError(errno.EBUSY, "Device or resource busy"))
        self.assertEqual(moves.relocate("old/.state", "new/.state", "machine"), "stayed")
        self.assertEqual(self.text("old/.state/a"), "1\n")


# ---------------------------------------------------------------------------------------------------------------------
# The journal
# ---------------------------------------------------------------------------------------------------------------------

class Journal(Moves):

    def test_one_line_each_with_paths_and_the_time_and_no_content(self):
        moves.journal("move", src="a", dst="b", why="machine")
        moves.journal("rekey", ids={"playground:x": "local/x"})
        lines = self.text(".lab/migration/journal.jsonl").splitlines()
        self.assertEqual(len(lines), 2)
        first = json.loads(lines[0])
        self.assertEqual({key: first[key] for key in ("op", "src", "dst", "why")},
                         {"op": "move", "src": "a", "dst": "b",
                                                                                     "why": "machine"})
        self.assertRegex(first["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_a_torn_line_is_skipped_and_the_next_one_starts_a_line(self):
        moves.journal("move", src="a", dst="b", why="machine")
        with open(self.root / moves.JOURNAL, "ab") as f:
            f.write(b'{"op":"move","src":"torn')   # the power went out in the middle of a write
        moves.journal("move", src="c", dst="d", why="machine")
        with open(self.root / moves.JOURNAL, "ab") as f:
            f.write(b"not json at all\n[1, 2]\n{\"no\": \"op\"}\n")
        self.assertEqual([(entry["src"], entry["dst"]) for entry in moves.read_journal()], [("a", "b"), ("c", "d")])

    def test_a_folder_that_was_open_to_others_is_closed(self):
        os.makedirs(self.root / ".lab/migration")
        os.chmod(self.root / ".lab/migration", 0o755)
        moves.journal("move", src="a", dst="b", why="machine")
        self.assertEqual((self.mode(".lab/migration"), self.mode(".lab/migration/journal.jsonl")), (0o700, 0o600))

    def test_lines_written_at_the_same_time_stay_whole(self):
        def write(number):
            for index in range(25):
                moves.journal("move", src=f"a{number}-{index}", dst="b", why="machine")
        threads = [threading.Thread(target=write, args=(number,)) for number in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        written = [entry["src"] for entry in moves.read_journal()]
        self.assertEqual(sorted(written), sorted(f"a{number}-{index}" for number in range(8) for index in range(25)))

    def test_no_journal_reads_as_nothing(self):
        self.assertEqual(moves.read_journal(), [])

    def test_a_name_that_is_not_utf8_survives(self):
        name = os.fsdecode(b"bad-\xff-name")
        moves.journal("move", src=name, dst="b", why="machine")
        self.assertEqual(moves.read_journal()[0]["src"], name)

    def test_the_journal_is_not_followed_when_it_is_a_link(self):
        os.makedirs(self.root / ".lab/migration")
        elsewhere = self.put("elsewhere.txt", "keep\n")
        os.symlink(elsewhere, self.root / moves.JOURNAL)
        with self.assertRaises(OSError):
            moves.journal("move", src="a", dst="b", why="machine")
        self.assertEqual(elsewhere.read_text(encoding="utf-8"), "keep\n")


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
            moves._INDEXES.clear()
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
                moves._INDEXES.clear()
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

    def test_the_index_is_read_again_when_git_replaces_it(self):
        repo = self.checkout(["a.txt"])
        self.assertEqual((moves.tracked(repo / "a.txt"), moves.tracked(repo / "b.txt")), (True, False))
        fresh = repo / ".git/index.lock"
        fresh.write_bytes(index_file(["a.txt", "b.txt"]))
        os.replace(fresh, repo / ".git/index")   # git writes a lock file and renames it: a new inode
        self.assertEqual(moves.tracked(repo / "b.txt"), True)

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
                moves.relocate(repo / source, self.root / "elsewhere" / source, "machine")
        self.assertEqual(digest(self.root), before)   # no journal either
        # What git does not track moves: the machine file beside a tracked one
        self.assertEqual(moves.relocate(repo / "addons/echo/.env", self.root / "elsewhere/.env", "machine"), "moved")

    def test_a_checkout_that_cannot_be_read_refuses_every_move(self):
        repo = self.root / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git/index").write_bytes(b"garbage")
        self.put("repo/.env", "TOKEN=1\n")
        with self.assertRaisesRegex(moves.Refused, "tracked by git"):
            moves.relocate(repo / ".env", self.root / "elsewhere/.env", "machine")
        self.assertEqual(self.text("repo/.env"), "TOKEN=1\n")

    def test_a_source_that_is_gone_is_gone_whatever_git_says(self):
        repo = self.checkout(["a.txt"])
        self.assertEqual(moves.relocate(repo / "a.txt", self.root / "elsewhere/a.txt", "machine"), "gone")


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
            moves.relocate(self.repo / "addons/echo", self.root / "elsewhere/echo", "machine")
        self.assertEqual(moves.relocate(self.repo / "addons/echo/.env", self.root / "elsewhere/.env", "machine"),
                         "moved")
        self.assertEqual(self.git("status", "--porcelain"), before.replace(b"?? addons/echo/.env\n", b""))


# ---------------------------------------------------------------------------------------------------------------------
# A rename that is refused
# ---------------------------------------------------------------------------------------------------------------------

class Elsewhere(Moves):
    """A rename that fails for a right or a filesystem: only the rename of the source is made to fail, so that the Lab's
    own files are still written."""

    def refusing(self, source, error):
        """A patch of `os.rename` that raises `error` for the rename of `source` and for no other."""
        real = os.rename

        def rename(src, dst, *more, **how):
            if str(src) == str(self.root / source):
                raise error
            return real(src, dst, *more, **how)
        return mock.patch.object(moves.os, "rename", rename)

    def failing(self, source, error):
        self.start(self.refusing(source, error))

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
        self.assertEqual(moves.relocate("playgrounds/shop/.state", "new/.state", "machine"), "copied")
        self.assertEqual(digest(self.root / "new/.state"), original)   # modes and links as they were
        self.assertEqual(digest(self.root / "playgrounds/shop/.state"), original)
        self.assertEqual(os.readlink(self.root / "new/.state/outside"), "/etc/hosts")   # a link stays a link
        self.assertFalse(self.exists("new/.state.partial"))
        (line,) = self.journal("copied")
        self.assertEqual((line["src"], line["dst"], line["left"]), ("playgrounds/shop/.state", "new/.state", 0))

    def test_a_file_refused_for_a_right_is_copied_with_its_mode(self):
        self.put("old/.env", "TOKEN=1\n", 0o600)
        self.failing("old/.env", PermissionError(errno.EPERM, "Operation not permitted"))
        self.assertEqual(moves.relocate("old/.env", "new/.env", "machine"), "copied")
        self.assertEqual((self.text("new/.env"), self.mode("new/.env"), self.text("old/.env")),
                         ("TOKEN=1\n", 0o600, "TOKEN=1\n"))

    def test_what_cannot_be_read_is_left_out_and_counted(self):
        self.tree()
        self.put("playgrounds/shop/.state/secret", "closed\n", 0o000)
        os.mkfifo(self.root / "playgrounds/shop/.state/pipe")
        self.addCleanup(os.chmod, self.root / "playgrounds/shop/.state/secret", 0o600)
        self.failing("playgrounds/shop/.state", PermissionError(errno.EACCES, "Permission denied"))
        self.assertEqual(moves.relocate("playgrounds/shop/.state", "new/.state", "machine"), "copied")
        readable = os.access(self.root / "playgrounds/shop/.state/secret", os.R_OK)   # root reads it
        self.assertEqual(self.journal("copied")[0]["left"], 1 if readable else 2)
        self.assertFalse(self.exists("new/.state/pipe"))
        self.assertEqual(self.exists("new/.state/secret"), readable)
        self.assertEqual(self.text("new/.state/created.json"), '{"a": 1}\n')

    def test_a_repeat_copies_nothing_more_and_is_not_a_conflict(self):
        self.tree()
        self.failing("playgrounds/shop/.state", PermissionError(errno.EACCES, "Permission denied"))
        self.assertEqual(moves.relocate("playgrounds/shop/.state", "new/.state", "machine"), "copied")
        (self.root / "new/.state/created.json").write_text("changed since\n", encoding="utf-8")
        self.assertEqual(moves.relocate("playgrounds/shop/.state", "new/.state", "machine"), "copied")
        self.assertEqual((self.text("new/.state/created.json"), len(self.journal("copied"))), ("changed since\n", 1))

    def test_a_copied_file_is_moved_once_the_right_comes_back(self):
        self.put("old/.env", "TOKEN=1\n", 0o600)
        with self.refusing("old/.env", PermissionError(errno.EACCES, "Permission denied")):
            self.assertEqual(moves.relocate("old/.env", "new/.env", "machine"), "copied")
        self.assertEqual(moves.relocate("old/.env", "new/.env", "machine"), "moved")
        self.assertEqual((self.exists("old/.env"), self.text("new/.env")), (False, "TOKEN=1\n"))

    def test_a_copy_that_differs_from_a_file_that_changed_is_a_conflict(self):
        self.put("old/.env", "TOKEN=1\n")
        with self.refusing("old/.env", PermissionError(errno.EACCES, "Permission denied")):
            moves.relocate("old/.env", "new/.env", "machine")
        (self.root / "old/.env").write_text("TOKEN=2\n", encoding="utf-8")
        self.assertEqual(moves.relocate("old/.env", "new/.env", "machine"), "copied")   # kept, not removed
        self.assertEqual((self.text("old/.env"), self.text("new/.env")), ("TOKEN=2\n", "TOKEN=1\n"))

    def test_a_folder_that_lost_its_mode_in_a_crash_gets_it_back_by_a_repeat(self):
        self.tree()
        os.chmod(self.root / "playgrounds/shop/.state", 0o750)
        self.failing("playgrounds/shop/.state", PermissionError(errno.EACCES, "Permission denied"))
        self.patch(moves, "STEP", crash_on("relocate:renamed"))
        with self.assertRaises(Crash):
            moves.relocate("playgrounds/shop/.state", "new/.state", "machine")
        self.assertEqual(self.mode("new/.state"), 0o700)   # the mode of a copy is given once it is in its place
        self.patch(moves, "STEP", None)
        self.assertEqual(moves.relocate("playgrounds/shop/.state", "new/.state", "machine"), "copied")
        self.assertEqual(self.mode("new/.state"), 0o750)

    def test_a_folder_that_was_read_only_gets_its_mode_back_after_a_crash_and_only_then(self):
        self.tree()
        os.chmod(self.root / "playgrounds/shop/.state", 0o555)
        self.addCleanup(os.chmod, self.root / "playgrounds/shop/.state", 0o755)
        self.addCleanup(os.chmod, self.root / "new/.state", 0o755)
        self.failing("playgrounds/shop/.state", PermissionError(errno.EACCES, "Permission denied"))
        self.patch(moves, "STEP", crash_on("relocate:renamed"))
        with self.assertRaises(Crash):
            moves.relocate("playgrounds/shop/.state", "new/.state", "machine")
        self.patch(moves, "STEP", None)
        self.assertEqual(moves.relocate("playgrounds/shop/.state", "new/.state", "machine"), "copied")
        self.assertEqual(self.mode("new/.state"), 0o555)   # the crash came before the seal: the repeat gives it
        os.chmod(self.root / "new/.state", 0o755)   # the person makes it writable
        (self.root / "new/.state/mine.json").write_text("{}\n", encoding="utf-8")
        before = os.lstat(self.root / "new/.state")
        self.assertEqual(moves.relocate("playgrounds/shop/.state", "new/.state", "machine"), "copied")
        after = os.lstat(self.root / "new/.state")
        self.assertEqual((self.mode("new/.state"), self.exists("new/.state/mine.json"), after.st_mtime_ns),
                         (0o755, True, before.st_mtime_ns))

    def test_a_copy_made_writable_since_is_not_made_read_only_by_the_next_repeat(self):
        self.put("old/locked/created.json", '{"a": 1}\n')
        os.chmod(self.root / "old/locked", 0o555)
        self.addCleanup(os.chmod, self.root / "old/locked", 0o755)
        self.failing("old/locked", PermissionError(errno.EACCES, "Permission denied"))
        self.assertEqual(moves.relocate("old/locked", "new/locked", "machine"), "copied")
        self.assertEqual(self.mode("new/locked"), 0o555)
        os.chmod(self.root / "new/locked", 0o755)   # the person makes it writable and writes to it
        (self.root / "new/locked/mine.json").write_text("{}\n", encoding="utf-8")
        for _ in range(3):
            self.assertEqual(moves.relocate("old/locked", "new/locked", "machine"), "copied")
        self.assertEqual((self.mode("new/locked"), self.exists("new/locked/mine.json")), (0o755, True))

    def test_a_partial_copy_of_an_earlier_crash_is_replaced(self):
        self.tree()
        self.put("new/.state.partial/stale", "half\n")
        os.chmod(self.root / "new/.state.partial", 0o555)   # a copy keeps the mode of what it copied
        self.failing("playgrounds/shop/.state", PermissionError(errno.EACCES, "Permission denied"))
        self.assertEqual(moves.relocate("playgrounds/shop/.state", "new/.state", "machine"), "copied")
        self.assertEqual((self.exists("new/.state.partial"), self.exists("new/.state/stale")), (False, False))

    @unittest.skipIf(os.geteuid() == 0, "root can rename a folder it cannot write")
    def test_a_folder_the_person_cannot_write_is_copied_for_real(self):
        self.put("old/locked/created.json", '{"a": 1}\n')
        os.chmod(self.root / "old/locked", 0o555)   # a rename to another folder needs the right to write to it
        self.addCleanup(os.chmod, self.root / "old/locked", 0o755)
        self.put("probe/locked/x", "x\n")
        os.chmod(self.root / "probe/locked", 0o555)
        self.addCleanup(os.chmod, self.root / "probe/locked", 0o755)
        try:
            os.rename(self.root / "probe/locked", self.root / "probe-moved")
        except PermissionError:
            pass
        else:
            os.chmod(self.root / "probe-moved", 0o755)
            self.skipTest("this filesystem renames a folder its owner cannot write")
        outcome = moves.relocate("old/locked", "new/locked", "machine")
        self.assertEqual(outcome, "copied")
        self.assertEqual((self.text("new/locked/created.json"), self.mode("new/locked"),
                          self.exists("old/locked/created.json")),
                         ('{"a": 1}\n', 0o555, True))
        os.chmod(self.root / "new/locked", 0o755)


class Devices(Elsewhere):

    def test_a_file_is_copied_checked_and_removed(self):
        self.put("old/.env", "TOKEN=1\n", 0o600)
        self.failing("old/.env", OSError(errno.EXDEV, "Invalid cross-device link"))
        self.assertEqual(moves.relocate("old/.env", "new/.env", "machine"), "moved")
        self.assertEqual((self.exists("old/.env"), self.text("new/.env"), self.mode("new/.env"),
                          self.exists("new/.env.partial")),
                         (False, "TOKEN=1\n", 0o600, False))
        self.assertEqual([entry["op"] for entry in self.journal()], ["move", "copied"])

    def test_a_link_is_copied_as_a_link(self):
        os.symlink("../target", self.root / "old-link")
        self.failing("old-link", OSError(errno.EXDEV, "Invalid cross-device link"))
        self.assertEqual(moves.relocate("old-link", "new/link", "machine"), "moved")
        self.assertEqual((os.path.lexists(self.root / "old-link"), os.readlink(self.root / "new/link")),
                         (False, "../target"))

    def test_a_folder_is_left_where_it_is(self):
        self.tree()
        self.failing("playgrounds/shop/.state", OSError(errno.EXDEV, "Invalid cross-device link"))
        before = digest(self.root / "playgrounds/shop/.state")
        self.assertEqual(moves.relocate("playgrounds/shop/.state", "new/.state", "machine"), "stayed")
        self.assertEqual((digest(self.root / "playgrounds/shop/.state"), self.exists("new/.state"),
                          self.exists("new/.state.partial")),
                         (before, False, False))

    def test_a_destination_that_is_there_is_a_conflict_before_any_copy(self):
        self.put("old/.env", "OLD=1\n")
        self.put("new/.env", "NEW=1\n")
        self.failing("old/.env", OSError(errno.EXDEV, "Invalid cross-device link"))
        self.assertEqual(moves.relocate("old/.env", "new/.env", "machine"), "conflict")
        self.assertEqual((self.text("old/.env"), self.text("new/.env")), ("OLD=1\n", "NEW=1\n"))

    def test_a_move_that_was_announced_and_not_made_is_said_and_undoing_it_does_nothing(self):
        self.tree()
        self.failing("playgrounds/shop/.state", OSError(errno.EXDEV, "Invalid cross-device link"))
        self.assertEqual(moves.relocate("playgrounds/shop/.state", "new/.state", "machine"), "stayed")
        self.assertEqual([(line["src"], line["dst"]) for line in self.journal("stayed")],
                         [("playgrounds/shop/.state", "new/.state")])
        before = digest(self.root, skip=(".lab",))
        self.assertEqual(subprocess.run(["sh", str(write_undo())], capture_output=True).returncode, 0)
        self.assertEqual(digest(self.root, skip=(".lab",)), before)

    def test_a_link_copied_whose_original_stayed_is_finished_by_a_repeat(self):
        os.symlink("../target", self.root / "old-link")
        self.failing("old-link", OSError(errno.EXDEV, "Invalid cross-device link"))
        self.patch(moves, "STEP", crash_on("relocate:renamed"))
        with self.assertRaises(Crash):
            moves.relocate("old-link", "new/link", "machine")
        self.patch(moves, "STEP", None)
        self.assertEqual(moves.relocate("old-link", "new/link", "machine"), "moved")
        self.assertEqual((os.path.lexists(self.root / "old-link"), os.readlink(self.root / "new/link")),
                         (False, "../target"))

    def test_a_copy_that_differs_is_an_error_and_leaves_the_original(self):
        self.put("old/.env", "TOKEN=1\n")
        self.failing("old/.env", OSError(errno.EXDEV, "Invalid cross-device link"))
        self.patch(moves.filecmp, "cmp", return_value=False)
        with self.assertRaisesRegex(OSError, "differs"):
            moves.relocate("old/.env", "new/.env", "machine")
        self.assertEqual((self.text("old/.env"), self.exists("new/.env"), self.exists("new/.env.partial")),
                         ("TOKEN=1\n", False, False))

    def test_a_file_copied_whose_original_stayed_is_finished_by_a_repeat(self):
        # The crash came between the rename of the copy and the removal of the original
        self.put("old/.env", "TOKEN=1\n")
        self.failing("old/.env", OSError(errno.EXDEV, "Invalid cross-device link"))
        self.patch(moves, "STEP", crash_on("relocate:renamed"))
        with self.assertRaises(Crash):
            moves.relocate("old/.env", "new/.env", "machine")
        self.assertEqual((self.exists("old/.env"), self.exists("new/.env")), (True, True))
        self.patch(moves, "STEP", None)
        self.assertEqual(moves.relocate("old/.env", "new/.env", "machine"), "moved")
        self.assertEqual((self.exists("old/.env"), self.text("new/.env")), (False, "TOKEN=1\n"))


# ---------------------------------------------------------------------------------------------------------------------
# A run that stops between any two steps
# ---------------------------------------------------------------------------------------------------------------------

class Crashes(Elsewhere):

    def converge(self, build, run, expected):
        """Runs `run` after `build` once to see how many steps it takes, then again for each step with a crash at it,
        and again without: the folder ends as it did the first time, and what was in it is never lost on the way."""
        build()
        hook = crash_at(0)
        self.patch(moves, "STEP", hook)
        run()
        clean, steps = digest(self.root, skip=(moves.JOURNAL,)), len(hook.calls)
        expected(clean)
        self.assertGreater(steps, 0)
        for number in range(1, steps + 1):
            with self.subTest(step=number):
                self.wipe()
                build()
                moves.STEP = crash_at(number)
                with self.assertRaises(Crash):
                    run()
                moves.STEP = None
                self.assertIn(b"TOKEN=1\n", contents(self.root, skip=(".lab",)), "lost at the crash")
                run()
                self.assertEqual(digest(self.root, skip=(moves.JOURNAL,)), clean)

    def test_a_move(self):
        def build():
            self.put("old/.env", "TOKEN=1\n", 0o600)
        self.converge(build, lambda: moves.relocate("old/.env", "new/.env", "machine"),
                      lambda clean: self.assertEqual(clean["new/.env"][0:2], ("file", 0o600)))

    def test_a_folder_copied_for_want_of_a_right(self):
        def build():
            self.put("old/.state/created.json", '{"a": 1}\n')
            self.put("old/.env", "TOKEN=1\n")
            self.failing("old/.state", PermissionError(errno.EACCES, "Permission denied"))
        self.converge(build, lambda: moves.relocate("old/.state", "new/.state", "machine"),
                      lambda clean: self.assertEqual(sorted(key for key in clean if key.startswith("new")),
                                                     ["new", "new/.state", "new/.state/created.json"]))

    def test_a_file_across_devices(self):
        def build():
            self.put("old/.env", "TOKEN=1\n", 0o600)
            self.failing("old/.env", OSError(errno.EXDEV, "Invalid cross-device link"))
        self.converge(build, lambda: moves.relocate("old/.env", "new/.env", "machine"),
                      lambda clean: self.assertNotIn("old/.env", clean))


# ---------------------------------------------------------------------------------------------------------------------
# repoint
# ---------------------------------------------------------------------------------------------------------------------

DEFAULT_NS = "fixture"
# What the migration gives a machine whose catalog is not read yet: the prefix of each kind, local ones first. The order
# does not matter: the longest prefix that holds a path wins.
PREFIXES = [("addons", f".lab/catalogs/{DEFAULT_NS}/addons"), ("setups", f".lab/catalogs/{DEFAULT_NS}/setups"),
            ("playgrounds", f".lab/catalogs/{DEFAULT_NS}/playgrounds"),
            ("playgrounds/local", ".lab/catalogs/local/playgrounds"), ("setups/local", ".lab/catalogs/local/setups"),
            ("addons/local", ".lab/catalogs/local/addons")]

# The four personal files of the owner's structure, with the names of the fixture catalog: (kind, name, as it was, as it
# is once it sits in .lab/catalogs/local)
GOLDEN = [
    ("playground", "shop-demo", """include:
  - ../../../setups/local/duo.yml
  - "../../../addons/echo/compose.yml"   # shared
services:
  shop-demo-seed:
    build: ../../../seeder
    volumes:
      - ./seed:/seed:ro
      - ./.state:/state
      - ../../../certs/mtls:/certs:ro
    env_file:
      - ./.env
    command: seed --from https://example.test/a/../b
""", """include:
  - ../../setups/duo.yml
  - "../../../fixture/addons/echo/compose.yml"   # shared
services:
  shop-demo-seed:
    build: ../../../../../seeder
    volumes:
      - ./seed:/seed:ro
      - ./.state:/state
      - ../../../../../certs/mtls:/certs:ro
    env_file:
      - ./.env
    command: seed --from https://example.test/a/../b
"""),
    ("playground", "tool-layer", """services:
  tool-layer-seed:
    build: ../../../seeder
    volumes:
      - ./seed:/seed:ro
""", """services:
  tool-layer-seed:
    build: ../../../../../seeder
    volumes:
      - ./seed:/seed:ro
"""),
    ("setup", "duo", """x-gravitee: {kind: setup, name: Duo}
include:
  - ../../addons/echo/compose.yml
  - ../../addons/echo-core/compose.yml
""", """x-gravitee: {kind: setup, name: Duo}
include:
  - ../../fixture/addons/echo/compose.yml
  - ../../fixture/addons/echo-core/compose.yml
"""),
    ("setup", "tracing", """include:
  - ../../addons/echo/compose.yml   # the traces
""", """include:
  - ../../fixture/addons/echo/compose.yml   # the traces
"""),
]
PLAYGROUND = GOLDEN[0][2]
SETUP = """x-gravitee: {kind: setup, name: Duo}
include:
  - ../../addons/echo/compose.yml
  - ../../addons/local/scratch/compose.yml
"""
ADDON = """include:
  - ../../echo/compose.yml
  - ../other/compose.yml
services:
  scratch:
    image: busybox
    volumes:
      - ../../../plugins/scratch:/plugins
"""


class Repoint(Case):

    def test_the_four_personal_files_of_the_owners_structure(self):
        # The golden of the migration: a local playground that includes a local setup and an add-on of the default catalog and
        # builds the seeder, another that builds the seeder, and two local setups that include add-ons of the default
        # catalog, each written as it was and as it must be once it sits in `.lab/catalogs/local`
        for kind, name, before, after in GOLDEN:
            with self.subTest(file=f"{kind}s/local/{name}"):
                folder = f"{kind}s/local" if kind == "setup" else f"{kind}s/local/{name}"
                where = f".lab/catalogs/local/{kind}s" if kind == "setup" else f".lab/catalogs/local/{kind}s/{name}"
                self.assertEqual(moves.repoint(before, folder, where, PREFIXES), after)

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

    # The cases of the function this one generalizes, with the one-entry table that gives it back

    def one(self, text, was, now, old, new):
        return moves.repoint(text, was, now, [(old, new)])

    def test_a_path_back_into_the_folder_stays_a_path(self):
        text = "volumes:\n  - ../whoami/certs:/certs\n  - ${CERTS:-../../kafka/certs}:/kafka\n"
        self.assertEqual(self.one(text, "addons/local/whoami", "addons/whoami", "addons/local/whoami", "addons/whoami"),
                         "volumes:\n  - ./certs:/certs\n  - ${CERTS:-../kafka/certs}:/kafka\n")

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
        text = ("include:\n  - path:\n      - ../../kafka/compose.yml\n      - ../extra.yml\n"
                "    env_file: ../../kafka/.env\nservices:\n  app:\n    environment:\n      - CONF=../conf\n"
                "    volumes:\n      - type: bind\n        source: ../data\n        bind:\n"
                "          create_host_path: true\n      - ../certs:/certs\n    env_file:\n    - ../app.env\n")
        self.assertEqual(self.one(text, "addons/local/whoami", "addons/whoami", "addons/local/whoami", "addons/whoami"),
                         text.replace("../../kafka", "../kafka").replace("../extra", "../local/extra")
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
        self.assertEqual(self.journal("rekey"), [])

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
        clean = digest(self.root, skip=(moves.JOURNAL,))
        for number in (1, 2):
            with self.subTest(step=number):
                shutil.rmtree(self.root / ".lab")
                build()
                self.patch(moves, "STEP", crash_at(number))
                with self.assertRaises(Crash):
                    moves.rekey(mapping)
                self.patch(moves, "STEP", None)
                moves.rekey(mapping)
                self.assertEqual(digest(self.root, skip=(moves.JOURNAL,)), clean)

    def test_the_files_keep_the_mode_they_have(self):
        self.stars({"addon": ["echo"], "playground": ["mine"]})
        self.versions({"mine": sets("1")})
        for file, mode in ((".lab/starred.json", 0o600), (".lab/tested.json", 0o640)):
            os.chmod(self.root / file, mode)
        moves.rekey({"addon:echo": "local/echo", "playground:mine": "local/mine"})
        self.assertEqual((self.read(".lab/starred.json")["addon"], list(self.read(".lab/tested.json"))),
                         (["local/echo"], ["local/mine"]))
        self.assertEqual((self.mode(".lab/starred.json"), self.mode(".lab/tested.json")), (0o600, 0o640))

    def test_the_journal_says_what_was_re_keyed(self):
        self.stars({"addon": ["echo"]})
        self.versions({"mine": sets("1")})
        moves.rekey({"addon:echo": "local/echo", "playground:mine": "local/mine"})
        (line,) = self.journal("rekey")
        self.assertEqual(line["ids"], {"addon:echo": "local/echo", "playground:mine": "local/mine"})


# ---------------------------------------------------------------------------------------------------------------------
# The settings held for an item
# ---------------------------------------------------------------------------------------------------------------------

HOLDER = "include:\n  - ../../../fixture/addons/moved-echo/compose.yml\n  - ../../../fixture/addons/echo/compose.yml\n"


class Held(Moves):
    """An item that left the repository for a catalog that is not installed: its machine files wait in the held area,
    with a star on its bare id. The catalog `acme-hub` has it now."""

    def setUp(self):
        super().setUp()
        self.patch(paths, "default_ns", lambda: DEFAULT_NS, create=True)
        self.populate()

    def populate(self):
        self.units()
        self.put(".lab/held/addons/moved-echo/.env", "MOVED_TOKEN=held-secret\n", 0o600)
        self.put(".lab/held/addons/moved-echo/secrets/token", "held-token\n", 0o600)
        self.put(".lab/held/addons/moved-echo/certs/server.crt", "held-crt\n")
        self.put(".lab/held/addons/moved-echo/certs/server.key", "held-key\n", 0o600)
        self.put(".lab/catalogs/acme-hub/addons/moved-echo/certs/README.md", "Put the certificates here\n")
        self.put(".lab/catalogs/local/playgrounds/mine/compose.yml", HOLDER)
        self.put(".lab/catalogs/local/setups/duo.yml", HOLDER.replace("../../../fixture", "../../fixture"))
        self.put(".lab/on.yml", "include:\n  - .lab/catalogs/fixture/addons/echo/compose.yml\n")
        self.put(".lab/starred.json", json.dumps({"addon": ["echo", "moved-echo"]}, indent=2) + "\n")
        os.chmod(self.root / ".lab/held", 0o700)

    def units(self):
        for ns, kind, name in (("acme-hub", "addon", "moved-echo"), ("fixture", "addon", "echo"),
                               ("acme-hub", "playground", "moved-shop"), ("acme-hub", "setup", "moved-pair")):
            file = paths.CATALOG_FILES[kind].format(ns=ns, name=name)
            self.put(file, "services: {}\n")

    def held(self):
        return sorted(str(path.relative_to(self.root / ".lab/held")) for path in (self.root / ".lab/held").rglob("*")
                      if path.is_file())

    def test_the_settings_move_into_the_catalog_and_the_item_follows(self):
        result = moves.adopt_held("acme-hub", "addon", "moved-echo")
        base = ".lab/catalogs/acme-hub/addons/moved-echo"
        self.assertEqual(result, [{"path": ".env", "result": "moved"}, {"path": "certs/server.crt", "result": "moved"},
                                  {"path": "certs/server.key", "result": "moved"},
                                  {"path": "secrets", "result": "moved"}])
        self.assertEqual((self.text(f"{base}/.env"), self.text(f"{base}/secrets/token"),
                          self.text(f"{base}/certs/server.crt"),
                          self.text(f"{base}/certs/README.md")),
                         ("MOVED_TOKEN=held-secret\n", "held-token\n", "held-crt\n", "Put the certificates here\n"))
        self.assertEqual([self.mode(f"{base}/{name}") for name in (".env", "secrets/token", "certs/server.key")],
                         [0o600] * 3)
        self.assertEqual(self.held(), [])
        self.assertEqual((self.exists(".lab/held/addons"), self.exists(".lab/held")), (False, True))   # only its own
        self.assertEqual(json.loads(self.text(".lab/starred.json")), {"addon": ["@acme-hub/moved-echo", "echo"]})

    def test_the_includes_of_the_items_of_this_machine_follow_it_too(self):
        moves.adopt_held("acme-hub", "addon", "moved-echo")
        for file, folders in (("playgrounds/mine/compose.yml", "../../../"), ("setups/duo.yml", "../../")):
            with self.subTest(file=file):
                text = self.text(f".lab/catalogs/local/{file}")
                self.assertIn(f"{folders}acme-hub/addons/moved-echo/compose.yml", text)
                self.assertIn(f"{folders}fixture/addons/echo/compose.yml", text)   # the other include is not touched
        (first, second) = self.journal("repoint")
        self.assertEqual((first["old"], first["new"]), (".lab/catalogs/fixture/addons/moved-echo",
                                                        ".lab/catalogs/acme-hub/addons/moved-echo"))

    def test_the_catalog_the_includes_are_written_again_from_can_be_named(self):
        self.put(".lab/catalogs/local/addons/other/compose.yml",
                 "include:\n  - ../../../provisional/addons/moved-echo/compose.yml\n")
        moves.adopt_held("acme-hub", "addon", "moved-echo", repoint_from="provisional")
        self.assertEqual(self.text(".lab/catalogs/local/addons/other/compose.yml"),
                         "include:\n  - ../../../acme-hub/addons/moved-echo/compose.yml\n")
        # not from there
        self.assertIn("fixture/addons/moved-echo", self.text(".lab/catalogs/local/playgrounds/mine/compose.yml"))

    def test_includes_are_not_written_again_from_the_catalog_that_adopts(self):
        before = self.text(".lab/catalogs/local/playgrounds/mine/compose.yml")
        moves.adopt_held("acme-hub", "addon", "moved-echo", repoint_from="acme-hub")
        self.assertEqual(self.text(".lab/catalogs/local/playgrounds/mine/compose.yml"), before)
        self.assertEqual(self.journal("repoint"), [])

    def test_a_machine_with_no_item_of_its_own_adopts_all_the_same(self):
        shutil.rmtree(self.root / ".lab/catalogs/local")
        self.assertEqual(len(moves.adopt_held("acme-hub", "addon", "moved-echo")), 4)
        self.assertEqual(json.loads(self.text(".lab/starred.json")), {"addon": ["@acme-hub/moved-echo", "echo"]})

    def test_the_moves_say_why(self):
        moves.adopt_held("acme-hub", "addon", "moved-echo")
        self.assertEqual({line["why"] for line in self.journal("move")}, {"adopt"})

    def test_a_second_run_changes_nothing(self):
        moves.adopt_held("acme-hub", "addon", "moved-echo")
        after = digest(self.root, skip=(moves.JOURNAL,))
        self.assertEqual(moves.adopt_held("acme-hub", "addon", "moved-echo"), [])
        self.assertEqual(digest(self.root, skip=(moves.JOURNAL,)), after)

    def test_the_on_file_is_never_touched(self):
        before = self.text(".lab/on.yml")
        moves.adopt_held("acme-hub", "addon", "moved-echo")
        self.assertEqual(self.text(".lab/on.yml"), before)

    def test_a_file_that_is_there_is_never_overwritten_and_stays_held(self):
        self.put(".lab/catalogs/acme-hub/addons/moved-echo/.env", "THEIRS=1\n")
        self.put(".lab/catalogs/acme-hub/addons/moved-echo/certs/server.crt", "theirs-crt\n")
        result = moves.adopt_held("acme-hub", "addon", "moved-echo")
        self.assertEqual({one["path"]: one["result"] for one in result},
                         {".env": "conflict", "certs/server.crt": "conflict", "certs/server.key": "moved",
                          "secrets": "moved"})
        base = ".lab/catalogs/acme-hub/addons/moved-echo"
        self.assertEqual((self.text(f"{base}/.env"), self.text(f"{base}/certs/server.crt")),
                         ("THEIRS=1\n", "theirs-crt\n"))
        self.assertEqual(self.held(), ["addons/moved-echo/.env", "addons/moved-echo/certs/server.crt"])
        self.assertEqual(self.text(".lab/held/addons/moved-echo/.env"), "MOVED_TOKEN=held-secret\n")
        # The same again: the conflict is still there and is said again, and nothing else changes
        again = moves.adopt_held("acme-hub", "addon", "moved-echo")
        self.assertEqual({one["path"]: one["result"] for one in again},
                         {".env": "conflict", "certs/server.crt": "conflict"})

    def test_a_folder_of_records_is_never_merged_into_another(self):
        self.put(".lab/held/addons/moved-echo/.state/created.json", "held\n")
        self.put(".lab/catalogs/acme-hub/addons/moved-echo/.state/other.json", "theirs\n")
        result = moves.adopt_held("acme-hub", "addon", "moved-echo")
        self.assertEqual([one["result"] for one in result if one["path"] == ".state"], ["conflict"])
        self.assertEqual(self.held(), ["addons/moved-echo/.state/created.json"])
        self.assertFalse(self.exists(".lab/catalogs/acme-hub/addons/moved-echo/.state/created.json"))

    def test_a_playground_has_its_tested_sets_follow_it(self):
        self.put(".lab/held/playgrounds/moved-shop/.state/created.json", "{}\n")
        self.put(".lab/tested.json", json.dumps({"moved-shop": sets("1"), "shop": sets("2")}, indent=2, sort_keys=True))
        self.put(".lab/starred.json", json.dumps({"playground": ["moved-shop"]}, indent=2) + "\n")
        moves.adopt_held("acme-hub", "playground", "moved-shop")
        self.assertEqual(json.loads(self.text(".lab/tested.json")),
                         {"@acme-hub/moved-shop": sets("1"), "shop": sets("2")})
        self.assertEqual(json.loads(self.text(".lab/starred.json")), {"playground": ["@acme-hub/moved-shop"]})
        self.assertEqual(self.text(".lab/catalogs/acme-hub/playgrounds/moved-shop/.state/created.json"), "{}\n")

    def test_a_setup_has_no_folder_so_only_its_star_moves(self):
        self.put(".lab/held/setups/moved-pair/.env", "x\n")   # not something the Lab makes: it stays
        self.put(".lab/starred.json", json.dumps({"setup": ["moved-pair"]}, indent=2) + "\n")
        self.assertEqual(moves.adopt_held("acme-hub", "setup", "moved-pair"), [])
        self.assertEqual(json.loads(self.text(".lab/starred.json")), {"setup": ["@acme-hub/moved-pair"]})
        self.assertTrue(self.exists(".lab/held/setups/moved-pair/.env"))

    def test_the_default_catalog_gets_an_item_back_with_its_bare_id(self):
        self.put(".lab/held/addons/echo/.env", "ECHO=held\n", 0o600)
        self.put(".lab/catalogs/local/addons/other/compose.yml",
                 "include:\n  - ../../../fixture/addons/echo/compose.yml\n")
        result = moves.adopt_held(DEFAULT_NS, "addon", "echo")
        self.assertEqual(result, [{"path": ".env", "result": "moved"}])
        self.assertEqual((self.text(".lab/catalogs/fixture/addons/echo/.env"),
                          self.mode(".lab/catalogs/fixture/addons/echo/.env")),
                         ("ECHO=held\n", 0o600))
        self.assertEqual(json.loads(self.text(".lab/starred.json")), {"addon": ["echo", "moved-echo"]})   # still bare
        self.assertEqual(self.text(".lab/catalogs/local/addons/other/compose.yml"),
                         "include:\n  - ../../../fixture/addons/echo/compose.yml\n")

    def test_a_catalog_cannot_claim_a_name_the_default_catalog_has(self):
        self.put(".lab/held/addons/echo/.env", "ECHO=held\n")
        self.put(".lab/catalogs/acme-hub/addons/echo/compose.yml", "services: {}\n")
        before = digest(self.root)
        self.assertEqual(moves.adopt_held("acme-hub", "addon", "echo"), [])
        self.assertEqual(digest(self.root), before)   # the star on echo is the default catalog's

    def test_a_catalog_that_does_not_have_the_item_adopts_nothing(self):
        before = digest(self.root)
        for ns in ("other-hub", "fixture"):
            with self.subTest(ns=ns):
                self.assertEqual(moves.adopt_held(ns, "addon", "moved-echo"), [])
        self.assertEqual(digest(self.root), before)

    def test_what_is_not_an_id_is_refused_before_a_path_is_made(self):
        before = digest(self.root)
        for ns, kind, name in (("../acme-hub", "addon", "moved-echo"), ("acme-hub", "addon", "../moved-echo"),
                               ("acme-hub", "addon", "Moved"), ("acme-hub", "addon", ""),
                               ("acme-hub", "bogus", "moved-echo"),
                               ("local", "addon", "moved-echo"), ("a", "addon", "moved-echo"), ("ACME", "addon", "x"),
                               ("acme-hub", "addon", "moved-echo\n"), ("acme-hub", "addon", "a/b"),
                               ("acme-hub/x", "addon", "x"),
                               ("acme-hub", "addon", None), (None, "addon", "x")):
            with self.subTest(ns=ns, kind=kind, name=name), self.assertRaises(moves.Refused):
                moves.adopt_held(ns, kind, name)
        with self.assertRaises(moves.Refused):
            moves.adopt_held("acme-hub", "addon", "moved-echo", repoint_from="../x")
        self.assertEqual(digest(self.root), before)

    def test_a_held_folder_that_is_a_link_is_refused_and_what_it_points_to_is_left(self):
        shutil.move(str(self.root / ".lab/held/addons/moved-echo"), str(self.root / "elsewhere"))
        os.symlink(self.root / "elsewhere", self.root / ".lab/held/addons/moved-echo")
        before = digest(self.root / "elsewhere")
        with self.assertRaisesRegex(moves.Refused, "plain folder"):
            moves.adopt_held("acme-hub", "addon", "moved-echo")
        self.assertEqual(digest(self.root / "elsewhere"), before)
        self.assertFalse(self.exists(".lab/catalogs/acme-hub/addons/moved-echo/.env"))

    def test_a_link_in_the_held_folder_moves_as_a_link(self):
        os.symlink("/etc/hosts", self.root / ".lab/held/addons/moved-echo/hosts")
        moves.adopt_held("acme-hub", "addon", "moved-echo")
        self.assertEqual(os.readlink(self.root / ".lab/catalogs/acme-hub/addons/moved-echo/hosts"), "/etc/hosts")

    def test_a_file_of_this_machine_that_is_a_link_or_is_not_text_is_not_rewritten(self):
        real = self.put("real.yml", HOLDER)
        os.makedirs(self.root / ".lab/catalogs/local/addons/linked")
        os.symlink(real, self.root / ".lab/catalogs/local/addons/linked/compose.yml")
        (self.root / ".lab/catalogs/local/addons/binary").mkdir()
        (self.root / ".lab/catalogs/local/addons/binary/compose.yml").write_bytes(b"\xff\xfe include")
        moves.adopt_held("acme-hub", "addon", "moved-echo")
        self.assertEqual(real.read_text(encoding="utf-8"), HOLDER)
        self.assertTrue(os.path.islink(self.root / ".lab/catalogs/local/addons/linked/compose.yml"))   # still a link
        binary = self.root / ".lab/catalogs/local/addons/binary/compose.yml"
        self.assertEqual(binary.read_bytes(), b"\xff\xfe include")
        self.assertIn("acme-hub", self.text(".lab/catalogs/local/playgrounds/mine/compose.yml"))

    def test_the_folder_of_a_kind_of_this_machine_may_be_a_link(self):
        shutil.move(str(self.root / ".lab/catalogs/local/playgrounds"), str(self.root / "synced"))
        os.symlink(self.root / "synced", self.root / ".lab/catalogs/local/playgrounds")
        moves.adopt_held("acme-hub", "addon", "moved-echo")
        self.assertIn("acme-hub/addons/moved-echo", (self.root / "synced/mine/compose.yml").read_text(encoding="utf-8"))

    def test_a_mode_of_a_file_of_this_machine_is_kept_when_it_is_written(self):
        os.chmod(self.root / ".lab/catalogs/local/playgrounds/mine/compose.yml", 0o640)
        moves.adopt_held("acme-hub", "addon", "moved-echo")
        self.assertEqual(self.mode(".lab/catalogs/local/playgrounds/mine/compose.yml"), 0o640)

    def test_what_waits_for_an_item(self):
        self.assertEqual(moves.has_held("addon", "moved-echo"), True)
        self.assertEqual([moves.has_held("addon", "nothing"), moves.has_held("setup", "moved-echo")], [False, False])
        moves.adopt_held("acme-hub", "addon", "moved-echo")
        self.assertEqual(moves.has_held("addon", "moved-echo"), False)   # the star moved with the item
        self.put(".lab/starred.json", json.dumps({"addon": ["only-starred"]}))
        self.put(".lab/tested.json", json.dumps({"only-tested": []}))
        self.assertEqual([moves.has_held("addon", "only-starred"), moves.has_held("playground", "only-tested"),
                          moves.has_held("addon", "only-tested")], [True, True, False])
        os.makedirs(self.root / ".lab/held/addons/empty")
        self.assertFalse(moves.has_held("addon", "empty"))
        with self.assertRaises(moves.Refused):
            moves.has_held("addon", "../x")

    def test_held_folders_that_cannot_be_trusted_count_as_settings_that_wait(self):
        shutil.move(str(self.root / ".lab/held/addons/moved-echo"), str(self.root / "elsewhere"))
        os.symlink(self.root / "elsewhere", self.root / ".lab/held/addons/moved-echo")
        self.assertTrue(moves.has_held("addon", "moved-echo"))   # adopt_held then says why it refuses

    def test_a_crash_at_any_step_converges_and_loses_nothing(self):
        def build():
            self.wipe()
            self.populate()
            self.put(".lab/held/addons/moved-echo/.state/created.json", "held\n")
        build()
        hook = crash_at(0)
        self.patch(moves, "STEP", hook)
        moves.adopt_held("acme-hub", "addon", "moved-echo")
        clean = digest(self.root, skip=(moves.JOURNAL,))
        secrets = {b"MOVED_TOKEN=held-secret\n", b"held-token\n", b"held-key\n", b"held-crt\n", b"held\n"}
        self.assertGreater(len(hook.calls), 6)
        for number in range(1, len(hook.calls) + 1):
            with self.subTest(step=number, label=hook.calls[number - 1]):
                build()
                moves.STEP = crash_at(number)
                with self.assertRaises(Crash):
                    moves.adopt_held("acme-hub", "addon", "moved-echo")
                moves.STEP = None
                self.assertTrue(secrets <= set(contents(self.root)), "lost at the crash")
                moves.adopt_held("acme-hub", "addon", "moved-echo")
                self.assertEqual(digest(self.root, skip=(moves.JOURNAL,)), clean)
        moves.STEP = None


class Merge(Moves):
    """`moves.merge`: what is in one folder goes into another that exists, and nothing is put over what is there."""

    def test_what_is_not_there_moves_and_a_folder_both_have_is_entered(self):
        self.put("from/.env", "A=1\n", 0o600)
        self.put("from/apps/web/.env", "W=1\n", 0o600)
        self.put("from/apps/web/key", "k")
        self.put("from/apps/api/.env", "I=1\n")
        self.put("to/apps/web/Dockerfile", "FROM busybox\n")
        found = moves.merge("from", "to", "use-catalog")
        self.assertEqual(sorted((one["path"], one["result"]) for one in found),
                         [(".env", "moved"), ("apps/api", "moved"), ("apps/web/.env", "moved"), ("apps/web/key", "moved")])
        self.assertEqual([self.text(path) for path in ("to/.env", "to/apps/web/.env", "to/apps/web/key", "to/apps/api/.env",
                                                       "to/apps/web/Dockerfile")],
                         ["A=1\n", "W=1\n", "k", "I=1\n", "FROM busybox\n"])
        self.assertEqual((self.mode("to/.env"), self.mode("to/apps/web/.env")), (0o600, 0o600))
        self.assertEqual([entry["why"] for entry in self.journal("move")], ["use-catalog"] * 4)

    def test_a_file_whose_place_is_taken_is_not_put_over_it_and_stays_where_it_is(self):
        self.put("from/.env", "mine\n")
        self.put("from/apps/web/.env", "mine\n")
        self.put("to/.env", "theirs\n")
        self.put("to/apps/web/.env", "theirs\n")
        found = moves.merge("from", "to", "use-catalog")
        self.assertEqual(sorted((one["path"], one["result"]) for one in found), [(".env", "conflict"), ("apps/web/.env", "conflict")])
        self.assertEqual([self.text(path) for path in ("to/.env", "to/apps/web/.env", "from/.env", "from/apps/web/.env")],
                         ["theirs\n", "theirs\n", "mine\n", "mine\n"])
        # Repeating it changes nothing, and what was moved is gone
        self.put("from/new", "n")
        again = moves.merge("from", "to", "use-catalog")
        self.assertEqual(sorted((one["path"], one["result"]) for one in again),
                         [(".env", "conflict"), ("apps/web/.env", "conflict"), ("new", "moved")])
        self.assertEqual(moves.merge("from", "to", "use-catalog")[-1]["result"], "conflict")

    def test_records_and_secrets_are_moved_whole_at_the_top_and_at_any_depth_when_asked(self):
        for base in ("from", "to"):
            self.put(f"{base}/.state/{base}.json", base)
            self.put(f"{base}/secrets/{base}", base)
            self.put(f"{base}/apps/web/.state/{base}.json", base)
        found = moves.merge("from", "to", "adopt")
        self.assertEqual(sorted((one["path"], one["result"]) for one in found),
                         [(".state", "conflict"), ("apps/web/.state/from.json", "moved"), ("secrets", "conflict")],
                         "at the top a record folder is never mixed; deeper it is a folder like another unless asked")
        self.wipe()
        for base in ("from", "to"):
            self.put(f"{base}/.state/{base}.json", base)
            self.put(f"{base}/secrets/{base}", base)
            self.put(f"{base}/apps/web/.state/{base}.json", base)
            self.put(f"{base}/apps/web/secrets/{base}", base)
        found = moves.merge("from", "to", "adopt", everywhere=True)
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
                    moves.merge(origin, home, "use-catalog")

    def test_a_link_inside_moves_as_a_link_and_is_never_followed(self):
        os.makedirs(self.root / "outside/deep")
        self.put("outside/deep/f", "x")
        self.put("from/keep", "k")
        os.symlink("../outside", self.root / "from/shared")
        os.makedirs(self.root / "to")
        found = moves.merge("from", "to", "use-catalog")
        self.assertEqual(sorted((one["path"], one["result"]) for one in found), [("keep", "moved"), ("shared", "moved")])
        self.assertTrue((self.root / "to/shared").is_symlink())
        self.assertEqual(self.text("outside/deep/f"), "x")

    def test_a_stop_between_two_entries_is_run_again_to_the_end(self):
        for index in range(4):
            self.put(f"from/f{index}", str(index))
        os.makedirs(self.root / "to")
        self.patch(moves, "STEP", crash_on("use-catalog:entry"))
        with self.assertRaises(Crash):
            moves.merge("from", "to", "use-catalog")
        moves.STEP = None
        moves.merge("from", "to", "use-catalog")
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
            self.assertEqual(moves.merge("from", "to", "use-catalog"), [{"path": "f", "result": "stayed"}])
        self.assertEqual(self.text("from/f"), "x")


# ---------------------------------------------------------------------------------------------------------------------
# The undo script
# ---------------------------------------------------------------------------------------------------------------------

def shells():
    """The shells of this machine that read a POSIX script: `sh` always, and the others that are installed. zsh is the
    one every recent Mac types, and it keeps names that sh does not (`status` is read-only in it); ksh is the one that
    reads `${name#"$pattern"}` and `set --` in its own way."""
    return [shell for shell in ("sh", "dash", "bash", "zsh", "ksh") if shutil.which(shell)]


class Undo(Moves):

    NASTY = ["a b$(touch x)", "it's 'quoted'", "semi;colon && echo no", "Démo ünï ✓", "-rf", "new\nline",
             "back`touch y`tick",
             "glob*[?]", "tab\there"]

    def run_undo(self, shell="sh", cwd=None):
        script = write_undo()
        return subprocess.run([shell, str(script)], cwd=cwd, capture_output=True, text=True)

    def test_the_moves_come_back_in_the_reverse_order_by_a_script_that_quotes_every_name(self):
        for name in self.NASTY:
            self.put(f"kept/{name}", f"{name}\n", 0o640)
        before = digest(self.root, skip=(".lab",))
        for name in self.NASTY:
            self.assertEqual(moves.relocate(f"kept/{name}", f".lab/migration/kept/{name}", "kept"), "moved")
        self.assertEqual(os.listdir(self.root / "kept"), [])
        for shell in shells():
            with self.subTest(shell=shell):
                if shell != "sh":   # run it again from the moved state
                    for name in self.NASTY:
                        os.replace(self.root / "kept" / name, self.root / f".lab/migration/kept/{name}")
                done = self.run_undo(shell, cwd="/")
                self.assertEqual((done.returncode, done.stderr), (0, ""))
                self.assertEqual({key: value for key, value in digest(self.root, skip=(".lab",)).items()}, before)
                self.assertFalse(self.exists("x") or self.exists("y") or self.exists("kept/x"))   # nothing was executed

    def test_nothing_is_put_back_over_something_that_is_there_and_a_second_run_does_nothing(self):
        self.put("a.txt", "one\n")
        self.put("b.txt", "two\n")
        moves.relocate("a.txt", "new/a.txt", "machine")
        moves.relocate("b.txt", "new/b.txt", "machine")
        self.put("a.txt", "made since\n")   # something is at the place it came from
        done = self.run_undo()
        self.assertEqual(done.returncode, 0)
        self.assertIn("Left new/a.txt: a.txt is there", done.stderr)
        self.assertEqual((self.text("a.txt"), self.text("new/a.txt"), self.text("b.txt"), self.exists("new/b.txt")),
                         ("made since\n", "one\n", "two\n", False))
        after = digest(self.root, skip=(".lab",))
        self.assertEqual(self.run_undo().returncode, 0)
        self.assertEqual(digest(self.root, skip=(".lab",)), after)

    def test_a_folder_comes_back_whole_with_what_was_written_in_it_since(self):
        self.put("playgrounds/shop/.state/created.json", "old\n")
        moves.relocate("playgrounds/shop/.state", ".lab/catalogs/fixture/playgrounds/shop/.state", "machine")
        self.put(".lab/catalogs/fixture/playgrounds/shop/.state/created.json", "new\n")   # a seed ran since
        self.assertEqual(self.run_undo().returncode, 0)
        self.assertEqual(self.text("playgrounds/shop/.state/created.json"), "new\n")

    def test_a_link_comes_back_as_a_link_even_when_it_points_nowhere(self):
        os.symlink("nowhere", self.root / "dangling")
        moves.relocate("dangling", "new/dangling", "machine")
        self.assertEqual(self.run_undo().returncode, 0)
        self.assertEqual(os.readlink(self.root / "dangling"), "nowhere")

    def test_the_copies_come_back_over_what_the_move_wrote_and_the_marker_goes(self):
        self.put(".lab/migration/backup/on.yml", "include:\n  - addons/echo/compose.yml\n")
        self.put(".lab/migration/backup/units/addons/local/x/compose.yml", "original\n", 0o640)
        self.put(".lab/on.yml", "include:\n  - .lab/catalogs/fixture/addons/echo/compose.yml\n")
        self.put("addons/local/x/compose.yml", "rewritten\n")
        self.put(".lab/layout.json", '{"layout": 2}\n')
        script = write_undo(restores=[(".lab/migration/backup/on.yml", ".lab/on.yml"),
                                            (".lab/migration/backup/units/addons/local/x/compose.yml",
                                             "addons/local/x/compose.yml"),
                                            (".lab/migration/backup/missing.json", ".lab/starred.json")],
                                  removes=[".lab/layout.json", ".lab/not-there"])
        done = subprocess.run(["sh", str(script)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0)
        self.assertEqual((self.text(".lab/on.yml"), self.text("addons/local/x/compose.yml"),
                          self.exists(".lab/layout.json"),
                          self.exists(".lab/starred.json")),
                         ("include:\n  - addons/echo/compose.yml\n", "original\n", False, False))
        self.assertIn("No copy of .lab/starred.json", done.stderr)
        self.assertEqual(self.mode("addons/local/x/compose.yml"), 0o640)

    def test_a_folder_of_copies_comes_back_file_by_file(self):
        self.put(".lab/migration/backup/targets/cloud/a__b.yml", "one\n")
        self.put(".lab/targets/cloud/other.yml", "mine\n")
        script = write_undo(restores=[(".lab/migration/backup/targets", ".lab/targets")])
        self.assertEqual(subprocess.run(["sh", str(script)], capture_output=True, text=True).returncode, 0)
        self.assertEqual((self.text(".lab/targets/cloud/a__b.yml"), self.text(".lab/targets/cloud/other.yml")),
                         ("one\n", "mine\n"))

    def test_the_text_of_the_script_is_pure_and_names_each_move_once(self):
        entries = [{"op": "move", "src": "a", "dst": "b", "why": "x"}, {"op": "copied", "src": "c", "dst": "d"},
                   {"op": "move", "src": "e", "dst": "f", "why": "x"}, {"op": "rekey", "ids": {}},
                   {"op": "move", "src": "", "dst": "g"}, {"op": "move", "src": 3, "dst": "h"},
                   {"op": "move", "dst": "i"}]
        text = moves.undo_script(entries, removes=[".lab/layout.json"])
        calls = [line for line in text.splitlines() if line.startswith(("back ", "drop "))]
        self.assertEqual(calls, ["back e f", "back a b", "drop .lab/layout.json"])   # the last move first
        self.assertTrue(text.startswith("#!/bin/sh\n") and text.endswith("exit $failed\n"))
        self.assertEqual(text, moves.undo_script(entries, removes=[".lab/layout.json"]))   # deterministic
        # A move written twice, since a run stopped before the rename and was run again, is one move: the later one
        again = entries[:3] + [{"op": "move", "src": "a", "dst": "b", "why": "x"}]
        calls = [line for line in moves.undo_script(again).splitlines() if line.startswith("back ")]
        self.assertEqual(calls, ["back a b", "back e f"])   # at the later line: the rename was made there

    def test_a_move_made_again_after_it_was_made_back_is_undone_each_time(self):
        def move(src, dst):
            return {"op": "move", "src": src, "dst": dst, "why": "shadow"}

        def calls(*entries):
            return [line for line in moves.undo_script(list(entries)).splitlines() if line.startswith("back ")]
        self.assertEqual(calls(move("a", "b"), move("b", "a"), move("a", "b")),
                         ["back a b", "back b a", "back a b"])
        self.assertEqual(calls(move("a", "b"), move("b", "a"), move("b", "a")), ["back b a", "back a b"])
        self.assertEqual(calls(move("a", "b"), move("b", "a"), move("a", "b"), move("a", "b")),
                         ["back a b", "back b a", "back a b"])
        self.assertEqual(calls(move("a", "b"), move("c", "d"), move("a", "b"), move("c", "d")),
                         ["back c d", "back a b"])

    def test_a_file_moved_made_back_and_moved_again_is_put_back_where_it_began(self):
        self.put("a/f.env", "TOKEN=1\n", 0o600)
        for src, dst in (("a/f.env", "b/f.env"), ("b/f.env", "a/f.env"), ("a/f.env", "b/f.env")):
            self.assertEqual(moves.relocate(src, dst, "shadow"), "moved")
        script = write_undo()
        for _ in range(2):   # and a second run leaves it there
            done = subprocess.run(["sh", str(script)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0)
            self.assertEqual((self.text("a/f.env"), self.exists("b/f.env"), self.mode("a/f.env")),
                             ("TOKEN=1\n", False, 0o600))

    def test_the_script_assigns_no_name_that_a_shell_keeps_for_itself(self):
        reserved = {"status", "pipestatus", "path", "cdpath", "fpath", "manpath", "options", "commands", "functions",
                    "aliases", "parameters", "history", "argv", "errno", "signals", "funcstack", "ppid", "pwd",
                    "oldpwd", "uid", "euid", "gid", "egid", "random", "seconds", "lineno", "shlvl", "ifs", "home",
                    "mail", "optarg", "optind", "term", "tty", "version"}
        names = set(re.findall(r"(?<![\w$-])([A-Za-z_]\w*)=", moves.SCRIPT))
        self.assertTrue(names)
        self.assertEqual({name for name in names if name.lower() in reserved}, set())

    @unittest.skipUnless(shutil.which("zsh"), "zsh is not installed: the script was not run in the shell of a Mac")
    def test_it_runs_in_zsh(self):
        self.put("a b.txt", "one\n")
        moves.relocate("a b.txt", "new/a b.txt", "machine")
        done = self.run_undo("zsh", cwd="/")
        self.assertEqual((done.returncode, done.stderr, self.text("a b.txt")), (0, "", "one\n"))

    def test_the_script_quotes_what_the_shell_would_read(self):
        text = moves.undo_script([{"op": "move", "src": "a b$(touch x)", "dst": "it's"}])
        self.assertIn("back 'a b$(touch x)' 'it'\"'\"'s'", text)

    def test_a_name_that_is_not_utf8_is_left_out_and_said(self):
        bad = os.fsdecode(b"bad-\xff")
        text = moves.undo_script([{"op": "move", "src": bad, "dst": "b"}, {"op": "move", "src": "c", "dst": "d"}])
        text.encode("utf-8")
        self.assertIn("back c d", text)
        self.assertIn("# 1 moved name not valid UTF-8", text)

    def test_the_script_reads_the_journal_when_it_is_given_no_entries(self):
        moves.journal("move", src="a", dst="b", why="x")
        self.assertIn("back a b\n", moves.undo_script())

    def test_the_script_is_private_whole_and_where_the_layout_says(self):
        script = write_undo()
        self.assertEqual((script, stat.S_IMODE(os.stat(script).st_mode)), (self.root / ".lab/migration/undo.sh", 0o700))
        self.assertEqual(self.mode(".lab/migration"), 0o700)

    def test_it_finds_the_project_from_where_it_is_not_from_where_it_is_run(self):
        self.put("a.txt", "one\n")
        moves.relocate("a.txt", "new/a.txt", "machine")
        script = write_undo()
        with tempfile.TemporaryDirectory() as elsewhere:
            self.assertEqual(subprocess.run(["sh", str(script)], cwd=elsewhere, capture_output=True).returncode, 0)
        self.assertEqual(self.text("a.txt"), "one\n")

    def test_a_move_the_script_cannot_make_is_said_and_the_exit_code_is_not_zero(self):
        self.put("new/a.txt", "one\n")
        os.makedirs(self.root / "blocked")
        (self.root / "blocked/sub").write_text("a file where a folder is needed\n", encoding="utf-8")
        text = moves.undo_script([{"op": "move", "src": "blocked/sub/a.txt", "dst": "new/a.txt"}])
        script = self.root / "undo.sh"
        script.write_text(text.replace('cd "$(dirname "$0")/../.."', f'cd "{self.root}"'), encoding="utf-8")
        for shell in shells():
            with self.subTest(shell=shell):
                done = subprocess.run([shell, str(script)], capture_output=True, text=True)
                self.assertEqual(done.returncode, 1)
                self.assertIn("Could not move new/a.txt back", done.stderr)


class Since(Moves):
    """What the undo script does with a file it is about to put the backup over (): the Lab's own work goes back as the
    backup has it, and what was written since the update is kept beside it, whatever it is and wherever it is. The measure
    of "since" is the script itself (`moves.undo_script`): nothing the migration writes comes after it."""

    LATER = 120   # seconds after the script was written: a person who used the Lab for two minutes

    def setUp(self):
        super().setUp()
        self.restores = [(".lab/migration/backup/on.yml", ".lab/on.yml"),
                         (".lab/migration/backup/starred.json", ".lab/starred.json"),
                         (".lab/migration/backup/tested.json", ".lab/tested.json"),
                         (".lab/migration/backup/targets", ".lab/targets")]

    def build(self, marker=True, witness=False):
        """The tree of a finished migration: the backup, what the migration wrote in its place (older than the script, which
        is written last, like the marker), the marker."""
        for name, text in (("on.yml", "include:\n  - addons/echo/compose.yml\n"), ("starred.json", '{"addon": []}\n'),
                           ("tested.json", "{}\n")):
            self.put(f".lab/migration/backup/{name}", text)
            self.put(f".lab/{name}", f"the migration wrote {name}\n")
        self.put(".lab/migration/backup/targets/cloud/addons__echo.yml", "original copy\n", 0o640)
        self.put(".lab/migration/backup/targets/cloud/same.yml", "same\n")
        self.put(".lab/targets/cloud/same.yml", "same\n")
        if marker:
            self.put(moves.MARKER, "{}\n")
        if witness:
            self.put(".lab/migration/started.json", "{}\n")
        self.script = write_undo(restores=self.restores, removes=[moves.MARKER, ".lab/migration/started.json"])
        for path in self.everything():
            os.utime(self.root / path, (time.time() - 60, time.time() - 60))   # all older than the script
        os.utime(self.script, (time.time(), time.time()))

    def everything(self):
        return [path for path in files(digest(self.root, skip=(".lab/migration/undo.sh",))) if not path.startswith(".lab/migration/")]

    def since(self, path, text, mode=None):
        """A file written after the script was: by the person, or by the Lab they used."""
        file = self.put(path, text, mode)
        later = os.stat(self.script).st_mtime + self.LATER
        os.utime(file, (later, later))

    def undo(self, shell="sh"):
        return subprocess.run([shell, str(self.script)], cwd="/", capture_output=True, text=True)

    def test_the_work_of_the_migration_goes_back_as_the_backup_has_it_and_says_nothing(self):
        for shell in shells():
            with self.subTest(shell=shell):
                self.wipe()
                self.build()
                done = self.undo(shell)
                self.assertEqual((done.returncode, done.stderr), (0, ""))
                self.assertEqual((self.text(".lab/on.yml"), self.text(".lab/starred.json")),
                                 ("include:\n  - addons/echo/compose.yml\n", '{"addon": []}\n'))
                self.assertEqual([path for path in self.everything() if "after-update" in path], [])

    def test_what_was_written_since_is_kept_beside_the_copy_that_comes_back(self):
        for shell in shells():
            with self.subTest(shell=shell):
                self.wipe()
                self.build()
                self.since(".lab/on.yml", "include:\n  - .lab/catalogs/fixture/addons/new/compose.yml\n", 0o600)   # the on file
                self.since(".lab/starred.json", '{"addon": ["new"]}\n')   # the stars
                self.since(".lab/targets/cloud/addons__echo.yml", "deployed again\n", 0o600)   # a platform copy
                self.since(".lab/targets/cloud/other.yml", "mine\n")   # not in the backup: not touched
                done = self.undo(shell)
                self.assertEqual(done.returncode, 0)
                self.assertEqual([self.text(".lab/on.yml"), self.text(".lab/starred.json"), self.text(".lab/tested.json"),
                                  self.text(".lab/targets/cloud/addons__echo.yml")],
                                 ["include:\n  - addons/echo/compose.yml\n", '{"addon": []}\n', "{}\n", "original copy\n"])
                self.assertEqual([self.text(".lab/on.yml.after-update"), self.text(".lab/starred.json.after-update"),
                                  self.text(".lab/targets/cloud/addons__echo.yml.after-update"),
                                  self.text(".lab/targets/cloud/other.yml")],
                                 ["include:\n  - .lab/catalogs/fixture/addons/new/compose.yml\n", '{"addon": ["new"]}\n',
                                  "deployed again\n", "mine\n"])
                self.assertEqual((self.mode(".lab/on.yml.after-update"), self.mode(".lab/on.yml")), (0o600, 0o644))
                for kept in (".lab/on.yml", ".lab/starred.json", ".lab/targets/cloud/addons__echo.yml"):
                    self.assertIn(f"Kept {kept} as {kept}.after-update", done.stderr)
                # what is the backup already is not kept, and what the migration wrote is not either
                self.assertFalse(self.exists(".lab/tested.json.after-update") or self.exists(".lab/targets/cloud/same.yml.after-update"))
                self.assertFalse(self.exists(".lab/layout.json"))

    def test_a_second_copy_never_takes_the_place_of_the_first(self):
        self.build()
        self.since(".lab/on.yml.after-update", "kept by an earlier undo\n")
        self.since(".lab/on.yml.after-update.2", "and another\n")
        self.since(".lab/on.yml", "written again\n")
        done = self.undo()
        self.assertEqual(done.returncode, 0)
        self.assertEqual([self.text(f".lab/on.yml.after-update{end}") for end in ("", ".2", ".3")],
                         ["kept by an earlier undo\n", "and another\n", "written again\n"])
        self.assertIn("as .lab/on.yml.after-update.3", done.stderr)

    def test_a_copy_is_never_kept_under_a_name_the_backup_is_about_to_put_a_file_at(self):
        """The backup of an earlier cycle holds `a.after-update`: the next cycle's undo keeps `a` as the next free name, and the
        copy of the backup that goes over the folder does not take the place of what was kept."""
        self.put(".lab/migration/backup/targets/cloud/a.yml", "original\n")
        self.put(".lab/migration/backup/targets/cloud/a.yml.after-update", "kept in the first cycle\n")
        self.script = write_undo(restores=[(".lab/migration/backup/targets", ".lab/targets")], removes=[moves.MARKER])
        self.put(moves.MARKER, "{}\n")
        self.since(".lab/targets/cloud/a.yml", "written since\n")
        done = self.undo()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([self.text(f".lab/targets/cloud/a.yml{end}") for end in ("", ".after-update", ".after-update.2")],
                         ["original\n", "kept in the first cycle\n", "written since\n"])

    def test_a_name_that_a_shell_would_read_is_a_name_in_a_folder_of_copies(self):
        names = ["a b$(touch x).yml", "it's 'quoted'.yml", "-rf.yml", "semi;colon && echo no.yml", "glob*[?].yml", "new\nline.yml",
                 "Démo ünï ✓.yml"]
        self.put(".lab/migration/backup/on.yml", "old\n")
        for name in names:
            self.put(f".lab/migration/backup/targets/cloud/{name}", "original\n")
        self.script = write_undo(restores=self.restores[:1] + self.restores[3:], removes=[moves.MARKER])
        self.put(moves.MARKER, "{}\n")
        for name in names:
            self.since(f".lab/targets/cloud/{name}", f"written since {name}\n")
        for shell in shells():
            with self.subTest(shell=shell):
                for name in names:   # each run starts from the same tree
                    self.since(f".lab/targets/cloud/{name}", f"written since {name}\n")
                    for kept in (f".lab/targets/cloud/{name}.after-update", f".lab/targets/cloud/{name}.after-update.2"):
                        if self.exists(kept):
                            (self.root / kept).unlink()
                self.put(moves.MARKER, "{}\n")
                done = self.undo(shell)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual([self.text(f".lab/targets/cloud/{name}.after-update") for name in names],
                                 [f"written since {name}\n" for name in names])
                self.assertEqual([self.text(f".lab/targets/cloud/{name}") for name in names], ["original\n"] * len(names))
                self.assertFalse(self.exists("x"))   # nothing was run

    def test_a_folder_or_a_link_where_a_file_goes_is_left_and_said(self):
        self.build()
        (self.root / ".lab/on.yml").unlink()
        os.makedirs(self.root / ".lab/on.yml")   # a folder where the on file goes
        self.put(".lab/theirs.json", "a file the link points to\n")
        (self.root / ".lab/starred.json").unlink()
        os.symlink("theirs.json", self.root / ".lab/starred.json")   # a link where the stars go
        os.utime(self.script, (time.time() - 600, time.time() - 600))   # both are newer than the script
        for shell in shells():
            with self.subTest(shell=shell):
                done = self.undo(shell)
                self.assertEqual(done.returncode, 1)
                self.assertIn("Left .lab/on.yml: it is not a file", done.stderr)
                self.assertIn("Left .lab/starred.json: it is not a file", done.stderr)
                self.assertIn("Could not restore .lab/on.yml", done.stderr)
                self.assertTrue(os.path.isdir(self.root / ".lab/on.yml"))
                self.assertEqual((os.readlink(self.root / ".lab/starred.json"), self.text(".lab/theirs.json")),
                                 ("theirs.json", "a file the link points to\n"))
                self.put(moves.MARKER, "{}\n")   # the marker went with the first run: the next starts like the first

    def test_a_copy_that_cannot_be_kept_is_not_put_over_the_file(self):
        self.build()
        self.since(".lab/targets/cloud/addons__echo.yml", "deployed again\n")
        os.chmod(self.root / ".lab/targets/cloud", 0o555)   # no copy can be made beside it
        self.addCleanup(os.chmod, self.root / ".lab/targets/cloud", 0o755)
        if os.access(self.root / ".lab/targets/cloud", os.W_OK):
            self.skipTest("this user writes everywhere")
        done = self.undo()
        self.assertEqual(done.returncode, 1)
        self.assertIn("Could not keep .lab/targets/cloud/addons__echo.yml", done.stderr)
        self.assertIn("Could not restore .lab/targets", done.stderr)
        self.assertEqual(self.text(".lab/targets/cloud/addons__echo.yml"), "deployed again\n")

    def test_a_migration_that_did_not_finish_puts_its_own_half_work_back_as_it_always_did(self):
        self.build(marker=False, witness=True)
        self.since(".lab/on.yml", "what the stopped migration wrote\n")
        done = self.undo()
        self.assertEqual((done.returncode, done.stderr, self.text(".lab/on.yml")),
                         (0, "", "include:\n  - addons/echo/compose.yml\n"))
        self.assertFalse(self.exists(".lab/on.yml.after-update"))

    def test_an_update_that_was_undone_already_keeps_whatever_differs_from_the_copy(self):
        self.build(marker=False)   # no marker, nothing of the migration left: the first run took them away
        for path in (".lab/on.yml", ".lab/starred.json"):
            self.put(path, "written by the old Lab\n")   # older than the script, and still kept
        done = self.undo()
        self.assertEqual(done.returncode, 0)
        self.assertEqual([self.text(".lab/on.yml.after-update"), self.text(".lab/starred.json.after-update")],
                         ["written by the old Lab\n"] * 2)
        self.assertEqual(self.text(".lab/on.yml"), "include:\n  - addons/echo/compose.yml\n")
        again = self.undo()   # and the next run has nothing to keep
        self.assertEqual((again.returncode, again.stderr), (0, ""))

    def test_closing_the_notice_does_not_change_what_was_written_since(self):
        self.build()
        self.since(".lab/starred.json", '{"addon": ["new"]}\n')
        later = os.stat(self.script).st_mtime + 10 * self.LATER
        os.utime(self.root / moves.MARKER, (later, later))   # the marker is written again when the notice is closed
        self.assertEqual(self.undo().returncode, 0)
        self.assertEqual(self.text(".lab/starred.json.after-update"), '{"addon": ["new"]}\n')

    def test_a_script_that_cannot_find_itself_keeps_what_it_would_put_over(self):
        self.build()
        self.put(".lab/on.yml", "the migration wrote it\n")
        os.utime(self.root / ".lab/on.yml", (time.time() - 3600, time.time() - 3600))
        script = self.root / "undo-copy.sh"
        script.write_text(self.script.read_text(encoding="utf-8").replace(f"ref={moves.UNDO}", "ref=.lab/not-there"),
                          encoding="utf-8")
        self.script = script
        script_text = script.read_text(encoding="utf-8").replace('cd "$(dirname "$0")/../.."', f'cd "{self.root}"')
        script.write_text(script_text, encoding="utf-8")
        self.assertEqual(self.undo().returncode, 0)
        self.assertEqual(self.text(".lab/on.yml.after-update"), "the migration wrote it\n")

    def test_the_measure_is_the_script_and_the_marker_is_the_layouts(self):
        self.assertEqual(moves.MARKER, layout.MARKER)
        text = moves.undo_script(removes=[moves.MARKER, ".lab/migration/started.json", "a b$(touch x)", os.fsdecode(b"bad-\xff")])
        line = [one for one in text.splitlines() if one.startswith("if present")]
        self.assertEqual(line, ["if present \"$marker\"; then counts=newer; elif present .lab/migration/started.json || present "
                                "'a b$(touch x)'; then counts=none; else counts=any; fi"])
        self.assertIn(f"ref={moves.UNDO}\n", text)
        self.assertEqual(len([one for one in moves.undo_script().splitlines() if one.startswith("if present")]), 1)
        self.assertIn("else counts=any; fi\n", moves.undo_script())   # no witness: a marker, or everything that differs

    def test_the_helpers_hold_no_single_quote_and_the_script_assigns_no_reserved_name(self):
        self.assertNotIn("'", moves._HELPERS)
        reserved = {"status", "pipestatus", "path", "cdpath", "fpath", "manpath", "options", "commands", "functions", "aliases",
                    "parameters", "history", "argv", "errno", "signals", "funcstack", "ppid", "pwd", "oldpwd", "uid", "euid",
                    "gid", "egid", "random", "seconds", "lineno", "shlvl", "ifs", "home", "mail", "optarg", "optind", "term",
                    "tty", "version", "mode", "time", "times"}
        text = moves.undo_script(removes=[moves.MARKER, ".lab/migration/started.json"])
        names = set(re.findall(r"(?<![\w$-])([A-Za-z_]\w*)=", text))
        self.assertTrue({"counts", "ref", "marker", "kept", "count"} <= names)
        self.assertEqual({name for name in names if name.lower() in reserved}, set())


class AfterTheMigration(Case):
    """The undo script of the real migration (`layout.migrate`) on a small machine: it puts every file back as it was, and what
    was written since is kept beside it. The migration writes the script last, and closing its notice writes the marker again:
    the measure of "since" has to hold with both."""

    def setUp(self):
        fixtures.project(self, paths, compose)
        self.patch(layout, "_failed", None)
        self.patch(layout, "_said", set())
        self.patch(layout, "_backed", None)
        self.patch(moves, "STEP", None)
        moves._INDEXES.clear()
        self.addCleanup(moves._INDEXES.clear)
        for path, text in {
            "addons/echo/.env": "ECHO_TOKEN=secret-echo\n",
            "addons/local/scratch/compose.yml": "x-gravitee: {kind: addon, name: Scratch, category: Other, description: Mine}\n"
                                                "include:\n  - ../../echo/compose.yml\nservices: {}\n",
            ".lab/on.yml": "include:\n  - addons/echo/compose.yml\n  - addons/local/scratch/compose.yml\n"
                           "  - {path: .lab/targets/cloud/addons__echo__compose.yml, project_directory: addons/echo}\n",
            ".lab/starred.json": json.dumps({"addon": ["echo"]}) + "\n",
            ".lab/tested.json": "{}\n",
            ".lab/targets/cloud/addons__echo__compose.yml": "a platform copy\n",
        }.items():
            self.write(path, text)
        self.before = digest(self.root, skip=(".lab/catalogs", ".lab/catalogs.flock"))
        self.script = self.root / moves.UNDO

    def migrate(self):
        layout.migrate()
        self.assertTrue((self.root / moves.MARKER).is_file())
        self.assertTrue(self.script.is_file())

    def undo(self):
        return subprocess.run(["sh", str(self.script)], cwd="/", capture_output=True, text=True)

    def later(self, path, text, seconds=60):
        """A file the person (or the Lab they used) wrote some time after the migration ended."""
        self.write(path, text)
        when = os.stat(self.script).st_mtime + seconds
        os.utime(self.root / path, (when, when))

    def settled(self):
        return {key: value for key, value in files(digest(self.root, skip=(".lab/catalogs", ".lab/catalogs.flock", ".lab/migration",
                                                                          ".lab/layout.json", ".lab/held"))).items()
                if not key.endswith(".after-update")}

    def test_straight_after_it_every_file_is_back_as_it_was_and_the_script_says_nothing(self):
        self.migrate()
        done = self.undo()
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(self.settled(), files({key: value for key, value in self.before.items()
                                                if not key.startswith((".lab/migration", ".lab/layout.json"))}))

    def test_what_was_written_since_the_migration_ended_is_kept_beside_the_copy_that_comes_back(self):
        self.migrate()
        self.later(".lab/starred.json", json.dumps({"addon": ["echo", "new"]}) + "\n")   # a star
        self.later(".lab/on.yml", "include:\n  - .lab/catalogs/fixture/addons/echo/compose.yml\n", 120)   # an Apply
        self.later(".lab/targets/cloud/addons__echo__compose.yml", "deployed again by a Lab that did not know\n", 180)   # a platform copy
        layout.dismiss()   # the person closed the notice: the marker is written again, after all of those
        later = os.stat(self.script).st_mtime + 3600
        os.utime(self.root / moves.MARKER, (later, later))
        done = self.undo()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(sorted(line.split(" as ")[1] for line in done.stderr.splitlines()),
                         [".lab/on.yml.after-update", ".lab/starred.json.after-update",
                          ".lab/targets/cloud/addons__echo__compose.yml.after-update"])
        self.assertEqual(json.loads(self.text(".lab/starred.json.after-update")), {"addon": ["echo", "new"]})
        self.assertEqual(self.settled(), files({key: value for key, value in self.before.items()
                                                if not key.startswith((".lab/migration", ".lab/layout.json"))}))

    def text(self, path):
        return (self.root / path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------------------
# A migration made of these primitives, run to its end and stopped at every step
# ---------------------------------------------------------------------------------------------------------------------

SECRETS = {"ECHO_TOKEN=secret-echo-env\n", "secret-echo-token\n", "secret-echo-crt\n", "secret-echo-key\n",
           "GATEWAY_TOKEN=secret-gateway\n", "SHOP=secret-shop-env\n", "secret-shop-state\n", "secret-shop-cloud\n",
           "MOVED=secret-held\n", "secret-held-token\n", "SCRATCH=secret-scratch\n", "SANDBOX=secret-sandbox\n",
           "secret-sandbox-state\n", "MINE=secret-mine\n", "secret-mine-state\n"}
HOME = ".lab/catalogs/fixture"
# The machine files of the units of the default catalog: where they were, where they go. A file of a key is moved alone
# and its folder stays, as the classes of the plan say.
MACHINE = [(f"addons/echo/{name}", f"{HOME}/addons/echo/{name}")
           for name in (".env", "secrets", "certs/server.crt", "certs/server.key")] \
    + [("addons/gateway/.env", f"{HOME}/addons/gateway/.env"),
       ("playgrounds/shop/.env", f"{HOME}/playgrounds/shop/.env"),
       ("playgrounds/shop/.state", f"{HOME}/playgrounds/shop/.state")]
# An item that is in no installed catalog: its settings wait in the held area
HELD = [(f"addons/moved-echo/{name}", f".lab/held/addons/moved-echo/{name}") for name in (".env", "secrets")]
STRAYS = [("addons/echo/notes.txt", ".lab/migration/kept/addons/echo/notes.txt"),
          ("playgrounds/shop/a b$(touch x)", ".lab/migration/kept/playgrounds/shop/a b$(touch x)")]
PERSONAL = [("addons/local/scratch", ".lab/catalogs/local/addons/scratch", "addon"),
            ("addons/local/other", ".lab/catalogs/local/addons/other", "addon"),
            ("setups/local/duo.yml", ".lab/catalogs/local/setups/duo.yml", "setup"),
            ("playgrounds/local/sandbox", ".lab/catalogs/local/playgrounds/sandbox", "playground"),
            ("playgrounds/mine", ".lab/catalogs/local/playgrounds/mine", "playground")]   # the last one is promoted
LEFTOVERS = ["addons/echo/certs", "addons/echo", "addons/gateway", "addons/moved-echo", "addons/local", "addons",
             "playgrounds/shop", "playgrounds/local", "playgrounds", "setups/local", "setups"]
KEPT_OUT = (".lab/catalogs", ".lab/migration", ".lab/layout.json", ".lab/held")


def files(found):
    """The entries of a digest that are not folders."""
    return {key: value for key, value in found.items() if value[0] != "dir"}


class Migration(Moves):
    """What the migration is made of, in a short version: the personal items, the machine files of the units of
    the default catalog, the held ones, what is kept aside, the stars and the undo script. The real one is
    `layout.py`."""

    SKIP = (moves.JOURNAL,)

    def build(self, filler=0):
        files = {
            "addons/echo/.env": ("ECHO_TOKEN=secret-echo-env\n", 0o600),
            "addons/echo/secrets/token": ("secret-echo-token\n", 0o600),
            "addons/echo/certs/server.crt": ("secret-echo-crt\n", 0o644),
            "addons/echo/certs/server.key": ("secret-echo-key\n", 0o600),
            "addons/echo/notes.txt": ("my notes\n", 0o644),
            "addons/gateway/.env": ("GATEWAY_TOKEN=secret-gateway\n", 0o600),
            "playgrounds/shop/.env": ("SHOP=secret-shop-env\n", 0o600),
            "playgrounds/shop/.state/created.json": ("secret-shop-state\n", 0o644),
            "playgrounds/shop/.state/cloud/env-1/created.json": ("secret-shop-cloud\n", 0o644),
            "playgrounds/shop/a b$(touch x)": ("a stray file\n", 0o644),
            "addons/moved-echo/.env": ("MOVED=secret-held\n", 0o600),
            "addons/moved-echo/secrets/token": ("secret-held-token\n", 0o600),
            "addons/local/scratch/compose.yml": (ADDON, 0o644),
            "addons/local/scratch/.env": ("SCRATCH=secret-scratch\n", 0o600),
            "addons/local/other/compose.yml": ("services: {}\n", 0o644),
            "setups/local/duo.yml": (SETUP, 0o644),
            "playgrounds/local/sandbox/compose.yml": (PLAYGROUND, 0o644),
            "playgrounds/local/sandbox/.env": ("SANDBOX=secret-sandbox\n", 0o600),
            "playgrounds/local/sandbox/.state/created.json": ("secret-sandbox-state\n", 0o644),
            "playgrounds/local/sandbox/seed/apim/apis/one.json": ("{}\n", 0o644),
            "playgrounds/mine/compose.yml": ("services: {}\n", 0o644),
            "playgrounds/mine/.env": ("MINE=secret-mine\n", 0o600),
            "playgrounds/mine/.state/created.json": ("secret-mine-state\n", 0o644),
            f"{HOME}/addons/echo/compose.yml": ("services: {}\n", 0o444),
            ".lab/on.yml": ("include:\n  - addons/echo/compose.yml\n  - addons/local/scratch/compose.yml\n"
                            "  - addons/moved-echo/compose.yml\n", 0o644),
            ".lab/starred.json": (json.dumps({"playground": ["mine", "shop"]}, indent=2) + "\n", 0o644),
            ".lab/tested.json": (json.dumps({"mine": sets("1"), "shop": sets("2")}, indent=2, sort_keys=True), 0o644),
        }
        files.update({f"playgrounds/shop/.state/records/r{index}.json": (f"{index}\n",
                                                                         0o644) for index in range(filler)})
        for path, (text, mode) in files.items():
            self.put(path, text, mode)
        os.symlink("../shop/.state/created.json", self.root / "playgrounds/shop/.state/latest")

    def migrate(self):
        root, backup = self.root, self.root / ".lab/migration/backup"
        restores = []
        for name in ("on.yml", "starred.json", "tested.json"):
            if not (backup / name).exists():
                backup.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / ".lab" / name, backup / name)
            restores.append((f".lab/migration/backup/{name}", f".lab/{name}"))
        for old, new, kind in PERSONAL:
            file_old, file_new = (old, new) if kind == "setup" else (f"{old}/compose.yml", f"{new}/compose.yml")
            copy = backup / "units" / file_old
            if (root / file_old).exists() and not copy.exists():
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / file_old, copy)
            moves.relocate(old, new, "personal")
            if (root / file_new).exists() and copy.exists():   # from the copy, so a second run writes the same text
                text = moves.repoint(copy.read_text(encoding="utf-8"), os.path.dirname(file_old),
                                     os.path.dirname(file_new), PREFIXES)
                if text != (root / file_new).read_text(encoding="utf-8"):
                    paths.write_atomic(root / file_new, text)
            restores.append((f".lab/migration/backup/units/{file_old}", file_old))
        for old, new in MACHINE:
            moves.relocate(old, new, "machine")
        for old, new in HELD:
            moves.relocate(old, new, "held")
        for old, new in STRAYS:
            moves.relocate(old, new, "kept")
        for folder in LEFTOVERS:
            try:
                os.rmdir(root / folder)
            except OSError:
                pass   # not empty yet, or already gone
        moves.rekey({"playground:mine": "local/mine"})
        on = (backup / "on.yml").read_text(encoding="utf-8")
        for old, new in (("addons/echo", f"{HOME}/addons/echo"), ("addons/local/scratch", PERSONAL[0][1])):
            on = on.replace(f"- {old}/", f"- {new}/")
        paths.write_atomic(root / ".lab/on.yml", on.replace("  - addons/moved-echo/compose.yml\n", ""))
        write_undo(restores=restores, removes=[".lab/layout.json"])
        paths.write_atomic(root / ".lab/layout.json", '{"layout": 2, "from": 1}\n')

    def undo(self, shell="sh"):
        return subprocess.run([shell, str(self.root / ".lab/migration/undo.sh")], cwd="/", capture_output=True,
                              text=True)

    def test_every_class_ends_where_the_table_says(self):
        self.build()
        self.migrate()
        self.assertEqual(
            [self.text(path) for path in (f"{HOME}/addons/echo/.env", f"{HOME}/addons/echo/secrets/token",
                                          f"{HOME}/addons/echo/certs/server.key", f"{HOME}/addons/gateway/.env",
                                          f"{HOME}/playgrounds/shop/.env",
                                          f"{HOME}/playgrounds/shop/.state/cloud/env-1/created.json",
                                          ".lab/held/addons/moved-echo/.env",
                                          ".lab/held/addons/moved-echo/secrets/token",
                                          ".lab/catalogs/local/addons/scratch/.env",
                                          ".lab/catalogs/local/playgrounds/mine/.env",
                                          ".lab/catalogs/local/playgrounds/sandbox/.state/created.json")],
            ["ECHO_TOKEN=secret-echo-env\n", "secret-echo-token\n", "secret-echo-key\n",
             "GATEWAY_TOKEN=secret-gateway\n",
             "SHOP=secret-shop-env\n", "secret-shop-cloud\n", "MOVED=secret-held\n", "secret-held-token\n",
             "SCRATCH=secret-scratch\n", "MINE=secret-mine\n", "secret-sandbox-state\n"])
        self.assertEqual((self.mode(f"{HOME}/addons/echo/.env"), self.mode(f"{HOME}/addons/echo/secrets/token"),
                          self.mode(".lab/held"), self.mode(".lab/held/addons/moved-echo")),
                         (0o600, 0o600, 0o700, 0o700))
        self.assertEqual(os.readlink(self.root / f"{HOME}/playgrounds/shop/.state/latest"),
                         "../shop/.state/created.json")
        self.assertEqual(self.text(".lab/migration/kept/playgrounds/shop/a b$(touch x)"), "a stray file\n")
        for gone in ("addons", "setups", "playgrounds"):
            self.assertFalse(self.exists(gone), gone)
        self.assertEqual(self.text(".lab/catalogs/local/setups/duo.yml"),
                         SETUP.replace("../../addons/echo/compose.yml", "../../fixture/addons/echo/compose.yml")
                         .replace("../../addons/local/scratch/compose.yml", "../addons/scratch/compose.yml"))
        self.assertEqual(self.text(".lab/on.yml"), f"include:\n  - {HOME}/addons/echo/compose.yml\n"
                                                   "  - .lab/catalogs/local/addons/scratch/compose.yml\n")
        self.assertEqual(json.loads(self.text(".lab/starred.json")), {"playground": ["local/mine", "shop"]})
        self.assertEqual(sorted(json.loads(self.text(".lab/tested.json"))), ["local/mine", "shop"])

    def test_a_second_run_changes_nothing_and_says_nothing(self):
        self.build()
        self.migrate()
        after, lines = digest(self.root, skip=self.SKIP), len(self.journal())
        self.migrate()
        self.assertEqual(digest(self.root, skip=self.SKIP), after)
        self.assertEqual(len(self.journal()), lines)

    def test_no_secret_is_in_the_journal_or_the_script_and_every_one_is_still_somewhere(self):
        self.build()
        self.migrate()
        for name in ("journal.jsonl", "undo.sh"):
            text = self.text(f".lab/migration/{name}")
            self.assertEqual([secret for secret in SECRETS if secret.strip() in text], [], name)
        self.assertEqual(self.mode(".lab/migration"), 0o700)
        self.assertEqual(SECRETS - {text.decode() for text in contents(self.root, skip=(".lab/migration",))}, set())

    def test_every_secret_is_somewhere_at_every_crash_and_the_run_converges(self):
        self.build()
        hook = crash_at(0)
        self.patch(moves, "STEP", hook)
        self.migrate()
        clean, steps = digest(self.root, skip=self.SKIP), len(hook.calls)
        self.assertGreater(steps, 40)
        for number in range(1, steps + 1):
            with self.subTest(step=number, label=hook.calls[number - 1]):
                self.wipe()
                self.build()
                moves.STEP = crash_at(number)
                with self.assertRaises(Crash):
                    self.migrate()
                moves.STEP = None
                found = {text.decode() for text in contents(self.root, skip=(".lab/migration/backup",))}
                self.assertEqual(SECRETS - found, set(), "lost at the crash")
                self.migrate()
                self.assertEqual(digest(self.root, skip=self.SKIP), clean)
        moves.STEP = None

    def test_the_undo_script_puts_back_every_file_byte_for_byte(self):
        self.build()
        before = digest(self.root, skip=KEPT_OUT)
        for shell in shells():
            with self.subTest(shell=shell):
                self.wipe()
                self.build()
                self.migrate()
                self.assertNotEqual(files(digest(self.root, skip=KEPT_OUT)), files(before))
                done = self.undo(shell)
                self.assertEqual((done.returncode, done.stderr), (0, ""))
                after = digest(self.root, skip=KEPT_OUT)
                self.assertEqual(files(after), files(before))   # contents, modes and links
                self.assertEqual([key for key in before if key not in after], [])   # and every folder that was there
                self.assertFalse(self.exists("x"))   # the stray named `a b$(touch x)` was not run
                self.assertFalse(self.exists(".lab/layout.json"))

    def test_the_undo_script_of_a_run_that_was_stopped_and_finished(self):
        self.build()
        before = digest(self.root, skip=KEPT_OUT)
        self.patch(moves, "STEP", crash_at(20))
        with self.assertRaises(Crash):
            self.migrate()
        self.patch(moves, "STEP", None)
        self.migrate()
        done = self.undo()
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(files(digest(self.root, skip=KEPT_OUT)), files(before))

    def test_nothing_needs_docker_git_or_the_network(self):
        self.build()
        refuse = AssertionError("this ran a program or opened a connection")
        with mock.patch.object(subprocess, "Popen", side_effect=refuse), \
                mock.patch.object(os, "system", side_effect=refuse), \
                mock.patch.object(socket.socket, "connect", side_effect=refuse), \
                mock.patch.object(socket, "getaddrinfo", side_effect=refuse):
            self.migrate()
            moves.undo_script()
            moves.tracked(self.root / ".lab")

    def test_five_hundred_files_do_not_cost_five_hundred_moves(self):
        self.build(filler=500)
        started = time.process_time()
        self.migrate()
        # a rename per entry: a tenth of a second where this was written
        self.assertLess(time.process_time() - started, 2.0)
        self.assertLess(len(self.journal("move")), 40)


# ---------------------------------------------------------------------------------------------------------------------
# What the module is, and the stub of the layout
# ---------------------------------------------------------------------------------------------------------------------

class Module(Case):

    def test_it_uses_the_standard_library_and_no_program_runner(self):
        allowed = {"bisect", "errno", "filecmp", "hashlib", "json", "os", "posixpath", "re", "shlex", "shutil", "stat",
                   "struct",
                   "threading", "time", "unicodedata", "pathlib", "typing"}
        tree = ast.parse(Path(moves.__file__).read_text(encoding="utf-8"))
        imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                    for alias in node.names}
        imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                     if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module}
        self.assertEqual(imported - allowed, set())

    def test_the_frozen_signatures(self):
        import inspect
        from typing import Optional

        def signature(*parameters, result):
            """A signature built, not printed: Python 3.14 prints Optional[str] as `str | None`, 3.9 as `Optional[str]`, and the
            two are equal as annotations."""
            kinds = inspect.Parameter.POSITIONAL_OR_KEYWORD
            return inspect.Signature([inspect.Parameter(name, kinds, annotation=note, **({"default": default} if default is not
                                                                                          inspect.Parameter.empty else {}))
                                      for name, note, default in parameters], return_annotation=result)
        empty = inspect.Parameter.empty
        wanted = {
            "relocate": signature(("src", empty, empty), ("dst", empty, empty), ("why", str, empty), result=str),
            "repoint": signature(("text", str, empty), ("was", str, empty), ("now", str, empty), ("table", list, empty),
                                 result=str),
            "rekey": signature(("mapping", dict, empty), result=None),
            "adopt_held": signature(("ns", str, empty), ("kind", str, empty), ("name", str, empty),
                                    ("repoint_from", Optional[str], None), result=list)}
        self.assertEqual({name: inspect.signature(getattr(moves, name)) for name in wanted}, wanted)
        self.assertEqual(inspect.signature(moves.adopt_held).parameters["repoint_from"].annotation, Optional[str])

    def test_the_helpers_the_migration_calls_have_names_that_are_not_private(self):
        self.assertEqual((moves.parents, moves.discard, moves.step), (moves._parents, moves._discard, moves._step))
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.patch(paths, "ROOT", Path(folder.name).resolve())
        target = str(paths.ROOT / moves.MIGRATION / "kept" / "a" / "b.txt")
        moves.parents(target)
        self.assertEqual((stat.S_IMODE(os.stat(paths.ROOT / moves.MIGRATION / "kept" / "a").st_mode),
                          stat.S_IMODE(os.stat(paths.ROOT / moves.MIGRATION).st_mode)), (0o700, 0o700))
        (paths.ROOT / moves.MIGRATION / "kept" / "a" / "c").mkdir()
        (paths.ROOT / moves.MIGRATION / "kept" / "a" / "c" / "f").write_text("x", encoding="utf-8")
        moves.discard(str(paths.ROOT / moves.MIGRATION / "kept" / "a"))
        self.assertFalse((paths.ROOT / moves.MIGRATION / "kept" / "a").exists())
        seen = []
        self.patch(moves, "STEP", seen.append)
        moves.step("label")
        self.assertEqual(seen, ["label"])

    def test_the_test_hook_is_off_by_default(self):
        self.assertIsNone(moves.STEP)

    def test_the_files_the_stars_and_the_tested_sets_follow_an_item_in_are_the_ones_those_modules_read(self):
        """`rekey` writes `.lab/starred.json` and `.lab/tested.json` and keeps twenty sets for a playground; starred.py and
        tested.py read and write the same: one name for each, or a star would follow an item into a file nobody reads."""
        self.assertEqual((moves.STARRED, moves.TESTED, moves.TESTED_KEPT), (starred.FILE, tested.FILE, tested.KEPT))

    def test_the_marker_is_the_one_the_layout_writes_and_the_undo_script_removes(self):
        self.assertEqual((moves.MARKER, moves.UNDO, moves.MIGRATION), (layout.MARKER, f"{layout.MIGRATION}/undo.sh", layout.MIGRATION))


class Layout(Moves):
    """The stub that ships first: a machine is ready, nothing waits and nothing moves."""

    def test_a_machine_is_ready(self):
        self.assertEqual(layout.status(), {"phase": "ready", "notice": None})
        self.assertIs(layout.waiting(), False)

    def test_ensure_does_nothing_even_on_an_old_layout(self):
        self.put("addons/echo/.env", "TOKEN=1\n")
        self.put(".lab/on.yml", "include:\n  - addons/echo/compose.yml\n")
        before = digest(self.root)
        self.assertIsNone(layout.ensure())
        self.assertEqual(digest(self.root), before)

    def test_the_signatures_are_the_ones_the_migration_keeps(self):
        import inspect
        self.assertEqual({name: str(inspect.signature(getattr(layout, name)))
                          for name in ("status", "waiting", "ensure", "migrate_personal", "migrate")},
                         {"status": "() -> dict", "waiting": "() -> bool", "ensure": "() -> None",
                          "migrate_personal": "() -> dict", "migrate": "() -> dict"})

    def test_the_two_parts_answer_an_empty_report_of_the_shape_of_the_real_one(self):
        keys = ["moved", "personal", "promoted", "kept", "left", "held", "turned_off", "problems"]
        for part in (layout.migrate_personal, layout.migrate):
            self.assertEqual(part(), {key: [] for key in keys})
        report = layout.migrate()
        report["moved"].append("x")
        self.assertEqual(layout.migrate()["moved"], [])   # a report is not shared between calls


if __name__ == "__main__":
    unittest.main()
