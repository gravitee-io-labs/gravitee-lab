"""The controls: the protections a playground's lessons teach. The declaration is read strictly (a typo is an error, never a
control that switches nothing), the mode is `on` or `off` and nothing else, and a flow or a step that matches nothing is an
error."""

import json
from unittest import mock

from lab_seeder import common
from lab_seeder import controls
from lab_seeder import seed
from lab_seeder.testkit import Seed

POLICY = "Agents move at most EUR 500"


def write(*lines):
    return "\n".join(lines) + "\n"


FLOWS = [{"name": "Prompt", "request": [{"name": "Guard"}, {"name": "Mask", "enabled": False}, {"name": "Cache"}],
          "response": [{"name": "Mask"}]},
         {"name": "Other", "request": [{"name": "Guard"}]}]
GUARD = controls.Control("guard", {"api": "LLM", "flow": "Prompt", "steps": {"request": ["Guard"]}})


class Declaration(Seed):

    def declare(self, text):
        self.seed_files({"controls.yaml": text})
        return controls.declared()

    def test_a_seed_without_controls_declares_none_and_reads_no_setting(self):
        self.env(SEED_PROTECTIONS="sometimes")
        self.assertEqual(controls.declared(), [])
        self.assertEqual(controls.begin(), [])
        self.assertEqual(controls.of("api", "LLM"), [])
        controls.finish()

    def test_a_control_has_one_target_and_the_steps_it_switches(self):
        found = self.declare(write("pii:", "  api: LLM", "  flow: Prompt", "  steps:", "    request: [A, B]", "    response: [C]",
                                   "tools:", "  mcp-proxy: MCP", "  flow: Visible", "  steps: {request: [D]}",
                                   "router:", "  workspace: Teams", "  steps: {request: [E]}",
                                   "cap:", "  policy: A rule"))
        self.assertEqual([(c.id, c.kind, c.target, c.flow, c.steps) for c in found], [
            ("pii", "api", "LLM", "Prompt", {"request": ["A", "B"], "response": ["C"]}),
            ("tools", "mcp-proxy", "MCP", "Visible", {"request": ["D"]}),
            ("router", "workspace", "Teams", None, {"request": ["E"]}),
            ("cap", "policy", "A rule", None, {})])

    def test_a_declaration_that_is_not_understood_is_an_error_that_names_the_control(self):
        for text, message in (
                ("x: [1]", "controls.yaml: x: expected the target"),
                ("x: {api: A, flow: F, steps: {request: [S]}, stpes: 1}", "x: stpes is not a key of a control"),
                ("x: {flow: F, steps: {request: [S]}}", "x: one target is needed"),
                ("x: {api: A, policy: P, flow: F, steps: {request: [S]}}", "x: one target is needed"),
                ("x: {api: '', flow: F, steps: {request: [S]}}", "x: api is the name of the api"),
                ("x: {api: A, steps: {request: [S]}}", "x: flow is the name of the flow"),
                ("x: {api: A, flow: F}", "x: steps are request and/or response"),
                ("x: {api: A, flow: F, steps: {requests: [S]}}", "x: steps are request and/or response"),
                ("x: {api: A, flow: F, steps: {request: []}}", "x: steps.request is a list of step names"),
                ("x: {api: A, flow: F, steps: {request: [1]}}", "x: steps.request is a list of step names"),
                ("x: {workspace: W, flow: F, steps: {request: [S]}}", "x: the router of a workspace is not in a flow"),
                ("x: {policy: P, steps: {request: [S]}}", "x: a policy has no flow and no steps"),
                ("[a, b]", "controls.yaml: expected the controls, by name")):
            with self.subTest(text=text), self.assertRaisesRegex(common.SeedError, message):
                self.declare(text)

    def test_a_control_is_found_by_the_kind_and_the_name_of_its_target(self):
        self.declare(write("a: {api: LLM, flow: Prompt, steps: {request: [S]}}",
                           "b: {mcp-proxy: LLM, flow: Prompt, steps: {request: [S]}}"))
        self.assertEqual([c.id for c in controls.of("api", "LLM")], ["a"])
        self.assertEqual([c.id for c in controls.of("mcp-proxy", "LLM")], ["b"])
        self.assertEqual(controls.of("api", "Other"), [])


class Failover(Seed):
    """A control may name the failover of an API (`failover: true`): an API-level switch, not a step."""

    def declare(self, text):
        self.seed_files({"controls.yaml": text})
        return controls.declared()

    def test_the_failover_of_an_api_is_a_control_without_flow_or_steps(self):
        [control] = self.declare("outage: {api: Kazoo LLM, failover: true}\n")
        self.assertEqual((control.id, control.kind, control.target, control.failover, control.flow, control.steps),
                         ("outage", "api", "Kazoo LLM", True, None, {}))
        self.assertEqual(control.describe(), "the failover of api 'Kazoo LLM'")
        self.assertEqual([c.id for c in controls.of("api", "Kazoo LLM")], ["outage"])

    def test_a_failover_declared_wrongly_is_an_error_that_names_the_control(self):
        for text, message in (
                ("x: {api: A, failover: false}", "x: failover is true or absent"),
                ("x: {api: A, failover: yes please}", "x: failover is true or absent"),
                ("x: {mcp-proxy: P, failover: true}", "x: only an api has a failover"),
                ("x: {workspace: W, failover: true}", "x: only an api has a failover"),
                ("x: {api: A, failover: true, flow: F}", "x: the failover of an API has no flow and no steps"),
                ("x: {api: A, failover: true, steps: {request: [S]}}", "x: the failover of an API has no flow and no steps")):
            with self.subTest(text=text), self.assertRaisesRegex(common.SeedError, message):
                self.declare(text)

    def test_the_failover_is_switched_as_the_mode_says_and_the_rest_of_it_is_kept(self):
        [control] = self.declare("outage: {api: LLM, failover: true}\n")
        api = {"name": "LLM", "failover": {"enabled": True, "maxRetries": 1, "slowCallDuration": 30000,
                                           "forceNextEndpointOnFailure": True}}
        self.assertTrue(controls.align_failover(api, [control], enabled=False))
        self.assertEqual(api["failover"], {"enabled": False, "maxRetries": 1, "slowCallDuration": 30000,
                                           "forceNextEndpointOnFailure": True})
        self.assertFalse(controls.align_failover(api, [control], enabled=False))
        self.env(SEED_PROTECTIONS="on")
        self.assertTrue(controls.align_failover(api, [control]))
        self.assertIs(api["failover"]["enabled"], True)

    def test_an_api_without_a_failover_block_is_an_error(self):
        [control] = self.declare("outage: {api: LLM, failover: true}\n")
        for api in ({"name": "LLM"}, {"failover": None}, None):
            with self.subTest(api=api), self.assertRaisesRegex(controls.Missing, "^control outage: api 'LLM' has no failover$"):
                controls.align_failover(api, [control], enabled=False)

    def test_the_steps_and_the_failover_are_aligned_each_by_its_own_function(self):
        found = self.declare("outage: {api: LLM, failover: true}\nguard: {api: LLM, flow: Prompt, steps: {request: [Guard]}}\n")
        flows = json.loads(json.dumps(FLOWS))
        self.assertTrue(controls.align(flows, found, enabled=False), "the failover control is no flow to look for")
        self.assertFalse(controls.align_failover({"failover": {"enabled": False}}, found, enabled=False),
                         "a step control is no failover")


class Mode(Seed):

    def test_the_default_is_on_so_that_a_seed_that_sets_nothing_protects_everything(self):
        self.assertEqual((controls.mode(), controls.protected()), ("on", True))
        self.env(SEED_PROTECTIONS="")
        self.assertEqual(controls.mode(), "on")

    def test_off_and_on_are_the_modes_whatever_their_case_and_spaces(self):
        for value, mode in (("off", "off"), (" OFF ", "off"), ("On", "on"), ("on", "on")):
            with self.subTest(value=value):
                self.env(SEED_PROTECTIONS=value)
                self.assertEqual((controls.mode(), controls.protected()), (mode, mode == "on"))

    def test_any_other_value_is_an_error_that_says_what_it_was_and_what_it_can_be(self):
        for value in ("false", "0", "yes", "partial"):
            with self.subTest(value=value):
                self.env(SEED_PROTECTIONS=value)
                with self.assertRaisesRegex(common.SeedError, f"^SEED_PROTECTIONS is '{value}': it is one of on or off$"):
                    controls.mode()

    def test_the_seed_stops_on_an_unknown_mode_before_it_changes_or_asks_anything(self):
        self.seed_files({"controls.yaml": "pii: {api: LLM, flow: Prompt, steps: {request: [Guard]}}\n"})
        self.env(SEED_PROTECTIONS="sometimes")
        self.assertEqual(self.main(), 1)
        self.assertIn("✗ SEED_PROTECTIONS is 'sometimes': it is one of on or off", self.output())
        self.assertEqual(self.platform.calls, [])


class Aligning(Seed):

    def test_the_steps_a_control_names_are_switched_and_the_others_left_as_they_are(self):
        flows = json.loads(json.dumps(FLOWS))
        self.assertTrue(controls.align(flows, [GUARD], enabled=False))
        self.assertEqual([s.get("enabled") for s in flows[0]["request"]], [False, False, None])
        self.assertEqual(flows[1], FLOWS[1], "the same step in another flow")
        self.assertEqual(flows[0]["response"], FLOWS[0]["response"], "the same name in the other phase")
        self.assertTrue(controls.align(flows, [GUARD], enabled=True))
        self.assertEqual([s.get("enabled") for s in flows[0]["request"]], [True, False, None])

    def test_the_answer_is_whether_a_step_was_not_so_already(self):
        flows = json.loads(json.dumps(FLOWS))
        controls.align(flows, [GUARD], enabled=False)
        self.assertFalse(controls.align(flows, [GUARD], enabled=False))
        self.assertTrue(controls.align(flows, [GUARD], enabled=True))
        self.assertFalse(controls.align(flows, [GUARD], enabled=True))

    def test_a_step_without_a_switch_is_enabled(self):
        flows = json.loads(json.dumps(FLOWS))
        self.assertFalse(controls.align(flows, [GUARD], enabled=True))
        self.assertIs(flows[0]["request"][0]["enabled"], True, "written, so that a platform that read it as off differs")

    def test_the_mode_decides_when_the_state_is_not_given(self):
        flows = json.loads(json.dumps(FLOWS))
        self.env(SEED_PROTECTIONS="off")
        controls.align(flows, [GUARD])
        self.assertIs(flows[0]["request"][0]["enabled"], False)
        self.env(SEED_PROTECTIONS="on")
        controls.align(flows, [GUARD])
        self.assertIs(flows[0]["request"][0]["enabled"], True)

    def test_a_flow_that_matches_nothing_is_an_error(self):
        lost = controls.Control("guard", {"api": "LLM", "flow": "Promt", "steps": {"request": ["Guard"]}})
        with self.assertRaisesRegex(controls.Missing, "^control guard: no flow named 'Promt' in api 'LLM'$"):
            controls.align(json.loads(json.dumps(FLOWS)), [lost], enabled=False)

    def test_a_step_that_matches_nothing_is_an_error_that_names_the_phase_and_the_place(self):
        for phase, name in (("request", "Gard"), ("response", "Guard")):
            lost = controls.Control("guard", {"api": "LLM", "flow": "Prompt", "steps": {phase: [name]}})
            with self.subTest(phase=phase), self.assertRaisesRegex(
                    controls.Missing, f"^control guard: no {phase} step named '{name}' in flow 'Prompt' of api 'LLM'$"):
                controls.align(json.loads(json.dumps(FLOWS)), [lost], enabled=False)

    def test_nothing_is_done_to_a_flow_that_has_no_control(self):
        flows = json.loads(json.dumps(FLOWS))
        self.assertFalse(controls.align(flows, [], enabled=False))
        self.assertFalse(controls.align(None, [], enabled=False))
        self.assertEqual(flows, FLOWS)

    def test_a_router_has_steps_and_no_flow(self):
        route = controls.Control("router", {"workspace": "Teams", "steps": {"request": ["Spend-aware routing"]}})
        router = {"request": [{"name": "Spend-aware routing", "enabled": True}, {"name": "Other"}], "response": []}
        self.assertTrue(controls.align_router(router, [route], enabled=False))
        self.assertEqual([s.get("enabled") for s in router["request"]], [False, None])
        lost = controls.Control("router", {"workspace": "Teams", "steps": {"response": ["Spend-aware routing"]}})
        with self.assertRaisesRegex(controls.Missing, "no response step named 'Spend-aware routing' in workspace 'Teams'"):
            controls.align_router(router, [lost], enabled=False)

    def test_a_policy_is_deployed_while_protected_and_a_draft_while_not(self):
        cap = controls.Control("cap", {"policy": POLICY})
        self.assertEqual(controls.status([], "DRAFT"), "DRAFT", "no control: the file's status")
        self.assertEqual(controls.status([], "DEPLOYED"), "DEPLOYED")
        self.assertEqual(controls.status([cap], "DEPLOYED"), "DEPLOYED")
        self.env(SEED_PROTECTIONS="off")
        self.assertEqual(controls.status([cap], "DEPLOYED"), "DRAFT")
        self.assertEqual(controls.status([], "DEPLOYED"), "DEPLOYED")


class Ending(Seed):

    def setUp(self):
        super().setUp()
        self.seed_files({"controls.yaml": write("pii: {api: LLM, flow: Prompt, steps: {request: [Guard]}}",
                                                "cap: {policy: A rule}")})

    def test_a_control_no_module_found_a_target_for_stops_the_seed_naming_it(self):
        controls.begin()
        controls.of("api", "LLM")
        with self.assertRaisesRegex(common.SeedError, "^controls.yaml: cap names policy 'A rule', which no seed file has$"):
            controls.finish()

    def test_a_new_seed_forgets_what_the_last_one_found(self):
        controls.begin()
        controls.of("api", "LLM")
        controls.of("policy", "A rule")
        controls.begin()
        with self.assertRaisesRegex(common.SeedError, "pii names api 'LLM'.*cap names policy 'A rule'"):
            controls.finish()


class Waiting(Seed):

    def setUp(self):
        super().setUp()
        self.env(SEED_WAIT_GATEWAY="1")   # what it tests is the wait for the gateway, which a seed does when asked

    def test_it_waits_only_for_what_is_left_of_that_time(self):
        self.env(GATEWAY_SYNC_SECONDS="5")
        controls.touched()
        self.clock.sleep(8)
        controls.settle()
        self.assertAlmostEqual(self.clock.elapsed, 11, delta=0.5)

    def test_it_does_not_wait_when_nothing_was_deployed(self):
        controls.settle()
        controls.begin()
        controls.finish()
        self.assertEqual(self.clock.sleeps, [])

    def test_it_waits_once(self):
        controls.touched()
        controls.settle()
        slept = list(self.clock.sleeps)
        controls.settle()
        self.assertEqual(self.clock.sleeps, slept)


class Main(Seed):
    """seed.main() with controls: the mode is told, and a control nobody found stops the seed after the modules ran."""

    def test_the_mode_and_the_controls_are_told_first(self):
        self.seed_files({"controls.yaml": "pii: {api: LLM, flow: Prompt, steps: {request: [Guard]}}\n"})
        self.env(SEED_PROTECTIONS="off")
        with mock.patch.object(seed, "AFTER_AM", ()), mock.patch.object(seed, "openfga"), mock.patch.object(seed, "am"):
            self.assertEqual(self.main(), 1)
        self.assertIn("[seed] Protections off: pii", self.output())
        self.assertIn("✗ controls.yaml: pii names api 'LLM', which no seed file has", self.output())
        self.assertNotIn("Playground ready", self.output())

    def test_a_seed_whose_controls_all_found_their_target_is_ready_once_the_gateway_has_taken_what_they_deployed(self):
        self.env(SEED_WAIT_GATEWAY="1")
        self.seed_files({"controls.yaml": "pii: {api: LLM, flow: Prompt, steps: {request: [Guard]}}\n"})
        self.env(SEED_PROTECTIONS="off", GATEWAY_SYNC_SECONDS="5")

        class Module:
            @staticmethod
            def seed():
                controls.of("api", "LLM")
                controls.touched()   # it deployed what the control changed

        with mock.patch.object(seed, "AFTER_AM", (Module,)), mock.patch.object(seed, "openfga"), mock.patch.object(seed, "am"), \
                mock.patch.object(seed, "require_modules"):
            self.assertEqual(self.main(), 0)
        self.assertIn("[seed] ✓ Playground ready", self.output())
        self.assertAlmostEqual(self.clock.elapsed, 11, delta=1)

    def test_a_seed_without_controls_says_nothing_about_them(self):
        with mock.patch.object(seed, "AFTER_AM", ()), mock.patch.object(seed, "openfga"), mock.patch.object(seed, "am"):
            self.assertEqual(self.main(), 0)
        self.assertNotIn("Protections", self.output())
