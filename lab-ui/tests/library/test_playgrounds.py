"""playgrounds: what a start needs, what the seed recorded, and the jobs that start a playground, seed it again and
stop it, on the small project of test_layers, and on a place besides This machine, an environment of a platform in the
fake Cloud. No Docker: commands are collected, not run."""

import gc
import json
import os
import re
import sys
import threading
import types
import unittest
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from lab_ui.gravitee import cloud, license, places, platforms
from lab_ui.docker import compose, envfiles, jobs, plugins
from lab_ui.library import layers, layout, playgrounds
from lab_ui import paths
from lab_ui.catalogs import trust
from lab_ui.docker.jobs import Job, JobFailed, awaited

from .. import Case, fake_cloud

from ..gravitee import test_places

from ..gravitee import test_platforms
from ..docker.test_jobs import Docker, container, progress, replay
from .test_layers import (API, APPS, GATEWAY, HEALTH_CHECKED, HOTELS, HYBRID, cloud as with_cloud, lab, licensed, on,
                          own, row, second)
from ..gravitee.test_places import DEV_KEY, KEY
from ..gravitee.test_platforms import ADDON_ENV, DEV, TEST

COMPOSE = compose.COMPOSE
CORE = ("apim-gateway", "apim-management-api", "mongodb")
STARTED = "2026-10-03T12:00:00.000000000Z"   # when the seed's container last started, as Docker says it
SEED_LOGS = ["docker", "logs", "-f", "--since", STARTED, "lab-seed"]   # its output since then, followed


def running(*names):
    """What compose.ps() answers when these services run."""
    return {name: {"state": "running", "health": "healthy"} for name in names}


def ran(steps, fail=None):
    """Runs the steps of a job without its commands: (job, the commands). A command that holds `fail` fails."""
    job, commands = Job("Test", steps), []

    def collect(cmd, quiet=False):
        commands.append(cmd)
        if fail in cmd:
            raise JobFailed("Failed with exit code 1")
    job.exec = collect
    job.run()
    return job, commands



def removes(command, seed):
    """Whether this is the run of a playground's seed that deletes what it created: Compose with the core and the
    playground's own files loaded (a playground that is off has no seed service in the files that are on), no service
    it depends on, and --remove."""
    start = len(COMPOSE)
    return (command[:start] == COMPOSE and command[start] == "-f" and command[start + 2] == "-f"
            and command[start + 4:] == ["run", "--rm", "--no-deps", "--pull=missing", seed, "--remove"])


class Playgrounds(Case):

    def setUp(self):
        self.folder(playgrounds, paths, playgrounds.projects)
        (self.root / "playgrounds/welcome").mkdir(parents=True)
        self.project = lab()
        self.project["playgrounds"]["welcome"]["requires"]["plugins"] = {"apim": ["gravitee-policy-x"]}
        self.state, self.containers, self.written = on(self.project), running(*CORE), []
        self.jobs = self.patch(playgrounds, "JOBS")
        self.patch(layers, "current", lambda project: self.state)
        self.patch(layers, "write", lambda playground, explicit, *where, **how: self.written.append((playground, explicit)))
        self.patch(compose, "ps", lambda *variable: self.containers)
        self.patch(compose, "project_name", lambda: "gravitee")
        # What is saved: nothing runs behind it, unless a test says so
        self.saved = self.patch(playgrounds.projects, "saved", return_value={"hashes": {}, "images": {}})
        self.memory = self.patch(compose, "docker_memory", return_value=16 * 2 ** 30)
        # Docker says when the seed last started, and that it ended well
        self.patch(compose, "run", lambda cmd, **how: (0, f"{STARTED}\n" if "{{.State.StartedAt}}" in cmd else "0\n",
                                                        ""))
        self.patch(plugins, "install_steps", lambda project, product, names: (
            [lambda job: job.lines.append(f"Plugins of {product}: {', '.join(names)}")], [], {}))

    def phases(self, job):
        self.assertTrue(job.ok, job.lines)
        return [phase["name"] for phase in job.phases]

    # --- What the page reads -----------------------------------------------------------------------------------------

    def test_plugin_ids_of_an_api_definition(self):
        definition = {"api": {"listeners": [{"entrypoints": [{"type": "http-proxy"}]}],
                              "endpointGroups": [{"type": "llm-proxy", "endpoints": [{"type": "llm-proxy"}]}],
                              "resources": [{"type": "cache-redis"}],
                              "flows": [{"request": [{"policy": "rate-limit"}]}]},
                      "plans": [{"security": {"type": "API_KEY"}}]}
        self.assertEqual(playgrounds._plugin_ids(definition),
                         {"http-proxy", "llm-proxy", "cache-redis", "rate-limit", "api-key"})

    def test_a_playground_of_this_machine_has_its_guide_and_its_record(self):
        folder = self.root / "playgrounds/local/sandbox"
        (folder / ".state").mkdir(parents=True)
        (folder / "compose.yml").write_text("", encoding="utf-8")
        (folder / "guide.md").write_text("# Sandbox\n", encoding="utf-8")
        (folder / ".state/created.json").write_text(json.dumps([{"kind": "api", "label": "APIs"}]), encoding="utf-8")
        self.assertEqual(playgrounds.guide("local/sandbox"), "# Sandbox\n")
        self.assertEqual(playgrounds.created("local/sandbox"), [{"what": "APIs", "count": 1}])
        with self.assertRaises(paths.NotFound):
            playgrounds.guide("local/gone")

    def test_created_is_what_the_seed_recorded_under_the_names_it_gives(self):
        self.assertEqual(playgrounds.created("welcome"), [])
        record = self.root / "playgrounds/welcome/.state/created.json"
        record.parent.mkdir()
        record.write_text(json.dumps([{"kind": "api", "label": "APIs"}, {"kind": "api", "label": "APIs"},
                                      {"kind": "store", "label": "OpenFGA stores"}]), encoding="utf-8")
        self.assertEqual(playgrounds.created("welcome"), [{"what": "APIs", "count": 2},
                                                           {"what": "OpenFGA stores", "count": 1}])
        record.write_text("not json", encoding="utf-8")
        self.assertEqual(playgrounds.created("welcome"), [])

    def test_a_record_that_stands_for_many_objects_counts_them_all(self):
        record = self.root / "playgrounds/welcome/.state/created.json"
        record.parent.mkdir()
        record.write_text(json.dumps([{"kind": "entities", "label": "Customer entities", "count": 8},
                                      {"kind": "entities", "label": "Order entities", "count": 13},
                                      {"kind": "api", "label": "APIs"}]), encoding="utf-8")
        self.assertEqual(playgrounds.created("welcome"), [{"what": "Customer entities", "count": 8},
                                                           {"what": "Order entities", "count": 13},
                                                           {"what": "APIs", "count": 1}])

    def test_a_guide_is_read_as_a_catalogs_file_is(self):
        folder = self.root / "playgrounds/local/sandbox"
        folder.mkdir(parents=True)
        (folder / "compose.yml").write_text("", encoding="utf-8")
        self.assertEqual(playgrounds.guide("local/sandbox"), "", "none")
        guide = folder / "guide.md"
        outside = self.root / "outside.md"
        outside.write_text("# Not mine\n", encoding="utf-8")
        for make in (lambda: os.symlink(outside, guide), lambda: os.mkfifo(guide),
                     lambda: guide.write_text("x" * (1 << 20) + "x", encoding="utf-8"),
                     lambda: guide.write_bytes(b"# \xff\xfe\n"), lambda: guide.mkdir()):
            with self.subTest(make=make):
                if guide.is_dir() and not guide.is_symlink():
                    guide.rmdir()
                elif os.path.lexists(guide):
                    guide.unlink()
                make()
                with self.assertRaisesRegex(ValueError, "^The guide cannot be read: it is not a plain text file of the "
                                                        "playground's folder$"):
                    playgrounds.guide("local/sandbox")

    def test_unknown_playground(self):
        for call in (lambda: playgrounds.check(self.project, "nope"),
                     lambda: playgrounds.start(self.project, "nope", []),
                     lambda: playgrounds.stop(self.project, "nope", False, []),
                     lambda: playgrounds.reseed(self.project, "nope"), lambda: playgrounds.guide("nope")):
            with self.assertRaisesRegex(paths.NotFound, "^No such playground: nope$"):
                call()

    def checks(self, read, status=None):
        """The checks of the welcome with this license read (license.info) and this status (license.status)."""
        self.patch(license, "info", lambda project: read)
        self.patch(license, "status", lambda project: status or {"status": "valid", "digest": "saved"})
        return playgrounds.check(self.project, "welcome")["checks"]

    def test_an_add_on_of_a_catalog_that_is_gone_does_not_break_the_checks(self):
        self.state = on(self.project, ["jade"])
        self.containers = running(*CORE, "jade")
        self.patch(plugins, "installed", lambda project, product: {"bundled": {}, "bundled_error": None, "ext": {}, "feature_by_id": {}})
        read = {"present": True, "readable": True, "valid": True, "expired": False, "features": ["feature-0"], "model_error": None}
        before = self.checks(read)
        self.project["playgrounds"]["welcome"]["addons"].append("@gone/ledger")   # what it includes names an add-on that no catalog holds now
        self.assertEqual(self.checks(read), before, "the note of the row says it needs the catalog; the checks go on")

    def test_check_lines(self):
        self.state = on(self.project, ["jade"])
        self.containers = running(*CORE, "jade")
        present = {"bundled": {}, "bundled_error": None, "ext": {}, "feature_by_id": {}}
        self.patch(plugins, "installed", lambda project, product: present)
        read = {"present": True, "readable": True, "valid": True, "expired": False, "features": ["feature-0"],
                "model_error": None}
        # What needs you is warn; what the start does, stopping Jade too, is auto. The platform first; each line
        # links to what it names, the license to the core's settings
        inspector, jade = {"kind": "addon", "id": "agent-lens"}, {"kind": "addon", "id": "jade"}
        self.assertEqual(self.checks(read), [
            {"name": "Gravitee platform", "state": "ok", "detail": "Running"},
            {"name": "AI Agent Lens", "state": "auto", "detail": "Turned on at start", "target": inspector},
            {"name": "Jade", "state": "auto", "detail": "Stops at start", "target": jade},
            {"name": "Gateway", "state": "auto", "detail": "Restarts at start"},
            {"name": "gravitee-policy-x", "state": "auto", "detail": "Installed at start", "plugin": "gravitee-policy-x"},
            {"name": "feature-1", "state": "warn", "detail": "Not in the license", "file": ".env"}])
        welcome = self.project["playgrounds"]["welcome"]
        # What the images bring is not known: the start pulls them, then sees whether it is there
        present["bundled_error"] = "Docker is not reachable"
        self.assertEqual(playgrounds._plugin_checks(self.project, welcome["requires"]), (
            [{"name": "gravitee-policy-x", "state": "auto", "detail": "Checked at start", "plugin": "gravitee-policy-x"}], set()))
        present["ext"] = {"gravitee-policy-x": {}}
        self.assertEqual(playgrounds._plugin_checks(self.project, welcome["requires"]), (
            [{"name": "gravitee-policy-x", "state": "ok", "detail": "Installed", "plugin": "gravitee-policy-x"}], set()))
        self.patch(license, "info", lambda project: read)
        self.patch(license, "status", lambda project: {"status": "valid", "digest": "saved"})
        read["features"] = ["feature-1"]
        self.assertEqual(playgrounds._license_checks(self.project, welcome), [
            {"name": "License", "state": "ok", "detail": "Every feature used is included"}])
        # No product image to read the license model from: the license cannot tell
        read.update(features=None, model_error="Not known until graviteeio/apim-gateway:4 is pulled")
        self.assertEqual(playgrounds._license_checks(self.project, welcome), [
            {"name": "License", "state": "warn", "detail": "Not known until graviteeio/apim-gateway:4 is pulled"}])

    def test_the_license_in_the_words_of_its_row(self):
        # "Needs a license", "License expired", "Not in the license", "Apply needed": one word per meaning, each
        # opening the core's settings
        welcome = self.project["playgrounds"]["welcome"]
        read = {"present": True, "readable": True, "valid": True, "expired": False, "features": ["feature-1"],
                "model_error": None}
        self.patch(license, "info", lambda project: dict(read))
        status = self.patch(license, "status", return_value={"status": "valid", "digest": "saved"})

        def said(containers=None):
            return [(line["detail"], line.get("file")) for line in
                    playgrounds._license_checks(self.project, welcome, containers)]
        usable = dict(read)
        for change, words in (({"present": False}, "Needs a license"), ({"readable": False}, "Needs a license"),
                              ({"valid": False}, "Needs a license"), ({"expired": True}, "License expired")):
            read.update({**usable, **change})
            self.assertEqual(said(), [(words, ".env")], change)
        read.update(usable)
        # Another license is saved than the one the gateway runs: it is applied once the gateway is made again
        self.project["license_services"] = ["apim-gateway"]
        other = {"apim-gateway": {"state": "running", "variable": "old"}}
        self.assertEqual(said(other), [("Apply needed", ".env")])
        # The page draws it as the badge: why it says as a line, and no value of a license
        self.assertEqual(playgrounds._license_checks(self.project, welcome, other)[0]["reason"],
                         "The license saved differs from the one that runs in " + self.project["services"]["apim-gateway"]["label"] + ".")
        self.assertEqual(said({"apim-gateway": {"state": "running", "variable": "saved"}}),
                         [("Every feature used is included", None)])
        # Without a feature to use, no license is needed
        welcome["requires"] = {}
        read.update(present=False)
        status.return_value = {"status": "none", "digest": "x"}
        self.assertEqual(said(), [("Every feature used is included", None)])

    def test_the_license_features_of_the_apis_of_every_environment_of_a_seed_are_needed(self):
        welcome = self.project["playgrounds"]["welcome"]
        welcome["requires"] = {"license": ["apim-en-message-reactor"]}
        feed = {"api": {"name": "Batch status", "listeners": [{"entrypoints": [{"type": "sse"}]}],
                        "endpointGroups": [{"type": "solace", "endpoints": [{"type": "solace"}]}]}}
        for name, text in {"apim/apis/own.json": {"api": {"name": "Own", "listeners": [{"entrypoints": [{"type": "sse"}]}]}},
                           "environments/novo-nordisk/apis/batch-status.json": feed,
                           "environments/moderna/apis/batch-status.json": feed}.items():
            self.write(f"playgrounds/welcome/seed/{name}", json.dumps(text))
        installed = {"feature_by_id": {"sse": "apim-en-entrypoint-sse"}}
        self.project["products"] = {"API Management": {}}
        self.patch(plugins, "installed", lambda project, product: installed)
        # The endpoint's plugin is not on the gateway yet: its feature is the one the playground declares
        self.assertEqual(playgrounds._features(self.project, welcome), {
            "apim-en-message-reactor": [],
            "apim-en-entrypoint-sse": ["Own: sse", "Batch status: sse"]})
        # Once installed it is found in the APIs of the environments, each API named once
        installed["feature_by_id"]["solace"] = "apim-en-endpoint-solace"
        self.assertEqual(playgrounds._features(self.project, welcome), {
            "apim-en-message-reactor": [],
            "apim-en-entrypoint-sse": ["Own: sse", "Batch status: sse"],
            "apim-en-endpoint-solace": ["Batch status: solace"]})

    def test_a_seed_file_that_is_a_link_or_a_pipe_or_huge_is_no_definition_of_an_api(self):
        welcome = self.project["playgrounds"]["welcome"]
        welcome["requires"] = {}
        sse = json.dumps({"api": {"name": "Own", "listeners": [{"entrypoints": [{"type": "sse"}]}]}})
        self.write("playgrounds/welcome/seed/apim/apis/own.json", sse)
        outside = self.root / "outside.json"
        outside.write_text(sse, encoding="utf-8")
        apis = self.root / "playgrounds/welcome/seed/apim/apis"
        os.symlink(outside, apis / "linked.json")
        os.mkfifo(apis / "piped.json")
        (apis / "huge.json").write_text(" " * (1 << 20) + sse, encoding="utf-8")
        self.patch(plugins, "installed", lambda project, product: {"feature_by_id": {"sse": "apim-en-entrypoint-sse"}})
        self.project["products"] = {"API Management": {}}
        self.assertEqual(playgrounds._features(self.project, welcome), {"apim-en-entrypoint-sse": ["Own: sse"]})

    def test_the_platform_line_comes_first(self):
        # Nothing of it was ever created; then its containers stopped; then it runs
        self.patch(plugins, "installed", lambda project, product: {"bundled": {"gravitee-policy-x": {}},
                                                                    "bundled_error": None, "ext": {}})
        self.containers = {}
        welcome = self.project["playgrounds"]["welcome"]
        self.assertEqual(playgrounds._addon_checks(self.project, welcome)[0],
                         {"name": "Gravitee platform", "state": "auto", "detail": "Created at start"})
        self.containers = {name: {"state": "exited", "exit_code": 143} for name in CORE}
        self.assertEqual(playgrounds._addon_checks(self.project, welcome)[0],
                         {"name": "Gravitee platform", "state": "auto", "detail": "Started at start"})
        # Part of it runs: only what its app needs starts with it, a group stopped that it needs, a group that runs
        # partly; what it does not need stays as it is
        self.containers = {**running("apim-gateway", "apim-management-api"),
                           "mongodb": {"state": "exited", "exit_code": 143}}
        self.assertEqual(playgrounds._addon_checks(self.project, welcome)[0],
                         {"name": "Gravitee platform", "state": "ok", "detail": "Running"})
        self.project["services"]["hello-app"]["depends_on"] = ["apim-gateway", "mongodb"]
        self.assertEqual(playgrounds._addon_checks(self.project, welcome)[0],
                         {"name": "Databases", "state": "auto", "detail": "Started at start"})
        self.containers = {**running("apim-management-api", "mongodb"),
                           "apim-gateway": {"state": "exited", "exit_code": 143}}
        self.assertEqual(playgrounds._addon_checks(self.project, welcome)[:2],
                         [{"name": "API Management", "state": "auto", "detail": "Started at start"},
                          {"name": "AI Agent Lens", "state": "auto", "detail": "Turned on at start",
                           "target": {"kind": "addon", "id": "agent-lens"}}])

    def test_an_add_on_on_says_whether_it_runs(self):
        # On and running: Running; on and not running: Started at start; off: Turned on at start
        welcome = self.project["playgrounds"]["welcome"]
        welcome["addons"] = ["agent-lens", "kiosk"]
        self.state = on(self.project, ["kiosk", "agent-lens"])
        self.containers = running(*CORE, "kiosk")
        lines = playgrounds._addon_checks(self.project, welcome)
        self.assertEqual([(line["name"], line["state"], line["detail"]) for line in lines[1:3]],
                         [("AI Agent Lens", "auto", "Started at start"), ("Kiosk", "ok", "Running")])

    def test_a_plugin_install_lists_what_it_restarts(self):
        # Installing a plugin stops the product that runs, to load it, then starts it again: said first
        self.project["products"] = {"apim": {"services": {"apim-management-api": "/p", "apim-gateway": "/p"}}}
        self.patch(plugins, "installed", lambda project, product: {"bundled": {}, "bundled_error": None, "ext": {},
                                                                    "feature_by_id": {}})
        self.patch(license, "info", lambda project: {"present": False})
        self.patch(license, "status", lambda project: {"status": "none", "digest": "x"})
        self.project["playgrounds"]["welcome"]["requires"]["license"] = []
        checks = playgrounds.check(self.project, "welcome")["checks"]
        self.assertEqual([line["name"] for line in checks if line["detail"] == "Restarts at start"],
                         ["Gateway", "Management API"])

    def test_its_start_says_what_installing_its_plugins_restarts(self):
        # What a plugin install stops to load it, then starts again, only the checks read: their dialog says it with
        # the rest of what the start does, also when that alone would not ask
        welcome = self.project["playgrounds"]["welcome"]
        welcome.update(addons=[], includes=[], restarts=[], requires={"plugins": {"apim": ["gravitee-policy-x"]}})
        self.project["products"] = {"apim": {"services": {"apim-management-api": "/p", "apim-gateway": "/p"}}}
        present = {"bundled": {}, "bundled_error": None, "ext": {}, "feature_by_id": {}}
        self.patch(plugins, "installed", lambda project, product: present)
        self.patch(license, "info", lambda project: {"present": False})
        self.patch(license, "status", lambda project: {"status": "none", "digest": "x"})
        self.assertEqual(playgrounds.check(self.project, "welcome")["dialog"],
                         {"starts": [APPS], "stops": [], "turns_off": [], "keeps": [], "restarts": [GATEWAY, API]})
        present["ext"] = {"gravitee-policy-x": {}}
        self.assertIsNone(playgrounds.check(self.project, "welcome")["dialog"])
        # Set up, its start runs it again without installing anything: what is missing says so, nothing restarts
        present["ext"] = {}
        self.record("APIs")
        self.state = on(self.project, playground="welcome")
        found = playgrounds.check(self.project, "welcome")
        self.assertIn({"name": "gravitee-policy-x", "state": "warn", "detail": "Not installed", "plugin": "gravitee-policy-x"}, found["checks"])
        self.assertEqual(([line for line in found["checks"] if line["detail"] == "Restarts at start"],
                          found["dialog"]), ([], None))

    def test_an_image_older_than_it_needs_says_what_to_choose(self):
        # Said before the start, which would otherwise end on its seed after the whole setup
        self.patch(playgrounds.images, "components", lambda project: [
            {"id": "APIM_VERSION", "detail": "API Management", "owner": {"name": "API Management"},
             "services": ["apim-gateway"]}])
        self.project["services"]["apim-gateway"]["tag"] = "4.12.20"
        requires = {"requires": {"images": {"APIM_VERSION": "4.13"}}, "tested": []}
        self.assertEqual(list(playgrounds._image_checks(self.project, requires)), [
            {"name": "API Management 4.13", "state": "warn", "detail": "Current: 4.12.20",
             "help": "Choose 4.13 or later on the Images page", "variable": "APIM_VERSION"}])
        # A preview is before its release: not enough, unless the playground lists it among the versions it was tested with
        self.project["services"]["apim-gateway"]["tag"] = "4.13.0-alpha.5"
        previous = [{"name": "API Management 4.13", "state": "warn", "detail": "Current: 4.13.0-alpha.5",
                     "help": "Choose 4.13 or later on the Images page", "variable": "APIM_VERSION"}]
        self.assertEqual(list(playgrounds._image_checks(self.project, requires)), previous)
        listed = {**requires, "tested": [{"APIM_VERSION": "4.13.0-alpha.5"}]}
        self.assertEqual(list(playgrounds._image_checks(self.project, listed)), [
            {"name": "API Management 4.13", "state": "ok", "detail": "4.13.0-alpha.5", "variable": "APIM_VERSION"}])
        self.project["services"]["apim-gateway"]["tag"] = "4.13.0"
        self.assertEqual(list(playgrounds._image_checks(self.project, requires)), [
            {"name": "API Management 4.13", "state": "ok", "detail": "4.13.0", "variable": "APIM_VERSION"}])

    def test_a_version_that_is_not_a_release_warns_unless_the_playground_names_it(self):
        self.patch(playgrounds.images, "components", lambda project: [
            {"id": "APIM_VERSION", "detail": "API Management", "owner": {"name": "API Management"},
             "services": ["apim-gateway"]}])
        gateway = self.project["services"]["apim-gateway"]
        gateway.update(raw_image="graviteeio/apim-gateway:${APIM_VERSION:-4.12.20}", tag="4.13.0-alpha.5")
        requires = {"requires": {"images": {"APIM_VERSION": "4.12"}}, "tested": []}
        self.assertEqual(list(playgrounds._image_checks(self.project, requires)), [
            {"name": "API Management 4.12", "state": "warn", "detail": "Not tested: 4.13.0-alpha.5",
             "help": "Choose a release on the Images page", "variable": "APIM_VERSION"}])
        gateway["tag"] = "4.13.0"
        self.assertEqual(list(playgrounds._image_checks(self.project, requires))[0]["state"], "ok")

    def test_an_image_it_was_never_tested_with_says_which_it_was(self):
        self.patch(playgrounds.images, "components", lambda project: [
            {"id": "APIM_VERSION", "detail": "API Management", "owner": {"name": "API Management"},
             "services": ["apim-gateway"]}])
        gateway = self.project["services"]["apim-gateway"]
        gateway.update(raw_image="graviteeio/apim-gateway:${APIM_VERSION:-4.12.20}", tag="amp-demo-latest")
        playground = {"requires": {"images": {"APIM_VERSION": "4.13.0-alpha.3"}},
                      "tested": [{"APIM_VERSION": "4.13.0-alpha.3"}]}
        self.assertEqual(list(playgrounds._image_checks(self.project, playground)), [
            {"name": "API Management 4.13.0-alpha.3", "state": "warn", "detail": "Not tested: amp-demo-latest",
             "help": "Tested with 4.13.0-alpha.3", "variable": "APIM_VERSION"}])
        gateway["tag"] = "4.13.0-alpha.3"
        self.assertEqual(list(playgrounds._image_checks(self.project, playground))[0]["state"], "ok")
        # An image it asks no minimum of is a line of its own once it was not tested
        gateway["tag"] = "latest"
        self.assertEqual(list(playgrounds._image_checks(self.project, {"requires": {}, "tested": playground["tested"]})), [
            {"name": "API Management", "state": "warn", "detail": "Not tested: latest",
             "help": "Tested with 4.13.0-alpha.3", "variable": "APIM_VERSION"}])

    def test_a_license_needed_for_a_part_of_the_guide_says_which(self):
        # ACME Hello needs one for its authorization step only: its check says so, the whole of it when one is
        # needed everywhere. The line is named for what needs the license (the feature's id while the license model
        # is not known), so that it does not say "License" and "Needs a license"
        welcome = self.project["playgrounds"]["welcome"]
        self.patch(plugins, "installed", lambda project, product: {"bundled": {}, "bundled_error": None, "ext": {},
                                                                    "feature_by_id": {}})
        self.patch(license, "info", lambda project: {"present": False})
        welcome["requires"] = {"license": {"feature-1": "Step 4"}}
        self.assertEqual(playgrounds._license_checks(self.project, welcome),
                         [{"name": "feature-1", "state": "warn", "detail": "Needs a license for Step 4", "file": ".env"}])
        welcome["requires"] = {"license": ["feature-1"]}
        self.assertEqual(playgrounds._license_checks(self.project, welcome)[0]["detail"], "Needs a license")

    def test_a_version_is_compared_by_its_numbers(self):
        # A preview is before its release (semantic versioning): 4.13.0-alpha.5 is not a 4.13, a release of it or a later version is
        for tag, wanted, enough in (("4.12.20", "4.13", False), ("4.13.0-alpha.2", "4.13", False), ("4.13.0-alpha.5", "4.13.0", False),
                                    ("4.13", "4.13", True), ("4.13.0", "4.13", True), ("4.13.7", "4.13.0", True),
                                    ("4.14.0-alpha.1", "4.13", True), ("5.0", "4.13", True), ("4", "4.13", False), ("latest", "4.13", True)):
            self.assertEqual(layers.at_least(tag, wanted), enough, tag)

    def test_a_preview_is_ordered_when_one_is_wanted(self):
        for tag, enough in (("4.13.0-alpha.2", False), ("4.13.0-alpha.3", True), ("4.13.0-alpha.10", True),
                            ("4.13.0-alpha.3-debian", True), ("4.13.0-beta.1", True), ("4.13.0", True),
                            ("4.13.1-alpha.1", True), ("4.12.20", False), ("latest", True)):
            self.assertEqual(layers.at_least(tag, "4.13.0-alpha.3"), enough, tag)

    def test_a_build_that_is_no_release_is_met_by_that_build_only(self):
        """requires.images may name a build (amp-demo-latest, master-latest): it has no numbers to order, so the saved tag is that
        build or it is not. It once raised a TypeError that no page could draw."""
        for tag, wanted, enough in (("amp-demo-latest", "amp-demo-latest", True), ("amp-demo-latest-debian", "amp-demo-latest", True),
                                    ("4.12.20", "amp-demo-latest", False), ("latest", "amp-demo-latest", False),
                                    ("master-latest", "amp-demo-latest", False), ("master-latest", "master-latest", True)):
            self.assertEqual(layers.at_least(tag, wanted), enough, (tag, wanted))
        self.assertEqual((layers.or_later("4.13"), layers.or_later("amp-demo-latest")), (" or later", ""))

    def test_docker_memory_when_it_is_short(self):
        self.patch(plugins, "installed", lambda project, product: {"bundled": {"gravitee-policy-x": {}},
                                                                    "bundled_error": None, "ext": {},
                                                                    "feature_by_id": {}})
        self.memory.return_value = 6 * 2 ** 30
        checks = self.checks({"present": True, "readable": True, "valid": True, "expired": False,
                              "features": ["feature-1"], "model_error": None})
        self.assertIn({"name": "Docker memory", "state": "warn", "detail": "6.0 GB, 8 GB+ recommended"}, checks)
        self.memory.return_value = 16 * 2 ** 30
        self.assertNotIn("Docker memory", [line["name"] for line in self.checks({"present": False})])

    def test_an_add_on_without_its_settings_needs_you(self):
        # It keeps the start from running, whether it is on or not; its line opens its settings
        welcome = self.project["playgrounds"]["welcome"]
        welcome["addons"] = ["agent-lens", "hybrid"]
        self.state = on(self.project, ["hybrid"], missing=["hybrid"])
        hybrid = {"kind": "addon", "id": "hybrid"}
        self.assertEqual(playgrounds._addon_checks(self.project, welcome)[1:3], [
            {"name": "Hybrid gateway", "state": "warn", "detail": "Needs settings", "target": hybrid,
             "file": "addons/hybrid/.env"},
            {"name": "AI Agent Lens", "state": "auto", "detail": "Turned on at start",
             "target": {"kind": "addon", "id": "agent-lens"}}])
        self.state = on(self.project, ["hybrid"])
        self.assertEqual(playgrounds._addon_checks(self.project, welcome)[1:3], [
            {"name": "AI Agent Lens", "state": "auto", "detail": "Turned on at start",
             "target": {"kind": "addon", "id": "agent-lens"}},
            {"name": "Hybrid gateway", "state": "auto", "detail": "Started at start", "target": hybrid}])

    # --- Start -------------------------------------------------------------------------------------------------------

    def test_start_is_refused_when_it_is_not_what_the_page_showed(self):
        self.state = on(self.project, ["jade"])
        self.containers = running(*CORE, "jade")
        with self.assertRaisesRegex(RuntimeError, "Would also stop Jade"):
            playgrounds.start(self.project, "welcome", [])
        self.state = on(self.project, ["jade"], missing=["agent-lens"])
        with self.assertRaisesRegex(RuntimeError, "AI Agent Lens needs settings"):
            playgrounds.start(self.project, "welcome", ["addon:jade"])
        self.jobs.start.assert_not_called()
        self.assertEqual(self.written, [])

    def test_start(self):
        self.state = on(self.project, ["jade"])
        self.containers = running(*CORE, "jade")
        # The plugin step stops the product to load what it installs: it shows as starting
        downloads = {("Download", "gravitee-policy-x"): "download", ("Container", "lab-apim-management-api"): "stop"}
        self.patch(plugins, "install_steps", lambda project, product, names: (
            [lambda job: job.lines.append(f"Plugins of {product}: {', '.join(names)}")], ["apim-management-api"],
            downloads))
        kept = self.patch(playgrounds.tested, "record")
        playgrounds.start(self.project, "welcome", ["addon:jade"])
        title, steps, more = self.started(self.jobs)
        self.assertEqual((title, more["key"], more["verb"]), ("Start Welcome", "playground:welcome", "start"))
        self.assertEqual(more["phases"], ["Plugins", "Apps and services", "Seed"])
        self.assertEqual(more["changes"], {
            "start": [["playground", "welcome"], ["addon", "agent-lens"], ["service", "apim-gateway"],
                      ["service", "apim-management-api"]],
            "stop": [["addon", "jade"]]})
        # The apps' phase waits for what it removes and what it starts, the seed until it ended well; following the
        # seed has nothing to count
        services = self.project["services"]
        self.assertEqual(more["waits"], [downloads, awaited(services, stop=["jade"], start=[
            "agent-lens", "hello-app", "seed", "apim-gateway", "apim-management-api"]), None])
        self.assertEqual(more["waits"][1][("Container", "lab-seed")], "once")
        job, commands = ran(steps)
        self.assertEqual(self.phases(job), ["Add-ons", "Plugins", "Apps and services", "Seed"])
        self.assertEqual(self.written, [("welcome", [])])
        # What goes is removed by name; what starts is named, with the product stopped to load its plugins: nothing
        # else is started or removed
        self.assertEqual(commands, [[*COMPOSE, "-p", "gravitee", "rm", "--stop", "--force", "jade"],
                                    [*COMPOSE, "up", "-d", "--no-deps", "agent-lens", "apim-gateway",
                                     "apim-management-api", "hello-app", "seed"],
                                    SEED_LOGS])
        self.assertEqual([line for line in job.lines if not line.startswith("▸")],
                         ["Stops: Jade", "Add-ons: AI Agent Lens", "Plugins of apim: gravitee-policy-x"])
        self.assertTrue((self.root / "playgrounds/welcome/.state").is_dir())   # where the seed records
        kept.assert_called_once_with(self.project, "welcome")   # once all of it ended well: the versions it worked on

    def test_what_a_start_that_failed_was_on_is_not_kept(self):
        kept = self.patch(playgrounds.tested, "record")
        self.patch(plugins, "install_steps", lambda project, product, names: ([], [], {}))
        playgrounds.start(self.project, "welcome", [])
        _, steps, _ = self.started(self.jobs)
        job, _ = ran(steps, fail="up")
        self.assertFalse(job.ok)
        kept.assert_not_called()

    def test_its_phase_says_the_platform_when_the_platform_starts_too(self):
        # A fresh clone: the start creates the platform too
        self.patch(plugins, "install_steps", lambda project, product, names: ([], [], {}))
        self.containers = {}
        playgrounds.start(self.project, "welcome", [])
        self.assertEqual(self.started(self.jobs)[2]["phases"], ["Platform, apps and services", "Seed"])
        self.assertEqual(self.phases(ran(self.started(self.jobs)[1])[0]),
                         ["Add-ons", "Plugins", "Platform, apps and services", "Seed"])

    def test_start_with_no_plugin_to_install_does_not_count_the_phase(self):
        self.patch(plugins, "install_steps", lambda project, product, names: ([], [], {}))
        playgrounds.start(self.project, "welcome", [])
        _, steps, more = self.started(self.jobs)
        self.assertEqual(more["phases"], ["Apps and services", "Seed"])
        self.assertEqual([waits and set(waits) for waits in more["waits"]], [
            {("Container", f"lab-{name}") for name in ("agent-lens", "hello-app", "seed", "apim-gateway")}, None])
        self.assertEqual(self.phases(ran(steps)[0]), ["Add-ons", "Plugins", "Apps and services", "Seed"])

    def test_start_is_measured_phase_by_phase(self):
        # The plugin it lacks, downloaded; then what the apps' `up` starts, the seed until it ended well; then the
        # seed followed, which says nothing until it is over. Each phase a third of the job
        def install(job):
            job.unit(("Download", "gravitee-policy-x"), 1)
        self.patch(plugins, "install_steps", lambda project, product, names: (
            [install], [], {("Download", "gravitee-policy-x"): "download"}))
        playgrounds.start(self.project, "welcome", [])
        title, steps, more = self.started(self.jobs)
        job, docker = Job(title, steps, **more), Docker(self.project, {name: container(health="healthy")
                                                                       for name in CORE}, HEALTH_CHECKED)
        up = [" Container lab-agent-lens Created ", " Container lab-hello-app Created ",
              " Container lab-apim-gateway Recreated ", " Container lab-agent-lens Started ",
              " Container lab-hello-app Started ", " Container lab-apim-gateway Started ",
              " Container lab-seed Created ", " Container lab-seed Started "]
        logs = ["[seed] Seeding the playground", "[seed] ✓ Playground ready"]
        self.assertEqual(progress(replay(job, up, logs, refresh=docker.refresh)),
                         [0.35, 0.367, 0.383, 0.45, 0.517, 0.542, 0.558, 0.583, 0.583, 0.583])
        # The seed's output ended: the gateway's health check and the seed's end are what Docker says next
        self.assertEqual((job.ok, round(job.progress, 3)), (True, 0.917))
        docker.healthy("apim-gateway")
        docker.containers["seed"] = container("exited", code=0)
        docker.observe(job)
        self.assertEqual((job.progress, [phase["progress"] for phase in job.summary()["phases"]]), (1, [1, 1, 1, 1]))

    def test_start_again_marks_the_playground_as_starting(self):
        # Never seeded, its apps running: only its seed starts, and the playground says that it starts
        self.state = on(self.project, playground="welcome")
        self.containers = running(*CORE, "agent-lens", "hello-app")
        playgrounds.start(self.project, "welcome", [])
        self.assertEqual(self.started(self.jobs)[2]["changes"],
                         {"start": [["service", "seed"], ["playground", "welcome"]], "stop": []})

    def test_a_seed_that_failed_runs_again_at_its_start(self):
        # On, it recorded what it created, then its seed exited (1): its Start sets it up again, seed included, and
        # follows only this run of the seed
        self.record("APIs")
        self.state = on(self.project, playground="welcome")
        self.containers = {**running(*CORE, "agent-lens", "hello-app"), "seed": {"state": "exited",
                                                                                     "exit_code": 1}}
        self.patch(plugins, "install_steps", lambda project, product, names: ([], [], {}))
        playgrounds.start(self.project, "welcome", [])
        title, steps, more = self.started(self.jobs)
        self.assertEqual((title, more["phases"]), ("Start Welcome", ["Apps and services", "Seed"]))
        job, commands = ran(steps)
        self.assertEqual(commands, [[*COMPOSE, "up", "-d", "--no-deps", "seed"], SEED_LOGS])
        # It ended well: its start runs it again as an add-on's, without its seed
        self.containers["seed"] = {"state": "exited", "exit_code": 0}
        jobs = self.patch(layers, "JOBS")
        playgrounds.start(self.project, "welcome", [])
        self.assertTrue(jobs.start.called)

    def plan(self):
        """What the playground's Start says now, as its button's dialog does."""
        return playgrounds._start_plan(self.project, self.state, "welcome", playgrounds._now(self.project))

    def test_a_start_that_failed_restarts_the_gateway_when_it_starts_again(self):
        # Demo runs; the playground's start turns it on, removes Demo's apps, then its build fails: the gateway still runs
        # as Demo had it. Started again, the welcome is on already, yet its start restarts the gateway it configures
        second(self.project)
        self.state = on(self.project, playground="demo")
        self.containers = running(*CORE, "demo-app", "kiosk")
        self.patch(plugins, "install_steps", lambda project, product, names: ([], [], {}))
        first = self.plan()
        self.assertEqual(first["restarts"], [GATEWAY])
        playgrounds.start(self.project, "welcome", [f"{row['kind']}:{row['id']}" for row in first["stops"]])
        job, commands = ran(self.started(self.jobs)[1], fail="up")
        self.assertFalse(job.ok)
        self.assertEqual(self.written, [("welcome", [])])
        # What Docker says now: the on file names the welcome, the gateway runs with what Demo configured
        self.state = on(self.project, playground="welcome")
        self.containers = {**running(*CORE), "apim-gateway": {**running("x")["x"], "config_hash": "demo"}}
        self.saved.return_value = {"hashes": {"apim-gateway": "welcome"}, "images": {}}
        self.assertEqual(self.plan()["restarts"], [GATEWAY])
        playgrounds.start(self.project, "welcome", [])
        _, steps, more = self.started(self.jobs)
        self.assertIn(["service", "apim-gateway"], more["changes"]["start"])
        self.assertEqual(ran(steps)[1][0], [*COMPOSE, "up", "-d", "--no-deps", "agent-lens",
                                            "apim-gateway", "hello-app", "seed"])

    def test_a_start_that_switched_the_versions_makes_again_the_platform_that_runs_behind_them(self):
        """Without it a Start takes what the playground configures only, and the rest of the platform stays on the versions it had."""
        self.patch(plugins, "install_steps", lambda project, product, names: ([], [], {}))
        self.state = on(self.project, playground="welcome")
        self.containers = {**running(*CORE), "mongodb": {**running("x")["x"], "config_hash": "old"}}
        self.saved.return_value = {"hashes": {"mongodb": "saved"}, "images": {}}
        playgrounds.start(self.project, "welcome", [])
        self.assertNotIn(["service", "mongodb"], self.started(self.jobs)[2]["changes"]["start"], "a plain start leaves it to the Stack")
        playgrounds.start(self.project, "welcome", [], switched=True)
        self.assertIn(["service", "mongodb"], self.started(self.jobs)[2]["changes"]["start"])

    def test_jobs_are_named_as_their_buttons(self):
        # Turned on while its app runs outside the Lab UI; turned off while nothing of it runs
        self.containers = running(*CORE, "agent-lens", "hello-app")
        playgrounds.start(self.project, "welcome", [])
        self.assertEqual(self.started(self.jobs)[0], "Turn on Welcome")
        self.state = on(self.project, playground="welcome")
        self.containers = running(*CORE)
        playgrounds.stop(self.project, "welcome", False, ["addon:agent-lens"])
        self.assertEqual(self.started(self.jobs)[0], "Turn off Welcome")

    def record(self, *labels):
        """What the playground's seed recorded as created."""
        record = self.root / "playgrounds/welcome" / playgrounds.CREATED_FILE
        record.parent.mkdir(exist_ok=True)
        record.write_text(json.dumps([{"kind": "api", "label": label} for label in labels]), encoding="utf-8")
        return record

    def test_start_again_runs_it_like_an_add_on(self):
        # On and seeded, its platform stopped: what of it and of the platform does not run starts, by name, without a
        # build, its plugins or its seed; Re-seed seeds it again
        self.record("APIs")
        self.state = on(self.project, playground="welcome")
        self.containers = {}
        jobs = self.patch(layers, "JOBS")
        playgrounds.start(self.project, "welcome", [])
        self.jobs.start.assert_not_called()
        title, steps, more = self.started(jobs)
        self.assertEqual((title, more["key"], more["verb"]), ("Start Welcome", "playground:welcome", "start"))
        job, commands = ran(steps)
        self.assertEqual(commands, [[*COMPOSE, "up", "-d", "--no-deps", "agent-lens", "apim-gateway",
                                     "apim-management-api", "hello-app", "mongodb"]])
        self.assertEqual(self.written, [("welcome", [])])

    def test_start_leaves_what_was_started_outside_the_lab_ui(self):
        # Kiosk runs without being on: the playground's start neither stops nor removes it
        self.containers = running(*CORE, "kiosk")
        playgrounds.start(self.project, "welcome", [])
        job, commands = ran(self.started(self.jobs)[1])
        self.assertNotIn("kiosk", [word for command in commands for word in command])
        self.assertNotIn("--remove-orphans", [word for command in commands for word in command])

    def test_a_seed_that_fails_fails_the_job_with_its_last_line(self):
        self.patch(compose, "run", lambda cmd, **how: (0, "1\n", ""))
        follow = playgrounds._follow_seed(self.project, self.project["playgrounds"]["welcome"])
        for printed, error in ((["[seed] Seeding the playground", "[seed] ✗ No such store: hello", ""],
                                "No such store: hello"),
                               (["Traceback (most recent call last):", "KeyError: 'id'"], "KeyError: 'id'"),
                               ([], "The seed failed")):
            job = Job("Test", [follow])
            job.exec = lambda cmd, quiet=False: job.lines.extend(printed)
            job.run()
            self.assertEqual((job.ok, job.error()), (False, error))

    def test_reseed_waits_for_what_the_seed_needs(self):
        # The seed would wait for the management API, then fail: it is refused with the reason the page shows
        self.project["services"]["seed"]["depends_on"] = ["apim-management-api"]
        self.state = on(self.project, playground="welcome")
        self.containers = running("mongodb", "agent-lens", "hello-app")
        with self.assertRaisesRegex(RuntimeError, "^Management API is not running$"):
            playgrounds.reseed(self.project, "welcome")
        self.record("APIs")
        with self.assertRaisesRegex(RuntimeError, "^Management API is not running$"):
            playgrounds.stop(self.project, "welcome", True, ["addon:agent-lens"])
        # Nothing recorded, nothing to delete: it stops
        self.record()
        playgrounds.stop(self.project, "welcome", True, ["addon:agent-lens"])
        self.containers = running(*CORE)
        playgrounds.reseed(self.project, "welcome")
        self.assertEqual(self.jobs.start.call_count, 2)

    def test_reseed_runs_the_seed_of_the_playground_that_is_on(self):
        with self.assertRaisesRegex(RuntimeError, "Welcome is off"):
            playgrounds.reseed(self.project, "welcome")
        self.state = on(self.project, playground="welcome")
        kept = self.patch(playgrounds.tested, "record")
        playgrounds.reseed(self.project, "welcome")
        title, steps, more = self.started(self.jobs)
        self.assertEqual((title, more["key"], more["verb"], more["changes"]),
                         ("Re-seed Welcome", "playground:welcome", "reseed", None))
        # Its container made again is what Compose measures; the seed runs after it
        self.assertEqual(more["phases"], ["Start the seed", "Seed"])
        job, commands = ran(steps)
        self.assertEqual(self.phases(job), more["phases"])
        self.assertEqual(commands, [[*COMPOSE, "up", "-d", "--force-recreate", "--no-deps", "seed"],
                                    SEED_LOGS])
        self.assertEqual(self.written, [])
        kept.assert_called_once_with(self.project, "welcome")   # the seed ended well: it works on the versions it runs on

    def test_reseed_is_there_once_the_seed_ended_well(self):
        # Its image built and its container made again, which Compose prints; the container is ready once the seed
        # ended well, which Docker says once its output ends. Following it is the second half, counted once over
        self.state = on(self.project, playground="welcome")
        playgrounds.reseed(self.project, "welcome")
        title, steps, more = self.started(self.jobs)
        self.assertEqual(more["waits"], [{("Container", "lab-seed"): "once"}, None])
        job, docker = Job(title, steps, **more), Docker(self.project, {"seed": container("exited", code=0)})
        up = [" Image gravitee-lab/hello-seed Building ", " Image gravitee-lab/hello-seed Built ",
              " Container lab-seed Recreate ", " Container lab-seed Recreated ", " Container lab-seed Started "]
        logs = ["[seed] Seeding the playground", "[seed] ✓ Playground ready"]
        self.assertEqual(progress(replay(job, up, logs, refresh=docker.refresh)), [0, 0.25, 0.25, 0.3, 0.375,
                                                                                  0.375, 0.375])
        self.assertEqual((job.ok, job.progress), (True, 0.875))
        docker.containers["seed"] = container("exited", code=0)
        docker.observe(job)
        self.assertEqual(job.progress, 1)

    # --- Stop --------------------------------------------------------------------------------------------------------

    def on_and_running(self, *core):
        self.state = on(self.project, ["kiosk"], "welcome")
        self.containers = running(*core, "agent-lens", "hello-app", "kiosk")

    def test_stop(self):
        self.on_and_running(*CORE)
        with self.assertRaisesRegex(RuntimeError, "Would also stop AI Agent Lens"):
            playgrounds.stop(self.project, "welcome", False, [])
        playgrounds.stop(self.project, "welcome", False, ["addon:agent-lens"])
        title, steps, more = self.started(self.jobs)
        self.assertEqual((title, more["key"], more["verb"], more["changes"], more["phases"]), (
            "Stop Welcome", "playground:welcome", "stop",
            {"start": [["service", "apim-gateway"]],
             "stop": [["playground", "welcome"], ["addon", "agent-lens"]]},
            ["Stop the apps"]))
        # What it removes by name, its seed too once it ran, then the gateway made again without its settings
        self.assertEqual(more["waits"], [awaited(self.project["services"], start=["apim-gateway"],
                                                 stop=["hello-app", "agent-lens"])])
        job, commands = ran(steps)
        self.assertEqual(self.phases(job), ["Stop the apps"])
        self.assertEqual(self.written, [(None, ["kiosk"])])
        self.assertEqual(commands, [[*COMPOSE, "-p", "gravitee", "rm", "--stop", "--force", "agent-lens",
                                     "hello-app"],
                                    [*COMPOSE, "up", "-d", "--no-deps", "apim-gateway"]])
        self.containers["seed"] = {"state": "exited", "exit_code": 0}
        playgrounds.stop(self.project, "welcome", False, ["addon:agent-lens"])
        self.assertIn("seed", ran(self.started(self.jobs)[1])[1][0])

    def test_stop_while_the_platform_is_not_up_only_removes(self):
        self.on_and_running()
        playgrounds.stop(self.project, "welcome", False, ["addon:agent-lens"])
        job, commands = ran(self.started(self.jobs)[1])
        self.assertEqual(self.phases(job), ["Stop the apps"])
        self.assertEqual(commands, [[*COMPOSE, "-p", "gravitee", "rm", "--stop", "--force", "agent-lens",
                                     "hello-app"]])
        self.assertEqual(self.written, [(None, ["kiosk"])])

    def test_stop_and_delete_deletes_first(self):
        self.on_and_running(*CORE)
        self.patch(playgrounds, "created", lambda pid: [{"what": "APIs", "count": 1}])
        playgrounds.stop(self.project, "welcome", True, ["addon:agent-lens"])
        _, steps, more = self.started(self.jobs)
        job, commands = ran(steps)
        self.assertEqual(self.phases(job), ["Delete what it created", "Stop the apps"])
        self.assertEqual(more["phases"], self.phases(job))
        self.assertEqual([waits and len(waits) for waits in more["waits"]], [None, 3])   # the delete says nothing
        self.assertTrue(removes(commands[0], "seed"), commands[0])
        self.assertEqual(self.written, [(None, ["kiosk"])])
        # When the delete fails the job ends there: the on file is not written, nothing stops
        self.written.clear()
        job, commands = ran(steps, fail="--remove")
        self.assertEqual((job.ok, len(commands), self.written), (False, 1, []))

    def test_a_delete_that_fails_gives_the_seed_s_reason(self):
        self.on_and_running(*CORE)
        self.patch(playgrounds, "created", lambda pid: [{"what": "APIs", "count": 1}])
        playgrounds.stop(self.project, "welcome", True, ["addon:agent-lens"])
        job = Job("Test", self.started(self.jobs)[1])

        def failing(cmd, quiet=False):   # what exec does: the command's last line is the reason
            job.lines.append("[seed] ✗ Cannot reach the management API")
            raise JobFailed(job.lines[-1])
        job.exec = failing
        job.run()
        self.assertEqual((job.ok, job.error()), (False, "Cannot reach the management API"))

    def test_stop_and_delete_with_nothing_recorded_only_stops(self):
        self.on_and_running(*CORE)
        playgrounds.stop(self.project, "welcome", True, ["addon:agent-lens"])
        job, commands = ran(self.started(self.jobs)[1])
        self.assertEqual(self.phases(job), ["Delete what it created", "Stop the apps"])
        self.assertEqual([command[4:7] for command in commands], [["-p", "gravitee", "rm"], ["up", "-d", "--no-deps"]])
        self.assertIn("Nothing recorded as created by this playground", job.lines)

    def test_a_start_that_stops_a_playground_deletes_what_it_created_when_asked_and_keeps_it_otherwise(self):
        with_cloud(self.project)   # a second playground, Hotels
        (self.root / "playgrounds/hotels").mkdir(parents=True)
        self.on_and_running(*CORE)
        self.patch(playgrounds, "created", lambda pid, place=None: [{"what": "APIs", "count": 1}] if pid == "welcome" else [])
        accept = ["playground:welcome", "addon:agent-lens"]
        # Kept: the start is what it always was
        playgrounds.start(self.project, "hotels", accept)
        job, commands = ran(self.started(self.jobs)[1])
        self.assertNotIn("Delete what it created", self.phases(job))
        self.assertFalse([command for command in commands if "--remove" in command])
        # Asked to delete: the seed of the playground that goes runs first, as its own stop would
        playgrounds.start(self.project, "hotels", accept, remove=True)
        _, steps, more = self.started(self.jobs)
        job, commands = ran(steps)
        self.assertEqual(self.phases(job)[0], "Delete what it created")
        self.assertEqual(more["phases"][0], "Delete what it created")
        self.assertIsNone(more["waits"][0])
        self.assertTrue(removes(commands[0], "seed"), commands[0])
        # When the delete fails the job ends there: nothing is written, nothing stops
        self.written.clear()
        job, commands = ran(steps, fail="--remove")
        self.assertEqual((job.ok, self.written), (False, []))
        # Nothing created: nothing to delete, nothing refused
        self.patch(playgrounds, "created", lambda pid, place=None: [])
        playgrounds.start(self.project, "hotels", accept, remove=True)
        self.assertTrue(ran(self.started(self.jobs)[1])[0].ok)

    def test_this_machine_is_the_place_that_is_not_named(self):
        # No place, and This machine by its id, are the checks, the start and the Re-seed that have always been
        self.patch(plugins, "installed", lambda project, product: {"bundled": {"gravitee-policy-x": {}},
                                                                    "bundled_error": None, "ext": {},
                                                                    "feature_by_id": {}})
        self.patch(license, "info", lambda project: {"present": False})
        self.patch(license, "status", lambda project: {"status": "none", "digest": "x"})
        found = playgrounds.check(self.project, "welcome")
        for place in (None, "machine"):
            self.assertEqual(playgrounds.check(self.project, "welcome", place), found)
        self.assertEqual(found["checks"][0]["name"], "Gravitee platform")

        def job(call):
            call()
            title, steps, how = self.started(self.jobs)
            return title, how, ran(steps)[1]

        def start(**more):
            return job(lambda: playgrounds.start(self.project, "welcome", [], **more))

        def reseed(**more):
            return job(lambda: playgrounds.reseed(self.project, "welcome", **more))
        for place in (None, "machine"):
            self.assertEqual(start(place=place), start())
        self.state = on(self.project, playground="welcome")
        for place in (None, "machine"):
            self.assertEqual(reseed(place=place), reseed())

    def test_forget_empties_what_the_seed_recorded(self):
        # After a reset of the stack, as the seed does once it deleted it; nothing recorded stays nothing
        record = self.record("APIs", "APIs")
        self.assertTrue(playgrounds.forget("welcome"))
        self.assertEqual((record.read_text(encoding="utf-8"), playgrounds.created("welcome")), ("[]", []))
        record.unlink()
        self.assertTrue(playgrounds.forget("welcome"))
        self.assertFalse(record.exists())

    # --- Catalogs: what it is started from, and what the gates see ------------------------------------------------------

    def test_a_start_that_sets_it_up_writes_the_on_file_through_the_gate_and_makes_its_folders(self):
        write, binds = self.patch(layers, "write"), self.patch(layers, "make_binds", return_value=[])
        self.project["addons"]["jade"]["blocked"] = {"reasons": ["Mounts the Docker socket"]}
        playgrounds.start(self.project, "welcome", [])
        _, steps, _ = self.started(self.jobs)
        ran(steps)
        write.assert_called_once_with("welcome", [], {"playgrounds/welcome/compose.yml": None, "addons/agent-lens/compose.yml": None},
                                      blocked={"addons/jade/compose.yml": "Mounts the Docker socket"})
        binds.assert_called_once()
        self.assertEqual(binds.call_args.args[1]["id"], "welcome")

    def test_a_playground_that_brings_a_blocked_unit_does_not_start(self):
        self.project["addons"]["agent-lens"]["blocked"] = {"reasons": ["Runs with full privileges"]}
        with self.assertRaisesRegex(RuntimeError, "^Runs with full privileges$"):
            playgrounds.start(self.project, "welcome", [])
        self.jobs.start.assert_not_called()
        self.assertEqual(self.written, [])

    def test_the_catalogs_verify_what_a_start_puts_in_the_on_file_before_the_job(self):
        self.project["playgrounds"]["welcome"]["catalog"] = "fixture"
        service = types.ModuleType("service")
        service.preflight = mock.Mock(side_effect=RuntimeError("Welcome changed on disk after it was installed. Repair Fixture, "
                                                               "then start it again."))
        with mock.patch.dict(sys.modules, {"lab_ui.catalogs.service": service}):
            with self.assertRaisesRegex(RuntimeError, "^Welcome changed on disk"):
                playgrounds.start(self.project, "welcome", [])
        service.preflight.assert_called_once_with(self.project, [("playground", "welcome")])
        self.jobs.start.assert_not_called()

    def test_nothing_starts_while_the_layout_is_moving(self):
        self.patch(layout, "waiting", return_value=True)
        with self.assertRaisesRegex(RuntimeError, "^Your settings are being moved. Try again in a moment.$"):
            playgrounds.start(self.project, "welcome", [])
        self.jobs.start.assert_not_called()

    def test_where_a_playground_comes_from_is_said_in_the_dialog_that_opens_for_another_reason(self):
        self.project["playgrounds"]["welcome"]["origin"] = {"id": "partner", "name": "Partner Bank", "version": "2.0.0"}
        self.state = on(self.project, ["jade"])   # starting it turns Jade off, which the dialog says
        self.patch(license, "info", lambda project: {"present": False})
        self.patch(license, "status", lambda project: {"status": "none", "digest": "x"})
        dialog = playgrounds.check(self.project, "welcome")["dialog"]
        self.assertEqual((dialog["from"], dialog["hints"]), ([{"kind": "catalog", "id": "partner", "name": "Partner Bank"}],
                                                              {"from": "Version 2.0.0"}))
        self.assertEqual([row["id"] for row in dialog["turns_off"]], ["jade"])
        # Nothing else to say, nothing opens: the row does not ask
        self.state = on(self.project)
        self.project["playgrounds"]["welcome"].update(addons=[], includes=[], restarts=[], requires={})
        self.assertIsNone(playgrounds.check(self.project, "welcome")["dialog"])

    # --- What a catalog chose to check on this machine ------------------------------------------------------------------

    HOST = {"requires": {"host": [{"name": "Ollama", "check": "http://localhost:11434/api/tags", "help": "Run ollama"},
                                  {"name": "Hub", "check": "https://huggingface.co/api", "help": "Needs the internet"},
                                  {"name": "Loose", "help": "Say it"}]}}

    def test_an_address_a_catalog_chose_that_is_not_on_this_machine_is_not_opened(self):
        playground = {**self.HOST, "verdict": {"notes": [trust.line("N03")]}}
        with mock.patch.object(playgrounds, "_host_check", return_value=(True, "Ready")) as opened:
            found = playgrounds._host_checks(playground)
        self.assertEqual([call.args[0]["name"] for call in opened.call_args_list], ["Ollama"])
        self.assertEqual(found, [
            {"name": "Ollama", "state": "ok", "detail": "Ready", "help": None},
            {"name": "Hub", "state": "warn", "detail": "Not checked", "help": "Needs the internet"},
            {"name": "Loose", "state": "warn", "detail": "Not checked", "help": "Say it"}])

    def test_what_the_policy_did_not_judge_or_found_nothing_against_is_checked_as_it_was(self):
        spec = {"requires": {"host": [self.HOST["requires"]["host"][1]]}}
        for playground in (spec, {**spec, "verdict": None}, {**spec, "verdict": {"notes": []}},
                           {**spec, "verdict": {"notes": ["Its gateway is not used: the Cloud pages do not offer it"]}}):
            with self.subTest(playground=playground), mock.patch.object(playgrounds, "_host_check",
                                                                         return_value=(False, "Not reachable (x)")) as opened:
                found = playgrounds._host_checks(playground)
            opened.assert_called_once()
            self.assertEqual(found[0]["detail"], "Not reachable (x)")


DEFAULT = json.dumps({"schema": 1, "catalogs": [{"id": "fixture", "url": "https://example.invalid/fixture.git"}]})


class State(Case):
    """What the seed of a playground of a catalog recorded, in a folder a container wrote in: the Lab writes nothing
    through a link that container planted, nor reads what it leads to."""

    def setUp(self):
        self.folder(playgrounds, paths, playgrounds.projects, files={paths.DEFAULT_FILE: DEFAULT})
        self.pid = "@acme/bank"
        self.unit = self.root / paths.unit_folder("playground", self.pid)
        self.state = self.unit / ".state"
        self.state.mkdir(parents=True)
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.record = self.outside / "created.json"
        self.record.write_text('[{"label": "API"}]', encoding="utf-8")

    def test_the_record_of_a_playground_of_a_catalog_is_in_its_folder(self):
        self.assertEqual(playgrounds.created(self.pid), [])
        (self.state / "created.json").write_text('[{"label": "API"}, {"label": "API"}]', encoding="utf-8")
        self.assertEqual(playgrounds.created(self.pid), [{"what": "API", "count": 2}])
        self.assertTrue(playgrounds.forget(self.pid))
        self.assertEqual((self.state / "created.json").read_text(encoding="utf-8"), "[]")

    def test_a_state_folder_that_is_a_link_out_of_the_folder_is_not_written_and_not_read(self):
        self.state.rmdir()
        os.symlink(self.outside, self.state)
        self.assertEqual(playgrounds.created(self.pid), [])
        self.assertFalse(playgrounds.forget(self.pid))
        self.assertEqual(self.record.read_text(encoding="utf-8"), '[{"label": "API"}]')

    def test_a_record_that_is_a_link_out_of_the_folder_is_not_written_and_not_read(self):
        os.symlink(self.record, self.state / "created.json")
        self.assertEqual(playgrounds.created(self.pid), [])
        self.assertFalse(playgrounds.forget(self.pid))
        self.assertEqual(self.record.read_text(encoding="utf-8"), '[{"label": "API"}]')

    def test_a_record_that_is_a_link_to_a_file_of_the_folder_is_replaced_and_its_target_stays(self):
        (self.state / "real.json").write_text('[{"label": "API"}]', encoding="utf-8")
        os.symlink(self.state / "real.json", self.state / "created.json")
        self.assertEqual(playgrounds.created(self.pid), [], "a link is not a record a seed wrote")
        self.assertTrue(playgrounds.forget(self.pid))
        self.assertEqual((self.state / "real.json").read_text(encoding="utf-8"), '[{"label": "API"}]')

    def test_a_record_that_is_a_pipe_does_not_stop_the_state(self):
        os.mkfifo(self.state / "created.json")
        self.assertEqual(playgrounds.created(self.pid), [])

    def test_the_folder_of_a_place_is_made_in_the_folder_of_the_playground(self):
        playgrounds._remember(self.pid, "my-account/fake-env-dev", "Development")
        meta = self.state / "my-account" / "fake-env-dev" / "meta.json"
        self.assertEqual(json.loads(meta.read_text(encoding="utf-8")), {"environment": "Development"})

    def test_the_folder_of_a_place_is_never_made_through_a_link(self):
        self.state.rmdir()
        os.symlink(self.outside, self.state)
        with self.assertRaisesRegex(JobFailed, "^The state folder of the playground leads out of its folder: nothing was "
                                               "written there$"):
            playgrounds._remember(self.pid, "my-account/fake-env-dev", "Development")
        self.assertEqual(sorted(os.listdir(self.outside)), ["created.json"])
        (self.unit / "other").mkdir()
        self.state.unlink()
        self.state.mkdir()
        os.symlink(self.outside, self.state / "my-account")
        with self.assertRaises(JobFailed):
            playgrounds._remember(self.pid, "my-account/fake-env-dev", "Development")
        self.assertEqual(sorted(os.listdir(self.outside)), ["created.json"])

    def test_a_link_to_a_folder_of_the_playground_is_never_written_through_either(self):
        # Inside its folder, but out of its state: what the policy judged and the seed reads is there
        (self.unit / "seed").mkdir()
        os.symlink(self.unit / "seed", self.state / "plat1")
        os.symlink(self.unit, self.state / "plat2")
        for platform in ("plat1", "plat2"):
            with self.subTest(platform=platform), self.assertRaisesRegex(JobFailed, "^The state folder of the playground "
                                                                         "leads out of its folder"):
                playgrounds._remember(self.pid, f"{platform}/env", "Environment")
        self.assertEqual((sorted(os.listdir(self.unit / "seed")), sorted(os.listdir(self.unit))), ([], [".state", "seed"]))

    def test_a_state_folder_that_is_a_link_to_a_folder_of_the_playground_is_never_written_through(self):
        for name in os.listdir(self.state):
            (self.state / name).unlink()
        self.state.rmdir()
        (self.unit / "seed").mkdir()
        os.symlink(self.unit / "seed", self.state)
        (self.unit / "seed" / "created.json").write_text('[{"label": "API"}]', encoding="utf-8")
        self.assertEqual(playgrounds.created(self.pid), [])
        self.assertFalse(playgrounds.forget(self.pid))
        with self.assertRaises(JobFailed):
            playgrounds._remember(self.pid, "my-account/fake-env-dev", "Development")
        self.assertEqual(sorted(os.listdir(self.unit / "seed")), ["created.json"])
        self.assertEqual((self.unit / "seed" / "created.json").read_text(encoding="utf-8"), '[{"label": "API"}]')

    # What a container wrote is valid text of any shape: the state of the Lab is made of it for every playground, on every
    # poll, and must not end on a file (the page cannot draw, so no button can empty it)
    SHAPES = ("5", "[1]", '{"a": 1}', '[{"label": []}]', '[{"label": "x", "count": "many"}]', '[{"label": "x", "count": null}]',
              "null", '[{"nolabel": 1}]', '"text"', "true", "[[]]", '[{"label": 5}]', '[{"label": "x", "count": true}]',
              '[{"label": "x", "count": 1.5}]', '[{"label": "x", "count": 0}]', '[{"label": "x", "count": -3}]',
              '[{"label": null}]', "[" * 100000, '{"a":' * 100000, "[1e999, NaN]")

    def test_a_record_of_any_shape_is_none_and_ends_nothing(self):
        for text in self.SHAPES:
            with self.subTest(text=text[:30]):
                (self.state / "created.json").write_text(text, encoding="utf-8")
                self.assertEqual(playgrounds.created(self.pid), [])

    def test_a_record_of_any_shape_in_a_place_is_none_and_ends_nothing(self):
        record = self.state / "my-account" / "fake-env-dev"
        record.mkdir(parents=True)
        for text in self.SHAPES:
            with self.subTest(text=text[:30]):
                (record / "created.json").write_text(text, encoding="utf-8")
                self.assertEqual(playgrounds.created(self.pid, "my-account/fake-env-dev"), [])
                self.assertEqual(places.deployed(self.pid, playgrounds.created), [])

    def test_the_entries_of_a_record_that_can_be_one_are_kept_when_others_are_not(self):
        (self.state / "created.json").write_text(json.dumps([
            {"label": "API"}, 5, None, {"label": "API", "count": 2}, {"label": "Bad", "count": "x"}, {"count": 4},
            {"label": "Flag", "count": True}, {"label": "Zero", "count": 0}, {"label": ["a"]}, [], "x",
            {"label": "Entities", "count": 7}]), encoding="utf-8")
        self.assertEqual(playgrounds.created(self.pid), [{"what": "API", "count": 3}, {"what": "Entities", "count": 7}])

    def test_the_guide_of_a_playground_of_any_catalog_is_read_by_its_id(self):
        for pid in ("@acme/bank", "bank", "local/bank"):
            self.write(f"{paths.unit_folder('playground', pid)}/compose.yml")
            self.write(f"{paths.unit_folder('playground', pid)}/guide.md", f"# {pid}\n")
            self.assertEqual(playgrounds.guide(pid), f"# {pid}\n")
        for pid in ("@acme/gone", "gone", "local/gone", "@nothing/bank"):
            with self.assertRaisesRegex(paths.NotFound, f"^No such playground: {pid}$"):
                playgrounds.guide(pid)


# ---------------------------------------------------------------------------------------------------------------------
# A place besides This machine: an environment of a platform, against the fake Cloud
# ---------------------------------------------------------------------------------------------------------------------

NAME = "My Account"
PLACE, TESTING = f"my-account/{DEV}", f"my-account/{TEST}"
DEVELOPMENT, TEST_ENVIRONMENT = "Development (My Account)", "Test (My Account)"
VARIABLES = ["AIM_AUTOMATION_URL", "AIM_GAMMA_URL", "APIM_ENV", "APIM_GATEWAY_URL", "APIM_MANAGEMENT_PATH",
             "APIM_MANAGEMENT_URL", "APIM_ORG", "APIM_PORTAL_PATH", "APIM_TOKEN", "GATEWAY_INTERNAL_URL",
             "STATE_DIR"]   # what places.seed_env gives a seed, by name
UP = [*COMPOSE, "up", "-d", "--no-deps", "apim-hybrid-gateway", "hotels-api", "hybrid-redis"]
ENVIRONMENT = row("environment", PLACE, DEVELOPMENT)
STATE_FOLDER = "playgrounds/hotels/.state"


def seed(*more):
    """The command of the seed run apart on a place: the settings by name, none by value, and the names a proxy and a trust
    store are taken from, empty."""
    return [*COMPOSE, "run", "--rm", "--no-deps", *(["--pull=missing"] if "--remove" in more else []), *[word for name in VARIABLES for word in ("-e", name)],
            *[word for name in places.CLEARED for word in ("-e", f"{name}=")], "hotels-seed", *more]


class Deploys(test_places.Places):
    """Hotels, a playground that deploys to a platform, in a project that has the Hybrid gateway, with the platform
    My Account registered in the fake Cloud. Commands do not run: Popen is replaced (execute())."""

    def setUp(self):
        super().setUp()
        self.patch(playgrounds, "ROOT", self.root)
        self.project = lab()
        licensed(self.project, True)
        own(self.project)
        second(self.project)
        with_cloud(self.project)
        self.core = [name for name, service in self.project["services"].items()
                     if service["owner"]["kind"] == "platform"]
        self.state, self.containers, self.written = on(self.project), {}, []
        self.jobs = self.patch(playgrounds, "JOBS")
        self.patch(layers, "JOBS")
        self.patch(layers, "current", lambda project: self.state)
        self.patch(layers, "write", lambda playground, explicit, *where, **how: self.written.append((playground, explicit)))
        self.patch(compose, "ps", lambda *variable: self.containers)
        self.patch(compose, "project_name", lambda: "gravitee")
        self.patch(playgrounds.projects, "saved", return_value={"hashes": {}, "images": {}})
        self.wait = playgrounds.GATEWAY_WAIT
        self.patch(playgrounds, "GATEWAY_POLL", 0)
        self.patch(playgrounds, "GATEWAY_WAIT", 0.2)

    def warm(self):
        """The platforms have said what they hold (places.py reads them in the background)."""
        places.places(self.project)
        self.settle()

    def ready(self, environment=DEV, name="lab"):
        """A hybrid gateway and a Cloud Token in an environment: the place is ready. Answers the gateway."""
        gateway = platforms.create_gateway(self.project, "my-account", environment, name)["gateway"]
        platforms.set_cloud_token("my-account", environment, fake_cloud.cloud_token(environment))
        places.invalidate("my-account")
        return gateway

    def secrets(self):
        """Every token the test holds: the fake's, those the Cloud issued, and those kept in the settings."""
        kept = [value for file in (".env", ADDON_ENV) for key, value in envfiles.read(file).items() if "TOKEN" in key]
        return [*self.tokens(), *kept]

    def assertNoSecret(self, *things):
        """None of the tokens is in these, and there are tokens to look for."""
        text = repr(things)
        self.assertTrue(self.secrets())
        self.assertFalse(any(token in text for token in self.secrets()), "a token is in it")

    def record(self, place, *labels, pid="hotels"):
        """What the seed recorded as created, on This machine (no place) or in a place: its file."""
        file = self.root / "playgrounds" / pid / ".state" / (place or "") / "created.json"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps([{"kind": "api", "label": label} for label in labels]), encoding="utf-8")
        return file

    def execute(self, outputs=None, seen=None):
        """Runs the job that the replaced JOBS was last asked to start, as its thread would, its commands printing what
        `outputs` says by a word of the command, (lines, exit code); `seen(command, options)` is called as each starts.
        Answers the job and what it started: [(command, the options Popen got)]."""
        title, steps, more = self.started(self.jobs)
        job, calls = Job(title, steps, **more), []

        def command(cmd, **options):
            if "--ignore-pull-failures" in cmd:   # the pull before an `up`: the images are there
                return mock.Mock(stdout=iter([]), wait=lambda: 0, returncode=0)
            calls.append((cmd, options))
            if seen:
                seen(cmd, options)
            lines, code = next((said for word, said in (outputs or {}).items() if word in cmd), ([], 0))
            return mock.Mock(stdout=iter(line + "\n" for line in lines), wait=lambda: code, returncode=code)
        with mock.patch.object(jobs.subprocess, "Popen", command):
            job.run()
        return job, calls

    def deploy(self, place=PLACE, accept=()):
        return playgrounds.start(self.project, "hotels", list(accept), place=place)


class Records(Deploys):
    def test_a_place_has_a_record_of_its_own(self):
        self.record(None, "APIs", "APIs")
        self.record(PLACE, "APIs", "Applications")
        self.assertEqual(playgrounds.created("hotels"), [{"what": "APIs", "count": 2}])
        self.assertEqual(playgrounds.created("hotels", PLACE), [{"what": "APIs", "count": 1},
                                                                {"what": "Applications", "count": 1}])
        self.assertEqual(playgrounds.created("hotels", TESTING), [])
        self.assertEqual(playgrounds.created("hotels", "machine"), playgrounds.created("hotels"), "This machine's")
        self.assertEqual(playgrounds.CREATED_FILE, ".state/created.json")

    def test_what_is_not_a_place_is_no_folder(self):
        for place in ("../x", "a/..", "a/b/c", "my-account/", "/my-account", "my-account", "a b/c", 5, ["a/b"],
                      {"a": 1}):
            with self.subTest(place=place), self.assertRaisesRegex(paths.NotFound, "^No place"):
                playgrounds.created("hotels", place)

    def test_forgetting_empties_the_record_of_this_machine_and_no_other(self):
        local, remote = self.record(None, "APIs"), self.record(PLACE, "APIs", "APIs")
        before = remote.read_bytes()
        playgrounds.forget("hotels")
        self.assertEqual((local.read_text(encoding="utf-8"), remote.read_bytes()), ("[]", before))
        self.assertEqual(playgrounds.created("hotels", PLACE), [{"what": "APIs", "count": 2}])

    def test_the_environment_is_named_beside_its_record(self):
        self.ready()
        playgrounds._remember("hotels", PLACE, "Development")
        folder = self.root / STATE_FOLDER / "my-account" / DEV
        self.assertEqual(json.loads((folder / "meta.json").read_text(encoding="utf-8")),
                         {"environment": "Development"})
        self.record(PLACE, "APIs")
        self.assertEqual(places.deployed("hotels", playgrounds.created),
                         [{"place": PLACE, "environment": "Development", "created": [{"what": "APIs", "count": 1}]}])


class PlaceChecks(Deploys):
    """Before you start, for a place."""

    def checks(self, place=PLACE):
        return playgrounds.check(self.project, "hotels", place)

    def test_a_ready_place_says_what_a_start_does_and_nothing_of_the_core(self):
        self.ready()
        found = self.checks()
        self.assertEqual(found["checks"], [
            {"name": NAME, "state": "ok", "detail": "Connected", "target": {"kind": "platform", "id": "my-account"}},
            {"name": "Cloud Token", "state": "ok", "detail": "Valid"},
            {"name": "Hybrid gateway", "state": "auto", "detail": "Turned on at start",
             "target": {"kind": "addon", "id": "hybrid"}}])
        self.assertEqual(found["dialog"], {"starts": [HOTELS, HYBRID], "stops": [], "turns_off": [], "keeps": [],
                                           "restarts": [], "deploys_to": [ENVIRONMENT], "serves": [],
                                           "stops_serving": []})
        self.assertNoSecret(found)

    def test_nothing_of_the_core_is_said_whatever_runs(self):
        # No platform line of the core, no plugin, image, license or memory line: the core is not part of a deploy
        self.ready()
        self.patch(compose, "docker_memory", return_value=2 ** 30)
        self.project["playgrounds"]["hotels"]["requires"] = {
            "license": ["feature-1"], "images": {"APIM_VERSION": "4.13"}, "plugins": {"apim": ["gravitee-policy-x"]}}
        for containers in ({}, running(*self.core), running(*self.core, "hotels-api")):
            self.containers = containers
            names = [line["name"] for line in self.checks()["checks"]]
            self.assertEqual(names, [NAME, "Cloud Token", "Hybrid gateway"], containers)

    def test_a_cloud_token_that_is_missing_expired_or_refused_is_a_line_with_its_fix(self):
        platforms.create_gateway(self.project, "my-account", DEV, "lab")
        self.warm()
        line = {"name": "Cloud Token", "state": "warn", "fix": "token"}
        self.assertEqual(self.checks()["checks"][1], {**line, "detail": "Not set"})
        envfiles.write(".env", {DEV_KEY: fake_cloud.CLOUD_TOKEN_EXPIRED})
        self.assertEqual(self.checks()["checks"][1], {**line, "detail": "Expired"})
        platforms.set_cloud_token("my-account", DEV, fake_cloud.CLOUD_TOKEN)
        self.fake.state.revoked.add(fake_cloud.CLOUD_TOKEN)
        with self.assertRaises(cloud.CloudError):
            platforms.prove("my-account", DEV)
        self.assertEqual(self.checks()["checks"][1], {**line, "detail": "Refused"})

    def test_an_environment_without_a_gateway_asks_for_one(self):
        self.warm()
        found = self.checks()["checks"]
        self.assertEqual(found[2], {"name": "Hybrid gateway", "state": "warn", "detail": "None in Development",
                                    "fix": "gateway"})
        self.assertEqual(self.checks(TESTING)["checks"][2]["detail"], "None in Test")

    def test_a_gateway_that_runs_says_whether_the_platform_hears_it(self):
        self.ready()
        self.state, self.containers = on(self.project, ["hybrid"]), running("apim-hybrid-gateway", "hybrid-redis")
        hybrid = {"kind": "addon", "id": "hybrid"}
        self.assertEqual(self.checks()["checks"][2],
                         {"name": "Hybrid gateway", "state": "ok", "detail": "Running", "target": hybrid})
        self.fake.state.connect_after = 3600
        places.invalidate("my-account")
        self.assertEqual(self.checks()["checks"][2],
                         {"name": "Hybrid gateway", "state": "warn", "detail": "Not connected", "target": hybrid})
        # On and stopped: started with the playground
        self.containers = {}
        self.assertEqual(self.checks()["checks"][2]["detail"], "Started at start")

    def test_a_gateway_whose_token_is_not_kept_here_says_so(self):
        # places.py counts only a gateway whose token is kept; if one were not, its line opens the add-on's settings
        self.ready()
        found = self.place_of()
        found["gateway"]["kept"] = False
        self.patch(places, "place", return_value=found)
        self.assertEqual(self.checks()["checks"][2], {
            "name": "Hybrid gateway", "state": "warn", "detail": "Its token is not kept here",
            "target": {"kind": "addon", "id": "hybrid"}, "file": ADDON_ENV})

    def place_of(self, place=PLACE):
        return json.loads(json.dumps(places.place(self.project, place)))

    def test_a_gateway_set_to_serve_another_environment_is_switched(self):
        self.ready()
        self.ready(TEST, "other")
        self.state, self.containers = on(self.project, ["hybrid"]), running("apim-hybrid-gateway", "hybrid-redis")
        found = self.checks(TESTING)
        self.assertEqual(found["checks"][2], {"name": "Hybrid gateway", "state": "auto", "detail": "Switches to Test",
                                              "target": {"kind": "addon", "id": "hybrid"}})
        dialog = found["dialog"]
        self.assertEqual((dialog["restarts"], dialog["serves"], dialog["stops_serving"]), (
            [HYBRID], [row("environment", TESTING, TEST_ENVIRONMENT)], [ENVIRONMENT]))

    def test_the_add_ons_it_brings_and_the_host_it_needs_are_in_the_checks(self):
        self.ready()
        hotels = self.project["playgrounds"]["hotels"]
        hotels.update(addons=["agent-lens"],
                      requires={"host": [{"name": "Ollama", "check": "http://x", "help": "Start it"}]})
        self.patch(playgrounds, "_host_check", return_value=(False, "Not reachable (refused)"))
        self.state = on(self.project, missing=["agent-lens"])
        self.assertEqual(self.checks()["checks"][3:], [
            {"name": "AI Agent Lens", "state": "warn", "detail": "Needs settings",
             "target": {"kind": "addon", "id": "agent-lens"}, "file": "addons/agent-lens/.env"},
            {"name": "Ollama", "state": "warn", "detail": "Not reachable (refused)", "help": "Start it"}])

    def test_what_a_start_stops_is_said(self):
        self.ready()
        self.state, self.containers = on(self.project, playground="demo"), running("demo-app", "kiosk")
        found = self.checks()
        self.assertIn({"name": "Demo apps", "state": "auto", "detail": "Stops at start",
                       "target": {"kind": "playground", "id": "demo"}}, found["checks"])
        self.assertIn("Demo apps", [row["name"] for row in found["dialog"]["stops"]])

    def test_a_platform_that_cannot_be_used_is_one_line_and_no_dialog(self):
        # Loading: nothing read yet. Then the Account Token refused, then a platform that does not answer
        self.assertEqual(self.checks(), {"checks": [{"name": NAME, "state": "warn", "detail": "Loading",
                                                     "target": {"kind": "platform", "id": "my-account"}}],
                                         "dialog": None})
        self.settle()
        self.ready()
        envfiles.write(".env", {KEY: "rotated-since"})
        places.invalidate("my-account")
        invalid = {"checks": [{"name": NAME, "state": "warn", "detail": "Invalid token",
                               "target": {"kind": "platform", "id": "my-account"}}], "dialog": None}
        for place in (PLACE, "my-account"):   # the page asks for the platform alone when it cannot be used
            self.assertEqual(self.checks(place), invalid)
        envfiles.write(".env", {KEY: fake_cloud.TOKEN})
        self.patch(platforms, "fetch", return_value={"state": "unreachable", "message": "x does not answer in time",
                                                     "environments": [], "gateways": []})
        places.invalidate("my-account")
        self.assertEqual(self.checks()["checks"][0]["detail"], "Unreachable")

    def test_what_is_not_a_place_is_not_found(self):
        self.warm()
        for place in ("nope/x", f"my-account/{DEV}x", "my-account/a/b", 5):
            with self.subTest(place=place), self.assertRaisesRegex(paths.NotFound, "^No place"):
                self.checks(place)
        with self.assertRaises(paths.NotFound):
            playgrounds.check(self.project, "nope", PLACE)


class Deploying(Deploys):
    """Start, Deploy and Re-seed on a place."""

    def test_a_start_runs_the_apps_and_the_gateway_here_and_the_seed_apart(self):
        self.ready()
        self.deploy()
        title, steps, more = self.started(self.jobs)
        self.assertEqual((title, more["key"], more["verb"], more["phases"]), (
            f"Start Hotels in {DEVELOPMENT}", "playground:hotels", "start", ["Apps and services", "Gateway", "Seed"]))
        self.assertEqual(more["changes"], {"start": [["playground", "hotels"], ["addon", "hybrid"]], "stop": []})
        self.assertEqual(more["waits"], [awaited(self.project["services"], start=[
            "apim-hybrid-gateway", "hotels-api", "hybrid-redis"])])
        job, calls = self.execute()
        self.assertTrue(job.ok, job.lines)
        self.assertEqual([phase["name"] for phase in job.phases], ["Add-ons", "Apps and services", "Gateway", "Seed"])
        self.assertEqual([command for command, _ in calls], [UP, seed()])
        self.assertEqual(self.written, [("hotels", ["hybrid"])])
        self.assertEqual([line for line in job.lines if not line.startswith(("▸", "$"))],
                         ["Starts: Hotels apps, Hybrid gateway", f"Deploys to: {DEVELOPMENT}"])

    def test_an_add_on_runs_on_a_place_with_the_gateway_of_the_platform_and_its_own_services(self):
        self.project["addons"]["kiosk"]["deploys_to"] = ["cloud"]
        self.ready()
        playgrounds.deploy_unit(self.project, "addon", "kiosk", [], PLACE)
        title, steps, more = self.started(self.jobs)
        self.assertEqual((title, more["key"], more["verb"], more["phases"]),
                         (f"Start Kiosk in {DEVELOPMENT}", "addon:kiosk", "start", ["Apps and services", "Gateway"]))
        job, calls = self.execute()
        self.assertTrue(job.ok, job.lines)
        self.assertEqual([phase["name"] for phase in job.phases], ["Add-ons", "Apps and services", "Gateway"])
        # No seed: the gateway of the platform, and what the add-on has
        self.assertEqual([command for command, _ in calls],
                         [[*COMPOSE, "up", "-d", "--no-deps", "apim-hybrid-gateway", "hybrid-redis", "kiosk"]])
        self.assertEqual(self.written[-1][1], ["hybrid", "kiosk"])
        self.assertIn(f"Runs on: {DEVELOPMENT}", job.lines)
        # What cannot go says why
        self.project["addons"]["kiosk"]["cannot"] = {"cloud": "Changes this machine's Management API"}
        self.project["addons"]["kiosk"]["deploys_to"] = []
        with self.assertRaisesRegex(ValueError, "cannot go to .*: Changes this machine's Management API"):
            playgrounds.deploy_unit(self.project, "addon", "kiosk", [], PLACE)

    def test_what_the_playground_needs_of_the_platform_starts_first_with_what_it_depends_on(self):
        # Access Management and its database run here: they start with their dependencies, then the apps and the
        # gateway alone, as they did
        self.project["services"]["hotels-api"]["depends_on"] = ["mongodb"]
        self.ready()
        self.deploy()
        job, calls = self.execute()
        self.assertTrue(job.ok, job.lines)
        commands = [command for command, _ in calls]
        self.assertEqual(commands[0], [*COMPOSE, "up", "-d", "mongodb"])
        self.assertEqual(commands[1], UP)
        self.assertNotIn("mongodb", UP)
        self.assertEqual(commands[2], seed())

    def test_the_seed_runs_with_its_settings_by_name_and_their_values_in_its_environment_alone(self):
        self.ready()
        self.deploy()
        job, calls = self.execute()
        (up, up_options), (command, options) = calls
        values = {name: options["env"][name] for name in VARIABLES}
        self.assertEqual(values["STATE_DIR"], f"/state/my-account/{DEV}")
        self.assertEqual((values["APIM_MANAGEMENT_PATH"], values["APIM_ENV"], values["APIM_ORG"]),
                         ("/apim/rest", DEV, "fake-organization"))
        self.assertEqual((values["APIM_GATEWAY_URL"], values["GATEWAY_INTERNAL_URL"], values["APIM_MANAGEMENT_URL"]),
                         ("http://localhost:8090", "http://apim-hybrid-gateway:8082", self.url))
        self.assertEqual(values["APIM_TOKEN"], envfiles.read(".env")[DEV_KEY], "the Cloud Token, from the environment")
        # Each by name, none by value: the values are not in the command, nor in the line that shows it
        self.assertEqual(command, seed())
        self.assertEqual([word for word in command if "=" in word], [f"{name}=" for name in places.CLEARED],
                         "none but the names that are emptied")
        self.assertFalse(any(value in " ".join(command) for value in values.values() if value.startswith("eyJ")))
        self.assertIn("$ " + " ".join(seed()), job.lines)
        # The other command gets the environment of every command, and none of the seed's
        self.assertFalse({"APIM_TOKEN", "STATE_DIR"} & set(up_options["env"]))
        self.assertEqual(up_options["env"]["COMPOSE_FILE"], compose.COMPOSE_FILE)

    def test_no_address_the_seed_keeps_is_the_authors(self):
        # The seeder sends the token to these three: each is the place's Cloud Gate, passed by name over the author's own value
        self.ready()
        self.deploy()
        _, calls = self.execute()
        command, options = calls[-1]
        for name in ("APIM_MANAGEMENT_URL", "AIM_AUTOMATION_URL", "AIM_GAMMA_URL"):
            self.assertIn(name, command[command.index("-e") - 1:], name)
            self.assertEqual(options["env"][name], self.url, name)
        # Nor the path it joins to them, which could name another host, nor the proxy and the trust store it would honor
        self.assertIn("APIM_PORTAL_PATH", command)
        self.assertEqual(options["env"]["APIM_PORTAL_PATH"], places.PINNED["APIM_PORTAL_PATH"])

    def test_the_proxy_and_the_trust_store_of_the_author_are_emptied_in_the_command(self):
        # `-e NAME=` is a value that replaces the author's, and holds nothing secret: it is in the command and the line of the
        # job; the token is still only in the environment of the command
        self.ready()
        self.deploy()
        job, calls = self.execute()
        command, options = calls[-1]
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy", "REQUESTS_CA_BUNDLE",
                     "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR"):
            with self.subTest(name=name):
                self.assertEqual(command[command.index(f"{name}=") - 1], "-e")
                self.assertNotIn(name, options["env"], "it is emptied by the command, not given a value")
        self.assertIn("$ " + " ".join(seed()), job.lines)

    def refuse_the_seed(self, *notes):
        self.project["playgrounds"]["hotels"]["verdict"] = {"notes": list(notes)}

    def test_a_seed_of_its_own_does_not_go_to_a_place(self):
        self.ready()
        self.refuse_the_seed(trust.SEED_OWN)
        with self.assertRaisesRegex(ValueError, f"^Hotels cannot go to {re.escape(DEVELOPMENT)}: Brings its own seed code$"):
            self.deploy()
        self.jobs.start.assert_not_called()
        self.assertEqual(places.place(self.project, PLACE)["cannot"]["hotels"], "Brings its own seed code")

    def test_seed_data_that_reads_a_token_does_not_go_to_a_place_either(self):
        self.ready()
        self.refuse_the_seed(trust.SEED_TOKEN)
        with self.assertRaisesRegex(ValueError, ": Seed data reads a token$"):
            self.deploy()

    def test_the_hand_off_itself_refuses_a_seed_that_must_not_hold_the_token(self):
        # Even when the place is not asked (a record is older than the policy's word), the seed is never given the token
        self.ready()
        self.patch(playgrounds, "_cannot")
        self.refuse_the_seed(trust.SEED_OWN)
        self.deploy()
        job, calls = self.execute()
        self.assertFalse(job.ok)
        self.assertEqual(job.error(), "Hotels cannot go to a place: Brings its own seed code")
        self.assertEqual([command for command, _ in calls if "run" in command], [], "the seed was not run")
        self.assertNoSecret(job.lines, [command for command, _ in calls], job.summary())

    def test_what_a_seed_of_its_own_created_in_a_place_is_not_deleted_with_the_token_either(self):
        self.ready()
        self.record(PLACE, "APIs")
        self.state = on(self.project, playground="hotels")
        self.refuse_the_seed(trust.SEED_OWN)
        playgrounds.delete_place(self.project, "hotels", PLACE)
        job, calls = self.execute()
        self.assertFalse(job.ok)
        self.assertIn("Brings its own seed code", job.error())
        self.assertEqual([command for command, _ in calls if "run" in command], [])

    def test_a_start_on_a_place_is_written_through_the_gate_and_makes_its_folders(self):
        write, binds = self.patch(layers, "write"), self.patch(layers, "make_binds", return_value=[])
        self.ready()
        self.deploy()
        self.execute()
        self.assertEqual(write.call_args.kwargs, {"blocked": {}})
        binds.assert_called_once()

    def test_no_token_is_in_a_command_a_line_an_answer_or_a_record(self):
        self.ready()
        self.ready(TEST, "other")
        answers = [playgrounds.check(self.project, "hotels", place) for place in (PLACE, TESTING)]
        self.deploy()
        title, steps, more = self.started(self.jobs)
        job, calls = self.execute({"run": (["[seed] Seeding", "[seed] ✓ Hotels ready"], 0)})
        self.assertTrue(job.ok, job.lines)
        files = [file.read_text(encoding="utf-8") for file in (self.root / STATE_FOLDER).rglob("*") if file.is_file()]
        self.assertTrue(files)
        self.assertNoSecret(answers, title, more, [command for command, _ in calls], job.lines, job.summary(), files,
                            self.written, places.deployed("hotels", playgrounds.created))

    def refused(self, why):
        """Start and Re-seed of the place are both refused in these words, before any job or any file."""
        self.state = on(self.project, playground="hotels")
        for call in (self.deploy, lambda: playgrounds.reseed(self.project, "hotels", PLACE)):
            with self.assertRaisesRegex(ValueError, why) as caught:
                call()
            self.assertNoSecret(str(caught.exception))
        self.jobs.start.assert_not_called()
        self.assertEqual((self.written, (self.root / STATE_FOLDER).exists()), ([], False))

    def test_a_place_is_asked_now_and_a_cloud_token_that_cloud_gate_refuses_comes_before_any_job(self):
        self.ready()
        self.fake.state.revoked.add(envfiles.read(".env")[DEV_KEY])
        self.refused(f"^Gravitee Cloud refuses the Cloud Token of {re.escape(DEVELOPMENT)}$")

    def test_a_cloud_token_that_expired_or_is_not_set_comes_before_any_job(self):
        self.ready()
        envfiles.write(".env", {DEV_KEY: fake_cloud.CLOUD_TOKEN_EXPIRED})
        self.refused(f"^Cloud Token for {re.escape(DEVELOPMENT)} has expired$")
        envfiles.write(".env", {DEV_KEY: None})
        self.refused(f"^Cloud Token for {re.escape(DEVELOPMENT)} is not set$")

    def test_an_environment_without_a_gateway_comes_before_any_job(self):
        gateway = self.ready()
        platforms.delete_gateway(self.project, "my-account", gateway["id"])   # in the console, since it was read
        self.refused(f"^Set up a gateway in {re.escape(DEVELOPMENT)} first$")

    def test_an_account_token_that_is_refused_blocks_every_place_of_its_platform(self):
        self.ready()
        envfiles.write(".env", {KEY: "rotated-since"})
        self.refused("^Gravitee Cloud refuses this token$")

    def test_a_platform_that_cannot_be_used_is_refused_in_its_own_words_whatever_the_place_is_called(self):
        self.ready()
        envfiles.write(".env", {KEY: "rotated-since"})
        for place in (PLACE, "my-account"):   # the page names the platform alone when it cannot be used
            with self.subTest(place=place), self.assertRaisesRegex(ValueError, "^Gravitee Cloud refuses this token$"):
                self.deploy(place)
        self.jobs.start.assert_not_called()

    def test_a_place_that_does_not_exist_is_refused_before_any_job(self):
        self.warm()
        for place in ("nope/x", "my-account/gone", "../x", 5):
            with self.subTest(place=place), self.assertRaises((paths.NotFound, ValueError)):
                self.deploy(place)
        self.jobs.start.assert_not_called()

    def test_a_playground_that_cannot_go_there_is_refused_with_the_reason_the_picker_gives(self):
        self.ready()
        hotels = self.project["playgrounds"]["hotels"]
        hotels.update(deploys_to=[], cannot={"cloud": "Needs plugins on the gateway"})
        with self.assertRaisesRegex(ValueError, f"^Hotels cannot go to {re.escape(DEVELOPMENT)}: Needs plugins on the gateway$"):
            self.deploy()
        hotels["cannot"] = {}
        with self.assertRaisesRegex(ValueError, f"^Hotels cannot go to {re.escape(DEVELOPMENT)}$"):
            self.deploy()
        self.jobs.start.assert_not_called()

    def test_a_playground_that_needs_more_than_the_environment_runs_is_refused_with_the_place_s_reason(self):
        # The gateway of Development runs API Management 4.12.20, as the platform says now
        self.ready()
        self.project["groups"][0]["product"] = "apim"
        asks = {"deploys": True, "products": ["API Management"], "versions": [["API Management", "4.13.0"]],
                "fills": ["API Management"], "plugins": False, "changes": False, "uses": False, "more": False}
        self.project["playgrounds"]["hotels"]["asks"] = asks
        with self.assertRaisesRegex(ValueError, f"^Hotels cannot go to {re.escape(DEVELOPMENT)}: Needs API Management 4.13$"):
            self.deploy()
        asks["versions"] = [["API Management", "4.12.0"]]
        self.deploy()
        self.jobs.start.assert_called_once()

    def test_a_playground_without_a_seed_has_nothing_to_deploy(self):
        self.ready()
        self.project["playgrounds"]["hotels"]["seed"] = None
        with self.assertRaisesRegex(RuntimeError, "^Hotels has no seed$"):
            self.deploy()

    def test_what_a_start_would_also_stop_is_accepted_first(self):
        self.ready()
        self.state, self.containers = on(self.project, playground="demo"), running("demo-app", "kiosk")
        with self.assertRaisesRegex(RuntimeError, "^Would also stop Demo apps$"):
            self.deploy()
        self.jobs.start.assert_not_called()
        self.deploy(accept=["playground:demo", "addon:kiosk"])
        job, calls = self.execute()
        self.assertTrue(job.ok, job.lines)
        self.assertIn("Stops: Demo apps, Kiosk", job.lines)
        self.assertEqual(calls[0][0], [*compose.by_name(), "rm", "--stop", "--force", "demo-app", "kiosk"])

    # --- Nothing of the core -----------------------------------------------------------------------------------------

    def test_no_command_of_a_deploy_names_a_service_of_the_core(self):
        self.ready()
        states = [(on(self.project), {}), (on(self.project), running(*self.core)),
                  (on(self.project, ["hybrid"], "hotels"), running(*self.core, "hotels-api")),
                  (on(self.project, ["kiosk"], "demo"), running(*self.core, "demo-app", "kiosk"))]
        for state, containers in states:
            self.state, self.containers = state, containers
            with self.subTest(state=state["playground"], running=sorted(containers)):
                self.deploy(accept=["playground:demo", "addon:kiosk"])
                job, calls = self.execute()
                self.assertTrue(job.ok, job.lines)
                words = {word for command, _ in calls for word in command}
                self.assertFalse(words & set(self.core))
                self.assertIn("hotels-seed", words)

    # --- The gateway -------------------------------------------------------------------------------------------------

    def test_a_gateway_that_serves_this_place_is_not_swapped(self):
        self.ready()
        swap = self.patch(platforms, "swap_gateway", wraps=platforms.swap_gateway)
        self.state, self.containers = on(self.project, ["hybrid"]), running("apim-hybrid-gateway", "hybrid-redis")
        held = envfiles.read(ADDON_ENV)["HYBRID_CLOUD_TOKEN"]
        self.deploy()
        job, calls = self.execute()
        self.assertTrue(job.ok, job.lines)
        swap.assert_not_called()
        self.assertEqual(envfiles.read(ADDON_ENV)["HYBRID_CLOUD_TOKEN"], held)
        self.assertEqual([command for command, _ in calls], [[*COMPOSE, "up", "-d", "--no-deps", "hotels-api"],
                                                             seed()])

    def test_a_gateway_that_serves_another_place_takes_this_one_s_token_before_it_is_made_again(self):
        first = self.ready()
        other = self.ready(TEST, "other")
        self.state, self.containers = on(self.project, ["hybrid"]), running("apim-hybrid-gateway", "hybrid-redis")
        self.assertEqual(envfiles.read(ADDON_ENV)["HYBRID_CLOUD_TOKEN"],
                         envfiles.read(".env")[test_platforms.gateway_key(first["id"], "MY_ACCOUNT")])
        self.deploy(TESTING)
        held = []

        def seen(command, options):
            held.append((command[len(COMPOSE):len(COMPOSE) + 1], envfiles.read(ADDON_ENV)["HYBRID_CLOUD_TOKEN"]))
        job, calls = self.execute(seen=seen)
        self.assertTrue(job.ok, job.lines)
        # The add-on holds this place's gateway's token when Compose makes it again, and the seed gets the Test token
        self.assertEqual(held[0], (["up"], envfiles.read(".env")[test_platforms.gateway_key(other["id"],
                                                                                           "MY_ACCOUNT")]))
        self.assertEqual(calls[0][0], UP)
        self.assertEqual(calls[1][1]["env"]["APIM_ENV"], TEST)
        self.assertIn("Restarts: Hybrid gateway", job.lines)

    def test_a_token_that_names_no_gateway_of_the_platform_counts_as_not_set(self):
        # Deleted in the Cloud console: the add-on holds a token no listed gateway matches
        gateway = self.ready()
        key = test_platforms.gateway_key(gateway["id"], "MY_ACCOUNT")
        for held in (fake_cloud.gateway_token("ghost", DEV), None):
            with self.subTest(held=bool(held)):
                envfiles.write(ADDON_ENV, {"HYBRID_CLOUD_TOKEN": held})
                self.deploy()
                job, calls = self.execute()
                self.assertTrue(job.ok, job.lines)
                self.assertEqual(envfiles.read(ADDON_ENV)["HYBRID_CLOUD_TOKEN"], envfiles.read(".env")[key])

    def test_a_swap_that_fails_fails_the_job_before_anything_starts(self):
        self.ready()
        self.patch(platforms, "swap_gateway", side_effect=ValueError("Its token is not kept here"))
        envfiles.write(ADDON_ENV, {"HYBRID_CLOUD_TOKEN": None})
        self.deploy()
        job, calls = self.execute()
        self.assertEqual((job.ok, job.error(), calls, self.written), (False, "Its token is not kept here", [], []))

    def test_the_job_waits_for_the_platform_to_say_the_gateway_is_connected(self):
        self.ready()
        answers = iter([False, False, True])
        asked = self.patch(playgrounds, "_connected", side_effect=lambda uid, gateway: next(answers))
        self.deploy()
        job, calls = self.execute()
        self.assertTrue(job.ok, job.lines)
        self.assertEqual(asked.call_count, 3)
        self.assertIn("Waiting for the hybrid gateway to connect to Gravitee Cloud", job.lines)
        self.assertEqual(len(calls), 2, "the seed ran once it was connected")

    def test_a_gateway_that_never_connects_ends_the_job_before_the_seed(self):
        self.ready()
        self.patch(playgrounds, "_connected", return_value=False)
        self.deploy()
        job, calls = self.execute()
        self.assertEqual((job.ok, job.error()), (False, "Hybrid gateway did not connect to Gravitee Cloud"))
        self.assertEqual([command for command, _ in calls], [UP])
        self.assertEqual([phase["name"] for phase in job.phases], ["Add-ons", "Apps and services", "Gateway"])

    def test_the_wait_is_two_minutes_at_most(self):
        self.assertEqual((self.wait, playgrounds.NOT_CONNECTED),
                         (120, "Hybrid gateway did not connect to Gravitee Cloud"))

    def test_a_job_canceled_while_it_waits_stops_waiting(self):
        job = Job("Test", [])
        self.patch(playgrounds, "GATEWAY_WAIT", 60)
        asked = self.patch(playgrounds, "_connected", side_effect=lambda uid, gateway: job.cancel() or False)
        playgrounds._wait_connected(job, "my-account", "g")
        self.assertEqual((job.canceled, asked.call_count), (True, 1))

    def test_the_platform_is_asked_whether_the_gateway_is_connected(self):
        gateway = self.ready()
        self.assertTrue(playgrounds._connected("my-account", gateway["id"]))
        self.assertFalse(playgrounds._connected("my-account", "another"))
        self.fake.state.connect_after = 3600
        self.assertFalse(playgrounds._connected("my-account", gateway["id"]))
        platforms.remove("my-account")
        with self.assertRaises(JobFailed):
            playgrounds._connected("my-account", gateway["id"])

    # --- The seed and its record -------------------------------------------------------------------------------------

    def test_the_folder_of_the_record_is_made_before_the_seed_runs_and_names_the_environment(self):
        self.ready()
        self.deploy()
        folder, existed = self.root / STATE_FOLDER / "my-account" / DEV, []

        def seen(command, options):
            if "run" in command:
                existed.append((folder / "meta.json").is_file())
        self.execute(seen=seen)
        self.assertEqual(existed, [True])
        self.assertEqual(json.loads((folder / "meta.json").read_text(encoding="utf-8")),
                         {"environment": "Development"})

    def test_a_deploy_leaves_the_record_of_this_machine_as_it_is_and_the_reverse(self):
        self.ready()
        local = self.record(None, "APIs", "APIs", "Applications")
        before = local.read_bytes()

        def seeds(command, options):   # what the seed does in its folder: records what it creates
            if "run" in command:
                self.record(PLACE, "APIs")
        self.deploy()
        job, _ = self.execute(seen=seeds)
        self.assertTrue(job.ok, job.lines)
        self.assertEqual(local.read_bytes(), before)
        remote = self.root / STATE_FOLDER / "my-account" / DEV / "created.json"
        after = remote.read_bytes()
        self.assertEqual(playgrounds.created("hotels", PLACE), [{"what": "APIs", "count": 1}])
        # This machine's start, Re-seed and forget leave the record of the place as it is
        self.state, self.containers = on(self.project, playground="hotels"), running(*self.core, "hotels-api")
        self.patch(compose, "run", lambda cmd, **how: (0, "0\n", ""))
        playgrounds.start(self.project, "hotels", [])
        playgrounds.reseed(self.project, "hotels")
        playgrounds.forget("hotels")
        self.assertEqual((remote.read_bytes(), local.read_text(encoding="utf-8")), (after, "[]"))
        self.assertEqual(playgrounds.created("hotels", PLACE), [{"what": "APIs", "count": 1}])

    def test_a_seed_that_fails_gives_its_last_line_and_leaves_the_record_empty(self):
        self.ready()
        local = self.record(None, "APIs")
        before = local.read_bytes()
        self.deploy()
        job, calls = self.execute({"run": (["[seed] Seeding", "[seed] ✗ Gravitee Cloud refuses the API: no license"],
                                           1)})
        self.assertEqual((job.ok, job.error()), (False, "Gravitee Cloud refuses the API: no license"))
        self.assertEqual(job.lines[-1], "Gravitee Cloud refuses the API: no license")
        self.assertEqual((playgrounds.created("hotels", PLACE), local.read_bytes()), ([], before))
        self.assertEqual(places.deployed("hotels", playgrounds.created), [])

    def test_a_seed_that_prints_nothing_fails_with_the_exit_code(self):
        self.ready()
        self.deploy()
        job, _ = self.execute({"run": ([], 3)})
        self.assertEqual((job.ok, job.error()), (False, "Failed with exit code 3"))

    def test_a_cloud_token_that_expired_since_the_request_fails_the_job_in_plain_words(self):
        self.ready()
        self.deploy()
        envfiles.write(".env", {DEV_KEY: fake_cloud.CLOUD_TOKEN_EXPIRED})
        job, calls = self.execute()
        self.assertEqual((job.ok, job.error(), [command for command, _ in calls]),
                         (False, "This Cloud Token has expired", [UP]))

    # --- Start, Deploy and Re-seed -----------------------------------------------------------------------------------

    def test_a_start_seeds_only_a_place_that_holds_no_record(self):
        self.ready()
        self.record(PLACE, "APIs")
        self.deploy()
        title, steps, more = self.started(self.jobs)
        self.assertEqual(more["phases"], ["Apps and services", "Gateway"])
        job, calls = self.execute()
        self.assertEqual(([command for command, _ in calls], job.ok), ([UP], True))

    def test_the_reseed_route_is_a_deploy_where_nothing_is_recorded_and_a_reseed_where_something_is(self):
        self.ready()
        self.state, self.containers = on(self.project, ["hybrid"], "hotels"), running(
            "apim-hybrid-gateway", "hybrid-redis", "hotels-api")
        playgrounds.reseed(self.project, "hotels", PLACE)
        title, steps, more = self.started(self.jobs)
        self.assertEqual((title, more["key"], more["verb"], more["phases"], more["changes"]), (
            f"Deploy Hotels to {DEVELOPMENT}", "playground:hotels", "deploy", ["Gateway", "Seed"], None))
        job, calls = self.execute()
        self.assertEqual(([command for command, _ in calls], job.ok), ([seed()], True), job.lines)
        self.record(PLACE, "APIs")
        playgrounds.reseed(self.project, "hotels", PLACE)
        title, steps, more = self.started(self.jobs)
        self.assertEqual((title, more["verb"]), (f"Re-seed Hotels in {DEVELOPMENT}", "reseed"))

    def test_a_reseed_brings_the_gateway_that_does_not_run(self):
        self.ready()
        self.state, self.containers = on(self.project, playground="hotels"), running("hotels-api")
        playgrounds.reseed(self.project, "hotels", PLACE)
        title, steps, more = self.started(self.jobs)
        self.assertEqual(more["phases"], ["Apps and services", "Gateway", "Seed"])
        job, calls = self.execute()
        self.assertEqual([command for command, _ in calls], [[*COMPOSE, "up", "-d", "--no-deps", "apim-hybrid-gateway",
                                                              "hybrid-redis"], seed()])

    def test_a_reseed_of_a_playground_that_is_off_is_refused_as_this_machine_s_is(self):
        self.ready()
        with self.assertRaisesRegex(RuntimeError, "^Hotels is off$"):
            playgrounds.reseed(self.project, "hotels", PLACE)
        self.jobs.start.assert_not_called()


class Deleting(Deploys):
    """What the seed created in a place: with Stop, and alone."""

    def on(self, *core):
        self.state, self.containers = on(self.project, ["hybrid"], "hotels"), running(*core, "hotels-api")

    def test_a_stop_that_keeps_what_it_created_asks_no_place(self):
        self.ready()
        self.record(PLACE, "APIs")
        self.on()
        asked = len(self.fake.state.asked)
        playgrounds.stop(self.project, "hotels", False, [])
        job, calls = self.execute()
        self.assertEqual((job.ok, [phase["name"] for phase in job.phases], len(self.fake.state.asked)),
                         (True, ["Stop the apps"], asked))
        self.assertEqual(playgrounds.created("hotels", PLACE), [{"what": "APIs", "count": 1}])

    def test_delete_what_it_created_covers_every_place_with_a_record(self):
        self.ready()
        self.ready(TEST, "other")
        self.record(None, "APIs")
        self.record(PLACE, "APIs")
        self.record(TESTING, "APIs")
        self.on(*self.core)
        playgrounds.stop(self.project, "hotels", True, [], place=PLACE)
        title, steps, more = self.started(self.jobs)
        self.assertEqual((title, more["verb"], more["phases"]), ("Stop Hotels", "stop",
                                                                 ["Delete what it created", "Stop the apps"]))
        job, calls = self.execute()
        self.assertTrue(job.ok, job.lines)
        commands = [command for command, _ in calls]
        # This machine's by the seed's own service, then each place apart with its settings; then the apps stop
        self.assertTrue(removes(commands[0], "hotels-seed"), commands[0])
        self.assertEqual(commands[1:3], [seed("--remove"), seed("--remove")])
        self.assertFalse("APIM_TOKEN" in calls[0][1]["env"], "This machine's delete gets no place's settings")
        self.assertEqual([call[1]["env"]["APIM_ENV"] for call in calls[1:3]], [DEV, TEST])
        self.assertIn(f"Deletes from {DEVELOPMENT}", job.lines)
        self.assertIn(f"Deletes from {TEST_ENVIRONMENT}", job.lines)
        self.assertEqual(commands[3], [*compose.by_name(), "rm", "--stop", "--force", "hotels-api"])
        self.assertNoSecret(job.lines, commands, job.summary())

    def test_a_stop_is_refused_with_the_reason_of_the_first_place_that_cannot_be_reached(self):
        self.ready()
        self.ready(TEST, "other")
        self.record(PLACE, "APIs")
        self.record(TESTING, "APIs")
        self.on(*self.core)
        envfiles.write(".env", {DEV_KEY: fake_cloud.CLOUD_TOKEN_EXPIRED})
        with self.assertRaisesRegex(ValueError, f"^Cloud Token for {re.escape(DEVELOPMENT)} has expired$"):
            playgrounds.stop(self.project, "hotels", True, [])
        self.jobs.start.assert_not_called()
        self.assertEqual(self.written, [])
        # Without deleting, nothing is asked of it
        playgrounds.stop(self.project, "hotels", False, [])
        self.assertTrue(self.jobs.start.called)

    def test_this_machine_comes_first_in_the_reasons(self):
        self.ready()
        self.record(None, "APIs")
        self.record(PLACE, "APIs")
        self.on()   # the core does not run: the seed of this machine would wait for the management API
        envfiles.write(".env", {DEV_KEY: fake_cloud.CLOUD_TOKEN_EXPIRED})
        with self.assertRaisesRegex(RuntimeError, "^Management API is not running$"):
            playgrounds.stop(self.project, "hotels", True, [])

    def test_a_place_that_is_gone_cannot_be_reached(self):
        self.ready()
        self.record("ghost/gone", "APIs")
        self.on()
        with self.assertRaisesRegex(ValueError, "^Account not connected$"):
            playgrounds.stop(self.project, "hotels", True, [])
        self.record("ghost/gone")
        self.record("my-account/gone", "APIs")
        with self.assertRaisesRegex(ValueError, "^Environment not found$"):
            playgrounds.stop(self.project, "hotels", True, [])

    def test_a_place_with_a_record_needs_the_playground_on(self):
        self.ready()
        self.record(PLACE, "APIs")
        with self.assertRaisesRegex(RuntimeError, "^Hotels is off$"):
            playgrounds.stop(self.project, "hotels", True, [])

    def test_a_record_that_holds_nothing_is_no_place_to_delete_from(self):
        self.ready()
        self.record(PLACE)
        self.on()
        asked = len(self.fake.state.asked)
        playgrounds.stop(self.project, "hotels", True, [])
        job, calls = self.execute()
        self.assertIn("Nothing recorded as created by this playground", job.lines)
        self.assertEqual((len(self.fake.state.asked), len(calls)), (asked, 1))

    def test_a_delete_that_fails_ends_the_job_with_the_seed_s_reason_and_stops_nothing(self):
        self.ready()
        self.ready(TEST, "other")
        self.record(PLACE, "APIs")
        self.record(TESTING, "APIs")
        self.on(*self.core)
        playgrounds.stop(self.project, "hotels", True, [])
        job, calls = self.execute({"--remove": (["[seed] ✗ Gravitee Cloud refuses this token"], 1)})
        self.assertEqual((job.ok, job.error(), len(calls), self.written),
                         (False, "Gravitee Cloud refuses this token", 1, []))
        self.assertEqual(playgrounds.created("hotels", PLACE), [{"what": "APIs", "count": 1}])

    # --- One place --------------------------------------------------------------------------------------------------

    def test_delete_place_deletes_what_the_seed_created_in_that_place_only(self):
        self.ready()
        self.ready(TEST, "other")
        local = self.record(None, "APIs")
        self.record(PLACE, "APIs", "APIs")
        other = self.record(TESTING, "APIs")
        before = local.read_bytes(), other.read_bytes()
        self.on(*self.core)
        playgrounds.delete_place(self.project, "hotels", PLACE)
        title, steps, more = self.started(self.jobs)
        self.assertEqual((title, more["key"], more["verb"], more["phases"]), (
            f"Delete what Hotels created in {DEVELOPMENT}", "playground:hotels", "delete", ["Delete what it created"]))
        job, calls = self.execute()
        self.assertTrue(job.ok, job.lines)
        self.assertEqual([command for command, _ in calls], [seed("--remove")])
        self.assertEqual(calls[0][1]["env"]["STATE_DIR"], f"/state/my-account/{DEV}")
        self.assertEqual((local.read_bytes(), other.read_bytes()), before)
        self.assertEqual(self.written, [])
        self.assertNoSecret(job.lines, job.summary(), calls[0][0])

    def test_delete_place_that_fails_keeps_the_record(self):
        self.ready()
        self.record(PLACE, "APIs")
        self.on()
        playgrounds.delete_place(self.project, "hotels", PLACE)
        job, _ = self.execute({"--remove": (["[seed] ✗ Cloud does not answer"], 1)})
        self.assertEqual((job.ok, job.error()), (False, "Cloud does not answer"))
        self.assertEqual(playgrounds.created("hotels", PLACE), [{"what": "APIs", "count": 1}])

    def test_delete_place_is_refused_in_words(self):
        self.ready()
        with self.assertRaisesRegex(RuntimeError, "^Hotels is off$"):
            playgrounds.delete_place(self.project, "hotels", PLACE)
        self.on()
        with self.assertRaisesRegex(RuntimeError, "^Nothing recorded as created there$"):
            playgrounds.delete_place(self.project, "hotels", PLACE)
        self.record(PLACE, "APIs")
        for place in ("machine", "../x", "my-account", 5, None):
            with self.subTest(place=place), self.assertRaisesRegex(paths.NotFound, "^No place"):
                playgrounds.delete_place(self.project, "hotels", place)
        self.fake.state.revoked.add(envfiles.read(".env")[DEV_KEY])
        with self.assertRaisesRegex(ValueError,
                                    f"^Gravitee Cloud refuses the Cloud Token of {re.escape(DEVELOPMENT)}$"):
            playgrounds.delete_place(self.project, "hotels", PLACE)
        self.project["playgrounds"]["hotels"]["seed"] = None
        with self.assertRaisesRegex(RuntimeError, "^Hotels has no seed$"):
            playgrounds.delete_place(self.project, "hotels", PLACE)
        self.jobs.start.assert_not_called()

    def test_a_place_whose_platform_is_gone_says_so(self):
        self.on()
        self.record("ghost/gone", "APIs")
        with self.assertRaisesRegex(ValueError, "^Account not connected$"):
            playgrounds.delete_place(self.project, "hotels", "ghost/gone")


class Environment(Case):
    """What a job gives a command that holds a secret."""

    def test_a_command_gets_the_variables_it_is_given_and_a_line_does_not_say_them(self):
        job = Job("Test", [])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)   # the pipe of a command is closed when it is collected
            job.exec([sys.executable, "-c", "import os; print(len(os.environ['SECRET']), 'PATH' in os.environ)"],
                     env={"SECRET": "kept-out-of-the-line"})
            self.assertEqual(job.lines[-1], "20 True")
            self.assertNotIn("kept-out-of-the-line", " ".join(job.lines))
            job.exec([sys.executable, "-c", "import os; print('SECRET' in os.environ)"])
            self.assertEqual(job.lines[-1], "False", "for that command alone")
            del job
            gc.collect()


class HostChecks(Case):
    """What a playground asks of this machine (requires.host), checked from the Lab UI."""

    def serve(self):
        """A web server of this machine that answers every request with a model list: (its port, the paths it was
        asked for, which a proxy gets in full)."""
        asked = []

        class Answers(BaseHTTPRequestHandler):
            def do_GET(self):
                asked.append(self.path)
                body = b'{"models": [{"name": "qwen3:1.7b"}]}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Answers)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_port, asked

    def proxied(self):
        """A proxy in the environment, as HTTP_PROXY sets it: the paths it was asked for."""
        port, asked = self.serve()
        proxy = f"http://127.0.0.1:{port}"
        self.start(mock.patch.dict(os.environ, {"HTTP_PROXY": proxy, "http_proxy": proxy, "NO_PROXY": "",
                                                "no_proxy": ""}))
        return asked

    def test_ready_when_it_answers_with_what_is_expected(self):
        port, _ = self.serve()
        address = f"http://localhost:{port}/api/tags"
        self.assertEqual(playgrounds._host_check({"check": address}), (True, "Ready"))
        self.assertEqual(playgrounds._host_check({"check": address, "expect": "qwen3"}), (True, "Ready"))
        self.assertEqual(playgrounds._host_check({"check": address, "expect": "llama"}),
                         (False, "Reachable, but llama is missing"))

    def test_not_reachable_says_why(self):
        found, why = playgrounds._host_check({"check": "http://localhost:9/api/tags"})   # nothing listens on 9
        self.assertFalse(found)
        self.assertRegex(why, r"^Not reachable \(.+\)$")

    def test_only_an_http_or_https_address_is_opened(self):
        secret = self.folder() / "secret.txt"
        secret.write_text("inside", encoding="utf-8")
        for address in (secret.as_uri(), "FILE:///etc/hosts", "ftp://localhost/x", "data:text/plain,inside",
                        "localhost:11434/api/tags", "//localhost:11434/api/tags", "", "http://[unclosed"):
            with self.subTest(address), mock.patch.object(playgrounds.urllib.request, "build_opener") as opener:
                self.assertEqual(playgrounds._host_check({"check": address, "expect": "inside"}),
                                 (False, playgrounds.NOT_WEB))
            opener.assert_not_called()

    def test_this_machine_is_not_asked_through_a_proxy(self):
        # A proxy reaches the internet, not this machine: a program that runs here would show as not reachable
        port, _ = self.serve()
        asked = self.proxied()
        for host in ("localhost", "127.0.0.1"):
            self.assertEqual(playgrounds._host_check({"check": f"http://{host}:{port}/api/tags"}), (True, "Ready"))
        self.assertEqual(asked, [])

    def test_another_machine_is_asked_through_the_proxy_of_the_environment(self):
        asked = self.proxied()
        self.assertEqual(playgrounds._host_check({"check": "http://huggingface.co/api/models", "expect": "qwen3"}),
                         (True, "Ready"))
        self.assertEqual(asked, ["http://huggingface.co/api/models"])

    def test_an_answer_cut_short_is_not_reachable(self):
        with mock.patch.object(playgrounds.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = playgrounds.http.client.IncompleteRead(b"{")
            found, why = playgrounds._host_check({"check": "http://localhost:11434/api/tags"})
        self.assertFalse(found)
        self.assertTrue(why.startswith("Not reachable"))


if __name__ == "__main__":
    unittest.main()
