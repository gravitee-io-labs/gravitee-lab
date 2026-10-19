"""The AI catalog: credentials, providers, models, MCP servers, documents, skills, agents and the tools of an API, seeded
against a memory that behaves like Gamma where it matters: one manual source, a duplicate entity id refused, an object
stored with less and more than it was sent, a secret never given back."""

import ast
import io
import itertools
import json
import os
import re
import unittest
import zipfile
from unittest import mock

import requests
import yaml

from lab_seeder import aim_catalog
from lab_seeder import common
from lab_seeder.testkit import PLAYGROUNDS, Response, Seed

SCOPE = "/gamma/organizations/DEFAULT/environments/DEFAULT/modules/aim"
MCP = "http://bakery-mcp:8000"
MOVED = "http://127.0.0.1:8000"
AGENT = "http://bakery-agent:8000"
CARD = AGENT + aim_catalog.CARD_PATH
REAL = PLAYGROUNDS / "acme-bakery" / "seed" / "aim"
POLICIES = PLAYGROUNDS / "acme-bakery" / "apps" / "bakery-mcp" / "policies.py"
MINE = {folder.split("/")[1] for folder in aim_catalog.FOLDERS}   # the folders of the seed that this module reads
ENV = {"OLLAMA_URL": "http://ollama:11434", "BAKERY_MCP_URL": MCP, "BAKERY_AGENT_URL": AGENT}


def path(pattern=""):
    """A route for any path that starts with this of the AI module, `*` standing for an id."""
    return re.compile(re.escape(SCOPE) + pattern.replace("*", "[^/]+"))


def slug(text):
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9_-]+", "-", text.lower())).strip("-")


def last(call):
    return call.path.rsplit("/", 1)[1]


def listing(rows):
    rows = list(rows)
    return 200, {"data": rows, "pagination": {"page": 1, "perPage": 200, "totalCount": len(rows)}}


def policy_texts():
    """What the bank's MCP server publishes as the resources acme://policies/*: the constants of its module, read without
    running it."""
    tree = ast.parse(POLICIES.read_text(encoding="utf-8"))
    return {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)}


class World:
    """Agent Management as far as the catalog goes. It keeps what it is given and gives ids. It has a single manual
    source, whatever the name asked for; refuses a duplicate entity id with a 500; keeps of an agent card only what it
    knows and adds a field of its own; stamps a price it is given; and never gives a secret back."""

    def __init__(self):
        self.ids = itertools.count(1)
        self.credentials, self.secrets, self.providers, self.sources, self.items = {}, {}, {}, {}, {}
        self.apis = {}   # id -> {"name": ..., "operations": [...], "errors": [...]}
        self.mcp = {"name": "Acme Bakery MCP", "protocol": "2025-11-25", "pages": 1, "sse": False, "session": None,
                    "tools": [{"name": "get_balance", "description": "Balance", "title": "Get balance",
                               "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
                              {"name": "transfer_funds", "description": "Transfer", "inputSchema": {"type": "object"}}],
                    "prompts": [{"name": "dispute_charge", "description": "Dispute"}],
                    "resources": [{"uri": "acme://policies/fees", "name": "fees", "mimeType": "text/markdown", "size": 3}]}
        self.card = {"name": "Ace", "description": "Acme Bakery's assistant", "version": "1.0.0",
                     "supportedInterfaces": [{"url": "http://gateway/ace/", "protocolBinding": "JSONRPC"}],
                     "capabilities": {"streaming": False, "extendedAgentCard": False},
                     "skills": [{"id": "accounts", "name": "Accounts", "description": "Balances", "tags": ["bank"]}]}

    def new(self):
        return f"id-{next(self.ids)}"

    def add_item(self, kind, entity_id, source_id, definition, **more):
        item = {"id": self.new(), "entityId": entity_id, "sourceId": source_id, "kind": kind, "definition": definition, **more}
        self.items[item["id"]] = item
        return item

    def taken(self, entity_id):
        return any(i["entityId"] == entity_id for i in self.items.values())

    def of_kind(self, kind):
        return [i for i in self.items.values() if i["kind"] == kind]

    def named(self, kind, entity_id):
        return next((i for i in self.of_kind(kind) if i["entityId"] == f"{kind}.{entity_id}"), None)

    def children(self, parent):
        return [i for i in self.items.values() if i.get("parentId") == parent["id"]]

    def empty(self):
        return not (self.items or self.sources or self.providers or self.credentials)

    # --- Credentials and providers -------------------------------------------------------------

    def create_credential(self, call):
        if not call.json.get("allowedUsageSurfaces"):   # Gamma refuses a credential that may be used from nowhere
            return 400, {"message": "must not be empty", "path": "create.arg0.allowedUsageSurfaces"}
        credential = {"id": self.new(), **{k: call.json.get(k) for k in ("name", "strategy", "serviceId")}}
        self.credentials[credential["id"]] = credential
        self.secrets[credential["id"]] = call.json["credentials"]
        return 201, credential

    def delete_credential(self, call):
        return 204 if self.credentials.pop(last(call), None) else 404

    @staticmethod
    def model_as_stored(model):
        if "pricingOverride" in model:
            stamps = {"updatedAt": "2026-10-05T10:00:00Z", "updatedByName": "admin"}
            model = {**model, "pricingOverride": {**model["pricingOverride"], **stamps}}
        if "registryRef" in model:
            model = {**model, "summary": model.get("summary", "From the registry"), "pricing": {"inputPer1M": 0.3}}
        return {"taskType": "CHAT", **{k: v for k, v in model.items() if k != "registryRef"}}

    def create_provider(self, call):
        if not call.json.get("authProfileId"):   # Gamma asks for a credential on every provider
            return 400, {"message": "must not be null", "path": "create.arg0.authProfileId"}
        if any(p["slug"] == call.json["slug"] for p in self.providers.values()):
            return 409, {"message": "slug taken"}
        provider = {**call.json, "id": self.new(), "connection": {**call.json["connection"], "headerNames": []},
                    "models": [{"id": self.new(), **self.model_as_stored(m)} for m in call.json["models"]]}
        self.providers[provider["id"]] = provider
        return 201, provider

    def save_provider(self, call):
        provider = self.providers[last(call)]
        held = {m["name"]: m for m in provider["models"]}
        models = [held.get(m["name"]) or {"id": self.new(), **self.model_as_stored(m)} for m in call.json["models"]]
        provider.update({**call.json, "models": models, "connection": {**call.json["connection"], "headerNames": []}})
        return 200, provider

    def save_model(self, call):
        model = next(m for m in self.providers[call.path.split("/")[-3]]["models"] if m["id"] == last(call))
        model.update(self.model_as_stored(call.json))
        return 200, model

    def delete_provider(self, call):
        return 204 if self.providers.pop(last(call), None) else 404

    # --- Sources and items ----------------------------------------------------------------------

    def create_source(self, call):
        manual = next((s for s in self.sources.values() if s["sourceKind"] == "manual"), None)
        if call.json["sourceKind"] == "manual" and manual:
            return 200, manual
        source = {"id": self.new(), **call.json}
        self.sources[source["id"]] = source
        return 201, source

    def delete_source(self, call):
        self.sources.pop(last(call), None)
        self.items = {i: item for i, item in self.items.items() if item["sourceId"] != last(call)}
        return 204

    @staticmethod
    def card_as_stored(definition):
        capabilities = {k: v for k, v in definition.get("capabilities", {}).items() if k != "extendedAgentCard"}
        return {**definition, "capabilities": {**capabilities, "stateTransitionHistory": False}}

    def definition_as_stored(self, kind, definition):
        return self.card_as_stored(definition) if kind == "agent" else definition

    def create_item(self, kind):
        def create(call):
            entity_id = f"{kind}.{call.json['entityId']}"
            if self.taken(entity_id):
                return 500, {"message": "E11000 duplicate key"}
            more = {"metadata": call.json["metadata"]} if "metadata" in call.json else {}
            definition = self.definition_as_stored(kind, call.json["definition"])
            return 201, self.add_item(kind, entity_id, call.json["sourceId"], definition, **more)
        return create

    def replace_item(self, call):
        item = self.items[last(call)]
        item.update(sourceId=call.json["sourceId"], definition=self.definition_as_stored(item["kind"], call.json["definition"]))
        item.update({k: call.json[k] for k in ("metadata", "extensions") if k in call.json})
        return 200, item

    def delete_item(self, call):
        return 204 if self.items.pop(last(call), None) else 404

    # --- MCP servers ----------------------------------------------------------------------------

    def import_server(self, call):
        source_id, found = call.path.split("/")[-3], call.json["discoverResult"]
        server = next((i for i in self.of_kind("mcp-server") if i["sourceId"] == source_id), None)
        name = slug(found["serverInfo"]["name"])
        server = server or self.add_item("mcp-server", f"mcp-server.{name}", source_id, {}, slug=name)
        server["definition"] = {k: v for k, v in found.items() if k not in ("tools", "prompts", "resources")}
        server["extensions"] = {k: call.json[k] for k in ("transport", "endpoint", "authType")}
        for child in self.children(server):
            del self.items[child["id"]]
        for kind, key in (("mcp-tool", "tools"), ("mcp-prompt", "prompts"), ("mcp-resource", "resources")):
            for row in found[key]:
                self.add_item(kind, f"{kind}.{name}.{slug(row['name'])}", source_id, row, parentId=server["id"])
        return 200, {"server": server, "toolCount": len(found["tools"]), "promptCount": len(found["prompts"]),
                     "resourceCount": len(found["resources"])}

    def rpc(self, call):
        method, server = call.json["method"], self.mcp
        headers = {"Mcp-Session-Id": server["session"]} if server["session"] else {}
        if method == "notifications/initialized":
            return Response(202, None, headers)
        if method == "initialize":
            result = {"protocolVersion": server["protocol"], "instructions": "Accounts and cards",
                      "serverInfo": {"name": server["name"], "title": server["name"], "version": "1.0.0"},
                      "capabilities": {"tools": {}, "prompts": {}, "resources": {}}}
        else:
            key = method.split("/")[0]
            rows, start = server[key], int((call.json["params"] or {}).get("cursor") or 0)
            size = -(-len(rows) // server["pages"])
            more = {"nextCursor": str(start + size)} if start + size < len(rows) else {}
            result = {key: rows[start:start + size], **more}
        body = {"jsonrpc": "2.0", "id": call.json["id"], "result": result}
        if server["sse"]:
            stream = f"event: message\ndata: {json.dumps(body)}\n\n"
            return Response(200, stream, {**headers, "Content-Type": "text/event-stream"})
        return Response(200, body, headers)

    # --- Skills ---------------------------------------------------------------------------------

    def upload_skill(self, call):
        package = zipfile.ZipFile(io.BytesIO(call.files["file"][1]))
        text = package.read(next(n for n in package.namelist() if n.endswith("SKILL.md"))).decode()
        meta = yaml.safe_load(re.match(r"---\n(.*?)\n---", text, re.S).group(1))
        if self.taken(f"skill.{meta['name']}"):
            return 500, {"message": "E11000 duplicate key"}
        return 201, self.add_item("skill", f"skill.{meta['name']}", "builtin", meta)

    # --- Tools of an API ------------------------------------------------------------------------

    def generate(self, call):
        api_id = call.json["apiIds"][0]
        api = self.apis[api_id]
        tools = [{"toolDefinition": {"name": op, "description": f"Does {op}", "inputSchema": {"type": "object"},
                                     "outputSchema": {"type": "object", "properties": {"bodySchema": {}}}},
                  "gatewayMapping": {"http": {"method": "GET", "path": f"/{op}"}}} for op in api["operations"]]
        errors = [{"key": "x", "message": m} for m in api.get("errors", [])]
        return 200, {"data": [{"apiId": api_id, "tools": tools, "errors": errors}]}

    def import_tools(self, call):
        wanted = call.json["sources"][0]
        source = next((s for s in self.sources.values() if s["definition"].get("apiId") == wanted["apiId"]), None)
        if source is None:
            source = {"id": self.new(), "sourceKind": "apim.api", "definition": wanted}
            self.sources[source["id"]] = source
        created = []
        for tool in call.json["tools"]:
            entity_id = f"mcp-tool.{slug(wanted['apiName'])}-{slug(tool['definition']['name'])}"
            if self.taken(entity_id):
                return 500, {"message": "E11000 duplicate key"}
            created.append(self.add_item("mcp-tool", entity_id, source["id"], tool["definition"]))
        return 201, {"data": created}

    # --- The routes -----------------------------------------------------------------------------

    def routes(self):
        routes = {
            ("GET", SCOPE + "/auth-profiles"): lambda call: listing(self.credentials.values()),
            ("POST", SCOPE + "/auth-profiles"): self.create_credential,
            ("DELETE", path("/auth-profiles/*")): self.delete_credential,
            ("GET", SCOPE + "/providers"): lambda call: listing(self.providers.values()),
            ("POST", SCOPE + "/providers"): self.create_provider,
            ("PUT", path("/providers/*")): self.save_provider,
            ("PUT", path("/providers/*/models/*")): self.save_model,
            ("DELETE", path("/providers/*")): self.delete_provider,
            ("GET", SCOPE + "/catalog/sources"):
                lambda call: listing(s for s in self.sources.values() if s["sourceKind"] == call.params["sourceKind"]),
            ("POST", SCOPE + "/catalog/sources"): self.create_source,
            ("DELETE", path("/catalog/sources/*")): self.delete_source,
            ("POST", path("/catalog/sources/*/imports/mcp-server")): self.import_server,
            ("GET", SCOPE + "/catalog/items"):
                lambda call: listing(i for i in self.items.values() if i["sourceId"] == call.params["sourceId"]),
            ("POST", SCOPE + "/catalog/skills"): self.upload_skill,
            ("GET", SCOPE + "/catalog/api-tools/sources"):
                lambda call: listing({"id": i, "name": api["name"]} for i, api in self.apis.items()),
            ("POST", SCOPE + "/catalog/api-tools/_generateCapabilities"): self.generate,
            ("POST", SCOPE + "/catalog/mcp-tools/_importFromApis"): self.import_tools,
            ("DELETE", path("/mcp-proxies/*")): lambda call: (204, None),
            ("POST", MCP): self.rpc,
            ("GET", CARD): lambda call: (200, self.card),
        }
        collections = {"model": "models", "knowledge": "knowledge", "agent": "agents", "mcp-server": "mcp-servers",
                       "skill": "skills"}
        for kind, collection in collections.items():
            routes[("GET", f"{SCOPE}/catalog/{collection}")] = lambda call, kind=kind: listing(self.of_kind(kind))
            routes[("PUT", path(f"/catalog/{collection}/*"))] = self.replace_item
            routes[("DELETE", path(f"/catalog/{collection}/*"))] = self.delete_item
            if kind in ("model", "knowledge", "agent"):
                routes[("POST", f"{SCOPE}/catalog/{collection}")] = self.create_item(kind)
        return routes


class Catalog(Seed):
    """A seed run against the world, with the files a test writes."""

    def setUp(self):
        super().setUp()
        self.world = World()
        self.answer(self.world.routes())
        self.env(**ENV)

    def run_seed(self):
        """What the run changed in Gamma. Generating the tools of an API only computes, and a server is read from here."""
        self.platform.calls.clear()
        aim_catalog.seed()
        return [(c.method, c.path.replace(SCOPE, "")) for c in self.platform.calls
                if c.method != "GET" and c.path.startswith(SCOPE) and not c.path.endswith("_generateCapabilities")]

    def records(self, kind=None):
        return [x for x in common.created() if x["product"] == aim_catalog.SECTION and kind in (None, x["kind"])]

    def sent(self, method, pattern):
        return self.platform.sent(method, path(pattern))

    def real_files(self):
        """The seed files of Acme Bakery that this module reads."""
        for file in REAL.rglob("*"):
            if file.is_file() and file.relative_to(REAL).parts[0] in MINE:
                self.seed_files({f"aim/{file.relative_to(REAL).as_posix()}": file.read_text(encoding="utf-8")})


CREDENTIAL = """\
name: Gemini key
service: google
credentials: {type: api-key, headerName: x-goog-api-key, value: "${KEY:-placeholder}"}
"""
OLLAMA = """\
name: Ollama
slug: ollama
visibility: EVERYONE
credential: Ollama key
connection: {type: openai-compatible, targetUri: "${OLLAMA_URL}/v1"}
models:
  - {name: "qwen3:1.7b", taskType: CHAT, summary: Small, pricingOverride: {inputPer1M: 50, outputPer1M: 50, currency: USD}}
  - {name: "qwen3:0.6b", taskType: CHAT, pricingOverride: {inputPer1M: 20, outputPer1M: 20, currency: USD}}
"""
OLLAMA_KEY = """\
name: Ollama key
credentials: {type: bearer, token: ollama}
"""
GEMINI = """\
name: Gemini
slug: gemini
credential: Gemini key
connection: {type: gemini, targetUri: "https://generativelanguage.googleapis.com/v1beta"}
models:
  - {name: gemini-3.5-flash-lite, registryRef: {templateId: google, modelId: gemini-3.5-flash-lite}}
"""
MODEL = """\
entityId: qwen
source: Acme Bakery
definition: {name: Qwen, queryName: "qwen3:1.7b", contextWindow: 40960, pricing: {inputPer1M: 50, outputPer1M: 50}}
"""
SERVER = """\
name: Acme Bakery MCP
entityId: mcp-server.acme-bakery-mcp
endpoint: ${BAKERY_MCP_URL}
description: The bank
"""
DOCUMENT = """\
entityId: fees
source: Acme Bakery
name: Fees
description: What it costs
content: |
  # Fees
  4.90 per month
"""
AGENT_FILE = "entityId: ace\nsource: Acme Bakery\nurl: ${BAKERY_AGENT_URL}\n"
TOOLS = "api: Acme Bakery Open API\n"
SKILL = "---\nname: complaints\ndescription: Takes a complaint.\nversion: 1.0.0\n---\n\n# Complaints\n\nListen first.\n"
MANUAL_SOURCE = {"id": "theirs", "sourceKind": "manual", "definition": {"type": "manual", "name": "Inline documents"}}


class Credentials(Catalog):

    def setUp(self):
        super().setUp()
        self.seed_files({"aim/credentials/gemini.yaml": CREDENTIAL})

    def test_a_credential_is_created_with_its_secret_and_recorded(self):
        self.assertEqual(self.run_seed(), [("POST", "/auth-profiles")])
        [credential] = self.world.credentials.values()
        self.assertEqual((credential["name"], credential["strategy"], credential["serviceId"]),
                         ("Gemini key", "API_KEY", "google"))
        self.assertEqual(self.world.secrets[credential["id"]],
                         {"type": "api-key", "headerName": "x-goog-api-key", "value": "placeholder"})
        self.assertEqual([(x["kind"], x["label"], x["name"]) for x in self.records()],
                         [("credential", "credentials", "Gemini key")])

    def test_a_file_that_needs_a_setting_is_skipped_while_it_is_empty(self):
        self.seed_files({"aim/credentials/gemini.yaml": "needs: GEMINI_KEY\n" + CREDENTIAL})
        self.assertEqual(self.run_seed(), [])
        self.assertIn("Skipped Gemini key: GEMINI_KEY is not set", self.output())
        self.env(GEMINI_KEY="AIza-real-key")
        self.assertEqual(self.run_seed(), [("POST", "/auth-profiles")])

    def test_a_secret_from_the_environment_is_stored_and_shown_nowhere(self):
        self.env(KEY="AIza-real-key")
        self.run_seed()
        [credential] = self.world.credentials.values()
        self.assertEqual(self.world.secrets[credential["id"]]["value"], "AIza-real-key")
        self.assertNotIn("AIza-real-key", self.output())
        self.assertNotIn("AIza-real-key", self.state.read_text(encoding="utf-8"))

    def test_a_second_run_writes_nothing_and_does_not_send_the_secret_again(self):
        self.run_seed()
        self.env(KEY="a-new-value")
        self.assertEqual(self.run_seed(), [])
        [credential] = self.world.credentials.values()
        self.assertEqual(self.world.secrets[credential["id"]]["value"], "placeholder")

    def test_a_credential_of_that_name_that_the_seed_did_not_create_stops_the_seed_and_is_left_alone(self):
        theirs = {"id": "theirs", "name": "Gemini key", "strategy": "BEARER"}
        self.world.credentials["theirs"] = dict(theirs)
        message = "The credential 'Gemini key' exists and was not created by this playground"
        with self.assertRaisesRegex(common.SeedError, message):
            self.run_seed()
        self.assertEqual(self.platform.changes(), [])
        self.assertEqual(self.world.credentials, {"theirs": theirs})

    def test_a_file_without_what_the_seed_needs_says_what_is_missing(self):
        self.seed_files({"aim/credentials/gemini.yaml": "name: Key\n"})
        with self.assertRaisesRegex(common.SeedError, "gemini.yaml: credentials missing"):
            self.run_seed()


class Providers(Catalog):

    def setUp(self):
        super().setUp()
        self.seed_files({"aim/credentials/gemini.yaml": CREDENTIAL, "aim/credentials/ollama.yaml": OLLAMA_KEY,
                         "aim/providers/a-gemini.yaml": GEMINI, "aim/providers/ollama.yaml": OLLAMA})

    def provider(self, provider_slug):
        return next(p for p in self.world.providers.values() if p["slug"] == provider_slug)

    def test_a_provider_is_created_with_its_declared_models_and_prices_and_recorded(self):
        self.run_seed()
        ollama = self.provider("ollama")
        self.assertEqual((ollama["visibility"], ollama["connection"]["targetUri"]),
                         ("EVERYONE", "http://ollama:11434/v1"))
        self.assertEqual({m["name"]: m["pricingOverride"]["inputPer1M"] for m in ollama["models"]},
                         {"qwen3:1.7b": 50, "qwen3:0.6b": 20})
        self.assertEqual([(x["name"], x["label"]) for x in self.records("provider")],
                         [("Gemini", "AI providers"), ("Ollama", "AI providers")])

    def test_a_model_that_the_registry_prices_is_declared_without_a_price(self):
        self.run_seed()
        declared = self.sent("POST", "/providers")[0].json["models"]
        self.assertEqual(declared, [{"name": "gemini-3.5-flash-lite",
                                     "registryRef": {"templateId": "google", "modelId": "gemini-3.5-flash-lite"}}])

    def test_a_provider_that_has_a_credential_uses_the_one_the_seed_made(self):
        self.run_seed()
        self.assertEqual(self.provider("gemini")["authProfileId"], next(iter(self.world.credentials)))

    def test_a_provider_whose_credential_is_not_seeded_says_so(self):
        (common.SEED_DIR / "aim/credentials/gemini.yaml").unlink()
        with self.assertRaisesRegex(common.SeedError, "The credential 'Gemini key' is not there"):
            self.run_seed()

    def test_a_second_run_writes_nothing(self):
        self.run_seed()
        self.assertEqual(self.run_seed(), [])

    def test_models_that_have_no_id_are_left_as_they_are(self):
        self.run_seed()
        for provider in self.world.providers.values():
            for model in provider["models"]:
                model.pop("id")
                model.pop("pricingOverride", None)
        self.run_seed()
        self.assertEqual([m for m in self.platform.changes() if "/models/" in m[1] and m[0] == "PUT"], [])

    def test_a_changed_price_is_sent_for_that_model_alone(self):
        self.run_seed()
        self.seed_files({"aim/providers/ollama.yaml": OLLAMA.replace("outputPer1M: 50", "outputPer1M: 55")})
        self.assertEqual([method for method, _ in self.run_seed()], ["PUT"])
        [call] = self.sent("PUT", "/providers/*/models/*")
        self.assertEqual(call.json, {"pricingOverride": {"inputPer1M": 50, "outputPer1M": 55, "currency": "USD"}})
        self.assertEqual(self.run_seed(), [])

    def test_a_model_that_is_declared_no_more_is_dropped_by_saving_the_provider(self):
        self.run_seed()
        self.seed_files({"aim/providers/ollama.yaml": OLLAMA.split('  - {name: "qwen3:0.6b"')[0]})
        self.assertEqual([method for method, _ in self.run_seed()], ["PUT"])
        self.assertEqual([m["name"] for m in self.provider("ollama")["models"]], ["qwen3:1.7b"])

    def test_a_provider_that_changed_is_saved_with_all_its_models(self):
        self.run_seed()
        self.seed_files({"aim/providers/ollama.yaml": OLLAMA.replace("name: Ollama", "name: Ollama on the host")})
        self.assertEqual([method for method, _ in self.run_seed()], ["PUT"])
        [saved] = self.sent("PUT", "/providers/*")
        self.assertEqual(saved.json["name"], "Ollama on the host")
        self.assertEqual(len(saved.json["models"]), 2)

    def test_a_provider_with_the_slug_of_another_stops_the_seed_and_is_left_alone(self):
        self.world.providers["theirs"] = {"id": "theirs", "name": "My Ollama", "slug": "ollama", "models": []}
        message = r"The provider 'Ollama' \(ollama\) exists and was not created by this playground"
        with self.assertRaisesRegex(common.SeedError, message):
            self.run_seed()
        self.assertEqual([method for method, _ in self.platform.changes() if method in ("PUT", "DELETE")], [])
        self.assertEqual(self.world.providers["theirs"]["name"], "My Ollama")

    def test_a_provider_with_the_name_of_another_stops_the_seed_too(self):
        self.world.providers["theirs"] = {"id": "theirs", "name": "Ollama", "slug": "my-ollama", "models": []}
        with self.assertRaisesRegex(common.SeedError, "The provider 'Ollama'"):
            self.run_seed()


class Items(Catalog):
    """Models, documents and agents, which the one manual source holds."""

    def setUp(self):
        super().setUp()
        self.seed_files({"aim/models/qwen.yaml": MODEL, "aim/knowledge/fees.yaml": DOCUMENT,
                         "aim/agents/acme-bakery-ace.yaml": AGENT_FILE})

    def test_each_is_created_in_the_manual_source_which_the_seed_makes_once(self):
        self.assertEqual(self.run_seed(), [("POST", "/catalog/sources"), ("POST", "/catalog/models"),
                                           ("POST", "/catalog/knowledge"), ("POST", "/catalog/agents")])
        [source] = self.world.sources.values()
        self.assertEqual(source["definition"], {"type": "manual", "name": "Acme Bakery"})
        self.assertEqual({i["sourceId"] for i in self.world.items.values()}, {source["id"]})
        self.assertEqual([(x["kind"], x["label"], x["name"]) for x in self.records()],
                         [("model", "AI models", "Qwen"), ("knowledge", "knowledge documents", "Fees"),
                          ("agent", "A2A agents", "Ace")])

    def test_a_manual_source_that_exists_under_another_name_is_used_and_left_as_it_is(self):
        self.world.sources["theirs"] = dict(MANUAL_SOURCE)
        self.run_seed()
        self.assertEqual({i["sourceId"] for i in self.world.items.values()}, {"theirs"})
        self.assertEqual(self.world.named("knowledge", "fees")["metadata"]["sourceName"], "Inline documents")
        self.assertEqual(self.world.sources, {"theirs": MANUAL_SOURCE})

    def test_a_second_run_writes_nothing(self):
        self.run_seed()
        self.assertEqual(self.run_seed(), [])

    def test_an_agent_card_that_the_platform_stores_otherwise_is_not_sent_again(self):
        self.run_seed()
        stored = self.world.named("agent", "ace")["definition"]
        self.assertEqual(stored["capabilities"], {"streaming": False, "stateTransitionHistory": False})
        self.assertEqual(self.run_seed(), [])

    def test_a_document_that_changed_is_replaced_whole(self):
        self.run_seed()
        self.seed_files({"aim/knowledge/fees.yaml": DOCUMENT.replace("4.90", "5.90")})
        document = self.world.named("knowledge", "fees")
        self.assertEqual(self.run_seed(), [("PUT", f"/catalog/knowledge/{document['id']}")])
        [call] = self.sent("PUT", "/catalog/knowledge/*")
        content = call.json["definition"]["content"]
        self.assertIn("5.90", content)
        self.assertEqual(call.json["metadata"]["inlineBytes"], str(len(content.encode())))
        self.assertEqual(self.run_seed(), [])

    def test_a_document_has_what_the_console_reads_to_fill_its_row(self):
        self.run_seed()
        self.assertEqual(self.world.named("knowledge", "fees")["metadata"], {
            "family": "documents", "providerLabel": "Inline", "sourceName": "Acme Bakery", "origin": "inline",
            "resourceShape": "individual", "documentType": "Individual document", "technicalLabel": "fees",
            "delivery": "direct-resource", "location": "Inline content",
            "inlineBytes": str(len("# Fees\n4.90 per month\n"))})

    def test_an_agent_is_registered_from_its_card_with_the_address_of_its_interface(self):
        self.run_seed()
        definition = self.world.named("agent", "ace")["definition"]
        self.assertEqual((definition["name"], definition["url"]), ("Ace", "http://gateway/ace/"))
        self.assertNotIn("supportedInterfaces", definition)

    def test_a_card_of_an_older_version_keeps_its_url(self):
        old = {"name": "Old", "url": "http://old/a2a", "protocolVersion": "0.3.0"}
        self.assertEqual(aim_catalog.catalog_card(old), {"name": "Old", "url": "http://old/a2a"})

    def test_the_json_rpc_interface_of_a_card_is_its_address_and_its_security_requirements_its_security(self):
        card = {"name": "N", "securityRequirements": [{"bearer": []}],
                "supportedInterfaces": [{"url": "http://x/rest", "protocolBinding": "HTTP+JSON"},
                                        {"url": "http://x/rpc", "protocolBinding": "jsonrpc"}]}
        self.assertEqual(aim_catalog.catalog_card(card),
                         {"name": "N", "url": "http://x/rpc", "security": [{"bearer": []}]})

    def test_an_agent_that_does_not_answer_stops_the_seed_saying_whose_card(self):
        self.platform.add({("GET", CARD): 404})
        with self.assertRaisesRegex(common.SeedError, "The agent card of http://bakery-agent:8000: GET /.well-known"):
            self.run_seed()

    def test_an_item_with_the_entity_id_of_another_stops_the_seed_and_is_left_alone(self):
        for kind, entity_id in (("model", "qwen"), ("knowledge", "fees"), ("agent", "ace")):
            with self.subTest(kind=kind):
                self.world.__init__()
                common.save_created([])
                theirs = self.world.add_item(kind, f"{kind}.{entity_id}", "s", {"name": "Theirs"})
                self.platform.calls.clear()
                message = f"The {aim_catalog.noun(kind)} '{kind}.{entity_id}' exists and was not created by this playground"
                with self.assertRaisesRegex(common.SeedError, message):
                    aim_catalog.seed()
                self.assertEqual(theirs["definition"], {"name": "Theirs"})
                self.assertEqual([m for m, _ in self.platform.changes() if m in ("PUT", "DELETE")], [])

    def test_an_entity_id_is_a_slug(self):
        self.seed_files({"aim/models/qwen.yaml": MODEL.replace("entityId: qwen", "entityId: Qwen 1.7B")})
        with self.assertRaisesRegex(common.SeedError, "Qwen 1.7B: an entity id is made of lower case letters"):
            self.run_seed()


class Discovery(Catalog):
    """What the seed asks an MCP server, since the platform does not discover a private address."""

    def setUp(self):
        super().setUp()
        self.seed_files({"aim/mcp-servers/bank.yaml": SERVER})

    def asked(self):
        return [(c.json["method"], c.headers.get("MCP-Protocol-Version"), c.headers.get("Mcp-Session-Id"))
                for c in self.platform.sent("POST", MCP)]

    def methods_asked(self):
        return [method for method, _, _ in self.asked()]

    def test_the_server_is_asked_what_it_offers_and_the_import_gets_the_result(self):
        self.run_seed()
        self.assertEqual(self.methods_asked(),
                         ["initialize", "notifications/initialized", "tools/list", "prompts/list", "resources/list"])
        [call] = self.sent("POST", "/catalog/sources/*/imports/mcp-server")
        self.assertEqual({k: call.json[k] for k in ("endpoint", "transport", "authType")},
                         {"endpoint": MCP, "transport": "http", "authType": "none"})
        self.assertEqual(call.json["discoverResult"], {
            "serverInfo": {"name": "Acme Bakery MCP", "version": "1.0.0"}, "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}, "prompts": {}, "resources": {}}, "instructions": "Accounts and cards",
            "tools": [{"name": "get_balance", "description": "Balance", "inputSchema": {"type": "object"},
                       "annotations": {"readOnlyHint": True}},
                      {"name": "transfer_funds", "description": "Transfer", "inputSchema": {"type": "object"}}],
            "prompts": [{"name": "dispute_charge", "description": "Dispute"}],
            "resources": [{"uri": "acme://policies/fees", "name": "fees", "mimeType": "text/markdown"}]})

    def test_the_server_is_asked_in_the_version_it_answers_and_in_its_session(self):
        self.world.mcp.update(protocol="2025-06-18", session="s-1")
        self.run_seed()
        self.assertEqual(self.asked(), [
            ("initialize", "2025-11-25", None), ("notifications/initialized", "2025-06-18", "s-1"),
            ("tools/list", "2025-06-18", "s-1"), ("prompts/list", "2025-06-18", "s-1"),
            ("resources/list", "2025-06-18", "s-1")])

    def test_a_list_in_pages_is_read_to_its_end(self):
        self.world.mcp["pages"] = 2
        self.run_seed()
        self.assertEqual(self.methods_asked().count("tools/list"), 2)
        self.assertEqual(len(self.world.of_kind("mcp-tool")), 2)

    def test_an_answer_in_an_event_stream_is_read(self):
        self.world.mcp["sse"] = True
        self.run_seed()
        self.assertEqual(len(self.world.of_kind("mcp-tool")), 2)

    def test_what_a_server_does_not_declare_is_not_asked(self):
        answer = self.world.rpc

        def tools_only(call):
            reply = answer(call)
            if call.json["method"] == "initialize":
                reply.body["result"]["capabilities"] = {"tools": {}}
            return reply
        self.platform.add({("POST", MCP): tools_only})
        self.run_seed()
        self.assertEqual(self.methods_asked(), ["initialize", "notifications/initialized", "tools/list"])

    def test_a_server_that_does_not_answer_stops_the_seed_before_any_write(self):
        self.platform.add({("POST", MCP): 502})
        with self.assertRaisesRegex(common.SeedError, "MCP initialize at http://bakery-mcp:8000: HTTP 502"):
            self.run_seed()
        self.assertEqual([c for c in self.platform.calls if c.path.startswith(SCOPE) and c.method != "GET"], [])

    def test_a_server_that_is_not_there_stops_the_seed(self):
        self.platform.add({("POST", MCP): requests.ConnectionError("refused")})
        with self.assertRaisesRegex(common.SeedError, "MCP initialize at http://bakery-mcp:8000: refused"):
            self.run_seed()

    def test_an_answer_without_a_result_stops_the_seed(self):
        error = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "bad"}}
        self.platform.add({("POST", MCP): (200, error)})
        with self.assertRaisesRegex(common.SeedError, "no result"):
            self.run_seed()


class McpServers(Catalog):

    def setUp(self):
        super().setUp()
        self.seed_files({"aim/mcp-servers/bank.yaml": SERVER})

    def server(self):
        return self.world.named("mcp-server", "acme-bakery-mcp")

    def test_the_server_is_imported_under_its_own_source_described_and_recorded_with_its_source(self):
        changes = self.run_seed()
        server = self.server()
        self.assertEqual(changes, [("POST", "/catalog/sources"),
                                   ("POST", f"/catalog/sources/{server['sourceId']}/imports/mcp-server"),
                                   ("PUT", f"/catalog/mcp-servers/{server['id']}")])
        self.assertEqual((server["entityId"], server["extensions"]["endpoint"], server["extensions"]["description"]),
                         ("mcp-server.acme-bakery-mcp", MCP, "The bank"))
        self.assertEqual(self.world.sources[server["sourceId"]]["definition"], {"type": "mcp-gateway", "name": MCP})
        self.assertEqual([len(self.world.of_kind(k)) for k in ("mcp-tool", "mcp-prompt", "mcp-resource")], [2, 1, 1])
        [record] = self.records("mcp-server")
        self.assertEqual((record["id"], record["source"], record["label"]),
                         (server["id"], server["sourceId"], "MCP servers"))
        self.assertIn("2 tools, 1 prompts, 1 resources", self.output())

    def test_a_second_run_asks_the_server_again_and_writes_nothing(self):
        self.run_seed()
        self.assertEqual(self.run_seed(), [])
        self.assertEqual(len(self.platform.sent("POST", MCP)), 5)

    def test_a_server_whose_tools_changed_is_imported_again_and_described_again(self):
        self.run_seed()
        server_id = self.server()["id"]
        self.world.mcp["tools"][0]["description"] = "Balance of an account"
        self.assertEqual([method for method, _ in self.run_seed()], ["POST", "PUT"])
        self.assertEqual(self.server()["id"], server_id)
        self.assertEqual(self.server()["extensions"]["description"], "The bank")
        self.assertEqual(len(self.world.sources), 1)
        self.assertEqual(self.run_seed(), [])

    def test_a_server_that_moved_is_imported_again_and_the_stored_endpoint_follows(self):
        self.run_seed()
        server_id, source_id = self.server()["id"], self.server()["sourceId"]
        self.platform.add({("POST", MOVED): self.world.rpc})
        self.env(BAKERY_MCP_URL=MOVED)
        self.assertEqual(self.run_seed(), [("POST", f"/catalog/sources/{source_id}/imports/mcp-server"),
                                           ("PUT", f"/catalog/mcp-servers/{server_id}")])
        self.assertEqual((self.server()["id"], self.server()["sourceId"], self.server()["extensions"]["endpoint"]),
                         (server_id, source_id, MOVED))
        self.assertEqual(len(self.world.sources), 1)
        self.assertEqual(self.run_seed(), [])

    def test_a_description_that_changed_is_all_that_is_sent(self):
        self.run_seed()
        self.seed_files({"aim/mcp-servers/bank.yaml": SERVER.replace("The bank", "ACME's bank")})
        self.assertEqual(self.run_seed(), [("PUT", f"/catalog/mcp-servers/{self.server()['id']}")])
        self.assertEqual(self.server()["extensions"]["description"], "ACME's bank")

    def test_a_server_that_gives_itself_another_name_stops_the_seed_naming_both_entity_ids(self):
        self.world.mcp["name"] = "Acme Bakery"
        message = "calls itself 'Acme Bakery', so its entity id is mcp-server.acme-bakery and not mcp-server.acme-bakery-mcp"
        with self.assertRaisesRegex(common.SeedError, message):
            self.run_seed()
        self.assertEqual(len(self.records("mcp-server")), 1)   # it was imported: removing the playground deletes it

    def test_a_server_of_that_entity_id_that_the_seed_did_not_create_stops_the_seed_and_is_left_alone(self):
        theirs = self.world.add_item("mcp-server", "mcp-server.acme-bakery-mcp", "s", {}, extensions={"endpoint": "http://x"})
        with self.assertRaisesRegex(common.SeedError, "The MCP server 'Acme Bakery MCP'"):
            self.run_seed()
        self.assertEqual(self.sent("POST", "/catalog/sources/*/imports/mcp-server"), [])
        self.assertEqual([method for method, _ in self.platform.changes() if method in ("PUT", "DELETE")], [])
        self.assertEqual(theirs["extensions"], {"endpoint": "http://x"})

    def test_a_server_at_that_endpoint_that_the_seed_did_not_create_stops_the_seed_too(self):
        self.world.add_item("mcp-server", "mcp-server.mine", "s", {}, extensions={"endpoint": MCP})
        with self.assertRaisesRegex(common.SeedError, r"The MCP server 'Acme Bakery MCP' \(http://bakery-mcp:8000\) exists"):
            self.run_seed()


class Skills(Catalog):

    def setUp(self):
        super().setUp()
        self.seed_files({"aim/skills/complaints/SKILL.md": SKILL,
                         "aim/skills/complaints/references/escalation.md": "Hand over money.\n"})

    def package(self):
        upload = self.sent("POST", "/catalog/skills")[-1]
        return zipfile.ZipFile(io.BytesIO(upload.files["file"][1]))

    def test_the_skill_is_uploaded_as_a_zip_of_its_folder_and_recorded(self):
        self.assertEqual(self.run_seed(), [("POST", "/catalog/skills")])
        package = self.package()
        self.assertIsNone(package.testzip())
        self.assertEqual(sorted(package.namelist()), ["complaints/SKILL.md", "complaints/references/escalation.md"])
        text = package.read("complaints/SKILL.md").decode()
        self.assertEqual(yaml.safe_load(text.split("---")[1]),
                         {"name": "complaints", "description": "Takes a complaint.", "version": "1.0.0"})
        self.assertIn("# Complaints", text)
        self.assertEqual([(x["label"], x["name"]) for x in self.records("skill")], [("skills", "complaints")])

    def test_it_is_sent_as_a_file_and_not_as_json(self):
        self.run_seed()
        [call] = self.sent("POST", "/catalog/skills")
        self.assertEqual(call.files["file"][0], "complaints.zip")
        self.assertNotIn("Content-Type", call.headers)

    def test_the_same_files_make_the_same_zip(self):
        files = {"SKILL.md": "a", "b.md": "b"}
        self.assertEqual(aim_catalog.skill_package("s", files), aim_catalog.skill_package("s", dict(files)))

    def test_a_second_run_writes_nothing(self):
        self.run_seed()
        self.assertEqual(self.run_seed(), [])

    def test_a_file_that_changed_deletes_the_skill_and_uploads_it_again(self):
        self.run_seed()
        old = self.world.named("skill", "complaints")["id"]
        self.seed_files({"aim/skills/complaints/references/escalation.md": "Hand over money and cards.\n"})
        self.assertEqual(self.run_seed(), [("DELETE", f"/catalog/skills/{old}"), ("POST", "/catalog/skills")])
        self.assertIn("and cards", self.package().read("complaints/references/escalation.md").decode())
        self.assertEqual(len(self.world.of_kind("skill")), 1)
        self.assertEqual(self.run_seed(), [])

    def test_a_skill_without_a_front_matter_with_a_name_and_a_description_is_refused_before_any_call(self):
        for text in ("# No front matter\n", "---\nname: x\n---\n", "---\ndescription: y\n---\n"):
            with self.subTest(text=text):
                self.seed_files({"aim/skills/complaints/SKILL.md": text})
                with self.assertRaisesRegex(common.SeedError, "complaints/SKILL.md must start with a front matter"):
                    aim_catalog.seed()

    def test_a_skill_of_that_name_that_the_seed_did_not_create_stops_the_seed_and_is_left_alone(self):
        theirs = self.world.add_item("skill", "skill.complaints", "builtin", {})
        message = "The skill 'complaints' exists and was not created by this playground"
        with self.assertRaisesRegex(common.SeedError, message):
            self.run_seed()
        self.assertEqual(self.platform.changes(), [])
        self.assertIn(theirs["id"], self.world.items)


class ApiTools(Catalog):

    def setUp(self):
        super().setUp()
        self.world.apis["api-1"] = {"name": "Acme Bakery Open API", "operations": ["listBranches", "getBranch"]}
        self.seed_files({"aim/api-tools/open.yaml": TOOLS})

    def test_the_tools_of_the_api_are_imported_and_recorded_by_their_source(self):
        self.assertEqual(self.run_seed(), [("POST", "/catalog/mcp-tools/_importFromApis")])
        self.assertEqual(sorted(i["entityId"] for i in self.world.of_kind("mcp-tool")),
                         ["mcp-tool.acme-bakery-open-api-getbranch", "mcp-tool.acme-bakery-open-api-listbranches"])
        [source] = self.world.sources.values()
        self.assertEqual([(x["id"], x["label"], x["name"]) for x in self.records("api-tools")],
                         [(source["id"], "API tools", "Acme Bakery Open API")])

    def test_a_second_run_writes_nothing(self):
        self.run_seed()
        self.assertEqual(self.run_seed(), [])

    def test_the_tools_are_imported_without_the_output_schema_a_strict_client_would_demand_structured_content_for(self):
        self.run_seed()
        [call] = self.sent("POST", "/catalog/mcp-tools/_importFromApis")
        self.assertEqual(len(call.json["tools"]), 2)
        for tool in call.json["tools"]:
            self.assertEqual(sorted(tool["definition"]), ["description", "inputSchema", "name"])
        self.assertTrue(all("outputSchema" not in i["definition"] for i in self.world.of_kind("mcp-tool")))

    def test_the_record_keeps_the_fingerprint_of_what_was_imported_and_tools_that_differ_from_it_are_replaced(self):
        self.run_seed()
        [call] = self.sent("POST", "/catalog/mcp-tools/_importFromApis")
        [record] = self.records("api-tools")
        self.assertEqual(record["digest"], aim_catalog.fingerprint(call.json["tools"]))
        common.record(aim_catalog.SECTION, "api-tools", "API tools", record["name"], record["id"],
                      digest=aim_catalog.fingerprint([{"definition": {"outputSchema": {}}}]))
        self.assertEqual([m for m, p in self.run_seed()], ["DELETE", "POST"])

    def test_the_studios_the_seed_made_go_before_tools_that_changed_so_that_none_holds_them(self):
        self.run_seed()
        common.record("aim-proxies", "studio", "MCP studios", "Acme Bakery Toolkit", "studio-1", digest="x")
        common.record("aim-proxies", "mcp-proxy", "MCP proxy APIs", "Acme Bakery MCP", "proxy-1")
        old = next(iter(self.world.sources))
        self.world.apis["api-1"]["operations"].append("listRates")
        self.assertEqual(self.run_seed(), [("DELETE", "/mcp-proxies/studio-1"), ("DELETE", f"/catalog/sources/{old}"),
                                           ("POST", "/catalog/mcp-tools/_importFromApis")])
        self.assertIn("Studio 'Acme Bakery Toolkit' deleted: it is made again from the new tools", self.output())

    def test_a_studio_is_left_alone_while_the_tools_are_up_to_date(self):
        self.run_seed()
        common.record("aim-proxies", "studio", "MCP studios", "Acme Bakery Toolkit", "studio-1", digest="x")
        self.assertEqual(self.run_seed(), [])

    def test_tools_that_changed_are_deleted_with_their_source_and_imported_again(self):
        self.run_seed()
        old = next(iter(self.world.sources))
        self.world.apis["api-1"]["operations"].append("listRates")
        self.assertEqual(self.run_seed(), [("DELETE", f"/catalog/sources/{old}"),
                                           ("POST", "/catalog/mcp-tools/_importFromApis")])
        self.assertEqual(len(self.world.of_kind("mcp-tool")), 3)
        self.assertEqual([x["id"] for x in self.records("api-tools")], list(self.world.sources))

    def test_an_api_that_cannot_give_tools_stops_the_seed_saying_what_it_needs(self):
        self.world.apis.clear()
        message = "No API named 'Acme Bakery Open API' can give tools: it must be a v4 HTTP proxy API"
        with self.assertRaisesRegex(common.SeedError, message):
            self.run_seed()

    def test_an_api_that_the_catalog_lists_a_moment_after_it_was_made_is_waited_for(self):
        asked = []

        def late(call):   # the catalog lists nothing the first two times it is asked
            asked.append(1)
            return listing({"id": i, "name": api["name"]} for i, api in self.world.apis.items() if len(asked) > 2)
        self.platform.add({("GET", SCOPE + "/catalog/api-tools/sources"): late})
        self.run_seed()
        self.assertGreaterEqual(len(asked), 3)
        self.assertGreater(len(self.clock.sleeps), 0, "it waited")
        self.assertEqual(len(self.world.of_kind("mcp-tool")), 2)

    def test_an_openapi_page_that_gives_errors_or_no_operation_stops_the_seed(self):
        self.world.apis["api-1"]["errors"] = ["no servers"]
        with self.assertRaisesRegex(common.SeedError, "gives no tools: no servers"):
            self.run_seed()
        self.world.apis["api-1"].update(errors=[], operations=[])
        with self.assertRaisesRegex(common.SeedError, "gives no tools: it has no operation"):
            self.run_seed()

    def test_tools_of_that_api_that_the_seed_did_not_create_stop_the_seed_and_are_left_alone(self):
        theirs = {"id": "theirs", "sourceKind": "apim.api",
                  "definition": {"apiId": "api-1", "apiName": "Acme Bakery Open API"}}
        self.world.sources["theirs"] = theirs
        message = "The tools of the API 'Acme Bakery Open API' exists and was not created"
        with self.assertRaisesRegex(common.SeedError, message):
            self.run_seed()
        self.assertEqual([m for m, p in self.platform.changes() if not p.endswith("_generateCapabilities")], [])
        self.assertEqual(self.world.sources, {"theirs": theirs})

    def test_a_playground_without_the_file_has_no_tools_of_an_api_and_does_not_look_for_the_api(self):
        (common.SEED_DIR / "aim/api-tools/open.yaml").unlink()
        self.seed_files({"aim/models/qwen.yaml": MODEL})
        self.run_seed()
        self.assertEqual(self.records("api-tools"), [])
        self.assertEqual(self.sent("GET", "/catalog/api-tools/sources"), [])


class Removal(Catalog):
    """What a removal deletes: what the records say, dependents first, and nothing else."""

    def seeded(self):
        self.real_files()
        self.env(GEMINI_API_KEY="AIza-real-key")
        self.world.apis["api-1"] = {"name": "Acme Bakery Open API", "operations": ["listBranches"]}
        aim_catalog.seed()
        self.platform.calls.clear()

    def deleted(self):
        return [re.sub(r"/[^/]+$", "", p.replace(SCOPE, "")) for m, p in self.platform.changes() if m == "DELETE"]

    def test_it_deletes_what_the_seed_created_in_the_order_of_its_kinds_and_the_source_it_made(self):
        self.seeded()
        records = common.created()
        aim_catalog.remove(records)
        kinds = [x["kind"] for x in common.owned(records, aim_catalog.SECTION, aim_catalog.ORDER)]
        self.assertEqual(kinds, ["api-tools", "agent", "skill", "knowledge", "knowledge", "mcp-server", "model", "model",
                                 "provider", "provider", "credential", "credential"])
        self.assertEqual(self.deleted(), [
            "/catalog/sources", "/catalog/agents", "/catalog/skills", "/catalog/knowledge", "/catalog/knowledge",
            "/catalog/sources", "/catalog/models", "/catalog/models", "/providers", "/providers", "/auth-profiles", "/auth-profiles",
            "/catalog/sources"])
        self.assertTrue(self.world.empty())

    def test_a_server_is_deleted_through_its_source_which_takes_its_tools_with_it(self):
        self.seeded()
        server = self.world.named("mcp-server", "acme-bakery-mcp")
        aim_catalog.remove(self.records("mcp-server"))
        self.assertEqual([p.replace(SCOPE, "") for _, p in self.platform.changes()],
                         [f"/catalog/sources/{server['sourceId']}"])
        self.assertEqual(self.world.children(server), [])
        self.assertEqual(len(self.world.of_kind("mcp-tool")), 1)   # the tool of the API is another source's

    def test_what_is_gone_already_counts_as_deleted(self):
        self.seeded()
        records = common.created()
        self.world.__init__()
        aim_catalog.remove(records)
        self.assertIn("✓ Deleted credential 'Acme Bakery Gemini key'", self.output())

    def test_what_the_seed_did_not_create_is_left_alone(self):
        self.seeded()
        self.world.credentials["theirs"] = {"id": "theirs", "name": "Theirs", "strategy": "BEARER"}
        self.world.providers["theirs"] = {"id": "theirs", "name": "Theirs", "slug": "theirs", "models": []}
        source = next(s for s in self.world.sources.values() if s["sourceKind"] == "manual")
        self.world.items["theirs"] = {"id": "theirs", "entityId": "knowledge.theirs", "sourceId": source["id"],
                                      "kind": "knowledge", "definition": {}}
        others = [{"product": "apim", "kind": "api", "name": "x", "id": "x", "label": ""},
                  {"product": aim_catalog.SECTION, "kind": "other", "name": "y", "id": "y", "label": ""}]
        aim_catalog.remove(common.created() + others)
        self.assertEqual((list(self.world.credentials), list(self.world.providers), list(self.world.items)),
                         (["theirs"], ["theirs"], ["theirs"]))
        self.assertIn(source["id"], self.world.sources)   # it still holds an item of someone else

    def test_a_manual_source_the_seed_did_not_name_is_never_deleted(self):
        self.world.sources["theirs"] = dict(MANUAL_SOURCE)
        self.seeded()
        aim_catalog.remove(common.created())
        self.assertEqual(list(self.world.sources), ["theirs"])

    def test_a_refused_delete_stops_the_removal_with_its_reason(self):
        self.seeded()
        self.platform.add({("DELETE", path("/providers/*")): (409, {"message": "provider.in_use"})})
        with self.assertRaisesRegex(common.SeedError, "HTTP 409.*provider.in_use"):
            aim_catalog.remove(common.created())

    def test_it_connects_only_when_it_has_something_to_delete(self):
        aim_catalog.remove([])
        aim_catalog.remove([{"product": "apim", "kind": "api", "name": "x", "id": "x", "label": ""}])
        self.assertEqual(self.platform.calls, [])


class Playground(Catalog):
    """The seed files of Acme Bakery, which are what this module is for."""

    def setUp(self):
        super().setUp()
        self.real_files()
        self.env(GEMINI_API_KEY="AIza-real-key")   # Gamma tests a Gemini provider with it: no key, no provider
        operations = ["listBranches", "getBranch", "listRates", "getRate"]
        self.world.apis["api-1"] = {"name": "Acme Bakery Open API", "operations": operations}

    def test_the_seed_makes_every_object_of_the_catalog_with_an_acme_bakery_name(self):
        self.run_seed()
        self.assertEqual(sorted(c["name"] for c in self.world.credentials.values()),
                         ["Acme Bakery Gemini key", "Acme Bakery Ollama key"])
        self.assertEqual(sorted((p["name"], p["slug"]) for p in self.world.providers.values()),
                         [("Acme Bakery Gemini", "acme-bakery-gemini"), ("Acme Bakery Ollama", "acme-bakery-ollama")])
        made = sorted(i["entityId"] for i in self.world.items.values() if "parentId" not in i)
        self.assertEqual(made, [
            "agent.acme-bakery-ace", "knowledge.acme-bakery-card-security-policy", "knowledge.acme-bakery-fee-schedule",
            "mcp-server.acme-bakery-mcp", "mcp-tool.acme-bakery-open-api-getbranch", "mcp-tool.acme-bakery-open-api-getrate",
            "mcp-tool.acme-bakery-open-api-listbranches", "mcp-tool.acme-bakery-open-api-listrates",
            "model.acme-bakery-qwen3-0-6b", "model.acme-bakery-qwen3-1-7b", "skill.acme-bakery-complaint-handling"])
        self.assertEqual({x["kind"] for x in self.records()}, set(aim_catalog.ORDER))

    def test_the_demo_prices_are_on_the_models_of_the_provider(self):
        self.run_seed()
        prices = {p["slug"]: {m["name"]: m.get("pricingOverride") for m in p["models"]}
                  for p in self.world.providers.values()}
        ollama = {name: (price["inputPer1M"], price["outputPer1M"]) for name, price in prices["acme-bakery-ollama"].items()}
        self.assertEqual(ollama, {"qwen3:1.7b": (50, 50), "qwen3:0.6b": (20, 20)})
        self.assertEqual({name: price is not None for name, price in prices["acme-bakery-gemini"].items()},
                         {"gemini-3.5-flash-lite": False, "gemini-3.8-flash": True})

    def test_the_catalog_names_the_server_so_that_the_access_policies_find_its_tools(self):
        self.run_seed()
        server = self.world.named("mcp-server", "acme-bakery-mcp")
        self.assertEqual(server["slug"], "acme-bakery-mcp")
        self.assertEqual(sorted(i["entityId"] for i in self.world.children(server) if i["kind"] == "mcp-tool"),
                         ["mcp-tool.acme-bakery-mcp.get_balance", "mcp-tool.acme-bakery-mcp.transfer_funds"])

    def test_the_documents_say_what_the_policy_resources_of_the_bakery_mcp_server_say(self):
        self.run_seed()
        texts = policy_texts()
        for entity_id, constant in (("acme-bakery-fee-schedule", "FEES"), ("acme-bakery-card-security-policy", "CARD_SECURITY")):
            self.assertEqual(self.world.named("knowledge", entity_id)["definition"]["content"], texts[constant], entity_id)

    def test_the_gemini_key_is_the_one_of_the_environment(self):
        self.run_seed()
        self.assertEqual(next(iter(self.world.secrets.values()))["value"], "AIza-real-key")

    def test_without_a_key_there_is_no_gemini_provider_to_test_and_the_rest_is_seeded(self):
        self.env(GEMINI_API_KEY="")
        self.run_seed()
        self.assertEqual([c["name"] for c in self.world.credentials.values()], ["Acme Bakery Ollama key"])
        self.assertEqual([p["slug"] for p in self.world.providers.values()], ["acme-bakery-ollama"])
        self.assertIn("Skipped Acme Bakery Gemini: GEMINI_API_KEY is not set", self.output())

    def test_a_second_run_writes_nothing(self):
        self.run_seed()
        self.assertEqual(self.run_seed(), [])

    def test_a_removal_leaves_nothing_and_a_seed_again_makes_it_all(self):
        self.run_seed()
        count = len(self.world.items)
        aim_catalog.remove(common.created())
        self.assertTrue(self.world.empty())
        common.save_created([])
        self.run_seed()
        self.assertEqual(len(self.world.items), count)

    def test_the_log_says_what_it_did_in_the_words_of_the_lab_ui(self):
        self.run_seed()
        for line in ("✓ Credential created: Acme Bakery Gemini key", "✓ AI provider created: Acme Bakery Ollama",
                     "✓ AI model created: Acme Bakery Qwen3 1.7B", "✓ Knowledge document created: Acme Bakery fee schedule",
                     "✓ MCP server imported: Acme Bakery MCP (2 tools, 1 prompts, 1 resources)",
                     "✓ Skill uploaded: acme-bakery-complaint-handling", "✓ A2A agent created: Ace",
                     "✓ API tool imported: Acme Bakery Open API (4 tools)"):
            self.assertIn("[aim-catalog] " + line, self.output())


class Guard(Catalog):

    def test_a_playground_that_seeds_none_of_its_folders_needs_no_setting_and_makes_no_call(self):
        self.seed_files({"aim/workspaces/team.yaml": "name: Team\n", "authz/policies.yaml": "x: 1\n"})
        with mock.patch.dict(os.environ, {}, clear=True):
            aim_catalog.seed()
        self.assertEqual(self.platform.calls, [])
        self.assertEqual(self.output(), "")

    def test_what_it_records_is_in_its_order_with_plural_labels_that_the_lab_ui_can_singularise(self):
        self.assertEqual(sorted(aim_catalog.LABELS), sorted(aim_catalog.ORDER))
        for label in aim_catalog.LABELS.values():
            self.assertTrue(label.endswith("s") and not label.endswith("ies"), label)


if __name__ == "__main__":
    unittest.main()
