"""Catalogs/firstrun.py, the first start: the intent written from the tracked default file
when `.lab/catalogs.json` does not exist, the catalogs with no copy marked as downloading before any I/O, a file that
exists left as the person's decision (a removed listed catalog is a Not added row, a malformed file is never a first run),
`state.content` for each of its four keys, LAB_NO_FIRST_RUN, and the early returns of the server (`--check`, `--list` and a
second `make ui` start nothing). Everything is on disk in a temporary project folder (a visible skip without git): no
network, no Docker daemon."""

import errno
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from lab_ui.server import api
from lab_ui.docker import compose, envfiles
from lab_ui.library import layout
from lab_ui import paths
from lab_ui.catalogs import defaults, firstrun, gitx, install, registry, service
from lab_ui.catalogs.registry import CatalogError

from .. import Case, fixtures
from .test_provision import Recorded, Served

ENGINE = Path(__file__).resolve().parents[2]
REAL_ADDRESS = defaults._address   # before a test puts the address of a repository on this machine in its place


class Quiet(Served, Case):
    """A project folder where nothing may reach for git, the network or a socket."""

    def setUp(self):
        self.world()
        self.silent()
        self.patch(socket.socket, "connect", side_effect=AssertionError("the network was reached"))
        self.patch(socket, "create_connection", side_effect=AssertionError("the network was reached"))

    def intent(self):
        return self.config()


class Reconcile(Quiet):

    def test_no_catalogs_file_writes_the_intent_from_the_tracked_file_and_nothing_else(self):
        self.assertIsNone(self.config())
        firstrun.reconcile()
        written = self.config()
        self.assertEqual(written["schema"], 1)
        (entry,) = written["catalogs"]
        self.assertEqual({key: entry[key] for key in ("id", "kind", "url", "branch", "auto")},
                         {"id": self.ns, "kind": "git", "url": self.remote.url, "branch": "main", "auto": True})
        self.assertRegex(entry["added"], r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
        self.assertEqual(sorted(entry), ["added", "auto", "branch", "id", "kind", "url"], "no pin, no allowance: nothing is trusted")
        self.assertEqual(oct((self.root / ".lab/catalogs.json").stat().st_mode & 0o777), "0o600")
        self.assertFalse((self.root / ".lab/catalogs.lock").exists(), "no copy, so no record of one")
        self.assertFalse((self.root / ".lab/cache").exists(), "nothing is fetched")

    def test_the_tracked_file_of_the_engine_seeds_a_first_run_as_it_is(self):
        # The file the engine ships, read with the address grammar of production: the reserved name of its first catalog is
        # accepted by role, and the intent keeps what the file says
        shutil.copy(ENGINE.parent / "catalogs.default.json", self.root / "catalogs.default.json")
        self.patch(defaults, "_address", REAL_ADDRESS)
        tracked = json.loads((ENGINE.parent / "catalogs.default.json").read_text(encoding="utf-8"))["catalogs"]
        firstrun.reconcile()
        self.assertEqual([(one["id"], one["url"], one.get("branch")) for one in registry.entries()],
                         [(one["id"], one["url"], one.get("branch")) for one in tracked])
        self.assertEqual(list(firstrun.PROGRESS), [one["id"] for one in tracked])
        self.assertEqual(paths.default_ns(), tracked[0]["id"])
        self.assertEqual(firstrun.content({}, {})["key"], "downloading")

    def test_the_file_is_written_whole_with_write_atomic(self):
        written = []
        real = paths.write_atomic
        self.patch(paths, "write_atomic", lambda file, text, mode=0o644: (written.append((Path(file).name, mode)), real(file, text, mode))[1])
        firstrun.reconcile()
        self.assertIn(("catalogs.json", 0o600), written)

    def test_every_listed_catalog_is_seeded_in_the_order_of_the_file(self):
        self.listed(self.ns, also=("second", "third"), url=self.remote.url)
        firstrun.reconcile()
        self.assertEqual([one["id"] for one in self.config()["catalogs"]], [self.ns, "second", "third"])
        self.assertEqual(list(firstrun.PROGRESS), [self.ns, "second", "third"])

    def test_the_first_state_says_downloading_before_any_io(self):
        firstrun.reconcile()
        self.assertEqual(firstrun.PROGRESS, {self.ns: {"phase": "probe", "fraction": 0.02}})
        said = firstrun.content({}, {})
        self.assertEqual((said["key"], said["progress"], said["line"], said["code"]), ("downloading", 0.02, "", None))
        self.assertEqual(said["address"], registry.display_address(self.remote.url))
        self.assertIsNone(said["offer"], "the catalog is added: there is nothing to offer")
        (row,) = [one for one in service.describe({}, {}) if one.get("id") == self.ns]
        self.assertEqual((row["state"]["key"], row["progress"]), ("downloading", 0.02),
                         "the Catalogs row and its bar say the same")
        self.assertEqual(registry.inputs()[self.ns]["state"], "downloading")

    def test_a_file_that_exists_adds_nothing_and_removes_nothing(self):
        mine = {"schema": 1, "catalogs": [{"id": "mine", "kind": "git", "url": "https://example.invalid/me/mine.git",
                                          "auto": False, "added": "2026-10-16T09:00:00Z"}]}
        (self.root / ".lab").mkdir(exist_ok=True)
        file = self.root / ".lab/catalogs.json"
        file.write_text(json.dumps(mine, indent=2) + "\n", encoding="utf-8")
        before = file.read_bytes()
        stamp = file.stat().st_mtime_ns
        firstrun.reconcile()
        self.assertEqual(file.read_bytes(), before)
        self.assertEqual(file.stat().st_mtime_ns, stamp, "not even rewritten")
        self.assertEqual(list(firstrun.PROGRESS), ["mine"], "only what has no copy is marked, and the listed one is not there")
        self.assertEqual([row["id"] for row in registry.suggestions()], [self.ns], "the listed catalog is one row to add")

    def test_a_removed_listed_catalog_is_a_not_added_row_and_the_content_offers_it(self):
        registry.write_config({"schema": 1, "catalogs": []})
        firstrun.reconcile()
        self.assertEqual(self.config(), {"schema": 1, "catalogs": []}, "the person removed it: it stays removed")
        self.assertEqual(firstrun.PROGRESS, {})
        rows = [one for one in service.describe({}, {}) if one.get("id") == self.ns]
        self.assertEqual([(one["kind"], one["listed"], one["state"]["key"], one["state"]["label"]) for one in rows],
                         [("suggested", True, "suggested", "Not added")])
        said = firstrun.content({}, {})
        self.assertEqual((said["key"], said["offer"]), ("none", {"id": self.ns, "name": "Demos"}))

    def test_an_existing_file_that_cannot_be_read_is_never_a_first_run(self):
        (self.root / ".lab").mkdir(exist_ok=True)
        file = self.root / ".lab/catalogs.json"
        damaged = ["{", "", "[]", "null", '{"schema": 7, "catalogs": []}', '{"schema": 1}', '{"schema": 1, "catalogs": 4}',
                   '{"catalogs": "x"}', "\x00\x01 not json", '{"schema": 1, "catalogs": [4, "x", null]}']
        for text in damaged:
            with self.subTest(text=text[:30]):
                firstrun.PROGRESS.clear()
                file.write_text(text, encoding="utf-8")
                firstrun.reconcile()
                self.assertEqual(file.read_text(encoding="utf-8"), text, "the file is the person's, damaged or not")
                self.assertEqual([one.name for one in file.parent.iterdir() if ".bad-" in one.name], [],
                                 "nothing is set aside: no write was made")
                self.assertEqual(firstrun.PROGRESS, {})
                self.assertEqual(firstrun.content({}, {})["key"], "none")
        self.assertTrue(registry.config_problem(), "and the Catalogs page says why")

    def test_a_file_that_is_there_but_cannot_be_opened_is_not_a_first_run_either(self):
        (self.root / ".lab").mkdir(exist_ok=True)
        file = self.root / ".lab/catalogs.json"
        file.write_text('{"schema": 1, "catalogs": []}', encoding="utf-8")
        file.chmod(0)
        self.addCleanup(file.chmod, 0o600)
        if os.access(file, os.R_OK):
            self.skipTest("this user reads any file (root)")
        firstrun.reconcile()
        file.chmod(0o600)
        self.assertEqual(file.read_text(encoding="utf-8"), '{"schema": 1, "catalogs": []}')
        self.assertEqual(firstrun.PROGRESS, {})

    def test_a_directory_in_the_place_of_the_file_is_not_a_first_run(self):
        (self.root / ".lab/catalogs.json").mkdir(parents=True)
        firstrun.reconcile()
        self.assertTrue((self.root / ".lab/catalogs.json").is_dir())
        self.assertEqual(firstrun.PROGRESS, {})

    def test_a_default_file_that_lists_nothing_writes_nothing_so_a_good_one_is_still_a_first_run(self):
        (self.root / "catalogs.default.json").write_text('{"schema": 1, "catalogs": [{"id": "x"}]}', encoding="utf-8")
        firstrun.reconcile()
        self.assertIsNone(self.config(), "an empty list would be the opt-out, which nobody asked for")
        (self.root / "catalogs.default.json").unlink()
        firstrun.reconcile()
        self.assertIsNone(self.config())
        self.listed(self.ns, url=self.remote.url)
        firstrun.reconcile()
        self.assertEqual([one["id"] for one in self.config()["catalogs"]], [self.ns])

    def test_the_empty_list_of_the_opt_out_is_kept_and_nothing_is_downloaded(self):
        (self.root / ".lab").mkdir(exist_ok=True)
        (self.root / ".lab/catalogs.json").write_text('{"schema": 1, "catalogs": []}', encoding="utf-8")
        firstrun.reconcile()
        firstrun.start_worker()
        self.addCleanup(firstrun.stop_worker)
        self.patch(firstrun, "provision", side_effect=AssertionError("nothing to download"))
        time.sleep(0.2)
        self.assertEqual(self.config(), {"schema": 1, "catalogs": []})
        self.assertEqual(firstrun.content({}, {})["key"], "none")

    def test_reconcile_twice_changes_nothing_and_keeps_the_bar_where_it_is(self):
        firstrun.reconcile()
        before = (self.root / ".lab/catalogs.json").read_bytes()
        firstrun.PROGRESS[self.ns] = {"phase": "fetch", "fraction": 0.6}
        firstrun.reconcile()
        firstrun.reconcile()
        self.assertEqual((self.root / ".lab/catalogs.json").read_bytes(), before)
        self.assertEqual(firstrun.PROGRESS[self.ns], {"phase": "fetch", "fraction": 0.6}, "a click during a download is no restart")

    def test_the_failure_of_an_earlier_run_is_forgotten_when_the_catalog_is_marked(self):
        firstrun.reconcile()
        registry.set_lock(self.ns, error="unreachable")
        firstrun.PROGRESS.clear()   # a new run of the Lab
        self.assertEqual(registry.inputs()[self.ns]["state"], "unreachable")
        firstrun.reconcile()
        self.assertIsNone(self.lock()["error"])
        self.assertEqual(registry.inputs()[self.ns]["state"], "downloading", "the row says what the content says")

    def test_only_a_git_catalog_with_no_copy_is_marked(self):
        folder = self.scratch / "folder"
        folder.mkdir()
        registry.write_config({"schema": 1, "catalogs": [
            {"id": "copied", "kind": "git", "url": "https://example.invalid/a/copied.git"},
            {"id": "waiting", "kind": "git", "url": "https://example.invalid/a/waiting.git"},
            {"id": "linked", "kind": "folder", "path": str(folder)}]})
        registry.set_lock("copied", pin="b" * 40)
        firstrun.reconcile()
        self.assertEqual(list(firstrun.PROGRESS), ["waiting"], "a folder has no pin by rule 8 and is not downloaded")

    def test_a_lock_that_another_process_holds_does_not_stop_the_start(self):
        firstrun.reconcile()
        registry.set_lock(self.ns, error="unreachable")
        firstrun.PROGRESS.clear()
        self.patch(registry, "set_lock", side_effect=CatalogError("busy", "Another catalog change is running.", 409))
        firstrun.reconcile()
        self.assertEqual(list(firstrun.PROGRESS), [self.ns], "marked all the same")

    def test_two_starts_at_the_same_moment_write_the_intent_once(self):
        gate = threading.Barrier(2)
        real = defaults.read

        def read():
            found = real()
            try:
                gate.wait(5)
            except threading.BrokenBarrierError:
                pass
            return found
        self.patch(defaults, "read", read)
        threads = [threading.Thread(target=firstrun.reconcile) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual([one["id"] for one in self.config()["catalogs"]], [self.ns])

    def test_reconcile_runs_no_git_and_touches_no_network(self):
        # Quiet.setUp made any process or connection a failure; the whole of reconcile is local
        firstrun.reconcile()
        self.assertTrue(self.config())


class OptOut(Quiet):

    def test_the_variable_skips_the_intent_the_marks_and_the_thread(self):
        for value in ("1", "true", "YES", " on "):
            with self.subTest(value=value), mock.patch.dict(os.environ, {firstrun.OPT_OUT: value}):
                firstrun.PROGRESS.clear()
                firstrun.reconcile()
                firstrun.start_worker()
                self.assertIsNone(self.config())
                self.assertEqual(firstrun.PROGRESS, {})
                self.assertIsNone(firstrun._worker)
                self.assertEqual([one for one in threading.enumerate() if one.name == "lab-first-run"], [])
                self.assertTrue(firstrun.opted_out())

    def test_any_other_value_does_not(self):
        for value in ("", "0", "no", "false", "off", "2"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {firstrun.OPT_OUT: value}):
                self.assertFalse(firstrun.opted_out())
        with mock.patch.dict(os.environ):
            os.environ.pop(firstrun.OPT_OUT, None)
            self.assertFalse(firstrun.opted_out())
            firstrun.reconcile()
            self.assertTrue(self.config())

    def test_wake_without_a_worker_does_nothing(self):
        firstrun.wake()


class Content(Quiet):
    """State.content: the four keys, from the disk and from memory. The default file lists a github.com address, so
    that the lines name a host, a repository and the sign-in that goes with them."""

    def setUp(self):
        super().setUp()
        self.listed(self.ns, url="https://github.com/example/demos.git")

    def put(self, *entries, **locks):
        registry.write_config({"schema": 1, "catalogs": list(entries)})
        for ns, fields in locks.items():
            registry.set_lock(ns, **fields)

    def git(self, ns="demos", url="https://github.com/example/demos.git"):
        return {"id": ns, "kind": "git", "url": url}

    def test_the_shape_is_the_one_the_page_reads_and_is_json(self):
        for state in ({}, {"catalogs": {}}, None):
            said = firstrun.content({}, state)
            self.assertEqual(set(said), {"key", "line", "code", "progress", "address", "offer"})
            json.dumps(said)

    def test_none_when_there_is_no_catalog_at_all_and_it_offers_the_first_listed_one(self):
        self.put()
        self.assertEqual(firstrun.content({}, {}), {"key": "none", "line": "", "code": None, "progress": None, "address": "",
                                                    "offer": {"id": self.ns, "name": "Demos"}})

    def test_the_offer_is_the_first_listed_catalog_that_is_not_added(self):
        self.listed(self.ns, also=("second",), url=self.remote.url)
        self.put(self.git("demos"))
        self.assertEqual(firstrun.content({}, {})["offer"], {"id": "second", "name": "second"})
        self.put(self.git("second"))
        self.assertEqual(firstrun.content({}, {})["offer"], {"id": self.ns, "name": "Demos"})
        self.put(self.git("demos"), self.git("second"))
        self.assertIsNone(firstrun.content({}, {})["offer"], "both are added")

    def test_none_without_a_listed_catalog_offers_nothing(self):
        (self.root / "catalogs.default.json").unlink()
        self.put()
        self.assertEqual(firstrun.content({}, {})["offer"], None, "the page then offers to browse")

    def test_no_file_at_all_is_none_too(self):
        self.assertEqual(firstrun.content({}, {})["key"], "none")

    def test_downloading_while_an_attempt_a_person_can_see_runs(self):
        self.put(self.git())
        firstrun.PROGRESS[self.ns] = {"phase": "fetch", "fraction": 0.42}
        registry.set_lock(self.ns, error="unreachable")   # a failure of an earlier attempt does not hide a click
        said = firstrun.content({}, {})
        self.assertEqual((said["key"], said["progress"], said["line"], said["code"]), ("downloading", 0.42, "", None))
        self.assertEqual(said["address"], "github.com/example/demos")

    def test_downloading_while_it_waits_for_its_turn(self):
        self.put(self.git())
        said = firstrun.content({}, {})
        self.assertEqual((said["key"], said["progress"], said["address"]), ("downloading", None, "github.com/example/demos"),
                         "no failure, no copy: it is about to start")

    COMMIT = {"op": "add", "from": None, "to": "c" * 40}

    def test_a_failure_the_lock_holds_is_failed_whatever_flag_a_commit_left_standing(self):
        # What a commit that failed in an earlier version of this module left: the failure and the flag of the commit. The
        # failure is what the page says, with its line and its way out; a flag never hides it behind "Downloading"
        self.put(self.git())
        registry.set_lock(self.ns, error="unreachable", pending=self.COMMIT)
        said = firstrun.content({}, {})
        self.assertEqual((said["key"], said["code"], said["line"]),
                         ("failed", "unreachable", "Cannot reach github.com. Check your connection, then try again."))

    def test_a_first_commit_under_way_is_a_download(self):
        self.put(self.git())
        registry.set_lock(self.ns, error=None, pending=self.COMMIT)   # commit_add: the failure is cleared, the flag set
        self.assertEqual(firstrun.content({}, {})["key"], "downloading", "nothing failed before: it is the first attempt")

    def test_a_silent_retry_that_reaches_its_commit_is_still_the_failure_it_was(self):
        # commit_add clears `error` when it sets `pending`: for the milliseconds between that and the end of the commit
        # the lock says nothing, and the page must not flicker from "failed" to "downloading" and back
        self.put(self.git())
        firstrun._lines[self.ns] = ("timeout", "github.com did not answer in time. Try again.")
        registry.set_lock(self.ns, error=None, pending=self.COMMIT)
        said = firstrun.content({}, {})
        self.assertEqual((said["key"], said["code"], said["line"]),
                         ("failed", "timeout", "github.com did not answer in time. Try again."))
        registry.set_lock(self.ns, pin="c" * 40, pending=None)
        self.assertEqual(firstrun.content({}, {})["key"], "ready", "and the commit point ends it")

    def test_a_retry_a_person_sees_is_a_download_through_its_commit(self):
        self.put(self.git())
        firstrun._lines[self.ns] = ("timeout", "github.com did not answer in time. Try again.")
        firstrun.PROGRESS[self.ns] = {"phase": "commit", "fraction": 0.97}
        registry.set_lock(self.ns, error=None, pending=self.COMMIT)
        self.assertEqual(firstrun.content({}, {})["key"], "downloading")

    def test_a_click_is_a_download_at_once_even_when_this_process_remembers_the_failure(self):
        # service.check_now clears the error, and only then does reconcile put the mark: in between there is no commit
        # under way, and the remembered failure must not bring the line back for a person who just pressed Try again
        self.put(self.git(), demos={"error": "timeout"})
        firstrun._lines[self.ns] = ("timeout", "github.com did not answer in time. Try again.")
        self.assertEqual(firstrun.content({}, {})["key"], "failed")
        registry.set_lock(self.ns, error=None)
        self.assertEqual(firstrun.content({}, {})["key"], "downloading")

    def test_a_missing_branch_is_named_when_the_catalog_says_which(self):
        # The line is rebuilt from the code in the lock when another process wrote it: its branch is the entry's
        (self.root / "catalogs.default.json").unlink()
        branched = {"id": "second", "kind": "git", "url": "https://git.example.test/team/second.git", "branch": "dev"}
        self.put(branched, second={"error": "branch_missing"})
        self.assertEqual(firstrun.content({}, {})["line"],
                         "team/second has no branch dev. Check the name, or leave it empty for the default.")
        self.put({k: v for k, v in branched.items() if k != "branch"}, second={"error": "branch_missing"})
        self.assertEqual(firstrun.content({}, {})["line"], "The catalog could not be downloaded. Try again.",
                         "a line that needs a branch it does not have is the general line, not 'no branch .'")

    def test_the_progress_is_the_first_download_that_has_one(self):
        self.put(self.git("demos"), self.git("second", "https://git.example.test/team/second.git"))
        firstrun.PROGRESS["second"] = {"phase": "stage", "fraction": 0.9}
        self.assertEqual(firstrun.content({}, {})["progress"], 0.9)

    def test_failed_says_the_first_failure_with_its_line_code_and_address(self):
        self.put(self.git("demos"), self.git("second", "https://git.example.test/team/second.git"),
                 demos={"error": "unreachable"}, second={"error": "no_access"})
        said = firstrun.content({}, {})
        self.assertEqual(said, {"key": "failed", "line": "Cannot reach github.com. Check your connection, then try again.",
                                "code": "unreachable", "progress": None, "address": "github.com/example/demos", "offer": None})

    def test_an_automatic_retry_leaves_no_mark_so_the_state_stays_failed(self):
        self.put(self.git(), demos={"error": "timeout"})
        for _ in range(3):   # the retries of the worker: the lock keeps the failure, PROGRESS is not touched
            self.assertEqual(firstrun.content({}, {})["key"], "failed")
        self.assertEqual(firstrun.content({}, {})["line"], "github.com did not answer in time. Try again.")

    def test_a_click_marks_the_catalog_and_the_state_is_downloading_until_it_fails_again(self):
        self.put(self.git(), demos={"error": "timeout"})
        self.assertEqual(firstrun.content({}, {})["key"], "failed")
        registry.set_lock(self.ns, error=None)   # what service.check_now does, then reconcile
        firstrun.reconcile()
        self.assertEqual(firstrun.content({}, {})["key"], "downloading")
        registry.set_lock(self.ns, error="timeout")   # the attempt fails: the error first, then the mark
        self.assertEqual(firstrun.content({}, {})["key"], "downloading", "the mark is still there for an instant")
        firstrun.PROGRESS.pop(self.ns)
        self.assertEqual(firstrun.content({}, {})["key"], "failed")

    def test_the_line_of_the_last_failure_of_this_process_is_the_precise_one_while_its_code_is_the_lock_s(self):
        self.put(self.git(), demos={"error": "git_old"})
        self.assertEqual(firstrun.content({}, {})["line"], "Git is too old. Install Git 2.25 or later, then try again.",
                         "another process's failure: the version is not known")
        firstrun._lines[self.ns] = ("git_old", "Git 2.20.1 is too old. Install Git 2.25 or later, then try again.")
        self.assertEqual(firstrun.content({}, {})["line"], "Git 2.20.1 is too old. Install Git 2.25 or later, then try again.")
        registry.set_lock(self.ns, error="unreachable")
        self.assertEqual(firstrun.content({}, {})["line"], "Cannot reach github.com. Check your connection, then try again.",
                         "a line of another code is not shown for this one")

    def test_the_lines_of_the_failures_of_e2_7_come_from_the_codes_in_the_lock(self):
        table = {"unreachable": "Cannot reach github.com. Check your connection, then try again.",
                 "timeout": "github.com did not answer in time. Try again.",
                 "git_missing": "Git is not installed. Install it, then try again.",
                 "no_access": "No access to example/demos. Run gh auth login in a terminal, then try again.",
                 "not_found": "No access to example/demos. Run gh auth login in a terminal, then try again.",
                 "certificate": "The certificate of github.com was refused. A company proxy may be in the way.",
                 "wrong_catalog": "example/demos is not the catalog this Lab expects. Run make update, then try again.",
                 "no_readable_version": "The catalog needs a newer Lab. Run make update, then restart the Lab.",
                 "lab_old": "The catalog is not ready for this version of the Lab. Try again later."}
        self.put(self.git())
        for code, line in table.items():
            with self.subTest(code):
                registry.set_lock(self.ns, error=code)
                said = firstrun.content({}, {})
                self.assertEqual((said["key"], said["code"], said["line"]), ("failed", code, line))

    def test_ready_when_a_catalog_has_a_copy_and_it_wins_over_a_download_and_a_failure(self):
        self.put(self.git("demos"), self.git("second", "https://git.example.test/team/second.git"),
                 self.git("third", "https://git.example.test/team/third.git"), demos={"pin": "a" * 40},
                 third={"error": "timeout"})
        firstrun.PROGRESS["second"] = {"phase": "fetch", "fraction": 0.1}
        said = firstrun.content({}, {})
        self.assertEqual((said["key"], said["line"], said["code"], said["progress"]), ("ready", "", None, None))

    def test_ready_when_a_folder_catalog_is_there_and_not_when_it_is_gone(self):
        folder = self.scratch / "mine"
        folder.mkdir()
        (folder / "catalog.yml").write_text("x-gravitee-catalog: {}\n", encoding="utf-8")
        self.put({"id": "mine", "kind": "folder", "path": str(folder)})
        link = self.root / ".lab/catalogs/mine"
        link.symlink_to(folder)
        self.assertEqual(firstrun.content({}, {})["key"], "ready")
        rows = registry.inputs()
        self.assertEqual(firstrun.content({}, {"catalogs": rows})["key"], "ready", "the rows the state read are used")
        link.unlink()
        self.assertEqual(firstrun.content({}, {})["key"], "none")

    def test_the_rows_the_state_read_are_used_and_the_registry_is_asked_only_for_a_folder(self):
        self.put(self.git(), demos={"error": "timeout"})
        self.patch(registry, "inputs", side_effect=AssertionError("not needed for a git catalog"))
        self.assertEqual(firstrun.content({}, {})["key"], "failed")

    def test_the_items_of_this_machine_count_for_nothing(self):
        self.put()
        local = self.root / ".lab/catalogs/local/addons/mine"
        local.mkdir(parents=True)
        (local / "compose.yml").write_text("x-gravitee: {kind: addon}\nservices: {}\n", encoding="utf-8")
        self.assertEqual(firstrun.content({}, {})["key"], "none")

    def test_a_shadow_that_stands_in_for_a_git_catalog_is_ready(self):
        folder = self.scratch / "copy"
        folder.mkdir()
        self.put({"id": self.ns, "kind": "folder", "path": str(folder), "shadows": {"url": self.remote.url}})
        (self.root / ".lab/catalogs" / self.ns).symlink_to(folder)
        self.assertEqual(firstrun.content({}, {})["key"], "ready")

    def test_content_reads_the_disk_and_memory_and_nothing_else(self):
        # Quiet.setUp: a process or a connection fails the test
        self.put(self.git(), demos={"error": "timeout"})
        for _ in range(3):
            firstrun.content({}, {})


class Early(Case):
    """The first start does not run when the Lab only checks, lists, or finds another Lab serving."""

    def setUp(self):
        self.steps = [self.patch(firstrun, "reconcile", side_effect=AssertionError("reconcile ran")),
                      self.patch(firstrun, "start_worker", side_effect=AssertionError("the worker started")),
                      self.patch(layout, "ensure", side_effect=AssertionError("the layout was moved")),
                      self.patch(service, "recover", side_effect=AssertionError("the sweeper ran"))]
        self.patch(api, "requirement", return_value="")
        self.patch(api.layers, "prepare")

    def test_check_starts_nothing(self):
        self.patch(api.check, "run", return_value=0)
        with self.assertRaises(SystemExit) as caught:
            api.main(["--check"])
        self.assertEqual(caught.exception.code, 0)

    def test_list_starts_nothing(self):
        said = self.patch(api, "listing")
        api.main(["--list"])
        said.assert_called_once_with()

    def test_a_second_make_ui_finds_the_first_and_touches_nothing(self):
        self.patch(api, "ThreadingHTTPServer", side_effect=OSError(errno.EADDRINUSE, "Address already in use"))
        self.patch(api, "is_lab_ui", return_value=True)
        opened = self.patch(api.webbrowser, "open")
        with mock.patch("builtins.print"):
            api.serve(9000, True)
        opened.assert_called_once_with("http://localhost:9000")
        self.assertEqual([step.call_count for step in self.steps], [0, 0, 0, 0])

    def test_importing_the_module_starts_no_thread(self):
        code = ("import sys, threading; sys.path.insert(0, sys.argv[1]); from lab_ui.catalogs import firstrun; "
                "print(sorted(t.name for t in threading.enumerate()), firstrun._worker)")
        done = subprocess.run([sys.executable, "-I", "-c", code, str(ENGINE)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.stdout.strip(), "['MainThread'] None", done.stderr)

    def test_the_server_starts_it_after_the_bind_in_this_order(self):
        calls = []
        for step in self.steps:
            step.side_effect = None
        self.patch(layout, "ensure", side_effect=lambda: calls.append("ensure"))
        self.patch(service, "recover", side_effect=lambda: calls.append("recover"))
        self.patch(firstrun, "reconcile", side_effect=lambda: calls.append("reconcile"))
        self.patch(firstrun, "start_worker", side_effect=lambda: calls.append("start_worker"))
        api.start_up()
        self.assertEqual(calls, ["ensure", "recover", "reconcile", "start_worker"])


class Wording(Case):
    """The words of the first start are said once: where the first start words a failure as the catalogue of errors does not, here."""

    def test_the_failures_that_are_worded_for_a_catalog_nobody_asked_for(self):
        for code in ("git_missing", "git_old", "no_readable_version", "no_access", "not_found", "lab_new", "schema_new"):
            self.assertIn(firstrun.ALIASES.get(code, code), firstrun.REWORDED, code)
        for line in firstrun.LINES.values():
            self.assertNotIn("add it again", line)
            self.assertNotIn("add the catalog again", line)
            self.assertFalse(re.search(r"[·•]", line))

    def test_a_refusal_that_section_11_words_the_same_is_left_as_it_is(self):
        error = CatalogError("unreachable", "Cannot reach x. Check your connection, then try again.", 502, output="o")
        self.assertIs(firstrun._worded(error, None), error)

    def test_a_reworded_refusal_keeps_its_code_status_and_facts(self):
        error = gitx.fail("git_old", have="2.20", output="raw")
        said = firstrun._worded(error, None)
        self.assertEqual((said.code, said.status, said.detail), ("git_old", error.status, {"output": "raw"}))
        self.assertEqual(said.message, "Git 2.20 is too old. Install Git 2.25 or later, then try again.")

    def test_a_missing_branch_is_named_and_a_line_that_lacks_its_fact_is_the_general_one(self):
        github = gitx.parse_address("https://github.com/example/demos.git")
        self.assertEqual(firstrun.line_of("branch_missing", github, branch="dev"),
                         "example/demos has no branch dev. Check the name, or leave it empty for the default.")
        for branch in ("", None):
            self.assertEqual(firstrun.line_of("branch_missing", github, branch=branch), firstrun.LINES["failed"])
        self.assertEqual(firstrun.line_of("branch_missing", github, "", "dev"),
                         "example/demos has no branch dev. Check the name, or leave it empty for the default.")

    def test_the_codes_whose_lines_only_the_attempt_knew_read_as_the_general_line(self):
        github = gitx.parse_address("https://github.com/example/demos.git")
        for code in ("catalog_invalid", "name_taken", "damaged"):
            self.assertEqual(firstrun.line_of(code, github), firstrun.LINES["failed"], code)

    def test_wrong_catalog_is_worded_once_by_install_under_its_public_name(self):
        github = gitx.parse_address("https://github.com/example/demos.git")
        line = "example/demos is not the catalog this Lab expects. Run make update, then try again."
        self.assertEqual(install.not_expected("example/demos").message, line)
        self.assertEqual(firstrun.line_of("wrong_catalog", github), line)
        self.assertEqual(firstrun.line_of("wrong_catalog", None), install.not_expected(None).message)
        named = self.patch(install, "not_expected", side_effect=lambda called=None: CatalogError("wrong_catalog", "public", 409))
        self.assertEqual(firstrun.line_of("wrong_catalog", github), "public")
        named.assert_called_once_with("example/demos")
        self.assertFalse(hasattr(firstrun, "_wrong_catalog"), "this module words it nowhere, and keeps no copy of a way to")

    def test_the_wait_between_two_failures(self):
        self.assertEqual((firstrun.SCHEDULE, firstrun.LATER), ((15.0, 60.0, 300.0), 1800.0))

    def test_the_progress_ranges_are_the_ones_of_e2_3(self):
        self.assertEqual(firstrun.PHASES, {"probe": (0.02, 0.05), "fetch": (0.05, 0.85), "stage": (0.85, 0.97),
                                           "commit": (0.97, 1.0)})

    def test_a_progress_that_is_not_seen_is_never_written(self):
        self.patch(firstrun, "PROGRESS", Recorded())
        firstrun._set("nowhere", "fetch", 0.5)
        self.assertEqual(firstrun.PROGRESS.sets, [])
        firstrun.PROGRESS["here"] = {"phase": "probe", "fraction": 0.02}
        firstrun._set("here", "fetch", 0.5)
        firstrun._set("here", "probe", 1.0)
        self.assertEqual(firstrun.PROGRESS["here"], {"phase": "fetch", "fraction": 0.45}, "and it never goes back")


class State(Served, Case):
    """/api/state as the page reads it, from the first poll to the lists filling: the real handler on a free port, the
    real core file read by Compose 5 (a visible skip without it), no Docker daemon."""

    def setUp(self):
        fixtures.need_compose()
        docker = os.path.join(os.path.expanduser("~"), ".docker")   # where the Compose plugin is, before the home is hidden
        self.world()
        fixtures.core(self)
        self.start(mock.patch.dict(os.environ, {"DOCKER_CONFIG": docker}))
        for module in (api.project, envfiles):
            self.patch(module, "ROOT", self.root)
        self.patch(api.compose, "ps", side_effect=compose.ComposeError("Cannot connect to the Docker daemon"))
        self.patch(api.license, "status_of", lambda variable: None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_port

    def state(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        self.addCleanup(connection.close)
        connection.request("GET", "/api/state")
        response = connection.getresponse()
        body = json.loads(response.read())
        self.assertEqual(response.status, 200, body)
        return body

    def test_the_first_state_says_downloading_the_failure_stays_and_the_lists_fill(self):
        firstrun.reconcile()
        with mock.patch.object(gitx, "run", side_effect=AssertionError("git was run")), \
                mock.patch.object(gitx, "stream", side_effect=AssertionError("git was streamed")):
            first = self.state()
        self.assertEqual((first["content"]["key"], first["content"]["progress"], first["content"]["line"]),
                         ("downloading", 0.02, ""), "before any I/O")
        (row,) = [one for one in first["catalogs"] if one["kind"] == "git"]
        self.assertEqual((row["id"], row["state"]["key"], row["progress"]), (self.ns, "downloading", 0.02))
        self.assertEqual((first["addons"], first["setups"], first["playgrounds"]), ([], [], []), "and no list yet")
        self.assertEqual(first["layout"]["phase"], "ready")
        # The attempt fails: the state says it, in place, and an automatic retry leaves it so
        real = gitx.ls_remote
        self.patch(gitx, "ls_remote", side_effect=gitx.fail("unreachable", gitx.parse_address(self.remote.url)))
        with self.assertRaises(CatalogError):
            firstrun.provision(self.ns)
        for _ in range(2):
            failed = self.state()
            self.assertEqual((failed["content"]["key"], failed["content"]["code"]), ("failed", "unreachable"))
            self.assertEqual(failed["content"]["line"], "Cannot reach localhost. Check your connection, then try again.")
            (row,) = [one for one in failed["catalogs"] if one["kind"] == "git"]
            self.assertEqual((row["state"]["key"], row["progress"], row["attention"]), ("unreachable", None, "download"))
        # The retry works: the lists fill, with no job and nothing else added to the state
        self.patch(gitx, "ls_remote", real)
        firstrun.provision(self.ns)
        ready = self.state()
        self.assertEqual(ready["content"], {"key": "ready", "line": "", "code": None, "progress": None, "address": "",
                                            "offer": None})
        self.assertEqual([one["id"] for one in ready["addons"]], ["echo"])
        self.assertEqual([one["id"] for one in ready["playgrounds"]], ["shop"])
        self.assertFalse(ready["job"] if "job" in ready else None, "a download is not a job")
