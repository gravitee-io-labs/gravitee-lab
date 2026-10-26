"""API Management converges: a second run changes nothing, a changed definition is replaced or updated, a subscription
asks for its key, the seed waits for the gateway within one deadline, and the files of Acme Bakery hold what the playground
says they do. API Management is a small memory that keeps what it is given and answers from it."""

import copy
import itertools
import json
import re
import shutil
import threading
import unittest
from unittest import mock

import requests

from lab_seeder import apim
from lab_seeder import common
from lab_seeder import controls
from lab_seeder.testkit import PLAYGROUNDS, Seed

V1 = "/management/organizations/DEFAULT/environments/DEFAULT"
QUERY_SYNTAX = set('()[]{}:"~^*?!\\')   # what the search of applications reads as syntax, not as a character of the name
V2 = "/management/v2/organizations/DEFAULT/environments/DEFAULT"
GATEWAY = "http://gateway:8082"
NO_API = (404, "No context-path matches the request URI.")   # what the gateway answers when no API has the path
ACME_BAKERY = PLAYGROUNDS / "acme-bakery" / "seed" / "apim"

# What the files of Acme Bakery use, as the seed service of the playground sets it
VARIABLES = {
    "OLLAMA_URL": "http://ollama:11434", "REDIS_URL": "redis://redis:6379", "REDIS_PASSWORD": "redis-secret",
    "BAKERY_AGENT_URL": "http://bakery-agent:8000", "BAKERY_API_URL": "http://bakery-api:8000",
    "AM_GATEWAY_URL": "http://am-gateway:8092", "MCP_RESOURCE_SECRET": "mcp-secret",
    "ACE_LLM_API_KEY": "ace-key-0001", "DEV_LLM_API_KEY": "dev-key-0001", "TRAFFIC_LLM_API_KEY": "traffic-key-0001",
}


def searched(applications, params):
    """What GET /applications answers: every application without a query; with one, a search, which a name holding query
    syntax (a parenthesis, a colon) never matches, as the Management API does."""
    query = params.get("query")
    if query is None:
        return list(applications)
    return [] if QUERY_SYNTAX & set(query) else [a for a in applications if a["name"] == query]


class Apim:
    """API Management as far as the seed uses it: APIs with their plans and state, applications and subscriptions."""

    NOT_UPDATABLE = ("LLM_PROXY", "A2A_PROXY", "MCP_PROXY")   # PUT _import/definition answers 500 for these

    def __init__(self):
        self.ids = itertools.count(1)
        self.apis, self.applications, self.subscriptions, self.settings = {}, {}, {}, {}
        self.deployed = []

    def new(self, kind):
        return f"{kind}-{next(self.ids)}"

    @staticmethod
    def path_of(api):
        return api["listeners"][0]["paths"][0]["path"]

    @staticmethod
    def id_in(call):
        return call.path.split("/apis/")[1].split("/")[0]

    # --- APIs ---------------------------------------------------------------------------------

    @staticmethod
    def row(api):
        return {key: api[key] for key in ("id", "name", "type", "listeners", "state", "lifecycleState")}

    def list_apis(self, call):
        return 200, {"data": [self.row(api) for api in self.apis.values()], "pagination": {"pageCount": 1}}

    def get_api(self, call):
        api = self.apis.get(self.id_in(call))
        if not api:
            return 404, {"message": "not found"}
        return 200, {**self.row(api), "flows": copy.deepcopy(api["flows"]), "deploymentState": api["deploymentState"]}

    def import_api(self, call):
        api = call.json["api"]
        api_id = api.get("id") or self.new("api")
        if api_id in self.apis:
            return 400, {"technicalCode": "api.exists"}
        if any(self.path_of(other) == self.path_of(api) for other in self.apis.values()):
            return 400, {"technicalCode": "path.invalid"}
        plans = [{"id": plan.get("id") or self.new("plan"), "name": plan["name"]} for plan in call.json.get("plans", [])]
        self.apis[api_id] = {"id": api_id, "name": api["name"], "type": api.get("type", "PROXY"), "listeners": api["listeners"],
                             "state": "STOPPED", "lifecycleState": "CREATED", "plans": plans, "definition": call.json,
                             "flows": copy.deepcopy(api.get("flows", [])), "deploymentState": "NEED_REDEPLOY"}
        return 201, {"id": api_id}

    def update_api(self, call):
        api = self.apis[self.id_in(call)]
        if api["type"] in self.NOT_UPDATABLE:
            return 500, {"message": f"Unsupported API type: {api['type']}"}
        api["definition"], api["flows"] = call.json, copy.deepcopy(call.json["api"].get("flows", []))
        api["deploymentState"] = "NEED_REDEPLOY"
        return 200, {}

    def publish(self, call):
        """An update of the entity: its lifecycle, and its flows, which then wait for a deployment."""
        api = self.apis[self.id_in(call)]
        api["lifecycleState"] = call.json["lifecycleState"]
        if "flows" in call.json and call.json["flows"] != api["flows"]:
            api["flows"], api["deploymentState"] = copy.deepcopy(call.json["flows"]), "NEED_REDEPLOY"
        return 200, {}

    def deploy(self, call):
        self.deployed.append(self.id_in(call))
        self.apis[self.id_in(call)]["deploymentState"] = "DEPLOYED"
        return 202

    def start(self, call):
        self.apis[self.id_in(call)]["state"] = "STARTED"
        return 204

    def stop(self, call):
        api = self.apis[self.id_in(call)]
        was_stopped, api["state"] = api["state"] == "STOPPED", "STOPPED"
        return 400 if was_stopped else 204

    def delete_api(self, call):
        api_id = self.id_in(call)
        self.subscriptions = {i: s for i, s in self.subscriptions.items() if s["apiId"] != api_id}   # closePlans
        return 204 if self.apis.pop(api_id, None) else 404

    def plans(self, call):
        return 200, {"data": self.apis[self.id_in(call)]["plans"]}

    # --- Applications and subscriptions ---------------------------------------------------------

    def applications_named(self, call):
        return 200, searched(self.applications.values(), call.params)

    def create_application(self, call):
        application = {"id": self.new("application"), **call.json}
        self.applications[application["id"]] = application
        return 201, application

    def update_application(self, call):
        self.applications[call.path.rsplit("/", 1)[1]].update(call.json)
        return 200, {}

    def subscriptions_of(self, call):
        return 200, {"data": [s for s in self.subscriptions.values() if s["apiId"] == self.id_in(call)
                              and s["planId"] == call.params["planIds"] and s["applicationId"] == call.params["applicationIds"]
                              and s["status"] == "ACCEPTED"]}

    def subscribe(self, call):
        subscription = {"id": self.new("subscription"), "apiId": self.id_in(call), "status": "ACCEPTED", **call.json}
        self.subscriptions[subscription["id"]] = subscription
        return 201, subscription

    def close_subscription(self, call):
        self.subscriptions[call.path.split("/")[-2]]["status"] = "CLOSED"
        return 204

    def routes(self):
        api = V2 + "/apis/[^/]+"
        return {
            ("GET", V1 + "/settings"): lambda call: (200, self.settings),
            ("POST", V1 + "/settings"): lambda call: self.settings.update(call.json) or 200,
            ("GET", V2 + "/apis"): self.list_apis,
            ("POST", V2 + "/apis/_import/definition"): self.import_api,
            ("GET", re.compile(api)): self.get_api,
            ("PUT", re.compile(api + "/_import/definition")): self.update_api,
            ("PUT", re.compile(api)): self.publish,
            ("POST", re.compile(api + "/deployments")): self.deploy,
            ("POST", re.compile(api + "/_start")): self.start,
            ("POST", re.compile(api + "/_stop")): self.stop,
            ("DELETE", re.compile(api)): self.delete_api,
            ("GET", re.compile(api + "/plans")): self.plans,
            ("GET", re.compile(api + "/subscriptions")): self.subscriptions_of,
            ("POST", re.compile(api + "/subscriptions")): self.subscribe,
            ("POST", re.compile(api + "/subscriptions/[^/]+/_close")): self.close_subscription,
            ("GET", V1 + "/applications"): self.applications_named,
            ("POST", V1 + "/applications"): self.create_application,
            ("PUT", re.compile(V1 + "/applications/[^/]+")): self.update_application,
            ("DELETE", re.compile(V1 + "/applications/[^/]+")): 204,
        }


def definition(kind, api_id, name, path, plans=("Open",), **api):
    """An API file, `{"api": ..., "plans": [...]}`, as text with its variables not replaced yet."""
    return json.dumps({"api": {"id": api_id, "type": kind, "name": name, "listeners": [{"paths": [{"path": path}]}], **api},
                       "plans": [{"id": f"{api_id}-{plan}", "name": plan, "validation": "AUTO"} for plan in plans]})


LLM = definition("LLM_PROXY", "llm-id", "ACME LLM", "/llm/", ("Ace", "Developers"), description="models",
                 endpointGroups=[{"endpoints": [{"configuration": {"target": "${TARGET}", "models": [
                     {"name": "qwen3:1.7b", "aliases": ["chat"]}]}}]}], resources=[{"type": "ai-model-text-classification"}])
A2A = definition("A2A_PROXY", "a2a-id", "Ace A2A", "/ace/", ("Bank users",))
REST = definition("PROXY", "rest-id", "Open API", "/open/")
FILES = {"apim/apis/llm.json": LLM, "apim/apis/a2a.json": A2A, "apim/apis/rest.json": REST,
         "apim/applications/web.yaml": "name: Web\nsettings:\n  app: {type: SIMPLE, client_id: web}\n",
         "apim/applications/dev.yaml": "name: Dev\ndescription: Developers\n",
         "apim/subscriptions/all.yaml": ("subscriptions:\n"
                                         "  - {application: Web, api: Ace A2A, plan: Bank users}\n"
                                         "  - {application: Dev, api: ACME LLM, plan: Developers, apiKey: '${DEV_KEY}'}\n")}
SETTINGS = "plan: {security: {customApiKey: {enabled: true}}}\n"

# An LLM proxy with the steps of two controls, and one the lessons leave on
GUARDED = definition(
    "LLM_PROXY", "llm-id", "ACME LLM", "/llm/", ("Ace", "Developers"), description="models",
    endpointGroups=[{"endpoints": [{"configuration": {"target": "${TARGET}", "models": [{"name": "qwen3:1.7b", "aliases": ["chat"]}]}}]}],
    resources=[{"type": "ai-model-text-classification"}],
    flows=[{"name": "Prompt", "enabled": True,
            "request": [{"name": "Prompt guard rails", "policy": "ai-prompt-guard-rails", "enabled": True},
                        {"name": "PII filtering", "policy": "pii-filtering", "enabled": True},
                        {"name": "Semantic cache", "policy": "ai-semantic-caching", "enabled": True}],
            "response": [{"name": "PII filtering", "policy": "pii-filtering", "enabled": True},
                         {"name": "Semantic cache", "policy": "ai-semantic-caching", "enabled": True}]}])
CONTROLS = ("guard: {api: ACME LLM, flow: Prompt, steps: {request: [Prompt guard rails]}}\n"
            "pii: {api: ACME LLM, flow: Prompt, steps: {request: [PII filtering], response: [PII filtering]}}\n")
PROTECTED = {("request", "Prompt guard rails"): True, ("request", "PII filtering"): True, ("request", "Semantic cache"): True,
             ("response", "PII filtering"): True, ("response", "Semantic cache"): True}
UNPROTECTED = {**PROTECTED, ("request", "Prompt guard rails"): False, ("request", "PII filtering"): False,
               ("response", "PII filtering"): False}
IN_PLACE = [("PUT", V2 + "/apis/llm-id"), ("POST", V2 + "/apis/llm-id/deployments")]


class Playground(Seed):
    """The module's seed files, and API Management ready to be seeded."""

    def setUp(self):
        super().setUp()
        self.apim = Apim()
        self.answer(self.apim.routes())
        self.env(TARGET="http://ollama:11434", DEV_KEY="dev-key-0001")
        self.seed_files(FILES)

    def run_seed(self):
        """One run of the module, and what it asked API Management to change."""
        before = len(self.platform.calls)
        apim.seed()
        return [(call.method, call.path) for call in self.platform.calls[before:] if call.method != "GET"]

    def edit(self, name, change):
        path = common.SEED_DIR / name
        path.write_text(change(path.read_text(encoding="utf-8")), encoding="utf-8")

    def record(self, name):
        return next(x for x in common.created() if x["name"] == name)

    def keys(self):
        return {s["id"]: s["customApiKey"] for s in self.apim.subscriptions.values() if "customApiKey" in s}


class FirstRun(Playground):

    def test_every_api_is_imported_published_deployed_and_started(self):
        self.run_seed()
        self.assertEqual({a["name"]: (a["state"], a["lifecycleState"]) for a in self.apim.apis.values()},
                         {"ACME LLM": ("STARTED", "PUBLISHED"), "Ace A2A": ("STARTED", "PUBLISHED"), "Open API": ("STARTED", "PUBLISHED")})
        self.assertEqual(sorted(self.apim.deployed), ["a2a-id", "llm-id", "rest-id"])

    def test_an_api_has_the_id_of_its_file_and_is_recorded_with_the_digest_of_its_definition(self):
        self.run_seed()
        digest = apim.digest_of(json.loads(common.render(LLM, "llm.json", json_escape=True)))
        self.assertEqual((self.record("ACME LLM")["id"], self.record("ACME LLM")["digest"]), ("llm-id", digest))
        self.assertEqual({x["kind"] for x in common.created()}, {"api", "application", "subscription"})

    def test_an_application_whose_name_holds_query_syntax_is_found_by_its_subscription_and_by_the_next_run(self):
        self.edit("apim/applications/dev.yaml", lambda text: text.replace("name: Dev", "name: Dev (bank team)"))
        self.edit("apim/subscriptions/all.yaml", lambda text: text.replace("application: Dev,", "application: Dev (bank team),"))
        self.run_seed()
        again = self.run_seed()
        dev = [i for i, a in self.apim.applications.items() if a["name"] == "Dev (bank team)"]
        self.assertEqual(len(dev), 1)
        self.assertEqual([s["planId"] for s in self.apim.subscriptions.values() if s["applicationId"] == dev[0]], ["llm-id-Developers"])
        self.assertNotIn(("POST", V1 + "/applications"), again)

    def test_a_subscription_with_a_key_asks_for_that_custom_key_and_one_without_asks_for_none(self):
        self.run_seed()
        by_plan = {s["planId"]: s for s in self.apim.subscriptions.values()}
        self.assertEqual(by_plan["llm-id-Developers"]["customApiKey"], "dev-key-0001")
        self.assertNotIn("customApiKey", by_plan["a2a-id-Bank users"])

    def test_no_key_reaches_the_output_or_the_records(self):
        self.run_seed()
        self.assertNotIn("dev-key-0001", self.output())
        self.assertNotIn("dev-key-0001", self.state.read_text(encoding="utf-8"))

    def test_the_settings_are_applied_before_a_subscription_asks_for_a_custom_key(self):
        self.seed_files({"apim/settings.yaml": SETTINGS})
        changes = self.run_seed()
        self.assertLess(changes.index(("POST", V1 + "/settings")), changes.index(("POST", V2 + "/apis/llm-id/subscriptions")))

    def test_an_application_is_made_with_its_client_id(self):
        self.run_seed()
        web = next(a for a in self.apim.applications.values() if a["name"] == "Web")
        self.assertEqual(web["settings"], {"app": {"type": "SIMPLE", "client_id": "web"}})

    def test_a_definition_without_an_id_is_found_again_by_its_path(self):
        self.seed_files({"apim/apis/rest.json": REST.replace('"id": "rest-id", ', "")})
        self.run_seed()
        self.assertEqual(self.record("Open API")["id"], "api-1")
        self.assertEqual(self.run_seed(), [])

    def test_an_api_the_seed_did_not_create_holds_the_path_of_one_with_a_free_id(self):
        self.apim.apis["mine"] = {"id": "mine", "name": "My proxy", "type": "PROXY", "state": "STARTED", "lifecycleState": "PUBLISHED",
                                  "listeners": [{"paths": [{"path": "/ace/"}]}]}
        with self.assertRaisesRegex(common.SeedError, "/ace/ is the path of 'My proxy', which the seed did not create"):
            self.run_seed()
        self.assertEqual([x for x in self.platform.changes() if "/apis/" in x[1]], [])

    def test_an_application_the_seed_did_not_create_stops_the_seed_and_is_left_as_it_is(self):
        mine = {"id": "mine", "name": "Web", "description": "MY OWN APP", "settings": {"app": {"type": "SIMPLE", "client_id": "my-client"}}}
        self.apim.applications["mine"] = dict(mine)
        with self.assertRaisesRegex(common.SeedError, "^Application Web: the name is taken by an application the seed did not create$"):
            self.run_seed()
        self.assertEqual(self.apim.applications["mine"], mine)
        self.assertNotIn("mine", [x["id"] for x in common.created()])

    def test_a_subscription_goes_to_the_api_the_seed_made_and_not_to_another_with_the_same_name(self):
        self.apim.apis["user-api"] = {"id": "user-api", "name": "Ace A2A", "type": "PROXY", "state": "STARTED", "lifecycleState": "PUBLISHED",
                                      "listeners": [{"paths": [{"path": "/user/"}]}], "plans": [{"id": "user-plan", "name": "Bank users"}]}
        self.run_seed()
        self.assertEqual({s["apiId"] for s in self.apim.subscriptions.values()}, {"a2a-id", "llm-id"})

    def test_an_api_of_the_platforms_own_environment_keeps_the_groups_its_file_names_without_a_lookup(self):
        self.seed_files({"apim/apis/rest.json": definition("PROXY", "rest-id", "Open API", "/open/", groups=["Some group"])})
        self.run_seed()
        self.assertEqual(self.apim.apis["rest-id"]["definition"]["api"]["groups"], ["Some group"])
        self.assertEqual(self.run_seed(), [])

    def test_a_subscription_of_the_platforms_own_environment_that_stays_pending_takes_no_key(self):
        self.seed_files({"apim/subscriptions/all.yaml": "subscriptions:\n  - {application: Web, api: Ace A2A, plan: Bank users, pending: true, apiKey: k}\n"})
        with self.assertRaisesRegex(common.SeedError, "^Subscription Web → Ace A2A \\(Bank users\\): a subscription that stays pending takes no apiKey"):
            self.run_seed()
        self.assertEqual(self.apim.subscriptions, {})

    def test_a_subscription_to_an_api_the_seed_did_not_make_stops_the_seed(self):
        self.apim.apis["user-api"] = {"id": "user-api", "name": "Other API", "type": "PROXY", "state": "STARTED", "lifecycleState": "PUBLISHED",
                                      "listeners": [{"paths": [{"path": "/user/"}]}], "plans": [{"id": "user-plan", "name": "Open"}]}
        self.seed_files({"apim/subscriptions/all.yaml": "subscriptions:\n  - {application: Web, api: Other API, plan: Open}\n"})
        with self.assertRaisesRegex(common.SeedError, r"^Subscription Web → Other API \(Open\): the seed made no API with this name$"):
            self.run_seed()
        self.assertEqual(self.apim.subscriptions, {})


class Converged(Playground):
    """Seeded once, with its settings."""

    def setUp(self):
        super().setUp()
        self.seed_files({"apim/settings.yaml": SETTINGS})
        self.run_seed()


class SecondRun(Converged):

    def test_a_second_run_asks_for_no_change(self):
        self.assertEqual(self.run_seed(), [])
        self.assertIn("API up to date: ACME LLM", self.output())

    def test_a_second_run_ends_with_the_same_objects_ids_and_keys(self):
        before = (dict(self.apim.apis), dict(self.apim.applications), self.keys(), list(self.apim.deployed), common.created())
        self.run_seed()
        self.assertEqual((dict(self.apim.apis), dict(self.apim.applications), self.keys(), list(self.apim.deployed), common.created()), before)

    def test_an_api_is_looked_up_by_its_id_and_not_by_listing_them(self):
        before = len(self.platform.calls)
        apim.ApiManagement().import_api(json.loads(common.render(LLM, "llm.json", json_escape=True)), "llm.json")
        self.assertEqual([(c.method, c.path) for c in self.platform.calls[before:]], [("GET", V2 + "/apis/llm-id")])

    def test_an_api_that_does_not_run_is_started_again_without_a_new_import(self):
        self.apim.apis["rest-id"]["state"] = "STOPPED"
        self.assertEqual(self.run_seed(), [("POST", V2 + "/apis/rest-id/deployments"), ("POST", V2 + "/apis/rest-id/_start")])

    def test_an_api_that_is_not_published_is_published_and_deployed_again(self):
        self.apim.apis["rest-id"]["lifecycleState"] = "CREATED"
        self.assertEqual(self.run_seed(), [("PUT", V2 + "/apis/rest-id"), ("POST", V2 + "/apis/rest-id/deployments")])

    def test_what_the_record_forgot_stops_the_seed_rather_than_adopting_it(self):
        self.state.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(common.SeedError, "a2a-id is the id of 'Ace A2A', which the seed did not create"):
            self.run_seed()

    def test_an_application_the_record_forgot_stops_the_seed_rather_than_being_adopted(self):
        common.save_created([x for x in common.created() if x["kind"] != "application"])
        before = len(self.platform.calls)
        # Run together, the applications are seeded at once: either one is the first to find its name taken, and nothing is written
        with self.assertRaisesRegex(common.SeedError, "Application (Dev|Web): the name is taken by an application the seed did not create"):
            self.run_seed()
        self.assertEqual([c for c in self.platform.calls[before:] if c.method != "GET" and "/applications" in c.path], [])

    def test_an_application_that_holds_its_file_is_left_alone(self):
        self.assertEqual([c for c in self.run_seed() if "/applications" in c[1]], [])
        self.assertIn("Application exists: Web", self.output())

    def test_an_application_that_differs_from_its_file_is_updated(self):
        self.edit("apim/applications/dev.yaml", lambda text: text.replace("Developers", "Developers of the bank"))
        changes = [c for c in self.run_seed() if "/applications" in c[1]]
        self.assertEqual(changes, [("PUT", V1 + "/applications/" + next(i for i, a in self.apim.applications.items() if a["name"] == "Dev"))])

    def test_settings_that_are_set_are_not_written_again(self):
        self.assertNotIn(("POST", V1 + "/settings"), self.run_seed())
        self.apim.settings.clear()
        self.assertIn(("POST", V1 + "/settings"), self.run_seed())


class ChangedDefinition(Converged):

    def test_an_api_that_cannot_be_updated_is_replaced_with_its_ids_and_deployed_once(self):
        before = self.record("ACME LLM")["digest"]
        self.env(TARGET="http://another:11434")
        self.assertEqual(self.run_seed(), [
            ("PUT", V2 + "/apis/llm-id/_import/definition"), ("POST", V2 + "/apis/llm-id/_stop"), ("DELETE", V2 + "/apis/llm-id"),
            ("POST", V2 + "/apis/_import/definition"), ("PUT", V2 + "/apis/llm-id"), ("POST", V2 + "/apis/llm-id/deployments"),
            ("POST", V2 + "/apis/llm-id/_start"), ("POST", V2 + "/apis/llm-id/subscriptions")])
        self.assertIn("API replaced: ACME LLM", self.output())
        target = self.apim.apis["llm-id"]["definition"]["api"]["endpointGroups"][0]["endpoints"][0]["configuration"]["target"]
        self.assertEqual(target, "http://another:11434")
        self.assertNotEqual(self.record("ACME LLM")["digest"], before)
        self.assertEqual(self.record("ACME LLM")["id"], "llm-id")

    def test_a_replaced_api_gets_its_subscriptions_again_with_the_same_key(self):
        keys = self.keys()
        self.env(TARGET="http://another:11434")
        self.run_seed()
        self.assertEqual(sorted(self.keys().values()), sorted(keys.values()))
        self.assertEqual(len(self.apim.subscriptions), 2)

    def test_the_other_apis_are_left_alone(self):
        self.env(TARGET="http://another:11434")
        self.assertEqual([c for c in self.run_seed() if "a2a-id" in c[1] or "rest-id" in c[1]], [])

    def test_an_api_that_can_be_updated_is_updated_in_place_and_deployed_once(self):
        before = self.record("Open API")["digest"]
        self.edit("apim/apis/rest.json", lambda text: text.replace('"name": "Open API"', '"name": "Open API", "description": "new"'))
        self.assertEqual(self.run_seed(), [("PUT", V2 + "/apis/rest-id/_import/definition"), ("POST", V2 + "/apis/rest-id/deployments")])
        self.assertIn("API updated: Open API", self.output())
        self.assertNotEqual(self.record("Open API")["digest"], before)
        self.assertEqual(self.run_seed(), [])

    def test_an_update_that_fails_for_another_reason_stops_the_seed_and_keeps_the_digest(self):
        before = self.record("Open API")["digest"]
        self.platform.add({("PUT", V2 + "/apis/rest-id/_import/definition"): (400, {"message": "invalid plan"})})
        self.edit("apim/apis/rest.json", lambda text: text.replace('"name": "Open API"', '"name": "Open API", "description": "new"'))
        with self.assertRaisesRegex(common.SeedError, "API Open API: HTTP 400"):
            self.run_seed()
        self.assertEqual(self.record("Open API")["digest"], before)


class Renamed(Converged):
    """An API found by its id or its path whose file gives it another name is the same API."""

    def rename(self, name="Open API renamed"):
        self.edit("apim/apis/rest.json", lambda text: text.replace('"name": "Open API"', f'"name": "{name}"'))

    def test_a_renamed_api_is_updated_once_and_is_one_record_that_a_second_run_leaves_alone(self):
        self.rename()
        self.assertEqual(self.run_seed(), [("PUT", V2 + "/apis/rest-id/_import/definition"), ("POST", V2 + "/apis/rest-id/deployments")])
        self.assertEqual([x["name"] for x in common.created() if x["kind"] == "api" and x["id"] == "rest-id"], ["Open API renamed"])
        self.assertEqual(self.run_seed(), [])
        self.assertIn("API up to date: Open API renamed", self.output())
        self.assertEqual(len([x for x in common.created() if x["kind"] == "api"]), 3)

    def test_a_renamed_api_is_counted_once_by_a_removal(self):
        self.rename()
        self.run_seed()
        self.assertEqual(self.main("--remove"), 0, self.output())
        self.assertEqual(len([line for line in self.output().splitlines() if "Deleted api" in line]), 3)

    def test_a_renamed_api_that_is_replaced_takes_its_new_id_and_leaves_no_record_of_the_old_one(self):
        without_id = LLM.replace('"id": "llm-id", ', "")   # found by its path, and replaced under an id of the platform's making
        self.seed_files({"apim/apis/llm.json": without_id})
        self.run_seed()
        first = self.record("ACME LLM")["id"]
        self.assertNotEqual(first, "llm-id")
        self.seed_files({"apim/apis/llm.json": without_id.replace("ACME LLM", "ACME models"),
                         "apim/subscriptions/all.yaml": FILES["apim/subscriptions/all.yaml"].replace("ACME LLM", "ACME models")})
        self.env(TARGET="http://another:11434")
        self.run_seed()
        llm = [x for x in common.created() if x["kind"] == "api" and x["name"] in ("ACME LLM", "ACME models")]
        self.assertEqual([x["name"] for x in llm], ["ACME models"])
        self.assertNotIn(first, [x["id"] for x in common.created()])
        self.assertEqual(self.run_seed(), [])


class ImportThatFails(Playground):
    """The platform keeps the API of an import that fails after it made it (STOPPED, with the error in its message). The seed
    records it, so that the run that follows finds it as its own, where it would stop on an API it did not create."""

    def fail(self, name="Open API", times=1):
        """The import of this API makes it and answers 400 with its id, the first `times` times."""
        failures = itertools.count()

        def imported(call):
            status, body = self.apim.import_api(call)
            if call.json["api"]["name"] == name and next(failures) < times:
                return 400, {"httpStatus": 400, "message": "API imported with error:\n- (Plans) Security type is required",
                             "parameters": {"apiId": body["id"]}, "details": []}
            return status, body
        self.platform.add({("POST", V2 + "/apis/_import/definition"): imported})

    def test_an_api_made_with_an_error_is_recorded_with_no_digest_and_the_next_run_updates_it(self):
        self.fail()
        with self.assertRaisesRegex(common.SeedError, "HTTP 400"):
            self.run_seed()
        self.assertEqual(self.record("Open API")["id"], "rest-id")
        self.assertNotIn("digest", self.record("Open API"))
        self.assertIn("! Open API: the platform made the API but not all of it: the next run updates it", self.output())
        changes = self.run_seed()
        self.assertIn(("PUT", V2 + "/apis/rest-id/_import/definition"), changes)
        self.assertNotIn(("POST", V2 + "/apis/_import/definition"), changes)
        self.assertIn("digest", self.record("Open API"))
        self.assertEqual(self.apim.apis["rest-id"]["state"], "STARTED")
        self.assertEqual(self.run_seed(), [])

    def test_the_api_of_the_error_is_not_one_the_seed_did_not_create(self):
        self.fail()
        with self.assertRaises(common.SeedError):
            self.run_seed()
        self.run_seed()
        self.assertNotIn("which the seed did not create", self.output())

    def test_an_error_that_names_no_api_records_nothing(self):
        for body in ("not json", {"message": "no"}, {"parameters": []}, {"parameters": {"apiId": ""}}, [1], None):
            with self.subTest(body=body):
                common.save_created([])
                self.platform.add({("POST", V2 + "/apis/_import/definition"): (400, body)})
                with self.assertRaises(common.SeedError):
                    self.run_seed()
                self.assertEqual([x for x in common.created() if x["kind"] == "api"], [])

    def test_a_replacement_that_fails_after_the_platform_made_the_api_is_recorded_under_its_name_with_no_digest(self):
        self.run_seed()
        self.fail("ACME LLM")
        self.env(TARGET="http://another:11434")
        with self.assertRaisesRegex(common.SeedError, "HTTP 400"):
            self.run_seed()
        self.assertEqual((self.record("ACME LLM")["id"], "digest" in self.record("ACME LLM")), ("llm-id", False))
        self.assertEqual(len([x for x in common.created() if x["name"] == "ACME LLM"]), 1)
        self.run_seed()
        self.assertIn("digest", self.record("ACME LLM"))
        self.assertEqual(self.run_seed(), [])

    def test_the_id_of_an_error_is_read_from_its_parameters_only(self):
        error = common.HttpError("x", 400, json.dumps({"parameters": {"apiId": "a1"}}))
        self.assertEqual(apim.made_by_error(error), "a1")
        for body in ("", "{", "[]", "null", json.dumps({"parameters": None}), json.dumps({"apiId": "a1"})):
            with self.subTest(body=body):
                self.assertIsNone(apim.made_by_error(common.HttpError("x", 400, body)))


class Controlled(Playground):
    """The LLM proxy has controls, and the mode is off."""

    def setUp(self):
        super().setUp()
        self.seed_files({"apim/apis/llm.json": GUARDED, "controls.yaml": CONTROLS})
        self.env(SEED_PROTECTIONS="off")

    def steps(self):
        """{(phase, step): enabled} of the LLM proxy, as APIM holds them."""
        return {(phase, step["name"]): step.get("enabled", True)
                for flow in self.apim.apis["llm-id"]["flows"] for phase in ("request", "response") for step in flow[phase]}

    def switch(self, phase, name, enabled):
        """What someone does in the console to a step of the LLM proxy: save it, and deploy."""
        api = self.apim.apis["llm-id"]
        next(step for step in api["flows"][0][phase] if step["name"] == name)["enabled"] = enabled

    def deployed(self):
        return self.apim.apis["llm-id"]["deploymentState"] == "DEPLOYED"


class FirstRunControlled(Controlled):

    def test_the_api_is_imported_with_the_steps_of_its_controls_off_and_the_others_on(self):
        self.run_seed()
        self.assertEqual(self.steps(), UNPROTECTED)
        self.assertEqual(self.apim.deployed.count("llm-id"), 1, "one import, one deployment: nothing is put back after it")
        self.assertEqual(self.apim.apis["llm-id"]["flows"], self.apim.apis["llm-id"]["definition"]["api"]["flows"],
                         "what it holds is what was imported")
        self.assertNotIn("put back", self.output())

    def test_in_the_mode_on_every_step_is_enabled(self):
        self.env(SEED_PROTECTIONS="on")
        self.run_seed()
        self.assertEqual(self.steps(), PROTECTED)

    def test_without_the_setting_the_mode_is_on(self):
        self.env(SEED_PROTECTIONS=None)
        self.run_seed()
        self.assertEqual(self.steps(), PROTECTED)

    def test_the_file_is_imported_as_it_is_for_the_digest_and_the_mode_is_not_part_of_it(self):
        self.run_seed()
        digest = apim.digest_of(json.loads(common.render(GUARDED, "llm.json", json_escape=True)))
        self.assertEqual(self.record("ACME LLM")["digest"], digest)
        sent = next(c.json for c in self.platform.sent("POST", V2 + "/apis/_import/definition") if c.json["api"]["id"] == "llm-id")
        self.assertIs(sent["api"]["flows"][0]["request"][0]["enabled"], False, "the definition that is imported has the mode applied")
        self.assertNotEqual(apim.digest_of(sent), digest, "so the digest is that of the file, not of what was imported")

    def test_a_control_that_names_a_step_the_file_lacks_stops_the_seed_before_the_api_is_imported(self):
        self.seed_files({"controls.yaml": CONTROLS.replace("Prompt guard rails", "Prompt guard rail")})
        with self.assertRaisesRegex(common.SeedError, "^control guard: no request step named 'Prompt guard rail' in flow 'Prompt' of api 'ACME LLM'$"):
            self.run_seed()
        self.assertEqual([c for c in self.platform.sent("POST", V2 + "/apis/_import/definition") if c.json["api"]["id"] == "llm-id"], [])
        self.assertNotIn("llm-id", self.apim.apis)

    def test_the_apis_without_a_control_are_imported_as_they_are(self):
        self.run_seed()
        self.assertIsNot(self.apim.apis["rest-id"]["definition"], None)
        self.assertEqual(self.apim.apis["rest-id"]["definition"], json.loads(common.render(REST, "rest.json", json_escape=True)))

    def test_what_a_run_says_about_the_controls(self):
        self.run_seed()
        self.run_seed()
        self.assertIn("Controls of ACME LLM are off: guard, pii", self.output())


class ControlledConverged(Controlled):
    """Seeded in the mode off, with its settings."""

    def setUp(self):
        super().setUp()
        self.seed_files({"apim/settings.yaml": SETTINGS})
        self.run_seed()


class StartingTheLessonsOver(ControlledConverged):

    def test_a_second_run_asks_for_no_change(self):
        self.assertEqual(self.run_seed(), [])
        self.assertIn("API up to date: ACME LLM", self.output())

    def test_a_step_someone_switched_on_and_deployed_is_switched_off_again_in_place(self):
        self.switch("request", "Prompt guard rails", True)
        self.switch("request", "PII filtering", True)
        self.apim.apis["llm-id"]["deploymentState"] = "DEPLOYED"
        before = (dict(self.keys()), dict(self.apim.apis["llm-id"]["definition"]), self.record("ACME LLM")["digest"])
        self.assertEqual(self.run_seed(), IN_PLACE)
        self.assertEqual(self.steps(), UNPROTECTED)
        self.assertTrue(self.deployed())
        self.assertEqual((self.keys(), self.apim.apis["llm-id"]["definition"], self.record("ACME LLM")["digest"]), before,
                         "not replaced: its keys, its subscriptions and its digest are the same")
        self.assertIn("Controls of ACME LLM put back, off: guard, pii", self.output())
        self.assertEqual(self.run_seed(), [])

    def test_the_other_mode_switches_the_steps_in_place_and_the_digest_stays_the_files(self):
        digest = self.record("ACME LLM")["digest"]
        self.env(SEED_PROTECTIONS="on")
        self.assertEqual(self.run_seed(), IN_PLACE)
        self.assertEqual(self.steps(), PROTECTED)
        self.assertEqual(self.record("ACME LLM")["digest"], digest)
        self.assertEqual(self.run_seed(), [])
        self.env(SEED_PROTECTIONS="off")
        self.assertEqual(self.run_seed(), IN_PLACE)
        self.assertEqual(self.steps(), UNPROTECTED)
        self.assertEqual(self.apim.deployed.count("llm-id"), 3, "once for each of the three states, never for a run that changed nothing")

    def test_a_state_someone_saved_without_deploying_is_not_the_state_the_gateway_has(self):
        """Switched off again by hand after being deployed on: the saved steps are right and the gateway still runs them."""
        self.apim.apis["llm-id"]["deploymentState"] = "NEED_REDEPLOY"
        self.assertEqual(self.run_seed(), [("POST", V2 + "/apis/llm-id/deployments")])
        self.assertTrue(self.deployed())

    def test_a_step_someone_saved_on_without_deploying_is_off_again_and_deployed(self):
        self.switch("request", "Prompt guard rails", True)
        self.apim.apis["llm-id"]["deploymentState"] = "NEED_REDEPLOY"
        self.assertEqual(self.run_seed(), IN_PLACE)
        self.assertEqual(self.steps(), UNPROTECTED)

    def test_a_step_no_control_names_is_left_as_someone_made_it(self):
        self.switch("request", "Semantic cache", False)
        self.assertEqual(self.run_seed(), [])
        self.assertIs(self.steps()[("request", "Semantic cache")], False)

    def test_a_step_someone_renamed_in_the_console_is_a_proxy_imported_again_from_its_file(self):
        self.apim.apis["llm-id"]["flows"][0]["request"][0]["name"] = "Guard"
        changes = self.run_seed()
        self.assertIn(("DELETE", V2 + "/apis/llm-id"), changes)
        self.assertEqual(changes.count(("POST", V2 + "/apis/_import/definition")), 1)
        self.assertEqual(self.steps(), UNPROTECTED)
        self.assertIn("! ACME LLM: control guard: no request step named 'Prompt guard rails'", self.output())
        self.assertEqual(self.run_seed(), [])

    def test_a_flow_someone_deleted_is_a_proxy_imported_again_from_its_file(self):
        self.apim.apis["llm-id"]["flows"] = []
        changes = self.run_seed()
        self.assertIn(("DELETE", V2 + "/apis/llm-id"), changes)
        self.assertEqual(self.steps(), UNPROTECTED)

    def refuse_the_update(self, *answers):
        """A guard rail someone switched on, and a platform that answers the update of the entity with `answers` (a status,
        or an exception raised in place of an answer, each; the last one for the rest) before it takes updates again."""
        self.switch("request", "Prompt guard rails", True)
        self.platform.add({("PUT", V2 + "/apis/llm-id"): [answer if isinstance(answer, Exception) else (answer, {"message": "no"})
                                                          for answer in answers] + [self.apim.publish]})

    def updates(self):
        return len(self.platform.sent("PUT", V2 + "/apis/llm-id"))

    def api_untouched(self, digest, since):
        """The API was neither stopped, deleted, imported again nor deployed by the calls after the `since`th, and keeps its id
        and its digest: all the seed did was ask for the update of its entity."""
        self.assertEqual([(c.method, c.path) for c in self.platform.calls[since:] if c.method in ("POST", "DELETE")], [])
        self.assertEqual((self.record("ACME LLM")["id"], self.record("ACME LLM")["digest"]), ("llm-id", digest))
        self.assertIn("llm-id", self.apim.apis, "the API is still there")

    def test_a_platform_that_does_not_offer_the_update_of_the_entity_has_the_api_imported_again_from_its_file(self):
        self.refuse_the_update(405)
        changes = self.run_seed()
        self.assertIn(("DELETE", V2 + "/apis/llm-id"), changes)
        self.assertEqual(self.steps(), UNPROTECTED)
        self.assertIn("! ACME LLM: HTTP 405", self.output())
        self.assertIn("the platform does not update it in place", self.output())
        self.assertEqual(self.record("ACME LLM")["id"], "llm-id")

    def test_only_404_405_and_501_have_the_api_imported_again_and_without_a_second_try(self):
        self.assertEqual(apim.UNSUPPORTED, (404, 405, 501))
        for status in apim.UNSUPPORTED:
            with self.subTest(status=status):
                self.setUp()
                self.run_seed()
                self.refuse_the_update(status)
                before, slept = len(self.platform.calls), len(self.clock.sleeps)
                changes = self.run_seed()
                self.assertIn(("DELETE", V2 + "/apis/llm-id"), changes)
                self.assertEqual(self.steps(), UNPROTECTED)
                asked = [(c.method, c.path) for c in self.platform.calls[before:]]
                self.assertEqual(asked[:asked.index(("DELETE", V2 + "/apis/llm-id"))].count(("PUT", V2 + "/apis/llm-id")), 1,
                                 "a platform that does not offer it is not asked again")
                self.assertIn(f"! ACME LLM: HTTP {status}", self.output())
                self.assertNotIn(apim.RETRY_PAUSE, self.clock.sleeps[slept:], "no wait for a retry, only the one for the gateway")

    def test_any_other_refusal_of_a_4xx_stops_the_seed_with_the_message_of_the_platform_and_replaces_nothing(self):
        for status in (400, 401, 403, 409, 410, 412, 415, 422):
            with self.subTest(status=status):
                self.setUp()
                self.run_seed()
                self.refuse_the_update(status)
                before, digest = self.updates(), self.record("ACME LLM")["digest"]
                since, slept = len(self.platform.calls), len(self.clock.sleeps)
                with self.assertRaisesRegex(common.SeedError, r'^API ACME LLM: HTTP %d \{"message": "no"\}$' % status):
                    self.run_seed()
                self.assertEqual(self.updates() - before, 1, "a refusal is not asked again")
                self.assertEqual(self.clock.sleeps[slept:], [], "and not waited for")
                self.api_untouched(digest, since)
                self.assertNotIn("trying again", self.output())

    def test_a_platform_that_is_busy_for_a_moment_is_asked_again_and_the_api_is_not_replaced(self):
        for statuses in ((503,), (502, 503), (500, 504, 429), (429, 429, 429, 429)):
            with self.subTest(statuses=statuses):
                self.setUp()
                self.run_seed()
                self.refuse_the_update(*statuses)
                before, slept = self.updates(), len(self.clock.sleeps)
                changes = self.run_seed()
                self.assertEqual(changes, [("PUT", V2 + "/apis/llm-id")] * (len(statuses) + 1) + [("POST", V2 + "/apis/llm-id/deployments")])
                self.assertNotIn(("DELETE", V2 + "/apis/llm-id"), changes)
                self.assertEqual(self.updates() - before, len(statuses) + 1)
                self.assertEqual(self.clock.sleeps[slept:], [apim.RETRY_PAUSE] * len(statuses), "it waits between two tries")
                self.assertEqual(self.steps(), UNPROTECTED)
                self.assertEqual(self.record("ACME LLM")["id"], "llm-id")
                self.assertIn(f"! ACME LLM: HTTP {statuses[0]}, trying again", self.output())

    def test_a_management_api_that_does_not_answer_is_asked_again_and_the_api_is_not_replaced(self):
        """It reloads for a while after a change of its settings: the connection is refused or reset, or the answer is too slow."""
        for failures in ((requests.ConnectionError("refused"),), (requests.ReadTimeout("slow"), requests.ConnectionError("reset")),
                         (requests.ConnectTimeout("no route"), 503, requests.exceptions.ChunkedEncodingError("cut"))):
            with self.subTest(failures=[type(f).__name__ if isinstance(f, Exception) else f for f in failures]):
                self.setUp()
                self.run_seed()
                self.refuse_the_update(*failures)
                before, slept = self.updates(), len(self.clock.sleeps)
                changes = self.run_seed()
                self.assertEqual(changes, [("PUT", V2 + "/apis/llm-id")] * (len(failures) + 1) + [("POST", V2 + "/apis/llm-id/deployments")])
                self.assertEqual(self.updates() - before, len(failures) + 1)
                self.assertEqual(self.clock.sleeps[slept:], [apim.RETRY_PAUSE] * len(failures))
                self.assertEqual(self.steps(), UNPROTECTED)
                self.assertEqual(self.record("ACME LLM")["id"], "llm-id")
                first = failures[0]
                self.assertIn(f"! ACME LLM: no answer from the management API: {type(first).__name__}, trying again", self.output())
                self.assertNotIn(str(first), self.output(), "what the library says of the connection is not for a reader")

    def test_a_platform_that_stays_busy_stops_the_seed_after_a_few_tries_and_leaves_the_api_where_it_is(self):
        for status in (500, 502, 503, 504, 429):
            with self.subTest(status=status):
                self.setUp()
                self.run_seed()
                self.refuse_the_update(*[status] * (apim.RETRIES + 5))
                before, digest, since = self.updates(), self.record("ACME LLM")["digest"], len(self.platform.calls)
                with self.assertRaisesRegex(common.SeedError, f"^API ACME LLM: HTTP {status} .*\\({apim.RETRIES + 1} tries\\)$"):
                    self.run_seed()
                self.assertEqual(self.updates() - before, apim.RETRIES + 1)
                self.api_untouched(digest, since)

    def test_a_management_api_that_never_answers_stops_the_seed_with_what_went_wrong_and_leaves_the_api_where_it_is(self):
        for failure in (requests.ConnectionError("refused"), requests.ReadTimeout("slow")):
            with self.subTest(failure=type(failure).__name__):
                self.setUp()
                self.run_seed()
                self.refuse_the_update(*[failure] * (apim.RETRIES + 5))
                before, digest, since = self.updates(), self.record("ACME LLM")["digest"], len(self.platform.calls)
                with self.assertRaisesRegex(common.SeedError, f"^API ACME LLM: no answer from the management API: {type(failure).__name__} \\({apim.RETRIES + 1} tries\\)$"):
                    self.run_seed()
                self.assertEqual(self.updates() - before, apim.RETRIES + 1)
                self.api_untouched(digest, since)

    def reads_failing(self, *failures):
        """The entity of the LLM proxy as the seed reads it just before its update (the second read of the run: the first is the look at
        what exists), answered with `failures` in turn, one each, and then as it should be."""
        seen, queue = {"reads": 0}, list(failures)

        def read(call):
            seen["reads"] += 1
            if seen["reads"] >= 2 and queue:
                answer = queue.pop(0)
                return answer if isinstance(answer, Exception) else (answer, {"message": "busy"})
            return self.apim.get_api(call)
        self.switch("request", "Prompt guard rails", True)
        self.platform.add({("GET", V2 + "/apis/llm-id"): read})

    def test_a_management_api_that_reloads_between_two_apis_is_asked_again_for_the_entity_before_its_update(self):
        """The read of the entity just before the update is asked again like the update: a connection that fails there does not end the seed."""
        for failures in ((requests.ConnectionError("reset"),), (503, requests.ReadTimeout("slow")), (429, 502, 500)):
            with self.subTest(failures=[type(f).__name__ if isinstance(f, Exception) else f for f in failures]):
                self.setUp()
                self.run_seed()
                self.reads_failing(*failures)
                slept = len(self.clock.sleeps)
                changes = self.run_seed()
                self.assertEqual(changes, [("PUT", V2 + "/apis/llm-id"), ("POST", V2 + "/apis/llm-id/deployments")])
                self.assertEqual(self.clock.sleeps[slept:], [apim.RETRY_PAUSE] * len(failures))
                self.assertEqual(self.steps(), UNPROTECTED)
                self.assertIn("trying again", self.output())
                self.assertNotIn(("DELETE", V2 + "/apis/llm-id"), changes, "an API that was only busy is not replaced")

    def test_a_management_api_that_never_answers_the_read_stops_the_seed_and_changes_nothing(self):
        self.reads_failing(*[requests.ConnectionError("refused")] * (apim.RETRIES + 5))
        before = len(self.platform.calls)
        with self.assertRaisesRegex(common.SeedError, f"^API ACME LLM: no answer from the management API: ConnectionError \\({apim.RETRIES + 1} tries\\)$"):
            self.run_seed()
        self.assertEqual([(c.method, c.path) for c in self.platform.calls[before:] if c.method != "GET"], [])

    def test_a_refusal_of_the_read_is_a_refusal_and_is_not_asked_again(self):
        self.reads_failing(403)
        slept = len(self.clock.sleeps)
        with self.assertRaisesRegex(common.SeedError, r'^API ACME LLM: HTTP 403 \{"message": "busy"\}$'):
            self.run_seed()
        self.assertEqual(self.clock.sleeps[slept:], [])

    def test_a_refusal_after_a_busy_answer_is_still_a_refusal(self):
        self.refuse_the_update(503, requests.ConnectionError("reset"), 400)
        digest, before, since = self.record("ACME LLM")["digest"], self.updates(), len(self.platform.calls)
        with self.assertRaisesRegex(common.SeedError, r"^API ACME LLM: HTTP 400 "):
            self.run_seed()
        self.assertEqual(self.updates() - before, 3, "asked again twice, and not a fourth time")
        self.api_untouched(digest, since)

    def deployments_failing(self, *failures):
        """The deployment that follows the update of the entity, answered with `failures` in turn, one each, and then as it should be."""
        queue = list(failures)

        def deploy(call):
            if queue:
                answer = queue.pop(0)
                return answer if isinstance(answer, Exception) else (answer, {"message": "busy"})
            return self.apim.deploy(call)
        self.switch("request", "Prompt guard rails", True)
        self.platform.add({("POST", V2 + "/apis/llm-id/deployments"): deploy})

    def test_a_management_api_that_reloads_between_the_update_and_the_deployment_is_asked_again_for_the_deployment(self):
        """A deployment that cannot be reached right after the update is asked again: the saved API does not stay undeployed."""
        for failures in ((requests.ConnectionError("reset"),), (503, requests.ReadTimeout("slow")), (429, 502, 500)):
            with self.subTest(failures=[type(f).__name__ if isinstance(f, Exception) else f for f in failures]):
                self.setUp()
                self.run_seed()
                self.deployments_failing(*failures)
                slept = len(self.clock.sleeps)
                changes = self.run_seed()
                self.assertEqual(changes, [("PUT", V2 + "/apis/llm-id")] + [("POST", V2 + "/apis/llm-id/deployments")] * (len(failures) + 1))
                self.assertEqual(self.clock.sleeps[slept:], [apim.RETRY_PAUSE] * len(failures))
                self.assertEqual(self.apim.apis["llm-id"]["deploymentState"], "DEPLOYED")
                self.assertNotIn(("DELETE", V2 + "/apis/llm-id"), changes, "an API that was only busy is not replaced")

    def test_a_deployment_that_never_gets_an_answer_stops_the_seed_after_a_few_tries_and_a_refusal_at_once(self):
        self.setUp()
        self.run_seed()
        self.deployments_failing(*[requests.ConnectionError("refused")] * (apim.RETRIES + 5))
        with self.assertRaisesRegex(common.SeedError, f"^API ACME LLM: no answer from the management API: ConnectionError \\({apim.RETRIES + 1} tries\\)$"):
            self.run_seed()
        self.setUp()
        self.run_seed()
        self.deployments_failing(403)
        slept = len(self.clock.sleeps)
        with self.assertRaisesRegex(common.SeedError, r'^API ACME LLM: HTTP 403 \{"message": "busy"\}$'):
            self.run_seed()
        self.assertEqual(self.clock.sleeps[slept:], [])

    def test_a_busy_platform_that_answers_in_time_is_waited_for_a_little_over_half_a_minute_at_most(self):
        self.assertLessEqual(apim.RETRIES * apim.RETRY_PAUSE, 60)
        self.assertGreaterEqual(apim.RETRIES, 3, "a few times")

    def test_an_api_that_does_not_run_is_started_with_its_controls_put_back_and_deployed_once(self):
        self.switch("request", "Prompt guard rails", True)
        self.apim.apis["llm-id"]["state"] = "STOPPED"
        deployed = self.apim.deployed.count("llm-id")
        self.assertEqual(self.run_seed(), [("PUT", V2 + "/apis/llm-id"), ("POST", V2 + "/apis/llm-id/deployments"),
                                           ("POST", V2 + "/apis/llm-id/_start")])
        self.assertEqual(self.apim.deployed.count("llm-id"), deployed + 1)
        self.assertEqual(self.steps(), UNPROTECTED)

    def test_only_the_api_that_has_controls_is_read_a_second_time(self):
        before = len(self.platform.calls)
        self.run_seed()
        asked = [c.path for c in self.platform.calls[before:] if c.method == "GET"]
        self.assertEqual({api: asked.count(f"{V2}/apis/{api}") for api in ("llm-id", "a2a-id", "rest-id")},
                         {"llm-id": 2, "a2a-id": 1, "rest-id": 1})

    def test_a_changed_file_still_replaces_the_api_and_imports_it_with_the_mode_applied(self):
        self.env(TARGET="http://another:11434")
        changes = self.run_seed()
        self.assertIn(("DELETE", V2 + "/apis/llm-id"), changes)
        self.assertEqual(self.steps(), UNPROTECTED)
        self.assertEqual(self.run_seed(), [])

    def test_a_deployment_of_a_control_is_not_waited_for_unless_asked(self):
        self.switch("request", "Prompt guard rails", True)
        self.env(GATEWAY_SYNC_SECONDS="5", SEED_WAIT_GATEWAY="0")
        slept = self.clock.elapsed
        self.run_seed()
        controls.settle()
        self.assertAlmostEqual(self.clock.elapsed - slept, 0, delta=1)

    def test_a_deployment_of_a_control_is_waited_for_until_the_gateway_has_taken_it(self):
        self.switch("request", "Prompt guard rails", True)
        self.env(GATEWAY_SYNC_SECONDS="5")
        slept = self.clock.elapsed
        self.run_seed()
        controls.settle()
        self.assertAlmostEqual(self.clock.elapsed - slept, 11, delta=1)


class Replacement(Converged):
    """An API replaced under its id leaves the gateway before its new definition is imported: the gateway keeps serving
    the old one, for good, when it never sees the deletion."""

    def setUp(self):
        super().setUp()
        self.env(TARGET="http://another:11434")

    def gateway(self, llm):
        self.env(GATEWAY_INTERNAL_URL=GATEWAY, GATEWAY_READY_TIMEOUT="30")
        self.platform.add({("GET", GATEWAY + "/llm/"): llm, ("GET", GATEWAY + "/ace/"): 401, ("GET", GATEWAY + "/open/"): 401})

    def steps(self, before):
        """What happened to the LLM proxy since `before`, and what the gateway was asked about it, in order."""
        names = {("DELETE", V2 + "/apis/llm-id"): "delete", ("POST", V2 + "/apis/_import/definition"): "import"}
        return ["gateway" if call.url == GATEWAY + "/llm/" else names[(call.method, call.path)] for call in self.platform.calls[before:]
                if call.url == GATEWAY + "/llm/" or (call.method, call.path) in names]

    def test_the_new_definition_is_imported_once_the_gateway_has_dropped_the_old_one(self):
        self.gateway([401, 401, NO_API, 401])
        before = len(self.platform.calls)
        apim.seed()
        self.assertEqual(self.steps(before), ["delete", "gateway", "gateway", "gateway", "import", "gateway"])
        self.assertEqual(self.clock.sleeps, [apim.LOOK_AGAIN] * 2)
        self.assertIn("Waiting for the gateway to drop ACME LLM", self.output())

    def test_a_gateway_that_keeps_serving_the_old_api_ends_the_seed_before_the_import(self):
        self.gateway(401)
        before = len(self.platform.calls)
        with self.assertRaisesRegex(common.SeedError, "^API ACME LLM: the gateway still serves it after its deletion$"):
            apim.seed()
        self.assertNotIn("import", self.steps(before))
        self.assertTrue(30 <= self.clock.elapsed <= 30 + apim.LOOK_AGAIN, self.clock.elapsed)

    def test_the_next_run_imports_the_api_that_the_seed_could_not_replace(self):
        self.gateway(401)
        with self.assertRaises(common.SeedError):
            apim.seed()
        self.gateway([NO_API, 401])
        apim.seed()
        self.assertEqual(self.apim.apis["llm-id"]["state"], "STARTED")
        self.assertEqual(self.run_seed(), [])

    def test_without_a_gateway_to_look_at_the_seed_pauses_for_two_sync_periods_before_the_import(self):
        slept = []
        def imported(call):
            slept.append(list(self.clock.sleeps))
            return self.apim.import_api(call)
        self.platform.add({("POST", V2 + "/apis/_import/definition"): imported})
        apim.seed()
        self.assertEqual(slept, [[apim.SYNC_PAUSE]])
        self.assertEqual([call for call in self.platform.calls if call.url.startswith(GATEWAY)], [])


class Waiting(Playground):
    """The seed ends once the gateway serves what it imported."""

    def setUp(self):
        super().setUp()
        self.env(GATEWAY_INTERNAL_URL=GATEWAY, GATEWAY_READY_TIMEOUT="30")
        self.paths = {"/llm/": 401, "/ace/": 401, "/open/": 401}

    def gateway(self, **answers):
        """Every API answers 401 to a caller without a key, unless its path has another answer."""
        self.paths.update({"/" + name + "/": answer for name, answer in answers.items()})
        self.platform.add({("GET", GATEWAY + path): answer for path, answer in self.paths.items()})

    def chat(self, *answers):
        self.platform.add({("POST", GATEWAY + "/llm/chat/completions"): list(answers)})

    def chats(self):
        return self.platform.sent("POST", GATEWAY + "/llm/chat/completions")

    ANSWER = (200, {"id": "chatcmpl-1", "choices": [{"message": {"content": "An API is a contract between two programs."}}]})
    FROM_CACHE = (200, {"id": "resp_llmproxy", "choices": [{"message": {"content": "An API is a contract between two programs."}}]})

    def test_a_seed_without_the_gateway_setting_does_not_wait(self):
        self.env(GATEWAY_INTERNAL_URL=None, LLM_PROBE_KEY="dev-key-0001")
        apim.seed()
        self.assertEqual([call for call in self.platform.calls if call.url.startswith(GATEWAY)], [])
        self.assertEqual(self.clock.sleeps, [])

    def test_a_seed_that_is_not_asked_to_wait_for_the_gateway_does_not_look_at_it_and_does_not_pause(self):
        self.gateway(llm=[NO_API], ace=[NO_API])
        self.env(SEED_WAIT_GATEWAY="0", LLM_PROBE_KEY="dev-key-0001")
        apim.seed()
        common.run_later()
        self.assertEqual([call for call in self.platform.calls if call.url.startswith(GATEWAY)], [])
        self.assertEqual(self.clock.sleeps, [])

    def test_the_seed_waits_until_every_api_is_served(self):
        self.gateway(llm=[NO_API, NO_API, 401], ace=[NO_API, 401])
        apim.seed()
        self.assertEqual(self.clock.sleeps, [apim.LOOK_AGAIN] * 3)
        self.assertIn("✓ ACME LLM answers on the gateway", self.output())

    def test_a_404_that_is_not_the_gateways_own_is_an_answer(self):
        self.gateway(open=(404, {"detail": "Not Found"}))
        apim.seed()
        self.assertEqual(self.clock.sleeps, [])

    def test_a_gateway_that_is_not_there_yet_is_waited_for(self):
        self.gateway(llm=[requests.ConnectionError("refused"), requests.Timeout("slow"), 401])
        apim.seed()
        self.assertEqual(len(self.clock.sleeps), 2)

    def test_an_llm_proxy_that_never_answers_ends_the_seed_with_where_the_wait_comes_from(self):
        self.gateway(llm=NO_API)
        with self.assertRaisesRegex(common.SeedError, r"^ACME LLM does not answer on the gateway: its AI models are downloaded "
                                                      r"from huggingface.co at the first deployment$"):
            apim.seed()
        # the deadline is the real time plus the fake one: a slow machine ends it a look sooner, never a look later
        self.assertTrue(30 - apim.LOOK_AGAIN <= self.clock.elapsed <= 30 + apim.LOOK_AGAIN, self.clock.elapsed)

    def test_another_api_that_never_answers_says_no_more_than_that(self):
        self.gateway(open=NO_API)
        with self.assertRaisesRegex(common.SeedError, r"^Open API does not answer on the gateway$"):
            apim.seed()

    def test_one_deadline_covers_every_wait(self):
        self.gateway(ace=[NO_API] * 3 + [401], llm=[NO_API] * 3 + [401], open=NO_API)
        with self.assertRaises(common.SeedError):
            apim.seed()
        self.assertTrue(30 <= self.clock.elapsed <= 30 + apim.LOOK_AGAIN, self.clock.elapsed)

    def test_the_wait_lasts_five_minutes_unless_the_setting_says_otherwise(self):
        self.env(GATEWAY_READY_TIMEOUT=None)
        self.gateway(llm=NO_API)
        with self.assertRaises(common.SeedError):
            apim.seed()
        self.assertTrue(300 <= self.clock.elapsed <= 300 + apim.LOOK_AGAIN)

    def test_a_seed_that_waits_in_vain_does_not_say_the_playground_is_ready(self):
        self.gateway(open=NO_API)
        self.assertEqual(self.main(), 1)
        self.assertIn("[seed] ✗ Open API does not answer on the gateway", self.output())
        self.assertNotIn("Playground ready", self.output())

    def test_the_probe_key_chats_and_the_same_question_again_comes_from_the_cache(self):
        self.env(LLM_PROBE_KEY="dev-key-0001")
        self.gateway()
        self.chat(self.ANSWER, self.FROM_CACHE)
        apim.seed()
        first, second = self.chats()
        self.assertEqual(first.headers["Authorization"], "Bearer dev-key-0001")
        self.assertEqual(first.json["model"], "chat")
        self.assertIsNone(re.search(r"\d", first.json["messages"][0]["content"]), "a question with a digit is never cached")
        self.assertEqual(first.json, second.json)
        self.assertIn("answers a question asked twice from its cache", self.output())
        self.assertNotIn("dev-key-0001", self.output())

    def test_nothing_chats_through_an_api_that_is_not_an_llm_proxy(self):
        self.env(LLM_PROBE_KEY="dev-key-0001")
        self.gateway()
        self.chat(self.ANSWER, self.FROM_CACHE)
        apim.seed()
        self.assertEqual([call.path for call in self.platform.calls if call.method == "POST" and call.url.startswith(GATEWAY)],
                         ["/llm/chat/completions"] * 2)

    def test_without_the_probe_key_nothing_chats(self):
        self.gateway()
        apim.seed()
        self.assertEqual(self.chats(), [])

    def test_a_chat_that_is_late_is_waited_for(self):
        self.env(LLM_PROBE_KEY="dev-key-0001")
        self.gateway()
        self.chat((401, {"message": "Invalid ApiKey"}), requests.ConnectionError("reset"), (503, "starting"), self.ANSWER, self.FROM_CACHE)
        apim.seed()
        self.assertEqual(len(self.chats()), 5)

    def test_a_key_the_gateway_refuses_ends_the_seed_with_what_it_answered(self):
        self.env(LLM_PROBE_KEY="dev-key-0001")
        self.gateway()
        self.chat((401, {"message": "Invalid ApiKey"}))
        with self.assertRaisesRegex(common.SeedError, "ACME LLM does not answer a chat with LLM_PROBE_KEY: HTTP 401 .*Invalid ApiKey"):
            apim.seed()
        # the deadline is the real time plus the fake one: a slow machine ends it a look sooner, never a look later
        self.assertTrue(30 - apim.LOOK_AGAIN <= self.clock.elapsed <= 30 + apim.LOOK_AGAIN, self.clock.elapsed)

    def test_a_cache_that_never_answers_ends_the_seed_with_what_to_check(self):
        self.env(LLM_PROBE_KEY="dev-key-0001")
        self.gateway()
        self.chat(self.ANSWER)
        with self.assertRaisesRegex(common.SeedError, "from its cache: check the vector store of its semantic cache"):
            apim.seed()
        self.assertEqual(len(self.chats()), 1 + apim.CACHE_TRIES)

    def test_an_answer_that_is_not_json_is_not_from_the_cache(self):
        self.env(LLM_PROBE_KEY="dev-key-0001")
        self.gateway()
        self.chat(self.ANSWER, (200, "plain text"), (200, "plain text"), self.FROM_CACHE)
        apim.seed()
        self.assertEqual(len(self.chats()), 4)


class Removal(Playground):

    def test_a_removal_closes_the_subscriptions_then_deletes_the_applications_then_the_apis(self):
        self.run_seed()
        calls = len(self.platform.calls)
        self.assertEqual(self.main("--remove"), 0, self.output())
        kinds = ["subscription" if c.path.endswith("/_close") else "application" if "/applications/" in c.path else "api"
                 for c in self.platform.calls[calls:] if c.method != "GET"]
        self.assertEqual(kinds, sorted(kinds, key=["subscription", "application", "api"].index))
        self.assertEqual((self.apim.apis, common.created()), ({}, []))

    def test_what_another_module_made_is_none_of_its_business(self):
        apim.remove([{"product": "aim-catalog", "kind": "provider", "name": "p", "id": "p1", "label": "AI providers"},
                     {"product": "apim", "kind": "something-else", "name": "x", "id": "x1", "label": ""}])
        self.assertEqual(self.platform.calls, [])


class AcmeBakery(Seed):
    """The files of Acme Bakery, as the seed reads them."""

    def setUp(self):
        super().setUp()
        self.assertTrue(ACME_BAKERY.is_dir(), "Acme Bakery has no seed/apim")
        self.env(**VARIABLES)

    def texts(self, *parts):
        return {path.name: path.read_text(encoding="utf-8") for path in sorted(ACME_BAKERY.joinpath(*parts).glob("*")) if path.is_file()}

    def loaded(self, *parts):
        return {name: common.load(ACME_BAKERY.joinpath(*parts, name)) for name in self.texts(*parts)}

    def apis(self):
        return self.loaded("apis")

    def plans(self, definition):
        return {plan["name"]: plan for plan in definition["plans"]}

    def test_the_files_use_no_variable_the_playground_does_not_document(self):
        texts = [text for parts in ((), ("apis",), ("applications",), ("subscriptions",)) for text in self.texts(*parts).values()]
        used = {m.group(2) for text in texts for m in common.VARIABLE.finditer(text) if not m.group(1)}
        self.assertEqual(used - set(VARIABLES), set())
        self.assertIn("ACE_LLM_API_KEY", used)

    def test_every_file_renders_and_parses(self):
        for parts in (("apis",), ("applications",), ("subscriptions",)):
            for name, loaded in self.loaded(*parts).items():
                with self.subTest(file=name):
                    self.assertTrue(loaded)
        self.assertEqual(common.load(ACME_BAKERY / "settings.yaml")["plan"]["security"]["customApiKey"], {"enabled": True})

    def test_every_api_and_plan_has_its_id_and_every_plan_a_validation(self):
        ids = []
        for name, file in self.apis().items():
            with self.subTest(file=name):
                self.assertIn("id", file["api"])
                self.assertTrue(file["plans"])
                for plan in file["plans"]:
                    self.assertIn("id", plan)
                    self.assertIn(plan.get("validation"), ("AUTO", "MANUAL"), "a plan with none makes every later read of its API fail")
                ids += [file["api"]["id"]] + [plan["id"] for plan in file["plans"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(self.apis()["acme-bakery-llm.json"]["api"]["id"], "00000000-0000-4000-8000-acb100000001")

    def test_no_file_of_apis_is_anything_but_an_api_export(self):
        for name, file in self.apis().items():
            with self.subTest(file=name):
                self.assertIsInstance(file["api"], dict)
                self.assertIn("name", file["api"])
                self.assertTrue(apim.ApiManagement.first_path(file["api"]))

    def test_the_llm_proxy_has_its_three_plans_and_their_limits(self):
        llm = self.apis()["acme-bakery-llm.json"]
        self.assertEqual((llm["api"]["type"], apim.ApiManagement.first_path(llm["api"])), ("LLM_PROXY", "/acme-bakery/llm/"))
        limits = {}
        for name, plan in self.plans(llm).items():
            self.assertEqual((plan["security"]["type"], plan["security"]["configuration"]["source"]), ("API_KEY", "BEARER"))
            rate = plan["flows"][0]["request"][0]["configuration"]["rate"]
            limits[name] = (rate["limit"], rate["periodTimeUnit"], rate["key"])
        key = "{#context.attributes['application']}"
        self.assertEqual(limits, {"Ace agent": (1000000, "HOURS", key), "Developers": (3000, "MINUTES", key), "Traffic": (3000, "MINUTES", key)})

    def test_the_llm_proxy_has_its_models_prices_and_a_timeout_that_outlasts_a_model(self):
        group = self.apis()["acme-bakery-llm.json"]["api"]["endpointGroups"][0]
        configuration = group["endpoints"][0]["configuration"]
        models = {m["name"]: (m["aliases"], m["inputPrice"], m["outputPrice"], json.loads(m["parametersOverride"])) for m in configuration["models"]}
        self.assertEqual(models, {"qwen3:1.7b": (["acme-chat"], 50.0, 50.0, {"reasoning_effort": "none"}),
                                  "qwen3:0.6b": (["acme-lite"], 20.0, 20.0, {"reasoning_effort": "none"})})
        self.assertEqual(configuration["target"], "http://ollama:11434/v1")
        self.assertEqual(configuration["modelGovernance"]["prefixPolicy"], {"policy": "NO_PREFIX"})
        self.assertEqual(json.loads(group["sharedConfiguration"])["http"]["readTimeout"], 60000)

    def test_the_llm_proxy_guards_masks_and_caches_in_one_flow_for_the_plans_that_cache(self):
        llm = self.apis()["acme-bakery-llm.json"]
        flow = llm["api"]["flows"][0]
        request = {}
        for policy in flow["request"]:
            request.setdefault(policy["policy"], []).append(policy)
        self.assertEqual([p["configuration"]["sensitivityThreshold"] for p in request["ai-prompt-guard-rails"]], [0.7, 0.9])
        self.assertEqual({tuple(p["configuration"]["categories"]): p["configuration"]["threshold"] for p in request["pii-filtering"]},
                         {("FINANCIAL_ACCOUNT",): 0.85, ("EMAIL", "PHONE", "GOVERNMENT_ID"): 0.5})
        self.assertTrue(all("condition" not in p for p in request["pii-filtering"]), "a condition switches the filter off from the second call on")
        plans = self.plans(llm)
        wanted = [plans["Developers"]["id"], plans["Traffic"]["id"]]
        for policy in request["policy-assign-attributes"] + request["ai-semantic-caching"] + flow["response"][1:]:
            self.assertEqual(re.findall(r"[0-9a-f-]{36}", policy["condition"]), wanted)
        self.assertNotIn(plans["Ace agent"]["id"], json.dumps(flow))
        self.assertNotIn("$.messages", json.dumps(llm), "the policies read the canonical request, not the client's")

    def test_the_llm_proxy_keeps_its_cache_in_the_lab_redis_and_logs_payloads(self):
        api = self.apis()["acme-bakery-llm.json"]["api"]
        redis = json.loads(next(r for r in api["resources"] if r["type"] == "ai-vector-store-redis")["configuration"])["redisConfig"]
        self.assertEqual((redis["url"], redis["password"], redis["index"]), ("redis://redis:6379", "redis-secret", "acme_bakery_llm_cache"))
        self.assertTrue(api["analytics"]["logging"]["content"]["payload"])

    def test_the_llm_proxy_and_ace_are_traced_in_detail_with_their_payloads(self):
        """Gamma's Tracing pages show every call through them, with what went in and came out."""
        for name in ("acme-bakery-llm.json", "ace-a2a.json"):
            with self.subTest(file=name):
                analytics = self.apis()[name]["api"]["analytics"]
                self.assertEqual((analytics["enabled"], analytics["tracing"], analytics["otelLogs"]),
                                 (True, {"enabled": True, "verbose": True}, {"enabled": True}))
                self.assertTrue(analytics["logging"]["content"]["payload"])

    def test_the_logs_keep_the_payloads_of_the_llm_proxy_and_ace_and_not_their_headers(self):
        """A header is the bearer token of a person or the API key of a team: Gamma's Logs, which Elasticsearch keeps until the
        lab is reset, must not hold it. What a lesson reads in them is a request and a response body."""
        for name in ("acme-bakery-llm.json", "ace-a2a.json"):
            with self.subTest(file=name):
                content = self.apis()[name]["api"]["analytics"]["logging"]["content"]
                self.assertEqual((content["headers"], content["payload"]), (False, True))
                self.assertEqual((content["messageHeaders"], content["messagePayload"], content["messageMetadata"]), (False,) * 3)

    def test_the_a2a_proxy_asks_access_management_about_every_token(self):
        ace = self.apis()["ace-a2a.json"]
        resource = ace["api"]["resources"][0]
        self.assertEqual((ace["api"]["type"], apim.ApiManagement.first_path(ace["api"])), ("A2A_PROXY", "/acme-bakery/ace/"))
        self.assertEqual(ace["api"]["endpointGroups"][0]["endpoints"][0]["configuration"]["target"], "http://bakery-agent:8000")
        self.assertEqual(json.loads(ace["api"]["endpointGroups"][0]["sharedConfiguration"])["http"]["readTimeout"], 60000)
        self.assertEqual((resource["configuration"]["authorizationServerUrl"], resource["configuration"]["introspectionEndpoint"]),
                         ("http://am-gateway:8092/acme-bakery", "/oauth/introspect"))
        self.assertEqual((resource["configuration"]["clientId"], resource["configuration"]["clientSecret"]), ("acme-bakery-mcp", "mcp-secret"))
        plan = self.plans(ace)["Bank users"]
        self.assertEqual(plan["security"]["type"], "OAUTH2")
        self.assertEqual(plan["security"]["configuration"]["oauthResource"], resource["name"])
        self.assertTrue(plan["security"]["configuration"]["extractPayload"], "without it every claim of the token is null")

    def test_the_open_api_is_a_keyless_proxy_with_the_openapi_of_the_bakery_api_as_its_page(self):
        self.assertIn("acme-bakery-open-api.json", self.texts("apis"))
        open_api = self.apis()["acme-bakery-open-api.json"]
        self.assertEqual((open_api["api"]["type"], apim.ApiManagement.first_path(open_api["api"])), ("PROXY", "/acme-bakery/open/"))
        self.assertEqual(open_api["api"]["endpointGroups"][0]["endpoints"][0]["configuration"]["target"], "http://bakery-api:8000")
        self.assertEqual(self.plans(open_api)["Open"]["security"]["type"], "KEY_LESS")
        (page,) = open_api["pages"]
        self.assertEqual((page["type"], page["published"], page["visibility"]), ("SWAGGER", True, "PUBLIC"))
        spec = json.loads(page["content"])
        operations = sorted(op["operationId"] for path in spec["paths"].values() for op in path.values())
        self.assertEqual(operations, ["getBranch", "getRate", "listBranches", "listRates"])

    def test_the_page_of_the_open_api_is_the_openapi_document_of_the_bakery_api(self):
        bakery_api = PLAYGROUNDS / "acme-bakery" / "apps" / "bakery-api" / "openapi.json"
        self.assertIn("acme-bakery-open-api.json", self.texts("apis"))
        self.assertTrue(bakery_api.is_file(), "the bank API serves an OpenAPI document")
        (page,) = self.apis()["acme-bakery-open-api.json"]["pages"]
        self.assertEqual(json.loads(page["content"]), json.loads(bakery_api.read_text(encoding="utf-8")), "the page is a copy of the document the bank API serves")

    def test_the_applications_and_the_subscriptions_agree_with_the_apis(self):
        apis = {file["api"]["name"]: self.plans(file) for file in self.apis().values()}
        applications = {app["name"]: app for app in self.loaded("applications").values()}
        self.assertEqual({name: app["settings"]["app"].get("client_id") for name, app in applications.items()},
                         {"Acme Bakery web": "acme-bakery-web", "Ace": "ace-agent", "Acme Bakery developers": None, "Acme Bakery traffic": "acme-bakery-traffic"})
        wanted = {("Acme Bakery web", "Ace", "Bank users"): None, ("Acme Bakery traffic", "Ace", "Bank users"): None,
                  ("Ace", "Acme Bakery LLM", "Ace agent"): "ace-key-0001",
                  ("Acme Bakery developers", "Acme Bakery LLM", "Developers"): "dev-key-0001",
                  ("Acme Bakery traffic", "Acme Bakery LLM", "Traffic"): "traffic-key-0001"}
        found = {}
        for file in self.loaded("subscriptions").values():
            for spec in file["subscriptions"]:
                self.assertIn(spec["application"], applications)
                self.assertIn(spec["plan"], apis[spec["api"]])
                found[(spec["application"], spec["api"], spec["plan"])] = spec.get("apiKey")
        self.assertEqual(found, wanted)

    def test_the_files_seed_and_a_second_run_changes_nothing(self):
        shutil.copytree(ACME_BAKERY, common.SEED_DIR / "apim")
        world = Apim()
        self.platform.add(world.routes())
        apim.seed()
        self.assertEqual({a["name"] for a in world.apis.values()} - {"Acme Bakery Open API"}, {"Acme Bakery LLM", "Ace"})
        self.assertEqual(sorted(s["customApiKey"] for s in world.subscriptions.values() if "customApiKey" in s),
                         ["ace-key-0001", "dev-key-0001", "traffic-key-0001"])
        self.assertTrue(all(a["state"] == "STARTED" for a in world.apis.values()))
        before = len(self.platform.calls)
        apim.seed()
        self.assertEqual([c for c in self.platform.calls[before:] if c.method != "GET"], [])


# --- Many environments ------------------------------------------------------------------------
# What follows is the seed of seed/environments/<id>/ against a platform that has several. The classes above are the proof
# that seed/apim/ is what it was.

ORG = "/management/organizations/DEFAULT"
ENV = ORG + "/environments/[^/]+"
ENV_V2 = "/management/v2/organizations/DEFAULT/environments/[^/]+"
AUTOMATION = "/automation/organizations/DEFAULT/environments/[^/]+"
PORTAL = "/portal/environments/[^/]+"


class Estate:
    """API Management with several environments, as far as the seed uses it: users and their identity providers, groups, APIs,
    applications and subscriptions of each environment. It keeps what it is given and answers from it, the way the platform
    was seen to answer a seed (each comment that says "the platform" is a behavior found on a running 4.13 platform)."""

    ROLES = ("USER", "OWNER", "ADMIN")

    def __init__(self, environments=("DEFAULT", "novo", "moderna"), directory=None):
        self.ids = itertools.count(1)
        self.environments, self.unlisted = set(environments), set()   # unlisted: where the login of the seed has no role, so the platform does not list it
        self.directory = dict(directory or {})   # sourceId -> displayName of the memory users of the management API's configuration
        self.users, self.groups, self.apis, self.applications, self.subscriptions, self.memberships = {}, {}, {}, {}, {}, {}
        self.settings = {}
        self.deployed, self.groups_by_import = [], []

    def new(self, kind):
        return f"{kind}-{next(self.ids)}"

    @staticmethod
    def after(call, marker):
        return call.path.split(marker)[1].split("/")[0]

    def env(self, call):
        return self.after(call, "/environments/")

    def api_id(self, call):
        return self.after(call, "/apis/")

    # --- The environment and who acts in it ------------------------------------------------------

    def environment(self, call):
        env = self.env(call)
        return (200, {"id": env}) if env in self.environments else (400, {"message": "Invalid organization or environment"})

    def listed(self, call):
        return 200, [{"id": env} for env in sorted(self.environments - self.unlisted)]

    def list_apis(self, call):
        env = self.env(call)
        rows = [self.row(api) for api in self.apis.values() if api["env"] == env]
        return 200, {"data": rows, "pagination": {"pageCount": 1}}

    @staticmethod
    def row(api):
        return {key: api[key] for key in ("id", "name", "type", "listeners", "state", "lifecycleState", "groups", "crossId")}

    def roles_of(self, call):
        user, env = self.after(call, "/users/"), call.json["referenceId"]
        self.memberships[(user, env)] = call.json["roles"]
        (self.unlisted.discard if call.json["roles"] else self.unlisted.add)(env)
        return 200

    # --- Users -----------------------------------------------------------------------------------

    def row_of_user(self, user):
        return {key: user[key] for key in ("id", "source", "sourceId", "status")}

    def find_users(self, call):
        found = [self.row_of_user(u) for u in self.users.values() if call.params["q"] in u["sourceId"]]   # an archived one too
        return 200, {"data": found, "page": {"current": 1, "size": len(found), "per_page": int(call.params["size"]), "total_pages": 1}}

    def register(self, call):
        if call.json["source"] != "gravitee":
            # The platform registers a user of the providers it holds in its database, and a memory user is not one
            return 404, {"message": "Identity provider [memory] cannot be found.", "technicalCode": "identityProvider.notFound"}
        user = {"id": self.new("user"), "status": "ACTIVE", "env": self.env(call), **call.json}
        self.users[user["id"]] = user
        return 200, user

    def search(self, call):
        q = call.params["q"].lower()
        return 200, [{"reference": f"ref:{source_id}", "displayName": name} for source_id, name in self.directory.items()
                     if q in source_id.lower() or q in name.lower()]

    def delete_user(self, call):
        user = self.users[self.after(call, "/users/")]
        user.update(status="ARCHIVED", sourceId=f"deleted-{user['id']}-{user['sourceId']}")   # the row stays, anonymized
        return 204

    def user(self, source, source_id):
        return next((u for u in self.users.values() if (u["source"], u["sourceId"], u["status"]) == (source, source_id, "ACTIVE")), None)

    # --- Groups ----------------------------------------------------------------------------------

    def group_named(self, env, name):
        return next((g for g in self.groups.values() if (g["env"], g["name"]) == (env, name)), None)

    def state_of(self, group):
        members = [{**{k: m[k] for k in ("source", "sourceId")}, "roles": {**m["roles"], "INTEGRATION": "USER"}} for m in group["members"]]
        return {"hrid": group["hrid"], "name": group["name"], "members": members, "notifyMembers": group["notifyMembers"],
                "id": group["id"], "environmentId": group["env"], "organizationId": "DEFAULT", "memberCount": len(members)}

    def get_group(self, call):
        group = next((g for g in self.groups.values() if (g["env"], g["hrid"]) == (self.env(call), call.path.rsplit("/", 1)[1])), None)
        return (200, self.state_of(group)) if group else (404, {"message": "HRID cannot be found."})

    def put_group(self, call):
        spec, env = call.json, self.env(call)
        group = next((g for g in self.groups.values() if (g["env"], g["hrid"]) == (env, spec["hrid"])), None)
        if not group:
            group = {"id": self.new("group"), "env": env, "hrid": spec["hrid"], "roles": {}, "lock_api_role": False, "members": []}
            self.groups[group["id"]] = group
        group.update(name=spec["name"], notifyMembers=spec.get("notifyMembers", True), origin="KUBERNETES", members=[])
        warnings = []
        for member in spec.get("members", []):
            if not self.user(member["source"], member["sourceId"]):   # the platform ignores a member it does not know, and says so
                warnings.append(f"member [{member['sourceId']}] of source [{member['source']}] could not be found in organization [DEFAULT]")
                continue
            roles = {}
            for scope, role in member.get("roles", {}).items():
                if role not in self.ROLES:
                    warnings.append(f"member role [{role}] doesn't exist")
                roles[scope] = role if role in self.ROLES else "USER"
            group["members"].append({"source": member["source"], "sourceId": member["sourceId"], "roles": roles})
        return 200, {**spec, **self.state_of(group), "members": spec.get("members", []), "errors": {"severe": [], "warning": warnings}}

    def entity_of(self, group):
        entity = {"id": group["id"], "hrid": group["hrid"], "name": group["name"], "roles": dict(group["roles"]),
                  "environmentId": group["env"], "max_invitation": 2147483647, "lock_api_role": group["lock_api_role"],
                  "lock_application_role": False, "system_invitation": False, "email_invitation": False,
                  "disable_membership_notifications": True}
        if group.get("origin"):
            entity["origin"] = group["origin"]
        return entity

    def get_entity(self, call):
        return 200, self.entity_of(self.groups[call.path.rsplit("/", 1)[1]])

    def update_group(self, call):
        group = self.groups[call.path.rsplit("/", 1)[1]]
        group.update(name=call.json["name"], roles=call.json.get("roles") or {}, lock_api_role=call.json.get("lock_api_role", False), origin=None)
        return 200, self.entity_of(group)   # the update drops the origin the Automation API gave

    def delete_group(self, call):
        return 204 if self.groups.pop(call.path.rsplit("/", 1)[1], None) else 404

    def add_members(self, call):
        group = self.groups[self.after(call, "/groups/")]
        for item in call.json:
            for role in item["roles"]:
                if role["name"] not in self.ROLES:
                    return 404, {"message": f"Role [{role['name']}] for scope [{role['scope']}] cannot be found."}
            source_id = item["reference"].split(":", 1)[1]   # the reference is the platform's own: only a search gives one
            if not self.user("memory", source_id):
                user = {"id": self.new("user"), "source": "memory", "sourceId": source_id, "status": "ACTIVE", "env": self.env(call)}
                self.users[user["id"]] = user
            group["members"].append({"source": "memory", "sourceId": source_id, "roles": {r["scope"]: r["name"] for r in item["roles"]}})
        return 200

    # --- APIs ------------------------------------------------------------------------------------

    def groups_named(self, env, names):
        """An import knows the groups of an API by name only: a name it finds is that group, any other makes a group of that name."""
        ids = []
        for name in names or []:
            group = self.group_named(env, name)
            if not group:
                group = {"id": self.new("group"), "env": env, "hrid": None, "name": name, "roles": {}, "lock_api_role": False, "members": []}
                self.groups[group["id"]] = group
                self.groups_by_import.append(name)
            ids.append(group["id"])
        return ids

    def import_api(self, call):
        env, api = self.env(call), call.json["api"]
        api_id = api.get("id") or self.new("api")
        if any(other["env"] == env and Apim.path_of(other) == Apim.path_of(api) for other in self.apis.values()):
            return 400, {"technicalCode": "path.invalid"}
        plans = [{"id": plan.get("id") or self.new("plan"), **{k: plan.get(k) for k in ("name", "mode", "validation", "commentRequired")}}
                 for plan in call.json.get("plans", [])]
        self.apis[api_id] = {"id": api_id, "env": env, "name": api["name"], "type": api.get("type", "MESSAGE"), "crossId": api.get("crossId"),
                             "listeners": api["listeners"], "state": "STOPPED", "lifecycleState": "CREATED", "plans": plans,
                             "groups": self.groups_named(env, api.get("groups")), "definition": call.json, "flows": [],
                             "deploymentState": "NEED_REDEPLOY"}
        return 201, {"id": api_id}

    def update_api(self, call):
        api = self.apis[self.api_id(call)]
        api.update(definition=call.json, groups=[], deploymentState="NEED_REDEPLOY")   # an update by import clears the groups
        return 200, {}

    def get_api(self, call):
        api = self.apis.get(self.api_id(call))
        return (200, {**self.row(api), "flows": [], "deploymentState": api["deploymentState"]}) if api else (404, {"message": "not found"})

    def put_api(self, call):
        api = self.apis[self.api_id(call)]
        api["lifecycleState"], api["groups"] = call.json["lifecycleState"], call.json.get("groups", api["groups"])
        return 200, {}

    def deploy(self, call):
        self.deployed.append(self.api_id(call))
        self.apis[self.api_id(call)]["deploymentState"] = "DEPLOYED"
        return 202

    def start(self, call):
        self.apis[self.api_id(call)]["state"] = "STARTED"
        return 204

    def stop(self, call):
        api = self.apis[self.api_id(call)]
        was, api["state"] = api["state"] == "STOPPED", "STOPPED"
        return 400 if was else 204

    def delete_api(self, call):
        api_id = self.api_id(call)
        self.subscriptions = {i: s for i, s in self.subscriptions.items() if s["apiId"] != api_id}
        return 204 if self.apis.pop(api_id, None) else 404

    def plans(self, call):
        return 200, {"data": [dict(plan) for plan in self.apis[self.api_id(call)]["plans"]]}

    # --- Applications ----------------------------------------------------------------------------

    def find_applications(self, call):
        return 200, searched([a for a in self.applications.values() if a["env"] == self.env(call)], call.params)

    def create_application(self, call):
        application = {"id": self.new("application"), "env": self.env(call), "groups": [], **call.json}
        self.applications[application["id"]] = application
        return 201, application

    def update_application(self, call):
        self.applications[call.path.rsplit("/", 1)[1]].update(call.json)
        return 200, {}

    def delete_application(self, call):
        self.applications.pop(call.path.rsplit("/", 1)[1], None)
        return 204

    # --- Subscriptions ---------------------------------------------------------------------------

    @staticmethod
    def view(sub):
        return {k: v for k, v in sub.items() if k not in ("apiId", "key")}

    def find_subscriptions(self, call):
        statuses = call.params.get("statuses", "ACCEPTED")
        # The platform reads the first of a repeated parameter and all of a comma-separated one
        statuses = statuses[:1] if isinstance(statuses, list) else statuses.split(",")
        found = [self.view(s) for s in self.subscriptions.values() if s["apiId"] == self.api_id(call)
                 and s["plan"]["id"] == call.params["planIds"] and s["application"]["id"] == call.params.get("applicationIds", s["application"]["id"])
                 and s["status"] in statuses]
        return 200, {"data": found}

    def subscription_of(self, call):
        return 200, self.view(self.subscriptions[call.path.rsplit("/", 1)[1]])

    def webhook_refusal(self, configuration):
        token = ((configuration or {}).get("entrypointConfiguration") or {}).get("auth", {}).get("token")
        if token is not None and not isinstance(token, dict):
            return 400, {"message": "$.auth.token: string found, object expected", "technicalCode": "data.invalid"}

    def add_subscription(self, api_id, application, plan, status, **fields):
        sub = {"id": self.new("subscription"), "apiId": api_id, "application": {"id": application}, "plan": {"id": plan},
               "status": status, "metadata": {}, "startingAt": "2026-10-16T10:00:00Z", **fields}
        self.subscriptions[sub["id"]] = sub
        return sub

    def plan_of(self, plan_id):
        return next(p for api in self.apis.values() for p in api["plans"] if p["id"] == plan_id)

    def subscribe(self, call):
        """The management API accepts at once what it creates, even on a plan that validates by hand."""
        refused = self.webhook_refusal(call.json.get("consumerConfiguration"))
        if refused:
            return refused
        fields = {"consumerConfiguration": call.json["consumerConfiguration"]} if "consumerConfiguration" in call.json else {}
        sub = self.add_subscription(self.api_id(call), call.json["applicationId"], call.json["planId"], "ACCEPTED", **fields)
        if call.json.get("customApiKey"):
            sub["key"] = call.json["customApiKey"]
        return 201, self.view(sub)

    def subscribe_from_portal(self, call):
        """The Portal API leaves a subscription to a plan that validates by hand for someone to accept, with the request."""
        body = call.json
        plan = self.plan_of(body["plan"])
        if plan["commentRequired"] and not body.get("request"):
            return 400, {"message": "A comment is required for this plan"}
        refused = self.webhook_refusal(body.get("configuration"))
        if refused:
            return refused
        api_id = next(a["id"] for a in self.apis.values() if plan in a["plans"])
        fields = {"consumerMessage": body["request"]} if body.get("request") else {}
        if body.get("configuration"):
            fields["consumerConfiguration"] = body["configuration"]
        status = "PENDING" if plan["validation"] == "MANUAL" else "ACCEPTED"
        return 200, self.view(self.add_subscription(api_id, body["application"], body["plan"], status, **fields))

    def accept(self, call):
        sub = self.subscriptions[call.path.split("/")[-2]]
        sub["status"] = "ACCEPTED"
        if call.json.get("customApiKey"):
            sub["key"] = call.json["customApiKey"]
        return 200, self.view(sub)

    def update_subscription(self, call):
        sub = self.subscriptions[call.path.rsplit("/", 1)[1]]
        for field in ("consumerConfiguration", "metadata", "startingAt", "endingAt"):
            if field in call.json:
                sub[field] = call.json[field]
            elif field in ("startingAt", "endingAt"):
                sub.pop(field, None)   # what the update is not given is cleared
        return 200, self.view(sub)

    def close(self, call):
        self.subscriptions[call.path.split("/")[-2]]["status"] = "CLOSED"
        return 200

    def routes(self):
        api, sub = ENV_V2 + "/apis/[^/]+", ENV_V2 + "/apis/[^/]+/subscriptions"
        group = ENV + "/configuration/groups/[^/]+"
        return {
            ("GET", ORG + "/environments"): self.listed,
            ("GET", re.compile(ENV)): self.environment,
            ("GET", re.compile(ENV + "/settings")): lambda call: (200, self.settings.setdefault(self.env(call), {})),
            ("POST", re.compile(ENV + "/settings")): lambda call: self.settings.setdefault(self.env(call), {}).update(call.json) or 200,
            ("GET", re.compile(ORG + "/users")): self.find_users,
            ("POST", re.compile(ENV + "/users")): self.register,
            ("DELETE", re.compile(ORG + "/users/[^/]+")): self.delete_user,
            ("GET", re.compile(ORG + "/search/users")): self.search,
            ("GET", ORG + "/user"): (200, {"id": "admin-id", "sourceId": "admin"}),
            ("GET", ORG + "/configuration/rolescopes/ENVIRONMENT/roles"): (200, [{"id": "role-user", "name": "USER"}, {"id": "role-admin", "name": "ADMIN"}]),
            ("PUT", re.compile(ORG + "/users/[^/]+/roles")): self.roles_of,
            ("GET", re.compile(AUTOMATION + "/groups/[^/]+")): self.get_group,
            ("PUT", re.compile(AUTOMATION + "/groups")): self.put_group,
            ("GET", re.compile(group)): self.get_entity,
            ("PUT", re.compile(group)): self.update_group,
            ("DELETE", re.compile(group)): self.delete_group,
            ("POST", re.compile(group + "/members")): self.add_members,
            ("GET", re.compile(ENV_V2 + "/apis")): self.list_apis,
            ("POST", re.compile(ENV_V2 + "/apis/_import/definition")): self.import_api,
            ("GET", re.compile(api)): self.get_api,
            ("PUT", re.compile(api + "/_import/definition")): self.update_api,
            ("PUT", re.compile(api)): self.put_api,
            ("POST", re.compile(api + "/deployments")): self.deploy,
            ("POST", re.compile(api + "/_start")): self.start,
            ("POST", re.compile(api + "/_stop")): self.stop,
            ("DELETE", re.compile(api)): self.delete_api,
            ("GET", re.compile(api + "/plans")): self.plans,
            ("GET", re.compile(sub)): self.find_subscriptions,
            ("POST", re.compile(sub)): self.subscribe,
            ("GET", re.compile(sub + "/[^/]+")): self.subscription_of,
            ("PUT", re.compile(sub + "/[^/]+")): self.update_subscription,
            ("POST", re.compile(sub + "/[^/]+/_accept")): self.accept,
            ("POST", re.compile(sub + "/[^/]+/_close")): self.close,
            ("POST", re.compile(PORTAL + "/subscriptions")): self.subscribe_from_portal,
            ("GET", re.compile(ENV + "/applications")): self.find_applications,
            ("POST", re.compile(ENV + "/applications")): self.create_application,
            ("PUT", re.compile(ENV + "/applications/[^/]+")): self.update_application,
            ("DELETE", re.compile(ENV + "/applications/[^/]+")): self.delete_application,
        }


def feed(name, cross_id, path, groups):
    """An API file of an environment: a message API with the three plans of the playground, its variables not replaced yet."""
    api = {"definitionVersion": "V4", "type": "MESSAGE", "name": name, "crossId": cross_id, "visibility": "PRIVATE",
           "description": "${ENV_NAME}: ${ENV_ID}", "listeners": [{"type": "HTTP", "paths": [{"path": path}]}]}
    if groups is not None:
        api["groups"] = groups
    return json.dumps({"api": api, "plans": [
        {"name": "Standard", "mode": "STANDARD", "validation": "AUTO", "commentRequired": False},
        {"name": "Restricted", "mode": "STANDARD", "validation": "MANUAL", "commentRequired": True},
        {"name": "Webhook push", "mode": "PUSH", "validation": "AUTO", "commentRequired": False}]})


CALLBACK = ("      entrypointId: webhook\n      entrypointConfiguration:\n        callbackUrl: ${RECEIVER}/hooks/${ENV_ID}/batch-status\n"
            "        headers: [{name: X-Source, value: apelor-gravitee}]\n        auth: {type: token, token: {value: '${HOOK_TOKEN}'}}\n")
NOVO = {
    "environments/novo/environment.yaml": "id: novo\nname: Novo Nordisk\ndescription: Dedicated environment of Novo Nordisk\n",
    "environments/novo/settings.yaml": "plan: {security: {customApiKey: {enabled: true}, push: {enabled: true}}}\nportalNext: {siteTitle: '${ENV_NAME}'}\n",
    "environments/novo/users.yaml": ("users:\n  - {source: memory, sourceId: astrid.holm, email: astrid@novo.test, firstname: Astrid, lastname: Holm}\n"
                                    "  - {source: memory, sourceId: mikkel.dahl, email: mikkel@novo.test, firstname: Mikkel, lastname: Dahl}\n"),
    "environments/novo/groups/novo-quality.yaml": (
        "hrid: novo-quality\nname: Novo Quality\nnotifyMembers: false\nmembers:\n"
        "  - {source: memory, sourceId: astrid.holm, roles: {API: USER, APPLICATION: OWNER}}\n"
        "  - {source: memory, sourceId: mikkel.dahl, roles: {API: USER, APPLICATION: OWNER}}\n"
        "defaults: {roles: {API: USER, APPLICATION: OWNER}, lockApiRole: false}\n"),
    "environments/novo/apis/batch-status.json": feed("Batch status", "novo-batch-status", "/events/${ENV_ID}/batch-status/", ["novo-quality"]),
    "environments/novo/applications/sap.yaml": "name: Novo SAP\ndescription: SAP integration\ngroups: [novo-quality]\nsettings: {app: {type: SIMPLE}}\n",
    "environments/novo/subscriptions/novo.yaml": (
        "subscriptions:\n"
        "  - {application: Novo SAP, api: Batch status, plan: Standard, apiKey: '${NOVO_KEY}'}\n"
        "  - {application: Novo SAP, api: Batch status, plan: Restricted, pending: true, comment: Reconcile batch genealogy}\n"
        "  - application: Novo SAP\n    api: Batch status\n    plan: Webhook push\n    consumerConfiguration:\n" + CALLBACK),
}
MODERNA = {
    "environments/moderna/environment.yaml": "id: moderna\nname: Moderna\n",
    "environments/moderna/users.yaml": "users:\n  - {source: memory, sourceId: lena.vogt, firstname: Lena, lastname: Vogt}\n",
    "environments/moderna/groups/moderna-process.yaml": (
        "hrid: moderna-process\nname: Moderna Process\nmembers:\n  - {source: memory, sourceId: lena.vogt, roles: {API: USER}}\n"),
    "environments/moderna/apis/batch-status.json": feed("Batch status", "moderna-batch-status", "/events/${ENV_ID}/batch-status/", ["moderna-process"]),
    "environments/moderna/applications/process.yaml": "name: Moderna Process\ngroups: [moderna-process]\n",
    "environments/moderna/subscriptions/moderna.yaml": "subscriptions:\n  - {application: Moderna Process, api: Batch status, plan: Standard}\n",
}
OWN = {"apim/apis/open.json": definition("PROXY", "open-id", "Open API", "/open/"),
       "apim/applications/web.yaml": "name: Web\n",
       "apim/subscriptions/web.yaml": "subscriptions:\n  - {application: Web, api: Open API, plan: Open}\n"}
DIRECTORY = {"astrid.holm": "astrid.holm", "mikkel.dahl": "mikkel.dahl", "lena.vogt": "lena.vogt"}
NOVO_API = "Batch status [novo]"


class Estates(Seed):
    """The module's seed files for the platform's own environment and two others, and a platform that has them."""

    FILES = {**OWN, **NOVO, **MODERNA}

    def setUp(self):
        super().setUp()
        self.estate = Estate(directory=DIRECTORY)
        self.answer(self.estate.routes())
        self.env(RECEIVER="http://receiver:8000", HOOK_TOKEN="hook-token", NOVO_KEY="novo-key-0001")
        self.seed_files(self.FILES)

    def run_seed(self):
        """One run of the module, and what it asked the platform to change."""
        before = len(self.platform.calls)
        apim.seed()
        return [(call.method, call.path) for call in self.platform.calls[before:] if call.method != "GET"]

    def edit(self, name, change):
        path = common.SEED_DIR / name
        path.write_text(change(path.read_text(encoding="utf-8")), encoding="utf-8")

    def record(self, name):
        return next(x for x in common.created() if x["name"] == name)

    def records(self, kind):
        return sorted(x["name"] for x in common.created() if x["kind"] == kind)

    def sent(self, method, path):
        return self.platform.sent(method, re.compile(path))

    def api(self, env, name="Batch status"):
        return next(a for a in self.estate.apis.values() if (a["env"], a["name"]) == (env, name))

    def subscription_to(self, env, plan, name="Batch status"):
        api = self.api(env, name)
        wanted = next(p["id"] for p in api["plans"] if p["name"] == plan)
        return next((s for s in self.estate.subscriptions.values() if s["plan"]["id"] == wanted), None)

    def group(self, env, hrid):
        return next(g for g in self.estate.groups.values() if (g["env"], g["hrid"]) == (env, hrid))

    def subscriptions_of(self, env):
        return [s for s in self.estate.subscriptions.values() if self.estate.apis[s["apiId"]]["env"] == env]


class RequiredEnvironments(Estates):

    def test_the_platforms_own_environment_is_seeded_first_and_the_others_in_name_order(self):
        self.run_seed()
        said = [line for line in self.output().splitlines() if line.startswith("[apim] Environment ") or "API imported" in line]
        self.assertEqual(said, ["[apim] ✓ API imported: Open API", "[apim] Environment Moderna (moderna)",
                                "[apim] ✓ API imported: Batch status [moderna]", "[apim] Environment Novo Nordisk (novo)",
                                "[apim] ✓ API imported: Batch status [novo]"])

    def test_an_environment_that_does_not_exist_stops_the_seed_before_anything_is_changed_anywhere(self):
        self.seed_files({"environments/ghost/environment.yaml": "id: ghost\nname: Ghost\n"})
        with self.assertRaisesRegex(common.SeedError, "^Environment ghost does not exist on this platform: create it, then Re-seed$"):
            self.run_seed()
        self.assertEqual(self.platform.changes(), [])
        self.assertEqual(common.created(), [])

    def test_the_environments_are_looked_at_before_the_first_change(self):
        self.run_seed()
        looked = [i for i, c in enumerate(self.platform.calls) if c.method == "GET" and c.path.endswith(("/environments/moderna", "/environments/novo"))]
        first = next(i for i, c in enumerate(self.platform.calls) if c.method != "GET")
        self.assertEqual(len(looked), 2)
        self.assertLess(max(looked), first)

    def test_a_folder_that_is_badly_named_or_is_the_platforms_own_environment_stops_the_seed_before_any_call(self):
        for folder, why in (("Novo_Nordisk", "an environment is named with lowercase letters, digits and dashes"),
                            ("default", "this is the environment of the platform")):
            with self.subTest(folder=folder):
                self.seed_files({f"environments/{folder}/environment.yaml": f"id: {folder}\nname: x\n"})
                with self.assertRaisesRegex(common.SeedError, f"seed/environments/{folder}: {why}"):
                    self.run_seed()
                self.assertEqual(self.platform.calls, [])
                shutil.rmtree(common.SEED_DIR / "environments" / folder)

    def test_the_environment_file_names_its_folder_and_the_environment(self):
        for text, why in (("name: x\n", "id is None, the folder's name is 'novo'"), ("id: other\nname: x\n", "id is 'other', the folder's name is 'novo'"),
                          ("id: novo\n", "name is missing")):
            with self.subTest(text=text):
                self.seed_files({"environments/novo/environment.yaml": text})
                with self.assertRaisesRegex(common.SeedError, f"seed/environments/novo/environment.yaml: {why}"):
                    self.run_seed()
                self.assertEqual(self.platform.calls, [])
        (common.SEED_DIR / "environments/novo/environment.yaml").unlink()
        with self.assertRaisesRegex(common.SeedError, "seed/environments/novo: environment.yaml is missing"):
            self.run_seed()
        self.assertEqual(self.platform.calls, [])

    def test_what_the_files_say_of_one_another_is_checked_before_any_call(self):
        cases = {
            "an API of a group the folder has not": ("environments/novo/apis/batch-status.json", feed("Batch status", "x", "/x/", ["ghost"]),
                                                     "apis/batch-status.json: ghost is no group of the environment"),
            "an application of a group the folder has not": ("environments/novo/applications/sap.yaml", "name: SAP\ngroups: [ghost]\n",
                                                             "applications: SAP names ghost, which is no group of the environment"),
            "two groups of a hrid": ("environments/novo/groups/again.yaml", "hrid: novo-quality\nname: Again\n", "two groups have the hrid novo-quality"),
            "a group with no name": ("environments/novo/groups/novo-quality.yaml", "hrid: novo-quality\n", "a group needs a hrid and a name"),
            "a member with no source": ("environments/novo/groups/novo-quality.yaml", "hrid: novo-quality\nname: N\nmembers: [{sourceId: x}]\n",
                                        "groups/novo-quality: a member needs a source and a sourceId"),
            "defaults the platform has no field for": ("environments/novo/groups/novo-quality.yaml", "hrid: novo-quality\nname: N\ndefaults: {color: red}\n",
                                                       "defaults has color, which the platform has no field for"),
            "two APIs of a name": ("environments/novo/apis/other.json", feed("Batch status", "y", "/y/", None), "two APIs are named Batch status"),
            "two applications of a name": ("environments/novo/applications/again.yaml", "name: Novo SAP\ndescription: Again\n",
                                           "novo/applications: two applications are named Novo SAP"),
            "a user with no source": ("environments/novo/users.yaml", "users: [{sourceId: x}]\n", "users.yaml: a user needs a source and a sourceId"),
            "a subscription with no plan": ("environments/novo/subscriptions/novo.yaml", "subscriptions:\n  - {application: A, api: B}\n",
                                            "a subscription needs an application, an api and a plan"),
            "a pending subscription with a key": ("environments/novo/subscriptions/novo.yaml",
                                                  "subscriptions:\n  - {application: A, api: B, plan: C, pending: true, apiKey: k}\n", "stays pending, so it takes no apiKey"),
            "a key YAML reads as a number": ("environments/novo/subscriptions/novo.yaml",
                                             "subscriptions:\n  - {application: A, api: B, plan: C, apiKey: 12345}\n", "the apiKey of A → B is read as int, not as text"),
            "a token YAML reads as a boolean": ("environments/novo/subscriptions/novo.yaml",
                                                "subscriptions:\n  - application: A\n    api: B\n    plan: C\n    consumerConfiguration:\n"
                                                "      entrypointId: webhook\n      entrypointConfiguration: {auth: {type: token, token: {value: true}}}\n",
                                                "the token of A → B is read as bool, not as text"),
            "a callback with no entrypoint": ("environments/novo/subscriptions/novo.yaml",
                                              "subscriptions:\n  - {application: A, api: B, plan: C, consumerConfiguration: {channel: x}}\n", "needs an entrypointId"),
        }
        for what, (name, text, why) in cases.items():
            with self.subTest(what):
                original = (common.SEED_DIR / name).read_text(encoding="utf-8") if (common.SEED_DIR / name).exists() else None
                self.seed_files({name: text})
                with self.assertRaisesRegex(common.SeedError, why):
                    self.run_seed()
                self.assertEqual(self.platform.calls, [])
                if original is None:
                    (common.SEED_DIR / name).unlink()
                else:
                    self.seed_files({name: original})

    def test_a_variable_no_seed_service_sets_stops_the_seed_before_any_call(self):
        self.env(NOVO_KEY=None)
        with self.assertRaisesRegex(common.SeedError, "subscriptions/novo.yaml: NOVO_KEY not set"):
            self.run_seed()
        self.assertEqual(self.platform.calls, [])

    def test_a_platform_without_extra_environments_is_seeded_from_seed_apim_alone(self):
        shutil.rmtree(common.SEED_DIR / "environments")
        self.run_seed()
        self.assertEqual({x["name"] for x in common.created()}, {"Open API", "Web", "Web → Open API (Open)"})
        self.assertTrue(all("env" not in x for x in common.created()))

    def test_a_seed_with_no_file_at_all_does_nothing_and_asks_for_nothing(self):
        shutil.rmtree(common.SEED_DIR / "environments")
        shutil.rmtree(common.SEED_DIR / "apim")
        apim.seed()
        self.assertEqual(self.platform.calls, [])


class UsersOfAnEnvironment(Estates):

    def test_a_memory_user_has_no_row_until_its_group_gets_it_from_its_identity_provider(self):
        changes = self.run_seed()
        self.assertEqual([c for c in changes if c[1].endswith("/users")].count(("POST", ORG + "/environments/novo/users")), 2)   # the 404s
        self.assertEqual(sorted(u["sourceId"] for u in self.estate.users.values()), ["astrid.holm", "lena.vogt", "mikkel.dahl"])
        self.assertEqual(sorted(self.records("user")), ["astrid.holm [novo]", "lena.vogt [moderna]", "mikkel.dahl [novo]"])
        self.assertEqual({x["env"] for x in common.created() if x["kind"] == "user"}, {"novo", "moderna"})
        self.assertIn("User astrid.holm: no row yet, its first login or its first group makes it", self.output())
        self.assertIn("✓ User astrid.holm: made by its group", self.output())

    def test_the_user_is_made_by_adding_it_to_its_group_from_the_search_with_the_roles_of_the_file(self):
        self.run_seed()
        search = self.sent("GET", ORG + "/search/users")
        self.assertEqual([c.params["q"] for c in search], ["lena.vogt", "astrid.holm", "mikkel.dahl"])   # the environments in name order
        group = self.group("novo", "novo-quality")
        added = self.sent("POST", ENV + "/configuration/groups/" + group["id"] + "/members")
        self.assertEqual(added[0].json, [{"reference": "ref:astrid.holm", "roles": [{"scope": "API", "name": "USER"}, {"scope": "APPLICATION", "name": "OWNER"}]}])
        self.assertEqual(self.sent("POST", ORG + "/environments/novo/configuration/groups/[^/]+/members")[0].path, added[0].path)

    def test_a_memory_user_of_no_group_has_no_row_and_the_seed_goes_on(self):
        self.seed_files({"environments/novo/groups/novo-quality.yaml": NOVO["environments/novo/groups/novo-quality.yaml"].replace(
            "  - {source: memory, sourceId: mikkel.dahl, roles: {API: USER, APPLICATION: OWNER}}\n", "")})
        self.run_seed()
        self.assertNotIn("mikkel.dahl", [u["sourceId"] for u in self.estate.users.values()])
        self.assertIn("User mikkel.dahl: no row yet", self.output())

    def test_a_user_of_a_source_the_platform_registers_is_registered_in_the_environment_and_recorded(self):
        self.seed_files({"environments/moderna/users.yaml": "users:\n  - {source: gravitee, sourceId: lena@moderna.test, email: lena@moderna.test, firstname: Lena, lastname: Vogt}\n",
                         "environments/moderna/groups/moderna-process.yaml": "hrid: moderna-process\nname: Moderna Process\n"})
        self.run_seed()
        (register,) = self.sent("POST", ORG + "/environments/moderna/users")
        self.assertEqual(register.json, {"source": "gravitee", "sourceId": "lena@moderna.test", "email": "lena@moderna.test", "firstname": "Lena", "lastname": "Vogt"})
        self.assertIn("lena@moderna.test [moderna]", self.records("user"))

    def test_a_user_that_exists_is_adopted_as_it_is_and_never_recorded(self):
        for source_id in ("astrid.holm", "mikkel.dahl"):
            self.estate.users[source_id] = {"id": source_id, "source": "memory", "sourceId": source_id, "status": "ACTIVE"}
        self.run_seed()
        self.assertEqual(self.sent("POST", ORG + "/environments/novo/users"), [])
        self.assertEqual([c.params["q"] for c in self.sent("GET", ORG + "/search/users")], ["lena.vogt"])   # no search for the users it has
        self.assertEqual(self.records("user"), ["lena.vogt [moderna]"])
        self.assertIn("User exists: astrid.holm", self.output())

    def test_a_user_that_was_deleted_is_none_and_is_made_again(self):
        self.estate.users["old"] = {"id": "old", "source": "memory", "sourceId": "astrid.holm", "status": "ARCHIVED"}
        self.run_seed()
        self.assertEqual(self.estate.users["old"]["status"], "ARCHIVED")
        self.assertEqual(len([u for u in self.estate.users.values() if u["sourceId"] == "astrid.holm"]), 2)

    def test_a_member_no_identity_provider_names_stops_the_seed_with_its_name_and_the_group(self):
        self.edit("environments/novo/groups/novo-quality.yaml", lambda text: text.replace("astrid.holm", "ghost.user"))
        with self.assertRaisesRegex(common.SeedError, r"^Group novo-quality \[novo\]: ghost.user is not a user of the platform and no identity provider names exactly one"):
            self.run_seed()

    def test_a_search_with_several_answers_picks_the_one_that_names_the_user_by_its_first_and_last_name(self):
        self.estate.directory = {"astrid.holm": "Astrid Holm", "astrid.holm.old": "Astrid Holm Old", "mikkel.dahl": "Mikkel Dahl", "lena.vogt": "Lena Vogt"}
        self.run_seed()   # users.yaml gives the first and last name of each: they name the user among those that contain its id
        self.assertEqual(sorted(u["sourceId"] for u in self.estate.users.values()), ["astrid.holm", "lena.vogt", "mikkel.dahl"])

    def test_a_search_with_several_answers_none_of_which_names_the_user_is_not_guessed(self):
        self.estate.directory = {**DIRECTORY, "astrid.holm": "Someone", "astrid.holm.old": "Someone else"}
        with self.assertRaisesRegex(common.SeedError, "astrid.holm is not a user of the platform and no identity provider names exactly one"):
            self.run_seed()

    def test_a_removal_deletes_the_users_the_seed_made_and_none_it_adopted(self):
        self.estate.users["adopted"] = {"id": "adopted", "source": "memory", "sourceId": "astrid.holm", "status": "ACTIVE"}
        self.run_seed()
        self.assertEqual(self.main("--remove"), 0, self.output())
        status = {u["sourceId"]: u["status"] for u in self.estate.users.values() if not u["sourceId"].startswith("deleted-")}
        self.assertEqual(status, {"astrid.holm": "ACTIVE"})
        self.assertEqual(sum(u["status"] == "ARCHIVED" for u in self.estate.users.values()), 2)
        self.assertEqual(common.created(), [])


    def test_a_user_the_platform_refuses_to_delete_is_kept_in_the_record_and_everything_else_is_removed(self):
        self.run_seed()
        refused = next(u["id"] for u in self.estate.users.values() if u["sourceId"] == "astrid.holm")
        answer = lambda call: ((400, {"message": "User is still the primary owner of 1 application"})   # noqa: E731
                               if self.estate.after(call, "/users/") == refused else self.estate.delete_user(call))
        self.platform.add({("DELETE", re.compile(ORG + "/users/[^/]+")): answer})
        before = len(common.created())
        self.assertEqual(self.main("--remove"), 0, self.output())
        self.assertEqual([x["name"] for x in common.created()], ["astrid.holm [novo]"])
        self.assertIn("! User 'astrid.holm [novo]' kept: HTTP 400 {\"message\": \"User is still the primary owner of 1 application\"}", self.output())
        self.assertIn(f"✓ {before - 1} items removed, 1 kept", self.output())
        self.assertEqual((self.estate.apis, self.estate.groups, self.estate.applications), ({}, {}, {}))
        self.assertEqual(sum(u["status"] == "ARCHIVED" for u in self.estate.users.values()), 2)
        self.platform.add({("DELETE", re.compile(ORG + "/users/[^/]+")): self.estate.delete_user})   # what owned nothing any more
        self.assertEqual(self.main("--remove"), 0, self.output())
        self.assertEqual(common.created(), [])


class GroupsOfAnEnvironment(Estates):

    def test_a_group_is_written_with_its_members_and_recorded_with_its_hrid_and_environment(self):
        self.run_seed()
        group = self.group("novo", "novo-quality")
        self.assertEqual([m["sourceId"] for m in group["members"]], ["astrid.holm", "mikkel.dahl"])
        self.assertEqual(self.record("novo-quality [novo]"), {"product": "apim", "kind": "group", "label": "groups", "name": "novo-quality [novo]",
                                                            "id": group["id"], "hrid": "novo-quality", "env": "novo"})
        put = next(c for c in self.sent("PUT", AUTOMATION + "/groups") if "/novo/" in c.path)
        self.assertEqual({k: put.json[k] for k in ("hrid", "name", "notifyMembers")}, {"hrid": "novo-quality", "name": "Novo Quality", "notifyMembers": False})
        self.assertNotIn("defaults", put.json)

    def test_the_roles_a_new_member_gets_are_written_with_the_update_of_the_group_and_only_when_they_differ(self):
        self.run_seed()
        group = self.group("novo", "novo-quality")
        self.assertEqual((group["roles"], group["lock_api_role"]), ({"API": "USER", "APPLICATION": "OWNER"}, False))
        (update,) = self.sent("PUT", ENV + "/configuration/groups/[^/]+")
        self.assertEqual(update.json["name"], "Novo Quality")
        self.assertEqual(update.json["roles"], {"API": "USER", "APPLICATION": "OWNER"})
        self.assertEqual(self.run_seed(), [])
        self.edit("environments/novo/groups/novo-quality.yaml", lambda text: text.replace("lockApiRole: false", "lockApiRole: true"))
        self.assertEqual(self.run_seed(), [("PUT", ORG + "/environments/novo/configuration/groups/" + group["id"])])
        self.assertTrue(group["lock_api_role"])

    def test_what_the_platform_adds_to_a_group_is_not_a_difference_and_what_someone_added_is_left(self):
        self.run_seed()
        group = self.group("novo", "novo-quality")
        group["members"].append({"source": "memory", "sourceId": "someone.else", "roles": {"API": "OWNER"}})
        self.assertEqual(self.run_seed(), [])
        self.assertEqual(len(group["members"]), 3)

    def test_a_group_that_lacks_a_member_or_a_name_is_written_again(self):
        self.run_seed()
        group = self.group("novo", "novo-quality")
        group["members"].pop()
        self.assertEqual(self.run_seed(), [("PUT", AUTOMATION.replace("[^/]+", "novo") + "/groups")])
        self.assertEqual(len(group["members"]), 2)
        self.edit("environments/novo/groups/novo-quality.yaml", lambda text: text.replace("name: Novo Quality", "name: Novo Quality Assurance"))
        self.run_seed()
        self.assertEqual(group["name"], "Novo Quality Assurance")
        self.assertEqual(self.estate.groups[group["id"]], group, "the same group, renamed")

    def test_a_member_whose_role_does_not_exist_stops_the_seed_with_what_the_platform_says(self):
        self.edit("environments/novo/groups/novo-quality.yaml", lambda text: text.replace("sourceId: astrid.holm, roles: {API: USER,", "sourceId: astrid.holm, roles: {API: NOSUCHROLE,"))
        for source_id in ("astrid.holm", "mikkel.dahl"):   # users with a row: the Automation API adds them itself
            self.estate.users[source_id] = {"id": source_id, "source": "memory", "sourceId": source_id, "status": "ACTIVE"}
        with self.assertRaisesRegex(common.SeedError, r"^Group novo-quality \[novo\]: astrid.holm is not a member of it as its file says: member role \[NOSUCHROLE\] doesn't exist$"):
            self.run_seed()

    def test_a_user_with_no_row_whose_role_does_not_exist_is_refused_by_the_platform_with_its_name_and_group(self):
        self.edit("environments/novo/groups/novo-quality.yaml", lambda text: text.replace("sourceId: astrid.holm, roles: {API: USER,", "sourceId: astrid.holm, roles: {API: NOSUCHROLE,"))
        with self.assertRaisesRegex(common.SeedError, r"^Group novo-quality \[novo\]: astrid.holm could not be added to it: HTTP 404 .*NOSUCHROLE"):
            self.run_seed()

    def test_a_hrid_that_a_group_the_seed_did_not_make_holds_stops_the_seed_and_changes_it_not(self):
        self.estate.groups["theirs"] = {"id": "theirs", "env": "novo", "hrid": "novo-quality", "name": "Theirs", "roles": {}, "lock_api_role": False,
                                        "members": [], "origin": None, "notifyMembers": True}
        with self.assertRaisesRegex(common.SeedError, r"^Group novo-quality \[novo\]: the hrid is taken by a group the seed did not create$"):
            self.run_seed()
        self.assertEqual(self.estate.groups["theirs"]["name"], "Theirs")
        self.assertEqual([c for c in self.platform.changes() if "/novo/" in c[1] and "groups" in c[1]], [])

    def test_the_flag_that_says_a_member_is_not_one_of_the_users_file_is_not_sent_to_the_platform(self):
        self.estate.users["admin"] = {"id": "admin", "source": "memory", "sourceId": "admin", "status": "ACTIVE"}
        self.edit("environments/novo/groups/novo-quality.yaml", lambda text: text.replace("defaults:", "  - {source: memory, sourceId: admin, external: true, roles: {API: OWNER}}\ndefaults:"))
        self.run_seed()
        put = next(c for c in self.sent("PUT", AUTOMATION + "/groups") if "/novo/" in c.path)
        self.assertEqual([m["sourceId"] for m in put.json["members"]], ["astrid.holm", "mikkel.dahl", "admin"])
        self.assertTrue(all("external" not in m for m in put.json["members"]))

    def test_a_removal_deletes_a_group_by_its_id_in_its_environment(self):
        self.run_seed()
        ids = {env: self.group(env, hrid)["id"] for env, hrid in (("novo", "novo-quality"), ("moderna", "moderna-process"))}
        calls = len(self.platform.calls)
        self.assertEqual(self.main("--remove"), 0, self.output())
        deleted = [c.path for c in self.platform.calls[calls:] if c.method == "DELETE" and "/configuration/groups/" in c.path]
        self.assertEqual(sorted(deleted), sorted(f"{ORG}/environments/{env}/configuration/groups/{id}" for env, id in ids.items()))
        self.assertEqual(self.estate.groups, {})


class ScopedRecords(Estates):

    def test_the_same_api_name_in_two_environments_is_two_records_each_with_its_environment(self):
        self.run_seed()
        records = [x for x in common.created() if x["kind"] == "api"]
        self.assertEqual(sorted((x["name"], x.get("env")) for x in records),
                         [("Batch status [moderna]", "moderna"), ("Batch status [novo]", "novo"), ("Open API", None)])
        self.assertEqual(len({x["id"] for x in records}), 3)
        self.assertEqual({a["env"]: a["id"] for a in self.estate.apis.values() if a["name"] == "Batch status"},
                         {"novo": self.record("Batch status [novo]")["id"], "moderna": self.record("Batch status [moderna]")["id"]})

    def test_what_is_recorded_for_the_platforms_own_environment_has_a_plain_name_and_no_environment(self):
        self.run_seed()
        own = [x for x in common.created() if x["name"] in ("Open API", "Web", "Web → Open API (Open)")]
        self.assertEqual(len(own), 3)
        self.assertTrue(all("env" not in x for x in own))
        self.assertEqual(apim.made_under("Open API")["id"], "open-id")
        self.assertIsNone(apim.made_under("Batch status"))

    def test_an_application_and_a_subscription_of_an_environment_are_recorded_with_it(self):
        self.run_seed()
        self.assertEqual(self.records("application"), ["Moderna Process [moderna]", "Novo SAP [novo]", "Web"])
        self.assertIn("Novo SAP → Batch status (Standard) [novo]", self.records("subscription"))
        self.assertEqual(self.record("Novo SAP → Batch status (Standard) [novo]")["api"], self.api("novo")["id"])

    def test_a_subscription_goes_to_the_api_of_its_environment_and_not_to_the_one_with_the_same_name_of_another(self):
        self.seed_files({"environments/moderna/applications/process.yaml": "name: Novo SAP\ngroups: [moderna-process]\n",
                         "environments/moderna/subscriptions/moderna.yaml": "subscriptions:\n  - {application: Novo SAP, api: Batch status, plan: Standard}\n"})
        self.run_seed()
        mine = {s["apiId"] for s in self.estate.subscriptions.values() if s["application"]["id"] in {
            a["id"] for a in self.estate.applications.values() if a["env"] == "moderna"}}
        self.assertEqual(mine, {self.api("moderna")["id"]})

    def test_a_second_environment_that_names_an_api_of_the_first_does_not_take_it_over(self):
        self.run_seed()
        calls = len(self.platform.calls)
        self.assertEqual(self.run_seed(), [])
        self.assertEqual(self.output().count("API up to date: Batch status [moderna]"), 1)
        self.assertEqual([c for c in self.platform.calls[calls:] if c.method == "POST" and "_import" in c.path], [])

    def test_a_removal_goes_where_each_record_says(self):
        self.run_seed()
        ids = {"DEFAULT": "open-id", "moderna": self.api("moderna")["id"], "novo": self.api("novo")["id"]}
        calls = len(self.platform.calls)
        self.assertEqual(self.main("--remove"), 0, self.output())
        deleted = [c.path for c in self.platform.calls[calls:] if c.method == "DELETE" and "/apis/" in c.path]
        self.assertEqual(sorted(deleted), sorted(f"{v2(env)}/apis/{id}" for env, id in ids.items()))
        self.assertEqual(self.estate.apis, {})


def v2(env):
    return f"/management/v2/organizations/DEFAULT/environments/{env}"


class ApisOfAnEnvironment(Estates):

    def test_an_api_is_imported_with_the_names_of_its_groups_and_the_import_makes_no_group(self):
        self.run_seed()
        group = self.group("novo", "novo-quality")
        imported = next(c for c in self.sent("POST", ENV_V2 + "/apis/_import/definition") if "/novo/" in c.path)
        self.assertEqual(imported.json["api"]["groups"], ["Novo Quality"])
        self.assertEqual(self.estate.groups_by_import, [], "an id or a name the platform does not know makes a group")
        self.assertEqual(self.api("novo")["groups"], [group["id"]])

    def test_the_names_the_engine_gives_are_replaced_in_the_files_of_an_environment(self):
        self.run_seed()
        definition = self.api("novo")["definition"]["api"]
        self.assertEqual((definition["listeners"][0]["paths"][0]["path"], definition["description"]), ("/events/novo/batch-status/", "Novo Nordisk: novo"))
        self.assertEqual(self.estate.settings["novo"]["portalNext"], {"siteTitle": "Novo Nordisk"})

    def test_the_digest_of_an_api_is_the_digest_of_its_file_with_the_group_hrids(self):
        self.run_seed()
        file = json.loads(common.render(NOVO["environments/novo/apis/batch-status.json"], "x.json", json_escape=True, provided={"ENV_ID": "novo", "ENV_NAME": "Novo Nordisk"}))
        self.assertEqual(self.record("Batch status [novo]")["digest"], apim.digest_of(file))

    def test_an_api_whose_file_names_no_group_is_left_to_the_platform(self):
        self.seed_files({"environments/moderna/apis/batch-status.json": feed("Batch status", "moderna-batch-status", "/events/moderna/batch-status/", None)})
        self.run_seed()
        self.assertEqual(self.api("moderna")["groups"], [])
        self.assertNotIn("Groups of Batch status [moderna]", self.output())
        self.assertEqual(self.run_seed(), [])

    def test_the_groups_an_update_by_import_cleared_are_written_back_and_the_api_is_deployed_once(self):
        self.run_seed()
        self.edit("environments/novo/apis/batch-status.json", lambda text: text.replace('"visibility": "PRIVATE"', '"visibility": "PRIVATE", "apiVersion": "2"'))
        api_id = self.api("novo")["id"]
        changes = self.run_seed()
        self.assertEqual(changes, [("PUT", f"{v2('novo')}/apis/{api_id}/_import/definition"), ("PUT", f"{v2('novo')}/apis/{api_id}"),
                                   ("POST", f"{v2('novo')}/apis/{api_id}/deployments")])
        self.assertEqual(self.api("novo")["groups"], [self.group("novo", "novo-quality")["id"]])
        self.assertEqual(self.run_seed(), [])

    def test_the_groups_someone_changed_are_set_back_without_an_import(self):
        self.run_seed()
        self.api("novo")["groups"] = []
        api_id = self.api("novo")["id"]
        self.assertEqual(self.run_seed(), [("PUT", f"{v2('novo')}/apis/{api_id}")])
        self.assertEqual(self.api("novo")["groups"], [self.group("novo", "novo-quality")["id"]])
        self.assertIn("Groups of Batch status [novo]: 1", self.output())

    def test_an_api_that_the_import_gave_the_groups_of_its_file_has_no_write_of_them(self):
        self.run_seed()
        self.assertNotIn("Groups of Batch status", self.output())
        self.assertEqual(len([c for c in self.sent("PUT", ENV_V2 + "/apis/[^/]+") if "/novo/" in c.path]), 1, "its publication, and no more")

    def test_the_api_ids_of_a_file_without_ids_are_the_platforms_and_the_api_is_found_again_by_its_path(self):
        self.run_seed()
        self.assertEqual(self.record("Batch status [novo]")["id"], self.api("novo")["id"])
        self.assertNotEqual(self.record("Batch status [novo]")["id"], self.record("Batch status [moderna]")["id"])
        self.assertEqual(self.run_seed(), [])

    def test_the_controls_name_an_api_of_the_platforms_own_environment_and_not_one_of_the_others(self):
        self.seed_files({"controls.yaml": "guard: {api: Batch status, flow: Events, steps: {request: [Rate limit]}}\n"})
        self.run_seed()   # a control that reached these APIs would find no flow in them and stop the seed
        self.assertEqual(self.output().count("Controls of"), 0)

    def test_an_application_has_the_ids_of_its_groups_and_they_are_compared_as_a_set(self):
        self.run_seed()
        group = self.group("novo", "novo-quality")
        (app,) = [a for a in self.estate.applications.values() if a["name"] == "Novo SAP"]
        self.assertEqual(app["groups"], [group["id"]])
        self.assertEqual(self.run_seed(), [])
        app["groups"] = []
        self.assertEqual(self.run_seed(), [("PUT", f"{ORG}/environments/novo/applications/{app['id']}")])
        self.assertEqual(app["groups"], [group["id"]])

    def test_an_application_without_groups_in_its_file_is_sent_without_any(self):
        self.run_seed()
        sent = next(c for c in self.sent("POST", ENV + "/applications") if "/DEFAULT/" in c.path)
        self.assertNotIn("groups", sent.json)


class SubscriptionsOfAnEnvironment(Estates):

    def test_a_subscription_with_a_key_is_made_by_the_management_api_with_that_custom_key(self):
        self.run_seed()
        standard = self.subscription_to("novo", "Standard")
        self.assertEqual((standard["status"], standard["key"]), ("ACCEPTED", "novo-key-0001"))
        (create,) = [c for c in self.sent("POST", ENV_V2 + "/apis/[^/]+/subscriptions") if "/novo/" in c.path and c.json.get("customApiKey")]
        self.assertEqual(create.json["customApiKey"], "novo-key-0001")
        self.assertNotIn("novo-key-0001", self.output())
        self.assertNotIn("novo-key-0001", self.state.read_text(encoding="utf-8"))

    def test_a_pending_subscription_goes_through_the_portal_api_with_its_comment_and_stays_pending(self):
        self.run_seed()
        pending = self.subscription_to("novo", "Restricted")
        self.assertEqual((pending["status"], pending["consumerMessage"]), ("PENDING", "Reconcile batch genealogy"))
        (portal,) = self.sent("POST", PORTAL + "/subscriptions")
        self.assertEqual(portal.path, "/portal/environments/novo/subscriptions")
        self.assertEqual(portal.json, {"application": next(a["id"] for a in self.estate.applications.values() if a["name"] == "Novo SAP"),
                                       "plan": pending["plan"]["id"], "request": "Reconcile batch genealogy"})
        self.assertEqual(self.sent("POST", ENV_V2 + "/apis/[^/]+/subscriptions/[^/]+/_accept"), [])
        self.assertIn("✓ Subscription: Novo SAP → Batch status (Restricted) [novo] (pending)", self.output())

    def test_a_comment_on_a_plan_that_validates_by_hand_is_sent_with_the_request_then_accepted_with_the_key(self):
        self.seed_files({"environments/novo/subscriptions/novo.yaml": (
            "subscriptions:\n  - {application: Novo SAP, api: Batch status, plan: Restricted, comment: Reconcile, apiKey: '${NOVO_KEY}'}\n")})
        self.run_seed()
        sub = self.subscription_to("novo", "Restricted")
        self.assertEqual((sub["status"], sub["consumerMessage"], sub["key"]), ("ACCEPTED", "Reconcile", "novo-key-0001"))
        (accept,) = self.sent("POST", ENV_V2 + "/apis/[^/]+/subscriptions/[^/]+/_accept")
        self.assertEqual(accept.json, {"customApiKey": "novo-key-0001"})

    def test_a_comment_on_a_plan_that_validates_by_hand_without_a_key_is_accepted_with_an_empty_body(self):
        self.seed_files({"environments/novo/subscriptions/novo.yaml": (
            "subscriptions:\n  - {application: Novo SAP, api: Batch status, plan: Restricted, comment: Reconcile}\n")})
        self.run_seed()
        self.assertEqual(self.subscription_to("novo", "Restricted")["status"], "ACCEPTED")
        self.assertEqual(self.sent("POST", ENV_V2 + "/apis/[^/]+/subscriptions/[^/]+/_accept")[0].json, {})

    def test_a_push_subscription_sends_its_callback_with_the_variables_replaced(self):
        self.run_seed()
        push = self.subscription_to("novo", "Webhook push")
        self.assertEqual(push["consumerConfiguration"], {
            "entrypointId": "webhook", "entrypointConfiguration": {
                "callbackUrl": "http://receiver:8000/hooks/novo/batch-status", "headers": [{"name": "X-Source", "value": "apelor-gravitee"}],
                "auth": {"type": "token", "token": {"value": "hook-token"}}}})
        self.assertNotIn("hook-token", self.output())
        self.assertNotIn("hook-token", self.state.read_text(encoding="utf-8"))

    def test_a_push_subscription_with_a_comment_goes_through_the_portal_api_with_its_callback(self):
        self.edit("environments/novo/subscriptions/novo.yaml", lambda text: text.replace("plan: Webhook push\n", "plan: Webhook push\n    comment: Push it\n"))
        self.run_seed()
        portal = [c for c in self.sent("POST", PORTAL + "/subscriptions") if c.json.get("request") == "Push it"]
        self.assertEqual(portal[0].json["configuration"]["entrypointId"], "webhook")
        push = self.subscription_to("novo", "Webhook push")
        self.assertEqual((push["status"], push["consumerMessage"]), ("ACCEPTED", "Push it"))

    def test_a_pending_subscription_is_found_again_and_made_once(self):
        """The platform reads the first status of a repeated parameter: asked as one comma-separated value, it finds a pending one."""
        self.run_seed()
        self.assertEqual(self.run_seed(), [])
        self.assertEqual(len([s for s in self.estate.subscriptions.values() if s["status"] == "PENDING"]), 1)
        statuses = {c.params["statuses"] for c in self.platform.calls if c.method == "GET" and "/subscriptions" in c.path and "statuses" in c.params}
        self.assertEqual(statuses, {"ACCEPTED,PENDING,PAUSED"})

    def test_a_pending_subscription_that_someone_accepted_is_left_accepted(self):
        self.run_seed()
        self.subscription_to("novo", "Restricted")["status"] = "ACCEPTED"
        self.assertEqual(self.run_seed(), [])

    def test_a_callback_that_differs_from_its_file_updates_the_subscription_and_keeps_its_dates(self):
        self.run_seed()
        push = self.subscription_to("novo", "Webhook push")
        self.edit("environments/novo/subscriptions/novo.yaml", lambda text: text.replace("/batch-status", "/changed"))
        self.assertEqual(self.run_seed(), [("PUT", f"{v2('novo')}/apis/{self.api('novo')['id']}/subscriptions/{push['id']}")])
        self.assertEqual(push["consumerConfiguration"]["entrypointConfiguration"]["callbackUrl"], "http://receiver:8000/hooks/novo/changed")
        self.assertEqual(push["startingAt"], "2026-10-16T10:00:00Z", "the update clears the dates it is not given")
        self.assertIn("✓ Subscription updated: Novo SAP → Batch status (Webhook push) [novo]", self.output())
        self.assertEqual(self.run_seed(), [])

    def test_a_callback_the_platform_added_defaults_to_is_not_a_difference(self):
        self.run_seed()
        push = self.subscription_to("novo", "Webhook push")
        push["consumerConfiguration"]["entrypointConfiguration"]["retry"] = {"retryOption": "No Delay"}
        self.assertEqual(self.run_seed(), [])

    def test_the_callback_of_a_subscription_the_seed_did_not_make_is_left_as_it_is(self):
        self.run_seed()
        push = self.subscription_to("novo", "Webhook push")
        common.save_created([x for x in common.created() if x["id"] != push["id"]])
        self.edit("environments/novo/subscriptions/novo.yaml", lambda text: text.replace("/batch-status", "/changed"))
        self.assertEqual(self.run_seed(), [])
        self.assertIn("! Subscription Novo SAP → Batch status (Webhook push) [novo] is not one the seed made", self.output())
        self.assertIn("/batch-status", push["consumerConfiguration"]["entrypointConfiguration"]["callbackUrl"])

    def test_a_webhook_token_written_as_a_plain_string_is_refused_by_the_platform_and_stops_the_seed(self):
        self.edit("environments/novo/subscriptions/novo.yaml", lambda text: text.replace("token: {value: '${HOOK_TOKEN}'}", "token: '${HOOK_TOKEN}'"))
        with self.assertRaisesRegex(common.SeedError, "HTTP 400 .*string found, object expected"):
            self.run_seed()
        self.assertEqual(self.subscription_to("novo", "Webhook push"), None)

    def test_a_subscription_that_stays_pending_on_a_plan_that_accepts_by_itself_stops_the_seed(self):
        self.seed_files({"environments/novo/subscriptions/novo.yaml": "subscriptions:\n  - {application: Novo SAP, api: Batch status, plan: Standard, pending: true}\n"})
        with self.assertRaisesRegex(common.SeedError, r"^Subscription Novo SAP → Batch status \(Standard\) \[novo\]: the plan accepts a subscription by itself, so it cannot stay pending$"):
            self.run_seed()
        self.assertEqual(self.subscriptions_of("novo"), [])

    def test_a_comment_with_a_key_on_a_plan_that_accepts_by_itself_stops_the_seed(self):
        self.seed_files({"environments/novo/subscriptions/novo.yaml": "subscriptions:\n  - {application: Novo SAP, api: Batch status, plan: Standard, comment: Why, apiKey: k}\n"})
        with self.assertRaisesRegex(common.SeedError, r"a comment goes with a request, which this plan accepts at once with a key of its own"):
            self.run_seed()
        self.assertEqual(self.subscriptions_of("novo"), [])

    def test_the_plan_of_a_subscription_must_exist_on_the_api_of_its_environment(self):
        self.seed_files({"environments/novo/subscriptions/novo.yaml": "subscriptions:\n  - {application: Novo SAP, api: Batch status, plan: Gold}\n"})
        with self.assertRaisesRegex(common.SeedError, r"^Subscription Novo SAP → Batch status \(Gold\) \[novo\]: plan not found$"):
            self.run_seed()

    def test_a_removal_closes_each_subscription_of_its_api_in_its_environment_pending_ones_too(self):
        self.run_seed()
        calls = len(self.platform.calls)
        self.assertEqual(self.main("--remove"), 0, self.output())
        closed = [c.path for c in self.platform.calls[calls:] if c.path.endswith("/_close")]
        self.assertEqual(len(closed), 5)
        self.assertEqual(sorted(p.split("/environments/")[1].split("/")[0] for p in closed), ["DEFAULT", "moderna", "novo", "novo", "novo"])
        self.assertEqual(self.estate.subscriptions, {})


class Membership(Estates):

    def test_an_environment_that_lists_the_login_is_left_as_it_is(self):
        self.run_seed()
        self.assertEqual(self.sent("PUT", ORG + "/users/[^/]+/roles"), [])
        self.assertEqual(self.records("membership"), [])

    def test_an_environment_the_login_has_no_role_in_is_not_listed_so_the_login_becomes_its_administrator_and_a_removal_takes_the_role_back(self):
        self.estate.unlisted = {"novo", "moderna"}
        self.run_seed()
        puts = self.sent("PUT", ORG + "/users/[^/]+/roles")
        self.assertEqual(sorted(p.json["referenceId"] for p in puts), ["moderna", "novo"])
        self.assertEqual({"user": "admin-id", "referenceType": "ENVIRONMENT", "referenceId": "novo", "roles": ["role-admin"]},
                         next(p.json for p in puts if p.json["referenceId"] == "novo"))
        self.assertEqual(self.record("admin [novo]"), {"product": "apim", "kind": "membership", "label": "memberships", "name": "admin [novo]",
                                                       "id": "admin-id", "env": "novo"})
        self.assertEqual(self.run_seed(), [])   # listed now: nothing is written again
        self.assertEqual(self.main("--remove"), 0, self.output())
        self.assertEqual(self.estate.memberships[("admin-id", "novo")], [])
        self.assertEqual(self.estate.memberships[("admin-id", "moderna")], [])
        self.assertEqual(common.created(), [])

    def test_a_login_that_the_environment_still_does_not_list_stops_the_seed(self):
        self.estate.unlisted = {"novo"}
        self.estate.roles_of = lambda call: 200   # the platform does not take the role
        self.platform.add(self.estate.routes())
        with self.assertRaisesRegex(common.SeedError, r"^Environment novo: the platform does not list it for the seed's login$"):
            self.run_seed()

    def test_a_platform_without_an_administrator_role_for_an_environment_stops_the_seed(self):
        self.estate.unlisted = {"novo"}
        self.platform.add({("GET", ORG + "/configuration/rolescopes/ENVIRONMENT/roles"): (200, [{"id": "role-user", "name": "USER"}])})
        with self.assertRaisesRegex(common.SeedError, "Environment novo: the platform has no administrator role for an environment"):
            self.run_seed()


class ManyEnvironments(Estates):
    """The whole of it, as a playground runs it."""

    def test_a_second_run_changes_nothing_anywhere(self):
        self.assertEqual(self.main(), 0, self.output())
        calls = len(self.platform.calls)
        self.assertEqual(self.main(), 0, self.output())
        self.assertEqual([c for c in self.platform.calls[calls:] if c.method != "GET"], [])
        self.assertEqual(self.output().count("API up to date"), 3)

    def test_a_second_run_ends_with_the_same_objects_ids_keys_and_records(self):
        self.run_seed()
        before = (dict(self.estate.apis), dict(self.estate.groups), dict(self.estate.applications), copy.deepcopy(self.estate.subscriptions),
                  copy.deepcopy(self.estate.users), common.created())
        self.run_seed()
        self.assertEqual((dict(self.estate.apis), dict(self.estate.groups), dict(self.estate.applications), self.estate.subscriptions,
                          self.estate.users, common.created()), before)

    def test_each_environment_is_seeded_in_the_order_its_objects_depend_on_one_another(self):
        changes = self.run_seed()
        novo = [c for c in changes if "/novo/" in c[1]]
        first = {}
        for kind, marker in (("users", "/users"), ("settings", "/settings"), ("groups", "/groups"), ("apis", "/_import/definition"),
                             ("applications", "/applications"), ("subscriptions", "/subscriptions")):
            first[kind] = next(i for i, c in enumerate(novo) if marker in c[1])
        # The APIs and the applications depend on the users, the settings and the groups, and not on one another; the
        # subscriptions on both
        self.assertEqual(sorted(first, key=first.get)[:3], ["users", "settings", "groups"])
        self.assertEqual(sorted(first, key=first.get)[-1], "subscriptions")
        self.assertEqual(first["users"], 0, "the users come before everything else")

    def test_the_gateway_is_waited_for_once_for_the_apis_of_every_environment_within_one_deadline(self):
        self.env(GATEWAY_INTERNAL_URL=GATEWAY, GATEWAY_READY_TIMEOUT="30")
        paths = {"/open/": 401, "/events/novo/batch-status/": [NO_API, 401], "/events/moderna/batch-status/": [NO_API, NO_API, 401]}
        self.platform.add({("GET", GATEWAY + path): answer for path, answer in paths.items()})
        apim.seed()
        self.assertEqual(self.clock.sleeps, [apim.LOOK_AGAIN] * 3)
        out = self.output()
        self.assertEqual([out.index(f"✓ {name} answers on the gateway") for name in ("Open API", "Batch status [moderna]", "Batch status [novo]")],
                         sorted(out.index(f"✓ {name} answers on the gateway") for name in ("Open API", "Batch status [moderna]", "Batch status [novo]")))
        self.assertEqual(out.count("answers on the gateway"), 3)

    def test_an_api_of_an_environment_that_never_answers_names_its_environment(self):
        self.env(GATEWAY_INTERNAL_URL=GATEWAY, GATEWAY_READY_TIMEOUT="30")
        self.platform.add({("GET", GATEWAY + "/open/"): 401, ("GET", GATEWAY + "/events/moderna/batch-status/"): 401,
                           ("GET", GATEWAY + "/events/novo/batch-status/"): NO_API})
        with self.assertRaisesRegex(common.SeedError, r"^Batch status \[novo\] does not answer on the gateway$"):
            apim.seed()
        # the deadline is the real time plus the fake one: a slow machine ends it a look sooner, never a look later
        self.assertTrue(30 - apim.LOOK_AGAIN <= self.clock.elapsed <= 30 + apim.LOOK_AGAIN, self.clock.elapsed)

    def test_a_removal_leaves_alone_what_an_environment_that_is_gone_held_and_still_deletes_the_users(self):
        self.run_seed()
        self.estate.environments.discard("novo")
        calls = len(self.platform.calls)
        self.assertEqual(self.main("--remove"), 0, self.output())
        novo = [c for c in self.platform.calls[calls:] if "/environments/novo" in c.path]
        self.assertEqual([(c.method, c.path) for c in novo], [("GET", ORG + "/environments/novo")], "asked once, then nothing")
        self.assertIn("✓ Deleted api 'Batch status [novo]': its environment is gone", self.output())
        self.assertEqual(common.created(), [])
        self.assertEqual(sum(u["status"] == "ARCHIVED" for u in self.estate.users.values()), 3)

    def test_a_removal_deletes_what_was_recorded_from_the_last_kind_to_the_first_in_every_environment(self):
        self.run_seed()
        self.assertEqual(self.main("--remove"), 0, self.output())
        order = ["subscription", "application", "api", "group", "user"]
        deleted = [line.split("Deleted ")[1].split(" ")[0] for line in self.output().splitlines() if "✓ Deleted" in line]
        self.assertEqual(deleted, sorted(deleted, key=order.index))
        self.assertEqual(sorted(set(deleted)), sorted(order))
        self.assertEqual((self.estate.apis, self.estate.groups, self.estate.applications, common.created()), ({}, {}, {}, []))
        self.assertEqual(self.estate.settings["novo"]["plan"], {"security": {"customApiKey": {"enabled": True}, "push": {"enabled": True}}},
                         "the settings of an environment are not the seed's to take back")


if __name__ == "__main__":
    unittest.main()


# --- The same, with the steps that do not depend on one another made at the same time ----------

class Together:
    """Runs the tests of the class it is mixed with with SEED_WORKERS=4: what the seed leaves must be what it leaves in order."""

    def setUp(self):
        super().setUp()
        self.workers(4)


class FirstRunTogether(Together, FirstRun):
    def test_a_definition_without_an_id_is_found_again_by_its_path(self):
        self.seed_files({"apim/apis/rest.json": REST.replace('"id": "rest-id", ', "")})
        self.run_seed()
        self.assertTrue(self.record("Open API")["id"].startswith("api-"))   # which number it gets depends on who was first
        self.assertEqual(self.run_seed(), [])

    def test_an_api_the_seed_did_not_create_holds_the_path_of_one_with_a_free_id(self):
        self.apim.apis["mine"] = {"id": "mine", "name": "My proxy", "type": "PROXY", "state": "STARTED", "lifecycleState": "PUBLISHED",
                                  "listeners": [{"paths": [{"path": "/ace/"}]}]}
        with self.assertRaisesRegex(common.SeedError, "/ace/ is the path of 'My proxy', which the seed did not create"):
            self.run_seed()
        self.assertEqual([x for x in self.platform.changes() if "ace" in x[1] or "a2a-id" in x[1]], [], "that API is left alone")


class SecondRunTogether(Together, SecondRun):
    pass


class ManyEnvironmentsTogether(Together, ManyEnvironments):
    pass


class ApisOfAnEnvironmentTogether(Together, ApisOfAnEnvironment):
    pass


class SubscriptionsOfAnEnvironmentTogether(Together, SubscriptionsOfAnEnvironment):
    pass


class ScopedRecordsTogether(Together, ScopedRecords):
    pass


class UsersOfTheOrganization(Together, Estates):
    """A user is the organization's, not an environment's: two environments never make users at the same time."""

    def test_no_environment_makes_a_user_while_another_makes_one(self):
        inside, overlapped, lock = [0], [], threading.Lock()
        register, make = apim.ApiManagement.register, apim.ApiManagement.make_user

        def watched(function):
            def wrapper(*arguments):
                with lock:
                    inside[0] += 1
                    if inside[0] > 1:
                        overlapped.append(arguments[1:])
                try:
                    return function(*arguments)
                finally:
                    with lock:
                        inside[0] -= 1
            return wrapper
        with mock.patch.object(apim.ApiManagement, "register", watched(register)), \
                mock.patch.object(apim.ApiManagement, "make_user", watched(make)):
            self.run_seed()
        self.assertEqual(overlapped, [])
