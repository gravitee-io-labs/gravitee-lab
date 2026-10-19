"""The MCP proxy of Acme Bakery and its studio, from their real seed files, against a platform that keeps what it is given:
the three steps of the proxy, a second run that writes nothing, what is put back when someone changed it, and removal."""

import itertools
import json
import re
import unittest

from lab_seeder import aim_proxies
from lab_seeder import common
from lab_seeder.testkit import PLAYGROUNDS, Seed

AIM = "/gamma/organizations/DEFAULT/environments/DEFAULT/modules/aim"
AUTOMATION = "/automation/organizations/DEFAULT/environments/DEFAULT/aim/mcp-proxies"
V2 = "/management/v2/organizations/DEFAULT/environments/DEFAULT"
SEED = PLAYGROUNDS / "acme-bakery" / "seed" / "aim"
PROXY = (SEED / "mcp-proxies" / "acme-bakery-mcp.yaml").read_text(encoding="utf-8")
CONTROLS = (SEED.parent / "controls.yaml").read_text(encoding="utf-8")
STUDIO = (SEED / "studios" / "acme-bakery-toolkit.yaml").read_text(encoding="utf-8")
STUDIO_UNCHECKED = STUDIO.replace("fga: true", "fga: false")
SETTINGS = {"BAKERY_MCP_URL": "http://bakery-mcp:8000", "AM_GATEWAY_URL": "http://am-gateway:8092", "MCP_RESOURCE_SECRET": "mcp-secret",
            "TRAFFIC_CLIENT_SECRET": "traffic-secret", "BANK_DEMO_PASSWORD": "demo-password"}
TOKEN_URL = "http://am-gateway:8092/acme-bakery/oauth/token"
TOOLS = ["list_accounts", "get_balance", "list_transactions", "transfer_funds", "freeze_card", "get_exchange_rate",
         "simulate_loan", "find_branches"]
HRID = "acme-bakery-mcp-proxy"


def ids(call):
    return tuple(re.findall(r"/(?:apis|plans|mcp-proxies|mcp-studios)/([^/]+)", call.path))


class World:
    """What the proxy and studio calls reach: the catalog, the Automation API, the APIM API behind it (which it writes
    through, as the real one does), Gamma's view of the proxies, Access Management's token endpoint and the gateway.
    Like the real ones it adds defaults, forgets what it does not give back (a secret) and says when an API waits for a
    deployment."""

    def __init__(self):
        self.next_id = itertools.count(1)
        self.servers = [{"id": "server-1", "entityId": "mcp-server.acme-bakery-mcp", "slug": "acme-bakery-mcp",
                         "definition": {"serverInfo": {"name": "Acme Bakery MCP"}}}]
        self.tools = {f"mcp-tool.acme-bakery-mcp.{t}": f"tool-{t}" for t in TOOLS}
        operations = ("listBranches", "getBranch", "listRates", "getRate")
        self.tools.update({f"mcp-tool.acme-bakery-open-api-{t.lower()}": f"tool-{t}" for t in operations})
        self.proxies = {}        # hrid -> what the Automation API gives back
        self.apis = {}           # api id -> the API, its plans and its deployment state
        self.studios = {}        # id -> the studio as it was created, with its authorization check
        self.gateway_calls, self.gateway_serves_after, self.tools_offered, self.event_stream = [], 0, {"alice": 7, "tina": 8}, False

    def routes(self):
        return {
            ("GET", AIM + "/mcp-proxies"): self.list_proxies,
            ("GET", re.compile(AIM + "/mcp-proxies/[^/]+")): self.detail,
            ("PUT", re.compile(AIM + "/mcp-proxies/[^/]+/protocol-versions")): self.set_versions,
            ("DELETE", re.compile(AIM + "/mcp-proxies/[^/]+")): self.delete_proxy,
            ("POST", AIM + "/mcp-studios"): self.create_studio,
            ("PUT", re.compile(AIM + "/mcp-studios/[^/]+/fga")): self.set_fga,
            ("GET", AIM + "/catalog/mcp-servers"): lambda call: self.paged(self.servers),
            ("GET", AIM + "/catalog/mcp-tools"): lambda call: self.paged([{"id": i, "entityId": e} for e, i in self.tools.items()]),
            ("GET", re.compile(AUTOMATION + "/[^/]+")): self.get_proxy,
            ("PUT", AUTOMATION): self.put_proxy,
            ("DELETE", re.compile(AUTOMATION + "/[^/]+")): self.delete_automation_proxy,
            ("GET", re.compile(V2 + "/apis/[^/]+")): lambda call: (200, self.apis[ids(call)[0]]),
            ("PUT", re.compile(V2 + "/apis/[^/]+")): self.put_api,
            ("GET", re.compile(V2 + "/apis/[^/]+/plans")): self.list_plans,
            ("PUT", re.compile(V2 + "/apis/[^/]+/plans/[^/]+")): self.put_plan,
            ("POST", re.compile(V2 + "/apis/[^/]+/deployments")): self.deployments,
            ("POST", TOKEN_URL): self.sign_in,
            ("POST", "http://gateway/acme-bakery/mcp"): self.gateway,
        }

    @staticmethod
    def paged(rows):
        return 200, {"data": rows, "pagination": {"totalCount": len(rows)}}

    # --- The Automation API and APIM --------------------------------------------------------

    def get_proxy(self, call):
        hrid = call.path.rsplit("/", 1)[1]
        return (200, self.proxies[hrid]) if hrid in self.proxies else (404, {"technicalCode": "hrid.notFound"})

    def put_proxy(self, call):
        spec = json.loads(json.dumps(call.json))
        api_id = f"api-{spec['hrid']}"
        for provider in spec["identityProviders"]:
            provider.pop("clientSecret")
        spec["proxy"].pop("upstreamAuth", None)
        for plan in spec["plans"]:
            plan["security"].setdefault("scopes", [])
        self.proxies[spec["hrid"]] = {**spec, "id": api_id, "environmentId": "DEFAULT"}
        if api_id not in self.apis:   # the platform makes the API with a plan that has no `extractPayload`
            self.apis[api_id] = {"id": api_id, "name": spec["name"], "contextPath": spec["contextPath"], "deploymentState": "DEPLOYED",
                                 "supported": ["2025-11-25"], "endpointSupported": ["2025-11-25"],
                                 "endpointGroups": [{"name": "default-group", "type": "mcp-proxy"}],
                                 "plans": [{"id": "plan-1", "apiId": api_id, "name": "Bank users", "status": "PUBLISHED", "order": 1,
                                            "security": {"type": "OAUTH2", "configuration": {"oauthResource": "acme-am"}}}]}
        else:
            self.apis[api_id]["deploymentState"] = "DEPLOYED"   # the Automation API deploys what it changes
        return 200, self.proxies[spec["hrid"]]

    def delete_automation_proxy(self, call):
        proxy = self.proxies.pop(call.path.rsplit("/", 1)[1], None)
        self.apis.pop(proxy and proxy["id"], None)
        return 204

    def put_api(self, call):
        api = self.apis[ids(call)[0]]
        api.update({k: v for k, v in call.json.items() if k in ("endpointGroups", "responseTemplates", "analytics")})
        api["deploymentState"] = "NEED_REDEPLOY"
        return 200, api

    def list_plans(self, call):
        plans = self.apis[ids(call)[0]]["plans"]
        return 200, {"data": plans, "pagination": {"totalCount": len(plans)}}

    def put_plan(self, call):
        api = self.apis[ids(call)[0]]
        plan = next(p for p in api["plans"] if p["id"] == ids(call)[1])
        plan["security"] = call.json["security"]
        api["deploymentState"] = "NEED_REDEPLOY"
        return 200, plan

    def deployments(self, call):
        self.apis[ids(call)[0]]["deploymentState"] = "DEPLOYED"
        return 202

    # --- Gamma's view of the proxies and the studios ----------------------------------------

    def list_proxies(self, call):
        rows = [{"id": p["id"], "contextPath": p.get("contextPath"), "name": p["name"], "mode": "PROXY"} for p in self.proxies.values()]
        rows += [{"id": i, "contextPath": s["contextPath"], "name": s["name"], "mode": "STUDIO"} for i, s in self.studios.items()]
        return 200, {"content": rows, "totalElements": len(rows)}

    def detail(self, call):
        proxy_id = ids(call)[0]
        if proxy_id in self.studios:
            return 200, {"id": proxy_id, "fgaEnabled": self.studios[proxy_id]["fgaEnabled"]}
        api = self.apis[proxy_id]
        return 200, {"id": proxy_id, "supportedProtocolVersions": api["supported"], "endpointProtocolVersions": api["endpointSupported"],
                     "fgaEnabled": True}

    def set_versions(self, call):
        api = self.apis[ids(call)[0]]
        api["supported"], api["endpointSupported"] = call.json["supportedProtocolVersions"], call.json["endpointProtocolVersions"]
        api["deploymentState"] = "NEED_REDEPLOY"
        return 200, {}

    def delete_proxy(self, call):
        self.studios.pop(ids(call)[0], None)
        return 204

    def create_studio(self, call):
        studio_id = f"studio-{next(self.next_id)}"
        self.studios[studio_id] = {**call.json, "fgaEnabled": False}
        self.apis[studio_id] = {"id": studio_id, "deploymentState": "DEPLOYED"}
        return 201, {"id": studio_id, "name": call.json["name"], "contextPath": call.json["contextPath"], "mode": "STUDIO"}

    def set_fga(self, call):
        self.studios[ids(call)[0]]["fgaEnabled"] = call.json["enabled"]
        self.apis[ids(call)[0]]["deploymentState"] = "NEED_REDEPLOY"
        return 200, {}

    # --- Access Management and the gateway --------------------------------------------------

    def sign_in(self, call):
        assert call.auth == ("acme-bakery-traffic", "traffic-secret") and call.data["password"] == "demo-password", call
        return 200, {"access_token": "token-" + call.data["username"].split("@")[0]}

    def gateway(self, call):
        self.gateway_calls.append(call)
        if len(self.gateway_calls) <= self.gateway_serves_after:
            return 404, {"message": "No context-path matches the request URI."}
        who = call.headers["Authorization"].removeprefix("Bearer token-")
        message = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": f"tool-{n}"} for n in range(self.tools_offered[who])]}}
        if self.event_stream:
            return 200, "event: message\ndata: " + json.dumps(message) + "\n\n", {"Content-Type": "text/event-stream"}
        return 200, message


class Proxies(Seed):

    def setUp(self):
        super().setUp()
        self.world = World()
        self.platform.add(self.world.routes())
        self.env(**SETTINGS)
        self.seed_files({"aim/mcp-proxies/acme-bakery-mcp.yaml": PROXY})

    def seed(self):
        aim_proxies.seed()

    def the_proxy(self):
        return self.world.proxies[HRID]

    def the_api(self):
        return self.world.apis[self.the_proxy()["id"]]

    def flow(self, name):
        return next(f for f in self.the_proxy()["flows"] if f["name"] == name)

    def rate_limit(self):
        return next(step for step in self.flow("Rate limit")["request"] if step["policy"] == "rate-limit")

    def writes(self):
        """What was written to the platform: not what the readiness checks post to Access Management and the gateway."""
        return [change for change in self.platform.changes() if change[1] not in ("/acme-bakery/oauth/token", "/acme-bakery/mcp")]


class FirstRun(Proxies):

    def test_the_proxy_is_made_by_the_automation_api_in_proxy_mode_on_the_bare_origin_of_the_server(self):
        self.seed()
        proxy = self.the_proxy()
        self.assertEqual((proxy["mode"], proxy["contextPath"], proxy["state"]), ("PROXY", "/acme-bakery/mcp", "STARTED"))
        self.assertEqual(proxy["proxy"]["serverUrl"], "http://bakery-mcp:8000")
        [sent] = self.platform.sent("PUT", AUTOMATION)
        self.assertEqual(sent.json["identityProviders"][0]["clientSecret"], "mcp-secret")
        self.assertEqual(sent.json["identityProviders"][0]["issuerUrl"], "http://am-gateway:8092/acme-bakery")

    def test_the_plan_is_oauth2_with_the_tokens_claims_extracted(self):
        self.seed()
        self.assertEqual(self.the_proxy()["plans"][0]["name"], "Bank users")
        self.assertIs(self.the_api()["plans"][0]["security"]["configuration"]["extractPayload"], True)
        self.assertEqual(self.the_api()["plans"][0]["security"]["configuration"]["oauthResource"], "acme-am")

    def test_the_gateway_traces_the_proxy_in_detail(self):
        self.seed()
        analytics = self.the_api()["analytics"]
        self.assertEqual((analytics["tracing"], analytics["otelLogs"]), ({"enabled": True, "verbose": True}, {"enabled": True}))
        self.assertTrue(analytics["logging"]["content"]["payload"], "the payloads that go with the spans")
        self.assertEqual(self.the_api()["deploymentState"], "DEPLOYED")

    def test_the_logs_keep_the_payloads_and_not_the_headers_because_one_of_them_is_the_bearer_token_of_the_caller(self):
        self.seed()
        content = self.the_api()["analytics"]["logging"]["content"]
        self.assertEqual((content["headers"], content["payload"]), (False, True))

    def test_the_headers_someone_logs_in_the_console_are_not_logged_again_after_a_run(self):
        self.seed()
        self.the_api()["analytics"]["logging"]["content"]["headers"] = True
        self.seed()
        self.assertIs(self.the_api()["analytics"]["logging"]["content"]["headers"], False)
        self.assertEqual(self.the_api()["deploymentState"], "DEPLOYED")

    def test_only_the_root_of_the_proxy_is_served_and_that_flow_runs_before_any_other(self):
        """The proxy hands every path under its own to the server, which has an admin route that is not MCP: a signed-in
        customer who knows its token would reach it. Any path but the root is answered by the gateway with a 404."""
        self.seed()
        flows = self.the_proxy()["flows"]
        self.assertEqual(flows[0]["name"], "Only the MCP endpoint")
        [selector] = flows[0]["selectors"]
        self.assertEqual(selector["type"], "CONDITION")
        self.assertIn("#request.pathInfo != ''", selector["condition"])
        self.assertIn("#request.pathInfo != '/'", selector["condition"])
        [step] = flows[0]["request"]
        self.assertEqual((step["policy"], step["configuration"]["status"]), ("mock", "404"))

    def test_the_rate_limit_counts_in_the_background_per_person(self):
        self.seed()
        owner, step = self.flow("Rate limit")["request"]
        self.assertEqual(step["policy"], "rate-limit")
        self.assertIs(step["configuration"]["async"], True)
        self.assertEqual(step["configuration"]["rate"]["limit"], 30)
        self.assertIn(".split('@')[0]", step["configuration"]["rate"]["key"])

    def test_the_person_stands_in_for_the_subscription_the_counter_is_stored_under(self):
        """The plan has no subscription, and the rate limit stores a counter under the id of one (the attribute below): left
        empty, the shared counter store refuses every refresh and the gateway log fills with stack traces."""
        self.seed()
        owner, step = self.flow("Rate limit")["request"]
        self.assertEqual(owner["policy"], "policy-assign-attributes", "it runs before the rate limit")
        self.assertEqual(owner["configuration"]["scope"], "REQUEST")
        [attribute] = owner["configuration"]["attributes"]
        self.assertEqual(attribute["name"], "gravitee.attribute.user-id")
        self.assertEqual(attribute["value"], step["configuration"]["rate"]["key"])

    def test_a_customer_sees_seven_tools_and_a_teller_all_of_them(self):
        self.seed()
        [step] = self.flow("Visible tools")["request"]
        teller, customer = step["configuration"]["authorizations"]
        self.assertIn("== 'teller'", teller["condition"])
        self.assertEqual([f["patternType"] for f in teller["authorizedFeatures"]], ["ANY"])
        self.assertIn("== 'customer'", customer["condition"])
        names = [f["patternValue"] for f in customer["authorizedFeatures"]]
        self.assertEqual(sorted(names), sorted(set(TOOLS) - {"freeze_card"}))

    def test_each_argument_is_read_where_its_tool_reads_it_and_the_amount_is_made_a_whole_number(self):
        self.seed()
        attributes = {a["name"]: a["value"] for a in self.flow("Authorization")["request"][0]["configuration"]["attributes"]}
        self.assertIn("== 'transfer_funds' ? #jsonPath(#request.content, '$.params.arguments.from_account') : "
                      "#jsonPath(#request.content, '$.params.arguments.account_id')", attributes["authz_account_id"])
        self.assertIn("T(java.lang.Math).ceil(#jsonPath(#request.content, '$.params.arguments.amount'))", attributes["authz_amount"])
        self.assertTrue(attributes["authz_amount"].endswith(".longValue() : null}"))

    def test_a_call_without_an_amount_is_not_asked_to_round_nothing(self):
        """`ceil` of an argument that is not there is an error the gateway logs for every call of a tool that has no amount:
        the attribute is only worked out when the argument is."""
        self.seed()
        attributes = {a["name"]: a["value"] for a in self.flow("Authorization")["request"][0]["configuration"]["attributes"]}
        guard = "#jsonPath(#request.content, '$.params.arguments.amount') != null ? "
        self.assertTrue(attributes["authz_amount"].startswith("{" + guard + "T(java.lang.Math).min("))

    def test_each_step_says_what_it_covers_so_that_the_console_shows_it_under_the_name_of_its_policy(self):
        """The console labels a step by its policy ("MCP ACL") and its Description field holds this: the Look beat of lessons 3
        and 4 sends the person to read it. The Automation API keeps it (the platform was asked, the fake answers what it was
        given), and no two steps are described alike."""
        self.seed()
        [sent] = self.platform.sent("PUT", AUTOMATION)
        said = {(f["name"], s["name"]): s.get("description") for f in sent.json["flows"] for s in f["request"]}
        self.assertEqual(len(said), 6)
        for key, description in said.items():
            self.assertTrue(description and description == description.strip() and not description.endswith("."), key)
        self.assertEqual(len(set(said.values())), 6)
        for key, words in {("Visible tools", "Tools by role"): ("teller", "every tool", "customer", "freeze_card"),
                           ("Authorization", "Fine-Grained Authorization"): ("policy engine", "person", "agent"),
                           ("Authorization", "Read tool arguments"): ("account", "amount")}.items():
            for word in words:
                self.assertIn(word, said[key], key)

    def test_the_authorization_names_the_tools_with_the_slug_the_catalog_gave_the_server(self):
        self.world.servers[0]["slug"] = "bank-tools"
        self.seed()
        configuration = self.flow("Authorization")["request"][1]["configuration"]
        self.assertIn("'bank-tools.' + #context.attributes['mcp_tool_name']", configuration["actionExpr"])
        self.assertEqual(configuration["resourceExpr"], configuration["actionExpr"])
        self.assertNotIn("<slug>", json.dumps(self.the_proxy()))

    def test_the_authorization_judges_the_person_the_agent_acts_for_and_fails_closed(self):
        self.seed()
        configuration = self.flow("Authorization")["request"][1]["configuration"]
        self.assertEqual((configuration["subjectType"], configuration["resourceType"], configuration["agentResolution"]),
                         ("User", "MCPTool", "AUTO"))
        self.assertEqual(configuration["subjectExpr"], self.rate_limit()["configuration"]["rate"]["key"])
        self.assertEqual((configuration["denyStatus"], configuration["logDecisions"], configuration["noSnapshotPolicy"]),
                         (403, True, "RETRY_THEN_DENY"))
        self.assertEqual([c["name"] for c in configuration["contextExpr"]], ["account_id", "customer_id", "card_id", "amount"])

    def test_a_refusal_reaches_a_client_as_a_json_rpc_error_that_names_the_action(self):
        self.seed()
        template = self.the_api()["responseTemplates"]["GAPL_AUTHZ"]["*/*"]
        self.assertEqual(template["statusCode"], 403)
        body = json.loads(template["body"])
        self.assertEqual((body["jsonrpc"], body["error"]["code"]), ("2.0", -32001))
        self.assertIn("{#parameters['action']}", body["error"]["data"]["action"])

    def test_the_endpoint_waits_a_minute_for_the_server(self):
        self.seed()
        self.assertEqual([g["sharedConfiguration"]["http"]["readTimeout"] for g in self.the_api()["endpointGroups"]], [60000])

    def test_clients_speak_the_two_latest_revisions_and_the_gateway_the_older(self):
        self.seed()
        self.assertEqual((self.the_api()["supported"], self.the_api()["endpointSupported"]),
                         (["2025-11-25", "2026-07-28"], ["2025-11-25"]))

    def test_everything_written_after_the_automation_api_is_deployed_once(self):
        self.seed()
        self.assertEqual(self.the_api()["deploymentState"], "DEPLOYED")
        self.assertEqual(len(self.platform.sent("POST", re.compile(V2 + "/apis/[^/]+/deployments"))), 1)
        self.assertEqual(self.platform.changes()[-1][0], "POST")

    def test_the_proxy_is_recorded_with_its_hrid_and_a_plural_label(self):
        self.seed()
        [made] = common.created()
        self.assertEqual((made["product"], made["kind"], made["label"], made["name"], made["id"], made["hrid"]),
                         ("aim-proxies", "mcp-proxy", "MCP proxy APIs", "Acme Bakery MCP", "api-" + HRID, HRID))

    def test_no_studio_is_made_without_a_file_for_it(self):
        self.seed()
        self.assertEqual(self.world.studios, {})
        self.assertEqual(self.platform.sent("POST", AIM + "/mcp-studios"), [])

    def test_a_server_the_catalog_does_not_have_stops_the_seed_with_what_to_do(self):
        self.world.servers.clear()
        with self.assertRaisesRegex(common.SeedError, "MCP server Acme Bakery MCP is not in the catalog"):
            self.seed()
        self.assertEqual(self.platform.changes(), [])

    def test_an_api_of_that_hrid_the_seed_did_not_create_is_never_adopted(self):
        self.world.proxies[HRID] = {"id": "theirs", "name": "Theirs"}
        with self.assertRaisesRegex(common.SeedError, "acme-bakery-mcp-proxy is the hrid of an API the seed did not create"):
            self.seed()
        self.assertEqual(self.platform.changes(), [])


class SecondRun(Proxies):

    def setUp(self):
        super().setUp()
        self.seed()
        self.before = json.dumps([self.world.proxies, self.world.apis], sort_keys=True)
        self.platform.calls.clear()

    def test_nothing_is_written(self):
        self.seed()
        self.assertEqual(self.platform.changes(), [])
        self.assertEqual(json.dumps([self.world.proxies, self.world.apis], sort_keys=True), self.before)
        self.assertEqual(len(common.created()), 1)

    def test_a_flow_someone_removed_is_put_back_and_the_rest_stays(self):
        self.the_proxy()["flows"].pop(1)
        self.seed()
        self.assertEqual([f["name"] for f in self.the_proxy()["flows"]],
                         ["Only the MCP endpoint", "Rate limit", "Visible tools", "Authorization"])
        self.assertEqual(self.platform.changes(), [("PUT", AUTOMATION)])
        self.assertEqual(len(common.created()), 1)
        self.assertIs(self.the_api()["plans"][0]["security"]["configuration"]["extractPayload"], True)

    def test_a_rate_limit_someone_changed_in_the_console_is_put_back(self):
        self.rate_limit()["configuration"]["rate"]["limit"] = 1000
        self.seed()
        self.assertEqual(self.rate_limit()["configuration"]["rate"]["limit"], 30)

    def test_a_description_someone_changed_in_the_console_is_put_back(self):
        step = next(s for s in self.flow("Visible tools")["request"] if s["name"] == "Tools by role")
        step["description"] = ""
        self.seed()
        self.assertEqual(self.platform.changes(), [("PUT", AUTOMATION)])
        self.assertIn("freeze_card", next(s for s in self.flow("Visible tools")["request"] if s["name"] == "Tools by role")["description"])

    def test_the_tracing_someone_switched_off_is_switched_on_again_and_deployed_with_what_the_gateway_keeps(self):
        self.the_api()["analytics"] = {"enabled": True, "tracing": {"enabled": False, "verbose": False}, "connectionEvents": []}
        self.seed()
        analytics = self.the_api()["analytics"]
        self.assertEqual((analytics["enabled"], analytics["tracing"], analytics["connectionEvents"]),
                         (True, {"enabled": True, "verbose": True}, []), "what the gateway keeps stays")
        self.assertEqual(analytics["otelLogs"], {"enabled": True})
        self.assertEqual([m for m, _ in self.platform.changes()], ["PUT", "POST"])

    def test_a_plan_that_lost_its_claims_gets_them_back_and_is_deployed(self):
        self.the_api()["plans"][0]["security"]["configuration"].pop("extractPayload")
        self.seed()
        self.assertIs(self.the_api()["plans"][0]["security"]["configuration"]["extractPayload"], True)
        self.assertEqual([m for m, _ in self.platform.changes()], ["PUT", "POST"])
        self.assertEqual(self.the_api()["deploymentState"], "DEPLOYED")

    def test_a_missing_template_and_a_default_timeout_are_written_in_one_call_and_deployed(self):
        self.the_api().pop("responseTemplates")
        self.seed()
        self.assertIn("GAPL_AUTHZ", self.the_api()["responseTemplates"])
        self.assertEqual([m for m, _ in self.platform.changes()], ["PUT", "POST"])

    def test_the_versions_someone_changed_are_set_again_and_deployed(self):
        self.the_api()["supported"] = ["2025-11-25"]
        self.seed()
        self.assertEqual(self.the_api()["supported"], ["2025-11-25", "2026-07-28"])
        self.assertEqual([m for m, _ in self.platform.changes()], ["PUT", "POST"])

    def test_an_api_that_waits_for_a_deployment_is_deployed_and_nothing_else_is_written(self):
        self.the_api()["deploymentState"] = "NEED_REDEPLOY"
        self.seed()
        self.assertEqual([m for m, _ in self.platform.changes()], ["POST"])

    def test_a_change_of_the_file_is_an_update_of_the_same_proxy(self):
        renamed = PROXY.replace("30 calls a minute per user", "30 calls a minute for each person")
        self.seed_files({"aim/mcp-proxies/acme-bakery-mcp.yaml": renamed})
        self.seed()
        self.assertEqual(self.rate_limit()["name"], "30 calls a minute for each person")
        self.assertEqual(len(self.world.proxies), 1)
        self.assertIn("✓ MCP proxy updated: Acme Bakery MCP", self.output())


class Gateway(Proxies):

    def setUp(self):
        super().setUp()
        self.env(GATEWAY_INTERNAL_URL="http://gateway")

    def test_the_gateway_is_not_asked_when_the_seed_does_not_know_it(self):
        self.env(GATEWAY_INTERNAL_URL=None)
        self.seed()
        self.assertEqual(self.world.gateway_calls, [])
        self.assertEqual(self.platform.sent("POST", TOKEN_URL), [])

    def test_the_gateway_is_not_asked_unless_the_seed_is_asked_to_wait_for_it(self):
        self.env(SEED_WAIT_GATEWAY="0")
        self.seed()
        self.assertEqual(self.world.gateway_calls, [])
        self.assertEqual(self.platform.sent("POST", TOKEN_URL), [])

    def test_the_seed_waits_until_each_person_is_offered_the_tools_of_their_role(self):
        self.world.gateway_serves_after = 3
        self.seed()
        self.assertEqual(len(self.world.gateway_calls), 5)   # three refusals, then alice, then tina
        self.assertEqual([c.headers["Authorization"] for c in self.world.gateway_calls[-2:]], ["Bearer token-alice", "Bearer token-tina"])
        self.assertEqual(self.world.gateway_calls[-1].json["method"], "tools/list")
        self.assertIn("✓ Acme Bakery MCP on the gateway (/acme-bakery/mcp) ready", self.output())

    def test_the_answer_may_come_as_an_event_stream(self):
        self.world.event_stream = True
        self.seed()
        self.assertEqual(len(self.world.gateway_calls), 2)

    def test_a_token_that_carries_no_claims_is_a_proxy_that_is_not_ready(self):
        self.world.tools_offered["alice"] = 0
        self.env(GATEWAY_READY_TIMEOUT="20")
        with self.assertRaisesRegex(common.SeedError, r"Acme Bakery MCP on the gateway \(/acme-bakery/mcp\) not ready after 20 s: "
                                                       r"alice@acme-bakery.test: 0 tools offered, 7 expected"):
            self.seed()

    def test_a_refused_sign_in_stops_the_seed_with_one_line_naming_the_person_and_the_endpoint(self):
        """The Lab UI shows the last line of what the seed printed: an error body must not spread over many."""
        self.platform.add({("POST", TOKEN_URL): (400, '{\n  "error" : "invalid_grant",\n  "error_description" : "The credentials entered are invalid"\n}')})
        self.env(GATEWAY_READY_TIMEOUT="20")
        with self.assertRaises(common.SeedError) as raised:
            self.seed()
        self.assertEqual(str(raised.exception), "Acme Bakery MCP on the gateway (/acme-bakery/mcp) not ready after 20 s: alice@acme-bakery.test: "
                         'POST /acme-bakery/oauth/token: HTTP 400 { "error" : "invalid_grant", '
                         '"error_description" : "The credentials entered are invalid" }')

    def test_a_gateway_that_never_serves_it_stops_the_seed_with_the_last_answer(self):
        self.world.gateway_serves_after = 10_000
        self.env(GATEWAY_READY_TIMEOUT="20")
        with self.assertRaisesRegex(common.SeedError, "not ready after 20 s: alice@acme-bakery.test: POST /acme-bakery/mcp: HTTP 404"):
            self.seed()

    def test_a_proxy_that_is_already_serving_is_checked_and_nothing_is_written(self):
        self.seed()
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.writes(), [])
        self.assertEqual(len(self.world.gateway_calls), 4)


class Protections(Proxies):
    """The proxy with the controls of Acme Bakery: the tools of a role, and the authorization check."""

    SWITCHED = {("Visible tools", "Tools by role"), ("Authorization", "Fine-Grained Authorization")}

    def setUp(self):
        super().setUp()
        self.seed_files({"controls.yaml": CONTROLS})
        self.env(SEED_PROTECTIONS="off")
        self.world.tools_offered["alice"] = 8   # a customer is offered every tool while the tools of a role are not a control

    def steps(self):
        """{(flow, step): enabled} for every step of the proxy, as the Automation API gives it."""
        return {(flow["name"], step["name"]): step.get("enabled", True)
                for flow in self.the_proxy()["flows"] for phase in ("request", "response") for step in flow.get(phase, [])}

    def switch(self, flow, name, enabled):
        """What someone does in the console to a step of the proxy: save it, and deploy."""
        step = next(s for f in self.the_proxy()["flows"] if f["name"] == flow for s in f["request"] if s["name"] == name)
        step["enabled"] = enabled

    def written(self):
        """{(flow, step): enabled} as the Automation API was sent them."""
        [sent] = self.platform.sent("PUT", AUTOMATION)
        return {(f["name"], s["name"]): s.get("enabled") for f in sent.json["flows"] for s in f["request"]}


class FirstRunProtections(Protections):

    def test_off_the_two_steps_of_the_lessons_are_written_disabled_and_every_other_step_enabled(self):
        self.seed()
        steps = self.steps()
        self.assertEqual({key for key, enabled in steps.items() if not enabled}, self.SWITCHED)
        self.assertEqual(len(steps), 6)
        self.assertEqual({key: enabled for key, enabled in self.written().items() if enabled is False},
                         {key: False for key in self.SWITCHED})
        self.assertEqual(self.the_api()["deploymentState"], "DEPLOYED")

    def test_on_those_steps_are_written_enabled_in_so_many_words(self):
        self.env(SEED_PROTECTIONS="on")
        self.world.tools_offered["alice"] = 7
        self.seed()
        self.assertEqual(self.steps(), {key: True for key in self.steps()})
        self.assertEqual({key: self.written()[key] for key in self.SWITCHED}, {key: True for key in self.SWITCHED})

    def test_a_step_that_is_not_a_control_is_written_without_a_switch_of_its_own(self):
        self.seed()
        [sent] = self.platform.sent("PUT", AUTOMATION)
        step = next(s for f in sent.json["flows"] for s in f["request"] if s["name"] == "30 calls a minute per user")
        self.assertNotIn("enabled", step)

    def test_without_the_setting_the_proxy_is_protected(self):
        self.env(SEED_PROTECTIONS=None)
        self.world.tools_offered["alice"] = 7
        self.seed()
        self.assertEqual(self.steps(), {key: True for key in self.steps()})

    def test_what_a_run_says_about_the_controls(self):
        self.seed()
        self.assertIn("Controls of Acme Bakery MCP are off: tools, pep", self.output())

    def test_a_step_a_control_names_that_the_file_lacks_stops_the_seed_before_the_proxy_is_written(self):
        self.seed_files({"controls.yaml": CONTROLS.replace("Fine-Grained Authorization", "Fine Grained Authorization")})
        with self.assertRaisesRegex(common.SeedError, "control pep: no request step named 'Fine Grained Authorization' in flow "
                                                       "'Authorization' of mcp-proxy 'Acme Bakery MCP'"):
            self.seed()
        self.assertEqual(self.platform.changes(), [])


class StartingTheLessonsOverProtections(Protections):

    def setUp(self):
        super().setUp()
        self.seed()
        self.platform.calls.clear()

    def test_a_second_run_writes_nothing(self):
        self.seed()
        self.assertEqual(self.platform.changes(), [])

    def test_a_step_someone_switched_on_is_switched_off_again_and_deployed(self):
        self.switch("Authorization", "Fine-Grained Authorization", True)
        self.switch("Visible tools", "Tools by role", True)
        self.seed()
        self.assertEqual({key for key, enabled in self.steps().items() if not enabled}, self.SWITCHED)
        self.assertEqual(self.platform.changes(), [("PUT", AUTOMATION)], "the Automation API deploys what it changes")
        self.assertEqual(self.the_api()["deploymentState"], "DEPLOYED")
        self.assertEqual(len(common.created()), 1, "the same proxy")

    def test_the_proxy_is_not_made_again_for_one_step(self):
        self.switch("Visible tools", "Tools by role", True)
        before = self.the_proxy()["id"]
        self.seed()
        self.assertIs(self.steps()[("Visible tools", "Tools by role")], False)
        self.assertEqual(self.the_proxy()["id"], before)
        self.assertEqual(self.platform.sent("DELETE", re.compile(".*")), [])

    def test_the_other_mode_switches_both_back_on_and_the_next_run_writes_nothing(self):
        self.env(SEED_PROTECTIONS="on")
        self.world.tools_offered["alice"] = 7
        self.seed()
        self.assertEqual(self.steps(), {key: True for key in self.steps()})
        self.assertEqual(self.platform.changes(), [("PUT", AUTOMATION)])
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.platform.changes(), [])

    def test_a_step_someone_switched_off_is_switched_on_again_in_the_mode_on(self):
        self.env(SEED_PROTECTIONS="on")
        self.world.tools_offered["alice"] = 7
        self.seed()
        self.switch("Authorization", "Fine-Grained Authorization", False)
        self.platform.calls.clear()
        self.seed()
        self.assertIs(self.steps()[("Authorization", "Fine-Grained Authorization")], True)
        self.assertEqual(self.platform.changes(), [("PUT", AUTOMATION)])

    def test_a_step_no_control_names_is_put_back_as_the_file_says_as_it_always_was(self):
        """The controls take the mode, not the rest of the proxy, which the manifest owns."""
        self.rate_limit()["configuration"]["rate"]["limit"] = 1000
        self.seed()
        self.assertEqual(self.rate_limit()["configuration"]["rate"]["limit"], 30)
        self.assertEqual({key for key, enabled in self.steps().items() if not enabled}, self.SWITCHED)


class ReadyProtections(Protections):
    """The seed waits for each person to be offered the tools the mode lets their role see."""

    def setUp(self):
        super().setUp()
        self.env(GATEWAY_INTERNAL_URL="http://gateway", GATEWAY_READY_TIMEOUT="20")

    def test_off_a_customer_is_expected_to_see_every_tool(self):
        self.seed()
        self.assertEqual(len(self.world.gateway_calls), 2)

    def test_off_a_gateway_that_still_hides_a_tool_has_not_taken_the_change(self):
        self.world.tools_offered["alice"] = 7
        with self.assertRaisesRegex(common.SeedError, r"alice@acme-bakery.test: 7 tools offered, 8 expected"):
            self.seed()

    def test_on_a_customer_is_expected_not_to_see_the_card_freeze(self):
        self.env(SEED_PROTECTIONS="on")
        self.world.tools_offered["alice"] = 8
        with self.assertRaisesRegex(common.SeedError, r"alice@acme-bakery.test: 8 tools offered, 7 expected"):
            self.seed()

    def test_a_teller_is_expected_to_see_all_eight_in_both_modes(self):
        self.world.tools_offered["tina"] = 7
        with self.assertRaisesRegex(common.SeedError, r"tina@acme-bakery.test: 7 tools offered, 8 expected"):
            self.seed()


class WithStudio(Proxies):

    def setUp(self):
        super().setUp()
        self.seed_files({"aim/studios/acme-bakery-toolkit.yaml": STUDIO})

    def the_studio(self):
        [studio] = self.world.studios.values()
        return studio

    def with_the_check_off(self):
        self.seed_files({"aim/studios/acme-bakery-toolkit.yaml": STUDIO_UNCHECKED})


class Studios(WithStudio):

    def test_the_studio_is_made_from_the_catalog_tools_with_their_aliases_and_ships_with_its_authorization_check_on(self):
        self.seed()
        studio = self.the_studio()
        self.assertEqual((studio["name"], studio["contextPath"], studio["fgaEnabled"]), ("Acme Bakery Toolkit", "/acme-bakery/toolkit", True))
        self.assertEqual(studio["specification"], {"promptIds": [], "resourceIds": [], "tools": [
            {"id": "tool-get_exchange_rate"}, {"id": "tool-simulate_loan"}, {"id": "tool-find_branches"},
            {"id": "tool-listBranches", "alias": "list_branches"}, {"id": "tool-getBranch", "alias": "get_branch"},
            {"id": "tool-listRates", "alias": "list_rates"}, {"id": "tool-getRate", "alias": "get_rate"}]})
        self.assertEqual(studio["plans"][0]["security"]["type"], "OAUTH2_GENERIC")
        self.assertEqual(studio["plans"][0]["security"]["configuration"]["clientSecret"], "mcp-secret")
        self.assertIn("2026-07-28", studio["supportedProtocolVersions"])

    def test_a_check_that_stays_off_is_not_written(self):
        self.with_the_check_off()
        self.seed()
        changes = [(m, p.rsplit("/", 1)[1]) for m, p in self.platform.changes() if "studio" in p]
        self.assertEqual(changes, [("POST", "mcp-studios")])

    def test_the_check_is_deployed_after_it_is_switched_on(self):
        self.seed()
        self.assertIs(self.the_studio()["fgaEnabled"], True)
        changes = [(m, p.rsplit("/", 1)[1]) for m, p in self.platform.changes() if "studio" in p]
        self.assertEqual(changes, [("POST", "mcp-studios"), ("PUT", "fga"), ("POST", "deployments")])

    def test_the_studio_is_recorded_with_what_it_was_made_from(self):
        self.seed()
        studio = next(x for x in common.created() if x["kind"] == "studio")
        self.assertEqual((studio["product"], studio["label"], studio["name"]), ("aim-proxies", "MCP studios", "Acme Bakery Toolkit"))
        self.assertEqual(len(studio["digest"]), 64)

    def test_a_second_run_writes_nothing(self):
        for text in (STUDIO, STUDIO_UNCHECKED):
            self.seed_files({"aim/studios/acme-bakery-toolkit.yaml": text})
            self.seed()
            self.platform.calls.clear()
            self.seed()
            self.assertEqual(self.platform.changes(), [])
            aim_proxies.remove(common.created())

    def test_a_studio_whose_file_changed_is_replaced_and_its_record_follows(self):
        self.seed()
        old = next(iter(self.world.studios))
        self.seed_files({"aim/studios/acme-bakery-toolkit.yaml": STUDIO.replace("alias: get_rate", "alias: rate")})
        self.seed()
        self.assertNotIn(old, self.world.studios)
        self.assertEqual(len(self.world.studios), 1)
        self.assertEqual([x["id"] for x in common.created() if x["kind"] == "studio"], list(self.world.studios))

    def test_a_check_someone_switched_off_is_switched_on_again_when_the_file_says_on(self):
        self.seed()
        self.the_studio()["fgaEnabled"] = False
        self.seed()
        self.assertIs(self.the_studio()["fgaEnabled"], True)

    def test_a_check_someone_switched_on_is_switched_off_again_when_the_file_says_off(self):
        self.with_the_check_off()
        self.seed()
        self.the_studio()["fgaEnabled"] = True
        self.seed()
        self.assertIs(self.the_studio()["fgaEnabled"], False)

    def test_a_tool_the_catalog_does_not_have_stops_the_seed_naming_it(self):
        del self.world.tools["mcp-tool.acme-bakery-open-api-getrate"]
        with self.assertRaisesRegex(common.SeedError, "MCP studio: the tool mcp-tool.acme-bakery-open-api-getrate is not in the catalog"):
            self.seed()
        self.assertEqual(self.world.studios, {})

    def test_a_studio_at_that_path_the_seed_did_not_create_is_never_adopted(self):
        self.world.studios["theirs"] = {"name": "Theirs", "contextPath": "/acme-bakery/toolkit", "fgaEnabled": False}
        with self.assertRaisesRegex(common.SeedError, "/acme-bakery/toolkit is the path of 'Theirs', which the seed did not create"):
            self.seed()
        self.assertEqual(self.platform.sent("DELETE", re.compile(AIM + "/mcp-proxies/.*")), [])


class Removal(WithStudio):

    def setUp(self):
        super().setUp()
        self.seed()
        self.platform.calls.clear()

    def test_the_proxy_goes_through_the_automation_api_and_the_studio_through_gamma(self):
        aim_proxies.remove(common.created())
        self.assertEqual(self.world.proxies, {})
        self.assertEqual(self.world.studios, {})
        self.assertEqual(self.platform.changes(), [("DELETE", f"{AUTOMATION}/{HRID}"), ("DELETE", AIM + "/mcp-proxies/studio-1")])
        self.assertIn("✓ Deleted MCP proxy 'Acme Bakery MCP'", self.output())
        self.assertIn("✓ Deleted MCP studio 'Acme Bakery Toolkit'", self.output())

    def test_what_another_module_made_is_left_alone(self):
        theirs = [{"product": "aim-workspaces", "kind": "workspace", "name": "w", "id": "w", "label": ""},
                  {"product": "apim", "kind": "api", "name": "a", "id": "a", "label": ""}]
        aim_proxies.remove(theirs)
        self.assertEqual(self.platform.calls, [])

    def test_a_proxy_that_is_gone_is_not_an_error(self):
        self.world.proxies.clear()
        aim_proxies.remove(common.created())


class Folder(Seed):

    def test_a_playground_without_proxies_needs_no_setting_and_makes_no_call(self):
        self.env(APIM_MANAGEMENT_URL=None)
        aim_proxies.seed()
        aim_proxies.remove([])
        self.assertEqual(self.platform.calls, [])


if __name__ == "__main__":
    unittest.main()
