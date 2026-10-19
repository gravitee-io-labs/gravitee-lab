"""The human approval of tool calls and the compliance of the agents, from the files of seed/aim/governance/: the settings and
the rules of approval, the frameworks switched on, and what is written and attested for an agent. Against a platform that keeps
what it is given: made once, left alone by a second run (no write at all), repaired when a rule differs from its file, never
taking over what the seed did not make, and removed with only what it made."""

import itertools
import os
import re
import unittest
from unittest import mock

import yaml

from lab_seeder import aim_governance
from lab_seeder import common
from lab_seeder import seed
from lab_seeder.testkit import PERSONAL, Seed

AIM = "/gamma/organizations/DEFAULT/environments/DEFAULT/modules/aim"
USERS = "/management/organizations/DEFAULT/users"
SETTINGS_PATH = AIM + "/approval-settings"
RULES = AIM + "/approval-rules"
ACTIVATIONS = AIM + "/compliance/activations"
AGENT = AIM + "/compliance/agents/agent-1"
CONTRACT = PERSONAL / "agent-toolbox" / "seed" / "aim" / "governance"

APPROVALS = """\
settings:
  defaultTtlSeconds: 300
  defaultOnExpiry: DENY

rules:
  - name: Refunds above EUR 100
    description: A refund above EUR 100 waits for a person
    tool: create_refund
    when:
      - {path: amount, operator: GT, value: "100"}
    ttlSeconds: 300
    onExpiry: DENY
    priority: 100
"""
COMPLIANCE = """\
frameworks:
  - eu-ai-act

agents:
  - agent: care-copilot
    metadata:
      compliance.intendedPurpose: Helps the care team answer questions about customers and orders.
      compliance.accountableHuman: {user: admin}
      compliance.riskAssessment: HARBOR-RA-2026-014
    attest:
      - authenticated-identity
      - tool-inventory
"""
ADMIN = {"id": "user-admin", "source": "memory", "sourceId": "admin"}
# The controls of the framework, as (id, category, the metadata key it reads): a METADATA control is read from the agent, the
# others the platform cannot read yet, except event-logging, which it measures from the traffic
METADATA = {"risk-classification": "riskClassification", "intended-purpose": "compliance.intendedPurpose",
            "provider-and-version": "compliance.providerAndVersion", "accountable-human": "compliance.accountableHuman",
            "oversight-measure": "compliance.oversightMeasure", "known-limitations": "compliance.knownLimitations",
            "risk-assessment": "compliance.riskAssessment"}
UNREAD = ("authenticated-identity", "tool-inventory", "authorization-rule", "transparency-disclosure")


class World:
    """Agent Management's approvals and compliance, and the users of the platform. Like the real ones it gives a rule the
    defaults it was not sent, replaces the settings it is sent without the price of a decision by the unpriced ones, merges
    the answers of an agent, and attests only a control it cannot read."""

    def __init__(self):
        self.next_id = itertools.count(1)
        self.settings = {"defaultTtlSeconds": 900, "defaultOnExpiry": "DENY", "approvalCost": None}
        self.rules = {}
        self.active = {}          # framework id -> its activation
        self.frameworks = ("eu-ai-act",)
        self.agents = [{"id": "agent-1", "entityId": "agent.care-copilot", "definition": {"name": "Care copilot"}}]
        # agent id -> {metadata key: value}. The risk class is the agent's own, from its file in aim/agents
        self.answers = {"agent-1": {"riskClassification": "limited"}}
        self.attested = {}        # agent id -> the controls attested
        self.users = [ADMIN, {"id": "user-2", "source": "memory", "sourceId": "administrator"}]

    def routes(self):
        return {
            ("GET", SETTINGS_PATH): lambda call: (200, self.settings),
            ("PUT", SETTINGS_PATH): self.save_settings,
            ("GET", RULES): lambda call: (200, {"data": list(self.rules.values()),
                                                "pagination": {"page": 1, "perPage": 200, "totalCount": len(self.rules)}}),
            ("POST", RULES): self.create_rule,
            ("PUT", re.compile(RULES + "/[^/]+")): self.replace_rule,
            ("DELETE", re.compile(RULES + "/[^/]+")): self.delete_rule,
            ("GET", ACTIVATIONS): lambda call: (200, list(self.active.values())),
            ("POST", ACTIVATIONS): self.activate,
            ("DELETE", re.compile(ACTIVATIONS + "/[^/]+")): self.deactivate,
            ("GET", AIM + "/catalog/agents"): lambda call: (200, {"data": self.agents,
                                                                  "pagination": {"totalCount": len(self.agents)}}),
            ("GET", re.compile(AIM + "/compliance/agents/[^/]+/assessment")):
                lambda call: (200, self.assessment(call.path.split("/")[-2])),
            ("PUT", re.compile(AIM + "/compliance/agents/[^/]+/metadata")): self.write_answers,
            ("POST", re.compile(AIM + "/compliance/agents/[^/]+/controls/[^/]+/attestation")): self.attest,
            ("GET", USERS): lambda call: (200, {"data": [u for u in self.users if call.params["q"] in u["sourceId"]]}),
        }

    # --- Approvals --------------------------------------------------------------------------

    def save_settings(self, call):
        self.settings = {"approvalCost": None, **call.json}   # what is not sent is cleared
        return 200, self.settings

    def rule(self, body, id):
        return {"id": id, "toolId": None, "description": None, "enabled": True, "argumentConditions": [],
                "ttlSeconds": self.settings["defaultTtlSeconds"], "onExpiry": self.settings["defaultOnExpiry"], "priority": 100,
                "cost": None, "createdAt": "2026-10-13T00:00:00Z", "updatedAt": "2026-10-13T00:00:00Z", **body}

    def create_rule(self, call):
        made = self.rule(call.json, f"rule-{next(self.next_id)}")
        self.rules[made["id"]] = made
        return 201, made

    def replace_rule(self, call):
        id = call.path.rsplit("/", 1)[1]
        if id not in self.rules:
            return 404, {"code": "approval-rule.not_found", "message": "No such rule"}
        self.rules[id] = self.rule(call.json, id)
        return 200, self.rules[id]

    def delete_rule(self, call):
        return 204 if self.rules.pop(call.path.rsplit("/", 1)[1], None) else 404

    # --- Compliance -------------------------------------------------------------------------

    def activate(self, call):
        framework = call.json["frameworkId"]
        if framework not in self.frameworks:
            return 404, {"code": "compliance.framework.not_found", "message": "No such framework"}
        self.active.setdefault(framework, {"id": f"activation-{next(self.next_id)}", "frameworkId": framework,
                                           "frameworkVersion": "1.0", "environmentId": "DEFAULT",
                                           "activatedAt": "2026-10-13T00:00:00Z"})
        return 201, self.active[framework]

    def deactivate(self, call):
        self.active.pop(call.path.rsplit("/", 1)[1], None)
        return 204

    def assessment(self, agent):
        answers, attested = self.answers.get(agent, {}), self.attested.get(agent, set())
        controls = []
        for control, key in METADATA.items():
            controls.append({"controlId": control, "name": control, "category": "METADATA", "proxyChecks": [],
                             "status": "SATISFIED" if answers.get(key) else "MISSING", "value": answers.get(key),
                             "metadata": {"kind": "TEXT", "metadataKey": key}})
        controls += [{"controlId": control, "name": control, "category": "AGENT_IDENTITY", "proxyChecks": [],
                      "status": "ATTESTED" if control in attested else "NOT_ASSESSED"} for control in UNREAD]
        controls.append({"controlId": "event-logging", "name": "Event logging", "category": "GATEWAY_MEDIATION", "proxyChecks": [],
                         "status": "MISSING"})
        frameworks = [{"frameworkId": f, "frameworkName": f, "functional": True, "missingCount": 0, "attestedCount": len(attested),
                       "compliant": False, "controls": controls} for f in self.active]
        return {"agentId": agent, "overallStatus": "NOT_ASSESSED", "frameworksPassed": 0, "frameworksAssessed": len(frameworks),
                "frameworks": frameworks}

    def write_answers(self, call):
        agent = call.path.split("/")[-2]
        stored = self.answers.setdefault(agent, {})
        if "riskClassification" in call.json["values"]:   # as the platform does
            return 400, {"message": "riskClassification is set on the agent, not by writing compliance metadata",
                         "http_status": 400}
        for key, value in call.json["values"].items():
            if value:
                stored[key] = value
            else:
                stored.pop(key, None)
        return 200, self.assessment(agent)

    def attest(self, call):
        parts = call.path.split("/")
        agent, control = parts[-4], parts[-2]
        if control in UNREAD:   # an attestation only ever lifts what the platform cannot read
            self.attested.setdefault(agent, set()).add(control)
        return 200, self.assessment(agent)


class Governance(Seed):
    def setUp(self):
        super().setUp()
        self.world = World()
        self.platform.add(self.world.routes())
        self.seed_files({"aim/governance/approvals.yaml": APPROVALS, "aim/governance/compliance.yaml": COMPLIANCE})

    def records(self, kind=None):
        return [x for x in common.created() if x["product"] == aim_governance.SECTION and kind in (None, x["kind"])]

    def made(self):
        """What a first run leaves, and a platform that has not been asked anything since."""
        aim_governance.seed()
        self.platform.calls.clear()

    def changed(self):
        return self.platform.changes()

    def file(self, **files):
        self.seed_files({f"aim/governance/{name}.yaml": text for name, text in files.items()})

    def rule(self):
        return next(iter(self.world.rules.values()))

    def fails(self, problem):
        with self.assertRaises(common.SeedError) as stopped:
            aim_governance.seed()
        self.assertEqual(str(stopped.exception), problem)


class Settings(Governance):
    def test_the_settings_of_the_file_are_sent_when_the_platform_holds_others(self):
        aim_governance.seed()
        sent = self.platform.sent("PUT", SETTINGS_PATH)
        self.assertEqual([call.json for call in sent], [{"defaultTtlSeconds": 300, "defaultOnExpiry": "DENY"}])
        self.assertEqual(self.world.settings["defaultTtlSeconds"], 300)
        self.assertIn("[aim-governance] ✓ Approval settings: 300 s, DENY on expiry", self.output())

    def test_a_second_run_writes_nothing(self):
        self.made()
        aim_governance.seed()
        self.assertEqual(self.platform.sent("PUT", SETTINGS_PATH), [])
        self.assertIn("[aim-governance] Approval settings as the file says", self.output())

    def test_the_price_of_a_decision_is_sent_back_as_it_is_because_a_replace_without_it_clears_it(self):
        self.world.settings["approvalCost"] = 5.0
        aim_governance.seed()
        self.assertEqual(self.platform.sent("PUT", SETTINGS_PATH)[0].json,
                         {"defaultTtlSeconds": 300, "defaultOnExpiry": "DENY", "approvalCost": 5.0})
        self.assertEqual(self.world.settings["approvalCost"], 5.0)

    def test_a_value_the_file_does_not_name_stays_what_the_platform_holds(self):
        self.world.settings.update(defaultOnExpiry="ALLOW", approvalCost=2)
        self.file(approvals="settings:\n  defaultTtlSeconds: 120\n")
        aim_governance.seed()
        self.assertEqual(self.world.settings, {"defaultTtlSeconds": 120, "defaultOnExpiry": "ALLOW", "approvalCost": 2})

    def test_the_file_may_price_a_decision(self):
        self.file(approvals="settings:\n  approvalCost: 7.5\n")
        aim_governance.seed()
        self.assertEqual(self.world.settings, {"defaultTtlSeconds": 900, "defaultOnExpiry": "DENY", "approvalCost": 7.5})

    def test_it_records_the_values_before_the_first_write_and_the_values_it_wrote(self):
        aim_governance.seed()
        record = self.records("approval-settings")[0]
        self.assertEqual((record["name"], record["label"]), ("Approval settings", "approval settings"))
        self.assertEqual(record["before"], {"defaultTtlSeconds": 900, "defaultOnExpiry": "DENY", "approvalCost": None})
        self.assertEqual(record["wrote"], {"defaultTtlSeconds": 300, "defaultOnExpiry": "DENY", "approvalCost": None})

    def test_the_values_before_the_first_write_are_kept_when_the_file_changes_again(self):
        self.made()
        self.file(approvals=APPROVALS.replace("defaultTtlSeconds: 300", "defaultTtlSeconds: 600"))
        aim_governance.seed()
        record = self.records("approval-settings")[0]
        self.assertEqual((record["before"]["defaultTtlSeconds"], record["wrote"]["defaultTtlSeconds"]), (900, 600))

    def test_settings_that_are_the_files_already_are_not_written_nor_recorded(self):
        self.world.settings.update(defaultTtlSeconds=300)
        aim_governance.seed()
        self.assertEqual(self.platform.sent("PUT", SETTINGS_PATH), [])
        self.assertEqual(self.records("approval-settings"), [])

    def test_a_file_without_settings_leaves_them_alone(self):
        self.file(approvals="rules: []\n")
        aim_governance.seed()
        self.assertEqual(self.platform.sent("GET", SETTINGS_PATH)[1:], [])   # only the platform probe asked for them
        self.assertEqual(self.platform.sent("PUT", SETTINGS_PATH), [])

    def test_a_removal_puts_them_back(self):
        self.world.settings["approvalCost"] = 5.0
        self.made()
        aim_governance.remove(common.created())
        self.assertEqual(self.world.settings, {"defaultTtlSeconds": 900, "defaultOnExpiry": "DENY", "approvalCost": 5.0})
        self.assertIn("[aim-governance] ✓ Approval settings put back", self.output())

    def test_a_removal_leaves_settings_that_someone_changed_since(self):
        self.made()
        self.world.settings["defaultTtlSeconds"] = 45
        aim_governance.remove(common.created())
        self.assertEqual(self.world.settings["defaultTtlSeconds"], 45)
        self.assertIn("! The approval settings were changed since the seed set them: left as they are", self.output())

    def test_the_settings_are_checked_before_any_call(self):
        which = "approvals.yaml: settings are defaultTtlSeconds, defaultOnExpiry, approvalCost"
        seconds = "approvals.yaml: defaultTtlSeconds is a number of seconds, 1 or more"
        for text, problem in (("settings: [1]\n", which),
                              ("settings: {ttl: 5}\n", which),
                              ("settings: {defaultTtlSeconds: 0}\n", seconds),
                              ("settings: {defaultTtlSeconds: true}\n", seconds),
                              ("settings: {defaultTtlSeconds: '300'}\n", seconds),
                              ("settings: {defaultOnExpiry: ignore}\n", "approvals.yaml: defaultOnExpiry is DENY or ALLOW"),
                              ("settings: {approvalCost: -1}\n", "approvals.yaml: approvalCost is a number, 0 or more"),
                              ("settings: {approvalCost: cheap}\n", "approvals.yaml: approvalCost is a number, 0 or more"),
                              ("- not a mapping\n", "approvals.yaml: a mapping of keys to values is expected")):
            with self.subTest(text=text):
                self.file(approvals=text)
                self.fails(problem)
                self.assertEqual(self.platform.calls, [])


class Rules(Governance):
    def test_a_rule_is_created_with_what_the_platform_reads(self):
        aim_governance.seed()
        self.assertEqual([call.json for call in self.platform.sent("POST", RULES)], [{
            "name": "Refunds above EUR 100", "toolPattern": "create_refund", "enabled": True,
            "argumentConditions": [{"path": "amount", "operator": "GT", "value": "100"}],
            "description": "A refund above EUR 100 waits for a person", "ttlSeconds": 300, "onExpiry": "DENY", "priority": 100}])
        self.assertIn("[aim-governance] ✓ Approval rule created: Refunds above EUR 100", self.output())

    def test_it_records_the_rule(self):
        aim_governance.seed()
        self.assertEqual([(x["name"], x["id"], x["label"]) for x in self.records("approval-rule")],
                         [("Refunds above EUR 100", self.rule()["id"], "approval rules")])

    def test_a_second_run_writes_nothing_though_the_platform_adds_its_own_fields(self):
        self.made()
        aim_governance.seed()
        self.assertEqual(self.changed(), [])
        self.assertIn("[aim-governance] Approval rule up to date: Refunds above EUR 100", self.output())

    def test_a_rule_that_differs_is_replaced_in_place_with_the_whole_file(self):
        self.made()
        self.world.rules[self.rule()["id"]]["argumentConditions"][0]["value"] = "500"
        rule_id = self.rule()["id"]
        aim_governance.seed()
        self.assertEqual(self.changed(), [("PUT", f"{RULES}/{rule_id}")])
        self.assertEqual(self.platform.sent("PUT", f"{RULES}/{rule_id}")[0].json["argumentConditions"],
                         [{"path": "amount", "operator": "GT", "value": "100"}])
        self.assertEqual(list(self.world.rules), [rule_id])
        self.assertEqual(self.rule()["argumentConditions"][0]["value"], "100")
        self.assertIn("[aim-governance] ✓ Approval rule updated: Refunds above EUR 100", self.output())

    def test_each_key_of_the_rule_is_compared(self):
        changes = {"toolPattern": "refund_*", "enabled": False, "ttlSeconds": 60, "onExpiry": "ALLOW", "priority": 5,
                   "description": "other", "argumentConditions": []}
        for key, value in changes.items():
            with self.subTest(key=key):
                self.world.rules.clear()
                self.made()
                self.world.rules[self.rule()["id"]][key] = value
                aim_governance.seed()
                self.assertEqual([method for method, _ in self.changed()], ["PUT"])

    def test_a_condition_in_another_order_or_with_another_operator_is_a_difference(self):
        self.file(approvals=APPROVALS.replace("      - {path: amount, operator: GT, value: \"100\"}\n",
                                              "      - {path: amount, operator: GT, value: \"100\"}\n"
                                              "      - {path: reason, operator: NEQ, value: goodwill}\n"))
        self.made()
        self.rule()["argumentConditions"].reverse()
        aim_governance.seed()
        self.assertEqual([m for m, _ in self.changed()], ["PUT"])

    def test_a_value_written_as_a_number_is_sent_as_the_text_the_platform_wants(self):
        self.file(approvals=APPROVALS.replace('value: "100"', "value: 100"))
        aim_governance.seed()
        self.assertEqual(self.platform.sent("POST", RULES)[0].json["argumentConditions"][0]["value"], "100")
        self.platform.calls.clear()
        aim_governance.seed()
        self.assertEqual(self.changed(), [])

    def test_a_rule_without_conditions_holds_every_call_to_the_tool(self):
        self.file(approvals="rules:\n  - {name: Every cancel, tool: cancel_order}\n")
        aim_governance.seed()
        self.assertEqual(self.platform.sent("POST", RULES)[0].json,
                         {"name": "Every cancel", "toolPattern": "cancel_order", "enabled": True, "argumentConditions": []})
        self.platform.calls.clear()
        aim_governance.seed()
        self.assertEqual(self.changed(), [])   # the defaults the platform adds are not differences

    def test_a_rule_may_be_switched_off_or_priced_in_its_file(self):
        self.file(approvals="rules:\n  - {name: A, tool: a, enabled: false, cost: 8, priority: 0}\n")
        aim_governance.seed()
        self.assertEqual(self.rule()["enabled"], False)
        self.assertEqual((self.rule()["cost"], self.rule()["priority"]), (8, 0))
        self.platform.calls.clear()
        aim_governance.seed()
        self.assertEqual(self.changed(), [])

    def test_a_rule_of_that_name_that_the_seed_did_not_make_stops_it(self):
        self.world.rules["rule-by-hand"] = self.world.rule({"name": "Refunds above EUR 100", "toolPattern": "x"}, "rule-by-hand")
        self.fails("The approval rule 'Refunds above EUR 100' exists and was not created by this playground: rename or delete "
                   "it, the seed leaves it alone")
        self.assertEqual(self.platform.sent("POST", RULES) + self.platform.sent("PUT", re.compile(RULES + "/.*")), [])
        self.assertEqual(self.records("approval-rule"), [])

    def test_among_rules_of_the_same_name_the_one_the_seed_made_is_the_rule(self):
        self.made()
        ours = self.rule()["id"]
        self.world.rules["rule-by-hand"] = self.world.rule({"name": "Refunds above EUR 100", "toolPattern": "x"}, "rule-by-hand")
        self.world.rules[ours]["priority"] = 1
        aim_governance.seed()
        self.assertEqual(self.changed(), [("PUT", f"{RULES}/{ours}")])
        self.assertEqual(self.world.rules["rule-by-hand"]["toolPattern"], "x")

    def test_a_rule_that_was_deleted_is_made_again(self):
        self.made()
        self.world.rules.clear()
        aim_governance.seed()
        self.assertEqual(self.changed(), [("POST", RULES)])
        self.assertEqual(len(self.records("approval-rule")), 1)

    def test_the_rules_are_checked_before_any_call(self):
        needs = "approvals.yaml: a rule needs a name and a tool"
        when = "approvals.yaml: the rule 'x': when is a list of {path, operator, value}"
        for text, problem in (("rules:\n  - {tool: x}\n", needs),
                              ("rules:\n  - {name: x}\n", needs),
                              ("rules:\n  - x\n", needs),
                              ("rules:\n  - {name: x, tool: y, when: [{path: a}]}\n", when),
                              ("rules:\n  - {name: x, tool: y, when: a}\n", when),
                              ("rules:\n  - {name: x, tool: y, onExpiry: later}\n",
                               "approvals.yaml: the rule 'x': onExpiry is DENY or ALLOW"),
                              ("rules:\n  - {name: x, tool: y}\n  - {name: x, tool: z}\n",
                               "approvals.yaml: two rules have the same name")):
            with self.subTest(text=text):
                self.file(approvals=text)
                self.fails(problem)
                self.assertEqual(self.platform.calls, [])

    def test_a_removal_deletes_the_rule_and_only_the_rule_of_the_seed(self):
        self.made()
        self.world.rules["rule-by-hand"] = self.world.rule({"name": "Other", "toolPattern": "x"}, "rule-by-hand")
        ours = self.rule()["id"]
        aim_governance.remove(common.created())
        self.assertNotIn(ours, self.world.rules)
        self.assertIn("rule-by-hand", self.world.rules)
        self.assertIn("[aim-governance] ✓ Deleted approval rule 'Refunds above EUR 100'", self.output())

    def test_a_rule_that_is_gone_is_no_error_for_a_removal(self):
        self.made()
        self.world.rules.clear()
        aim_governance.remove(common.created())
        self.assertIn("✓ Deleted approval rule", self.output())


class Frameworks(Governance):
    def test_a_framework_that_is_off_is_switched_on_and_recorded(self):
        aim_governance.seed()
        self.assertEqual([call.json for call in self.platform.sent("POST", ACTIVATIONS)], [{"frameworkId": "eu-ai-act"}])
        self.assertEqual([(x["name"], x["id"], x["label"]) for x in self.records("compliance-framework")],
                         [("eu-ai-act", self.world.active["eu-ai-act"]["id"], "compliance frameworks")])
        self.assertIn("[aim-governance] ✓ Framework on: eu-ai-act", self.output())

    def test_a_second_run_writes_nothing(self):
        self.made()
        aim_governance.seed()
        self.assertEqual(self.platform.sent("POST", ACTIVATIONS), [])
        self.assertIn("[aim-governance] Framework on: eu-ai-act", self.output())

    def test_a_framework_that_was_on_before_is_not_the_seeds_and_is_not_recorded(self):
        self.world.active["eu-ai-act"] = {"id": "activation-0", "frameworkId": "eu-ai-act"}
        aim_governance.seed()
        self.assertEqual(self.platform.sent("POST", ACTIVATIONS), [])
        self.assertEqual(self.records("compliance-framework"), [])

    def test_a_removal_switches_off_the_framework_the_seed_switched_on(self):
        self.made()
        aim_governance.remove(common.created())
        self.assertEqual(self.world.active, {})
        self.assertEqual(self.platform.sent("DELETE", ACTIVATIONS + "/eu-ai-act")[0].method, "DELETE")
        self.assertIn("[aim-governance] ✓ Framework off: eu-ai-act", self.output())

    def test_a_removal_leaves_on_a_framework_that_was_on_before(self):
        self.world.active["eu-ai-act"] = {"id": "activation-0", "frameworkId": "eu-ai-act"}
        self.made()
        aim_governance.remove(common.created())
        self.assertIn("eu-ai-act", self.world.active)
        self.assertEqual(self.platform.sent("DELETE", re.compile(ACTIVATIONS + "/.*")), [])

    def test_a_framework_the_platform_does_not_know_is_reported_whole(self):
        self.file(compliance="frameworks: [no-such-law]\n")
        with self.assertRaises(common.SeedError) as stopped:
            aim_governance.seed()
        self.assertIn("compliance.framework.not_found", str(stopped.exception))


class Answers(Governance):
    def test_the_answers_are_written_as_text_with_the_user_resolved_to_its_id(self):
        aim_governance.seed()
        sent = self.platform.sent("PUT", AGENT + "/metadata")
        self.assertEqual([call.json for call in sent], [{"values": {
            "compliance.intendedPurpose": "Helps the care team answer questions about customers and orders.",
            "compliance.accountableHuman": "user-admin", "compliance.riskAssessment": "HARBOR-RA-2026-014"}}])
        self.assertEqual(self.world.answers["agent-1"]["compliance.accountableHuman"], "user-admin")
        self.assertIn("[aim-governance] ✓ Compliance answers written: Care copilot", self.output())

    def test_the_user_is_the_one_whose_source_id_is_that_name_and_not_one_that_contains_it(self):
        aim_governance.seed()
        looked = self.platform.sent("GET", USERS)
        self.assertEqual(looked[0].params["q"], "admin")
        self.assertEqual(self.world.answers["agent-1"]["compliance.accountableHuman"], "user-admin")

    def test_it_records_the_answers_with_a_fingerprint_for_the_agent(self):
        aim_governance.seed()
        record = self.records("compliance-answers")[0]
        self.assertEqual((record["name"], record["id"], record["label"]), ("Care copilot", "agent-1", "compliance answers"))
        self.assertEqual(len(record["digest"]), 64)

    def test_a_second_run_writes_nothing(self):
        self.made()
        aim_governance.seed()
        self.assertEqual(self.changed(), [])
        self.assertIn("[aim-governance] Compliance answers up to date: Care copilot", self.output())

    def test_a_value_that_a_person_changed_for_a_key_of_the_file_stays_while_the_file_is_the_same(self):
        self.made()
        self.world.answers["agent-1"]["compliance.riskAssessment"] = "SET-IN-THE-CONSOLE"
        aim_governance.seed()
        self.assertEqual(self.changed(), [])
        self.assertEqual(self.world.answers["agent-1"]["compliance.riskAssessment"], "SET-IN-THE-CONSOLE")

    def test_a_key_the_file_does_not_name_is_never_touched_even_when_the_file_changes(self):
        self.made()
        self.world.answers["agent-1"]["compliance.oversightMeasure"] = "human-in-the-loop"
        self.file(compliance=COMPLIANCE.replace("HARBOR-RA-2026-014", "HARBOR-RA-2026-015"))
        aim_governance.seed()
        sent = self.platform.sent("PUT", AGENT + "/metadata")[0].json["values"]
        self.assertEqual(sent["compliance.riskAssessment"], "HARBOR-RA-2026-015")
        self.assertNotIn("compliance.oversightMeasure", sent)
        self.assertEqual(self.world.answers["agent-1"]["compliance.oversightMeasure"], "human-in-the-loop")
        self.assertEqual(self.world.answers["agent-1"]["compliance.riskAssessment"], "HARBOR-RA-2026-015")

    def test_another_user_is_another_answer(self):
        self.made()
        self.world.users[0] = {**ADMIN, "id": "user-new"}
        aim_governance.seed()
        self.assertEqual(self.world.answers["agent-1"]["compliance.accountableHuman"], "user-new")

    def test_an_agent_that_the_catalog_made_again_is_answered_again(self):
        self.made()
        self.world.agents[0] = {**self.world.agents[0], "id": "agent-9"}
        aim_governance.seed()
        self.assertEqual(len(self.platform.sent("PUT", AIM + "/compliance/agents/agent-9/metadata")), 1)
        self.assertEqual([x["id"] for x in self.records("compliance-answers")], ["agent-9"])

    def test_a_number_is_sent_as_text_and_an_empty_value_clears_the_answer(self):
        self.file(compliance="agents:\n  - agent: care-copilot\n    metadata: {a: 2, b: true, c: ~, d: ''}\n")
        aim_governance.seed()
        self.assertEqual(self.platform.sent("PUT", AGENT + "/metadata")[0].json["values"], {"a": "2", "b": "True", "c": "", "d": ""})

    def test_a_user_that_is_not_known_stops_it(self):
        self.world.users.clear()
        self.fails("The user 'admin' is not known to API Management: it signs in once first")
        self.assertEqual(self.platform.sent("PUT", AGENT + "/metadata"), [])

    def test_a_user_in_two_sources_must_be_named_with_its_source(self):
        self.world.users.append({"id": "user-gravitee", "source": "gravitee", "sourceId": "admin"})
        self.fails("The user 'admin' is in more than one source (gravitee, memory): write it {user: admin, source: ...}")
        self.file(compliance=COMPLIANCE.replace("{user: admin}", "{user: admin, source: gravitee}"))
        aim_governance.seed()
        self.assertEqual(self.world.answers["agent-1"]["compliance.accountableHuman"], "user-gravitee")

    def test_an_agent_that_is_not_in_the_catalog_stops_it(self):
        self.world.agents.clear()
        self.fails("The agent 'agent.care-copilot' is not in the catalog: seed/aim/agents registers it before")

    def test_the_risk_class_is_the_agents_own_and_stops_the_seed_before_any_call(self):
        self.file(compliance=COMPLIANCE.replace("    metadata:\n", "    metadata:\n      riskClassification: limited\n"))
        self.fails("compliance.yaml: riskClassification of 'care-copilot' is set on the agent: write it in the metadata "
                   "of its file in aim/agents")
        self.assertEqual(self.changed(), [])        # not an approval setting, a rule or a framework either: the file is read whole first

    def test_a_value_that_is_neither_text_nor_a_user_stops_it(self):
        self.file(compliance="agents:\n  - agent: care-copilot\n    metadata: {compliance.accountableHuman: {team: support}}\n")
        self.fails("compliance.yaml: the value of compliance.accountableHuman for 'care-copilot' is a text or {user: ...}")

    def test_the_platforms_refusal_of_a_value_is_reported_whole(self):
        self.platform.add({("PUT", AGENT + "/metadata"): (400, {"code": "compliance.metadata.invalid",
                                                                "message": "riskClassification must be one of negligible, limited"})})
        with self.assertRaises(common.SeedError) as stopped:
            aim_governance.seed()
        self.assertIn("riskClassification must be one of negligible, limited", str(stopped.exception))
        self.assertEqual(self.records("compliance-answers"), [])

    def test_a_file_without_answers_for_an_agent_asks_the_user_directory_nothing(self):
        self.file(compliance="agents:\n  - agent: care-copilot\n")
        aim_governance.seed()
        self.assertEqual(self.platform.sent("GET", USERS), [])
        self.assertEqual(self.platform.sent("PUT", AGENT + "/metadata"), [])


class Attestations(Governance):
    def test_a_control_the_platform_cannot_read_is_attested(self):
        aim_governance.seed()
        self.assertEqual([call.path for call in self.platform.calls if call.path.endswith("/attestation")],
                         [AGENT + "/controls/authenticated-identity/attestation", AGENT + "/controls/tool-inventory/attestation"])
        self.assertEqual(self.world.attested["agent-1"], {"authenticated-identity", "tool-inventory"})
        self.assertIn("[aim-governance]   ✓ Attested: authenticated-identity", self.output())

    def test_the_answers_assessment_is_the_one_it_attests_from_and_it_asks_for_none_of_its_own(self):
        aim_governance.seed()
        self.assertEqual(self.platform.sent("GET", AGENT + "/assessment"), [])

    def test_a_second_run_writes_nothing_after_one_look_at_the_assessment(self):
        self.made()
        aim_governance.seed()
        self.assertEqual(self.changed(), [])
        self.assertEqual(len(self.platform.sent("GET", AGENT + "/assessment")), 1)
        self.assertIn("[aim-governance]   Attested already: tool-inventory", self.output())

    def test_a_control_that_is_attested_already_is_not_attested_again(self):
        self.world.attested["agent-1"] = {"tool-inventory"}
        aim_governance.seed()
        paths = [call.path for call in self.platform.calls if call.path.endswith("/attestation")]
        self.assertEqual(paths, [AGENT + "/controls/authenticated-identity/attestation"])

    def test_a_control_the_platform_reads_itself_is_left_alone_and_said(self):
        self.file(compliance=COMPLIANCE.replace("      - tool-inventory\n", "      - event-logging\n      - risk-classification\n"))
        aim_governance.seed()
        self.assertEqual([call.path for call in self.platform.calls if call.path.endswith("/attestation")],
                         [AGENT + "/controls/authenticated-identity/attestation"])
        self.assertIn("  ! event-logging is missing: the platform measures it itself, an attestation does not lift it", self.output())
        self.assertIn("  Attested already: risk-classification", self.output())

    def test_a_control_that_is_in_no_framework_that_is_on_stops_it(self):
        self.file(compliance=COMPLIANCE.replace("      - tool-inventory\n", "      - no-such-control\n"))
        self.fails("The control 'no-such-control' is in no framework that is on: it cannot be attested for 'Care copilot'")

    def test_a_control_in_two_frameworks_is_attested_once(self):
        framework = {"frameworkId": "other", "controls": [{"controlId": "tool-inventory", "status": "NOT_ASSESSED"}]}
        base = self.world.assessment
        self.world.assessment = lambda agent: {**base(agent), "frameworks": base(agent)["frameworks"] + [framework]}
        aim_governance.seed()
        paths = [call.path for call in self.platform.calls if call.path.endswith("/tool-inventory/attestation")]
        self.assertEqual(len(paths), 1)

    def test_an_agent_with_attestations_and_no_answers_is_assessed_once_for_them(self):
        self.file(compliance="frameworks: [eu-ai-act]\nagents:\n  - agent: care-copilot\n    attest: [tool-inventory]\n")
        aim_governance.seed()
        self.assertEqual(len(self.platform.sent("GET", AGENT + "/assessment")), 1)
        self.assertEqual(self.platform.sent("PUT", AGENT + "/metadata"), [])
        self.assertEqual(self.world.attested["agent-1"], {"tool-inventory"})

    def test_the_attestations_are_checked_before_any_call(self):
        attest = "compliance.yaml: attest of 'x' is a list of control ids"
        for text, problem in (("agents:\n  - agent: x\n    attest: tool-inventory\n", attest),
                              ("agents:\n  - agent: x\n    attest: [1]\n", attest),
                              ("agents:\n  - {metadata: {}}\n",
                               "compliance.yaml: an agent needs the entity id of the agent in the catalog"),
                              ("agents:\n  - agent: x\n    metadata: [a]\n",
                               "compliance.yaml: the metadata of 'x' is a mapping of keys to values"),
                              ("frameworks: eu-ai-act\n", "compliance.yaml: frameworks is a list of framework ids"),
                              ("- not a mapping\n", "compliance.yaml: a mapping of keys to values is expected")):
            with self.subTest(text=text):
                self.file(compliance=text)
                self.fails(problem)
                self.assertEqual(self.platform.calls, [])

    def test_a_removal_leaves_the_answers_and_the_attestations_to_the_agent(self):
        self.made()
        aim_governance.remove(common.created())
        self.assertEqual(self.world.attested["agent-1"], {"authenticated-identity", "tool-inventory"})
        self.assertEqual(self.platform.sent("DELETE", re.compile(AGENT + ".*")), [])
        self.assertEqual(self.world.answers["agent-1"]["riskClassification"], "limited")


class Files(Governance):
    def test_a_folder_with_only_the_approvals_asks_nothing_of_compliance(self):
        (common.SEED_DIR / "aim" / "governance" / "compliance.yaml").unlink()
        aim_governance.seed()
        self.assertEqual(self.platform.sent("GET", ACTIVATIONS), [])
        self.assertEqual([m for m, _ in self.changed()], ["PUT", "POST"])

    def test_a_folder_with_only_the_compliance_asks_nothing_of_the_rules(self):
        (common.SEED_DIR / "aim" / "governance" / "approvals.yaml").unlink()
        aim_governance.seed()
        self.assertEqual(self.platform.sent("GET", RULES), [])
        self.assertEqual(self.platform.sent("PUT", SETTINGS_PATH), [])
        self.assertEqual(self.world.attested["agent-1"], {"authenticated-identity", "tool-inventory"})

    def test_an_empty_folder_makes_no_call(self):
        (common.SEED_DIR / "aim" / "governance" / "approvals.yaml").unlink()
        (common.SEED_DIR / "aim" / "governance" / "compliance.yaml").unlink()
        aim_governance.seed()
        self.assertEqual(self.platform.calls, [])

    def test_a_playground_that_seeds_no_governance_needs_no_setting_and_makes_no_call(self):
        with mock.patch.object(common, "SEED_DIR", common.SEED_DIR / "nothing"), mock.patch.dict(os.environ, {}, clear=True):
            aim_governance.seed()
            aim_governance.remove([])
        self.assertEqual(self.platform.calls, [])

    def test_a_platform_without_agent_management_ends_the_wait_at_once(self):
        self.platform.add({("GET", SETTINGS_PATH): (404, {"message": "not found"})})
        with self.assertRaises(common.SeedError) as stopped:
            aim_governance.seed()
        self.assertIn("is not available on this platform", str(stopped.exception))
        self.assertEqual(self.clock.sleeps, [])

    def test_the_records_of_other_modules_are_left_alone(self):
        theirs = [{"product": "aim-catalog", "kind": "agent", "name": "a", "id": "a", "label": ""},
                  {"product": "aim-agents", "kind": "subscription", "name": "s", "id": "s", "label": ""},
                  {"product": "aim-governance", "kind": "other", "name": "o", "id": "o", "label": ""}]
        aim_governance.remove(theirs)
        self.assertEqual(self.platform.calls, [])

    def test_the_answers_alone_are_a_removal_that_asks_nothing_of_the_platform(self):
        aim_governance.remove([{"product": "aim-governance", "kind": "compliance-answers", "name": "Care copilot",
                                "id": "agent-1", "label": "compliance answers", "digest": "x"}])
        self.assertEqual(self.platform.calls, [])

    def test_a_removal_goes_the_other_way_round_and_through_the_seed_it_empties_the_record(self):
        self.made()
        rule_id = next(iter(self.world.rules))
        with mock.patch.object(seed, "MODULES", (aim_governance,)):
            self.assertEqual(self.main("--remove"), 0, self.output())
        self.assertEqual([(m, p.split("/modules/aim")[1]) for m, p in self.changed()],
                         [("DELETE", f"/approval-rules/{rule_id}"), ("PUT", "/approval-settings"),
                          ("DELETE", "/compliance/activations/eu-ai-act")])
        self.assertEqual(common.created(), [])
        self.assertEqual(self.world.rules, {})
        self.assertEqual(self.world.settings["defaultTtlSeconds"], 900)

    def test_nothing_it_logs_holds_a_secret_or_a_token(self):
        aim_governance.seed()
        aim_governance.remove(common.created())
        self.assertNotRegex(self.output().lower(), r"password|secret|token|bearer")


class Declared(Seed):
    def test_the_kinds_it_records_are_the_kinds_it_labels(self):
        self.assertEqual(sorted(aim_governance.LABELS), sorted(aim_governance.ORDER))
        self.assertTrue(all(label.endswith("s") for label in aim_governance.LABELS.values()))

    def test_the_rules_go_before_the_settings_and_the_answers_before_the_framework_they_are_checked_against(self):
        order = aim_governance.ORDER
        self.assertLess(order.index("approval-rule"), order.index("approval-settings"))
        self.assertLess(order.index("compliance-answers"), order.index("compliance-framework"))

    def test_it_reads_the_folder_of_the_governance_and_needs_agent_management(self):
        self.assertEqual((aim_governance.FOLDERS, aim_governance.GAMMA, aim_governance.SECTION),
                         (("aim/governance",), ("aim",), "aim-governance"))


@unittest.skipUnless((CONTRACT / "approvals.yaml").is_file() and (CONTRACT / "compliance.yaml").is_file(),
                     "the personal playground agent-toolbox is not on this machine")
class Contract(Governance):
    """The files of the playground, as their author wrote them."""

    def setUp(self):
        super().setUp()
        self.file(approvals=(CONTRACT / "approvals.yaml").read_text(encoding="utf-8"),
                  compliance=(CONTRACT / "compliance.yaml").read_text(encoding="utf-8"))

    @staticmethod
    def attested_by_the_file():
        """The controls the file attests: the fake platform cannot read any of them."""
        spec = yaml.safe_load((CONTRACT / "compliance.yaml").read_text(encoding="utf-8"))
        return [control for entry in spec["agents"] for control in entry["attest"]]

    def test_the_files_set_up_the_approval_of_refunds_and_the_compliance_of_the_agent_and_a_second_run_changes_nothing(self):
        aim_governance.seed()
        self.assertEqual(self.world.settings["defaultTtlSeconds"], 90)
        refund = self.rule()
        self.assertEqual((refund["toolPattern"], refund["argumentConditions"], refund["priority"]),
                         ("create_refund|createRefund", [{"path": "amount", "operator": "GT", "value": "100"}], 100))
        self.assertIn("eu-ai-act", self.world.active)
        answers = self.world.answers["agent-1"]
        self.assertEqual((answers["compliance.accountableHuman"], answers["riskClassification"]), ("user-admin", "limited"))
        self.assertNotIn("compliance.oversightMeasure", answers)
        self.assertEqual(self.world.attested["agent-1"], set(self.attested_by_the_file()))
        self.platform.calls.clear()
        aim_governance.seed()
        self.assertEqual(self.changed(), [])


if __name__ == "__main__":
    unittest.main()
