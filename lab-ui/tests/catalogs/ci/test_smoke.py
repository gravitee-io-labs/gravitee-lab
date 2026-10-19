"""`catalog.py ci smoke` (SM01 to SM03): the services of a touched unit that need no platform start, stay up and answer their
health check; the seed and whatever depends on the platform are left to the end-to-end run; a unit that needs settings or a license
is not tried, and said so; the project is always taken down.

The units are given by the loader seam of `ci` (the reading of Compose belongs to the `ci` package and has its own tests), the project by a session
seam, and Docker is a `docker` that is not Docker (`tests/fakedocker.py`) on the PATH of the test, so what runs is the real stage
with real processes. One class runs the default session over the real Compose, when this machine has one."""

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.library import check, project
from lab_ui.docker import compose, envfiles
from lab_ui import paths
from lab_ui.catalogs import ci, explain, registry
from lab_ui.catalogs.ci import main as ci_main
from lab_ui.catalogs.ci import run, smoke
from tests import Case
from tests import fixtures
from tests.data.rules import support
from tests.fakedocker import FakeDocker

UP = ("compose", "up")
PS = ("compose", "ps")
DOWN = ("compose", "down")


def service(**more):
    return {"image": "example.test/lab/svc:1", **more}


def unit(kind="playground", name="bank", services=None, own=None, folder="", **more):
    """A loaded unit: its resolved services and the names of those it defines itself (all of them by default)."""
    services = services if services is not None else {"web": service()}
    file = ci.unit_path(kind, name)
    return ci.Loaded(kind, name, name, file, folder, {"services": services}, tuple(services if own is None else own), (), None,
                     **more)


def loader(*units, refused=()):
    asked = []

    def load(folder, names):
        asked.append((folder, list(names)))
        wanted = set(names)
        return ci.Loading([each for each in units if (each.kind, each.name) in wanted],
                          [each for each in refused if (each.kind, each.name) in wanted], [])
    load.asked = asked
    return load


def ps_line(name, state="running", health="", code=0):
    return json.dumps({"Service": name, "State": state, "Health": health, "ExitCode": code, "Name": f"p-{name}-1"})


def ps_lines(*rows):
    return "\n".join(ps_line(*row) if isinstance(row, tuple) else ps_line(row) for row in rows) + "\n"


class Selection(unittest.TestCase):
    """Which services of a unit are started, and in what order."""

    def chosen(self, services, own=None):
        return smoke.select(unit(services=services, own=own))

    def test_a_service_with_no_dependency_is_started(self):
        found = self.chosen({"bakery-mcp": service(healthcheck={"test": ["CMD", "true"]})})
        self.assertEqual((found.waves, found.left), ([["bakery-mcp"]], []))

    def test_the_seed_service_is_left_out_by_its_label(self):
        found = self.chosen({"bakery-mcp": service(), "bakery-seed": service(labels={"io.gravitee.role": "seed"})})
        self.assertEqual(found.waves, [["bakery-mcp"]])
        self.assertEqual(found.left, [("bakery-seed", "it is the seed, which needs the platform")])

    def test_the_labels_may_be_a_list_in_the_json_of_an_older_compose(self):
        found = self.chosen({"a": service(labels=["io.gravitee.role=seed"]), "b": service()})
        self.assertEqual(found.waves, [["b"]])

    def test_a_service_that_runs_once_starts_with_the_service_that_waits_for_it(self):
        done = {"condition": "service_completed_successfully"}
        found = self.chosen({"certs": service(), "db": service(),
                             "broker": service(depends_on={"certs": done, "db": {"condition": "service_healthy"}})})
        self.assertEqual(found.done, frozenset({"certs"}))
        self.assertEqual(found.waves, [["db"], ["broker", "certs"]])
        alone = self.chosen({"certs": service(), "broker": service(depends_on={"certs": done})})
        self.assertEqual(alone.waves, [["broker", "certs"]])
        self.assertEqual(self.chosen({"a": service(), "b": service(depends_on={"a": {"condition": "service_started"}})}).done, frozenset())

    def test_a_service_that_exits_without_a_license_is_left_out(self):
        found = self.chosen({"a": service(labels={"io.gravitee.license": "required"}), "b": service()})
        self.assertEqual((found.waves, found.left), ([["b"]], [("a", "it exits without a license")]))

    def test_a_service_that_depends_on_the_core_or_on_another_unit_is_left_to_the_end_to_end_run(self):
        services = {"agent": service(depends_on={"apim-gateway": {"condition": "service_healthy", "required": True}}),
                    "web": service(depends_on={"mock-bank": {"condition": "service_started", "required": True}}),
                    "mcp": service()}
        found = self.chosen(services)
        self.assertEqual(found.waves, [["mcp"]])
        self.assertEqual(dict(found.left)["agent"], "it depends on apim-gateway, which this unit does not run")
        self.assertEqual(dict(found.left)["web"], "it depends on mock-bank, which this unit does not run")

    def test_a_dependency_through_an_own_service_reaches_the_platform_too(self):
        services = {"a": service(depends_on={"b": {"condition": "service_started"}}),
                    "b": service(depends_on={"c": {"condition": "service_started"}}),
                    "c": service(depends_on={"mongodb": {"condition": "service_healthy"}}), "d": service()}
        found = self.chosen(services)
        self.assertEqual(found.waves, [["d"]])
        self.assertEqual([name for name, _ in found.left], ["a", "b", "c"])

    def test_a_service_that_waits_for_the_seed_is_left_out_with_it(self):
        services = {"seed": service(labels={"io.gravitee.role": "seed"}),
                    "after": service(depends_on={"seed": {"condition": "service_completed_successfully"}})}
        found = self.chosen(services)
        self.assertEqual(found.waves, [])
        self.assertEqual(dict(found.left)["after"], "it depends on seed, which is left out")

    def test_the_other_ties_between_services_count_as_dependencies(self):
        for key, value in (("links", ["mongodb:db"]), ("volumes_from", ["mongodb:ro"]), ("network_mode", "service:apim-gateway"),
                           ("pid", "service:apim-gateway"), ("ipc", "container:other")):
            with self.subTest(key=key):
                found = self.chosen({"a": service(**{key: value}), "b": service()})
                self.assertEqual(found.waves, [["b"]])

    def test_the_services_start_in_waves_one_for_each_level_of_depends_on(self):
        services = {"web": service(depends_on={"api": {"condition": "service_healthy"}}),
                    "api": service(depends_on={"db": {"condition": "service_healthy"}, "cache": {"condition": "service_started"}}),
                    "db": service(), "cache": service(), "solo": service()}
        found = self.chosen(services)
        self.assertEqual([sorted(wave) for wave in found.waves], [["cache", "db", "solo"], ["api"], ["web"]])

    def test_a_cycle_is_not_started(self):
        found = self.chosen({"a": service(depends_on={"b": {}}), "b": service(depends_on={"a": {}}), "c": service()})
        self.assertEqual(found.waves, [["c"]])
        self.assertEqual(dict(found.left)["a"], "its dependencies form a cycle")

    def test_a_unit_of_many_services_costs_in_proportion_to_their_number(self):
        count = 20000
        chain = {f"s{number}": service(depends_on={f"s{number + 1}": {"condition": "service_started"}}) for number in range(count - 1)}
        chain[f"s{count - 1}"] = service(depends_on={"apim-gateway": {"condition": "service_healthy"}})   # (the end of the chain needs the core)
        fan = {f"f{number}": service() for number in range(count)}
        ladder = {f"l{number}": service(depends_on={f"l{number - 1}": {"condition": "service_started"}} if number else None)
                  for number in range(count)}
        for label, services, waves in (("chain to the core", chain, 0), ("fan", fan, 1), ("ladder", ladder, count)):
            with self.subTest(shape=label):
                found, spent = support.timed(smoke.select, unit(services=services))
                self.assertLess(spent, 2.0)
                self.assertEqual(len(found.waves), waves)
        self.assertEqual(len(smoke.select(unit(services=chain)).left), count)

    def test_only_the_services_the_unit_defines_itself_are_considered(self):
        services = {"apim-gateway": service(), "web": service()}
        found = self.chosen(services, own=["web"])
        self.assertEqual(found.waves, [["web"]])

    def test_a_name_that_could_start_a_workflow_command_is_never_given_to_docker(self):
        found = self.chosen({"a\n::error::x": service(), "ok": service()})
        self.assertEqual(found.waves, [["ok"]])
        self.assertEqual(found.left[0][1], "it cannot be read")

    def test_the_project_name_is_stable_and_short_enough_for_a_container(self):
        self.assertEqual(smoke.project_name(unit(name="bank")), "catalog-smoke-playground-bank")
        long = smoke.project_name(unit(kind="addon", name="a" * 100))
        self.assertRegex(long, r"catalog-smoke-[0-9a-f]{12}")
        self.assertEqual(long, smoke.project_name(unit(kind="addon", name="a" * 100)))


class Reading(unittest.TestCase):

    def test_requires_license_reads_the_unit_file_as_text(self):
        for text, expected in (
                ("x-gravitee:\n  requires:\n    license: [apim-x]\nservices: {}\n", True),
                ("x-gravitee:\n  requires:\n    license:\n      apim-x: step 4\n", True),
                ("x-gravitee:\n  requires:\n    license:\n      - apim-x\n", True),
                ("x-gravitee: {requires: {license: [apim-x]}}\n", True),
                ("x-gravitee:\n  requires:\n    license: []\n", False),
                ("x-gravitee:\n  requires:\n    images: {APIM_VERSION: '4.13'}\n", False),
                ("x-gravitee:\n  name: Bank\n", False), ("services: {}\n", False), ("", False), (None, False)):
            with self.subTest(text=text):
                self.assertEqual(smoke.requires_license(text), expected)

    def test_a_line_of_a_megabyte_is_read_in_under_a_second(self):
        for text in ("x-gravitee: {" + "[" * 1_000_000, "x-gravitee:\n  requires:\n    license: " + "[" * 1_000_000,
                     "x-gravitee: {requires: {license: " + "a" * 60_000 + "}}", "services:\n" + "  a: &x\n" * 100_000,
                     "x-gravitee:\n" + "  - \n" * 200_000, "x" * 1_000_000):
            with self.subTest(start=text[:24]):
                _, spent = support.timed(smoke.requires_license, text)
                self.assertLess(spent, 1.0)

    def test_a_unit_refused_for_a_setting_it_lacks_is_not_a_fault_of_the_unit(self):
        needs = ci.Refused("addon", "x", "addons/x/compose.yml", (
            "Cannot be read: error while interpolating services.web.environment.TOKEN: required variable TOKEN is missing a "
            "value: set TOKEN",))
        blocked = ci.Refused("addon", "y", "addons/y/compose.yml", ("Mounts the Docker socket",), "T10")
        broken = ci.Refused("addon", "z", "addons/z/compose.yml", ("Cannot be read: services.a.image must be a string",))
        forged = ci.Refused("addon", "w", "addons/w/compose.yml", (
            'Cannot be read: service "required variable X is missing a value" depends on undefined service "y"',))
        forged_too = ci.Refused("addon", "v", "addons/v/compose.yml", (
            "Cannot be read: services.a.image must be a string: error while interpolating a.b: required variable X is missing a value",))
        self.assertTrue(smoke.needs_settings(needs))
        for refused in (blocked, broken, forged, forged_too):
            self.assertFalse(smoke.needs_settings(refused), refused.reasons)
        self.assertFalse(smoke.needs_settings(ci.Refused("addon", "w", "w", ())))

    def test_the_rows_of_ps_may_be_lines_or_an_array(self):
        lines = ps_lines("a", ("b", "exited", "", 3))
        array = json.dumps([json.loads(line) for line in lines.splitlines()])
        for text in (lines, array, lines + "not json\n"):
            with self.subTest(text=text[:20]):
                found = smoke.state_of(smoke.ps_rows(text), ["a", "b", "c"])
                self.assertEqual(found, {"a": ("running", "", 0), "b": ("exited", "", 3), "c": ("missing", "", 0)})
        self.assertEqual(smoke.ps_rows(""), [])
        self.assertEqual(smoke.ps_rows("[1, 2, {\"Service\": \"a\"}]"), [{"Service": "a"}])

    def test_the_messages_are_the_words_of_the_standard(self):
        self.assertEqual(smoke.finding("SM01", "p", service="bakery-mcp").message, "bakery-mcp was not healthy after 240 s")
        self.assertEqual(smoke.finding("SM02", "p", service="bakery-mcp", code=3).message, "bakery-mcp exited with 3")
        sm03 = smoke.finding("SM03", "p", unit="addon:hybrid")
        self.assertEqual(sm03.message, "addon:hybrid was not tried: it needs settings or a license.")
        self.assertEqual([smoke.finding(code, "p", service="s", code=1, unit="u").severity for code in ("SM01", "SM02", "SM03")],
                         ["error", "error", "warning"])
        table = {code: (severity, message) for code, severity, _, message in explain.STAGES if code.startswith("SM")}
        for code, (severity, message) in smoke.MESSAGES.items():
            self.assertEqual((severity, message), table[code])


class Stage(Case):
    """The stage over a docker that is not Docker."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.work = Path(holder.name).resolve()
        self.catalog = self.work / "catalog"
        self.catalog.mkdir()
        self.project = self.work / "project"
        self.project.mkdir()
        self.fake = FakeDocker(self)
        (self.fake.folder / "real.json").write_text("null", encoding="utf-8")   # (no call of this class reaches the real Docker)
        self.out, self.err = io.StringIO(), io.StringIO()
        self.slept = []

    def session(self, folder):
        @contextlib.contextmanager
        def opened():
            yield smoke.Session(str(self.project), lambda each: ["-f", "docker-compose.yml", "-f", f"on-{each.name}.yml"])
        return opened()

    def run_stage(self, *units, refused=(), only=None, extra=(), files=None):
        for path, text in (files or {}).items():
            file = self.catalog / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text, encoding="utf-8")
        wanted = only if only is not None else [f"{each.kind}:{each.name}" for each in units]
        wanted += [f"{each.kind}:{each.name}" for each in refused]
        with mock.patch.object(ci, "LOADER", loader(*units, refused=refused)):
            return smoke.main([str(self.catalog), "--units", json.dumps(wanted), *extra], self.out, self.err,
                              session=self.session, sleep=self.slept.append)

    def up_calls(self):
        return [call for call in self.fake.commands(*UP)]

    def test_a_unit_whose_service_comes_up_healthy_passes_and_the_project_is_taken_down(self):
        self.fake.when(*PS, stdout=ps_lines(("bakery-mcp", "running", "healthy")))
        bank = unit(services={"bakery-mcp": service(healthcheck={"test": ["CMD", "true"]}),
                              "bakery-seed": service(labels={"io.gravitee.role": "seed"}),
                              "bakery-agent": service(depends_on={"apim-gateway": {"condition": "service_healthy"}})})
        code = self.run_stage(bank)
        self.assertEqual(code, 0, self.err.getvalue() + self.out.getvalue())
        self.assertEqual(self.up_calls(), [[
            *compose.COMPOSE[1:], "-p", "catalog-smoke-playground-bank", "-f", "docker-compose.yml", "-f", "on-bank.yml", "up", "-d",
            "--wait", "--wait-timeout", "240", "--no-build", "--pull", "missing", "--no-deps", "bakery-mcp"]])
        self.assertEqual(self.fake.commands(*DOWN), [[
            *compose.COMPOSE[1:], "-p", "catalog-smoke-playground-bank", "-f", "docker-compose.yml", "-f", "on-bank.yml", "down",
            "-v", "--remove-orphans"]])
        text = self.out.getvalue()
        self.assertIn("Smoke: 1 started, 0 failed, 0 not tried, 0 with nothing to start without the platform", text)
        self.assertRegex(text, r"✓ playground:bank  bakery-mcp started and answered \(\d+ s\)")
        self.assertEqual(self.slept, [smoke.SETTLE])   # (the services are looked at again once they are up)
        self.assertEqual(self.fake.calls()[-1].cwd, str(self.project))

    def test_the_services_of_a_unit_start_wave_by_wave_in_the_order_of_their_dependencies(self):
        self.fake.when(*PS, stdout=ps_lines("db", "web"))
        web = unit(services={"web": service(depends_on={"db": {"condition": "service_healthy"}}), "db": service()})
        self.assertEqual(self.run_stage(web), 0)
        ups = self.up_calls()
        self.assertEqual([call[-1] for call in ups], ["db", "web"])
        self.assertTrue(all("--no-deps" in call for call in ups))

    def test_a_service_that_runs_once_is_well_when_it_exited_with_0_and_not_when_it_failed(self):
        done = {"condition": "service_completed_successfully"}
        services = {"certs": service(), "broker": service(depends_on={"certs": done})}
        self.fake.when(*PS, stdout=ps_lines(("certs", "exited", "", 0), ("broker", "running", "healthy")))
        self.assertEqual(self.run_stage(unit(services=services)), 0)
        self.assertIn("broker, certs started and answered", self.out.getvalue())

    def test_a_service_that_runs_once_and_exited_with_an_error_is_sm02(self):
        done = {"condition": "service_completed_successfully"}
        services = {"certs": service(), "broker": service(depends_on={"certs": done})}
        self.fake.when(*UP, code=1, stderr="container p-certs-1 exited (2)")
        self.fake.when(*PS, stdout=ps_lines(("certs", "exited", "", 2), ("broker", "created", "")))
        self.fake.when("compose", "logs", stdout="no\n")
        self.assertEqual(self.run_stage(unit(services=services), extra=["--format", "json"]), 1)
        self.assertIn("certs exited with 2", self.out.getvalue())

    def test_a_service_that_is_not_healthy_is_sm01_with_its_log_and_the_project_is_taken_down(self):
        self.fake.when(*UP, code=1, stderr="container p-bakery-mcp-1 is unhealthy")
        self.fake.when(*PS, stdout=ps_lines(("bakery-mcp", "running", "unhealthy")))
        self.fake.when("compose", "logs", stdout="line one\nline two\n::error::forged\n")
        code = self.run_stage(unit(services={"bakery-mcp": service()}), extra=["--format", "github"])
        self.assertEqual(code, 1)
        text = self.out.getvalue()
        self.assertIn("::error file=playgrounds/bank/compose.yml,title=SM01::bakery-mcp was not healthy after 240 s", text)
        self.assertEqual(len(self.fake.commands(*DOWN)), 1)
        self.assertIn("The last 3 lines of the log of playground:bank bakery-mcp:", text)
        match = re.search(r"::stop-commands::([0-9a-f]+)\nline one\nline two\n::error::forged\n::\1::\n", text)
        self.assertIsNotNone(match, "what the container printed is between stop-commands and its token")
        self.assertEqual(self.fake.commands("compose", "logs")[0][-3:], ["--tail", "60", "bakery-mcp"])

    def test_a_service_that_exited_is_sm02_with_its_code(self):
        self.fake.when(*UP, code=1, stderr="dependency failed to start")
        self.fake.when(*PS, stdout=ps_lines(("bakery-mcp", "exited", "", 137)))
        self.fake.when("compose", "logs", stdout="Killed\n")
        code = self.run_stage(unit(services={"bakery-mcp": service()}), extra=["--format", "json"])
        self.assertEqual(code, 1)
        found = json.loads(self.out.getvalue())
        self.assertEqual([(each["code"], each["message"]) for each in found], [("SM02", "bakery-mcp exited with 137")])
        self.assertEqual(found[0]["path"], "playgrounds/bank/compose.yml")

    def test_a_service_that_dies_after_it_started_is_found_when_the_wave_is_looked_at_again(self):
        self.fake.when(*PS, stdout=ps_lines(("bakery-mcp", "exited", "", 1)))
        self.fake.when("compose", "logs", stdout="boom\n")
        code = self.run_stage(unit(services={"bakery-mcp": service()}), extra=["--format", "json"])
        self.assertEqual(code, 1)
        self.assertEqual([(each["code"], each["message"]) for each in json.loads(self.out.getvalue())],
                         [("SM02", "bakery-mcp exited with 1")])
        self.assertEqual(self.slept, [smoke.SETTLE])

    def test_a_first_wave_that_fails_stops_the_next_ones_and_the_project_still_goes_down(self):
        self.fake.when(*UP, code=1, stderr="unhealthy")
        self.fake.when(*PS, stdout=ps_lines(("db", "running", "unhealthy")))
        web = unit(services={"web": service(depends_on={"db": {"condition": "service_healthy"}}), "db": service()})
        self.assertEqual(self.run_stage(web), 1)
        self.assertEqual([call[-1] for call in self.up_calls()], ["db"])
        self.assertEqual(len(self.fake.commands(*DOWN)), 1)

    def test_a_crash_of_the_stage_itself_still_takes_the_project_down(self):
        def explode(*args, **kwargs):
            raise RuntimeError("the stage failed in the middle")
        with mock.patch.object(smoke, "start_unit", explode), self.assertRaises(RuntimeError):
            self.run_stage(unit(services={"bakery-mcp": service()}))
        self.assertEqual(len(self.fake.commands(*DOWN)), 1)

    def test_an_interrupt_still_takes_the_project_down(self):
        with mock.patch.object(smoke, "start_unit", side_effect=KeyboardInterrupt):
            self.assertEqual(self.run_stage(unit(services={"bakery-mcp": service()})), 130)
        self.assertEqual(len(self.fake.commands(*DOWN)), 1)

    def test_a_down_that_fails_is_said_and_is_no_verdict(self):
        self.fake.when(*PS, stdout=ps_lines("bakery-mcp"))
        self.fake.when(*DOWN, code=1, stderr="Error response from daemon: network has active endpoints")
        self.assertEqual(self.run_stage(unit(services={"bakery-mcp": service()})), 0)
        self.assertIn("could not remove its project catalog-smoke-playground-bank: Error response from daemon", self.err.getvalue())

    def test_an_image_that_is_not_here_is_no_verdict_on_the_unit(self):
        self.fake.when(*UP, code=1, stderr="Error response from daemon: pull access denied for lab/svc, repository does not exist")
        self.fake.when(*PS, stdout="")
        code = self.run_stage(unit(services={"bakery-mcp": service()}))
        self.assertEqual(code, 2)
        self.assertIn("is not here and cannot be pulled", self.err.getvalue())
        self.assertIn("build it first (catalog.py ci build)", self.err.getvalue())
        self.assertEqual(len(self.fake.commands(*DOWN)), 1)

    def test_a_docker_that_does_not_run_is_said_and_nothing_is_started(self):
        self.fake.when("version", code=1, stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?")
        self.assertEqual(self.run_stage(unit(services={"bakery-mcp": service()})), 2)
        self.assertIn("Docker is not running", self.err.getvalue())
        self.assertEqual(self.up_calls(), [])

    def test_a_unit_that_needs_settings_is_not_tried_and_is_never_a_pass(self):
        needs = ci.Refused("addon", "apigee", "addons/apigee/compose.yml", (
            "Cannot be read: error while interpolating services.a.environment.KEY: required variable KEY is missing a value",))
        code = self.run_stage(refused=[needs], extra=["--format", "json"])
        self.assertEqual(code, 0)   # (a warning: it is said, it fails nothing)
        found = json.loads(self.out.getvalue())
        self.assertEqual([(each["code"], each["severity"], each["message"]) for each in found],
                         [("SM03", "warning", "addon:apigee was not tried: it needs settings or a license.")])
        self.assertEqual(self.fake.calls(), [])   # (not even a docker version: there was nothing to start)

    def test_a_unit_that_needs_a_license_is_not_tried(self):
        text = "x-gravitee:\n  requires:\n    license: [apim-x]\nservices:\n  a:\n    image: example.test/a:1\n"
        code = self.run_stage(unit(kind="addon", name="lic", services={"a": service()}), files={"addons/lic/compose.yml": text})
        self.assertEqual(code, 0)
        text = self.out.getvalue()
        self.assertIn("Smoke: 0 started, 0 failed, 1 not tried", text)
        self.assertRegex(text, r"- addon:lic  it needs settings or a license")
        self.assertNotIn("✓", text)
        self.assertEqual(self.up_calls(), [])

    def test_a_unit_with_nothing_that_starts_without_the_platform_is_left_to_the_end_to_end_run(self):
        agent = unit(services={"bakery-agent": service(depends_on={"apim-gateway": {"condition": "service_healthy"}}),
                               "bakery-seed": service(labels={"io.gravitee.role": "seed"})})
        self.assertEqual(self.run_stage(agent, unit(kind="setup", name="pair", services={}, own=())), 0)
        text = self.out.getvalue()
        self.assertIn("2 with nothing to start without the platform", text)
        self.assertRegex(text, r"- playground:bank  bakery-agent: it depends on apim-gateway, which this unit does not run")
        self.assertRegex(text, r"- setup:pair  it only includes other units")
        self.assertEqual(self.fake.calls(), [])

    def test_a_unit_the_trust_policy_blocks_is_an_error_and_is_never_started(self):
        blocked = ci.Refused("addon", "bad", "addons/bad/compose.yml", ("Mounts the Docker socket",), "T10")
        self.assertEqual(self.run_stage(refused=[blocked], extra=["--format", "json"]), 1)
        found = json.loads(self.out.getvalue())
        self.assertEqual([(each["code"], each["path"]) for each in found], [("T10", "addons/bad/compose.yml")])
        self.assertEqual(self.fake.calls(), [])

    def test_the_bind_folders_a_unit_keeps_its_state_in_are_made_before_it_starts(self):
        folder = self.catalog / "playgrounds" / "bank"
        folder.mkdir(parents=True)
        mounted = {"bakery-mcp": service(volumes=[
            {"type": "bind", "source": "/tmp/gone-0/.lab/catalogs/acme-hub/playgrounds/bank/.state/mcp", "target": "/state"},
            {"type": "bind", "source": "/tmp/gone-0/.lab/catalogs/acme-hub/playgrounds/bank/seed", "target": "/seed"},
            {"type": "bind", "source": "/etc/hosts", "target": "/etc/hosts"}])}
        self.fake.when(*PS, stdout=ps_lines("bakery-mcp"))
        self.assertEqual(self.run_stage(unit(services=mounted, folder=str(folder))), 0)
        self.assertTrue((folder / ".state" / "mcp").is_dir())
        self.assertEqual(oct((folder / ".state" / "mcp").stat().st_mode & 0o777), "0o777")
        self.assertEqual(oct((folder / ".state").stat().st_mode & 0o777), "0o777")
        self.assertFalse((folder / "seed").exists(), "only the state is made: the rest is the unit's own")

    def test_a_unit_that_names_a_great_many_state_folders_has_a_hundred_made_and_costs_one_pass(self):
        mounts = [{"type": "bind", "source": f"/tmp/gone-0/.lab/catalogs/acme-hub/playgrounds/bank/.state/d{number}", "target": "/x"}
                  for number in range(50_000)]
        found, spent = support.timed(smoke.state_folders, unit(services={"a": service(volumes=mounts)}), ["a"])
        self.assertEqual(len(found), smoke.STATE_FOLDERS)
        self.assertLess(spent, 1.0)

    def test_a_link_in_the_way_of_a_state_folder_is_not_followed(self):
        folder = self.catalog / "playgrounds" / "bank"
        folder.mkdir(parents=True)
        elsewhere = self.work / "elsewhere"
        elsewhere.mkdir()
        os.symlink(elsewhere, folder / ".state")
        made = smoke.make_state(unit(folder=str(folder)), [".state/inside", ".state"])
        self.assertEqual(made, [])
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_no_secret_and_no_variable_of_the_runner_reaches_docker(self):
        self.fake.when(*PS, stdout=ps_lines("bakery-mcp"))
        names = {"ACTIONS_RUNTIME_TOKEN": "a", "GITHUB_TOKEN": "b", "GH_TOKEN": "c", "SSH_AUTH_SOCK": "/s", "LAB_DEPLOY_KEY": "d",
                 "MY_SECRET": "e"}
        with mock.patch.dict(os.environ, names):
            self.assertEqual(self.run_stage(unit(services={"bakery-mcp": service()})), 0)
        self.assertTrue(self.fake.calls())
        for call in self.fake.calls():
            self.assertFalse(set(call.env) & set(names), f"{call.argv[:3]} got {set(call.env) & set(names)}")
            self.assertFalse([name for name in call.env if name.startswith(("ACTIONS_", "GITHUB_", "RUNNER_"))])

    def test_the_summary_of_a_ci_run_lists_each_unit_and_the_logs_fenced(self):
        summary = self.work / "summary.md"
        self.fake.when(*UP, code=1, stderr="unhealthy")
        self.fake.when(*PS, stdout=ps_lines(("bakery-mcp", "running", "unhealthy")))
        self.fake.when("compose", "logs", stdout="``` fence\nbreak\n")
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
            code = self.run_stage(unit(services={"bakery-mcp": service()}), extra=["--ci"])
        self.assertEqual(code, 1)
        text = summary.read_text(encoding="utf-8")
        self.assertIn("### Units started", text)
        self.assertRegex(text, r"\| playground:bank \|  \| failed \| \d+ s \|")
        self.assertIn("````\n``` fence\nbreak\n````", text)   # (a fence longer than any run of backticks in the log)
        self.assertIn("| ✗ | playgrounds/bank/compose.yml | SM01 | bakery-mcp was not healthy after 240 s |", text)
        self.assertIn("::error file=playgrounds/bank/compose.yml,title=SM01::bakery-mcp was not healthy after 240 s", self.out.getvalue())

    def test_a_hostile_service_name_in_the_json_cannot_reach_the_output_or_the_command(self):
        hostile = unit(services={"web\n::error::x": service(), "ok": service()})
        self.fake.when(*PS, stdout=ps_lines("ok"))
        self.assertEqual(self.run_stage(hostile), 0)
        for call in self.fake.calls():
            self.assertFalse([word for word in call.argv if "::error::" in word])
        self.assertNotIn("::error::x", self.out.getvalue())


class Dispatch(unittest.TestCase):

    def test_the_runner_of_make_ci_finds_this_stage_and_gives_it_what_it_gives_the_others(self):
        self.assertEqual(ci.STAGES["smoke"], ("smoke", "main"))
        self.assertIs(run.stage_function("smoke"), smoke.main)
        self.assertIn("smoke", run.ORDER)
        self.assertEqual(run.SMOKE_LEVEL, 2)

    def test_the_command_line_reaches_the_stage_and_says_a_usage_error_with_exit_2(self):
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(ci_main(["smoke", "--units", "not json"], out, err), 2)
        self.assertIn("--units is not JSON", err.getvalue())
        err = io.StringIO()
        self.assertEqual(ci_main(["smoke", "--units", '["playground:Bank"]'], io.StringIO(), err), 2)
        self.assertEqual(ci_main(["smoke"], io.StringIO(), io.StringIO()), 2)


class MakeCi(support.WithGit):
    """`make ci` from level 2: the runner of `ci` gives this stage the units it planned, with no line of its own to change."""

    def test_ci_run_starts_what_changed_when_the_standard_has_the_stage(self):
        repo = self.work(files={"catalog.yml": support.CATALOG, "addons/echo/compose.yml": "services: {}\n"})
        first = self.git(repo, "rev-parse", "HEAD").strip()
        self.git(repo, "update-ref", "refs/remotes/origin/main", first)
        self.put(repo, "addons/echo/README.md", "# Echo\n")
        self.commit(repo, "Write the readme")
        fake = FakeDocker(self)
        (fake.folder / "real.json").write_text("null", encoding="utf-8")
        fake.when("compose", "ps", stdout=ps_lines("web"))
        seen = []

        def quiet(name):
            def stage(argv, out, err, **seams):
                seen.append(name)
                return 0
            return stage
        project = Path(self.holder, "project")
        project.mkdir()

        @contextlib.contextmanager
        def session(folder):
            yield smoke.Session(str(project), lambda each: ["-f", "docker-compose.yml", "-f", "on.yml"])
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(run, "level", return_value=2), mock.patch.object(ci, "LOADER", loader(unit("addon", "echo"))):
            code = run.main([str(repo)], out, err, stages={name: quiet(name) for name in ("manifests", "build", "test", "gitleaks")},
                            session=session, sleep=lambda seconds: None)
        self.assertEqual(code, 0, out.getvalue() + err.getvalue())
        self.assertRegex(out.getvalue(), r"== smoke\n")
        self.assertRegex(out.getvalue(), r"✓ smoke\s+passed")
        self.assertEqual(fake.commands("compose", "up")[0][-1], "web")
        self.assertEqual(len(fake.commands("compose", "down")), 1)
        self.assertEqual(seen, ["manifests", "gitleaks"], "(nothing builds: the change is a readme)")


class Real(Case):
    """The default session over the real Compose and the fixture catalog: the folder is read in a temporary project of this run, the
    units are the Lab's own reading of them, and `up` is the fake docker's."""

    def setUp(self):
        fixtures.need_compose()   # (a skip that shows without Compose 5)
        fixtures.project(self, paths, check, project, compose, envfiles, ns="fixture", link=True)
        fixtures.install(self, "partner")   # (the catalog it includes from has to be here, as it has for the check)
        self.folder = next(one["path"] for one in registry.entries() if one["id"] == "fixture")
        self.fake = FakeDocker(self)
        self.guard(self.fake)
        self.out, self.err = io.StringIO(), io.StringIO()

    @staticmethod
    def guard(fake):
        """Compose may read files here (`config`, `version`: no daemon, nothing started) and nothing else reaches the real Docker: the
        calls a stage makes to start, look at and remove containers are the fake's, and anything it has no answer for is refused."""
        real = json.loads((fake.folder / "real.json").read_text(encoding="utf-8"))
        script = fake.folder / "guard.sh"
        script.write_text('#!/bin/sh\nfor word in "$@"; do\n  case "$word" in config | version) exec "%s" "$@" ;; esac\ndone\n'
                          'echo "this test does not run: $*" >&2\nexit 97\n' % real, encoding="utf-8")
        script.chmod(0o755)
        (fake.folder / "real.json").write_text(json.dumps(str(script)), encoding="utf-8")
        for words in (UP, DOWN, ("compose", "logs")):
            fake.when(*words)

    def test_an_addon_that_needs_nothing_starts_in_a_temporary_project_and_the_unit_that_needs_a_license_is_not_tried(self):
        self.fake.when(*PS, stdout=ps_lines("fixture-echo"))
        before = (registry.entries(), registry.read_lock(), sorted(os.listdir(Path(paths.ROOT) / paths.CATALOGS_DIR)))
        code = smoke.main([self.folder, "--units", '["addon:echo", "playground:shop"]', "--format", "json"], self.out, self.err,
                          sleep=lambda seconds: None)
        self.assertEqual((code, self.err.getvalue()), (0, ""))
        self.assertEqual([(each["code"], each["message"]) for each in json.loads(self.out.getvalue())],
                         [("SM03", "playground:shop was not tried: it needs settings or a license.")])
        up = self.fake.commands(*UP)
        self.assertEqual(len(up), 1)
        self.assertEqual(up[0][up[0].index("-p") + 1], "catalog-smoke-addon-echo")
        self.assertEqual(up[0][-1], "fixture-echo")
        self.assertEqual(up[0][up[0].index("-f") + 1], "docker-compose.yml")
        # the project the services started in is the temporary one of this run, and it is gone
        calls = [call for call in self.fake.calls() if call.argv[:2] == ["compose", "--progress"]
                 and {"up", "ps", "down", "logs"} & set(call.argv)]
        projects = {call.cwd for call in calls}
        self.assertEqual(len(projects), 1)
        folder = projects.pop()
        self.assertIn("gravitee-lab-check-", folder)
        self.assertFalse(os.path.exists(folder))
        self.assertEqual(len(self.fake.commands(*DOWN)), 1)
        self.assertEqual((registry.entries(), registry.read_lock(), sorted(os.listdir(Path(paths.ROOT) / paths.CATALOGS_DIR))),
                         before, "nothing is linked into the Lab")

    def test_the_file_the_services_start_from_includes_the_unit_where_the_lab_reads_it(self):
        seen = {}

        def look(argv, timeout, cwd):
            if "up" in argv:
                files = [argv[number + 1] for number, word in enumerate(argv) if word == "-f"]
                seen["files"] = files
                seen["text"] = Path(files[-1]).read_text(encoding="utf-8")
                seen["cwd"] = cwd
            return 0, ps_lines("fixture-echo") if "ps" in argv else "", ""
        smoke.main([self.folder, "--units", '["addon:echo"]'], self.out, self.err, sleep=lambda seconds: None, runner=look)
        self.assertEqual(seen["files"][0], "docker-compose.yml")
        self.assertIn("/.lab/catalogs/fixture/addons/echo/compose.yml", seen["text"])
        self.assertTrue(str(seen["cwd"]).startswith(str(Path(tempfile.gettempdir()).resolve())) or "gravitee-lab-check-" in str(seen["cwd"]))


if __name__ == "__main__":
    unittest.main()
