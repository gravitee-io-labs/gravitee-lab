"""catalogs/service.py: what the API calls to work with catalogs. The rows of the state and the words they carry, the Add check
and the Add, Check again and the pass of the checker, Update and Go back, Remove, Repair, the allowance of items that use this
machine, the gate at Start, the folder catalog made from the template, and the tickets that tie a check to its change. Everything
is on disk in a temporary project folder against bare repositories made on this machine (a visible skip without git): no network, no
Docker daemon. Two bases: `Real` reads the fixture catalogs with Compose 5 (a visible skip without it) and judges them as the
project does, for the few tests whose point is the judgement; `Seam` puts a stand-in for the project and its judgement behind the
module, for the rest, which are about the flows, the lock, the lines and the codes."""

import ast
import contextlib
import http.client  # noqa: F401 - the module the network steps would use: a test patches socket to prove none is made
import json
import os
import shutil
import socket
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.docker import compose, envfiles, jobs
from lab_ui.library import layers, project
from lab_ui.gravitee import license
from lab_ui import paths
from lab_ui.catalogs import defaults, firstrun, gitx, install, manifest, registry, service, standard, trust
from lab_ui.catalogs import store as bare
from lab_ui.catalogs.registry import CatalogError

from .. import Case, fixtures, gitfixture
from .test_provision import Served

HOUR = 3600
PARTNER = {"name": "Partner fixture", "version": "2.0.0"}
BARE = {"contract": 1, "contract_min": 1, "seeder_image": ""}   # the level of this Lab, as the core says it


def parsed(path, address=None, borrowed=()):
    """manifest.read_catalog without Compose: the scalars of the file, the way the fixtures read them."""
    text = (Path(paths.ROOT) / path).read_text(encoding="utf-8")
    meta = fixtures._meta_of(text)
    return {"schema": 1, "id": meta["id"], "name": meta["name"], "description": meta.get("description", ""),
            "version": meta["version"], "lab": meta["lab"], "license": meta.get("license"), "images": None,
            "contribute": "pull-request", "shared_settings": [], "moved_from": [], "canary": [],
            "requires": {"catalogs": {}}, "unknown": []}


def lines_of(error):
    """What a refusal says: (code, status, line)."""
    return error.code, error.status, error.message


class Base(Case):
    """A project folder with the engine's files and the fixture catalog as the default one, git that reads local
    repositories, and the memory of the module cleared."""

    DEFAULT = "fixture"   # the catalog installed as the default one

    def setUp(self):
        gitfixture.require()
        fixtures.need_compose()
        # The Docker CLI finds its Compose plugin in the person's configuration folder, which the isolated home hides
        self.environ(DOCKER_CONFIG=os.path.join(os.path.expanduser("~"), ".docker"))
        fixtures.project(self, paths, project, compose, envfiles, ns=self.DEFAULT)
        self.patch(paths, "_default", (None, None))
        self.home = gitfixture.isolate(self)
        self.patch(gitx, "_ending", False)   # a test of the exit function elsewhere leaves it for good
        self.patch(license, "status_of", lambda variable: None)
        self.patch(jobs, "JOBS", jobs.Jobs())
        self.patch(service, "TICKETS", {})
        self.patch(service, "_checking", {})
        self.patch(service, "_started", {})
        self.patch(service, "_known", {"at": None, "version": None})
        project.forget()
        self.addCleanup(project.forget)
        self.remotes = {}

    def environ(self, **values):
        patcher = mock.patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def remote(self, ns="partner", **patch):
        if ns not in self.remotes:
            self.remotes[ns] = fixtures.remote(self, ns, **patch)
        return self.remotes[ns]

    def folder_catalog(self, ns="partner", name=None):
        """A working copy of a fixture catalog under the home folder: a folder an author keeps."""
        target = self.home / (name or f"{ns}-copy")
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(fixtures.CATALOGS / ns, target)
        return target

    def list_at(self, ns, remote):
        """The tracked file lists `ns` at the address of a repository on this machine. The grammar of the file takes https
        addresses only, which nothing here serves, so the check of that grammar stands aside."""
        self.patch(defaults, "_address", lambda value: value if isinstance(value, str) and "://" in value else None)
        self.patch(defaults, "_seen", (None, []))
        fixtures.default(self, ns, url=remote.url, branch="main")
        project.forget()

    def rows(self):
        return {row["id"]: row for row in service.describe(*project.loaded()) if row.get("id")}

    def row(self, ns):
        return self.rows()[ns]

    def state(self, ns):
        return self.row(ns)["state"]["key"]


class Real(Base):
    """Reads the fixtures with Compose, as the project does."""


class Seam(Base):
    """The project and its judgement are stand-ins, so that a test is about the flow: `manifest.read_catalog` reads the
    scalars of catalog.yml, the project is the units of the disk named by their folders, and `service._review` finds nothing
    against them unless `self.against` says so. Compose is not asked."""

    def setUp(self):
        gitfixture.require()
        fixtures.project(self, paths, project, compose, envfiles, ns=self.DEFAULT)
        self.patch(paths, "_default", (None, None))
        self.home = gitfixture.isolate(self)
        self.patch(gitx, "_ending", False)
        self.patch(jobs, "JOBS", jobs.Jobs())
        self.patch(service, "TICKETS", {})
        self.patch(service, "_checking", {})
        self.patch(service, "_started", {})
        self.patch(service, "_known", {"at": None, "version": None})
        self.patch(manifest, "read_catalog", parsed)
        self.remotes = {}
        self.against = {}   # {unit id: [reasons]} the stand-in judgement blocks
        self.uses = {}   # {unit id: [{"service", "label", "what", "why"}]} what it says they take of this machine
        self.up = set()   # the kind:name of the units of any catalog that run
        self.items = []   # [(kind, id, name, includes)] items of other origins the project has
        self.patch(project, "loaded", self.loaded)
        self.patch(project, "load", lambda: self.loaded()[0])
        self.forgot = []
        self.patch(project, "forget", lambda: self.forgot.append(True))
        self.patch(service, "_review", self.review)
        self.patch(project, "judge_inputs", lambda inputs: ({"addon": {}, "setup": {}, "playground": {}}, {}))
        self.patch(service, "_running", lambda found, ns: {key for key in self.up if self.in_catalog(ns, key)})
        self.patch(compose, "ps", side_effect=compose.ComposeError("no docker"))

    def in_catalog(self, ns, key):
        kind, _, name = key.partition(":")
        return name in registry.units_of(ns).get(kind, ())

    def loaded(self):
        """A project made of the disk: every unit of every catalog under the name of its folder, its catalog, and what the
        test says of it."""
        found = {"addons": {}, "setups": {}, "playgrounds": {}, "catalogs": {}, "services": {}, "groups": []}
        default = paths.default_ns()
        for ns, names in ((ns, registry.units_of(ns)) for ns in registry.precedence() if ns != "local"):
            for kind, listing in names.items():
                for name in listing:
                    uid = name if ns == default else f"@{ns}/{name}"
                    found[f"{kind}s"][uid] = {"id": uid, "kind": kind, "name": name.capitalize(), "catalog": ns,
                                              "file": paths.CATALOG_FILES[kind].format(ns=ns, name=name),
                                              "blocked": None, "uses_machine": [], "includes": [], "needs": [],
                                              "services": [], "apps": [], "addons": [], "members": []}
        for kind, uid, name, includes in self.items:
            found[f"{kind}s"][uid] = {"id": uid, "kind": kind, "name": name, "catalog": "local", "file": uid, "blocked": None,
                                      "uses_machine": [], "includes": list(includes), "needs": [], "services": [],
                                      "apps": [], "addons": [], "members": []}
        return found, {"contract": dict(BARE), "catalogs": registry.inputs(), "raw": {}}

    def review(self, ns, meta, kind, audience, listed, host, ignored, replacing=False):
        """The judgement the test dictates: every unit of the staged tree, by the name of its folder, blocked as `against`
        says and using this machine as `uses` says."""
        judged = {"addon": {}, "setup": {}, "playground": {}}
        verdicts, mine = {}, []
        default = paths.default_ns()
        for k, name, _ in project.staged_units(ns):
            uid = name if ns == default else f"@{ns}/{name}"
            reasons = self.against.get(uid) or self.against.get(name)
            judged[k][uid] = {"id": uid, "kind": k, "name": name.capitalize(), "catalog": ns,
                              "blocked": {"reasons": list(reasons)} if reasons else None}
            surface = {"images": [], "builds": [], "ports": [], "core": [], "reads": [], "plugins": [], "seed": None,
                       "shell": None, "uses_machine": list(self.uses.get(uid) or self.uses.get(name) or ()), "needs": []}
            verdicts[(k, uid)] = {"state": "ok", "reasons": [], "notes": [], "surface": surface, "codes": [], "name": name}
            mine.append((k, uid))
        return judged, verdicts, mine

    def installed(self, ns="partner", **patch):
        """Catalog `ns` installed from a bare repository, with the Lab's store: the state of a catalog that was added."""
        done = fixtures.install(self, ns, kind="git", store=True, **patch)
        self.remotes[ns] = done.remote
        return done

    def fetched(self, ns, **patch):
        """Catalog `ns` added through the Add check and the Add (against a remote), as a person adds one."""
        self.remote(ns, **patch)
        shown = service.check_address(self.remotes[ns].url)
        return service.add(shown["ticket"])


# ---------------------------------------------------------------------------------------------------------------------
# describe: the rows of the state
# ---------------------------------------------------------------------------------------------------------------------

class Rows(Seam):
    """state.catalogs: from the disk and from memory, in the order the page lists them, with its words."""

    def test_the_rows_are_the_default_catalog_personal_when_used_the_others_as_added_and_the_not_added(self):
        self.installed("partner")
        fixtures.local(self, "scratch")
        fixtures.declare(self, "lab-new")
        rows = service.describe(*project.loaded())
        self.assertEqual([(row["id"], row["kind"]) for row in rows],
                         [("fixture", "folder"), ("local", "machine"), ("partner", "git"), ("lab-new", "git")])
        self.assertEqual(rows[1]["name"], "Personal")

    def test_personal_has_no_row_until_it_holds_something(self):
        self.assertNotIn("local", [row["id"] for row in service.describe(*project.loaded())])
        fixtures.local(self, "scratch")
        row = next(row for row in service.describe(*project.loaded()) if row["id"] == "local")
        self.assertEqual((row["kind"], row["name"], row["counts"], row["state"]["key"]),
                         ("machine", "Personal", {"playground": 0, "setup": 0, "addon": 1}, "machine"))
        self.assertEqual(sorted(row["state"]), ["key", "label", "next", "reason", "tone", "whatsnew"])

    def test_a_listed_catalog_the_person_does_not_have_and_a_suggestion_are_rows_that_are_not_added(self):
        config = self.root / ".lab" / "catalogs.json"
        config.write_text(json.dumps({"schema": 1, "catalogs": []}), encoding="utf-8")
        suggestions = self.home / "suggestions.json"
        suggestions.write_text(json.dumps({"schema": 1, "catalogs": [
            {"name": "Example internal", "description": "Demos kept private", "url": "https://git.example.invalid/team/hub.git",
             "branch": "main", "host_access": True}]}), encoding="utf-8")
        self.environ(LAB_CATALOGS_FILE=str(suggestions))
        rows = service.describe(*project.loaded())
        listed, suggested = rows
        self.assertEqual((listed["id"], listed["kind"], listed["listed"], listed["name"]),
                         ("fixture", "suggested", True, "Fixture catalog"))
        self.assertEqual(listed["state"], {"key": "suggested", "label": "Not added", "tone": "muted",
                                           "reason": "Synthetic units for the tests of the Lab", "next": "", "whatsnew": []})
        self.assertEqual((suggested["id"], suggested["listed"], suggested["name"], suggested["url"], suggested["host_access"]),
                         (None, False, "Example internal", "https://git.example.invalid/team/hub.git", True))
        self.assertEqual(suggested["address"], "git.example.invalid/team/hub")

    def test_a_row_has_the_fields_of_13_3_and_none_that_is_a_credential_or_the_place_of_an_install(self):
        self.installed("partner")
        row = next(row for row in service.describe(*project.loaded()) if row["id"] == "partner")
        self.assertEqual(sorted(row), sorted([
            "id", "kind", "listed", "name", "imported", "description", "address", "url", "web_url", "version", "commit", "updated",
            "checked", "audience", "counts", "state", "progress", "attention", "auto", "has_previous", "writable", "host",
            "held", "blocked", "uses_machine", "ignored", "problems", "needs"]))
        self.assertEqual((row["name"], row["version"], row["commit"], row["counts"], row["audience"]),
                         ("Partner fixture", "2.0.0", self.remotes["partner"].head()[:7],
                          {"playground": 0, "setup": 0, "addon": 1}, "private"))
        self.assertEqual((row["auto"], row["has_previous"], row["writable"], row["host"], row["listed"], row["imported"]),
                         (True, False, False, "no", False, False))
        self.assertEqual(row["held"], {"update": "", "remove": "", "undo": "No previous version"})
        text = json.dumps(row)
        self.assertNotIn(str(self.root), text, "the place of the install is not said")
        self.assertNotIn(".lab/", text)

    def test_describe_reads_no_git_no_socket_and_no_docker(self):
        self.installed("partner")
        fixtures.declare(self, "lab-new", error="unreachable")
        with mock.patch.object(gitx, "run", side_effect=AssertionError("git was run")), \
                mock.patch.object(gitx, "stream", side_effect=AssertionError("git was streamed")), \
                mock.patch.object(gitx, "version", side_effect=AssertionError("git was asked")), \
                mock.patch.object(socket, "socket", side_effect=AssertionError("a socket was opened")), \
                mock.patch.object(compose, "run", side_effect=AssertionError("Compose was run")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("a process was started")):
            rows = service.describe(*project.loaded())
        self.assertEqual([row["id"] for row in rows if row.get("id")], ["fixture", "partner", "lab-new"])

    def test_describe_takes_what_it_is_given_and_never_raises_for_a_project_that_cannot_be_read(self):
        self.installed("partner")
        rows = service.describe({}, {})
        self.assertEqual([row["id"] for row in rows if row.get("id")], ["fixture", "partner"])
        self.assertEqual(service.describe(None, None), rows)

    def test_the_summary_of_the_project_gives_the_blocked_items_what_uses_the_machine_the_needs_and_the_problems(self):
        self.installed("partner")
        summary = {"partner": {
            "blocked": [{"kind": "addon", "id": "@partner/x", "name": "X", "reasons": ["Mounts the Docker socket"]}],
            "uses_machine": [{"id": "@partner/k3s", "name": "Kubernetes", "service": "k3s", "what": ["runs privileged"],
                              "why": "Runs containerd and the kubelet", "extra": "not part of the row"}],
            "needs": [{"id": "other", "name": "Other", "url": "https://example.invalid/o.git", "min": "1.0.0"}],
            "problems": [{"file": ".lab/catalogs/partner/addons/y/compose.yml", "display": "addons/y/compose.yml",
                          "message": "Cannot be read: boom"}]}}
        row = next(one for one in service.describe({"catalogs": summary}, {}) if one["id"] == "partner")
        self.assertEqual(row["blocked"], summary["partner"]["blocked"])
        self.assertEqual(row["uses_machine"], [{"id": "@partner/k3s", "name": "Kubernetes", "service": "k3s",
                                                "what": ["runs privileged"], "why": "Runs containerd and the kubelet"}])
        self.assertEqual(row["needs"], summary["partner"]["needs"])
        self.assertEqual(row["problems"], ["addons/y/compose.yml: Cannot be read: boom"])

    def test_a_catalog_an_item_needs_is_preselected_for_the_allowance_when_the_suggestions_file_says_so(self):
        self.installed("partner")
        suggestions = self.home / "suggestions.json"
        suggestions.write_text(json.dumps({"schema": 1, "catalogs": [
            {"name": "Other", "url": "https://git.example.invalid/team/other.git", "host_access": True},
            {"name": "Plain", "url": "https://git.example.invalid/team/plain.git"}]}), encoding="utf-8")
        self.environ(LAB_CATALOGS_FILE=str(suggestions))
        needs = [{"id": "other", "name": "Other", "url": "https://git.example.invalid/team/other.git", "min": "1.0.0"},
                 {"id": "plain", "name": "Plain", "url": "git@git.example.invalid:team/plain.git", "min": None},
                 {"id": "none", "name": "None", "url": None, "min": None}]
        row = next(one for one in service.describe({"catalogs": {"partner": {"needs": needs}}}, {}) if one.get("id") == "partner")
        self.assertEqual(row["needs"], [dict(needs[0], host_access=True), needs[1], needs[2]])
        rows = [one for one in service.describe({}, {}) if one["kind"] == "suggested"]
        self.assertEqual([(one["name"], one.get("host_access", False)) for one in rows], [("Other", True), ("Plain", False)])

    def test_a_folder_catalog_is_editable_a_git_one_is_not_and_the_address_of_a_folder_is_its_folder(self):
        folder = self.folder_catalog("partner")
        shown = service.check_address(str(folder))
        service.add(shown["ticket"])
        self.installed("lab-new")
        rows = self.rows()
        self.assertEqual((rows["partner"]["writable"], rows["partner"]["kind"], rows["partner"]["url"], rows["partner"]["web_url"]),
                         (True, "folder", "", None))
        self.assertEqual(rows["partner"]["address"], registry.display_address(str(folder)))
        self.assertEqual((rows["lab-new"]["writable"], rows["lab-new"]["kind"]), (False, "git"))
        self.assertEqual(rows["partner"]["state"]["key"], "linked")

    def test_a_listed_catalog_says_it_is_and_the_address_is_the_tracked_one(self):
        remote = self.remote("partner")
        fixtures.default(self, "partner", url=remote.url, branch="main")
        self.installed("partner")
        row = self.rows()["partner"]
        self.assertEqual((row["listed"], row["host"]), (True, "listed"))
        self.assertEqual(row["url"], remote.url)

    def test_the_web_address_is_https_only_and_a_catalog_the_person_allowed_says_so(self):
        self.installed("partner", url="https://github.com/acme/lab-hub.git", host=True)
        row = self.rows()["partner"]
        self.assertEqual((row["web_url"], row["host"], row["address"]), ("https://github.com/acme/lab-hub", "allowed",
                                                                          "github.com/acme/lab-hub"))
        fixtures.install(self, "lab-new", kind="git", store=True, url="git@github.com:acme/other.git")
        self.assertIsNone(self.rows()["lab-new"]["web_url"])


class PublicNames(unittest.TestCase):
    """service.py works with the modules around it by their public names. A rename inside one of them is then a failure of its
    own tests, and not of a route of the Catalogs page on a day nobody ran it."""

    # Until firstrun publishes `failure(ns, held)`, which `_remembered` asks first
    ALLOWED = {("firstrun", "_failure")}

    @staticmethod
    def reaches(path):
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        modules = {alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level
                   for alias in node.names}
        return sorted({(node.value.id, node.attr) for node in ast.walk(tree)
                       if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in modules
                       and node.attr.startswith("_") and not node.attr.startswith("__")})

    def test_the_service_names_nothing_private_of_the_modules_it_imports_but_what_is_allowed_and_listed(self):
        self.assertEqual(set(self.reaches(service.__file__)), self.ALLOWED)

    def test_the_names_it_took_are_public_where_they_are(self):
        for module, names in ((project, ("staged_units", "judge_staged", "judge_inputs", "held_reasons", "held_why", "conflict_id")),
                              (paths, ("names",)), (registry, ("busy",)), (install, ("not_expected", "taken", "unit_key"))):
            for name in names:
                with self.subTest(module=module.__name__, name=name):
                    self.assertTrue(callable(getattr(module, name)))
                    self.assertFalse(name.startswith("_"))

    def test_the_guard_sees_a_private_name_where_there_is_one(self):
        folder = Path(__file__).parents[1]
        with mock.patch.object(Path, "read_text", return_value="from .. import project\nfrom . import registry\n"
                               "x = project._screen\ny = registry.flock\nz = registry.__name__\n"):
            self.assertEqual(self.reaches(folder / "anything.py"), [("project", "_screen")])


class Retrying(Served, Case):
    """A download that failed and is tried again, nobody watching: `firstrun.content` says it failed through every step
    of the commit, and the row of the Catalogs page says the same, not Downloading for the milliseconds the disk does."""

    def setUp(self):
        self.world()
        self.patch(service, "_started", {})

    def failed_once(self):
        """An entry with no copy whose first attempt found the host unreachable: the lock holds the failure, this process
        remembers its line, and nothing is on the bar."""
        registry.write_config({"schema": 1, "catalogs": [{"id": self.ns, "kind": "git", "url": self.remote.url}]})
        with mock.patch.object(gitx, "ls_remote", side_effect=gitx.fail("unreachable")), self.assertRaises(CatalogError):
            firstrun.provision(self.ns)
        self.assertEqual(self.row()["state"]["key"], "unreachable")

    def row(self):
        return next(one for one in service.describe({}, {}) if one.get("id") == self.ns and one.get("kind") != "progress")

    def steps(self):
        """The attempt, with the row and the content as the page reads them at every step of the commit: [(step, row state key,
        content key)]."""
        seen = []
        self.patch(install, "STEP", lambda step: seen.append((step, self.row()["state"]["key"], firstrun.content({}, {})["key"])))
        firstrun.provision(self.ns)
        return seen

    def test_a_silent_retry_leaves_the_row_failed_through_every_step_of_its_commit_and_up_to_date_when_the_copy_is_there(self):
        self.failed_once()
        seen = self.steps()
        labels = [step for step, _, _ in seen]
        self.assertTrue({"add:pending", "add:install", "add:placed", "add:lock", "add:adopt"} <= set(labels), labels)
        end = labels.index("add:adopt")
        self.assertEqual({(row, content) for _, row, content in seen[:end]}, {("unreachable", "failed")}, str(seen))
        self.assertEqual({(row, content) for _, row, content in seen[end:]}, {("uptodate", "ready")}, str(seen))
        row = self.row()
        self.assertEqual((row["state"]["key"], row["progress"], row["attention"]), ("uptodate", None, None))

    def test_the_row_of_a_silent_retry_says_the_failure_in_the_words_of_the_lock_and_asks_for_the_download_dot(self):
        self.failed_once()
        held = {}
        self.patch(install, "STEP", lambda step: held.update({step: self.row()}) if step == "add:placed" else None)
        firstrun.provision(self.ns)
        row = held["add:placed"]
        self.assertEqual((row["state"]["label"], row["state"]["tone"]), ("Unreachable", "warn"))
        self.assertEqual((row["state"]["reason"], row["state"]["next"]),
                         ("Cannot reach localhost.", "Check your connection, then try again."))
        self.assertEqual((row["attention"], row["progress"], row["commit"]), ("download", None, None))

    def test_a_retry_a_click_started_is_a_download_through_every_step_of_its_commit(self):
        self.failed_once()
        registry.set_lock(self.ns, error=None)   # what service.check_now does, and then it marks the catalog
        firstrun.reconcile()
        self.assertEqual(self.row()["state"]["key"], "downloading")
        seen = self.steps()
        labels = [step for step, _, _ in seen]
        end = labels.index("add:adopt")
        self.assertEqual({row for _, row, _ in seen[:end]}, {"downloading"}, str(seen))
        self.assertEqual({row for _, row, _ in seen[end:]}, {"uptodate"}, str(seen))

    def test_the_first_attempt_and_the_code_in_the_lock_are_what_they_were(self):
        registry.write_config({"schema": 1, "catalogs": [{"id": self.ns, "kind": "git", "url": self.remote.url}]})
        self.assertEqual(self.row()["state"]["key"], "downloading", "no attempt yet: nothing is remembered")
        firstrun.reconcile()
        seen = self.steps()   # the first attempt, seen by a person (reconcile marked it): a download to the end
        self.assertEqual({row for step, row, _ in seen if step != "add:done"} - {"uptodate"}, {"downloading"}, str(seen))
        self.assertEqual(self.row()["state"]["key"], "uptodate")

    def test_a_failure_only_this_process_remembers_is_not_a_failure_of_a_catalog_that_has_a_copy(self):
        self.failed_once()
        firstrun.provision(self.ns)
        firstrun._lines[self.ns] = ("unreachable", "stale")   # a memory that outlived the failure
        self.assertEqual(self.row()["state"]["key"], "uptodate")
        registry.set_lock(self.ns, pending={"op": "update", "from": self.remote.head(), "to": self.remote.head()})
        self.assertEqual(self.row()["state"]["key"], "updating", "and an update under way is an update")


class States(Seam):
    """The state key and the words of a git catalog, the first that applies."""

    def given(self, ns="partner", **lock):
        done = self.installed(ns)
        registry.set_lock(ns, **lock)
        return done

    def said(self, ns="partner"):
        state = self.row(ns)["state"]
        return state["key"], state["label"], state["tone"], state["reason"], state["next"]

    def test_up_to_date_says_the_version_and_when_it_was_checked(self):
        self.given(checked=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3 * HOUR - 60)))
        self.assertEqual(self.said(), ("uptodate", "Up to date", "muted", "Version 2.0.0, checked 3 hours ago", ""))
        registry.set_lock("partner", checked=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400 * 2)))
        self.assertEqual(self.said()[3], "Version 2.0.0, checked 2 days ago")
        registry.set_lock("partner", checked=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60)))
        self.assertEqual(self.said()[3], "Version 2.0.0, checked 1 minute ago")

    def test_a_catalog_never_checked_says_checking(self):
        done = self.installed()
        registry.set_lock("partner", checked=None)
        self.assertEqual(self.said(), ("checking", "Checking", "muted", "", ""))
        self.assertTrue(done)

    def test_a_newer_version_that_changes_something_is_an_update_available(self):
        self.given(seen={"target": "c" * 40, "update": True, "tip": "ok", "version": "2.1.0"})
        self.assertEqual(self.said(), ("update", "Update available", "warn", "Version 2.1.0", ""))
        self.assertEqual(self.row("partner")["state"]["whatsnew"], [])

    def test_an_update_whose_version_was_not_raised_names_no_version(self):
        self.given(seen={"target": "c" * 40, "update": True, "tip": "ok", "version": "2.0.0"}, version="2.0.0")
        self.assertEqual(self.said(), ("update", "Update available", "warn", "", ""))

    def test_a_check_that_found_nothing_to_change_or_whose_target_is_the_pin_offers_no_update(self):
        done = self.given(seen={"target": "c" * 40, "update": False, "tip": "ok", "version": "2.1.0"})
        self.assertEqual(self.state("partner"), "uptodate")
        registry.set_lock("partner", seen={"target": done.lock["pin"], "update": True, "tip": "ok", "version": "2.0.0"})
        self.assertEqual(self.state("partner"), "uptodate")
        registry.set_lock("partner", seen="not a mapping")
        self.assertEqual(self.state("partner"), "uptodate", "a record that is none is no record")

    def test_a_tip_that_needs_a_newer_lab_is_said_and_no_update_is_offered_for_it(self):
        self.given(seen={"target": None, "update": False, "tip": "new", "version": None}, error=None)
        self.assertEqual(self.said(), (
            "lab_new", "Needs a newer Lab", "warn", "A newer version needs a newer Lab. This is the newest one this Lab reads.",
            "Run make update, then restart the Lab."))
        self.assertEqual(self.row("partner")["attention"], "lab")

    def test_every_version_needing_a_newer_lab_is_the_same_word(self):
        self.given(error="no_readable_version")
        self.assertEqual(self.state("partner"), "lab_new")

    def test_an_installed_version_below_what_this_lab_reads_is_written_for_an_older_lab(self):
        self.given(lab=0)
        self.patch(service, "_contract", lambda: dict(BARE))
        rows = service.describe(*project.loaded())
        old = next(row for row in rows if row["id"] == "partner")
        self.assertEqual((old["state"]["key"], old["state"]["label"], old["state"]["reason"], old["state"]["next"]),
                         ("lab_old", "Written for an older Lab", "This version is too old for this Lab.",
                          "Update it, or ask the owners of Partner fixture to update it."))
        self.assertIsNone(old["attention"], "nothing to press until there is an update")
        registry.set_lock("partner", seen={"target": "c" * 40, "update": True, "tip": "ok", "version": "2.1.0"})
        self.assertEqual(self.row("partner")["attention"], "update")

    def test_a_listed_catalog_older_than_this_lab_reads_says_it_is_not_ready_and_does_not_ask_its_owners(self):
        remote = self.remote("partner")
        fixtures.default(self, "partner", url=remote.url, branch="main")
        self.installed("partner", lab=0)
        state = self.row("partner")["state"]
        self.assertEqual((state["key"], state["reason"], state["next"]),
                         ("lab_old", "The catalog is not ready for this version of the Lab. Try again later.", ""))

    def test_the_failures_of_a_check_are_the_state_with_the_lines_of_section_11(self):
        cases = {
            "unreachable": ("unreachable", "Unreachable", "warn", "Cannot reach {host}. Showing the copy from 16 Oct.",
                            "Check your connection, then check again."),
            "timeout": ("unreachable", "Unreachable", "warn", "{host} did not answer in time.", "Try again."),
            "certificate": ("unreachable", "Unreachable", "warn", "The certificate of {host} was refused.",
                            "A company proxy may be in the way."),
            "no_access": ("noaccess", "No access", "error", "No access to acme/lab-hub.",
                          "Sign in with Git on this machine, then try again."),
            "not_found": ("noaccess", "No access", "error", "acme/lab-hub was not found.",
                          "Check the address, or sign in with Git if it is private."),
            "ssh_denied": ("noaccess", "No access", "error", "Permission denied for {host}.",
                           "Add your SSH key to {host}, then try again."),
            "host_key": ("noaccess", "No access", "error", "{host} is not a known SSH host.",
                         "Connect to it once from a terminal, then try again."),
            "git_missing": ("needs_git", "Needs Git", "error", "Git is not installed.", "Install Git, then try again."),
            "git_old": ("needs_git", "Needs Git", "error", "Git is too old.", "Install Git 2.25 or later, then try again."),
            "wrong_catalog": ("wrong_catalog", "Wrong catalog", "error",
                              "This address does not serve the catalog this Lab expects.", "Run make update, then try again."),
            "history_changed": ("rewritten", "Rewritten", "error", "This catalog was rewritten or moved.",
                                "Update only if you expected it."),
            "lab_new": ("lab_new", "Needs a newer Lab", "warn",
                        "A newer version needs a newer Lab. This is the newest one this Lab reads.",
                        "Run make update, then restart the Lab."),
        }
        self.installed("partner", url="https://example.com/acme/lab-hub.git")
        for code, (key, label, tone, reason, after) in cases.items():
            with self.subTest(code=code):
                registry.set_lock("partner", error=code)
                host = "example.com"
                self.assertEqual(self.said(), (key, label, tone, reason.format(host=host), after.format(host=host)))

    def test_a_failure_that_has_no_state_of_its_own_is_a_failed_download_with_its_line(self):
        self.installed("partner", url="https://github.com/acme/lab-hub.git")
        registry.set_lock("partner", error="too_large")
        state = self.row("partner")["state"]
        self.assertEqual((state["key"], state["label"], state["tone"]), ("failed", "Failed", "error"))
        self.assertEqual(state["reason"], "This catalog is larger than the Lab accepts.")
        self.assertEqual(state["next"], "Ask its owners to make it smaller.")

    def test_a_catalog_never_downloaded_says_downloading_from_where_and_one_that_failed_offers_the_failure(self):
        fixtures.declare(self, "lab-new", url="https://github.com/acme/lab-hub.git")
        self.assertEqual(self.said("lab-new"), ("downloading", "Downloading", "working", "From github.com/acme/lab-hub", ""))
        registry.set_lock("lab-new", error="unreachable")
        self.assertEqual(self.said("lab-new")[:4], ("unreachable", "Unreachable", "warn", "Cannot reach github.com."))
        self.assertEqual(self.said("lab-new")[4], "Check your connection, then try again.")

    def test_an_operation_under_way_is_updating_and_files_that_do_not_match_the_manifest_are_damaged(self):
        self.given()
        registry.set_lock("partner", pending={"op": "update", "from": "a" * 40, "to": "b" * 40})
        self.assertEqual(self.said(), ("updating", "Updating", "working", "", ""))
        registry.set_lock("partner", pending=None)
        manifest_file = self.root / ".lab" / "catalogs" / "partner" / ".installed.json"
        manifest_file.chmod(0o644)
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
        data["commit"] = "d" * 40
        manifest_file.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.said(), ("damaged", "Damaged", "error", "Some installed files changed.",
                                       "Repair it from the menu."))
        self.assertEqual(self.row("partner")["attention"], "repair")

    def test_a_folder_that_is_there_is_linked_and_one_that_is_gone_is_not_found(self):
        folder = self.folder_catalog("partner")
        service.add(service.check_address(str(folder))["ticket"])
        self.assertEqual(self.said(), ("linked", "Linked", "muted", "The folder is linked.", ""))
        shutil.rmtree(folder)
        self.assertEqual(self.said(), ("missing_folder", "Folder not found", "error", "The folder is gone.",
                                       "Put it back or remove the catalog."))

    def test_the_words_never_name_the_machine_a_namespace_a_commit_or_the_internals(self):
        self.installed("partner", url="https://github.com/acme/lab-hub.git")
        for code in ("unreachable", "no_access", "history_changed", "wrong_catalog", "too_large", "fsck"):
            registry.set_lock("partner", error=code)
            text = json.dumps(self.row("partner")["state"])
            for word in ("ticket", "namespace", "manifest", "digest", "provision", "shadow", ".lab", "refs/", str(self.root)):
                self.assertNotIn(word, text, (code, word))


class Attention(Seam):
    """What lights the dot of the menu, and what does not."""

    def given(self, **lock):
        self.installed("partner")
        registry.set_lock("partner", **lock)

    UPDATE = {"target": "c" * 40, "update": True, "tip": "ok", "version": "2.1.0"}

    def test_an_update_lights_it_unless_the_catalog_is_not_checked_automatically(self):
        self.given(seen=self.UPDATE)
        self.assertEqual(self.row("partner")["attention"], "update")
        config = registry.read_config()
        config["catalogs"] = [dict(one, auto=False) if one["id"] == "partner" else one for one in config["catalogs"]]
        registry.write_config(config)
        row = self.row("partner")
        self.assertEqual((row["state"]["key"], row["attention"], row["auto"]), ("update", None, False))

    def test_the_version_the_person_just_left_with_go_back_does_not_light_it_again(self):
        self.given(seen=self.UPDATE, previous="c" * 40)
        row = self.row("partner")
        self.assertEqual((row["state"]["key"], row["attention"], row["has_previous"]), ("update", None, True))

    def test_a_catalog_that_must_be_updated_in_the_lab_and_one_to_repair_and_a_download_that_failed_light_it(self):
        self.given(seen={"target": None, "update": False, "tip": "new", "version": None})
        self.assertEqual(self.row("partner")["attention"], "lab")
        fixtures.declare(self, "lab-new", error="unreachable")
        self.assertEqual(self.row("lab-new")["attention"], "download")

    def test_a_download_of_a_few_seconds_lights_nothing_and_one_that_lasts_does(self):
        fixtures.declare(self, "lab-new")
        now = [100.0]
        self.patch(service, "_now", lambda: now[0])
        self.assertEqual(self.row("lab-new")["attention"], None)
        now[0] += service.LONG_DOWNLOAD - 1
        self.assertEqual(self.row("lab-new")["attention"], None)
        now[0] += 2
        self.assertEqual(self.row("lab-new")["attention"], "download")
        registry.set_lock("lab-new", error=None, pin="a" * 40)   # it ended: the memory of its start goes
        self.row("lab-new")
        self.assertNotIn("lab-new", service._started)

    def test_the_offline_state_of_an_installed_catalog_does_not_light_it(self):
        self.given(error="unreachable")
        self.assertEqual(self.row("partner")["attention"], None)

    def test_a_download_says_how_far_it_is_from_the_memory_of_the_worker(self):
        fixtures.declare(self, "lab-new")
        from lab_ui.catalogs import firstrun
        with mock.patch.object(firstrun, "PROGRESS", {"lab-new": {"phase": "fetch", "fraction": 0.42}}):
            self.assertEqual(self.row("lab-new")["progress"], 0.42)
        with mock.patch.object(firstrun, "PROGRESS", {}):
            self.assertIsNone(self.row("lab-new")["progress"])
        self.installed("partner")
        with mock.patch.object(firstrun, "PROGRESS", {"partner": {"phase": "fetch", "fraction": 0.5}}):
            self.assertIsNone(self.row("partner")["progress"], "only a download has a progress")

    def test_the_fetch_of_an_add_check_is_an_entry_that_is_no_catalog(self):
        self.assertNotIn("check", [row["id"] for row in service.describe(*project.loaded())])
        service._checking["abc"] = None
        last = service.describe(*project.loaded())[-1]
        self.assertEqual(last, {"id": "check", "kind": "progress", "progress": None})
        service._checking["abc"] = 0.25
        self.assertEqual(service.describe(*project.loaded())[-1]["progress"], 0.25)
        self.assertIn("check", paths.RESERVED_NS, "so that no catalog is ever called so")


class Holds(Seam):
    """What holds the menu of a row: a job; an item that is on."""

    def test_a_job_holds_every_change_and_an_item_that_is_on_holds_the_removal_and_going_back(self):
        self.installed("partner")
        registry.set_lock("partner", previous="c" * 40)
        self.assertEqual(self.row("partner")["held"], {"update": "", "remove": "", "undo": ""})
        job = jobs.Job("Start Ledger", [["true"]], key="addon:x", verb="start")
        jobs.JOBS.jobs.append(job)
        jobs.JOBS.current = job
        self.assertEqual(self.row("partner")["held"], {"update": "Wait for Start Ledger", "remove": "Wait for Start Ledger",
                                                       "undo": "Wait for Start Ledger"})
        job.done = True
        on = self.root / ".lab" / "on.yml"
        on.write_text("include:\n  - .lab/catalogs/partner/addons/ledger/compose.yml\n", encoding="utf-8")
        self.assertEqual(self.row("partner")["held"], {"update": "", "remove": "Turn off Ledger first", "undo": "Turn off Ledger first"})

    def test_the_files_that_record_the_catalogs_cannot_be_read_is_a_line_of_the_banner_and_nothing_when_they_can(self):
        self.installed("partner")
        self.assertEqual(service.problems(), [])
        (self.root / ".lab" / "catalogs.json").write_text("{not json", encoding="utf-8")
        found = service.problems()
        self.assertEqual([(one["file"], one["editable"]) for one in found], [("Catalogs", False)])
        self.assertTrue(found[0]["message"].startswith("The file that lists your catalogs could not be read"), found)
        (self.root / ".lab" / "catalogs.json").write_text('{"schema": 1, "catalogs": []}', encoding="utf-8")
        self.assertEqual(service.problems(), [], "gone as soon as the file is fixed")

    def test_going_back_says_no_previous_version_without_one(self):
        self.installed("partner")
        self.assertEqual(self.row("partner")["held"]["undo"], "No previous version")

    def test_an_item_that_is_on_holds_the_removal_of_a_folder_catalog_too_since_the_request_is_refused_for_it(self):
        fixtures.install(self, "lab-old", kind="folder")
        self.assertEqual(self.row("lab-old")["held"]["remove"], "")
        (self.root / ".lab" / "on.yml").write_text("include:\n  - .lab/catalogs/lab-old/addons/older/compose.yml\n", encoding="utf-8")
        self.assertEqual(self.row("lab-old")["held"]["remove"], "Turn off Older first")
        with self.assertRaises(CatalogError) as raised:
            service.remove("lab-old")
        self.assertEqual(lines_of(raised.exception), ("held", 409, "Turn off Older first."))

    def test_the_on_file_is_walked_once_for_all_the_rows_and_says_what_it_says_of_each_catalog(self):
        self.installed("partner")
        fixtures.install(self, "lab-old", kind="folder")
        (self.root / ".lab" / "on.yml").write_text(
            "include:\n  - .lab/catalogs/partner/addons/ledger/compose.yml\n  - .lab/catalogs/lab-old/addons/older/compose.yml\n",
            encoding="utf-8")
        walked = []
        real = compose.includes
        self.patch(compose, "includes", lambda path: walked.append(path) or real(path))
        found = service._on_by_catalog()
        self.assertEqual(found, {"partner": {"addon:ledger"}, "lab-old": {"addon:older"}})
        self.assertEqual(sorted(found), sorted(ns for ns in found if install.on_units(ns)))
        for ns, keys in found.items():
            self.assertEqual(keys, install.on_units(ns), "the same answer as the function install holds the rule with")
        walked.clear()
        self.patch(install, "on_units", side_effect=AssertionError("the state asked once for each row"))
        rows = self.rows()
        self.assertEqual((rows["partner"]["held"]["remove"], rows["lab-old"]["held"]["remove"]),
                         ("Turn off Ledger first", "Turn off Older first"))
        self.assertEqual(len(walked), len(set(walked)), "no file of the closure is read twice for a state")


# ---------------------------------------------------------------------------------------------------------------------
# Add: the check, the ticket, the commit
# ---------------------------------------------------------------------------------------------------------------------

def held_elsewhere(lock) -> bool:
    """Whether another thread cannot take `lock` now, which is to say that this one holds it."""
    free = []
    thread = threading.Thread(target=lambda: free.append(lock.acquire(blocking=False)) or (free[0] and lock.release()))
    thread.start()
    thread.join()
    return free == [False]


class Check(Seam):
    """The address, the fetch, the stage, the preview and its ticket."""

    def test_a_repository_with_more_root_commits_than_the_lock_keeps_is_too_large_and_nothing_is_left(self):
        remote = self.remote("partner")
        tree = gitfixture.git("rev-parse", "HEAD^{tree}", repo=remote.bare).strip()
        parents = [gitfixture.git("rev-parse", "HEAD", repo=remote.bare).strip()]
        parents += [gitfixture.git("commit-tree", tree, "-m", f"root {number}", repo=remote.bare).strip()
                    for number in range(install.ROOTS_LIMIT)]
        merge = gitfixture.git("commit-tree", tree, *[word for one in parents for word in ("-p", one)], "-m", "Merge many histories",
                               repo=remote.bare).strip()
        gitfixture.git("update-ref", "refs/heads/main", merge, repo=remote.bare)
        with self.assertRaises(CatalogError) as raised:
            service.check_address(remote.url)
        self.assertEqual(lines_of(raised.exception), ("too_large", 400,
                                                      "This catalog is larger than the Lab accepts. Ask its owners to make it smaller."))
        self.assertEqual((service.TICKETS, service._checking), ({}, {}))
        self.assertEqual(sorted(os.listdir(self.root / ".lab" / "cache" / "git")) if (self.root / ".lab" / "cache" / "git").is_dir()
                         else [], [], "no store is left to be swept")
        self.assertIsNone(registry.entry("partner"))

    def test_the_preview_is_the_shape_of_13_2_and_a_ticket_that_lives_ten_minutes(self):
        remote = self.remote("partner")
        shown = service.check_address(remote.url)
        self.assertEqual(sorted(shown), ["address", "audience", "blocked", "catalog", "counts", "expires", "ignored", "rows",
                                         "ticket", "url"])
        self.assertRegex(shown["ticket"], r"^[0-9a-f]{16}$")
        self.assertEqual(shown["expires"], 600)
        self.assertEqual(shown["catalog"], {"id": "partner", "name": "Partner fixture", "version": "2.0.0", "lab": 1,
                                            "kind": "git", "description": "Synthetic units that another catalog includes"})
        self.assertEqual((shown["audience"], shown["counts"], shown["blocked"], shown["ignored"]),
                         ("public", {"playground": 0, "setup": 0, "addon": 1}, [], 0))
        self.assertEqual(sorted(shown["rows"]), ["builds", "core", "needs", "ports", "reads", "uses_machine"])
        self.assertEqual(shown["rows"]["builds"], {"local": 0, "remote": []})
        self.assertEqual(shown["url"], remote.url)
        self.assertIn(shown["ticket"], service.TICKETS)
        self.assertNotIn("newer", shown)

    def test_a_check_keeps_nothing_but_the_staged_tree_and_the_ticket_and_installs_nothing(self):
        shown = service.check_address(self.remote("partner").url)
        self.assertIsNone(registry.entry("partner"))
        self.assertNotIn("partner", registry.read_lock()["catalogs"])
        self.assertEqual(sorted(path.name for path in (self.root / ".lab" / "catalogs").iterdir()), [".stage-partner", "fixture"])
        self.assertTrue((self.root / ".lab" / "cache" / "git" / f".new-{shown['ticket']}.git").is_dir())
        self.assertEqual(registry.units("addon"), ["cluster", "echo", "echo-core", "echo-twin", "future", "gateway", "port-core"],
                         "the units of the stage are no units of the project")

    def test_the_network_steps_hold_no_lock_and_the_fetch_says_how_far_it_is_in_the_state(self):
        remote = self.remote("partner")
        real, seen = bare.fetch, {}

        def fetch(address, branch, store_path, **how):
            seen["lock"] = held_elsewhere(registry.LOCK)
            seen["flock"] = registry.flock(wait=0)
            with seen["flock"]:
                pass
            how["progress"](0.4)
            seen["rows"] = [row for row in service.describe(*project.loaded()) if row.get("kind") == "progress"]
            return real(address, branch, store_path, **how)
        self.patch(bare, "fetch", fetch)
        service.check_address(remote.url)
        self.assertFalse(seen["lock"], "the catalogs lock is not held while the network is asked")
        self.assertEqual(seen["rows"], [{"id": "check", "kind": "progress", "progress": 0.4}])
        self.assertEqual([row for row in service.describe(*project.loaded()) if row.get("kind") == "progress"], [],
                         "and the entry goes with the check")

    def test_the_audience_is_public_when_a_stranger_can_read_it_and_private_when_not_or_when_in_doubt(self):
        remote = self.remote("partner")
        asked = []
        real = gitx.ls_remote

        def ls_remote(address, branch=None, anonymous=False, timeout=20):
            asked.append(anonymous)
            if anonymous:
                raise gitx.fail("no_access", address)
            return real(address, branch, anonymous, timeout)
        self.patch(gitx, "ls_remote", ls_remote)
        self.assertEqual(service.check_address(remote.url)["audience"], "private")
        self.assertEqual(asked, [False, True])

    def test_a_folder_is_read_where_it_is_with_nothing_fetched_and_its_stage_is_a_link(self):
        folder = self.folder_catalog("partner")
        self.patch(bare, "fetch", side_effect=AssertionError("a folder is not fetched"))
        shown = service.check_address(str(folder))
        self.assertEqual((shown["catalog"]["kind"], shown["audience"], shown["address"], shown["url"]),
                         ("folder", "private", registry.display_address(str(folder)), registry.display_address(str(folder))))
        self.assertEqual(shown["rows"]["builds"]["local"], 0)
        self.assertTrue((self.root / ".lab" / "catalogs" / ".stage-partner").is_symlink())
        data = service.TICKETS[shown["ticket"]]
        self.assertIsNone(data["store"])
        self.assertEqual(data["roots"], [])

    def test_the_short_form_is_tried_over_ssh_when_https_is_refused_and_the_address_that_answered_is_kept(self):
        remote = self.remote("partner")
        asked = []

        def ls_remote(address, branch=None, anonymous=False, timeout=20):
            asked.append(address.url)
            if address.url.startswith("https://"):
                raise gitx.fail("not_found", address)
            return {"branch": "main", "head": remote.head()}
        self.patch(gitx, "ls_remote", ls_remote)
        real = bare.fetch
        self.patch(bare, "fetch", lambda address, branch, store_path, **how: real(gitx.parse_address(remote.url), branch,
                                                                                    store_path, **how))
        shown = service.check_address("acme/lab-hub")
        self.assertEqual(shown["url"], "git@github.com:acme/lab-hub.git")
        self.assertEqual(asked[:2], ["https://github.com/acme/lab-hub.git", "git@github.com:acme/lab-hub.git"])
        self.assertEqual(service.TICKETS[shown["ticket"]]["address"].url, "git@github.com:acme/lab-hub.git")

    def test_when_both_forms_are_refused_the_first_refusal_is_the_persons_and_a_long_form_is_not_tried_over_ssh(self):
        asked = []

        def ls_remote(address, branch=None, anonymous=False, timeout=20):
            asked.append(address.url)
            raise gitx.fail("no_access" if address.url.startswith("https") else "ssh_denied", address)
        self.patch(gitx, "ls_remote", ls_remote)
        with self.assertRaises(CatalogError) as raised:
            service.check_address("acme/lab-hub")
        self.assertEqual(lines_of(raised.exception), ("no_access", 400,
                                                      "No access to acme/lab-hub. Run gh auth login in a terminal, then try again."))
        self.assertEqual(len(asked), 2)
        asked.clear()
        with self.assertRaises(CatalogError) as raised:
            service.check_address("https://github.com/acme/lab-hub")
        self.assertEqual(raised.exception.code, "no_access")
        self.assertEqual(asked, ["https://github.com/acme/lab-hub"], "only the short form means GitHub, and may be ssh")

    def test_a_check_that_ends_in_a_refusal_leaves_no_stage_and_no_store(self):
        remote = self.remote("partner", drop=["catalog.yml"])
        with self.assertRaises(CatalogError) as raised:
            service.check_address(remote.url)
        self.assertEqual(raised.exception.code, "not_catalog")
        self.assertEqual(service.TICKETS, {})
        self.assertEqual(sorted(path.name for path in (self.root / ".lab" / "catalogs").iterdir()), ["fixture"])
        self.assertEqual(list((self.root / ".lab" / "cache" / "git").glob(".new-*")) if (self.root / ".lab" / "cache" / "git").is_dir() else [], [])
        self.assertEqual([row for row in service.describe(*project.loaded()) if row.get("kind") == "progress"], [])

    def test_a_second_check_of_the_same_catalog_takes_the_place_of_the_first_and_its_ticket_with_it(self):
        remote = self.remote("partner")
        first = service.check_address(remote.url)["ticket"]
        second = service.check_address(remote.url)["ticket"]
        self.assertNotEqual(first, second)
        self.assertEqual(list(service.TICKETS), [second])

    def test_the_arguments_are_text(self):
        for address, branch in ((None, None), (12, None), ({"a": 1}, None), ("owner/repo", 5)):
            with self.subTest(address=address, branch=branch), self.assertRaises(ValueError):
                service.check_address(address, branch)


class Refusals(Seam):
    """Every refusal of an Add check that is on the way before the catalog is read, with the line of the catalogue of errors."""

    def refused(self, address, branch=None):
        with self.assertRaises(CatalogError) as raised:
            service.check_address(address, branch)
        self.assertEqual(service.TICKETS, {})
        return lines_of(raised.exception)

    BAD = "Use owner/repo, a Git address such as https://github.com/owner/repo, or the path of a folder."

    def test_an_address_the_grammar_refuses_is_bad_address(self):
        self.patch(gitx, "ALLOW_FILE", False)
        for address in ("http://example.com/o/r.git", "git://example.com/o/r.git", "ext::sh -c touch% /tmp/x",
                        "fd::17/o/r", "file:///tmp/repo", "-oProxyCommand=x", "owner/repo extra", "owner/repo\x07",
                        "", "   ", "https://exa mple.com/o/r"):
            with self.subTest(address=address):
                self.assertEqual(self.refused(address), ("bad_address", 400, self.BAD))

    def test_a_credential_in_the_address_is_refused_and_never_repeated(self):
        for address in ("https://user:ghp_secret@github.com/o/r.git", "https://token@github.com/o/r.git",
                        "user:ghp_secret@github.com:o/r.git"):
            with self.subTest(address=address):
                code, status, line = self.refused(address)
                self.assertEqual((code, status, line), ("bad_address_secret", 400,
                                 "Remove the user name and token from the address. Git uses the sign-in of this machine."))
                self.assertNotIn("secret", line)

    def test_a_folder_that_is_not_there_has_no_catalog_or_is_out_of_reach_of_docker(self):
        self.assertEqual(self.refused(str(self.home / "gone")), ("folder_missing", 400, f"No folder at {self.home / 'gone'}."))
        (self.home / "empty").mkdir()
        self.assertEqual(self.refused(str(self.home / "empty")), ("folder_not_catalog", 400,
                                                                   f"{self.home / 'empty'} has no catalog.yml, so it is not a catalog."))
        outside = Path(self.root) / "elsewhere"
        outside.mkdir()
        (outside / "catalog.yml").write_text("x-gravitee-catalog:\n  id: x\n", encoding="utf-8")
        self.assertEqual(self.refused(str(outside)), ("folder_not_shared", 400,
                                                      "Docker cannot share this folder. Move it under your home folder."))

    def test_a_folder_that_cannot_be_linked_is_symlink_unsupported(self):
        folder = self.folder_catalog("partner")
        self.patch(os, "symlink", side_effect=OSError("not allowed"))
        self.assertEqual(self.refused(str(folder)), ("symlink_unsupported", 400,
                                                     "The Lab cannot link folders on this system. Use WSL 2."))
        self.assertEqual(sorted(path.name for path in (self.root / ".lab" / "catalogs").iterdir()), ["fixture"])

    def test_no_git_and_a_git_that_is_too_old(self):
        self.patch(gitx, "executable", return_value=None)
        self.assertEqual(self.refused("acme/lab-hub"), ("git_missing", 400,
                                                        "Git is not installed. Install it, then add the catalog again."))
        self.patch(gitx, "executable", return_value="/usr/bin/git")
        self.patch(gitx, "version", return_value=(2, 20, 1))
        self.assertEqual(self.refused("acme/lab-hub"), ("git_old", 400,
                                                        "Git 2.20.1 is too old. Install Git 2.25 or later, then add the catalog again."))

    def test_what_git_refuses_is_the_line_of_section_11_with_the_host_and_the_sign_in_of_that_host(self):
        cases = [
            ("no_access", 400, "No access to acme/lab-hub. Run gh auth login in a terminal, then try again."),
            ("not_found", 400, "acme/lab-hub was not found. Check the address, or sign in with Git if it is private."),
            ("ssh_denied", 400, "Permission denied for github.com. Add your SSH key to github.com, then try again."),
            ("host_key", 400, "github.com is not a known SSH host. Connect to it once from a terminal, then try again."),
            ("unreachable", 502, "Cannot reach github.com. Check your connection, then try again."),
            ("timeout", 502, "github.com did not answer in time. Try again."),
            ("certificate", 502, "The certificate of github.com was refused. A company proxy may be in the way."),
            ("git_failed", 502, "Git could not read acme/lab-hub. Check the address and your connection, then try again."),
        ]
        for code, status, line in cases:
            with self.subTest(code=code):
                self.patch(gitx, "ls_remote", side_effect=lambda address, *a, _code=code, **k: (_ for _ in ()).throw(
                    gitx.fail(_code, address)))
                self.assertEqual(self.refused("https://github.com/acme/lab-hub"), (code, status, line))
        # ... and the sign-in elsewhere than GitHub is the general words
        self.patch(gitx, "ls_remote", side_effect=lambda address, *a, **k: (_ for _ in ()).throw(gitx.fail("no_access", address)))
        self.assertEqual(self.refused("https://git.example.invalid/team/hub.git"), (
            "no_access", 400, "No access to team/hub. Sign in with Git on this machine, then try again."))

    def test_a_branch_the_repository_lacks_is_branch_missing(self):
        remote = self.remote("partner")
        code, status, line = self.refused(remote.url, "nope")
        self.assertEqual((code, status), ("branch_missing", 400))
        self.assertTrue(line.endswith("has no branch nope. Check the name, or leave it empty for the default."), line)

    def test_a_branch_that_is_no_name_is_refused_before_anything_runs(self):
        self.patch(gitx, "run", side_effect=AssertionError("git was run"))
        code, status, line = self.refused("https://github.com/acme/lab-hub", "a b;rm")
        self.assertEqual((code, status), ("branch_missing", 400))

    def test_a_repository_over_the_limit_is_too_large_and_nothing_stays(self):
        remote = self.remote("partner")
        self.patch(bare, "STORE_LIMIT", 100)
        self.assertEqual(self.refused(remote.url), ("too_large", 400,
                                                    "This catalog is larger than the Lab accepts. Ask its owners to make it smaller."))
        self.assertEqual(list((self.root / ".lab" / "cache" / "git").glob(".new-*")), [])

    def test_a_repository_git_finds_damaged_is_fsck(self):
        self.patch(bare, "fetch", side_effect=lambda address, *a, **k: (_ for _ in ()).throw(gitx.fail("fsck", address)))
        remote = self.remote("partner")
        self.assertEqual(self.refused(remote.url), ("fsck", 400, "This repository is damaged. Ask its owners to check it."))

    def test_a_catalog_the_person_has_is_already_added_in_any_spelling_and_before_anything_is_fetched(self):
        done = self.installed("partner", url="https://github.com/acme/lab-hub.git")
        self.patch(gitx, "ls_remote", side_effect=AssertionError("the network was asked"))
        for address in ("acme/lab-hub", "https://github.com/acme/lab-hub", "git@github.com:acme/lab-hub.git",
                        "https://github.com/acme/lab-hub.git"):
            with self.subTest(address=address):
                self.assertEqual(self.refused(address), ("already_added", 409, "Already added as Partner fixture."))
        self.assertTrue(done)

    def test_a_folder_the_person_linked_is_already_added_too(self):
        folder = self.folder_catalog("partner")
        service.add(service.check_address(str(folder))["ticket"])
        self.assertEqual(self.refused(str(folder)), ("already_added", 409, "Already added as Partner fixture."))


class Reading(Real):
    """The catalog.yml of a staged tree, read and checked, and the judgement of it, with Compose."""

    def refused(self, remote_or_folder, **how):
        with self.assertRaises(CatalogError) as raised:
            service.check_address(remote_or_folder, **how)
        self.assertEqual(service.TICKETS, {})
        self.assertEqual(sorted(path.name for path in (self.root / ".lab" / "catalogs").iterdir()), ["fixture"],
                         "a refusal leaves nothing staged")
        return lines_of(raised.exception)

    def meta(self, **fields):
        base = {"schema": "1", "id": "partner", "name": "Partner fixture", "description": "Units", "version": "2.0.0", "lab": "1"}
        base.update({key: str(value) for key, value in fields.items()})
        return "x-gravitee-catalog:\n" + "".join(f"  {key}: {value}\n" for key, value in base.items())

    def test_a_repository_without_catalog_yml_is_not_a_catalog(self):
        code, status, line = self.refused(self.remote("partner", drop=["catalog.yml"]).url)
        self.assertEqual((code, status), ("not_catalog", 400))
        self.assertTrue(line.endswith("has no catalog.yml, so it is not a catalog."), line)

    def test_a_catalog_yml_that_breaks_the_format_is_catalog_invalid_with_the_first_problem(self):
        remote = self.remote("partner", files={"catalog.yml": self.meta(id="Not_A_Namespace")})
        self.assertEqual(self.refused(remote.url), ("catalog_invalid", 400,
                                                    "catalog.yml: id must be 2 to 24 lowercase letters, digits and dashes."))

    def test_a_format_newer_than_this_lab_reads_is_schema_new(self):
        remote = self.remote("partner", files={"catalog.yml": self.meta(schema=2)})
        self.assertEqual(self.refused(remote.url), ("schema_new", 400,
                                                    "This catalog uses a newer format. Run make update, then add it again."))

    def test_a_reserved_name_is_refused_unless_the_listed_address_serves_it(self):
        remote = self.remote("partner", files={"catalog.yml": self.meta(id="lab")})
        self.assertEqual(self.refused(remote.url), ("name_reserved", 400,
                                                    "lab is a reserved name. Ask the owner of this catalog to choose another."))

    def test_a_name_another_catalog_uses_is_name_taken_with_where_that_one_is(self):
        remote = self.remote("partner", files={"catalog.yml": self.meta(id="fixture")})
        code, status, line = self.refused(remote.url)
        self.assertEqual((code, status), ("name_taken", 409))
        self.assertTrue(line.startswith("Another catalog already uses the name fixture ("), line)
        self.assertTrue(line.endswith("). Remove it first, or ask the owner of this one to rename it."), line)

    def test_a_catalog_written_for_a_lab_that_does_not_exist_yet_is_lab_new_and_one_for_an_older_lab_lab_old(self):
        new = self.remote("partner", lab=99)
        self.assertEqual(self.refused(new.url), ("no_readable_version", 400,
                                                 "Every version of this catalog needs a newer Lab. Run make update, then add it again."))
        old = fixtures.remote(self, "lab-old")
        self.assertEqual(self.refused(old.url), (
            "lab_old", 400, "Written for an older Lab (level 0, this Lab reads 1 to 1). Ask its owners to update it."))

    def test_a_level_the_quick_reading_cannot_be_sure_of_is_refused_on_the_real_one_with_the_line_of_lab_new(self):
        # the reading of the versions in the history is a restricted one (an anchor is beyond it); the staged file is read by
        # Compose, and what it says is the answer
        remote = self.remote("partner", files={"catalog.yml": self.meta().replace("  lab: 1\n", "  lab: &level 99\n")})
        self.assertEqual(self.refused(remote.url), (
            "lab_new", 400, "Needs Lab level 99, this Lab reads 1 to 1. Run make update, then add it again."))

    def test_the_address_the_engine_lists_must_serve_the_catalog_it_lists(self):
        remote = self.remote("partner", files={"catalog.yml": self.meta(id="other-hub")})
        self.list_at("partner", remote)
        code, status, line = self.refused(remote.url)
        self.assertEqual((code, status), ("wrong_catalog", 409))
        self.assertTrue(line.endswith(" is not the catalog this Lab expects. Run make update, then try again."), line)

    def test_a_tip_that_needs_a_newer_lab_installs_the_newest_version_this_lab_reads_and_says_so(self):
        remote = self.remote("partner")
        older = remote.head()
        remote.commit({"catalog.yml": self.meta(version="3.0.0", lab=99)}, "Needs a newer Lab")
        shown = service.check_address(remote.url)
        self.assertEqual((shown["catalog"]["version"], shown["newer"]), ("2.0.0", True))
        self.assertEqual(service.TICKETS[shown["ticket"]]["staged"].commit, older)


class Judging(Real):
    """The preview is the policy's own judgement of the staged tree, put through the project's code as if it were installed:
    what it says agrees with the oracle of the fixtures (tests/data/expected.json) and with what the project says after the
    catalog is added."""

    def listed_fixture(self):
        """The fixture catalog as the engine lists it, served by a repository, with the partner catalog added beside it:
        the default catalog is not installed yet, and its Add is checked."""
        install.remove("fixture", keep_settings=False)
        fixtures.install(self, "partner", kind="folder")
        remote = fixtures.remote(self, "fixture")
        self.list_at("fixture", remote)
        return remote

    def test_what_the_preview_says_of_each_unit_is_what_the_oracle_expects(self):
        remote = self.listed_fixture()
        shown = service.check_address(remote.url)
        data = service.TICKETS[shown["ticket"]]
        judged, verdicts, mine = service._review("fixture", data["meta"], "git", "public", True, "listed", 0)
        expected = fixtures.expected()
        seen = {}
        for kind, uid in mine:
            key = f"{kind}:{uid}"
            seen[key] = (judged[kind][uid]["blocked"] or {}).get("reasons") or []
            with self.subTest(unit=key):
                self.assertEqual(verdicts[(kind, uid)]["surface"], expected[key]["surface"])
                self.assertEqual(seen[key], expected[key]["reasons"])
        self.assertEqual(sorted(seen), sorted(key for key in expected if "@partner/" not in key))
        self.assertEqual({key for key, reasons in seen.items() if reasons}, {"addon:future", "setup:pair"})

    def test_the_rows_of_the_preview_are_the_sums_of_what_the_units_that_can_run_do(self):
        remote = self.listed_fixture()
        shown = service.check_address(remote.url)
        self.assertEqual(shown["catalog"]["id"], "fixture")
        self.assertEqual(shown["counts"], {"playground": 2, "setup": 1, "addon": 7})
        self.assertEqual([(one["id"], one["reasons"]) for one in shown["blocked"]], [
            ("future", ["Needs Lab level 99, this Lab reads 1 to 1"]), ("pair", ["Depends on private content"])])
        rows = shown["rows"]
        expected = fixtures.expected()
        runnable = [expected[key]["surface"] for key in expected if "@partner/" not in key
                    and expected[key]["verdict"] == "ok"]
        self.assertEqual(rows["ports"], sorted({port for one in runnable for port in one["ports"]}))
        self.assertEqual(rows["builds"], {"local": sum(len(one["builds"]) for one in runnable), "remote": []})
        self.assertEqual(rows["reads"], sorted({name for one in runnable for name in one["reads"]}))
        self.assertEqual(rows["uses_machine"], [], "a listed catalog has the allowance by being listed")
        self.assertEqual(rows["needs"], [], "partner is added")

    def test_a_catalog_that_is_not_listed_shows_what_it_asks_of_this_machine_and_the_items_it_cannot_run_anyway(self):
        self.patch(trust, "ALLOWANCE", True)   # milestone M2: the allowance of a person is honored
        install.remove("fixture", keep_settings=False)
        fixtures.install(self, "partner", kind="folder")
        fixtures.default(self, "partner")
        project.forget()
        shown = service.check_address(fixtures.remote(self, "fixture").url)
        self.assertEqual(shown["catalog"]["id"], "fixture")
        uses = shown["rows"]["uses_machine"]
        self.assertEqual([(one["id"], one["service"], one["what"]) for one in uses],
                         [("@fixture/cluster", "fixture-cluster", ["runs privileged"])])
        self.assertTrue(uses[0]["why"].startswith("Runs a service manager"), "the reason is the author's, shown")
        self.assertEqual([(one["id"], one["reasons"]) for one in shown["blocked"]], [
            ("@fixture/future", ["Needs Lab level 99, this Lab reads 1 to 1"]),
            ("@fixture/pair", ["Depends on private content"])], "partner is added, private, and this one is public")
        self.assertNotIn("@fixture/cluster", [one["id"] for one in shown["blocked"]],
                         "judged as if allowed: the page adds these itself while they are kept blocked")

    def test_a_catalog_that_includes_one_that_is_not_added_lists_it_as_needed_and_is_not_blocked_for_it(self):
        install.remove("fixture", keep_settings=False)
        fixtures.default(self, "partner")
        project.forget()
        shown = service.check_address(fixtures.remote(self, "fixture").url)   # nothing is installed: the core is read for it
        self.assertEqual(shown["rows"]["needs"], [
            {"id": "partner", "name": "Partner fixture", "url": "https://example.invalid/partner.git", "min": "1.0.0"}])
        self.assertNotIn("@fixture/pair", [one["id"] for one in shown["blocked"]], "it is judged when the catalog arrives")

    def test_a_unit_that_takes_the_name_of_an_installed_one_is_blocked_in_the_preview_and_the_installed_one_is_not(self):
        clash = ("x-gravitee: {kind: addon, name: Clash, category: Other, description: Takes a name}\n"
                 "services:\n  fixture-echo:\n    image: busybox:1.36\n    command: [sleep, '1']\n    networks: [internal]\n")
        shown = service.check_address(self.remote("partner", files={"addons/clash/compose.yml": clash}).url)
        self.assertEqual([(one["id"], one["reasons"]) for one in shown["blocked"]], [("@partner/clash", ["Same name as Echo"])])
        self.assertEqual(shown["counts"]["addon"], 2)

    def test_a_unit_that_reads_files_outside_its_folder_is_blocked_and_never_given_to_compose(self):
        ran = []
        real = compose.run
        self.patch(compose, "run", lambda cmd, *a, **k: ran.append(list(cmd)) or real(cmd, *a, **k))
        shown = service.check_address(fixtures.remote(self, "hostile").url)
        blocked = {one["id"]: one["reasons"] for one in shown["blocked"]}
        self.assertIn("@hostile/t19-env-file-dev-zero", blocked)
        self.assertTrue(all(blocked.values()))
        self.assertNotIn("@hostile/p-clean-addon", blocked)
        self.assertEqual([cmd for cmd in ran if any("t19-env-file-dev-zero" in part for part in cmd)], [])

    def test_without_the_allowance_milestone_an_item_that_uses_the_machine_is_simply_blocked(self):
        self.patch(trust, "ALLOWANCE", False)   # the policy before the milestone ships; the test does not depend on the flag
        install.remove("fixture", keep_settings=False)
        fixtures.install(self, "partner", kind="folder")
        fixtures.default(self, "partner")
        project.forget()
        shown = service.check_address(fixtures.remote(self, "fixture").url)
        self.assertEqual(shown["rows"]["uses_machine"], [], "nothing to allow: the page does not offer the choice")
        self.assertIn("@fixture/cluster", [one["id"] for one in shown["blocked"]])

    def test_the_preview_and_the_project_after_the_add_say_the_same_of_what_cannot_run(self):
        self.patch(trust, "ALLOWANCE", True)
        shown = service.check_address(fixtures.remote(self, "hostile").url)
        service.add(shown["ticket"], host=True)   # allowed: the preview is judged as if it were
        built = project.loaded()[0]
        after = {uid: unit["blocked"]["reasons"] for kind in ("addons", "setups", "playgrounds")
                 for uid, unit in built[kind].items() if unit["catalog"] == "hostile" and unit["blocked"]}
        self.assertEqual({one["id"]: one["reasons"] for one in shown["blocked"]}, after)
        self.assertEqual(shown["counts"], self.row("hostile")["counts"])

    def test_a_preview_replaced_by_a_staged_tree_changes_nothing_the_project_reads(self):
        before = project.loaded()
        shown = service.check_address(self.remote("partner").url)
        self.assertTrue(shown)
        after = project.loaded()
        self.assertEqual(sorted(after[0]["addons"]), sorted(before[0]["addons"]))
        self.assertEqual(after[1]["catalogs"].keys(), before[1]["catalogs"].keys(), "the inputs it was given are as they were")


@contextlib.contextmanager
def held_by_another():
    """Another thread holds the catalogs lock (as another change, or another Lab, would) until the block ends."""
    taken, free = threading.Event(), threading.Event()

    def hold():
        with registry.flock():
            taken.set()
            free.wait(20)
    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    taken.wait(5)
    try:
        with mock.patch.object(service, "PATIENCE", 0.05):
            yield
    finally:
        free.set()
        thread.join(5)


class Adding(Seam):
    """The Add the check showed, and what it leaves."""

    def test_the_add_installs_the_catalog_and_answers_its_row(self):
        shown = service.check_address(self.remote("partner").url)
        answer = service.add(shown["ticket"])
        self.assertEqual(list(answer), ["catalog"])
        row = answer["catalog"]
        self.assertEqual((row["id"], row["kind"], row["name"], row["version"], row["commit"]),
                         ("partner", "git", "Partner fixture", "2.0.0", self.remotes["partner"].head()[:7]))
        self.assertEqual(row["state"]["key"], "uptodate")
        self.assertEqual((registry.entry("partner")["url"], registry.entry("partner")["branch"], registry.entry("partner")["auto"]),
                         (self.remotes["partner"].url, "main", True))
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["tip"], held["audience"], held["pending"], held["error"]),
                         (self.remotes["partner"].head(), self.remotes["partner"].head(), "public", None, None))
        self.assertEqual(registry.units_of("partner")["addon"], ["ledger"])
        self.assertEqual(sorted(path.name for path in (self.root / ".lab" / "catalogs").iterdir()), ["fixture", "partner"])

    def test_the_two_settings_that_keep_compose_from_fetching_an_include_come_with_the_first_catalog(self):
        env = self.root / ".env"
        env.write_text("A=1\n", encoding="utf-8")
        service.add(service.check_address(self.remote("partner").url)["ticket"])
        names = envfiles.read(".env")
        for name, value in compose.NO_REMOTE.items():
            self.assertEqual(names[name], value)
        self.assertEqual(names["A"], "1")

    def test_the_ticket_is_spent_by_the_add(self):
        ticket = service.check_address(self.remote("partner").url)["ticket"]
        service.add(ticket)
        self.assertNotIn(ticket, service.TICKETS)
        with self.assertRaises(CatalogError) as raised:
            service.add(ticket)
        self.assertEqual(lines_of(raised.exception), ("ticket_expired", 409, "This took too long. Start again."))

    def test_a_ticket_that_is_unknown_not_text_or_for_another_change_is_expired(self):
        for ticket in (None, 5, "", "0123456789abcdef", ["x"]):
            with self.subTest(ticket=ticket), self.assertRaises(CatalogError) as raised:
                service.add(ticket)
            self.assertEqual(lines_of(raised.exception), ("ticket_expired", 409, "This took too long. Start again."))
        made = service.check_address(self.remote("partner").url)["ticket"]
        service.TICKETS[made]["kind"] = "update"
        with self.assertRaises(CatalogError) as raised:
            service.add(made)
        self.assertEqual(raised.exception.code, "ticket_expired")

    def test_a_ticket_lives_ten_minutes_and_a_restart_forgets_it(self):
        clock = [1000.0]
        self.patch(service, "_now", lambda: clock[0])
        ticket = service.check_address(self.remote("partner").url)["ticket"]
        store_path = registry.new_store_dir(ticket)
        clock[0] += 599
        self.assertIn(ticket, service.TICKETS)
        service.check_address(self.remote("partner").url)   # (a check sweeps what is old; the ticket is replaced by it)
        clock[0] += 1000
        with self.assertRaises(CatalogError) as raised:
            service.add(ticket)
        self.assertEqual(raised.exception.code, "ticket_expired")
        self.assertFalse(store_path.exists(), "what an expired ticket held on disk goes with it")
        self.assertNotIn(ticket, service.TICKETS)

    def test_the_staged_tree_and_the_store_of_a_ticket_that_expires_are_removed_when_the_next_one_is_made(self):
        clock = [1000.0]
        self.patch(service, "_now", lambda: clock[0])
        first = service.check_address(self.remote("partner").url)["ticket"]
        clock[0] += service.TTL + 1
        second = service.check_address(self.remote("lab-new", lab=1).url)["ticket"]
        self.assertEqual(list(service.TICKETS), [second])
        self.assertFalse((self.root / ".lab" / "catalogs" / ".stage-partner").exists())
        self.assertFalse(registry.new_store_dir(first).exists())
        self.assertTrue(registry.new_store_dir(second).exists())

    def test_a_second_change_at_the_same_time_is_busy_and_the_ticket_is_still_good_afterwards(self):
        ticket = service.check_address(self.remote("partner").url)["ticket"]
        with held_by_another():
            started = time.monotonic()
            with self.assertRaises(CatalogError) as raised:
                service.add(ticket)
            self.assertLess(time.monotonic() - started, 5, "it does not wait for the first change")
        self.assertEqual(lines_of(raised.exception), ("busy", 409, "Another catalog change is running. Try again in a moment."))
        self.assertIn(ticket, service.TICKETS)
        self.assertEqual(service.add(ticket)["catalog"]["id"], "partner")

    def test_the_allowance_the_person_chose_with_the_dialog_is_stored_for_a_catalog_that_is_not_listed(self):
        service.add(service.check_address(self.remote("partner").url)["ticket"], host=True)
        self.assertIs(registry.entry("partner").get("host"), True)
        self.assertEqual(self.row("partner")["host"], "allowed")
        service.add(service.check_address(self.remote("lab-new", lab=1).url)["ticket"])
        self.assertNotIn("host", registry.entry("lab-new"))

    def test_a_listed_catalog_has_no_stored_allowance_it_is_implied(self):
        remote = self.remote("partner")
        self.list_at("partner", remote)
        service.add(service.check_address(remote.url)["ticket"], host=True)
        self.assertNotIn("host", registry.entry("partner"))
        self.assertEqual(self.row("partner")["host"], "listed")

    def test_a_catalog_added_between_the_check_and_the_add_makes_the_add_a_refusal_not_a_second_copy(self):
        remote = self.remote("partner")
        ticket = service.check_address(remote.url)["ticket"]
        other = service.check_address(remote.url)["ticket"]
        self.assertNotIn(ticket, service.TICKETS, "replaced")
        self.assertTrue((self.root / ".lab" / "catalogs" / ".stage-partner").is_dir(), "its tree is the one that stands")
        self.assertFalse(registry.new_store_dir(ticket).exists(), "and the store of the one it replaced goes")
        service.add(other)
        with self.assertRaises(CatalogError) as raised:
            service.add(ticket)
        self.assertEqual(raised.exception.code, "ticket_expired")
        with self.assertRaises(CatalogError) as raised:
            service.check_address(remote.url)
        self.assertEqual(raised.exception.code, "already_added")

    def test_a_stage_that_was_swept_away_is_ticket_expired_and_nothing_is_added(self):
        ticket = service.check_address(self.remote("partner").url)["ticket"]
        shutil.rmtree(self.root / ".lab" / "catalogs" / ".stage-partner")
        with self.assertRaises(CatalogError) as raised:
            service.add(ticket)
        self.assertEqual(raised.exception.code, "ticket_expired")
        self.assertIsNone(registry.entry("partner"))

    def test_a_folder_catalog_is_linked_and_the_authors_folder_is_not_touched(self):
        folder = self.folder_catalog("partner")
        before = sorted(str(path.relative_to(folder)) for path in folder.rglob("*"))
        row = service.add(service.check_address(str(folder))["ticket"])["catalog"]
        self.assertEqual((row["kind"], row["writable"], row["commit"], row["state"]["key"]), ("folder", True, None, "linked"))
        self.assertTrue((self.root / ".lab" / "catalogs" / "partner").is_symlink())
        self.assertEqual(sorted(str(path.relative_to(folder)) for path in folder.rglob("*")), before)
        self.assertEqual(registry.entry("partner")["path"], str(folder))

    def test_the_items_whose_settings_were_kept_for_a_catalog_are_said_in_the_preview_and_taken_over_by_the_add(self):
        install.remove("fixture", keep_settings=False)
        remote = fixtures.remote(self, "fixture")
        self.list_at("fixture", remote)
        held = self.root / ".lab" / "held" / "addons" / "echo"
        held.mkdir(parents=True)
        (held / ".env").write_text("FIXTURE_ECHO_GREETING=hi\n", encoding="utf-8")
        shown = service.check_address(remote.url)
        self.assertEqual(shown["rows"]["found"], [{"id": "echo", "name": "Echo"}])
        service.add(shown["ticket"])
        self.assertEqual((self.root / ".lab" / "catalogs" / "fixture" / "addons" / "echo" / ".env").read_text(encoding="utf-8"),
                         "FIXTURE_ECHO_GREETING=hi\n")


class Hostile(Seam):
    """What an author wrote goes through a person's eyes and into the state the page polls: it is cleaned and cut, and a
    stranger cannot fill the disk with checks."""

    def test_the_name_of_a_unit_in_a_line_has_no_control_character_and_is_cut(self):
        found = {"addons": {"@partner/ledger": {"name": "Led\x1b[31mger\nTurn it \u202eoff\ufeff" + "x" * 300, "catalog": "partner"}}}
        text = service._unit_name(found, "partner", "addon:ledger")
        self.assertLessEqual(len(text), service.NAME_CUT)
        for bad in ("\x1b", "\n", "\u202e", "\ufeff"):
            self.assertNotIn(bad, text)
        self.assertEqual(service._unit_name(found, "partner", "addon:other"), "other", "an unknown unit is its folder")
        line = service._turn_off_line(found, "partner", {"addon:ledger"})
        self.assertTrue(line.startswith("Turn off Led") and line.endswith(" first") and "\n" not in line)

    def test_the_names_in_a_plan_a_removal_and_a_blocked_list_are_cleaned_too(self):
        found = {"addons": {"@partner/a": {"name": "A\nB" + "z" * 200, "includes": [], "needs": [], "catalog": "partner"}},
                 "setups": {}, "playgrounds": {"local/p": {"name": "P\x07" + "q" * 200, "catalog": "local", "includes": ["@partner/a"],
                                                           "needs": [], "addons": ["@partner/a"], "members": [], "services": [], "apps": []}}}
        broken = service._items_that_include(found, {("addon", "@partner/a")})
        self.assertEqual(len(broken), 1)
        for text in (broken[0]["name"], broken[0]["needs"]):
            self.assertLessEqual(len(text), service.NAME_CUT)
            self.assertNotIn("\x07", text)
            self.assertNotIn("\n", text)
        judged = {"addon": {"@partner/a": {"name": "A\nB" * 100, "blocked": {"reasons": ["r"]}}}}
        blocked = service._blocked_of(judged, [("addon", "@partner/a")])
        self.assertLessEqual(len(blocked[0]["name"]), service.NAME_CUT)
        self.assertNotIn("\n", blocked[0]["name"])

    def test_only_a_few_checks_are_kept_at_once_the_oldest_goes_with_its_tree_and_its_store(self):
        clock = [100.0]
        self.patch(service, "_now", lambda: clock[0])
        self.patch(service, "MAX_TICKETS", 2)
        tickets = []
        for ns in ("partner", "hostile", "lab-new"):
            clock[0] += 1
            tickets.append(service.check_address(self.remote(ns, lab=1).url)["ticket"])
        self.assertEqual(sorted(service.TICKETS), sorted(tickets[-2:]))
        self.assertFalse(registry.new_store_dir(tickets[0]).exists())
        self.assertFalse((self.root / ".lab" / "catalogs" / ".stage-partner").exists())
        self.assertTrue(registry.new_store_dir(tickets[-1]).exists())

    def test_a_catalog_is_added_even_when_the_env_file_cannot_be_written(self):
        ticket = service.check_address(self.remote("partner").url)["ticket"]
        self.patch(service.layers, "prepare_catalogs", side_effect=OSError("read-only file system"))
        self.assertEqual(service.add(ticket)["catalog"]["id"], "partner")


class Listed(Seam):
    """A catalog the engine lists is added with no check and no dialog; the one background thread downloads it."""

    def setUp(self):
        super().setUp()
        from lab_ui.catalogs import firstrun
        self.woken = []
        self.patch(firstrun, "reconcile", side_effect=lambda: self.woken.append("reconcile"))
        self.patch(firstrun, "wake", side_effect=lambda: self.woken.append("wake"))
        self.remote("partner")
        self.list_at("partner", self.remotes["partner"])

    def test_the_entry_is_written_with_the_tracked_address_and_the_row_says_it_is_downloading(self):
        answer = service.add_listed("partner")
        entry = registry.entry("partner")
        self.assertEqual((entry["kind"], entry["url"], entry["branch"], entry["auto"]),
                         ("git", self.remotes["partner"].url, "main", True))
        self.assertRegex(entry["added"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        row = answer["catalog"]
        self.assertEqual((row["id"], row["listed"], row["host"], row["state"]["key"], row["commit"]),
                         ("partner", True, "listed", "downloading", None))
        self.assertEqual(self.woken, ["reconcile", "wake"], "the worker is marked and woken, and the answer does not wait")

    def test_an_id_the_file_does_not_list_is_not_listed(self):
        for value in ("lab-new", "", None, 5, ["partner"]):
            with self.subTest(value=value), self.assertRaises(CatalogError) as raised:
                service.add_listed(value)
            self.assertEqual((raised.exception.code, raised.exception.status), ("not_listed", 400))
        with self.assertRaises(CatalogError) as raised:
            service.add_listed("lab-new")
        self.assertEqual(raised.exception.message, "lab-new is not a catalog this Lab lists.")
        self.assertIsNone(registry.entry("lab-new"))

    def test_a_catalog_that_has_a_copy_is_already_added_and_one_without_is_tried_again(self):
        service.add_listed("partner")
        registry.set_lock("partner", error="unreachable")
        answer = service.add_listed("partner")
        self.assertEqual(answer["catalog"]["state"]["key"], "downloading", "a new attempt shows downloading")
        self.assertEqual(len(registry.entries()) - 1, 1 if registry.entry("fixture") else 0)
        registry.set_lock("partner", pin="a" * 40, name="Partner fixture")
        with self.assertRaises(CatalogError) as raised:
            service.add_listed("partner")
        self.assertEqual(lines_of(raised.exception), ("already_added", 409, "Already added as Partner fixture."))

    def test_a_removed_listed_catalog_comes_back_as_one_row_that_adds_it_again(self):
        service.add_listed("partner")
        install.remove("partner", keep_settings=True)
        row = next(one for one in service.describe(*project.loaded()) if one.get("id") == "partner")
        self.assertEqual((row["kind"], row["listed"]), ("suggested", True))
        self.assertEqual(service.add_listed("partner")["catalog"]["kind"], "git")

    def test_the_allowance_is_not_written_and_a_busy_lock_is_busy(self):
        with held_by_another(), self.assertRaises(CatalogError) as raised:
            service.add_listed("partner")
        self.assertEqual(raised.exception.code, "busy")
        self.assertIsNone(registry.entry("partner"))


class Creating(Seam):
    """A folder catalog made from the template, under the home folder, and linked."""

    def template(self):
        """The real template renders; what it was asked for is recorded: (folder, id, name)."""
        made = []
        real = standard.create

        def create(dest, params):
            made.append((Path(dest), params["id"], params["name"]))
            return real(dest, params)
        patcher = mock.patch.object(standard, "create", create)
        patcher.start()
        self.addCleanup(patcher.stop)
        return made

    def test_the_folder_is_made_under_the_home_folder_and_linked_and_the_answer_is_its_row(self):
        made = self.template()
        answer = service.create_folder("My Demo Catalog")
        folder = self.home / "gravitee-lab-catalogs" / "my-demo-catalog"
        self.assertEqual(made, [(folder, "my-demo-catalog", "My Demo Catalog")])
        row = answer["catalog"]
        self.assertEqual((row["id"], row["kind"], row["writable"], row["name"], row["state"]["key"]),
                         ("my-demo-catalog", "folder", True, "My Demo Catalog", "linked"))
        self.assertTrue((self.root / ".lab" / "catalogs" / "my-demo-catalog").is_symlink())
        self.assertEqual(registry.entry("my-demo-catalog")["path"], str(folder))
        self.assertFalse((folder / ".git").exists(), "the Lab does not run git init")
        self.assertEqual(manifest.read(str(folder))["id"] if hasattr(manifest, "read") else "my-demo-catalog", "my-demo-catalog")

    def test_a_folder_that_exists_is_folder_exists_and_nothing_is_made(self):
        made = self.template()
        (self.home / "gravitee-lab-catalogs" / "taken").mkdir(parents=True)
        with self.assertRaises(CatalogError) as raised:
            service.create_folder("Taken")
        self.assertEqual(lines_of(raised.exception), ("folder_exists", 409,
                                                      "There is already a catalog folder named taken. Choose another name."))
        self.assertEqual(made, [])
        self.assertIsNone(registry.entry("taken"))

    def test_a_clash_with_a_catalog_or_a_reserved_word_takes_a_number(self):
        self.template()
        self.assertEqual(service.create_folder("Partner")["catalog"]["id"], "partner")
        self.assertEqual(service.create_folder("Partner")["catalog"]["id"], "partner-2")
        self.assertEqual(service.create_folder("Gravitee")["catalog"]["id"], "gravitee-2")
        self.assertEqual(service.create_folder("Local")["catalog"]["id"], "local-2")

    def test_a_folder_that_could_not_be_linked_is_not_left_behind_to_answer_folder_exists_for_ever(self):
        self.template()
        folder = self.home / "gravitee-lab-catalogs" / "my-demo"
        self.patch(service, "check_address", side_effect=CatalogError("catalog_invalid", "catalog.yml: name cannot be My demo.", 400))
        with self.assertRaises(CatalogError) as raised:
            service.create_folder("My demo")
        self.assertEqual(raised.exception.code, "catalog_invalid")
        self.assertFalse(folder.exists())
        self.assertIsNone(registry.entry("my-demo"))

    def test_a_template_that_fails_halfway_leaves_no_folder_and_the_same_name_works_after(self):
        with mock.patch.object(standard, "create", side_effect=OSError(28, "No space left on device")), self.assertRaises(OSError):
            service.create_folder("My demo")
        self.assertFalse((self.home / "gravitee-lab-catalogs" / "my-demo").exists())
        self.template()
        self.assertEqual(service.create_folder("My demo")["catalog"]["id"], "my-demo")

    def test_a_folder_that_was_linked_stays_whatever_comes_after(self):
        self.template()
        self.patch(layers, "prepare_catalogs", side_effect=RuntimeError("after the link"))
        with self.assertRaises(RuntimeError):
            service.create_folder("My demo")
        self.assertEqual(registry.entry("my-demo")["kind"], "folder")
        self.assertTrue((self.home / "gravitee-lab-catalogs" / "my-demo" / "catalog.yml").is_file(), "the author's folder is kept")

    def test_a_name_with_no_usable_slug_is_catalog_invalid(self):
        made = self.template()
        for name in ("", "   ", "!!!", "é", "a", "9lives"):
            with self.subTest(name=name), self.assertRaises(CatalogError) as raised:
                service.create_folder(name)
            self.assertEqual(lines_of(raised.exception), ("catalog_invalid", 400,
                                                          "catalog.yml: id must be 2 to 24 lowercase letters, digits and dashes."))
        self.assertEqual(made, [])
        with self.assertRaises(ValueError):
            service.create_folder(None)

    def test_a_long_name_makes_an_id_the_grammar_takes(self):
        self.template()
        row = service.create_folder("A catalog with a name long enough")["catalog"]
        self.assertRegex(row["id"], paths.NS)
        self.assertLessEqual(len(row["id"]), 24)

    def test_a_name_the_template_refuses_is_said_and_nothing_is_made(self):
        with self.assertRaises(CatalogError) as raised:
            service.create_folder("A catalog with a name that is far too long to be an id of a catalog")
        self.assertEqual(lines_of(raised.exception), ("catalog_invalid", 400, "The name is at most 40 characters."))
        self.assertEqual(list((self.home / "gravitee-lab-catalogs").iterdir()), [])

    def test_two_requests_for_the_same_name_make_two_catalogs_and_none_is_broken_by_the_other(self):
        made, failed = [], []

        def go():
            try:
                made.append(service.create_folder("Twin")["catalog"]["id"])
            except Exception as error:   # noqa: BLE001 - what a request answers is what the test reads
                failed.append(error)
        threads = [threading.Thread(target=go) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual((sorted(made), failed), (["twin", "twin-2", "twin-3", "twin-4"], []))
        for ident in made:
            self.assertTrue((self.home / "gravitee-lab-catalogs" / ident / "catalog.yml").is_file(), ident)

    def test_the_folder_it_makes_is_a_catalog_that_checks(self):
        service.create_folder("test")
        folder = self.home / "gravitee-lab-catalogs" / "test"
        self.assertTrue((folder / "catalog.yml").is_file())
        self.assertEqual(registry.entry("test")["kind"], "folder")


def meta_text(**fields):
    """A catalog.yml of the fixture partner catalog with these fields changed."""
    base = {"schema": "1", "id": "partner", "name": "Partner fixture", "description": "Synthetic units that another catalog "
            "includes", "version": "2.0.0", "lab": "1", "license": "AGPL-3.0-only"}
    base.update({key: str(value) for key, value in fields.items()})
    return "x-gravitee-catalog:\n" + "".join(f"  {key}: {value}\n" for key, value in base.items())


NEWER = {"addons/ledger/compose.yml": "x-gravitee: {kind: addon, name: Ledger, category: Other, description: Keeps the entries"
                                      " of a partner}\nservices:\n  partner-ledger:\n    image: busybox:1.36\n    networks: [internal]\n"}


class Checking(Seam):
    """Check again, in a request: what the lock learns, and the states a refusal makes."""

    def setUp(self):
        super().setUp()
        self.done = self.installed("partner")
        self.remote = self.remotes["partner"]

    def test_with_no_news_the_catalog_is_up_to_date_and_the_lock_says_when_it_was_looked_at(self):
        registry.set_lock("partner", checked="2026-01-01T00:00:00Z", tip="b" * 40)
        row = service.check_now("partner")["catalog"]
        held = registry.lock_of("partner")
        self.assertEqual((row["state"]["key"], held["tip"], held["error"], held["audience"]),
                         ("uptodate", self.remote.head(), None, "public"))
        self.assertGreater(held["checked"], "2026-01-02")
        self.assertEqual(held["seen"], {"target": self.remote.head(), "update": False, "tip": "ok", "version": "2.0.0"})

    def test_a_newer_version_that_changes_something_is_an_update_available_with_its_version(self):
        self.remote.commit({"catalog.yml": meta_text(version="2.1.0"), **NEWER}, "Add the ledger")
        row = service.check_now("partner")["catalog"]
        self.assertEqual((row["state"]["key"], row["state"]["reason"], row["attention"]), ("update", "Version 2.1.0", "update"))
        held = registry.lock_of("partner")
        self.assertEqual((held["tip"], held["pin"]), (self.remote.head(), self.done.commit))
        self.assertEqual(held["seen"], {"target": self.remote.head(), "update": True, "tip": "ok", "version": "2.1.0"})

    def test_a_commit_that_changes_nothing_installable_is_no_update(self):
        self.remote.commit({"README.md": "# Hub\n", ".github/workflows/ci.yml": "name: ci\n"}, "Edit the readme")
        row = service.check_now("partner")["catalog"]
        self.assertEqual((row["state"]["key"], registry.lock_of("partner")["tip"]), ("uptodate", self.remote.head()))
        self.assertFalse(registry.lock_of("partner")["seen"]["update"])

    def test_a_check_holds_no_lock_while_it_asks_the_network(self):
        seen = {}
        real = bare.fetch

        def fetch(*args, **how):
            seen["lock"] = held_elsewhere(registry.LOCK)
            return real(*args, **how)
        self.patch(bare, "fetch", fetch)
        service.check_now("partner")
        self.assertFalse(seen["lock"])

    def test_every_refusal_of_the_network_is_the_state_of_the_row_and_never_an_error_of_the_request(self):
        states = {"unreachable": "unreachable", "timeout": "unreachable", "certificate": "unreachable",
                  "no_access": "noaccess", "not_found": "noaccess", "ssh_denied": "noaccess", "host_key": "noaccess",
                  "git_missing": "needs_git", "git_old": "needs_git", "fsck": "failed", "too_large": "failed"}
        registry.set_lock("partner", checked="2026-10-16T09:00:00Z")
        for code, key in states.items():
            with self.subTest(code=code):
                self.patch(bare, "fetch", side_effect=lambda address, *a, _code=code, **k: (_ for _ in ()).throw(
                    gitx.fail(_code, address)))
                answer = service.check_now("partner")
                self.assertEqual(answer["catalog"]["state"]["key"], key)
                held = registry.lock_of("partner")
                self.assertEqual((held["error"], held["checked"]), (code, "2026-10-16T09:00:00Z"),
                                 "a check that failed is not a check that was made")
                self.assertEqual(answer["catalog"]["commit"], self.done.commit[:7], "the copy that is installed keeps working")

    def test_a_catalog_found_damaged_stays_damaged_through_a_check_that_succeeds_one_that_finds_a_rewrite_and_one_that_fails(self):
        registry.set_lock("partner", error="damaged")
        row = service.check_now("partner")["catalog"]
        held = registry.lock_of("partner")
        self.assertEqual((row["state"]["key"], row["attention"], held["error"]), ("damaged", "repair", "damaged"))
        self.assertEqual((held["tip"], held["seen"]["target"]), (self.remote.head(), self.remote.head()), "and it was a check")
        gitfixture.force_push_over(self.remote.bare, {"catalog.yml": meta_text(version="9.0.0"), **NEWER})
        row = service.check_now("partner")["catalog"]
        held = registry.lock_of("partner")
        self.assertEqual((row["state"]["key"], held["error"], held["tip"]), ("damaged", "damaged", self.remote.head()))
        self.patch(bare, "fetch", side_effect=lambda address, *a, **k: (_ for _ in ()).throw(gitx.fail("unreachable", address)))
        row = service.check_now("partner")["catalog"]
        self.assertEqual((row["state"]["key"], registry.lock_of("partner")["error"]), ("damaged", "damaged"))
        self.assertEqual(service.repair("partner")["catalog"]["state"]["key"], "uptodate")
        self.assertIsNone(registry.lock_of("partner")["error"], "Repair is what clears it")

    def test_a_success_after_a_refusal_clears_it(self):
        registry.set_lock("partner", error="unreachable")
        self.assertEqual(service.check_now("partner")["catalog"]["state"]["key"], "uptodate")
        self.assertIsNone(registry.lock_of("partner")["error"])

    def test_a_history_that_is_not_the_one_installed_is_rewritten_and_nothing_else_proceeds(self):
        before = registry.lock_of("partner")
        gitfixture.force_push_over(self.remote.bare, {"catalog.yml": meta_text(version="9.0.0"),
                                                      "addons/other/compose.yml": NEWER["addons/ledger/compose.yml"]})
        row = service.check_now("partner")["catalog"]
        held = registry.lock_of("partner")
        self.assertEqual((row["state"]["key"], held["error"], held["tip"]), ("rewritten", "history_changed", self.remote.head()))
        self.assertEqual((held["pin"], held["roots"], held.get("seen")), (before["pin"], before["roots"], before.get("seen")),
                         "the identity is not taken from a history that is not the one that was added")

    def test_a_rewrite_that_lands_on_the_roots_the_engine_pins_is_not_flagged(self):
        self.list_at("partner", self.remote)
        gitfixture.force_push_over(self.remote.bare, {"catalog.yml": meta_text(version="2.5.0"), **NEWER})
        roots = self.remote.git("rev-list", "--max-parents=0", self.remote.head()).split()
        fixtures.default(self, "partner", url=self.remote.url, branch="main", roots=roots)
        self.patch(defaults, "_seen", (None, []))
        row = service.check_now("partner")["catalog"]
        self.assertEqual(row["state"]["key"], "update")

    def test_a_tip_that_needs_a_newer_lab_leaves_the_newest_readable_version_and_offers_no_update_for_it(self):
        self.remote.commit({"catalog.yml": meta_text(version="3.0.0", lab=99)}, "Needs a newer Lab")
        row = service.check_now("partner")["catalog"]
        self.assertEqual((row["state"]["key"], row["attention"]), ("lab_new", "lab"))
        self.assertEqual(registry.lock_of("partner")["seen"], {"target": self.done.commit, "update": False, "tip": "new",
                                                               "version": "2.0.0"})

    def test_a_newer_readable_version_below_a_tip_that_is_not_readable_is_still_offered(self):
        self.remote.commit({"catalog.yml": meta_text(version="2.1.0"), **NEWER}, "Readable")
        readable = self.remote.head()
        self.remote.commit({"catalog.yml": meta_text(version="3.0.0", lab=99)}, "Needs a newer Lab")
        service.check_now("partner")
        self.assertEqual(registry.lock_of("partner")["seen"], {"target": readable, "update": True, "tip": "new",
                                                               "version": "2.1.0"})
        self.assertEqual(self.state("partner"), "update", "an update to what this Lab reads comes before the word about the tip")

    def test_the_audience_is_measured_again_at_every_check(self):
        self.assertEqual(registry.lock_of("partner")["audience"], "private")
        service.check_now("partner")
        self.assertEqual(registry.lock_of("partner")["audience"], "public")
        self.patch(gitx, "ls_remote", side_effect=lambda address, branch=None, anonymous=False, timeout=20: (
            (_ for _ in ()).throw(gitx.fail("no_access", address)) if anonymous else {"branch": "main", "head": self.remote.head()}))
        service.check_now("partner")
        self.assertEqual(registry.lock_of("partner")["audience"], "private")

    def test_an_entry_with_no_copy_is_the_download_the_worker_does_and_the_request_answers_at_once(self):
        from lab_ui.catalogs import firstrun
        woken = []
        self.patch(firstrun, "reconcile", side_effect=lambda: woken.append("reconcile"))
        self.patch(firstrun, "wake", side_effect=lambda: woken.append("wake"))
        fixtures.declare(self, "lab-new", error="unreachable")
        self.patch(gitx, "run", side_effect=AssertionError("git was run"))
        row = service.check_now("lab-new")["catalog"]
        self.assertEqual((row["state"]["key"], woken), ("downloading", ["reconcile", "wake"]))
        self.assertIsNone(registry.lock_of("lab-new")["error"])

    def test_a_folder_a_working_copy_and_an_operation_under_way_have_nothing_to_ask(self):
        self.patch(bare, "fetch", side_effect=AssertionError("something was fetched"))
        fixtures.install(self, "lab-old", kind="folder")
        self.assertEqual(service.check_now("lab-old")["catalog"]["kind"], "folder")
        shadow = fixtures.install(self, "lab-new", kind="folder", store=True)
        self.assertTrue(shadow)
        self.assertEqual(service.check_now("lab-new")["catalog"]["state"]["key"], "linked")
        registry.set_lock("partner", pending={"op": "update", "from": self.done.commit, "to": "b" * 40})
        with held_by_another():   # a change that runs is left alone
            self.assertEqual(service.check_now("partner")["catalog"]["state"]["key"], "updating")
        self.assertIsNotNone(registry.lock_of("partner")["pending"])

    def test_a_change_that_failed_halfway_is_finished_by_check_again_or_the_catalog_is_damaged_and_repair_clears_it(self):
        pin = self.done.commit
        registry.set_lock("partner", pending={"op": "update", "from": pin, "to": pin})   # a repair that raised
        row = service.check_now("partner")["catalog"]
        held = registry.lock_of("partner")
        self.assertEqual((row["state"]["key"], held["pending"], held["error"], held["pin"]), ("uptodate", None, None, pin))
        registry.set_lock("partner", pending={"op": "update", "from": pin, "to": "b" * 40})   # towards a commit nobody has
        row = service.check_now("partner")["catalog"]
        held = registry.lock_of("partner")
        self.assertEqual((row["state"]["key"], row["attention"], held["pending"], held["error"], held["pin"]),
                         ("damaged", "repair", None, "damaged", pin))
        self.assertEqual(service.repair("partner")["catalog"]["state"]["key"], "uptodate")
        self.assertIsNone(registry.lock_of("partner")["error"])

    def test_the_download_of_a_first_copy_is_the_workers_and_check_again_does_not_finish_it(self):
        fixtures.declare(self, "lab-new")
        registry.set_lock("lab-new", pending={"op": "add", "from": None, "to": "b" * 40})
        self.patch(install, "recover", side_effect=AssertionError("the download was recovered under the worker"))
        self.assertEqual(service.check_now("lab-new")["catalog"]["state"]["key"], "downloading")
        self.assertIsNotNone(registry.lock_of("lab-new")["pending"])

    def test_the_project_is_read_again_after_every_check_and_every_retry(self):
        #: a transient failure of Compose heals at the next gather
        service.check_now("partner")
        self.assertTrue(self.forgot)
        self.forgot.clear()
        from lab_ui.catalogs import firstrun
        self.patch(firstrun, "reconcile")
        self.patch(firstrun, "wake")
        fixtures.declare(self, "lab-new", error="unreachable")
        service.check_now("lab-new")
        self.assertTrue(self.forgot)
        self.forgot.clear()
        self.patch(bare, "fetch", side_effect=lambda address, *a, **k: (_ for _ in ()).throw(gitx.fail("unreachable", address)))
        service.check_now("partner")
        self.assertTrue(self.forgot, "also when the check was refused")

    def test_a_catalog_the_person_does_not_have_is_not_found(self):
        for ns in ("nowhere", "lab", "A", "", None, 5):
            with self.subTest(ns=ns), self.assertRaises(paths.NotFound):
                service.check_now(ns)

    def test_the_ssh_form_of_a_listed_address_is_tried_once_and_remembered(self):
        https = "https://github.com/acme/lab-hub.git"
        self.list_at("partner", types.SimpleNamespace(url=https))
        registry.write_config({"schema": 1, "catalogs": [dict(registry.entry("partner"), url=https), *[
            one for one in registry.entries() if one["id"] != "partner"]]})
        tried = []
        real = bare.fetch

        def fetch(address, branch, store_path, **how):
            tried.append(address.url)
            if address.url.startswith("https://"):
                raise gitx.fail("no_access", address)
            return real(gitx.parse_address(self.remote.url), branch, store_path, **how)
        self.patch(bare, "fetch", fetch)
        self.patch(gitx, "ls_remote", side_effect=lambda address, branch=None, anonymous=False, timeout=20: {
            "branch": "main", "head": self.remote.head()})
        service.check_now("partner")
        self.assertEqual(tried, [https, "git@github.com:acme/lab-hub.git"])
        self.assertEqual(registry.lock_of("partner")["via"], "ssh")
        tried.clear()
        service.check_now("partner")
        self.assertEqual(tried, ["git@github.com:acme/lab-hub.git"], "the next runs start with the form that answered")


class Checker(Seam):
    """The pass the worker calls: every catalog that has a copy and is checked automatically."""

    def setUp(self):
        super().setUp()
        self.partner = self.installed("partner")
        self.other = self.installed("lab-new", lab=1)

    def test_it_checks_each_git_catalog_that_has_a_copy_and_says_what_it_found_in_the_lock_and_the_state(self):
        self.remotes["partner"].commit({"catalog.yml": meta_text(version="2.1.0"), **NEWER}, "Add the ledger")
        service.check_all()
        self.assertEqual(self.state("partner"), "update")
        self.assertEqual(self.state("lab-new"), "uptodate")
        for ns in ("partner", "lab-new"):
            self.assertIsNotNone(registry.lock_of(ns)["checked"])
            self.assertEqual(registry.lock_of(ns)["tip"], self.remotes[ns].head())

    def test_it_skips_a_catalog_that_is_not_checked_automatically_a_folder_a_working_copy_and_one_with_no_copy(self):
        config = registry.read_config()
        config["catalogs"] = [dict(one, auto=False) if one["id"] == "lab-new" else one for one in config["catalogs"]]
        registry.write_config(config)
        fixtures.install(self, "lab-old", kind="folder")
        fixtures.declare(self, "hostile")
        checked = []
        real = service.check_now
        self.patch(service, "check_now", lambda ns: checked.append(ns) or real(ns))
        service.check_all()
        self.assertEqual(checked, ["partner"])

    def test_it_asks_nothing_without_git(self):
        before = registry.read_lock()
        self.patch(gitx, "version", return_value=None)
        self.patch(bare, "fetch", side_effect=AssertionError("something was fetched"))
        service.check_all()
        self.assertEqual(registry.read_lock(), before)

    def test_one_catalog_that_fails_is_its_state_and_the_others_are_checked(self):
        real = bare.fetch

        def fetch(address, branch, store_path, **how):
            if "partner" in str(store_path):
                raise gitx.fail("unreachable", address)
            return real(address, branch, store_path, **how)
        self.patch(bare, "fetch", fetch)
        service.check_all()
        self.assertEqual((self.state("partner"), self.state("lab-new")), ("unreachable", "uptodate"))

    def test_a_failure_nobody_expected_is_said_in_a_line_and_does_not_stop_the_pass(self):
        real = service.check_now

        def check(ns):
            if ns == "partner":
                raise KeyError("boom")
            return real(ns)
        self.patch(service, "check_now", check)
        said = io_stderr()
        with said:
            service.check_all()
        self.assertEqual(said.text().strip(), "Gravitee Lab UI: could not check partner (KeyError)")
        self.assertEqual(self.state("lab-new"), "uptodate")

    def test_the_module_starts_no_thread(self):
        starts = []
        self.patch(threading.Thread, "start", lambda self_: starts.append(self_) or None)
        service.check_all()
        service.check_now("partner")
        self.assertEqual(starts, [])
        import ast
        tree = ast.parse(Path(service.__file__).read_text(encoding="utf-8"))
        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertNotIn("Thread", names, "the one background thread of the Lab is the worker's")
        self.assertNotIn("Timer", names)


class io_stderr:   # noqa: N801 - a context manager used like a function
    """Standard error captured as text."""

    def __enter__(self):
        import io
        self.buffer, self.patcher = io.StringIO(), mock.patch.object(sys, "stderr")
        self.patcher.start().write = self.buffer.write
        return self

    def __exit__(self, *exc):
        self.patcher.stop()

    def text(self):
        return self.buffer.getvalue()


class BringingForward(Seam):
    """The one update nobody asks for, a listed catalog this Lab can no longer read, with nothing of it on."""

    def setUp(self):
        super().setUp()
        self.partner = self.installed("partner", lab=0)
        self.remote = self.remotes["partner"]
        self.list_at("partner", self.remote)
        self.remote.commit({"catalog.yml": meta_text(version="2.1.0", lab=1), **NEWER}, "Written for this Lab")

    def test_it_is_brought_to_the_newest_readable_version_with_go_back_kept(self):
        service.check_all()
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["previous"], held["version"]), (self.remote.head(), self.partner.commit, "2.1.0"))
        self.assertEqual(self.state("partner"), "uptodate")

    def test_a_newer_version_the_strict_reading_of_its_level_refuses_is_not_brought_and_nothing_is_left_behind(self):
        # `pick` chooses by a tolerant reading that is unsure of a level spelled with an anchor; the plan reads it strictly
        self.remote.commit({"catalog.yml": meta_text(version="3.0.0").replace("lab: 1", "lab: &level 99"), **NEWER}, "Needs a newer Lab")
        before = registry.lock_of("partner")
        service.check_all()
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["previous"], held["version"]), (before["pin"], before["previous"], before["version"]))
        self.assertEqual((held["pending"], held["error"]), (None, None), "a refusal of the plan is no failure of the catalog")
        self.assertFalse((self.root / ".lab" / "catalogs" / ".stage-partner").exists())
        self.assertEqual(service.TICKETS, {})
        self.assertEqual(self.state("partner"), "lab_old")

    def test_the_level_of_this_lab_is_what_decides_whether_it_is_brought(self):
        self.patch(service, "_contract", lambda: {"contract": 1, "contract_min": 1, "seeder_image": ""})
        self.remote.commit({"catalog.yml": meta_text(version="3.0.0", lab=2), **NEWER}, "Written for the Lab after this one")
        service.check_all()
        self.assertEqual(registry.lock_of("partner")["version"], "2.1.0", "the newest it can read is the one it moves to")

    def test_something_on_a_job_that_runs_or_a_catalog_that_is_not_listed_keeps_it_from_moving(self):
        on = self.root / ".lab" / "on.yml"
        on.write_text("include:\n  - .lab/catalogs/partner/addons/ledger/compose.yml\n", encoding="utf-8")
        service.check_all()
        row = self.row("partner")
        self.assertEqual((registry.lock_of("partner")["pin"], row["state"]["key"], row["attention"]),
                         (self.partner.commit, "lab_old", "update"), "it waits, and the dot lights")
        on.write_text("", encoding="utf-8")
        job = jobs.Job("Start Ledger", [["true"]], key="addon:x", verb="start")
        jobs.JOBS.jobs.append(job)
        jobs.JOBS.current = job
        service.check_all()
        self.assertEqual(registry.lock_of("partner")["pin"], self.partner.commit)
        job.done = True
        fixtures.default(self, "fixture")   # listed no more
        self.patch(defaults, "_seen", (None, []))
        project.forget()
        service.check_all()
        self.assertEqual(registry.lock_of("partner")["pin"], self.partner.commit, "only a catalog the engine lists is moved by itself")


GAINS = {"addons/ledger/compose.yml": "x-gravitee: {kind: addon, name: Ledger, category: Other, description: Keeps the entries of "
                                      "a partner}\nservices:\n  partner-ledger:\n    image: busybox:1.36\n    ports: ['127.0.0.1:8192:80']\n"
                                      "    networks: [internal]\n",
         "addons/audit/compose.yml": "x-gravitee: {kind: addon, name: Audit, category: Other, description: Keeps a trail}\nservices:\n"
                                     "  partner-audit:\n    image: busybox:1.36\n    networks: [internal]\n"}


class Updating(Seam):
    """The plan, its ticket, and the apply; Go back is the same with the previous version."""

    def setUp(self):
        super().setUp()
        self.done = self.installed("partner")
        self.remote = self.remotes["partner"]
        self.first = self.done.commit

    def news(self, **how):
        """The repository gets a version that adds an add-on, and changes the ledger; Check again learns of it."""
        self.remote.commit({"catalog.yml": meta_text(version="2.1.0"), **GAINS}, how.get("message", "Add the audit trail"))
        service.check_now("partner")
        return self.remote.head()

    def plan(self, **how):
        return service.update("partner", check=True, **how)

    def test_the_plan_is_the_shape_of_13_2_and_a_ticket_that_lives_ten_minutes(self):
        target = self.news()
        plan = self.plan()
        self.assertEqual(sorted(plan), ["adds", "blocked", "breaks", "changes", "expires", "from", "held", "removes", "rewritten",
                                        "ticket", "to", "updates", "uses_machine", "version", "version_from", "whatsnew"])
        self.assertEqual((plan["from"], plan["to"], plan["version"], plan["rewritten"], plan["expires"], plan["held"]),
                         (self.first[:7], target[:7], "2.1.0", False, 600, None))
        self.assertEqual(plan["updates"], [{"kind": "addon", "id": "@partner/ledger", "name": "Ledger", "what": ["compose.yml"]}],
                         "an item that changes says what changed in its folder")
        self.assertEqual(plan["version_from"], registry.lock_of("partner")["version"], "the version installed, beside the new one")
        self.assertEqual(plan["adds"], [{"kind": "addon", "id": "@partner/audit", "name": "Audit"}])
        self.assertEqual((plan["removes"], plan["breaks"], plan["changes"], plan["blocked"], plan["uses_machine"]),
                         ([], [], [], [], []))
        self.assertEqual(plan["whatsnew"], ["Add the audit trail"])
        self.assertRegex(plan["ticket"], r"^[0-9a-f]{16}$")
        self.assertIn(plan["ticket"], service.TICKETS)

    def test_a_plan_changes_nothing_on_disk_but_the_staged_candidate(self):
        self.news()
        before = self.digest()
        self.plan()
        self.assertEqual(self.digest(), before)
        self.assertTrue((self.root / ".lab" / "catalogs" / ".stage-partner").is_dir())

    def digest(self):
        base = self.root / ".lab" / "catalogs" / "partner"
        return {str(path.relative_to(base)): path.read_bytes() for path in sorted(base.rglob("*")) if path.is_file()}, \
            registry.lock_of("partner")["pin"], registry.lock_of("partner")["pending"]

    def test_nothing_to_apply_is_up_to_date_and_so_is_a_version_that_changes_nothing_installable(self):
        self.assertEqual(self.plan(), {"upToDate": True})
        self.remote.commit({"README.md": "# Hub\n"}, "Edit the readme")
        service.check_now("partner")
        self.assertEqual(self.plan(), {"upToDate": True})
        self.assertEqual(service.TICKETS, {})

    def test_the_apply_moves_the_pin_keeps_the_one_before_and_spends_the_ticket(self):
        target = self.news()
        ticket = self.plan()["ticket"]
        answer = service.update("partner", ticket=ticket)
        row = answer["catalog"]
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["previous"], held["version"], held["pending"], held["error"]),
                         (target, self.first, "2.1.0", None, None))
        self.assertEqual((row["state"]["key"], row["version"], row["commit"], row["has_previous"], row["attention"]),
                         ("uptodate", "2.1.0", target[:7], True, None))
        self.assertEqual(registry.units_of("partner")["addon"], ["audit", "ledger"])
        self.assertNotIn(ticket, service.TICKETS)
        with self.assertRaises(CatalogError) as raised:
            service.update("partner", ticket=ticket)
        self.assertEqual(raised.exception.code, "ticket_expired")

    def test_a_removed_unit_a_changed_one_and_an_item_that_loses_what_it_includes(self):
        self.remote.commit({"catalog.yml": meta_text(version="3.0.0")}, "Retire the ledger", remove=["addons/ledger"])
        self.remote.commit({"addons/audit/compose.yml": GAINS["addons/audit/compose.yml"]}, "Add the audit trail")
        service.check_now("partner")
        self.items = [("playground", "local/demo", "Demo", ["@partner/ledger"]),
                      ("playground", "local/other", "Other", ["echo"])]
        plan = self.plan()
        self.assertEqual(plan["removes"], [{"kind": "addon", "id": "@partner/ledger", "name": "Ledger"}])
        self.assertEqual(plan["adds"], [{"kind": "addon", "id": "@partner/audit", "name": "Audit"}])
        self.assertEqual(plan["breaks"], [{"kind": "playground", "id": "local/demo", "name": "Demo", "needs": "Ledger"}])
        self.assertEqual(plan["whatsnew"], ["Add the audit trail", "Retire the ledger"], "the news of the units that come, change or go")

    def test_what_the_policy_blocks_in_the_new_version_is_named_with_its_reason(self):
        self.news()
        self.against = {"@partner/audit": ["Mounts the Docker socket"]}
        plan = self.plan()
        self.assertEqual(plan["blocked"], [{"kind": "addon", "id": "@partner/audit", "name": "Audit",
                                            "reasons": ["Mounts the Docker socket"]}])

    def test_what_the_new_version_takes_of_this_machine_is_a_row_while_the_catalog_is_not_allowed_and_not_after(self):
        self.news()
        self.uses = {"@partner/audit": [{"service": "partner-audit", "label": "Audit", "what": ["runs privileged"], "why": "Needs it"}]}
        plan = self.plan()
        self.assertEqual(plan["uses_machine"], [{"id": "@partner/audit", "name": "Audit", "service": "partner-audit",
                                                 "what": ["runs privileged"], "why": "Needs it"}])
        config = registry.read_config()
        config["catalogs"] = [dict(one, host=True) if one["id"] == "partner" else one for one in config["catalogs"]]
        registry.write_config(config)
        self.assertEqual(self.plan()["uses_machine"], [])

    def test_an_item_that_is_on_holds_the_update_and_says_which_in_the_words_of_a_start_or_a_stop(self):
        self.news()
        on = self.root / ".lab" / "on.yml"
        on.write_text("include:\n  - .lab/catalogs/partner/addons/ledger/compose.yml\n", encoding="utf-8")
        self.assertEqual(self.plan()["held"], "Turn off Ledger first")
        self.up = {"addon:ledger"}
        self.assertEqual(self.plan()["held"], "Stop Ledger first")
        self.up = {"addon:audit"}   # a unit the update only adds is not held
        on.write_text("", encoding="utf-8")
        self.assertIsNone(self.plan()["held"])

    def test_a_job_that_runs_holds_the_plan_and_the_apply_is_job_running_and_the_ticket_is_kept(self):
        self.news()
        ticket = self.plan()["ticket"]
        job = jobs.Job("Start Ledger", [["true"]], key="addon:x", verb="start")
        jobs.JOBS.jobs.append(job)
        jobs.JOBS.current = job
        self.assertEqual(self.plan()["held"], "Wait for Start Ledger")
        ticket = next(iter(service.TICKETS))
        with self.assertRaises(CatalogError) as raised:
            service.update("partner", ticket=ticket)
        self.assertEqual(lines_of(raised.exception), ("job_running", 409, "Wait for Start Ledger."))
        job.done = True
        self.assertEqual(service.update("partner", ticket=ticket)["catalog"]["version"], "2.1.0")

    def test_an_apply_while_an_item_is_on_is_held_with_the_name_of_the_item(self):
        self.news()
        ticket = self.plan()["ticket"]
        (self.root / ".lab" / "on.yml").write_text("include:\n  - .lab/catalogs/partner/addons/ledger/compose.yml\n", encoding="utf-8")
        with self.assertRaises(CatalogError) as raised:
            service.update("partner", ticket=ticket)
        self.assertEqual(lines_of(raised.exception), ("held", 409, "Turn off Ledger first."))
        self.up = {"addon:ledger"}
        with self.assertRaises(CatalogError) as raised:
            service.update("partner", ticket=ticket)
        self.assertEqual(lines_of(raised.exception), ("held", 409, "Stop Ledger first."))
        self.assertEqual(registry.lock_of("partner")["pin"], self.first, "nothing was touched")

    def test_a_catalog_that_is_already_at_the_version_of_the_plan_is_up_to_date(self):
        target = self.news()
        ticket = self.plan()["ticket"]
        registry.set_lock("partner", pin=target)   # another change got there first
        with self.assertRaises(CatalogError) as raised:
            service.update("partner", ticket=ticket)
        self.assertEqual(lines_of(raised.exception), ("up_to_date", 409, "Already up to date."))

    def test_a_history_that_changed_is_a_plan_that_says_so_and_an_apply_that_wants_the_person_to_confirm(self):
        gitfixture.force_push_over(self.remote.bare, {"catalog.yml": meta_text(version="9.0.0"), **GAINS})
        service.check_now("partner")
        plan = self.plan()
        self.assertTrue(plan["rewritten"])
        with self.assertRaises(CatalogError) as raised:
            service.update("partner", ticket=plan["ticket"])
        self.assertEqual(lines_of(raised.exception), ("history_changed", 409,
                                                      "This catalog was rewritten or moved. Confirm to update anyway."))
        self.assertEqual(registry.lock_of("partner")["pin"], self.first)
        service.update("partner", ticket=plan["ticket"], confirm_history=True)
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["roots"]), (self.remote.head(), self.remote.git(
            "rev-list", "--max-parents=0", self.remote.head()).split()), "the history confirmed is the history now")
        self.assertEqual(self.state("partner"), "uptodate")

    def test_a_ticket_is_for_one_catalog_and_one_change(self):
        self.news()
        plan = self.plan()
        self.installed("lab-new", lab=1)
        with self.assertRaises(CatalogError) as raised:
            service.update("lab-new", ticket=plan["ticket"])
        self.assertEqual(raised.exception.code, "ticket_expired")
        with self.assertRaises(CatalogError) as raised:
            service.undo("partner", ticket=plan["ticket"])
        self.assertEqual(raised.exception.code, "ticket_expired")
        with self.assertRaises(CatalogError) as raised:
            service.changes("lab-new", plan["ticket"])
        self.assertEqual(raised.exception.code, "ticket_expired")
        self.assertIn(plan["ticket"], service.TICKETS)

    def test_a_ticket_older_than_ten_minutes_is_expired_and_its_staged_tree_goes(self):
        clock = [500.0]
        self.patch(service, "_now", lambda: clock[0])
        self.news()
        ticket = self.plan()["ticket"]
        clock[0] += service.TTL + 1
        with self.assertRaises(CatalogError) as raised:
            service.update("partner", ticket=ticket)
        self.assertEqual(raised.exception.code, "ticket_expired")
        self.assertFalse((self.root / ".lab" / "catalogs" / ".stage-partner").exists())

    def test_neither_a_check_nor_a_ticket_is_nothing_to_do(self):
        with self.assertRaises(CatalogError):
            service.update("partner")
        with self.assertRaises(CatalogError):
            service.undo("partner")

    def test_a_cache_that_was_cleared_is_fetched_again_before_the_plan(self):
        target = self.news()
        shutil.rmtree(registry.store_dir("partner"))
        plan = self.plan()
        self.assertEqual(plan["to"], target[:7])
        self.assertTrue(registry.store_dir("partner").is_dir())

    def test_a_folder_has_no_versions_and_a_working_copy_is_linked(self):
        fixtures.install(self, "lab-old", kind="folder")
        with self.assertRaises(ValueError) as raised:
            service.update("lab-old", check=True)
        self.assertIn("has no versions", str(raised.exception))
        fixtures.install(self, "lab-new", kind="folder", store=True)
        with self.assertRaises(CatalogError) as raised:
            service.update("lab-new", check=True)
        self.assertEqual(lines_of(raised.exception), ("linked", 409,
                                                      "This catalog is linked to a folder on this machine. Unlink it from a terminal first."))
        with self.assertRaises(paths.NotFound):
            service.update("nowhere", check=True)

    def test_an_address_that_now_serves_another_catalog_is_wrong_catalog_and_nothing_is_staged_to_install(self):
        self.remote.commit({"catalog.yml": meta_text(id="other-hub", version="9.0.0")}, "Another catalog")
        service.check_now("partner")
        with self.assertRaises(CatalogError) as raised:
            self.plan()
        code, status, line = lines_of(raised.exception)
        self.assertEqual((code, status), ("wrong_catalog", 409))
        self.assertTrue(line.endswith(" is not the catalog this Lab expects. Run make update, then try again."), line)
        self.assertFalse((self.root / ".lab" / "catalogs" / ".stage-partner").exists())

    def test_the_level_of_this_lab_goes_to_the_plan_for_its_strict_reading_of_the_version(self):
        self.news()
        seen = []
        real = install.plan_update

        def watch(ns, target, context=None):
            seen.append(dict(context or {}))
            return real(ns, target, context)
        self.patch(install, "plan_update", watch)
        self.plan()
        self.assertEqual([one["contract"] for one in seen], [BARE])
        self.assertEqual(sorted(seen[0]), ["contract", "on", "vouched"])

    def test_a_history_that_was_confirmed_is_what_the_next_check_compares_with_and_nothing_older_is_on_offer(self):
        service.check_now("partner")
        self.assertEqual(registry.lock_of("partner")["seen"]["target"], self.first, "the newest known is the installed one")
        gitfixture.force_push_over(self.remote.bare, {"catalog.yml": meta_text(version="9.0.0"), **GAINS})
        self.assertEqual(service.check_now("partner")["catalog"]["state"]["key"], "rewritten")
        self.assertEqual(registry.lock_of("partner")["seen"]["target"], self.first, "a rewrite proceeds no further")
        plan = self.plan()
        row = service.update("partner", ticket=plan["ticket"], confirm_history=True)["catalog"]
        held = registry.lock_of("partner")
        self.assertEqual(held["seen"], {"target": self.remote.head(), "update": False, "tip": "ok", "version": "9.0.0"},
                         "the newest is the version just installed, not the commit of the history that was replaced")
        self.assertEqual((row["state"]["key"], row["attention"], row["version"]), ("uptodate", None, "9.0.0"))

    def test_going_back_after_a_confirmed_history_needs_no_confirmation_the_version_is_one_the_person_ran(self):
        gitfixture.force_push_over(self.remote.bare, {"catalog.yml": meta_text(version="9.0.0"), **GAINS})
        service.check_now("partner")
        service.update("partner", ticket=self.plan()["ticket"], confirm_history=True)
        roots_after = registry.lock_of("partner")["roots"]
        back = service.undo("partner", check=True)
        self.assertTrue(back["rewritten"], "the plan says so, the dialog warns")
        row = service.undo("partner", ticket=back["ticket"])["catalog"]
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["version"], held["pending"], held["error"]), (self.first, "2.0.0", None, None))
        self.assertNotEqual(held["roots"], roots_after, "and the history is the one just returned to")
        self.assertEqual((row["state"]["key"], row["has_previous"]), ("update", True))

    def test_a_change_that_fails_halfway_does_not_leave_the_row_updating_it_is_damaged_and_repair_makes_it_whole(self):
        self.news()
        plan = self.plan()
        calls = []

        def flaky(source, target):
            calls.append(target)
            raise OSError(28, "No space left on device")
        with mock.patch.object(install, "safe_replace", flaky), self.assertRaises(OSError):
            service.update("partner", ticket=plan["ticket"])
        held = registry.lock_of("partner")
        self.assertEqual((held["pending"], held["error"], held["pin"]), (None, "damaged", self.first))
        row = self.row("partner")
        self.assertEqual((row["state"]["key"], row["attention"]), ("damaged", "repair"), "the person has the action")
        self.assertIn(plan["ticket"], service.TICKETS, "a ticket is spent by a change that was made")
        row = service.repair("partner")["catalog"]
        self.assertEqual((row["state"]["key"], registry.lock_of("partner")["error"]), ("update", None))
        again = service.update("partner", ticket=self.plan()["ticket"])["catalog"]
        self.assertEqual((again["version"], again["state"]["key"]), ("2.1.0", "uptodate"))

    def test_a_failure_that_does_not_last_is_finished_as_a_start_would_finish_it_and_the_request_still_says_it_failed(self):
        self.news()
        plan = self.plan()
        real = install.safe_replace
        calls = []

        def once(source, target):
            calls.append(target)
            if len(calls) == 2:
                raise OSError(5, "Input/output error")
            return real(source, target)
        with mock.patch.object(install, "safe_replace", once), self.assertRaises(OSError):
            service.update("partner", ticket=plan["ticket"])
        held = registry.lock_of("partner")
        self.assertEqual((held["pending"], held["error"], held["pin"], held["version"]),
                         (None, None, self.remote.head(), "2.1.0"))
        self.assertEqual(self.state("partner"), "uptodate")

    def test_a_refusal_before_anything_was_written_is_not_a_failure_halfway(self):
        self.news()
        ticket = self.plan()["ticket"]
        (self.root / ".lab" / "on.yml").write_text("include:\n  - .lab/catalogs/partner/addons/ledger/compose.yml\n", encoding="utf-8")
        ran = []
        self.patch(install, "recover", side_effect=lambda: ran.append(True))
        with self.assertRaises(CatalogError):
            service.update("partner", ticket=ticket)
        self.assertEqual((ran, registry.lock_of("partner")["pin"]), ([], self.first))

    def test_a_catalog_with_no_copy_has_nothing_to_update(self):
        fixtures.declare(self, "lab-new")
        with self.assertRaises(ValueError) as raised:
            service.update("lab-new", check=True)
        self.assertEqual(str(raised.exception), "lab-new has nothing installed yet")


class GoingBack(Seam):
    """The same plan and apply with the previous version, and the action toggles."""

    def setUp(self):
        super().setUp()
        self.done = self.installed("partner")
        self.remote = self.remotes["partner"]
        self.first = self.done.commit
        self.remote.commit({"catalog.yml": meta_text(version="2.1.0"), **GAINS}, "Add the audit trail")
        self.second = self.remote.head()
        service.check_now("partner")
        service.update("partner", ticket=service.update("partner", check=True)["ticket"])

    def test_there_is_nothing_to_go_back_to_before_a_first_update(self):
        self.installed("lab-new", lab=1)
        with self.assertRaises(CatalogError) as raised:
            service.undo("lab-new", check=True)
        self.assertEqual(lines_of(raised.exception), ("no_previous", 409, "No previous version."))

    def test_going_back_names_what_changes_and_leaves_the_version_it_left_on_offer_so_the_action_toggles(self):
        plan = service.undo("partner", check=True)
        self.assertEqual((plan["from"], plan["to"], plan["version"]), (self.second[:7], self.first[:7], "2.0.0"))
        self.assertEqual(plan["removes"], [{"kind": "addon", "id": "@partner/audit", "name": "Audit"}])
        self.assertEqual(plan["whatsnew"], [], "a step back has no news")
        row = service.undo("partner", ticket=plan["ticket"])["catalog"]
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["previous"], held["version"]), (self.first, self.second, "2.0.0"))
        self.assertEqual((row["state"]["key"], row["has_previous"], row["attention"]), ("update", True, None),
                         "Update available again, and the dot is not lit by the version just left")
        self.assertEqual(row["state"]["reason"], "Version 2.1.0")
        again = service.update("partner", ticket=service.update("partner", check=True)["ticket"])["catalog"]
        self.assertEqual((again["version"], again["state"]["key"]), ("2.1.0", "uptodate"))
        self.assertEqual(registry.lock_of("partner")["previous"], self.first, "and Go back toggles")

    def test_going_back_to_a_version_written_for_a_level_this_lab_no_longer_reads_is_refused_as_an_update_is(self):
        # The Lab moved on since: the version the person left is "Written for an older Lab", and going back to it is not offered
        before = (registry.lock_of("partner"), self.digest())
        self.patch(service, "_contract", lambda: {"contract": 2, "contract_min": 2, "seeder_image": ""})
        for listed, line in ((False, "Written for an older Lab (level 1, this Lab reads 2 to 2). Ask its owners to update it."),
                             (True, "The catalog is not ready for this version of the Lab. Try again later.")):
            if listed:
                self.list_at("partner", self.remote)
            with self.subTest(listed=listed), self.assertRaises(CatalogError) as raised:
                service.undo("partner", check=True)
            self.assertEqual(lines_of(raised.exception), ("lab_old", 400, line))
            self.assertFalse((self.root / ".lab" / "catalogs" / ".stage-partner").exists(), "what was refused is not left to install")
            self.assertEqual(service.TICKETS, {})
        self.assertEqual((registry.lock_of("partner"), self.digest()), before, "nothing moved")
        self.patch(service, "_contract", lambda: {"contract": 2, "contract_min": 1, "seeder_image": ""})
        self.assertEqual(service.undo("partner", check=True)["to"], self.first[:7], "a Lab that still reads it goes back")

    def digest(self):
        base = self.root / ".lab" / "catalogs" / "partner"
        return {str(path.relative_to(base)): path.read_bytes() for path in sorted(base.rglob("*")) if path.is_file()}

    def test_going_back_while_an_item_is_on_is_held_like_an_update(self):
        (self.root / ".lab" / "on.yml").write_text("include:\n  - .lab/catalogs/partner/addons/audit/compose.yml\n", encoding="utf-8")
        plan = service.undo("partner", check=True)
        self.assertEqual(plan["held"], "Turn off Audit first")
        with self.assertRaises(CatalogError) as raised:
            service.undo("partner", ticket=plan["ticket"])
        self.assertEqual(lines_of(raised.exception), ("held", 409, "Turn off Audit first."))


class ShowingChanges(Seam):
    """"Show changes": the files of a plan with a unified diff of each."""

    def setUp(self):
        super().setUp()
        self.done = self.installed("partner")
        self.remote = self.remotes["partner"]

    def plan(self, files, message="Change the ledger", remove=()):
        self.remote.commit(files, message, remove=remove)
        service.check_now("partner")
        return service.update("partner", check=True)

    def test_each_changed_file_has_its_status_and_a_diff(self):
        plan = self.plan({"catalog.yml": meta_text(version="2.1.0"), **GAINS})
        answer = service.changes("partner", plan["ticket"])
        files = {one["path"]: one for one in answer["files"]}
        self.assertEqual(sorted(files), ["addons/audit/compose.yml", "addons/ledger/compose.yml", "catalog.yml"])
        self.assertEqual({path: one["status"] for path, one in files.items()},
                         {"addons/audit/compose.yml": "added", "addons/ledger/compose.yml": "changed", "catalog.yml": "changed"})
        self.assertIn("+  version: 2.1.0", files["catalog.yml"]["diff"])
        self.assertIn("-  version: 2.0.0", files["catalog.yml"]["diff"])
        self.assertTrue(files["addons/audit/compose.yml"]["diff"].startswith("diff --git"))
        self.assertEqual([one["path"] for one in answer["files"]], sorted(files), "in the order of the paths")

    def test_a_file_that_goes_is_removed(self):
        plan = self.plan({"catalog.yml": meta_text(version="3.0.0")}, remove=["addons/ledger"])
        files = {one["path"]: one["status"] for one in service.changes("partner", plan["ticket"])["files"]}
        self.assertEqual(files, {"addons/ledger/README.md": "removed", "addons/ledger/compose.yml": "removed",
                                 "catalog.yml": "changed"})

    def test_a_file_that_is_binary_or_too_large_to_show_has_no_diff(self):
        plan = self.plan({"catalog.yml": meta_text(version="2.1.0"), "addons/ledger/logo.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR"})
        files = {one["path"]: one for one in service.changes("partner", plan["ticket"])["files"]}
        self.assertEqual((files["addons/ledger/logo.png"]["status"], files["addons/ledger/logo.png"]["diff"]), ("added", None))
        self.patch(bare, "DIFF_LIMIT", 20)
        files = {one["path"]: one for one in service.changes("partner", plan["ticket"])["files"]}
        self.assertIsNone(files["catalog.yml"]["diff"], "Changed, not shown")

    def test_past_a_number_of_files_or_an_amount_of_text_the_rest_are_listed_and_not_shown_and_no_process_is_run_for_them(self):
        plan = self.plan({"catalog.yml": meta_text(version="2.1.0"), **GAINS, "addons/audit/README.md": "# Audit\n"})
        asked = []
        real = bare.diff
        self.patch(bare, "diff", lambda store, old, new, path: asked.append(path) or real(store, old, new, path))
        self.patch(service, "DIFF_FILES", 2)
        files = service.changes("partner", plan["ticket"])["files"]
        self.assertEqual([one["path"] for one in files],
                         ["addons/audit/README.md", "addons/audit/compose.yml", "addons/ledger/compose.yml", "catalog.yml"])
        self.assertEqual(asked, ["addons/audit/README.md", "addons/audit/compose.yml"], "the first ones in the order the dialog lists")
        self.assertEqual([one["diff"] is None for one in files], [False, False, True, True])
        self.assertEqual([one["status"] for one in files], ["added", "added", "changed", "changed"], "all are said")
        asked.clear()
        self.patch(service, "DIFF_FILES", 200)
        self.patch(service, "DIFF_BYTES", 10)   # the first diff fills the budget
        files = service.changes("partner", plan["ticket"])["files"]
        self.assertEqual((asked, [one["diff"] is None for one in files]), (["addons/audit/README.md"], [False, True, True, True]))

    def test_without_a_ticket_there_is_nothing_to_show(self):
        for ticket in (None, "0" * 16, 5):
            with self.subTest(ticket=ticket), self.assertRaises(CatalogError) as raised:
                service.changes("partner", ticket)
            self.assertEqual(raised.exception.code, "ticket_expired")


class Growth(unittest.TestCase):
    """The growth of power an update makes: only what the new surface does that the old did not."""

    def surface(self, **more):
        return {"images": [], "builds": [], "ports": [], "core": [], "reads": [], "plugins": [], "seed": None, "shell": None,
                "uses_machine": [], "needs": [], **more}

    def test_nothing_grew_when_nothing_is_new_or_less(self):
        old = self.surface(ports=[8140, 8141], reads=["license"], builds=[{"service": "a", "source": "local"}])
        self.assertEqual(service._growth(old, old), [])
        self.assertEqual(service._growth(old, self.surface(ports=[8140])), [])

    def test_each_kind_of_growth_is_one_fact_and_its_value(self):
        old = self.surface(ports=[8140], uses_machine=[{"service": "k3s", "what": ["runs privileged"]}],
                           core=[{"service": "apim-gateway", "label": "API Management gateway", "keys": ["environment"]}])
        new = self.surface(
            ports=[8140, 8141], builds=[{"service": "mock", "source": "github.com/acme/mock"}, {"service": "web", "source": "local"}],
            core=[{"service": "apim-gateway", "label": "API Management gateway", "keys": ["environment", "labels"]},
                  {"service": "mongodb", "label": "MongoDB", "keys": ["environment"]}],
            reads=["license", "gateway token"], plugins=["apim"], seed="custom",
            uses_machine=[{"service": "k3s", "what": ["runs privileged", "mounts the Docker socket"]},
                          {"service": "new", "what": ["publishes a port to the network"]}])
        self.assertEqual(service._growth(old, new), [
            ("port", "8141"), ("build", "github.com/acme/mock"), ("build", "local"),
            ("core", "API Management gateway"), ("core", "MongoDB"), ("reads", "license"), ("reads", "gateway token"),
            ("plugins", "apim"), ("seed", "custom"), ("host", "mounts the Docker socket"),
            ("host", "publishes a port to the network")])

    def test_what_the_old_surface_already_did_is_not_growth(self):
        old = self.surface(reads=["license"], seed="custom", plugins=["apim"],
                           uses_machine=[{"service": "k3s", "what": ["runs privileged"]}])
        new = self.surface(reads=["license", "gateway token"], seed="custom", plugins=["apim"],
                           uses_machine=[{"service": "k3s", "what": ["runs privileged"]}])
        self.assertEqual(service._growth(old, new), [("reads", "gateway token")])


class Removing(Seam):
    """What Remove says first, what holds it, and what it leaves."""

    def setUp(self):
        super().setUp()
        self.done = self.installed("partner")
        self.base = self.root / ".lab" / "catalogs" / "partner"

    def test_the_removal_check_says_how_many_items_and_what_would_break_and_what_stays_in_gravitee(self):
        self.items = [("playground", "local/demo", "Demo", ["@partner/ledger"]), ("playground", "local/other", "Other", ["echo"])]
        answer = service.remove("partner", check=True)
        self.assertEqual(answer, {"held": None, "items": 1, "folder": False,
                                  "breaks": [{"kind": "playground", "id": "local/demo", "name": "Demo"}], "stays": None})

    def test_what_its_playgrounds_created_in_gravitee_is_counted_under_the_words_the_seed_gave(self):
        self.installed("lab-new", lab=1)
        self.patch(service, "_stays", lambda found, ns: {"APIs": 3, "applications": 1} if ns == "lab-new" else None)
        self.assertEqual(service.remove("lab-new", check=True)["stays"], {"APIs": 3, "applications": 1})

    def test_the_stays_row_adds_up_the_records_of_each_playground_of_the_catalog_only(self):
        from lab_ui.library import playgrounds
        found = {"playgrounds": {"shop": {"catalog": "fixture"}, "tour": {"catalog": "fixture"},
                                 "@partner/x": {"catalog": "partner"}, "local/mine": {"catalog": "local"}}}
        made = {"shop": [{"what": "APIs", "count": 2}, {"what": "applications", "count": 1}], "tour": [{"what": "APIs", "count": 1}],
                "@partner/x": [{"what": "plans", "count": 9}], "local/mine": [{"what": "APIs", "count": 5}]}
        self.patch(playgrounds, "created", side_effect=lambda pid, place=None: made[pid])
        self.assertEqual(service._stays(found, "fixture"), {"APIs": 3, "applications": 1})
        self.assertIsNone(service._stays({"playgrounds": {}}, "fixture"))

    def test_an_item_that_is_on_or_up_holds_the_removal_in_the_check_and_refuses_it_in_the_request(self):
        (self.root / ".lab" / "on.yml").write_text("include:\n  - .lab/catalogs/partner/addons/ledger/compose.yml\n", encoding="utf-8")
        self.assertEqual(service.remove("partner", check=True)["held"], "Turn off Ledger first")
        with self.assertRaises(CatalogError) as raised:
            service.remove("partner", keep_settings=True)
        self.assertEqual(lines_of(raised.exception), ("held", 409, "Turn off Ledger first."))
        self.up = {"addon:ledger"}
        self.assertEqual(service.remove("partner", check=True)["held"], "Stop Ledger first")
        with self.assertRaises(CatalogError) as raised:
            service.remove("partner", keep_settings=False)
        self.assertEqual(lines_of(raised.exception), ("held", 409, "Stop Ledger first."))
        self.assertIsNotNone(registry.entry("partner"), "nothing was removed")

    def test_an_item_of_another_origin_that_is_on_and_includes_a_unit_of_it_holds_it_too(self):
        on = self.root / ".lab" / "on.yml"
        local = self.root / ".lab" / "catalogs" / "local" / "addons" / "mine"
        local.mkdir(parents=True)
        (local / "compose.yml").write_text("include:\n  - ../../../partner/addons/ledger/compose.yml\nservices: {}\n", encoding="utf-8")
        on.write_text("include:\n  - .lab/catalogs/local/addons/mine/compose.yml\n", encoding="utf-8")
        self.assertEqual(service.remove("partner", check=True)["held"], "Turn off Ledger first")

    def test_a_job_that_runs_holds_it_and_the_request_is_job_running(self):
        job = jobs.Job("Start Ledger", [["true"]], key="addon:x", verb="start")
        jobs.JOBS.jobs.append(job)
        jobs.JOBS.current = job
        self.assertEqual(service.remove("partner", check=True)["held"], "Wait for Start Ledger")
        with self.assertRaises(CatalogError) as raised:
            service.remove("partner", keep_settings=True)
        self.assertEqual(lines_of(raised.exception), ("job_running", 409, "Wait for Start Ledger."))

    def test_remove_keeps_the_settings_by_default_and_leaves_a_marker_and_forgets_the_catalog(self):
        env = self.base / "addons" / "ledger" / ".env"
        env.write_text("PARTNER_LEDGER_CURRENCY=USD\n", encoding="utf-8")
        answer = service.remove("partner")
        self.assertEqual(answer, {"warning": None})
        self.assertIsNone(registry.entry("partner"))
        self.assertNotIn("partner", registry.read_lock()["catalogs"])
        self.assertFalse(registry.store_dir("partner").exists())
        self.assertEqual(env.read_text(encoding="utf-8"), "PARTNER_LEDGER_CURRENCY=USD\n")
        self.assertTrue((self.base / install.MARKER).is_file())
        self.assertFalse((self.base / "addons" / "ledger" / "compose.yml").exists())
        row = next((one for one in service.describe(*project.loaded()) if one.get("id") == "partner"), None)
        self.assertIsNone(row)

    def test_adding_it_again_restores_what_was_kept(self):
        env = self.base / "addons" / "ledger" / ".env"
        env.write_text("PARTNER_LEDGER_CURRENCY=USD\n", encoding="utf-8")
        service.remove("partner", keep_settings=True)
        service.add(service.check_address(self.remotes["partner"].url)["ticket"])
        self.assertEqual(env.read_text(encoding="utf-8"), "PARTNER_LEDGER_CURRENCY=USD\n")

    def test_delete_them_too_removes_the_folder(self):
        (self.base / "addons" / "ledger" / ".env").write_text("A=1\n", encoding="utf-8")
        self.assertEqual(service.remove("partner", keep_settings=False), {"warning": None})
        self.assertFalse(self.base.exists())

    def test_files_docker_made_that_cannot_be_deleted_are_said_in_the_line_of_removed_partial(self):
        self.patch(install, "_remove_tree", return_value=2)
        answer = service.remove("partner", keep_settings=False)
        self.assertEqual(answer, {"warning": "Some files were created by Docker and could not be deleted. Remove the folder "
                                             ".lab/catalogs/partner with administrator rights."})
        self.assertIsNone(registry.entry("partner"))

    def test_the_settings_choice_is_a_boolean(self):
        for value in (None, "yes", 1, [], "false"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                service.remove("partner", keep_settings=value)
        self.assertIsNotNone(registry.entry("partner"))

    def test_a_folder_catalog_loses_its_link_and_the_authors_folder_is_never_touched(self):
        folder = self.folder_catalog("hostile")
        service.add(service.check_address(str(folder))["ticket"])
        before = sorted(str(path.relative_to(folder)) for path in folder.rglob("*"))
        check = service.remove("hostile", check=True)
        self.assertEqual((check["folder"], check["held"]), (True, None))
        service.remove("hostile", keep_settings=False)
        self.assertFalse((self.root / ".lab" / "catalogs" / "hostile").exists())
        self.assertEqual(sorted(str(path.relative_to(folder)) for path in folder.rglob("*")), before)

    def test_a_working_copy_standing_in_for_a_catalog_is_stopped_with_unlink_not_removed(self):
        fixtures.install(self, "lab-old", kind="folder", store=True)
        for call in (lambda: service.remove("lab-old", check=True), lambda: service.remove("lab-old", keep_settings=True)):
            with self.assertRaises(CatalogError) as raised:
                call()
            self.assertEqual(lines_of(raised.exception), ("linked", 409,
                                                          "This catalog is linked to a folder on this machine. Unlink it from a terminal first."))

    def test_a_catalog_the_person_does_not_have_is_not_found(self):
        for call in (lambda: service.remove("nowhere", check=True), lambda: service.remove("nowhere")):
            with self.assertRaises(paths.NotFound):
                call()

    def test_the_default_catalog_can_be_removed_and_comes_back_as_a_row_that_adds_it(self):
        service.remove("fixture")
        row = next(one for one in service.describe(*project.loaded()) if one.get("id") == "fixture")
        self.assertEqual((row["kind"], row["listed"], row["state"]["key"]), ("suggested", True, "suggested"))

    def test_the_stars_of_its_units_go_with_it(self):
        from lab_ui.library import starred
        starred.star({"addons": {"@partner/ledger": {}}, "setups": {}, "playgrounds": {}}, "addon", "@partner/ledger", True)
        self.assertIn("@partner/ledger", starred.read()["addon"])
        service.remove("partner")
        self.assertNotIn("@partner/ledger", starred.read()["addon"])


class Repairing(Seam):
    """The install made again at the pin from the local store; the store fetched again when it lacks an object."""

    def setUp(self):
        super().setUp()
        self.done = self.installed("partner")
        self.file = self.root / ".lab" / "catalogs" / "partner" / "addons" / "ledger" / "compose.yml"

    def damage(self):
        self.file.chmod(0o644)
        self.file.write_text("services: {}\n", encoding="utf-8")

    def test_a_file_that_changed_is_written_again_and_the_state_is_what_it_was(self):
        original = self.file.read_bytes()
        self.damage()
        row = service.repair("partner")["catalog"]
        self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(row["state"]["key"], "uptodate")
        self.assertIsNone(registry.lock_of("partner")["error"])

    def test_the_settings_beside_the_units_are_not_touched(self):
        env = self.file.parent / ".env"
        env.write_text("A=1\n", encoding="utf-8")
        self.damage()
        service.repair("partner")
        self.assertEqual(env.read_text(encoding="utf-8"), "A=1\n")

    def test_a_cache_that_was_cleared_is_fetched_again_with_no_lock_held_and_the_repair_is_made_again(self):
        original = self.file.read_bytes()
        self.damage()
        shutil.rmtree(registry.store_dir("partner"))
        seen = {}
        real = bare.fetch

        def fetch(*args, **how):
            seen["lock"] = held_elsewhere(registry.LOCK)
            return real(*args, **how)
        self.patch(bare, "fetch", fetch)
        service.repair("partner")
        self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(seen, {"lock": False})

    def test_a_job_that_runs_a_working_copy_a_folder_and_a_catalog_with_nothing_installed_refuse_it(self):
        job = jobs.Job("Start Ledger", [["true"]], key="addon:x", verb="start")
        jobs.JOBS.jobs.append(job)
        jobs.JOBS.current = job
        with self.assertRaises(CatalogError) as raised:
            service.repair("partner")
        self.assertEqual(lines_of(raised.exception), ("job_running", 409, "Wait for Start Ledger."))
        job.done = True
        fixtures.install(self, "lab-old", kind="folder", store=True)
        with self.assertRaises(CatalogError) as raised:
            service.repair("lab-old")
        self.assertEqual(raised.exception.code, "linked")
        fixtures.install(self, "lab-new", kind="folder")
        with self.assertRaises(ValueError):
            service.repair("lab-new")
        fixtures.declare(self, "hostile")
        with self.assertRaises(ValueError):
            service.repair("hostile")

    def test_unlinking_a_working_copy_fetches_again_from_the_address_the_entry_remembers(self):
        shadow = fixtures.install(self, "lab-old", kind="folder", store=True)
        shutil.rmtree(registry.store_dir("lab-old"))
        service.unshadow("lab-old")
        self.assertEqual(registry.entry("lab-old")["kind"], "git")
        self.assertEqual(registry.entry("lab-old")["url"], shadow.remote.url)
        self.assertTrue(registry.store_dir("lab-old").is_dir())
        with self.assertRaises(ValueError):
            service.unshadow("partner")


class Allowing(Seam):
    """The allowance of items that use this machine, and the choice to check by itself."""

    def setUp(self):
        super().setUp()
        self.installed("partner")
        self.patch(trust, "ALLOWANCE", True)

    def test_the_allowance_is_stored_in_the_entry_and_the_row_says_it(self):
        row = service.allow_host("partner", True)["catalog"]
        self.assertIs(registry.entry("partner")["host"], True)
        self.assertEqual(row["host"], "allowed")
        row = service.allow_host("partner", False)["catalog"]
        self.assertNotIn("host", registry.entry("partner"))
        self.assertEqual(row["host"], "no")

    def test_turning_it_off_is_held_while_an_item_that_uses_the_machine_is_on_or_up(self):
        service.allow_host("partner", True)
        real = service.project.loaded
        built = real()[0]
        built["catalogs"] = {"partner": {"uses_machine": [{"id": "@partner/ledger", "name": "Ledger", "service": "x",
                                                           "what": ["runs privileged"], "why": ""}]}}
        self.patch(service.project, "loaded", lambda: (built, real()[1]))
        (self.root / ".lab" / "on.yml").write_text("include:\n  - .lab/catalogs/partner/addons/ledger/compose.yml\n", encoding="utf-8")
        with self.assertRaises(CatalogError) as raised:
            service.allow_host("partner", False)
        self.assertEqual(lines_of(raised.exception), ("host_not_allowed", 409, "Turn off Ledger first."))
        self.up = {"addon:ledger"}
        with self.assertRaises(CatalogError) as raised:
            service.allow_host("partner", False)
        self.assertEqual(lines_of(raised.exception), ("host_not_allowed", 409, "Stop Ledger first."))
        self.assertIs(registry.entry("partner")["host"], True)
        (self.root / ".lab" / "on.yml").write_text("", encoding="utf-8")
        self.up = set()
        self.assertEqual(service.allow_host("partner", False)["catalog"]["host"], "no")

    def test_a_listed_catalog_has_it_by_being_listed_and_nothing_is_written(self):
        remote = self.remotes["partner"]
        self.list_at("partner", remote)
        before = registry.read_config()
        self.assertEqual(service.allow_host("partner", True)["catalog"]["host"], "listed")
        self.assertEqual(service.allow_host("partner", False)["catalog"]["host"], "listed")
        self.assertEqual(registry.read_config(), before)

    def test_it_is_a_boolean_for_a_catalog_the_person_has(self):
        for value in (None, "true", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                service.allow_host("partner", value)
        with self.assertRaises(paths.NotFound):
            service.allow_host("nowhere", True)

    def test_a_folder_catalog_can_be_allowed_too(self):
        fixtures.install(self, "lab-old", kind="folder")
        self.assertEqual(service.allow_host("lab-old", True)["catalog"]["host"], "allowed")

    def test_stop_checking_automatically_is_stored_and_the_row_says_it(self):
        self.assertTrue(self.row("partner")["auto"])
        row = service.set_auto("partner", False)["catalog"]
        self.assertEqual((registry.entry("partner")["auto"], row["auto"]), (False, False))
        self.assertTrue(service.set_auto("partner", True)["catalog"]["auto"])
        for value in (None, "no", 0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                service.set_auto("partner", value)
        with self.assertRaises(paths.NotFound):
            service.set_auto("nowhere", True)

    def test_a_second_change_at_the_same_time_is_busy(self):
        with held_by_another():
            for call in (lambda: service.set_auto("partner", False), lambda: service.allow_host("partner", True),
                         lambda: service.remove("partner"), lambda: service.repair("partner")):
                with self.assertRaises(CatalogError) as raised:
                    call()
                self.assertEqual(raised.exception.code, "busy")
        self.assertTrue(self.row("partner")["auto"])


class Patience(Seam):
    """A change waits a moment for the lock: the checker writes the lock for milliseconds, and that is no other change."""

    def test_a_short_hold_of_the_lock_is_waited_for_and_a_long_one_is_busy(self):
        self.installed("partner")
        taken = threading.Event()

        def hold(seconds):
            with registry.flock():
                taken.set()
                time.sleep(seconds)
        thread = threading.Thread(target=hold, args=(0.3,))
        thread.start()
        taken.wait(5)
        self.assertEqual(service.set_auto("partner", False)["catalog"]["auto"], False)
        thread.join(5)
        self.assertGreaterEqual(service.PATIENCE, 1.0)
        taken.clear()
        thread = threading.Thread(target=hold, args=(1.0,))
        thread.start()
        taken.wait(5)
        with mock.patch.object(service, "PATIENCE", 0.1), self.assertRaises(CatalogError) as raised:
            service.set_auto("partner", True)
        self.assertEqual(raised.exception.code, "busy")
        thread.join(5)


class Starting(Seam):
    """The recovery at the start, and the version of git."""

    def test_the_recovery_runs_under_the_lock_tried_once_and_is_skipped_while_another_change_holds_it(self):
        ran = []
        self.patch(install, "recover", side_effect=lambda: ran.append(True))
        service.recover()
        self.assertEqual(ran, [True])
        with held_by_another():
            service.recover()
        self.assertEqual(ran, [True], "a Lab that is writing is not swept from under")

    def test_a_failure_of_the_recovery_is_not_hidden(self):
        self.patch(install, "recover", side_effect=CatalogError("damaged", "x", 409))
        with self.assertRaises(CatalogError):
            service.recover()

    def test_the_version_of_git_is_asked_once_in_a_while_and_again_when_a_click_may_have_changed_it(self):
        clock = [10.0]
        self.patch(service, "_now", lambda: clock[0])
        asked = self.patch(gitx, "version", return_value=(2, 43, 2))
        self.assertEqual(service.git_version(), (2, 43, 2))
        service.git_version()
        service.git_version()
        self.assertEqual(asked.call_count, 1)
        clock[0] += service.GIT_AGE + 1
        service.git_version()
        self.assertEqual(asked.call_count, 2)
        service.git_version(fresh=True)
        self.assertEqual(asked.call_count, 3)
        asked.return_value = None
        self.assertIsNone(service.git_version(fresh=True))


class Gate(Real):
    """The gate at Start, asked by layers.check for a start that puts a unit of a catalog in the on file."""

    def added(self, ns="partner"):
        remote = self.remote(ns)
        return service.add(service.check_address(remote.url)["ticket"])["catalog"]

    def test_units_that_are_whole_and_not_blocked_pass_and_so_do_this_machines_own_and_a_core_alone(self):
        self.added("partner")
        built = project.loaded()[0]
        service.preflight(built, [("addon", "@partner/ledger"), ("addon", "echo"), ("addon", "nothing-of-that-name")])
        service.preflight(built, [])

    def test_a_unit_the_policy_blocks_now_is_refused_with_its_first_reason(self):
        built = project.loaded()[0]
        with self.assertRaises(RuntimeError) as raised:
            service.preflight(built, [("addon", "future")])
        self.assertEqual(str(raised.exception), "Future is blocked: Needs Lab level 99, this Lab reads 1 to 1")

    def test_an_installed_file_that_changed_after_it_was_installed_is_damaged_in_the_words_of_section_11(self):
        self.added("partner")
        built = project.loaded()[0]
        file = self.root / ".lab" / "catalogs" / "partner" / "addons" / "ledger" / "compose.yml"
        file.chmod(0o644)
        file.write_text(file.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
        with self.assertRaises(RuntimeError) as raised:
            service.preflight(built, [("addon", "@partner/ledger")])
        error = raised.exception
        self.assertIsInstance(error, CatalogError, "it is a RuntimeError for layers.check and a CatalogError for the API")
        self.assertEqual(lines_of(error), ("damaged", 409,
                                           "Ledger changed on disk after it was installed. Repair Partner fixture, then start it again."))
        self.assertEqual(error.detail["files"], ["addons/ledger/compose.yml"])

    def test_the_catalog_file_is_always_checked_since_it_names_the_units_and_the_file_of_another_unit_is_not(self):
        self.added("partner")
        built = project.loaded()[0]
        file = self.root / ".lab" / "catalogs" / "partner" / "catalog.yml"
        file.chmod(0o644)
        file.write_text(file.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            service.preflight(built, [("addon", "@partner/ledger")])
        service.preflight(built, [("addon", "echo")])

    def test_a_catalog_without_a_manifest_the_pin_accepts_is_damaged(self):
        self.added("partner")
        built = project.loaded()[0]
        (self.root / ".lab" / "catalogs" / "partner" / ".installed.json").unlink()
        project.forget()
        with self.assertRaises(RuntimeError) as raised:
            service.preflight(built, [("addon", "@partner/ledger")])
        self.assertEqual(lines_of(raised.exception), ("damaged", 409,
                                                      "Ledger changed on disk after it was installed. Repair Partner fixture, then start it again."))

    def test_a_refusal_is_recorded_so_that_the_row_says_damaged_with_repair_and_a_check_does_not_clear_it(self):
        self.added("partner")
        built = project.loaded()[0]
        file = self.root / ".lab" / "catalogs" / "partner" / "addons" / "ledger" / "compose.yml"
        original = file.read_bytes()
        file.chmod(0o644)
        file.write_text(file.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
        self.assertEqual(self.row("partner")["state"]["key"], "uptodate", "nothing has looked yet")
        with self.assertRaises(CatalogError) as raised:
            service.preflight(built, [("addon", "@partner/ledger")])
        self.assertEqual(raised.exception.code, "damaged")
        row = self.row("partner")
        self.assertEqual((row["state"]["key"], row["state"]["label"], row["attention"], registry.lock_of("partner")["error"]),
                         ("damaged", "Damaged", "repair", "damaged"), "the person is told to Repair and the row offers it")
        row = service.check_now("partner")["catalog"]   # the first check in the background, or Check again
        self.assertEqual((row["state"]["key"], row["attention"]), ("damaged", "repair"))
        self.assertIsNotNone(registry.lock_of("partner")["checked"], "and the check was made all the same")
        row = service.repair("partner")["catalog"]
        self.assertEqual((row["state"]["key"], row["attention"], registry.lock_of("partner")["error"]), ("uptodate", None, None))
        self.assertEqual(file.read_bytes(), original)
        service.preflight(project.loaded()[0], [("addon", "@partner/ledger")])

    def test_a_catalog_without_a_manifest_is_recorded_damaged_too_and_repair_makes_it_whole(self):
        self.added("partner")
        built = project.loaded()[0]
        (self.root / ".lab" / "catalogs" / "partner" / ".installed.json").unlink()
        project.forget()
        with self.assertRaises(CatalogError):
            service.preflight(built, [("addon", "@partner/ledger")])
        self.assertEqual(registry.lock_of("partner")["error"], "damaged")
        self.assertEqual(service.repair("partner")["catalog"]["state"]["key"], "uptodate")
        self.assertIsNone(registry.lock_of("partner")["error"])

    def test_only_a_catalog_that_is_installed_from_git_and_not_under_a_change_is_recorded(self):
        self.added("partner")
        service._mark_damaged("nowhere")
        service._mark_damaged("local")
        service._mark_damaged("not a namespace")
        self.assertEqual(sorted(registry.read_lock()["catalogs"]), ["fixture", "partner"], "no record is made for what is not there")
        registry.set_lock("partner", pending={"op": "update", "from": registry.lock_of("partner")["pin"],
                                              "to": registry.lock_of("partner")["pin"]})
        service._mark_damaged("partner")
        self.assertIsNone(registry.lock_of("partner")["error"], "its files are in flux")

    def test_a_folder_catalog_a_working_copy_and_personal_items_have_no_manifest_to_check(self):
        fixtures.local(self, "scratch")
        built = project.loaded()[0]
        service.preflight(built, [("addon", "local/scratch"), ("addon", "echo"), ("addon", "cluster")])

    def test_the_project_is_judged_again_at_the_gate_since_the_environment_changes_without_a_file(self):
        forgot = []
        real = project.forget
        self.patch(project, "forget", lambda: forgot.append(True) or real())
        service.preflight(project.loaded()[0], [("addon", "echo")])
        self.assertEqual(forgot, [True])


class Reading2(Real):
    """What an update says of the growth of power, read with Compose from the installed version and the new one."""

    def test_a_new_port_and_a_new_privilege_are_lines_of_the_plan_and_the_dialog_can_say_what_the_item_now_does(self):
        self.patch(trust, "ALLOWANCE", True)
        remote = self.remote("partner")
        service.add(service.check_address(remote.url)["ticket"])
        grown = ("x-gravitee: {kind: addon, name: Ledger, category: Other, description: Keeps the entries of a partner, "
                 "host_access: {partner-ledger: Needs the host}}\nservices:\n  partner-ledger:\n    image: busybox:1.36\n"
                 "    privileged: true\n    ports: ['127.0.0.1:8192:80', '127.0.0.1:8193:81']\n    networks: [internal]\n")
        remote.commit({"catalog.yml": meta_text(version="2.1.0"), "addons/ledger/compose.yml": grown}, "Grow the ledger")
        service.check_now("partner")
        plan = service.update("partner", check=True)
        self.assertEqual(plan["updates"], [{"kind": "addon", "id": "@partner/ledger", "name": "Ledger", "what": ["compose.yml"]}])
        facts = [(one["id"], one["name"], one["fact"], one["value"]) for one in plan["changes"]]
        self.assertEqual(facts, [("@partner/ledger", "Ledger", "port", "8193"), ("@partner/ledger", "Ledger", "host", "runs privileged")])
        self.assertEqual([(one["id"], one["what"]) for one in plan["uses_machine"]], [("@partner/ledger", ["runs privileged"])],
                         "the catalog is not allowed: the dialog offers the choice")
        self.assertEqual(plan["blocked"], [])

    def test_an_update_never_raises_power_silently_the_item_that_now_needs_it_is_blocked_while_the_catalog_is_not_allowed(self):
        self.patch(trust, "ALLOWANCE", True)
        remote = self.remote("partner")
        service.add(service.check_address(remote.url)["ticket"])
        grown = ("x-gravitee: {kind: addon, name: Ledger, category: Other, description: Keeps the entries of a partner, "
                 "host_access: {partner-ledger: Needs the host}}\nservices:\n  partner-ledger:\n    image: busybox:1.36\n"
                 "    privileged: true\n    networks: [internal]\n")
        remote.commit({"catalog.yml": meta_text(version="2.1.0"), "addons/ledger/compose.yml": grown}, "Grow the ledger")
        service.check_now("partner")
        service.update("partner", ticket=service.update("partner", check=True)["ticket"])
        unit = project.loaded()[0]["addons"]["@partner/ledger"]
        self.assertIsNotNone(unit["blocked"], "kept blocked in the plan of the project until the person allows it")
        service.allow_host("partner", True)
        self.assertIsNone(project.loaded()[0]["addons"]["@partner/ledger"]["blocked"])


SETUP_BOTH = ("x-gravitee:\n  kind: setup\n  name: Combo\n  description: Ledger and vault\ninclude:\n"
              "  - ../addons/ledger/compose.yml\n  - ../addons/vault/compose.yml\n")
SETUP_LEDGER = ("x-gravitee:\n  kind: setup\n  name: Combo\n  description: Ledger only\ninclude:\n"
                "  - ../addons/ledger/compose.yml\n")
SETUP_OTHER = ("x-gravitee:\n  kind: setup\n  name: Other\n  description: Ledger and vault, as before\ninclude:\n"
               "  - ../addons/ledger/compose.yml\n  - ../addons/vault/compose.yml\n")
VAULT = ("x-gravitee: {kind: addon, name: Vault, category: Other, description: Keeps secrets}\n"
         "services:\n  partner-vault:\n    image: busybox:1.36\n    container_name: gio-partner-vault\n"
         "    command: [httpd, -f, -p, '80']\n    ports: ['127.0.0.1:8193:80']\n    networks: [internal]\n")


class Planning(Real):
    """9.3 with the policy and Compose behind it: what the plan refuses and what it says breaks."""

    def added(self, ns="partner", **patch):
        remote = self.remote(ns, **patch)
        service.add(service.check_address(remote.url)["ticket"])
        return remote

    def test_a_version_written_for_a_lab_this_one_cannot_read_is_refused_by_the_update_as_the_add_refuses_it(self):
        remote = self.added()
        before = (registry.lock_of("partner"), self.digest())
        text = (self.root / ".lab" / "catalogs" / "partner" / "catalog.yml").read_text(encoding="utf-8")
        # The level spelled with an anchor: the reading that picks a version is unsure of it and takes it for readable
        remote.commit({"catalog.yml": text.replace("  lab: 1\n", "  lab: &level 99\n").replace("version: 2.0.0", "version: 3.0.0")},
                      "Needs a newer Lab, spelled with an anchor")
        self.assertEqual(service.check_now("partner")["catalog"]["state"]["key"], "update", "this is what the check can tell")
        with self.assertRaises(CatalogError) as raised:
            service.update("partner", check=True)
        self.assertEqual(lines_of(raised.exception), ("lab_new", 400,
                                                      "Needs Lab level 99, this Lab reads 1 to 1. Run make update, then add it again."))
        self.assertFalse((self.root / ".lab" / "catalogs" / ".stage-partner").exists(), "what was refused is not left to install")
        self.assertEqual(service.TICKETS, {})
        self.assertEqual(self.digest(), before[1])
        held = registry.lock_of("partner")
        self.assertEqual((held["pin"], held["version"], held["lab"]), (before[0]["pin"], "2.0.0", 1))

    def digest(self):
        base = self.root / ".lab" / "catalogs" / "partner"
        return {str(path.relative_to(base)): path.read_bytes() for path in sorted(base.rglob("*")) if path.is_file()}

    def test_an_item_of_the_catalog_that_the_same_release_rewrites_is_not_broken_by_the_unit_it_stopped_including(self):
        remote = self.added(files={"setups/combo.yml": SETUP_BOTH, "addons/vault/compose.yml": VAULT})
        remote.commit({"setups/combo.yml": SETUP_LEDGER}, "Combo drops the vault", remove=["addons/vault"])
        service.check_now("partner")
        plan = service.update("partner", check=True)
        self.assertEqual(plan["removes"], [{"kind": "addon", "id": "@partner/vault", "name": "Vault"}])
        self.assertEqual(plan["updates"], [{"kind": "setup", "id": "@partner/combo", "name": "Combo", "what": ["combo.yml"]}])
        self.assertEqual(plan["breaks"], [], "after the update nothing includes the vault")
        service.update("partner", ticket=plan["ticket"])
        self.assertEqual(self.row("partner")["state"]["key"], "uptodate")

    def test_an_item_of_the_catalog_that_stays_as_it_is_and_still_includes_the_unit_that_goes_is_broken(self):
        remote = self.added(files={"setups/combo.yml": SETUP_BOTH, "setups/other.yml": SETUP_OTHER,
                                   "addons/vault/compose.yml": VAULT})
        remote.commit({"setups/combo.yml": SETUP_LEDGER}, "Combo drops the vault", remove=["addons/vault"])
        service.check_now("partner")
        plan = service.update("partner", check=True)
        self.assertEqual(plan["breaks"], [{"kind": "setup", "id": "@partner/other", "name": "Other", "needs": "Vault"}])

    def test_an_item_the_release_rewrites_but_still_includes_the_unit_that_goes_is_broken_too(self):
        remote = self.added(files={"setups/combo.yml": SETUP_BOTH, "addons/vault/compose.yml": VAULT})
        remote.commit({"setups/combo.yml": SETUP_BOTH.replace("Ledger and vault", "Ledger and vault, reworded")},
                      "Reword the combo, drop the vault", remove=["addons/vault"])
        service.check_now("partner")
        plan = service.update("partner", check=True)
        self.assertEqual(plan["updates"], [{"kind": "setup", "id": "@partner/combo", "name": "Combo", "what": ["combo.yml"]}])
        self.assertEqual(plan["breaks"], [{"kind": "setup", "id": "@partner/combo", "name": "Combo", "needs": "Vault"}])
