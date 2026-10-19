"""The MCP studio of the new shape (`access` in its file), against a platform that keeps what it is given and does what
the real one was seen to do: the studio is made with its registration plan, the plan of the agents is published, the
registration plan closed, the switches write their flows, an update of an API without its visibility makes it private.
The requests are those of the scripts that were run against the live platform."""

import copy
import itertools
import re
import unittest
from types import SimpleNamespace

import requests

from lab_seeder import aim_proxies
from lab_seeder import common
from lab_seeder.testkit import PERSONAL, Seed

AIM = "/gamma/organizations/DEFAULT/environments/DEFAULT/modules/aim"
V2 = "/management/v2/organizations/DEFAULT/environments/DEFAULT"
CONTRACT = PERSONAL / "agent-toolbox" / "seed" / "aim" / "studios" / "customer-care.yaml"
SETTINGS = {"AM_GATEWAY_URL": "http://am-gateway:8092", "AGENT_TOOLBOX_KEY": "harbor-key-123"}
PATH = "/mcp/customer-care"
READ_ONLY = ("id", "apiId", "createdAt", "updatedAt", "publishedAt", "closedAt", "status", "type", "referenceId", "referenceType")

STUDIO = """\
name: Customer care
description: The tools of Harbor's customer care team, made of three REST APIs
contextPath: /mcp/customer-care
protocolVersion: "2025-11-25"
tools:
  - {api: Harbor Customers API, tool: searchCustomers, alias: search_customers}
  - {api: Harbor Customers API, tool: getCustomer, alias: get_customer}
  - {api: Harbor Orders API, tool: createRefund, alias: create_refund}
upstreamAuth:
  - {api: Harbor Customers API, type: api-key, apiKeyHeader: X-Gravitee-Api-Key, apiKey: "${AGENT_TOOLBOX_KEY}"}
  - {api: Harbor Orders API, type: api-key, apiKeyHeader: X-Gravitee-Api-Key, apiKey: "${AGENT_TOOLBOX_KEY}"}
access:
  registration:
    name: Registration
    type: OAUTH2_AM
  agents:
    name: Agents
    type: JWT
    jwksUrl: ${AM_GATEWAY_URL}/harbor/oidc/.well-known/jwks.json
    clientIdClaim: client_id
authorization:
  enabled: true
  attributes:
    - {name: customer_ref, value: 'Customer::"{#request.jsonContent.params.arguments.customerId}"'}
    - {name: arg_amount, value: '{#request.jsonContent.params.arguments.amount}'}
  subject:
    region: "{#context.attributes['jwt.claims']['region']}"
    team: "{#context.attributes['jwt.claims']['team']}"
  context:
    amount: "{#context.attributes['arg_amount']}"
    customer: "{#context.attributes['customer_ref']}"
humanApproval:
  enabled: true
  holdWindowMs: 60000
"""
OLD_STUDIO = """\
name: Old studio
contextPath: /old
supportedProtocolVersions: ["2025-11-25"]
plan: {name: Agents, security: {type: OAUTH2_AM, configuration: {scopes: []}}}
fga: true
tools:
  - {api: Harbor Customers API, tool: getCustomer}
"""

WANTED_ATTRIBUTES = [{"name": "customer_ref", "value": 'Customer::"{#request.jsonContent.params.arguments.customerId}"'},
                     {"name": "arg_amount", "value": "{#request.jsonContent.params.arguments.amount}"}]
WANTED_SUBJECT = [{"name": "region", "value": "{#context.attributes['jwt.claims']['region']}"},
                  {"name": "team", "value": "{#context.attributes['jwt.claims']['team']}"}]
WANTED_CONTEXT = [{"name": "amount", "value": "{#context.attributes['arg_amount']}"},
                  {"name": "customer", "value": "{#context.attributes['customer_ref']}"}]


def ids(call):
    return tuple(re.findall(r"/(?:apis|plans|mcp-proxies|mcp-studios)/([^/]+)", call.path))


class World:
    """The catalog and API Management as far as a studio reaches them."""

    def __init__(self, operations):
        self.studio_ids, self.plan_ids = itertools.count(1), itertools.count(1)
        self.apim = {"Harbor Customers API": "apim-customers", "Harbor Orders API": "apim-orders", "Harbor Catalog API": "apim-catalog"}
        self.tools = {f"mcp-tool.{re.sub(r'[^a-z0-9_-]+', '-', api.lower())}-{op.lower()}": f"tool-{op}" for api, op in operations}
        self.studios, self.apis, self.plans = {}, {}, {}
        self.all_statuses = False   # a platform that lists the closed plans too
        self.gateway_calls, self.gateway_serves_after, self.gateway_status = [], 0, 401

    def routes(self):
        every = re.compile
        return {
            ("GET", AIM + "/mcp-proxies"): self.list_proxies,
            ("GET", every(AIM + "/mcp-proxies/[^/]+")): self.detail,
            ("DELETE", every(AIM + "/mcp-proxies/[^/]+")): self.delete,
            ("POST", AIM + "/mcp-studios"): self.create,
            ("PUT", every(AIM + "/mcp-studios/[^/]+/fga")): self.set_fga,
            ("PUT", every(AIM + "/mcp-proxies/[^/]+/human-approval")): self.set_approval,
            ("GET", AIM + "/catalog/mcp-tools"): lambda call: self.paged([{"id": i, "entityId": e} for e, i in self.tools.items()]),
            ("GET", AIM + "/catalog/api-tools/sources"): self.api_tools,
            ("GET", AIM + "/catalog/sources?sourceKind=apim.api"): lambda call: self.paged(
                [{"id": f"source-{i}", "definition": {"apiId": i}} for i in self.apim.values()]),
            ("GET", every(V2 + "/apis/[^/]+")): lambda call: (200, copy.deepcopy(self.apis[ids(call)[0]])),
            ("PUT", every(V2 + "/apis/[^/]+")): self.put_api,
            ("GET", every(V2 + "/apis/[^/]+/plans")): self.list_plans,
            ("POST", every(V2 + "/apis/[^/]+/plans")): self.create_plan,
            ("PUT", every(V2 + "/apis/[^/]+/plans/[^/]+")): self.put_plan,
            ("POST", every(V2 + "/apis/[^/]+/plans/[^/]+/_publish")): lambda call: self.plan_status(call, "PUBLISHED"),
            ("POST", every(V2 + "/apis/[^/]+/plans/[^/]+/_close")): lambda call: self.plan_status(call, "CLOSED"),
            ("POST", every(V2 + "/apis/[^/]+/deployments")): self.deployments,
            ("POST", "http://gateway" + PATH): self.gateway,
        }

    @staticmethod
    def paged(rows):
        return 200, {"data": rows, "pagination": {"totalCount": len(rows)}}

    # --- The catalog -------------------------------------------------------------------------

    def api_tools(self, call):
        return self.paged([{"id": i, "name": n} for n, i in self.apim.items() if call.params["q"] in n])

    # --- Gamma's view of the studios ---------------------------------------------------------

    def list_proxies(self, call):
        rows = [{"id": i, "contextPath": s["contextPath"], "name": s["name"], "mode": "STUDIO"} for i, s in self.studios.items()]
        return 200, {"content": rows, "totalElements": len(rows)}

    def detail(self, call):
        studio = self.studios[ids(call)[0]]
        return 200, {"id": ids(call)[0], "fgaEnabled": studio["fga"], "humanApprovalEnabled": studio["approval"]}

    def delete(self, call):
        studio_id = ids(call)[0]
        self.studios.pop(studio_id, None)
        self.apis.pop(studio_id, None)
        self.plans.pop(studio_id, None)
        return 204

    def create(self, call):
        studio_id = f"studio-{next(self.studio_ids)}"
        body = call.json
        self.studios[studio_id] = {"request": body, "name": body["name"], "contextPath": body["contextPath"], "fga": False, "approval": False}
        self.apis[studio_id] = {"id": studio_id, "name": body["name"], "visibility": "PUBLIC", "lifecycleState": "STARTED",
                                "deploymentState": "DEPLOYED", "definitionVersion": "V4", "type": "MCP_PROXY", "flows": [],
                                "createdAt": "2026-10-13T08:00:00Z", "updatedAt": "2026-10-13T08:00:00Z", "state": "STARTED",
                                "primaryOwner": {"id": "admin"}, "definitionContext": {"origin": "MANAGEMENT"}, "_links": {}}
        registration = body["plans"][0]
        self.plans[studio_id] = [self.plan(studio_id, registration["name"], registration["security"], "PUBLISHED")]
        return 201, {"id": studio_id, "name": body["name"], "contextPath": body["contextPath"], "mode": "STUDIO"}

    def plan(self, api_id, name, security, status):
        return {"id": f"plan-{next(self.plan_ids)}", "apiId": api_id, "name": name, "status": status, "definitionVersion": "V4",
                "mode": "STANDARD", "validation": "AUTO", "type": "API", "referenceId": api_id, "referenceType": "API",
                "createdAt": "2026-10-13T08:00:00Z", "updatedAt": "2026-10-13T08:00:00Z", "order": 1, "flows": [], "security": security}

    def set_fga(self, call):
        studio_id = ids(call)[0]
        self.studios[studio_id]["fga"] = call.json["enabled"]
        api = self.apis[studio_id]
        api["flows"] = [f for f in api["flows"] if f["name"] != "FGA Authorization"]
        if call.json["enabled"]:   # the flow the platform writes: its check reads the user and the tool, and nothing of the call
            api["flows"].append({"name": "FGA Authorization", "enabled": True, "selectors": [{"type": "MCP", "methods": ["tools/call"]}],
                                 "request": [{"name": "Fine-Grained Authorization", "enabled": True, "policy": "authz-pep", "configuration": {
                                     "subjectExpr": "{#context.attributes['user']}", "subjectType": "User", "resourceType": "MCPTool",
                                     "actionExpr": "{#context.attributes['mcp_tool_name']}",
                                     "resourceExpr": "{#context.attributes['mcp_tool_name']}", "denyStatus": 403}}], "response": []})
        api["deploymentState"] = "NEED_REDEPLOY"
        return 200, {}

    def set_approval(self, call):
        studio_id = ids(call)[0]
        self.studios[studio_id]["approval"] = call.json["enabled"]
        api = self.apis[studio_id]
        api["flows"] = [f for f in api["flows"] if f["name"] != "Human approval"]
        if call.json["enabled"]:
            step = {"name": "Human approval", "enabled": True, "policy": "human-approval",
                    "configuration": {"holdWindowMs": 40000, "guardSetRefreshSeconds": 30, "onBrokerUnavailable": "DENY"}}
            api["flows"].append({"name": "Human approval", "enabled": True, "selectors": [{"type": "MCP", "methods": ["tools/call"]}],
                                 "request": [copy.deepcopy(step)], "response": [copy.deepcopy(step)]})
        api["deploymentState"] = "NEED_REDEPLOY"
        return 200, {}

    # --- APIM ---------------------------------------------------------------------------------

    def put_api(self, call):
        """An update that does not say the visibility or the lifecycle puts the API back to private and created."""
        api = self.apis[ids(call)[0]]
        api.update({k: v for k, v in call.json.items() if k not in ("id", "deploymentState")})
        api["visibility"], api["lifecycleState"] = call.json.get("visibility", "PRIVATE"), call.json.get("lifecycleState", "CREATED")
        api["deploymentState"] = "NEED_REDEPLOY"
        return 200, api

    def list_plans(self, call):
        plans = [p for p in self.plans[ids(call)[0]] if self.all_statuses or p["status"] == "PUBLISHED"]
        return self.paged(copy.deepcopy(plans))

    def create_plan(self, call):
        plan = self.plan(ids(call)[0], call.json["name"], call.json["security"], "STAGING")
        plan.update({k: v for k, v in call.json.items() if k != "security"})
        self.plans[ids(call)[0]].append(plan)
        return 201, copy.deepcopy(plan)

    def find_plan(self, call):
        return next(p for p in self.plans[ids(call)[0]] if p["id"] == ids(call)[1])

    def put_plan(self, call):
        plan = self.find_plan(call)
        plan.update(call.json)
        self.apis[ids(call)[0]]["deploymentState"] = "NEED_REDEPLOY"
        return 200, copy.deepcopy(plan)

    def plan_status(self, call, status):
        plan = self.find_plan(call)
        plan["status"] = status
        self.apis[ids(call)[0]]["deploymentState"] = "NEED_REDEPLOY"
        return 200, copy.deepcopy(plan)

    def deployments(self, call):
        self.apis[ids(call)[0]]["deploymentState"] = "DEPLOYED"
        return 202

    def gateway(self, call):
        self.gateway_calls.append(call)
        if len(self.gateway_calls) <= self.gateway_serves_after:
            return 404, {"message": "No context-path matches the request URI."}
        return self.gateway_status, {"error": "unauthorized"}


class Studios(Seed):
    OPERATIONS = [("Harbor Customers API", "searchCustomers"), ("Harbor Customers API", "getCustomer"),
                  ("Harbor Customers API", "deleteCustomer"), ("Harbor Orders API", "createRefund"), ("Harbor Orders API", "cancelOrder")]

    def setUp(self):
        super().setUp()
        self.world = World(self.OPERATIONS)
        self.platform.add(self.world.routes())
        self.env(**SETTINGS, GATEWAY_INTERNAL_URL="http://gateway", GATEWAY_READY_TIMEOUT="30")
        self.file(STUDIO)

    def file(self, text, name="customer-care"):
        self.seed_files({f"aim/studios/{name}.yaml": text})

    def seed(self):
        aim_proxies.seed()

    def run_twice(self):
        self.seed()
        self.platform.calls.clear()
        self.seed()

    def the_studio(self):
        [studio_id] = self.world.studios
        return studio_id

    def the_api(self):
        return self.world.apis[self.the_studio()]

    def the_flow(self, name):
        return next(f for f in self.the_api()["flows"] if f["name"] == name)

    def changes(self):
        """What was written to the platform, as method and path without its prefix. The call to the gateway that asks
        whether it serves the studio is a POST that writes nothing."""
        return [(m, p.replace(V2, "").replace(AIM, "")) for m, p in self.platform.changes() if p != PATH]

    def plan_named(self, name, status=None):
        return next(p for p in self.world.plans[self.the_studio()] if p["name"] == name and status in (None, p["status"]))

    def sent(self, method, tail):
        return [c for c in self.platform.calls if (c.method, c.path) == (method, tail)]


class Creation(Studios):

    def test_the_studio_is_created_with_the_body_the_live_platform_took(self):
        self.seed()
        [call] = self.platform.sent("POST", AIM + "/mcp-studios")
        self.assertEqual(call.json, {
            "name": "Customer care", "description": "The tools of Harbor's customer care team, made of three REST APIs",
            "contextPath": "/mcp/customer-care", "protocolVersion": "2025-11-25",
            "specification": {"tools": [{"id": "tool-searchCustomers", "alias": "search_customers"},
                                        {"id": "tool-getCustomer", "alias": "get_customer"},
                                        {"id": "tool-createRefund", "alias": "create_refund"}]},
            "plans": [{"name": "Registration", "security": {"type": "OAUTH2_AM", "configuration": {"scopes": []}}}],
            "fgaEnabled": False,
            "upstreamAuth": [
                {"catalogSourceId": "source-apim-customers", "upstreamAuth": {"type": "api-key", "apiKeyHeader": "X-Gravitee-Api-Key", "apiKey": "harbor-key-123"}},
                {"catalogSourceId": "source-apim-orders", "upstreamAuth": {"type": "api-key", "apiKeyHeader": "X-Gravitee-Api-Key", "apiKey": "harbor-key-123"}}]})

    def test_a_tool_without_an_alias_goes_by_its_own_name(self):
        self.file(STUDIO.replace(", alias: search_customers", ""))
        self.seed()
        [call] = self.platform.sent("POST", AIM + "/mcp-studios")
        self.assertEqual(call.json["specification"]["tools"][0], {"id": "tool-searchCustomers"})

    def test_a_studio_without_a_description_is_made_without_one(self):
        self.file(STUDIO.replace("description: The tools of Harbor's customer care team, made of three REST APIs\n", ""))
        self.seed()
        [call] = self.platform.sent("POST", AIM + "/mcp-studios")
        self.assertNotIn("description", call.json)

    def test_every_step_runs_in_order_and_the_registration_plan_is_closed_after_the_agents_plan_is_published(self):
        self.seed()
        self.assertEqual(self.changes(), [
            ("POST", "/mcp-studios"),
            ("POST", "/apis/studio-1/plans"),
            ("POST", "/apis/studio-1/plans/plan-2/_publish"),
            ("POST", "/apis/studio-1/plans/plan-1/_close"),
            ("PUT", "/mcp-studios/studio-1/fga"),
            ("PUT", "/mcp-proxies/studio-1/human-approval"),
            ("PUT", "/apis/studio-1"),
            ("POST", "/apis/studio-1/deployments")])

    def test_the_agents_plan_is_the_jwt_plan_that_was_run_for_real(self):
        self.seed()
        [call] = self.sent("POST", V2 + "/apis/studio-1/plans")
        self.assertEqual(call.json, {
            "definitionVersion": "V4", "name": "Agents", "mode": "STANDARD", "validation": "AUTO",
            "description": "Agents signed in by Access Management, one subscription each",
            "characteristics": [], "tags": [], "excludedGroups": [], "commentRequired": False, "flows": [],
            "security": {"type": "JWT", "configuration": {
                "signature": "RSA_RS256", "publicKeyResolver": "JWKS_URL",
                "resolverParameter": "http://am-gateway:8092/harbor/oidc/.well-known/jwks.json",
                "connectTimeout": 2000, "requestTimeout": 2000, "followRedirects": False, "useSystemProxy": False,
                "extractClaims": True, "propagateAuthHeader": True, "userClaim": "sub", "clientIdClaim": "client_id",
                "checkRequiredScopes": False, "modeStrict": True}}})
        self.assertEqual(self.plan_named("Agents")["status"], "PUBLISHED")
        self.assertEqual(self.plan_named("Registration")["status"], "CLOSED")

    def test_the_switches_are_turned_with_the_bodies_that_were_run_for_real(self):
        self.seed()
        self.assertEqual([c.json for c in self.sent("PUT", AIM + "/mcp-studios/studio-1/fga")], [{"enabled": True}])
        self.assertEqual([c.json for c in self.sent("PUT", AIM + "/mcp-proxies/studio-1/human-approval")], [{"enabled": True}])

    def test_the_studio_is_recorded_with_its_digest_for_its_removal(self):
        self.seed()
        [studio] = [x for x in common.created() if x["kind"] == "studio"]
        self.assertEqual((studio["product"], studio["label"], studio["name"], studio["id"]),
                         ("aim-proxies", "MCP studios", "Customer care", "studio-1"))
        self.assertEqual(len(studio["digest"]), 64)

    def test_the_api_key_never_reaches_the_log(self):
        self.seed()
        self.assertNotIn("harbor-key-123", self.output())


class Flows(Studios):

    def test_the_authorization_flow_reads_the_call_and_the_person(self):
        self.seed()
        steps = self.the_flow("FGA Authorization")["request"]
        self.assertEqual([s["policy"] for s in steps], ["policy-assign-attributes", "authz-pep"])
        self.assertEqual(steps[0]["enabled"], True)
        self.assertEqual(steps[0]["configuration"], {"scope": "REQUEST", "attributes": WANTED_ATTRIBUTES})
        check = steps[1]["configuration"]
        self.assertEqual((check["subjectPropertiesExpr"], check["contextExpr"]), (WANTED_SUBJECT, WANTED_CONTEXT))
        self.assertEqual(check["resourceType"], "MCPTool")   # what the switch wrote stays

    def test_the_hold_window_is_on_the_step_of_each_phase(self):
        self.seed()
        flow = self.the_flow("Human approval")
        windows = [s["configuration"]["holdWindowMs"] for phase in ("request", "response") for s in flow[phase]]
        self.assertEqual(windows, [60000, 60000])
        self.assertEqual(flow["request"][0]["configuration"]["onBrokerUnavailable"], "DENY")

    def test_the_authorization_flow_runs_before_the_approval_flow(self):
        self.seed()
        self.assertEqual([f["name"] for f in self.the_api()["flows"]], ["FGA Authorization", "Human approval"])

    def test_flows_in_the_other_order_are_put_in_this_one(self):
        # the platform appends the flow of the switch turned on last: human approval first, then the authorization flow
        self.seed()
        self.the_api()["flows"].reverse()
        self.platform.calls.clear()
        self.seed()
        self.assertEqual([f["name"] for f in self.the_api()["flows"]], ["FGA Authorization", "Human approval"])
        self.assertEqual(self.changes(), [("PUT", "/apis/studio-1"), ("POST", "/apis/studio-1/deployments")])

    def test_the_visibility_and_the_lifecycle_of_the_api_are_kept(self):
        self.seed()
        [call] = self.sent("PUT", V2 + "/apis/studio-1")
        self.assertEqual((call.json["visibility"], call.json["lifecycleState"]), ("PUBLIC", "STARTED"))
        self.assertEqual((self.the_api()["visibility"], self.the_api()["lifecycleState"]), ("PUBLIC", "STARTED"))

    def test_the_api_is_written_without_what_it_does_not_take(self):
        self.seed()
        [call] = self.sent("PUT", V2 + "/apis/studio-1")
        for key in ("id", "deploymentState", "createdAt", "updatedAt", "state", "definitionContext", "_links", "primaryOwner"):
            self.assertNotIn(key, call.json)
        self.assertEqual((call.json["name"], call.json["definitionVersion"]), ("Customer care", "V4"))

    def test_the_api_is_written_once_for_both_flows(self):
        self.seed()
        self.assertEqual(len(self.sent("PUT", V2 + "/apis/studio-1")), 1)

    def test_an_authorization_check_without_attributes_adds_no_step(self):
        text = re.sub(r"  attributes:\n(    - .*\n)+", "", STUDIO)
        self.file(text)
        self.seed()
        self.assertEqual([s["policy"] for s in self.the_flow("FGA Authorization")["request"]], ["authz-pep"])

    def test_the_subject_and_the_context_may_be_written_as_lists(self):
        text = re.sub(r"  subject:\n(    .*\n)+", "  subject:\n    - {name: region, value: \"{#context.attributes['jwt.claims']['region']}\"}\n"
                      "    - {name: team, value: \"{#context.attributes['jwt.claims']['team']}\"}\n", STUDIO, count=1)
        self.file(text)
        self.seed()
        self.assertEqual(self.the_flow("FGA Authorization")["request"][1]["configuration"]["subjectPropertiesExpr"], WANTED_SUBJECT)


class SecondRun(Studios):

    def test_nothing_is_written_the_second_time(self):
        self.run_twice()
        self.assertEqual(self.changes(), [])

    def test_the_switches_are_not_turned_again(self):
        self.run_twice()
        self.assertEqual(self.sent("PUT", AIM + "/mcp-studios/studio-1/fga"), [])
        self.assertEqual(self.sent("PUT", AIM + "/mcp-proxies/studio-1/human-approval"), [])

    def test_the_registration_plan_is_closed_once(self):
        self.run_twice()
        self.assertEqual(self.sent("POST", V2 + "/apis/studio-1/plans/plan-1/_close"), [])
        self.assertEqual(self.sent("POST", V2 + "/apis/studio-1/plans"), [])

    def test_a_closed_plan_a_platform_lists_is_left_closed(self):
        self.seed()
        self.world.all_statuses = True
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.changes(), [])

    def test_a_registration_plan_that_is_open_again_is_closed_again(self):
        self.seed()
        self.plan_named("Registration")["status"] = "PUBLISHED"
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.changes(), [("POST", "/apis/studio-1/plans/plan-1/_close"), ("POST", "/apis/studio-1/deployments")])

    def test_one_deployment_when_the_api_says_it_waits_for_one(self):
        self.seed()
        self.the_api()["deploymentState"] = "NEED_REDEPLOY"
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.changes(), [("POST", "/apis/studio-1/deployments")])

    def test_the_first_run_deploys_once(self):
        self.seed()
        self.assertEqual(len(self.sent("POST", V2 + "/apis/studio-1/deployments")), 1)

    def test_a_flow_someone_edited_is_written_again_without_turning_the_switch(self):
        self.seed()
        self.the_flow("FGA Authorization")["request"][1]["configuration"]["contextExpr"] = []
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.changes(), [("PUT", "/apis/studio-1"), ("POST", "/apis/studio-1/deployments")])
        self.assertEqual(self.the_flow("FGA Authorization")["request"][1]["configuration"]["contextExpr"], WANTED_CONTEXT)

    def test_a_step_someone_removed_is_put_back_before_the_check(self):
        self.seed()
        steps = self.the_flow("FGA Authorization")["request"]
        del steps[0]
        self.platform.calls.clear()
        self.seed()
        self.assertEqual([s["policy"] for s in self.the_flow("FGA Authorization")["request"]], ["policy-assign-attributes", "authz-pep"])
        self.assertEqual(self.sent("PUT", AIM + "/mcp-studios/studio-1/fga"), [])

    def test_a_step_someone_put_behind_the_check_is_put_before_it(self):
        self.seed()
        steps = self.the_flow("FGA Authorization")["request"]
        steps.reverse()
        self.platform.calls.clear()
        self.seed()
        self.assertEqual([s["policy"] for s in self.the_flow("FGA Authorization")["request"]], ["policy-assign-attributes", "authz-pep"])

    def test_a_step_someone_disabled_is_enabled_again(self):
        self.seed()
        self.the_flow("FGA Authorization")["request"][0]["enabled"] = False
        self.seed()
        self.assertTrue(self.the_flow("FGA Authorization")["request"][0]["enabled"])

    def test_a_hold_window_someone_changed_is_set_again(self):
        self.seed()
        self.the_flow("Human approval")["response"][0]["configuration"]["holdWindowMs"] = 1000
        self.seed()
        self.assertEqual(self.the_flow("Human approval")["response"][0]["configuration"]["holdWindowMs"], 60000)

    def test_a_switch_someone_turned_off_is_turned_on_again_and_its_flow_written_again(self):
        self.seed()
        self.platform.calls.clear()
        self.world.set_fga(SimpleNamespace(path=AIM + "/mcp-studios/studio-1/fga", json={"enabled": False}))
        self.seed()
        self.assertEqual(self.changes(), [("PUT", "/mcp-studios/studio-1/fga"), ("PUT", "/apis/studio-1"), ("POST", "/apis/studio-1/deployments")])
        self.assertEqual([s["policy"] for s in self.the_flow("FGA Authorization")["request"]], ["policy-assign-attributes", "authz-pep"])

    def test_a_file_that_turns_the_check_off_turns_the_switch_off(self):
        self.seed()
        self.file(STUDIO.replace("authorization:\n  enabled: true", "authorization:\n  enabled: false"))
        self.platform.calls.clear()
        self.seed()
        self.assertEqual([c.json for c in self.sent("PUT", AIM + "/mcp-studios/studio-1/fga")], [{"enabled": False}])
        self.assertNotIn("FGA Authorization", [f["name"] for f in self.the_api()["flows"]])

    def test_a_file_without_authorization_or_approval_leaves_the_switches_alone(self):
        self.file(STUDIO[:STUDIO.index("authorization:")])
        self.seed()
        self.assertEqual(self.sent("PUT", AIM + "/mcp-studios/studio-1/fga"), [])
        self.assertEqual(self.sent("PUT", AIM + "/mcp-proxies/studio-1/human-approval"), [])
        self.assertEqual(self.sent("PUT", V2 + "/apis/studio-1"), [])
        self.assertEqual(self.changes()[-1], ("POST", "/apis/studio-1/deployments"))


class Replacement(Studios):

    def test_a_changed_alias_replaces_the_studio_and_its_record_follows(self):
        self.seed()
        self.file(STUDIO.replace("alias: get_customer", "alias: customer"))
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.changes()[:2], [("DELETE", "/mcp-proxies/studio-1"), ("POST", "/mcp-studios")])
        self.assertEqual(list(self.world.studios), ["studio-2"])
        self.assertEqual([x["id"] for x in common.created() if x["kind"] == "studio"], ["studio-2"])
        self.assertEqual(self.plan_named("Agents")["status"], "PUBLISHED")
        self.assertEqual(self.plan_named("Registration")["status"], "CLOSED")
        self.assertEqual(self.sent("PUT", AIM + "/mcp-studios/studio-2/fga")[0].json, {"enabled": True})

    def test_a_changed_key_replaces_the_studio(self):
        self.seed()
        self.env(AGENT_TOOLBOX_KEY="another-key")
        self.seed()
        self.assertEqual(list(self.world.studios), ["studio-2"])

    def test_a_changed_catalog_source_replaces_the_studio(self):
        self.seed()
        self.world.apim["Harbor Orders API"] = "apim-orders-again"
        self.seed()
        self.assertEqual(list(self.world.studios), ["studio-2"])

    def test_a_changed_flow_or_plan_setting_edits_in_place(self):
        self.seed()
        self.file(STUDIO.replace("holdWindowMs: 60000", "holdWindowMs: 90000"))
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.changes(), [("PUT", "/apis/studio-1"), ("POST", "/apis/studio-1/deployments")])
        self.assertEqual(self.the_flow("Human approval")["request"][0]["configuration"]["holdWindowMs"], 90000)

    def test_a_studio_the_seed_did_not_create_is_never_adopted(self):
        self.world.studios["theirs"] = {"name": "Theirs", "contextPath": PATH, "fga": False, "approval": False}
        with self.assertRaisesRegex(common.SeedError, "/mcp/customer-care is the path of 'Theirs', which the seed did not create"):
            self.seed()
        self.assertEqual(self.platform.sent("DELETE", re.compile(AIM + "/mcp-proxies/.*")), [])


class AgentsPlan(Studios):

    def test_a_jwt_plan_that_differs_is_updated_in_place(self):
        self.seed()
        self.file(STUDIO.replace("clientIdClaim: client_id", "clientIdClaim: azp"))
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.changes(), [("PUT", "/apis/studio-1/plans/plan-2"), ("POST", "/apis/studio-1/deployments")])
        [call] = self.sent("PUT", V2 + "/apis/studio-1/plans/plan-2")
        self.assertEqual(call.json["security"]["configuration"]["clientIdClaim"], "azp")
        self.assertEqual((call.json["definitionVersion"], call.json["mode"], call.json["name"]), ("V4", "STANDARD", "Agents"))
        for key in READ_ONLY:
            self.assertNotIn(key, call.json)
        self.assertEqual(len([p for p in self.world.plans["studio-1"] if p["name"] == "Agents"]), 1)

    def test_a_default_the_platform_added_to_the_plan_is_no_change(self):
        self.seed()
        self.plan_named("Agents")["security"]["configuration"]["tokenCacheEnabled"] = False
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.changes(), [])

    def test_the_registration_plan_is_not_closed_before_the_agents_plan_is_published(self):
        original = self.world.plan_status

        def refuse(call, status):
            if status == "PUBLISHED":
                return 500, {"message": "boom"}
            return original(call, status)

        self.world.plan_status = refuse
        with self.assertRaisesRegex(common.SeedError, "HTTP 500"):
            self.seed()
        self.assertEqual(self.plan_named("Registration")["status"], "PUBLISHED")
        self.assertEqual(self.sent("POST", V2 + "/apis/studio-1/plans/plan-1/_close"), [])


class Gateway(Studios):

    def test_the_gateway_is_not_asked_unless_the_seed_is_asked_to_wait_for_it(self):
        self.env(SEED_WAIT_GATEWAY="0")
        self.seed()
        self.assertEqual(self.world.gateway_calls, [])
        self.assertEqual(self.clock.sleeps, [])

    def test_the_seed_waits_until_the_gateway_refuses_a_call_without_a_token(self):
        self.world.gateway_serves_after = 2
        self.seed()
        self.assertEqual(len(self.world.gateway_calls), 3)
        self.assertEqual(self.clock.sleeps, [0.5, 1])
        self.assertIn("✓ Customer care on the gateway (/mcp/customer-care) ready", self.output())

    def test_the_call_is_an_unauthenticated_initialize_of_the_protocol_version_of_the_file(self):
        self.seed()
        [call] = self.world.gateway_calls[:1]
        self.assertEqual((call.json["method"], call.json["params"]["protocolVersion"]), ("initialize", "2025-11-25"))
        self.assertNotIn("Authorization", call.headers)
        self.assertIn("text/event-stream", call.headers["Accept"])

    def test_a_gateway_that_never_serves_it_ends_in_one_line(self):
        self.world.gateway_serves_after = 10 ** 6
        with self.assertRaises(common.SeedError) as raised:
            self.seed()
        message = str(raised.exception)
        self.assertEqual(message, "Customer care on the gateway (/mcp/customer-care) not ready after 30 s: "
                                  "HTTP 404 without a token, 401 expected")
        self.assertNotIn("\n", message)

    def test_a_studio_that_lets_a_call_without_a_token_in_is_not_ready(self):
        self.world.gateway_status = 200
        with self.assertRaisesRegex(common.SeedError, "HTTP 200 without a token, 401 expected"):
            self.seed()

    def test_a_gateway_that_does_not_answer_ends_in_one_line(self):
        self.platform.add({("POST", "http://gateway" + PATH): requests.ConnectionError("Max retries exceeded\nwith url: /mcp")})
        with self.assertRaises(common.SeedError) as raised:
            self.seed()
        self.assertNotIn("\n", str(raised.exception))
        self.assertIn("Max retries exceeded with url", str(raised.exception))

    def test_a_seed_that_does_not_know_the_gateway_does_not_wait(self):
        self.env(GATEWAY_INTERNAL_URL=None)
        self.seed()
        self.assertEqual(self.world.gateway_calls, [])

    def test_the_second_run_asks_the_gateway_too(self):
        self.run_twice()
        self.assertEqual(len(self.world.gateway_calls), 2)


class Errors(Studios):

    def failing(self, pattern):
        with self.assertRaises(common.SeedError) as raised:
            self.seed()
        self.assertRegex(str(raised.exception), pattern)
        self.assertNotIn("\n", str(raised.exception))

    def test_a_tool_the_catalog_does_not_have_stops_the_seed_naming_it(self):
        del self.world.tools["mcp-tool.harbor-orders-api-createrefund"]
        self.failing("MCP studio: the tool mcp-tool.harbor-orders-api-createrefund is not in the catalog")
        self.assertEqual(self.world.studios, {})

    def test_an_api_without_tools_in_the_catalog_stops_the_seed_naming_it(self):
        del self.world.apim["Harbor Orders API"]
        self.failing("the API 'Harbor Orders API' has no tools in the catalog")
        self.assertEqual(self.world.studios, {})

    def test_an_api_that_has_a_namesake_is_not_mistaken_for_it(self):
        self.world.apim = {"Harbor Orders API v2": "apim-orders-2", "Harbor Customers API": "apim-customers"}
        self.failing("the API 'Harbor Orders API' has no tools in the catalog")

    def test_a_missing_key_of_the_access_is_named(self):
        self.file(STUDIO.replace("    clientIdClaim: client_id\n", ""))
        self.failing(r"MCP studio Customer care: access\.agents\.clientIdClaim is missing")

    def test_a_missing_registration_plan_is_named(self):
        self.file(STUDIO.replace("  registration:\n    name: Registration\n    type: OAUTH2_AM\n", ""))
        self.failing(r"MCP studio Customer care: access\.registration is missing")

    def test_a_plan_of_the_agents_that_is_not_jwt_is_refused(self):
        self.file(STUDIO.replace("    type: JWT", "    type: OAUTH2_AM"))
        self.failing("access.agents.type is OAUTH2_AM, only JWT keeps the subscription required")

    def test_a_variable_no_seed_service_sets_is_named(self):
        self.env(AM_GATEWAY_URL=None)
        with self.assertRaisesRegex(common.SeedError, "AM_GATEWAY_URL not set"):
            self.seed()

    def test_a_refusal_of_the_platform_is_one_line(self):
        self.platform.add({("POST", AIM + "/mcp-studios"): (400, '{\n  "message": "contextPath already used"\n}')})
        self.failing(r"POST /mcp-studios: HTTP 400 \{ \"message\": \"contextPath already used\" \}")

    def test_a_switch_that_wrote_no_flow_is_named(self):
        self.platform.add({("PUT", re.compile(AIM + "/mcp-studios/[^/]+/fga")): lambda call: (200, {})})
        self.failing("the API has no flow 'FGA Authorization' although its switch is on")

    def test_an_approval_flow_without_a_step_of_the_policy_is_named(self):
        original = self.world.set_approval

        def without_steps(call):
            original(call)
            for flow in self.world.apis[ids(call)[0]]["flows"]:
                if flow["name"] == "Human approval":
                    flow["request"], flow["response"] = [], []
            return 200, {}

        self.platform.add({("PUT", re.compile(AIM + "/mcp-proxies/[^/]+/human-approval")): without_steps})
        self.failing("the flow 'Human approval' has no human-approval step")


class OldShape(Studios):

    def setUp(self):
        super().setUp()
        self.file(OLD_STUDIO, "customer-care")

    def test_a_file_without_access_is_made_the_way_it_always_was(self):
        self.env(GATEWAY_INTERNAL_URL=None)
        self.seed()
        [call] = self.platform.sent("POST", AIM + "/mcp-studios")
        self.assertEqual(call.json["supportedProtocolVersions"], ["2025-11-25"])
        self.assertEqual(call.json["plans"], [{"name": "Agents", "security": {"type": "OAUTH2_AM", "configuration": {"scopes": []}}}])
        self.assertEqual(call.json["specification"]["promptIds"], [])
        self.assertEqual(self.changes(), [("POST", "/mcp-studios"), ("PUT", "/mcp-studios/studio-1/fga"), ("POST", "/apis/studio-1/deployments")])
        self.assertEqual(self.world.gateway_calls, [])


class Removal(Studios):

    def test_the_studio_is_deleted_through_gamma(self):
        self.seed()
        self.platform.calls.clear()
        aim_proxies.remove(common.created())
        self.assertEqual(self.changes(), [("DELETE", "/mcp-proxies/studio-1")])
        self.assertEqual(self.world.studios, {})
        self.assertIn("✓ Deleted MCP studio 'Customer care'", self.output())


@unittest.skipUnless(CONTRACT.is_file(), "the personal playground agent-toolbox is not on this machine")
class Contract(Studios):
    """The studio file of the playground, as its author wrote it."""

    OPERATIONS = [(api, op) for api, ops in (
        ("Harbor Customers API", ("searchCustomers", "getCustomer")),
        ("Harbor Orders API", ("listOrders", "getOrder", "listRefunds", "createRefund", "cancelOrder")),
        ("Harbor Catalog API", ("searchProducts", "getProduct", "getStock"))) for op in ops]

    def setUp(self):
        super().setUp()
        self.env(AM_GATEWAY_URL="http://am-gateway:8092", AGENT_TOOLBOX_KEY="harbor-tool-layer-key")
        self.file(CONTRACT.read_text(encoding="utf-8"))

    def test_the_file_builds_the_studio_and_a_second_run_changes_nothing(self):
        self.run_twice()
        self.assertEqual(self.changes(), [])
        studio = self.world.studios["studio-1"]["request"]
        self.assertEqual(len(studio["specification"]["tools"]), 10)
        self.assertEqual(len(studio["upstreamAuth"]), 3)
        self.assertEqual([f["name"] for f in self.the_api()["flows"]], ["FGA Authorization", "Human approval"])
        self.assertEqual(self.the_flow("Human approval")["request"][0]["configuration"]["holdWindowMs"], 90000)
        check = self.the_flow("FGA Authorization")["request"]
        self.assertEqual([s["policy"] for s in check], ["policy-assign-attributes", "authz-pep"])
        self.assertEqual(len(check[0]["configuration"]["attributes"]), 4)
        self.assertEqual([e["name"] for e in check[1]["configuration"]["subjectPropertiesExpr"]], ["region", "team"])
        self.assertEqual([e["name"] for e in check[1]["configuration"]["contextExpr"]], ["amount", "customer", "order", "region"])


if __name__ == "__main__":
    unittest.main()
