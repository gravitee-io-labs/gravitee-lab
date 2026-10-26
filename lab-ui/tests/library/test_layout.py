"""layout: the migration of an existing machine to the layout of the catalogs, on synthetic trees. The tree is the
shape of a clone after the `git pull` of the split: the units that git removed leave their settings, keys and state
behind (`.env`, `secrets/`, certificates, `.state/`), the person's own items sit in the `local` folders, and the on file
and the platform copies name folders that are not there. The default catalog is the fixture catalog, so the unit names
are the fixture's (`echo`, `shop`, ...): no real item is named. Nothing here needs Docker or the network: a guard makes
a program run or a connection opened a failure of the test. A crash is injected between any two steps with
`moves.STEP`, which the migration extends with steps of its own."""

import ast
import contextlib
import errno
import hashlib
import io
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.docker import compose, envfiles
from lab_ui.library import layers, layout, moves, project
from lab_ui.gravitee import license
from lab_ui import paths
from lab_ui.catalogs import registry, store

from .. import Case, fixtures, gitfixture

# The pattern of the Makefile (an ERE for `grep -E`): the lines of an on file that name folders of the old layout
GUARD_PATTERN = r'^[[:space:]]*-[[:space:]]*"?(addons|setups|playgrounds)/'

DATA = Path(__file__).resolve().parents[1] / "data" / "layout"
STAMP = "2026-10-17T09:00:00Z"
HOME = ".lab/catalogs/fixture"
SKIP = (moves.JOURNAL, ".lab/catalogs.flock")   # the journal holds the time of each line; the lock file is made by taking it
# What the undo script does not put back, because it is not the old layout's: the install, the migration's own folder, the
# marker, the held settings. The identical copies it dropped are not either: git brings them back
UNDONE = (".lab/catalogs", ".lab/migration", ".lab/layout.json", ".lab/held", ".lab/catalogs.flock")
DROPPED = ("addons/echo-core/compose.yml", "addons/echo-core/README.md")

# Everything below is a dummy. A value that starts with `secret-` is one that must be somewhere at every step
MACHINE = {
    # the settings of units of the catalog: their folder has no compose.yml any more
    "addons/echo/.env": ("ECHO_TOKEN=secret-echo-env\n", 0o600),
    "addons/echo/secrets/token": ("secret-echo-token\n", 0o600),
    "addons/echo/certs/server.crt": ("secret-echo-crt\n", 0o644),
    "addons/echo/certs/server.key": ("secret-echo-key\n", 0o600),
    "addons/gateway/.env": ("FIXTURE_GATEWAY_TOKEN=secret-gateway\n", 0o600),
    "addons/cluster/.env.local": ("CLUSTER=secret-cluster\n", 0o600),
    "playgrounds/shop/.env": ("FIXTURE_SHOP=secret-shop-env\n", 0o600),
    "playgrounds/shop/.state/created.json": ("secret-shop-state\n", 0o644),
    "playgrounds/shop/.state/cloud/env-1/created.json": ("secret-shop-cloud\n", 0o644),
    "playgrounds/tour/.state/created.json": ("secret-tour-state\n", 0o644),
    # a unit whose compose text is the catalog's, byte for byte (the test writes it), and one that was changed
    "addons/echo-core/.env": ("CORE=secret-core\n", 0o600),
    "addons/echo-twin/.env": ("TWIN=secret-twin\n", 0o600),
    # a file of the person's in a folder of settings
    "addons/port-core/.env": ("PORT=secret-port\n", 0o600),
    "addons/port-core/notes.txt": ("my notes\n", 0o644),
    "playgrounds/shop/a b$(touch x)": ("a stray file\n", 0o644),
    # an item that left for a catalog that is not installed
    "addons/moved-echo/.env": ("MOVED=secret-held\n", 0o600),
    "addons/moved-echo/secrets/token": ("secret-held-token\n", 0o600),
    # items the catalog does not have: the person's now
    "addons/custom/.env": ("CUSTOM=secret-custom\n", 0o600),
    "playgrounds/mine/.env": ("MINE=secret-mine\n", 0o600),
    "playgrounds/mine/.state/created.json": ("secret-mine-state\n", 0o644),
    # the person's own items
    "addons/local/scratch/.env": ("SCRATCH=secret-scratch\n", 0o600),
    "playgrounds/local/trip-demo/.env": ("TRIP=secret-trip\n", 0o600),
    "playgrounds/local/trip-demo/.state/created.json": ("secret-trip-state\n", 0o644),
    "playgrounds/local/trip-demo/seed/apim/apis/one.json": ("{}\n", 0o644),
}
SECRETS = {text for text, _ in MACHINE.values() if "secret-" in text}
CUSTOM = """x-gravitee: {kind: addon, name: Custom, category: Other, description: A thing of mine}
services:
  custom:
    image: busybox
    restart: unless-stopped
    volumes: ["./data:/data"]
"""
MINE = """x-gravitee: {kind: playground, name: Mine, category: Other, description: A playground of mine}
include:
  - ../../addons/custom/compose.yml
  - ../../addons/echo/compose.yml
services:
  mine-seed:
    build: ../../seeder
    volumes: ["./.state:/state", "./seed:/seed:ro"]
    command: sh -c "ls ../.."
"""
MINE_SET = """x-gravitee: {kind: setup, name: Mine set, description: Mine}
include:
  - ../addons/custom/compose.yml
  - ../addons/echo/compose.yml
"""
SCRATCH = "x-gravitee: {kind: addon, name: Scratch, category: Other, description: Scratch of mine}\nservices: {}\n"
OTHER = "x-gravitee: {kind: addon, name: Other, category: Other, description: Other of mine}\nservices: {}\n"
GATEWAY_COPY = f"{compose.HEADER}addons/gateway/compose.yml (fixture-gateway=gateway-cloud)\nservices: {{}}\n"
ON = """include:
  - playgrounds/shop/compose.yml
  - addons/echo/compose.yml
  - addons/local/scratch/compose.yml
  - addons/moved-echo/compose.yml
  - playgrounds/mine/compose.yml
  - "addons/custom/compose.yml"   # quoted
  - {path: ".lab/targets/cloud/addons__gateway__compose.yml", project_directory: "addons/gateway"}
  - /somewhere/of/mine.yml   # a line of the person's
"""
ON_AFTER = f"""include:
  - {HOME}/playgrounds/shop/compose.yml
  - {HOME}/addons/echo/compose.yml
  - .lab/catalogs/local/addons/scratch/compose.yml
  - .lab/catalogs/local/playgrounds/mine/compose.yml
  - ".lab/catalogs/local/addons/custom/compose.yml"   # quoted
  - {{path: ".lab/targets/cloud/.lab__catalogs__fixture__addons__gateway__compose.yml", project_directory: "{HOME}/addons/gateway"}}
  - /somewhere/of/mine.yml   # a line of the person's
"""
STARRED = {"addon": ["custom", "echo", "moved-echo"], "playground": ["mine", "shop"], "setup": ["mine-set"]}


def sets(*versions):
    return [{"versions": {"APIM_VERSION": version}, "images": {"APIM_VERSION": f"sha256:{version}"},
             "at": f"2026-10-{index + 1:02d}T10:00:00Z"} for index, version in enumerate(versions)]


class Crash(Exception):
    """What a hook raises to stop a run between two steps."""


def crash_at(number):
    """A `moves.STEP` that raises on its `number`th call; its `calls` says what the calls were."""
    calls = []

    def hook(label):
        calls.append(label)
        if len(calls) == number:
            raise Crash(label)
    hook.calls = calls
    return hook


def crash_on(label, nth=1):
    """A `moves.STEP` that raises on the `nth` call with this label: a run stopped at a point of the module's own."""
    seen = []

    def hook(found):
        if found == label:
            seen.append(found)
            if len(seen) == nth:
                raise Crash(found)
    return hook


def digest(root, skip=()):
    """Every entry under `root`, links not followed: {path: (kind, mode[, sha256 or target])}."""
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


def texts(root, skip=()):
    """What the files under `root` hold: a sorted list, since where a secret ended up does not matter, that it is
    somewhere does."""
    found = []
    for folder, _, files in os.walk(root):
        for name in files:
            path = os.path.join(folder, name)
            if not any(os.path.relpath(path, root).startswith(one) for one in skip) and not os.path.islink(path):
                with open(path, "rb") as f:
                    found.append(f.read().decode("utf-8", "replace"))
    return sorted(found)


def files_of(found):
    return {key: value for key, value in found.items() if value[0] != "dir"}


@contextlib.contextmanager
def outside():
    """The migration runs no program and opens no connection: Docker, git and the network are out of reach."""
    refuse = AssertionError("this ran a program or opened a connection")
    with mock.patch.object(subprocess, "Popen", side_effect=refuse), \
            mock.patch.object(subprocess, "run", side_effect=refuse), \
            mock.patch.object(os, "system", side_effect=refuse), \
            mock.patch.object(socket.socket, "connect", side_effect=refuse), \
            mock.patch.object(socket, "getaddrinfo", side_effect=refuse):
        yield


class Machine(Case):
    """A clone after the pull of the split: the fixture catalog installed as the default catalog, and the old layout's
    leftovers (`build`)."""

    def setUp(self):
        self.install()
        self.patch(layout, "_failed", None)
        self.patch(layout, "_said", set())
        self.patch(layout, "_backed", None)
        self.patch(layout, "_now", lambda: STAMP)
        self.patch(moves, "STEP", None)
        moves._INDEXES.clear()
        self.addCleanup(moves._INDEXES.clear)

    def install(self):
        fixtures.project(self, paths, compose)

    # -- the tree

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

    def installed(self, rel):
        """A file of the fixture catalog as the install has it, or as the fixtures have it while it is not installed."""
        file = self.root / HOME / rel
        return (file if file.exists() else fixtures.CATALOGS / "fixture" / rel).read_text(encoding="utf-8")

    def build(self, filler=0, on=ON, personal=True):
        """The legacy tree: 12 units and their machine entries, the person's own items, the on file, the stars, the tested
        sets and a platform copy. `filler` adds records to a `.state`."""
        for path, (text, mode) in MACHINE.items():
            if personal or "/local/" not in path:
                self.put(path, text, mode)
        # a copy of the catalog's unit, byte for byte, and a copy that was changed
        self.put("addons/echo-core/compose.yml", self.installed("addons/echo-core/compose.yml"))
        self.put("addons/echo-core/README.md", self.installed("addons/echo-core/README.md"))
        self.put("addons/echo-twin/compose.yml", self.installed("addons/echo-twin/compose.yml") + "# mine\n")
        self.put("addons/echo-twin/README.md", self.installed("addons/echo-twin/README.md"))
        # items the catalog does not have
        self.put("addons/custom/compose.yml", CUSTOM)
        self.put("playgrounds/mine/compose.yml", MINE)
        self.put("playgrounds/mine/seed/apim/apis/one.json", "{}\n")
        self.put("setups/mine-set.yml", MINE_SET)
        if personal:
            self.put("addons/local/scratch/compose.yml", SCRATCH)
            self.put("addons/local/other/compose.yml", OTHER)
            for rel in ("playgrounds/local/trip-demo/compose.yml", "playgrounds/local/relay/compose.yml",
                        "setups/local/duo.yml", "setups/local/trio.yml"):
                self.put(rel, (DATA / "legacy" / rel).read_text(encoding="utf-8"))
        os.symlink("../shop/.state/created.json", self.root / "playgrounds/shop/.state/latest")
        for index in range(filler):
            self.put(f"playgrounds/shop/.state/records/r{index:03d}.json", f"{index}\n")
        self.put(".lab/on.yml", on)
        self.put(".lab/starred.json", json.dumps(STARRED, indent=2) + "\n")
        self.put(".lab/tested.json", json.dumps({"mine": sets("1"), "shop": sets("2")}, indent=2, sort_keys=True))
        self.put(".lab/targets/cloud/addons__gateway__compose.yml", GATEWAY_COPY)
        self.put(".lab/platforms.json", "{}\n")

    # -- running it

    def migrate(self):
        with outside():
            return layout.migrate()

    def ensure(self):
        with outside():
            return layout.ensure()

    def moved(self, **how):
        """The tree after a clean run of `build(**how)`, for what a run that was stopped must end as."""
        self.build(**how)
        self.migrate()
        return digest(self.root, skip=SKIP)

    def marker(self):
        return json.loads(self.text(".lab/layout.json"))

    def report(self):
        return json.loads(self.text(f"{layout.REPORT}"))["report"]

    def wipe(self):
        for folder, folders, _ in os.walk(self.root):
            for name in folders:
                if not os.path.islink(os.path.join(folder, name)):
                    os.chmod(os.path.join(folder, name), 0o755)
        for child in list(self.root.iterdir()):
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()

    def keep(self):
        """A copy of the folder as it is, to come back to: restore()."""
        self.copy = self.root.parent / f"{self.root.name}-copy"
        self.addCleanup(shutil.rmtree, self.copy, True)
        shutil.rmtree(self.copy, True)
        shutil.copytree(self.root, self.copy, symlinks=True)

    def restore(self):
        self.wipe()
        for child in self.copy.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.copytree(child, self.root / child.name, symlinks=True)
            else:
                shutil.copy2(child, self.root / child.name, follow_symlinks=False)
        moves._INDEXES.clear()


# ---------------------------------------------------------------------------------------------------------------------
# Detection, the fresh clone and the content that git tracks
# ---------------------------------------------------------------------------------------------------------------------

class Detection(Machine):

    def test_a_fresh_clone_gets_the_marker_and_nothing_moves(self):   # B1
        before = digest(self.root, skip=SKIP)
        self.ensure()
        self.assertEqual(self.marker(), {"layout": 2, "from": 0})
        after = digest(self.root, skip=SKIP)
        self.assertEqual({key: value for key, value in after.items() if key != ".lab/layout.json"}, before)
        self.assertEqual(layout.status(), {"phase": "ready", "notice": None})

    def test_nothing_is_signalled_by_a_tree_of_the_new_layout(self):
        fixtures.local(self, "scratch")
        fixtures.local(self, "sandbox")
        self.assertEqual(layout._signals(), [])
        self.assertIs(layout.waiting(), False)

    def test_each_signal_is_read_from_the_disk(self):
        self.assertEqual(layout._signals(), [])
        self.put(".lab/on.yml", "include:\n  - addons/echo/compose.yml\n")
        self.assertEqual(layout._signals(), ["S1"])
        self.put(".lab/on.yml", "include:\n  - .lab/catalogs/fixture/addons/echo/compose.yml\n")
        self.assertEqual(layout._signals(), [])
        self.put("addons/echo/.env", "X=1\n")
        self.assertEqual(layout._signals(), ["S2"])
        shutil.rmtree(self.root / "addons")
        self.put("setups/local/duo.yml", SCRATCH)
        self.assertEqual(layout._signals(), ["S3"])
        shutil.rmtree(self.root / "setups")
        self.put(".lab/targets/cloud/playgrounds__x__compose.yml", "x\n")
        self.assertEqual(layout._signals(), ["S4"])
        shutil.rmtree(self.root / ".lab/targets")
        self.put(layout.PLAN, "{}")
        self.assertEqual(layout._signals(), ["S0"])

    def test_s0_is_the_witness_or_the_plan_and_the_marker_ends_it(self):
        self.put(layout.STARTED, "{}")
        self.assertEqual(layout._signals(), ["S0"])
        self.assertEqual((layout.status()["phase"], layout.waiting()), ("waiting", True))
        self.put(".lab/layout.json", '{"layout": 2}')
        self.assertEqual((layout.status()["phase"], layout.waiting()), ("ready", False))

    def test_the_guide_files_junk_and_ghost_folders_are_not_signals(self):
        for name in ("README.md", "AGENTS.md", "CLAUDE.md", ".DS_Store"):
            self.put(f"addons/{name}", "x\n")
        self.put("playgrounds/.DS_Store", "x\n")
        os.makedirs(self.root / "addons/ghost/deeper")
        os.makedirs(self.root / "setups/local")
        self.put("setups/local/.DS_Store", "x\n")
        self.assertEqual(layout._signals(), [])

    def test_after_the_marker_only_the_on_file_and_the_platform_copies_count(self):
        self.put(".lab/layout.json", '{"layout": 2, "from": 1}')
        self.put("addons/echo/.env", "X=1\n")
        self.put("playgrounds/local/x/compose.yml", SCRATCH)
        self.assertEqual(layout._signals(first=False), [])   # B5: a directory the Lab could not move is not found again
        self.put(".lab/on.yml", "include:\n  - addons/echo/compose.yml\n")
        self.assertEqual(layout._signals(first=False), ["S1"])

    def test_the_forms_of_the_on_file_that_the_lab_writes_and_no_others_are_signals(self):
        for line in ("  - addons/echo/compose.yml", '  - "playgrounds/shop/compose.yml"', "  - ./setups/pair.yml",
                     "  - 'addons/echo/compose.yml'", "  - addons/echo/compose.yml   # note",
                     '  - {path: ".lab/targets/cloud/addons__gateway__compose.yml", project_directory: "addons/gateway"}'):
            with self.subTest(line=line):
                self.assertTrue(layout._signal_s1(f"include:\n{line}\n"))
        for line in ("  - /somewhere/addons/echo/compose.yml", "  - .lab/catalogs/fixture/addons/echo/compose.yml",
                     "  - path: addons/echo/compose.yml", "  - ../addons/echo/compose.yml", "# - addons/echo/compose.yml",
                     "  - addons-extra/compose.yml",
                     '  - {path: ".lab/targets/cloud/.lab__catalogs__fixture__addons__gateway__compose.yml", '
                     'project_directory: ".lab/catalogs/fixture/addons/gateway"}'):
            with self.subTest(line=line):
                self.assertFalse(layout._signal_s1(f"include:\n{line}\n"))

    def test_the_makefile_pattern_and_the_signal_agree_on_the_lines_the_lab_writes(self):
        pattern = re.compile(GUARD_PATTERN.replace("[[:space:]]", r"\s"))
        for line in ("  - addons/echo/compose.yml", '  - "setups/pair.yml"', "-playgrounds/x/compose.yml",
                     "  - addons/echo/compose.yml   # note"):
            self.assertTrue(pattern.match(line) and layout._signal_s1(line + "\n"), line)
        for line in ("  - .lab/catalogs/fixture/addons/echo/compose.yml", "  - /elsewhere/addons/x.yml", "include:",
                     "  - addons-extra/compose.yml"):
            self.assertFalse(pattern.match(line) or layout._signal_s1(line + "\n"), line)
        self.assertEqual(layout.GUARD, "This Lab was updated: run make update to move your settings.")

    def test_no_default_file_means_the_legacy_folders_are_the_layout(self):
        (self.root / "catalogs.default.json").unlink()
        self.build()
        before = digest(self.root)
        self.assertEqual(layout.status(), {"phase": "ready", "notice": None})
        self.assertIs(layout.waiting(), False)
        self.ensure()
        self.assertEqual(self.migrate(), {key: [] for key in layout._report()})
        self.assertEqual(digest(self.root), before)
        self.assertFalse(self.exists(".lab/layout.json"))

    def test_a_lock_is_not_taken_when_there_is_nothing_to_do(self):
        self.put(".lab/layout.json", '{"layout": 2, "from": 0}')
        with mock.patch.object(registry, "flock", side_effect=AssertionError("the lock was taken")):
            self.ensure()
            self.migrate()
            self.assertEqual(layout.migrate_personal(), {key: [] for key in layout._report()})


@unittest.skipUnless(gitfixture.available(), "git is not installed")
class Tracked(Machine):
    """The engine before the catalogs, or a fork that keeps its content: the paths of the old layout are the layout."""

    def git(self, *args):
        return gitfixture.git("-C", str(self.root), *args)

    def test_nothing_is_touched_nothing_is_written_and_one_line_says_why(self):
        self.build()
        self.put(".gitignore", "/.lab/\n.env\n.env.*\n*.key\n*.crt\n/addons/*/secrets/\n/addons/local/\n/setups/local/\n"
                               "/playgrounds/local/\n/playgrounds/*/.state/\n")
        self.git("init", "-q")
        self.put("addons/echo/compose.yml", "services: {}\n")   # tracked content, beside the machine files
        self.put("setups/pair.yml", "include: []\n")
        self.git("add", ".gitignore", "addons/echo/compose.yml", "setups/pair.yml")
        self.git("commit", "-qm", "the old layout")
        moves._INDEXES.clear()
        before, index, status = digest(self.root), (self.root / ".git/index").read_bytes(), self.git("status", "--porcelain")
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.ensure()
            self.ensure()
            report = self.migrate()
        self.assertEqual((digest(self.root), (self.root / ".git/index").read_bytes(), self.git("status", "--porcelain")),
                         (before, index, status))
        self.assertEqual((report, layout.status(), layout.waiting()), ({key: [] for key in layout._report()},
                                                                        {"phase": "ready", "notice": None}, False))
        self.assertFalse(self.exists(".lab/layout.json"))
        self.assertEqual(said.getvalue().count("\n"), 1)   # one line, the first time only
        self.assertIn("git tracks", said.getvalue())
        with self.assertRaisesRegex(moves.Refused, "tracked by git"):
            moves.relocate("addons/echo/compose.yml", ".lab/catalogs/local/addons/echo/compose.yml", "personal")

    def test_a_download_without_a_git_folder_is_not_tracked(self):
        self.build()
        self.assertFalse(layout._tracked())
        self.assertEqual(layout.status()["phase"], "waiting")

    def test_a_folder_with_a_git_folder_whose_index_cannot_be_read_is_tracked(self):
        self.build()
        (self.root / ".git").mkdir()
        (self.root / ".git/index").write_bytes(b"garbage")
        moves._INDEXES.clear()
        self.assertTrue(layout._tracked())
        self.assertEqual(layout.status(), {"phase": "ready", "notice": None})


# ---------------------------------------------------------------------------------------------------------------------
# The synthetic replica (B2), class by class (C)
# ---------------------------------------------------------------------------------------------------------------------

class Replica(Machine):

    def test_every_class_of_the_table_ends_where_the_table_says(self):   # B2
        self.build()
        report = self.migrate()
        self.assertEqual(self.marker(), {"layout": 2, "from": 1, "migrated": STAMP})
        for gone in ("addons", "setups", "playgrounds"):
            self.assertFalse(self.exists(gone), gone)   # nothing is left in the legacy folders
        for path, (text, mode) in MACHINE.items():   # settings of the catalog's units: the same path in the install
            if path.startswith(("addons/echo/", "addons/gateway/", "addons/cluster/", "addons/echo-core/",
                                "addons/echo-twin/", "addons/port-core/.env", "playgrounds/shop/.", "playgrounds/tour/")):
                self.assertEqual(self.text(f"{HOME}/{path}"), text, path)
                self.assertEqual(self.mode(f"{HOME}/{path}"), mode, path)
        self.assertEqual(report, {
            # every item whose folder is another now: each one of the catalog, the person's own and the promoted
            "moved": ["addon:cluster", "addon:echo", "addon:echo-core", "addon:echo-twin", "addon:future", "addon:gateway",
                      "addon:local/custom", "addon:local/other", "addon:local/scratch", "addon:port-core",
                      "playground:local/mine", "playground:local/relay", "playground:local/trip-demo", "playground:shop",
                      "playground:tour", "setup:local/duo", "setup:local/mine-set", "setup:local/trio", "setup:pair"],
            "personal": ["addon:local/other", "addon:local/scratch", "playground:local/relay",
                         "playground:local/trip-demo", "setup:local/duo", "setup:local/trio"],
            "promoted": ["addon:local/custom", "playground:local/mine", "setup:local/mine-set"],
            "kept": ["addon:echo-twin", "addon:port-core", "playground:shop"],
            "left": [], "held": ["addon:moved-echo"], "turned_off": ["addon:moved-echo"], "problems": []})

    def test_the_machine_files_of_the_catalog_s_units_land_at_the_same_relative_path_with_the_same_mode(self):   # C1
        self.build()
        sizes = {path: len(text) for path, (text, _) in MACHINE.items()}
        self.migrate()
        for path in ("addons/echo/.env", "addons/echo/secrets/token", "addons/echo/certs/server.crt",
                     "addons/echo/certs/server.key", "addons/gateway/.env", "addons/cluster/.env.local"):
            self.assertEqual((os.stat(self.root / HOME / path).st_size, self.mode(f"{HOME}/{path}")),
                             (sizes[path], MACHINE[path][1]), path)

    def test_a_state_folder_with_one_folder_for_each_platform_is_the_same_tree_afterwards(self):   # C2
        self.build(filler=40)
        before = digest(self.root / "playgrounds/shop/.state")
        self.migrate()
        self.assertEqual(digest(self.root / HOME / "playgrounds/shop/.state"), before)
        self.assertEqual(os.readlink(self.root / HOME / "playgrounds/shop/.state/latest"), "../shop/.state/created.json")

    def test_a_promoted_unit_is_local_with_its_state_its_includes_its_star_and_its_tested_set(self):   # C3
        self.build()
        self.migrate()
        self.assertEqual(self.text(".lab/catalogs/local/playgrounds/mine/.state/created.json"), "secret-mine-state\n")
        self.assertEqual(self.text(".lab/catalogs/local/playgrounds/mine/.env"), "MINE=secret-mine\n")
        self.assertEqual(self.text(".lab/catalogs/local/playgrounds/mine/compose.yml"), MINE.replace(
            "../../addons/echo/compose.yml", "../../../fixture/addons/echo/compose.yml").replace(
            "build: ../../seeder", "build: ../../../../../seeder"))   # custom is local too: the include stays
        self.assertEqual(self.text(".lab/catalogs/local/setups/mine-set.yml"), MINE_SET.replace(
            "../addons/echo/compose.yml", "../../fixture/addons/echo/compose.yml"))
        self.assertEqual(json.loads(self.text(".lab/starred.json")),
                         {"addon": ["echo", "local/custom", "moved-echo"], "playground": ["local/mine", "shop"],
                          "setup": ["local/mine-set"]})
        self.assertEqual(sorted(json.loads(self.text(".lab/tested.json"))), ["local/mine", "shop"])
        self.assertEqual(paths.ids("playground"), ["shop", "tour", "local/mine", "local/relay", "local/trip-demo"])

    def test_a_promoted_unit_with_the_name_of_one_that_is_local_takes_a_suffix_everywhere(self):   # C4
        self.build()
        self.put(".lab/catalogs/local/addons/custom/compose.yml", OTHER)
        self.migrate()
        self.assertEqual(self.text(".lab/catalogs/local/addons/custom/compose.yml"), OTHER)   # theirs is not touched
        self.assertEqual(self.text(".lab/catalogs/local/addons/custom-2/compose.yml"), CUSTOM)
        self.assertEqual(self.text(".lab/catalogs/local/addons/custom-2/.env"), "CUSTOM=secret-custom\n")
        self.assertIn("../../addons/custom-2/compose.yml", self.text(".lab/catalogs/local/playgrounds/mine/compose.yml"))
        self.assertIn("addons/custom-2/compose.yml", self.text(".lab/on.yml"))
        self.assertEqual(json.loads(self.text(".lab/starred.json"))["addon"], ["echo", "local/custom-2", "moved-echo"])
        plan = json.loads(self.text(layout.PLAN))
        self.assertEqual(plan["ids"]["addon:custom"], "local/custom-2")

    def test_a_modified_copy_is_kept_aside_whole_and_its_machine_files_move(self):   # C5
        self.build()
        self.migrate()
        self.assertEqual(self.text(".lab/migration/kept/addons/echo-twin/compose.yml"),
                         self.installed("addons/echo-twin/compose.yml") + "# mine\n")
        self.assertTrue(self.exists(".lab/migration/kept/addons/echo-twin/README.md"))   # all of it, the identical file too
        self.assertEqual(self.text(f"{HOME}/addons/echo-twin/.env"), "TWIN=secret-twin\n")
        self.assertIn("addon:echo-twin", self.report()["kept"])

    def test_an_identical_copy_is_dropped_and_reported_nowhere(self):   # C6
        self.build()
        report = self.migrate()
        self.assertFalse(self.exists(".lab/migration/kept/addons/echo-core"))
        self.assertEqual(self.text(f"{HOME}/addons/echo-core/.env"), "CORE=secret-core\n")
        self.assertEqual(sorted(path.name for path in (self.root / HOME / "addons/echo-core").iterdir()),
                         [".env", "README.md", "compose.yml"])   # the catalog's own files, and the settings beside them
        self.assertNotIn("addon:echo-core", report["kept"] + report["left"] + report["held"] + report["promoted"])

    def test_a_stray_file_goes_to_kept_and_a_held_settings_folder_to_held(self):   # C7
        self.build()
        self.migrate()
        self.assertEqual(self.text(".lab/migration/kept/addons/port-core/notes.txt"), "my notes\n")
        self.assertEqual(self.text(".lab/migration/kept/playgrounds/shop/a b$(touch x)"), "a stray file\n")
        self.assertEqual(self.text(".lab/held/addons/moved-echo/.env"), "MOVED=secret-held\n")
        self.assertEqual(self.text(".lab/held/addons/moved-echo/secrets/token"), "secret-held-token\n")
        self.assertEqual((self.mode(".lab/held"), self.mode(".lab/held/addons"), self.mode(".lab/held/addons/moved-echo"),
                          self.mode(".lab/migration"), self.mode(".lab/migration/kept")), (0o700,) * 5)
        self.assertEqual(self.text(f"{HOME}/addons/port-core/.env"), "PORT=secret-port\n")

    def test_junk_in_the_folders_goes_and_is_never_kept_aside(self):   # C11
        self.build()
        for rel in ("addons/echo/__pycache__/x.pyc", "addons/echo/certs/.DS_Store", "playgrounds/shop/node_modules/a/b.js",
                    "playgrounds/shop/.pytest_cache/v", "addons/port-core/.compose.yml.check.yml",
                    "addons/port-core/.a.effects.yml", "addons/port-core/.b.1234.tmp", "addons/Thumbs.db"):
            self.put(rel, "junk\n")
        os.chmod(self.root / "playgrounds/shop/node_modules/a", 0o555)
        os.symlink("/etc", self.root / "addons/gateway/node_modules")   # a link: removed as a link, /etc is not followed
        report = self.migrate()
        for gone in ("addons", "playgrounds", "setups"):
            self.assertFalse(self.exists(gone), gone)
        self.assertEqual(report["kept"], ["addon:echo-twin", "addon:port-core", "playground:shop"])
        self.assertTrue(os.path.isdir("/etc"))
        self.assertEqual(texts(self.root / ".lab/migration/kept"), sorted(
            ["a stray file\n", "my notes\n", self.installed("addons/echo-twin/README.md"),
             self.installed("addons/echo-twin/compose.yml") + "# mine\n"]))

    def test_a_copy_an_editor_made_of_a_settings_file_is_a_settings_file_and_never_junk(self):
        self.build()
        secrets = {"addons/echo/.env.tmp": "A=secret-tmp-one\n", "addons/echo/.env.local.tmp": "B=secret-tmp-two\n",
                   "addons/moved-echo/.env.tmp": "C=secret-tmp-held\n", "addons/echo/server.key.tmp": "D=secret-key-tmp\n",
                   "addons/.env.tmp": "E=secret-in-the-kind-folder\n"}
        for path, text in secrets.items():
            self.put(path, text, 0o600)
        self.put("addons/echo/..env.k3j.tmp", "junk-of-an-interrupted-write\n")   # what the Lab's own write leaves is junk
        self.assertFalse(layout._scratch_name(".env.tmp", False))
        self.assertTrue(layout._scratch_name("..env.k3j.tmp", False))
        report = self.migrate()
        for path in ("addons/echo/.env.tmp", "addons/echo/.env.local.tmp"):   # a settings file: into the install, 0600
            self.assertEqual((self.text(f"{HOME}/{path}"), self.mode(f"{HOME}/{path}")), (secrets[path], 0o600), path)
        self.assertEqual(self.text(".lab/held/addons/moved-echo/.env.tmp"), "C=secret-tmp-held\n")
        self.assertEqual(self.text(".lab/migration/kept/addons/echo/server.key.tmp"), "D=secret-key-tmp\n")   # a stray: kept
        self.assertEqual(self.text("addons/.env.tmp"), "E=secret-in-the-kind-folder\n")   # no unit to go to: stays, told
        self.assertEqual(report["left"], ["addons/.env.tmp"])
        self.assertNotIn("junk-of-an-interrupted-write\n", texts(self.root))
        self.assertEqual({text for text in secrets.values()} - set(texts(self.root, skip=(".lab/migration/backup",))), set())

    def test_a_ghost_folder_and_an_empty_folder_named_like_a_key_go_and_are_never_reported(self):   # B9
        self.build()
        os.makedirs(self.root / "addons/ghost/deeper")
        os.makedirs(self.root / "addons/echo/certs/server.key.d")
        os.makedirs(self.root / "playgrounds/shop/data")
        self.put(".lab/catalogs/local/x", "")
        os.remove(self.root / ".lab/catalogs/local/x")
        report = self.migrate()
        self.assertEqual((self.exists("addons"), self.exists(f"{HOME}/addons/ghost")), (False, False))
        self.assertEqual(report["kept"], ["addon:echo-twin", "addon:port-core", "playground:shop"])
        self.assertEqual(report["left"], [])

    def test_an_empty_folder_named_like_a_key_is_a_ghost_and_a_ghost_alone_is_no_signal(self):   # B9
        os.makedirs(self.root / "addons/kafka/certs/server.key")
        os.makedirs(self.root / "playgrounds/acme/data")
        self.assertEqual(layout._signals(), [])
        self.ensure()
        self.assertEqual(self.marker(), {"layout": 2, "from": 0})


# ---------------------------------------------------------------------------------------------------------------------
# Part A offline (A1, E4), the other ways in (B3, B4, B5, B6, B7)
# ---------------------------------------------------------------------------------------------------------------------

class Offline(Machine):
    """The default catalog is listed and has been asked for, and has not arrived: nothing of it is installed."""

    def install(self):
        self.folder(paths, compose)
        fixtures.core(self)
        fixtures.default(self, "fixture")
        fixtures.declare(self, "fixture")

    def test_the_persons_own_items_move_at_once_and_nothing_else_does(self):   # A1
        self.build()
        untouched = {key: value for key, value in digest(self.root, skip=SKIP).items()
                     if not key.startswith(("addons/local", "setups/local", "playgrounds/local", ".lab/migration"))}
        report = layout.migrate_personal()
        self.assertEqual(report["personal"], ["addon:local/other", "addon:local/scratch", "playground:local/relay",
                                              "playground:local/trip-demo", "setup:local/duo", "setup:local/trio"])
        for rel in ("addons/local/scratch", "playgrounds/local/relay", "setups/local/duo.yml"):
            self.assertFalse(self.exists(rel), rel)
        self.assertEqual(self.text(".lab/catalogs/local/playgrounds/trip-demo/.env"), "TRIP=secret-trip\n")
        self.assertEqual(paths.ids("playground"), ["local/relay", "local/trip-demo"])
        self.assertEqual(paths.ids("addon"), ["local/other", "local/scratch"])
        self.assertEqual(paths.ids("setup"), ["local/duo", "local/trio"])
        # their includes of the catalog's units point where the catalog will put them, whether or not it is there
        self.assertIn("../../../fixture/addons/echo/compose.yml", self.text(".lab/catalogs/local/playgrounds/trip-demo/compose.yml"))
        after = {key: value for key, value in digest(self.root, skip=SKIP).items()
                 if not key.startswith((".lab/catalogs", ".lab/migration", "addons/local", "setups/local",
                                        "playgrounds/local"))}
        self.assertEqual(after, {key: value for key, value in untouched.items() if not key.startswith(".lab/catalogs")})
        self.assertFalse(self.exists(".lab/layout.json"))
        again = digest(self.root, skip=SKIP)
        self.assertEqual(layout.migrate_personal(), report)   # a second run says the same, from the disk
        self.assertEqual(digest(self.root, skip=SKIP), again)   # and changes nothing

    def test_what_part_a_writes_is_the_golden_text(self):   # D1
        self.build()
        layout.migrate_personal()
        for kind, rel in (("playgrounds", "trip-demo"), ("playgrounds", "relay")):
            self.assertEqual(self.text(f".lab/catalogs/local/{kind}/{rel}/compose.yml"),
                             (DATA / "expected" / kind / "local" / rel / "compose.yml").read_text(encoding="utf-8"), rel)
        for rel in ("duo", "trio"):
            self.assertEqual(self.text(f".lab/catalogs/local/setups/{rel}.yml"),
                             (DATA / "expected/setups/local" / f"{rel}.yml").read_text(encoding="utf-8"), rel)

    def test_the_layout_waits_and_the_core_alone_is_read_until_the_catalog_is_installed(self):
        self.build()
        on = self.text(".lab/on.yml")
        legacy = digest(self.root / "addons/echo"), digest(self.root / "playgrounds/shop")
        self.ensure()
        self.assertEqual(self.text(".lab/on.yml"), on)   # byte for byte
        self.assertEqual((digest(self.root / "addons/echo"), digest(self.root / "playgrounds/shop")), legacy)
        self.assertFalse(self.exists(".lab/layout.json"))
        self.assertTrue(self.exists(".lab/catalogs/local/addons/scratch/compose.yml"))   # but Part A has run
        status = layout.status()
        self.assertEqual((status["phase"], layout.waiting()), ("waiting", True))
        self.assertEqual(status["notice"]["rows"], [{"key": "settings", "label": "Settings", "items": ["Not changed"],
                                                      "hint": ""}])
        self.assertEqual(compose.environment()["COMPOSE_FILE"], "docker-compose.yml")   # the core alone
        self.assertEqual(self.migrate(), self.migrate())   # Part B waits, and says what Part A did
        self.assertEqual(self.text(".lab/on.yml"), on)
        self.assertFalse(self.exists(layout.PLAN))

    def test_part_b_runs_when_the_catalog_arrives_and_finishes_the_move(self):   # E4 then B6
        self.build()
        self.ensure()
        self.assertEqual(layout.status()["phase"], "waiting")
        fixtures.install(self, "fixture")
        self.put("addons/echo-core/compose.yml", self.installed("addons/echo-core/compose.yml"))
        report = self.migrate()
        self.assertEqual((self.marker()["from"], layout.status()["phase"], layout.waiting()), (1, "ready", False))
        self.assertEqual(self.text(".lab/on.yml"), ON_AFTER)
        self.assertIn("playground:local/mine", report["promoted"])
        self.assertEqual(compose.environment()["COMPOSE_FILE"], paths.COMPOSE_FILE)

    def test_the_catalog_has_to_be_valid_not_only_added(self):
        self.build()
        fixtures.install(self, "fixture", kind="folder")
        os.remove(self.root / ".lab/catalogs/fixture/catalog.yml")
        self.assertTrue(layout._default_ready("fixture"))   # a linked working copy is a folder: it is what it is
        fixtures.declare(self, "fixture", pin="1" * 40)   # a git entry with a pin and no install
        self.assertFalse(layout._default_ready("fixture"))

    def test_a_person_s_edit_while_the_catalog_downloads_is_kept(self):
        self.build()
        layout.migrate_personal()
        edited = self.text(".lab/catalogs/local/setups/duo.yml") + "# mine, while I waited\n"
        self.put(".lab/catalogs/local/setups/duo.yml", edited)
        fixtures.install(self, "fixture")
        self.migrate()
        self.assertEqual(self.text(".lab/catalogs/local/setups/duo.yml"), edited)


class OptedOut(Machine):
    """The person has no default catalog: their catalogs file reads whole and does not list it (`catalog.py reset --empty`).
    Nothing will install it, so the layout does not wait for it."""

    def install(self):
        self.folder(paths, compose)
        fixtures.core(self)
        fixtures.default(self, "fixture")
        self.put(".lab/catalogs.json", json.dumps({"schema": 1, "catalogs": []}))

    def test_the_move_runs_against_a_catalog_with_no_items_and_the_layout_does_not_wait(self):
        self.build()
        self.ensure()
        self.assertEqual((layout.status()["phase"], layout.waiting()), ("ready", False))
        self.assertEqual(self.marker()["from"], 1)
        self.assertEqual(SECRETS - set(texts(self.root, skip=(".lab/migration/backup",))), set())
        report = self.report()
        self.assertIn("addon:echo", report["held"])   # its settings wait for a catalog that has it
        self.assertEqual(self.text(".lab/held/addons/echo/.env"), "ECHO_TOKEN=secret-echo-env\n")
        self.assertIn("addon:echo", report["turned_off"])
        self.assertIn("addon:local/custom", report["promoted"])   # what the pull left with a compose file is theirs
        self.assertNotIn("addons/echo", self.text(".lab/on.yml"))
        self.assertIn(".lab/catalogs/local/addons/scratch/compose.yml", self.text(".lab/on.yml"))
        self.assertEqual(paths.ids("addon").count("local/scratch"), 1)
        layers.ready()   # Start, Apply and Create are not refused

    def test_a_line_a_stale_lab_writes_later_is_translated_against_the_same_catalog(self):
        self.build()
        self.ensure()
        self.put(".lab/on.yml", self.text(".lab/on.yml") + "  - addons/echo/compose.yml\n")
        self.ensure()
        self.assertNotIn("addons/echo", self.text(".lab/on.yml"))
        self.assertEqual(layout._signals(first=False), [])

    def test_a_catalogs_file_that_does_not_read_is_a_doubt_not_a_decision(self):
        self.put(".lab/catalogs.json", "{not json")
        self.build()
        self.ensure()
        self.assertEqual((layout.status()["phase"], self.exists(".lab/layout.json")), ("waiting", False))
        self.assertEqual(self.text("addons/echo/.env"), "ECHO_TOKEN=secret-echo-env\n")   # only Part A ran

    def test_no_catalogs_file_at_all_is_a_first_run_not_a_decision(self):
        os.remove(self.root / ".lab/catalogs.json")
        self.build()
        self.ensure()
        self.assertEqual(layout.status()["phase"], "waiting")


class Ways(Machine):

    def test_only_a_legacy_on_file_is_detected_and_only_it_is_translated(self):   # B3
        self.put(".lab/on.yml", "include:\n  - addons/echo/compose.yml\n  - playgrounds/shop/compose.yml\n"
                                "  - addons/gone/compose.yml\n")
        self.put(".lab/starred.json", '{"addon": ["echo"]}')
        self.assertEqual(layout._signals(), ["S1"])
        before = digest(self.root, skip=(*SKIP, ".lab/on.yml", ".lab/migration", ".lab/layout.json"))
        report = self.migrate()
        self.assertEqual(self.text(".lab/on.yml"), f"include:\n  - {HOME}/addons/echo/compose.yml\n"
                                                   f"  - {HOME}/playgrounds/shop/compose.yml\n")
        self.assertEqual(report["turned_off"], ["addon:gone"])
        self.assertEqual(digest(self.root, skip=(*SKIP, ".lab/on.yml", ".lab/migration", ".lab/layout.json")), before)
        self.assertEqual(self.marker()["from"], 1)

    def test_a_stale_lab_that_writes_a_legacy_line_after_the_move_is_translated_at_the_next_start(self):   # B4
        self.build()
        self.migrate()
        self.put(".lab/on.yml", "include:\n  - addons/gateway/compose.yml\n  - playgrounds/mine/compose.yml\n"
                                "  - addons/not-a-unit/compose.yml\n")
        self.put(".lab/targets/cloud/addons__gateway__compose.yml", GATEWAY_COPY + "# a later one\n")
        self.assertEqual((layout.status()["phase"], layout._signals(first=False)), ("ready", ["S1", "S4"]))
        self.ensure()
        self.assertEqual(self.text(".lab/on.yml"), f"include:\n  - {HOME}/addons/gateway/compose.yml\n"
                                                   "  - .lab/catalogs/local/playgrounds/mine/compose.yml\n")
        self.assertFalse(self.exists(".lab/targets/cloud/addons__gateway__compose.yml"))   # nothing names it any more
        backups = sorted(path.name for path in (self.root / layout.BACKUP / "targets/cloud").iterdir())
        self.assertEqual(len(backups), 2)   # the first copy, and the later one that was not in the backup
        self.assertEqual(layout._signals(first=False), [])
        after = digest(self.root, skip=SKIP)
        self.ensure()
        self.assertEqual(digest(self.root, skip=SKIP), after)

    def test_a_directory_the_lab_could_not_move_is_not_found_again_after_the_marker(self):   # B5
        self.build()
        real = os.rename

        def rename(src, dst, *more, **how):
            if str(src) == str(self.root / "playgrounds/shop/.state"):
                raise OSError(errno.EBUSY, "Resource busy")
            return real(src, dst, *more, **how)
        with mock.patch.object(moves.os, "rename", rename):
            report = self.migrate()
        self.assertEqual(report["left"], ["playgrounds/shop/.state"])
        self.assertTrue(self.exists("playgrounds/shop/.state/created.json"))   # it stayed where it was
        self.assertEqual((layout.status()["phase"], layout._signals(first=False)), ("ready", []))
        before = digest(self.root, skip=SKIP)
        self.ensure()
        self.assertEqual(self.migrate(), {key: [] for key in layout._report()})
        self.assertEqual(digest(self.root, skip=SKIP), before)
        self.assertEqual(layout.status()["notice"]["rows"][-1],
                         {"key": "not_moved", "label": "Not moved", "items": ["playgrounds/shop/.state"],
                          "hint": "Files stay where they were"})

    def test_a_catalog_installed_before_the_first_start_is_moved_to_at_once(self):   # B6
        self.build()
        self.ensure()
        self.assertEqual(self.marker(), {"layout": 2, "from": 1, "migrated": STAMP})
        self.assertEqual(self.text(".lab/on.yml"), ON_AFTER)
        notice = layout.status()["notice"]
        self.assertEqual([row["key"] for row in notice["rows"]], ["personal", "kept", "turned_off"])
        self.assertTrue(self.exists(layout.REPORT))
        self.assertTrue(self.exists(".lab/migration/undo.sh"))

    def test_a_run_that_stops_between_the_switch_and_the_marker_is_resumed_by_its_plan(self):   # B7
        self.build()
        self.patch(moves, "STEP", lambda label: (_ for _ in ()).throw(Crash(label)) if label == "layout:undo" else None)
        with self.assertRaises(Crash):
            self.migrate()
        self.patch(moves, "STEP", None)
        layout._failed = None   # the process ended with the crash: the next one knows nothing of it
        self.assertEqual(self.text(".lab/on.yml"), ON_AFTER)   # the switch was made
        self.assertFalse(self.exists(".lab/layout.json"))
        self.assertEqual(layout._signals(), ["S0"])   # and only the plan says the move is not over
        self.assertEqual((layout.status()["phase"], layout.waiting()), ("waiting", True))
        self.assertEqual(compose.environment()["COMPOSE_FILE"], "docker-compose.yml")
        report = self.migrate()
        self.assertEqual(self.text(".lab/on.yml"), ON_AFTER)   # nothing was translated twice
        self.assertEqual(self.marker()["from"], 1)
        self.assertTrue(self.exists(".lab/migration/undo.sh"))
        self.assertEqual(report["turned_off"], ["addon:moved-echo"])   # said by the journal, not by this run
        self.assertEqual((layout.status()["phase"], layout.waiting()), ("ready", False))

    def test_a_marker_that_does_not_read_is_still_the_commit_point(self):
        self.build(on="include: []\n")
        os.remove(self.root / ".lab/targets/cloud/addons__gateway__compose.yml")
        self.put(".lab/layout.json", "not json")
        self.assertEqual(layout.status(), {"phase": "ready", "notice": None})
        before = digest(self.root, skip=SKIP)
        self.migrate()
        self.assertEqual(digest(self.root, skip=SKIP), before)


# ---------------------------------------------------------------------------------------------------------------------
# What the filesystem does not allow (C8, C9, C10), and hostile trees
# ---------------------------------------------------------------------------------------------------------------------

class Elsewhere(Machine):

    def refusing(self, source, error):
        """A patch of `os.rename` that raises `error` for the rename of `source` and for no other."""
        real = os.rename

        def rename(src, dst, *more, **how):
            if str(src) == str(self.root / source):
                raise error
            return real(src, dst, *more, **how)
        return mock.patch.object(moves.os, "rename", rename)

    def test_a_symlinked_local_folder_is_linked_again_and_its_texts_are_written_in_place(self):   # C8
        self.build(personal=False)
        outer = self.root.parent / (self.root.name + "-synced")
        self.addCleanup(shutil.rmtree, outer, True)
        (outer / "scratch").mkdir(parents=True)
        (outer / "scratch/compose.yml").write_text("include:\n  - ../../echo/compose.yml\nservices: {}\n", encoding="utf-8")
        (outer / "scratch/.env").write_text("SCRATCH=secret-synced\n", encoding="utf-8")
        os.makedirs(self.root / "addons", exist_ok=True)
        os.symlink(outer, self.root / "addons/local")
        report = self.migrate()
        self.assertTrue(os.path.islink(self.root / ".lab/catalogs/local/addons"))
        self.assertEqual(os.path.realpath(self.root / ".lab/catalogs/local/addons"), os.path.realpath(outer))
        self.assertTrue(os.path.islink(self.root / "addons/local"))   # re-linked, not moved
        self.assertEqual(report["left"], ["addons/local"])
        self.assertEqual((outer / "scratch/compose.yml").read_text(encoding="utf-8"),
                         "include:\n  - ../../../fixture/addons/echo/compose.yml\nservices: {}\n")
        self.assertEqual((outer / "scratch/.env").read_text(encoding="utf-8"), "SCRATCH=secret-synced\n")
        self.assertIn("local/scratch", paths.ids("addon"))
        self.assertIn("addons/local", [item for row in layout.status()["notice"]["rows"] for item in row["items"]])
        self.assertIn("drop .lab/catalogs/local/addons", self.text(".lab/migration/undo.sh"))
        done = subprocess.run(["sh", str(self.root / ".lab/migration/undo.sh")], cwd="/", capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stderr, os.path.islink(self.root / ".lab/catalogs/local/addons")),
                         (0, "", False))
        self.assertTrue((outer / "scratch/.env").exists())   # the person's folder is not touched by the undo

    def synced(self, suffix="synced"):
        """A folder outside the project with one personal item in it: what `addons/local` is a link to."""
        outer = self.root.parent / f"{self.root.name}-{suffix}"
        self.addCleanup(shutil.rmtree, outer, True)
        (outer / "scratch").mkdir(parents=True)
        (outer / "scratch/compose.yml").write_text("include:\n  - ../../echo/compose.yml\nservices: {}\n", encoding="utf-8")
        (outer / "scratch/.env").write_text("SCRATCH=secret-synced\n", encoding="utf-8")
        return outer

    def test_a_run_resumed_after_the_plan_does_not_back_up_what_part_b_put_in_a_linked_folder(self):   # C8
        self.build(personal=False)
        outer = self.synced()
        os.symlink(outer, self.root / "addons/local")
        self.patch(moves, "STEP", crash_on("layout:drop"))   # the promoted add-on is in the linked folder by then
        with self.assertRaises(Crash):
            self.migrate()
        moves.STEP = None
        layout._failed = None
        self.assertTrue((outer / "custom/compose.yml").exists())
        self.migrate()
        self.assertFalse(self.exists(f"{layout.BACKUP}/units/addons/local/custom"))   # it is not a personal item's text
        self.assertEqual(sorted(os.listdir(self.root / layout.BACKUP / "units/addons/local")), ["scratch"])
        done = subprocess.run(["sh", str(self.root / ".lab/migration/undo.sh")], cwd="/", capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(sorted(os.listdir(outer)), ["scratch"])   # the person's folder is as it was: nothing was added
        self.assertEqual(self.text("addons/custom/compose.yml"), CUSTOM)

    def test_a_file_called_local_is_a_file_that_is_no_unit_and_does_not_fail_the_move(self):
        self.build(personal=False)
        for folder in ("addons", "setups", "playgrounds"):
            self.put(f"{folder}/local", f"a file called local, in {folder}\n")
        report = self.migrate()
        self.assertEqual(report["problems"], [])
        self.assertEqual(report["left"], ["addons/local", "playgrounds/local", "setups/local"])
        self.assertEqual(self.text("setups/local"), "a file called local, in setups\n")
        self.assertEqual(self.text(f"{HOME}/addons/echo/.env"), "ECHO_TOKEN=secret-echo-env\n")   # the rest went on
        self.assertEqual((self.marker()["from"], layout.status()["phase"]), (1, "ready"))

    def test_a_local_folder_the_person_cannot_read_is_left_and_said_and_does_not_fail_the_move(self):
        self.build()
        os.chmod(self.root / "addons/local", 0o000)
        try:
            if os.access(self.root / "addons/local", os.R_OK):
                self.skipTest("this user reads any folder")
            report = self.migrate()
        finally:
            os.chmod(self.root / "addons/local", 0o755)
        self.assertEqual(report["problems"], ["addons/local could not be read: its items were left as they are"])
        self.assertEqual(report["left"], ["addons/local"])
        self.assertEqual(self.text("addons/local/scratch/.env"), "SCRATCH=secret-scratch\n")
        self.assertEqual(self.text(".lab/catalogs/local/playgrounds/relay/compose.yml")[:1], "x")   # the others moved
        self.assertEqual((self.marker()["from"], layout.status()["phase"]), (1, "ready"))

    def test_an_item_that_stays_where_it_was_is_told_as_not_moved_and_not_as_turned_off(self):
        self.build(on=ON + "  - addons/local/linked/compose.yml\n")
        elsewhere = self.root.parent / f"{self.root.name}-elsewhere"
        self.addCleanup(shutil.rmtree, elsewhere, True)
        elsewhere.mkdir()
        (elsewhere / "compose.yml").write_text(OTHER, encoding="utf-8")
        os.symlink(elsewhere, self.root / "addons/local/linked")
        report = self.migrate()
        self.assertEqual((report["left"], report["turned_off"]), (["addons/local/linked"], ["addon:moved-echo"]))
        self.assertNotIn("linked", self.text(".lab/on.yml"))   # its line went: nothing reads it where it was
        rows = {row["key"]: row["items"] for row in layout.status()["notice"]["rows"]}
        self.assertEqual((rows["turned_off"], rows["not_moved"]), (["moved-echo"], ["addons/local/linked"]))
        self.assertEqual(self.text(f"{layout.BACKUP}/on.yml").count("linked"), 1)   # and the backup keeps it

    def test_a_relative_symlink_is_made_relative_to_its_new_place(self):   # C8
        self.build(personal=False)
        (self.root.parent / (self.root.name + "-rel")).mkdir()
        self.addCleanup(shutil.rmtree, self.root.parent / (self.root.name + "-rel"), True)
        outer = self.root.parent / (self.root.name + "-rel")
        (outer / "scratch").mkdir()
        (outer / "scratch/compose.yml").write_text("services: {}\n", encoding="utf-8")
        os.makedirs(self.root / "addons", exist_ok=True)
        os.symlink(os.path.join("..", "..", outer.name), self.root / "addons/local")   # from addons/: two up is the parent
        self.assertTrue(os.path.isdir(self.root / "addons/local"))
        self.migrate()
        link = self.root / ".lab/catalogs/local/addons"
        self.assertTrue(os.path.islink(link) and os.path.isdir(link))
        self.assertEqual(os.path.realpath(link), os.path.realpath(outer))
        self.assertFalse(os.path.isabs(os.readlink(link)))

    def test_a_folder_refused_for_a_right_is_copied_the_original_stays_and_no_run_retries(self):   # C9
        self.build()
        with self.refusing("playgrounds/shop/.state", PermissionError(errno.EACCES, "Permission denied")):
            report = self.migrate()   # no exception
        self.assertEqual(self.text(f"{HOME}/playgrounds/shop/.state/created.json"), "secret-shop-state\n")
        self.assertEqual(self.text("playgrounds/shop/.state/created.json"), "secret-shop-state\n")   # the original stays
        self.assertEqual(digest(self.root / "playgrounds/shop/.state"), digest(self.root / HOME / "playgrounds/shop/.state"))
        self.assertEqual(report["left"], ["playgrounds/shop/.state"])
        copied = [entry for entry in moves.read_journal() if entry["op"] == "copied"]
        self.assertEqual([entry["src"] for entry in copied], ["playgrounds/shop/.state"])
        before = digest(self.root, skip=SKIP)
        self.assertEqual(layout.migrate(), {key: [] for key in layout._report()})   # the marker is there: no retry
        self.ensure()
        self.assertEqual(digest(self.root, skip=SKIP), before)
        self.assertEqual(len([entry for entry in moves.read_journal() if entry["op"] == "copied"]), 1)

    def test_a_place_that_is_taken_keeps_both_the_old_one_under_kept_and_says_so(self):   # C10
        self.build()
        self.put(f"{HOME}/addons/echo/.env", "ECHO_TOKEN=a-new-one\n", 0o600)
        self.put(f"{HOME}/playgrounds/shop/.state/created.json", "a new record\n")
        report = self.migrate()
        self.assertEqual(self.text(f"{HOME}/addons/echo/.env"), "ECHO_TOKEN=a-new-one\n")   # never overwritten
        self.assertEqual(self.text(".lab/migration/kept/addons/echo/.env"), "ECHO_TOKEN=secret-echo-env\n")
        self.assertEqual(self.text(".lab/migration/kept/playgrounds/shop/.state/cloud/env-1/created.json"), "secret-shop-cloud\n")
        self.assertEqual(self.text(f"{HOME}/playgrounds/shop/.state/created.json"), "a new record\n")
        self.assertIn("addon:echo", report["kept"])
        self.assertEqual(report["problems"], [])
        self.assertEqual(self.mode(".lab/migration/kept/addons/echo/.env"), 0o600)

    def test_a_file_on_another_filesystem_is_copied_checked_and_removed_and_a_folder_stays(self):   # C10
        self.build()
        with self.refusing("addons/echo/.env", OSError(errno.EXDEV, "Invalid cross-device link")):
            with self.refusing("playgrounds/shop/.state", OSError(errno.EXDEV, "Invalid cross-device link")):
                report = self.migrate()
        self.assertEqual(self.text(f"{HOME}/addons/echo/.env"), "ECHO_TOKEN=secret-echo-env\n")
        self.assertFalse(self.exists("addons/echo/.env"))   # copied, checked, removed
        self.assertTrue(self.exists("playgrounds/shop/.state/created.json"))   # a folder is left in place
        self.assertFalse(self.exists(f"{HOME}/playgrounds/shop/.state"))
        self.assertEqual(report["left"], ["playgrounds/shop/.state"])
        self.assertEqual(self.mode(f"{HOME}/addons/echo/.env"), 0o600)

    def test_a_link_inside_a_unit_is_never_followed_and_a_link_to_a_secret_is_moved_as_a_link(self):
        self.build()
        os.symlink("/etc/hosts", self.root / "addons/gateway/.env.link")   # matches .env.*: a machine file, a link
        os.symlink("/etc", self.root / "addons/echo/certs/etc")   # a link to a folder: a stray, moved as it is
        report = self.migrate()
        self.assertEqual(os.readlink(self.root / HOME / "addons/gateway/.env.link"), "/etc/hosts")
        self.assertEqual(os.readlink(self.root / ".lab/migration/kept/addons/echo/certs/etc"), "/etc")
        self.assertTrue(os.path.exists("/etc/hosts") and "addon:echo" in report["kept"])

    def test_names_the_grammar_refuses_are_left_where_they_are_and_said(self):
        self.build()
        self.put("addons/My Addon/compose.yml", OTHER)
        self.put("playgrounds/Upper/.env", "X=secret-upper\n")
        self.put("setups/readme.txt", "x\n")
        self.put("addons/local/Spaces Here/compose.yml", OTHER)
        report = self.migrate()
        self.assertEqual(report["left"], ["addons/My Addon", "addons/local/Spaces Here", "playgrounds/Upper",
                                          "setups/readme.txt"])
        self.assertEqual(self.text("playgrounds/Upper/.env"), "X=secret-upper\n")
        self.assertEqual((layout.status()["phase"], layout._signals(first=False)), ("ready", []))

    def test_a_folder_too_big_to_sort_is_left_and_said(self):
        self.build()
        self.put("addons/echo-core/.env", "CORE=secret-core\n")
        with mock.patch.object(layout, "SCAN_LIMIT", 3):
            report = self.migrate()
        self.assertIn("addons/echo", report["left"])
        self.assertEqual(self.text("addons/echo/.env"), "ECHO_TOKEN=secret-echo-env\n")   # not moved, not lost
        self.assertEqual(layout.status()["phase"], "ready")

    def test_a_secret_file_with_a_capital_in_its_suffix_is_one(self):
        self.build()
        self.put("addons/echo/Server.KEY", "secret-capital-key\n", 0o600)
        self.put("addons/echo/Backup.P12", "secret-capital-p12\n", 0o600)
        self.migrate()
        self.assertEqual(self.text(f"{HOME}/addons/echo/Server.KEY"), "secret-capital-key\n")
        self.assertEqual(self.text(f"{HOME}/addons/echo/Backup.P12"), "secret-capital-p12\n")
        self.assertFalse(self.exists(".lab/migration/kept/addons/echo/Server.KEY"))

    def test_an_example_of_settings_is_not_a_machine_file(self):
        self.build()
        self.put("addons/echo/.env.example", "ECHO_TOKEN=\n")
        self.migrate()
        self.assertEqual(self.text(".lab/migration/kept/addons/echo/.env.example"), "ECHO_TOKEN=\n")
        self.assertFalse(self.exists(f"{HOME}/addons/echo/.env.example"))

    def test_a_folder_the_person_cannot_read_is_a_machine_entry_and_is_never_lost(self):
        self.build()
        self.put("addons/echo/vault/key", "secret-vault\n")
        os.chmod(self.root / "addons/echo/vault", 0o000)
        try:
            report = self.migrate()
        finally:
            for folder in (self.root / "addons/echo/vault", self.root / HOME / "addons/echo/vault"):
                if folder.exists():
                    os.chmod(folder, 0o755)
        # a folder with no right on it may be renamed or, if the system refuses, copied with the original left: the key
        # is in one of the two places, and a left original is said
        places = [path for path in ("addons/echo/vault/key", f"{HOME}/addons/echo/vault/key") if self.exists(path)]
        self.assertTrue(places)
        self.assertEqual({self.text(path) for path in places}, {"secret-vault\n"})
        self.assertNotIn("addon:echo", report["kept"])
        self.assertEqual("addons/echo/vault" in report["left"], self.exists("addons/echo/vault"))


# ---------------------------------------------------------------------------------------------------------------------
# Idempotency, crashes, two processes, going back, secrets, time
# ---------------------------------------------------------------------------------------------------------------------

class Converges(Machine):

    def test_a_second_run_changes_nothing_and_says_nothing(self):
        self.build()
        self.migrate()
        after, lines = digest(self.root, skip=SKIP), len(moves.read_journal())
        self.assertEqual(self.migrate(), {key: [] for key in layout._report()})
        self.ensure()
        self.assertEqual(layout.migrate_personal(), {key: [] for key in layout._report()})
        self.assertEqual(digest(self.root, skip=SKIP), after)
        self.assertEqual(len(moves.read_journal()), lines)

    def undoes(self, original, label):
        """`undo.sh` of a tree where a run stopped, run on a copy of it, puts back every file the run moved and every text it
        wrote. The one thing it can miss is the move in flight when a run stops right after a rename: it is in
        the journal and not yet in the script, which the next run writes again, and nothing of it is lost."""
        script = self.root / layout.MIGRATION / "undo.sh"
        if not script.exists():
            self.assertEqual([entry for entry in moves.read_journal() if entry["op"] == "move"], [], label)
            return
        copy = self.root.parent / f"{self.root.name}-undo"
        self.addCleanup(shutil.rmtree, copy, True)
        shutil.rmtree(copy, True)
        shutil.copytree(self.root, copy, symlinks=True)
        done = subprocess.run(["sh", str(copy / layout.MIGRATION / "undo.sh")], cwd="/", capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, (label, done.stderr))
        moved = [entry for entry in moves.read_journal() if entry["op"] == "move"]
        aside = tuple(moved[-1][key] for key in ("src", "dst")) if label == "relocate:renamed" and moved else ()

        def counted(found):
            return {key: value for key, value in files_of(found).items() if key not in DROPPED
                    and not os.path.basename(key).startswith(f"{paths.LAB_DIR}__")   # a platform copy of the new places
                    and not any(key == one or key.startswith(f"{one}/") for one in aside)}
        after = digest(copy, skip=UNDONE)
        self.assertEqual(counted(after), counted(original), label)
        self.assertEqual([key for key in original if key not in after and key not in DROPPED
                          and not any(key == one or key.startswith(f"{one}/") for one in aside)], [], label)   # every folder

    def test_a_stop_leaves_a_script_that_puts_back_what_had_been_moved(self):
        self.build()
        original = digest(self.root, skip=UNDONE)
        self.keep()
        stops = (("layout:undo-personal", 1), ("relocate:renamed", 3), ("layout:plan", 1), ("layout:text", 1),
                 ("relocate:renamed", 12), ("layout:drop", 1), ("relocate:renamed", 25), ("layout:empty", 1),
                 ("layout:rekey", 1), ("layout:on", 1), ("layout:clear", 1), ("layout:undo", 1), ("layout:marker", 1))
        for label, nth in stops:
            with self.subTest(label=label, nth=nth):
                self.restore()
                moves.STEP = crash_on(label, nth)
                with self.assertRaises(Crash):
                    self.migrate()
                moves.STEP = None
                layout._failed = None
                self.assertTrue(self.exists(".lab/migration/undo.sh"))
                self.undoes(original, label)

    def test_the_script_is_there_as_soon_as_the_first_copy_is(self):
        self.build(personal=False)
        self.patch(moves, "STEP", crash_on("layout:undo-personal"))
        with self.assertRaises(Crash):
            self.migrate()
        moves.STEP = None
        script = self.text(".lab/migration/undo.sh")
        self.assertIn(f"drop {layout.STARTED}", script)   # a person who goes back leaves no witness for the next Lab
        self.assertIn(f"restore {layout.BACKUP}/on.yml .lab/on.yml", script)
        self.assertEqual([entry for entry in moves.read_journal() if entry["op"] == "move"], [])

    def test_a_run_that_stops_at_any_step_is_run_again_to_the_same_end_loses_nothing_and_can_be_undone(self):
        hook = crash_at(0)
        self.patch(moves, "STEP", hook)
        self.build()
        original = digest(self.root, skip=UNDONE)
        self.keep()
        self.migrate()
        clean = digest(self.root, skip=SKIP)
        steps = len(hook.calls)
        self.assertGreater(steps, 90)
        self.assertTrue({"layout:start", "layout:backup", "layout:plan", "layout:text", "layout:on", "layout:undo",
                         "layout:marker", "relocate:journaled", "rekey:starred"} <= set(hook.calls))
        for number in range(1, steps + 1):
            with self.subTest(step=number, label=hook.calls[number - 1]):
                self.restore()
                moves.STEP = crash_at(number)
                with self.assertRaises(Crash):
                    self.migrate()
                moves.STEP = None
                layout._failed = None
                found = set(texts(self.root, skip=(".lab/migration/backup",)))
                self.assertEqual(SECRETS - found, set(), "a secret was lost at the stop")
                if hook.calls[number - 1] not in ("relocate:before", "relocate:journaled"):   # nothing was renamed yet
                    self.undoes(original, hook.calls[number - 1])
                self.migrate()
                self.assertEqual(digest(self.root, skip=SKIP), clean)
                self.assertEqual([path for path in digest(self.root) if path.endswith(".partial")], [])
        moves.STEP = None

    def test_a_run_that_stops_while_the_catalog_is_not_there_and_another_when_it_is_end_where_one_that_did_not_stop_does(self):
        # Part A alone, stopped anywhere, then the catalog arrives and Part B is stopped anywhere
        hook = crash_at(0)
        self.patch(moves, "STEP", hook)
        self.build()
        self.keep()
        shutil.rmtree(self.root / ".lab/catalogs/fixture")
        fixtures.declare(self, "fixture")   # listed and asked for, and not here
        self.keep()
        self.migrate()
        part_a = len(hook.calls)
        clean_a = digest(self.root, skip=SKIP)
        for number in range(1, part_a + 1):
            with self.subTest(part="A", step=number, label=hook.calls[number - 1]):
                self.restore()
                moves.STEP = crash_at(number)
                with self.assertRaises(Crash):
                    self.migrate()
                moves.STEP = None
                layout._failed = None
                self.migrate()
                self.assertEqual(digest(self.root, skip=SKIP), clean_a)
        moves.STEP = None
        self.restore()
        self.migrate()
        fixtures.install(self, "fixture")
        self.put("addons/echo-core/compose.yml", self.installed("addons/echo-core/compose.yml"))
        self.put("addons/echo-core/README.md", self.installed("addons/echo-core/README.md"))
        self.put("addons/echo-twin/compose.yml", self.installed("addons/echo-twin/compose.yml") + "# mine\n")
        self.put("addons/echo-twin/README.md", self.installed("addons/echo-twin/README.md"))
        self.keep()
        hook.calls.clear()
        moves.STEP = hook
        self.migrate()
        moves.STEP = None
        clean_b, part_b = digest(self.root, skip=SKIP), len(hook.calls)
        self.assertGreater(part_b, 60)
        for number in range(1, part_b + 1):
            with self.subTest(part="B", step=number, label=hook.calls[number - 1]):
                self.restore()
                moves.STEP = crash_at(number)
                with self.assertRaises(Crash):
                    self.migrate()
                moves.STEP = None
                layout._failed = None
                self.migrate()
                self.assertEqual(digest(self.root, skip=SKIP), clean_b)
        moves.STEP = None

    def test_a_run_over_places_that_are_taken_stops_anywhere_and_loses_nothing(self):
        self.build(personal=True)
        self.put(f"{HOME}/addons/echo/.env", "ECHO_TOKEN=a-new-one\n", 0o600)
        self.put(".lab/catalogs/local/addons/custom/compose.yml", OTHER)
        self.put(".lab/catalogs/local/addons/scratch/compose.yml", OTHER)
        self.put(".lab/catalogs/local/playgrounds/mine/compose.yml", OTHER)
        self.keep()
        hook = crash_at(0)
        self.patch(moves, "STEP", hook)
        self.migrate()
        steps = len(hook.calls)
        clean = digest(self.root, skip=SKIP)
        found = set(texts(self.root, skip=(".lab/migration/backup",)))
        self.assertEqual(SECRETS - found, set())
        for number in range(1, steps + 1):
            with self.subTest(step=number, label=hook.calls[number - 1]):
                self.restore()
                moves.STEP = crash_at(number)
                with self.assertRaises(Crash):
                    self.migrate()
                moves.STEP = None
                layout._failed = None
                self.assertEqual(SECRETS - set(texts(self.root, skip=(".lab/migration/backup",))), set())
                self.migrate()
                self.assertEqual(digest(self.root, skip=SKIP), clean)
        moves.STEP = None

    def stopped_after_the_plan(self, label="layout:drop"):
        """The tree of `build()` with a run stopped at `label`, which comes after the plan is frozen."""
        self.build()
        self.patch(moves, "STEP", crash_on(label))
        with self.assertRaises(Crash):
            self.migrate()
        moves.STEP = None
        layout._failed = None

    def test_a_copy_that_was_the_catalog_s_at_the_plan_and_is_not_when_it_goes_is_put_aside_not_dropped(self):
        self.stopped_after_the_plan()
        first = next(op["path"] for op in json.loads(self.text(layout.PLAN))["ops"] if op["op"] == "drop")
        edited = self.text(first) + "# edited after the plan\n"
        self.put(first, edited)
        report = self.migrate()
        self.assertEqual(self.text(f"{layout.KEPT}/{first}"), edited)
        self.assertFalse(self.exists(first))
        self.assertIn("addon:echo-core", report["kept"])
        self.assertEqual(SECRETS - set(texts(self.root, skip=(".lab/migration/backup",))), set())

    def test_what_appears_in_a_ghost_folder_after_the_plan_stays_and_is_told(self):
        self.build()
        os.makedirs(self.root / "addons/ghost/deeper")
        os.makedirs(self.root / "playgrounds/shop/data")
        self.patch(moves, "STEP", crash_on("layout:drop"))
        with self.assertRaises(Crash):
            self.migrate()
        moves.STEP = None
        layout._failed = None
        self.put("addons/ghost/deeper/note.txt", "mine\n")
        self.put("playgrounds/shop/data/mine.txt", "mine too\n")
        report = self.migrate()
        self.assertEqual((self.text("addons/ghost/deeper/note.txt"), self.text("playgrounds/shop/data/mine.txt")),
                         ("mine\n", "mine too\n"))
        self.assertEqual(report["left"], ["addons/ghost", "playgrounds/shop"])
        self.assertEqual(self.report()["left"], report["left"])
        rows = {row["key"]: row["items"] for row in layout.status()["notice"]["rows"]}
        self.assertEqual(rows["not_moved"], ["addons/ghost", "playgrounds/shop"])
        self.assertEqual((self.marker()["from"], layout.status()["phase"]), (1, "ready"))

    def test_a_file_whose_place_under_kept_is_taken_stays_and_is_told(self):
        self.build()
        self.put(f"{layout.KEPT}/addons/port-core/notes.txt", "an older note\n")
        report = self.migrate()
        self.assertEqual(self.text("addons/port-core/notes.txt"), "my notes\n")   # not moved over the older one
        self.assertEqual(self.text(f"{layout.KEPT}/addons/port-core/notes.txt"), "an older note\n")
        self.assertEqual(report["left"], ["addons/port-core"])
        self.assertEqual(self.text(f"{HOME}/addons/port-core/.env"), "PORT=secret-port\n")   # the settings went on
        self.assertTrue(any("addons/port-core/notes.txt" in line for line in report["problems"]), report["problems"])

    def test_a_platform_copy_a_stale_lab_wrote_after_the_backup_is_backed_up_before_it_goes(self):
        self.stopped_after_the_plan("layout:on")
        later = GATEWAY_COPY.replace("services: {}", "services: {later: {image: busybox}}")
        self.put(".lab/targets/cloud/addons__gateway__compose.yml", later)
        self.migrate()
        saved = self.root / layout.BACKUP / "targets/cloud"
        self.assertEqual(sorted(path.read_text(encoding="utf-8") for path in saved.iterdir()), sorted([GATEWAY_COPY, later]))
        self.assertFalse(self.exists(".lab/targets/cloud/addons__gateway__compose.yml"))

    def test_the_run_that_stops_leaves_the_layout_waiting_until_the_marker(self):
        self.build()
        self.keep()
        hook = crash_at(0)
        self.patch(moves, "STEP", hook)
        self.migrate()
        steps = hook.calls[:]
        marker = max(index for index, label in enumerate(steps, 1) if label == "layout:marker")
        for number in (5, marker - 1):
            self.restore()
            moves.STEP = crash_at(number)
            with self.assertRaises(Crash):
                self.migrate()
            moves.STEP = None
            layout._failed = None
            self.assertEqual((self.exists(".lab/layout.json"), layout.waiting()), (False, True), number)
        moves.STEP = None

    def test_a_second_process_waits_for_the_lock_then_does_nothing(self):
        self.build()
        entered, release, reports = threading.Event(), threading.Event(), []

        def hook(label):
            if label == "layout:plan" and not entered.is_set():
                entered.set()
                self.assertTrue(release.wait(20))
        self.patch(moves, "STEP", hook)
        first = threading.Thread(target=lambda: reports.append(("first", layout.migrate())))
        first.start()
        self.assertTrue(entered.wait(20))
        second = threading.Thread(target=lambda: reports.append(("second", layout.migrate())))
        second.start()
        second.join(0.5)
        self.assertTrue(second.is_alive(), "the second one runs while the first holds the lock")
        release.set()
        first.join(30)
        second.join(30)
        done = dict(reports)
        self.assertEqual(done["second"], {key: [] for key in layout._report()})
        self.assertEqual(done["first"]["promoted"], ["addon:local/custom", "playground:local/mine", "setup:local/mine-set"])
        self.assertEqual(self.marker()["from"], 1)

    def test_the_undo_script_puts_back_every_file_byte_for_byte(self):
        self.build()
        dropped = DROPPED   # the catalog has them: git brings them back
        before = digest(self.root, skip=UNDONE)
        self.migrate()
        self.assertNotEqual(files_of(digest(self.root, skip=SKIP)), files_of(before))
        done = subprocess.run(["sh", str(self.root / ".lab/migration/undo.sh")], cwd="/", capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        after = digest(self.root, skip=UNDONE)
        wanted = {key: value for key, value in files_of(before).items() if key not in dropped}
        self.assertEqual(files_of(after), wanted)   # contents, modes and links
        self.assertEqual([key for key in before if key not in after and key not in dropped], [])   # and every folder
        self.assertFalse(self.exists("x"))   # the stray called `a b$(touch x)` was not run
        self.assertFalse(self.exists(".lab/layout.json"))
        self.assertFalse(self.exists(layout.PLAN) or self.exists(layout.STARTED))

    def test_the_undo_script_never_overwrites_what_is_there(self):
        self.build()
        self.migrate()
        self.put("addons/echo/.env", "ECHO_TOKEN=new\n", 0o600)   # made since, in the place the file goes back to
        done = subprocess.run(["sh", str(self.root / ".lab/migration/undo.sh")], cwd="/", capture_output=True, text=True)
        self.assertEqual(self.text("addons/echo/.env"), "ECHO_TOKEN=new\n")
        self.assertEqual(self.text(f"{HOME}/addons/echo/.env"), "ECHO_TOKEN=secret-echo-env\n")
        self.assertIn("Left", done.stderr)

    def test_a_machine_that_went_back_and_comes_forward_again_is_moved_again(self):
        self.build()
        self.migrate()
        subprocess.run(["sh", str(self.root / ".lab/migration/undo.sh")], cwd="/", capture_output=True, check=True)
        self.assertEqual(layout._signals(), ["S1", "S2", "S3", "S4"])   # the old layout is back, and the witness is gone
        self.put("addons/echo/.env", "ECHO_TOKEN=changed-since\n", 0o600)
        self.migrate()
        self.assertEqual(self.text(f"{HOME}/addons/echo/.env"), "ECHO_TOKEN=changed-since\n")
        self.assertEqual(self.text(".lab/on.yml"), ON_AFTER)
        self.assertEqual(layout.status()["phase"], "ready")
        aside = [path.name for path in (self.root / layout.MIGRATION).iterdir() if path.name.startswith("backup.")]
        self.assertEqual(len(aside), 1)   # the first one's backup was set aside, not deleted
        subprocess.run(["sh", str(self.root / ".lab/migration/undo.sh")], cwd="/", capture_output=True, check=True)
        self.assertEqual(self.text("addons/echo/.env"), "ECHO_TOKEN=changed-since\n")

    def test_no_secret_is_in_the_migration_folder_the_script_the_output_or_the_log(self):
        self.build()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.migrate()
            self.ensure()
        migration = self.root / ".lab/migration"
        written = [path for path in sorted(migration.rglob("*")) if path.is_file() and not path.is_symlink()]
        self.assertTrue(written)
        for secret in SECRETS:
            needle = secret.strip()
            self.assertNotIn(needle, out.getvalue() + err.getvalue())
            self.assertEqual([str(path) for path in written if needle.encode() in path.read_bytes()], [], needle)
        self.assertEqual(SECRETS - set(texts(self.root, skip=(".lab/migration",))), set(), "every secret is somewhere")
        for folder in (".lab/migration", ".lab/migration/backup", ".lab/migration/kept", ".lab/held", ".lab/held/addons"):
            self.assertEqual(self.mode(folder), 0o700, folder)
        self.assertEqual(sorted(digest(self.root / ".lab/held")),
                         ["addons", "addons/moved-echo", "addons/moved-echo/.env", "addons/moved-echo/secrets",
                          "addons/moved-echo/secrets/token"])   # only what was moved
        self.assertEqual(self.mode(".lab/migration/undo.sh"), 0o700)

    def test_nothing_needs_docker_git_or_the_network(self):
        self.build()
        calls = []
        refuse = AssertionError("this ran a program or opened a connection")
        with mock.patch.object(subprocess, "Popen", side_effect=refuse), mock.patch.object(subprocess, "run", side_effect=refuse), \
                mock.patch.object(os, "system", side_effect=refuse), mock.patch.object(socket.socket, "connect", side_effect=refuse), \
                mock.patch.object(socket, "getaddrinfo", side_effect=refuse), \
                mock.patch.object(compose, "run", side_effect=refuse), mock.patch.object(compose, "COMPOSE", calls):
            layout.status()
            layout.waiting()
            layout.ensure()
            layout.migrate_personal()
            layout.migrate()
            layout.dismiss()
        self.assertEqual(self.marker()["from"], 1)

    def test_the_machine_of_the_spec_does_not_cost_a_move_for_each_file(self):
        self.build()
        legacy = ("addons", "setups", "playgrounds")

        def count():
            return sum(len(files) for folder in legacy for _, _, files in os.walk(self.root / folder))
        for index in range(572 - count()):   # the files of the machine the migration was first tried on
            self.put(f"playgrounds/shop/.state/records/r{index:03d}.json", f"{index}\n")
        self.assertEqual(count(), 572)
        started = time.process_time()
        self.migrate()
        spent = time.process_time() - started
        # the spec says 200 ms: of the process's own time, which a machine under load does not stretch the way it stretches a
        # wall clock; about a tenth of a second where this was written
        self.assertLess(spent, 0.2)
        self.assertLess(len(moves.read_journal()), 150)


# ---------------------------------------------------------------------------------------------------------------------
# The texts and the on file (D1 to D6)
# ---------------------------------------------------------------------------------------------------------------------

ON_CLEAN = """include:
  - playgrounds/shop/compose.yml
  - addons/echo/compose.yml
  - addons/local/scratch/compose.yml
  - addons/moved-echo/compose.yml
  - "addons/custom/compose.yml"   # quoted
  - {path: ".lab/targets/cloud/addons__gateway__compose.yml", project_directory: "addons/gateway"}
"""


class Texts(Machine):

    def loaded(self):
        """The project as the Lab UI reads it from this folder: Compose renders the files, no daemon."""
        fixtures.need_compose()
        for module in (project, envfiles):
            self.patch(module, "ROOT", self.root)
        self.patch(license, "status_of", lambda variable: None)
        project.forget()
        self.addCleanup(project.forget)
        return project.build(project.gather())

    def test_the_four_personal_files_are_written_as_the_golden_texts(self):   # D1
        self.build()
        self.migrate()
        for rel in ("playgrounds/local/trip-demo", "playgrounds/local/relay"):
            kind, _, name = rel.split("/")
            self.assertEqual(self.text(f".lab/catalogs/local/{kind}/{name}/compose.yml"),
                             (DATA / "expected" / rel / "compose.yml").read_text(encoding="utf-8"), rel)
        for name in ("duo", "trio"):
            self.assertEqual(self.text(f".lab/catalogs/local/setups/{name}.yml"),
                             (DATA / "expected/setups/local" / f"{name}.yml").read_text(encoding="utf-8"), name)

    def test_the_golden_lines_are_the_ones_of_the_table_of_the_spec(self):   # D1
        self.build()
        self.migrate()
        trip = self.text(".lab/catalogs/local/playgrounds/trip-demo/compose.yml")
        for line in ("  - ../../setups/duo.yml", "  - \"../../../fixture/addons/echo/compose.yml\"   # shared",
                     "    build: ../../../../../seeder"):
            self.assertIn(line + "\n", trip)
        self.assertIn("  - ../../fixture/addons/echo/compose.yml\n", self.text(".lab/catalogs/local/setups/duo.yml"))
        self.assertIn("  - ../../fixture/addons/echo-twin/compose.yml   # the traces\n",
                      self.text(".lab/catalogs/local/setups/trio.yml"))

    def test_what_leaves_the_folder_is_written_again_and_a_command_with_dots_is_not(self):   # D2
        self.build()
        self.put("playgrounds/mine/compose.yml", MINE + """    env_file: ["../../.env", "./.env"]
    working_dir: /srv/../app
    configs:
      - source: x
        target: ../../in-container
""")
        self.migrate()
        text = self.text(".lab/catalogs/local/playgrounds/mine/compose.yml")
        self.assertIn("    build: ../../../../../seeder\n", text)
        self.assertIn('    command: sh -c "ls ../.."\n', text)   # read in the container: untouched
        self.assertIn("    working_dir: /srv/../app\n", text)
        self.assertIn("        target: ../../in-container\n", text)
        self.assertIn('    volumes: ["./.state:/state", "./seed:/seed:ro"]\n', text)   # in the folder: as written

    def test_the_on_file_is_translated_line_by_line_with_a_target_in_its_long_form(self):   # D3
        self.build()
        self.migrate()
        self.assertEqual(self.text(".lab/on.yml"), ON_AFTER)
        self.assertIn(f'  - {{path: ".lab/targets/cloud/.lab__catalogs__fixture__addons__gateway__compose.yml", '
                      f'project_directory: "{HOME}/addons/gateway"}}\n', self.text(".lab/on.yml"))
        copy = self.text(".lab/targets/cloud/.lab__catalogs__fixture__addons__gateway__compose.yml")
        self.assertEqual(copy, f"{compose.HEADER}{HOME}/addons/gateway/compose.yml (fixture-gateway=gateway-cloud)\n"
                               + compose.retarget(self.installed("addons/gateway/compose.yml"),
                                                  {"fixture-gateway": "gateway-cloud"}))   # made from the install, same names
        self.assertEqual(compose.target_names(".lab/targets/cloud/.lab__catalogs__fixture__addons__gateway__compose.yml"),
                         {"fixture-gateway": "gateway-cloud"})
        self.assertFalse(self.exists(".lab/targets/cloud/addons__gateway__compose.yml"))   # the backup holds it
        self.assertEqual(self.text(f"{layout.BACKUP}/targets/cloud/addons__gateway__compose.yml"), GATEWAY_COPY)
        self.assertEqual(self.text(f"{layout.BACKUP}/on.yml"), ON)
        self.assertEqual(compose.targets(".lab/on.yml"), {f"{HOME}/addons/gateway/compose.yml": "cloud"})

    def test_the_on_file_keeps_its_comments_its_line_ends_and_a_line_of_the_persons(self):   # D3
        self.build(on="# what I run\ninclude:\n  - addons/echo/compose.yml  # echo\n\n  - /mine.yml\n")
        (self.root / ".lab/on.yml").write_bytes(b"# what I run\r\ninclude:\r\n  - addons/echo/compose.yml  # echo\r\n\r\n"
                                                b"  - /mine.yml\r\n")
        self.migrate()
        self.assertEqual((self.root / ".lab/on.yml").read_bytes(),
                         f"# what I run\r\ninclude:\r\n  - {HOME}/addons/echo/compose.yml  # echo\r\n\r\n"
                         "  - /mine.yml\r\n".encode())

    def test_an_on_file_with_nothing_left_is_a_list_that_compose_reads(self):
        self.build(on="include:\n  - addons/moved-echo/compose.yml\n")
        report = self.migrate()
        self.assertEqual((self.text(".lab/on.yml"), report["turned_off"]), ("include:\n", ["addon:moved-echo"]))

    def test_a_unit_of_the_catalog_with_no_folder_left_is_still_mapped(self):   # D4
        self.build()
        shutil.rmtree(self.root / "addons/echo-core")
        shutil.rmtree(self.root / "addons/echo/certs")
        self.put(".lab/on.yml", "include:\n  - addons/echo-core/compose.yml\n  - setups/pair.yml\n")
        self.put("setups/local/duo.yml", "include:\n  - ../../addons/echo-core/compose.yml\n  - ../../setups/pair.yml\n")
        report = self.migrate()
        self.assertEqual(self.text(".lab/on.yml"), f"include:\n  - {HOME}/addons/echo-core/compose.yml\n  - {HOME}/setups/pair.yml\n")
        self.assertIn("  - ../../fixture/addons/echo-core/compose.yml\n  - ../../fixture/setups/pair.yml\n",
                      self.text(".lab/catalogs/local/setups/duo.yml"))
        self.assertEqual(report["turned_off"], [])

    def test_the_project_reads_the_migrated_tree_and_the_on_file_locks_nothing(self):   # D5, H1
        self.build(on=ON_CLEAN)
        self.migrate()
        built = self.loaded()
        state = layers.current(built)
        self.assertEqual((state["locked"], state["problems"], state["missing"]), (None, [], []))
        self.assertEqual((state["playground"], state["explicit"]), ("shop", ["echo", "gateway", "local/custom", "local/scratch"]))
        self.assertEqual(state["targets"], {"addon:gateway": "cloud"})
        self.assertEqual(sorted(built["playgrounds"]), ["local/mine", "local/relay", "local/trip-demo", "shop", "tour"])
        self.assertTrue(all(unit["personal"] for uid, unit in built["playgrounds"].items() if uid.startswith("local/")))
        self.assertEqual(self.read_problems(built), [])

    def read_problems(self, built):
        return [problem for problem in built["problems"]]

    def test_each_personal_and_promoted_item_loads_with_compose(self):   # D6
        fixtures.need_compose()
        self.build()
        self.migrate()
        for rel in ("playgrounds/trip-demo", "playgrounds/relay", "playgrounds/mine", "addons/scratch", "addons/custom",
                    "setups/duo.yml", "setups/trio.yml", "setups/mine-set.yml"):
            with self.subTest(rel=rel):
                name = rel if rel.endswith(".yml") else f"{rel}/compose.yml"
                found = fixtures.render(self.root, f".lab/catalogs/local/{name}")
                self.assertIn("apim-gateway", found["services"])
        mine = fixtures.render(self.root, ".lab/catalogs/local/playgrounds/mine/compose.yml")["services"]
        self.assertEqual({binding["source"] for binding in mine["mine-seed"]["volumes"]},
                         {str(self.root / ".lab/catalogs/local/playgrounds/mine/.state"),
                          str(self.root / ".lab/catalogs/local/playgrounds/mine/seed")})
        self.assertEqual(mine["mine-seed"]["build"]["context"], str(self.root / "seeder"))
        self.assertIn("custom", mine)   # the promoted add-on it includes


# ---------------------------------------------------------------------------------------------------------------------
# What Compose does with a folder that moved (F1, F2)
# ---------------------------------------------------------------------------------------------------------------------

LONGRUN = """services:
  longrun:
    image: busybox
    restart: unless-stopped
    volumes: ["./data:/data"]
  plain:
    image: busybox
    restart: unless-stopped
"""
SEEDING = """services:
  pg-seed:
    image: registry.example.invalid/lab/seeder
    build: ../../seeder
    restart: "no"
    labels: {io.gravitee.role: seed}
    volumes: ["./seed:/seed:ro", "./.state:/state"]
"""


class Hashes(Machine):
    """Compose labels a container with the hash of its configuration, and the Lab UI says an item is behind when the hash
    it would get now is another. Moving a folder changes the hash of a service with a bind inside it, and of nothing else."""

    def setUp(self):
        super().setUp()
        fixtures.need_compose()

    def hashes(self, *files):
        include = self.root.parent / f"{self.root.name}-hash.yml"
        include.write_text("include:\n" + "".join(f"  - {json.dumps(str(self.root / file))}\n" for file in files),
                           encoding="utf-8")
        self.addCleanup(include.unlink, missing_ok=True)
        return compose.config_hashes(["-f", "docker-compose.yml", "-f", str(include)])

    def moved(self, **how):
        self.build(on="include:\n  - addons/longrun/compose.yml\n  - playgrounds/seeding/compose.yml\n", **how)
        self.put("addons/longrun/compose.yml", LONGRUN)
        self.put("playgrounds/seeding/compose.yml", SEEDING)
        self.put("playgrounds/seeding/seed/apim/apis/one.json", "{}\n")
        old = self.hashes("addons/longrun/compose.yml", "playgrounds/seeding/compose.yml")
        self.migrate()
        new = self.hashes(".lab/catalogs/local/addons/longrun/compose.yml",
                          ".lab/catalogs/local/playgrounds/seeding/compose.yml")
        return old, new

    def test_only_a_service_with_a_bind_inside_the_moved_folder_changes_its_hash(self):   # F1
        old, new = self.moved()
        self.assertTrue(old and new)
        self.assertEqual(sorted(name for name in new if new[name] != old.get(name)), ["longrun", "pg-seed"])
        self.assertEqual(new["plain"], old["plain"])   # in a moved file, with nothing of the folder in it
        self.assertEqual(new["apim-gateway"], old["apim-gateway"])
        self.assertEqual(self.text(".lab/on.yml"), "include:\n  - .lab/catalogs/local/addons/longrun/compose.yml\n"
                                                   "  - .lab/catalogs/local/playgrounds/seeding/compose.yml\n")

    def test_only_the_services_that_run_for_ever_are_behind_afterwards(self):   # F2
        from lab_ui.library import model
        old, new = self.moved()
        built = self.loaded_project()
        containers = {name: {"state": "running", "config_hash": old.get(name, new[name]), "image_id": None}
                      for name in new}
        late = model.lateness(built, containers, {"hashes": new, "images": {}})
        self.assertEqual(late, {"longrun": "config"})
        self.assertNotIn("pg-seed", late)   # a one-shot runs once: left out by design
        self.assertNotIn("plain", late)

    def test_the_row_restart_needed_names_a_promoted_item_that_runs_behind(self):
        from lab_ui.library import model
        old, new = self.moved()
        built = self.loaded_project()
        containers = {name: {"state": "running", "config_hash": old.get(name, new[name]), "image_id": None} for name in new}
        late = model.lateness(built, containers, {"hashes": new, "images": {}})
        self.assertEqual(late, {"longrun": "config"})
        owners = {f"{built['services'][name]['owner']['kind']}:{built['services'][name]['owner']['id']}" for name in late}
        self.assertEqual(owners, {"addon:local/longrun"})   # promoted: the catalog never had it
        self.assertEqual(layout.restart_row(layout.status()["notice"], owners),
                         {"key": "restart", "label": "Restart needed", "items": ["longrun"], "hint": "Their folders moved"})

    def loaded_project(self):
        for module in (project, envfiles):
            self.patch(module, "ROOT", self.root)
        self.patch(license, "status_of", lambda variable: None)
        project.forget()
        self.addCleanup(project.forget)
        return project.build(project.gather())


# ---------------------------------------------------------------------------------------------------------------------
# The notice, a move that fails, and what the person does with it
# ---------------------------------------------------------------------------------------------------------------------

REPORT_FULL = {"moved": ["addon:echo", "playground:shop"], "personal": ["addon:local/scratch"],
               "promoted": ["playground:local/mine", "addon:local/custom"], "kept": ["addon:echo-twin", "path:addons/README.md"],
               "left": ["playgrounds/shop/.state"], "held": ["addon:moved-echo"], "turned_off": ["addon:moved-echo"],
               "problems": []}


class Notice(Machine):

    def written(self, report, **marker):
        self.put(layout.REPORT, json.dumps({"schema": 1, "report": report}))
        self.put(".lab/layout.json", json.dumps({"layout": 2, "from": 1, "migrated": STAMP, **marker}))
        return layout.status()

    def test_each_row_has_its_words_and_only_a_row_with_something_in_it_is_drawn(self):
        found = self.written(REPORT_FULL)
        self.assertEqual(found["phase"], "ready")
        notice = found["notice"]
        self.assertEqual((notice["title"], notice["button"], notice["text"], notice["id"]), ("Lab updated", "Close", "", STAMP))
        self.assertEqual(notice["rows"], [
            {"key": "personal", "label": "Now personal", "items": ["custom", "mine"], "hint": "Not in a catalog"},
            {"key": "kept", "label": "Kept aside", "items": ["addons/README.md", "echo-twin"],
             "hint": "Your changes, in .lab/migration/kept"},
            {"key": "turned_off", "label": "Turned off", "items": ["moved-echo"], "hint": "Needs a catalog"},
            {"key": "not_moved", "label": "Not moved", "items": ["playgrounds/shop/.state"],
             "hint": "Files stay where they were"}])
        self.assertEqual(notice["moved"], [{"kind": "addon", "id": "echo"}, {"kind": "playground", "id": "shop"}])
        for row, key in (("personal", "promoted"), ("kept", "kept"), ("turned_off", "turned_off"), ("not_moved", "left")):
            lone = {name: [] for name in REPORT_FULL}
            lone[key] = REPORT_FULL[key]
            rows = self.written(lone)["notice"]["rows"]
            self.assertEqual([one["key"] for one in rows], [row])   # a row with something in it, and no other

    def test_nothing_is_said_when_nothing_is_to_say(self):
        empty = {name: [] for name in REPORT_FULL}
        self.assertEqual(self.written(empty), {"phase": "ready", "notice": None})
        self.assertEqual(self.written({**empty, "held": ["addon:x"], "problems": ["p"]}), {"phase": "ready", "notice": None})
        only_moved = self.written({**empty, "moved": ["addon:echo"]})["notice"]
        self.assertEqual((only_moved["rows"], only_moved["moved"]), ([], [{"kind": "addon", "id": "echo"}]))

    def test_the_words_follow_the_rules_of_the_interface(self):
        found = self.written(REPORT_FULL)["notice"]
        strings = [found["title"], found["button"], *(row["label"] for row in found["rows"]),
                   *(row["hint"] for row in found["rows"]), layout.FAILED_LINE, layout.status()["notice"]["title"],
                   *(label for label, _ in layout.ROWS.values()), *(hint for _, hint in layout.ROWS.values())]
        for text in strings:
            self.assertEqual(text, text.strip())
            self.assertNotIn("·", text)   # no middle dot
            self.assertNotIn("•", text)
            self.assertFalse(text.endswith("..."), text)
            self.assertFalse(text.endswith("…"), text)
            self.assertTrue(text[0].isupper(), text)
            self.assertNotRegex(text[1:], r"(?<![.\w/])[A-Z][a-z]+ [A-Z][a-z]+", text)   # sentence case, not Title Case
        for label, hint in layout.ROWS.values():
            self.assertFalse(label.endswith(".") or hint.endswith("."), (label, hint))
        # a path is shown only where it is the fix: the hint of Kept aside, and nothing else
        self.assertEqual([hint for _, hint in layout.ROWS.values() if ".lab" in hint], ["Your changes, in .lab/migration/kept"])
        for key in ("personal", "turned_off", "not_moved"):
            self.assertNotIn(".lab", layout.ROWS[key][1])
        self.assertNotRegex(json.dumps(found), r"@[a-z-]+/|/Users/|/private/")

    def test_what_the_person_reads_of_an_item_is_its_name_never_its_place(self):
        found = self.written({**REPORT_FULL, "promoted": ["setup:local/mine-set"], "kept": ["playground:local/deep",
                                                                                             "setup:pair"],
                              "turned_off": ["path:addons/odd/other.yml"]})["notice"]["rows"]
        self.assertEqual([row["items"] for row in found[:3]], [["mine-set"], ["deep", "pair"], ["addons/odd/other.yml"]])

    def test_the_row_restart_needed_is_the_moved_items_that_run_and_are_behind(self):
        notice = self.written(REPORT_FULL)["notice"]
        row = layout.restart_row(notice, {"addon:echo", "addon:other", "playground:nothing"})
        self.assertEqual(row, {"key": "restart", "label": "Restart needed", "items": ["echo"], "hint": "Their folders moved"})
        self.assertIsNone(layout.restart_row(notice, set()))
        self.assertIsNone(layout.restart_row(None, {"addon:echo"}))
        self.assertEqual(layout.restart_row(notice, ["addon:echo", "playground:shop"])["items"], ["echo", "shop"])

    def test_the_row_restart_needed_names_the_personal_the_promoted_and_the_catalog_items_too(self):
        self.build()
        self.ensure()
        notice = layout.status()["notice"]
        # none of these had a machine file moved into the install, and each one's compose file is in another folder now
        late = {"addon:local/scratch", "addon:local/custom", "playground:local/mine", "addon:future", "setup:pair",
                "addon:not-an-item"}
        self.assertEqual(layout.restart_row(notice, late),
                         {"key": "restart", "label": "Restart needed", "items": ["custom", "future", "mine", "pair", "scratch"],
                          "hint": "Their folders moved"})
        # and nothing that is of another catalog, or that the migration did not move
        self.assertIsNone(layout.restart_row(notice, {"addon:@elsewhere/x", "addon:other"}))

    def test_a_closed_notice_is_not_shown_again_and_closing_it_twice_changes_nothing(self):
        self.written(REPORT_FULL)
        layout.dismiss()
        self.assertEqual(layout.status(), {"phase": "ready", "notice": None})
        self.assertEqual(self.marker()["closed"], STAMP)
        self.assertEqual(self.marker()["from"], 1)
        before = digest(self.root, skip=SKIP)
        layout.dismiss()
        self.assertEqual(digest(self.root, skip=SKIP), before)

    def test_closing_with_no_marker_does_nothing(self):
        layout.dismiss()
        self.assertFalse(self.exists(".lab/layout.json"))

    def test_the_notice_of_a_real_move_says_what_the_move_did(self):
        self.build()
        self.ensure()
        notice = layout.status()["notice"]
        self.assertEqual([(row["key"], row["items"]) for row in notice["rows"]], [
            ("personal", ["custom", "mine", "mine-set"]),
            ("kept", ["echo-twin", "port-core", "shop"]),
            ("turned_off", ["moved-echo"])])
        self.assertEqual([(item["kind"], item["id"]) for item in notice["moved"]], [
            ("addon", "cluster"), ("addon", "echo"), ("addon", "echo-core"), ("addon", "echo-twin"), ("addon", "future"),
            ("addon", "gateway"), ("addon", "local/custom"), ("addon", "local/other"), ("addon", "local/scratch"),
            ("addon", "port-core"), ("playground", "local/mine"), ("playground", "local/relay"),
            ("playground", "local/trip-demo"), ("playground", "shop"), ("playground", "tour"), ("setup", "local/duo"),
            ("setup", "local/mine-set"), ("setup", "local/trio"), ("setup", "pair")])

    def test_a_move_with_nothing_to_report_says_nothing_at_all(self):
        self.put("addons/echo/.env", "ECHO_TOKEN=secret-echo-env\n", 0o600)
        self.put("playgrounds/shop/.env", "X=secret-x\n", 0o600)
        self.ensure()
        notice = layout.status()["notice"]
        self.assertEqual(notice["rows"], [])   # the page adds the row of the items that run
        os.remove(self.root / HOME / "addons/echo/.env")
        os.remove(self.root / HOME / "playgrounds/shop/.env")
        self.written({name: [] for name in REPORT_FULL})
        self.assertIsNone(layout.status()["notice"])


class Failing(Machine):

    def test_a_move_that_fails_is_said_nothing_is_deleted_and_the_next_try_finishes_it(self):
        self.build()
        trip = digest(self.root / "playgrounds/local/trip-demo")
        real = os.rename

        def rename(src, dst, *more, **how):
            if str(src).endswith("playgrounds/local/trip-demo"):
                raise OSError(errno.EIO, "Input/output error")
            return real(src, dst, *more, **how)
        said = io.StringIO()
        with mock.patch.object(moves.os, "rename", rename), contextlib.redirect_stderr(said):
            report = self.migrate()
        self.assertEqual(report["problems"], [layout.FAILED_LINE])
        self.assertEqual(self.text("playgrounds/local/trip-demo/.env"), "TRIP=secret-trip\n")
        self.assertEqual(digest(self.root / "playgrounds/local/trip-demo"), trip)   # nothing of it was touched
        status = layout.status()
        self.assertEqual((status["phase"], layout.waiting(), status["notice"]["text"], status["notice"]["button"]),
                         ("failed", True, "Could not move your settings. Nothing was deleted.", "Try again"))
        self.assertEqual(status["notice"]["rows"], [])
        self.assertIn("Input/output error", said.getvalue())
        self.assertFalse(self.exists(".lab/layout.json"))
        self.assertEqual(self.migrate()["problems"], [])   # Try again
        self.assertEqual((layout.status()["phase"], layout.waiting()), ("ready", False))
        self.assertEqual(self.text(".lab/on.yml"), ON_AFTER)

    def test_a_move_that_fails_leaves_the_script_for_a_person_who_goes_back_before_the_next_try(self):
        self.build()
        real = os.rename
        calls = []

        def rename(src, dst, *more, **how):
            if str(src).endswith(".state") and "playgrounds/shop" in str(src):
                raise OSError(errno.EIO, "Input/output error")
            calls.append(str(src))
            return real(src, dst, *more, **how)
        with mock.patch.object(moves.os, "rename", rename), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.migrate()["problems"], [layout.FAILED_LINE])
        script = self.text(".lab/migration/undo.sh")
        self.assertIn("addons/echo/.env", script)   # what was moved before the failure goes back
        done = subprocess.run(["sh", str(self.root / ".lab/migration/undo.sh")], cwd="/", capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(self.text("addons/echo/.env"), "ECHO_TOKEN=secret-echo-env\n")
        self.assertEqual(self.text("addons/local/scratch/.env"), "SCRATCH=secret-scratch\n")
        self.assertEqual(self.text(".lab/on.yml"), ON)

    def test_a_move_that_fails_after_the_new_copies_were_made_leaves_a_script_that_takes_them_away(self):
        self.build()
        real = layout._overwrite

        def overwrite(file, text):
            if str(file).endswith(".lab/on.yml"):
                raise OSError(errno.EIO, "Input/output error")
            return real(file, text)
        with mock.patch.object(layout, "_overwrite", overwrite), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.migrate()["problems"], [layout.FAILED_LINE])
        new_copy = ".lab/targets/cloud/.lab__catalogs__fixture__addons__gateway__compose.yml"
        self.assertTrue(self.exists(new_copy))   # written before the on file that names it
        self.assertIn(f"drop {new_copy}", self.text(".lab/migration/undo.sh"))
        done = subprocess.run(["sh", str(self.root / ".lab/migration/undo.sh")], cwd="/", capture_output=True, text=True)
        self.assertEqual((done.returncode, self.exists(new_copy), self.text(".lab/on.yml")), (0, False, ON))
        self.assertEqual(self.text(".lab/targets/cloud/addons__gateway__compose.yml"), GATEWAY_COPY)

    def test_a_failure_before_any_migration_started_writes_no_script(self):
        self.build()
        with mock.patch.object(registry, "flock", side_effect=registry.CatalogError("busy", "Another change is running.")):
            with contextlib.redirect_stderr(io.StringIO()):
                self.ensure()
        self.assertFalse(self.exists(".lab/migration"))

    def test_a_move_that_fails_is_printed_once_at_the_start_with_its_reason(self):
        self.build()
        real = os.rename

        def rename(src, dst, *more, **how):
            if str(src).endswith("playgrounds/local/trip-demo"):
                raise OSError(errno.EIO, "Input/output error")
            return real(src, dst, *more, **how)
        said = io.StringIO()
        with mock.patch.object(moves.os, "rename", rename), contextlib.redirect_stderr(said):
            self.ensure()
        self.assertEqual(said.getvalue().count("Nothing was deleted"), 1)
        self.assertIn("Input/output error", said.getvalue())

    def test_starting_the_server_comes_first_a_failure_is_recorded_and_never_raised(self):
        self.build()
        said = io.StringIO()
        with mock.patch.object(layout, "_run", side_effect=RuntimeError("a bug")), contextlib.redirect_stderr(said):
            self.assertIsNone(layout.ensure())
        self.assertIn("RuntimeError", said.getvalue())
        self.assertNotIn("a bug", said.getvalue().replace("RuntimeError", ""))   # the type, not the content
        with mock.patch.object(registry, "flock", side_effect=registry.CatalogError("busy", "Another change is running.")):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertIsNone(layout.ensure())
            self.assertEqual(layout.status()["phase"], "failed")

    def test_waiting_never_raises(self):
        with mock.patch.object(layout, "_phase", side_effect=OSError("denied")):
            self.assertIs(layout.waiting(), False)


# ---------------------------------------------------------------------------------------------------------------------
# An item that left for a catalog that is not installed (H1, H2, H3)
# ---------------------------------------------------------------------------------------------------------------------

HELD_UNIT = "x-gravitee: {kind: addon, name: Moved echo, category: Other, description: Echo that moved}\nservices: {}\n"


class Held(Machine):

    def held(self):
        """The migrated machine: `moved-echo` is in no installed catalog, a personal setup includes it, it is starred, and a
        playground that is in none has a tested set."""
        self.build(on=ON_CLEAN + "  - playgrounds/gone-pg/compose.yml\n")
        self.put("playgrounds/gone-pg/.state/created.json", "secret-gone-state\n")
        self.put("setups/local/duo.yml", "include:\n  - ../../addons/moved-echo/compose.yml\n  - ../../addons/echo/compose.yml\n")
        self.put(".lab/starred.json", json.dumps({"addon": ["moved-echo", "echo"], "playground": ["gone-pg"]}))
        self.put(".lab/tested.json", json.dumps({"gone-pg": sets("1", "2")}, sort_keys=True))
        return self.migrate()

    def test_its_settings_wait_its_line_goes_and_its_stars_and_tested_sets_stay(self):   # H1
        report = self.held()
        self.assertEqual(report["held"], ["addon:moved-echo", "playground:gone-pg"])
        self.assertEqual(report["turned_off"], ["addon:moved-echo", "playground:gone-pg"])
        self.assertEqual(self.text(".lab/held/addons/moved-echo/.env"), "MOVED=secret-held\n")
        self.assertEqual(self.text(".lab/held/playgrounds/gone-pg/.state/created.json"), "secret-gone-state\n")
        self.assertEqual([self.mode(folder) for folder in (".lab/held", ".lab/held/playgrounds", ".lab/held/playgrounds/gone-pg")],
                         [0o700] * 3)   # the folders the Lab made; a moved entry keeps its own mode
        self.assertNotIn("moved-echo", self.text(".lab/on.yml"))
        self.assertNotIn("gone-pg", self.text(".lab/on.yml"))
        self.assertEqual(self.text(f"{layout.BACKUP}/on.yml"), ON_CLEAN + "  - playgrounds/gone-pg/compose.yml\n")   # the backup keeps the line
        self.assertEqual(json.loads(self.text(".lab/starred.json")), {"addon": ["moved-echo", "echo"], "playground": ["gone-pg"]})   # untouched
        self.assertEqual(sorted(json.loads(self.text(".lab/tested.json"))["gone-pg"][0]), ["at", "images", "versions"])
        self.assertEqual(len(json.loads(self.text(".lab/tested.json"))["gone-pg"]), 2)
        self.assertEqual(self.text(".lab/catalogs/local/setups/duo.yml"),
                         "include:\n  - ../../fixture/addons/moved-echo/compose.yml\n  - ../../fixture/addons/echo/compose.yml\n")

    def test_the_project_is_not_locked_by_the_line_that_went(self):   # H1
        fixtures.need_compose()
        self.held()
        for module in (project, envfiles):
            self.patch(module, "ROOT", self.root)
        self.patch(license, "status_of", lambda variable: None)
        project.forget()
        self.addCleanup(project.forget)
        state = layers.current(project.build(project.gather()))
        self.assertEqual((state["locked"], state["problems"]), (None, []))

    def test_a_catalog_that_takes_the_item_over_adopts_the_settings_and_the_keys(self):   # H2
        self.held()
        fixtures.install(self, "partner", files={"addons/moved-echo/compose.yml": HELD_UNIT})
        done = moves.adopt_held("partner", "addon", "moved-echo", None)
        self.assertEqual([(one["path"], one["result"]) for one in done], [(".env", "moved"), ("secrets", "moved")])
        self.assertEqual(self.text(".lab/catalogs/partner/addons/moved-echo/.env"), "MOVED=secret-held\n")
        self.assertEqual(self.text(".lab/catalogs/partner/addons/moved-echo/secrets/token"), "secret-held-token\n")
        self.assertFalse(self.exists(".lab/held/addons/moved-echo"))
        self.assertEqual(json.loads(self.text(".lab/starred.json"))["addon"], ["@partner/moved-echo", "echo"])
        self.assertIn("../../partner/addons/moved-echo/compose.yml", self.text(".lab/catalogs/local/setups/duo.yml"))   # repointed
        self.assertIn("../../fixture/addons/echo/compose.yml", self.text(".lab/catalogs/local/setups/duo.yml"))   # not this one
        again = digest(self.root, skip=SKIP)
        self.assertEqual(moves.adopt_held("partner", "addon", "moved-echo"), [])
        self.assertEqual(digest(self.root, skip=SKIP), again)   # idempotent

    def test_a_catalog_that_does_not_have_the_item_adopts_nothing(self):   # H2
        self.held()
        fixtures.install(self, "partner")
        before = digest(self.root, skip=SKIP)
        self.assertEqual(moves.adopt_held("partner", "addon", "moved-echo"), [])
        self.assertEqual(digest(self.root, skip=SKIP), before)

    def test_an_item_that_comes_back_to_the_default_catalog_gets_its_settings_with_no_declaration(self):   # H3
        self.held()
        fixtures.install(self, "fixture", files={"addons/moved-echo/compose.yml": HELD_UNIT})
        done = moves.adopt_held("fixture", "addon", "moved-echo")
        self.assertEqual([one["path"] for one in done], [".env", "secrets"])
        self.assertEqual(self.text(f"{HOME}/addons/moved-echo/.env"), "MOVED=secret-held\n")
        self.assertEqual(self.text(f"{HOME}/addons/moved-echo/secrets/token"), "secret-held-token\n")
        self.assertEqual(json.loads(self.text(".lab/starred.json"))["addon"], ["moved-echo", "echo"])   # a bare id stays bare
        self.assertEqual(self.text(".lab/catalogs/local/setups/duo.yml"),
                         "include:\n  - ../../fixture/addons/moved-echo/compose.yml\n  - ../../fixture/addons/echo/compose.yml\n")


# ---------------------------------------------------------------------------------------------------------------------
# A real git checkout
# ---------------------------------------------------------------------------------------------------------------------

OLD_IGNORE = """# Secrets and credentials
.env
.env.*
!.env.example
/addons/*/secrets/
*.key
*.pem
*.crt
/.lab/
/addons/local/
/setups/local/
/playgrounds/local/
/playgrounds/*/.state/
"""
NEW_IGNORE = """/.lab/
.env
.env.*
!.env.example
/addons/*/secrets/
/addons/local/
/setups/local/
/playgrounds/local/
/playgrounds/*/.state/
"""


@unittest.skipUnless(gitfixture.available(), "git is not installed")
class Pulled(Machine):
    """A repository with tracked units and ignore rules, a pull of the commit that removes them, and what is left."""

    def git(self, folder, *args):
        return gitfixture.git("-C", str(folder), *args)

    def test_what_a_pull_leaves_is_what_the_migration_expects_and_moves(self):
        base = self.root.parent / (self.root.name + "-git")
        self.addCleanup(shutil.rmtree, base, True)
        base.mkdir()
        upstream, bare = base / "upstream", base / "bare.git"
        upstream.mkdir()
        self.git(upstream, "init", "-q", "-b", "main")
        for rel, text in {".gitignore": OLD_IGNORE, "addons/echo/compose.yml": "services: {}\n",
                          "addons/echo/README.md": "Echo\n", "addons/gateway/compose.yml": "services: {}\n",
                          "playgrounds/shop/compose.yml": "services: {}\n", "playgrounds/shop/guide.md": "# Shop\n",
                          "playgrounds/shop/seed/apim/apis/one.json": "{}\n", "setups/pair.yml": "include: []\n",
                          "addons/README.md": "Add-ons\n"}.items():
            (upstream / rel).parent.mkdir(parents=True, exist_ok=True)
            (upstream / rel).write_text(text, encoding="utf-8")
        self.git(upstream, "add", "-A")
        self.git(upstream, "commit", "-qm", "the old layout")
        self.git(base, "clone", "-q", "--bare", str(upstream), str(bare))
        work = base / "work"
        self.git(base, "clone", "-q", str(bare), str(work))
        # the clone becomes the project folder: the engine's files and the catalog are installed beside the checkout
        self.root = work.resolve()
        for module in (paths, compose):
            self.patch(module, "ROOT", self.root)
        fixtures.core(self)
        fixtures.install(self, "fixture")
        fixtures.default(self, "fixture")
        for rel, (text, mode) in {"addons/echo/.env": ("ECHO=secret-git-env\n", 0o600),
                                  "addons/echo/secrets/token": ("secret-git-token\n", 0o600),
                                  "addons/echo/certs/server.key": ("secret-git-key\n", 0o600),
                                  "playgrounds/shop/.state/created.json": ("secret-git-state\n", 0o644),
                                  "playgrounds/shop/.env": ("SHOP=secret-git-shop\n", 0o600),
                                  "addons/local/mine/compose.yml": (SCRATCH, 0o644),
                                  "addons/local/mine/.env": ("MINE=secret-git-mine\n", 0o600)}.items():
            self.put(rel, text, mode)
        self.put(".lab/on.yml", "include:\n  - addons/echo/compose.yml\n  - addons/local/mine/compose.yml\n")
        moves._INDEXES.clear()
        # while git tracks the units the paths are the layout, and nothing is touched
        before = digest(self.root, skip=SKIP)
        status = self.git(work, "status", "--porcelain")
        with contextlib.redirect_stderr(io.StringIO()):
            self.ensure()
        self.assertEqual((digest(self.root, skip=SKIP), self.git(work, "status", "--porcelain")), (before, status))
        self.assertEqual(layout.status(), {"phase": "ready", "notice": None})
        # the split commit: the units go, five legacy lines of the ignore file stay
        self.git(upstream, "rm", "-rq", "addons", "setups", "playgrounds")
        (upstream / ".gitignore").write_text(NEW_IGNORE, encoding="utf-8")
        self.git(upstream, "add", "-A")
        self.git(upstream, "commit", "-qm", "the split")
        self.git(upstream, "push", "-q", str(bare), "main")
        self.git(work, "pull", "-q", "--ff-only")
        moves._INDEXES.clear()
        left = sorted(path for path in digest(self.root) if path.startswith(("addons", "playgrounds", "setups")))
        self.assertEqual(left, sorted([
            "addons", "addons/echo", "addons/echo/.env", "addons/echo/secrets", "addons/echo/secrets/token",
            "addons/echo/certs", "addons/echo/certs/server.key", "addons/local", "addons/local/mine",
            "addons/local/mine/.env", "addons/local/mine/compose.yml", "playgrounds", "playgrounds/shop",
            "playgrounds/shop/.env", "playgrounds/shop/.state", "playgrounds/shop/.state/created.json"]))
        self.assertEqual((layout.status()["phase"], layout._signals()), ("waiting", ["S1", "S2", "S3"]))
        report = self.migrate()   # offline: it reads the checkout's index, it does not run git
        self.assertEqual(self.text(f"{HOME}/addons/echo/.env"), "ECHO=secret-git-env\n")
        self.assertEqual(self.text(f"{HOME}/addons/echo/certs/server.key"), "secret-git-key\n")
        self.assertEqual(self.text(f"{HOME}/playgrounds/shop/.state/created.json"), "secret-git-state\n")
        self.assertEqual(self.text(".lab/catalogs/local/addons/mine/.env"), "MINE=secret-git-mine\n")
        for gone in ("addons", "playgrounds", "setups"):
            self.assertFalse(self.exists(gone), gone)
        self.assertEqual(self.text(".lab/on.yml"), f"include:\n  - {HOME}/addons/echo/compose.yml\n"
                                                   "  - .lab/catalogs/local/addons/mine/compose.yml\n")
        self.assertEqual(report["personal"], ["addon:local/mine"])
        self.assertEqual((report["kept"], report["left"], report["problems"]), ([], [], []))
        self.assertEqual(self.git(work, "status", "--porcelain").count("\n"), len(self.git(work, "status", "--porcelain").splitlines()))
        self.assertNotIn("addons", self.git(work, "status", "--porcelain"))   # git sees none of it: the files are ignored or gone
        self.assertEqual(layout.status()["phase"], "ready")


# ---------------------------------------------------------------------------------------------------------------------
# Odd trees
# ---------------------------------------------------------------------------------------------------------------------

class Odd(Machine):

    def test_a_clone_with_no_lab_folder_gets_one_with_its_marker(self):
        shutil.rmtree(self.root / ".lab")
        self.ensure()
        self.assertEqual(self.marker(), {"layout": 2, "from": 0})

    def test_a_name_that_is_not_utf8_is_kept_aside_and_the_run_goes_on(self):
        self.build()
        bad = "bad-" + os.fsdecode(b"\xff")
        try:
            with open(os.path.join(os.fsencode(self.root), b"addons/port-core/" + os.fsencode(bad)), "wb") as f:
                f.write(b"x\n")
        except OSError:
            self.skipTest("this filesystem refuses a name that is not UTF-8")
        report = self.migrate()
        self.assertTrue(os.path.exists(os.path.join(os.fsencode(self.root), b".lab/migration/kept/addons/port-core/",
                                                    os.fsencode(bad))))
        self.assertEqual(self.marker()["from"], 1)
        self.assertIn("addon:port-core", report["kept"])
        json.loads(self.text(layout.PLAN))   # the plan reads back, whatever the names
        self.assertEqual(subprocess.run(["sh", "-n", str(self.root / ".lab/migration/undo.sh")]).returncode, 0)

    def test_a_compose_file_that_is_a_link_or_not_text_is_left_as_it_is_and_said(self):
        self.build()
        (self.root / "playgrounds/local/relay/real.yml").write_text(
            (DATA / "legacy/playgrounds/local/relay/compose.yml").read_text(encoding="utf-8"), encoding="utf-8")
        os.remove(self.root / "playgrounds/local/relay/compose.yml")
        os.symlink("real.yml", self.root / "playgrounds/local/relay/compose.yml")
        (self.root / "addons/local/other/compose.yml").write_bytes(b"services: {}\n# \xff\xfe not text\n")
        report = self.migrate()
        self.assertEqual(os.readlink(self.root / ".lab/catalogs/local/playgrounds/relay/compose.yml"), "real.yml")
        self.assertIn("build: ../../../seeder", self.text(".lab/catalogs/local/playgrounds/relay/real.yml"))   # untouched
        self.assertEqual(report["problems"], [
            ".lab/catalogs/local/addons/other/compose.yml is not UTF-8 text: its paths were not written again",
            ".lab/catalogs/local/playgrounds/relay/compose.yml is a link: its paths were not written again"])

    def test_an_on_file_that_is_not_text_is_left_as_it_is(self):
        self.build()
        (self.root / ".lab/on.yml").write_bytes(b"include:\n  - addons/echo/compose.yml\n  - \xff\n")
        report = self.migrate()
        self.assertEqual((self.root / ".lab/on.yml").read_bytes(), b"include:\n  - addons/echo/compose.yml\n  - \xff\n")
        self.assertIn("The file that says what is on is not UTF-8 text: it was left as it is", report["problems"])

    def test_compose_file_in_the_dot_env_that_names_a_moved_file_is_said_and_not_touched(self):
        self.build()
        self.put(".env", "GRAVITEE_LICENSE=secret-license\nCOMPOSE_FILE=docker-compose.yml:addons/echo/compose.yml\n", 0o600)
        report = self.migrate()
        self.assertEqual(self.text(".env"), "GRAVITEE_LICENSE=secret-license\nCOMPOSE_FILE=docker-compose.yml:addons/echo/compose.yml\n")
        self.assertEqual(report["problems"], ["COMPOSE_FILE in .env names a file that moved: edit it by hand"])
        self.assertNotIn("secret-license", json.dumps(report) + self.text(layout.REPORT))

    def test_a_problem_of_the_move_is_printed_once_when_the_lab_starts(self):
        self.build()
        self.put(".env", "COMPOSE_FILE=docker-compose.yml:addons/echo/compose.yml\n", 0o600)
        said, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(said), contextlib.redirect_stdout(out):
            self.ensure()
            self.ensure()
        self.assertEqual(said.getvalue(), "COMPOSE_FILE in .env names a file that moved: edit it by hand\n")
        self.assertEqual(out.getvalue(), "")

    def test_a_second_migration_reports_and_undoes_itself_and_not_the_first(self):
        self.build()
        self.migrate()
        self.assertEqual(self.report()["turned_off"], ["addon:moved-echo"])
        self.assertIn("addon:local/scratch", self.report()["personal"])
        subprocess.run(["sh", str(self.root / ".lab/migration/undo.sh")], cwd="/", capture_output=True, check=True)
        # the person went back, took the line of the item that waits for a catalog out, deleted an item of their own, and
        # updated again
        self.put(".lab/on.yml", self.text(".lab/on.yml").replace("  - addons/moved-echo/compose.yml\n", ""))
        shutil.rmtree(self.root / "addons/local/scratch")
        self.migrate()
        report = self.report()
        self.assertEqual(report["turned_off"], ["addon:local/scratch"])   # its line is still there: this migration's finding
        self.assertNotIn("addon:local/scratch", report["personal"] + report["moved"])
        rows = {row["key"]: row["items"] for row in layout.status()["notice"]["rows"]}
        self.assertEqual(rows["turned_off"], ["scratch"])   # and moved-echo, the first migration's, is not in the notice
        undo = self.text(".lab/migration/undo.sh")
        self.assertNotIn("scratch", undo)   # the first migration's moves are not this one's
        self.assertIn("trip-demo", undo)

    def test_the_usual_compose_file_line_says_nothing(self):
        self.build()
        self.put(".env", "COMPOSE_FILE=docker-compose.yml:.lab/on.yml\n", 0o600)
        self.assertEqual(self.migrate()["problems"], [])

    def test_a_plan_for_another_default_catalog_or_one_that_does_not_read_is_set_aside_and_made_again(self):
        self.build()
        self.put(layout.PLAN, "not json")
        self.migrate()
        asides = sorted(path.name for path in (self.root / layout.MIGRATION).iterdir() if path.name.startswith("plan.json.bad-"))
        self.assertEqual(len(asides), 1)
        self.assertEqual(json.loads(self.text(layout.PLAN))["default"], "fixture")
        self.assertEqual(self.marker()["from"], 1)

    def test_a_marker_from_a_clone_that_had_nothing_to_move_still_translates_a_stale_line(self):
        self.ensure()
        self.assertEqual(self.marker(), {"layout": 2, "from": 0})
        self.put(".lab/on.yml", "include:\n  - playgrounds/tour/compose.yml\n")
        self.ensure()
        self.assertEqual(self.text(".lab/on.yml"), f"include:\n  - {HOME}/playgrounds/tour/compose.yml\n")
        self.assertEqual(self.marker(), {"layout": 2, "from": 0})   # the marker is not rewritten

    def test_status_and_waiting_are_cheap_enough_for_every_poll(self):
        # The CPU time of this process: the clock on the wall also counts the other tests of a parallel run
        self.build(filler=300)
        started = time.process_time()
        for _ in range(200):
            layout.status()
            layout.waiting()
        self.assertLess((time.process_time() - started) / 200, 0.01)   # well under the 15 ms the fingerprint is allowed
        self.migrate()
        started = time.process_time()
        for _ in range(200):
            layout.status()
            layout.waiting()
        self.assertLess((time.process_time() - started) / 200, 0.005)

    def test_a_setup_that_is_a_folder_or_a_file_in_a_kind_folder_is_left(self):
        self.build()
        os.makedirs(self.root / "setups/folder-not-file")
        self.put("setups/folder-not-file/x.yml", "include: []\n")
        self.put("playgrounds/notes.d/n.txt", "x\n")
        self.put("playgrounds/loose-file", "x\n")
        report = self.migrate()
        self.assertEqual(report["left"], ["playgrounds/loose-file", "playgrounds/notes.d", "setups/folder-not-file"])

    def test_the_guide_files_of_a_kind_folder_that_stayed_are_kept_aside_not_deleted(self):
        self.build()
        self.put("addons/README.md", "my edit of the guide\n")
        self.put("playgrounds/local/AGENTS.md", "mine\n")
        report = self.migrate()
        self.assertEqual(self.text(".lab/migration/kept/addons/README.md"), "my edit of the guide\n")
        self.assertEqual(self.text(".lab/migration/kept/playgrounds/local/AGENTS.md"), "mine\n")
        self.assertIn("path:addons/README.md", report["kept"])
        self.assertFalse(self.exists("addons"))

    def test_junk_in_a_local_folder_goes_too(self):
        self.build()
        self.put("addons/local/.DS_Store", "x\n")
        os.makedirs(self.root / "playgrounds/local/ghost/empty")
        self.migrate()
        for gone in ("addons", "playgrounds", "setups"):
            self.assertFalse(self.exists(gone), gone)
        self.assertEqual(self.report()["left"], [])

    def test_a_promoted_setup_clash_takes_a_suffix_too(self):
        self.build()
        self.put(".lab/catalogs/local/setups/mine-set.yml", MINE_SET)
        self.migrate()
        self.assertEqual(self.text(".lab/catalogs/local/setups/mine-set.yml"), MINE_SET)
        self.assertEqual(self.text(".lab/catalogs/local/setups/mine-set-2.yml"), MINE_SET.replace(
            "../addons/echo/compose.yml", "../../fixture/addons/echo/compose.yml"))
        self.assertEqual(json.loads(self.text(".lab/starred.json"))["setup"], ["local/mine-set-2"])

    def test_a_personal_item_that_clashes_with_one_already_in_the_new_place_is_put_aside_whole(self):
        self.build()
        self.put(".lab/catalogs/local/addons/scratch/compose.yml", OTHER)
        report = self.migrate()
        self.assertEqual(self.text(".lab/catalogs/local/addons/scratch/compose.yml"), OTHER)   # theirs, untouched
        self.assertEqual(self.text(".lab/migration/kept/addons/local/scratch/compose.yml"), SCRATCH)   # the old one, whole
        self.assertEqual(self.text(".lab/migration/kept/addons/local/scratch/.env"), "SCRATCH=secret-scratch\n")
        self.assertIn("addon:local/scratch", report["kept"])
        self.assertEqual(report["kept"].count("addon:local/scratch"), 1)
        self.assertNotIn("scratch", self.text(".lab/on.yml"))   # the line must not turn the other one on
        self.assertIn("addon:local/scratch", report["turned_off"])

    def test_a_line_of_a_modified_copy_goes_to_the_catalog_s_item_and_not_off(self):
        self.build(on="include:\n  - addons/echo-twin/compose.yml\n  - playgrounds/shop/compose.yml\n")
        report = self.migrate()
        self.assertTrue(self.exists(".lab/migration/kept/addons/echo-twin/compose.yml"))
        self.assertEqual(self.text(".lab/on.yml"), f"include:\n  - {HOME}/addons/echo-twin/compose.yml\n"
                                                   f"  - {HOME}/playgrounds/shop/compose.yml\n")
        self.assertEqual(report["turned_off"], [])

    def test_an_edit_to_a_personal_file_is_not_written_over_by_the_final_text_either(self):
        self.build()
        layout.migrate_personal()
        edited = self.text(".lab/catalogs/local/playgrounds/relay/compose.yml") + "# I changed this\n"
        self.put(".lab/catalogs/local/playgrounds/relay/compose.yml", edited)
        self.migrate()
        self.assertEqual(self.text(".lab/catalogs/local/playgrounds/relay/compose.yml"), edited)

    def test_a_hostile_plan_moves_nothing_outside_what_it_is_for(self):
        self.build()
        self.migrate()
        valid = json.loads(self.text(layout.PLAN))
        outside_file = self.root.parent / (self.root.name + "-outside")
        outside_file.write_text("mine\n", encoding="utf-8")
        self.addCleanup(outside_file.unlink, missing_ok=True)
        for name, op in (("an absolute source", {"step": 4, "op": "move", "from": str(outside_file), "to": ".lab/x",
                                                  "why": "kept"}),
                         ("a source that climbs", {"step": 4, "op": "move", "from": "addons/../../outside", "to": ".lab/x",
                                                   "why": "kept"}),
                         ("a place outside .lab", {"step": 4, "op": "move", "from": "addons/echo/.env", "to": "elsewhere/x",
                                                   "why": "kept"}),
                         ("a drop outside", {"step": 4, "op": "drop", "path": "../x", "sha": "0"}),
                         ("a scratch outside", {"step": 5, "op": "scratch", "path": "/etc"}),
                         ("an unknown operation", {"step": 4, "op": "delete", "path": "addons/x"})):
            with self.subTest(name):
                self.assertFalse(layout._valid_plan({**valid, "ops": [*valid["ops"], op]}, "fixture"))
        self.assertTrue(layout._valid_plan(valid, "fixture"))
        self.assertFalse(layout._valid_plan({**valid, "table": [["a"]]}, "fixture"))
        self.assertFalse(layout._valid_plan({**valid, "default": "other"}, "fixture"))
        self.assertFalse(layout._valid_plan([], "fixture"))
        self.assertEqual(outside_file.read_text(encoding="utf-8"), "mine\n")

    def test_a_plan_that_was_edited_to_move_a_file_out_of_the_project_is_set_aside_and_not_run(self):
        self.build()
        self.keep()
        hook = crash_at(0)
        self.patch(moves, "STEP", hook)
        self.migrate()
        stop = max(index for index, label in enumerate(hook.calls, 1) if label == "layout:plan") + 1
        self.restore()
        outside_file = self.root.parent / (self.root.name + "-outside")
        outside_file.write_text("mine\n", encoding="utf-8")
        self.addCleanup(outside_file.unlink, missing_ok=True)
        moves.STEP = crash_at(stop)
        with self.assertRaises(Crash):
            self.migrate()
        moves.STEP = None
        layout._failed = None
        plan = json.loads(self.text(layout.PLAN))
        plan["ops"].append({"step": 4, "op": "move", "from": str(outside_file), "to": ".lab/stolen", "why": "kept"})
        self.put(layout.PLAN, json.dumps(plan))
        self.migrate()
        self.assertEqual(outside_file.read_text(encoding="utf-8"), "mine\n")
        self.assertFalse(self.exists(".lab/stolen"))
        self.assertTrue(any(path.name.startswith("plan.json.bad-") for path in (self.root / layout.MIGRATION).iterdir()))
        self.assertEqual(self.marker()["from"], 1)

    def test_a_legacy_folder_that_cannot_be_read_is_a_signal_and_a_failed_move_never_a_fresh_clone(self):
        self.build()
        os.chmod(self.root / "addons", 0o000)
        try:
            self.assertIn("S2", layout._signals())
            with contextlib.redirect_stderr(io.StringIO()):
                self.ensure()
            self.assertFalse(self.exists(".lab/layout.json"))
            self.assertEqual((layout.status()["phase"], layout.waiting()), ("failed", True))
        finally:
            os.chmod(self.root / "addons", 0o755)
        self.assertEqual(self.migrate()["problems"], [])
        self.assertEqual(layout.status()["phase"], "ready")

    def test_a_link_to_nothing_in_place_of_a_local_folder_is_said_and_does_not_fail_the_move(self):
        self.build(personal=False)
        os.makedirs(self.root / "addons", exist_ok=True)
        os.symlink("/nowhere/at/all", self.root / "addons/local")
        report = self.migrate()
        self.assertEqual(report["problems"], ["addons/local is a link to something that is not a folder: it was left as it is"])
        self.assertEqual(os.readlink(self.root / "addons/local"), "/nowhere/at/all")
        self.assertEqual(report["left"], ["addons/local"])
        self.assertEqual(self.marker()["from"], 1)

    def test_a_unit_folder_that_cannot_be_read_is_left_and_said(self):
        self.build()
        os.chmod(self.root / "addons/gateway", 0o000)
        try:
            report = self.migrate()
        finally:
            os.chmod(self.root / "addons/gateway", 0o755)
        self.assertEqual(report["left"], ["addons/gateway"])
        self.assertEqual(self.text("addons/gateway/.env"), "FIXTURE_GATEWAY_TOKEN=secret-gateway\n")
        self.assertEqual(layout.status()["phase"], "ready")

    def test_status_never_raises(self):
        with mock.patch.object(layout, "_phase", side_effect=OSError("denied")):
            self.assertEqual(layout.status(), {"phase": "ready", "notice": None})
        with mock.patch.object(layout, "_marker", side_effect=ValueError("odd")):
            self.assertEqual(layout.status(), {"phase": "ready", "notice": None})

    def test_closing_the_notice_of_a_clone_that_has_none_takes_no_lock(self):
        with mock.patch.object(registry, "flock", side_effect=AssertionError("the lock was taken")):
            layout.dismiss()


class KeyFiles(Machine):
    """The files that are keys are the machine's in the old layout as they are what the Lab does not install from a catalog:
    one list, the standard's (ST06), which `store` keeps."""

    SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".jks", ".keystore", ".crt", ".csr", ".srl")

    def test_it_is_the_list_of_the_store_and_no_list_of_its_own(self):
        self.assertIs(layout.KEY_SUFFIXES, store.KEY_SUFFIXES)
        self.assertEqual(sorted(layout.KEY_SUFFIXES), sorted(self.SUFFIXES), "the standard's list: .pfx and .keystore are in it")
        self.assertIsNone(re.search(r"^KEY_SUFFIXES = \(", Path(layout.__file__).read_text(encoding="utf-8"), re.M))

    def test_a_key_is_a_machine_file_whatever_the_case_of_its_name(self):
        for suffix in self.SUFFIXES:
            for name in (f"server{suffix}", f"SERVER{suffix.upper()}", f"a.b{suffix}", suffix):
                with self.subTest(name=name):
                    self.assertTrue(layout._machine_name(name))

    def test_the_settings_are_machine_files_and_the_rest_is_not(self):
        for name in (".env", ".ENV", ".env.local", ".env.production", ".Env.x"):
            self.assertTrue(layout._machine_name(name), name)
        for name in (".env.example", ".ENV.EXAMPLE", "env", "notes.txt", "server.pfx.txt", "server.keystore.bak", "pfx", "keystore",
                     "server.pfxx", "server.pub", "compose.yml", "README.md", "server.key.pub"):
            self.assertFalse(layout._machine_name(name), name)

    def test_a_key_is_never_junk_whatever_else_its_name_says(self):
        for suffix in (".pfx", ".keystore"):
            self.assertFalse(layout._scratch_name(f"..server{suffix}", False))
            self.assertFalse(layout._scratch_name(f"..server{suffix}", True))
        self.assertTrue(layout._scratch_name("..server.pfx.tmp", False), "an interrupted write of the Lab's own is junk")

    def test_a_certificate_store_of_a_unit_moves_with_the_machine_files_and_is_not_put_aside(self):
        self.build()
        for path, text in (("addons/echo/certs/server.pfx", "secret-pfx\n"), ("addons/echo/certs/Server.KEYSTORE", "secret-store\n"),
                           ("addons/cluster/ca.pfx", "secret-ca\n")):
            self.put(path, text, 0o600)
        report = self.migrate()
        for path, text in (("addons/echo/certs/server.pfx", "secret-pfx\n"), ("addons/echo/certs/Server.KEYSTORE", "secret-store\n"),
                           ("addons/cluster/ca.pfx", "secret-ca\n")):
            self.assertEqual((self.text(f"{HOME}/{path}"), self.mode(f"{HOME}/{path}")), (text, 0o600), path)
            self.assertFalse(self.exists(path), path)
            self.assertFalse(self.exists(f".lab/migration/kept/{path}"), "not a stray")
        self.assertEqual(report["kept"], ["addon:echo-twin", "addon:port-core", "playground:shop"], "what is kept aside is as before")

    def test_a_certificate_store_of_the_persons_own_item_goes_with_the_item(self):
        self.build()
        self.put("addons/local/scratch/id.pfx", "secret-own-pfx\n", 0o600)
        self.migrate()
        self.assertEqual(self.text(".lab/catalogs/local/addons/scratch/id.pfx"), "secret-own-pfx\n")
        self.assertEqual(self.mode(".lab/catalogs/local/addons/scratch/id.pfx"), 0o600)

    def test_no_secret_is_lost(self):
        self.build()
        self.put("addons/echo/certs/server.pfx", "secret-pfx\n", 0o600)
        self.put("addons/echo/certs/Server.KEYSTORE", "secret-store\n", 0o600)
        self.migrate()
        found = {path.read_text(encoding="utf-8", errors="replace") for path in self.root.rglob("*") if path.is_file()}
        for text in ("secret-pfx\n", "secret-store\n"):
            self.assertIn(text, found)


# ---------------------------------------------------------------------------------------------------------------------
# What the module is
# ---------------------------------------------------------------------------------------------------------------------

class Module(Case):

    SOURCE = Path(layout.__file__).read_text(encoding="utf-8")

    def test_it_uses_the_standard_library_the_moves_and_nothing_that_runs_a_program_or_opens_a_connection(self):
        tree = ast.parse(self.SOURCE)
        imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                     if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module}
        self.assertEqual(imported - {"contextlib", "hashlib", "json", "os", "posixpath", "re", "shutil", "stat", "sys",
                                     "time", "typing", "pathlib"}, set())
        for forbidden in ("subprocess", "socket", "urllib", "http", "docker", "git"):
            self.assertNotIn(forbidden, imported)
        relative = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level}
        self.assertEqual(relative - {None, "catalogs", "docker", "project"}, set())   # project is imported where it is used: it imports this

    def test_it_reads_as_python_3_9(self):
        ast.parse(self.SOURCE, feature_version=(3, 9))
        ast.parse(Path(__file__).read_text(encoding="utf-8"), feature_version=(3, 9))

    def test_the_signatures_are_the_ones_the_stub_shipped_and_the_spec_froze(self):
        import inspect
        self.assertEqual({name: str(inspect.signature(getattr(layout, name)))
                          for name in ("status", "waiting", "ensure", "migrate_personal", "migrate")},
                         {"status": "() -> dict", "waiting": "() -> bool", "ensure": "() -> None",
                          "migrate_personal": "() -> dict", "migrate": "() -> dict"})

    def test_it_names_no_product_catalog_address_or_organization(self):
        for word in ("dobl1", "github.com", "azurecr", "acme-bakery", "Gravitee public", "gravitee-lab", "kafka"):
            self.assertNotIn(word.lower(), self.SOURCE.lower(), word)

    def test_a_report_is_not_shared_between_calls(self):
        report = layout.migrate()
        report["moved"].append("x")
        self.assertEqual(layout.migrate()["moved"], [])

    def test_the_old_enumeration_does_not_see_a_catalog_path(self):
        for kind, (_, pattern) in paths.UNITS.items():
            self.assertIsNone(pattern.fullmatch(".lab/catalogs/fixture/addons/echo/compose.yml"), kind)
            self.assertIsNone(pattern.fullmatch(".lab/catalogs/local/playgrounds/mine/compose.yml"), kind)
        self.assertIsNone(paths.UNITS["addon"][1].fullmatch(f"{HOME}/addons/echo/compose.yml"))

    def test_the_steps_a_test_can_stop_a_run_at_are_all_named_with_the_module_s_prefix(self):
        labels = set(re.findall(r'_step\("([a-z-]+)"\)', self.SOURCE))
        self.assertTrue({"start", "backup", "plan", "text", "on", "undo", "undo-personal", "marker", "report", "rekey",
                         "empty", "drop", "scratch", "clear", "copy", "link"} <= labels, labels)


if __name__ == "__main__":
    unittest.main()
