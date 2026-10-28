"""e2e: the run of an item on a machine with no lab: how it is named (any id of any catalog), the versions it runs on, the machine
it needs, what it checks once started, and how it says it. No Docker, no server: the Lab UI's answers, the time and the shell are
given. Then the plan CI makes before it tries anything: which items of a catalog a change touches, and the jobs of each."""

import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.docker import compose, envfiles
from lab_ui import e2e, paths
from lab_ui.library import project
from lab_ui.catalogs import gitx, manifest, registry

from . import Case, fixtures, gitfixture

JOB = {"id": "abcd1234", "done": True, "ok": True, "error": None}
UP = {"key": "healthy", "label": "Healthy", "running": True}


def lab_state(services, links=(), job=None, action=None, kind="playground", uid="shop", name="Shop"):
    """What /api/state says of one item and its containers."""
    unit = {"id": uid, "name": name, "services": list(services), "action": action or {"do": "start", "enabled": True, "dialog": {}}}
    return {f"{kind}s": [unit], "services": {name: {"state": state, "container": f"gio-{name}"} for name, state in services.items()},
            "links": list(links), "job": job}


class FakeLab:
    """The Lab UI of the tests: its state is what `states` gives, the next one at each reading."""

    def __init__(self, *states, refuse=None):
        self.states, self.refuse, self.calls, self.reads = list(states), refuse, [], 0

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET":
            self.reads += 1
            return self.states[min(self.reads - 1, len(self.states) - 1)]
        if self.refuse:
            raise RuntimeError(self.refuse)
        return {"id": JOB["id"]}


class Clock:
    """Time that only moves when the run sleeps."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def attempt(lab, **more):
    clock = Clock()
    more.setdefault("get", lambda url: 200)
    more.setdefault("shell", lambda command, timeout=0: (0, "", ""))
    return e2e.Run(lab, "playground", "shop", clock=clock, sleep=clock.sleep, **more)


class Naming(Case):

    BUILT = {"playgrounds": {"shop": {}}, "addons": {"mailer": {}}, "setups": {}}

    def test_an_item_is_named_by_its_kind_and_its_id(self):
        self.assertEqual(e2e.target("playground:shop", self.BUILT), ("playground", "shop"))
        self.assertEqual(e2e.target("addon:mailer", self.BUILT), ("addon", "mailer"))

    def test_what_is_not_an_item_of_the_project_is_said(self):
        for text in (None, "", "shop", "playground:", "plugin:x", ":shop"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "Name the item as kind:id"):
                e2e.target(text, self.BUILT)
        with self.assertRaisesRegex(ValueError, "No setup named events"):
            e2e.target("setup:events", self.BUILT)

    def test_what_belongs_to_this_machine_is_never_run(self):
        with self.assertRaisesRegex(ValueError, "belongs to this machine"):
            e2e.target("playground:local/shop", {"playgrounds": {"local/shop": {}}, "addons": {}, "setups": {}})


class AnyId(Case):
    """An item is named with the id the Lab gives it, whatever its catalog: bare for the first, @catalog/name for the others."""

    BUILT = {"playgrounds": {"shop": {"catalog": "base"}, "@hub/bank": {"catalog": "hub"}, "local/mine": {"catalog": "local"}},
             "addons": {"@hub/ledger": {"catalog": "hub"}, "echo": {"catalog": "base"}}, "setups": {"pair": {"catalog": "base"}}}

    def setUp(self):
        self.patch(paths, "default_ns", return_value="base")

    def test_an_item_of_another_catalog_is_named_with_its_catalog(self):
        self.assertEqual(e2e.target("playground:@hub/bank", self.BUILT), ("playground", "@hub/bank"))
        self.assertEqual(e2e.target("addon:@hub/ledger", self.BUILT), ("addon", "@hub/ledger"))

    def test_an_item_of_the_first_catalog_may_be_written_with_its_catalog_too_and_is_the_bare_one(self):
        self.assertEqual(e2e.target("playground:@base/shop", self.BUILT), ("playground", "shop"))

    def test_an_item_of_a_catalog_that_has_no_such_item_is_said(self):
        with self.assertRaisesRegex(ValueError, "No playground named @hub/shop"):
            e2e.target("playground:@hub/shop", self.BUILT)
        with self.assertRaisesRegex(ValueError, "No add-on named @other/ledger"):
            e2e.target("addon:@other/ledger", self.BUILT)

    def test_what_belongs_to_this_machine_is_refused_for_what_it_is(self):
        with self.assertRaisesRegex(ValueError, "playground:local/mine belongs to this machine"):
            e2e.target("playground:local/mine", self.BUILT)

    def test_a_name_that_is_no_id_is_refused_by_its_form_and_never_repeated(self):
        hostile = ["playground:a:b", "playground:../shop", "playground:@a/x", "playground:@local/x", "playground:@hub/",
                   "playground:@hub/x/y", "playground:Shop", "playground:sho p", "playground:shop\n::error::x", "playground:",
                   "PLAYGROUND:shop", "playground:shop\0", "playground:" + "a" * 300, "::error::x", "playground:@/x",
                   "playground:local/../shop", "playground:shop\n", "playground: shop", "playground:shop ", "playground:\tshop",
                   "playground:shop\r"]
        for text in hostile:
            with self.subTest(text=text[:30]), self.assertRaises(ValueError) as caught:
                e2e.target(text, self.BUILT)
            said = str(caught.exception)
            self.assertTrue(said.startswith("Name the item as kind:id, kind being addon, setup, playground"), said)
            self.assertNotIn("error", said, "what was typed is not said back")

    def test_the_example_of_the_message_is_an_item_of_the_project(self):
        with self.assertRaises(ValueError) as caught:
            e2e.target("shop", self.BUILT)
        self.assertEqual(str(caught.exception),
                         "Name the item as kind:id, kind being addon, setup, playground, for example playground:@hub/bank")

    def test_the_example_leaves_out_what_belongs_to_a_machine_and_a_project_with_nothing_shared_has_none(self):
        self.assertEqual(e2e.example({"playgrounds": {"local/a": {}}, "addons": {}, "setups": {"pair": {}}}), "setup:pair")
        self.assertIsNone(e2e.example({"playgrounds": {"local/a": {}}, "addons": {}, "setups": {}}))
        self.assertIsNone(e2e.example(None))
        self.assertEqual(e2e.example(self.BUILT, "hub"), "playground:@hub/bank")
        with self.assertRaises(ValueError) as caught:
            e2e.parse("nonsense")
        self.assertEqual(str(caught.exception), "Name the item as kind:id, kind being addon, setup, playground")

    def test_the_form_alone_is_judged_by_parse(self):
        self.assertEqual(e2e.parse("addon:@hub/ghost"), ("addon", "@hub/ghost"))
        self.assertEqual(e2e.parse("addon:local/mine"), ("addon", "local/mine"))
        self.assertEqual(e2e.parse("setup:pair"), ("setup", "pair"))


class Versions(Case):

    UNIT = {"name": "Shop", "tested": [{"APIM_VERSION": "4.13.0-alpha.3"}, {"APIM_VERSION": 4.14, "AM_VERSION": "4.14.0"}]}

    def test_the_core_runs_on_its_defaults_without_a_set(self):
        self.assertEqual(e2e.versions(self.UNIT, None), {})

    def test_a_set_gives_the_variables_of_the_process_as_text(self):
        self.assertEqual(e2e.versions(self.UNIT, 0), {"APIM_VERSION": "4.13.0-alpha.3"})
        self.assertEqual(e2e.versions(self.UNIT, 1), {"APIM_VERSION": "4.14", "AM_VERSION": "4.14.0"})

    def test_a_set_sets_the_version_or_the_registry_of_an_image_and_nothing_of_the_process(self):
        known = {"APIM_VERSION", "AM_VERSION", "APIM_REGISTRY"}
        self.assertEqual(e2e.versions(self.UNIT, 1, known), {"APIM_VERSION": "4.14", "AM_VERSION": "4.14.0"})
        for variable in ("PATH", "HOME", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "DOCKER_HOST", "DOCKER_CONFIG", "COMPOSE_FILE",
                         "GIT_SSH_COMMAND", "PYTHONPATH", "NODE_OPTIONS", "GITHUB_TOKEN", "ACTIONS_RUNTIME_TOKEN", "RUNNER_TEMP",
                         "GRAVITEE_LICENSE", "BASH_ENV", "LC_ALL", "SSH_AUTH_SOCK", "BUILDKIT_HOST", "lowercase", "with space",
                         "A;B", "1A", ""):
            with self.subTest(variable=variable), self.assertRaisesRegex(ValueError, "not the version or the registry of an image"):
                e2e.versions({"name": "Shop", "tested": [{variable: "x"}]}, 0, None if variable.isupper() else known | {variable})

    def test_a_set_given_is_tried_as_it_is_with_the_rules_of_a_set_of_the_catalog(self):
        known = {"APIM_VERSION", "APIM_REGISTRY"}
        self.assertEqual(e2e.versions(self.UNIT, None, known, {"APIM_VERSION": "4.12.25", "APIM_REGISTRY": "graviteeio"}),
                         {"APIM_VERSION": "4.12.25", "APIM_REGISTRY": "graviteeio"}, "whatever the playground says")
        for given in ({"PATH": "/tmp"}, {"SHOP_VERSION": "1"}, {}, [], {"APIM_VERSION": 4}):
            with self.subTest(given=given), self.assertRaises(ValueError):
                e2e.versions(self.UNIT, None, known, given)

    def test_a_start_that_would_switch_versions_or_needs_a_registry_login_is_not_tried(self):
        for reason in ("Would also change API Management 4.12.25 → 4.13.1", "Not logged in to Azure"):
            with self.subTest(reason=reason):
                self.assertTrue(e2e.UNABLE.search(reason))

    def test_a_variable_the_tools_read_is_not_a_version_even_when_an_image_is_written_with_it(self):
        """The catalog that writes the sets writes the images too: `known` is no barrier, the form of a setting is."""
        read = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR", "KUBECONFIG", "BUILDX_CONFIG", "BUILDX_BUILDER",
                "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "JAVA_TOOL_OPTIONS", "GOFLAGS", "GOPROXY", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
                "OPENSSL_CONF", "MAKEFLAGS", "PS4", "SHELLOPTS", "PROMPT_COMMAND", "KUBE_CONFIG_PATH")
        for variable in read:
            for known in (set(read), None):
                with self.subTest(variable=variable, known=known is not None), \
                        self.assertRaisesRegex(ValueError, "not the version or the registry of an image"):
                    e2e.versions({"name": "Shop", "tested": [{variable: "x"}]}, 0, known)

    def test_a_setting_says_the_version_the_tag_the_registry_the_image_the_repository_or_the_prefix_of_an_image(self):
        for variable in ("APIM_VERSION", "AM_REGISTRY", "KAFKA_DESK_TAG", "NODE_IMAGE", "SEEDER_REPOSITORY", "ACME_PREFIX", "A1_B2_VERSION"):
            with self.subTest(variable=variable):
                self.assertEqual(e2e.versions({"name": "Shop", "tested": [{variable: "1"}]}, 0, {variable}), {variable: "1"})
                self.assertEqual(e2e.versions({"name": "Shop", "tested": [{variable: "1"}]}, 0), {variable: "1"}, "with no list of images")
        for variable in ("VERSION", "_VERSION", "APIM_VERSIONS", "APIM_VERSION_X", "APIM_VERSION_", "APIM", "APIM_REGISTRY2", "APIM__VERSION"):
            with self.subTest(refused=variable), self.assertRaisesRegex(ValueError, "not the version or the registry of an image"):
                e2e.versions({"name": "Shop", "tested": [{variable: "1"}]}, 0, {variable})

    def test_a_name_with_that_form_that_the_process_or_the_tools_read_is_refused_too(self):
        for variable in ("GITHUB_REPOSITORY", "DOCKER_API_VERSION", "GIT_VERSION", "RUNNER_IMAGE", "BUILDX_VERSION", "COMPOSE_VERSION",
                         "ACTIONS_RUNTIME_TOKEN_PREFIX", "SSH_REGISTRY", "LD_PREFIX", "BUILDKIT_IMAGE", "PYTHON3_VERSION"):
            with self.subTest(variable=variable), self.assertRaisesRegex(ValueError, "not the version or the registry of an image"):
                e2e.versions({"name": "Shop", "tested": [{variable: "x"}]}, 0, {variable})

    def test_a_variable_no_image_is_written_with_is_not_a_version_even_when_it_is_a_setting(self):
        with self.assertRaisesRegex(ValueError, "Shop sets 'OTHER_SETTING' in its versions"):
            e2e.versions({"name": "Shop", "tested": [{"OTHER_SETTING": "x"}]}, 0, {"APIM_VERSION"})
        self.assertEqual(e2e.versions({"name": "Shop", "tested": [{"NODE_VERSION": "24", "PYTHON_VERSION": "3.14"}]}, 0,
                                      {"NODE_VERSION", "PYTHON_VERSION"}), {"NODE_VERSION": "24", "PYTHON_VERSION": "3.14"})

    def test_the_variables_of_the_images_are_those_the_project_writes_them_with(self):
        built = {"services": {"gateway": {"raw_image": "registry/${APIM_REGISTRY:-graviteeio}/gateway:${APIM_VERSION:-4.13}"},
                              "db": {"raw_image": "mongo:8"}, "odd": {}}}
        self.assertEqual(e2e.image_variables(built), {"APIM_REGISTRY", "APIM_VERSION"})
        self.assertIsNone(e2e.image_variables({"playgrounds": {}}), "a project that lists no services is not one the Lab UI builds")

    def test_a_set_that_is_not_there_says_how_many_are(self):
        with self.assertRaisesRegex(ValueError, "was tested with 2 sets of versions: choose from 0 to 1"):
            e2e.versions(self.UNIT, 2)
        with self.assertRaisesRegex(ValueError, "was tested with 2 sets"):
            e2e.versions(self.UNIT, -1)
        with self.assertRaisesRegex(ValueError, "says no versions it was tested with: it has no set 0"):
            e2e.versions({"name": "Shop", "tested": []}, 0)


class Machine(Case):

    def shell(self, answers):
        calls = []

        def run(command, timeout=0):
            calls.append(command)
            return answers.pop(0)
        return run, calls

    def test_a_machine_with_no_lab_is_free(self):
        shell, calls = self.shell([(0, "", ""), (0, "\n", "")])
        self.assertIsNone(e2e.clean_machine(shell, "gravitee"))
        self.assertEqual([command[:3] for command in calls], [["docker", "ps", "-aq"], ["docker", "volume", "ls"]])
        self.assertIn("label=com.docker.compose.project=gravitee", calls[0])

    def test_containers_or_volumes_a_person_left_are_not_run_over(self):
        for answers, what in (([(0, "a1b2\n", "")], "containers"), ([(0, "", ""), (0, "gravitee_mongo\n", "")], "volumes")):
            with self.subTest(what=what):
                shell, _ = self.shell(answers)
                said = e2e.clean_machine(shell, "gravitee")
                self.assertIn(f"already has {what} of the lab (gravitee)", said)
                self.assertIn("make reset", said)

    def test_docker_that_does_not_answer_is_said(self):
        shell, _ = self.shell([(1, "", "Cannot connect\nIs the docker daemon running?")])
        self.assertEqual(e2e.clean_machine(shell, "gravitee"), "Docker does not answer: Is the docker daemon running?")


class Starting(Case):

    def test_a_start_that_ends_well_is_a_step(self):
        lab = FakeLab(lab_state({"shop": UP}), lab_state({"shop": UP}, job={**JOB, "done": False}), lab_state({"shop": UP}, job=JOB))
        run = attempt(lab)
        run.start(60)
        self.assertEqual([(step.name, step.ok) for step in run.steps], [("Start Shop", True)])
        self.assertEqual(lab.calls[1], ("POST", "/api/playgrounds/shop/start", {"accept": []}))

    def test_an_item_of_another_catalog_is_started_at_its_own_address(self):
        lab = FakeLab(lab_state({"bank": UP}, uid="@hub/bank", name="Bank"), lab_state({"bank": UP}, job=JOB, uid="@hub/bank", name="Bank"))
        clock = Clock()
        run = e2e.Run(lab, "playground", "@hub/bank", clock=clock, sleep=clock.sleep, get=lambda url: 200)
        run.start(60)
        self.assertEqual([(step.name, step.ok) for step in run.steps], [("Start Bank", True)])
        self.assertEqual(lab.calls[1], ("POST", "/api/playgrounds/@hub/bank/start", {"accept": []}))

    def test_what_it_stops_or_turns_off_is_what_the_dialog_said(self):
        dialog = {"stops": [{"kind": "playground", "id": "other"}], "turns_off": [{"kind": "addon", "id": "mailer"}], "starts": []}
        lab = FakeLab(lab_state({"shop": UP}, action={"enabled": True, "dialog": dialog}), lab_state({"shop": UP}, job=JOB))
        attempt(lab).start(60)
        self.assertEqual(lab.calls[1][2], {"accept": ["playground:other", "addon:mailer"]})

    def test_a_start_the_lab_ui_refuses_for_want_of_a_license_or_a_setting_cannot_be_tried_here(self):
        for reason in ("Needs a license", "Not in the license", "License expired", "Needs settings: HYBRID_CLOUD_TOKEN",
                       "The license is not signed by GraviteeSource"):
            with self.subTest(reason=reason), self.assertRaises(e2e.Unable) as caught:
                attempt(FakeLab(lab_state({"shop": UP}, action={"enabled": False, "reason": reason}))).start(60)
            self.assertEqual(str(caught.exception), reason)

    def test_any_other_start_the_lab_ui_refuses_is_a_failure(self):
        run = attempt(FakeLab(lab_state({"shop": UP}, action={"enabled": False, "reason": "Needs newer versions"})))
        with self.assertRaisesRegex(e2e.Failed, "does not allow the start: Needs newer versions"):
            run.start(60)

    def test_a_start_the_server_refuses_is_a_failure_with_its_words(self):
        with self.assertRaisesRegex(e2e.Failed, "Stop it first"):
            attempt(FakeLab(lab_state({"shop": UP}), refuse="Stop it first")).start(60)

    def test_a_job_that_fails_says_why(self):
        lab = FakeLab(lab_state({"shop": UP}), lab_state({"shop": UP}, job={**JOB, "ok": False, "error": "seed: HTTP 400"}))
        run = attempt(lab)
        with self.assertRaisesRegex(e2e.Failed, "seed: HTTP 400"):
            run.start(60)
        self.assertEqual([(step.name, step.ok, step.detail) for step in run.steps], [("Start Shop", False, "seed: HTTP 400")])

    def test_a_job_that_does_not_end_is_a_failure_after_the_time_it_was_given(self):
        run = attempt(FakeLab(lab_state({"shop": UP}), lab_state({"shop": UP}, job={**JOB, "done": False})))
        with self.assertRaisesRegex(e2e.Failed, "the start did not happen within 30 s"):
            run.start(30)
        self.assertGreaterEqual(run.clock(), 30)


class Settling(Case):

    def test_every_container_of_the_item_up_is_enough(self):
        starting = {"key": "starting", "label": "Starting", "running": True}
        done = {"key": "done", "label": "Done", "running": False}
        lab = FakeLab(lab_state({"shop": starting, "seed": done}), lab_state({"shop": starting, "seed": done}),
                      lab_state({"shop": UP, "seed": done}))
        run = attempt(lab)
        run.settle()
        self.assertEqual([(step.name, step.ok) for step in run.steps], [("Containers are up", True)])
        self.assertGreater(run.clock(), 0, "it waited for the container that was starting")

    def test_a_container_in_an_issue_ends_the_wait_and_says_which(self):
        issue = {"key": "issue", "label": "Restarting (1)", "running": True}
        run = attempt(FakeLab(lab_state({"shop": UP, "api": issue})))
        with self.assertRaisesRegex(e2e.Failed, r"api in an issue: Restarting \(1\)"):
            run.settle()
        self.assertFalse(run.steps[-1].ok)

    def test_a_platform_in_an_issue_ends_the_wait_too(self):
        state = lab_state({"shop": UP})
        state["platform"] = {"state": {"key": "issue", "label": "Gateway exited (1)"}}
        with self.assertRaisesRegex(e2e.Failed, r"the platform is in an issue: Gateway exited \(1\)"):
            attempt(FakeLab(state)).settle()
        state["platform"] = {"state": {"key": "partial", "label": "Partly running"}}
        attempt(FakeLab(state)).settle()   # what the item does not need may be off

    def test_a_container_that_never_comes_up_is_named_once_the_time_is_over(self):
        starting = {"key": "starting", "label": "Starting", "running": True}
        run = attempt(FakeLab(lab_state({"shop": UP, "slow": starting})))
        with self.assertRaisesRegex(e2e.Failed, r"every container being up did not happen within 300 s \(not up: slow\)"):
            run.settle()

    def test_the_log_of_what_is_not_up_is_read_by_its_container(self):
        calls = []

        def shell(command, timeout=0):
            calls.append(command)
            return 0, "line 1\n", "warning\n"
        starting = {"key": "starting", "label": "Starting", "running": True}
        run = attempt(FakeLab(lab_state({"shop": UP, "slow": starting})), shell=shell)
        self.assertEqual(run.logs(), {"slow": "line 1\nwarning"})
        self.assertEqual(calls, [["docker", "logs", "--tail", "80", "gio-slow"]])


class Consoles(Case):

    LINKS = [{"service": "shop", "label": "Shop", "url": "http://localhost:8130"},
             {"service": "gateway", "label": "Not ours", "url": "http://localhost:8082"}]

    def test_the_consoles_of_the_item_answer(self):
        asked = []
        run = attempt(FakeLab(lab_state({"shop": UP}, self.LINKS)), get=lambda url: asked.append(url) or 302)
        run.consoles()
        self.assertEqual(asked, ["http://localhost:8130"], "only its own, a redirect is an answer")
        self.assertEqual(run.steps[0].name, "1 console answers")

    def test_an_item_with_no_console_has_no_step(self):
        run = attempt(FakeLab(lab_state({"shop": UP}, [self.LINKS[1]])))
        run.consoles()
        self.assertEqual(run.steps, [])

    def test_a_console_is_asked_again_until_it_answers(self):
        answers = iter([0, 503, 200])
        run = attempt(FakeLab(lab_state({"shop": UP}, self.LINKS)), get=lambda url: next(answers))
        run.consoles()
        self.assertTrue(run.steps[0].ok)
        self.assertEqual(run.clock(), 2 * e2e.POLL)

    def test_a_console_that_does_not_answer_is_named_with_its_address(self):
        for status, said in ((0, "nothing"), (500, "status 500")):
            with self.subTest(status=status):
                run = attempt(FakeLab(lab_state({"shop": UP}, self.LINKS)), get=lambda url: status)
                with self.assertRaisesRegex(e2e.Failed, rf"Shop \(http://localhost:8130\) does not answer: {said}"):
                    run.consoles()


class OnFile(Case):

    def test_the_on_file_is_put_back_as_it_was_found(self):
        self.folder(e2e, files={".lab/on.yml": "include:\n  - addons/mailer/compose.yml\n"})
        restore = e2e.keep_on_file()
        (self.root / ".lab/on.yml").write_text("include:\n  - playgrounds/shop/compose.yml\n", encoding="utf-8")
        restore()
        self.assertEqual((self.root / ".lab/on.yml").read_text(encoding="utf-8"), "include:\n  - addons/mailer/compose.yml\n")

    def test_a_file_that_was_not_there_is_not_left_behind(self):
        self.folder(e2e)
        restore = e2e.keep_on_file()
        self.write(".lab/on.yml", "include: []\n")
        restore()
        self.assertFalse((self.root / ".lab/on.yml").exists())


class Server(Case):

    def test_the_lab_ui_answers_on_a_free_port_of_this_machine_as_its_page_asks(self):
        lab = e2e.Lab()
        self.addCleanup(lab.close)
        self.assertGreater(lab.port, 0)
        with self.assertRaisesRegex(RuntimeError, "Not found|No such|404"):
            lab.call("GET", "/api/nothing-here")
        with self.assertRaises(RuntimeError):   # a POST has the header of the page: what it asks is refused for another reason
            lab.call("POST", "/api/nothing-here", {})


class Removing(Case):

    def test_what_the_run_made_is_removed_by_the_name_of_the_project(self):
        calls = []
        self.patch(compose, "project_name", lambda: "gravitee")
        run = attempt(FakeLab(), shell=lambda command, timeout=0: calls.append(command) or (0, "", ""))
        self.assertIsNone(run.stop())
        self.assertEqual(calls, [["docker", "compose", "--progress", "plain", "-p", "gravitee", "down", "--volumes", "--remove-orphans",
                                  "--timeout", "30"]])

    def test_a_removal_that_fails_says_so(self):
        run = attempt(FakeLab(), shell=lambda command, timeout=0: (1, "", "network in use\nremoving failed"))
        self.assertEqual(run.stop(), "removing failed")


class Saying(Case):

    def run_of(self):
        run = attempt(FakeLab())
        run.steps = [e2e.Step("Start Shop", True, "", 252.4), e2e.Step("Containers are up", False, "api | in an issue", 12)]
        return run

    def test_a_time_is_said_as_a_person_reads_it(self):
        self.assertEqual([e2e.seconds(value) for value in (0, 44.6, 59.6, 60, 252.4, 3600)],
                         ["0 s", "45 s", "1 min 00 s", "1 min 00 s", "4 min 12 s", "60 min 00 s"])

    def test_the_run_says_its_versions_its_steps_and_its_verdict(self):
        run = self.run_of()
        self.assertEqual(e2e.lines(run, e2e.FAILED, {"APIM_VERSION": "4.13.0"}, "api in an issue"), [
            "Versions: APIM_VERSION=4.13.0", "✓ Start Shop (4 min 12 s)", "✗ Containers are up (12 s): api | in an issue",
            "It does not work: api in an issue"])
        self.assertEqual(e2e.lines(run, e2e.CANNOT, {}, "Needs a license")[-1], "Not tried: Needs a license")
        self.assertEqual(e2e.lines(attempt(FakeLab()), e2e.OK, {}, "")[-1], "It works")

    def test_the_summary_of_a_job_is_markdown_with_the_log_of_what_is_not_up(self):
        text = e2e.summary(self.run_of(), e2e.FAILED, {"APIM_VERSION": "4.13.0"}, "api in an issue", {"api": "boom"})
        self.assertIn("### playground:shop does not work", text)
        self.assertIn("Versions: `APIM_VERSION=4.13.0`", text)
        self.assertIn("| Start Shop | ✅ | 4 min 12 s |", text)
        self.assertIn("| Containers are up | ❌ api / in an issue | 12 s |", text, "a pipe would end the cell")
        self.assertIn("<summary>Log of api</summary>", text)
        self.assertIn("Versions: the defaults of the core", e2e.summary(self.run_of(), e2e.OK, {}))

    def test_the_report_goes_to_the_page_of_the_job_and_groups_the_logs_on_github(self):
        said = []
        with tempfile.NamedTemporaryFile("r", suffix=".md") as file, mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            e2e.report(self.run_of(), e2e.CANNOT, {}, "Needs a license", {"api": "boom"}, said.append, file.name)
            written = file.read()
        self.assertIn("### playground:shop not tried", written)
        self.assertIn("::group::Log of api", said)
        self.assertIn("::endgroup::", said)
        self.assertIn("::warning title=playground:shop not tried::Needs a license", said, "a run that was not tried is not a pass")

    def test_what_a_unit_says_never_runs_a_workflow_command(self):
        """A log, a step's detail: a hostile unit writes what it likes, and on GitHub a line that begins `::` is a command."""
        said = []
        run = self.run_of()
        run.steps[1].detail = "api\n::add-mask::secret"
        log = "fine\n::error title=Forged::a check that did not fail\n::stop-commands::guess"
        with tempfile.NamedTemporaryFile("r", suffix=".md") as file, mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            e2e.report(run, e2e.CANNOT, {}, "Needs a license\n::set-output name=x::y 100%", {"api": log}, said.append, file.name)
        opened = [i for i, line in enumerate(said) if line.startswith("::stop-commands::")]
        closed = [i for i, line in enumerate(said) if re.fullmatch(r"::[0-9a-f]{32}::", line)]
        self.assertEqual(len(opened), len(closed), "every block that is opened is closed")
        self.assertGreaterEqual(len(opened), 2, "the steps and the log are each a block")
        tokens = [said[i][len("::stop-commands::"):] for i in opened]
        self.assertEqual(len(set(tokens)), len(tokens), "a token for each block, which only this call knows")
        for line in ("::add-mask::secret", "::error title=Forged::a check that did not fail", "::stop-commands::guess",
                     "::set-output name=x::y 100%"):
            at = next(i for i, one in enumerate(said) if line in one)
            self.assertTrue(any(a < at < b for a, b in zip(opened, closed)), f"{line!r} is outside a block")
        self.assertIn("::warning title=playground:shop not tried::Needs a license%0A::set-output name=x::y 100%25", said,
                      "the message of the one command that is ours has no line break and no percent sign of its own")
        self.assertEqual(len([line for line in said if line.startswith("::warning")]), 1, "the one command that is ours")

    def test_a_summary_that_cannot_be_written_is_said_and_the_run_keeps_what_it_found(self):
        said = []
        e2e.report(self.run_of(), e2e.FAILED, {}, "x", None, said.append, "/no/such/folder/summary.md")
        self.assertIn("The summary could not be written: No such file or directory", said)
        self.assertIn("It does not work: x", said)

    def test_off_github_nothing_is_wrapped(self):
        said = []
        with mock.patch.dict(os.environ, {}, clear=True):
            e2e.report(self.run_of(), e2e.FAILED, {}, "x", {"api": "::error::boom"}, said.append)
        self.assertNotIn("::stop-commands", "\n".join(said))

    def test_a_log_is_kept_by_its_end_and_a_step_is_one_line_of_the_table(self):
        self.assertEqual(e2e.tail(None), "")
        self.assertEqual(e2e.tail("short"), "short")
        long = "a" * (e2e.LOG_CHARS + 50) + "THE END"
        self.assertTrue(e2e.tail(long).startswith("…") and e2e.tail(long).endswith("THE END"))
        self.assertEqual(len(e2e.tail(long)), e2e.LOG_CHARS + 1)
        run = self.run_of()
        run.steps[1].detail = "line one\n| a cell\n\tline three"
        text = e2e.summary(run, e2e.FAILED, {}, "")
        self.assertIn("| Containers are up | ❌ line one / a cell line three | 12 s |", text)

    def test_what_a_unit_says_cannot_write_the_page_of_the_job(self):
        """The name of a step, its detail, the message and a version are a unit's words, shown in a table and in lines a
        maintainer trusts: a break, a pipe, a heading, a link, an image and a tag are the unit's to write only in a log."""
        hostile = "Shop |\n\n### shop works\n![x](https://evil.example/p.png) [a](https://evil.example) <img src=x> `c` *b* _i_"
        run = self.run_of()
        run.steps = [e2e.Step("Start " + hostile, True, "", 1), e2e.Step("Containers are up", False, "boom\n\n## fake\n" + hostile, 2)]
        text = e2e.summary(run, e2e.FAILED, {"APIM_VERSION": "4`\n\n## forged\n![i](https://evil.example/i.png)"},
                           "# boom\n\n## fake heading\n![x](https://evil.example/p.png)")
        lines = text.splitlines()
        self.assertEqual([line for line in lines if line.startswith("#")], ["### playground:shop does not work"], "one heading, ours")
        self.assertEqual(len([line for line in lines if line.startswith("|")]), 4, "the header, the rule, and a row for each step")
        versions = [line for line in lines if line.startswith("Versions:")]
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].count("`"), 2, "one span of code, which a backtick of the value cannot end")
        for line in lines:
            if line not in versions:   # a span of code holds a bare address as it was written; the rest has no markup of the unit's
                bare = re.sub(r"`[^`]*`", "", re.sub(r"\\.", "", line))
                self.assertFalse(set("[]<>*_`") & set(bare), f"markup of the unit's in {line!r}")
        self.assertEqual(e2e.plain("a|b\nc"), "a/b c")
        self.assertEqual(e2e.plain("\\a [b] <c> `d` *e* _f_ #g ~h"), "\\\\a \\[b\\] \\<c\\> \\`d\\` \\*e\\* \\_f\\_ \\#g \\~h")
        self.assertEqual(e2e.code("a`b\nc"), "`a'b c`")

    def test_a_bare_address_in_what_a_unit_says_is_code_and_never_a_link_of_the_page_of_the_job(self):
        """GitHub makes a link of `https://...`, `www....` and an address of mail by itself: on the page a maintainer trusts that
        is a link the unit chose. The word that holds one is a span of code, which no renderer links; the rest is escaped as ever."""
        spans = lambda text: re.findall(r"`[^`\n]*`", text)   # noqa: E731 - the one reading of a code span used below
        for said in ("https://evil.example/login", "HTTP://EVIL.example", "ftp://evil.example/x", "ssh://git@evil.example/r",
                     "www.evil.example/login", "WWW.Evil.example", "mailto:a@evil.example", "xmpp:a@evil.example",
                     "a@evil.example", "(https://evil.example/x)", "see:https://evil.example/x.", "<https://evil.example>",
                     "[click](https://evil.example)", "![i](https://evil.example/i.png)", "https://evil.example/a_b*c"):
            with self.subTest(said=said):
                shown = e2e.plain(f"Pull the image from {said} now")
                self.assertTrue(shown.startswith("Pull the image from ") and shown.endswith(" now"), shown)
                held = spans(shown)
                self.assertEqual(len(held), 1, shown)
                self.assertEqual(held[0], e2e.code(said))   # as it was written, to read and to copy
                outside = shown.replace(held[0], "")
                self.assertIsNone(e2e.LINK.search(outside), f"a link outside the code: {outside!r}")
        self.assertEqual(e2e.plain("Pull from https://a.example/x and https://b.example/y"),
                         "Pull from `https://a.example/x` and `https://b.example/y`")
        self.assertEqual(e2e.plain("a|b https://c.example/d|e"), "a/b `https://c.example/d/e`", "a pipe never ends a cell")
        self.assertEqual(e2e.plain("tick https://e.example/`x`"), "tick `https://e.example/'x'`", "a backtick never ends the span")
        self.assertEqual(e2e.plain("https://e.example/a\n\n## forged"), "`https://e.example/a` \\#\\# forged")

    def test_a_very_long_word_costs_what_it_is_long_and_not_its_square(self):
        """A unit writes what it likes in a step or a detail: a word of a million letters, one of a million `a.b-`, a million
        `a@`. The pattern for an address reads each once."""
        started = time.process_time()
        for word in ("a" * 1_000_000, "a.b-" * 250_000, "a@" * 500_000, "w" * 1_000_000, "www" * 300_000, ":" * 1_000_000):
            e2e.plain(f"{word} x")
        self.assertLess(time.process_time() - started, 10.0)

    def test_words_that_are_no_address_are_only_escaped(self):
        self.assertEqual(e2e.plain("Shop on port 8082, version 4.13.0 (a_b) at host:80"), "Shop on port 8082, version 4.13.0 (a\\_b) at host:80")
        self.assertEqual(e2e.plain(""), "")
        self.assertEqual(e2e.plain("   "), "")
        self.assertEqual(e2e.plain("@user and x @ y"), "@user and x @ y", "a mention and a lone @ are not an address of mail")

    def test_an_address_in_a_step_or_a_message_reaches_the_summary_as_code(self):
        run = self.run_of()
        run.steps = [e2e.Step("Start https://evil.example/a", False, "see www.evil.example and mail a@evil.example", 1)]
        text = e2e.summary(run, e2e.FAILED, {}, "Visit https://evil.example/phish to continue")
        outside = re.sub(r"`[^`\n]*`", "", text.replace("<details>", ""))
        self.assertIsNone(e2e.LINK.search(outside), outside)
        self.assertIn("| Start `https://evil.example/a` | ❌ see `www.evil.example` and mail `a@evil.example` | 1 s |", text)
        self.assertIn("\nVisit `https://evil.example/phish` to continue\n", text)
        # A log is in its fence, as it is written, and the fence is no link either
        self.assertIn("\nhttps://evil.example/log\n", e2e.summary(run, e2e.FAILED, {}, "", {"api": "https://evil.example/log"}))

    def test_a_log_cannot_end_its_own_block_and_write_the_page_of_the_job(self):
        self.assertEqual(e2e.fence("no ticks"), "```")
        self.assertEqual(e2e.fence("a ``` b"), "````")
        self.assertEqual(e2e.fence("`` ````` `"), "``````")
        text = e2e.summary(self.run_of(), e2e.FAILED, {}, "", {"api": "x\n```\n## A heading of my own\n```"})
        lines = text.splitlines()
        at = lines.index("````")
        self.assertEqual(lines[at + 1:at + 6], ["x", "```", "## A heading of my own", "```", "````"])
        self.assertEqual(e2e.escaped("a%b\r\nc"), "a%25b%0D%0Ac")

    def test_off_github_the_report_is_plain_text_and_writes_nothing(self):
        said = []
        with mock.patch.dict(os.environ, {}, clear=True):
            e2e.report(self.run_of(), e2e.FAILED, {}, "x", {"api": "boom"}, said.append)
        self.assertIn("--- Log of api", "\n".join(said))
        self.assertNotIn("::group::", "\n".join(said))


class Running(Case):
    """run(): the steps in order, the exit code, the machine left as it was found."""

    def setUp(self):
        self.said = []
        self.built = {"playgrounds": {"shop": {"name": "Shop", "tested": [{"APIM_VERSION": "4.13.0"}]}}, "addons": {}, "setups": {}}
        self.patch(project, "load", lambda: self.built)
        self.patch(project, "forget")
        self.patch(e2e.layers, "prepare")
        self.removed = []
        self.stub_run = mock.Mock()
        self.stub_run.return_value.stop.side_effect = lambda: self.removed.append(True)
        self.lab = mock.Mock()
        self.patch(e2e, "Lab", lambda: self.lab)
        self.patch(e2e, "Run", lambda *args, **more: self.stub_run.return_value)
        self.patch(e2e, "report")
        self.patch(e2e, "clean_machine", lambda *args: None)
        self.restored = []
        self.patch(e2e, "keep_on_file", lambda: lambda: self.restored.append(True))
        self.env = mock.patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)

    def go(self, text="playground:shop", **more):
        return e2e.run(text, out=self.said.append, **more)

    def test_a_run_that_holds_is_zero_and_removes_what_it_made(self):
        self.assertEqual(self.go(tested=0), e2e.OK)
        self.assertEqual(os.environ["APIM_VERSION"], "4.13.0", "the set is the process's: no file is written")
        self.assertEqual([call[0] for call in self.stub_run.return_value.method_calls], ["start", "settle", "consoles", "stop"])
        self.assertEqual(self.removed, [True])
        self.assertEqual(self.restored, [True], "the on file is as it was")
        self.lab.close.assert_called_once_with()

    def test_what_is_left_to_look_at_is_not_removed(self):
        self.assertEqual(self.go(keep=True), e2e.OK)
        self.assertEqual((self.removed, self.restored), ([], []), "what stays running stays on")

    def test_a_failure_is_one_and_reads_the_logs_before_the_lab_goes(self):
        self.stub_run.return_value.settle.side_effect = e2e.Failed("api in an issue")
        self.stub_run.return_value.logs.return_value = {"api": "boom"}
        self.assertEqual(self.go(), e2e.FAILED)
        self.assertEqual(e2e.report.call_args.args[3:5], ("api in an issue", {"api": "boom"}))
        self.assertEqual(self.removed, [True], "removed all the same")

    def test_what_the_server_or_docker_refuses_is_a_failure_too(self):
        self.stub_run.return_value.start.side_effect = RuntimeError("Docker did not answer")
        self.assertEqual(self.go(), e2e.FAILED)
        self.assertEqual(e2e.report.call_args.args[3], "Docker did not answer")

    def test_what_cannot_be_tried_here_is_two_and_is_said(self):
        self.stub_run.return_value.start.side_effect = e2e.Unable("Needs a license")
        self.assertEqual(self.go(), e2e.CANNOT)
        self.assertEqual(e2e.report.call_args.args[2:4], ({}, "Needs a license"))

    def test_a_wrong_name_or_a_wrong_set_is_two_before_anything_starts(self):
        for text, tested, said in (("playground", None, "Name the item"), ("addon:mailer", None, "No add-on named mailer"),
                                   ("playground:shop", 3, "choose from 0 to 0")):
            with self.subTest(text=text):
                self.said.clear()
                self.assertEqual(self.go(text, tested=tested), e2e.CANNOT)
                self.assertIn(said, self.said[0])
        self.stub_run.assert_not_called()

    def test_a_set_that_is_not_a_version_stops_the_run_before_anything_starts(self):
        self.built["playgrounds"]["shop"]["tested"] = [{"PATH": "/tmp/mine"}]
        self.built["services"] = {"gateway": {"raw_image": "gw:${APIM_VERSION:-4}"}}
        self.assertEqual(self.go(tested=0), e2e.CANNOT)
        self.assertIn("sets 'PATH' in its versions", self.said[0])
        self.assertNotEqual(os.environ.get("PATH"), "/tmp/mine", "the process is not given what the set asked")
        self.stub_run.assert_not_called()

    def test_the_words_of_a_unit_in_a_refusal_are_data_on_github(self):
        self.built["playgrounds"]["shop"]["name"] = "Shop\n::error::forged"
        self.built["playgrounds"]["shop"]["tested"] = [{}]
        said = []
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            e2e.run("playground:shop", tested=3, out=said.append)
        at = next(i for i, line in enumerate(said) if "::error::forged" in line)
        self.assertTrue(said[at - 1].startswith("::stop-commands::") and re.fullmatch(r"::[0-9a-f]{32}::", said[at + 1]), said)
        self.assertNotIn("\n", said[at])

    def test_a_machine_that_holds_a_lab_is_two_and_nothing_is_started_or_removed(self):
        self.patch(e2e, "clean_machine", lambda *args: "This machine already has containers of the lab (gravitee)")
        self.assertEqual(self.go(), e2e.CANNOT)
        self.assertEqual(self.said, ["Not tried: This machine already has containers of the lab (gravitee)"])
        self.assertEqual(self.removed, [])


# ---------------------------------------------------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------------------------------------------------

def unit(catalog, tested=0):
    """What the plan reads of a unit of the project: its catalog and the sets of versions it says."""
    return {"catalog": catalog, "tested": [{"APIM_VERSION": str(n)} for n in range(tested)]}


# base is the first catalog (bare ids), hub another; what belongs to a machine is in the project too
PROJECT = {
    "addons": {"echo": unit("base"), "relay": unit("base"), "@hub/ledger": unit("hub"), "local/mine": unit("local")},
    "setups": {"pair": unit("base"), "@hub/duo": unit("hub")},
    "playgrounds": {"shop": unit("base", 2), "tour": unit("base"), "@hub/bank": unit("hub", 1), "local/draft": unit("local")}}


def broken(ns, *files):
    """PROJECT with the files of catalog `ns` that Compose could not read: as `project.gather` lists them, in the problems of
    the catalog, and not among its items."""
    return {**PROJECT, "catalog_problems": {ns: [{"file": f".lab/catalogs/{ns}/{one}", "display": one,
                                                   "message": "Cannot be read: go-yaml load error"} for one in files]}}


class Files(Case):
    """Which item a file of a catalog belongs to, and what a change of files touches."""

    def setUp(self):
        self.patch(paths, "default_ns", return_value="base")

    def test_a_file_belongs_to_the_item_whose_folder_it_is_in(self):
        self.assertEqual(e2e.item_of("addons/echo/compose.yml"), "addon:echo")
        self.assertEqual(e2e.item_of("addons/echo/certs/generate.sh"), "addon:echo")
        self.assertEqual(e2e.item_of("playgrounds/shop/apps/web/server.py"), "playground:shop")

    def test_a_setup_is_one_file(self):
        self.assertEqual(e2e.item_of("setups/pair.yml"), "setup:pair")
        for path in ("setups/README.md", "setups/pair.yml.bak", "setups/pair/x.yml", "setups/Pair.yml"):
            with self.subTest(path=path):
                self.assertIsNone(e2e.item_of(path))

    def test_what_belongs_to_a_machine_and_what_belongs_to_no_item_are_none(self):
        for path in ("addons/local/mine/compose.yml", "playgrounds/local/draft/guide.md", "setups/local.yml", "README.md",
                     "addons/README.md", "docs/a.md", "addons/Echo/compose.yml", "addons/echo", "tests/addons/echo/x.yml",
                     ".github/workflows/e2e.yml", "addons//x", "", "/addons/echo/x.yml", "addons/.hidden/x"):
            with self.subTest(path=path):
                self.assertIsNone(e2e.item_of(path))

    def test_the_id_of_an_item_is_bare_in_the_first_catalog_and_qualified_in_the_others(self):
        self.assertEqual(e2e.lab_item("base", "addon:echo"), "addon:echo")
        self.assertEqual(e2e.lab_item("hub", "playground:bank"), "playground:@hub/bank")
        self.assertEqual(e2e.lab_item(None, "setup:pair"), "setup:pair", "a repository that held its items itself")

    def test_the_ids_are_not_bare_when_the_engine_lists_no_catalog(self):
        self.patch(paths, "default_ns", return_value=None)
        self.assertEqual(e2e.lab_item("hub", "addon:echo"), "addon:@hub/echo")

    def canary(self):
        self.asked = getattr(self, "asked", 0) + 1
        return ["playground:tour"]

    def test_a_change_touches_the_items_of_its_files_each_once_in_order(self):
        files = ["setups/pair.yml", "playgrounds/shop/guide.md", "addons/echo/compose.yml", "addons/echo/README.md", "docs/a.md"]
        self.assertEqual(e2e.touched(files, e2e.CATALOG_PATHS, self.canary), ["addon:echo", "playground:shop", "setup:pair"])
        self.assertEqual(getattr(self, "asked", 0), 0, "the canary is for a change that touches no item")

    def test_a_change_of_what_the_items_share_runs_the_canary_when_no_item_changed(self):
        for path in ("catalog.yml", "Makefile", ".github/workflows/ci.yml", ".github/actions/lab/action.yml", ".github/standard.json",
                     ".github/scripts/plan.py", ".catalog/rules.json"):
            with self.subTest(path=path):
                self.assertEqual(e2e.touched([path, "docs/a.md"], e2e.CATALOG_PATHS, self.canary), ["playground:tour"])

    def test_an_item_that_changed_is_tried_alone_even_with_what_they_share(self):
        self.assertEqual(e2e.touched(["catalog.yml", "addons/echo/compose.yml"], e2e.CATALOG_PATHS, self.canary), ["addon:echo"])

    def test_a_change_that_deletes_the_only_item_it_touches_and_changes_what_they_share_runs_the_canary(self):
        gone = lambda item: item != "addon:gone"   # noqa: E731 - a table, not a function worth a name
        files = ["addons/gone/compose.yml", "addons/gone/README.md", "Makefile"]
        self.assertEqual(e2e.touched(files, e2e.CATALOG_PATHS, self.canary, alive=gone), ["playground:tour"])
        self.assertEqual(e2e.touched(files, e2e.CATALOG_PATHS, self.canary), ["addon:gone"], "without the question: the item it names")
        self.assertEqual(e2e.touched(["addons/gone/compose.yml", "docs/a.md"], e2e.CATALOG_PATHS, self.canary, alive=gone), [],
                         "a deletion alone has nothing to run")
        self.assertEqual(e2e.touched([*files, "addons/echo/compose.yml"], e2e.CATALOG_PATHS, self.canary, alive=gone), ["addon:echo"])

    def test_documentation_and_the_description_of_a_repository_run_nothing(self):
        self.assertEqual(e2e.touched(["README.md", "docs/a.md", ".github/dependabot.yml", ".github/CODEOWNERS"],
                                     e2e.CATALOG_PATHS, self.canary), [])
        self.assertEqual(e2e.touched([], e2e.CATALOG_PATHS, self.canary), [])

    def test_a_change_of_the_engine_reads_no_item_from_its_files(self):
        # an engine that held a folder called addons would not be telling the catalog which of its items changed
        files = ["addons/echo/compose.yml", "docs/a.md"]
        self.assertEqual(e2e.touched(files, e2e.ENGINE_PATHS, self.canary, named=False), [])
        self.assertEqual(e2e.touched([*files, "seeder/seed.py"], e2e.ENGINE_PATHS, self.canary, named=False), ["playground:tour"])

    def test_what_every_item_shares_in_the_engine_triggers_and_the_page_does_not(self):
        for path in ("docker-compose.yml", ".env.example", "Makefile", "contract.lock", "catalogs.default.json", "seeder/seed.py",
                     "lab-ui/lab_ui/library/layers.py", "lab-ui/lab_ui/catalogs/trust.py", "lab-ui/server.py", "lab-ui/catalog.py",
                     "certs/mtls/generate.sh", "lab-ui/frames/x", ".github/actions/compose/action.yml", ".github/workflows/e2e.yml",
                     ".github/workflows/e2e-run.yml", ".github/scripts/e2e_matrix.py", ".github/scripts/image_manifests.py"):
            with self.subTest(path=path):
                self.assertEqual(e2e.triggers([path, "docs/a.md"]), [path])
        for path in ("README.md", "docs/a.md", "lab-ui/web/js/core.js", "lab-ui/tests/test_e2e.py", ".github/workflows/ci.yml",
                     ".github/tests/test_e2e_matrix.py", "lab-ui/templates/catalog/README.md", "seeder.md"):
            with self.subTest(path=path):
                self.assertEqual(e2e.triggers([path]), [])

    def test_every_path_that_triggers_is_a_path_of_this_repository(self):
        # a file renamed or a folder moved must not turn the trigger off without a test saying so
        for path in e2e.ENGINE_PATHS:
            with self.subTest(path=path):
                found = e2e.ROOT / path
                self.assertTrue(found.is_dir() if path.endswith("/") else found.is_file(), f"{path} is not in the repository")


class Selecting(Case):
    """Which items of a project a plan takes, and the jobs of each."""

    def setUp(self):
        self.patch(paths, "default_ns", return_value="base")

    def test_every_shared_item_of_a_catalog_and_none_of_a_machine_or_of_another_catalog(self):
        self.assertEqual(e2e.shared_items(PROJECT, "base"), ["addon:echo", "addon:relay", "setup:pair", "playground:shop",
                                                              "playground:tour"])
        self.assertEqual(e2e.shared_items(PROJECT, "hub"), ["addon:@hub/ledger", "setup:@hub/duo", "playground:@hub/bank"])
        self.assertEqual(len(e2e.shared_items(PROJECT)), 10 - 2, "without a catalog: every shared item, not the two of a machine")

    def test_an_item_is_there_when_the_project_has_it_in_the_catalog_asked_for(self):
        self.assertTrue(e2e.exists("addon:echo", PROJECT))
        self.assertTrue(e2e.exists("addon:echo", PROJECT, "base"))
        self.assertFalse(e2e.exists("addon:echo", PROJECT, "hub"))
        self.assertFalse(e2e.exists("addon:gone", PROJECT))
        self.assertFalse(e2e.exists("addon:", {"addons": {}}))

    def test_the_items_a_person_names_each_once_in_the_order_written(self):
        self.assertEqual(e2e.named(["playground:tour", "addon:@hub/ledger", "playground:tour"], PROJECT),
                         ["playground:tour", "addon:@hub/ledger"])
        self.assertEqual(e2e.named(["addon:@base/echo"], PROJECT), ["addon:echo"], "the first catalog may be written with its name")
        self.assertEqual(e2e.named([], PROJECT), [])

    def test_a_name_that_is_no_shared_item_of_the_catalog_stops_everything_with_how_to_write_it(self):
        for word in ("playground:gone", "tour", "addon:local/mine", "addon:Echo; rm -rf /", "addon:@hub/ledger", "-x", "", "::error::x"):
            with self.subTest(word=word), self.assertRaises(e2e.PlanError) as caught:
                e2e.named([word], PROJECT, "base")
            said = str(caught.exception)
            self.assertIn("is not a shared item of base: name it kind:id, for example playground:shop", said)
            self.assertTrue(said.startswith("'"), "what was typed is quoted, so that no line of the log starts with it")

    def test_what_was_typed_is_cut_and_on_one_line(self):
        with self.assertRaises(e2e.PlanError) as caught:
            e2e.named(["x" * 500 + "\n::error::forged"], PROJECT)
        self.assertEqual(len(str(caught.exception).split("\n")), 1)
        self.assertLess(len(str(caught.exception)), 200)
        self.assertEqual(e2e.shortened("a\n b"), "'a b'")

    def test_a_job_for_each_set_of_versions_of_an_item(self):
        sets = lambda item: {"playground:shop": 2}.get(item, 0)   # noqa: E731 - a table, not a function worth a name
        self.assertEqual(e2e.jobs(["playground:shop"], sets), [
            {"item": "playground:shop", "tested": "0", "name": "playground:shop (versions 0)"},
            {"item": "playground:shop", "tested": "1", "name": "playground:shop (versions 1)"}])

    def test_an_item_that_says_no_versions_runs_once_on_the_defaults_of_the_core(self):
        self.assertEqual(e2e.jobs(["playground:tour", "addon:@hub/ledger"], lambda item: 0), [
            {"item": "playground:tour", "tested": "", "name": "playground:tour (core defaults)"},
            {"item": "addon:@hub/ledger", "tested": "", "name": "addon:@hub/ledger (core defaults)"}])
        self.assertEqual(e2e.jobs([], lambda item: 0), [])

    def test_the_matrix_is_what_a_workflow_reads_and_its_count(self):
        found = e2e.matrix(["playground:shop", "playground:@hub/bank", "addon:echo"], PROJECT)
        self.assertEqual(found["count"], 4)
        self.assertEqual([job["name"] for job in found["matrix"]["include"]],
                         ["playground:shop (versions 0)", "playground:shop (versions 1)", "playground:@hub/bank (versions 0)",
                          "addon:echo (core defaults)"])
        self.assertEqual(json.loads(json.dumps(found)), found, "it is JSON")

    def test_a_matrix_of_more_jobs_than_github_runs_is_refused_and_says_so(self):
        built = {"addons": {f"a{n}": unit("base") for n in range(e2e.JOBS_LIMIT + 1)}, "setups": {}, "playgrounds": {}}
        self.assertEqual(e2e.matrix([f"addon:a{n}" for n in range(e2e.JOBS_LIMIT)], built)["count"], e2e.JOBS_LIMIT)
        with self.assertRaisesRegex(e2e.PlanError, f"{e2e.JOBS_LIMIT + 1} jobs and a matrix of GitHub runs {e2e.JOBS_LIMIT} at most"):
            e2e.matrix([f"addon:a{n}" for n in range(e2e.JOBS_LIMIT + 1)], built)


class Planning(Case):
    """plan(): the choice among everything, the canary, named items and a change, in a catalog that is linked."""

    def setUp(self):
        self.patch(paths, "default_ns", return_value="base")
        self.linked = {"base", "hub"}
        self.patch(registry, "entry", lambda ns: {"id": ns} if ns in self.linked else None)
        self.canary = ["playground:tour"]
        self.patch(manifest, "read_catalog", lambda path: {"canary": list(self.canary)})
        self.folders = []

    def git(self, files):
        def read(folder, base, head):
            self.folders.append((folder, base, head))
            return files
        return read

    def items(self, found):
        return [job["item"] for job in found["matrix"]["include"]]

    def test_everything_is_every_item_of_the_catalog(self):
        self.assertEqual(self.items(e2e.plan("hub", PROJECT, everything=True)), ["addon:@hub/ledger", "setup:@hub/duo", "playground:@hub/bank"])
        self.assertEqual(e2e.plan("base", PROJECT, everything=True)["count"], 6, "the two versions of shop are two jobs")

    def test_the_default_catalog_is_the_one_the_engine_lists_first(self):
        self.assertEqual(self.items(e2e.plan(None, PROJECT, canary_only=True)), ["playground:tour"])

    def test_an_engine_that_lists_no_catalog_cannot_say_which_is_the_default(self):
        self.patch(paths, "default_ns", return_value=None)
        with self.assertRaisesRegex(e2e.PlanError, "lists no default catalog"):
            e2e.plan(None, PROJECT, everything=True)

    def test_the_canary_is_what_catalog_yml_names_as_the_ids_of_the_lab(self):
        self.canary = ["playground:bank", "addon:ledger"]
        self.assertEqual(self.items(e2e.plan("hub", PROJECT, canary_only=True)), ["playground:@hub/bank", "addon:@hub/ledger"])

    def test_a_canary_that_names_an_item_that_is_gone_is_an_error_and_not_a_check_that_turns_off(self):
        self.canary = ["playground:tour", "playground:ghost"]
        with self.assertRaisesRegex(e2e.PlanError, "The canary of base names playground:ghost, which is not an item of it"):
            e2e.plan("base", PROJECT, canary_only=True)
        self.canary = ["playground:bank"]
        with self.assertRaisesRegex(e2e.PlanError, "The canary of base names playground:bank, which is not an item of it"):
            e2e.plan("base", PROJECT, canary_only=True)   # it is an item of another catalog

    def test_a_catalog_that_names_no_canary_cannot_be_asked_for_it_and_an_engine_change_needs_it(self):
        """A check with nothing to try passes: the engine's gate and the night would be green with no start at all."""
        self.canary = []
        said = []
        fix = "add `canary: \\[kind:name\\]` to the x-gravitee-catalog block of its catalog.yml"
        with self.assertRaisesRegex(e2e.PlanError, f"The catalog hub names no canary, so there is nothing to try: {fix}"):
            e2e.plan("hub", PROJECT, canary_only=True)
        with self.assertRaisesRegex(e2e.PlanError, "The catalog hub names no canary"):
            e2e.plan("hub", PROJECT, changed=("abc", "HEAD"), engine=True, git=self.git(["seeder/seed.py"]), warn=said.append)
        self.assertEqual(said, [], "an error and not a warning")
        self.assertEqual(e2e.plan("hub", PROJECT, changed=("abc", "HEAD"), engine=True, git=self.git(["docs/a.md"]))["count"], 0,
                         "a change of the engine that touches nothing the items share needs no canary")

    def test_a_change_of_a_catalog_that_names_no_canary_tries_nothing_and_says_so(self):
        self.canary = []
        said = []
        found = e2e.plan("hub", PROJECT, changed=("abc", "HEAD"), git=self.git(["Makefile", "README.md"]), warn=said.append)
        self.assertEqual(found["count"], 0)
        self.assertEqual(said, ["The catalog hub names no canary, so a change of what its items share tries nothing: "
                                "add `canary: [kind:name]` to the x-gravitee-catalog block of its catalog.yml"])
        said.clear()
        e2e.plan("hub", PROJECT, changed=("abc", "HEAD"), git=self.git(["README.md"]), warn=said.append)
        e2e.plan("hub", PROJECT, changed=("abc", "HEAD"), git=self.git(["playgrounds/bank/guide.md", "Makefile"]), warn=said.append)
        self.assertEqual(said, [], "nothing to say when no canary was needed")
        self.assertEqual(e2e.plan("hub", PROJECT, changed=("abc", "HEAD"), git=self.git(["Makefile"]))["count"], 0, "warn is optional")

    def test_a_catalog_yml_that_cannot_be_read_is_said(self):
        def refuse(path):
            raise registry.CatalogError("catalog_invalid", "catalog.yml: it has no x-gravitee-catalog block.", 400)
        self.patch(manifest, "read_catalog", refuse)
        with self.assertRaisesRegex(e2e.PlanError, "catalog.yml of base cannot be read: catalog.yml: it has no x-gravitee-catalog block"):
            e2e.plan("base", PROJECT, canary_only=True)

    def test_named_items_are_those_of_the_catalog(self):
        self.assertEqual(self.items(e2e.plan("hub", PROJECT, words=["playground:@hub/bank", "playground:@hub/bank"])), ["playground:@hub/bank"])
        with self.assertRaisesRegex(e2e.PlanError, "is not a shared item of hub"):
            e2e.plan("hub", PROJECT, words=["playground:tour"])

    def test_a_change_in_the_catalog_is_read_in_its_folder_and_touches_its_items(self):
        found = e2e.plan("hub", PROJECT, changed=("abc", "HEAD"), git=self.git(["playgrounds/bank/guide.md", "docs/a.md", "addons/gone/x.yml"]))
        self.assertEqual(self.items(found), ["playground:@hub/bank"], "an item the change deleted has nothing to run")
        self.assertEqual(self.folders, [(paths.ROOT / ".lab/catalogs/hub", "abc", "HEAD")])

    def test_a_change_in_the_catalog_that_touches_no_item_runs_the_canary_when_it_changes_what_they_share(self):
        self.canary = ["playground:bank"]
        found = e2e.plan("hub", PROJECT, changed=("abc", "HEAD"), git=self.git([".github/workflows/e2e.yml", "README.md"]))
        self.assertEqual(self.items(found), ["playground:@hub/bank"])
        self.assertEqual(e2e.plan("hub", PROJECT, changed=("abc", "HEAD"), git=self.git(["README.md"]))["count"], 0)

    def test_a_change_in_the_engine_is_read_in_the_engine_and_runs_the_canary_when_it_changes_what_every_item_shares(self):
        found = e2e.plan("base", PROJECT, changed=("abc", "HEAD"), engine=True, git=self.git(["seeder/seed.py", "docs/a.md"]))
        self.assertEqual(self.items(found), ["playground:tour"])
        self.assertEqual(self.folders, [(paths.ROOT, "abc", "HEAD")])
        self.assertEqual(e2e.plan("base", PROJECT, changed=("abc", "HEAD"), engine=True, git=self.git(["docs/a.md"]))["count"], 0)

    def test_a_change_in_the_engine_never_tries_an_item_for_a_file_that_looks_like_one(self):
        found = e2e.plan("base", PROJECT, changed=("abc", "HEAD"), engine=True, git=self.git(["addons/echo/compose.yml"]))
        self.assertEqual(found["count"], 0)

    def test_an_item_that_does_not_load_is_not_one_the_change_deleted(self):
        """Compose could not read the file: the project lists the item among the problems of the catalog and not among its items,
        and a plan that asked only whether it is there would find nothing to run, and pass."""
        built = broken("hub", "addons/mailer/compose.yml")
        for files in (["addons/mailer/compose.yml"], ["addons/mailer/README.md", "docs/a.md"], ["playgrounds/bank/guide.md", "addons/mailer/x.yml"],
                      ["Makefile", "addons/mailer/compose.yml"]):
            with self.subTest(files=files), self.assertRaises(e2e.PlanError) as caught:
                e2e.plan("hub", built, changed=("abc", "HEAD"), git=self.git(files))
            self.assertEqual(str(caught.exception), "addon:mailer of hub cannot be tried, its files do not load: Cannot be read: "
                                                   "go-yaml load error. Fix: catalog.py check hub")
        found = e2e.plan("hub", built, changed=("abc", "HEAD"), git=self.git(["playgrounds/bank/guide.md", "addons/gone/compose.yml"]))
        self.assertEqual(self.items(found), ["playground:@hub/bank"], "an item that was really deleted is still left out")
        self.assertEqual(e2e.plan("hub", built, changed=("abc", "HEAD"), git=self.git(["addons/gone/compose.yml"]))["count"], 0)

    def test_the_night_does_not_leave_out_an_item_that_does_not_load(self):
        built = broken("hub", "setups/duo.yml")
        with self.assertRaisesRegex(e2e.PlanError, "setup:duo of hub cannot be tried, its files do not load: Cannot be read: go-yaml"):
            e2e.plan("hub", built, everything=True)
        self.assertEqual(e2e.plan("base", built, everything=True)["count"], 6, "another catalog is not the one that is broken")

    def test_a_canary_or_a_named_item_that_does_not_load_says_so_and_not_that_it_is_gone(self):
        built = broken("hub", "playgrounds/bank/compose.yml")
        self.canary = ["playground:bank"]
        with self.assertRaisesRegex(e2e.PlanError, "playground:bank of hub cannot be tried, its files do not load"):
            e2e.plan("hub", built, canary_only=True)
        with self.assertRaisesRegex(e2e.PlanError, "playground:bank of hub cannot be tried"):
            e2e.plan("hub", built, changed=("abc", "HEAD"), git=self.git(["Makefile"]))
        with self.assertRaisesRegex(e2e.PlanError, "playground:bank of hub cannot be tried"):
            e2e.plan("hub", built, words=["addon:@hub/ledger", "playground:@hub/bank"])
        self.canary = ["addon:ledger"]
        self.assertEqual(self.items(e2e.plan("hub", built, canary_only=True)), ["addon:@hub/ledger"], "the canary does not need the others")
        self.assertEqual(self.items(e2e.plan("hub", built, words=["addon:@hub/ledger"])), ["addon:@hub/ledger"])
        with self.assertRaisesRegex(e2e.PlanError, "is not a shared item of hub"):
            e2e.plan("hub", built, words=["playground:ghost"])

    def test_a_change_of_the_engine_runs_the_canary_even_when_another_item_does_not_load(self):
        built = broken("hub", "addons/mailer/compose.yml")
        self.canary = ["addon:ledger"]
        found = e2e.plan("hub", built, changed=("abc", "HEAD"), engine=True, git=self.git(["seeder/seed.py"]))
        self.assertEqual(self.items(found), ["addon:@hub/ledger"])

    def test_what_does_not_load_is_read_from_the_problems_of_the_catalog(self):
        built = {"catalog_problems": {"hub": [
            {"file": "x", "display": "addons/mailer/compose.yml", "message": "Cannot be read: first\n\nline"},
            {"file": "x", "display": "addons/mailer/other.yml", "message": "second"},
            {"file": "x", "display": "catalog.yml", "message": "belongs to no item"},
            {"file": "x", "display": "addons/local/mine/compose.yml", "message": "belongs to a machine"},
            {"file": "x", "display": "playgrounds/bank/compose.yml", "message": "x" * 400},
            {"file": "x", "message": "no display"}], "base": [{"display": "addons/echo/compose.yml", "message": "m"}]}}
        found = e2e.unreadable_items(built, "hub")
        self.assertEqual(sorted(found), ["addon:mailer", "playground:bank"])
        self.assertEqual(found["addon:mailer"], "Cannot be read: first line", "the first problem, on one line")
        self.assertEqual(len(found["playground:bank"]), 300)
        self.assertEqual(e2e.unreadable_items(PROJECT, "hub"), {}, "a project that lists no problems")
        self.assertEqual(e2e.unreadable_items({"catalog_problems": None}, "hub"), {})
        self.assertEqual(e2e.unreadable_items(None, "hub"), {})

    def test_one_thing_is_asked_for(self):
        for how in ({}, {"everything": True, "canary_only": True}, {"changed": ("a", "b"), "words": ["addon:echo"]}):
            with self.subTest(how=how), self.assertRaisesRegex(e2e.PlanError, "Ask for one of"):
                e2e.plan("base", PROJECT, **how)

    def test_a_catalog_this_engine_does_not_have_is_said_with_how_to_link_it(self):
        with self.assertRaisesRegex(e2e.PlanError, "neither installed nor linked in this engine: link its folder first"):
            e2e.plan("other", PROJECT, everything=True)
        for ns in ("Hub", "../hub", "h", "a" * 40, "hub\n::error::x", "", None, 7):
            with self.subTest(ns=ns), self.assertRaises(e2e.PlanError) as caught:
                e2e.available(ns)
            self.assertNotIn("\n", str(caught.exception))

    def test_what_belongs_to_a_machine_is_not_a_catalog_to_try(self):
        with self.assertRaisesRegex(e2e.PlanError, "area of this machine"):
            e2e.plan("local", PROJECT, everything=True)


class Repositories(Case):
    """changed_files(): what git says a change touches, in a repository of the case."""

    def setUp(self):
        fixtures.need_git()
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.repo = gitfixture.make_repo(Path(holder.name) / "catalog.git", {"addons/echo/compose.yml": "a\n", "README.md": "r\n",
                                                                           "setups/pair.yml": "s\n"})
        self.base = gitfixture.tip(self.repo)

    def test_the_files_a_change_touches_since_it_left_the_base(self):
        head = gitfixture.commit(self.repo, {"addons/echo/compose.yml": "b\n", "playgrounds/new/compose.yml": "p\n"}, "Change")
        self.assertEqual(sorted(e2e.changed_files(self.repo, self.base, head)), ["addons/echo/compose.yml", "playgrounds/new/compose.yml"])
        self.assertEqual(e2e.changed_files(self.repo, self.base, self.base), [])

    def test_a_deleted_file_and_a_moved_one_count_at_both_ends(self):
        head = gitfixture.commit(self.repo, {"setups/pair.yml": None, "setups/duo.yml": "s\n"}, "Rename")
        self.assertEqual(sorted(e2e.changed_files(self.repo, self.base, head)), ["setups/duo.yml", "setups/pair.yml"])

    def test_a_file_with_a_space_or_a_quote_in_its_name_is_one_path(self):
        head = gitfixture.commit(self.repo, {'playgrounds/a b/x "y".md': "t\n", "playgrounds/é/x.md": "t\n"}, "Odd names")
        self.assertEqual(sorted(e2e.changed_files(self.repo, self.base, head)), ['playgrounds/a b/x "y".md', "playgrounds/é/x.md"])

    def test_a_branch_name_is_a_ref_and_a_commit_is_too(self):
        head = gitfixture.commit(self.repo, {"README.md": "r2\n"}, "Doc")
        self.assertEqual(e2e.changed_files(self.repo, self.base[:12], "main"), ["README.md"])
        self.assertEqual(e2e.changed_files(self.repo, "main~1", head), ["README.md"])

    def test_what_is_not_a_commit_or_a_branch_name_is_refused_before_git_hears_it(self):
        for ref in ("--output=/tmp/x", "-p", "a b", "a;b", "$(x)", "a\nb", "", None, "x" * 300, "a..b", "a...b"):
            with self.subTest(ref=ref), self.assertRaisesRegex(e2e.PlanError, "is not a commit or a branch|cannot tell"):
                e2e.changed_files(self.repo, ref, "main")
            with self.subTest(head=ref), self.assertRaisesRegex(e2e.PlanError, "is not a commit or a branch|cannot tell"):
                e2e.changed_files(self.repo, "main", ref)

    def test_a_commit_git_does_not_have_is_said_with_its_words(self):
        with self.assertRaisesRegex(e2e.PlanError, "git cannot tell what main changes since deadbeef"):
            e2e.changed_files(self.repo, "deadbeef", "main")

    def test_a_git_that_is_missing_is_slow_or_says_too_much_is_said_in_a_sentence(self):
        failures = [registry.CatalogError("git_missing", "Git is not installed.", 502), OSError("no such file"), gitx.OutputLimit("big")]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), mock.patch.object(gitx, "run", side_effect=failure):
                with self.assertRaisesRegex(e2e.PlanError, "git cannot tell what main changes since abc"):
                    e2e.changed_files(self.repo, "abc", "main")
        top = gitx.Result(0, b"true\n.\n", "")
        slow = gitx.Result(124, b"", "git did not answer within 60 s\nkilled")
        with mock.patch.object(gitx, "run", side_effect=[top, slow]), self.assertRaisesRegex(e2e.PlanError, "did not answer within 60 s killed"):
            e2e.changed_files(self.repo, "abc", "main")
        with mock.patch.object(gitx, "run", side_effect=[top, gitx.Result(128, b"", "")]), self.assertRaisesRegex(e2e.PlanError, "no reason given"):
            e2e.changed_files(self.repo, "abc", "main")

    def test_a_folder_inside_another_repository_is_not_read_as_the_repository_above_it(self):
        """A catalog installed from git is a copy with no history, in a folder of the engine: asking git there would answer for
        the engine's own."""
        working = self.repo.parent / "working"
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_AUTHOR_NAME": "t",
               "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        (working / "catalog" / "addons" / "echo").mkdir(parents=True)
        (working / "catalog" / "addons" / "echo" / "compose.yml").write_text("a\n", encoding="utf-8")
        for command in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "first"]):
            subprocess.run(["git", *command], cwd=working, env=env, check=True, capture_output=True)
        (working / "catalog" / "addons" / "echo" / "compose.yml").write_text("b\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-q", "-am", "second"], cwd=working, env=env, check=True, capture_output=True)
        self.assertEqual(e2e.changed_files(working, "main~1", "main"), ["catalog/addons/echo/compose.yml"], "the top of a working copy")
        with self.assertRaisesRegex(e2e.PlanError, "not a git repository of its own"):
            e2e.changed_files(working / "catalog", "main~1", "main")
        # a linked work tree, where .git is a file, is the top of a repository: it is how a maintainer keeps a catalog
        subprocess.run(["git", "worktree", "add", "-q", "-b", "other", str(working.parent / "linked")], cwd=working, env=env, check=True,
                       capture_output=True)
        self.assertEqual(e2e.changed_files(working.parent / "linked", "main~1", "main"), ["catalog/addons/echo/compose.yml"])
        (working.parent / "alias").symlink_to(working, target_is_directory=True)
        self.assertEqual(e2e.changed_files(working.parent / "alias", "main~1", "main"), ["catalog/addons/echo/compose.yml"],
                         "a link to the folder, as catalog.py link makes it, is the folder")
        with self.assertRaisesRegex(e2e.PlanError, "not a git repository of its own"):
            e2e.changed_files(self.repo.parent, "main~1", "main")   # no repository here, nor above

    def test_a_range_git_cannot_resolve_is_an_error_and_never_a_file_named_like_it(self):
        """In a repository where the base is not a branch (the default branch is not main, or the commit was not fetched), a
        committed file called like the range would be read by git as a path: the change would be empty, and nothing tried."""
        working = self.repo.parent / "ranged"
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_AUTHOR_NAME": "t",
               "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        (working / "origin").mkdir(parents=True)
        (working / "origin" / "main...HEAD").write_text("a\n", encoding="utf-8")
        for command in (["init", "-q", "-b", "trunk"], ["add", "-A"], ["commit", "-q", "-m", "first"]):
            subprocess.run(["git", *command], cwd=working, env=env, check=True, capture_output=True)
        with self.assertRaisesRegex(e2e.PlanError, "git cannot tell what HEAD changes since origin/main"):
            e2e.changed_files(working, "origin/main", "HEAD")
        self.assertEqual(e2e.changed_files(working, "HEAD", "HEAD"), [], "a range that does resolve is read as before")

    def test_a_folder_that_is_no_repository_is_said(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(e2e.PlanError, "not a git repository of its own"):
            e2e.changed_files(folder, "main", "main")


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class FixtureCatalogs(Case):
    """The plan on the fixture catalogs, read as the Lab UI reads a machine with catalogs: a catalog is linked, the project is
    loaded, the items are those of the units. Compose renders the files (no daemon)."""

    CANARY = ("x-gravitee-catalog:\n  schema: 1\n  id: fixture\n  name: Fixture catalog\n  description: Synthetic units for the tests of the "
              "Lab\n  version: 1.0.0\n  lab: 1\n  license: AGPL-3.0-only\n  canary:\n    - playground:tour\n    - addon:echo\n")

    def setUp(self):
        from lab_ui.gravitee import license   # the files of the project folder, not the machine's license
        fixtures.need_compose()
        fixtures.project(self, paths, project, compose, envfiles, e2e, files={"catalog.yml": self.CANARY})
        fixtures.install(self, "partner")
        self.patch(license, "status_of", lambda variable: None)
        project.forget()
        self.addCleanup(project.forget)
        self.built = project.load()

    def items(self, found):
        return [job["item"] for job in found["matrix"]["include"]]

    def test_any_item_of_the_project_is_one_to_try_and_a_machine_s_is_not(self):
        self.assertEqual(e2e.target("playground:tour", self.built), ("playground", "tour"))
        self.assertEqual(e2e.target("addon:@partner/ledger", self.built), ("addon", "@partner/ledger"))
        with self.assertRaisesRegex(ValueError, "No add-on named @partner/echo"):
            e2e.target("addon:@partner/echo", self.built)
        with self.assertRaises(ValueError) as caught:
            e2e.target("tour", self.built)
        self.assertRegex(str(caught.exception), r"for example playground:[a-z-]+$")

    def test_every_item_of_a_catalog_and_the_jobs_of_the_sets_of_versions_it_says(self):
        found = e2e.plan("fixture", self.built, everything=True)
        wanted = [f"{kind}:{uid}" for kind in ("addon", "setup", "playground") for uid, one in sorted(self.built[f"{kind}s"].items())
                  if one["catalog"] == "fixture"]
        self.assertEqual(sorted(set(self.items(found))), sorted(wanted))
        shop = [job for job in found["matrix"]["include"] if job["item"] == "playground:shop"]
        self.assertEqual([job["tested"] for job in shop], ["0"], "the one set of versions shop says")
        self.assertEqual(self.items(e2e.plan("partner", self.built, everything=True)), ["addon:@partner/ledger"])

    def test_the_canary_is_read_from_catalog_yml_of_the_linked_catalog(self):
        found = e2e.plan("fixture", self.built, canary_only=True)
        self.assertEqual(self.items(found), ["playground:tour", "addon:echo"])
        self.assertEqual(found["count"], 2)

    def test_a_change_that_touches_no_item_but_the_catalog_file_runs_that_canary(self):
        found = e2e.plan("fixture", self.built, changed=("a", "b"), git=lambda folder, base, head: ["catalog.yml"])
        self.assertEqual(self.items(found), ["playground:tour", "addon:echo"])

    def test_a_change_of_the_engine_runs_that_canary_too(self):
        found = e2e.plan(None, self.built, changed=("a", "b"), engine=True, git=lambda folder, base, head: ["docker-compose.yml"])
        self.assertEqual(self.items(found), ["playground:tour", "addon:echo"], "the default catalog of the project folder is fixture")

    def test_the_first_catalog_is_planned_with_bare_ids_and_the_others_with_their_catalog(self):
        self.assertTrue(all("@" not in item for item in self.items(e2e.plan("fixture", self.built, everything=True))))
        self.assertTrue(all("@partner/" in item for item in self.items(e2e.plan("partner", self.built, everything=True))))

    def test_a_catalog_that_is_not_linked_is_said(self):
        with self.assertRaisesRegex(e2e.PlanError, "neither installed nor linked"):
            e2e.plan("lab-new", self.built, everything=True)

    def test_an_item_whose_file_no_longer_loads_ends_the_plan_as_the_project_lists_it(self):
        """The shape of `catalog_problems` is the project's: what the plan reads of it is what Compose said of a real file."""
        fixtures.install(self, "fixture", files={"catalog.yml": self.CANARY, "addons/echo-twin/compose.yml": "x: [unclosed\n"})
        project.forget()
        built = project.load()
        self.assertNotIn("echo-twin", built["addons"], "the project has no such item, as for one the change deleted")
        found = e2e.unreadable_items(built, "fixture")
        self.assertEqual(list(found), ["addon:echo-twin"], found)
        self.assertRegex(found["addon:echo-twin"], r"^Cannot be read: ")
        git = lambda folder, base, head: ["addons/echo-twin/compose.yml"]   # noqa: E731
        with self.assertRaisesRegex(e2e.PlanError, "addon:echo-twin of fixture cannot be tried, its files do not load: Cannot be read: "
                                                   ".*Fix: catalog.py check fixture"):
            e2e.plan("fixture", built, changed=("a", "b"), git=git)
        with self.assertRaisesRegex(e2e.PlanError, "addon:echo-twin of fixture cannot be tried"):
            e2e.plan("fixture", built, everything=True)
        self.assertEqual(self.items(e2e.plan("fixture", built, canary_only=True)), ["playground:tour", "addon:echo"])


if __name__ == "__main__":
    unittest.main()
