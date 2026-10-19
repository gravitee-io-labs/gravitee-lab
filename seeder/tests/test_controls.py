"""The controls: the protections a playground's lessons teach. The declaration is read strictly (a typo is an error, never a
control that switches nothing), the mode is `on` or `off` and nothing else, a flow or a step that matches nothing is an
error, and the controls of Acme Bakery name exactly what its lessons switch, in files that say the protected state."""

import json
import os
import re
import unittest
from unittest import mock

import yaml

from lab_seeder import common
from lab_seeder import controls
from lab_seeder import seed
from lab_seeder.testkit import PLAYGROUNDS, Seed, compose_value, seed_environment, seed_service

ACME_BAKERY = PLAYGROUNDS / "acme-bakery"
ACME_SEED = ACME_BAKERY / "seed"

# The lessons of Acme Bakery: the steps and the policy each control switches, as the seed files name them
SWITCHED = {
    "pii": [("Acme Bakery LLM", "Prompt", "request", "PII filtering (financial_account)")],
    "guard": [("Acme Bakery LLM", "Prompt", "request", "Prompt guard rails (question)")],
    "tools": [("Acme Bakery MCP", "Visible tools", "request", "Tools by role")],
    "pep": [("Acme Bakery MCP", "Authorization", "request", "Fine-Grained Authorization")],
    "router": [("Acme Bakery Teams", None, "request", "Spend-aware routing")],
}
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

    def test_a_playground_without_controls_ignores_the_mode(self):
        self.env(SEED_PROTECTIONS="sometimes")
        self.assertEqual(controls.begin(), [])


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

    def test_a_seed_whose_controls_all_found_their_target_ends(self):
        controls.begin()
        controls.of("api", "LLM")
        controls.of("policy", "A rule")
        controls.finish()

    def test_a_target_that_is_misspelled_in_the_declaration_is_found_by_nobody(self):
        self.seed_files({"controls.yaml": "pii: {api: LMM, flow: Prompt, steps: {request: [Guard]}}\n"})
        controls.begin()
        self.assertEqual(controls.of("api", "LLM"), [])
        with self.assertRaisesRegex(common.SeedError, "pii names api 'LMM'"):
            controls.finish()

    def test_a_new_seed_forgets_what_the_last_one_found(self):
        controls.begin()
        controls.of("api", "LLM")
        controls.of("policy", "A rule")
        controls.begin()
        with self.assertRaisesRegex(common.SeedError, "pii names api 'LLM'.*cap names policy 'A rule'"):
            controls.finish()


class Waiting(Seed):

    def test_the_seed_waits_two_synchronizations_and_a_second_after_a_deployment_of_a_control(self):
        self.env(GATEWAY_SYNC_SECONDS="5")
        controls.touched()
        controls.settle()
        self.assertAlmostEqual(self.clock.elapsed, 11, delta=0.5)

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


class AcmeBakery(Seed):
    """The controls of Acme Bakery against its real files: each names what exists, and what it names is what the lessons switch."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(common, "SEED_DIR", ACME_SEED)
        patch.start()
        self.addCleanup(patch.stop)
        compose = ACME_BAKERY / "compose.yml"
        patch = mock.patch.dict(os.environ, seed_environment(seed_service(compose)), clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def definitions(self):
        """The API of the LLM proxy, the flows of the MCP proxy, the router of the workspace, as their files give them."""
        llm = common.load(ACME_SEED / "apim" / "apis" / "acme-bakery-llm.json")["api"]
        proxy = common.load(ACME_SEED / "aim" / "mcp-proxies" / "acme-bakery-mcp.yaml")["automation"]
        workspace = common.load(ACME_SEED / "aim" / "workspaces" / "acme-bakery-teams.yaml")
        return {"Acme Bakery LLM": llm["flows"], "Acme Bakery MCP": proxy["flows"], "Acme Bakery Teams": workspace["router"]}

    @staticmethod
    def named(declared):
        return {(c.target, c.flow, phase, step) for c in declared for phase, names in c.steps.items() for step in names}

    def test_the_controls_are_the_six_of_the_lessons(self):
        self.assertEqual([c.id for c in controls.declared()], ["pii", "guard", "tools", "pep", "cap", "router"])
        self.assertEqual({c.id: c.kind for c in controls.declared()},
                         {"pii": "api", "guard": "api", "tools": "mcp-proxy", "pep": "mcp-proxy", "cap": "policy",
                          "router": "workspace"})

    def test_each_control_switches_the_steps_of_its_lesson_and_no_others(self):
        wanted = {(target, flow, phase, step) for steps in SWITCHED.values() for target, flow, phase, step in steps}
        self.assertEqual(self.named(controls.declared()), wanted)
        for control in controls.declared():
            if control.id in SWITCHED:
                self.assertEqual(self.named([control]), set(SWITCHED[control.id]), control.id)

    def test_the_files_say_the_protected_state_so_the_mode_off_is_what_changes_them(self):
        for target, flows in self.definitions().items():
            containers = [flows] if isinstance(flows, dict) else flows
            for container in containers:
                for phase in ("request", "response"):
                    for step in container.get(phase) or []:
                        with self.subTest(target=target, step=step["name"]):
                            self.assertTrue(step.get("enabled", True), "a step is switched off by the mode, not by its file")
        status = common.load(ACME_SEED / "authz" / "policies" / "06-agents-move-at-most-eur-500.yaml")
        self.assertEqual((status["name"], status["status"]), (POLICY, "DEPLOYED"))

    def test_every_control_finds_what_it_names_in_the_files_and_off_switches_exactly_those_steps(self):
        definitions = json.loads(json.dumps(self.definitions()))
        switched = set()
        for control in controls.declared():
            if control.kind == "policy":
                continue
            where = definitions[control.target]
            if control.kind == "workspace":
                controls.align_router(where, [control], enabled=False)
            else:
                controls.align(where, [control], enabled=False)
        for target, flows in definitions.items():
            for flow in [flows] if isinstance(flows, dict) else flows:
                for phase in ("request", "response"):
                    for step in flow.get(phase) or []:
                        if not step.get("enabled", True):
                            switched.add((target, flow.get("name"), phase, step["name"]))
        self.assertEqual(switched, {(t, f, p, s) for steps in SWITCHED.values() for t, f, p, s in steps})

    def test_the_policy_a_control_names_is_a_policy_of_the_files(self):
        names = [common.load(path)["name"] for path in common.files("authz", "policies")]
        self.assertIn(POLICY, names)
        [cap] = [c for c in controls.declared() if c.kind == "policy"]
        self.assertEqual((cap.id, cap.target), ("cap", POLICY))

    def test_the_twins_of_the_two_steps_of_lesson_2_stay_on_in_both_modes(self):
        """Mode off disables the request step for IBANs and the one for the question, and no other step of the flow: the
        steps for email, phone and ID numbers, for a tool result and for the answer on its way back are on from the start."""
        flow = next(f for f in self.definitions()["Acme Bakery LLM"] if f["name"] == "Prompt")
        found = [c for c in controls.declared() if c.kind == "api"]
        self.assertEqual({c.id for c in found}, {"pii", "guard"})
        controls.align([flow], found, enabled=False)
        state = {(phase, step["name"]): step.get("enabled", True) for phase in ("request", "response") for step in flow[phase]}
        self.assertEqual({key for key, on in state.items() if not on},
                         {("request", "PII filtering (financial_account)"), ("request", "Prompt guard rails (question)")})
        for kept in (("request", "PII filtering (email+phone+government_id)"), ("request", "Prompt guard rails (tool result)"),
                     ("response", "PII filtering")):
            self.assertIs(state[kept], True, kept)

    def test_the_steps_of_lesson_2_say_what_each_covers_so_that_reading_them_tells_the_two_that_start_off_from_their_twins(self):
        """The console labels these steps by their policy ("PII Filtering"), so what tells them apart is their Description:
        the step that starts off covers account numbers, its twin that is on covers email, phone and ID numbers."""
        flow = next(f for f in self.definitions()["Acme Bakery LLM"] if f["name"] == "Prompt")
        said = {(phase, step["name"]): step["description"] for phase in ("request", "response") for step in flow[phase]
                if step["policy"] in ("pii-filtering", "ai-prompt-guard-rails")}
        self.assertEqual(len(said), 5)
        self.assertEqual(len(set(said.values())), 5, "no two of these steps are described alike")
        for key, words in {("request", "PII filtering (financial_account)"): ("account numbers", "IBAN"),
                           ("request", "PII filtering (email+phone+government_id)"): ("email", "phone", "ID numbers"),
                           ("response", "PII filtering"): ("answers",),
                           ("request", "Prompt guard rails (question)"): ("user asks",),
                           ("request", "Prompt guard rails (tool result)"): ("tool returns",)}.items():
            for word in words:
                self.assertIn(word, said[key], key)

    def test_the_guide_finds_the_two_steps_of_lesson_2_by_their_place_in_the_request_phase(self):
        """Lesson 2 of the guide names them "the 1st and the 2nd step of the Request Phase", side by side at the front so that
        a person who opens the second one cannot land on its twin that is already on: a step added before them, or a
        reorder, makes that sentence false."""
        flow = next(f for f in self.definitions()["Acme Bakery LLM"] if f["name"] == "Prompt")
        self.assertEqual(flow["request"][0]["name"], "Prompt guard rails (question)")
        self.assertEqual(flow["request"][1]["name"], "PII filtering (financial_account)")
        self.assertEqual([step["name"] for step in flow["request"][2:]],
                         ["Prompt guard rails (tool result)", "PII filtering (email+phone+government_id)", "Can this answer be cached?",
                          "Semantic cache"])

    def test_what_the_other_lessons_leave_on_has_no_control(self):
        """The rate limit, the semantic cache, the token budgets, the OAuth2 plans, the arguments the policies read and the
        404 on any other path stay on in both modes."""
        steps = {step for _, _, _, step in self.named(controls.declared())}
        for kept in ("30 calls a minute per user", "Semantic cache", "Read tool arguments", "Nothing else is served",
                     "Counter owner", "Can this answer be cached?", "Token budget (Developers)"):
            self.assertNotIn(kept, steps)

    def test_the_playground_sets_the_mode_off_unless_its_setting_says_otherwise(self):
        service = seed_service(ACME_BAKERY / "compose.yml")
        self.assertEqual(seed_environment(service)["SEED_PROTECTIONS"], "off")
        raw = (service.get("environment") or {})["SEED_PROTECTIONS"]
        self.assertEqual(raw, "${ACME_BAKERY_PROTECTIONS:-off}")
        self.assertEqual(compose_value(raw), "off")

    def test_the_setting_is_described_for_the_settings_form_and_says_what_applies_a_change(self):
        """The Lab UI shows the comment of the first line that uses a variable, which is the traffic generator's. Two services
        read the setting and a change reaches each by another way: the seed when Re-seed runs it again (it recreates the seed
        and nothing else), the generator, which reads the mode when its container is made, when Save & apply recreates it."""
        lines = (ACME_BAKERY / "compose.yml").read_text(encoding="utf-8").splitlines()
        [first, *_] = [number for number, line in enumerate(lines) if "ACME_BAKERY_PROTECTIONS" in line.split(" # ")[0]]
        service = next(line for line in reversed(lines[:first]) if re.fullmatch(r"  [a-z][a-z-]*:", line))
        self.assertEqual(service, "  bakery-traffic:", "the line the form shows describes the generator")
        comment = lines[first].split(" # ", 1)[1]
        self.assertRegex(comment, r"\boff\b.*\bon\b")
        self.assertIn("Save & apply restarts the traffic generator", comment)
        self.assertIn("Re-seed puts the protections in the new state", comment)
        self.assertNotIn("Re-seed applies a change", comment, "it does not reach the generator")
        services = yaml.safe_load((ACME_BAKERY / "compose.yml").read_text(encoding="utf-8"))["services"]
        self.assertEqual(seed_service(ACME_BAKERY / "compose.yml"), services["bakery-seed"], "Re-seed runs the seed again, and only it")
        self.assertNotEqual(seed_service(ACME_BAKERY / "compose.yml"), services["bakery-traffic"])

    def test_the_traffic_generator_follows_the_mode_of_the_seed(self):
        """It is strict when the protections are on (a denial that becomes a permit is a failure) and takes both outcomes
        when they are off: the one setting reaches both, as PROTECTIONS for the generator and SEED_PROTECTIONS for the seed."""
        compose = ACME_BAKERY / "compose.yml"
        services = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]
        self.assertEqual(services["bakery-traffic"]["environment"]["PROTECTIONS"], "${ACME_BAKERY_PROTECTIONS:-off}")
        self.assertEqual(services["bakery-seed"]["environment"]["SEED_PROTECTIONS"], services["bakery-traffic"]["environment"]["PROTECTIONS"])
        self.assertEqual(seed_environment(services["bakery-traffic"])["PROTECTIONS"], "off")
        used = [name for name, service in services.items() for value in (service.get("environment") or {}).values()
                if "ACME_BAKERY_PROTECTIONS" in str(value)]
        self.assertEqual(sorted(used), ["bakery-seed", "bakery-traffic"], "no other service reads the setting")


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


if __name__ == "__main__":
    unittest.main()
