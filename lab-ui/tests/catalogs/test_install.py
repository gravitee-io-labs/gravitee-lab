"""catalogs/install.py: the files of the catalogs on this machine. The primitives that never follow a link, staging, Add and
the first download of a listed catalog, Update and Go back, Repair, Remove, the sweeper, the working copy that stands in
place of a git install, and `recover()` after a stop at every step of every operation. Everything is on disk in a temporary
project folder against bare repositories made on this machine (a visible skip without git): no network, no Docker daemon.

A stop is simulated by raising `Crash`, a BaseException, from `install.STEP` at the Nth step: no handler of the code under
test that cleans up after an error runs, as when the process dies. Then `recover()` runs and the state must be the old
complete one or the new complete one, and while the operation ran no reader that holds the project lock could see a mix."""

import atexit
import contextlib
import hashlib
import json
import os
import secrets
import shutil
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.docker import compose
from lab_ui.library import moves, project, starred
from lab_ui import paths
from lab_ui.catalogs import gitx, install, manifest, registry
from lab_ui.catalogs import store as bare

from .. import Case, fixtures, gitfixture

READ_ONLY, EXECUTABLE = 0o444, 0o555
TOPS = ["addons/ledger/.env", "addons/ledger/.state", "addons/ledger/certs", "addons/ledger/data",
        "addons/ledger/secrets"]   # what a shadow moves of the machine's files of ODD: the top-most entries
ODD = {".env": "SECRET=1\n", ".state/created.json": "{}\n", "secrets/token": "t\n", "data/db.sqlite": "rows\n",
       "certs/ca.txt": "ca\n"}   # the machine's files of a unit: three that names tell, two that no name does
REAL_READ = manifest.read_catalog   # Compose's reading of catalog.yml, before the tests put the fixtures' in its place
REAL_VACATE = install._vacate

# What is the same for every test is made once for the run: the folder the remotes are in (so that an address written in
# catalogs.json is the same in every test), the Lab's store of a repository as a fetch makes it (a fetch is store.py's to
# test, and costs ten git commands), and a project folder with a catalog added (an Add is this module's to test)
_CACHE = {}


def _made(name):
    """A folder for the whole run, removed at its end, and always the same one."""
    if name not in _CACHE:
        folder = Path(tempfile.mkdtemp(prefix=f"gravitee-lab-{name}-")).resolve()
        atexit.register(shutil.rmtree, folder, True)
        _CACHE[name] = folder
    return _CACHE[name]


def _wipe(folder):
    for child in list(Path(folder).iterdir()):
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


class Crash(BaseException):
    """The process died here. A BaseException, so that the handlers of the code under test do not clean up."""


def stop_at(label):
    """A hook for `install.STEP` that stops the run at the first step with this label."""
    def hook(step):
        if step == label:
            raise Crash(label)
    return hook


def parsed(path, address=None, borrowed=()):
    """manifest.read_catalog without Compose: the scalars of the file, the way the fixtures read them."""
    text = (Path(paths.ROOT) / path).read_text(encoding="utf-8")
    meta = fixtures._meta_of(text)
    return {"schema": 1, "id": meta["id"], "name": meta["name"], "description": meta.get("description", ""),
            "version": meta["version"], "lab": meta["lab"], "license": meta.get("license"), "images": None,
            "contribute": "pull-request", "shared_settings": [], "moved_from": [], "canary": [],
            "requires": {"catalogs": {}}, "unknown": []}


def meta_of(ns, **more):
    """The validated catalog.yml a fixture catalog has, as commit_add takes it."""
    meta = fixtures.catalog_meta(ns)
    return {"schema": 1, "id": meta["id"], "name": meta["name"], "description": meta.get("description", ""),
            "version": meta["version"], "lab": meta["lab"], "license": meta.get("license"), "images": None,
            "contribute": "pull-request", "shared_settings": [], "moved_from": [], "canary": [],
            "requires": {"catalogs": {}}, "unknown": [], **more}


class Watch:
    """The hook of `install.STEP` and the project lock, together: it counts the steps, stops the run at the Nth, and
    checks at every step outside the project lock that what a reader would read is whole."""

    def __init__(self, case, stop=None, ns=None):
        self.case, self.stop, self.ns, self.labels, self.depths, self.depth = case, stop, ns, [], [], 0
        case.patch(project, "exclusive", self.exclusive, create=True)
        case.patch(install, "STEP", self)

    @contextlib.contextmanager
    def exclusive(self):
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1

    def __call__(self, label):
        if self.depth == 0 and self.ns:
            self.case.whole(self.ns, f"before {label}")
        self.labels.append(label)
        self.depths.append(self.depth)
        if self.stop is not None and len(self.labels) - 1 == self.stop:
            raise Crash(label)


class Base(Case):
    """A project folder with the core, the default file naming `fixture`, git that reads local repositories, and the
    helpers every test uses."""

    def setUp(self):
        gitfixture.require()
        self.folder(paths, compose)
        self.patch(paths, "_default", (None, None))
        fixtures.core(self)
        fixtures.default(self, "fixture")
        self.home = gitfixture.isolate(self)
        self.patch(gitx, "_ending", False)   # a test of the exit function elsewhere leaves it for good
        self.patch(manifest, "read_catalog", parsed)
        self.remotes = {}
        _wipe(_made("remotes"))
        self.__dict__.setdefault("_fixture_scratch", {})["remotes"] = _made("remotes")

    # -- the world -------------------------------------------------------------------------------------------------

    def fresh(self):
        """A project folder of its own again, the remotes kept: for the runs of a stop at every step."""
        self.folder(paths, compose)
        self.patch(paths, "_default", (None, None))
        fixtures.core(self)
        fixtures.default(self, "fixture")
        self.remotes.clear()
        _wipe(_made("remotes"))

    def remote(self, ns="partner", **patch):
        if ns not in self.remotes:
            self.remotes[ns] = fixtures.remote(self, ns, **patch)
        return self.remotes[ns]

    def prepare(self, ns="partner", store_ns=None, **patch):
        """The address, a fetch into a new store, the listing, the stage under its namespace. Answers
        (staged, address, roots)."""
        remote = self.remote(ns, **patch)
        address = gitx.parse_address(remote.url)
        ticket = secrets.token_hex(8)
        store_path = registry.new_store_dir(ticket)
        tip = self.fetched(remote, address, store_path)
        kept, ignored = bare.selected(bare.listing(store_path, tip))
        staged = install.stage(address, f"new-{ticket}", store_path, tip, kept, ignored)
        staged = install.rename_stage(staged, store_ns or ns)
        return staged, address, bare.roots(store_path, tip)

    def fetched(self, remote, address, store_path):
        """What `store.fetch` leaves at `store_path` for this remote: a copy of the store of the first fetch of this tip."""
        tip = remote.head()
        template = _made("stores") / f"{tip}.git"
        if not template.exists():
            bare.fetch(address, None, template)
        shutil.rmtree(store_path, ignore_errors=True)   # a store a stopped download left
        shutil.copytree(template, store_path, symlinks=True)
        return tip

    def installed(self, ns="partner"):
        """Catalog `ns` added, as `add` leaves it. The first time it is an Add; after that, the project folder and the
        remote of that Add are put back as they were, which is the same state for a hundredth of the time."""
        key = ("installed", ns)
        if key not in _CACHE:
            self.add(ns)
            saved = _made(f"world-{ns}")
            _wipe(saved)
            shutil.copytree(self.root, saved / "root", symlinks=True)
            shutil.copytree(_made("remotes"), saved / "remotes", symlinks=True)
            _CACHE[key] = saved
            return
        saved = _CACHE[key]
        _wipe(self.root)
        shutil.copytree(saved / "root", self.root, symlinks=True, dirs_exist_ok=True)
        _wipe(_made("remotes"))
        shutil.copytree(saved / "remotes", _made("remotes"), symlinks=True, dirs_exist_ok=True)
        self.remotes[ns] = fixtures.Remote(_made("remotes") / f"{ns}.git")

    def add(self, ns="partner", audience="public", **how):
        """Add: everything before the commit, then commit_add. Answers (result, staged, address)."""
        staged, address, roots = self.prepare(ns)
        meta = how.pop("meta", None) or meta_of(ns)
        result = install.commit_add(staged, address, "main", audience, roots, meta, **how)
        return result, staged, address

    def folder_catalog(self, ns="partner", name=None):
        """A working copy of a fixture catalog under the home folder: a folder an author keeps."""
        target = self.home / (name or f"{ns}-copy")
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(fixtures.CATALOGS / ns, target)
        return target

    def tree(self, ns):
        """{relative path: (mode, bytes)} of everything an install folder holds."""
        base = Path(paths.ROOT) / ".lab" / "catalogs" / ns
        return {p.relative_to(base).as_posix(): (stat.S_IMODE(p.lstat().st_mode), p.read_bytes())
                for p in sorted(base.rglob("*")) if p.is_file() and not p.is_symlink()}

    def content(self, ns):
        """The paths of an install folder that its manifest lists."""
        return set(registry.manifest(ns)["files"])

    def machine(self, ns, unit="addons/ledger", **files):
        """Machine files beside a unit: {relative path inside the unit: text}. Answers their paths in the install. The
        default holds the ones a name tells (`.env`, `.state/`, `secrets/`) and two that no pattern knows, a database in a
        bind folder and a certificate with an ordinary name, which only the manifest and the marker can vouch for."""
        base = Path(paths.ROOT) / ".lab" / "catalogs" / ns / unit
        made = []
        for rel, text in (files or ODD).items():
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (base / rel).write_text(text, encoding="utf-8")
            made.append(f"{unit}/{rel}")
        return made

    @staticmethod
    def held_elsewhere(lock) -> bool:
        """Whether another thread cannot take `lock` now, which is to say that this one holds it."""
        free = []
        thread = threading.Thread(target=lambda: free.append(lock.acquire(blocking=False)) or (free[0] and lock.release()))
        thread.start()
        thread.join()
        return free == [False]

    def whole(self, ns, when=""):
        """What a reader sees of catalog `ns` is whole: a manifest the lock accepts lists files that are all there with
        the sha256 it says, and the units are those of the manifest."""
        found = registry.manifest(ns)
        if found is None:
            return
        base = Path(paths.ROOT) / ".lab" / "catalogs" / ns
        for rel, record in found["files"].items():
            path = base / rel
            self.assertTrue(path.is_file() and not path.is_symlink(), f"{rel} is missing {when}")
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"], f"{rel} differs {when}")
        seen = {f"{kind}:{name}" for kind, names in registry.units_of(ns).items() for name in names}
        self.assertEqual(seen, set(found["units"]) if registry.entry(ns) else set(),
                         "a catalog with no entry has no units, one with an entry has those of its manifest")
        default = paths.default_ns()
        listed = {f"{kind}:{uid}" for kind in paths.KINDS for uid in paths.ids(kind)
                  if paths.origin(uid) == ns}   # what the rest of the Lab finds on disk
        self.assertEqual(listed, {f"{key.partition(':')[0]}:{key.partition(':')[2] if ns == default else '@' + ns + '/' + key.partition(':')[2]}"
                                  for key in seen}, "the paths module lists the units of the manifest, and only those")

    def intact(self, ns):
        """The install passes every check a start makes: a manifest for the pin, every file as it says, read-only."""
        held = registry.lock_of(ns)
        found = registry.manifest(ns)
        self.assertIsNotNone(found, "no manifest the lock accepts")
        self.assertEqual(found["commit"], held["pin"])
        self.assertIsNone(held["pending"])
        self.whole(ns)
        self.assertEqual(install.verify(ns, [f"{k}:{n}" for k, names in registry.units_of(ns).items() for n in names]), [])
        for rel in found["files"]:
            self.assertIn(stat.S_IMODE((Path(paths.ROOT) / ".lab/catalogs" / ns / rel).lstat().st_mode),
                          (READ_ONLY, EXECUTABLE), rel)

    def digests(self, ns):
        """The manifest says what the store says of the commit it is for: the digest of each unit, and the files."""
        found = registry.manifest(ns)
        kept, ignored = bare.selected(bare.listing(registry.store_dir(ns), found["commit"]))
        digests = bare.unit_digests(kept)
        self.assertEqual(found["units"], {key: value for key, value in digests.items() if key != "@catalog"})
        self.assertEqual(digests["@catalog"], found["files"]["catalog.yml"]["oid"])
        self.assertEqual((set(found["files"]), found["ignored"]), ({entry.path for entry in kept}, ignored))

    def nothing(self, ns):
        """Nothing of catalog `ns` is left but what the machine's files are: no entry, no lock, no store, no stage."""
        self.assertIsNone(registry.entry(ns))
        self.assertNotIn(ns, registry.read_lock()["catalogs"])
        base = Path(paths.ROOT) / ".lab"
        self.assertFalse((base / "cache/git" / f"{ns}.git").exists())

    def state(self, ns):
        return {"entry": registry.entry(ns), "lock": registry.lock_of(ns), "tree": self.tree(ns)
                if (Path(paths.ROOT) / ".lab/catalogs" / ns).is_dir() else None}


# ---------------------------------------------------------------------------------------------------------------------
# The primitives that never follow a link
# ---------------------------------------------------------------------------------------------------------------------

class SafeWrite(Case):

    def setUp(self):
        self.folder(paths)
        self.base = self.root / ".lab" / "catalogs"
        self.base.mkdir(parents=True)
        self.stage = self.base / ".stage-acme-hub"
        self.stage.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()

    def test_a_file_is_made_read_only_in_folders_the_lab_makes(self):
        install.safe_write(self.stage, "addons/echo/compose.yml", b"services: {}\n", READ_ONLY)
        file = self.stage / "addons" / "echo" / "compose.yml"
        self.assertEqual(file.read_bytes(), b"services: {}\n")
        self.assertEqual(stat.S_IMODE(file.stat().st_mode), READ_ONLY)
        for folder in (self.stage / "addons", self.stage / "addons" / "echo"):
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o755, "the Lab UI writes a .env beside a unit")

    def test_a_file_is_read_only_whatever_mode_it_is_asked_with(self):
        for asked, made in ((0o644, 0o444), (0o755, 0o555), (0o444, 0o444), (0o555, 0o555), (0o666, 0o444), (0o700, 0o500)):
            install.safe_write(self.stage, f"m/{asked:o}.txt", b"x", asked)
            self.assertEqual(stat.S_IMODE((self.stage / f"m/{asked:o}.txt").stat().st_mode), made, oct(asked))

    def test_the_mode_is_the_one_asked_whatever_the_umask(self):
        old = os.umask(0o277)
        self.addCleanup(os.umask, old)
        install.safe_write(self.stage, "a/run.sh", b"#!/bin/sh\n", EXECUTABLE)
        install.safe_write(self.stage, "a/b.txt", b"x", READ_ONLY)
        self.assertEqual(stat.S_IMODE((self.stage / "a/run.sh").stat().st_mode), EXECUTABLE)
        self.assertEqual(stat.S_IMODE((self.stage / "a/b.txt").stat().st_mode), READ_ONLY)
        self.assertEqual(stat.S_IMODE((self.stage / "a").stat().st_mode), 0o755)

    def test_a_link_in_any_parent_is_refused_and_nothing_is_written_through_it(self):
        (self.stage / "addons").symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(install.Unsafe) as raised:
            install.safe_write(self.stage, "addons/echo/compose.yml", b"x", READ_ONLY)
        self.assertEqual((raised.exception.code, raised.exception.status), ("damaged", 409))
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_a_link_deeper_down_is_refused_too(self):
        (self.stage / "a").mkdir()
        (self.stage / "a" / "b").symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(install.Unsafe):
            install.safe_write(self.stage, "a/b/c/d.txt", b"x", READ_ONLY)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_the_root_itself_may_not_be_a_link(self):
        link = self.base / ".stage-linked"
        link.symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(install.Unsafe):
            install.safe_write(link, "a.txt", b"x", READ_ONLY)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_a_file_in_the_place_of_a_folder_is_refused(self):
        (self.stage / "addons").write_text("a file", encoding="utf-8")
        with self.assertRaises(install.Unsafe):
            install.safe_write(self.stage, "addons/echo/compose.yml", b"x", READ_ONLY)
        self.assertEqual((self.stage / "addons").read_text(encoding="utf-8"), "a file")

    def test_an_existing_file_is_refused_and_kept_as_it_is(self):
        (self.stage / "a.txt").write_text("first", encoding="utf-8")
        with self.assertRaises(install.Unsafe):
            install.safe_write(self.stage, "a.txt", b"second", READ_ONLY)
        self.assertEqual((self.stage / "a.txt").read_text(encoding="utf-8"), "first")

    def test_an_existing_link_is_refused_and_not_followed(self):
        target = self.outside / "target.txt"
        target.write_text("theirs", encoding="utf-8")
        (self.stage / "a.txt").symlink_to(target)
        with self.assertRaises(install.Unsafe):
            install.safe_write(self.stage, "a.txt", b"mine", READ_ONLY)
        self.assertEqual(target.read_text(encoding="utf-8"), "theirs")

    def test_a_dangling_link_is_not_a_place_to_write_either(self):
        (self.stage / "a.txt").symlink_to(self.outside / "nowhere.txt")
        with self.assertRaises(install.Unsafe):
            install.safe_write(self.stage, "a.txt", b"mine", READ_ONLY)
        self.assertFalse((self.outside / "nowhere.txt").exists())

    def test_a_path_that_could_leave_the_folder_is_not_a_path(self):
        for rel in ("", "/abs", "../x", "a/../x", "a//b", "./a", "a/.", "a\\b", "a\0b"):
            with self.assertRaises(ValueError, msg=repr(rel)):
                install.safe_write(self.stage, rel, b"x", READ_ONLY)
        self.assertEqual(sorted(p.name for p in self.stage.rglob("*")), [])

    def test_a_missing_root_is_an_error_and_nothing_is_made(self):
        with self.assertRaises(FileNotFoundError):
            install.safe_write(self.base / ".stage-gone", "a.txt", b"x", READ_ONLY)
        self.assertFalse((self.base / ".stage-gone").exists())

    def test_a_failed_write_leaves_no_file(self):
        with mock.patch("os.fchmod", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                install.safe_write(self.stage, "a/b.txt", b"x", READ_ONLY)
        self.assertFalse((self.stage / "a" / "b.txt").exists())

    def test_what_is_said_of_a_refusal_holds_no_path_of_this_machine(self):
        """`detail` is for the log and the dialog's "Show output": the place of the project is for neither."""
        (self.stage / "addons").symlink_to(self.outside, target_is_directory=True)
        link = self.base / ".stage-linked"
        link.symlink_to(self.outside, target_is_directory=True)
        (self.stage / "a.txt").write_text("first", encoding="utf-8")
        calls = (lambda: install.safe_write(self.stage, "addons/echo/compose.yml", b"x", READ_ONLY),   # a link above
                 lambda: install.safe_write(link, "a.txt", b"x", READ_ONLY),   # the root is a link
                 lambda: install.safe_write(self.stage, "a.txt", b"x", READ_ONLY),   # a file is there
                 lambda: install._read(self.stage / "addons"),   # a link cannot be read
                 lambda: install._read(self.base))   # a folder is not a file
        for call in calls:
            with self.assertRaises(install.Unsafe) as raised:
                call()
            why = raised.exception.detail["why"]
            self.assertTrue(why)
            for root in {str(self.root), os.path.realpath(self.root)}:
                self.assertNotIn(root, why)


class SafeReplace(Case):

    def setUp(self):
        self.folder(paths)
        self.base = self.root / ".lab" / "catalogs"
        self.install = self.base / "acme-hub"
        self.install.mkdir(parents=True)
        self.stage = self.base / ".stage-acme-hub"
        self.stage.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()

    def staged(self, text="new", mode=READ_ONLY):
        install.safe_write(self.stage, "addons/echo/compose.yml", text.encode(), mode)
        return self.stage / "addons/echo/compose.yml"

    def test_a_file_replaces_another_whole_and_keeps_its_mode(self):
        (self.install / "addons/echo").mkdir(parents=True)
        old = self.install / "addons/echo/compose.yml"
        old.write_text("old", encoding="utf-8")
        old.chmod(READ_ONLY)
        install.safe_replace(self.staged("new"), old)
        self.assertEqual(old.read_text(encoding="utf-8"), "new")
        self.assertEqual(stat.S_IMODE(old.stat().st_mode), READ_ONLY)
        self.assertFalse((self.stage / "addons/echo/compose.yml").exists())

    def test_missing_folders_are_made(self):
        install.safe_replace(self.staged(), self.install / "addons/echo/compose.yml")
        self.assertEqual((self.install / "addons/echo/compose.yml").read_text(encoding="utf-8"), "new")
        self.assertEqual(stat.S_IMODE((self.install / "addons/echo").stat().st_mode), 0o755)

    def test_a_link_in_a_parent_is_refused_and_nothing_is_written_through_it(self):
        (self.install / "addons").symlink_to(self.outside, target_is_directory=True)
        source = self.staged()
        with self.assertRaises(install.Unsafe):
            install.safe_replace(source, self.install / "addons/echo/compose.yml")
        self.assertEqual(list(self.outside.iterdir()), [])
        self.assertTrue(source.exists(), "what was refused stays where it was")

    def test_a_link_in_the_place_of_the_install_is_refused(self):
        shutil.rmtree(self.install)
        self.install.symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(install.Unsafe):
            install.safe_replace(self.staged(), self.install / "addons/echo/compose.yml")
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_a_link_in_the_place_of_the_file_is_replaced_and_not_followed(self):
        (self.install / "addons/echo").mkdir(parents=True)
        target = self.outside / "theirs.txt"
        target.write_text("theirs", encoding="utf-8")
        (self.install / "addons/echo/compose.yml").symlink_to(target)
        install.safe_replace(self.staged("mine"), self.install / "addons/echo/compose.yml")
        self.assertFalse((self.install / "addons/echo/compose.yml").is_symlink())
        self.assertEqual((self.install / "addons/echo/compose.yml").read_text(encoding="utf-8"), "mine")
        self.assertEqual(target.read_text(encoding="utf-8"), "theirs")

    def test_a_folder_in_the_place_of_the_file_is_refused(self):
        (self.install / "addons/echo/compose.yml").mkdir(parents=True)
        (self.install / "addons/echo/compose.yml/.state").write_text("x", encoding="utf-8")
        with self.assertRaises(install.Unsafe):
            install.safe_replace(self.staged(), self.install / "addons/echo/compose.yml")
        self.assertTrue((self.install / "addons/echo/compose.yml/.state").exists())

    def test_a_source_that_is_a_link_is_refused(self):
        target = self.outside / "x.txt"
        target.write_text("x", encoding="utf-8")
        link = self.stage / "a.txt"
        link.symlink_to(target)
        with self.assertRaises(install.Unsafe):
            install.safe_replace(link, self.install / "a.txt")
        self.assertFalse((self.install / "a.txt").exists())

    def test_what_is_said_of_a_refusal_holds_no_path_of_this_machine(self):
        (self.install / "addons/echo/compose.yml").mkdir(parents=True)
        link = self.stage / "a.txt"
        link.symlink_to(self.outside / "x.txt")
        for call in (lambda: install.safe_replace(self.staged(), self.install / "addons/echo/compose.yml"),   # a folder
                     lambda: install.safe_replace(link, self.install / "a.txt")):   # the source is a link
            with self.assertRaises(install.Unsafe) as raised:
                call()
            why = raised.exception.detail["why"]
            self.assertTrue(why)
            for root in {str(self.root), os.path.realpath(self.root)}:
                self.assertNotIn(root, why)

    def test_a_destination_outside_the_catalogs_is_not_one(self):
        source = self.staged()
        with self.assertRaises(ValueError):
            install.safe_replace(source, self.root / "elsewhere.txt")
        with self.assertRaises(ValueError):
            install.safe_replace(source, self.base / "acme-hub" / ".." / ".." / "x.txt")
        self.assertTrue(source.exists())


# ---------------------------------------------------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------------------------------------------------

class Staging(Base):

    def test_the_stage_holds_the_files_that_were_kept_read_only_and_nothing_else(self):
        staged, address, roots = self.prepare("fixture")
        self.assertEqual((staged.ns, staged.kind), ("fixture", "git"))
        self.assertEqual(staged.path, registry.stage_dir("fixture"))
        found = {p.relative_to(staged.path).as_posix() for p in staged.path.rglob("*") if p.is_file()}
        self.assertEqual(found, {entry.path for entry in staged.kept})
        for entry in staged.kept:
            file = staged.path / entry.path
            self.assertEqual(hashlib.sha1(b"blob %d\0" % entry.size + file.read_bytes()).hexdigest(), entry.oid)
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), READ_ONLY)
        for folder in [p for p in staged.path.rglob("*") if p.is_dir()]:
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o755)
        self.assertEqual(roots, bare.roots(staged.store, staged.commit))

    def test_an_executable_file_stays_executable_and_read_only(self):
        files = {**gitfixture.catalog_files("scripts"), "addons/echo/run.sh": gitfixture.Executable("#!/bin/sh\n")}
        bare_repo = gitfixture.make_repo(self.home / "scripts.git", files)
        address = gitx.parse_address(gitfixture.url(bare_repo))
        store_path = registry.new_store_dir("ab12")
        tip = bare.fetch(address, None, store_path)
        kept, ignored = bare.selected(bare.listing(store_path, tip))
        staged = install.stage(address, "scripts", store_path, tip, kept, ignored)
        self.assertEqual(stat.S_IMODE((staged.path / "addons/echo/run.sh").stat().st_mode), EXECUTABLE)
        self.assertEqual(stat.S_IMODE((staged.path / "addons/echo/compose.yml").stat().st_mode), READ_ONLY)

    def test_the_catalog_file_is_written_first(self):
        order = []
        real = install.safe_write
        self.patch(install, "safe_write", lambda root, rel, data, mode: (order.append(rel), real(root, rel, data, mode))[1])
        self.prepare("fixture")
        self.assertEqual(order[0], "catalog.yml")
        self.assertEqual(len(order), len(set(order)))

    def test_a_hostile_catalog_is_staged_without_what_the_filter_drops(self):
        files = {**gitfixture.catalog_files("hostile-one"), **gitfixture.hostile_files()}
        repo = gitfixture.make_repo(self.home / "hostile.git", files)
        address = gitx.parse_address(gitfixture.url(repo))
        store_path = registry.new_store_dir("cd34")
        tip = bare.fetch(address, None, store_path)
        kept, ignored = bare.selected(bare.listing(store_path, tip))
        staged = install.stage(address, "hostile-one", store_path, tip, kept, ignored)
        found = {p.relative_to(staged.path).as_posix() for p in staged.path.rglob("*") if p.is_file() or p.is_symlink()}
        self.assertEqual(found, {entry.path for entry in kept})
        self.assertIn("addons/echo/.env.example", found)
        for path in found:
            low = path.lower()
            self.assertFalse(low.endswith((".key", ".pem", "agents.md", "claude.md")) or "secrets" in low or "/.env" in low
                             and not low.endswith(".env.example") or ".state" in low, path)
        self.assertFalse(any(p.is_symlink() for p in staged.path.rglob("*")))
        self.assertGreater(ignored, 10)
        outside = [p for p in (Path(paths.ROOT) / ".lab").rglob("*") if p.is_file()
                   and registry.stage_dir("hostile-one") not in p.parents and "cache" not in p.parts]
        self.assertEqual(outside, [], "nothing but the stage and the store was written")

    def test_a_stage_an_earlier_attempt_left_is_replaced(self):
        stale = registry.stage_dir("partner")
        stale.mkdir(parents=True)
        (stale / "left-over.txt").write_text("x", encoding="utf-8")
        staged, _, _ = self.prepare("partner")
        self.assertFalse((staged.path / "left-over.txt").exists())
        self.assertTrue((staged.path / "catalog.yml").is_file())

    def test_a_stage_that_is_a_link_is_replaced_and_not_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep", encoding="utf-8")
        registry.stage_dir("partner").parent.mkdir(parents=True, exist_ok=True)
        registry.stage_dir("partner").symlink_to(outside, target_is_directory=True)
        staged, _, _ = self.prepare("partner")
        self.assertFalse(staged.path.is_symlink())
        self.assertEqual([p.name for p in outside.iterdir()], ["keep.txt"])

    def test_a_failed_stage_leaves_no_folder(self):
        self.remote("partner")
        address = gitx.parse_address(self.remote("partner").url)
        store_path = registry.new_store_dir("ef56")
        tip = bare.fetch(address, None, store_path)
        kept, ignored = bare.selected(bare.listing(store_path, tip))
        with mock.patch.object(install, "safe_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                install.stage(address, "partner", store_path, tip, kept, ignored)
        self.assertFalse(registry.stage_dir("partner").exists())

    def test_an_object_the_store_lacks_is_the_damage_a_repair_fetches_for(self):
        address = gitx.parse_address(self.remote("partner").url)
        store_path = registry.new_store_dir("0a1b")
        tip = bare.fetch(address, None, store_path)
        kept, ignored = bare.selected(bare.listing(store_path, tip))
        other = [bare.Entry(e.mode, "1" * 40, e.size, e.path) if e.path == "catalog.yml" else e for e in kept]
        with self.assertRaises(registry.CatalogError) as raised:
            install.stage(address, "partner", store_path, tip, other, ignored)
        self.assertEqual(raised.exception.code, "fsck")
        self.assertFalse(registry.stage_dir("partner").exists())

    def test_a_git_stage_needs_its_store_and_commit(self):
        address = gitx.parse_address(self.remote("partner").url)
        with self.assertRaises(ValueError):
            install.stage(address, "partner", None, "a" * 40, [])

    def test_the_placeholder_stage_is_renamed_to_the_namespace(self):
        address = gitx.parse_address(self.remote("partner").url)
        store_path = registry.new_store_dir("1c2d")
        tip = bare.fetch(address, None, store_path)
        kept, ignored = bare.selected(bare.listing(store_path, tip))
        staged = install.stage(address, "new-1c2d", store_path, tip, kept, ignored)
        self.assertEqual(staged.path.name, ".stage-new-1c2d")
        moved = install.rename_stage(staged, "partner")
        self.assertEqual((moved.ns, moved.path.name), ("partner", ".stage-partner"))
        self.assertFalse(staged.path.exists())
        self.assertTrue((moved.path / "catalog.yml").is_file())
        self.assertIs(install.rename_stage(moved, "partner").path, moved.path, "a stage under its name stays")

    def test_the_stage_of_a_folder_is_a_link_and_nothing_is_copied(self):
        folder = self.folder_catalog("partner")
        address = gitx.parse_address(str(folder))
        staged = install.stage(address, "partner", None, "", [])
        self.assertEqual(staged.kind, "folder")
        self.assertTrue(staged.path.is_symlink())
        self.assertEqual(os.readlink(staged.path), address.path)
        again = install.stage(address, "partner", None, "", [])
        self.assertEqual(os.readlink(again.path), address.path, "a stage left by an earlier attempt is replaced")

    def test_a_folder_that_is_gone_or_holds_no_catalog_is_refused(self):
        folder = self.folder_catalog("partner")
        address = gitx.parse_address(str(folder))
        (folder / "catalog.yml").unlink()
        with self.assertRaises(registry.CatalogError) as raised:
            install.stage(address, "partner", None, "", [])
        self.assertEqual(raised.exception.code, "folder_not_catalog")
        shutil.rmtree(folder)
        with self.assertRaises(registry.CatalogError) as raised:
            install.stage(address, "partner", None, "", [])
        self.assertEqual(raised.exception.code, "folder_missing")

    def test_a_system_that_cannot_link_says_so(self):
        folder = self.folder_catalog("partner")
        address = gitx.parse_address(str(folder))
        with mock.patch("os.symlink", side_effect=OSError("not permitted")):
            with self.assertRaises(registry.CatalogError) as raised:
                install.stage(address, "partner", None, "", [])
        self.assertEqual((raised.exception.code, raised.exception.message),
                         ("symlink_unsupported", "The Lab cannot link folders on this system. Use WSL 2."))

    def test_the_audit_names_what_is_not_what_was_judged(self):
        staged, _, _ = self.prepare("partner")
        install._audit(staged.path, staged.kept)
        victim = staged.path / "addons/ledger/compose.yml"
        victim.chmod(0o644)
        victim.write_text("services: {evil: {privileged: true}}\n", encoding="utf-8")
        victim.chmod(READ_ONLY)
        with self.assertRaises(install.Unsafe):
            install._audit(staged.path, staged.kept)

    def test_the_audit_refuses_an_extra_file_a_link_an_extra_folder_a_missing_file_and_another_mode(self):
        for plant in ("file", "link", "folder", "missing", "mode", "deep"):
            with self.subTest(plant):
                staged, _, _ = self.prepare("partner")
                if plant == "file":
                    (staged.path / "extra.txt").write_text("x", encoding="utf-8")
                elif plant == "link":
                    (staged.path / "addons/ledger/link").symlink_to(self.root)
                elif plant == "folder":
                    (staged.path / "addons/empty-folder").mkdir()
                elif plant == "missing":
                    (staged.path / "addons/ledger/README.md").chmod(0o644)
                    (staged.path / "addons/ledger/README.md").unlink()
                elif plant == "mode":
                    (staged.path / "addons/ledger/README.md").chmod(0o644)
                else:
                    (staged.path / "addons/ledger/x").mkdir()
                    (staged.path / "addons/ledger/x/y.txt").write_text("x", encoding="utf-8")
                with self.assertRaises(install.Unsafe):
                    install._audit(staged.path, staged.kept)

    def test_the_audit_ignores_a_manifest_in_the_stage_only_at_its_top(self):
        staged, _, _ = self.prepare("partner")
        install._write_manifest(staged.path, {"schema": 1})
        install._audit(staged.path, staged.kept)
        (staged.path / "addons/ledger/.installed.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(install.Unsafe):
            install._audit(staged.path, staged.kept)


class Walking(Base):

    def test_a_folder_catalog_is_listed_like_a_repository(self):
        folder = self.folder_catalog("fixture")
        (folder / "addons" / "echo" / ".env").write_text("SECRET=1\n", encoding="utf-8")
        (folder / "addons" / "echo" / "link").symlink_to(self.root)
        (folder / "README.md").write_text("not installed\n", encoding="utf-8")
        entries = install.walk(str(folder))
        by_path = {entry.path: entry for entry in entries}
        self.assertEqual(by_path["catalog.yml"].mode, "100644")
        self.assertEqual(by_path["addons/echo/link"].mode, "120000")
        self.assertNotIn("README.md", by_path, "only the places that hold units are walked")
        kept, ignored = bare.selected(entries)
        self.assertNotIn("addons/echo/.env", {entry.path for entry in kept})
        self.assertNotIn("addons/echo/link", {entry.path for entry in kept})
        self.assertGreaterEqual(ignored, 2)

    def test_the_mode_comes_from_lstat_and_a_linked_folder_is_not_followed(self):
        folder = self.folder_catalog("partner")
        (folder / "addons" / "ledger" / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (folder / "addons" / "ledger" / "run.sh").chmod(0o755)
        (folder / "playgrounds").symlink_to(self.root, target_is_directory=True)
        entries = {entry.path: entry for entry in install.walk(str(folder))}
        self.assertEqual(entries["addons/ledger/run.sh"].mode, "100755")
        self.assertEqual(entries["playgrounds"].mode, "120000")
        self.assertFalse(any(path.startswith("playgrounds/") for path in entries))

    def test_a_folder_with_too_many_entries_is_too_large(self):
        folder = self.folder_catalog("partner")
        with mock.patch.object(install, "WALK_LIMIT", 3):
            with self.assertRaises(registry.CatalogError) as raised:
                install.walk(str(folder))
        self.assertEqual(raised.exception.code, "too_large")

    def test_a_tree_deeper_than_a_catalog_goes_is_counted_not_followed(self):
        folder = self.folder_catalog("partner")
        deep = folder / "addons" / "ledger"
        for _ in range(12):
            deep = deep / "d"
        deep.mkdir(parents=True)
        (deep / "f.txt").write_text("x", encoding="utf-8")
        kept, ignored = bare.selected(install.walk(str(folder)))
        self.assertGreaterEqual(ignored, 1)
        self.assertFalse(any("/d/d/d/d/d/d/d/d/d/d/d" in entry.path for entry in kept))


# ---------------------------------------------------------------------------------------------------------------------
# Add, and the first download of a listed catalog
# ---------------------------------------------------------------------------------------------------------------------

class Adding(Base):

    def test_an_add_installs_the_catalog_whole(self):
        result, staged, address = self.add("partner")
        base = Path(paths.ROOT) / ".lab" / "catalogs" / "partner"
        self.assertEqual({p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()},
                         {".installed.json", "catalog.yml", "addons/ledger/README.md", "addons/ledger/compose.yml"})
        self.intact("partner")
        self.assertFalse(staged.path.exists(), "the stage became the install")
        for rel, (mode, data) in self.tree("partner").items():
            if rel != ".installed.json":
                self.assertEqual(mode, READ_ONLY, rel)
        self.assertEqual(stat.S_IMODE(base.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((base / "addons").stat().st_mode), 0o755)
        self.assertEqual(result["entry"]["id"], "partner")

    def test_the_manifest_digests_are_the_ones_of_the_store(self):
        self.add("fixture")
        found = registry.manifest("fixture")
        store_path = registry.store_dir("fixture")
        listing = bare.listing(store_path, found["commit"])
        kept, ignored = bare.selected(listing)
        digests = bare.unit_digests(kept)
        self.assertEqual(found["units"], {k: v for k, v in digests.items() if k != "@catalog"})
        self.assertEqual(digests["@catalog"], found["files"]["catalog.yml"]["oid"])
        self.assertEqual(found["ignored"], ignored)
        self.assertEqual(set(found["files"]), {entry.path for entry in kept})
        for entry in kept:
            record = found["files"][entry.path]
            self.assertEqual((record["mode"], record["oid"], record["size"]), (entry.mode, entry.oid, entry.size))

    def test_the_manifest_is_written_the_same_way_twice(self):
        self.add("partner")
        first = (Path(paths.ROOT) / ".lab/catalogs/partner/.installed.json").read_bytes()
        install.repair("partner")
        self.assertEqual((Path(paths.ROOT) / ".lab/catalogs/partner/.installed.json").read_bytes(), first)

    def test_the_lock_and_the_config_say_what_was_installed(self):
        self.add("partner", audience="public")
        remote = self.remotes["partner"]
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["tip"], held["previous"]), (remote.head(), remote.head(), None))
        self.assertEqual((held["roots"], held["audience"]), (remote.git("rev-list", "--max-parents=0", "HEAD").split(), "public"))
        self.assertEqual((held["name"], held["version"], held["lab"]), ("Partner fixture", "2.0.0", 1))
        self.assertIsNone(held["pending"])
        self.assertIsNone(held["error"])
        self.assertRegex(held["installed"], r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
        entry = registry.entry("partner")
        self.assertEqual((entry["kind"], entry["url"], entry["branch"], entry["auto"]),
                         ("git", remote.url, "main", True))
        self.assertNotIn("host", entry)
        self.assertEqual(registry.units_of("partner")["addon"], ["ledger"])

    def test_the_store_is_renamed_and_keeps_its_refs(self):
        self.add("partner")
        store_path = registry.store_dir("partner")
        remote = self.remotes["partner"]
        self.assertTrue(store_path.is_dir())
        self.assertEqual(bare.resolve(store_path, "pin"), remote.head())
        self.assertEqual(bare.resolve(store_path, "tip"), remote.head())
        self.assertEqual([p.name for p in (Path(paths.ROOT) / ".lab/cache/git").iterdir()], ["partner.git"])

    def test_an_installed_unit_is_found_by_the_paths_module(self):
        self.add("partner")
        self.assertEqual(paths.ids("addon"), ["@partner/ledger"])
        self.assertEqual(paths.unit_of(".lab/catalogs/partner/addons/ledger/compose.yml"), ("addon", "@partner/ledger"))

    def test_the_branch_the_page_leaves_empty_is_not_stored(self):
        staged, address, roots = self.prepare("partner")
        install.commit_add(staged, address, "", "public", roots, meta_of("partner"))
        self.assertNotIn("branch", registry.entry("partner"))

    def test_an_audience_or_roots_that_cannot_be_recorded_are_refused_before_anything_is_made(self):
        staged, address, roots = self.prepare("partner")
        for audience, bad_roots in (("secret", roots), ("public", ["x"]), ("public", [None]), (None, roots)):
            with self.assertRaises(ValueError):
                install.commit_add(staged, address, "main", audience, bad_roots, meta_of("partner"))
        self.assertTrue(staged.path.is_dir())
        self.assertFalse(registry.store_dir("partner").exists())
        self.assertEqual(registry.read_lock()["catalogs"], {})

    def test_the_choice_to_use_the_machine_is_stored_for_a_catalog_that_is_not_listed(self):
        staged, address, roots = self.prepare("partner")
        install.commit_add(staged, address, "main", "private", roots, meta_of("partner"), host=True, auto=False)
        entry = registry.entry("partner")
        self.assertEqual((entry["host"], entry["auto"]), (True, False))

    def test_the_array_order_is_the_order_the_catalogs_were_added(self):
        self.add("partner")
        self.add("lab-new")
        self.assertEqual([one["id"] for one in registry.entries()], ["partner", "lab-new"])

    def test_the_same_catalog_cannot_be_added_twice(self):
        self.add("partner")
        staged, address, roots = self.prepare("partner", store_ns="partner")
        with self.assertRaises(registry.CatalogError) as raised:
            install.commit_add(staged, address, "main", "public", roots, meta_of("partner"))
        self.assertEqual((raised.exception.code, raised.exception.status), ("already_added", 409))
        self.assertEqual(raised.exception.message, "Already added as Partner fixture.")
        self.intact("partner")

    def test_the_same_address_under_another_name_cannot_be_added_twice(self):
        self.add("partner")
        staged, address, roots = self.prepare("partner", store_ns="partner-two")
        with self.assertRaises(registry.CatalogError) as raised:
            install.commit_add(staged, address, "main", "public", roots, meta_of("partner", id="partner-two"))
        self.assertEqual(raised.exception.code, "already_added")
        self.assertFalse(registry.install_dir("partner-two").exists())

    def test_a_reserved_name_is_refused_unless_the_address_is_the_listed_one(self):
        remote = self.remote("partner")
        staged, address, roots = self.prepare("partner", store_ns="official")
        with self.assertRaises(registry.CatalogError) as raised:
            install.commit_add(staged, address, "main", "public", roots, meta_of("partner", id="official"))
        self.assertEqual(raised.exception.code, "name_reserved")
        self.assertFalse(registry.install_dir("official").exists())
        self.assertEqual(registry.entries(), [])
        listed = {"schema": 1, "catalogs": [{"id": "official", "name": "Official one", "description": "",
                                             "url": "https://example.invalid/partner.git"}]}
        self.assertNotEqual(registry.display_address(listed["catalogs"][0]["url"]), registry.display_address(remote.url))

    def test_a_catalog_whose_file_declares_another_namespace_is_not_the_one_expected(self):
        staged, address, roots = self.prepare("partner")
        with self.assertRaises(registry.CatalogError) as raised:
            install.commit_add(staged, address, "main", "public", roots, meta_of("partner", id="someone-else"))
        self.assertEqual((raised.exception.code, raised.exception.status), ("wrong_catalog", 409))
        self.assertEqual(raised.exception.message, f"{address.repo or address.display} is not the catalog this Lab expects. "
                         "Run make update, then try again.")
        self.nothing("partner")

    def test_a_ticket_whose_stage_is_gone_is_expired(self):
        staged, address, roots = self.prepare("partner")
        shutil.rmtree(staged.path)
        with self.assertRaises(registry.CatalogError) as raised:
            install.commit_add(staged, address, "main", "public", roots, meta_of("partner"))
        self.assertEqual((raised.exception.code, raised.exception.message),
                         ("ticket_expired", "This took too long. Start again."))
        self.nothing("partner")

    def test_a_stage_changed_after_it_was_judged_is_not_installed(self):
        staged, address, roots = self.prepare("partner")
        file = staged.path / "addons/ledger/compose.yml"
        file.chmod(0o644)
        file.write_text("services: {evil: {privileged: true}}\n", encoding="utf-8")
        file.chmod(READ_ONLY)
        with self.assertRaises(registry.CatalogError) as raised:
            install.commit_add(staged, address, "main", "public", roots, meta_of("partner"))
        self.assertEqual(raised.exception.code, "damaged")
        self.assertFalse(registry.install_dir("partner").exists())
        self.assertIsNone(registry.entry("partner"))
        self.assertNotIn("partner", registry.read_lock()["catalogs"])

    def test_a_stage_with_a_file_added_to_it_is_not_installed(self):
        staged, address, roots = self.prepare("partner")
        (staged.path / "addons/ledger/extra.yml").write_text("x", encoding="utf-8")
        with self.assertRaises(registry.CatalogError) as raised:
            install.commit_add(staged, address, "main", "public", roots, meta_of("partner"))
        self.assertEqual(raised.exception.code, "damaged")
        self.assertFalse(registry.install_dir("partner").exists())

    def test_nothing_is_written_outside_the_lab_folder_by_an_add(self):
        before = {p for p in self.root.rglob("*")}
        self.add("fixture")
        after = {p for p in self.root.rglob("*")} - before
        for path in after:
            self.assertIn(".lab", path.relative_to(self.root).parts[:1], str(path))

    def test_a_folder_catalog_is_added_as_a_link_and_nothing_is_copied(self):
        folder = self.folder_catalog("partner")
        address = gitx.parse_address(str(folder))
        staged = install.stage(address, "partner", None, "", [])
        before = sorted(p.name for p in folder.rglob("*"))
        result = install.commit_add(staged, address, None, "private", [], meta_of("partner"))
        link = registry.install_dir("partner")
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), address.path)
        self.assertEqual(sorted(p.name for p in folder.rglob("*")), before, "the author's folder is not touched")
        self.assertEqual(registry.entry("partner")["kind"], "folder")
        self.assertEqual(registry.entry("partner")["path"], address.path)
        self.assertNotIn("pin", registry.entry("partner"))
        self.assertIsNone(registry.lock_of("partner")["pin"])
        self.assertEqual(registry.lock_of("partner")["name"], "Partner fixture")
        self.assertEqual(registry.units_of("partner")["addon"], ["ledger"])
        self.assertEqual(result["adopted"], [])
        self.assertFalse((Path(paths.ROOT) / ".lab/cache/git").exists() and list((Path(paths.ROOT) / ".lab/cache/git").iterdir()))

    def test_a_folder_catalog_takes_the_place_of_the_settings_a_removed_git_catalog_left(self):
        self.add("partner")
        self.machine("partner")
        install.remove("partner", keep_settings=True)
        folder = self.folder_catalog("partner")
        address = gitx.parse_address(str(folder))
        staged = install.stage(address, "partner", None, "", [])
        install.commit_add(staged, address, None, "private", [], meta_of("partner"))
        self.assertTrue(registry.install_dir("partner").is_symlink())
        aside = [p for p in (Path(paths.ROOT) / ".lab/catalogs").iterdir() if p.name.startswith(".removed-partner-")]
        self.assertEqual(len(aside), 1)
        self.assertEqual((aside[0] / "addons/ledger/.env").read_text(encoding="utf-8"), "SECRET=1\n")


class AddingOverSettings(Base):
    """An Add into the folder a Remove that kept the settings left (rule 12), or beside the one the sweeper renamed."""

    def removed_with_settings(self):
        self.add("partner")
        self.machine("partner")
        install.remove("partner", keep_settings=True)

    def test_a_remove_that_kept_the_settings_leaves_exactly_them_and_the_marker(self):
        self.add("partner")
        made = self.machine("partner")
        install.remove("partner", keep_settings=True)
        base = Path(paths.ROOT) / ".lab/catalogs/partner"
        left = sorted(p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file())
        self.assertEqual(left, sorted([install.MARKER, *made]))
        self.assertEqual(stat.S_IMODE((base / install.MARKER).stat().st_mode), 0o600)

    def test_an_add_merges_into_that_folder_and_the_settings_stay_where_they_were(self):
        self.removed_with_settings()
        base = Path(paths.ROOT) / ".lab/catalogs/partner"
        env = base / "addons/ledger/.env"
        inode = env.stat().st_ino
        self.add("partner")
        self.intact("partner")
        self.assertEqual(env.read_text(encoding="utf-8"), "SECRET=1\n")
        self.assertEqual(env.stat().st_ino, inode, "a machine file is not moved or rewritten")
        self.assertEqual((base / "addons/ledger/.state/created.json").read_text(encoding="utf-8"), "{}\n")
        self.assertEqual((base / "addons/ledger/secrets/token").read_text(encoding="utf-8"), "t\n")
        self.assertFalse((base / install.MARKER).exists(), "the marker goes")
        self.assertFalse(registry.stage_dir("partner").exists())
        self.assertEqual(self.content("partner") & {"addons/ledger/.env", "addons/ledger/secrets/token"}, set())

    def test_a_restart_in_between_changes_nothing(self):
        self.removed_with_settings()
        install.recover()
        base = Path(paths.ROOT) / ".lab/catalogs/partner"
        self.assertTrue((base / "addons/ledger/.env").is_file(), "the sweeper leaves a .kept folder alone")
        self.assertTrue((base / install.MARKER).is_file())
        self.add("partner")
        self.assertEqual((base / "addons/ledger/.env").read_text(encoding="utf-8"), "SECRET=1\n")

    def test_the_sweeper_renames_a_folder_with_content_and_settings_and_the_next_add_restores_the_settings(self):
        self.add("partner")
        self.machine("partner")
        (Path(paths.ROOT) / ".lab/catalogs.json").unlink()   # the person deleted the file by hand: a first run
        install.recover()
        renamed = [p for p in (Path(paths.ROOT) / ".lab/catalogs").iterdir() if p.name.startswith(".removed-partner-")]
        self.assertEqual(len(renamed), 1)
        self.assertFalse(registry.install_dir("partner").exists())
        self.assertEqual((renamed[0] / "addons/ledger/.env").read_text(encoding="utf-8"), "SECRET=1\n")
        self.add("partner")
        base = Path(paths.ROOT) / ".lab/catalogs/partner"
        self.intact("partner")
        self.assertEqual((base / "addons/ledger/.env").read_text(encoding="utf-8"), "SECRET=1\n")
        self.assertEqual((base / "addons/ledger/.state/created.json").read_text(encoding="utf-8"), "{}\n")
        self.assertFalse(renamed[0].exists(), "nothing of the machine is left in it, so it goes")

    def test_a_folder_a_remove_left_and_one_the_sweeper_renamed_are_both_merged(self):
        self.add("partner")
        self.machine("partner", **{".env": "OLD=1\n", "notes.txt": "kept\n"})
        (Path(paths.ROOT) / ".lab/catalogs.json").unlink()
        install.recover()   # renamed
        self.add("partner")
        install.remove("partner", keep_settings=True)   # .kept, and the .env that came back
        base = Path(paths.ROOT) / ".lab/catalogs/partner"
        (base / "addons/ledger/.state").mkdir()
        (base / "addons/ledger/.state/created.json").write_text("{}\n", encoding="utf-8")
        aside = Path(paths.ROOT) / ".lab/catalogs/.removed-partner-20200101T000000Z"
        (aside / "addons/ledger").mkdir(parents=True)
        (aside / "addons/ledger/secrets").mkdir()
        (aside / "addons/ledger/secrets/token").write_text("t\n", encoding="utf-8")
        self.add("partner")
        self.intact("partner")
        self.assertEqual((base / "addons/ledger/secrets/token").read_text(encoding="utf-8"), "t\n")
        self.assertEqual((base / "addons/ledger/.state/created.json").read_text(encoding="utf-8"), "{}\n")
        self.assertEqual((base / "addons/ledger/.env").read_text(encoding="utf-8"), "OLD=1\n")

    def test_a_conflict_stays_in_the_removed_folder(self):
        self.add("partner")
        self.machine("partner")
        (Path(paths.ROOT) / ".lab/catalogs.json").unlink()
        install.recover()
        renamed = next(p for p in (Path(paths.ROOT) / ".lab/catalogs").iterdir() if p.name.startswith(".removed-partner-"))
        staged, address, roots = self.prepare("partner")
        # the staged install is renamed first, then a file the person already made there blocks one move
        original = install._restore

        def blocked(ns, root):
            (Path(root) / "addons/ledger").mkdir(parents=True, exist_ok=True)
            (Path(root) / "addons/ledger/.env").write_text("NEW=1\n", encoding="utf-8")
            return original(ns, root)
        self.patch(install, "_restore", blocked)
        install.commit_add(staged, address, "main", "public", roots, meta_of("partner"))
        base = Path(paths.ROOT) / ".lab/catalogs/partner"
        self.assertEqual((base / "addons/ledger/.env").read_text(encoding="utf-8"), "NEW=1\n")
        self.assertEqual((renamed / "addons/ledger/.env").read_text(encoding="utf-8"), "SECRET=1\n")
        self.assertEqual((base / "addons/ledger/secrets/token").read_text(encoding="utf-8"), "t\n")

    def test_a_folder_with_files_nobody_vouches_for_is_set_aside_not_overwritten(self):
        base = Path(paths.ROOT) / ".lab/catalogs/partner"
        (base / "addons/ledger").mkdir(parents=True)
        (base / "addons/ledger/compose.yml").write_text("services: {mine: {}}\n", encoding="utf-8")
        (base / "addons/ledger/.env").write_text("KEEP=1\n", encoding="utf-8")
        self.add("partner")
        self.intact("partner")
        aside = [p for p in (Path(paths.ROOT) / ".lab/catalogs").iterdir() if p.name.startswith(".removed-partner-")]
        self.assertEqual((base / "addons/ledger/.env").read_text(encoding="utf-8"), "KEEP=1\n", "the settings came back")
        self.assertEqual(len(aside), 1, "and the person's own file is not deleted unasked: it waits where it was")
        self.assertEqual((aside[0] / "addons/ledger/compose.yml").read_text(encoding="utf-8"), "services: {mine: {}}\n")
        self.assertTrue((aside[0] / install.MARKER).is_file(), "nothing in a folder no manifest vouches for is judged by name")
        self.assertEqual(self.tree("partner")["addons/ledger/compose.yml"][1],
                         (fixtures.CATALOGS / "partner/addons/ledger/compose.yml").read_bytes())

    def test_a_machine_file_where_a_content_file_goes_sends_the_folder_aside(self):
        self.add("partner")
        install.remove("partner", keep_settings=True)
        base = Path(paths.ROOT) / ".lab/catalogs/partner"
        base.mkdir(exist_ok=True)
        (base / "addons/ledger").mkdir(parents=True, exist_ok=True)
        (base / "addons/ledger/compose.yml").write_text("theirs\n", encoding="utf-8")
        (base / install.MARKER).write_text("", encoding="utf-8")
        self.add("partner")
        self.assertEqual(self.tree("partner")["addons/ledger/compose.yml"][1], (fixtures.CATALOGS / "partner/addons/ledger/compose.yml").read_bytes())
        aside = [p for p in (Path(paths.ROOT) / ".lab/catalogs").iterdir() if p.name.startswith(".removed-partner-")]
        self.assertEqual(len(aside), 1)
        self.assertEqual((aside[0] / "addons/ledger/compose.yml").read_text(encoding="utf-8"), "theirs\n")

    def test_a_link_or_a_file_at_the_name_is_never_replaced(self):
        base = Path(paths.ROOT) / ".lab/catalogs"
        (base).mkdir(parents=True, exist_ok=True)
        (base / "partner").write_text("a file", encoding="utf-8")
        staged, address, roots = self.prepare("partner")
        with self.assertRaises(registry.CatalogError) as raised:
            install.commit_add(staged, address, "main", "public", roots, meta_of("partner"))
        self.assertEqual(raised.exception.code, "name_taken")
        self.assertEqual((base / "partner").read_text(encoding="utf-8"), "a file")


class Unvouched(Base):
    """A folder that holds no manifest says nothing of what is content in it, and a name the patterns do not know
    (`data/db.sqlite`, `certs/ca.txt`) must never read as content that a restore may delete. Every way a folder loses its
    manifest or its marker, and what is in it afterwards."""

    def setUp(self):
        super().setUp()
        self.catalogs = Path(paths.ROOT) / ".lab/catalogs"
        self.base = self.catalogs / "partner"

    def asides(self):
        return sorted(p for p in self.catalogs.iterdir() if p.name.startswith(".removed-partner-"))

    def odd(self, folder):
        for rel, text in {"addons/ledger/data/db.sqlite": "rows\n", "addons/ledger/certs/ca.txt": "ca\n",
                          "addons/ledger/.env": "SECRET=1\n"}.items():
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_text(text, encoding="utf-8")

    def settled(self):
        self.add("partner")
        self.intact("partner")
        for rel, text in {"data/db.sqlite": "rows\n", "certs/ca.txt": "ca\n", ".env": "SECRET=1\n"}.items():
            self.assertEqual((self.base / "addons/ledger" / rel).read_text(encoding="utf-8"), text, rel)

    def test_a_folder_with_no_manifest_is_marked_before_the_sweeper_sets_it_aside(self):
        self.odd(self.base)
        (self.base / "addons/ledger/compose.yml").write_text("half\n", encoding="utf-8")   # content a merge had written
        self.assertIn(("renamed", "partner"), install.sweep())
        (aside,) = self.asides()
        self.assertTrue((aside / install.MARKER).is_file())
        self.assertFalse(self.base.exists())
        self.settled()

    def test_a_stop_in_a_merge_into_a_kept_folder_loses_no_setting_whatever_it_is_called(self):
        self.add("partner")
        self.machine("partner")
        install.remove("partner", keep_settings=True)
        staged, address, roots = self.prepare("partner")
        self.patch(install, "STEP", stop_at("add:file"))
        with self.assertRaises(Crash):
            install.commit_add(staged, address, "main", "public", roots, meta_of("partner"))
        self.patch(install, "STEP", None)
        install.recover()
        self.assertEqual(paths.ids("addon"), [], "a half-made install is not seen")
        self.settled()
        for rel, text in ODD.items():
            self.assertEqual((self.base / "addons/ledger" / rel).read_text(encoding="utf-8"), text, rel)

    def test_a_stop_in_a_remove_between_the_manifest_and_the_marker_loses_no_setting(self):
        self.add("partner")
        made = self.machine("partner")
        self.patch(install, "_vacate", mock.Mock(side_effect=Crash("after the marker")))
        with self.assertRaises(Crash):
            install.remove("partner", keep_settings=True)
        self.patch(install, "_vacate", REAL_VACATE)
        self.assertTrue((self.base / install.MARKER).is_file(), "the marker is written before the content goes")
        self.assertIn(("emptied", "partner"), install.sweep(), "a start finishes the Remove")
        self.assertFalse((self.base / "addons/ledger/compose.yml").exists())
        self.assertEqual(sorted(p.relative_to(self.base).as_posix() for p in self.base.rglob("*") if p.is_file()),
                         sorted([install.MARKER, *made]))
        self.settled()

    def test_a_stop_at_the_step_between_the_marker_and_the_content_going_is_finished_by_the_sweeper(self):
        """The step `remove:vacate` is the window the line above simulates by patching `_vacate`: the marker is written, the
        manifest and the content are still there, and a start empties the folder and loses no setting."""
        self.add("partner")
        made = self.machine("partner")
        self.patch(install, "STEP", stop_at("remove:vacate"))
        with self.assertRaises(Crash):
            install.remove("partner", keep_settings=True)
        self.patch(install, "STEP", None)
        self.assertTrue((self.base / install.MARKER).is_file(), "marked")
        self.assertTrue((self.base / paths.MANIFEST).is_file(), "the manifest has not gone")
        self.assertTrue((self.base / "addons/ledger/compose.yml").is_file(), "nor the content")
        self.assertEqual(registry.entry("partner"), None, "the catalog is out of the list: this is a Remove that was committed")
        self.assertIn(("emptied", "partner"), install.sweep())
        self.assertFalse((self.base / "addons/ledger/compose.yml").exists())
        self.assertEqual(sorted(p.relative_to(self.base).as_posix() for p in self.base.rglob("*") if p.is_file()),
                         sorted([install.MARKER, *made]))
        self.assertEqual(install.sweep(), [], "a second start changes nothing")
        self.settled()

    def test_a_removed_folder_that_a_person_made_is_not_judged_by_names_either(self):
        self.odd(self.catalogs / ".removed-partner-20200101T000000Z")
        self.settled()
        self.assertEqual(self.asides(), [], "and goes once nothing of the machine's is left in it")

    def test_a_removed_folder_keeps_what_cannot_be_put_back(self):
        aside = self.catalogs / ".removed-partner-20200101T000000Z"
        self.odd(aside)
        (aside / "addons/ledger/compose.yml").write_text("services: {mine: {}}\n", encoding="utf-8")
        self.settled()
        (kept,) = self.asides()
        self.assertEqual((kept / "addons/ledger/compose.yml").read_text(encoding="utf-8"), "services: {mine: {}}\n")
        self.assertEqual(self.tree("partner")["addons/ledger/compose.yml"][1],
                         (fixtures.CATALOGS / "partner/addons/ledger/compose.yml").read_bytes())

    @unittest.skipIf(os.geteuid() == 0, "root writes where it likes")
    def test_a_removed_folder_the_lab_cannot_mark_is_left_whole_and_the_add_goes_on(self):
        aside = self.catalogs / ".removed-partner-20200101T000000Z"
        self.odd(aside)
        aside.chmod(0o555)
        self.addCleanup(aside.chmod, 0o755)
        self.add("partner")
        self.intact("partner")
        self.assertEqual((aside / "addons/ledger/data/db.sqlite").read_text(encoding="utf-8"), "rows\n")
        self.assertFalse((self.base / "addons/ledger/data").exists(), "nothing is taken out of a folder that is not marked")

    def test_a_download_that_is_undone_leaves_the_files_it_cannot_vouch_for(self):
        remote = self.remote("partner")
        fixtures.default(self, "partner", url=remote.url)
        registry.write_config({"schema": 1, "catalogs": [{"id": "partner", "kind": "git", "url": remote.url, "branch": "main",
                                                           "auto": True, "added": "2026-10-16T09:00:00Z"}]})
        registry.set_lock("partner", audience="public", pending={"op": "add", "from": None, "to": remote.head()})
        self.odd(self.base)
        (self.base / "addons/ledger/compose.yml").write_text("half\n", encoding="utf-8")
        install.recover()
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["pending"], held["error"]), (None, None, None))
        self.assertEqual((self.base / "addons/ledger/data/db.sqlite").read_text(encoding="utf-8"), "rows\n")
        self.assertTrue((self.base / install.MARKER).is_file(), "nothing in it is judged by name from then on")
        staged, address, roots = self.prepare("partner")
        install.commit_add(staged, address, "main", "public", roots, meta_of("partner"))
        self.intact("partner")
        self.assertEqual((self.base / "addons/ledger/data/db.sqlite").read_text(encoding="utf-8"), "rows\n")
        self.assertEqual((self.base / "addons/ledger/certs/ca.txt").read_text(encoding="utf-8"), "ca\n")

    def test_a_remove_of_an_install_with_no_manifest_keeps_everything_it_cannot_vouch_for(self):
        self.add("partner")
        self.machine("partner", **{"data/db.sqlite": "rows\n"})
        (self.base / paths.MANIFEST).unlink()
        install.remove("partner", keep_settings=True)
        self.assertEqual((self.base / "addons/ledger/data/db.sqlite").read_text(encoding="utf-8"), "rows\n")
        self.assertTrue((self.base / install.MARKER).is_file())

    def test_a_marker_does_not_hide_the_manifest_of_a_folder_that_has_both(self):
        self.add("partner")
        self.machine("partner")
        install._mark(self.base)
        self.assertEqual(install._machine("partner", self.base), TOPS, "content is what the manifest lists")


class Downloading(Base):
    """The first download of a listed catalog: its entry exists already, so the lock is the commit point."""

    def listed(self, ns="partner"):
        """The tracked file lists `ns` at the address of its remote, the entry exists, nothing is downloaded."""
        remote = self.remote(ns)
        fixtures.default(self, ns, url=remote.url)
        registry.write_config({"schema": 1, "catalogs": [{"id": ns, "kind": "git", "url": remote.url, "branch": "main",
                                                           "auto": True, "added": "2026-10-16T09:00:00Z"}]})
        registry.set_lock(ns, audience="public")

    def test_the_entry_is_kept_and_the_lock_commits(self):
        self.listed("partner")
        before = registry.entry("partner")
        staged, address, roots = self.prepare("partner")
        install.commit_add(staged, address, "main", "public", roots, meta_of("partner"))
        self.assertEqual(registry.entry("partner"), before)
        self.assertEqual(len(registry.entries()), 1)
        self.intact("partner")

    def test_a_store_fetched_into_its_own_place_is_not_moved(self):
        self.listed("partner")
        address = gitx.parse_address(self.remotes["partner"].url)
        store_path = registry.store_dir("partner")
        tip = bare.fetch(address, None, store_path)
        kept, ignored = bare.selected(bare.listing(store_path, tip))
        staged = install.stage(address, "partner", store_path, tip, kept, ignored)
        install.commit_add(staged, address, "main", "public", bare.roots(store_path, tip), meta_of("partner"))
        self.intact("partner")
        self.assertEqual(bare.resolve(store_path, "pin"), tip)

    def test_a_refused_adoption_does_not_make_a_finished_download_damaged(self):
        self.listed("partner")
        staged, address, roots = self.prepare("partner")
        self.patch(install, "STEP", stop_at("add:placed"))
        with self.assertRaises(Crash):
            install.commit_add(staged, address, "main", "public", roots, meta_of("partner"))
        self.patch(install, "STEP", None)
        self.patch(install, "adopt", mock.Mock(side_effect=moves.Refused("a held folder is a link")))
        install.recover()
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["pending"], held["error"]), (staged.commit, None, None),
                         "the install and the pin were whole: the settings that wait are tried again at the next start")
        self.intact("partner")

    def test_an_entry_that_has_a_pin_is_already_added(self):
        self.add("partner")
        staged, address, roots = self.prepare("partner")
        with self.assertRaises(registry.CatalogError) as raised:
            install.commit_add(staged, address, "main", "public", roots, meta_of("partner"))
        self.assertEqual(raised.exception.code, "already_added")

    def test_a_reserved_listed_name_is_accepted_at_its_listed_address_only(self):
        remote = self.remote("partner")
        row = {"id": "gravitee", "name": "Base", "description": "", "url": remote.url, "branch": "main"}
        self.patch(install.defaults, "read", lambda: [row])
        staged, address, roots = self.prepare("partner", store_ns="gravitee")
        install.commit_add(staged, address, "main", "public", roots, meta_of("partner", id="gravitee"))
        self.assertEqual(registry.entry("gravitee")["kind"], "git")
        self.assertTrue(registry.install_dir("gravitee").is_dir())
        self.patch(install.defaults, "read", lambda: [{**row, "url": "https://example.invalid/other.git"}])
        install.remove("gravitee", keep_settings=False)
        staged, address, roots = self.prepare("partner", store_ns="gravitee")
        with self.assertRaises(registry.CatalogError) as raised:
            install.commit_add(staged, address, "main", "public", roots, meta_of("partner", id="gravitee"))
        self.assertEqual(raised.exception.code, "name_reserved")


# ---------------------------------------------------------------------------------------------------------------------
# Update and Go back
# ---------------------------------------------------------------------------------------------------------------------

LEDGER = "addons/ledger"
AUDIT = {"addons/audit/compose.yml": "x-gravitee: {kind: addon, name: Audit}\nservices: {}\n",
         "addons/audit/README.md": "Audit\n"}


class Updating(Base):

    def setUp(self):
        super().setUp()
        self.installed("partner")
        self.address = gitx.parse_address(self.remote("partner").url)
        self.first = self.remote("partner").head()

    def advance(self, files=None, remove=(), message="Update the catalog"):
        """A new commit on the remote, fetched into the store of the catalog: answers it."""
        commit = self.remotes["partner"].commit(files, message, remove)
        bare.fetch(self.address, "main", registry.store_dir("partner"))
        registry.set_lock("partner", tip=commit)   # what a check does after its fetch
        return commit

    def version(self, text):
        """The catalog.yml of the fixture with another version."""
        return (fixtures.CATALOGS / "partner/catalog.yml").read_text(encoding="utf-8").replace("2.0.0", text)

    def apply(self, target, **how):
        plan = install.plan_update("partner", target, how.pop("context", None))
        install.apply_update("partner", plan, **how)
        return plan

    def test_a_version_written_for_a_level_this_lab_does_not_read_is_refused_and_nothing_is_left_staged(self):
        """`pick` takes a version whose level its tolerant reading could not be sure of for one this Lab may read; the strict
        reading of the staged catalog.yml says, here, when the caller gives the level of this Lab."""
        for level, contract, code in ((99, {"contract": 1, "contract_min": 1}, "lab_new"),
                                      (1, {"contract": 3, "contract_min": 2}, "lab_old"),
                                      (0, {"contract": 1, "contract_min": 1}, "lab_old")):
            with self.subTest(level=level, contract=contract):
                target = self.advance({"catalog.yml": self.version("2.1.0").replace("lab: 1", f"lab: {level}"), **AUDIT})
                with self.assertRaises(registry.CatalogError) as raised:
                    install.plan_update("partner", target, {"contract": contract})
                self.assertEqual(raised.exception.code, code)
                self.assertFalse(registry.stage_dir("partner").exists(), "what was refused is not left to be installed")
                self.assertEqual(self.content("partner"), {"catalog.yml", "addons/ledger/README.md", "addons/ledger/compose.yml"})
        target = self.advance({"catalog.yml": self.version("2.2.0").replace("lab: 1", "lab: 2"), **AUDIT})
        plan = install.plan_update("partner", target, {"contract": {"contract": 2, "contract_min": 1}})
        self.assertEqual((plan["lab"], plan["version"]), (2, "2.2.0"), "a Lab that reads the level takes it")
        self.assertEqual(install.plan_update("partner", target)["lab"], 2, "and one whose level the caller gave not is not asked")

    def test_a_go_back_to_a_version_this_lab_no_longer_reads_is_refused_as_well(self):
        target = self.advance({"catalog.yml": self.version("2.1.0").replace("lab: 1", "lab: 2"), **AUDIT})
        self.apply(target, context={"contract": {"contract": 2, "contract_min": 1}})
        with self.assertRaises(registry.CatalogError) as raised:
            install.plan_update("partner", self.first, {"contract": {"contract": 2, "contract_min": 2}})
        self.assertEqual(raised.exception.code, "lab_old")
        self.assertEqual(install.plan_update("partner", self.first, {"contract": {"contract": 2, "contract_min": 1}})["op"], "undo")

    def test_a_commit_that_changes_nothing_installable_is_up_to_date(self):
        target = self.advance({"README.md": "Only the readme of the repository\n", "docs/notes.md": "x\n"})
        self.assertEqual(install.plan_update("partner", target), {"upToDate": True})
        self.assertEqual(install.plan_update("partner", self.first), {"upToDate": True})
        self.assertFalse(registry.stage_dir("partner").exists(), "nothing was staged")

    def test_the_plan_says_what_is_added_changed_and_removed(self):
        target = self.advance({**AUDIT, "addons/ledger/README.md": "Changed\n", "catalog.yml": self.version("2.1.0")},
                              message="Add the audit add-on")
        plan = install.plan_update("partner", target)
        self.assertEqual((plan["op"], plan["from"], plan["to"], plan["ns"]), ("update", self.first, target, "partner"))
        self.assertEqual((plan["adds"], plan["updates"], plan["removes"]), (["addon:audit"], ["addon:ledger"], []))
        self.assertEqual(plan["files"], {"add": ["addons/audit/README.md", "addons/audit/compose.yml"],
                                         "change": ["addons/ledger/README.md", "catalog.yml"], "remove": []})
        self.assertEqual((plan["name"], plan["version"], plan["lab"]), ("Partner fixture", "2.1.0", 1))
        self.assertEqual(plan["whatsnew"], ["Add the audit add-on"])
        self.assertEqual(plan["touched"], {"addon:audit": ["README.md", "compose.yml"], "addon:ledger": ["README.md"]})
        self.assertEqual((plan["rewritten"], plan["held"]), (False, []))
        self.assertEqual(plan["stage"], str(registry.stage_dir("partner")))
        self.assertEqual({entry.path for entry in plan["kept"]}, set(install._audit(plan["stage"], plan["kept"])))
        self.assertEqual(self.content("partner"), {"catalog.yml", "addons/ledger/README.md", "addons/ledger/compose.yml"},
                         "a plan changes nothing that is installed")

    def test_a_removed_unit_is_in_the_plan(self):
        target = self.advance(remove=[LEDGER])
        plan = install.plan_update("partner", target)
        self.assertEqual((plan["removes"], plan["adds"], plan["updates"]), (["addon:ledger"], [], []))
        self.assertEqual(plan["files"]["remove"], ["addons/ledger/README.md", "addons/ledger/compose.yml"])

    def test_an_update_replaces_what_changed_and_leaves_the_rest_alone(self):
        self.add("lab-new")
        target = self.advance({**AUDIT, "addons/ledger/README.md": "Changed\n", "catalog.yml": self.version("2.1.0")})
        before = {rel: (p.stat().st_ino, p.stat().st_mtime_ns) for rel, p in
                  ((r, Path(paths.ROOT) / ".lab/catalogs/partner" / r) for r in ("addons/ledger/compose.yml",))}
        other = self.tree("lab-new")
        self.apply(target)
        self.intact("partner")
        after = self.tree("partner")
        self.assertEqual(after["addons/ledger/README.md"][1], b"Changed\n")
        self.assertEqual(after["addons/audit/README.md"][1], b"Audit\n")
        for rel, mode_bytes in after.items():
            if rel != ".installed.json":
                self.assertEqual(mode_bytes[0], READ_ONLY, rel)
        for rel, (inode, mtime) in before.items():
            now = (Path(paths.ROOT) / ".lab/catalogs/partner" / rel).stat()
            self.assertEqual((now.st_ino, now.st_mtime_ns), (inode, mtime), f"{rel} did not change, so it is not rewritten")
        self.assertEqual(self.tree("lab-new"), other, "another catalog is not touched")
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["previous"], held["tip"]), (target, self.first, target))
        self.assertEqual((held["version"], held["pending"], held["error"]), ("2.1.0", None, None))
        self.assertEqual(registry.manifest("partner")["commit"], target)
        self.digests("partner")
        self.assertEqual(registry.units_of("partner")["addon"], ["audit", "ledger"])
        store_path = registry.store_dir("partner")
        self.assertEqual((bare.resolve(store_path, "pin"), bare.resolve(store_path, "previous")), (target, self.first))
        self.assertFalse(registry.stage_dir("partner").exists())

    def test_a_unit_the_update_drops_leaves_its_files_and_the_folders_they_were_in(self):
        self.machine("partner", **{".env": "SECRET=1\n"})
        target = self.advance(remove=[LEDGER])
        self.apply(target)
        base = Path(paths.ROOT) / ".lab/catalogs/partner"
        self.assertFalse((base / "addons/ledger/compose.yml").exists())
        self.assertFalse((base / "addons/ledger/README.md").exists())
        self.assertEqual((base / "addons/ledger/.env").read_text(encoding="utf-8"), "SECRET=1\n",
                         "a machine file is not deleted by an update")
        self.assertEqual(registry.units_of("partner")["addon"], [])

    def test_the_folders_a_dropped_unit_leaves_empty_go_with_it(self):
        target = self.advance(remove=[LEDGER])
        self.apply(target)
        self.assertFalse((Path(paths.ROOT) / ".lab/catalogs/partner/addons").exists())
        self.intact("partner")

    def test_machine_files_survive_an_update_in_the_unit_that_changes(self):
        made = self.machine("partner")
        inodes = {rel: (Path(paths.ROOT) / ".lab/catalogs/partner" / rel).stat().st_ino for rel in made}
        target = self.advance({"addons/ledger/README.md": "Changed\n"})
        self.apply(target)
        for rel, inode in inodes.items():
            self.assertEqual((Path(paths.ROOT) / ".lab/catalogs/partner" / rel).stat().st_ino, inode, rel)
        self.assertEqual(self.content("partner") & set(made), set())

    def test_a_file_that_is_not_listed_is_never_deleted_or_overwritten(self):
        base = Path(paths.ROOT) / ".lab/catalogs/partner"
        (base / "addons/ledger/notes.txt").write_text("mine\n", encoding="utf-8")
        target = self.advance({"addons/ledger/README.md": "Changed\n"})
        self.apply(target)
        self.assertEqual((base / "addons/ledger/notes.txt").read_text(encoding="utf-8"), "mine\n")

    def test_an_update_holds_the_project_lock_while_it_replaces_files_and_not_before_or_after(self):
        target = self.advance({**AUDIT, "addons/ledger/README.md": "Changed\n"})
        plan = install.plan_update("partner", target)
        watch = Watch(self, ns="partner")
        install.apply_update("partner", plan)
        steps = dict(zip(watch.labels, watch.depths))
        for label in ("update:apply", "update:file", "update:manifest"):
            self.assertGreater(steps[label], 0, f"{label} runs under the project lock")
        for label in ("update:pending", "update:lock", "update:refs", "update:adopt"):
            self.assertEqual(steps[label], 0, f"{label} does not")

    def test_the_project_lock_is_taken_while_the_catalogs_lock_is_held(self):
        target = self.advance({"addons/ledger/README.md": "Changed\n"})
        plan = install.plan_update("partner", target)
        seen = []

        @contextlib.contextmanager
        def exclusive():
            seen.append(self.held_elsewhere(registry.LOCK))
            yield
        self.patch(project, "exclusive", exclusive, create=True)
        install.apply_update("partner", plan)
        self.assertTrue(seen)
        self.assertTrue(all(seen), "the catalogs lock is held when the project lock is taken")

    def test_what_a_unit_that_is_on_would_lose_holds_the_update_back(self):
        target = self.advance({"addons/ledger/README.md": "Changed\n"})
        self.write(".lab/on.yml", "include:\n  - .lab/catalogs/partner/addons/ledger/compose.yml\n")
        plan = install.plan_update("partner", target)
        self.assertEqual(plan["held"], ["addon:ledger"])
        before = self.tree("partner")
        with self.assertRaises(registry.CatalogError) as raised:
            install.apply_update("partner", plan)
        self.assertEqual((raised.exception.code, raised.exception.status, raised.exception.message),
                         ("held", 409, "Turn off ledger first."))
        self.assertEqual(self.tree("partner"), before)
        self.assertIsNone(registry.lock_of("partner")["pending"], "nothing was started")
        (self.root / ".lab/on.yml").write_text("", encoding="utf-8")
        install.apply_update("partner", install.plan_update("partner", target))
        self.assertEqual(registry.lock_of("partner")["pin"], target)

    def test_a_unit_that_an_item_of_another_catalog_includes_is_on_when_that_item_is(self):
        self.add("fixture")
        self.write(".lab/on.yml", "include:\n  - .lab/catalogs/fixture/setups/pair.yml\n")
        self.assertEqual(install.on_units("fixture"), {"setup:pair", "addon:echo"})
        self.assertEqual(install.on_units("partner"), {"addon:ledger"}, "the setup of fixture includes the ledger")
        self.assertEqual(install.on_units("lab-new"), set())
        self.write(".lab/on.yml", "include:\n  - .lab/catalogs/fixture/addons/echo/compose.yml\n")
        self.assertEqual(install.on_units("partner"), set())
        target = self.advance({"addons/ledger/README.md": "Changed\n"})
        self.write(".lab/on.yml", "include:\n  - .lab/catalogs/fixture/setups/pair.yml\n")
        self.assertEqual(install.plan_update("partner", target)["held"], ["addon:ledger"])

    def test_what_the_caller_knows_to_be_up_holds_the_update_back_too(self):
        target = self.advance({"addons/ledger/README.md": "Changed\n"})
        asked = []
        plan = install.plan_update("partner", target, {"on": lambda: asked.append(1) or ["addon:ledger"]})
        self.assertEqual(plan["held"], ["addon:ledger"])
        with self.assertRaises(registry.CatalogError) as raised:
            install.apply_update("partner", plan)
        self.assertEqual(raised.exception.code, "held")
        self.assertGreaterEqual(len(asked), 2, "asked again when the update is applied")

    def test_a_unit_that_does_not_change_is_not_held_by_being_on(self):
        target = self.advance(AUDIT)
        self.write(".lab/on.yml", "include:\n  - .lab/catalogs/partner/addons/ledger/compose.yml\n")
        plan = install.plan_update("partner", target)
        self.assertEqual((plan["held"], plan["adds"]), ([], ["addon:audit"]))
        install.apply_update("partner", plan)
        self.assertEqual(registry.lock_of("partner")["pin"], target)

    def test_a_plan_that_starts_from_another_commit_is_expired(self):
        target = self.advance({"addons/ledger/README.md": "Changed\n"})
        plan = install.plan_update("partner", target)
        install.apply_update("partner", plan)
        with self.assertRaises(registry.CatalogError) as raised:
            install.apply_update("partner", plan)
        self.assertEqual((raised.exception.code, raised.exception.message),
                         ("ticket_expired", "This took too long. Start again."))
        self.intact("partner")

    def test_a_stage_changed_after_the_plan_is_not_what_is_installed(self):
        target = self.advance({"addons/ledger/README.md": "Changed\n"})
        plan = install.plan_update("partner", target)
        evil = Path(plan["stage"]) / "addons/ledger/compose.yml"
        evil.chmod(0o644)
        evil.write_text("services: {evil: {privileged: true}}\n", encoding="utf-8")
        (Path(plan["stage"]) / "addons/ledger/payload.sh").write_text("rm -rf\n", encoding="utf-8")
        install.apply_update("partner", plan)
        self.intact("partner")
        now = self.tree("partner")
        self.assertNotIn("addons/ledger/payload.sh", now)
        self.assertEqual(now["addons/ledger/compose.yml"][1], (fixtures.CATALOGS / "partner" / LEDGER / "compose.yml").read_bytes())

    def test_a_rewritten_history_is_not_applied_unless_confirmed(self):
        target = gitfixture.force_push_over(self.remotes["partner"].bare, {
            **{path: (fixtures.CATALOGS / "partner" / path).read_text(encoding="utf-8")
               for path in ("catalog.yml", "addons/ledger/compose.yml")}, "addons/ledger/README.md": "Other root\n"})
        bare.fetch(self.address, "main", registry.store_dir("partner"))
        plan = install.plan_update("partner", target)
        self.assertTrue(plan["rewritten"])
        with self.assertRaises(registry.CatalogError) as raised:
            install.apply_update("partner", plan)
        self.assertEqual((raised.exception.code, raised.exception.status, raised.exception.message),
                         ("history_changed", 409, "This catalog was rewritten or moved. Confirm to update anyway."))
        self.assertEqual(registry.lock_of("partner")["pin"], self.first)
        self.assertIsNone(registry.lock_of("partner")["pending"])
        install.apply_update("partner", install.plan_update("partner", target), confirm_history=True)
        self.assertEqual(registry.lock_of("partner")["pin"], target)
        self.intact("partner")

    def test_the_same_files_on_a_rewritten_history_are_news_until_the_pin_moves_to_it(self):
        root = fixtures.CATALOGS / "partner"
        files = {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8") for path in sorted(root.rglob("*"))
                 if path.is_file()}
        target = gitfixture.force_push_over(self.remotes["partner"].bare, files)
        bare.fetch(self.address, "main", registry.store_dir("partner"))
        plan = install.plan_update("partner", target)
        self.assertNotEqual(plan, {"upToDate": True})
        self.assertEqual((plan["rewritten"], plan["adds"], plan["updates"], plan["removes"]), (True, [], [], []))
        install.apply_update("partner", plan, confirm_history=True)
        self.assertEqual(registry.lock_of("partner")["pin"], target)
        self.intact("partner")
        self.assertEqual(install.plan_update("partner", target), {"upToDate": True})
        later = self.advance({"addons/ledger/README.md": "Changed\n"})
        self.assertFalse(install.plan_update("partner", later)["rewritten"], "the pin is on the new history now")

    def test_a_rewrite_the_engine_vouches_for_is_not_flagged(self):
        target = gitfixture.force_push_over(self.remotes["partner"].bare, {**{
            path: (fixtures.CATALOGS / "partner" / path).read_text(encoding="utf-8")
            for path in ("catalog.yml", "addons/ledger/compose.yml")}, "addons/ledger/README.md": "Other root\n"})
        bare.fetch(self.address, "main", registry.store_dir("partner"))
        roots = bare.roots(registry.store_dir("partner"), target)
        plan = install.plan_update("partner", target, {"vouched": roots})
        self.assertFalse(plan["rewritten"])
        self.assertTrue(install.plan_update("partner", target, {"vouched": ["a" * 40]})["rewritten"])
        self.assertTrue(install.plan_update("partner", target, {"vouched": []})["rewritten"])

    def test_a_commit_the_store_does_not_hold_is_the_damage_a_fetch_repairs(self):
        with self.assertRaises(registry.CatalogError) as raised:
            install.plan_update("partner", "a" * 40)
        self.assertEqual(raised.exception.code, "fsck")
        self.assertFalse(registry.stage_dir("partner").exists())

    def test_going_back_is_an_update_to_the_previous_commit_and_toggles(self):
        target = self.advance({"addons/ledger/README.md": "Changed\n"})
        self.apply(target)
        before = self.tree("partner")
        plan = install.plan_update("partner", self.first)
        self.assertEqual((plan["op"], plan["from"], plan["to"], plan["whatsnew"]), ("undo", target, self.first, []))
        self.assertFalse(plan["rewritten"], "going back is not a rewrite")
        install.apply_update("partner", plan)
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["previous"], held["tip"]), (self.first, target, target))
        self.intact("partner")
        self.digests("partner")
        self.assertEqual(self.tree("partner")["addons/ledger/README.md"][1],
                         (fixtures.CATALOGS / "partner" / LEDGER / "README.md").read_bytes())
        self.assertNotEqual(self.tree("partner"), before)
        again = install.plan_update("partner", target)
        self.assertEqual(again["op"], "undo", "the action toggles")
        install.apply_update("partner", again)
        self.assertEqual(self.tree("partner")["addons/ledger/README.md"][1], b"Changed\n")

    def test_going_back_removes_what_the_newer_version_added(self):
        target = self.advance(AUDIT)
        self.apply(target)
        self.assertTrue((Path(paths.ROOT) / ".lab/catalogs/partner/addons/audit/compose.yml").is_file())
        install.apply_update("partner", install.plan_update("partner", self.first))
        self.assertFalse((Path(paths.ROOT) / ".lab/catalogs/partner/addons/audit").exists())
        self.assertEqual(registry.units_of("partner")["addon"], ["ledger"])

    def test_going_back_across_a_rewrite_is_a_rewrite_of_the_roots(self):
        target = gitfixture.force_push_over(self.remotes["partner"].bare, {**{
            path: (fixtures.CATALOGS / "partner" / path).read_text(encoding="utf-8")
            for path in ("catalog.yml", "addons/ledger/compose.yml")}, "addons/ledger/README.md": "Other\n"})
        bare.fetch(self.address, "main", registry.store_dir("partner"))
        install.apply_update("partner", install.plan_update("partner", target), confirm_history=True)
        plan = install.plan_update("partner", self.first)
        self.assertEqual(plan["op"], "undo")
        self.assertTrue(plan["rewritten"], "the two commits are not one history")

    def rewrite(self):
        """The remote's history replaced by another with the same files and another root: the commit it ends at, fetched."""
        target = gitfixture.force_push_over(self.remotes["partner"].bare, {**{
            path: (fixtures.CATALOGS / "partner" / path).read_text(encoding="utf-8")
            for path in ("catalog.yml", "addons/ledger/compose.yml")}, "addons/ledger/README.md": "Other root\n"})
        bare.fetch(self.address, "main", registry.store_dir("partner"))
        registry.set_lock("partner", tip=target)
        return target

    def test_a_rewrite_the_person_confirmed_is_the_history_from_then_on(self):
        store = registry.store_dir("partner")
        before = registry.lock_of("partner")["roots"]
        target = self.rewrite()
        self.assertNotEqual(bare.roots(store, target), before)
        install.apply_update("partner", install.plan_update("partner", target), confirm_history=True)
        self.assertEqual(registry.lock_of("partner")["roots"], bare.roots(store, target), "the lock holds the new history")
        follow = self.advance({"addons/ledger/README.md": "A normal commit on the new history\n"})
        plan = install.plan_update("partner", follow)
        self.assertFalse(plan["rewritten"], "it was confirmed, so it is not asked again")
        install.apply_update("partner", plan)
        self.assertEqual(registry.lock_of("partner")["pin"], follow)
        self.intact("partner")

    def test_going_back_across_a_rewrite_takes_the_roots_of_the_commit_gone_back_to(self):
        store = registry.store_dir("partner")
        old_roots = registry.lock_of("partner")["roots"]
        target = self.rewrite()
        install.apply_update("partner", install.plan_update("partner", target), confirm_history=True)
        plan = install.plan_update("partner", self.first)
        self.assertTrue(plan["rewritten"])
        with self.assertRaises(registry.CatalogError) as raised:
            install.apply_update("partner", plan)
        self.assertEqual(raised.exception.code, "history_changed")
        self.assertNotEqual(registry.lock_of("partner")["roots"], old_roots, "nothing was applied")
        install.apply_update("partner", plan, confirm_history=True)
        self.assertEqual(registry.lock_of("partner")["roots"], old_roots)
        self.assertEqual(registry.lock_of("partner")["roots"], bare.roots(store, self.first))
        again = install.plan_update("partner", target)
        self.assertTrue(again["rewritten"], "and the way forward is a rewrite to confirm again")

    def test_a_repair_keeps_the_roots_and_a_commit_that_extends_the_history_keeps_them(self):
        before = registry.lock_of("partner")["roots"]
        install.apply_update("partner", install.plan_update("partner", self.advance({"addons/ledger/README.md": "More\n"})))
        install.repair("partner")
        self.assertEqual(registry.lock_of("partner")["roots"], before)

    def test_an_update_to_a_history_with_more_roots_than_a_lock_holds_is_still_applied(self):
        target = self.advance({"addons/ledger/README.md": "Changed\n"})
        self.patch(bare, "roots", lambda store, commit: [f"{number:040x}" for number in range(1, install.ROOTS_LIMIT + 2)])
        before = registry.lock_of("partner")["roots"]
        install.apply_update("partner", install.plan_update("partner", target), confirm_history=True)
        self.assertEqual(registry.lock_of("partner")["pin"], target)
        self.assertEqual(registry.lock_of("partner")["roots"], before, "what the lock cannot hold is not recorded")

    def test_a_file_nobody_listed_where_the_new_version_puts_one_is_not_written_over(self):
        base = Path(paths.ROOT) / ".lab/catalogs/partner"
        (base / LEDGER / "NOTES.md").write_text("mine, precious\n", encoding="utf-8")
        target = self.advance({"addons/ledger/NOTES.md": "from the catalog\n"})
        plan = install.plan_update("partner", target)
        self.assertEqual(plan["files"]["add"], ["addons/ledger/NOTES.md"])
        before = self.tree("partner")
        with self.assertRaises(registry.CatalogError) as raised:
            install.apply_update("partner", plan)
        self.assertEqual((raised.exception.code, raised.exception.status), ("damaged", 409))
        self.assertEqual(self.tree("partner"), before, "nothing was touched")
        self.assertIsNone(registry.lock_of("partner")["pending"], "nothing was started")
        self.assertEqual(registry.lock_of("partner")["pin"], self.first)
        (base / LEDGER / "NOTES.md").unlink()   # the person moves their file away: the update goes through
        install.apply_update("partner", install.plan_update("partner", target))
        self.assertEqual((base / LEDGER / "NOTES.md").read_text(encoding="utf-8"), "from the catalog\n")

    def test_a_link_nobody_listed_where_the_new_version_puts_a_file_is_not_replaced(self):
        base = Path(paths.ROOT) / ".lab/catalogs/partner"
        (self.root / "theirs.txt").write_text("theirs\n", encoding="utf-8")
        (base / LEDGER / "NOTES.md").symlink_to(self.root / "theirs.txt")
        target = self.advance({"addons/ledger/NOTES.md": "from the catalog\n"})
        with self.assertRaises(registry.CatalogError) as raised:
            self.apply(target)
        self.assertEqual(raised.exception.code, "damaged")
        self.assertTrue((base / LEDGER / "NOTES.md").is_symlink())
        self.assertEqual((self.root / "theirs.txt").read_text(encoding="utf-8"), "theirs\n")
        self.assertIsNone(registry.lock_of("partner")["pending"])

    def test_a_file_the_old_version_listed_and_the_new_one_changes_is_replaced(self):
        target = self.advance({"addons/ledger/README.md": "Changed\n"})
        self.apply(target)
        self.assertEqual(self.tree("partner")["addons/ledger/README.md"][1], b"Changed\n")

    def test_an_executable_bit_that_changes_is_a_change(self):
        target = self.advance({"addons/ledger/run.sh": gitfixture.Executable("#!/bin/sh\n")})
        self.apply(target)
        self.assertEqual(stat.S_IMODE((Path(paths.ROOT) / ".lab/catalogs/partner/addons/ledger/run.sh").stat().st_mode),
                         EXECUTABLE)
        target = self.advance({"addons/ledger/run.sh": "#!/bin/sh\n"})
        plan = install.plan_update("partner", target)
        self.assertEqual(plan["files"]["change"], ["addons/ledger/run.sh"])
        install.apply_update("partner", plan)
        self.assertEqual(stat.S_IMODE((Path(paths.ROOT) / ".lab/catalogs/partner/addons/ledger/run.sh").stat().st_mode),
                         READ_ONLY)

    def test_a_file_that_becomes_a_folder_and_a_folder_that_becomes_a_file_find_their_place_free(self):
        first = self.advance({"addons/ledger/data": "a file\n", "addons/ledger/docs/guide.md": "a folder\n"})
        self.apply(first)
        target = self.advance({"addons/ledger/data": None, "addons/ledger/data/one.txt": "now a folder\n",
                               "addons/ledger/docs/guide.md": None, "addons/ledger/docs": "now a file\n"})
        plan = install.plan_update("partner", target)
        self.assertEqual(plan["files"]["remove"], ["addons/ledger/data", "addons/ledger/docs/guide.md"])
        install.apply_update("partner", plan)
        self.intact("partner")
        base = Path(paths.ROOT) / ".lab/catalogs/partner/addons/ledger"
        self.assertEqual((base / "data/one.txt").read_text(encoding="utf-8"), "now a folder\n")
        self.assertEqual((base / "docs").read_text(encoding="utf-8"), "now a file\n")

    def test_the_machines_folder_where_the_catalog_now_has_a_file_stops_the_update_before_it_starts(self):
        data = Path(paths.ROOT) / ".lab/catalogs/partner/addons/ledger/data"
        (data / ".state").mkdir(parents=True)
        (data / ".state/created.json").write_text("{}\n", encoding="utf-8")
        target = self.advance({"addons/ledger/data": "now a file\n", "addons/ledger/README.md": "Changed\n"})
        plan = install.plan_update("partner", target)
        before = self.tree("partner")
        with self.assertRaises(registry.CatalogError) as raised:
            install.apply_update("partner", plan)
        self.assertEqual(raised.exception.code, "damaged")
        self.assertEqual(self.tree("partner"), before, "not one file was replaced")
        self.assertIsNone(registry.lock_of("partner")["pending"])
        self.assertEqual(registry.lock_of("partner")["pin"], self.first)

    def test_a_folder_that_holds_only_files_that_go_is_not_in_the_way(self):
        first = self.advance({"addons/ledger/docs/guide.md": "a folder\n"})
        self.apply(first)
        target = self.advance({"addons/ledger/docs/guide.md": None, "addons/ledger/docs": "now a file\n"})
        self.apply(target)
        self.intact("partner")

    def test_a_catalog_that_a_working_copy_stands_in_for_cannot_be_updated(self):
        target = self.advance({"addons/ledger/README.md": "Changed\n"})
        install.shadow("partner", str(self.folder_catalog("partner")))
        with self.assertRaises(registry.CatalogError) as raised:
            install.plan_update("partner", target)
        self.assertEqual((raised.exception.code, raised.exception.message), ("linked", install.LINKED))

    def test_a_damaged_install_is_repaired_before_it_is_updated(self):
        (Path(paths.ROOT) / ".lab/catalogs/partner/.installed.json").unlink()
        with self.assertRaises(registry.CatalogError) as raised:
            install.plan_update("partner", self.advance({"addons/ledger/README.md": "Changed\n"}))
        self.assertEqual(raised.exception.code, "damaged")

    def test_the_catalog_file_of_the_new_version_is_judged(self):
        target = self.advance({"catalog.yml": self.version("2.1.0").replace("id: partner", "id: someone-else")})
        with self.assertRaises(registry.CatalogError) as raised:
            install.plan_update("partner", target)
        self.assertEqual(raised.exception.code, "wrong_catalog")
        self.assertEqual(raised.exception.message, f"{self.address.repo or self.address.display} is not the catalog this "
                         "Lab expects. Run make update, then try again.")
        refused = mock.Mock(side_effect=registry.CatalogError("catalog_invalid", "catalog.yml: version is required.", 400))
        self.patch(manifest, "read_catalog", refused)
        with self.assertRaises(registry.CatalogError) as raised:
            install.plan_update("partner", self.advance({"addons/ledger/README.md": "Again\n"}))
        self.assertEqual(raised.exception.code, "catalog_invalid")

    def test_what_was_added_is_adopted_from_the_settings_that_wait(self):
        held = Path(paths.ROOT) / ".lab/held/addons/audit"
        held.mkdir(parents=True)
        (held / ".env").write_text("TOKEN=1\n", encoding="utf-8")
        self.patch(manifest, "read_catalog", lambda path, address=None, borrowed=(): {**parsed(path),
                                                                                       "moved_from": ["addon:audit"]})
        target = self.advance(AUDIT)
        self.apply(target)
        self.assertEqual((Path(paths.ROOT) / ".lab/catalogs/partner/addons/audit/.env").read_text(encoding="utf-8"), "TOKEN=1\n")
        self.assertFalse(held.exists())


class Repairing(Base):

    def setUp(self):
        super().setUp()
        self.installed("partner")
        self.base = Path(paths.ROOT) / ".lab/catalogs/partner"

    def test_a_changed_file_a_missing_file_and_a_loosened_mode_are_made_right(self):
        compose_file, readme = self.base / LEDGER / "compose.yml", self.base / LEDGER / "README.md"
        compose_file.chmod(0o644)
        compose_file.write_text("services: {evil: {privileged: true}}\n", encoding="utf-8")
        readme.unlink()
        (self.base / "catalog.yml").chmod(0o644)
        self.assertNotEqual(install.verify("partner", ["addon:ledger"]), [])
        install.repair("partner")
        self.intact("partner")
        self.assertEqual(stat.S_IMODE((self.base / "catalog.yml").stat().st_mode), READ_ONLY)
        self.assertEqual(readme.read_bytes(), (fixtures.CATALOGS / "partner" / LEDGER / "README.md").read_bytes())

    def test_a_link_in_the_place_of_a_file_is_replaced_not_followed(self):
        outside = self.root / "theirs.txt"
        outside.write_text("theirs", encoding="utf-8")
        readme = self.base / LEDGER / "README.md"
        readme.unlink()
        readme.symlink_to(outside)
        install.repair("partner")
        self.assertFalse(readme.is_symlink())
        self.assertEqual(outside.read_text(encoding="utf-8"), "theirs")
        self.intact("partner")

    def test_the_machine_files_and_the_files_nobody_listed_are_not_touched(self):
        made = self.machine("partner")
        (self.base / LEDGER / "notes.txt").write_text("mine\n", encoding="utf-8")
        before = {rel: (self.base / rel).read_bytes() for rel in made}
        (self.base / LEDGER / "README.md").unlink()
        install.repair("partner")
        for rel, data in before.items():
            self.assertEqual((self.base / rel).read_bytes(), data)
        self.assertEqual((self.base / LEDGER / "notes.txt").read_text(encoding="utf-8"), "mine\n")

    def test_a_manifest_that_is_gone_or_wrong_is_written_again(self):
        good = (self.base / ".installed.json").read_bytes()
        (self.base / ".installed.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(registry.manifest("partner"))
        install.repair("partner")
        self.assertEqual((self.base / ".installed.json").read_bytes(), good)
        (self.base / ".installed.json").unlink()
        install.repair("partner")
        self.assertEqual((self.base / ".installed.json").read_bytes(), good)

    def test_the_error_is_cleared_and_the_previous_version_is_kept(self):
        registry.set_lock("partner", error="damaged", previous="b" * 40)
        install.repair("partner")
        held = registry.lock_of("partner")
        self.assertEqual((held["error"], held["previous"], held["pending"]), (None, "b" * 40, None))

    def test_a_repair_is_idempotent(self):
        (self.base / LEDGER / "README.md").unlink()
        install.repair("partner")
        first = self.tree("partner")
        stamps = {rel: (self.base / rel).stat().st_mtime_ns for rel in first if rel != ".installed.json"}
        install.repair("partner")
        self.assertEqual(self.tree("partner"), first)
        self.assertEqual({rel: (self.base / rel).stat().st_mtime_ns for rel in stamps}, stamps, "nothing was rewritten")

    def test_an_object_the_store_lacks_is_fsck_and_the_install_is_as_it_was(self):
        shutil.rmtree(registry.store_dir("partner"))
        (self.base / LEDGER / "README.md").unlink()
        with self.assertRaises(registry.CatalogError) as raised:
            install.repair("partner")
        self.assertEqual(raised.exception.code, "fsck")
        self.assertFalse((self.base / LEDGER / "README.md").exists())
        self.assertIsNone(registry.lock_of("partner")["pending"])

    def test_a_folder_catalog_has_nothing_to_repair(self):
        folder = self.folder_catalog("lab-new")
        address = gitx.parse_address(str(folder))
        install.commit_add(install.stage(address, "lab-new", None, "", []), address, None, "private", [], meta_of("lab-new"))
        install.repair("lab-new")
        self.assertTrue(registry.install_dir("lab-new").is_symlink())

    def test_a_catalog_that_was_never_downloaded_has_nothing_to_repair(self):
        fixtures.declare(self, "lab-old")
        with self.assertRaises(ValueError):
            install.repair("lab-old")

    def test_a_working_copy_stands_in_so_there_is_nothing_to_repair(self):
        install.shadow("partner", str(self.folder_catalog("partner")))
        with self.assertRaises(registry.CatalogError) as raised:
            install.repair("partner")
        self.assertEqual(raised.exception.code, "linked")

    def test_a_pending_operation_that_a_failure_left_is_finished_first(self):
        registry.set_lock("partner", pending={"op": "update", "from": registry.lock_of("partner")["pin"],
                                              "to": registry.lock_of("partner")["pin"]})
        (self.base / LEDGER / "README.md").unlink()
        install.repair("partner")
        self.intact("partner")


class Wording(Case):
    """The lines of the catalogue of errors that other modules say too, by one public name each: the first start and the service of the
    Catalogs page say `wrong_catalog`, `name_taken` and the unit of a file by these, not by their own copies."""

    def test_wrong_catalog_names_the_repository_or_says_this_one(self):
        for called, line in (("example/demos", "example/demos is not the catalog this Lab expects. Run make update, then try again."),
                             (None, "This repository is not the catalog this Lab expects. Run make update, then try again."),
                             ("", "This repository is not the catalog this Lab expects. Run make update, then try again.")):
            with self.subTest(called=called):
                error = install.not_expected(called)
                self.assertEqual((error.code, error.status, error.message), ("wrong_catalog", 409, line))
        self.assertEqual(install.not_expected().message, install.not_expected(None).message)
        self.assertIsInstance(install.not_expected("x"), registry.CatalogError)

    def test_name_taken_names_the_catalog_and_is_a_conflict(self):
        error = install.taken("partner")
        self.assertEqual((error.code, error.status, error.detail), ("name_taken", 409, {"id": "partner"}))
        self.assertEqual(error.message, "Another catalog already uses the name partner. Remove it first, or ask the owner of this "
                         "one to rename it.")

    def test_the_unit_a_path_belongs_to_is_kind_and_name_and_a_path_of_no_unit_is_none(self):
        for path, key in (("addons/echo/compose.yml", "addon:echo"), ("addons/echo/deeper/file.txt", "addon:echo"),
                          ("playgrounds/shop/seed/apim/apis/shop.json", "playground:shop"), ("setups/pair.yml", "setup:pair"),
                          ("catalog.yml", None), ("README.md", None), ("addons/README.md", None), ("setups/pair.md", None),
                          ("docs/addons/echo/compose.yml", None), ("setups/", None), ("", None)):
            with self.subTest(path=path):
                self.assertEqual(install.unit_key(path), key)

    def test_the_private_names_are_gone_for_good(self):
        for name in ("_not_expected", "_taken", "_unit_key"):
            self.assertFalse(hasattr(install, name), name)


class Verifying(Base):

    def setUp(self):
        super().setUp()
        self.installed("fixture")
        self.base = Path(paths.ROOT) / ".lab/catalogs/fixture"

    def test_an_install_that_was_not_changed_verifies(self):
        self.assertEqual(install.verify("fixture", ["addon:echo", "playground:shop", "setup:pair"]), [])
        self.assertEqual(install.verify("fixture", []), [])

    def test_a_changed_a_missing_and_a_replaced_file_are_named(self):
        (self.base / "addons/echo/compose.yml").chmod(0o644)
        (self.base / "addons/echo/compose.yml").write_text("services: {}\n", encoding="utf-8")
        (self.base / "setups/pair.yml").unlink()
        readme = self.base / "playgrounds/shop/guide.md"
        readme.unlink()
        readme.symlink_to(self.root)
        self.assertEqual(install.verify("fixture", ["addon:echo", "playground:shop", "setup:pair"]),
                         ["addons/echo/compose.yml", "playgrounds/shop/guide.md", "setups/pair.yml"])

    def test_only_the_units_asked_for_are_read_and_the_catalog_file_always(self):
        (self.base / "addons/echo/compose.yml").chmod(0o644)
        (self.base / "addons/echo/compose.yml").write_text("services: {}\n", encoding="utf-8")
        self.assertEqual(install.verify("fixture", ["playground:shop"]), [])
        (self.base / "catalog.yml").chmod(0o644)
        (self.base / "catalog.yml").write_text("x: 1\n", encoding="utf-8")
        self.assertEqual(install.verify("fixture", ["playground:shop"]), ["catalog.yml"])

    def test_a_file_of_the_same_size_is_read_and_found_different(self):
        file = self.base / "addons/echo/compose.yml"
        data = file.read_bytes()
        file.chmod(0o644)
        file.write_bytes(data[:-1] + (b"X" if data[-1:] != b"X" else b"Y"))
        self.assertEqual(install.verify("fixture", ["addon:echo"]), ["addons/echo/compose.yml"])

    def test_a_catalog_with_no_manifest_the_pin_accepts_is_damaged(self):
        (self.base / ".installed.json").unlink()
        with self.assertRaises(registry.CatalogError) as raised:
            install.verify("fixture", ["addon:echo"])
        self.assertEqual((raised.exception.code, raised.exception.status), ("damaged", 409))

    def test_a_manifest_that_lists_a_path_out_of_the_install_is_no_reason_to_read_it(self):
        found = json.loads((self.base / ".installed.json").read_text(encoding="utf-8"))
        found["files"]["../../../../etc/passwd"] = {"mode": "100644", "oid": "a" * 40, "sha256": "0" * 64, "size": 1}
        found["files"]["addons/echo/odd"] = "not a record"
        (self.base / ".installed.json").write_text(json.dumps(found), encoding="utf-8")
        self.assertEqual(install.verify("fixture", ["addon:echo"]),
                         ["../../../../etc/passwd", "addons/echo/odd"])


# ---------------------------------------------------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------------------------------------------------

class Removing(Base):

    def setUp(self):
        super().setUp()
        self.installed("partner")
        self.add("lab-new")
        self.base = Path(paths.ROOT) / ".lab/catalogs/partner"

    def test_a_remove_that_keeps_the_settings_leaves_them_and_a_marker(self):
        made = self.machine("partner")
        answer = install.remove("partner", keep_settings=True)
        self.assertEqual(answer, {"warning": None})
        self.assertIsNone(registry.entry("partner"))
        self.assertNotIn("partner", registry.read_lock()["catalogs"])
        self.assertFalse(registry.store_dir("partner").exists())
        left = sorted(p.relative_to(self.base).as_posix() for p in self.base.rglob("*") if p.is_file())
        self.assertEqual(left, sorted([install.MARKER, *made]))
        self.assertEqual([one["id"] for one in registry.entries()], ["lab-new"])
        self.assertEqual(paths.ids("addon"), ["@lab-new/newer"], "the units of a removed catalog are gone")
        self.intact("lab-new")

    def test_the_default_is_to_keep_the_settings(self):
        self.machine("partner")
        install.remove("partner")
        self.assertTrue((self.base / install.MARKER).is_file())

    def test_a_remove_that_deletes_them_removes_the_folder(self):
        self.machine("partner")
        answer = install.remove("partner", keep_settings=False)
        self.assertEqual(answer, {"warning": None})
        self.assertFalse(self.base.exists())
        self.assertIsNone(registry.entry("partner"))
        self.intact("lab-new")

    def test_a_folder_with_no_settings_is_not_kept_for_nothing(self):
        install.remove("partner", keep_settings=True)
        self.assertFalse(self.base.exists())

    def test_files_nobody_listed_are_settings_too(self):
        (self.base / LEDGER / "notes.txt").write_text("mine\n", encoding="utf-8")
        install.remove("partner", keep_settings=True)
        self.assertEqual((self.base / LEDGER / "notes.txt").read_text(encoding="utf-8"), "mine\n")
        self.assertTrue((self.base / install.MARKER).is_file())
        self.assertFalse((self.base / LEDGER / "compose.yml").exists())

    def test_files_docker_made_that_cannot_be_deleted_are_reported_and_what_can_go_goes(self):
        if os.geteuid() == 0:
            self.skipTest("root can delete what the test makes undeletable")
        self.machine("partner")
        locked = self.base / LEDGER / ".state" / "root-owned"
        locked.mkdir(parents=True)
        (locked / "data").write_text("x", encoding="utf-8")
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)
        answer = install.remove("partner", keep_settings=False)
        self.assertEqual(answer["warning"], "Some files were created by Docker and could not be deleted. Remove the folder "
                                           ".lab/catalogs/partner with administrator rights.")
        self.assertTrue((locked / "data").is_file())
        self.assertFalse((self.base / LEDGER / "compose.yml").exists())
        self.assertFalse((self.base / LEDGER / ".env").exists())
        self.assertIsNone(registry.entry("partner"))
        self.assertTrue((self.base / install.MARKER).is_file(), "what is left waits for an Add, as the line says")

    def test_the_stars_of_the_units_go(self):
        starred.star(self.project(), "addon", "@partner/ledger", True)
        starred.star(self.project(), "addon", "@lab-new/newer", True)
        install.remove("partner")
        self.assertEqual(starred.read()["addon"], {"@lab-new/newer"})

    def project(self):
        return {"addons": {"@partner/ledger": {}, "@lab-new/newer": {}}, "setups": {}, "playgrounds": {}}

    def test_a_remove_is_idempotent(self):
        self.machine("partner")
        install.remove("partner")
        before = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))
        self.assertEqual(install.remove("partner"), {"warning": None})
        self.assertEqual(sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")), before)

    def test_a_folder_catalog_loses_its_link_and_the_authors_folder_is_untouched(self):
        folder = self.folder_catalog("fixture", "mine")
        address = gitx.parse_address(str(folder))
        install.commit_add(install.stage(address, "mine", None, "", []), address, None, "private", [],
                           meta_of("fixture", id="mine"))
        before = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))
        self.assertTrue(registry.install_dir("mine").is_symlink())
        install.remove("mine", keep_settings=False)
        self.assertFalse(os.path.lexists(registry.install_dir("mine")))
        self.assertEqual(sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")), before)
        self.assertIsNone(registry.entry("mine"))
        self.assertNotIn("mine", registry.read_lock()["catalogs"])

    def test_a_catalog_that_a_working_copy_stands_in_for_cannot_be_removed(self):
        install.shadow("partner", str(self.folder_catalog("partner")))
        with self.assertRaises(registry.CatalogError) as raised:
            install.remove("partner")
        self.assertEqual(raised.exception.code, "linked")
        self.assertIsNotNone(registry.entry("partner"))

    def test_a_catalog_with_a_unit_that_is_on_is_not_removed(self):
        self.write(".lab/on.yml", "include:\n  - .lab/catalogs/partner/addons/ledger/compose.yml\n")
        before = self.tree("partner")
        with self.assertRaises(registry.CatalogError) as raised:
            install.remove("partner")
        self.assertEqual((raised.exception.code, raised.exception.message), ("held", "Turn off ledger first."))
        self.assertEqual(self.tree("partner"), before)
        self.assertIsNotNone(registry.entry("partner"))

    def test_a_catalog_with_a_unit_that_an_item_of_another_catalog_includes_is_not_removed_while_that_item_is_on(self):
        self.add("fixture")
        self.write(".lab/on.yml", "include:\n  - .lab/catalogs/fixture/setups/pair.yml\n")
        with self.assertRaises(registry.CatalogError) as raised:
            install.remove("partner")
        self.assertEqual(raised.exception.code, "held")
        self.write(".lab/on.yml", "")
        install.remove("partner")
        self.assertIsNone(registry.entry("partner"))

    def test_a_name_that_is_not_a_catalog_is_not_removed(self):
        for bad in ("local", "", "A", "../x", None, "a" * 40):
            with self.assertRaises(ValueError, msg=repr(bad)):
                install.remove(bad)
        self.assertTrue((Path(paths.ROOT) / ".lab/catalogs/partner").is_dir())

    def test_nothing_is_removed_for_a_name_the_unreadable_list_does_not_hold(self):
        (Path(paths.ROOT) / ".lab/catalogs.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(registry.CatalogError) as raised:
            install.remove("partner", keep_settings=False)
        self.assertEqual(raised.exception.code, "damaged")
        self.assertTrue((self.base / LEDGER / "compose.yml").is_file())

    def test_a_remove_that_stopped_after_its_commit_point_is_finished_by_the_next_start(self):
        self.machine("partner")
        watch = Watch(self)
        watch.stop = 1   # after the entry left catalogs.json
        with self.assertRaises(Crash):
            install.remove("partner", keep_settings=True)
        self.assertIsNone(registry.entry("partner"))
        self.assertTrue(registry.store_dir("partner").is_dir())
        install.STEP = None
        install.recover()
        self.nothing("partner")
        self.assertEqual(paths.ids("addon"), ["@lab-new/newer"])
        left = [p for p in (Path(paths.ROOT) / ".lab/catalogs").iterdir() if p.name.startswith("partner") or ".removed-partner" in p.name]
        self.assertEqual(len(left), 1)
        settings = left[0]
        self.assertEqual((settings / "addons/ledger/.env").read_text(encoding="utf-8"), "SECRET=1\n")


# ---------------------------------------------------------------------------------------------------------------------
# The sweeper, and what a start finishes
# ---------------------------------------------------------------------------------------------------------------------

DAY = 24 * 3600


class Sweeping(Base):

    def old(self, path, age=3 * DAY):
        past = time.time() - age
        os.utime(path, (past, past), follow_symlinks=False)
        return path

    def catalogs(self, *parts):
        return Path(paths.ROOT) / ".lab/catalogs" / Path(*parts)

    def test_a_stage_older_than_a_day_goes_and_a_younger_one_stays(self):
        old, young = self.catalogs(".stage-old-one"), self.catalogs(".stage-young-one")
        for folder in (old, young):
            folder.mkdir(parents=True)
            (folder / "f.txt").write_text("x", encoding="utf-8")
        self.old(old)
        done = install.sweep()
        self.assertFalse(old.exists())
        self.assertTrue(young.is_dir())
        self.assertIn(("swept", ".stage-old-one"), done)

    def test_a_stage_that_is_a_link_is_removed_as_a_link(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep", encoding="utf-8")
        link = self.catalogs(".stage-linked")
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside, target_is_directory=True)
        self.old(link)
        install.sweep()
        self.assertFalse(os.path.lexists(link))
        self.assertEqual([p.name for p in outside.iterdir()], ["keep.txt"])

    def test_a_store_being_added_that_is_older_than_a_day_goes(self):
        cache = Path(paths.ROOT) / ".lab/cache/git"
        for name in (".new-0a1b.git", ".new-2c3d.git", "partner.git"):
            (cache / name).mkdir(parents=True)
            (cache / name / "HEAD").write_text("x", encoding="utf-8")
        self.old(cache / ".new-0a1b.git")
        self.old(cache / "partner.git")
        install.sweep()
        self.assertFalse((cache / ".new-0a1b.git").exists())
        self.assertTrue((cache / ".new-2c3d.git").exists())
        self.assertTrue((cache / "partner.git").exists(), "a store of a catalog is not a store being added")

    def test_a_partial_file_goes(self):
        partial = self.catalogs("fixture.partial")
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_text("half", encoding="utf-8")
        self.old(partial)
        young = self.catalogs("other.partial")
        young.write_text("half", encoding="utf-8")
        install.sweep()
        self.assertFalse(partial.exists())
        self.assertTrue(young.exists())

    def test_a_share_folder_in_an_install_goes_and_one_in_an_authors_folder_does_not(self):
        self.add("partner")
        share = self.catalogs("partner", ".share-12ab")
        share.mkdir()
        (share / "x").write_text("x", encoding="utf-8")
        self.old(share)
        folder = self.folder_catalog("lab-new", "mine")
        address = gitx.parse_address(str(folder))
        install.commit_add(install.stage(address, "mine", None, "", []), address, None, "private", [],
                           meta_of("lab-new", id="mine"))
        theirs = folder / ".share-34cd"
        theirs.mkdir()
        self.old(theirs)
        install.sweep()
        self.assertFalse(share.exists())
        self.assertTrue(theirs.exists(), "the author's folder is never touched")

    def test_a_folder_with_the_marker_is_left_alone_for_ever(self):
        folder = self.catalogs("partner")
        (folder / "addons/ledger").mkdir(parents=True)
        (folder / "addons/ledger/.env").write_text("S=1\n", encoding="utf-8")
        (folder / install.MARKER).write_text("", encoding="utf-8")
        self.old(folder, 400 * DAY)
        self.assertEqual(install.sweep(), [])
        self.assertTrue((folder / "addons/ledger/.env").is_file())

    def test_the_settings_of_a_listed_catalog_wait_for_its_add(self):
        folder = self.catalogs("fixture")
        (folder / "addons/echo").mkdir(parents=True)
        (folder / "addons/echo/.env").write_text("S=1\n", encoding="utf-8")
        self.assertEqual(install.sweep(), [])
        self.assertTrue((folder / "addons/echo/.env").is_file())

    def test_the_settings_of_a_catalog_nobody_lists_are_renamed_not_deleted(self):
        folder = self.catalogs("partner")
        (folder / "addons/ledger").mkdir(parents=True)
        (folder / "addons/ledger/.env").write_text("S=1\n", encoding="utf-8")
        done = install.sweep()
        self.assertEqual(done, [("renamed", "partner")])
        self.assertFalse(folder.exists())
        (renamed,) = [p for p in self.catalogs().iterdir() if p.name.startswith(".removed-partner-")]
        self.assertEqual((renamed / "addons/ledger/.env").read_text(encoding="utf-8"), "S=1\n")

    def test_an_empty_folder_and_a_complete_install_with_no_entry_are_removed(self):
        empty = self.catalogs("lab-old")
        (empty / "addons/x").mkdir(parents=True)
        self.add("partner")
        registry.write_config({"schema": 1, "catalogs": []})
        done = install.sweep()
        self.assertEqual(sorted(done), [("removed", "lab-old"), ("removed", "partner")])
        self.assertFalse(empty.exists())
        self.assertFalse(self.catalogs("partner").exists())

    def test_a_complete_install_with_settings_and_no_entry_is_renamed(self):
        self.add("partner")
        self.machine("partner")
        registry.write_config({"schema": 1, "catalogs": []})
        self.assertEqual(install.sweep(), [("renamed", "partner")])
        (renamed,) = [p for p in self.catalogs().iterdir() if p.name.startswith(".removed-partner-")]
        self.assertTrue((renamed / "addons/ledger/.env").is_file())
        self.assertTrue((renamed / "addons/ledger/compose.yml").is_file(), "nothing is deleted when settings are there")

    def test_files_nobody_vouches_for_are_renamed_not_deleted(self):
        folder = self.catalogs("partner")
        (folder / "addons/ledger").mkdir(parents=True)
        (folder / "addons/ledger/compose.yml").write_text("mine\n", encoding="utf-8")
        self.assertEqual(install.sweep(), [("renamed", "partner")])
        (renamed,) = [p for p in self.catalogs().iterdir() if p.name.startswith(".removed-partner-")]
        self.assertEqual((renamed / "addons/ledger/compose.yml").read_text(encoding="utf-8"), "mine\n")

    def test_the_personal_area_a_link_and_the_installs_that_have_an_entry_are_never_swept(self):
        self.add("partner")
        local = self.catalogs("local", "addons", "mine", "compose.yml")
        local.parent.mkdir(parents=True)
        local.write_text("services: {}\n", encoding="utf-8")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "catalog.yml").write_text("x", encoding="utf-8")
        link = self.catalogs("stray")
        link.symlink_to(outside, target_is_directory=True)
        self.old(self.catalogs("partner"), 400 * DAY)
        self.assertEqual(install.sweep(), [])
        self.assertTrue(local.is_file())
        self.assertTrue(link.is_symlink() and (outside / "catalog.yml").is_file())
        self.intact("partner")

    def test_nothing_of_an_install_is_touched_while_the_list_of_catalogs_cannot_be_read(self):
        self.add("partner")
        self.machine("partner")
        stale = self.catalogs(".stage-old-one")
        stale.mkdir()
        self.old(stale)
        (Path(paths.ROOT) / ".lab/catalogs.json").write_text("{not json", encoding="utf-8")
        install.recover()
        self.assertTrue((self.catalogs("partner") / "addons/ledger/.env").is_file())
        self.assertTrue((self.catalogs("partner") / "addons/ledger/compose.yml").is_file())
        self.assertNotIn("partner", [p.name for p in self.catalogs().iterdir() if p.name.startswith(".removed")])
        self.assertIn("partner", registry.read_lock()["catalogs"], "its record is kept too")
        self.assertTrue(registry.store_dir("partner").is_dir())
        self.assertFalse(stale.exists(), "what only age judges still goes")

    def test_a_lock_entry_and_a_store_with_no_entry_go_and_the_others_stay(self):
        self.add("partner")
        self.add("lab-new")
        registry.write_config({"schema": 1, "catalogs": [registry.entry("lab-new")]})
        install.recover()
        self.nothing("partner")
        self.assertFalse(self.catalogs("partner").exists())
        self.intact("lab-new")
        self.assertTrue(registry.store_dir("lab-new").is_dir())

    def test_the_refs_of_a_store_are_written_again(self):
        self.add("partner")
        store_path = registry.store_dir("partner")
        commit = registry.lock_of("partner")["pin"]
        bare.set_ref(store_path, "pin", None)
        install.recover()
        self.assertEqual(bare.resolve(store_path, "pin"), commit)

    def test_a_marker_in_a_catalog_that_is_added_is_removed(self):
        self.add("partner")
        (self.catalogs("partner") / install.MARKER).write_text("", encoding="utf-8")
        install.recover()
        self.assertFalse((self.catalogs("partner") / install.MARKER).exists())

    def test_a_start_with_nothing_installed_does_nothing(self):
        def listing():
            return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")
                          if p.name != "catalogs.flock")   # the file the advisory lock is taken on
        before = listing()
        install.recover()
        install.recover()
        self.assertEqual(listing(), before)

    def test_a_start_changes_nothing_in_a_good_install(self):
        self.add("partner")
        self.add("fixture")
        self.machine("partner")
        before = {ns: self.tree(ns) for ns in ("partner", "fixture")}
        stamps = {p: p.stat().st_mtime_ns for p in (Path(paths.ROOT) / ".lab").rglob("*") if p.is_file()
                  and "cache" not in p.parts}
        install.recover()
        self.assertEqual({ns: self.tree(ns) for ns in ("partner", "fixture")}, before)
        changed = {p.name for p, stamp in stamps.items() if p.exists() and p.stat().st_mtime_ns != stamp}
        self.assertEqual(changed, set())

    def test_a_second_process_that_holds_the_lock_makes_a_start_wait_then_busy(self):
        import fcntl
        path = Path(paths.ROOT) / registry.FLOCK_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        self.patch(registry, "FLOCK_WAIT", 0.2)
        with self.assertRaises(registry.CatalogError) as raised:
            install.recover()
        self.assertEqual(raised.exception.code, "busy")


class Adopting(Base):
    """The settings that wait for a unit go to it when its catalog is added or updated."""

    def held(self, kind, name, **files):
        base = Path(paths.ROOT) / ".lab/held" / f"{kind}s" / name
        for rel, text in (files or {".env": "TOKEN=1\n", "secrets/key": "k\n"}).items():
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (base / rel).write_text(text, encoding="utf-8")
        return base

    def test_every_unit_of_the_default_catalog_recovers_its_settings(self):
        base = self.held("addon", "echo")
        result, _, _ = self.add("fixture")
        target = Path(paths.ROOT) / ".lab/catalogs/fixture/addons/echo"
        self.assertEqual((target / ".env").read_text(encoding="utf-8"), "TOKEN=1\n")
        self.assertEqual((target / "secrets/key").read_text(encoding="utf-8"), "k\n")
        self.assertFalse(base.exists())
        self.assertEqual([one["unit"] for one in result["adopted"]], ["addon:echo"])
        self.assertEqual(self.content("fixture") & {"addons/echo/.env"}, set())

    def test_a_catalog_receives_only_what_it_declares_in_moved_from(self):
        mine, other = self.held("addon", "ledger"), self.held("addon", "audit")
        starred.star({"addons": {"ledger": {}}, "setups": {}, "playgrounds": {}}, "addon", "ledger", True)
        staged, address, roots = self.prepare("partner")
        result = install.commit_add(staged, address, "main", "public", roots,
                                    meta_of("partner", moved_from=["addon:ledger", "addon:gone"]))
        base = Path(paths.ROOT) / ".lab/catalogs/partner/addons/ledger"
        self.assertEqual((base / ".env").read_text(encoding="utf-8"), "TOKEN=1\n")
        self.assertFalse(mine.exists())
        self.assertTrue(other.exists(), "an item the catalog does not have stays where it waits")
        self.assertEqual(starred.read()["addon"], {"@partner/ledger"})
        self.assertEqual([one["unit"] for one in result["adopted"]], ["addon:ledger"])

    def test_a_catalog_that_does_not_declare_the_item_adopts_nothing(self):
        held = self.held("addon", "ledger")
        starred.star({"addons": {"ledger": {}}, "setups": {}, "playgrounds": {}}, "addon", "ledger", True)
        result, _, _ = self.add("partner")
        self.assertTrue(held.exists())
        self.assertEqual(starred.read()["addon"], {"ledger"})
        self.assertEqual(result["adopted"], [])
        self.assertFalse((Path(paths.ROOT) / ".lab/catalogs/partner/addons/ledger/.env").exists())

    def test_a_conflict_stays_in_the_held_folder(self):
        held = self.held("addon", "ledger")
        base = Path(paths.ROOT) / ".lab/catalogs/partner/addons/ledger"
        staged, address, roots = self.prepare("partner")
        original = install.adopt

        def blocked(ns, moved_from=None):
            (base / "secrets").mkdir(parents=True, exist_ok=True)
            (base / "secrets/key").write_text("already\n", encoding="utf-8")
            return original(ns, moved_from)
        self.patch(install, "adopt", blocked)
        result = install.commit_add(staged, address, "main", "public", roots,
                                    meta_of("partner", moved_from=["addon:ledger"]))
        self.assertEqual((base / "secrets/key").read_text(encoding="utf-8"), "already\n")
        self.assertEqual((held / "secrets/key").read_text(encoding="utf-8"), "k\n")
        self.assertEqual((base / ".env").read_text(encoding="utf-8"), "TOKEN=1\n")
        results = {one["path"]: one["result"] for one in result["adopted"][0]["files"]}
        self.assertEqual(results, {".env": "moved", "secrets": "conflict"}, "a folder of credentials moves whole or stays")

    def test_settings_that_cannot_be_moved_do_not_undo_an_add_that_was_committed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (Path(paths.ROOT) / ".lab/held/addons").mkdir(parents=True)
        (Path(paths.ROOT) / ".lab/held/addons/ledger").symlink_to(outside, target_is_directory=True)
        staged, address, roots = self.prepare("partner")
        result = install.commit_add(staged, address, "main", "public", roots,
                                    meta_of("partner", moved_from=["addon:ledger"]))
        self.assertEqual(result["adopted"], [])
        self.intact("partner")
        self.assertEqual(list(outside.iterdir()), [], "nothing was moved through the link")
        install.recover()   # tried again, and refused again, without ending the start
        self.assertIsNotNone(registry.entry("partner"))

    def test_an_update_adopts_for_the_units_it_adds(self):
        self.add("partner")
        address = gitx.parse_address(self.remotes["partner"].url)
        self.held("addon", "audit")
        target = self.remotes["partner"].commit(AUDIT, "Add audit")
        bare.fetch(address, "main", registry.store_dir("partner"))
        self.patch(manifest, "read_catalog", lambda path, address=None, borrowed=(): {**parsed(path),
                                                                                       "moved_from": ["addon:audit"]})
        install.apply_update("partner", install.plan_update("partner", target))
        self.assertEqual((Path(paths.ROOT) / ".lab/catalogs/partner/addons/audit/.env").read_text(encoding="utf-8"),
                         "TOKEN=1\n")

    def test_a_start_repeats_an_adoption_that_stopped(self):
        self.held("addon", "ledger")
        self.patch(manifest, "read_catalog", lambda path, address=None, borrowed=(): {**parsed(path),
                                                                                       "moved_from": ["addon:ledger"]})
        staged, address, roots = self.prepare("partner")
        self.patch(install, "STEP", stop_at("add:adopt"))
        with self.assertRaises(Crash):
            install.commit_add(staged, address, "main", "public", roots, meta_of("partner"))
        self.patch(install, "STEP", None)
        env = Path(paths.ROOT) / ".lab/catalogs/partner/addons/ledger/.env"
        self.assertEqual(registry.entry("partner")["kind"], "git", "the catalog was added")
        self.assertFalse(env.exists())
        install.recover()
        self.assertEqual(env.read_text(encoding="utf-8"), "TOKEN=1\n")
        install.recover()   # and again
        self.assertEqual(env.read_text(encoding="utf-8"), "TOKEN=1\n")


# ---------------------------------------------------------------------------------------------------------------------
# A working copy in place of a git install
# ---------------------------------------------------------------------------------------------------------------------

class Shadowing(Base):

    def setUp(self):
        super().setUp()
        self.installed("partner")
        self.base = Path(paths.ROOT) / ".lab/catalogs/partner"
        self.made = self.machine("partner")
        self.folder = self.folder_catalog("partner", "partner-work")
        self.before = self.tree("partner")
        self.original = registry.entry("partner")

    def test_the_install_gives_way_to_the_folder_and_its_machine_files_move_in(self):
        answer = install.shadow("partner", str(self.folder))
        self.assertEqual(answer["folder"], str(self.folder))
        self.assertEqual(sorted(answer["moved"]), TOPS)
        self.assertEqual(answer["kept"], [])
        self.assertTrue(self.base.is_symlink())
        self.assertEqual(os.readlink(self.base), str(self.folder))
        for rel, text in {"addons/ledger/.env": "SECRET=1\n", "addons/ledger/.state/created.json": "{}\n",
                          "addons/ledger/secrets/token": "t\n", "addons/ledger/data/db.sqlite": "rows\n",
                          "addons/ledger/certs/ca.txt": "ca\n"}.items():
            self.assertEqual((self.folder / rel).read_text(encoding="utf-8"), text)
        entry = registry.entry("partner")
        self.assertEqual((entry["kind"], entry["path"]), ("folder", str(self.folder)))
        self.assertEqual(entry["shadows"], {key: self.original[key] for key in ("url", "branch", "added")})
        held = registry.lock_of("partner")
        self.assertEqual((held["shadowed"], held["pending"]), (True, None))
        self.assertRegex(held["pin"], "[0-9a-f]{40}")
        self.assertTrue(registry.store_dir("partner").is_dir(), "the store stays")
        self.assertEqual(registry.units_of("partner")["addon"], ["ledger"], "the folder is listed")
        self.assertEqual([one["id"] for one in registry.entries()], ["partner"])

    def test_the_working_copy_is_not_changed_but_by_the_entries_that_moved_in(self):
        before = sorted(p.relative_to(self.folder).as_posix() for p in self.folder.rglob("*"))
        install.shadow("partner", str(self.folder))
        after = sorted(p.relative_to(self.folder).as_posix() for p in self.folder.rglob("*"))
        self.assertEqual(sorted(set(after) - set(before)), sorted([*TOPS, "addons/ledger/.state/created.json",
                                                                     "addons/ledger/secrets/token",
                                                                     "addons/ledger/data/db.sqlite",
                                                                     "addons/ledger/certs/ca.txt"]))
        self.assertEqual(set(before) - set(after), set())

    def test_an_unlink_puts_the_install_back_as_it_was(self):
        install.shadow("partner", str(self.folder))
        answer = install.unshadow("partner")
        self.assertEqual(sorted(answer["moved"]), TOPS)
        self.assertFalse(self.base.is_symlink())
        self.assertEqual(self.tree("partner"), self.before)
        self.intact("partner")
        self.assertEqual(registry.entry("partner"), self.original)
        self.assertNotIn("shadowed", registry.lock_of("partner"))
        self.assertFalse((self.folder / "addons/ledger/.env").exists(), "what moved in goes back")
        self.assertEqual(sorted(p.name for p in self.folder.rglob("*") if p.is_file()),
                         sorted(p.name for p in (fixtures.CATALOGS / "partner").rglob("*") if p.is_file()))
        self.assertFalse((Path(paths.ROOT) / ".lab/catalogs/.shadow-partner.json").exists())

    def test_a_round_trip_with_nothing_to_move_returns_the_same_tree(self):
        shutil.rmtree(self.base / "addons/ledger/.state")
        shutil.rmtree(self.base / "addons/ledger/secrets")
        (self.base / "addons/ledger/.env").unlink()
        before = self.tree("partner")
        install.shadow("partner", str(self.folder))
        install.unshadow("partner")
        self.assertEqual(self.tree("partner"), before)

    def test_a_file_the_working_copy_already_has_wins_and_the_installs_is_kept_aside(self):
        (self.folder / "addons/ledger").mkdir(parents=True, exist_ok=True)
        (self.folder / "addons/ledger/.env").write_text("THEIRS=1\n", encoding="utf-8")
        answer = install.shadow("partner", str(self.folder))
        self.assertEqual(answer["kept"], ["addons/ledger/.env"])
        self.assertEqual((self.folder / "addons/ledger/.env").read_text(encoding="utf-8"), "THEIRS=1\n")
        kept = Path(paths.ROOT) / ".lab/migration/kept/catalogs/partner/addons/ledger/.env"
        self.assertEqual(kept.read_text(encoding="utf-8"), "SECRET=1\n")
        install.unshadow("partner")
        self.assertEqual((self.folder / "addons/ledger/.env").read_text(encoding="utf-8"), "THEIRS=1\n",
                         "what the working copy had is not taken from it")
        self.assertFalse((self.base / "addons/ledger/.env").exists())
        self.assertEqual(kept.read_text(encoding="utf-8"), "SECRET=1\n", "and the kept copy waits for the person")

    def test_what_appears_in_the_working_copy_meanwhile_stays_there(self):
        install.shadow("partner", str(self.folder))
        (self.folder / "addons/ledger/.env.local").write_text("NEW=1\n", encoding="utf-8")
        install.unshadow("partner")
        self.assertTrue((self.folder / "addons/ledger/.env.local").is_file())
        self.assertFalse((self.base / "addons/ledger/.env.local").exists())

    def test_an_item_that_is_on_holds_the_link_back(self):
        self.write(".lab/on.yml", "include:\n  - .lab/catalogs/partner/addons/ledger/compose.yml\n")
        with self.assertRaises(registry.CatalogError) as raised:
            install.shadow("partner", str(self.folder))
        self.assertEqual((raised.exception.code, raised.exception.message), ("held", "Turn off ledger first."))
        self.assertEqual(self.tree("partner"), self.before)
        self.assertFalse((Path(paths.ROOT) / ".lab/catalogs/.shadow-partner.json").exists())

    def test_a_folder_that_declares_another_id_is_not_linked(self):
        wrong = self.folder_catalog("lab-new", "wrong")
        with self.assertRaises(registry.CatalogError) as raised:
            install.shadow("partner", str(wrong))
        self.assertEqual(raised.exception.code, "catalog_invalid")
        self.assertEqual(self.tree("partner"), self.before)
        (wrong / "catalog.yml").unlink()
        with self.assertRaises(registry.CatalogError) as raised:
            install.shadow("partner", str(wrong))
        self.assertEqual(raised.exception.code, "folder_not_catalog")

    def test_a_catalog_that_is_already_linked_or_not_installed_is_not_linked(self):
        install.shadow("partner", str(self.folder))
        with self.assertRaises(registry.CatalogError) as raised:
            install.shadow("partner", str(self.folder))
        self.assertEqual(raised.exception.code, "linked")
        with self.assertRaises(ValueError):
            install.shadow("lab-new", str(self.folder))

    def test_only_a_linked_catalog_is_unlinked(self):
        with self.assertRaises(ValueError):
            install.unshadow("partner")
        with self.assertRaises(ValueError):
            install.unshadow("nobody")

    def test_a_machine_entry_that_would_not_move_waits_aside_and_nothing_is_lost(self):
        real = moves.relocate

        def stuck(src, dst, why):
            return "copied" if src.endswith("secrets") else real(src, dst, why)
        self.patch(moves, "relocate", stuck)
        answer = install.shadow("partner", str(self.folder))
        self.assertIn("addons/ledger/secrets", answer["moved"])
        self.assertTrue(self.base.is_symlink())
        (aside,) = [p for p in (Path(paths.ROOT) / ".lab/catalogs").iterdir() if p.name.startswith(".removed-partner-")]
        self.assertEqual((aside / "addons/ledger/secrets/token").read_text(encoding="utf-8"), "t\n")

    def test_an_unlink_with_the_cache_cleared_fails_before_it_starts_and_works_after_a_fetch(self):
        install.shadow("partner", str(self.folder))
        shutil.rmtree(registry.store_dir("partner"))   # the cache is disposable
        with self.assertRaises(registry.CatalogError) as raised:
            install.unshadow("partner")
        self.assertEqual(raised.exception.code, "fsck")
        self.assertTrue(self.base.is_symlink(), "the link stays")
        self.assertEqual(registry.entry("partner")["kind"], "folder")
        self.assertEqual(install._read_record("partner")["phase"], "linked", "nothing was started that a start would retry")
        install.recover()
        self.assertTrue(self.base.is_symlink())
        bare.fetch(gitx.parse_address(self.original["url"]), None, registry.store_dir("partner"))
        install.unshadow("partner")
        self.assertEqual(self.tree("partner"), self.before)
        self.intact("partner")

    def test_an_entry_no_pattern_knows_that_would_not_move_is_kept_by_the_marker_and_comes_back_at_the_next_add(self):
        real = moves.relocate

        def stuck(src, dst, why):
            return "stayed" if src.endswith("/data") else real(src, dst, why)
        self.patch(moves, "relocate", stuck)
        install.shadow("partner", str(self.folder))
        (aside,) = [p for p in (Path(paths.ROOT) / ".lab/catalogs").iterdir() if p.name.startswith(".removed-partner-")]
        self.assertTrue((aside / install.MARKER).is_file(), "a folder with no manifest is marked before it is set aside")
        self.assertEqual((aside / "addons/ledger/data/db.sqlite").read_text(encoding="utf-8"), "rows\n")
        install.unshadow("partner")
        install.remove("partner", keep_settings=True)
        self.add("partner")
        self.intact("partner")
        self.assertEqual((self.base / "addons/ledger/data/db.sqlite").read_text(encoding="utf-8"), "rows\n",
                         "a name that no pattern knows is not content that a restore may delete")
        self.assertFalse(aside.exists(), "and the folder goes once nothing of the machine's is left in it")


class WithCompose(Base):
    """The same flows with Compose reading the catalog.yml, for the one place that asks it."""

    def setUp(self):
        fixtures.need_compose()   # asked with the real home folder: the Compose plugin is found there
        docker = os.path.join(os.path.expanduser("~"), ".docker")
        super().setUp()
        self.start(mock.patch.dict(os.environ, {"DOCKER_CONFIG": os.environ.get("DOCKER_CONFIG", docker)}))
        self.patch(manifest, "read_catalog", REAL_READ)

    def test_an_add_and_an_update_read_the_catalog_file_with_compose(self):
        staged, address, roots = self.prepare("partner")
        meta = REAL_READ(os.path.relpath(staged.path / "catalog.yml", paths.ROOT))
        self.assertEqual((meta["id"], meta["name"], meta["version"], meta["lab"]), ("partner", "Partner fixture", "2.0.0", 1))
        install.commit_add(staged, address, "main", "public", roots)
        self.intact("partner")
        self.assertEqual(registry.lock_of("partner")["name"], "Partner fixture")
        target = self.remotes["partner"].commit({"addons/ledger/README.md": "Changed\n"})
        bare.fetch(address, "main", registry.store_dir("partner"))
        plan = install.plan_update("partner", target)
        self.assertEqual((plan["updates"], plan["meta"]["id"]), (["addon:ledger"], "partner"))
        install.apply_update("partner", plan)
        self.intact("partner")

    def test_a_catalog_file_compose_cannot_read_is_refused_before_anything_is_installed(self):
        files = {**gitfixture.catalog_files("broken"), "catalog.yml": "x-gravitee-catalog:\n  id: [unclosed\n"}
        repo = gitfixture.make_repo(self.home / "broken.git", files)
        address = gitx.parse_address(gitfixture.url(repo))
        store_path = registry.new_store_dir("5e6f")
        tip = bare.fetch(address, None, store_path)
        kept, ignored = bare.selected(bare.listing(store_path, tip))
        staged = install.stage(address, "broken", store_path, tip, kept, ignored)
        with self.assertRaises(registry.CatalogError) as raised:
            install.commit_add(staged, address, "main", "public", [])
        self.assertEqual(raised.exception.code, "catalog_invalid")
        self.assertFalse(registry.install_dir("broken").exists())
        self.assertIsNone(registry.entry("broken"))


# ---------------------------------------------------------------------------------------------------------------------
# A stop at every step
# ---------------------------------------------------------------------------------------------------------------------

class Stops(Base):
    """Each operation is run once to count its steps, then again for each of them with the run stopped there, as if the
    process had died. After `recover()` the state is the old complete one or the new complete one, the machine's files are
    where they were, a second `recover()` changes nothing, and the operation can be done again. While it ran, no reader that
    holds the project lock could see a mix."""

    maxDiff = None

    def snapshot(self):
        """The project folder, the folder the author's copies are in and the remotes, copied aside: a run of the
        operation starts from them again, at the same paths, so that what `build` answered is still true."""
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        base = Path(holder.name)
        shutil.copytree(self.root, base / "root", symlinks=True)
        shutil.copytree(self.home, base / "home", symlinks=True)
        for ns, remote in self.remotes.items():
            shutil.copytree(remote.bare, base / f"remote-{ns}", symlinks=True)
        return base

    def restore(self, base):
        places = [(self.root, base / "root"), (self.home, base / "home")]
        places += [(remote.bare, base / f"remote-{ns}") for ns, remote in self.remotes.items()]
        for target, saved in places:
            for child in list(Path(target).iterdir()):
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            shutil.copytree(saved, target, symlinks=True, dirs_exist_ok=True)

    def runs(self, build, operate, check, ns="partner", reader=True):
        """build() makes the world (and answers what it needs), operate(world) is the operation, check(world, step, label)
        runs after the stop and `recover()`. The operation is run once to count its steps, then once for each of them, from
        the same world, stopped there. Answers the labels of the steps."""
        self.fresh()
        world = build()
        self.remote(ns)   # the remote of the catalog is part of the world, made or not by `build`
        saved = self.snapshot()
        watch = Watch(self, ns=ns if reader else None)
        operate(world)
        labels = list(watch.labels)
        self.assertGreater(len(labels), 3, labels)
        for step, label in enumerate(labels):
            with self.subTest(step=step, label=label):
                self.restore(saved)
                watch = Watch(self, stop=step, ns=ns if reader else None)
                with self.assertRaises(Crash):
                    operate(world)
                self.patch(install, "STEP", None)
                install.recover()
                check(world, step, label)
                settled = self.state(ns)
                install.recover()
                self.assertEqual(self.state(ns), settled, "a second start changes nothing")
        return labels

    def settings_exist(self, ns, made):
        found = [rel for rel in made if (Path(paths.ROOT) / ".lab/catalogs" / ns / rel).is_file()]
        if len(found) == len(made):
            return True
        aside = [p for p in (Path(paths.ROOT) / ".lab/catalogs").iterdir() if p.name.startswith(f".removed-{ns}-")]
        self.assertTrue(aside, f"the settings are neither in the install nor kept aside: {made}")
        for rel in made:
            self.assertTrue(any((p / rel).is_file() for p in aside), f"{rel} is lost")
        return False

    # -- Add ---------------------------------------------------------------------------------------------------------

    def test_a_stop_at_any_step_of_an_add_leaves_nothing_or_a_whole_catalog_and_the_add_can_be_done_again(self):
        def check(_, step, label):
            if registry.entry("partner") is None:
                self.assertEqual(paths.ids("addon"), [], label)
                self.nothing("partner")
                self.assertFalse(registry.install_dir("partner").exists(), label)
                self.add("partner")
                self.intact("partner")
            else:
                self.intact("partner")
                with self.assertRaises(registry.CatalogError) as raised:
                    self.add("partner")
                self.assertEqual(raised.exception.code, "already_added")
        labels = self.runs(lambda: None, lambda _: self.add("partner"), check)
        for wanted in ("stage:begin", "add:begin", "add:manifest", "add:store", "add:pending", "add:install", "add:placed",
                       "add:lock", "add:config", "add:adopt", "add:done"):
            self.assertIn(wanted, labels)

    def test_a_stop_after_the_install_was_renamed_with_pending_set_and_no_pin_is_cleaned(self):
        self.fresh()
        staged, address, roots = self.prepare("partner")
        self.patch(install, "STEP", stop_at("add:placed"))
        with self.assertRaises(Crash):
            install.commit_add(staged, address, "main", "public", roots, meta_of("partner"))
        self.patch(install, "STEP", None)
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["pending"]["op"], held["pending"]["to"]), (None, "add", staged.commit))
        self.assertIsNone(registry.entry("partner"))
        self.assertEqual(paths.ids("addon"), [], "a catalog with no entry has no units")
        install.recover()
        self.nothing("partner")
        self.assertFalse(registry.install_dir("partner").exists())
        self.add("partner")
        self.intact("partner")

    def test_a_stop_at_any_step_of_an_add_over_the_folder_a_remove_left_keeps_every_setting(self):
        made = []

        def build():
            self.add("partner")
            made[:] = self.machine("partner")
            install.remove("partner", keep_settings=True)

        def check(_, step, label):
            self.settings_exist("partner", made)
            if registry.entry("partner") is None:
                self.assertEqual(paths.ids("addon"), [], label)
                self.add("partner")
            else:
                self.intact("partner")
            self.intact("partner")
            self.assertTrue(self.settings_exist("partner", made), "the settings are in the install")
        self.runs(build, lambda _: self.add("partner"), check)

    def test_a_stop_at_any_step_of_an_add_beside_the_folder_the_sweeper_renamed_keeps_every_setting(self):
        made = []

        def build():
            self.add("partner")
            made[:] = self.machine("partner")
            (Path(paths.ROOT) / ".lab/catalogs.json").unlink()
            install.recover()

        def check(_, step, label):
            self.settings_exist("partner", made)
            if registry.entry("partner") is None:
                self.assertEqual(paths.ids("addon"), [], label)
                self.add("partner")
            self.intact("partner")
            self.assertTrue(self.settings_exist("partner", made), "the settings are back in the install")
        self.runs(build, lambda _: self.add("partner"), check)

    # -- the first download of a listed catalog ------------------------------------------------------------------------

    def provision(self, aside=False):
        """The world of a first start: the entry exists, nothing is downloaded. Answers what the operation needs."""
        remote = self.remote("partner")
        fixtures.default(self, "fixture")
        registry.write_config({"schema": 1, "catalogs": [{"id": "partner", "kind": "git", "url": remote.url,
                                                           "branch": "main", "auto": True, "added": "2026-10-16T09:00:00Z"}]})
        registry.set_lock("partner", audience="public")
        made = []
        if aside:
            made[:] = self.machine_after_a_removed_install()
        return made

    def machine_after_a_removed_install(self):
        """Settings waiting in a `.removed-partner-*` folder, as the sweeper leaves them."""
        folder = registry.install_dir("partner").parent / ".removed-partner-20261015T090000Z"
        made = []
        for rel, text in {".env": "SECRET=1\n", ".state/created.json": "{}\n"}.items():
            target = folder / "addons/ledger" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            made.append(f"addons/ledger/{rel}")
        return made

    def download(self, _=None):
        address = gitx.parse_address(self.remotes["partner"].url)
        store_path = registry.store_dir("partner")
        tip = self.fetched(self.remotes["partner"], address, store_path)
        kept, ignored = bare.selected(bare.listing(store_path, tip))
        staged = install.stage(address, "partner", store_path, tip, kept, ignored)
        install.commit_add(staged, address, "main", "public", bare.roots(store_path, tip), meta_of("partner"))

    def test_a_stop_at_any_step_of_a_download_ends_installed_or_ready_to_start_again(self):
        def check(_, step, label):
            held = registry.lock_of("partner")
            self.assertIsNone(held["pending"], label)
            self.assertEqual(len(registry.entries()), 1)
            if held["pin"] is None:
                self.assertEqual(paths.ids("addon"), [], label)
                self.assertNotIn("addons/ledger/compose.yml", self.tree("partner") if registry.install_dir("partner").is_dir() else {})
                self.assertIsNone(held["error"])
                self.download()
            self.intact("partner")
        labels = self.runs(self.provision, self.download, check)
        self.assertIn("add:pending", labels)
        self.assertNotIn("add:config", labels, "the entry exists already")

    def test_a_stop_at_any_step_of_a_download_beside_settings_that_wait_keeps_them(self):
        made = []

        def build():
            made[:] = self.provision(aside=True)

        def check(_, step, label):
            base = Path(paths.ROOT) / ".lab/catalogs"
            waiting = [p for p in (base / "partner", *base.glob(".removed-partner-*")) if (p / "addons/ledger/.env").is_file()]
            self.assertTrue(waiting, f"{label}: the settings are lost")
            if registry.lock_of("partner")["pin"] is None:
                self.download()
            self.intact("partner")
            self.assertEqual((base / "partner/addons/ledger/.env").read_text(encoding="utf-8"), "SECRET=1\n", label)
            self.assertEqual((base / "partner/addons/ledger/.state/created.json").read_text(encoding="utf-8"), "{}\n")
        self.runs(build, self.download, check)

    # -- Update, Go back, Repair -------------------------------------------------------------------------------------

    def world_to_update(self):
        """partner installed at its first commit, a second commit fetched, a plan made, settings beside the unit."""
        self.add("partner")
        address = gitx.parse_address(self.remotes["partner"].url)
        made = self.machine("partner")
        first = registry.lock_of("partner")["pin"]
        target = self.remotes["partner"].commit({**AUDIT, "addons/ledger/README.md": "Changed\n"}, "Change the catalog")
        bare.fetch(address, "main", registry.store_dir("partner"))
        registry.set_lock("partner", tip=target)
        return {"first": first, "target": target, "made": made, "plan": install.plan_update("partner", target)}

    def test_a_stop_at_any_step_of_an_update_ends_at_the_old_version_or_the_new(self):
        def check(world, step, label):
            held = registry.lock_of("partner")
            self.assertIsNone(held["pending"], label)
            self.assertIsNone(held["error"], label)
            self.intact("partner")
            self.assertIn(held["pin"], (world["first"], world["target"]), label)
            readme = self.tree("partner")["addons/ledger/README.md"][1]
            if held["pin"] == world["first"]:
                self.assertEqual(readme, (fixtures.CATALOGS / "partner" / LEDGER / "README.md").read_bytes(), label)
                self.assertEqual(registry.units_of("partner")["addon"], ["ledger"])
                self.assertIsNone(held["previous"])
                again = install.plan_update("partner", world["target"])
                install.apply_update("partner", again)
            else:
                self.assertEqual(readme, b"Changed\n", label)
                self.assertEqual(registry.units_of("partner")["addon"], ["audit", "ledger"])
                self.assertEqual(held["previous"], world["first"])
                self.assertEqual(install.plan_update("partner", world["target"]), {"upToDate": True})
            self.intact("partner")
            self.assertEqual(registry.lock_of("partner")["pin"], world["target"])
            self.assertEqual(sorted(rel for rel in world["made"] if (Path(paths.ROOT) / ".lab/catalogs/partner" / rel).is_file()),
                             sorted(world["made"]), "the machine's files are where they were")
        labels = self.runs(self.world_to_update, lambda world: install.apply_update("partner", world["plan"]), check)
        for wanted in ("update:pending", "update:apply", "update:file", "update:manifest", "update:lock", "update:refs",
                       "update:adopt", "update:done"):
            self.assertIn(wanted, labels)

    def test_a_stop_at_any_step_of_going_back_ends_at_one_version_or_the_other(self):
        def build():
            world = self.world_to_update()
            install.apply_update("partner", world["plan"])
            world["back"] = install.plan_update("partner", world["first"])
            return world

        def check(world, step, label):
            held = registry.lock_of("partner")
            self.assertIsNone(held["pending"], label)
            self.intact("partner")
            self.assertIn(held["pin"], (world["first"], world["target"]))
            if held["pin"] == world["first"]:
                self.assertEqual(held["previous"], world["target"], "the action toggles")
                self.assertEqual(registry.units_of("partner")["addon"], ["ledger"])
            else:
                self.assertEqual(held["previous"], world["first"])
                self.assertEqual(registry.units_of("partner")["addon"], ["audit", "ledger"])
            self.assertEqual(held["tip"], world["target"])
        self.runs(build, lambda world: install.apply_update("partner", world["back"]), check)

    def test_a_stop_at_any_step_of_a_repair_ends_repaired_or_as_damaged_as_it_was(self):
        def build():
            self.add("partner")
            base = Path(paths.ROOT) / ".lab/catalogs/partner"
            (base / LEDGER / "README.md").unlink()
            (base / LEDGER / "compose.yml").chmod(0o644)
            (base / LEDGER / "compose.yml").write_text("services: {evil: {}}\n", encoding="utf-8")
            return {"made": self.machine("partner")}

        def check(world, step, label):
            held = registry.lock_of("partner")
            self.assertIsNone(held["pending"], label)
            self.assertIsNone(held["error"], label)
            bad = install.verify("partner", ["addon:ledger"])
            if bad:
                self.assertEqual(bad, ["addons/ledger/README.md", "addons/ledger/compose.yml"], label)
                install.repair("partner")
            self.intact("partner")
            for rel in world["made"]:
                self.assertTrue((Path(paths.ROOT) / ".lab/catalogs/partner" / rel).is_file())
        labels = self.runs(build, lambda _: install.repair("partner"), check, reader=False)   # damaged by design
        self.assertIn("repair:pending", labels)

    # -- Remove ------------------------------------------------------------------------------------------------------

    def test_a_stop_at_any_step_of_a_remove_ends_installed_or_removed_with_the_settings_kept(self):
        made = []

        def build():
            self.add("partner")
            made[:] = self.machine("partner")
            self.add("lab-new")

        def check(_, step, label):
            self.intact("lab-new")
            if registry.entry("partner") is not None:
                self.intact("partner")
            else:
                self.nothing("partner")
                self.assertEqual(paths.ids("addon"), ["@lab-new/newer"], label)
                self.assertFalse(Path(paths.ROOT, ".lab/catalogs/partner/addons/ledger/compose.yml").exists(), label)
                self.settings_exist("partner", made)
                self.add("partner")   # an Add right after the start brings every setting back, the odd names too
                self.intact("partner")
                self.assertTrue(self.settings_exist("partner", made), f"{label}: the settings are in the install")
                self.assertEqual(install.remove("partner"), {"warning": None})
                self.settings_exist("partner", made)
            self.assertFalse(registry.store_dir("partner").exists() and registry.entry("partner") is None)
        labels = self.runs(build, lambda _: install.remove("partner", keep_settings=True), check)
        for wanted in ("remove:config", "remove:store", "remove:files", "remove:marker", "remove:vacate", "remove:lock",
                       "remove:stars"):
            self.assertIn(wanted, labels)

    def test_a_stop_at_any_step_of_a_remove_that_deletes_leaves_nothing_visible(self):
        def build():
            self.add("partner")
            self.machine("partner")

        def check(_, step, label):
            if registry.entry("partner") is not None:
                self.intact("partner")
            else:
                self.nothing("partner")
                self.assertEqual(paths.ids("addon"), [], label)
        self.runs(build, lambda _: install.remove("partner", keep_settings=False), check)

    # -- the working copy ----------------------------------------------------------------------------------------------

    def world_to_link(self):
        self.add("partner")
        made = self.machine("partner")
        return {"made": made, "folder": self.folder_catalog("partner", "partner-work"),
                "before": self.tree("partner"), "entry": registry.entry("partner")}

    def test_a_stop_at_any_step_of_a_link_ends_unlinked_or_linked_with_every_setting_in_the_folder(self):
        def check(world, step, label):
            entry = registry.entry("partner")
            if entry["kind"] == "git":
                self.assertEqual(self.tree("partner"), world["before"], label)
                self.intact("partner")
                self.assertNotIn("shadowed", registry.lock_of("partner"))
            else:
                self.assertEqual(entry["shadows"]["url"], world["entry"]["url"])
                self.assertTrue(registry.install_dir("partner").is_symlink(), label)
                self.assertTrue(registry.lock_of("partner")["shadowed"])
                for rel in world["made"]:
                    self.assertTrue((world["folder"] / rel).is_file(), f"{label}: {rel}")
                self.assertEqual(registry.units_of("partner")["addon"], ["ledger"])
                install.unshadow("partner")
                self.assertEqual(self.tree("partner"), world["before"], label)
        labels = self.runs(self.world_to_link, lambda world: install.shadow("partner", str(world["folder"])), check)
        for wanted in ("shadow:record", "shadow:move", "shadow:install", "shadow:link", "shadow:config", "shadow:lock"):
            self.assertIn(wanted, labels)

    def test_a_stop_at_any_step_of_an_unlink_ends_linked_or_back_as_it_was(self):
        def build():
            world = self.world_to_link()
            install.shadow("partner", str(world["folder"]))
            return world

        def check(world, step, label):
            entry = registry.entry("partner")
            if entry["kind"] == "folder":
                self.assertTrue(registry.install_dir("partner").is_symlink(), label)
                for rel in world["made"]:
                    self.assertTrue((world["folder"] / rel).is_file(), f"{label}: {rel}")
                install.unshadow("partner")
            self.assertEqual(registry.entry("partner"), world["entry"], label)
            self.assertEqual(self.tree("partner"), world["before"], label)
            self.intact("partner")
            self.assertNotIn("shadowed", registry.lock_of("partner"))
            for rel in world["made"]:
                self.assertFalse((world["folder"] / rel).exists(), f"{label}: {rel} went back")
        labels = self.runs(build, lambda world: install.unshadow("partner"), check)
        for wanted in ("unshadow:record", "unshadow:link", "unshadow:install", "unshadow:move", "unshadow:config",
                       "unshadow:lock"):
            self.assertIn(wanted, labels)

    # -- a stop inside the adoption of the settings that wait -----------------------------------------------------------

    def test_a_stop_at_any_step_of_the_adoption_is_finished_by_the_next_start(self):
        self.patch(manifest, "read_catalog", lambda path, address=None, borrowed=(): {**parsed(path),
                                                                                       "moved_from": ["addon:ledger"]})
        self.fresh()
        held = Path(paths.ROOT) / ".lab/held/addons/ledger"
        for rel, text in {".env": "TOKEN=1\n", "secrets/key": "k\n", "notes/a.txt": "a\n"}.items():
            (held / rel).parent.mkdir(parents=True, exist_ok=True)
            (held / rel).write_text(text, encoding="utf-8")
        starred.star({"addons": {"ledger": {}}, "setups": {}, "playgrounds": {}}, "addon", "ledger", True)
        self.remote("partner")
        saved = self.snapshot()
        seen = []
        self.patch(moves, "STEP", seen.append)
        meta = meta_of("partner", moved_from=["addon:ledger"])
        self.add("partner", meta=meta)
        steps = list(seen)
        self.assertGreater(len(steps), 4, steps)
        for step, label in enumerate(steps):
            with self.subTest(step=step, label=label):
                self.restore(saved)
                count = []

                def stop(name):
                    count.append(name)
                    if len(count) - 1 == step:
                        raise Crash(name)
                self.patch(moves, "STEP", stop)
                with self.assertRaises(Crash):
                    self.add("partner", meta=meta)
                self.patch(moves, "STEP", None)
                self.assertEqual(registry.entry("partner")["kind"], "git", "the catalog was added before the adoption")
                install.recover()
                base = Path(paths.ROOT) / ".lab/catalogs/partner/addons/ledger"
                self.assertEqual((base / ".env").read_text(encoding="utf-8"), "TOKEN=1\n")
                self.assertEqual((base / "secrets/key").read_text(encoding="utf-8"), "k\n")
                self.assertEqual((base / "notes/a.txt").read_text(encoding="utf-8"), "a\n")
                self.assertFalse(held.exists(), "nothing waits any more")
                self.assertEqual(starred.read()["addon"], {"@partner/ledger"})
                self.intact("partner")

    # -- a stop inside the start ---------------------------------------------------------------------------------------

    def test_a_stop_inside_recover_is_finished_by_the_next_start(self):
        self.fresh()
        world = self.world_to_update()
        self.remote("partner")
        saved = self.snapshot()
        Watch(self, stop=3, ns="partner")
        with self.assertRaises(Crash):
            install.apply_update("partner", world["plan"])
        self.patch(install, "STEP", None)
        counting = Watch(self, ns="partner")
        install.recover()
        steps = list(counting.labels)
        self.assertGreater(len(steps), 5)
        self.assertEqual(registry.lock_of("partner")["pin"], world["target"])
        for step in range(len(steps)):
            with self.subTest(step=step, label=steps[step]):
                self.restore(saved)
                Watch(self, stop=3, ns="partner")
                with self.assertRaises(Crash):
                    install.apply_update("partner", world["plan"])
                Watch(self, stop=step)
                with self.assertRaises(Crash):
                    install.recover()
                self.patch(install, "STEP", None)
                install.recover()
                self.intact("partner")
                self.assertEqual(registry.lock_of("partner")["pin"], world["target"])


# ---------------------------------------------------------------------------------------------------------------------
# Failures that are not stops, hostile manifests, and the promise about the machine's files
# ---------------------------------------------------------------------------------------------------------------------

class WithoutGit(Base):
    """A start on a machine where git is gone, or where it is the shim of macOS without its tools, goes on: an installed
    catalog keeps working (the state is `needs_git`), and an operation that was under way waits for a start that has git
    instead of being given up on."""

    def no_git(self):
        return mock.patch.object(gitx, "executable", return_value=None)

    def update_stopped_at(self, label):
        self.installed("partner")
        address = gitx.parse_address(self.remotes["partner"].url)
        first = registry.lock_of("partner")["pin"]
        target = self.remotes["partner"].commit({"addons/ledger/README.md": "Changed\n"}, "Change the catalog")
        bare.fetch(address, "main", registry.store_dir("partner"))
        registry.set_lock("partner", tip=target)
        plan = install.plan_update("partner", target)
        self.patch(install, "STEP", stop_at(label))
        with self.assertRaises(Crash):
            install.apply_update("partner", plan)
        self.patch(install, "STEP", None)
        return first, target

    def test_a_start_without_git_does_not_raise_and_changes_nothing(self):
        self.installed("partner")
        self.machine("partner")
        before = self.state("partner")
        with self.no_git():
            install.recover()
        self.assertEqual(self.state("partner"), before)
        self.intact("partner")

    def test_an_update_under_way_is_not_given_up_on_and_a_start_with_git_finishes_it(self):
        first, target = self.update_stopped_at("update:manifest")
        with self.no_git():
            install.recover()
        held = registry.lock_of("partner")
        self.assertEqual(held["pending"], {"op": "update", "from": first, "to": target})
        self.assertIsNone(held["error"], "it is not damaged: git is not here")
        install.recover()
        self.intact("partner")
        self.assertEqual(registry.lock_of("partner")["pin"], target)
        self.assertEqual(self.tree("partner")["addons/ledger/README.md"][1], b"Changed\n")

    def test_a_download_under_way_is_not_given_up_on_either(self):
        remote = self.remote("partner")
        fixtures.default(self, "partner", url=remote.url)
        registry.write_config({"schema": 1, "catalogs": [{"id": "partner", "kind": "git", "url": remote.url, "branch": "main",
                                                           "auto": True, "added": "2026-10-16T09:00:00Z"}]})
        registry.set_lock("partner", audience="public")
        staged, address, roots = self.prepare("partner")
        self.patch(install, "STEP", stop_at("add:placed"))
        with self.assertRaises(Crash):
            install.commit_add(staged, address, "main", "public", roots, meta_of("partner"))
        self.patch(install, "STEP", None)
        with self.no_git():
            install.recover()
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["pending"]["op"], held["error"]), (None, "add", None))
        self.assertTrue(registry.install_dir("partner").is_dir(), "and what was installed is not taken away")
        install.recover()
        self.intact("partner")

    def test_an_operation_that_has_to_settle_first_is_refused_without_git_and_the_record_stays(self):
        first, target = self.update_stopped_at("update:file")
        with self.no_git(), self.assertRaises(registry.CatalogError) as raised:
            install.repair("partner")
        self.assertEqual(raised.exception.code, "git_missing")
        self.assertEqual(registry.lock_of("partner")["pending"], {"op": "update", "from": first, "to": target})
        install.repair("partner")
        self.assertEqual(registry.lock_of("partner")["pin"], target)
        self.intact("partner")

    def test_a_remove_needs_no_git_so_an_operation_under_way_goes_with_the_catalog(self):
        self.update_stopped_at("update:manifest")
        with self.no_git():
            self.assertEqual(install.remove("partner", keep_settings=False), {"warning": None})
        self.nothing("partner")
        self.assertFalse(registry.install_dir("partner").exists())

    def test_a_git_that_ends_with_the_lab_is_not_damage_either(self):
        first, target = self.update_stopped_at("update:manifest")
        ending = registry.CatalogError("git_failed", "Git could not read this repository.", 502, output=gitx.ENDING)
        with mock.patch.object(bare, "resolve", side_effect=ending):
            install.recover()
        held = registry.lock_of("partner")
        self.assertEqual((held["pending"]["to"], held["error"]), (target, None), "the Lab was ending, not the catalog damaged")
        install.recover()
        self.assertEqual(registry.lock_of("partner")["pin"], target)

    def test_a_catalog_that_is_damaged_is_still_marked_damaged(self):
        first, target = self.update_stopped_at("update:manifest")
        shutil.rmtree(registry.store_dir("partner"))
        install.recover()
        held = registry.lock_of("partner")
        self.assertEqual((held["pending"], held["error"]), (None, "damaged"), "a store that is gone is damage, not a wait")


class Failing(Base):
    """An error in the middle of an operation (a full disk) is not a stop: the process lives on, with a lock that says
    what was under way, and the next operation finishes it."""

    def setUp(self):
        super().setUp()
        self.installed("partner")
        self.address = gitx.parse_address(self.remotes["partner"].url)
        self.first = registry.lock_of("partner")["pin"]

    def advance(self, files):
        target = self.remotes["partner"].commit(files)
        bare.fetch(self.address, "main", registry.store_dir("partner"))
        return target

    def test_a_failure_while_the_files_are_replaced_leaves_a_pending_update_the_next_operation_finishes(self):
        target = self.advance({"addons/ledger/README.md": "Changed\n"})
        plan = install.plan_update("partner", target)
        real = install._write_manifest
        self.patch(install, "_write_manifest", mock.Mock(side_effect=OSError("disk full")))
        with self.assertRaises(OSError):
            install.apply_update("partner", plan)
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["pending"]), (self.first, {"op": "update", "from": self.first, "to": target}))
        self.patch(install, "_write_manifest", real)
        self.assertEqual(install.plan_update("partner", target), {"upToDate": True}, "finished before anything else")
        self.assertEqual(registry.lock_of("partner")["pin"], target)
        self.intact("partner")

    def stopped_halfway(self, files):
        """An update whose second replacement fails, as a full disk does: what its first one wrote stays, the lock says
        the update is under way. Answers (the target, the plan)."""
        target = self.advance(files)
        plan = install.plan_update("partner", target)
        real, calls = install.safe_replace, []

        def fail_from_the_second(source, destination):
            calls.append(destination)
            if len(calls) > 1:
                raise OSError("disk full")
            return real(source, destination)
        with mock.patch.object(install, "safe_replace", fail_from_the_second), self.assertRaises(OSError):
            install.apply_update("partner", plan)
        self.assertEqual(len(calls), 2, "the first file was replaced, the second was not")
        return target, plan

    def test_an_update_that_failed_halfway_and_was_repaired_to_the_old_pin_can_be_made_again(self):
        """A Repair writes the old version and, the old manifest not listing them, leaves the files the new one added: they
        are not the machine's, they are the very files the next update writes."""
        base = Path(paths.ROOT) / ".lab/catalogs/partner"
        target, _ = self.stopped_halfway({**AUDIT, "addons/ledger/README.md": "Changed\n"})
        self.assertEqual((base / "addons/audit/README.md").read_text(encoding="utf-8"), "Audit\n", "what the first call wrote")
        self.assertFalse((base / "addons/audit/compose.yml").exists())
        self.assertEqual(registry.lock_of("partner")["pending"], {"op": "update", "from": self.first, "to": target})
        with mock.patch.object(install, "safe_replace", side_effect=OSError("disk full")):   # the next start fails the same way
            install.recover()
        self.assertEqual(registry.inputs()["partner"]["state"], "damaged")
        install.repair("partner")
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["pending"], held["error"]), (self.first, None, None))
        self.intact("partner")
        self.assertTrue((base / "addons/audit/README.md").is_file(), "a repair does not take what the manifest does not list")
        self.assertNotIn("addons/audit/README.md", self.content("partner"))
        plan = install.plan_update("partner", target)
        self.assertIn("addons/audit/README.md", plan["files"]["add"])
        install.apply_update("partner", plan)   # it was `damaged`: "addons/audit/README.md is in the way of addons/audit/README.md"
        self.intact("partner")
        self.assertEqual(registry.lock_of("partner")["pin"], target)
        self.assertEqual(self.tree("partner")["addons/audit/README.md"], (READ_ONLY, b"Audit\n"), "read-only, as the catalog's")
        self.assertEqual(self.tree("partner")["addons/audit/compose.yml"][1], AUDIT["addons/audit/compose.yml"].encode())
        self.assertIn("addons/audit/README.md", self.content("partner"))

    def test_a_file_that_is_not_the_one_the_update_writes_is_still_in_its_way_whatever_it_looks_like(self):
        base = Path(paths.ROOT) / ".lab/catalogs/partner"
        target = self.advance(AUDIT)
        outside = self.root / "same.txt"
        outside.write_text("Audit\n", encoding="utf-8")
        wrong = {"another content": lambda place: place.write_text("Audit, but mine\n", encoding="utf-8"),
                 "one byte more": lambda place: place.write_text("Audit\n\n", encoding="utf-8"),
                 "a link to the same bytes": lambda place: place.symlink_to(outside),
                 "a folder with a file": lambda place: (place.mkdir(), (place / "mine.txt").write_text("m\n", encoding="utf-8"))}
        for name, make in wrong.items():
            with self.subTest(name):
                place = base / "addons/audit/README.md"
                (place.parent).mkdir(parents=True, exist_ok=True)
                make(place)
                before = self.tree("partner")
                plan = install.plan_update("partner", target)
                with self.assertRaises(registry.CatalogError) as raised:
                    install.apply_update("partner", plan)
                self.assertEqual(raised.exception.code, "damaged")
                self.assertEqual(raised.exception.detail["why"], "addons/audit/README.md is in the way of addons/audit/README.md")
                self.assertEqual(self.tree("partner"), before, "nothing was replaced")
                self.assertEqual((registry.lock_of("partner")["pin"], registry.lock_of("partner")["pending"]), (self.first, None))
                if place.is_dir() and not place.is_symlink():
                    shutil.rmtree(place)
                else:
                    place.unlink()
        self.assertEqual(outside.read_text(encoding="utf-8"), "Audit\n", "what a link led to is not touched")

    def test_a_pending_update_whose_commit_is_gone_marks_the_catalog_damaged_and_a_repair_clears_it(self):
        registry.set_lock("partner", pending={"op": "update", "from": self.first, "to": "a" * 40})
        install.recover()
        held = registry.lock_of("partner")
        self.assertEqual((held["pending"], held["error"], held["pin"]), (None, "damaged", self.first))
        self.assertEqual(registry.inputs()["partner"]["state"], "damaged")
        install.repair("partner")
        self.assertIsNone(registry.lock_of("partner")["error"])
        self.intact("partner")

    def test_a_pending_operation_is_not_the_end_of_the_start_for_the_other_catalogs(self):
        self.add("lab-new")
        registry.set_lock("partner", pending={"op": "update", "from": self.first, "to": "b" * 40})
        registry.set_lock("lab-new", error="unreachable")
        install.recover()
        self.assertEqual(registry.lock_of("lab-new")["error"], "unreachable")
        self.assertEqual(registry.lock_of("partner")["error"], "damaged")
        self.intact("lab-new")

    def test_a_download_whose_install_is_not_whole_starts_again(self):
        shutil.rmtree(registry.install_dir("partner"))
        registry.write_config({"schema": 1, "catalogs": [registry.entry("partner")]})
        registry.set_lock("partner", pin=None, pending={"op": "add", "from": None, "to": self.first})
        install.recover()
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["pending"], held["error"]), (None, None, None))
        self.assertEqual(registry.inputs()["partner"]["state"], "downloading")
        self.assertEqual(paths.ids("addon"), [])

    def test_an_error_before_anything_changed_changes_nothing(self):
        target = self.advance({"addons/ledger/README.md": "Changed\n"})
        plan = install.plan_update("partner", target)
        before = self.state("partner")
        self.patch(registry, "set_lock", mock.Mock(side_effect=OSError("disk full")))
        with self.assertRaises(OSError):
            install.apply_update("partner", plan)
        self.assertEqual(self.state("partner"), before)

    def test_a_remove_that_fails_in_the_middle_is_finished_by_running_it_again(self):
        self.machine("partner")
        real = install._take_out
        self.patch(install, "_take_out", mock.Mock(side_effect=OSError("disk")))
        with self.assertRaises(OSError):
            install.remove("partner")
        self.assertIsNone(registry.entry("partner"))
        self.patch(install, "_take_out", real)
        self.assertEqual(install.remove("partner"), {"warning": None})
        self.nothing("partner")
        self.assertTrue((Path(paths.ROOT) / ".lab/catalogs/partner" / install.MARKER).is_file())


class Hostile(Base):

    def setUp(self):
        super().setUp()
        self.installed("partner")
        self.base = Path(paths.ROOT) / ".lab/catalogs/partner"
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "precious.txt").write_text("precious\n", encoding="utf-8")
        (self.root / "precious.txt").write_text("also precious\n", encoding="utf-8")

    def poison(self):
        """A manifest that lists paths that lead out of the install."""
        found = json.loads((self.base / ".installed.json").read_text(encoding="utf-8"))
        for path in ("../../../outside/precious.txt", "../../../precious.txt", "/etc/hosts", "a/../../b", ""):
            found["files"][path] = {"mode": "100644", "oid": "a" * 40, "sha256": "0" * 64, "size": 1}
        (self.base / ".installed.json").write_text(json.dumps(found), encoding="utf-8")

    def untouched(self):
        self.assertEqual((self.outside / "precious.txt").read_text(encoding="utf-8"), "precious\n")
        self.assertEqual((self.root / "precious.txt").read_text(encoding="utf-8"), "also precious\n")
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()), ["precious.txt"])

    def test_a_manifest_that_leads_out_of_the_install_deletes_nothing_on_a_remove(self):
        self.poison()
        install.remove("partner", keep_settings=True)
        self.untouched()

    def test_nor_on_a_repair_or_an_update(self):
        self.poison()
        install.repair("partner")
        self.untouched()
        address = gitx.parse_address(self.remotes["partner"].url)
        target = self.remotes["partner"].commit({"addons/ledger/README.md": "Changed\n"})
        bare.fetch(address, "main", registry.store_dir("partner"))
        self.poison()
        install.apply_update("partner", install.plan_update("partner", target))
        self.untouched()

    def test_nor_when_the_folder_is_swept_or_added_over(self):
        self.poison()
        registry.write_config({"schema": 1, "catalogs": []})
        install.recover()
        self.untouched()
        self.add("partner")
        self.untouched()

    def test_nor_when_a_working_copy_takes_its_place(self):
        self.poison()
        install.shadow("partner", str(self.folder_catalog("partner")))
        self.untouched()

    def test_a_link_planted_in_a_unit_folder_is_never_written_through_by_an_update(self):
        (self.base / "addons/ledger/README.md").unlink()
        (self.base / "addons/ledger/README.md").symlink_to(self.outside / "precious.txt")
        address = gitx.parse_address(self.remotes["partner"].url)
        target = self.remotes["partner"].commit({"addons/ledger/README.md": "Changed\n"})
        bare.fetch(address, "main", registry.store_dir("partner"))
        install.apply_update("partner", install.plan_update("partner", target))
        self.untouched()
        self.assertFalse((self.base / "addons/ledger/README.md").is_symlink())

    def test_a_folder_that_became_a_link_to_somewhere_else_is_not_written_through(self):
        shutil.move(str(self.base / "addons"), str(self.outside / "addons"))
        (self.base / "addons").symlink_to(self.outside / "addons", target_is_directory=True)
        before = sorted(p.relative_to(self.outside).as_posix() for p in self.outside.rglob("*"))
        address = gitx.parse_address(self.remotes["partner"].url)
        target = self.remotes["partner"].commit({"addons/ledger/README.md": "Changed\n"})
        bare.fetch(address, "main", registry.store_dir("partner"))
        with self.assertRaises(registry.CatalogError) as raised:
            install.apply_update("partner", install.plan_update("partner", target))
        self.assertEqual(raised.exception.code, "damaged")
        self.assertEqual(sorted(p.relative_to(self.outside).as_posix() for p in self.outside.rglob("*")), before)
        self.assertEqual((self.outside / "addons/ledger/README.md").read_bytes(),
                         (fixtures.CATALOGS / "partner/addons/ledger/README.md").read_bytes())

    def test_an_install_that_is_a_link_is_not_written_through(self):
        shutil.move(str(self.base), str(self.outside / "moved"))
        self.base.symlink_to(self.outside / "moved", target_is_directory=True)
        with self.assertRaises(registry.CatalogError) as raised:
            install.repair("partner")
        self.assertEqual(raised.exception.code, "damaged")


class Promise(Base):
    """Nothing is written, read for content or deleted that the manifest does not list, except Delete them too."""

    def opened(self, work):
        """The paths work() opened, by os.open and by open."""
        seen = []
        real_os, real_open = os.open, open

        def os_open(path, *args, **kwargs):
            seen.append(os.fspath(path) if not isinstance(path, int) else None)
            return real_os(path, *args, **kwargs)

        def builtin_open(file, *args, **kwargs):
            seen.append(os.fspath(file) if not isinstance(file, int) else None)
            return real_open(file, *args, **kwargs)
        with mock.patch("os.open", os_open), mock.patch("builtins.open", builtin_open):
            work()
        return [path for path in seen if path]

    def test_the_machine_files_are_never_opened_by_any_operation(self):
        self.add("partner")
        made = self.machine("partner", **{".env": "SECRET=1\n", ".state/created.json": "{}\n", "secrets/token": "t\n",
                                          "server.key": "KEY\n", "notes.txt": "mine\n"})
        base = Path(paths.ROOT) / ".lab/catalogs/partner"
        address = gitx.parse_address(self.remotes["partner"].url)
        target = self.remotes["partner"].commit({**AUDIT, "addons/ledger/README.md": "Changed\n"})
        bare.fetch(address, "main", registry.store_dir("partner"))
        names = {Path(rel).name for rel in made}

        def everything():
            install.apply_update("partner", install.plan_update("partner", target))
            install.apply_update("partner", install.plan_update("partner", registry.lock_of("partner")["previous"]))
            (base / "addons/ledger/README.md").unlink()
            install.repair("partner")
            install.verify("partner", ["addon:ledger"])
            install.recover()
            install.shadow("partner", str(self.folder_catalog("partner")))
            install.unshadow("partner")
            install.remove("partner", keep_settings=True)
            staged, addr, roots = self.prepare("partner")
            install.commit_add(staged, addr, "main", "public", roots, meta_of("partner"))
            install.remove("partner", keep_settings=True)
            install.recover()
        opened = self.opened(everything)
        mine = [path for path in opened if Path(path).name in names or "/.state/" in path or "/secrets/" in path]
        # shadow and unshadow move the folders with a rename: nothing is opened to be read or written
        self.assertEqual(mine, [], "a machine file was opened")
        self.assertTrue(opened, "the check saw nothing")

    def test_every_machine_file_is_where_it_was_with_the_content_it_had_after_every_operation(self):
        self.add("partner")
        made = self.machine("partner", **{".env": "SECRET=1\n", ".state/created.json": "{}\n", "secrets/token": "t\n"})
        base = Path(paths.ROOT) / ".lab/catalogs/partner"
        stamps = {rel: ((base / rel).stat().st_ino, (base / rel).read_text(encoding="utf-8")) for rel in made}
        address = gitx.parse_address(self.remotes["partner"].url)
        target = self.remotes["partner"].commit({**AUDIT, "addons/ledger/README.md": "Changed\n"})
        bare.fetch(address, "main", registry.store_dir("partner"))
        for work in (lambda: install.apply_update("partner", install.plan_update("partner", target)),
                     lambda: install.apply_update("partner", install.plan_update("partner", registry.lock_of("partner")["previous"])),
                     lambda: (base / "addons/ledger/compose.yml").unlink() or install.repair("partner"),
                     install.recover,
                     lambda: install.remove("partner", keep_settings=True),
                     lambda: self.add("partner")):
            work()
            for rel, (inode, text) in stamps.items():
                self.assertEqual(((base / rel).stat().st_ino, (base / rel).read_text(encoding="utf-8")), (inode, text), rel)

    def test_a_remove_that_deletes_them_is_the_only_thing_that_does(self):
        self.add("partner")
        self.machine("partner")
        install.remove("partner", keep_settings=False)
        self.assertFalse((Path(paths.ROOT) / ".lab/catalogs/partner").exists())


class Concurrency(Base):

    def test_a_second_change_waits_for_the_first_and_does_not_see_it_half_done(self):
        self.installed("partner")
        address = gitx.parse_address(self.remote("partner").url)
        target = self.remotes["partner"].commit({"addons/ledger/README.md": "Changed\n"})
        bare.fetch(address, "main", registry.store_dir("partner"))
        plan = install.plan_update("partner", target)
        inside, release, order = threading.Event(), threading.Event(), []

        def hook(label):
            if label == "update:file":
                inside.set()
                self.assertTrue(release.wait(10))
        self.patch(install, "STEP", hook)

        def update():
            install.apply_update("partner", plan)
            order.append("update")

        def remove():
            install.remove("partner")
            order.append("remove")
        first = threading.Thread(target=update)
        first.start()
        self.assertTrue(inside.wait(10))
        second = threading.Thread(target=remove)
        second.start()
        second.join(0.5)
        self.assertTrue(second.is_alive(), "the second change waits")
        self.assertEqual(order, [])
        release.set()
        first.join(10)
        second.join(10)
        self.assertEqual(order, ["update", "remove"])
        self.assertIsNone(registry.entry("partner"))


class FolderAdds(Base):

    def test_a_file_at_the_name_of_a_folder_catalog_is_never_replaced(self):
        folder = self.folder_catalog("partner")
        address = gitx.parse_address(str(folder))
        staged = install.stage(address, "partner", None, "", [])
        target = registry.install_dir("partner")
        target.write_text("a file", encoding="utf-8")
        with self.assertRaises(registry.CatalogError) as raised:
            install.commit_add(staged, address, None, "private", [], meta_of("partner"))
        self.assertEqual(raised.exception.code, "name_taken")
        self.assertEqual(target.read_text(encoding="utf-8"), "a file")

    def test_a_stage_link_that_points_elsewhere_is_not_the_one_that_was_judged(self):
        folder = self.folder_catalog("partner")
        address = gitx.parse_address(str(folder))
        staged = install.stage(address, "partner", None, "", [])
        os.unlink(staged.path)
        os.symlink(self.root, staged.path)
        with self.assertRaises(registry.CatalogError) as raised:
            install.commit_add(staged, address, None, "private", [], meta_of("partner"))
        self.assertEqual(raised.exception.code, "ticket_expired")
        self.assertIsNone(registry.entry("partner"))

    def test_the_same_folder_cannot_be_added_twice(self):
        folder = self.folder_catalog("partner")
        address = gitx.parse_address(str(folder))
        install.commit_add(install.stage(address, "partner", None, "", []), address, None, "private", [], meta_of("partner"))
        with self.assertRaises(registry.CatalogError) as raised:
            install.commit_add(install.stage(address, "partner-two", None, "", []), address, None, "private", [],
                               meta_of("partner", id="partner-two"))
        self.assertEqual(raised.exception.code, "already_added")


if __name__ == "__main__":
    unittest.main()
