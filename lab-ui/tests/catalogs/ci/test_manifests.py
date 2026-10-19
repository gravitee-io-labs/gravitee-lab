"""`catalog.py ci manifests` (5.5, 6.11 MF01 and MF02): every image the touched units pull exists in its registry.

The registries are fakes: a table of recorded answers behind the opener of `.github/scripts/image_manifests.py`, so the real
`exists` of that script runs (the redirects, the anonymous token, the refusal of a registry on the machine's own network) and
no network is touched. The units are given to the stage by the loader seam, and one class reads a real catalog through the
Lab's own project (Compose 5, no daemon; a visible skip without it) to prove that a unit the screen refuses is never given to
Compose."""

import email.message
import functools
import io
import json
import os
import shutil
import socket
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from lab_ui.library import check, project
from lab_ui.docker import compose, envfiles
from lab_ui import paths
from lab_ui.catalogs import ci, manifest, registry
from lab_ui.catalogs.ci import manifests
from lab_ui.catalogs.ci import main as ci_main
from tests import Case
from tests import fixtures
from tests.fakedocker import FakeDocker

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
CONFIG = {"services": {
    "gateway": {"image": "graviteeio/apim-gateway:4.12.0"},
    "console": {"image": "ghcr.io/example/console:1.0"},
    "web": {"image": "example.test/lab/web:1.0", "build": {"context": "/x"}},
    "mock": {"image": "busybox:1.36"},
    "bare": {"build": {"context": "/y"}},
}}


def loaded(kind="addon", name="mock", config=None, tested=(), resolve=None, variables=None):
    return ci.Loaded(kind, name, name, ci.unit_path(kind, name), "", config if config is not None else CONFIG, ("mock",),
                     tuple(tested), None if variables is None else frozenset(variables), resolve)


class Registry:
    """The opener of a registry that answers from a table {url: status}; any other URL is a 404. 'down' is a request that
    fails the way a dead network does, and a token request is answered with a token. An error has a body to close, as the
    ones urllib raises do (an HTTPError with none fails in `close()` on Python 3.9)."""

    def __init__(self, table=None, default=404):
        self.table, self.default, self.asked = dict(table or {}), default, []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.asked.append((request.get_method(), url, dict(request.header_items())))
        if "/token" in url:
            return Response(200, body=json.dumps({"token": "anonymous"}).encode())
        what = self.table.get(url, self.default)
        if what == "down":
            raise urllib.error.URLError("unreachable")
        if what == "timeout":
            raise socket.timeout("timed out")
        if isinstance(what, tuple):   # (401, the challenge)
            headers = email.message.Message()
            headers["WWW-Authenticate"] = what[1]
            raise urllib.error.HTTPError(url, what[0], "no", headers, io.BytesIO(b""))
        if what >= 400 or what in (301, 302):
            raise urllib.error.HTTPError(url, what, "no", email.message.Message(), io.BytesIO(b""))
        return Response(what)


class Response:
    def __init__(self, status, body=b""):
        self.status, self.headers, self._body = status, email.message.Message(), body

    def __enter__(self):
        return self

    def __exit__(self, *rest):
        return False

    def read(self):
        return self._body


class Asking(Case):
    """The script of the engine as a module, with the name resolution and the network replaced, and an `ask` that is its
    `exists` over a fake registry."""

    def setUp(self):
        self.script = manifests.script()
        self.script.RESOLVE = lambda name, port, type=None: PUBLIC if name != "registry.internal.test" else \
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", port))]
        self.registry = Registry()

    def ask(self):
        return functools.partial(self.script.exists, opener=self.registry)


class Images(Asking):

    def test_the_images_it_builds_are_left_out(self):
        found = manifests.images_of(loaded(), self.script.pulled, [])
        self.assertEqual(found, ["busybox:1.36", "ghcr.io/example/console:1.0", "graviteeio/apim-gateway:4.12.0"])
        self.assertNotIn("example.test/lab/web:1.0", found)   # it builds that one, and builds nothing under the name of `bare`

    def test_the_sets_of_versions_it_was_tried_with_are_read_too(self):
        seen = []

        def resolve(variables):
            seen.append(variables)
            return {"services": {"gateway": {"image": "graviteeio/apim-gateway:" + variables["APIM_VERSION"]}}}
        unit = loaded(tested=[{"APIM_VERSION": "4.12.0"}, {"APIM_VERSION": "4.11.3"}], resolve=resolve, variables={"APIM_VERSION"})
        found = manifests.images_of(unit, self.script.pulled, [])
        self.assertEqual(seen, [{"APIM_VERSION": "4.12.0"}, {"APIM_VERSION": "4.11.3"}])
        self.assertEqual(found, ["busybox:1.36", "ghcr.io/example/console:1.0", "graviteeio/apim-gateway:4.11.3",
                                 "graviteeio/apim-gateway:4.12.0"])

    def test_a_set_may_set_only_the_version_or_the_registry_of_an_image(self):
        called = []
        notes = []
        unit = loaded(tested=[{"PATH": "/tmp/evil"}, {"APIM_VERSION": "4.12.0"}, {"NOT_AN_IMAGE_VARIABLE": "x"}],
                      resolve=lambda variables: called.append(variables) or {"services": {}}, variables={"APIM_VERSION"})
        manifests.images_of(unit, self.script.pulled, notes)
        self.assertEqual(called, [{"APIM_VERSION": "4.12.0"}])   # nothing of PATH reaches Compose
        self.assertEqual(len(notes), 2)
        self.assertTrue(all("sets something that is not the version or the registry of an image" in each for each in notes))

    def test_a_set_that_compose_cannot_read_is_said_and_the_rest_goes_on(self):
        def resolve(variables):
            raise compose.ComposeError("service x has neither an image nor a build context")
        notes = []
        found = manifests.images_of(loaded(tested=[{"APIM_VERSION": "4.12.0"}], resolve=resolve, variables={"APIM_VERSION"}),
                                    self.script.pulled, notes)
        self.assertIn("graviteeio/apim-gateway:4.12.0", found)
        self.assertEqual(notes, ["addon:mock: set 0 of x-gravitee.tested could not be read: service x has neither an image nor "
                                 "a build context"])

    def test_sets_that_a_loader_cannot_resolve_are_said(self):
        notes = []
        manifests.images_of(loaded(tested=[{"APIM_VERSION": "4.12.0"}]), self.script.pulled, notes)
        self.assertEqual(notes, ["addon:mock: the sets of versions it was tried with were not read"])

    def test_a_loader_failure_that_is_no_compose_error_is_not_hidden(self):
        def resolve(variables):
            raise KeyError("a fault of the loader")
        with self.assertRaises(KeyError):
            manifests.images_of(loaded(tested=[{"APIM_VERSION": "4.12.0"}], resolve=resolve, variables={"APIM_VERSION"}),
                                self.script.pulled, [])


class Answers(Asking):
    """What the registry says decides the finding, and only an answered 'no such tag' is an error."""

    def stage(self, table, default=404, images=("graviteeio/apim-gateway:9.9.9",)):
        self.registry = Registry(table, default)
        unit = loaded(config={"services": {f"s{n}": {"image": image} for n, image in enumerate(images)}})
        return manifests.run([unit], self.ask(), module=self.script)

    def test_an_answered_no_such_tag_is_mf01(self):
        problems, results, _ = self.stage({})
        self.assertEqual(results, {"graviteeio/apim-gateway:9.9.9": False})
        self.assertEqual([(each.code, each.severity, each.path, each.line) for each in problems],
                         [("MF01", "error", "addons/mock/compose.yml", 0)])
        self.assertEqual(problems[0].message, "graviteeio/apim-gateway:9.9.9: the registry has no tag 9.9.9. Fix: use a tag "
                                              "that exists, or an earlier version.")

    def test_a_tag_that_exists_is_fine(self):
        url = "https://registry-1.docker.io/v2/graviteeio/apim-gateway/manifests/4.12.0"
        problems, results, _ = self.stage({url: 200}, images=("graviteeio/apim-gateway:4.12.0",))
        self.assertEqual((problems, results), ([], {"graviteeio/apim-gateway:4.12.0": True}))

    def test_a_registry_that_asks_for_an_anonymous_token_is_asked_again_with_it(self):
        url = "https://ghcr.io/v2/example/console/manifests/1.0"
        challenge = 'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:example/console:pull"'
        seen = {url: (401, challenge)}
        registry = self.registry = Registry(seen)
        original = registry.__call__

        def opener(request, timeout=None):
            if "Authorization" in dict(request.header_items()) or "authorization" in dict(request.header_items()):
                return Response(200)
            return original(request, timeout)
        problems, results, _ = manifests.run([loaded(config={"services": {"c": {"image": "ghcr.io/example/console:1.0"}}})],
                                             functools.partial(self.script.exists, opener=opener), module=self.script)
        self.assertEqual((problems, results), ([], {"ghcr.io/example/console:1.0": True}))

    def test_a_registry_that_does_not_answer_is_mf02_and_no_failure(self):
        for what in ("down", "timeout", 500, 502, 503, 504, 429, 403, 401, 400, 301, 302):
            with self.subTest(answer=what):
                url = "https://registry-1.docker.io/v2/graviteeio/apim-gateway/manifests/9.9.9"
                problems, results, _ = self.stage({url: what})
                self.assertEqual(results, {"graviteeio/apim-gateway:9.9.9": None})
                self.assertEqual([(each.code, each.severity) for each in problems], [("MF02", "warning")])
                self.assertEqual(problems[0].message, "registry-1.docker.io did not answer: graviteeio/apim-gateway:9.9.9 was "
                                                      "not checked.")

    def test_a_registry_on_the_machines_own_network_is_not_asked_at_all(self):
        for image in ("registry.internal.test/team/app:1", "localhost:5000/app:1", "10.0.0.5/app:1", "127.0.0.1:5000/app:1",
                      "registry.local/app:1"):
            with self.subTest(image=image):
                problems, results, _ = self.stage({}, images=(image,))
                self.assertEqual(results, {image: None})
                self.assertEqual([each.code for each in problems], ["MF02"])
        self.assertEqual(self.registry.asked, [])

    def test_a_request_that_fails_in_a_way_the_script_does_not_expect_did_not_say(self):
        def ask(image):
            raise ValueError("a registry that answers strangely")
        problems, results, _ = manifests.run([loaded(config={"services": {"c": {"image": "busybox:1"}}})], ask, module=self.script)
        self.assertEqual((results, [each.code for each in problems]), ({"busybox:1": None}, ["MF02"]))

    def test_each_image_is_asked_once_whoever_pulls_it(self):
        self.registry = Registry({"https://registry-1.docker.io/v2/library/busybox/manifests/1.36": 200})
        one, two = loaded("addon", "one"), loaded("playground", "two")
        problems, results, _ = manifests.run([one, two], self.ask(), module=self.script)
        self.assertEqual(problems[:0], [])
        busybox = [url for _, url, _ in self.registry.asked if "library/busybox" in url]
        self.assertEqual(len(busybox), 1)

    def test_a_missing_tag_is_said_at_the_first_unit_that_pulls_it_only(self):
        config = {"services": {"s": {"image": "graviteeio/apim-gateway:9.9.9"}}}
        problems, _, _ = manifests.run([loaded("addon", "one", config), loaded("playground", "two", config)], self.ask(),
                                       module=self.script)
        self.assertEqual([(each.code, each.path) for each in problems], [("MF01", "addons/one/compose.yml")])

    def test_a_digest_is_asked_for_as_it_is(self):
        digest = "sha256:" + "ab" * 32
        image = f"busybox:1.36@{digest}"
        self.registry = Registry({f"https://registry-1.docker.io/v2/library/busybox/manifests/{digest}": 404})
        problems, _, _ = manifests.run([loaded(config={"services": {"b": {"image": image}}})], self.ask(), module=self.script)
        self.assertEqual(problems[0].message, f"{image}: the registry has no tag {digest}. Fix: use a tag that exists, or an "
                                              "earlier version.")

    def test_an_image_name_that_is_not_a_name_is_not_asked_and_is_said(self):
        notes = []
        names = ["--output=x", "a b", "x\ny", "ghcr.io/x/{tag}:{image}", "a" * 300, "::error::x", "", "-x"]
        config = {"services": {**{f"s{n}": {"image": image} for n, image in enumerate(names)}, "ok": {"image": "busybox:1.36"}}}
        found = manifests.images_of(loaded(config=config), self.script.pulled, notes)
        self.assertEqual(found, ["busybox:1.36"])
        self.assertEqual(len(notes), len(set(names) - {""}))   # (an empty image is no image: Docker would pull none)
        self.assertTrue(all(note.startswith("addon:mock: an image name that is not a name was not asked: ") for note in notes))
        self.assertFalse(any("\n" in note for note in notes))
        self.assertEqual(self.registry.asked, [])


class Stage(Asking):
    """`ci manifests` as the workflow calls it, with the loader replaced."""

    def setUp(self):
        super().setUp()
        self.fake = FakeDocker(self)
        self.out, self.err = io.StringIO(), io.StringIO()
        self.patch(compose, "run", mock.Mock(side_effect=AssertionError("a command was run")))
        self.units = [loaded(config={"services": {"g": {"image": "graviteeio/apim-gateway:9.9.9"},
                                                  "b": {"image": "busybox:1.36"}}})]
        self.refused = []

    def run_stage(self, *argv, units='["addon:mock"]'):
        loader = mock.Mock(return_value=ci.Loading(self.units, self.refused, []))
        with mock.patch.object(ci, "LOADER", loader):
            code = manifests.main([".", "--units", units, *argv], self.out, self.err, ask=self.ask(), module=self.script)
        return code, loader

    def test_a_missing_tag_ends_the_stage_with_1(self):
        code, loader = self.run_stage("--format", "github")
        self.assertEqual(code, 1)
        self.assertIn("::error file=addons/mock/compose.yml,title=MF01::graviteeio/apim-gateway:9.9.9: the registry has no tag "
                      "9.9.9. Fix: use a tag that exists, or an earlier version.", self.out.getvalue())
        self.assertEqual(loader.call_args[0][1], [("addon", "mock")])

    def test_a_registry_that_did_not_answer_is_a_warning_and_exit_0(self):
        self.registry = Registry(default="down")
        code, _ = self.run_stage("--format", "github")
        self.assertEqual(code, 0)
        self.assertIn("::warning file=addons/mock/compose.yml,title=MF02::registry-1.docker.io did not answer", self.out.getvalue())

    def test_nothing_missing_is_all_good(self):
        self.registry = Registry(default=200)
        code, _ = self.run_stage()
        self.assertEqual(code, 0)
        self.assertIn("✓ busybox:1.36", self.out.getvalue())
        self.assertTrue(self.out.getvalue().endswith("All good\n"))

    def test_a_unit_the_lab_refused_is_a_finding_and_no_registry_is_asked_for_it(self):
        self.units = []
        self.refused = [ci.Refused("addon", "mock", "addons/mock/compose.yml", ("Runs with full privileges",), "T03")]
        code, _ = self.run_stage("--format", "github")
        self.assertEqual(code, 1)
        self.assertIn("title=T03::addon:mock was not read: Runs with full privileges. Fix: make check says how.", self.out.getvalue())
        self.assertEqual(self.registry.asked, [])

    def test_the_summary_is_written_with_ci(self):
        import tempfile
        folder = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        summary = os.path.join(folder, "summary.md")
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary}):
            code, _ = self.run_stage("--ci")
        self.assertEqual(code, 1)
        with open(summary, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("MF01", text)
        self.assertIn("make ci", text)

    def test_a_usage_that_is_wrong_is_exit_2_and_asks_nothing(self):
        for units in ("not json", '"addon:x"', '["addon:x", 3]', '["Addon:x"]', '["addon:A"]', '["addon:x;rm"]',
                      '["addon:' + "a" * 200 + '"]', json.dumps(["addon:a"] * 2000), '["addon:a\\n::error::x"]'):
            with self.subTest(units=units[:40]):
                self.out, self.err = io.StringIO(), io.StringIO()
                code, loader = self.run_stage(units=units)
                self.assertEqual(code, 2)
                loader.assert_not_called()
                self.assertEqual(self.registry.asked, [])
                self.assertEqual([line for line in self.err.getvalue().splitlines() if line.startswith("::")], [])

    def test_a_catalog_the_lab_cannot_read_is_exit_2(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        with mock.patch.object(ci, "LOADER", mock.Mock(side_effect=ci.Unavailable("the Lab does not have this folder"))):
            code = manifests.main([folder, "--units", '["addon:x"]'], self.out, self.err)
        self.assertEqual(code, 2)
        self.assertIn("catalog.py ci: the Lab does not have this folder", self.err.getvalue())

    def test_no_unit_to_read_is_nothing_to_read_and_the_loader_is_not_asked(self):
        loader = mock.Mock(side_effect=ci.Unavailable("the Lab does not have this folder"))
        with mock.patch.object(ci, "LOADER", loader):
            code = manifests.main([".", "--units", "[]"], self.out, self.err)
        self.assertEqual((code, self.err.getvalue()), (0, ""))
        loader.assert_not_called()

    def test_the_dispatcher_reaches_it(self):
        with mock.patch.object(ci, "LOADER", mock.Mock(return_value=ci.Loading([], [], []))):
            code = ci_main(["manifests", ".", "--units", "[]"], self.out, self.err)
        self.assertEqual(code, 0, self.err.getvalue())

    def test_it_holds_no_secret(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_" + "A" * 36, "ACTIONS_RUNTIME_TOKEN": "runtime-secret"}):
            self.run_stage()
        seen = json.dumps(self.registry.asked)
        self.assertNotIn("ghp_", seen)
        self.assertNotIn("runtime-secret", seen)
        self.assertEqual({method for method, _, _ in self.registry.asked}, {"HEAD"})


class Script(unittest.TestCase):
    """The engine's script, as a module and as a command."""

    def test_it_is_a_module_with_the_names_the_stage_uses(self):
        module = manifests.script()
        for name in ("reference", "exists", "check", "pulled", "images", "main"):
            self.assertTrue(callable(getattr(module, name)), name)
        self.assertEqual(module.reference("ghcr.io/a/b:1"), ("ghcr.io", "a/b", "1"))
        self.assertEqual(module.pulled({"a": {"image": "x:1"}, "b": {"image": "y:1", "build": {"context": "."}}, "c": {"build": "."},
                                        "d": "not a service", "e": {"image": 3}}), ["x:1"])

    def test_importing_it_runs_nothing_and_exits_nothing(self):
        # The command line is the same: with no argument it still refuses (exit text), but only when it is run as main()
        module = manifests.script()
        with self.assertRaises(SystemExit) as raised:
            module.main([])
        self.assertIn("image_manifests.py ITEM", str(raised.exception))


class Hostile(Case):
    """A catalog read through the Lab's own project: a unit the screen refuses is never given to Compose, and the units that are
    fine are read with the scrubbed environment."""

    HOSTILE = {
        # (a setting of the catalog's own, HOSTILE_*, is the one a unit may read: T20)
        "addons/leak/compose.yml": "services:\n  leak:\n    image: example.test/lab/leak:${HOSTILE_TAG:-none}\n"
                                   "    environment:\n      T: ${HOSTILE_TOKEN:-unset}\n",
        "addons/remote/compose.yml": "include:\n  - https://example.test/evil/compose.yml\nservices:\n  r:\n    image: busybox\n",
        "addons/absolute/compose.yml": "include:\n  - /etc/hosts\nservices:\n  r:\n    image: busybox\n",
        "addons/env/compose.yml": "services:\n  r:\n    image: busybox\n    env_file: /dev/zero\n",
        "addons/zero/compose.yml": "include:\n  - ../env/compose.yml\nservices:\n  z:\n    image: busybox\n",
        "addons/privileged/compose.yml": "services:\n  p:\n    image: busybox\n    privileged: true\n",
        # a unit that is fine, with a file in it whose name holds a line break and a workflow command, and one at the top
        "addons/named/compose.yml": "services:\n  n:\n    image: busybox\n",
        "addons/named/read\n::error::me.txt": "x\n",
        "notes\n::error::top.md": "x\n",
        # a unit that is fine but for a link in its folder (made in setUp: the fixture writes text), and one that includes the unit
        # with the odd file name
        "addons/linked/compose.yml": "services:\n  l:\n    image: busybox\n",
        "addons/pointer/compose.yml": "include:\n  - ../named/compose.yml\nservices:\n  p:\n    image: busybox\n",
        # a service that Compose prints only when a profile is on, and that asks for everything
        "addons/gated/compose.yml": "services:\n  shown:\n    image: busybox\n  evil:\n    profiles: [debug]\n    image: example.test/lab/evil:1\n"
                                    "    privileged: true\n    pid: host\n    build:\n      context: .\n      dockerfile_inline: |\n"
                                    "        FROM busybox\n",
    }

    def setUp(self):
        fixtures.need_compose()   # (a skip that shows without Compose 5)
        fixtures.project(self, paths, check, project, compose, envfiles, ns="hostile", link=True, files=self.HOSTILE)
        fixtures.install(self, "partner")   # (the catalog it includes from has to be here, as it has for the check)
        self.folder = next(one["path"] for one in __import__("lab_ui.catalogs.registry", fromlist=["x"]).entries()
                           if one["id"] == "hostile")
        os.symlink("/etc", os.path.join(self.folder, "addons", "linked", "data"))   # (a link out of the folder, inside a unit)
        self.calls = []
        real = compose.run

        def recording(cmd, *args, **kwargs):
            self.calls.append((list(cmd), dict(kwargs)))
            return real(cmd, *args, **kwargs)
        self.patch(compose, "run", recording)
        project.forget()

    def test_a_unit_the_screen_or_the_policy_refuses_is_refused_here_and_no_compose_call_names_it(self):
        names = [("addon", name) for name in ("remote", "absolute", "env", "zero", "privileged", "leak")]
        project.load()
        before = len(self.calls)
        loading = ci.lab_loader(self.folder, names)
        refused = {each.name: each for each in loading.refused}
        self.assertEqual(sorted(refused), ["absolute", "env", "privileged", "remote", "zero"])
        self.assertEqual([each.name for each in loading.units], ["leak"])
        self.assertEqual(refused["remote"].reasons, ("Includes content from outside the catalog",))
        self.assertEqual(refused["remote"].code, "T19")
        every = " ".join(" ".join(cmd) for cmd, _ in self.calls[before:])
        for name in ("remote", "absolute", "env", "zero"):   # (the screen refuses these: no call names them. `privileged` passes the
            self.assertNotIn(f"hostile/addons/{name}/", every)   # screen and is refused by the policy, so the project read it)

    def test_the_resolved_json_is_read_with_the_scrubbed_environment(self):
        with mock.patch.dict(os.environ, {"HOSTILE_TAG": "from-the-shell", "HOSTILE_TOKEN": "ghp_" + "B" * 36}):
            loading = ci.lab_loader(self.folder, [("addon", "leak")])
        self.assertEqual([each.name for each in loading.units], ["leak"], loading.refused)
        service = loading.units[0].config["services"]["leak"]
        self.assertEqual(service["image"], "example.test/lab/leak:none")   # nothing of the shell reached what is built
        self.assertEqual(service["environment"]["T"], "unset")
        self.assertNotIn("ghp_", json.dumps(loading.units[0].config))

    def reads(self, calls):
        """Everything these Compose calls were told to read: their words, and the text of each file they were given with -f (the
        generated files that include the units name them)."""
        seen = []
        for cmd, _ in calls:
            seen.append(" ".join(cmd))
            for at, word in enumerate(cmd[:-1]):
                if word == "-f" and os.path.isfile(cmd[at + 1]):
                    seen.append(Path(cmd[at + 1]).read_text(encoding="utf-8", errors="replace"))
        return "\n".join(seen)

    def test_a_unit_that_holds_a_name_the_lab_cannot_install_or_a_link_is_refused_before_anything_is_read(self):
        loading = ci.load(self.folder, [("addon", "named"), ("addon", "linked"), ("addon", "leak")])
        self.assertEqual([each.name for each in loading.units], ["leak"])
        refused = {each.name: each for each in loading.refused}
        self.assertEqual(sorted(refused), ["linked", "named"])
        self.assertEqual((refused["named"].code, refused["named"].reasons),
                         ("ST03", ("addons/named/read\n::error::me.txt has a name the Lab cannot install",)))
        self.assertEqual((refused["linked"].code, refused["linked"].reasons), ("ST01", ("addons/linked/data is a symbolic link",)))
        problems = ci.refusals(loading.refused, "read")
        self.assertIn("addon:named was not read: addons/named/read ::error::me.txt has a name the Lab cannot install. "
                      "Fix: make check says how.", [each.message for each in problems])

    def test_no_compose_call_names_a_unit_that_holds_such_a_file_whether_it_was_asked_for_or_not(self):
        project.load()
        before = len(self.calls)
        ci.load(self.folder, [("addon", "named"), ("addon", "linked"), ("addon", "leak")])
        every = self.reads(self.calls[before:])
        self.assertIn("addons/leak/", every)   # (the others are read: the proof that this is a look at the calls)
        for name in ("named", "linked"):
            self.assertNotIn(f"addons/{name}", every)
        before = len(self.calls)
        ci.load(self.folder, [("addon", "leak")])   # (nobody asked for them: the project does not read them either)
        every = self.reads(self.calls[before:])
        self.assertIn("addons/leak/", every)
        for name in ("named", "linked"):
            self.assertNotIn(f"addons/{name}", every)

    def test_with_no_unit_to_read_nothing_is_read_at_all(self):
        for names in ([], [("addon", "named")], [("addon", "named"), ("addon", "linked")]):
            with self.subTest(names=names):
                before = len(self.calls)
                loading = ci.load(self.folder, names)
                self.assertEqual((loading.units, len(loading.refused)), ([], len(names)))
                self.assertEqual(self.calls[before:], [])

    def test_a_unit_that_includes_one_that_holds_such_a_file_is_held_and_the_file_is_not_read(self):
        project.load()
        before = len(self.calls)
        loading = ci.load(self.folder, [("addon", "pointer"), ("addon", "leak")])
        self.assertEqual([each.name for each in loading.units], ["leak"])
        self.assertEqual([each.name for each in loading.refused], ["pointer"])
        self.assertNotIn("addons/named", self.reads(self.calls[before:]))

    def test_the_lab_is_put_back_as_it_was_after_a_unit_was_left_out_of_the_reading(self):
        names = paths.names
        ci.load(self.folder, [("addon", "leak")])
        self.assertIs(paths.names, names)
        with mock.patch.object(ci, "lab_loader", mock.Mock(side_effect=RuntimeError("boom"))):
            with self.assertRaises(RuntimeError):
                ci.load(self.folder, [("addon", "leak")])
        self.assertIs(paths.names, names)
        with ci.hidden_units("hostile", [("addon", "leak")]):
            self.assertEqual(paths.names("hostile", "addon").count("leak"), 0)
            self.assertIn("named", paths.names("hostile", "addon"))   # (only what was named, and only in that catalog)
            self.assertEqual(paths.names("partner", "addon"), names("partner", "addon"))
        self.assertIn("leak", paths.names("hostile", "addon"))

    def test_a_service_that_only_a_profile_turns_on_is_judged_and_a_unit_that_has_one_is_refused(self):
        loading = ci.load(self.folder, [("addon", "gated"), ("addon", "leak")])
        self.assertEqual([each.name for each in loading.units], ["leak"])
        refused = loading.refused[0]
        self.assertEqual((refused.name, refused.code), ("gated", "T01"))
        self.assertEqual(refused.reasons, ("Uses a Compose feature the Lab does not check: profiles (evil)",))
        problems = ci.refusals(loading.refused, "read")
        self.assertEqual(problems[0].message, "addon:gated was not read: Uses a Compose feature the Lab does not check: profiles (evil). "
                                              "Fix: Remove `profiles`, or ask the Lab's maintainers to support it.")

    def test_the_premise_of_that_compose_prints_the_service_only_with_a_profile_on(self):
        file = ".lab/catalogs/hostile/addons/gated/compose.yml"
        scrubbed = compose.scrubbed_environment()
        plain = compose.config_json(compose.all_files([file]), timeout=60, environ=scrubbed)
        every = compose.config_json(compose.all_files([file]), timeout=60, environ={**scrubbed, **ci.ALL_PROFILES})
        self.assertNotIn("evil", plain["services"])
        self.assertEqual(sorted(every["services"]["evil"]["profiles"]), ["debug"])
        self.assertEqual(ci.gated(plain), [])
        self.assertEqual(ci.gated(every), ["evil"])

    def test_the_core_of_the_lab_has_no_profile_so_that_a_service_with_one_is_always_a_units(self):
        core = (Path(__file__).resolve().parents[4] / paths.CORE_FILE).read_text(encoding="utf-8")
        self.assertFalse([line for line in core.splitlines() if line.strip().startswith("profiles:")])

    def test_a_service_with_a_profile_is_a_refusal_for_every_stage_and_nothing_of_it_is_built_or_asked(self):
        out, err = io.StringIO(), io.StringIO()
        asked = []
        code = manifests.main([self.folder, "--units", '["addon:gated"]', "--format", "github"], out, err, ask=lambda image: asked.append(image))
        self.assertEqual((code, asked), (1, []))
        self.assertIn("title=T01::addon:gated was not read: Uses a Compose feature the Lab does not check: profiles (evil).", out.getvalue())
        fake = FakeDocker(self)
        from lab_ui.catalogs.ci import build
        out = io.StringIO()
        code = build.main([self.folder, "--units", '["addon:gated"]', "--platform", "linux/amd64"], out, err)
        self.assertEqual(code, 1, err.getvalue())
        self.assertEqual(fake.commands("buildx"), [])
        self.assertIn("profiles (evil)", out.getvalue())

    def test_the_index_of_the_same_catalog_is_made_from_the_one_reading_and_asks_compose_for_no_unit(self):
        project.load()
        before = len(self.calls)
        index = manifest.build_index("hostile")
        self.assertEqual([" ".join(cmd) for cmd, _ in self.calls[before:] if "/addons/" in " ".join(cmd)], [])   # (catalog.yml only)
        self.assertIn("leak", {each["id"] for each in index["units"]})

    def test_the_stage_answers_for_the_refused_units_with_exit_1_and_asks_no_registry(self):
        out, err = io.StringIO(), io.StringIO()
        asked = []
        code = manifests.main([self.folder, "--units", '["addon:privileged", "addon:remote"]', "--format", "github"], out, err,
                              ask=lambda image: asked.append(image))
        self.assertEqual(code, 1, err.getvalue())
        self.assertEqual(asked, [])
        self.assertIn("addon:remote was not read: Includes content from outside the catalog. Fix: make check says how.", out.getvalue())
        self.assertIn("addon:privileged was not read: ", out.getvalue())

    def test_a_folder_the_lab_never_heard_of_is_read_where_it_is_and_nothing_is_linked(self):
        elsewhere = self.folder + "-elsewhere"
        shutil.copytree(self.folder, elsewhere, symlinks=True)
        self.addCleanup(shutil.rmtree, elsewhere, True)
        before = (registry.entries(), sorted(os.listdir(Path(paths.ROOT) / paths.CATALOGS_DIR)))
        loading = ci.lab_loader(elsewhere, [("addon", "leak")])
        self.assertEqual([each.name for each in loading.units], ["leak"])
        self.assertEqual((registry.entries(), sorted(os.listdir(Path(paths.ROOT) / paths.CATALOGS_DIR))), before)
        self.assertTrue(os.path.realpath(loading.units[0].folder).startswith(os.path.realpath(elsewhere)))   # the real folder, not a link

    def test_a_folder_with_no_usable_id_or_a_catalog_it_needs_that_is_not_here_is_exit_2_with_what_to_do(self):
        elsewhere = self.folder + "-bad"
        os.makedirs(elsewhere)
        self.addCleanup(shutil.rmtree, elsewhere, True)
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(manifests.main([elsewhere, "--units", '["addon:x"]'], out, err), 2)
        self.assertIn("catalog.yml gives no id this machine can use", err.getvalue())
        Path(elsewhere, "catalog.yml").write_text("x-gravitee-catalog:\n  schema: 1\n  id: lonely\n  name: Lonely\n  version: 1.0.0\n  lab: 1\n"
                                                  "  license: MIT\n  requires:\n    catalogs:\n      absent: {name: Absent, "
                                                  "url: \"https://example.invalid/a.git\", min: 1.0.0}\n")
        err = io.StringIO()
        self.assertEqual(manifests.main([elsewhere, "--units", '["addon:x"]'], out, err), 2)
        self.assertIn("lonely includes from absent, which is neither installed nor linked here", err.getvalue())


if __name__ == "__main__":
    unittest.main()
