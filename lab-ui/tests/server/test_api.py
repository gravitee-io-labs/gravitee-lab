"""The HTTP handler on a free port, with what it calls replaced: who may ask, what a failure answers, static files,
the files shown with one, the state when Docker is down, the accept check, what is created, shared and deleted. No
Docker."""

import contextlib
import errno
import http.client
import io
import json
import queue
import re
import socket
import sys
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.error import URLError

import lab_ui
from lab_ui.server import api, stack
from lab_ui.gravitee import cloud, core, places, platforms
from lab_ui.docker import compose, envfiles, files, probe
from lab_ui.library import layers, playgrounds
from lab_ui import paths
from lab_ui.catalogs import gitx, trust
from lab_ui.catalogs.registry import CatalogError
from lab_ui.docker.jobs import Job, Jobs, awaited

from .. import Case, fake_cloud

from ..library import test_layers
from ..gravitee.test_cloud import serving
from ..docker.test_jobs import replay

ADDON = {"kind": "addon", "id": "agent-lens", "name": "AI Agent Lens"}
PROJECT = {
    "name": "Gravitee platform", "core_loaded": True, "license_services": [], "license_usable": True,
    "groups": [{"id": "databases", "name": "Databases", "services": ["mongodb", "redis"]}],
    "addons": {"kafka": {"name": "Kafka", "services": ["kafka", "kafka-ui"]},
               "jade": {"name": "Jade", "services": ["jade"]},
               "tuning": {"name": "Tuning", "services": []},
               "local/whoami": {"name": "Whoami", "services": ["whoami"]}},
    "playgrounds": {"welcome": {"name": "Welcome", "apps": ["hello-app"]}, "local/sandbox": {"name": "Sandbox"}},
    "setups": {"observability": {"name": "Observability", "members": ["jade", "kiosk"]},
               "local/my-demo": {"name": "My demo", "members": ["kafka"]}},
    # Its container gio-<name>, its image (built here or pulled)
    "services": {name: {"role": role, "owner": {"kind": kind, "id": uid}, "container": f"gio-{name}", "image": image,
                        "built": built, "oneshot": role == "seed", "depends_on": [], "label": name,
                        "needs_license": False}
                 for name, kind, uid, role, image, built in [
        ("mongodb", "platform", "databases", None, "mongo:8.0.29", False),
        ("redis", "platform", "databases", None, "redis:8.10.1-alpine", False),
        ("kafka", "addon", "kafka", None, "docker.io/bitnamilegacy/kafka:3.9", False),
        ("kafka-ui", "addon", "kafka", None, "provectuslabs/kafka-ui:latest", False),
        ("jade", "addon", "jade", None, "jadetracing/jade:2.21.0", False),
        ("hello-app", "playground", "welcome", None, "gravitee-lab/hello-app", True),
        ("welcome-seed", "playground", "welcome", "seed", "gravitee-lab/welcome-seed", True)]},
}


class LabUI(Case):
    """The handler listening on a free port."""

    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, args=(0.01,), daemon=True).start()   # stops at once
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        """(status, headers, body) of a request sent as the page sends it: a POST carries the Lab UI's header, and its
        body as JSON, or as it is when it is text."""
        sent = {"X-Gravitee-Lab-UI": "1"} if method == "POST" else {}
        sent.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        self.addCleanup(connection.close)
        payload = body if isinstance(body, str) else json.dumps(body or {})
        connection.request(method, path, payload if method == "POST" else None,
                           {key: value for key, value in sent.items() if value is not None})
        response = connection.getresponse()
        return response.status, response.headers, response.read()


class Access(LabUI):
    def test_post_without_the_lab_ui_header_is_refused(self):
        action = self.patch(api.stack, "action")
        status, _, body = self.request("POST", "/api/stack/start", headers={"X-Gravitee-Lab-UI": None})
        self.assertEqual((status, json.loads(body)), (403, {"error": "Forbidden"}))   # as every failure answers
        action.assert_not_called()

    def test_foreign_host_is_refused(self):
        state = self.patch(api.images, "state")
        action = self.patch(api.stack, "action")
        for method, path in (("GET", "/api/images"), ("POST", "/api/stack/start")):
            status, _, _ = self.request(method, path, headers={"Host": "evil.example:9000"})
            self.assertEqual(status, 403)
        state.assert_not_called()
        action.assert_not_called()

    def test_the_home_page_of_a_refused_host_says_where_to_open_the_lab_ui(self):
        action = self.patch(api.stack, "action")
        for host in ("0.0.0.0:9777", "[::1]:9777", "evil.example"):
            status, headers, body = self.request("GET", "/", headers={"Host": host})
            address = f"http://localhost:{self.server.server_port}"
            self.assertEqual((status, headers["Content-Type"]), (403, "text/html; charset=utf-8"), host)
            self.assertIn(f'Open the Lab UI at <a href="{address}">{address}</a>', body.decode())
            self.assertNotIn(host, body.decode())   # what the request says of itself is not repeated
            self.assertEqual(headers["X-Frame-Options"], "DENY")
        # Everything else stays a refusal for a program: the API, a POST, another page
        for method, path in (("GET", "/api/state"), ("POST", "/"), ("GET", "/js/main.js")):
            status, headers, body = self.request(method, path, headers={"Host": "0.0.0.0:9777"})
            self.assertEqual((status, headers["Content-Type"], json.loads(body)),
                             (403, "application/json", {"error": "Forbidden"}), (method, path))
        action.assert_not_called()

    def test_a_request_from_a_page_of_another_site_is_refused(self):
        # A browser says where a request comes from; a GET needs no header, so this is all that stops it
        state = self.patch(api.images, "state", return_value={"components": []})
        for site in ("cross-site", "same-site"):
            status, _, _ = self.request("GET", "/api/images", headers={"Sec-Fetch-Site": site})
            self.assertEqual(status, 403, site)
        state.assert_not_called()
        self.patch(api.project, "load", return_value=PROJECT)
        for site in ("same-origin", "none"):   # the Lab UI's own page, an address typed or a bookmark
            status, _, _ = self.request("GET", "/api/images", headers={"Sec-Fetch-Site": site})
            self.assertEqual(status, 200, site)
        # A link to the Lab UI on another page (a playground's guide) still opens it
        status, _, _ = self.request("GET", "/", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertNotEqual(status, 403)

    def test_local_names_are_served(self):
        self.patch(api.project, "load", return_value=PROJECT)
        self.patch(api.images, "state", return_value={"components": []})
        for host in ("localhost:9000", "127.0.0.1:9000", "localhost"):
            status, headers, body = self.request("GET", "/api/images", headers={"Host": host})
            self.assertEqual((status, json.loads(body)), (200, {"components": []}))
            self.assertEqual(headers["Cache-Control"], "no-store")


    def test_no_page_frames_the_lab_ui(self):
        # It frames the consoles of a playground and holds the keys to the lab: the page, its API, a refusal and the
        # streams all say that no page may frame it; the page, which a browser runs, says the whole policy of 14.3
        self.patch(api.project, "load", return_value=PROJECT)
        self.patch(api.images, "state", return_value={"components": []})
        for path in ("/", "/js/main.js", "/api/images", "/nothing-here"):
            _, headers, _ = self.request("GET", path)
            policy = api.POLICY if path == "/" else "frame-ancestors 'none'"
            self.assertEqual(headers["Content-Security-Policy"], policy, path)
            self.assertEqual(headers["X-Frame-Options"], "DENY", path)
        _, headers, _ = self.request("GET", "/api/images", headers={"Host": "evil.example"})   # a refusal too
        self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_the_page_carries_exactly_the_policy_the_page_needs(self):
        # test_static reads it from the page's files: the server's header is that, word for word
        from ..web import test_static
        _, headers, _ = self.request("GET", "/")
        self.assertEqual(headers["Content-Security-Policy"], test_static.wanted_policy())
        self.assertEqual(api.POLICY, test_static.wanted_policy())
        self.assertEqual(test_static.policy_problems(headers["Content-Security-Policy"]), [])
        # ... and the refusal of a wrong host is a page too
        _, headers, _ = self.request("GET", "/", headers={"Host": "0.0.0.0:9777"})
        self.assertEqual(headers["Content-Security-Policy"], api.POLICY)

    def test_the_stream_of_a_job_says_it_too(self):
        job = mock.Mock(done=True, lines=[], summary=lambda: {"ok": True})
        self.patch(api.JOBS, "get", return_value=job)
        _, headers, _ = self.request("GET", "/api/jobs/abc/stream")
        self.assertEqual((headers["Content-Security-Policy"], headers["X-Frame-Options"]),
                         ("frame-ancestors 'none'", "DENY"))


class Failures(LabUI):
    def setUp(self):
        super().setUp()
        self.patch(api.project, "load", return_value=PROJECT)

    def test_a_failure_answers_by_its_kind(self):
        cases = [(error, code, str(error)) for error, code in [
            (ValueError("bad value"), 400), (paths.NotFound("No such add-on"), 404),
            (RuntimeError("'Start Kafka' is still running"), 409), (compose.ComposeError("no such file"), 409),
            (URLError("unreachable"), 502), (TimeoutError("too slow"), 502),
            (ConnectionResetError("reset by the registry"), 502)]]
        # What nothing expects is headed by its type: a bare key would not say what failed
        cases += [(KeyError("name"), 500, "KeyError: 'name'"), (OSError("disk full"), 500, "OSError: disk full")]
        state, action = self.patch(api.images, "state"), self.patch(api.stack, "action")
        for error, code, message in cases:
            state.side_effect = action.side_effect = error
            for method, path in (("GET", "/api/images"), ("POST", "/api/stack/start")):
                with self.subTest(error=error, method=method):
                    status, _, body = self.request(method, path)
                    self.assertEqual((status, json.loads(body)), (code, {"error": message}))

    def test_unknown_route(self):
        for method in ("GET", "POST"):
            status, _, body = self.request(method, "/api/nothing")
            self.assertEqual((status, json.loads(body)), (404, {"error": "Not found"}))
        # A group or a service takes start, stop and restart only: the route says so
        for path in ("/api/groups/databases/rm", "/api/services/mongodb/kill"):
            self.assertEqual(self.request("POST", path)[0], 404, path)

    def test_body_must_be_a_json_object(self):
        action = self.patch(api.stack, "action")
        self.assertEqual(self.request("POST", "/api/stack/start", "[1]")[0], 400)
        action.assert_not_called()

    def test_a_body_of_a_size_the_page_never_sends_is_refused_unread(self):
        # The size is told before the body: a request that lies about it would make the server wait for bytes that
        # never come (the client here sends two)
        action = self.patch(api.stack, "action", return_value={"ok": True})
        wrong = "The Content-Length must be a number of bytes"
        large = f"The request is larger than {api.MAX_BODY >> 20} MB"
        for size, code, message in [("-1", 400, wrong), ("-9999999999", 400, wrong), ("12abc", 400, wrong),
                                    ("1e3", 400, wrong), ("1.5", 400, wrong), ("\u00b2", 400, wrong),
                                    (str(api.MAX_BODY + 1), 413, large), ("99999999999999999999", 413, large)]:
            with self.subTest(size=size):
                status, _, body = self.request("POST", "/api/stack/start", headers={"Content-Length": size})
                self.assertEqual((status, json.loads(body)), (code, {"error": message}))
        action.assert_not_called()
        self.assertEqual(self.request("POST", "/api/stack/start")[0], 200)   # what the page sends is read

    def test_a_job_answers_its_summary(self):
        job = Job("Start the stack", [])
        action = self.patch(api.stack, "action", return_value=job)
        status, _, body = self.request("POST", "/api/stack/start")
        self.assertEqual((status, json.loads(body)), (200, job.summary()))
        action.assert_called_once_with(PROJECT, "start", for_core=False, accept=[])
        # The Dashboard's Start all says it is the core's, with what its dialog showed
        self.request("POST", "/api/stack/start", {"core": True, "accept": ["addon:kafka"]})
        self.assertEqual(action.call_args, ((PROJECT, "start"), {"for_core": True, "accept": ["addon:kafka"]}))


class Static(LabUI):
    def setUp(self):
        super().setUp()
        root = self.folder(files={"web/index.html": "<p>lab_ui</p>", "web/js/pages/list.js": "export {};",
                                  "web/js/notes.txt": "not a page file", "web/img/guide.png": "png bytes", "web/js/data.json": "{}",
                                  "secret.js": "outside web/"})
        self.patch(paths, "WEB_DIR", root / "web")

    def test_files_of_web_are_served(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual((status, body), (200, b"<p>lab_ui</p>"))
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(headers["Cache-Control"], "no-cache")
        status, headers, body = self.request("GET", "/js/pages/list.js")
        self.assertEqual((status, body), (200, b"export {};"))
        self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")
        status, headers, body = self.request("GET", "/img/guide.png")
        self.assertEqual((status, body, headers["Content-Type"]), (200, b"png bytes", "image/png"))

    def test_nothing_outside_the_folder_is_served(self):
        for path in ("/js/../index.html", "/js/../../secret.js", "/css/../js/pages/list.js", "/js/%2e%2e/index.html",
                     "/js/pages/../../../secret.js", "/js/notes.txt", "/js/data.json", "/js/missing.js",
                     "/index.html"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)

    def test_favicon(self):
        self.assertEqual(self.request("GET", "/favicon.ico")[0], 204)


class Files(LabUI):
    def test_the_files_shown_with_one(self):
        self.patch(api.project, "load", return_value=PROJECT)
        sections = self.patch(api.files, "sections", return_value=[{"title": "Core", "on": True, "files": []}])
        status, _, body = self.request("GET", "/api/files?path=addons%2Fkafka%2F.env")
        self.assertEqual((status, json.loads(body)), (200, {"sections": sections.return_value}))
        sections.assert_called_once_with(PROJECT, "addons/kafka/.env")
        sections.side_effect = ValueError("Not a file of this project")
        status, _, body = self.request("GET", "/api/files")
        self.assertEqual((status, json.loads(body)), (400, {"error": "Not a file of this project"}))

    def test_a_file_is_saved_with_the_project(self):
        self.patch(api.project, "load", return_value=PROJECT)
        saved = {"content": "services: {}\n", "version": "v2", "problems": []}
        save = self.patch(api.files, "save", return_value=saved)
        sent = {"path": "addons/kafka/compose.yml", "content": "services: {}\n", "version": "v1"}
        status, _, body = self.request("POST", "/api/files/save", sent)
        self.assertEqual((status, json.loads(body)), (200, save.return_value))
        save.assert_called_once_with(PROJECT, "addons/kafka/compose.yml", "services: {}\n", "v1")


class State(LabUI):
    def setUp(self):
        super().setUp()
        self.layers = {"playground": None, "explicit": [], "effective": [], "problems": [], "locked": None}
        self.patch(api.project, "load", return_value=PROJECT)
        self.read = {"raw": {}}   # what the project was read from
        self.patch(api.project, "loaded", return_value=(PROJECT, self.read))
        self.patch(api.layers, "current", return_value=self.layers)
        # What the catalogs say: from this machine's files and memory, never from git, a network or Docker
        self.git = self.patch(api.service, "git_version", return_value=(2, 43, 2))
        self.rows = [{"id": "gravitee", "kind": "git"}]
        self.catalogs = self.patch(api.service, "describe", return_value=self.rows)
        self.content = {"key": "ready", "line": "", "code": None, "progress": None, "address": "", "offer": None}
        self.said = self.patch(api.firstrun, "content", return_value=self.content)
        self.layout = {"phase": "ready", "notice": None}
        self.placed = self.patch(api.layout, "status", return_value=self.layout)
        self.patch(api.license, "features_cached", return_value=None)
        self.patch(api.playgrounds, "created", return_value=[{"what": "APIs", "count": 3}])
        self.patch(api.personal, "skills", return_value={"addon": ".agents/skills/new-addon/SKILL.md"})
        # Where the core runs, from this machine's choice (test_core)
        self.core = self.patch(api.core, "describe", return_value={"place": "machine", "chosen": False})
        self.more = {"root": str(paths.ROOT), "skills": {"addon": ".agents/skills/new-addon/SKILL.md"},
                     "core": {"place": "machine", "chosen": False}}
        # ... and what the state says of the catalogs: git, the rows, the content and the layout
        self.catalogued = {"git": {"available": True, "version": "2.43.2"}, "catalogs": self.rows,
                           "content": self.content, "layout": self.layout}
        self.patch(api, "JOBS", Jobs())
        self.name = self.patch(api.compose, "project_name", return_value="gravitee")
        self.build = self.patch(api.model, "build", side_effect=lambda *args, **more: {
            "containers": sorted(args[2]), "docker": args[6]})
        self.memory = self.patch(api.compose, "docker_memory", return_value=16)
        self.saved = self.patch(api.project, "saved", return_value={"hashes": {"mongodb": "h"}, "images": {}})
        self.status = self.patch(api.license, "status", return_value={"status": "none"})
        # What is starred is said of every item (test_starred)
        self.mark = self.patch(api.starred, "mark")
        # No platform is registered here, and none of its playgrounds recorded anything in a place
        self.registered = self.patch(api.platforms, "read", return_value=[])
        self.held = self.patch(api.places, "deployed", return_value=[])

    def test_docker_down_is_a_state_not_a_failure(self):
        self.patch(api.compose, "ps", side_effect=compose.ComposeError("Cannot connect to the Docker daemon"))
        status, _, body = self.request("GET", "/api/state")
        down = {"error": "Cannot connect to the Docker daemon", "memory": None}
        self.assertEqual((status, json.loads(body)), (200, {"containers": [], "docker": down, **self.more}))
        created = [{"what": "APIs", "count": 3}]
        self.build.assert_called_once_with(PROJECT, self.layers, {}, None, api.JOBS.failed_job, None, down,
                                           {"welcome": created, "local/sandbox": created}, saved=None,
                                           license_status={"status": "none"}, platforms=[],
                                           deployed={"welcome": [], "local/sandbox": []}, changed=None, **self.catalogued)
        self.memory.assert_not_called()
        self.saved.assert_not_called()

    def test_a_core_file_that_does_not_load_is_like_docker_down(self):
        # Compose reads nothing without it: what runs is not known, nothing is asked of Docker
        ps = self.patch(api.compose, "ps", return_value={"mongodb": {}})
        self.patch(api.project, "loaded", return_value=({**PROJECT, "core_loaded": False}, self.read))
        status, _, body = self.request("GET", "/api/state")
        self.assertEqual((status, json.loads(body)["containers"]), (200, []))
        ps.assert_not_called()
        self.memory.assert_not_called()
        self.assertIsNone(self.build.call_args.kwargs["saved"])

    def test_what_runs_behind_what_is_saved_and_the_license_it_runs(self):
        # Each container says which license it runs; what is saved is asked of Compose for the state
        ps = self.patch(api.compose, "ps", return_value={"mongodb": {}})
        self.request("GET", "/api/state")
        ps.assert_called_once_with(api.project.LICENSE_KEY)
        self.assertEqual(self.build.call_args.kwargs, {"saved": {"hashes": {"mongodb": "h"}, "images": {}},
                                                       "license_status": {"status": "none"}, "platforms": [],
                                                       "deployed": {"welcome": [], "local/sandbox": []},
                                                       "changed": {}, **self.catalogued})

    def test_the_platforms_and_what_each_playground_holds_in_them_are_given_to_the_state(self):
        # Read from this machine's files, each playground by its own record: the way it reads them (places.deployed)
        # is given what reads a record
        record = {"id": "my-account", "name": "My Account", "kind": "cloud", "url": "u",
                  "account": {"id": "a", "hrid": "", "name": ""}}
        held = [{"place": "my-account/dev", "environment": "Development", "created": [{"what": "APIs", "count": 3}]}]
        self.registered.return_value = [record]
        self.held.side_effect = lambda pid, created: held if pid == "welcome" else []
        self.patch(api.compose, "ps", return_value={})
        self.request("GET", "/api/state")
        self.assertEqual(self.build.call_args.kwargs["platforms"], [record])
        self.assertEqual(self.build.call_args.kwargs["deployed"], {"welcome": held, "local/sandbox": []})
        self.assertEqual({call.args[1] for call in self.held.call_args_list}, {api.playgrounds.created})

    def test_the_status_of_the_layout_is_forwarded_as_it_is_whatever_phase_the_move_is_in(self):
        # the page draws the notice from it (a closed dialog, a waiting one, a Try again); the server adds nothing to it and
        # takes nothing out, and reads it from the disk at every state, not from the project: a move that failed is in memory
        self.patch(api.compose, "ps", return_value={})
        notice = {"id": "2026-10-17T09:00:00Z", "title": "Lab updated", "text": "", "button": "Close", "moved": [],
                  "rows": [{"key": "personal", "label": "Now personal", "items": ["scratch"], "hint": "Not in a catalog"}]}
        for status in ({"phase": "ready", "notice": notice}, {"phase": "ready", "notice": None},
                       {"phase": "waiting", "notice": {**notice, "rows": [{"key": "settings", "label": "Settings",
                                                                          "items": ["Not changed"], "hint": ""}]}},
                       {"phase": "failed", "notice": {**notice, "text": "Could not move your settings. Nothing was deleted.",
                                                       "rows": [], "button": "Try again"}}):
            with self.subTest(phase=status["phase"]):
                self.placed.return_value = status
                self.assertEqual(self.request("GET", "/api/state")[0], 200)
                self.assertIs(self.build.call_args.kwargs["layout"], status, "the very object layout.status() answered")

    def test_a_core_file_never_loaded_is_not_docker_down(self):
        # Without its name there is no project to ask Docker about: the file is among the project's problems
        self.name.side_effect = compose.ComposeError("yaml: line 12: did not find expected key")
        ps = self.patch(api.compose, "ps", return_value={"mongodb": {}})
        status, _, body = self.request("GET", "/api/state")
        self.assertEqual((status, json.loads(body)["docker"]["error"]), (200, None))
        ps.assert_not_called()

    def test_docker_up(self):
        self.patch(api.compose, "ps", return_value={"redis": {}, "mongodb": {}})
        status, _, body = self.request("GET", "/api/state")
        docker = {"error": None, "memory": 16}
        # With where the project is and the skills it has: what the page gives an AI agent
        self.assertEqual((status, json.loads(body)),
                         (200, {"containers": ["mongodb", "redis"], "docker": docker, **self.more}))

    def test_what_docker_says_moves_the_last_job(self):
        # Compose returned once MongoDB ran: its health, read with the state, is what the job still waited for
        job = Job("Start Databases", [["docker", "compose", "up"]], key="group:databases", verb="start",
                  waits=[awaited(PROJECT["services"], start=["mongodb"])])
        replay(job, [" Container gio-mongodb Started "])
        api.JOBS.jobs.append(job)
        api.JOBS.current = job
        ps = self.patch(api.compose, "ps", side_effect=compose.ComposeError("Cannot connect to the Docker daemon"))
        self.request("GET", "/api/state")
        self.assertEqual(self.build.call_args[0][3]["progress"], 0.5)   # Docker did not answer: nothing more known
        ps.side_effect, ps.return_value = None, {"mongodb": {"state": "running", "health": "healthy"}}
        self.request("GET", "/api/state")
        self.assertEqual((job.progress, self.build.call_args[0][3]["progress"]), (1, 1))

    def test_unchanged_state_is_not_sent_again(self):
        ps = self.patch(api.compose, "ps", return_value={"mongodb": {}})
        status, headers, _ = self.request("GET", "/api/state")
        tag = headers["ETag"]
        self.assertEqual(status, 200)
        status, headers, body = self.request("GET", "/api/state", headers={"If-None-Match": tag})
        self.assertEqual((status, body, headers["ETag"]), (304, b"", tag))
        ps.return_value = {"mongodb": {}, "redis": {}}
        status, headers, _ = self.request("GET", "/api/state", headers={"If-None-Match": tag})
        self.assertEqual(status, 200)
        self.assertNotEqual(headers["ETag"], tag)


class Frame(LabUI):
    """What one UI of a playground is for the page that shows it next to the guide."""

    GAMMA = "http://localhost:8086"

    def setUp(self):
        super().setUp()
        link = {"label": "Gamma Console", "url": self.GAMMA, "port": "8086", "service": "gamma-ui", "section": "", "legacy": False}
        playground = {"services": [], "addons": [], "consoles": ["gamma-ui"]}
        self.patch(api.project, "load", return_value={**PROJECT, "links": [link],
                                                      "playgrounds": {"welcome": playground, "local/mine": playground}})
        self.patch(probe, "KEPT", {})

    def ask(self, path, **headers):
        status, _, body = self.request("GET", path, headers={"Host": "localhost:9000", **headers})
        return status, json.loads(body)

    def test_the_page_is_the_one_that_asks_by_the_name_it_was_opened_with(self):
        looked = self.patch(probe, "look", return_value={"answers": True, "frameable": True, "reason": ""})
        url = "/api/playgrounds/welcome/frame?url=http%3A%2F%2Flocalhost%3A8086"
        self.assertEqual(self.ask(url), (200, {"answers": True, "frameable": True, "reason": ""}))
        looked.assert_called_once_with(self.GAMMA, "http://localhost:9000")
        self.ask(url, Host="127.0.0.1:9000")
        self.assertEqual(looked.call_args.args, (self.GAMMA, "http://127.0.0.1:9000"))

    def test_a_personal_playground_is_asked_by_its_id(self):
        looked = self.patch(probe, "look", return_value={"answers": False, "frameable": False, "reason": "refused"})
        status, body = self.ask("/api/playgrounds/local/mine/frame?url=http%3A%2F%2Flocalhost%3A8086")
        self.assertEqual((status, body["answers"]), (200, False))
        looked.assert_called_once()

    def test_an_address_the_playground_does_not_declare_is_a_bad_request_and_never_asked(self):
        looked = self.patch(probe, "look")
        for url in ("", "?url=http%3A%2F%2Flocalhost%3A8084", "?url=http%3A%2F%2F169.254.169.254%2F", "?url=file%3A%2F%2F%2Fetc%2Fpasswd"):
            status, body = self.ask(f"/api/playgrounds/welcome/frame{url}")
            self.assertEqual((status, body), (400, {"error": "Not a UI of this playground"}), url)
        status, _ = self.ask("/api/playgrounds/gone/frame?url=http%3A%2F%2Flocalhost%3A8086")
        self.assertEqual(status, 400)
        looked.assert_not_called()

    def test_it_is_for_the_lab_uis_own_page(self):
        looked = self.patch(probe, "look")
        status, _ = self.ask("/api/playgrounds/welcome/frame?url=http%3A%2F%2Flocalhost%3A8086", **{"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        looked.assert_not_called()


class Switch(LabUI):
    """Start and stop of an add-on or a setup: the plan is made again and checked against what the page showed."""

    def setUp(self):
        super().setUp()
        self.patch(api.project, "load", return_value=PROJECT)
        self.patch(api.compose, "ps", return_value={})
        self.patch(api.layers, "current", return_value="state")
        self.patch(api.model, "up", return_value="up")
        self.patch(api.model, "behind", return_value={"jade"})
        self.patch(api.project, "saved", return_value={"hashes": {}, "images": {}})
        self.plan = self.patch(api.layers, "plan", side_effect=self.found, return_value=self.planned())
        self.apply = self.patch(api.layers, "apply", return_value=Job("Start Jade", []))

    @staticmethod
    def found(project, state, up, kind, uid, do, behind=None):
        """The plan of an item the project has: one it does not have is not found, as layers.plan says."""
        layers.lookup(project, kind, uid)
        return mock.DEFAULT

    @staticmethod
    def planned(blocked=None, stops=(), label="Start"):
        """A plan of Jade: the item itself is the row of Jade."""
        return {"blocked": blocked, "stops": list(stops), "turns_off": [], "itself": {"addon:jade"},
                "label": label}

    def test_blocked_plan_is_refused(self):
        self.plan.return_value = self.planned("Jade needs settings")
        status, _, body = self.request("POST", "/api/addons/jade/start")
        self.assertEqual((status, json.loads(body)), (409, {"error": "Jade needs settings"}))
        self.apply.assert_not_called()

    def test_a_stop_the_page_did_not_show_is_refused(self):
        self.plan.return_value = self.planned(stops=[ADDON])
        status, _, body = self.request("POST", "/api/addons/jade/start", {"accept": ["agent-lens"]})
        self.assertEqual((status, json.loads(body)), (409, {"error": "Would also stop AI Agent Lens"}))
        self.apply.assert_not_called()

    def test_accepted_stops_apply(self):
        self.plan.return_value = self.planned(stops=[ADDON])
        status, _, _ = self.request("POST", "/api/addons/jade/start", {"accept": ["addon:agent-lens"]})
        self.assertEqual(status, 200)
        # What runs behind what is saved: its start makes it again
        self.plan.assert_called_once_with(PROJECT, "state", "up", "addon", "jade", "start", behind={"jade"})
        self.apply.assert_called_once_with(PROJECT, self.plan.return_value, "Start Jade")

    def test_the_job_is_named_as_its_button(self):
        # "Turn off Jade": what the click did, in the activity drawer too
        self.plan.return_value = self.planned(label="Turn off")
        self.assertEqual(self.request("POST", "/api/addons/jade/stop")[0], 200)
        self.apply.assert_called_once_with(PROJECT, self.plan.return_value, "Turn off Jade")

    def test_what_belongs_to_this_machine_and_unknown_ids(self):
        self.assertEqual(self.request("POST", "/api/setups/local/my-demo/start")[0], 200)
        self.plan.assert_called_once_with(PROJECT, "state", "up", "setup", "local/my-demo", "start", behind={"jade"})
        self.assertEqual(self.request("POST", "/api/addons/local/whoami/stop")[0], 200)
        self.plan.assert_called_with(PROJECT, "state", "up", "addon", "local/whoami", "stop", behind={"jade"})
        for path in ("/api/addons/nothing/start", "/api/addons/local/start", "/api/addons/local/nothing/start",
                     "/api/addons/local/whoami/more/start", "/api/addons/other/whoami/start"):
            self.assertEqual(self.request("POST", path)[0], 404, path)


class Accepted(LabUI):
    """What the page sends back of the rows its dialog showed is a list of keys. A text would be searched by pieces of it, and a
    mapping by its keys: the gate that asks for what the page showed would take them for it, so no route takes either."""

    ROUTES = [("POST", "/api/addons/kafka/start"), ("POST", "/api/setups/observability/start"),
              ("POST", "/api/playgrounds/welcome/start"), ("POST", "/api/playgrounds/welcome/stop"),
              ("POST", "/api/playgrounds/welcome/reseed"), ("POST", "/api/platforms/my-account/gateways/gw-1/start"),
              ("POST", "/api/stack/start")]

    def setUp(self):
        super().setUp()
        self.patch(api.project, "load", return_value=PROJECT)
        self.patch(api.compose, "ps", return_value={})
        self.patch(api.layers, "current", return_value="state")
        self.patch(api.model, "up", return_value="up")
        self.patch(api.model, "behind", return_value=set())
        self.patch(api.project, "saved", return_value={"hashes": {}, "images": {}})
        self.plan = self.patch(api.layers, "plan", return_value={"blocked": None, "stops": [{"kind": "addon", "id": "kafka-desk",
                               "name": "Kafka Desk"}], "turns_off": [], "itself": {"addon:kafka"}, "label": "Start"})
        self.apply = self.patch(api.layers, "apply", return_value=Job("Start Kafka", []))
        self.calls = {name: self.patch(api.playgrounds, name, autospec=True, return_value={}) for name in ("start", "stop", "reseed")}
        self.stack = self.patch(api.stack, "action", return_value={})
        self.gateway = self.patch(api, "gateway_plan", return_value=(self.plan.return_value, {"here": True, "gateway": "kafka"}))

    def test_a_text_a_mapping_or_a_list_of_anything_but_rows_is_a_bad_request_on_every_route(self):
        for method, path in self.ROUTES:
            for sent in ("addon:kafka-desk", {"addon:kafka-desk": True}, 5, True, [5], [["addon:kafka-desk"]], [None],
                         ["addon:kafka-desk", {"id": "x"}]):
                with self.subTest(path=path, sent=sent):
                    status, _, body = self.request(method, path, {"accept": sent})
                    self.assertEqual((status, json.loads(body)), (400, {"error": "What was accepted must be a list of rows"}))
        self.apply.assert_not_called()
        for call in (*self.calls.values(), self.stack):
            call.assert_not_called()

    def test_nothing_sent_is_nothing_accepted_and_a_list_of_rows_goes_through_whole(self):
        for sent in (None, []):
            with self.subTest(sent=sent):
                self.assertEqual(self.request("POST", "/api/stack/start", {"accept": sent})[0], 200)
                self.assertEqual(self.stack.call_args.kwargs["accept"], [])
        self.assertEqual(self.request("POST", "/api/stack/start")[0], 200)
        self.assertEqual(self.stack.call_args.kwargs["accept"], [])
        self.request("POST", "/api/stack/start", {"accept": ["addon:kafka-desk", "playground:welcome"]})
        self.assertEqual(self.stack.call_args.kwargs["accept"], ["addon:kafka-desk", "playground:welcome"])
        # ... and the plan that stops something besides the item is still refused for what was not sent
        status, _, body = self.request("POST", "/api/addons/kafka/start", {"accept": []})
        self.assertEqual((status, json.loads(body)), (409, {"error": "Would also stop Kafka Desk"}))
        self.assertEqual(self.request("POST", "/api/addons/kafka/start", {"accept": ["addon:kafka-desk"]})[0], 200)


class Effects(LabUI):
    """What Apply, or a Save & apply, would recreate: of what is saved, or with a change the editor has not saved."""

    def setUp(self):
        super().setUp()
        self.patch(api.project, "load", return_value=PROJECT)
        self.effects = self.patch(api.stack, "effects", return_value={"restarts": [], "starts": [], "runs": True,
                                                                       "exact": True})
        self.affected = self.patch(api.files, "affected", return_value=["kafka"])

    def test_of_what_is_saved(self):
        status, _, body = self.request("POST", "/api/effects", {})
        self.assertEqual((status, json.loads(body)), (200, {"restarts": [], "starts": [], "runs": True, "exact": True}))
        self.effects.assert_called_once_with(PROJECT, None, ())
        # What is saved could not be read: what it says is not exact either
        self.effects.return_value = {**self.effects.return_value, "exact": False}
        self.assertFalse(json.loads(self.request("POST", "/api/effects", {})[2])["exact"])

    def test_with_a_change_not_saved(self):
        env = self.patch(api.files, "env_hashes", return_value=({"kafka": "h"}, True))
        draft = self.patch(api.files, "draft_hashes", return_value=({"kafka": "h2"}, True))
        self.request("POST", "/api/effects", {"file": ".env", "values": {"GRAVITEE_LICENSE": "x"}})
        env.assert_called_once_with(PROJECT, ".env", {"GRAVITEE_LICENSE": "x"})
        self.effects.assert_called_with(PROJECT, {"kafka": "h"}, ())
        self.request("POST", "/api/effects", {"path": "addons/kafka/compose.yml", "content": "services: {}"})
        draft.assert_called_once_with(PROJECT, "addons/kafka/compose.yml", "services: {}")
        self.effects.assert_called_with(PROJECT, {"kafka": "h2"}, ())
        # What it makes Compose create cannot be known: what the file describes and runs may restart, and it says so
        draft.return_value = (None, False)
        status, _, body = self.request("POST", "/api/effects", {"path": "addons/kafka/compose.yml", "content": "x"})
        self.assertEqual((status, json.loads(body)["exact"]), (200, False))
        self.affected.assert_called_with(PROJECT, "addons/kafka/compose.yml")
        self.effects.assert_called_with(PROJECT, None, ["kafka"])
        # Versions and registries of Images
        drafted = self.patch(api.images, "drafted", return_value=({"kafka": "h3"}, True))
        self.patch(api.images, "changing", return_value=["kafka"])
        self.request("POST", "/api/effects", {"images": {"KAFKA_VERSION": "3.8"}})
        drafted.assert_called_once_with(PROJECT, {"KAFKA_VERSION": "3.8"})
        self.effects.assert_called_with(PROJECT, {"kafka": "h3"}, ())
        self.assertEqual(self.request("POST", "/api/effects", {"images": ["KAFKA_VERSION"]})[0], 400)
        # Only a file of the project: refused as the editor refuses it
        draft.side_effect = ValueError("Not a compose file of this project")
        self.assertEqual(self.request("POST", "/api/effects", {"path": "../x", "content": ""})[0], 400)


class Products(LabUI):
    """What a product loads, with the license's words for its plugins; the login of a registry."""

    def setUp(self):
        super().setUp()
        self.patch(api.project, "load", return_value=PROJECT)

    def test_installed_plugins_say_what_the_license_lacks(self):
        # In the words of a row of the lists, from the server: a plugin whose feature the license does not
        # give; one it gives says nothing
        found = {"bundled": {}, "bundled_error": None, "ext": {}, "features": {"gravitee-policy-a": "feature-a",
                                                                              "gravitee-policy-b": "feature-b"}}
        installed = self.patch(api.plugins, "installed", return_value=found)
        self.start(mock.patch.dict(api.license._known, packs=None))   # no license model: a feature by its id
        self.patch(api.license, "status", return_value={"status": "valid", "date": None})
        self.patch(api.license, "info", return_value={"readable": True, "valid": True, "expired": False,
                                                      "features": ["feature-a"]})
        status, _, body = self.request("GET", "/api/plugins/installed?product=apim")
        installed.assert_called_once_with(PROJECT, "apim")
        self.assertEqual((status, json.loads(body)["licensed"]), (200, {"gravitee-policy-b": {
            "note": "Not in the license", "note_tone": "warn", "note_title": "Uses: feature-b", "note_target": None,
            "note_file": ".env", "note_causes": [{"kind": "license", "features": ["feature-b"]}]}}))
        self.assertNotIn("licensed", found)   # what plugins.installed keeps is not changed
        # Without a license: every one that needs a feature
        api.license.status.return_value = {"status": "none"}
        api.license.info.return_value = {"present": False}
        self.assertEqual(sorted(json.loads(self.request("GET", "/api/plugins/installed?product=apim")[2])["licensed"]),
                         ["gravitee-policy-a", "gravitee-policy-b"])

    def test_a_login_is_the_job_of_the_row_that_asked(self):
        # Its row's button shows it; from Images, the registry's
        login = self.patch(api.images, "login", return_value={})
        self.request("POST", "/api/registry/login", {"host": "graviteeio.azurecr.io", "key": "playground:welcome"})
        login.assert_called_once_with("graviteeio.azurecr.io", "playground:welcome")
        self.request("POST", "/api/registry/login", {"host": "graviteeio.azurecr.io"})
        login.assert_called_with("graviteeio.azurecr.io", None)


class FakeStats:
    """The `docker stats` that pages share: who joined and left, and the loads it reads in turn (None: the first sample
    is on its way), the last one for good."""

    def __init__(self, *loads):
        self.loads, self.cores = list(loads), []
        self.joined, self.left = threading.Event(), threading.Event()

    def join(self):
        self.joined.set()

    def leave(self):
        self.left.set()

    def read(self, cores):
        self.cores.append(cores)
        return self.loads.pop(0) if len(self.loads) > 1 else self.loads[0]


class Dashboard(LabUI):
    """The Dashboard's data, and the load of the containers as an event stream."""

    def setUp(self):
        super().setUp()
        self.patch(api.project, "load", return_value=PROJECT)

    def test_the_data_of_the_page_and_a_fresh_read_on_demand(self):
        read = self.patch(api.dashboard, "read", return_value={"docker": {"engine": "29.7.2"}, "pending": False})
        status, headers, body = self.request("GET", "/api/dashboard")
        self.assertEqual((status, headers["Content-Type"], json.loads(body)),
                         (200, "application/json", {"docker": {"engine": "29.7.2"}, "pending": False}))
        # What runs is the state's: the page reads it from /api/state, this read has none of it
        read.assert_called_once_with(PROJECT, refresh=False)
        for query, refresh in (("?refresh=1", True), ("?refresh=0", False), ("?refresh=yes", False)):
            self.request("GET", f"/api/dashboard{query}")
            read.assert_called_with(PROJECT, refresh=refresh)

    def test_a_failure_answers_as_every_one_does(self):
        self.patch(api.dashboard, "read", side_effect=RuntimeError("Docker said no"))
        status, _, body = self.request("GET", "/api/dashboard")
        self.assertEqual((status, json.loads(body)), (409, {"error": "Docker said no"}))

    def test_the_load_is_streamed_once_the_first_sample_is_there_and_the_last_page_leaving_is_noticed(self):
        stats = self.patch(api.dashboard, "STATS", FakeStats(None, None, {"cpu_percent": 2.5, "containers": 3},
                                                            {"cpu_percent": 7.0, "containers": 3}))
        self.patch(api.dashboard, "cores", return_value=6)
        self.patch(api, "STATS_EVERY", 0.01)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        self.addCleanup(connection.close)
        connection.request("GET", "/api/dashboard/stats")
        response = connection.getresponse()
        self.assertEqual((response.status, response.getheader("Content-Type")), (200, "text/event-stream"))
        self.assertEqual((response.getheader("X-Frame-Options"), response.getheader("Cache-Control")),
                         ("DENY", "no-store"))
        events = []
        for _ in range(3):
            line, blank = response.readline(), response.readline()
            events.append(json.loads(line.decode()[len("data: "):]))
            self.assertEqual(blank, b"\n")
        # Nothing was sent while there was no sample; every event is one the page can draw
        self.assertEqual(events, [{"cpu_percent": 2.5, "containers": 3}, {"cpu_percent": 7.0, "containers": 3},
                                  {"cpu_percent": 7.0, "containers": 3}])
        self.assertTrue(stats.joined.is_set())
        self.assertFalse(stats.left.is_set())
        response.close()   # the page was closed: the next write fails
        connection.close()
        self.assertTrue(stats.left.wait(5))
        self.assertEqual(set(stats.cores), {6})

    def test_a_page_that_goes_before_the_stream_starts_leaves_it_too(self):
        stats = FakeStats({"cpu_percent": 1})
        self.patch(api.dashboard, "STATS", stats)
        self.patch(api.dashboard, "cores", return_value=6)

        class Request:
            def sse_start(self):
                raise BrokenPipeError
        with self.assertRaises(BrokenPipeError):
            api.stream_stats(Request())
        self.assertTrue(stats.joined.is_set() and stats.left.is_set())

    def test_it_is_for_the_lab_uis_own_page(self):
        read = self.patch(api.dashboard, "read")
        stats = self.patch(api.dashboard, "STATS", FakeStats({"cpu_percent": 1}))
        for path in ("/api/dashboard", "/api/dashboard?refresh=1", "/api/dashboard/stats"):
            status, _, _ = self.request("GET", path, headers={"Sec-Fetch-Site": "cross-site"})
            self.assertEqual(status, 403, path)
            status, _, _ = self.request("GET", path, headers={"Host": "evil.example:9000"})
            self.assertEqual(status, 403, path)
        read.assert_not_called()
        self.assertFalse(stats.joined.is_set())


class FakeLogs:
    """What `docker logs -f` prints, then it ends: as its container stops."""

    def __init__(self, lines):
        self.stdout = iter(f"{line}\n" for line in lines)

    def wait(self):
        return 0


class Logs(Case):
    """A service's logs followed, and again once its container runs after a new start."""

    def follow(self, runs, starts, closed_after=None):
        """What a follower puts in its queue: `runs` are what each `docker logs` prints, `starts` what inspect says
        in turn ((running, started)). It ends once it is asked for more than `runs`."""
        lines, closed, spawned, asked = queue.Queue(), threading.Event(), [], iter(starts)
        self.patch(api, "FOLLOW_AGAIN", 0)
        self.patch(api, "_started", lambda container: next(asked))
        runs = list(runs)

        def spawn(command):
            spawned.append(command)
            if not runs:
                closed.set()
                return None
            return FakeLogs(runs.pop(0))
        api.Follower("kafka", "gio-kafka", 300, lines, closed, spawn).run()
        return [lines.get() for _ in range(lines.qsize())], spawned

    def test_followed_again_once_it_runs_after_a_new_start(self):
        first = ["2026-10-03T12:00:00.000000001Z starting", "2026-10-03T12:00:01.000000000Z crashed"]
        again = ["2026-10-03T12:00:01.000000000Z crashed", "2026-10-03T12:00:09.000000000Z starting again",
                 "Error response from daemon: No such container: gio-kafka"]
        found, spawned = self.follow([first, again], [
            (True, "2026-10-03T12:00:00.000000000Z"),        # it runs as the stream opens
            (False, "2026-10-03T12:00:00.000000000Z"),       # it stopped
            (True, "2026-10-03T12:00:08.500000000Z"),        # it runs again, a new start
            (True, "2026-10-03T12:00:08.500000000Z")])
        self.assertEqual(found, [["kafka", "2026-10-03T12:00:00.000000001Z", "starting"],
                                 ["kafka", "2026-10-03T12:00:01.000000000Z", "crashed"],
                                 {"service": "kafka", "timestamp": "2026-10-03T12:00:08.500000000Z"},
                                 ["kafka", "2026-10-03T12:00:09.000000000Z", "starting again"]])
        # The last line read is not sent twice; what Docker says itself is not a line of the container
        self.assertEqual(spawned[:2], [["docker", "logs", "-f", "--timestamps", "--tail", "300", "gio-kafka"],
                                       ["docker", "logs", "-f", "--timestamps", "--since",
                                        "2026-10-03T12:00:01.000000000Z", "gio-kafka"]])

    def test_followed_again_without_a_mark_when_it_did_not_start_again(self):
        found, spawned = self.follow([["2026-10-03T12:00:00.000000001Z a"], []], [
            (True, "2026-10-03T12:00:00.000000000Z"), (True, "2026-10-03T12:00:00.000000000Z"),
            (True, "2026-10-03T12:00:00.000000000Z")])
        self.assertEqual((found, len(spawned)), ([["kafka", "2026-10-03T12:00:00.000000001Z", "a"]], 3))

    def test_a_time_as_a_log_line_writes_it(self):
        for when, stamp in (("2026-10-03T12:44:35.3299Z", "2026-10-03T12:44:35.329900000Z"),
                            ("2026-10-03T12:44:35Z", "2026-10-03T12:44:35.000000000Z"),
                            ("2026-10-03T12:44:35.329927337Z", "2026-10-03T12:44:35.329927337Z")):
            self.assertEqual(api._stamp(when), stamp)

    def test_the_logs_of_a_container_are_read_as_utf8_whatever_the_terminal_says(self):
        self.patch(api.project, "load", return_value=PROJECT)
        self.patch(api, "_started", return_value=(False, None))
        popen = self.patch(api.subprocess, "Popen")
        popen.return_value.stdout = iter([])

        def until_spawned(req, lines):
            deadline = time.time() + 5
            while not popen.called and time.time() < deadline:
                time.sleep(0.01)
        self.patch(api, "send_batches", until_spawned)

        class Request:
            query = {"services": "kafka"}

            def sse_start(self):
                pass
        api.stream_logs(Request())
        self.assertEqual((popen.call_args.kwargs["encoding"], popen.call_args.kwargs["errors"]), ("utf-8", "replace"))
        popen.return_value.kill.assert_called()

    def test_a_start_again_is_its_own_event_after_the_lines_before_it(self):
        sent, lines = [], queue.Queue()
        for item in (["kafka", "t1", "a"], {"service": "kafka", "timestamp": "t2"}, ["kafka", "t3", "b"]):
            lines.put(item)

        class Request:
            def sse_send(self, data, event=None):
                sent.append((event, data))
                if len(sent) == 3:
                    raise BrokenPipeError   # the page closed the stream
        with self.assertRaises(BrokenPipeError):
            api.send_batches(Request(), lines)
        self.assertEqual(sent, [(None, [["kafka", "t1", "a"]]), ("restarted", {"service": "kafka", "timestamp": "t2"}),
                                (None, [["kafka", "t3", "b"]])])


class Personal(LabUI):
    """What is created, shared and deleted, and the other routes of an add-on or a playground of this machine."""

    def setUp(self):
        super().setUp()
        self.patch(api.project, "load", return_value=PROJECT)
        self.patch(api.layout, "waiting", return_value=False)   # a create is refused while the old layout moves (a test says so)

    def test_created_from_the_fields_of_the_form(self):
        created = {"file": ".lab/catalogs/local/addons/whoami/compose.yml"}
        fields = {"name": "Whoami", "description": "Says who answers", "category": "Other"}
        # (the route, what it calls, what the form sends besides, what is given besides): a field not sent is None
        for path, function, more, given in (
                ("/api/addons", "create_addon", {"image": "traefik/whoami", "port": "8098"},
                 {"image": "traefik/whoami", "port": "8098"}),
                ("/api/setups", "save_setup", {"addons": ["kafka"], "id": "local/mine"},
                 {"addons": ["kafka"], "sid": "local/mine", "check_only": False}),
                ("/api/playgrounds", "create_playground", {"setups": ["local/mine"], "addons": ["kafka"]},
                 {"setups": ["local/mine"], "addons": ["kafka"]})):
            with self.subTest(path=path):
                create = self.patch(api.personal, function, return_value=created)
                status, _, body = self.request("POST", path, {**fields, **more})
                self.assertEqual((status, json.loads(body)), (200, created))
                create.assert_called_once_with(PROJECT, **fields, **given)   # no catalog: Personal, as it always was
        create.side_effect = ValueError("Another playground has this name")
        status, _, body = self.request("POST", "/api/playgrounds", fields)
        self.assertEqual((status, json.loads(body)), (400, {"error": "Another playground has this name"}))
        # A setup's save asked first: what it would write again, nothing written
        save = self.patch(api.personal, "save_setup", return_value={"file": "f", "changes": ["c"]})
        self.request("POST", "/api/setups", {**fields, "addons": ["kafka"], "id": "local/mine", "check": True})
        self.assertTrue(save.call_args.kwargs["check_only"])

    def test_the_create_routes_take_the_catalog_they_keep_the_item_in_and_ignore_shared(self):
        # `catalog` in place of `shared`. Not given, the item is Personal; the namespace of an editable folder catalog is
        # given on to personal.py, which writes the item there (and says `not_editable` for any other)
        fields = {"name": "Whoami", "description": "Says who answers", "category": "Other"}
        for path, function, more in (("/api/addons", "create_addon", {"image": "traefik/whoami", "port": "8098"}),
                                     ("/api/setups", "save_setup", {"addons": ["kafka"]}),
                                     ("/api/playgrounds", "create_playground", {"setups": [], "addons": ["kafka"]})):
            with self.subTest(path=path):
                create = self.patch(api.personal, function, return_value={})
                self.request("POST", path, {**fields, **more, "shared": True})   # the project destination is gone
                self.assertNotIn("shared", create.call_args.kwargs)
                self.assertNotIn("catalog", create.call_args.kwargs)
                self.request("POST", path, {**fields, **more, "catalog": "my-catalog"})
                self.assertEqual(create.call_args.kwargs["catalog"], "my-catalog")
                self.assertNotIn("shared", create.call_args.kwargs)
                status, _, body = self.request("POST", path, {**fields, **more, "catalog": ["my-catalog"]})
                self.assertEqual((status, json.loads(body)), (400, {"error": "The catalog must be text"}))
                create.side_effect = CatalogError("not_editable", "Acme Hub cannot take new items from the Lab. Choose "
                                                  "Personal or a folder catalog.", 400)
                status, _, body = self.request("POST", path, {**fields, **more, "catalog": "acme-hub"})
                self.assertEqual((status, json.loads(body)), (400, {
                    "error": "Acme Hub cannot take new items from the Lab. Choose Personal or a folder catalog.",
                    "code": "not_editable"}))

    def test_create_is_refused_while_the_layout_is_being_moved_with_the_line_and_the_code_of_section_11(self):
        # what a create writes is read from files that are not where the on file says
        waiting = self.patch(api.layout, "waiting", return_value=True)
        fields = {"name": "Whoami", "description": "Says who answers", "category": "Other"}
        for path, function in (("/api/addons", "create_addon"), ("/api/setups", "save_setup"),
                               ("/api/playgrounds", "create_playground")):
            with self.subTest(path=path):
                create = self.patch(api.personal, function, return_value={})
                status, _, body = self.request("POST", path, {**fields, "addons": ["kafka"], "image": "x", "port": "1"})
                self.assertEqual((status, json.loads(body)), (409, {"error": "Your settings are being moved. Try again in a moment.",
                                                                    "code": "layout_waiting"}))
                create.assert_not_called()
        waiting.return_value = False
        create = self.patch(api.personal, "create_playground", return_value={})
        self.assertEqual(self.request("POST", "/api/playgrounds", {**fields, "addons": ["kafka"]})[0], 200)

    def test_a_start_or_an_apply_that_waits_for_the_layout_says_the_code_too(self):
        refusal = self.patch(api.stack, "action", side_effect=RuntimeError(api.layers.WAITING))
        status, _, body = self.request("POST", "/api/stack/start")
        self.assertEqual((status, json.loads(body)), (409, {"error": "Your settings are being moved. Try again in a moment.",
                                                            "code": "layout_waiting"}))
        refusal.side_effect = RuntimeError("Wait for Start Ledger")
        status, _, body = self.request("POST", "/api/stack/start")
        self.assertEqual((status, json.loads(body)), (409, {"error": "Wait for Start Ledger"}), "any other refusal is as it was")

    def test_shared_and_deleted_by_kind_and_id(self):
        share = self.patch(api.personal, "share", return_value={"file": "f", "problems": ["p"]})
        delete = self.patch(api.personal, "delete", return_value={})
        self.patch(api, "destinations", return_value={})   # the catalogs a share can go to are the module's, tested below
        status, _, body = self.request("POST", "/api/addons/local/whoami/share", {"check": True})
        self.assertEqual((status, json.loads(body)), (200, share.return_value))
        share.assert_called_once_with(PROJECT, "addon", "local/whoami", check_only=True)
        self.request("POST", "/api/setups/local/mine/share")
        share.assert_called_with(PROJECT, "setup", "local/mine", check_only=False)
        for kind in ("addon", "setup", "playground"):
            status, _, body = self.request("POST", f"/api/{kind}s/local/it/delete")
            self.assertEqual((status, json.loads(body)), (200, delete.return_value))
            delete.assert_called_with(PROJECT, kind, "local/it")
        # An item that is on, then one of the project: the refusal is the answer
        delete.side_effect = RuntimeError("Stop it first")
        status, _, body = self.request("POST", "/api/playgrounds/local/sandbox/delete")
        self.assertEqual((status, json.loads(body)), (409, {"error": "Stop it first"}))
        share.side_effect = ValueError("Only your own add-ons can be shared here")
        self.assertEqual(self.request("POST", "/api/addons/kafka/share")[0], 400)
        self.assertEqual(self.request("POST", "/api/groups/local/it/delete")[0], 404)

    def test_starred_by_kind_and_id(self):
        star = self.patch(api.starred, "star", return_value={})
        self.assertEqual(self.request("POST", "/api/addons/local/whoami/star", {"starred": True})[0], 200)
        star.assert_called_once_with(PROJECT, "addon", "local/whoami", True)
        self.request("POST", "/api/playgrounds/welcome/star", {})
        star.assert_called_with(PROJECT, "playground", "welcome", False)
        star.side_effect = paths.NotFound("No setup nope")
        self.assertEqual(self.request("POST", "/api/setups/nope/star", {"starred": True})[0], 404)

    def test_the_other_routes_take_its_id(self):
        calls = {name: self.patch(target, name, return_value={}) for target, name in (
            (api.stack, "unit_action"), (api.playgrounds, "check"),
            (api.playgrounds, "guide"), (api.playgrounds, "start"), (api.playgrounds, "stop"),
            (api.playgrounds, "reseed"))}
        for method, path, name, arguments in (
                # The actions on the containers of an add-on or a playground: the Stack's, the catalog's Restart
                ("POST", "/api/addons/local/whoami/containers/stop", "unit_action",
                 (PROJECT, "addon", "local/whoami", "stop")),
                ("GET", "/api/playgrounds/local/sandbox/check", "check", (PROJECT, "local/sandbox")),
                ("GET", "/api/playgrounds/local/sandbox/guide", "guide", ("local/sandbox",)),
                ("POST", "/api/playgrounds/local/sandbox/start", "start", (PROJECT, "local/sandbox", [])),
                ("POST", "/api/playgrounds/local/sandbox/stop", "stop", (PROJECT, "local/sandbox", False, [])),
                ("POST", "/api/playgrounds/local/sandbox/reseed", "reseed", (PROJECT, "local/sandbox"))):
            with self.subTest(path=path):
                self.assertEqual(self.request(method, path)[0], 200)
                calls[name].assert_called_once_with(*arguments)
        # One route restarts an add-on: its containers'
        self.assertEqual(self.request("POST", "/api/addons/local/whoami/restart")[0], 404)
        self.assertEqual(self.request("POST", "/api/addons/local/whoami/containers/restart")[0], 200)
        calls["unit_action"].assert_called_with(PROJECT, "addon", "local/whoami", "restart")

    def test_the_containers_of_a_playground(self):
        action = self.patch(api.stack, "unit_action", return_value={})
        self.assertEqual(self.request("POST", "/api/playgrounds/welcome/containers/start")[0], 200)
        action.assert_called_once_with(PROJECT, "playground", "welcome", "start")
        # A setup has no container of its own; a verb other than start, stop and restart is no route
        for path in ("/api/setups/observability/containers/start", "/api/addons/kafka/containers/rm"):
            self.assertEqual(self.request("POST", path)[0], 404, path)


class PlatformRoutes(LabUI):
    """The routes of the remote platforms: what they call, and what a failure answers."""

    def setUp(self):
        super().setUp()
        self.patch(api.project, "load", return_value=PROJECT)
        # What the platforms say is read again after each action, for the places: the reads are not made here
        self.invalidate = self.patch(api.places, "invalidate")
        self.patch(api.platforms, "runs_here", return_value=False)

    def test_listed(self):
        listing = self.patch(api.platforms, "listing", return_value={"platforms": [{"id": "cloud"}]})
        status, _, body = self.request("GET", "/api/platforms")
        self.assertEqual((status, json.loads(body)), (200, listing.return_value))

    def test_registered_from_the_fields_of_the_form(self):
        add = self.patch(api.platforms, "add", return_value={"id": "gravitee-cloud"})
        sent = {"name": "Gravitee Cloud", "kind": "cloud", "url": "https://eu.cloud.gravitee.io", "token": "t",
                "account": "4b2f6a1e-8c3d-4e57-9a10-7d5c2b8e3f46"}
        status, _, body = self.request("POST", "/api/platforms", sent)
        self.assertEqual((status, json.loads(body)), (200, add.return_value))
        add.assert_called_once_with(**sent)
        add.reset_mock()
        self.request("POST", "/api/platforms", {"name": "Gravitee Cloud", "url": "u", "token": "t"})
        add.assert_called_once_with(name="Gravitee Cloud", kind=None, url="u", token="t", account=None)   # not sent: None

    def test_checked_and_forgotten_by_id(self):
        check = self.patch(api.platforms, "check", return_value={"state": "ok"})
        remove = self.patch(api.platforms, "remove", return_value={})
        status, _, body = self.request("POST", "/api/platforms/gravitee-cloud/check")
        self.assertEqual((status, json.loads(body)), (200, check.return_value))
        check.assert_called_once_with("gravitee-cloud", PROJECT)
        self.assertEqual(self.request("POST", "/api/platforms/gravitee-cloud/delete")[0], 200)
        remove.assert_called_once_with("gravitee-cloud", PROJECT)

    def test_every_action_on_a_platform_reads_it_again_for_the_places(self):
        """Add, check, replace the Account Token, set and delete a Cloud Token, create and delete a gateway, forget the
        platform: the places the page asks for next are those of now. A check that refuses changes nothing."""
        record = {"id": "gravitee-cloud"}
        for function, answer in (("add", record), ("check", {"state": "ok"}), ("replace_account_token", record),
                                 ("set_cloud_token", {}), ("delete_cloud_token", {}),
                                 ("create_gateway", {"gateway": {}, "tokenSaved": True}),
                                 ("delete_gateway", {}), ("remove", {})):
            self.patch(api.platforms, function, return_value=answer)
        for path, sent in (("/api/platforms", {"name": "x"}), ("/api/platforms/gravitee-cloud/check", {}),
                           ("/api/platforms/gravitee-cloud/token", {"token": "t"}),
                           ("/api/platforms/gravitee-cloud/environments/env-1/token", {"token": "t"}),
                           ("/api/platforms/gravitee-cloud/environments/env-1/token/delete", {}),
                           ("/api/platforms/gravitee-cloud/gateways", {"environmentId": "e", "name": "lab"}),
                           ("/api/platforms/gravitee-cloud/gateways/gw-1/delete", {}),
                           ("/api/platforms/gravitee-cloud/delete", {})):
            self.invalidate.reset_mock()
            with self.subTest(path=path):
                self.assertEqual(self.request("POST", path, sent)[0], 200)
                self.invalidate.assert_called_once_with("gravitee-cloud")
        self.invalidate.reset_mock()
        self.patch(api.platforms, "check", side_effect=paths.NotFound("No platform nope"))
        self.assertEqual(self.request("POST", "/api/platforms/nope/check")[0], 404)
        self.invalidate.assert_not_called()

    def test_the_tokens_of_a_platform_are_replaced_and_forgotten_by_id(self):
        replace = self.patch(api.platforms, "replace_account_token", return_value={"id": "gravitee-cloud"})
        keep = self.patch(api.platforms, "set_cloud_token", return_value={})
        forget = self.patch(api.platforms, "delete_cloud_token", return_value={})
        status, _, body = self.request("POST", "/api/platforms/gravitee-cloud/token", {"token": "new"})
        self.assertEqual((status, json.loads(body)), (200, {"id": "gravitee-cloud"}))
        replace.assert_called_once_with("gravitee-cloud", "new")
        status, _, body = self.request("POST", "/api/platforms/gravitee-cloud/environments/fake-env-dev/token",
                                       {"token": "cloud-token"})
        self.assertEqual((status, json.loads(body)), (200, {}))
        keep.assert_called_once_with("gravitee-cloud", "fake-env-dev", "cloud-token")
        self.request("POST", "/api/platforms/gravitee-cloud/environments/fake-env-dev/token/delete")
        forget.assert_called_once_with("gravitee-cloud", "fake-env-dev")
        self.request("POST", "/api/platforms/gravitee-cloud/token")   # a form without its field: None, and a refusal
        replace.assert_called_with("gravitee-cloud", None)
        # What a platform refuses is the page's problem line
        keep.side_effect = cloud.CloudError("This Cloud Token is for Test, not Development", "invalid")
        status, _, body = self.request("POST", "/api/platforms/gravitee-cloud/environments/fake-env-dev/token", {})
        self.assertEqual((status, json.loads(body)), (400, {"error": "This Cloud Token is for Test, not Development"}))
        self.assertEqual(self.invalidate.call_count, 4, "a refusal read nothing again")

    def test_an_environment_or_a_gateway_is_one_segment_of_what_a_platform_accepts(self):
        for path in ("a/b", "..", ".", ".hidden", "a.b", "a_b", "-a", "x" * 65):
            for route in (f"/api/platforms/p/environments/{path}/token", f"/api/platforms/p/environments/{path}/token/delete",
                          f"/api/platforms/p/gateways/{path}/delete", f"/api/platforms/p/gateways/{path}/start"):
                with self.subTest(route=route):
                    self.assertEqual(self.request("POST", route)[0], 404)
            self.assertEqual(self.request("GET", f"/api/platforms/p/gateways/{path}/plan")[0], 404, path)

    def test_gateways_are_created_and_deleted_in_a_platform(self):
        create = self.patch(api.platforms, "create_gateway", return_value={"gateway": {}, "tokenSaved": True})
        delete = self.patch(api.platforms, "delete_gateway", return_value={})
        sent = {"environmentId": "env", "name": "lab"}
        status, _, body = self.request("POST", "/api/platforms/gravitee-cloud/gateways", sent)
        self.assertEqual((status, json.loads(body)), (200, create.return_value))
        create.assert_called_once_with(PROJECT, "gravitee-cloud", "env", "lab")
        self.assertEqual(self.request("POST", "/api/platforms/gravitee-cloud/gateways/9f1c-2-xy/delete")[0], 200)
        delete.assert_called_once_with(PROJECT, "gravitee-cloud", "9f1c-2-xy")
        # An id is one segment of what a platform accepts: letters, digits and dashes
        for path in ("a/b", "..", ".", ".hidden", "9f1c-2.x_y"):
            status, _, _ = self.request("POST", f"/api/platforms/gravitee-cloud/gateways/{path}/delete")
            self.assertEqual(status, 404, path)

    def test_what_a_platform_refuses_is_a_bad_request_in_its_words(self):
        for function, path, sent in [("add", "/api/platforms", {"name": "x"}),
                                     ("create_gateway", "/api/platforms/p/gateways", {}),
                                     ("delete_gateway", "/api/platforms/p/gateways/g/delete", {})]:
            refusal = cloud.CloudError("Gravitee Cloud refuses this token", "invalid")
            self.patch(api.platforms, function, side_effect=refusal)
            with self.subTest(path=path):
                status, _, body = self.request("POST", path, sent)
                self.assertEqual((status, json.loads(body)), (400, {"error": "Gravitee Cloud refuses this token"}))

    def test_a_platform_that_is_not_known(self):
        for function, path in [("check", "/api/platforms/nope/check"), ("remove", "/api/platforms/nope/delete")]:
            self.patch(api.platforms, function, side_effect=paths.NotFound("No platform nope"))
            status, _, body = self.request("POST", path)
            self.assertEqual((status, json.loads(body)), (404, {"error": "No platform nope"}))

    def test_nothing_is_asked_of_a_platform_but_by_the_page(self):
        check = self.patch(api.platforms, "check")
        add = self.patch(api.platforms, "add")
        self.assertEqual(self.request("POST", "/api/platforms/p/check", headers={"X-Gravitee-Lab-UI": None})[0], 403)
        self.assertEqual(self.request("POST", "/api/platforms", {"token": "t"},
                                      headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.request("GET", "/api/platforms/p/check")[0], 404)   # a read is no way to use the token
        check.assert_not_called()
        add.assert_not_called()


class PlatformFlow(LabUI):
    """A platform registered, read, given a gateway and forgotten through the routes, against the fake Cloud and the
    files of a project folder: no token is in any answer."""

    def setUp(self):
        super().setUp()
        self.folder(paths, envfiles, files, files={".env": "", "addons/hybrid/compose.yml": ""})
        setting = {"key": "HYBRID_CLOUD_TOKEN", "default": None, "required": True, "secret": True, "comment": ""}
        gateway = {"platform": "cloud", "service": "apim-hybrid-gateway", "token": "HYBRID_CLOUD_TOKEN",
                   "domains": ["localhost:8090", "apim-hybrid-gateway:8082"]}
        self.patch(api.project, "load", return_value={"addons": {"hybrid": {
            "id": "hybrid", "name": "Hybrid gateway", "env_file": "addons/hybrid/.env", "settings": [setting],
            "gateway": gateway, "services": []}}, "playgrounds": {}, "groups": [], "services": {}})
        # Docker is not asked about a gateway that is neither on nor running
        self.patch(api.compose, "ps", return_value={})
        self.patch(api.layers, "current", return_value={"playground": None, "explicit": [], "effective": [],
                                                        "problems": [], "locked": None})
        self.patch(places, "_cache", {})
        self.patch(places, "_reading", {})
        self.fake = fake_cloud.serve(connect_after=0)
        serving(self.fake)
        self.addCleanup(self.fake.server_close)
        self.addCleanup(self.fake.shutdown)
        self.answers = []

    def ask(self, method, path, body=None, expect=200):
        status, _, text = self.request(method, path, body)
        self.assertEqual(status, expect, path)
        self.answers.append(text.decode())
        return json.loads(text)

    def test_the_whole_flow(self):
        sent = {"name": "Gravitee Cloud", "kind": "cloud", "url": f"http://127.0.0.1:{self.fake.server_port}",
                "account": fake_cloud.ACCOUNT["id"]}
        words = {"error": "The token or the Account ID is not right"}
        self.assertEqual(self.ask("POST", "/api/platforms", {**sent, "token": "not-the-token"}, 400), words)
        self.assertEqual(self.ask("POST", "/api/platforms", {**sent, "token": fake_cloud.TOKEN, "account": "my-account"},
                                  400), words)   # the hrid is no Account ID
        self.assertEqual(self.ask("POST", "/api/platforms", {**sent, "token": fake_cloud.TOKEN, "account": ""}, 400),
                         {"error": "Enter the Account ID"})
        self.assertEqual(self.ask("GET", "/api/platforms"), {"platforms": []})
        record = self.ask("POST", "/api/platforms", {**sent, "token": fake_cloud.TOKEN})
        self.assertEqual((record["id"], record["account"], record["tokenSet"]),
                         ("gravitee-cloud", {"id": fake_cloud.ACCOUNT["id"], "hrid": "my-account",
                                             "name": "My Account"}, True))
        self.assertEqual(self.ask("GET", "/api/platforms"), {"platforms": [record]})
        found = self.ask("POST", "/api/platforms/gravitee-cloud/check", {})
        self.assertEqual((found["state"], [e["name"] for e in found["environments"]], found["gateways"]),
                         ("ok", ["Development", "Test", "Production"], []))
        made = self.ask("POST", "/api/platforms/gravitee-cloud/gateways",
                        {"environmentId": "fake-env-dev", "name": "lab"})
        self.assertEqual((made["tokenSaved"], made["gateway"]["domains"]),
                         (True, ["localhost:8090", "apim-hybrid-gateway:8082"]))
        (issued,) = self.fake.state.tokens.values()
        self.assertEqual(envfiles.read("addons/hybrid/.env"), {"HYBRID_CLOUD_TOKEN": issued})
        found = self.ask("POST", "/api/platforms/gravitee-cloud/check", {})
        self.assertEqual([(g["name"], g["connection"]) for g in found["gateways"]], [("lab", "CONNECTED")])
        self.ask("POST", f"/api/platforms/gravitee-cloud/gateways/{made['gateway']['id']}/delete", {})
        self.assertEqual(self.ask("POST", "/api/platforms/gravitee-cloud/check", {})["gateways"], [])
        self.ask("POST", "/api/platforms/gravitee-cloud/delete", {})
        self.ask("POST", "/api/platforms/gravitee-cloud/check", {}, 404)
        self.assertEqual(self.ask("GET", "/api/platforms"), {"platforms": []})
        self.assertEqual(envfiles.read(".env"), {})
        self.assertFalse(any(token in "".join(self.answers) for token in (fake_cloud.TOKEN, issued, "not-the-token")),
                         "a token is in an answer")
        # Only the paths of the account were called: the list of accounts and /me are for a user
        self.assertEqual({path for _, path in self.fake.state.asked
                          if not path.startswith(f"/accounts/{fake_cloud.ACCOUNT['id']}")},
                         {"/accounts/my-account"}, "besides the hrid that was refused, only the account's own paths")


class PlatformPlaces(PlatformFlow):
    """The places, through the routes, against the fake Cloud: what an action on a platform changes in them is seen by the
    next call, with no wait for the minute that a read is kept."""

    URL = "http://127.0.0.1:{}"
    DEV, TEST = "fake-env-dev", "fake-env-test"

    def register(self, name="My Account"):
        sent = {"name": name, "kind": "cloud", "url": self.URL.format(self.fake.server_port),
                "account": fake_cloud.ACCOUNT["id"], "token": fake_cloud.TOKEN}
        return self.ask("POST", "/api/platforms", sent)

    def place(self, environment=None):
        found = {found["id"]: found for found in self.ask("GET", "/api/places")}
        return found[f"my-account/{environment or self.DEV}"]

    def test_nothing_registered_has_no_place_and_asks_nothing(self):
        self.assertEqual(self.ask("GET", "/api/places"), [])
        self.assertEqual(self.fake.state.asked, [])

    def test_a_platform_is_read_when_it_is_registered_and_its_places_are_named_by_environment_and_platform(self):
        self.register()
        found = self.ask("GET", "/api/places")
        self.assertEqual([(place["id"], place["name"], place["state"], place["missing"]) for place in found], [
            ("my-account/fake-env-dev", "Development (My Account)", "missing", "No hybrid gateway"),
            ("my-account/fake-env-test", "Test (My Account)", "missing", "No hybrid gateway"),
            ("my-account/fake-env-prod", "Production (My Account)", "missing", "No hybrid gateway")])
        self.assertFalse(any(" · " in place["name"] for place in found), "a middle dot separates a place")

    def test_what_is_done_to_a_platform_is_seen_by_the_places_at_once(self):
        self.register()
        self.ask("POST", "/api/platforms/my-account/gateways", {"environmentId": self.DEV, "name": "lab"})
        self.assertEqual((self.place()["missing"], self.place()["gateway"]["here"]), ("No Cloud Token", True))
        self.ask("POST", f"/api/platforms/my-account/environments/{self.DEV}/token",
                 {"token": fake_cloud.cloud_token(self.DEV)})
        self.assertEqual((self.place()["state"], self.place()["token"]["state"]), ("ready", "valid"))
        # Another environment's token is refused in the words of the form, and changes nothing
        wrong = self.ask("POST", f"/api/platforms/my-account/environments/{self.TEST}/token",
                         {"token": fake_cloud.cloud_token(self.DEV)}, 400)
        self.assertEqual(wrong, {"error": "This Cloud Token is for Development, not Test"})
        self.assertEqual(self.place(self.TEST)["token"], {"state": "unset"})
        self.ask("POST", f"/api/platforms/my-account/environments/{self.DEV}/token/delete", {})
        self.assertEqual(self.place()["missing"], "No Cloud Token")
        # The Account Token replaced: the record and the places stay
        self.assertEqual(self.ask("POST", "/api/platforms/my-account/token", {"token": "not-the-token"}, 400),
                         {"error": "The token or the Account ID is not right"})
        replaced = self.ask("POST", "/api/platforms/my-account/token", {"token": fake_cloud.TOKEN})
        self.assertEqual((replaced["id"], replaced["tokenSet"]), ("my-account", True))
        self.assertEqual(self.place()["missing"], "No Cloud Token")

    def test_a_check_reads_the_places_again_too(self):
        self.register()
        self.fake.state.asked.clear()
        self.ask("POST", "/api/platforms/my-account/check", {})
        # The check and the places each asked the platform for its environments and gateways: the cache is fresh
        asked = len(self.fake.state.asked)
        self.assertGreaterEqual(asked, 4)
        self.ask("GET", "/api/places")
        self.assertEqual(len(self.fake.state.asked), asked, "the places were served from what was read")

    def test_the_state_of_the_lab_never_asks_a_platform(self):
        """It is read from this machine's files: a platform is not asked for it, not even in the background."""
        self.register()
        places.invalidate("my-account")
        self.patch(places, "TTL", 0)   # whatever it holds is old: a call that asks would read it again
        asked = len(self.fake.state.asked)
        records = platforms.read()
        held = {"welcome": places.deployed("welcome", lambda pid, place: [])}
        self.assertEqual((held, [record["id"] for record in records]), ({"welcome": []}, ["my-account"]))
        self.assertEqual((len(self.fake.state.asked), places._reading), (asked, {}))


class PlaceRoutes(LabUI):
    """A playground's routes take the place to deploy to; This machine is the call they always made."""

    def setUp(self):
        super().setUp()
        self.patch(api.project, "load", return_value=PROJECT)
        # (as what they replace is called: a call that does not fit it is a failure here too)
        self.calls = {name: self.patch(api.playgrounds, name, autospec=True, return_value={}) for name in
                      ("check", "start", "reseed", "stop", "delete_place")}
        self.delete = self.patch(api.personal, "delete", return_value={})

    PLACE = "my-account/fake-env-dev"

    def test_nothing_to_say_of_a_place_is_this_machine_and_its_calls_are_as_they_always_were(self):
        for method, path, name, arguments in (
                ("GET", "/api/playgrounds/welcome/check", "check", (PROJECT, "welcome")),
                ("POST", "/api/playgrounds/welcome/start", "start", (PROJECT, "welcome", [])),
                ("POST", "/api/playgrounds/welcome/reseed", "reseed", (PROJECT, "welcome")),
                ("POST", "/api/playgrounds/welcome/stop", "stop", (PROJECT, "welcome", False, []))):
            for sent in ({}, {"place": None}, {"place": "machine"}):
                with self.subTest(path=path, sent=sent):
                    self.calls[name].reset_mock()
                    self.assertEqual(self.request(method, path, sent)[0], 200)
                    self.calls[name].assert_called_once_with(*arguments)
        self.request("GET", "/api/playgrounds/welcome/check?place=machine")
        self.calls["check"].assert_called_with(PROJECT, "welcome")

    def test_a_start_on_this_machine_can_go_to_the_versions_it_was_tested_with_first(self):
        saved = self.patch(api.images, "save")
        self.patch(api.layers, "lookup", return_value={"id": "welcome"})
        self.patch(api.tested, "untested", return_value={"images": [], "to": {"APIM_VERSION": "4.13.0-alpha.3"}})
        self.request("POST", "/api/playgrounds/welcome/start", {"accept": [], "tested": True})
        saved.assert_called_once_with(PROJECT, {"APIM_VERSION": "4.13.0-alpha.3"})
        self.calls["start"].assert_called_once_with(PROJECT, "welcome", [])
        # Without it, or on a place, the saved versions are left as they are
        saved.reset_mock()
        self.request("POST", "/api/playgrounds/welcome/start", {"accept": []})
        self.request("POST", "/api/playgrounds/welcome/start", {"place": self.PLACE, "tested": True})
        saved.assert_not_called()

    def test_a_place_goes_to_what_deploys_there_with_what_its_dialog_showed(self):
        self.request("GET", f"/api/playgrounds/welcome/check?place={self.PLACE}")
        self.calls["check"].assert_called_once_with(PROJECT, "welcome", place=self.PLACE)
        self.request("POST", "/api/playgrounds/welcome/start", {"place": self.PLACE, "accept": ["addon:hybrid"]})
        self.calls["start"].assert_called_once_with(PROJECT, "welcome", ["addon:hybrid"], place=self.PLACE)
        # Re-seed and Deploy are one route: the place and its dialog, which Deploy may have started the gateway by
        self.request("POST", "/api/playgrounds/welcome/reseed", {"place": self.PLACE, "accept": ["playground:x"]})
        self.calls["reseed"].assert_called_once_with(PROJECT, "welcome", place=self.PLACE, accept=["playground:x"])
        self.calls["reseed"].reset_mock()
        self.request("POST", "/api/playgrounds/welcome/reseed", {"place": self.PLACE})
        self.calls["reseed"].assert_called_once_with(PROJECT, "welcome", place=self.PLACE, accept=[])
        self.request("POST", "/api/playgrounds/welcome/stop", {"place": self.PLACE, "remove": True})
        self.calls["stop"].assert_called_once_with(PROJECT, "welcome", True, [], place=self.PLACE)

    def test_a_place_that_is_no_text_is_refused_and_never_taken_for_this_machine(self):
        for sent in ("", 0, False, [], ["machine"], {"id": "machine"}, 12):
            for path in ("start", "reseed", "stop", "delete"):
                with self.subTest(sent=sent, path=path):
                    status, _, body = self.request("POST", f"/api/playgrounds/welcome/{path}", {"place": sent})
                    self.assertEqual((status, json.loads(body)["error"]), (400, json.loads(body)["error"]))
        for name in ("start", "reseed", "stop", "delete_place"):
            self.calls[name].assert_not_called()
        self.delete.assert_not_called()

    def test_a_place_that_is_not_there_is_a_not_found_in_the_words_of_what_deploys_there(self):
        self.calls["start"].side_effect = paths.NotFound("No place my-account/nope")
        status, _, body = self.request("POST", "/api/playgrounds/welcome/start", {"place": "my-account/nope"})
        self.assertEqual((status, json.loads(body)), (404, {"error": "No place my-account/nope"}))
        self.calls["check"].side_effect = ValueError("Create a hybrid gateway for Development (My Account)")
        status, _, body = self.request("GET", f"/api/playgrounds/welcome/check?place={self.PLACE}")
        self.assertEqual((status, json.loads(body)["error"]), (400, "Create a hybrid gateway for Development (My Account)"))

    def test_the_delete_of_a_place_comes_before_the_delete_of_a_personal_playground(self):
        # The same route: with a place it deletes what the seed created there, without it the playground's folder
        self.assertEqual(self.request("POST", "/api/playgrounds/local/sandbox/delete", {"place": self.PLACE})[0], 200)
        self.calls["delete_place"].assert_called_once_with(PROJECT, "local/sandbox", self.PLACE)
        self.delete.assert_not_called()
        self.assertEqual(self.request("POST", "/api/playgrounds/local/sandbox/delete", {})[0], 200)
        self.delete.assert_called_once_with(PROJECT, "playground", "local/sandbox")
        self.calls["delete_place"].assert_called_once()
        # An add-on and a setup have no place
        self.request("POST", "/api/addons/local/whoami/delete", {"place": self.PLACE})
        self.delete.assert_called_with(PROJECT, "addon", "local/whoami")

    def test_the_delete_of_this_machine_is_never_a_delete_of_the_folder(self):
        # Asking for it by name, or with no value, is a refusal: the folder of a playground is not what was meant
        for sent in ("machine", None, "", 3):
            with self.subTest(sent=sent):
                status, _, body = self.request("POST", "/api/playgrounds/local/sandbox/delete", {"place": sent})
                self.assertEqual(status, 400)
                self.assertIn("place", json.loads(body)["error"].lower())
        self.delete.assert_not_called()
        self.calls["delete_place"].assert_not_called()

    def test_what_a_place_refuses_to_delete_is_the_answer(self):
        self.calls["delete_place"].side_effect = RuntimeError("Cloud Token for Development (My Account) has expired")
        status, _, body = self.request("POST", "/api/playgrounds/welcome/delete", {"place": self.PLACE})
        self.assertEqual((status, json.loads(body)), (409, {"error": "Cloud Token for Development (My Account) has expired"}))

    def test_the_places_are_what_the_platforms_last_said(self):
        found = self.patch(api.places, "places", return_value=[{"id": "my-account/fake-env-dev"}])
        status, _, body = self.request("GET", "/api/places")
        self.assertEqual((status, json.loads(body)), (200, [{"id": "my-account/fake-env-dev"}]))
        found.assert_called_once_with(PROJECT)
        # A read: the page's own, like the state
        self.assertEqual(self.request("GET", "/api/places", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)


class GatewayCase(LabUI):
    """The small project of test_layers with the Hybrid gateway as Cloud's, what runs and what is on as a test sets them,
    and what the routes of a gateway call replaced."""

    def setUp(self):
        super().setUp()
        self.project = test_layers.lab()
        test_layers.cloud(self.project)
        self.state = test_layers.on(self.project)
        self.running = test_layers.up(project=self.project)
        self.where = {"id": "my-account/fake-env-dev", "name": "Development (My Account)", "gateway": "hybrid",
                      "here": True, "serving": None}
        self.patch(api.project, "load", return_value=self.project)
        self.patch(api.project, "saved", return_value={"hashes": {}, "images": {}})
        self.patch(api.compose, "ps", return_value={})
        self.patch(api.layout, "waiting", return_value=False)   # whatever tree this runs in: no old layout is being moved
        self.patch(api.layers, "current", side_effect=lambda project: self.state)
        self.patch(api.model, "up", side_effect=lambda project, containers: self.running)
        self.patch(api.model, "behind", return_value=set())
        self.place = self.patch(api.places, "gateway", side_effect=lambda *asked: self.where)
        self.invalidate = self.patch(api.places, "invalidate")
        self.swap = self.patch(api.platforms, "swap_gateway", return_value={})
        self.write = self.patch(api.layers, "write")
        self.binds = self.patch(api.layers, "make_binds", return_value=[])
        self.jobs = self.patch(api, "JOBS")
        self.jobs.start.return_value = Job("Start Hybrid gateway", [])

    GATEWAY = "/api/platforms/my-account/gateways/gw-1"

    def elsewhere(self):
        """The gateway runs, for another place, and so do the apps: the one it serves is Test."""
        self.state = test_layers.on(self.project, ["hybrid"], "hotels")
        self.running = test_layers.up("hybrid", playgrounds=["hotels"], project=self.project)
        self.where = {**self.where, "here": False, "serving": {"id": "client-lab/test", "name": "Test (Client lab)"}}


class GatewayRoutes(GatewayCase):
    """The start of a gateway of a platform: what it says first, and the job, whose key is the add-on's."""

    # --- What it says first -----------------------------------------------------------------------------------------

    def test_a_gateway_that_only_starts_says_nothing_first(self):
        status, _, body = self.request("GET", f"{self.GATEWAY}/plan")
        self.assertEqual((status, json.loads(body)), (200, None))   # null: the page starts it
        self.place.assert_called_once_with(self.project, "my-account", "gw-1")

    def test_a_gateway_that_serves_another_place_says_what_changes(self):
        self.elsewhere()
        status, _, body = self.request("GET", f"{self.GATEWAY}/plan")
        plan = layers.plan(self.project, self.state, self.running, "addon", "hybrid", "start", place=self.where)
        self.assertEqual((status, json.loads(body)), (200, layers.dialog(plan)))
        found = json.loads(body)
        self.assertEqual([row["name"] for row in found["restarts"]], ["Hybrid gateway"])
        self.assertEqual(([row["name"] for row in found["serves"]], [row["name"] for row in found["stops_serving"]]),
                         (["Development (My Account)"], ["Test (Client lab)"]))
        self.assertEqual(found["deploys_to"], [], "a gateway is not a playground: it deploys nowhere")

    def test_a_gateway_that_is_served_already_has_nothing_to_say(self):
        self.state = test_layers.on(self.project, ["hybrid"])
        self.running = test_layers.up("hybrid", project=self.project)
        self.assertEqual(json.loads(self.request("GET", f"{self.GATEWAY}/plan")[2]), None)

    def test_what_a_platform_says_of_a_gateway_that_cannot_start_is_the_answer(self):
        for error, status in ((paths.NotFound("No gateway gw-1 in My Account"), 404),
                              (ValueError("Its token is not kept here"), 400),
                              (ValueError("Gravitee Cloud refuses this token"), 400)):
            self.place.side_effect = error
            with self.subTest(error=str(error)):
                code, _, body = self.request("GET", f"{self.GATEWAY}/plan")
                self.assertEqual((code, json.loads(body)), (status, {"error": str(error)}))
                self.assertEqual(self.request("POST", f"{self.GATEWAY}/start", {})[0], status)
        self.jobs.start.assert_not_called()

    # --- The job ---------------------------------------------------------------------------------------------------

    def run_steps(self):
        """The job it started, its title, key and verb, and its steps run on a job that keeps its lines."""
        title, steps, more = self.started(self.jobs)
        job = Job(title, [])
        for step in steps:
            if callable(step):
                step(job)
        return title, steps, more, job

    def test_the_gateway_that_runs_here_starts_as_the_add_on_does_with_nothing_swapped(self):
        self.assertEqual(self.request("POST", f"{self.GATEWAY}/start", {})[0], 200)
        title, steps, more, job = self.run_steps()
        plan = layers.plan(self.project, self.state, self.running, "addon", "hybrid", "start", place=self.where)
        self.assertEqual((title, more["key"], more["verb"]), ("Start Hybrid gateway", "addon:hybrid", "start"))
        self.assertEqual(more["changes"], layers.changes(self.project, plan))
        self.assertEqual(more["waits"], layers.waits(self.project, plan))
        self.assertEqual(steps[1:-1], layers.commands(plan), "the commands of the plan, by name, without the core")
        self.write.assert_called_once_with(None, ["hybrid"], blocked={})   # the gateway is turned on, on the on file
        self.binds.assert_called_once_with(self.project, self.binds.call_args.args[1])
        self.assertEqual(job.lines, ["Starts: Hybrid gateway"])
        self.swap.assert_not_called()
        self.assertEqual(self.invalidate.call_args_list, [mock.call("my-account")], "its platform is read again at the end")

    def test_the_on_file_is_written_through_the_gate_of_a_unit_that_is_blocked_and_the_folders_of_its_binds_are_made_after(self):
        # Every other writer of the on file passes the map of the units that must not start and makes the folders a bind
        # names: the start of a gateway is one of them, its add-on may come from a catalog
        self.project["addons"]["kafka"]["blocked"] = {"reasons": ["Publishes a port to the network"]}
        self.project["addons"]["kafka"]["file"] = "addons/kafka/compose.yml"
        calls = mock.Mock()
        calls.attach_mock(self.write, "write")
        calls.attach_mock(self.binds, "binds")
        self.assertEqual(self.request("POST", f"{self.GATEWAY}/start", {})[0], 200)
        self.run_steps()
        plan = layers.plan(self.project, self.state, self.running, "addon", "hybrid", "start", place=self.where)
        self.assertEqual(calls.mock_calls, [
            mock.call.write(None, ["hybrid"], blocked={"addons/kafka/compose.yml": "Publishes a port to the network"}),
            mock.call.binds(self.project, plan)], "the file first, then the folders, as a start writes them")

    def test_a_gateway_whose_add_on_a_catalog_blocks_does_not_start_and_changes_nothing(self):
        self.project["addons"]["hybrid"]["blocked"] = {"reasons": ["Mounts the Docker socket"]}
        status, _, body = self.request("POST", f"{self.GATEWAY}/start", {})
        self.assertEqual((status, json.loads(body)), (409, {"error": "Mounts the Docker socket"}))
        self.jobs.start.assert_not_called()
        for step in (self.write, self.binds, self.swap):
            step.assert_not_called()

    def test_a_gateway_that_does_not_run_here_gets_its_token_before_the_add_on_is_made(self):
        self.elsewhere()
        calls = mock.Mock()
        calls.attach_mock(self.swap, "swap")
        calls.attach_mock(self.write, "write")
        accepted = [layers.key("addon", "hybrid")]
        self.assertEqual(self.request("POST", f"{self.GATEWAY}/start", {"accept": accepted})[0], 200)
        title, steps, more, job = self.run_steps()
        self.assertEqual(more["key"], "addon:hybrid")
        self.swap.assert_called_once_with(self.project, "my-account", "gw-1")
        self.assertEqual([call[0] for call in calls.mock_calls], ["write", "swap"], "the token, then the commands")
        self.assertEqual(job.lines, ["Restarts: Hybrid gateway"])
        self.assertTrue(callable(steps[0]) and all(isinstance(step, list) for step in steps[1:-1]))

    def test_a_token_that_no_gateway_matches_is_replaced_whatever_the_add_on_does(self):
        # The add-on holds a token that no platform lists any more (here is false), and it does not run: it starts, with
        # the token of this gateway, which is written first
        self.where = {**self.where, "here": False, "serving": None}
        self.assertEqual(self.request("POST", f"{self.GATEWAY}/start", {})[0], 200)
        self.run_steps()
        self.swap.assert_called_once_with(self.project, "my-account", "gw-1")

    def test_a_start_that_cannot_run_as_the_page_showed_it_is_refused(self):
        self.state = {**self.state, "locked": layers.LOCKED}
        status, _, body = self.request("POST", f"{self.GATEWAY}/start", {})
        self.assertEqual((status, json.loads(body)), (409, {"error": layers.LOCKED}))
        self.jobs.start.assert_not_called()
        self.swap.assert_not_called()

    def test_a_job_that_runs_keeps_the_gateway_from_starting_and_changes_nothing(self):
        self.jobs.start.side_effect = RuntimeError("'Start Kafka' is still running")
        status, _, body = self.request("POST", f"{self.GATEWAY}/start", {})
        self.assertEqual((status, json.loads(body)), (409, {"error": "'Start Kafka' is still running"}))
        self.swap.assert_not_called()   # the token is the job's to write: nothing was written
        self.write.assert_not_called()

    def test_only_the_page_starts_a_gateway(self):
        self.assertEqual(self.request("POST", f"{self.GATEWAY}/start", {}, headers={"X-Gravitee-Lab-UI": None})[0], 403)
        self.jobs.start.assert_not_called()


class GatewayDeletes(GatewayCase):
    """A gateway or a platform forgotten while its gateway runs here: the add-on is turned off by a job, asked for before
    anything changes, and the platform acts first."""

    def setUp(self):
        super().setUp()
        self.record = {"id": "my-account", "kind": "cloud"}
        self.patch(api.platforms, "find", return_value=self.record)
        self.here = self.patch(api.platforms, "runs_here", return_value=True)
        self.calls = mock.Mock()
        self.delete = self.patch(api.platforms, "delete_gateway", return_value={})
        self.remove = self.patch(api.platforms, "remove", return_value={})
        self.apply = self.patch(api.layers, "apply", return_value=Job("Stop Hybrid gateway", []))
        self.calls.attach_mock(self.delete, "delete")
        self.calls.attach_mock(self.remove, "remove")
        self.calls.attach_mock(self.apply, "apply")

    def on(self, runs=True, brought=False):
        """The add-on is on and runs, or it is on only (a stop that failed), or it runs without being on."""
        self.state = test_layers.on(self.project, ["hybrid"] if not brought else [], "welcome" if brought else None)
        self.running = test_layers.up("hybrid", project=self.project) if runs else test_layers.up(project=self.project)

    def test_the_gateway_that_ran_here_is_deleted_and_its_add_on_turned_off_by_a_job(self):
        self.on()
        status, _, body = self.request("POST", f"{self.GATEWAY}/delete", {})
        self.assertEqual((status, json.loads(body)["title"]), (200, "Stop Hybrid gateway"))
        self.here.assert_called_once_with(self.project, "my-account", "gw-1")
        self.delete.assert_called_once_with(self.project, "my-account", "gw-1")
        plan = self.apply.call_args.args[1]
        self.assertEqual((plan["do"], plan["kind"], plan["id"], self.apply.call_args.args[2]),
                         ("stop", "addon", "hybrid", "Stop Hybrid gateway"))
        self.assertEqual([call[0] for call in self.calls.mock_calls], ["delete", "apply"], "the platform, then the job")
        self.invalidate.assert_called_once_with("my-account")

    def test_an_add_on_that_is_on_and_does_not_run_is_turned_off_too(self):
        self.state = test_layers.on(self.project, ["hybrid"])
        self.running = test_layers.up(project=self.project)
        self.request("POST", f"{self.GATEWAY}/delete", {})
        self.assertEqual(self.apply.call_args.args[2], "Turn off Hybrid gateway")

    def test_an_add_on_that_is_off_and_does_not_run_has_nothing_to_turn_off(self):
        self.state, self.running = test_layers.on(self.project), test_layers.up(project=self.project)
        status, _, body = self.request("POST", f"{self.GATEWAY}/delete", {})
        self.assertEqual((status, json.loads(body)), (200, {}))
        self.delete.assert_called_once()
        self.apply.assert_not_called()

    def test_a_gateway_that_did_not_run_here_leaves_the_add_on_and_docker_alone(self):
        self.here.return_value = False
        ps = self.patch(api.compose, "ps", return_value={})
        self.on()
        self.assertEqual(json.loads(self.request("POST", f"{self.GATEWAY}/delete", {})[2]), {})
        self.delete.assert_called_once()
        self.apply.assert_not_called()
        ps.assert_not_called()

    def test_a_turn_off_that_would_stop_more_is_refused_before_anything_changes(self):
        # A playground brings it: turning it off turns the playground off too, which nobody agreed to
        self.project["playgrounds"]["welcome"]["addons"].append("hybrid")
        self.on(brought=True)
        status, _, body = self.request("POST", f"{self.GATEWAY}/delete", {})
        self.assertEqual(status, 409)
        self.assertRegex(json.loads(body)["error"], r"^Would also turn off Welcome")
        self.delete.assert_not_called()
        self.apply.assert_not_called()
        self.invalidate.assert_not_called()

    def test_a_job_that_runs_is_waited_for_before_anything_changes(self):
        self.on()
        self.jobs.current = types.SimpleNamespace(title="Start Kafka", done=False)
        status, _, body = self.request("POST", f"{self.GATEWAY}/delete", {})
        self.assertEqual((status, json.loads(body)), (409, {"error": "'Start Kafka' is still running"}))
        self.delete.assert_not_called()

    def test_an_on_file_with_the_users_own_changes_keeps_the_gateway_from_being_deleted(self):
        self.on()
        self.state = {**self.state, "locked": layers.LOCKED}
        self.assertEqual(self.request("POST", f"{self.GATEWAY}/delete", {})[0], 409)
        self.delete.assert_not_called()

    def test_what_the_platform_refuses_leaves_the_add_on_as_it_is(self):
        self.on()
        self.delete.side_effect = cloud.CloudError("Gravitee Cloud refuses this token", "invalid")
        status, _, body = self.request("POST", f"{self.GATEWAY}/delete", {})
        self.assertEqual((status, json.loads(body)), (400, {"error": "Gravitee Cloud refuses this token"}))
        self.apply.assert_not_called()
        self.invalidate.assert_not_called()

    def test_a_platform_is_forgotten_with_its_gateway_that_ran_here_turned_off(self):
        self.on()
        status, _, body = self.request("POST", "/api/platforms/my-account/delete", {})
        self.assertEqual((status, json.loads(body)["title"]), (200, "Stop Hybrid gateway"))
        self.here.assert_called_once_with(self.project, "my-account", None)
        self.remove.assert_called_once_with("my-account", self.project)
        self.assertEqual([call[0] for call in self.calls.mock_calls], ["remove", "apply"])

    def test_a_platform_whose_gateway_did_not_run_here_is_just_forgotten(self):
        self.here.return_value = False
        self.on()
        self.assertEqual(json.loads(self.request("POST", "/api/platforms/my-account/delete", {})[2]), {})
        self.apply.assert_not_called()

    def test_what_could_not_be_deleted_of_a_forgotten_platform_is_said_after_its_add_on_is_off(self):
        self.on()
        self.remove.side_effect = RuntimeError("The platform was forgotten, but what the playgrounds recorded of it "
                                               "could not be deleted, delete these yourself: playgrounds/x")
        status, _, body = self.request("POST", "/api/platforms/my-account/delete", {})
        self.assertEqual(status, 409)
        self.assertIn("delete these yourself", json.loads(body)["error"])
        self.apply.assert_called_once()
        self.invalidate.assert_called_once_with("my-account")

    def test_a_platform_that_is_not_known_is_not_found_and_nothing_is_asked_of_docker(self):
        self.here.side_effect = paths.NotFound("No platform nope")
        ps = self.patch(api.compose, "ps", return_value={})
        status, _, body = self.request("POST", "/api/platforms/nope/delete", {})
        self.assertEqual((status, json.loads(body)), (404, {"error": "No platform nope"}))
        ps.assert_not_called()


class StartsAreVerified(GatewayCase):
    """A start that puts a unit of a catalog in the on file is verified by the catalogs before anything runs, whichever route
    asks for it. The routes are the real ones, over the small project of test_layers whose units come from a catalog; what is
    replaced is what would run (the jobs), the platform's word (the place) and the preflight, which says what it was asked. A
    route that reaches its job without asking fails here, and so does a route that starts something and is neither in the table
    nor said not to take a plan."""

    LINE = "Kiosk changed on disk after it was installed. Repair Fixture catalog, then start it again."
    PLACE = "my-account/fake-env-dev"
    # The routes that start containers of what is on already: they take no plan and put no unit in the on file (8.5 gates the
    # plan of a start), and say so here. A route that is new and starts something is not among them: it joins the table
    NO_PLAN = {
        f"/api/(addon|playground)s/{api.UNIT}/containers/{api.DO}": "the containers of an item that is on, as the files say now",
        f"/api/groups/{api.ID}/{api.DO}": "the containers of a group of the platform",
        f"/api/services/{api.SERVICE}/{api.DO}": "one service that is on",
    }

    def setUp(self):
        super().setUp()
        for kind in ("addons", "setups", "playgrounds"):
            for unit in self.project[kind].values():
                unit["catalog"] = "fixture"
        for kind, uid in (("addons", "kiosk"), ("addons", "hybrid"), ("setups", "observability")):
            self.project[kind][uid]["deploys_to"] = ["cloud"]
        self.verify = self.patch(api.service, "preflight")
        # What would run: one job queue for every module that starts a job
        for module in (layers, playgrounds, stack):
            self.patch(module, "JOBS", self.jobs)
        self.place_found = {
            "id": self.PLACE, "name": "Development (My Account)", "state": "ready", "missing": None, "reason": "",
            "platform": {"id": "my-account", "name": "My Account", "kind": "cloud"}, "capabilities": None, "cannot": {},
            "environment": {"id": "fake-env-dev", "name": "Development", "organization": "org"}, "token": {"state": "valid"},
            "gateway": {"id": "gw-1", "name": "lab", "state": "STARTED", "here": True, "kept": True, "connection": "CONNECTED"}}
        self.patch(api.places, "verify", side_effect=lambda project, place: self.place_found)
        self.patch(api.places, "serving", return_value=None)
        self.patch(api.playgrounds, "created", return_value=[])
        self.patch(core, "current", return_value={"place": self.PLACE, "chosen": True, "environment": "Development"})
        self.patch(core, "plan", side_effect=self.cloud_plan)

    def cloud_plan(self, project, state, containers, saved=None, live=False):
        """What core.plan answers for Start all with API Management in the Cloud: the plan of the gateway's add-on for that place."""
        gateway = layers.plan(project, state, self.running, "addon", "hybrid", "start", place=self.where)
        return {"gateway": gateway, "place": self.where, "gateway_id": "gw-1", "others": [], "not_started": [],
                "dialog": layers.dialog(gateway), "blocked": gateway["blocked"], "needed": True}

    def routes(self):
        """(what it is, the path, the body, what the preflight must be handed of it, what is on and runs when it asks as
        (add-ons, playground)): every route that starts something through a plan. The units are the item's and what it brings."""
        place, nothing, seeded = {"place": self.PLACE}, ([], None), (["hybrid"], "hotels")
        return [
            ("an add-on", "/api/addons/kiosk/start", {}, [("addon", "kiosk")], nothing),
            ("a setup", "/api/setups/observability/start", {}, [("setup", "observability"), ("addon", "jade"), ("addon", "kiosk")],
             nothing),
            ("a playground", "/api/playgrounds/welcome/start", {}, [("playground", "welcome"), ("addon", "agent-lens")], nothing),
            ("an add-on in a place", "/api/addons/kiosk/start", place, [("addon", "kiosk"), ("addon", "hybrid")], nothing),
            ("a setup in a place", "/api/setups/observability/start", place,
             [("addon", "jade"), ("addon", "kiosk"), ("addon", "hybrid")], nothing),
            ("a playground in a place", "/api/playgrounds/hotels/start", place, [("playground", "hotels"), ("addon", "hybrid")],
             nothing),
            ("a playground that is on, seeded in a place", "/api/playgrounds/hotels/reseed", place,
             [("playground", "hotels"), ("addon", "hybrid")], seeded),
            ("the gateway of a platform", f"{self.GATEWAY}/start", {}, [("addon", "hybrid")], nothing),
            ("Start all with API Management in the Cloud", "/api/stack/start", {"core": True}, [("addon", "hybrid")], nothing),
        ]

    def being(self, explicit, playground):
        """What is on, and runs, as a route finds it."""
        self.state = test_layers.on(self.project, explicit, playground)
        self.running = test_layers.up(*explicit, playgrounds=[playground] if playground else (), project=self.project)

    def hit(self, path):
        """The pattern of the route that answers a POST to this path."""
        return next(pattern.pattern for method, pattern, _ in api.ROUTES if method == "POST" and pattern.fullmatch(path))

    def test_every_route_that_starts_something_asks_the_catalogs_with_the_project_before_its_job(self):
        for what, path, body, closure, (explicit, playground) in self.routes():
            with self.subTest(what=what):
                self.being(explicit, playground)
                self.verify.reset_mock(side_effect=True)
                self.jobs.reset_mock()
                self.verify.side_effect = CatalogError("damaged", self.LINE, 409)
                status, _, answer = self.request("POST", path, body)
                self.assertEqual((status, json.loads(answer)), (409, {"error": self.LINE, "code": "damaged"}),
                                 "refused with the line of the catalogs")
                self.jobs.start.assert_not_called()
                self.verify.assert_called_once()
                handed_project, handed = self.verify.call_args.args
                self.assertIs(handed_project, self.project, "the project the route loaded, not another one")
                self.assertLessEqual(set(closure), set(handed), f"the units it puts in the on file: {handed}")
                # ... and when the catalogs let it through, the job starts
                self.verify.side_effect = None
                self.jobs.reset_mock()
                self.assertEqual(self.request("POST", path, body)[0], 200)
                self.jobs.start.assert_called_once()

    def test_a_route_that_starts_something_through_a_plan_is_in_the_table_and_the_others_say_why_not(self):
        starting = {pattern.pattern for method, pattern, _ in api.ROUTES
                    if method == "POST" and re.search("start|reseed", pattern.pattern)}
        tried = {self.hit(route[1]) for route in self.routes()}
        self.assertEqual(starting - tried, set(self.NO_PLAN), "a new route that starts something takes a plan: add it to the table")
        self.assertEqual(set(self.NO_PLAN) - starting, set(), "and a route that is gone leaves the list")
        for pattern in self.NO_PLAN:
            self.assertTrue(any(one.pattern == pattern for _, one, _ in api.ROUTES), pattern)

    def test_a_stop_asks_the_catalogs_nothing(self):
        self.being(["kiosk"], None)
        self.assertEqual(self.request("POST", "/api/addons/kiosk/stop", {})[0], 200)
        self.jobs.start.assert_called_once()
        self.verify.assert_not_called()

    def test_what_the_page_did_not_show_is_refused_before_the_catalogs_are_asked(self):
        self.being(["jade"], None)   # AI Agent Lens conflicts with Jade: starting it stops it, which the page must have shown
        status, _, answer = self.request("POST", "/api/addons/agent-lens/start", {})
        self.assertEqual((status, json.loads(answer)), (409, {"error": "Would also stop Jade"}))
        self.verify.assert_not_called()
        self.jobs.start.assert_not_called()


class JobRoutes(LabUI):
    def setUp(self):
        super().setUp()
        self.job = Job("Start Kafka", [])
        jobs = Jobs()
        jobs.jobs.append(self.job)
        self.patch(api, "JOBS", jobs)

    def test_output_is_streamed_then_the_summary(self):
        self.job.lines += ["$ docker compose up -d kafka", "Container gio-kafka  Started"]
        self.job.done, self.job.ok = True, True
        status, headers, body = self.request("GET", f"/api/jobs/{self.job.id}")
        self.assertEqual((status, headers["Content-Type"]), (200, "text/event-stream"))
        lines, done = body.decode().strip().split("\n\n")
        self.assertEqual(json.loads(lines[len("data: "):]), self.job.lines)
        self.assertEqual(done, f"event: done\ndata: {json.dumps(self.job.summary())}")

    def test_cancel(self):
        status, _, body = self.request("POST", f"/api/jobs/{self.job.id}/cancel")
        self.assertEqual((status, json.loads(body)["id"]), (200, self.job.id))
        ran = []
        self.job.steps = [ran.append]
        self.job.run()
        self.assertEqual((ran, self.job.ok, self.job.error()), ([], False, "Canceled"))

    def test_unknown_job(self):
        self.assertEqual(self.request("GET", "/api/jobs/00000000")[0], 404)
        self.assertEqual(self.request("POST", "/api/jobs/00000000/cancel")[0], 404)


class CommandLine(LabUI):

    RUNNING = "Gravitee Lab UI is already running on http://localhost:{}. " \
              "To load a new version: stop it with Ctrl+C in its terminal, then run make ui again"

    def another_program(self, handler=None):
        """A web server that is not the Lab UI, on a free port of its own: answers its port."""
        class Other(BaseHTTPRequestHandler):
            server_version = "SomethingElse"

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, fmt, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler or Other)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_port

    def test_compose_version(self):
        # The words of the Makefile's, so that `make ui` and `python3 lab-ui/server.py` say the same
        run = self.patch(api.compose, "run")
        old = "Docker Compose 5.0 or later is needed, found: {} (see Prerequisites in README.md)"
        for answer, expected in [((0, "2.40.3\n", ""), old.format("2.40.3")),
                                 ((0, "v4.9.1\n", ""), old.format("4.9.1")),
                                 ((0, "dev\n", ""), old.format("dev")),
                                 ((0, "5.0.0-desktop.1\n", ""), None), ((0, "v5.4.0\n", ""), None),
                                 ((0, "5.6.0\n", ""), None),
                                 ((1, "", "docker: 'compose' is not a docker command"), old.format("none")),
                                 ((127, "", "docker: not found"),
                                  "docker not found: install Docker with Compose 5.0 or later "
                                  "(see Prerequisites in README.md)")]:
            run.return_value = answer
            self.assertEqual(api.requirement(), expected)

    def test_busy_port_opens_the_lab_ui_that_runs(self):
        port = self.server.server_port
        opened, printed = self.patch(api.webbrowser, "open"), io.StringIO()
        with contextlib.redirect_stdout(printed):
            api.serve(port, browser=False)
            opened.assert_not_called()
            api.serve(port, browser=True)
        opened.assert_called_once_with(f"http://localhost:{port}")
        self.assertEqual(printed.getvalue(), (self.RUNNING.format(port) + "\n") * 2)

    def test_busy_port_of_another_program_is_said_with_the_port_to_use_instead(self):
        port, opened = self.another_program(), self.patch(api.webbrowser, "open")
        with self.assertRaises(SystemExit) as stopped:
            api.serve(port, browser=True)
        self.assertEqual(stopped.exception.code,
                         f"Port {port} is used by another program. Free it, or use another port: "
                         f"UI_PORT={port + 1} make ui (the UIs of a playground do not show next to the guide on "
                         "another port)")
        opened.assert_not_called()

    def test_what_answers_is_the_lab_ui_by_the_server_header_of_its_page(self):
        self.assertTrue(api.is_lab_ui(self.server.server_port))
        self.assertFalse(api.is_lab_ui(self.another_program()))

    def test_anything_but_an_answer_is_another_program(self):
        class Hangs(BaseHTTPRequestHandler):
            def do_GET(self):
                self.connection.close()   # no answer at all
        self.assertFalse(api.is_lab_ui(self.another_program(Hangs)))
        with socket.socket() as free:   # nothing listens
            free.bind(("127.0.0.1", 0))
            self.assertFalse(api.is_lab_ui(free.getsockname()[1]))
        for error in (TimeoutError("timed out"), http.client.BadStatusLine("")):
            with mock.patch.object(api.http.client, "HTTPConnection", side_effect=error):
                self.assertFalse(api.is_lab_ui(self.server.server_port), error)

    def test_the_probe_does_not_wait_longer_than_a_second(self):
        with mock.patch.object(api.http.client, "HTTPConnection") as connection:
            connection.return_value.getresponse.return_value.getheader.return_value = "GraviteeLabUI Python/3.12"
            self.assertTrue(api.is_lab_ui(9000))
        connection.assert_called_once_with("127.0.0.1", 9000, timeout=1)

    def test_what_it_prints_is_utf8_whatever_the_terminal_says(self):
        streams = [mock.Mock(spec=["reconfigure"]), mock.Mock(spec=["reconfigure"])]   # no file number to ask a tty
        self.patch(api, "requirement", return_value=None)
        self.patch(api.layers, "prepare")
        serve = self.patch(api, "serve")
        with mock.patch.object(api.sys, "stdout", streams[0]), mock.patch.object(api.sys, "stderr", streams[1]):
            api.main(["--no-open", "--port", "9123"])
        for stream in streams:
            stream.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
        serve.assert_called_once_with(9123, False)
        # A stream that cannot be reconfigured (a test's, a pipe wrapper) is left as it is
        with mock.patch.object(api.sys, "stdout", io.StringIO()), mock.patch.object(api.sys, "stderr", io.StringIO()):
            api.main(["--no-open"])


class Listing(Case):
    """`server.py --list`: what is on and its consoles, in text; it moves nothing, and says so while a move waits."""

    def setUp(self):
        self.project = test_layers.lab()
        self.state = test_layers.on(self.project, ["kiosk"], "welcome")
        self.patch(api.project, "load", return_value=self.project)
        self.patch(api.layers, "current", side_effect=lambda project: self.state)
        self.waiting = self.patch(api.layout, "waiting", return_value=False)

    def printed(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            api.listing()
        return out.getvalue()

    def test_what_is_on_comes_first_then_its_consoles_by_section(self):
        lines = self.printed().splitlines()
        self.assertEqual(lines[0], "On top of the platform: Welcome, AI Agent Lens, Kiosk")
        self.assertIn("Hello", self.printed())
        self.assertNotIn(api.layout.GUARD, self.printed())
        self.state = test_layers.on(self.project)
        self.assertEqual(self.printed().splitlines()[0], "On top of the platform: nothing")

    def test_a_layout_that_waits_to_be_moved_is_said_before_anything_else_and_nothing_is_moved(self):
        self.waiting.return_value = True
        migrate = self.patch(api.layout, "migrate", side_effect=AssertionError("--list moves nothing"))
        lines = self.printed().splitlines()
        self.assertEqual(lines[:2], ["This Lab was updated: run make update to move your settings.",
                                     "On top of the platform: Welcome, AI Agent Lens, Kiosk"])
        migrate.assert_not_called()


class Port(Case):
    """The port the Lab UI listens on: 9000, UI_PORT, or --port."""

    def setUp(self):
        self.patch(api, "requirement", return_value=None)
        self.patch(api.layers, "prepare")
        self.serve = self.patch(api, "serve")

    def main(self, argv, **environment):
        with mock.patch.dict(api.os.environ, environment):
            if "UI_PORT" not in environment:
                api.os.environ.pop("UI_PORT", None)
            api.main(argv)

    def test_9000_unless_told_otherwise(self):
        for environment in ({}, {"UI_PORT": ""}):
            self.main(["--no-open"], **environment)
            self.serve.assert_called_with(9000, False)

    def test_ui_port_is_the_port(self):
        self.main([], UI_PORT="9001")
        self.serve.assert_called_with(9001, True)

    def test_the_option_is_stronger_than_ui_port(self):
        self.main(["--port", "9100"], UI_PORT="9001")
        self.serve.assert_called_with(9100, True)
        self.main(["--port", "9100"], UI_PORT="not a port")   # not read
        self.serve.assert_called_with(9100, True)

    def test_a_ui_port_that_is_no_port_is_said_in_one_line(self):
        for value in ("abc", "9000.5", "-1", "0", "65536", "9 000", "\u0663", "9000;"):
            self.serve.reset_mock()
            with self.subTest(value), self.assertRaises(SystemExit) as stopped:
                self.main([], UI_PORT=value)
            self.assertEqual(stopped.exception.code, "UI_PORT must be a port number, for example 9001")
            self.serve.assert_not_called()

    def test_a_ui_port_that_is_no_port_does_not_stop_what_does_not_serve(self):
        self.patch(api, "listing")
        self.main(["--list"], UI_PORT="abc")
        api.listing.assert_called_once_with()
        self.serve.assert_not_called()

    def test_a_port_option_that_is_no_port_is_a_usage_error(self):
        for value in ("abc", "0", "65536", "-1"):
            with self.subTest(value), contextlib.redirect_stderr(io.StringIO()) as said, \
                    self.assertRaises(SystemExit) as stopped:
                self.main(["--port", value])
            self.assertEqual(stopped.exception.code, 2)
            self.assertIn("--port: must be a port number, for example 9001", said.getvalue())
        self.serve.assert_not_called()



class CatalogRoutes(LabUI):
    """Every route of the catalogs calls one function of the service with what the body says, and answers what it
    answered; the logic is the service's (test_catalog_service)."""

    ROW = {"id": "acme-hub", "kind": "git"}

    def setUp(self):
        super().setUp()
        self.patch(api.project, "load", return_value=PROJECT)
        self.calls = {}
        for name in ("check_address", "add", "add_listed", "create_folder", "check_now", "set_auto", "allow_host", "update",
                     "undo", "changes", "repair", "remove"):
            self.calls[name] = self.patch(api.service, name, return_value={"catalog": self.ROW})
        self.removed = self.patch(api.firstrun, "removed")   # the first-run thread looks at the layout again after a Remove

    def post(self, path, body=None, **how):
        status, _, answer = self.request("POST", path, body, **how)
        return status, json.loads(answer)

    def test_the_check_of_an_address_takes_the_address_and_a_branch_and_answers_the_preview(self):
        self.calls["check_address"].return_value = {"ticket": "9f3a4c1d2e5b6a70", "expires": 600}
        self.assertEqual(self.post("/api/catalogs/check", {"address": "acme/lab-hub", "branch": "main"}),
                         (200, {"ticket": "9f3a4c1d2e5b6a70", "expires": 600}))
        self.calls["check_address"].assert_called_once_with("acme/lab-hub", "main")
        self.post("/api/catalogs/check", {"address": "acme/lab-hub"})
        self.calls["check_address"].assert_called_with("acme/lab-hub", None)

    def test_the_add_takes_a_ticket_and_may_say_the_allowance_and_a_listed_catalog_is_added_by_its_id(self):
        self.assertEqual(self.post("/api/catalogs", {"ticket": "abc"}), (200, {"catalog": self.ROW}))
        self.calls["add"].assert_called_once_with("abc", host=False)
        self.post("/api/catalogs", {"ticket": "abc", "host": True})
        self.calls["add"].assert_called_with("abc", host=True)
        self.post("/api/catalogs", {"ticket": "abc", "host": "yes"})
        self.calls["add"].assert_called_with("abc", host=False)
        self.assertEqual(self.post("/api/catalogs", {"listed": "gravitee"}), (200, {"catalog": self.ROW}))
        self.calls["add_listed"].assert_called_once_with("gravitee")
        self.assertEqual(self.calls["add"].call_count, 3, "a listed catalog is not a ticket")

    def test_new_catalog_takes_a_name(self):
        self.assertEqual(self.post("/api/catalogs/new", {"name": "My catalog"}), (200, {"catalog": self.ROW}))
        self.calls["create_folder"].assert_called_once_with("My catalog")

    def test_the_routes_of_one_catalog_take_its_namespace(self):
        cases = [("check", {}, "check_now", ("acme-hub",), {}),
                 ("auto", {"enabled": False}, "set_auto", ("acme-hub", False), {}),
                 ("allow", {"enabled": True}, "allow_host", ("acme-hub", True), {}),
                 ("repair", {}, "repair", ("acme-hub",), {}),
                 ("changes", {"ticket": "abc"}, "changes", ("acme-hub", "abc"), {})]
        for route, body, function, args, how in cases:
            with self.subTest(route=route):
                self.assertEqual(self.post(f"/api/catalogs/acme-hub/{route}", body)[0], 200)
                self.calls[function].assert_called_once_with(*args, **how)

    def test_update_and_go_back_ask_for_the_plan_or_apply_a_ticket(self):
        self.post("/api/catalogs/acme-hub/update", {"check": True})
        self.calls["update"].assert_called_with("acme-hub", check=True, ticket=None, confirm_history=False)
        self.post("/api/catalogs/acme-hub/update", {"ticket": "abc", "confirm_history": True})
        self.calls["update"].assert_called_with("acme-hub", check=False, ticket="abc", confirm_history=True)
        self.post("/api/catalogs/acme-hub/update", {"ticket": "abc", "confirm_history": "true"})
        self.calls["update"].assert_called_with("acme-hub", check=False, ticket="abc", confirm_history=False)
        self.post("/api/catalogs/acme-hub/undo", {"check": True})
        self.calls["undo"].assert_called_with("acme-hub", check=True, ticket=None)
        self.post("/api/catalogs/acme-hub/undo", {"ticket": "abc"})
        self.calls["undo"].assert_called_with("acme-hub", check=False, ticket="abc")
        for route in ("update", "undo"):
            status, body = self.post(f"/api/catalogs/acme-hub/{route}", {})
            self.assertEqual((status, body), (400, {"error": "Send check, or the ticket of the plan"}), route)

    def test_remove_says_what_it_would_do_or_removes_and_a_request_that_says_neither_removes_nothing(self):
        self.post("/api/catalogs/acme-hub/delete", {"check": True})
        self.calls["remove"].assert_called_once_with("acme-hub", check=True)
        self.post("/api/catalogs/acme-hub/delete", {"keep_settings": False})
        self.calls["remove"].assert_called_with("acme-hub", keep_settings=False)
        self.post("/api/catalogs/acme-hub/delete", {"keep_settings": True})
        self.calls["remove"].assert_called_with("acme-hub", keep_settings=True)
        calls = self.calls["remove"].call_count
        self.assertEqual(self.post("/api/catalogs/acme-hub/delete", {}), (400, {"error": "Say whether to keep the settings of the catalog"}))
        self.assertEqual(self.calls["remove"].call_count, calls)

    def test_once_a_catalog_is_removed_the_first_run_thread_looks_at_the_layout_again_and_not_before(self):
        # a move that waits for the default catalog is settled by the person's decision to have none
        self.post("/api/catalogs/acme-hub/delete", {"check": True})
        self.assertEqual(self.post("/api/catalogs/acme-hub/delete", {})[0], 400)
        self.removed.assert_not_called()
        self.calls["remove"].side_effect = CatalogError("held", "Turn off Bank first.", 409)
        self.assertEqual(self.post("/api/catalogs/acme-hub/delete", {"keep_settings": True})[0], 409)
        self.removed.assert_not_called()   # nothing was removed
        self.calls["remove"].side_effect = None
        order = mock.Mock()
        order.attach_mock(self.calls["remove"], "remove")
        order.attach_mock(self.removed, "removed")
        self.assertEqual(self.post("/api/catalogs/acme-hub/delete", {"keep_settings": False}), (200, {"catalog": self.ROW}))
        self.assertEqual(order.mock_calls, [mock.call.remove("acme-hub", keep_settings=False), mock.call.removed()])

    def test_what_is_not_a_namespace_is_not_a_route(self):
        for path in ("/api/catalogs/Acme/check", "/api/catalogs/a/check", "/api/catalogs/-x-/check", "/api/catalogs/x_y/check",
                     "/api/catalogs/acme-hub/delete/now", "/api/catalogs/acme-hub", "/api/catalogs/acme-hub/launch"):
            with self.subTest(path=path):
                self.assertEqual(self.post(path, {})[0], 404)
        for function in self.calls.values():
            function.assert_not_called()

    def test_a_refusal_of_the_service_is_its_line_and_its_code_with_its_own_status_and_nothing_of_the_output(self):
        cases = [(400, "bad_address", "Use owner/repo, a Git address such as https://github.com/owner/repo, or the path of a folder."),
                 (409, "busy", "Another catalog change is running. Try again in a moment."),
                 (409, "ticket_expired", "This took too long. Start again."),
                 (502, "unreachable", "Cannot reach github.com. Check your connection, then try again.")]
        for status, code, line in cases:
            with self.subTest(code=code):
                self.calls["check_now"].side_effect = CatalogError(code, line, status, output="fatal: token ghp_secret failed")
                answer = self.request("POST", "/api/catalogs/acme-hub/check", {})
                self.assertEqual((answer[0], json.loads(answer[2])), (status, {"error": line, "code": code}))
                self.assertNotIn(b"ghp_secret", answer[2])

    def test_a_refusal_that_is_a_runtime_error_too_is_a_catalog_error_first(self):
        from lab_ui.catalogs import service
        self.calls["check_now"].side_effect = service.Refused("damaged", "Ledger changed on disk after it was installed. Repair Acme "
                                                              "Hub, then start it again.", 409)
        status, body = self.post("/api/catalogs/acme-hub/check", {})
        self.assertEqual((status, body["code"]), (409, "damaged"))
        self.calls["check_now"].side_effect = RuntimeError("Stop it first")
        self.assertEqual(self.post("/api/catalogs/acme-hub/check", {}), (409, {"error": "Stop it first"}), "no code, as before")
        self.calls["check_now"].side_effect = paths.NotFound("No catalog nowhere")
        self.assertEqual(self.post("/api/catalogs/acme-hub/check", {})[0], 404)
        self.calls["check_now"].side_effect = ValueError("The branch must be text")
        self.assertEqual(self.post("/api/catalogs/acme-hub/check", {})[0], 400)
        self.calls["check_now"].side_effect = KeyError("boom")
        self.assertEqual(self.post("/api/catalogs/acme-hub/check", {}), (500, {"error": "KeyError: 'boom'"}))

    def test_only_the_page_asks_and_never_by_a_get(self):
        for path in ("/api/catalogs/check", "/api/catalogs", "/api/catalogs/new", "/api/catalogs/acme-hub/check",
                     "/api/catalogs/acme-hub/delete", "/api/catalogs/acme-hub/allow"):
            with self.subTest(path=path):
                self.assertEqual(self.request("POST", path, {}, headers={"X-Gravitee-Lab-UI": None})[0], 403)
                self.assertEqual(self.request("POST", path, {}, headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
                self.assertEqual(self.request("POST", path, {}, headers={"Host": "evil.example"})[0], 403)
                self.assertEqual(self.request("GET", path)[0], 404, "no GET ever runs git or asks a network")
        for function in self.calls.values():
            function.assert_not_called()

class CatalogBodies(LabUI):
    """A body that is not what the service takes is a bad request, with the real functions behind the routes."""

    def test_a_field_of_the_wrong_type_is_a_bad_request_or_an_expired_ticket_never_a_crash(self):
        self.patch(api.project, "load", return_value=PROJECT)
        for path, body, status, line in (
                ("/api/catalogs/check", {"address": 5}, 400, "The address must be text"),
                ("/api/catalogs/check", {}, 400, "The address must be text"),
                ("/api/catalogs/check", {"address": "owner/repo", "branch": 5}, 400, "The branch must be text"),
                ("/api/catalogs/new", {"name": 5}, 400, "The name must be text"),
                ("/api/catalogs/acme-hub/auto", {"enabled": "yes"}, 400, "enabled must be true or false"),
                ("/api/catalogs/acme-hub/allow", {"enabled": 1}, 400, "enabled must be true or false"),
                ("/api/catalogs", {"ticket": 5}, 409, "This took too long. Start again."),
                ("/api/catalogs", {}, 409, "This took too long. Start again."),
                ("/api/catalogs/acme-hub/changes", {"ticket": 5}, 409, "This took too long. Start again."),
                ("/api/catalogs", {"listed": 5}, 400, "That is not a catalog this Lab lists."),
                ("/api/catalogs/acme-hub/delete", {"keep_settings": "no"}, 404, "No catalog acme-hub")):
            with self.subTest(path=path, body=body):
                status_now, _, answer = self.request("POST", path, body)
                self.assertEqual((status_now, json.loads(answer)["error"]), (status, line))
        self.assertEqual(self.request("POST", "/api/catalogs/nowhere/check", {})[0], 404)



class LayoutRoutes(LabUI):
    """The notice of a move is closed, a move that failed is tried again, and the answer is the status of the
    layout, which is what the state says. The move itself is the layout's (test_layout)."""

    def setUp(self):
        super().setUp()
        self.calls = mock.Mock()
        self.status = {"phase": "ready", "notice": None}
        for module, name in ((api.layout, "dismiss"), (api.layout, "migrate"), (api.firstrun, "wake"), (api.project, "forget")):
            self.calls.attach_mock(self.patch(module, name), f"{module.__name__.rsplit('.', 1)[-1]}_{name}")
        self.patch(api.layout, "status", side_effect=lambda: self.status)

    def test_closing_the_notice_writes_it_down_and_has_the_project_read_again_before_the_status_is_answered(self):
        self.status = {"phase": "ready", "notice": None}
        status, _, body = self.request("POST", "/api/layout/close", {})
        self.assertEqual((status, json.loads(body)), (200, {"phase": "ready", "notice": None}))
        self.assertEqual(self.calls.mock_calls, [mock.call.layout_dismiss(), mock.call.project_forget()],
                         "the marker is written, then what the project was read from is forgotten")

    def test_trying_again_asks_what_has_no_copy_again_and_makes_the_move_again_in_that_order_and_answers_how_it_went(self):
        failed = {"phase": "failed", "notice": {"text": "Could not move your settings. Nothing was deleted.", "button": "Try again"}}
        self.status = failed
        self.calls.layout_migrate.side_effect = lambda: setattr(self, "status", {"phase": "ready", "notice": None}) or {}
        status, _, body = self.request("POST", "/api/layout/retry", {})
        self.assertEqual((status, json.loads(body)), (200, {"phase": "ready", "notice": None}),
                         "the status after the move, not the one before")
        self.assertEqual(self.calls.mock_calls, [mock.call.firstrun_wake(), mock.call.layout_migrate(), mock.call.project_forget()])
        # A move that fails again is not a failure of the request: the status says it, with the Try again to press once more
        self.calls.reset_mock()
        self.calls.layout_migrate.side_effect = lambda: setattr(self, "status", failed) or {
            "problems": ["Could not move your settings. Nothing was deleted."]}
        status, _, body = self.request("POST", "/api/layout/retry", {})
        self.assertEqual((status, json.loads(body)), (200, failed))
        self.assertEqual(self.calls.project_forget.call_count, 1)

    def test_a_fault_of_the_move_is_the_answer_of_any_other_and_the_project_is_not_told_it_changed(self):
        self.calls.layout_migrate.side_effect = KeyError("plan")
        status, _, body = self.request("POST", "/api/layout/retry", {})
        self.assertEqual((status, json.loads(body)), (500, {"error": "KeyError: 'plan'"}))
        self.calls.project_forget.assert_not_called()

    def test_only_the_page_asks_and_never_by_a_get(self):
        for path in ("/api/layout/close", "/api/layout/retry"):
            with self.subTest(path=path):
                self.assertEqual(self.request("POST", path, {}, headers={"X-Gravitee-Lab-UI": None})[0], 403)
                self.assertEqual(self.request("POST", path, {}, headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
                self.assertEqual(self.request("POST", path, {}, headers={"Host": "evil.example"})[0], 403)
                self.assertEqual(self.request("GET", path)[0], 404)
        self.assertEqual(self.calls.mock_calls, [], "none of them reached the layout")


class ClosingTheNotice(LabUI):
    """The route over a real marker: closing the notice changes what the project is read from, so the next state is read again
    and does not carry it."""

    REPORT = {"report": {"moved": ["addon:scratch"], "personal": [], "promoted": ["addon:scratch"], "kept": [], "left": [],
                         "held": [], "turned_off": [], "problems": []}}

    def setUp(self):
        super().setUp()
        self.folder(paths, api.project)
        self.write(".lab/layout.json", json.dumps({"layout": 2, "from": 1, "migrated": "2026-10-17T09:00:00Z"}))
        self.write(".lab/migration/report.json", json.dumps(self.REPORT))

    def test_the_notice_is_gone_from_the_status_and_the_project_fingerprint_moved(self):
        before = api.layout.status()
        self.assertEqual(before["phase"], "ready")
        self.assertEqual([row["items"] for row in before["notice"]["rows"]], [["scratch"]])
        fingerprint = api.project.fingerprint()
        status, _, body = self.request("POST", "/api/layout/close", {})
        self.assertEqual((status, json.loads(body)), (200, {"phase": "ready", "notice": None}))
        self.assertEqual(api.layout.status(), {"phase": "ready", "notice": None})
        self.assertNotEqual(api.project.fingerprint(), fingerprint, "what the project reads changed: it is read again")
        self.assertIsNone(api.project._cache["key"], "and it is told to forget, whatever the fingerprint of the next second")
        self.assertIn("closed", json.loads((self.root / ".lab/layout.json").read_text(encoding="utf-8")))
        # Closed twice, or with nothing to close: the same answer, the marker is not written again
        marker = (self.root / ".lab/layout.json").read_text(encoding="utf-8")
        self.assertEqual(self.request("POST", "/api/layout/close", {})[0], 200)
        self.assertEqual((self.root / ".lab/layout.json").read_text(encoding="utf-8"), marker)


class JunkBodies(LabUI):
    """A body that is not what a route reads is a 400 with a reason, never a 500."""

    def test_the_versions_of_the_images_are_a_mapping(self):
        self.patch(api.project, "load", return_value=PROJECT)
        save = self.patch(api.images, "save")
        status, _, body = self.request("POST", "/api/images", {"values": "x"})
        self.assertEqual((status, json.loads(body)), (400, {"error": "The versions must be a mapping"}))
        save.assert_not_called()


class UseCatalog(LabUI):
    """"Use the catalog version": the function of `contribute` behind its route."""

    def setUp(self):
        super().setUp()
        self.patch(api.project, "load", return_value=PROJECT)
        self.contribute = mock.Mock()
        self.contribute.use_catalog_version.return_value = {"id": "@acme-hub/bank"}
        self.contribute.destinations.return_value = [{"id": "gravitee", "name": "Gravitee public", "kind": "git",
                                                      "writable": True, "reason": ""}]
        self.patch(api, "contribute", self.contribute)

    def test_the_route_takes_a_kind_and_an_id_and_answers_the_id_of_the_catalog_item(self):
        status, _, body = self.request("POST", "/api/playgrounds/local/bank/use-catalog", {})
        self.assertEqual((status, json.loads(body)), (200, {"id": "@acme-hub/bank"}))
        self.contribute.use_catalog_version.assert_called_once_with(PROJECT, "playground", "local/bank")
        self.request("POST", "/api/addons/local/echo/use-catalog", {})
        self.contribute.use_catalog_version.assert_called_with(PROJECT, "addon", "local/echo")
        self.assertEqual(self.request("POST", "/api/groups/local/echo/use-catalog", {})[0], 404)

    def test_held_while_either_twin_is_on_is_the_modules_refusal(self):
        self.contribute.use_catalog_version.side_effect = CatalogError("held", "Turn off Bank first.", 409)
        status, _, body = self.request("POST", "/api/playgrounds/local/bank/use-catalog", {})
        self.assertEqual((status, json.loads(body)), (409, {"error": "Turn off Bank first.", "code": "held"}))

    def test_the_first_check_of_a_share_names_the_destinations_beside_the_fields_it_always_had(self):
        share = self.patch(api.personal, "share", return_value={"file": "f", "problems": [], "changes": ["compose.yml"]})
        status, _, body = self.request("POST", "/api/playgrounds/local/bank/share", {"check": True})
        self.assertEqual(json.loads(body), {"file": "f", "problems": [], "changes": ["compose.yml"], "destinations": [
            {"id": "gravitee", "name": "Gravitee public", "kind": "git", "writable": True, "reason": ""}]})
        share.assert_called_once_with(PROJECT, "playground", "local/bank", check_only=True)
        self.contribute.destinations.assert_called_once_with(PROJECT, "playground", "local/bank")

    def test_a_share_with_a_destination_is_the_modules_check_and_share_and_the_old_share_has_no_destinations(self):
        share = self.patch(api.personal, "share", return_value={"branch": "lab/bank-20261016"})
        self.request("POST", "/api/playgrounds/local/bank/share", {"check": True, "to": "gravitee"})
        share.assert_called_with(PROJECT, "playground", "local/bank", check_only=True, to="gravitee")
        self.request("POST", "/api/playgrounds/local/bank/share", {"to": "gravitee"})
        share.assert_called_with(PROJECT, "playground", "local/bank", check_only=False, to="gravitee")
        share.return_value = {"file": "f", "problems": [], "changes": []}
        _, _, body = self.request("POST", "/api/playgrounds/local/bank/share", {})
        self.assertNotIn("destinations", json.loads(body), "only a check names them")
        self.request("POST", "/api/playgrounds/local/bank/share", {"to": ""})
        share.assert_called_with(PROJECT, "playground", "local/bank", check_only=False)
        status, _, body = self.request("POST", "/api/playgrounds/local/bank/share", {"to": ["x"]})
        self.assertEqual((status, json.loads(body)), (400, {"error": "The destination must be text"}))

    def test_no_destination_at_all_is_an_empty_list_the_page_answers_with_new_catalog_and_add_catalog(self):
        self.contribute.destinations.return_value = []
        self.patch(api.personal, "share", return_value={"file": "f", "problems": [], "changes": []})
        _, _, body = self.request("POST", "/api/playgrounds/local/bank/share", {"check": True})
        self.assertEqual(json.loads(body)["destinations"], [])


class StateOfCatalogs(LabUI):
    """/api/state for a project with no catalog carries git, catalogs, content and layout, and nothing is run to say them."""

    def test_a_project_with_no_catalog_has_the_four_keys_and_today_s_fields_and_asks_no_git_no_network(self):
        from .. import fixtures
        fixtures.need_compose()
        self.folder(paths, api.project, compose, envfiles)
        fixtures.core(self)
        (self.root / "catalogs.default.json").unlink() if (self.root / "catalogs.default.json").exists() else None
        self.patch(paths, "_default", (None, None))
        self.patch(api.project, "forget", wraps=api.project.forget)
        self.patch(api.compose, "ps", side_effect=compose.ComposeError("Cannot connect to the Docker daemon"))
        self.patch(api.license, "status_of", lambda variable: None)
        self.patch(gitx, "version", return_value=(2, 43, 2))
        with mock.patch.object(gitx, "run", side_effect=AssertionError("git was run")), \
                mock.patch.object(gitx, "stream", side_effect=AssertionError("git was streamed")):
            status, _, body = self.request("GET", "/api/state")
        state = json.loads(body)
        self.assertEqual(status, 200, state)
        self.assertEqual(state["git"], {"available": True, "version": "2.43.2"})
        self.assertEqual(state["catalogs"], [])
        self.assertEqual(state["layout"], {"phase": "ready", "notice": None})
        self.assertIn(state["content"]["key"], ("ready", "downloading", "failed", "none"), "what the first start says")
        self.assertIsNone(state["content"]["offer"], "nothing is listed to offer")
        for key in ("docker", "wait", "license", "platform", "playgrounds", "setups", "addons", "services", "links", "problems"):
            self.assertIn(key, state)
        self.assertEqual(state["problems"], [], "nothing is wrong with the files that record the catalogs")

    def test_the_files_that_record_the_catalogs_unreadable_are_a_problem_of_the_state_for_the_banner(self):
        from .. import fixtures
        fixtures.need_compose()
        self.folder(paths, api.project, compose, envfiles)
        fixtures.core(self)
        self.patch(paths, "_default", (None, None))
        self.patch(api.compose, "ps", side_effect=compose.ComposeError("Cannot connect to the Docker daemon"))
        self.patch(api.license, "status_of", lambda variable: None)
        self.patch(gitx, "version", return_value=(2, 43, 2))
        (self.root / ".lab").mkdir(exist_ok=True)
        (self.root / ".lab" / "catalogs.json").write_text("{not json", encoding="utf-8")
        status, _, body = self.request("GET", "/api/state")
        state = json.loads(body)
        self.assertEqual(status, 200, state)
        found = [one for one in state["problems"] if one["file"] == "Catalogs"]
        self.assertEqual(len(found), 1, state["problems"])
        self.assertTrue(found[0]["message"].startswith("The file that lists your catalogs could not be read ("), found)
        self.assertIs(found[0]["editable"], False)
        self.assertEqual(state["catalogs"], [], "and nothing is shown of catalogs, as before")



class Startup(Case):
    """Bind the port first; only once it is bound, look at the layout, finish what a stop left (in the installs of the Lab
    and in the folders of the catalogs that are linked), write the intent of the first run and start the one background thread.
    A second `make ui` touches nothing; `--check`, `--list` and `--e2e` run none
    of it."""

    STEPS = (("layout", "ensure"), ("service", "recover"), ("contribute", "sweep"), ("firstrun", "reconcile"),
             ("firstrun", "start_worker"))

    def setUp(self):
        self.events = []
        events = self.events

        class Server:
            daemon_threads = False
            server_port = 9000

            def __init__(self, address, handler):
                events.append(("bind", address))

            def serve_forever(self):
                events.append(("serve",))
                raise KeyboardInterrupt

        self.patch(api, "ThreadingHTTPServer", Server)
        for module, name in self.STEPS:
            self.patch(getattr(api, module), name, side_effect=lambda module=module, name=name: events.append((module, name)))
        self.patch(api.compose, "stop_on_signals", side_effect=lambda: events.append(("signals",)))
        self.timer = self.patch(api.threading, "Timer")
        self.opened = self.patch(api.webbrowser, "open")

    def serve(self, browser=False):
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            api.serve(9000, browser)
        return printed.getvalue()

    def test_the_port_is_bound_first_then_the_five_steps_in_order_and_then_the_page_is_served(self):
        said = self.serve()
        self.assertEqual(self.events, [("bind", ("127.0.0.1", 9000)), ("signals",), ("layout", "ensure"), ("service", "recover"),
                                       ("contribute", "sweep"), ("firstrun", "reconcile"), ("firstrun", "start_worker"),
                                       ("serve",)])
        self.assertEqual(said, "Gravitee Lab UI on http://localhost:9000  (Ctrl+C to stop)\n")

    def test_the_browser_opens_once_the_steps_are_done_and_not_when_it_is_not_asked(self):
        self.serve(browser=True)
        self.timer.assert_called_once_with(0.5, api.webbrowser.open, args=("http://localhost:9000",))
        self.timer.return_value.start.assert_called_once_with()
        self.timer.reset_mock()
        self.serve(browser=False)
        self.timer.assert_not_called()

    def test_a_lab_that_serves_already_is_left_alone_not_one_of_the_five_steps_runs(self):
        for module, name in self.STEPS:
            self.patch(getattr(api, module), name, side_effect=AssertionError(f"{module}.{name} ran on the already running branch"))
        self.patch(api.compose, "stop_on_signals", side_effect=AssertionError("signals were taken over"))
        self.patch(api, "ThreadingHTTPServer", side_effect=OSError(errno.EADDRINUSE, "Address already in use"))
        self.patch(api, "is_lab_ui", return_value=True)
        said = self.serve(browser=True)
        self.assertIn("is already running", said)
        self.opened.assert_called_once_with("http://localhost:9000")

    def test_another_program_on_the_port_is_an_exit_and_touches_nothing(self):
        for module, name in self.STEPS:
            self.patch(getattr(api, module), name, side_effect=AssertionError(f"{module}.{name} ran"))
        self.patch(api, "ThreadingHTTPServer", side_effect=OSError(errno.EADDRINUSE, "Address already in use"))
        self.patch(api, "is_lab_ui", return_value=False)
        with self.assertRaises(SystemExit):
            self.serve()

    def test_an_error_other_than_a_busy_port_is_raised_before_anything_is_touched(self):
        for module, name in self.STEPS:
            self.patch(getattr(api, module), name, side_effect=AssertionError(f"{module}.{name} ran"))
        self.patch(api, "ThreadingHTTPServer", side_effect=OSError(errno.EACCES, "Permission denied"))
        with self.assertRaises(OSError):
            self.serve()

    def test_a_step_that_fails_is_a_line_on_standard_error_and_the_rest_of_the_start_goes_on(self):
        self.patch(api.service, "recover", side_effect=CatalogError("damaged", "A catalog changed on disk.", 409))
        self.patch(api.layout, "ensure", side_effect=OSError("disk full"))
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.serve()
        self.assertEqual(said.getvalue().splitlines(), [
            "Gravitee Lab UI: looking at the layout failed (OSError: disk full)",
            "Gravitee Lab UI: finishing what a stop left failed (CatalogError: A catalog changed on disk.)"])
        self.assertEqual(self.events[-3:], [("firstrun", "reconcile"), ("firstrun", "start_worker"), ("serve",)])

    def test_what_a_share_left_is_swept_after_what_a_stop_left_and_a_sweep_that_fails_keeps_nothing_from_starting(self):
        # The half copies Share leaves in the folder of a linked catalog go at start, as the installs' sweep does
        self.patch(api.contribute, "sweep", side_effect=OSError("read-only file system"))
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            self.serve()
        self.assertEqual(said.getvalue().splitlines(),
                         ["Gravitee Lab UI: sweeping what a share left failed (OSError: read-only file system)"])
        self.assertEqual([event for event in self.events if event[0] != "bind" and event != ("signals",)],
                         [("layout", "ensure"), ("service", "recover"), ("firstrun", "reconcile"), ("firstrun", "start_worker"),
                          ("serve",)])

    def test_the_command_line_runs_requirement_and_prepare_for_every_command_and_the_five_steps_for_serve_only(self):
        order = []
        self.patch(api, "requirement", side_effect=lambda: order.append("requirement"))
        self.patch(api.layers, "prepare", side_effect=lambda: order.append("prepare"))
        self.patch(api, "serve", side_effect=lambda port, browser: order.append("serve"))
        self.patch(api.check, "run", side_effect=lambda: order.append("check") or 0)
        self.patch(api, "listing", side_effect=lambda: order.append("list"))
        e2e = types.ModuleType("lab_ui.e2e")
        e2e.run = lambda *args: order.append("e2e") or 0
        with mock.patch.dict(sys.modules, {"lab_ui.e2e": e2e}), mock.patch.object(lab_ui, "e2e", e2e, create=True):   # `from . import e2e` looks at the package first
            api.main(["--no-open", "--port", "9123"])
            self.assertEqual(order, ["requirement", "prepare", "serve"])
            order.clear()
            with self.assertRaises(SystemExit):
                api.main(["--check"])
            api.main(["--list"])
            with self.assertRaises(SystemExit):
                api.main(["--e2e", "addon:echo"])
        self.assertEqual(order, ["requirement", "prepare", "check", "requirement", "prepare", "list", "requirement", "prepare", "e2e"])
        self.assertEqual(self.events, [], "none of the commands but serve binds a port or starts a step")

    def test_what_the_machine_lacks_stops_the_start_before_the_port_is_asked_for(self):
        self.patch(api, "requirement", return_value="docker not found")
        prepared = self.patch(api.layers, "prepare")
        with self.assertRaises(SystemExit) as stopped:
            api.main(["--no-open"])
        self.assertEqual(stopped.exception.code, "docker not found")
        prepared.assert_not_called()
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()


class Terminal(LabUI):
    """The shell beside a guide: what the page may ask of it, and nothing of a playground that has none."""

    PATH = "/api/playgrounds/welcome/terminal"

    def setUp(self):
        super().setUp()
        playground = {"name": "Welcome", "services": [], "addons": [], "consoles": [], "terminal": "hello-shell"}
        service = {"container": "gio-hello-shell", "owner": {"kind": "playground", "id": "welcome"}, "role": None}
        self.patch(api.project, "load", return_value={**PROJECT, "links": [], "services": {**PROJECT["services"], "hello-shell": service},
                                                      "playgrounds": {"welcome": playground, "bare": {"name": "Bare"}}})
        self.session = mock.Mock(alive=True)
        self.attached = self.patch(api.terminal, "attach", return_value=self.session)

    def test_what_is_typed_and_the_size_reach_the_shell_of_the_service_the_playground_names(self):
        status, _, body = self.request("POST", self.PATH, {"data": "k get pods\n", "size": [30, 100]})
        self.assertEqual((status, json.loads(body)), (200, {"alive": True}))
        self.attached.assert_called_once_with("welcome", "gio-hello-shell")
        self.session.resize.assert_called_once_with(30, 100)
        self.session.write.assert_called_once_with(b"k get pods\n")

    def test_what_is_not_text_or_a_size_is_not_sent(self):
        self.request("POST", self.PATH, {"data": 12, "size": [0, 0]})
        self.session.write.assert_not_called()
        self.session.resize.assert_not_called()

    def test_a_playground_without_a_terminal_or_that_does_not_exist_has_none(self):
        status, _, body = self.request("POST", "/api/playgrounds/bare/terminal", {"data": "x"})
        self.assertEqual((status, json.loads(body)), (400, {"error": "Bare has no terminal"}))
        status, _, body = self.request("POST", "/api/playgrounds/gone/terminal", {"data": "x"})
        self.assertEqual(status, 404)
        self.attached.assert_not_called()

    def test_a_container_that_does_not_run_is_a_conflict_that_says_so(self):
        self.attached.side_effect = RuntimeError("gio-hello-shell is not running")
        status, _, body = self.request("POST", self.PATH, {"data": "x"})
        self.assertEqual((status, json.loads(body)), (409, {"error": "gio-hello-shell is not running"}))

    def test_a_terminal_a_catalog_does_not_offer_is_not_opened_whatever_the_page_asks(self):
        # N05: the verdict of the playground says its terminal is not in a service of its own or of what it brings
        playground = {"name": "Welcome", "services": [], "addons": [], "consoles": [], "terminal": "hello-shell",
                      "verdict": {"state": "ok", "notes": [trust.line("N05")]}}
        service = {"container": "gio-hello-shell", "owner": {"kind": "playground", "id": "welcome"}, "role": None}
        self.patch(api.project, "load", return_value={**PROJECT, "links": [], "services": {**PROJECT["services"], "hello-shell": service},
                                                      "playgrounds": {"welcome": playground}})
        for method, path in (("POST", self.PATH), ("GET", self.PATH)):
            status, _, body = self.request(method, path, {"data": "k get pods\n"} if method == "POST" else None)
            self.assertEqual((status, json.loads(body)), (400, {"error": "Not offered by this catalog"}), method)
        self.attached.assert_not_called()
        playground["verdict"] = {"state": "ok", "notes": [trust.line("N03"), trust.line("N01")]}   # other notes say nothing of it
        self.assertEqual(self.request("POST", self.PATH, {"data": "x"})[0], 200)
        playground.pop("verdict")   # a playground of this machine is not judged
        self.assertEqual(self.request("POST", self.PATH, {"data": "x"})[0], 200)

    def test_the_page_ends_the_shell_with_close(self):
        dropped = self.patch(api.terminal, "drop")
        status, _, body = self.request("POST", f"{self.PATH}/close", {})
        self.assertEqual((status, json.loads(body)), (200, {"closed": True}))
        dropped.assert_called_once_with("welcome")

    def test_it_is_for_the_lab_uis_own_page(self):
        status, _, _ = self.request("POST", self.PATH, {"data": "x"}, headers={"X-Gravitee-Lab-UI": None})
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", self.PATH, {"data": "x"}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("GET", self.PATH, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", self.PATH, {"data": "x"}, headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        self.session.write.assert_not_called()

    def test_the_output_is_streamed_from_the_offset_asked_for_until_the_shell_ends(self):
        self.session.read.side_effect = [(5, b"hello", True), (5, b"", False)]
        status, headers, body = self.request("GET", f"{self.PATH}?from=2")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream")
        self.assertEqual(headers["Content-Security-Policy"], "frame-ancestors 'none'")
        self.assertEqual([call.args[0] for call in self.session.read.call_args_list], [2, 5])
        self.assertEqual(body.decode(), 'event: out\ndata: {"at": 5, "data": "aGVsbG8="}\n\nevent: exit\ndata: {}\n\n')

    def test_a_quiet_shell_is_said_to_be_still_there(self):
        self.session.read.side_effect = [(0, b"", True), (3, b"end", False)]
        _, _, body = self.request("GET", self.PATH)
        self.assertTrue(body.decode().startswith(": still here\n\n"))
        self.assertEqual(self.session.read.call_args_list[0].args[0], 0)

    def test_a_stream_for_a_container_that_does_not_run_is_an_error_not_a_stream(self):
        self.attached.side_effect = RuntimeError("gio-hello-shell is not running")
        status, headers, body = self.request("GET", self.PATH)
        self.assertEqual((status, headers["Content-Type"]), (409, "application/json"))