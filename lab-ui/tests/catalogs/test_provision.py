"""Catalogs/firstrun.py, the download of a listed catalog  and the thread that makes it. `provision` runs
against bare repositories made on this machine that the default file lists, with git as the Lab runs it: the phases of its
progress, the checks that the repository is the catalog expected, the newest version this Lab reads, a stalled fetch, no
git, no credential prompt (the environment of every command is asserted), the ssh form when https is refused, a crash at
every step of the install, and no lock held while git talks to a network. The worker runs against a fake clock for the
schedule, then once for real. Everything is on disk in a temporary project folder (a visible skip without git): no
network, no Docker daemon."""

import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

from lab_ui.docker import compose, envfiles
from lab_ui.library import layers, project
from lab_ui import paths
from lab_ui.catalogs import defaults, firstrun, gitx, install, manifest, registry, service
from lab_ui.catalogs import store as bare
from lab_ui.catalogs.registry import CatalogError

from .. import Case, fixtures, gitfixture

CONTRACT = {"contract": 1, "contract_min": 1}
OPEN = "No access to example/demos. Run gh auth login in a terminal, then try again."


class Crash(BaseException):
    """The process died here. A BaseException, so that no handler of the code under test cleans up."""


def stop_at(label):
    """A hook for `install.STEP` that stops the run at the first step with this label."""
    def hook(step):
        if step == label:
            raise Crash(label)
    return hook


def read_catalog(path, address=None, borrowed=(), published=False):
    """manifest.read_catalog without Compose: the scalars of the file, as the fixtures write them."""
    text = (Path(paths.ROOT) / path).read_text(encoding="utf-8")

    def scalar(key):
        return re.search(rf"^  {key}: (.+)$", text, re.M).group(1).strip()
    return {"schema": 1, "id": scalar("id"), "name": scalar("name"), "description": "", "version": scalar("version"),
            "lab": int(scalar("lab")), "license": None, "images": None, "contribute": "pull-request",
            "shared_settings": [], "moved_from": [], "canary": [], "requires": {"catalogs": {}}, "unknown": [], "unknown_values": []}


def file_address(value):
    """defaults._address for a test: an address of a repository on this machine is one too (production lists https
    only)."""
    return gitx.parse_address(value).url if isinstance(value, str) else None


def free(lock):
    """Whether no thread holds `lock` now. Asked from another thread, because a lock that is reentrant is free to its
    holder."""
    answer = []

    def ask():
        got = lock.acquire(blocking=False)
        answer.append(got)
        if got:
            lock.release()
    thread = threading.Thread(target=ask)
    thread.start()
    thread.join()
    return answer[0]


def unlocked():
    """Whether the thread that asks holds none of the locks of a change of catalogs: the lock of this process, the
    advisory lock of the others, the project's."""
    return free(registry.LOCK) and not getattr(registry._held, "held", {}) and free(project._lock)


class Recorded(dict):
    """PROGRESS that remembers every value put in it."""

    def __init__(self, *args):
        super().__init__(*args)
        self.sets = []

    def __setitem__(self, key, value):
        self.sets.append((key, dict(value)))
        super().__setitem__(key, value)


class Served:
    """What a test of the download needs: a project folder, git that reads local repositories, and a catalog served by a
    bare repository that the default file lists (`demos`, the first and so the default one)."""

    NS = "demos"

    def world(self, files=None, ns=None, **entry):
        """The project folder, the default file listing `ns` at the address of its remote, and the remote. `entry` sets
        keys of the listed entry (a branch, roots)."""
        gitfixture.require()
        ns = ns or self.NS
        self.ns = ns
        self.folder(paths, compose)
        self.patch(paths, "_default", (None, None))
        self.home = gitfixture.isolate(self)
        self.patch(gitx, "_ending", False)   # a test of the exit function elsewhere leaves it for good
        self.patch(manifest, "read_catalog", read_catalog)
        self.patch(defaults, "_address", file_address)
        self.patch(firstrun, "_contract", return_value=dict(CONTRACT))
        self.patch(firstrun, "PROGRESS", {})
        self.patch(firstrun, "_lines", {})
        self.patch(firstrun, "_worker", None)
        (self.root / ".lab" / "catalogs").mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix="gravitee-lab-remotes-")).resolve()
        self.addCleanup(shutil.rmtree, scratch, True)
        self.scratch = scratch
        self.remote = fixtures.Remote(gitfixture.make_repo(scratch / f"{ns}.git", files or gitfixture.catalog_files(ns)))
        self.listed(ns, url=self.remote.url, **entry)
        return self.remote

    def listed(self, ns, **entry):
        """catalogs.default.json: these catalogs, in this order (`ns` and then the ids in `entry["also"]`), the first
        owning the bare ids. Every key of `entry` is set in every row."""
        also = entry.pop("also", ())
        rows = []
        for one in (ns, *also):
            rows.append({"id": one, "name": "Demos" if one == ns else one, "description": "Playgrounds of the demos",
                         "url": entry.get("url", f"https://example.invalid/team/{one}.git"), "branch": "main",
                         **{key: value for key, value in entry.items() if key != "url"}})
        (self.root / "catalogs.default.json").write_text(json.dumps({"schema": 1, "catalogs": rows}), encoding="utf-8")

    def config(self):
        file = self.root / ".lab" / "catalogs.json"
        return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else None

    def lock(self, ns=None):
        return registry.lock_of(ns or self.ns)

    def started(self, ns=None):
        """The first start, as the server makes it: the intent written, the catalogs with no copy marked."""
        firstrun.reconcile()
        return self.lock(ns)

    def silent(self):
        """git and the network may not be run: any process the Lab starts is a failure of the test."""
        self.patch(gitx.subprocess, "Popen", side_effect=AssertionError("a process was started"))

    def fake_clock(self):
        """A clock the test moves."""
        now = [1000.0]
        return now, lambda: now[0]


class Provision(Served, Case):

    def setUp(self):
        self.world()

    # -- the download ----------------------------------------------------------------------------------------------

    def test_a_listed_catalog_is_downloaded_and_installed(self):
        self.started()
        row = firstrun.provision(self.ns)
        held = self.lock()
        self.assertEqual((held["pin"], held["tip"]), (self.remote.head(),) * 2)
        self.assertEqual(held["roots"], sorted(self.remote.git("rev-list", "--max-parents=0", "HEAD").split()))
        self.assertEqual((held["audience"], held["name"], held["version"], held["lab"]), ("public", "Acme Hub", "1.0.0", 1))
        self.assertEqual((held["pending"], held["error"]), (None, None))
        self.assertTrue(held["installed"] and held["checked"])
        entry = registry.entry(self.ns)
        self.assertEqual((entry["kind"], entry["url"], entry.get("host")), ("git", self.remote.url, None))
        self.assertEqual(registry.units_of(self.ns), {"addon": ["echo"], "setup": ["pair"], "playground": ["shop"]})
        found = registry.manifest(self.ns)
        self.assertEqual(found["commit"], held["pin"])
        self.assertEqual(install.verify(self.ns, list(found["units"])), [])
        for rel in found["files"]:
            self.assertEqual(stat.S_IMODE((self.root / ".lab/catalogs" / self.ns / rel).stat().st_mode) & 0o222, 0, rel)
        self.assertEqual((row["id"], row["name"]), (self.ns, "Acme Hub"))
        self.assertEqual(row["state"]["key"], "uptodate")
        self.assertEqual(firstrun.PROGRESS, {}, "an attempt that ended leaves nothing on the bar")
        self.assertEqual(firstrun.content({}, {})["key"], "ready")

    def test_a_download_asked_for_by_name_is_not_skipped_by_the_variable_of_the_start(self):
        # LAB_NO_FIRST_RUN is for the start of the Lab: a tool that names the catalog (make catalogs) gets it
        registry.write_config({"schema": 1, "catalogs": [{"id": self.ns, "kind": "git", "url": self.remote.url}]})
        self.start(mock.patch.dict(os.environ, {firstrun.OPT_OUT: "1"}))
        firstrun.provision(self.ns)
        self.assertEqual(self.lock()["pin"], self.remote.head())

    def test_the_progress_goes_through_the_phases_and_only_grows(self):
        self.patch(firstrun, "PROGRESS", Recorded())
        self.started()
        firstrun.provision(self.ns)
        seen = [(phase["phase"], phase["fraction"]) for ns, phase in firstrun.PROGRESS.sets if ns == self.ns]
        self.assertTrue(seen)
        self.assertEqual(seen[0], ("probe", 0.02), "reconcile put it there before anything else")
        fractions = [fraction for _, fraction in seen]
        self.assertEqual(fractions, sorted(fractions), "the bar never goes back")
        self.assertEqual(fractions[-1], 1.0)
        order = list(dict.fromkeys(phase for phase, _ in seen))
        self.assertEqual(order, ["probe", "fetch", "stage", "commit"])
        for phase, fraction in seen:
            low, high = firstrun.PHASES[phase]
            self.assertTrue(low <= fraction <= high, (phase, fraction))
        self.assertIn(("fetch", 0.85), seen, "the fetch ends at the start of the stage")

    def test_the_rows_say_where_the_download_is_while_it_runs(self):
        seen = []
        real = bare.fetch

        def fetch(address, branch, store, stall=30, cap=600, progress=None):
            tip = real(address, branch, store, stall, cap, progress)
            (row,) = [one for one in service.describe({}, {}) if one.get("id") == self.ns]
            seen.append((row["state"]["key"], row["progress"], firstrun.content({}, {})))
            return tip
        self.patch(bare, "fetch", fetch)
        self.started()
        firstrun.provision(self.ns)
        ((state, progress, said),) = seen
        self.assertEqual(state, "downloading", "the row and the content tell the same")
        self.assertEqual((said["key"], said["progress"]), ("downloading", progress))
        self.assertEqual(progress, 0.85, "the fetch is done, the stage is next")

    def test_an_attempt_nobody_sees_writes_no_progress(self):
        self.patch(firstrun, "PROGRESS", Recorded())
        registry.write_config({"schema": 1, "catalogs": [{"id": self.ns, "kind": "git", "url": self.remote.url}]})
        firstrun.provision(self.ns)
        self.assertEqual(firstrun.PROGRESS.sets, [], "an automatic retry leaves no mark: the state stays what it was")
        self.assertEqual(self.lock()["pin"], self.remote.head())

    def test_a_catalog_that_has_a_copy_is_left_alone(self):
        self.started()
        firstrun.provision(self.ns)
        self.silent()
        before = (registry.read_lock(), registry.read_config())
        row = firstrun.provision(self.ns)
        self.assertEqual((registry.read_lock(), registry.read_config()), before)
        self.assertEqual(row["id"], self.ns)

    def test_a_catalog_the_person_does_not_have_is_downloaded_and_its_entry_written(self):
        registry.write_config({"schema": 1, "catalogs": []})
        self.assertEqual([one["id"] for one in defaults.not_added()], [self.ns])
        row = firstrun.provision(self.ns)
        entry = registry.entry(self.ns)
        self.assertEqual((entry["kind"], entry["url"], entry["auto"]), ("git", self.remote.url, True))
        self.assertNotIn("host", entry, "a listed catalog has the allowance by being listed")
        self.assertTrue(registry.lock_of(self.ns)["pin"])
        self.assertEqual(row["id"], self.ns)
        self.assertEqual(sorted(os.listdir(self.root / ".lab/cache/git")), [f"{self.ns}.git"], "no store of a ticket left")
        self.assertEqual(defaults.not_added(), [])

    def test_a_catalog_the_engine_does_not_list_is_not_downloaded_from_nowhere(self):
        with self.assertRaises(CatalogError) as caught:
            firstrun.provision("elsewhere")
        self.assertEqual((caught.exception.code, caught.exception.message),
                         ("not_listed", "elsewhere is not a catalog this Lab lists."))
        self.assertIsNone(registry.entry("elsewhere"))
        with self.assertRaises(ValueError):
            firstrun.provision("../demos")

    def test_the_branch_of_the_file_is_the_one_fetched_and_none_means_the_default(self):
        self.remote.git("branch", "stable", "HEAD")
        self.remote.commit({"README.md": "more\n"}, "More on main")
        stable = self.remote.git("rev-parse", "refs/heads/stable")
        self.listed(self.ns, url=self.remote.url, branch="stable")
        self.started()
        firstrun.provision(self.ns)
        self.assertEqual(self.lock()["pin"], stable, "the branch the file asks for")
        self.world()
        self.remote.commit({"README.md": "more\n"}, "More on main")
        self.listed(self.ns, url=self.remote.url, branch=None)
        self.started()
        firstrun.provision(self.ns)
        self.assertEqual(self.lock()["pin"], self.remote.head(), "no branch: the default one of the remote")

    def test_a_branch_the_remote_lacks_is_branch_missing(self):
        self.listed(self.ns, url=self.remote.url, branch="gone")
        self.started()
        with self.assertRaises(CatalogError) as caught:
            firstrun.provision(self.ns)
        self.assertEqual(caught.exception.code, "branch_missing")
        self.assertIn("gone", caught.exception.message)
        self.assertEqual(self.lock()["error"], "branch_missing")

    def test_the_times_are_those_of_the_spec(self):
        asked = []
        real_ls, real_fetch = gitx.ls_remote, bare.fetch

        def ls_remote(address, branch=None, anonymous=False, timeout=20):
            asked.append(("ls-remote", anonymous, timeout))
            return real_ls(address, branch, anonymous, timeout)

        def fetch(address, branch, store, stall=30, cap=600, progress=None):
            asked.append(("fetch", stall, cap))
            return real_fetch(address, branch, store, stall, cap, progress)
        self.patch(gitx, "ls_remote", ls_remote)
        self.patch(bare, "fetch", fetch)
        self.started()
        firstrun.provision(self.ns)
        self.assertEqual(asked, [("ls-remote", False, 20.0), ("ls-remote", True, 20.0), ("fetch", 30.0, 600.0)],
                         "the probe, the audience as a stranger, then a fetch that stalls at 30 s and ends at 10 min")

    # -- is it the catalog expected --------------------------------------------------------------------------------

    def test_an_address_that_serves_another_catalog_is_wrong_catalog_and_nothing_is_installed(self):
        stranger = fixtures.Remote(gitfixture.make_repo(self.scratch / "other.git", gitfixture.catalog_files("other")))
        self.listed(self.ns, url=stranger.url)
        self.started()
        with self.assertRaises(CatalogError) as caught:
            firstrun.provision(self.ns)
        self.assertEqual((caught.exception.code, caught.exception.status), ("wrong_catalog", 409))
        self.assertEqual(caught.exception.message,
                         "other is not the catalog this Lab expects. Run make update, then try again.")
        self.assertEqual(self.lock()["pin"], None)
        self.assertEqual(self.lock()["error"], "wrong_catalog")
        self.assertFalse((self.root / ".lab/catalogs" / self.ns).exists(), "nothing of it is put in place")
        self.assertFalse((self.root / ".lab/catalogs" / f".stage-{self.ns}").exists(), "the stage goes")
        self.assertEqual(registry.inputs()[self.ns]["state"], "wrong_catalog")
        said = firstrun.content({}, {})
        self.assertEqual((said["key"], said["code"], said["line"]), ("failed", "wrong_catalog", caught.exception.message))

    def test_roots_that_differ_from_the_ones_the_file_pins_are_wrong_catalog(self):
        roots = sorted(self.remote.git("rev-list", "--max-parents=0", "HEAD").split())
        self.listed(self.ns, url=self.remote.url, roots=["a" * 40])
        self.started()
        with self.assertRaises(CatalogError) as caught:
            firstrun.provision(self.ns)
        self.assertEqual(caught.exception.code, "wrong_catalog")
        self.assertEqual((self.lock()["pin"], self.lock()["error"]), (None, "wrong_catalog"))
        self.assertFalse((self.root / ".lab/cache/git" / f"{self.ns}.git").exists(), "another repository is not kept")
        self.listed(self.ns, url=self.remote.url, roots=roots)
        firstrun.reconcile()
        firstrun.provision(self.ns)
        self.assertEqual(self.lock()["pin"], self.remote.head(), "the roots the file pins")

    def test_a_repository_without_a_catalog_file_is_not_a_catalog(self):
        self.world(files={"README.md": "nothing\n"})
        self.started()
        with self.assertRaises(CatalogError) as caught:
            firstrun.provision(self.ns)
        self.assertEqual(caught.exception.code, "not_catalog")
        self.assertEqual(self.lock()["error"], "not_catalog")

    # -- the compatible pin ----------------------------------------------------------------------------------------

    def newer(self, lab=99, extra=None):
        """A commit on top of the catalog whose catalog.yml asks for a Lab level."""
        text = gitfixture.catalog_files(self.ns)["catalog.yml"].replace("lab: 1", f"lab: {lab}")
        return self.remote.commit({"catalog.yml": text, **(extra or {})}, "A version for a newer Lab")

    def test_when_the_tip_needs_a_newer_lab_the_newest_version_this_lab_reads_is_installed(self):
        older = self.remote.head()
        tip = self.newer(extra={"addons/later/compose.yml": "x-gravitee: {kind: addon, name: Later}\nservices: {}\n"})
        self.started()
        firstrun.provision(self.ns)
        held = self.lock()
        self.assertEqual((held["pin"], held["tip"]), (older, tip), "the tip stays the tip, the pin is the readable one")
        self.assertEqual(registry.units_of(self.ns)["addon"], ["echo"], "what only the newer version holds is not there")
        self.assertEqual(held["seen"], {"target": older, "update": False, "tip": "new", "version": "1.0.0"})
        row = service.describe({}, {})[0]
        self.assertEqual(row["state"]["key"], "lab_new", "the row says so at once, before the first check")

    def test_when_the_tip_uses_what_this_lab_does_not_read_the_newest_version_it_reads_is_installed(self):
        """The first start reads the versions by their files too (`manifest.offer` with `install.readable`): a tip whose unit uses
        a key of x-gravitee this Lab does not know is passed over, though its catalog.yml did not change."""
        # The Docker CLI finds its Compose plugin in the person's configuration folder, which the isolated home hides
        self.start(mock.patch.dict(os.environ, {"DOCKER_CONFIG": os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".docker")}))
        self.patch(fixtures, "_compose", [])
        fixtures.need_compose()
        self.patch(project, "ROOT", self.root)   # the units of a version are read as the project reads them, in this folder
        shutil.copy(fixtures.CORE.parent / "contract.lock", self.root / "contract.lock")
        older = self.remote.head()
        tip = self.remote.commit({"addons/later/compose.yml": "x-gravitee: {kind: addon, name: Later, badge: gold}\nservices: {}\n"},
                                 "A unit for a newer Lab")
        self.started()
        firstrun.provision(self.ns)
        held = self.lock()
        self.assertEqual((held["pin"], held["tip"]), (older, tip))
        self.assertEqual(registry.units_of(self.ns)["addon"], ["echo"])

    def test_when_no_version_is_readable_it_is_no_readable_version_in_the_words_of_the_first_start(self):
        self.world(files={**gitfixture.catalog_files(self.NS), "catalog.yml":
                          gitfixture.catalog_files(self.NS)["catalog.yml"].replace("lab: 1", "lab: 99")})
        self.started()
        with self.assertRaises(CatalogError) as caught:
            firstrun.provision(self.ns)
        self.assertEqual(caught.exception.code, "no_readable_version")
        self.assertEqual(caught.exception.message, "The catalog needs a newer Gravitee Lab. Run make update, then restart the Lab.")
        self.assertEqual(self.lock()["error"], "no_readable_version")
        self.assertEqual(registry.inputs()[self.ns]["state"], "lab_new")
        self.assertFalse((self.root / ".lab/catalogs" / self.ns).exists())

    def test_a_catalog_older_than_this_lab_reads_has_the_listed_wording(self):
        self.patch(firstrun, "_contract", return_value={"contract": 3, "contract_min": 2})
        self.started()
        with self.assertRaises(CatalogError) as caught:
            firstrun.provision(self.ns)
        self.assertEqual(caught.exception.code, "lab_old")
        self.assertEqual(caught.exception.message, "The catalog is not ready for this version of the Lab. Try again later.")
        self.assertEqual(registry.inputs()[self.ns]["state"], "lab_old")

    # -- no git, no stall, no prompt -------------------------------------------------------------------------------

    def test_no_git_is_known_before_anything_is_asked_of_the_network(self):
        self.patch(gitx, "executable", return_value=None)
        self.silent()
        self.started()
        with self.assertRaises(CatalogError) as caught:
            firstrun.provision(self.ns)
        self.assertEqual((caught.exception.code, caught.exception.message),
                         ("git_missing", "Git is not installed. Install it, then try again."))
        self.assertEqual(self.lock()["error"], "git_missing")
        self.assertEqual(registry.inputs()[self.ns]["state"], "needs_git")
        self.assertFalse((self.root / ".lab/cache").exists(), "nothing was made")

    def test_a_git_that_is_too_old_names_its_version(self):
        self.patch(gitx, "version", return_value=(2, 20, 1))
        self.started()
        with self.assertRaises(CatalogError) as caught:
            firstrun.provision(self.ns)
        self.assertEqual((caught.exception.code, caught.exception.message),
                         ("git_old", "Git 2.20.1 is too old. Install Git 2.25 or later, then try again."))
        self.assertEqual(registry.inputs()[self.ns]["state"], "needs_git")
        self.assertEqual(firstrun.content({}, {})["line"], caught.exception.message,
                         "the line of the content is the one of the attempt")

    def test_a_fetch_that_stalls_is_a_timeout_and_leaves_nothing(self):
        gitfixture.fake_git(self, 'case "$*" in *" fetch "*) echo "remote: starting" >&2; sleep 30;; *) exec "$REAL" "$@";; esac')
        self.patch(firstrun, "STALL", 0.5)
        self.started()
        began = time.monotonic()
        with self.assertRaises(CatalogError) as caught:
            firstrun.provision(self.ns)
        self.assertLess(time.monotonic() - began, 20)
        self.assertEqual((caught.exception.code, caught.exception.status), ("timeout", 502))
        self.assertEqual(caught.exception.message, "localhost did not answer in time. Try again.")
        self.assertEqual((self.lock()["pin"], self.lock()["error"]), (None, "timeout"))
        self.assertFalse((self.root / ".lab/cache/git" / f"{self.ns}.git").exists(), "a first attempt that failed leaves no store")
        self.assertEqual(registry.inputs()[self.ns]["state"], "unreachable")

    def test_every_command_runs_without_a_terminal_a_question_or_a_credential_prompt(self):
        ran = []
        real = gitx.subprocess.Popen

        def popen(argv, **how):
            ran.append((list(argv), dict(how)))
            return real(argv, **how)
        self.patch(gitx.subprocess, "Popen", popen)
        # What the shell the Lab was started from carries must reach no command
        spoilt = {"GIT_ASKPASS": "/usr/bin/ask", "SSH_ASKPASS": "/usr/bin/ask", "GIT_TERMINAL_PROMPT": "1",
                  "GCM_INTERACTIVE": "always", "DISPLAY": ":0", "GIT_SSH_COMMAND": "ssh", "GIT_CONFIG_COUNT": "1",
                  "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": "osxkeychain"}
        self.start(mock.patch.dict(os.environ, spoilt))
        self.started()
        firstrun.provision(self.ns)
        git = [(argv, how) for argv, how in ran if os.path.basename(argv[0]) == "git"]
        self.assertGreater(len(git), 10, "the download ran git, many times")
        for argv, how in git:
            env = how["env"]
            self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0", argv)
            self.assertEqual(env["GIT_ASKPASS"], "", "an empty askpass is no askpass at all", )
            self.assertEqual(env["SSH_ASKPASS_REQUIRE"], "never", argv)
            self.assertEqual(env["GCM_INTERACTIVE"], "never", argv)
            self.assertIn("BatchMode=yes", env["GIT_SSH_COMMAND"], argv)
            self.assertNotIn("SSH_ASKPASS", env)
            self.assertNotIn("DISPLAY", env)
            self.assertFalse([name for name in env if name.startswith("GIT_CONFIG")], "no configuration from the shell")
            if "ls-remote" in argv or "fetch" in argv:   # the commands that meet a server, and could be asked for a password
                self.assertEqual(how["stdin"], subprocess.DEVNULL, "git is given no input")
            self.assertTrue(how["start_new_session"], "no controlling terminal to ask on")
            self.assertNotIn("credential.helper=osxkeychain", " ".join(argv))
        asked = [argv for argv, _ in git if "ls-remote" in argv]
        self.assertTrue(any("credential.helper=" in argv for argv in asked), "the audience is asked as a stranger")

    def test_the_environment_of_git_is_asserted_on_its_own_too(self):
        env = gitx.environment()
        self.assertEqual((env["GIT_TERMINAL_PROMPT"], env["GIT_ASKPASS"], env["SSH_ASKPASS_REQUIRE"], env["GCM_INTERACTIVE"]),
                         ("0", "", "never", "never"))
        self.assertIn("-o BatchMode=yes", env["GIT_SSH_COMMAND"])

    # -- the ssh form ----------------------------------------------------------------------------------------------

    def github(self, refuse=("no_access",), ssh_refuses=None, ssh_code="ssh_denied"):
        """The default file lists a github.com address; the https form is refused with `refuse` and the ssh form is served by
        the remote of this test (or refused with `ssh_code`). Answers the list of (form, answer) of every ls-remote."""
        self.listed(self.ns, url="https://github.com/example/demos.git")
        real_ls, real_fetch = gitx.ls_remote, bare.fetch
        local = gitx.parse_address(self.remote.url)
        asked = []

        def served(address):
            return local if gitx.is_ssh(address) and ssh_refuses is None else address

        def ls_remote(address, branch=None, anonymous=False, timeout=20):
            asked.append((address.url, anonymous))
            if anonymous:
                raise gitx.fail("no_access", address)
            if gitx.is_ssh(address):
                if ssh_refuses is not None:
                    raise gitx.fail(ssh_code, address)
                return real_ls(local, branch, anonymous, timeout)
            raise gitx.fail(refuse[0], address)

        def fetch(address, branch, store, stall=30, cap=600, progress=None):
            self.assertTrue(gitx.is_ssh(address), "only the form that answered is fetched")
            return real_fetch(local, branch, store, stall, cap, progress)
        self.patch(gitx, "ls_remote", ls_remote)
        self.patch(bare, "fetch", fetch)
        return asked

    def test_https_refused_and_ssh_accepted_installs_and_records_the_form_that_answered(self):
        asked = self.github()
        self.started()
        firstrun.provision(self.ns)
        self.assertEqual(self.lock()["via"], "ssh")
        self.assertEqual(self.lock()["pin"], self.remote.head())
        self.assertEqual([url for url, anonymous in asked if not anonymous],
                         ["https://github.com/example/demos.git", "git@github.com:example/demos.git"])
        self.assertNotIn("git@", json.dumps(self.config()), "the form that answered is never written to the intent")

    def test_the_next_run_starts_with_the_form_that_answered(self):
        asked = self.github()
        self.started()
        registry.set_lock(self.ns, via="ssh")
        firstrun.provision(self.ns)
        self.assertEqual([url for url, anonymous in asked if not anonymous][0], "git@github.com:example/demos.git")
        self.assertEqual(len([url for url, anonymous in asked if not anonymous]), 1, "it answered: no second form")
        self.assertEqual(self.lock()["via"], "ssh")

    def test_the_form_that_answers_is_https_and_it_is_recorded_for_a_listed_catalog(self):
        # A public catalog answers over https: nothing is asked over ssh, and the next run starts with https
        self.listed(self.ns, url="https://github.com/example/demos.git")
        real_ls, real_fetch = gitx.ls_remote, bare.fetch
        local = gitx.parse_address(self.remote.url)
        asked = []
        self.patch(gitx, "ls_remote", lambda a, b=None, anonymous=False, timeout=20: (asked.append(a.url),
                                                                                         real_ls(local, b, anonymous, timeout))[1])
        self.patch(bare, "fetch", lambda a, b, s, stall=30, cap=600, progress=None: real_fetch(local, b, s, stall, cap, progress))
        self.started()
        firstrun.provision(self.ns)
        self.assertEqual(self.lock()["via"], "https")
        self.assertNotIn("git@github.com:example/demos.git", asked)

    def test_both_forms_refused_is_no_access_with_the_sign_in_hint_of_the_host(self):
        self.github(ssh_refuses=True, ssh_code="ssh_denied")
        self.started()
        with self.assertRaises(CatalogError) as caught:
            firstrun.provision(self.ns)
        self.assertEqual((caught.exception.code, caught.exception.message), ("no_access", OPEN))
        self.assertEqual(self.lock()["error"], "no_access")
        self.assertEqual(registry.inputs()[self.ns]["state"], "noaccess")
        self.assertNotIn("via", self.lock(), "no form answered")

    def test_a_404_over_https_reads_as_no_access_and_keeps_its_code(self):
        self.github(refuse=("not_found",), ssh_refuses=True, ssh_code="host_key")
        self.started()
        with self.assertRaises(CatalogError) as caught:
            firstrun.provision(self.ns)
        self.assertEqual((caught.exception.code, caught.exception.message), ("not_found", OPEN),
                         "GitHub answers 404 to a private repository, and the address is the engine's own")
        self.assertEqual(self.lock()["error"], "not_found")
        self.assertEqual(firstrun.content({}, {})["line"], OPEN)

    def test_another_host_is_told_to_sign_in_with_git(self):
        self.listed(self.ns, url="https://git.example.test/team/demos.git")
        self.patch(gitx, "ls_remote", side_effect=lambda a, *rest, **how: (_ for _ in ()).throw(gitx.fail("no_access", a)))
        self.started()
        with self.assertRaises(CatalogError) as caught:
            firstrun.provision(self.ns)
        self.assertEqual(caught.exception.message, "No access to team/demos. Sign in with Git on this machine, then try again.")

    def test_a_failure_that_is_not_a_refusal_does_not_try_the_other_form(self):
        self.listed(self.ns, url="https://github.com/example/demos.git")
        asked = []

        def ls_remote(address, branch=None, anonymous=False, timeout=20):
            asked.append(address.url)
            raise gitx.fail("unreachable", address)
        self.patch(gitx, "ls_remote", ls_remote)
        self.started()
        with self.assertRaises(CatalogError) as caught:
            firstrun.provision(self.ns)
        self.assertEqual((caught.exception.code, caught.exception.message),
                         ("unreachable", "Cannot reach github.com. Check your connection, then try again."))
        self.assertEqual(asked, ["https://github.com/example/demos.git"])
        self.assertEqual(registry.inputs()[self.ns]["state"], "unreachable")

    def test_a_catalog_that_is_not_listed_is_asked_over_the_address_it_has_and_no_other(self):
        registry.write_config({"schema": 1, "catalogs": [
            {"id": "mine", "kind": "git", "url": "https://github.com/example/mine.git"}]})
        asked = []

        def ls_remote(address, branch=None, anonymous=False, timeout=20):
            asked.append(address.url)
            raise gitx.fail("no_access", address)
        self.patch(gitx, "ls_remote", ls_remote)
        with self.assertRaises(CatalogError):
            firstrun.provision("mine")
        self.assertEqual(asked, ["https://github.com/example/mine.git"], "the ssh form is for the catalogs the engine lists")

    # -- no lock for the network ------------------------------------------------------------------------------------

    def test_a_download_holds_no_lock_while_git_talks_to_a_network(self):
        talked = []
        real_run, real_stream = gitx.run, gitx.stream

        def watch(args, kind):
            if "ls-remote" in args or "fetch" in args:
                talked.append((kind, unlocked()))

        def run(args, *rest, **how):
            watch(args, "run")
            return real_run(args, *rest, **how)

        def stream(args, *rest, **how):
            watch(args, "stream")
            return real_stream(args, *rest, **how)
        self.patch(gitx, "run", run)
        self.patch(gitx, "stream", stream)
        self.started()
        firstrun.provision(self.ns)
        self.assertEqual([kind for kind, _ in talked], ["run", "run", "stream"], "two ls-remote and one fetch")
        self.assertTrue(all(ok for _, ok in talked), talked)

    def test_a_person_who_adds_a_catalog_during_the_download_is_not_told_busy(self):
        answers = []
        real_stream = gitx.stream

        def stream(args, *rest, **how):
            if "fetch" in args:
                done = []

                def another():
                    try:
                        with registry.flock(wait=0):
                            done.append("free")
                    except CatalogError as error:
                        done.append(error.code)
                thread = threading.Thread(target=another)
                thread.start()
                thread.join()
                answers.extend(done)
            return real_stream(args, *rest, **how)
        self.patch(gitx, "stream", stream)
        self.started()
        firstrun.provision(self.ns)
        self.assertEqual(answers, ["free"], "the catalogs lock is free while the fetch runs")

    def test_the_commit_takes_the_locks_and_only_the_commit(self):
        held = []
        real = install.commit_add

        def commit_add(*args, **how):
            held.append(not unlocked())   # asked from inside: this thread holds them only within the call
            return real(*args, **how)
        self.patch(install, "commit_add", commit_add)
        self.started()
        firstrun.provision(self.ns)
        self.assertEqual(held, [False], "commit_add is called with none held: it takes them itself")
        self.assertTrue(unlocked())

    # -- what a stop leaves ----------------------------------------------------------------------------------------

    def restart(self):
        """What a start does after a crash: the memory is gone, the sweeper and the pending operations are finished, the
        first start runs again."""
        firstrun.PROGRESS.clear()
        install.recover()
        firstrun.reconcile()

    def test_a_crash_after_the_install_was_put_in_place_converges_at_the_next_start(self):
        self.started()
        self.patch(install, "STEP", stop_at("add:placed"))   # step 15 done, the lock not written (step 16)
        with self.assertRaises(Crash):
            firstrun.provision(self.ns)
        held = self.lock()
        self.assertEqual(held["pin"], None)
        self.assertEqual(held["pending"]["op"], "add", "a stop is recognized from here on")
        self.assertTrue((self.root / ".lab/catalogs" / self.ns / ".installed.json").is_file())
        self.patch(install, "STEP", None)
        self.restart()
        held = self.lock()
        self.assertEqual((held["pin"], held["pending"], held["error"]), (self.remote.head(), None, None))
        self.assertEqual(firstrun.content({}, {})["key"], "ready")
        self.assertEqual(firstrun.PROGRESS, {}, "nothing is left to download")
        self.assertEqual(install.verify(self.ns, list(registry.manifest(self.ns)["units"])), [])
        self.silent()
        self.assertEqual(firstrun.provision(self.ns)["id"], self.ns, "and a provision after it has nothing to do")

    def test_a_crash_at_every_step_of_the_install_converges_to_a_whole_catalog(self):
        labels = ["stage:begin", "stage:written", "add:begin", "add:manifest", "add:store", "add:pending", "add:install",
                  "add:placed", "add:lock", "add:adopt", "add:done"]
        template = self.scratch / "template.git"
        bare.fetch(gitx.parse_address(self.remote.url), None, template)

        def cached(address, branch, store, stall=30, cap=600, progress=None):
            shutil.rmtree(store, ignore_errors=True)
            shutil.copytree(template, store, symlinks=True)
            return bare.resolve(store, "refs/lab/tip")
        for label in labels:
            with self.subTest(label):
                self.world()
                self.patch(bare, "fetch", cached)
                self.started()
                self.patch(install, "STEP", stop_at(label))
                with self.assertRaises(Crash):
                    firstrun.provision(self.ns)
                self.patch(install, "STEP", None)
                self.restart()
                if not self.lock()["pin"]:   # the stop was before the install was whole: the worker downloads again
                    firstrun.provision(self.ns)
                held = self.lock()
                self.assertEqual((held["pin"], held["pending"], held["error"]), (self.remote.head(), None, None))
                found = registry.manifest(self.ns)
                self.assertEqual(found["commit"], held["pin"])
                self.assertEqual(install.verify(self.ns, list(found["units"])), [])
                self.assertEqual(registry.units_of(self.ns)["addon"], ["echo"])
                self.assertEqual(firstrun.content({}, {})["key"], "ready")

    def test_a_stale_install_with_no_pin_is_replaced_and_the_machine_files_stay(self):
        self.started()
        base = self.root / ".lab/catalogs" / self.ns
        (base / "addons/echo").mkdir(parents=True)
        (base / "addons/echo/compose.yml").write_text("services: {old: {}}\n", encoding="utf-8")
        (base / "addons/old").mkdir()
        (base / "addons/old/compose.yml").write_text("services: {}\n", encoding="utf-8")
        (base / "addons/echo/.env").write_text("TOKEN=mine\n", encoding="utf-8")
        (base / "addons/echo/.state").mkdir()
        (base / "addons/echo/.state/created.json").write_text("{}\n", encoding="utf-8")
        stage = self.root / ".lab/catalogs" / f".stage-{self.ns}"
        (stage / "junk").mkdir(parents=True)
        (stage / "junk/file").write_text("x", encoding="utf-8")
        firstrun.provision(self.ns)
        self.assertEqual(registry.units_of(self.ns)["addon"], ["echo"], "the content of the catalog, and none of the old")
        self.assertEqual((base / "addons/echo/compose.yml").read_text(encoding="utf-8"),
                         self.remote.git("show", "HEAD:addons/echo/compose.yml") + "\n")
        self.assertEqual((base / "addons/echo/.env").read_text(encoding="utf-8"), "TOKEN=mine\n")
        self.assertEqual((base / "addons/echo/.state/created.json").read_text(encoding="utf-8"), "{}\n")
        self.assertFalse(stage.exists())
        self.assertEqual(self.lock()["pin"], self.remote.head())

    def test_settings_a_remove_kept_come_back_with_the_download(self):
        self.started()
        base = self.root / ".lab/catalogs" / self.ns
        firstrun.provision(self.ns)
        (base / "addons/echo/.env").write_text("TOKEN=mine\n", encoding="utf-8")
        install.remove(self.ns, keep_settings=True)
        config = registry.read_config()
        self.assertEqual(config["catalogs"], [])
        self.assertTrue((base / ".kept").exists())
        row = firstrun.provision(self.ns)   # the listed catalog is not added: its Add
        self.assertEqual(row["id"], self.ns)
        self.assertEqual((base / "addons/echo/.env").read_text(encoding="utf-8"), "TOKEN=mine\n")
        self.assertFalse((base / ".kept").exists())

    # -- what goes wrong without a git error ----------------------------------------------------------------------

    def test_another_process_that_finished_first_is_no_failure(self):
        self.started()
        real = install.commit_add

        def commit_add(*args, **how):
            real(*args, **how)
            raise CatalogError("already_added", "Already added as Acme Hub.", 409)
        self.patch(install, "commit_add", commit_add)
        row = firstrun.provision(self.ns)
        self.assertEqual(row["id"], self.ns)
        self.assertEqual(self.lock()["error"], None)

    def test_a_failure_does_not_mark_a_catalog_another_process_finished(self):
        self.started()

        def stage(*args, **how):
            registry.set_lock(self.ns, pin="a" * 40, error=None)   # the command line finished it meanwhile
            raise CatalogError("damaged", "x", 409)
        self.patch(install, "stage", stage)
        with self.assertRaises(CatalogError):
            firstrun.provision(self.ns)
        self.assertIsNone(self.lock()["error"], "a good install is not marked with the failure of a second attempt")

    def test_a_download_that_git_did_not_run_because_the_lab_ui_ends_is_no_failure_of_the_catalog(self):
        self.started()
        self.patch(gitx, "ls_remote", side_effect=gitx.fail("git_failed", output=gitx.ENDING))
        with self.assertRaises(CatalogError) as caught:
            firstrun.provision(self.ns)
        self.assertEqual(caught.exception.code, "git_failed")
        self.assertIsNone(self.lock()["error"], "the next start downloads it as if nothing had happened")

    def test_a_note_that_cannot_be_written_after_the_install_is_no_failure(self):
        calls = []

        def remember(ns, address, answered):
            calls.append(ns)
            if len(calls) > 1:   # the second one is after the commit: the lock is held by another process
                raise CatalogError("busy", "Another catalog change is running.", 409)
        self.patch(firstrun, "_remember", remember)
        self.started()
        row = firstrun.provision(self.ns)
        self.assertEqual(len(calls), 2)
        self.assertEqual((row["id"], self.lock()["pin"], self.lock()["error"]), (self.ns, self.remote.head(), None))

    def test_the_form_that_answered_is_remembered_even_when_the_fetch_then_fails(self):
        self.github()
        real = bare.fetch

        def fetch(*args, **how):
            real(*args, **how)
            raise gitx.fail("timeout", args[0])
        self.patch(bare, "fetch", fetch)
        self.started()
        with self.assertRaises(CatalogError):
            firstrun.provision(self.ns)
        self.assertEqual(self.lock()["via"], "ssh", "and the next attempt starts with it")

    def test_a_catalog_the_person_does_not_have_leaves_no_store_behind_when_it_fails(self):
        registry.write_config({"schema": 1, "catalogs": []})
        self.patch(manifest, "levels", side_effect=gitx.fail("fsck"))
        with self.assertRaises(CatalogError):
            firstrun.provision(self.ns)
        self.assertEqual(os.listdir(self.root / ".lab/cache/git"), [], "a store no catalog owns is not kept")
        self.assertIsNone(registry.entry(self.ns))
        self.assertNotIn(self.ns, registry.read_lock()["catalogs"], "and nothing is written for a catalog that is not added")

    def test_a_full_disk_is_a_failure_with_a_line_and_a_code_of_its_own(self):
        self.patch(install, "stage", side_effect=OSError(28, "No space left on device"))
        self.started()
        with self.assertRaises(CatalogError) as caught:
            firstrun.provision(self.ns)
        self.assertEqual((caught.exception.code, caught.exception.message),
                         ("failed", "The catalog could not be downloaded. Try again."))
        self.assertEqual(self.lock()["error"], "failed")
        self.assertEqual(registry.inputs()[self.ns]["state"], "failed")
        self.assertEqual(caught.exception.detail, {"why": "OSError"}, "the reason is for the log, never in the line")
        self.assertEqual(firstrun.PROGRESS, {})

    def test_a_fault_of_the_code_is_recorded_as_a_failure_and_raised_as_it_is(self):
        self.patch(manifest, "levels", side_effect=KeyError("boom"))
        self.started()
        with self.assertRaises(KeyError):
            firstrun.provision(self.ns)
        self.assertEqual(self.lock()["error"], "failed")
        self.assertEqual(firstrun.PROGRESS, {})

    # -- a commit that fails in the process: not a stop, so nothing but this module is there to clean up -----------

    def failure_row(self):
        return next(one for one in service.describe({}, {}) if one.get("id") == self.ns)["state"]["key"]

    def test_a_failure_of_the_commit_ends_as_a_failure_with_its_line_and_a_way_out(self):
        self.started()
        link = self.root / ".lab/catalogs" / self.ns
        link.symlink_to(self.scratch)   # a link where the install goes: the pipeline never replaces it (name_taken)
        line = "Another catalog already uses the name demos. Remove it first, or ask the owner of this one to rename it."
        for _ in range(3):   # the first attempt, then two automatic retries: the page says the same each time
            with self.assertRaises(CatalogError) as caught:
                firstrun.provision(self.ns)
            held = self.lock()
            self.assertEqual((caught.exception.code, held["error"], held["pending"], held["pin"]),
                             ("name_taken", "name_taken", None, None), "the flag of the commit is not left standing")
            said = firstrun.content({}, {})
            self.assertEqual((said["key"], said["code"], said["line"]), ("failed", "name_taken", line))
            self.assertEqual(self.failure_row(), "failed", "the Catalogs row says it too, and has its Try again")
            self.assertEqual(firstrun.PROGRESS, {})
        link.unlink()   # the person removed what was in the way
        row = firstrun.provision(self.ns)
        self.assertEqual((row["id"], self.lock()["pin"], self.lock()["error"]), (self.ns, self.remote.head(), None))
        self.assertEqual(firstrun.content({}, {})["key"], "ready")

    def test_an_ordinary_failure_at_any_step_of_the_commit_is_a_failure_and_the_next_attempt_converges(self):
        labels = ["add:begin", "add:manifest", "add:store", "add:pending", "add:install", "add:placed", "add:lock"]
        template = self.scratch / "template.git"
        bare.fetch(gitx.parse_address(self.remote.url), None, template)

        def cached(address, branch, store, stall=30, cap=600, progress=None):
            shutil.rmtree(store, ignore_errors=True)
            shutil.copytree(template, store, symlinks=True)
            return bare.resolve(store, "refs/lab/tip")

        def refuse(label):
            def hook(step):
                if step == label:
                    raise OSError(13, "Permission denied")
            return hook
        for label in labels:
            with self.subTest(label):
                self.world()
                self.patch(bare, "fetch", cached)
                self.started()
                self.patch(install, "STEP", refuse(label))
                with self.assertRaises(CatalogError) as caught:
                    firstrun.provision(self.ns)
                self.assertEqual(caught.exception.code, "failed")
                held = self.lock()
                self.assertEqual((held["pin"], held["pending"], held["error"]), (None, None, "failed"),
                                 "a failed commit is a failure, and not a download that never ends")
                said = firstrun.content({}, {})
                self.assertEqual((said["key"], said["code"], said["line"]), ("failed", "failed", firstrun.LINES["failed"]))
                self.assertEqual(self.failure_row(), "failed")
                self.patch(install, "STEP", None)
                firstrun.provision(self.ns)   # the retry: whatever the failure left is replaced, whole
                held = self.lock()
                self.assertEqual((held["pin"], held["pending"], held["error"]), (self.remote.head(), None, None))
                found = registry.manifest(self.ns)
                self.assertEqual(found["commit"], held["pin"])
                self.assertEqual(install.verify(self.ns, list(found["units"])), [])
                self.assertEqual(firstrun.content({}, {})["key"], "ready")

    def test_a_failure_after_the_commit_point_leaves_a_whole_catalog_and_no_failure(self):
        self.started()

        def refuse(step):
            if step == "add:adopt":
                raise OSError(13, "Permission denied")
        self.patch(install, "STEP", refuse)
        with self.assertRaises(CatalogError) as caught:
            firstrun.provision(self.ns)
        self.assertEqual(caught.exception.code, "failed", "the worker counts it, and the next round finds nothing to do")
        held = self.lock()
        self.assertEqual((held["pin"], held["pending"], held["error"]), (self.remote.head(), None, None))
        self.assertEqual(firstrun.content({}, {})["key"], "ready")
        self.assertEqual(self.failure_row(), "uptodate")

    def failed_once(self):
        """An entry with no copy whose first attempt, nobody watching, found the host unreachable: the lock holds the
        failure, this process remembers its line, and nothing is on the bar."""
        registry.write_config({"schema": 1, "catalogs": [{"id": self.ns, "kind": "git", "url": self.remote.url}]})
        with mock.patch.object(gitx, "ls_remote", side_effect=gitx.fail("unreachable")), self.assertRaises(CatalogError):
            firstrun.provision(self.ns)
        self.assertEqual(firstrun.content({}, {})["key"], "failed")

    def test_a_silent_retry_stays_failed_through_every_step_of_its_commit(self):
        self.failed_once()
        seen = []
        self.patch(install, "STEP", lambda step: seen.append((step, firstrun.content({}, {})["key"])))
        firstrun.provision(self.ns)
        steps = [step for step, _ in seen]
        self.assertTrue({"add:pending", "add:install", "add:placed", "add:lock", "add:adopt"} <= set(steps), steps)
        end = steps.index("add:adopt")
        self.assertEqual({key for _, key in seen[:end]}, {"failed"}, f"no flicker through the commit: {seen}")
        self.assertEqual({key for _, key in seen[end:]}, {"ready"}, "and it is ready the moment the lock says so")

    def test_a_retry_a_click_started_is_a_download_through_every_step_of_its_commit(self):
        self.failed_once()
        registry.set_lock(self.ns, error=None)   # what service.check_now does, and then it marks the catalog
        firstrun.reconcile()
        self.assertEqual(firstrun.content({}, {})["key"], "downloading")
        seen = []
        self.patch(install, "STEP", lambda step: seen.append((step, firstrun.content({}, {})["key"])))
        firstrun.provision(self.ns)
        steps = [step for step, _ in seen]
        end = steps.index("add:adopt")
        self.assertEqual({key for _, key in seen[:end]}, {"downloading"}, str(seen))
        self.assertEqual({key for _, key in seen[end:]}, {"ready"})

    def test_an_env_that_is_not_utf8_is_no_failure_of_a_catalog_that_is_installed(self):
        self.started()
        self.patch(envfiles, "ROOT", self.root)
        env = b"# caf\xe9\nFOO=1\n"
        (self.root / ".env").write_bytes(env)
        row = firstrun.provision(self.ns)   # layers.prepare_catalogs reads it as UTF-8: it leaves the file alone, after the install
        self.assertEqual((row["id"], self.lock()["pin"], self.lock()["error"]), (self.ns, self.remote.head(), None))
        self.assertEqual((self.root / ".env").read_bytes(), env, "the person's file is not touched")
        self.assertEqual(firstrun.content({}, {})["key"], "ready")
        self.assertFalse(layers.prepare_catalogs(), "nothing to say of a file it cannot read, for any caller")

    def test_the_first_catalog_that_arrives_adds_the_two_settings_that_keep_compose_from_fetching_an_include_to_the_env_once(self):
        # A `docker compose` typed by hand reads a catalog's files as the Lab does
        self.started()
        self.patch(envfiles, "ROOT", self.root)
        (self.root / ".env").write_text("GRAVITEE_LICENSE=mine\nCOMPOSE_EXPERIMENTAL_OCI_REMOTE=true\n", encoding="utf-8")
        firstrun.provision(self.ns)
        held = envfiles.read(".env")
        self.assertEqual(held, {"GRAVITEE_LICENSE": "mine", "COMPOSE_EXPERIMENTAL_OCI_REMOTE": "true",
                                "COMPOSE_EXPERIMENTAL_GIT_REMOTE": "false"}, "a value the person set is theirs; the other is added")
        self.assertEqual(oct((self.root / ".env").stat().st_mode & 0o777), "0o600")
        self.assertFalse(layers.prepare_catalogs(), "and once the lines are there it is asked again for nothing")

    def test_without_a_dot_env_the_arrival_of_the_first_catalog_makes_none(self):
        self.started()
        self.patch(envfiles, "ROOT", self.root)
        firstrun.provision(self.ns)
        self.assertFalse((self.root / ".env").exists(), "make init makes it from .env.example, and a file made here would stop it")

    def test_the_lines_of_the_first_start_are_the_ones_of_the_spec(self):
        github = gitx.parse_address("https://github.com/example/demos.git")
        other = gitx.parse_address("https://git.example.test/team/demos.git")
        table = [("unreachable", github, "", "Cannot reach github.com. Check your connection, then try again."),
                 ("timeout", github, "", "github.com did not answer in time. Try again."),
                 ("git_missing", None, "", "Git is not installed. Install it, then try again."),
                 ("git_old", None, " 2.20", "Git 2.20 is too old. Install Git 2.25 or later, then try again."),
                 ("git_old", None, "", "Git is too old. Install Git 2.25 or later, then try again."),
                 ("no_access", github, "", OPEN),
                 ("not_found", github, "", OPEN),
                 ("no_access", other, "", "No access to team/demos. Sign in with Git on this machine, then try again."),
                 ("certificate", github, "", "The certificate of github.com was refused. A company proxy may be in the way."),
                 ("wrong_catalog", github, "", "example/demos is not the catalog this Lab expects. Run make update, then try again."),
                 ("no_readable_version", github, "", "The catalog needs a newer Gravitee Lab. Run make update, then restart the Lab."),
                 ("lab_new", github, "", "The catalog needs a newer Gravitee Lab. Run make update, then restart the Lab."),
                 ("lab_old", github, "", "The catalog is not ready for this version of the Lab. Try again later."),
                 ("whatever", github, "", "The catalog could not be downloaded. Try again.")]
        for code, address, have, line in table:
            with self.subTest(code=code, have=have):
                self.assertEqual(firstrun.line_of(code, address, have), line)
        self.assertEqual(firstrun.LINES["lab_old"], manifest.compat_error(0, 1, 1, listed=True).message,
                         "the listed wording of the old catalog is said once, here and in manifest")


class Beat(Served, Case):
    """The thread: the schedule against a clock the test moves, then once with real time."""

    def setUp(self):
        self.world()
        self.calls = []
        self.migrated = []
        self.checked = []
        # The schedule of the downloads is the point of most tests: the check and the look at the file are far away unless
        # a test brings them near
        self.patch(firstrun, "FIRST_CHECK", 10 ** 9)
        self.patch(firstrun, "NAP", 10 ** 9)
        self.patch(service, "check_all", side_effect=lambda: self.checked.append(1))
        from lab_ui.library import layout
        self.layout = layout
        self.patch(layout, "waiting", return_value=False)
        self.patch(layout, "migrate", side_effect=lambda: self.migrated.append(1) or {})

    def failing(self, code="unreachable", then=None):
        """provision replaced by one that fails with `code` (and, from the call number `then` on, succeeds): the calls
        are in self.calls."""
        def provision(ns):
            self.calls.append(ns)
            if then is not None and len(self.calls) >= then:
                registry.set_lock(ns, pin="a" * 40, error=None)
                return {"id": ns}
            raise CatalogError(code, "Cannot reach somewhere.", 502)
        self.patch(firstrun, "provision", provision)

    def arriving(self):
        """provision replaced by one that succeeds: the catalog has a copy."""
        def provision(ns):
            self.calls.append(ns)
            registry.set_lock(ns, pin="a" * 40, error=None)
            return {"id": ns}
        self.patch(firstrun, "provision", provision)

    def worker(self):
        now, clock = self.fake_clock()
        self.started()
        return now, firstrun.Worker(clock)

    def test_the_schedule_is_15_seconds_1_minute_5_minutes_then_every_30_minutes(self):
        self.assertEqual([firstrun.delay(n) for n in range(0, 8)], [0.0, 15.0, 60.0, 300.0, 1800.0, 1800.0, 1800.0, 1800.0])
        self.failing()
        now, worker = self.worker()
        began = now[0]
        attempts = []
        for _ in range(7):
            before = len(self.calls)
            wait = worker.tick()
            attempts.append((round(now[0] - began), len(self.calls) - before, round(wait)))
            now[0] += wait
        self.assertEqual(attempts, [(0, 1, 15), (15, 1, 60), (75, 1, 300), (375, 1, 1800), (2175, 1, 1800),
                                    (3975, 1, 1800), (5775, 1, 1800)])

    def test_nothing_is_attempted_before_the_retry_is_due(self):
        self.failing()
        now, worker = self.worker()
        worker.tick()
        now[0] += 14.9
        worker.tick()
        self.assertEqual(len(self.calls), 1)
        now[0] += 0.2
        worker.tick()
        self.assertEqual(len(self.calls), 2)

    def test_a_click_starts_the_schedule_again_and_is_attempted_at_once(self):
        self.failing()
        now, worker = self.worker()
        for _ in range(3):
            worker.tick()
            now[0] = worker.retry_at
        calls = len(self.calls)
        worker.tick()
        self.assertEqual(len(self.calls), calls + 1)
        self.assertGreater(worker.retry_at - now[0], 100, "the schedule has gone on")
        worker.wake()
        wait = worker.tick()
        self.assertEqual(len(self.calls), calls + 2, "Try again does not wait for the schedule")
        self.assertEqual(wait, 15.0, "and it starts from the first delay again")
        self.assertEqual(worker.failures, 1)

    def test_a_click_makes_the_attempt_one_a_person_sees(self):
        self.failing()
        now, worker = self.worker()
        worker.tick()
        firstrun.PROGRESS.clear()   # an automatic retry has no mark
        seen = []
        real = firstrun.provision
        self.patch(firstrun, "provision", lambda ns: (seen.append(ns in firstrun.PROGRESS), real(ns))[1])
        now[0] += 15
        worker.tick()
        worker.wake()
        worker.tick()
        self.assertEqual(seen, [False, True], "the automatic attempt is silent, the one a click starts is not")

    def test_a_success_ends_the_schedule(self):
        self.failing(then=3)
        now, worker = self.worker()
        for _ in range(3):
            wait = worker.tick()
            now[0] += wait
        self.assertEqual(len(self.calls), 3)
        self.assertEqual((worker.failures, worker.retry_at), (0, None))
        worker.tick()
        self.assertEqual(len(self.calls), 3, "a catalog with a copy is not asked for again")

    def test_a_failing_catalog_does_not_keep_the_next_from_its_turn(self):
        self.listed(self.ns, also=("second",), url=self.remote.url)
        registry.write_config({"schema": 1, "catalogs": [
            {"id": self.ns, "kind": "git", "url": self.remote.url}, {"id": "second", "kind": "git", "url": self.remote.url}]})
        order = []

        def provision(ns):
            order.append(ns)
            raise CatalogError("unreachable", "no", 502)
        self.patch(firstrun, "provision", provision)
        _, worker = self.worker()
        worker.tick()
        self.assertEqual(order, [self.ns, "second"])
        self.assertEqual(worker.failures, 1, "one round is one failure of the schedule")

    def two(self):
        """The default file lists a second catalog at a repository of its own, and both are added with no copy."""
        other = fixtures.Remote(gitfixture.make_repo(self.scratch / "second.git", gitfixture.catalog_files("second")))
        rows = [{"id": ns, "name": ns.title(), "description": "", "url": remote.url, "branch": "main"}
                for ns, remote in ((self.ns, self.remote), ("second", other))]
        (self.root / "catalogs.default.json").write_text(json.dumps({"schema": 1, "catalogs": rows}), encoding="utf-8")
        registry.write_config({"schema": 1, "catalogs": [{"id": row["id"], "kind": "git", "url": row["url"]} for row in rows]})
        return other

    def test_a_catalog_removed_while_an_earlier_one_downloads_is_not_brought_back(self):
        self.two()
        real, removed = bare.fetch, []

        def fetch(*args, **how):
            tip = real(*args, **how)
            if not removed:   # the first catalog is fetched; the person presses Remove on the second
                removed.append(install.remove("second"))
            return tip
        self.patch(bare, "fetch", fetch)
        calls = []
        provision = firstrun.provision
        self.patch(firstrun, "provision", lambda ns: (calls.append(ns), provision(ns))[1])
        _, worker = self.worker()
        worker.tick()
        self.assertEqual(calls, [self.ns], "the removed catalog is not taken for the Add of a listed one")
        self.assertEqual([one["id"] for one in registry.entries()], [self.ns])
        self.assertNotIn("second", registry.read_lock()["catalogs"])
        self.assertFalse((self.root / ".lab/catalogs/second").exists())
        self.assertFalse((self.root / ".lab/cache/git/second.git").exists())
        self.assertEqual([one["id"] for one in defaults.not_added()], ["second"], "it is a Not added row, as the person left it")
        self.assertEqual((worker.failures, worker.retry_at), (0, None), "and a catalog that is gone is no failure")

    def test_a_catalog_another_process_finished_meanwhile_still_counts_as_an_arrival(self):
        self.two()
        self.layout.waiting.return_value = True
        calls = []

        def provision(ns):
            calls.append(ns)
            for one in (self.ns, "second"):   # `make catalogs` in a terminal finished both while the first was asked
                registry.set_lock(one, pin="a" * 40, error=None)
            return {"id": ns}
        self.patch(firstrun, "provision", provision)
        _, worker = self.worker()
        worker.first = False
        worker.tick()
        self.assertEqual(calls, [self.ns, "second"], "only a catalog that is gone is skipped")
        self.assertEqual(self.migrated, [1], "the arrival is seen, as it is for a download of this thread")

    def test_a_catalog_that_arrived_with_an_env_that_is_not_utf8_still_runs_the_migration(self):
        self.layout.waiting.return_value = True
        (self.root / ".env").write_bytes(b"# caf\xe9\nFOO=1\n")
        _, worker = self.worker()
        worker.first = False   # only the arrival of the catalog can start it
        with mock.patch("sys.stderr") as err:
            worker.tick()
        self.assertEqual(self.lock()["pin"], self.remote.head())
        self.assertEqual((worker.failures, worker.retry_at, self.migrated), (0, None, [1]))
        self.assertEqual(err.write.call_args_list, [], "and nothing is said of a failure that did not happen")

    def test_a_fault_of_an_attempt_is_a_failure_of_the_schedule_and_the_thread_goes_on(self):
        def provision(ns):
            self.calls.append(ns)
            raise RuntimeError("boom")
        self.patch(firstrun, "provision", provision)
        now, worker = self.worker()
        with mock.patch("sys.stderr") as err:
            wait = worker.tick()
        self.assertEqual((worker.failures, wait), (1, 15.0))
        self.assertIn("RuntimeError", "".join(call.args[0] for call in err.write.call_args_list))

    def test_the_first_check_is_5_seconds_after_the_start_and_then_every_6_hours_with_a_jitter(self):
        self.patch(firstrun, "FIRST_CHECK", 5.0)
        self.patch(firstrun, "NAP", 60.0)
        registry.write_config({"schema": 1, "catalogs": []})
        now, worker = self.worker()
        self.patch(firstrun.random, "uniform", side_effect=lambda low, high: high)
        began = now[0]
        wait = worker.tick()
        self.assertEqual((self.checked, wait), ([], 5.0))
        now[0] = began + 4.9
        worker.tick()
        self.assertEqual(self.checked, [])
        now[0] = began + 5.0
        wait = worker.tick()
        self.assertEqual(len(self.checked), 1)
        self.assertEqual(wait, firstrun.NAP, "the thread looks again within a minute")
        self.assertEqual(worker.check_at, began + 5.0 + 6 * 3600 + 600, "six hours and up to ten minutes")
        now[0] = worker.check_at - 1
        worker.tick()
        self.assertEqual(len(self.checked), 1)
        now[0] = worker.check_at
        worker.tick()
        self.assertEqual(len(self.checked), 2)

    def test_the_jitter_is_up_to_ten_minutes(self):
        self.assertEqual((firstrun.CHECK_EVERY, firstrun.JITTER), (6 * 3600.0, 600.0))
        seen = {firstrun.random.uniform(0, firstrun.JITTER) for _ in range(50)}
        self.assertTrue(all(0 <= one <= 600 for one in seen) and len(seen) > 1)

    def test_a_check_that_fails_does_not_end_the_thread(self):
        self.patch(firstrun, "FIRST_CHECK", 5.0)
        self.patch(service, "check_all", side_effect=RuntimeError("boom"))
        registry.write_config({"schema": 1, "catalogs": []})
        now, worker = self.worker()
        now[0] += 6
        with mock.patch("sys.stderr"):
            worker.tick()
        self.assertGreater(worker.check_at, now[0], "and the next is scheduled")

    def test_the_check_comes_after_the_downloads_of_the_same_round(self):
        self.patch(firstrun, "FIRST_CHECK", 5.0)
        order = []
        self.patch(firstrun, "provision", side_effect=lambda ns: (order.append("provision"),
                                                                    registry.set_lock(ns, pin="a" * 40), {"id": ns})[2])
        self.patch(service, "check_all", side_effect=lambda: order.append("check"))
        now, worker = self.worker()
        now[0] += 60
        worker.tick()
        self.assertEqual(order, ["provision", "check"])

    def test_the_migration_of_an_old_layout_runs_once_the_catalog_is_there(self):
        self.layout.waiting.return_value = True
        self.arriving()
        now, worker = self.worker()
        worker.tick()
        self.assertEqual(self.migrated, [1])
        worker.tick()
        self.assertEqual(self.migrated, [1], "once, not at every pass")
        self.failing()
        registry.drop_lock(self.ns)
        worker.arrived = False
        now[0] += 100
        worker.tick()
        self.assertEqual(self.migrated, [1], "and not while the download fails")

    def test_the_migration_runs_after_a_retry_succeeds(self):
        self.layout.waiting.return_value = True
        self.failing(then=2)
        now, worker = self.worker()
        worker.tick()
        self.assertEqual(self.migrated, [1], "the first pass looks at the layout once")
        now[0] += 15
        worker.tick()
        self.assertEqual(self.migrated, [1, 1], "and the arrival of the catalog is the second")

    def test_the_move_of_an_old_layout_is_made_right_after_the_default_catalog_not_after_the_downloads_that_follow_it(self):
        # a second catalog that takes ten minutes to fetch, or never arrives, does not keep a person from their settings
        self.two()
        order = []

        def provision(ns):
            order.append(("provision", ns))
            registry.set_lock(ns, pin="a" * 40, error=None)
            return {"id": ns}
        self.patch(firstrun, "provision", provision)
        self.layout.waiting.side_effect = lambda: ("migrate",) not in order   # waiting until the move is made
        self.layout.migrate.side_effect = lambda: order.append(("migrate",)) or {}
        _, worker = self.worker()
        worker.first = False
        worker.tick()
        self.assertEqual(order, [("provision", self.ns), ("migrate",), ("provision", "second")])
        worker.tick()
        self.assertEqual(order.count(("migrate",)), 1, "once")

    def test_the_arrival_of_a_catalog_that_is_not_the_default_one_is_none_the_move_waits_for(self):
        # Part B needs the default catalog, and no other: a second catalog that arrives does not make it again
        self.two()
        self.layout.waiting.return_value = True
        order = []

        def provision(ns):
            order.append(ns)
            if ns == self.ns:
                raise CatalogError("unreachable", "Cannot reach somewhere.", 502)
            registry.set_lock(ns, pin="a" * 40, error=None)
            return {"id": ns}
        self.patch(firstrun, "provision", provision)
        _, worker = self.worker()
        worker.first = False
        worker.tick()
        self.assertEqual((order, self.migrated), ([self.ns, "second"], []), "the default one failed, the other arrived")

    def test_a_remove_makes_the_thread_look_at_the_layout_at_once_and_only_once(self):
        # the person decided to have no default catalog; the move that waited for it runs against none
        self.layout.waiting.return_value = True
        registry.write_config({"schema": 1, "catalogs": []})
        _, worker = self.worker()
        worker.first = False
        worker.tick()
        self.assertEqual(self.migrated, [], "nothing arrived and nothing was removed since: nothing to look at")
        worker.removed()
        self.assertTrue(worker.nudge.is_set(), "the thread wakes, it does not wait for its nap")
        worker.tick()
        self.assertEqual(self.migrated, [1])
        worker.tick()
        self.assertEqual(self.migrated, [1], "once")

    def test_a_remove_when_the_layout_waits_for_nothing_changes_nothing(self):
        registry.write_config({"schema": 1, "catalogs": []})
        _, worker = self.worker()
        worker.first = False
        worker.removed()
        worker.tick()
        self.assertEqual(self.migrated, [], "the layout is ready: the move is not made again for a click of Remove")

    def test_the_module_tells_the_live_worker_and_nobody_when_there_is_none(self):
        firstrun.removed()   # no thread: the next start looks at the layout, and this raises nothing
        worker = firstrun.Worker()
        self.patch(firstrun, "_worker", worker)
        firstrun.removed()
        self.assertTrue(worker.nudge.is_set())
        self.assertTrue(worker._removed)

    def test_what_the_move_reports_is_said_once_but_the_line_that_it_has_said_itself_is_not_said_again(self):
        self.layout.waiting.return_value = True
        failed = self.layout.FAILED_LINE
        self.layout.migrate.side_effect = lambda: {"problems": ["Edit COMPOSE_FILE in .env by hand.", failed]}
        self.arriving()
        _, worker = self.worker()
        with mock.patch("sys.stderr") as err:
            worker.tick()
        said = "".join(call.args[0] for call in err.write.call_args_list)
        self.assertIn("Gravitee Lab UI: Edit COMPOSE_FILE in .env by hand.", said)
        self.assertNotIn(failed, said, "`migrate` said it, with its reason")

    def test_a_move_that_failed_is_not_made_again_by_the_thread_the_person_presses_try_again(self):
        self.layout.waiting.return_value = True   # still waiting: the move failed
        self.layout.migrate.side_effect = lambda: self.migrated.append(1) or {"problems": [self.layout.FAILED_LINE]}
        self.arriving()
        now, worker = self.worker()
        for _ in range(4):
            worker.tick()
            now[0] += 100
        self.assertEqual(self.migrated, [1], "no loop on a disk that is full")

    def test_a_migration_that_fails_is_said_and_the_thread_goes_on(self):
        self.layout.waiting.return_value = True
        self.layout.migrate.side_effect = OSError("disk")
        self.arriving()
        _, worker = self.worker()
        with mock.patch("sys.stderr") as err:
            worker.tick()
        self.assertIn("could not move the settings", "".join(call.args[0] for call in err.write.call_args_list))

    def test_nothing_is_attempted_when_nothing_is_waiting_and_the_wait_is_bounded(self):
        self.patch(firstrun, "NAP", 60.0)
        self.patch(firstrun, "provision", side_effect=AssertionError("nothing to download"))
        registry.write_config({"schema": 1, "catalogs": []})
        _, worker = self.worker()
        self.assertLessEqual(worker.tick(), firstrun.NAP)

    def test_a_catalog_edited_into_the_file_later_is_found_within_a_minute(self):
        self.patch(firstrun, "NAP", 60.0)
        registry.write_config({"schema": 1, "catalogs": []})
        self.failing()
        now, worker = self.worker()
        worker.tick()
        self.assertEqual(self.calls, [])
        registry.write_config({"schema": 1, "catalogs": [{"id": self.ns, "kind": "git", "url": self.remote.url}]})
        now[0] += firstrun.NAP
        worker.tick()
        self.assertEqual(self.calls, [self.ns])

    def test_the_end_of_the_lab_ui_ends_the_thread_without_recording_a_failure(self):
        def provision(ns):
            raise CatalogError("git_failed", "Git could not read x.", 502, output=gitx.ENDING)
        self.patch(firstrun, "provision", provision)
        _, worker = self.worker()
        worker.tick()
        self.assertTrue(worker.stop.is_set())
        self.assertEqual(self.lock()["error"], None, "git did not run, so nothing failed")

    # -- the real thread -------------------------------------------------------------------------------------------

    def thread(self):
        firstrun.start_worker()
        self.addCleanup(firstrun.stop_worker)
        return firstrun._worker

    def until(self, test, seconds=60.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if test():
                return True
            time.sleep(0.01)
        return False

    def test_the_worker_is_one_daemon_thread_and_a_second_start_does_nothing(self):
        registry.write_config({"schema": 1, "catalogs": []})
        first = self.thread()
        firstrun.start_worker()
        firstrun.start_worker()
        self.assertIs(firstrun._worker, first)
        found = [one for one in threading.enumerate() if one.name == "lab-first-run"]
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].daemon)
        firstrun.stop_worker()
        self.assertFalse(found[0].is_alive())
        firstrun.start_worker()   # after it ended, a new one can start
        self.assertIsNot(firstrun._worker, first)
        self.addCleanup(firstrun.stop_worker)

    def test_the_worker_downloads_the_catalog_and_is_a_thread_apart(self):
        self.started()
        main = threading.current_thread()
        ran = []
        real = firstrun.provision
        self.patch(firstrun, "provision", lambda ns: (ran.append(threading.current_thread()), real(ns))[1])
        self.thread()
        self.assertTrue(self.until(lambda: self.lock()["pin"]), "the catalog arrived")
        self.assertEqual(ran[0].name, "lab-first-run")
        self.assertIsNot(ran[0], main)
        self.assertTrue(self.until(lambda: firstrun.content({}, {})["key"] == "ready"))

    def test_the_add_of_a_listed_catalog_from_its_row_is_a_download_of_the_worker(self):
        registry.write_config({"schema": 1, "catalogs": []})
        self.patch(service, "_row_of", return_value={})
        self.thread()
        service.add_listed(self.ns)   # the route of Add on a Not added row: the entry, the mark, the wake
        self.assertTrue(self.until(lambda: self.lock()["pin"]), "the worker downloaded it")
        self.assertEqual(registry.entry(self.ns)["url"], self.remote.url)
        self.assertEqual(defaults.not_added(), [])

    def test_a_remove_wakes_the_real_thread_which_makes_the_move_that_waited_for_the_default_catalog(self):
        # The thread sleeps for its nap (patched to forever): only the Remove makes it look again
        self.layout.waiting.return_value = True
        registry.write_config({"schema": 1, "catalogs": []})
        self.started()
        self.thread()
        self.assertTrue(self.until(lambda: self.migrated == [1], 10), "the first pass looks at the layout once")
        firstrun.removed()
        self.assertTrue(self.until(lambda: self.migrated == [1, 1], 10), "and the Remove makes it look again, at once")

    def test_two_starts_at_once_make_one_thread(self):
        registry.write_config({"schema": 1, "catalogs": []})
        gate = threading.Barrier(4)

        def start():
            gate.wait(10)
            firstrun.start_worker()
        threads = [threading.Thread(target=start) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.addCleanup(firstrun.stop_worker)
        self.assertEqual(len([one for one in threading.enumerate() if one.name == "lab-first-run"]), 1)

    def test_a_pass_that_fails_does_not_end_the_thread(self):
        self.patch(firstrun, "NAP", 0.05)
        ticks = []
        real = firstrun.Worker.tick

        def tick(worker):
            ticks.append(1)
            if len(ticks) == 1:
                raise RuntimeError("boom")
            return real(worker)
        self.patch(firstrun.Worker, "tick", tick)
        registry.write_config({"schema": 1, "catalogs": []})
        with mock.patch("sys.stderr") as err:
            self.thread()
            self.assertTrue(self.until(lambda: len(ticks) >= 3, 10), "it went on")
        self.assertIn("RuntimeError", "".join(call.args[0] for call in err.write.call_args_list))

    def test_an_automatic_retry_leaves_the_content_failed_with_no_flicker(self):
        # The repository does not exist yet: every attempt fails, silently, until it does
        missing = self.scratch / "later.git"
        self.listed(self.ns, url=f"file://{missing}")
        self.patch(firstrun, "SCHEDULE", (0.05,))
        self.patch(firstrun, "LATER", 0.05)
        self.started()
        self.assertEqual(firstrun.content({}, {})["key"], "downloading", "the first state, before any attempt")
        seen = []
        self.thread()
        self.assertTrue(self.until(lambda: (seen.append(firstrun.content({}, {})["key"]) or True) and seen[-1] == "failed"))
        for _ in range(60):   # the retries go on every 50 ms, and what the page reads is the same each time
            seen.append(firstrun.content({}, {})["key"])
            time.sleep(0.01)
        self.assertEqual(set(seen[seen.index("failed"):]), {"failed"}, f"no flicker: {seen}")
        gitfixture.make_repo(missing, gitfixture.catalog_files(self.ns))
        self.assertTrue(self.until(lambda: self.lock()["pin"]), "and the retry that works installs it")
        self.assertTrue(self.until(lambda: firstrun.content({}, {})["key"] == "ready"))

    def test_try_again_wakes_the_worker_and_shows_the_download(self):
        missing = self.scratch / "later.git"
        self.listed(self.ns, url=f"file://{missing}")
        self.patch(firstrun, "SCHEDULE", (3600.0,))
        self.patch(firstrun, "LATER", 3600.0)
        self.patch(firstrun, "PROGRESS", Recorded())
        self.patch(service, "_row_of", return_value={})
        self.started()
        self.thread()
        self.assertTrue(self.until(lambda: self.lock()["error"] == "not_found"), "the first attempt failed")
        self.assertEqual(firstrun.content({}, {})["key"], "failed")
        marks = len([one for one in firstrun.PROGRESS.sets if one[0] == self.ns])
        gitfixture.make_repo(missing, gitfixture.catalog_files(self.ns))
        service.check_now(self.ns)   # the route of Try again: it clears the failure, marks the catalog, wakes the worker
        self.assertTrue(self.until(lambda: self.lock()["pin"], 60), "the schedule says an hour: only the click did this")
        self.assertGreater(len([one for one in firstrun.PROGRESS.sets if one[0] == self.ns]), marks,
                           "and the attempt was one a person sees")
