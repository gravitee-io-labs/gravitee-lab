"""The AI workspace of Acme Bakery Teams, from its real seed file, against a platform that keeps what it is given: created
once, left alone by a second run (no write at all), repaired when it differs from its file, and removed with only what
the seed made."""

import itertools
import json
import re
import unittest
from unittest import mock
from urllib.parse import unquote

from lab_seeder import aim_workspaces
from lab_seeder import common
from lab_seeder import refills
from lab_seeder.testkit import PLAYGROUNDS, FakeRedis, Seed

AIM = "/gamma/organizations/DEFAULT/environments/DEFAULT/modules/aim"
ENV = "/management/organizations/DEFAULT/environments/DEFAULT"
USERS = "/management/organizations/DEFAULT/users"
FILE = PLAYGROUNDS / "acme-bakery" / "seed" / "aim" / "workspaces" / "acme-bakery-teams.yaml"
CONTROLS = (PLAYGROUNDS / "acme-bakery" / "seed" / "controls.yaml").read_text(encoding="utf-8")
REFILLS = (PLAYGROUNDS / "acme-bakery" / "seed" / "refills.yaml").read_text(encoding="utf-8")
WORKSPACE = "Acme Bakery Teams"
TEAMS = ("team-care", "team-fraud", "team-hackathon")
ADMIN = {"id": "user-admin", "source": "memory", "sourceId": "admin"}


def ids(call):
    """The ids a path names: /ai-workspaces/ws-1/users/sub-2 gives ('ws-1', 'sub-2')."""
    return tuple(re.findall(r"/(?:ai-workspaces|budgets|users|applications|models)/([^/]+)", call.path))


class World:
    """Agent Management (workspaces, budgets, models, router, members) and the platform's users and applications, as the
    seed calls them. Like the real ones it adds defaults to what it is given, starts a workspace with a budget named
    Default, and says when a workspace waits for a deployment."""

    def __init__(self):
        self.next_id = itertools.count(1)
        self.providers = {"prov-1": {"id": "prov-1", "name": "Acme Bakery Ollama", "slug": "acme-bakery-ollama"}}
        self.workspaces, self.users, self.applications = {}, {}, {}   # applications: id -> workspaces that subscribe
        self.gateway_calls, self.gateway_serves_after, self.gateway_flows_after = [], 0, 0

    def new(self, kind):
        return f"{kind}-{next(self.next_id)}"

    def routes(self):
        ws = AIM + r"/ai-workspaces/[^/]+"
        return {
            ("GET", AIM + "/providers"): lambda call: (200, {"data": list(self.providers.values()),
                                                              "pagination": {"totalCount": len(self.providers)}}),
            ("GET", AIM + "/ai-workspaces"): self.list_workspaces,
            ("POST", AIM + "/ai-workspaces"): self.create_workspace,
            ("GET", re.compile(ws)): self.get_workspace,
            ("PUT", re.compile(ws)): self.update_workspace,
            ("DELETE", re.compile(ws)): self.delete_workspace,
            ("GET", re.compile(ws + "/budgets")): self.list_budgets,
            ("POST", re.compile(ws + "/budgets")): self.create_budget,
            ("PUT", re.compile(ws + "/budgets/[^/]+")): self.update_budget,
            ("GET", re.compile(ws + "/models")): lambda call: (200, self.workspace(call)["models"]),
            ("POST", re.compile(ws + "/models/_bulk")): self.add_models,
            ("PUT", re.compile(ws + "/models/[^/]+")): self.replace_models,
            ("DELETE", re.compile(ws + "/models/[^/]+/[^/]+")): self.remove_model,
            ("GET", re.compile(ws + "/router")): lambda call: (200, self.workspace(call)["router"]),
            ("PUT", re.compile(ws + "/router")): self.save_router,
            ("GET", re.compile(ws + "/analytics")): self.get_analytics,
            ("PUT", re.compile(ws + "/analytics")): self.save_analytics,
            ("POST", re.compile(ws + "/_deploy")): self.deploy,
            ("GET", re.compile(ws + "/users")): self.list_members,
            ("POST", re.compile(ws + "/users")): self.add_member,
            ("PUT", re.compile(ws + "/users/[^/]+")): self.move_member,
            ("GET", re.compile(ws + "/users/[^/]+/api-key")): lambda call: (200, {"apiKey": f"key-of-{ids(call)[1]}"}),
            ("GET", USERS): self.search_users,
            ("POST", USERS): self.create_user,
            ("DELETE", re.compile(USERS + "/[^/]+")): self.delete_user,
            ("GET", re.compile(ENV + "/applications/[^/]+/subscriptions")): self.subscriptions_of_application,
            ("DELETE", re.compile(ENV + "/applications/[^/]+")): self.delete_application,
            ("GET", "http://gateway/acme-bakery-teams/models"): self.gateway,
        }

    # --- Workspaces and budgets -----------------------------------------------------------

    def workspace(self, call):
        return self.workspaces[ids(call)[0]]

    @staticmethod
    def page(rows):
        return 200, {"content": rows, "totalElements": len(rows)}

    def list_budgets(self, call):
        # As APIM answers: its pages start at 1
        if int(call.params.get("page", 1)) < 1:
            return 400, {"message": "Pagination page param must be >= 1"}
        return self.page(self.workspace(call)["budgets"])

    def list_workspaces(self, call):
        return self.page([w for w in self.workspaces.values() if call.params.get("q", "") in w["name"]])

    def get_workspace(self, call):
        return (200, self.workspace(call)) if ids(call)[0] in self.workspaces else (404, {"message": "AI Workspace not found."})

    def create_workspace(self, call):
        body = call.json
        wid, budget = self.new("ws"), {"id": self.new("budget"), "name": "Default", **call.json["defaultBudget"]}
        self.workspaces[wid] = {"id": wid, "name": body["name"], "version": body["version"], "tags": [],
                                "deploymentState": "NEED_REDEPLOY", "budgets": [budget], "models": [], "members": [],
                                "router": {"request": [], "response": [], "divergences": []},
                                "analytics": {"enabled": True}, "analyticsPending": False}
        if "description" in body:
            self.workspaces[wid]["description"] = body["description"]
        return 201, {"id": wid, "defaultBudgetId": budget["id"]}

    def update_workspace(self, call):
        workspace = self.workspace(call)
        workspace.update(call.json)
        workspace["deploymentState"] = "NEED_REDEPLOY"
        return 200, workspace

    def delete_workspace(self, call):
        for member in self.workspaces.pop(ids(call)[0])["members"]:
            self.applications[member["applicationId"]].discard(ids(call)[0])
        return 204

    def create_budget(self, call):
        budget = {"id": self.new("budget"), "status": "PUBLISHED", **call.json}
        self.workspace(call)["budgets"].append(budget)
        self.workspace(call)["deploymentState"] = "NEED_REDEPLOY"
        return 201, budget

    def update_budget(self, call):
        budget = next(b for b in self.workspace(call)["budgets"] if b["id"] == ids(call)[1])
        budget.update(call.json)
        self.workspace(call)["deploymentState"] = "NEED_REDEPLOY"
        return 200, budget

    # --- Models, router, reporter settings, deployment ------------------------------------

    @staticmethod
    def component(component):
        """What the platform adds: the name callers use for the provider, and a price on each model."""
        return {**component, "routingName": "acme-bakery-ollama", "models": [{**m, "price": 0.5} for m in component["models"]]}

    def add_models(self, call):
        """Adds a provider, or merges into it: the models and aliases it has stay."""
        workspace = self.workspace(call)
        for component in call.json:
            have = next((c for c in workspace["models"] if c["providerId"] == component["providerId"]), None)
            if have is None:
                workspace["models"].append(self.component(component))
            else:
                have["models"] += self.component(component)["models"]
        workspace["contextPath"] = "/" + re.sub(r"[^a-z0-9]+", "-", workspace["name"].lower()).strip("-")
        if len(workspace["models"]) == 1:   # the first model creates the LLM proxy and deploys it
            workspace["deploymentState"] = "DEPLOYED"
        return 200, workspace["models"]

    def replace_models(self, call):
        """Sets the aliases and governance of a provider: not which models it has."""
        workspace = self.workspace(call)
        have = next(c for c in workspace["models"] if c["providerId"] == ids(call)[1])
        if sorted(m["name"] for m in have["models"]) != sorted(m["name"] for m in call.json["models"]):
            return 400, {"message": "Editing provider changes its models' settings, not which models it exposes; "
                                    "add or remove a model on its own."}
        have.update(self.component(call.json))
        return 200, workspace["models"]

    def remove_model(self, call):
        have = next(c for c in self.workspace(call)["models"] if c["providerId"] == ids(call)[1])
        have["models"] = [m for m in have["models"] if m["name"] != unquote(call.path.rsplit("/", 1)[1])]
        return 200, self.workspace(call)["models"]

    def save_router(self, call):
        workspace = self.workspace(call)
        for step in call.json["request"]:   # the platform writes the policy's defaults into the configuration
            configuration = json.loads(step["configuration"])
            for band in configuration.get("bands", []):
                band.setdefault("selector", "ROUND_ROBIN")
            step["configuration"] = json.dumps(configuration, separators=(",", ":"))
        workspace["router"] = {**call.json, "divergences": []}
        workspace["deploymentState"] = "NEED_REDEPLOY"
        return 200, workspace["router"]

    def get_analytics(self, call):
        workspace = self.workspace(call)
        return 200, {"analytics": workspace["analytics"], "needsDeploy": workspace["analyticsPending"], "divergent": False}

    def save_analytics(self, call):
        workspace = self.workspace(call)
        workspace["analytics"] = {"tracing": {"enabled": False}, **call.json["analytics"]}      # what the file does not say is off
        workspace["analyticsPending"] = True
        return self.get_analytics(call)

    def deploy(self, call):
        workspace = self.workspace(call)
        workspace["deploymentState"], workspace["analyticsPending"] = "DEPLOYED", False
        return self.get_analytics(call)

    # --- Members, users and applications --------------------------------------------------

    def list_members(self, call):
        rows = self.workspace(call)["members"]
        return 200, {"data": rows, "pagination": {"page": 1, "perPage": 100, "totalCount": len(rows)}}

    def add_member(self, call):
        workspace = self.workspace(call)
        application = f"app-{call.json['userId']}"
        self.applications.setdefault(application, set()).add(workspace["id"])
        member = {"subscriptionId": self.new("sub"), "userId": call.json["userId"], "applicationId": application,
                  "budgetId": call.json["budgetId"], "status": "ACTIVE"}
        workspace["members"].append(member)
        return 201, member

    def move_member(self, call):
        member = next(m for m in self.workspace(call)["members"] if m["subscriptionId"] == ids(call)[1])
        member["budgetId"] = call.json["budgetId"]
        return 200, member

    def search_users(self, call):
        return 200, {"data": [u for u in self.users.values() if call.params["q"] in u["sourceId"]]}

    def create_user(self, call):
        user = {"id": self.new("user"), **call.json}
        self.users[user["id"]] = user
        return 200, user

    def delete_user(self, call):
        self.users.pop(ids(call)[0], None)
        return 204

    def subscriptions_of_application(self, call):
        return 200, {"data": [{"workspace": w} for w in sorted(self.applications[ids(call)[0]])][:1]}

    def delete_application(self, call):
        self.applications.pop(ids(call)[0], None)
        return 204

    # --- The gateway ----------------------------------------------------------------------

    def gateway(self, call):
        """Not serving the workspace for the first calls, then serving it with the plans of the previous deployment (no
        budget yet), then answering with the budget of the member whose key it is."""
        self.gateway_calls.append(call)
        if len(self.gateway_calls) <= self.gateway_serves_after:
            return 404, {"message": "No context-path matches the request URI."}
        if len(self.gateway_calls) <= self.gateway_serves_after + self.gateway_flows_after:
            return 200, {"data": []}
        workspace, member = next((w, m) for w in self.workspaces.values() for m in w["members"]
                                 if call.headers.get("Authorization") == f"Bearer key-of-{m['subscriptionId']}")
        budget = next(b for b in workspace["budgets"] if b["id"] == member["budgetId"])
        return 200, {"data": []}, {"X-Cost-Rate-Limit-Limit": str(budget["costBudget"])}


class Workspaces(Seed):

    def setUp(self):
        super().setUp()
        self.world = World()
        self.platform.add(self.world.routes())
        self.seed_files({"aim/workspaces/acme-bakery-teams.yaml": FILE.read_text(encoding="utf-8")})
        self.env(GATEWAY_SYNC_SECONDS="1")

    def seed(self):
        aim_workspaces.seed()

    def the_workspace(self):
        [workspace] = self.world.workspaces.values()
        return workspace

    def budgets(self):
        return {b["name"]: (b["costBudget"], b["period"], b.get("requestRate")) for b in self.the_workspace()["budgets"]}


class FirstRun(Workspaces):

    def test_the_workspace_is_made_as_its_file_says_and_deployed(self):
        self.seed()
        workspace = self.the_workspace()
        self.assertEqual((workspace["name"], workspace["version"], workspace["deploymentState"]), (WORKSPACE, "1.0", "DEPLOYED"))
        self.assertEqual(workspace["contextPath"], "/acme-bakery-teams")
        self.assertEqual(self.budgets(), {"Customer care": (5_000_000, "DAY", None), "Fraud analytics": (2_000_000, "DAY", None),
                                          "Hackathon sandbox": (120_000, "HOUR", {"limit": 20, "period": "MINUTE"})})

    def test_the_first_budget_is_the_one_the_workspace_started_with_renamed(self):
        self.seed()
        self.assertEqual(len(self.the_workspace()["budgets"]), 3)
        self.assertEqual(len(self.platform.sent("POST", re.compile(AIM + "/ai-workspaces/[^/]+/budgets"))), 2)
        self.assertEqual(len(self.platform.sent("PUT", re.compile(AIM + "/ai-workspaces/[^/]+/budgets/[^/]+"))), 1)

    def test_the_models_come_from_the_catalog_provider_under_their_aliases(self):
        self.seed()
        [component] = self.the_workspace()["models"]
        self.assertEqual(component["providerId"], "prov-1")
        self.assertEqual({m["name"]: m["aliases"] for m in component["models"]}, {"qwen3:1.7b": ["smart"], "qwen3:0.6b": ["lite"]})
        self.assertEqual(component["modelGovernance"]["prefixPolicy"], {"policy": "NO_PREFIX"})

    def test_the_router_steps_carry_their_configuration_as_a_json_string(self):
        self.seed()
        [saved] = self.platform.sent("PUT", re.compile(AIM + "/ai-workspaces/[^/]+/router"))
        [step] = saved.json["request"]
        self.assertEqual((step["policy"], step["condition"]),
                         ("ai-routing", "{#context.attributes['gravitee.attribute.llm.kind'] == 'PROMPT'}"))
        self.assertIsInstance(step["configuration"], str)
        bands = json.loads(step["configuration"])["bands"]
        self.assertEqual([(b["name"], [c["model"] for c in b["candidates"]]) for b in bands],
                         [("Plenty left", ["smart"]), ("Running low", ["lite"])])

    def test_payloads_are_logged_and_headers_are_not_because_they_hold_the_key_of_the_team(self):
        self.seed()
        content = self.the_workspace()["analytics"]["logging"]["content"]
        self.assertEqual((content["payload"], content["headers"]), (True, False))

    def test_the_calls_of_the_workspace_are_traced_like_those_of_the_proxies_so_that_an_answer_has_its_trace_in_gamma(self):
        self.seed()
        analytics = self.the_workspace()["analytics"]
        self.assertEqual((analytics["tracing"], analytics["otelLogs"]), ({"enabled": True, "verbose": True}, {"enabled": True}))

    def test_the_teams_are_made_platform_users_and_the_presenter_is_found_not_made(self):
        self.world.users["user-admin"] = ADMIN
        self.seed()
        made = [u for u in self.world.users.values() if u["source"] == "gravitee"]
        self.assertEqual(sorted(u["sourceId"] for u in made), sorted(TEAMS))
        self.assertEqual({u["firstname"] + " " + u["lastname"] for u in made},
                         {"Customer care team", "Fraud analytics team", "Hackathon team"})
        self.assertTrue(all(u["service"] is False and u["email"].endswith("@acme-bakery.test") for u in made))
        self.assertEqual(len(self.platform.sent("POST", USERS)), 3)
        self.assertEqual(len(self.the_workspace()["members"]), 4)
        self.assertIn("user-admin", [m["userId"] for m in self.the_workspace()["members"]])

    def test_each_team_is_in_the_budget_that_carries_its_name(self):
        self.seed()
        budgets = {b["id"]: b["name"] for b in self.the_workspace()["budgets"]}
        users = {u["id"]: u["sourceId"] for u in self.world.users.values()}
        self.assertEqual({users[m["userId"]]: budgets[m["budgetId"]] for m in self.the_workspace()["members"]},
                         {"team-care": "Customer care", "team-fraud": "Fraud analytics", "team-hackathon": "Hackathon sandbox"})

    def test_only_what_the_seed_created_is_recorded_with_plural_labels(self):
        self.world.users["user-admin"] = ADMIN
        self.seed()
        records = sorted((x["product"], x["kind"], x["label"], x["name"]) for x in common.created())
        self.assertEqual(records, sorted([("aim-workspaces", "workspace", "AI workspaces", WORKSPACE)]
                                         + [("aim-workspaces", "workspace-user", "workspace users", name) for name in TEAMS]))

    def test_no_key_is_written_anywhere(self):
        self.seed()
        written = json.dumps([call.json for call in self.platform.calls if call.json]) + self.state.read_text(encoding="utf-8") + self.output()
        self.assertNotIn("key-of-", written)
        self.assertEqual(self.platform.sent("GET", re.compile(AIM + "/ai-workspaces/[^/]+/users/[^/]+/api-key")), [])

    def test_a_presenter_who_never_signed_in_is_left_out_with_a_warning(self):
        self.seed()
        self.assertEqual(len(self.the_workspace()["members"]), 3)
        self.assertIn("! admin is not a user of the platform yet: left out", self.output())

    def test_a_user_of_another_source_that_is_not_optional_stops_the_seed(self):
        self.seed_files({"aim/workspaces/acme-bakery-teams.yaml": FILE.read_text(encoding="utf-8").replace(", optional: true", "")})
        with self.assertRaisesRegex(common.SeedError, "User admin .memory. is not known to the platform"):
            self.seed()

    def test_a_provider_the_catalog_did_not_make_stops_the_seed_with_what_to_do(self):
        self.world.providers.clear()
        with self.assertRaisesRegex(common.SeedError, "AI provider acme-bakery-ollama is not in the catalog"):
            self.seed()


class SecondRun(Workspaces):

    def setUp(self):
        super().setUp()
        self.world.users["user-admin"] = ADMIN
        self.seed()
        self.before = json.dumps(self.world.workspaces, sort_keys=True)
        self.platform.calls.clear()

    def test_nothing_is_written(self):
        self.seed()
        self.assertEqual(self.platform.changes(), [])
        self.assertEqual(json.dumps(self.world.workspaces, sort_keys=True), self.before)

    def test_it_records_nothing_twice(self):
        self.seed()
        self.assertEqual(len(common.created()), 4)

    def test_a_budget_raised_in_the_file_is_updated_and_deployed(self):
        self.seed_files({"aim/workspaces/acme-bakery-teams.yaml": FILE.read_text(encoding="utf-8").replace("dollars: 2\n", "dollars: 3\n")})
        self.seed()
        self.assertEqual(self.budgets()["Fraud analytics"], (3_000_000, "DAY", None))
        self.assertEqual([method for method, _ in self.platform.changes()], ["PUT", "POST"])
        self.assertEqual(self.the_workspace()["deploymentState"], "DEPLOYED")

    def test_a_model_the_workspace_lost_is_added_back_on_its_own_with_its_alias(self):
        self.the_workspace()["models"][0]["models"].pop()
        self.seed()
        self.assertEqual({m["name"]: m["aliases"] for m in self.the_workspace()["models"][0]["models"]},
                         {"qwen3:1.7b": ["smart"], "qwen3:0.6b": ["lite"]})
        [added] = self.platform.sent("POST", re.compile(AIM + "/ai-workspaces/[^/]+/models/_bulk"))
        self.assertEqual([m["name"] for m in added.json[0]["models"]], ["qwen3:0.6b"])
        self.assertEqual([m for m, _ in self.platform.changes()], ["POST"])   # a model reaches the gateway without a deployment

    def test_a_model_the_file_does_not_list_is_removed_on_its_own(self):
        self.the_workspace()["models"][0]["models"].append({"name": "qwen3:30b", "aliases": []})
        self.seed()
        self.assertEqual([m["name"] for m in self.the_workspace()["models"][0]["models"]], ["qwen3:1.7b", "qwen3:0.6b"])
        self.assertEqual([m for m, _ in self.platform.changes()], ["DELETE"])

    def test_an_alias_that_differs_is_set_without_touching_which_models_there_are(self):
        self.the_workspace()["models"][0]["models"][0]["aliases"] = ["smart", "mine"]
        self.seed()
        self.assertEqual(self.the_workspace()["models"][0]["models"][0]["aliases"], ["smart"])
        self.assertEqual([m for m, _ in self.platform.changes()], ["PUT"])

    def test_a_router_someone_emptied_is_saved_again_and_deployed(self):
        self.the_workspace()["router"]["request"] = []
        self.seed()
        self.assertEqual(len(self.the_workspace()["router"]["request"]), 1)
        self.assertEqual(self.the_workspace()["deploymentState"], "DEPLOYED")

    def test_a_member_who_was_moved_goes_back_to_their_budget_with_the_same_key(self):
        member = self.the_workspace()["members"][0]
        subscription = member["subscriptionId"]
        member["budgetId"] = self.the_workspace()["budgets"][2]["id"]
        self.seed()
        self.assertEqual(self.the_workspace()["members"][0]["subscriptionId"], subscription)
        self.assertEqual(self.the_workspace()["members"][0]["budgetId"], self.the_workspace()["budgets"][0]["id"])

    def test_a_description_that_changed_is_written(self):
        shorter = FILE.read_text(encoding="utf-8").replace("Models for the bank's teams", "Models for teams")
        self.seed_files({"aim/workspaces/acme-bakery-teams.yaml": shorter})
        self.seed()
        self.assertTrue(self.the_workspace()["description"].startswith("Models for teams"))

    def test_a_workspace_of_that_name_the_seed_did_not_create_is_never_adopted(self):
        common.save_created([])
        with self.assertRaisesRegex(common.SeedError, "AI workspace Acme Bakery Teams exists and the seed did not create it"):
            self.seed()
        self.assertEqual(self.platform.changes(), [])


class Refilling(Workspaces):
    """The sandbox of the Hackathon gets its whole budget back on every run: after the workspace is deployed and the gateway
    serves it, and whatever Redis does."""

    def setUp(self):
        super().setUp()
        self.seed_files({"refills.yaml": REFILLS})
        self.env(GATEWAY_INTERNAL_URL="http://gateway")
        self.world.users["user-admin"] = ADMIN
        self.redis = FakeRedis()
        self.moments = []

        def connect():
            workspace = self.the_workspace()
            self.moments.append((workspace["deploymentState"], len(self.world.gateway_calls) > 0, len(workspace["members"])))
            return self.redis

        patch = mock.patch.object(refills, "connect", connect)
        patch.start()
        self.addCleanup(patch.stop)

    def subscription_of(self, source_id):
        user = next(u["id"] for u in self.world.users.values() if u["sourceId"] == source_id)
        return next(m["subscriptionId"] for m in self.the_workspace()["members"] if m["userId"] == user)

    def spend(self):
        """The counters the gateway keeps after the teams spent: one for each team, and one of another workspace."""
        workspace = self.the_workspace()["id"]
        for team in TEAMS:
            self.redis.data[f"ratelimit:{workspace}:app-{team}:cost-rl"] = {
                "counter": "116910", "limit": "120000", "subscription": self.subscription_of(team)}
        self.redis.data["ratelimit:elsewhere:app-team-hackathon:cost-rl"] = {"counter": "1", "subscription": "sub-elsewhere"}

    def kept(self):
        return sorted(key.split(":")[-2] for key in self.redis.data)

    def sandbox_counter(self):
        return f"ratelimit:{self.the_workspace()['id']}:app-team-hackathon:cost-rl"

    def test_the_first_run_finds_nothing_spent_and_says_so(self):
        self.seed()
        self.assertIn("[refills] hackathon: team-hackathon has spent nothing in this period", self.output())
        self.assertTrue(self.redis.closed)

    def test_a_run_gives_the_sandbox_its_budget_back_and_no_other_team_theirs(self):
        self.seed()
        self.spend()
        self.seed()
        self.assertEqual(self.kept(), ["app-team-care", "app-team-fraud", "app-team-hackathon"])
        self.assertNotIn(self.sandbox_counter(), self.redis.data)
        self.assertIn("[refills] ✓ hackathon: the cost budget of team-hackathon is whole again", self.output())

    def test_it_is_done_once_the_workspace_is_deployed_and_the_gateway_serves_it(self):
        self.seed()
        self.assertEqual(self.moments, [("DEPLOYED", True, 4)])

    def test_it_is_done_on_every_run_not_only_the_first(self):
        self.seed()
        for _ in range(2):
            self.spend()
            self.seed()
            self.assertNotIn(self.sandbox_counter(), self.redis.data)
        self.assertEqual(len(self.moments), 3)

    def test_a_refill_changes_nothing_in_the_platform(self):
        self.seed()
        self.platform.calls.clear()
        self.spend()
        self.seed()
        self.assertEqual(self.platform.changes(), [])

    def test_a_redis_that_cannot_be_reached_does_not_stop_the_workspace_being_seeded(self):
        with mock.patch.object(refills, "connect", side_effect=ConnectionError("Connection refused")):
            self.seed()
        self.assertEqual(self.the_workspace()["deploymentState"], "DEPLOYED")
        self.assertIn("! Could not refill hackathon: ConnectionError: Connection refused", self.output())
        self.assertIn("✓ AI workspace Acme Bakery Teams", self.output())

    def test_a_member_the_platform_does_not_have_yet_is_told_and_the_rest_of_the_workspace_is_seeded(self):
        self.world.users.pop("user-admin")
        self.seed_files({"refills.yaml": REFILLS.replace("member: team-hackathon", "member: admin")})
        self.seed()
        self.assertIn("! hackathon: admin is not a member of Acme Bakery Teams yet: nothing to refill", self.output())
        self.assertEqual(self.moments, [])

    def test_a_refill_of_a_member_the_file_does_not_list_stops_the_seed_before_anything_is_changed(self):
        self.seed_files({"refills.yaml": REFILLS.replace("member: team-hackathon", "member: team-nobody")})
        with self.assertRaisesRegex(common.SeedError, "team-nobody, who is not a member of AI workspace 'Acme Bakery Teams'"):
            self.seed()
        self.assertEqual(self.platform.changes(), [])

    def test_a_workspace_that_declares_no_refill_never_reaches_redis(self):
        self.seed_files({"refills.yaml": REFILLS.replace("workspace: Acme Bakery Teams", "workspace: Another")})
        self.seed()
        self.assertEqual(self.moments, [])


class SpendAwareRouting(Workspaces):
    """The router is a control: its step is enabled or not as the mode says, on every run."""

    def setUp(self):
        super().setUp()
        self.world.users["user-admin"] = ADMIN
        self.seed_files({"controls.yaml": CONTROLS})
        self.env(SEED_PROTECTIONS="off")

    def router(self):
        return self.the_workspace()["router"]["request"]

    def routes(self):
        return [(step["name"], step["enabled"]) for step in self.router()]

    def test_off_the_step_is_saved_disabled_with_the_rest_of_its_configuration_and_deployed(self):
        self.seed()
        self.assertEqual(self.routes(), [("Spend-aware routing", False)])
        self.assertEqual(json.loads(self.router()[0]["configuration"])["bands"][0]["name"], "Plenty left")
        self.assertEqual(self.router()[0]["condition"], "{#context.attributes['gravitee.attribute.llm.kind'] == 'PROMPT'}")
        self.assertEqual(self.the_workspace()["deploymentState"], "DEPLOYED")
        self.assertEqual(len(self.platform.sent("PUT", re.compile(AIM + "/ai-workspaces/[^/]+/router"))), 1)
        self.assertIn("Router controls are off: router", self.output())

    def test_on_the_step_is_saved_enabled(self):
        self.env(SEED_PROTECTIONS="on")
        self.seed()
        self.assertEqual(self.routes(), [("Spend-aware routing", True)])

    def test_without_the_setting_the_router_is_on(self):
        self.env(SEED_PROTECTIONS=None)
        self.seed()
        self.assertEqual(self.routes(), [("Spend-aware routing", True)])

    def test_a_second_run_writes_nothing(self):
        self.seed()
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.platform.changes(), [])

    def test_a_step_someone_switched_on_is_switched_off_again_and_deployed(self):
        self.seed()
        self.router()[0]["enabled"] = True
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.routes(), [("Spend-aware routing", False)])
        self.assertEqual([m for m, _ in self.platform.changes()], ["PUT", "POST"])
        self.assertEqual(self.the_workspace()["deploymentState"], "DEPLOYED")

    def test_the_other_mode_switches_it_in_place_without_touching_anything_else(self):
        self.seed()
        self.env(SEED_PROTECTIONS="on")
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.routes(), [("Spend-aware routing", True)])
        changed = [path.rsplit("/", 1)[1] for _, path in self.platform.changes()]
        self.assertEqual(changed, ["router", "_deploy"])

    def test_a_router_step_a_control_names_that_the_file_lacks_stops_the_seed_before_anything_is_written(self):
        self.seed_files({"controls.yaml": CONTROLS.replace("Spend-aware routing", "Spend aware routing")})
        with self.assertRaisesRegex(common.SeedError, "control router: no request step named 'Spend aware routing' in workspace "
                                                       "'Acme Bakery Teams'"):
            self.seed()
        self.assertEqual(self.platform.sent("PUT", re.compile(AIM + "/ai-workspaces/[^/]+/router")), [])

    def test_a_workspace_without_a_control_saves_its_router_as_its_file_says(self):
        self.seed_files({"controls.yaml": "other: {workspace: Another, steps: {request: [Spend-aware routing]}}\n"})
        self.seed()
        self.assertEqual(self.routes(), [("Spend-aware routing", True)])


class Gateway(Workspaces):

    def setUp(self):
        super().setUp()
        self.env(GATEWAY_INTERNAL_URL="http://gateway")
        self.world.users["user-admin"] = ADMIN

    def test_the_gateway_is_not_asked_when_the_seed_does_not_know_it(self):
        self.env(GATEWAY_INTERNAL_URL=None)
        self.seed()
        self.assertEqual(self.world.gateway_calls, [])
        self.assertEqual(self.clock.sleeps, [])

    def test_the_gateway_is_not_asked_and_nothing_is_slept_unless_the_seed_is_asked_to_wait_for_it(self):
        self.env(SEED_WAIT_GATEWAY="0")
        self.seed()
        self.assertEqual(self.world.gateway_calls, [])
        self.assertEqual(self.clock.sleeps, [])

    def test_the_seed_waits_until_a_key_is_accepted_with_its_budget_then_for_two_synchronizations(self):
        self.world.gateway_serves_after = 3
        self.seed()
        self.assertEqual(len(self.world.gateway_calls), 4)
        self.assertTrue(self.world.gateway_calls[-1].headers["Authorization"].startswith("Bearer key-of-sub-"))
        self.assertGreaterEqual(self.clock.elapsed, 2 * 1 + 1)

    def test_an_answer_that_does_not_carry_the_budget_is_the_previous_deployment_not_the_workspace(self):
        self.world.gateway_flows_after = 2
        self.seed()
        self.assertEqual(len(self.world.gateway_calls), 3)

    def test_a_workspace_that_is_already_live_is_checked_once_and_not_waited_for(self):
        self.seed()
        self.world.gateway_calls.clear()
        slept = len(self.clock.sleeps)
        self.seed()
        self.assertEqual(len(self.world.gateway_calls), 1)
        self.assertEqual(self.clock.sleeps[slept:], [])

    def test_a_gateway_that_never_serves_it_stops_the_seed_with_the_last_answer(self):
        self.world.gateway_serves_after = 10_000
        self.env(GATEWAY_READY_TIMEOUT="20")
        with self.assertRaisesRegex(common.SeedError, r"Acme Bakery Teams on the gateway \(/acme-bakery-teams\) not ready after 20 s: "
                                                       r"HTTP 404 .*No context-path matches"):
            self.seed()


class Removal(Workspaces):

    def setUp(self):
        super().setUp()
        self.world.users["user-admin"] = ADMIN
        self.seed()
        self.platform.calls.clear()

    def remove(self):
        aim_workspaces.remove(common.created())

    def test_the_workspace_goes_then_the_applications_of_its_users_then_the_users(self):
        self.remove()
        self.assertEqual(self.world.workspaces, {})
        self.assertEqual(list(self.world.users), ["user-admin"])
        changes = self.platform.changes()
        self.assertEqual([method for method, _ in changes], ["DELETE"] * 7)
        kinds = ["workspace" if "/ai-workspaces/" in path else "application" if "/applications/" in path else "user"
                 for _, path in changes]
        self.assertEqual(kinds, ["workspace"] + ["application"] * 3 + ["user"] * 3)

    def test_the_presenters_application_and_user_are_never_touched(self):
        self.remove()
        self.assertEqual(list(self.world.applications), ["app-user-admin"])
        self.assertEqual(self.platform.sent("DELETE", re.compile(USERS + "/user-admin")), [])

    def test_an_application_another_workspace_uses_stays(self):
        care = next(u["id"] for u in self.world.users.values() if u["sourceId"] == "team-care")
        self.world.applications[f"app-{care}"].add("someone-elses-workspace")
        self.remove()
        self.assertEqual(sorted(self.world.applications), sorted([f"app-{care}", "app-user-admin"]))

    def test_a_workspace_that_is_gone_is_not_an_error_and_its_users_still_go(self):
        self.world.workspaces.clear()
        self.remove()
        self.assertEqual(list(self.world.users), ["user-admin"])

    def test_what_another_module_made_is_left_alone(self):
        theirs = [{"product": "aim-proxies", "kind": "mcp-proxy", "name": "p", "id": "p", "label": ""},
                  {"product": "aim-catalog", "kind": "provider", "name": "o", "id": "o", "label": ""}]
        aim_workspaces.remove(theirs)
        self.assertEqual(self.platform.calls, [])

    def test_it_says_what_it_deleted(self):
        self.remove()
        self.assertIn("✓ Deleted AI workspace 'Acme Bakery Teams'", self.output())
        self.assertIn("✓ Deleted workspace user 'team-care'", self.output())


class Folder(Seed):

    def test_a_playground_without_workspaces_needs_no_setting_and_makes_no_call(self):
        self.env(APIM_MANAGEMENT_URL=None)
        aim_workspaces.seed()
        aim_workspaces.remove([])
        self.assertEqual(self.platform.calls, [])


if __name__ == "__main__":
    unittest.main()
