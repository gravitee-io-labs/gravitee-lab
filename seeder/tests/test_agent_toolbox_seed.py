"""The personal playground "agent-toolbox", read as one: its seed files, the data and the OpenAPI documents of its apps, and
its compose file say the same thing. A name, an id, a scope, an alias or a number that differs between two of them is a demo
that breaks in front of people: a tool that no policy permits, a scope the sign-in refuses, a customer whose region is not the
one the policies know.

Skipped on a machine that does not have the playground (it is git-ignored). The checks that read the compose file are skipped
until it exists. The API exports are the output of apps/harbor-api/export_seed.py, which this test runs again and compares."""

import ast
import functools
import importlib.util
import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

import yaml

from lab_seeder import common
from lab_seeder.testkit import PERSONAL, compose_value, literal_addresses, seed_environment, seed_paths, seed_service, unset_variables

PLAYGROUND = PERSONAL / "agent-toolbox"
SEED, APP, COPILOT, COMPOSE = (PLAYGROUND / "seed", PLAYGROUND / "apps" / "harbor-api", PLAYGROUND / "apps" / "harbor-copilot",
                               PLAYGROUND / "compose.yml")
PRESENT = unittest.skipUnless(PLAYGROUND.is_dir(), "the personal playground agent-toolbox is not on this machine")
WITH_COMPOSE = unittest.skipUnless(COMPOSE.is_file(), "the compose file of agent-toolbox is not written yet")

KEYS = ("customers", "orders", "catalog")   # the three Harbor APIs: the names of their OpenAPI documents and of their exports
METHODS = ("get", "put", "post", "delete", "patch")
SNAKE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
ACTION = re.compile(r'Action::"([^"]+)"')
AGENT = re.compile(r"<agent:([^>]+)>")
STATEMENT = re.compile(r"\b(permit|forbid)\s*\(([^)]*)\)", re.S)
CONTEXT = re.compile(r"\bcontext(?:\s+has\s+|\.)([A-Za-z_]\w*)")
PRINCIPAL = re.compile(r"\bprincipal(?:\s+has\s+|\.)([A-Za-z_]\w*)")
ARGUMENT = re.compile(r"\.arguments\.([A-Za-z_]\w*)")
ATTRIBUTE = re.compile(r"context\.attributes\['([^']+)'\]")
CLAIM = re.compile(r"context\.attributes\['jwt\.claims'\]\['([^']+)'\]")
USER_FIELD = re.compile(r"\['additionalInformation'\]\['([^']+)'\]")
SECRET_KEY = re.compile(r"(password|passwd|passphrase|secret|api_?key|token)$", re.I)
SECRET_HEADER = re.compile(r"secret|password|token|api-?key", re.I)
FROM_A_VARIABLE = "dummy-"   # what read() puts in place of a variable that nothing sets
SIGN_IN_SCOPES = {"openid", "profile", "email"}
HIDDEN_BY_DESIGN = {"updateShippingAddress", "deleteCustomer", "updatePrice"}   # the operations that never become tools


# --- Reading the playground ---------------------------------------------------------------

def variables_of(text):
    """The variables a file uses, each with a dummy value that is easy to recognize."""
    return {m.group(2): FROM_A_VARIABLE + m.group(2).lower() for m in common.VARIABLE.finditer(text) if not m.group(1)}


def read(path, environment=None):
    """A seed file as the seed reads it: its variables replaced by `environment`, and by a dummy value where that has none."""
    values = {**variables_of(Path(path).read_text(encoding="utf-8")), **{k: v for k, v in (environment or {}).items() if v}}
    with mock.patch.dict(os.environ, values, clear=True):
        return common.load(path)


@functools.lru_cache(maxsize=None)
def module(path):
    """A Python file of the playground, imported by its path: its own folder is not on the path of the tests."""
    spec = importlib.util.spec_from_file_location("agent_toolbox_" + Path(path).stem, path)
    imported = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(imported)
    return imported


@functools.lru_cache(maxsize=None)
def openapi(key):
    """The text of the OpenAPI document of an API, as written, and the document."""
    text = (APP / "openapi" / f"{key}.json").read_bytes().decode("utf-8")
    return text, json.loads(text)


@functools.lru_cache(maxsize=None)
def export(key):
    """The export of an API as it is committed: the JSON of the file, its variables not replaced."""
    return json.loads((SEED / "apim" / "apis" / f"harbor-{key}.json").read_text(encoding="utf-8"))


def api_names():
    return {export(key)["api"]["name"]: key for key in KEYS}


def operations(document):
    """operation id -> (method, path, operation) of an OpenAPI document."""
    return {operation["operationId"]: (method, path, operation) for path, item in document["paths"].items()
            for method, operation in item.items() if method in METHODS}


def resolved(document, node):
    """A node, or what its local $ref points at."""
    while "$ref" in node:
        node = functools.reduce(lambda found, part: found[part], node["$ref"].lstrip("#/").split("/"), document)
    return node


def inputs_of(document, operation):
    """name -> (type, required) of what a call to an operation can say: its path and query parameters, and the properties of
    its JSON body."""
    found = {}
    for parameter in operation.get("parameters", []):
        parameter = resolved(document, parameter)
        if parameter["in"] in ("path", "query"):
            found[parameter["name"]] = (resolved(document, parameter["schema"]).get("type"), bool(parameter.get("required")))
    body = operation.get("requestBody")
    if body:
        schema = resolved(document, body["content"]["application/json"]["schema"])
        for name, prop in schema.get("properties", {}).items():
            found[name] = (resolved(document, prop).get("type"), name in schema.get("required", []))
    return found


def tools():
    """(api name, operation id, override or None) of every operation the api-tools files import."""
    found = []
    for path in sorted((SEED / "aim" / "api-tools").glob("*.yaml")):
        data = read(path)
        for entry in data["operations"]:
            found.append((data["api"], entry, None) if isinstance(entry, str) else (data["api"], entry["id"], entry))
    return found


def studio():
    return read(SEED / "aim" / "studios" / "customer-care.yaml")


def aliases():
    """alias -> (api name, operation id) of the tools of the studio."""
    return {t["alias"]: (t["api"], t["tool"]) for t in studio()["tools"]}


def policies():
    """(file name, policy file) of seed/authz/policies, in order."""
    return [(path.name, read(path)) for path in sorted((SEED / "authz" / "policies").glob("*.yaml"))]


def statements(policy):
    """(effect, actions) of each permit and forbid of a policy text."""
    return [(effect, ACTION.findall(head)) for effect, head in STATEMENT.findall(policy)]


def written_secrets(node, where=""):
    """Where a secret is written out: a key that names one, or a header called like one, whose text came from no variable."""
    found = []
    if isinstance(node, dict):
        header = SECRET_HEADER.search(str(node.get("name", ""))) and "value" in node
        for key, value in node.items():
            here = f"{where}/{key}"
            if isinstance(value, str) and value and FROM_A_VARIABLE not in value and (
                    SECRET_KEY.search(str(key)) or (header and key == "value")):
                found.append(here)
            found += written_secrets(value, here)
    elif isinstance(node, list):
        for number, item in enumerate(node):
            found += written_secrets(item, f"{where}/{number}")
    return found


def compose():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def seed_settings():
    """What the seed service of the compose file sets, as the seed reads it."""
    return seed_environment(seed_service(COMPOSE))


def service_with(setting, value):
    """The (name, service) of the compose file whose environment has this setting."""
    return next((name, service) for name, service in compose()["services"].items()
                if isinstance(service.get("environment"), dict) and service["environment"].get(setting) == value)


# --- The checks, on files made for it -------------------------------------------------------

class Helpers(unittest.TestCase):
    """What the lint is made of finds what it is meant to find."""

    def test_an_alias_is_lower_case_words_joined_by_single_underscores(self):
        for alias in ("get_customer", "search_products", "list_orders", "tool2", "a_b_c"):
            self.assertRegex(alias, SNAKE)
        for alias in ("getCustomer", "Get_customer", "get__customer", "_get", "get_", "get-customer", "2get", ""):
            self.assertNotRegex(alias, SNAKE)

    def test_the_actions_of_a_policy_are_those_of_its_permits_and_forbids(self):
        policy = ('@id("a") permit (principal, action in [Action::"get_x", Action::"list_x"], resource is MCPTool) when { true };\n'
                  '@id("b") forbid (principal, action == Action::"drop_x", resource is MCPTool) when { principal has team };')
        self.assertEqual(statements(policy), [("permit", ["get_x", "list_x"]), ("forbid", ["drop_x"])])

    def test_the_names_a_policy_reads_are_found_whichever_way_it_asks(self):
        policy = "when { context has agent && context.order.customer.region == principal.region && principal has team }"
        self.assertEqual(set(CONTEXT.findall(policy)), {"agent", "order"})
        self.assertEqual(set(PRINCIPAL.findall(policy)), {"region", "team"})

    def test_the_inputs_of_an_operation_are_its_parameters_and_its_body_with_references_followed(self):
        document = {"components": {"parameters": {"Id": {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}},
                                   "schemas": {"Body": {"type": "object", "required": ["amount"],
                                                        "properties": {"amount": {"type": "integer"}, "note": {"type": "string"}}}}}}
        operation = {"parameters": [{"$ref": "#/components/parameters/Id"},
                                    {"name": "q", "in": "query", "schema": {"type": "string"}},
                                    {"name": "X-Trace", "in": "header", "schema": {"type": "string"}}],
                     "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Body"}}}}}
        self.assertEqual(inputs_of(document, operation), {"id": ("string", True), "q": ("string", False),
                                                          "amount": ("integer", True), "note": ("string", False)})

    def test_a_secret_is_found_where_it_is_written_and_not_where_a_variable_gave_it(self):
        node = {"clientSecret": "care-copilot-secret", "password": FROM_A_VARIABLE + "x", "apiKeyHeader": "X-Gravitee-Api-Key",
                "tokenCustomClaims": [{"claimName": "region"}],
                "flows": [{"addHeaders": [{"name": "X-Gateway-Secret", "value": "hunter2"},
                                          {"name": "X-Gateway-Secret", "value": FROM_A_VARIABLE + "y"},
                                          {"name": "X-Region", "value": "EU"}]}]}
        self.assertEqual(written_secrets(node), ["/clientSecret", "/flows/0/addHeaders/0/value"])

    def test_a_variable_the_files_use_gets_a_dummy_value_and_an_escaped_one_does_not(self):
        self.assertEqual(variables_of("${A} $${B} ${C:-x}"), {"A": FROM_A_VARIABLE + "a", "C": FROM_A_VARIABLE + "c"})


# --- The exports of the APIs ----------------------------------------------------------------

@PRESENT
class Exports(unittest.TestCase):

    def test_each_export_is_what_export_seed_writes_from_the_openapi_documents(self):
        exporter = module(APP / "export_seed.py")
        for api in exporter.APIS:
            with self.subTest(api=api.name):
                committed = (SEED / "apim" / "apis" / f"harbor-{api.key}.json").read_bytes()
                self.assertEqual(committed, exporter.text_of(api).encode("utf-8"), "run python3 apps/harbor-api/export_seed.py")

    def test_the_openapi_page_of_each_export_is_the_document_of_the_app(self):
        for key in KEYS:
            with self.subTest(api=key):
                text, document = openapi(key)
                pages = [p for p in export(key)["pages"] if p["type"] == "SWAGGER"]
                self.assertEqual([p["name"] for p in pages], ["OpenAPI"])
                self.assertEqual(pages[0]["content"], text)
                self.assertTrue(pages[0]["published"])
                self.assertEqual(document["info"]["title"], export(key)["api"]["name"])
                self.assertNotIn("${", text, "the seeder would take it for a variable")

    def test_the_getting_started_page_tells_the_path_and_the_limits_of_the_plan(self):
        for key in KEYS:
            with self.subTest(api=key):
                [page] = [p for p in export(key)["pages"] if p["type"] == "MARKDOWN"]
                [path] = export(key)["api"]["listeners"][0]["paths"]
                steps = {s["policy"]: s["configuration"] for s in export(key)["plans"][0]["flows"][0]["request"]}
                self.assertTrue(page["published"])
                self.assertIn(f"`{path['path']}`", page["content"])
                self.assertIn(f"{steps['rate-limit']['rate']['limit']} calls per minute", page["content"])
                self.assertIn(f"{steps['quota']['quota']['limit']:,} per day", page["content"])
                self.assertEqual(re.findall(r"\$\{(\w+)\}", page["content"]), ["APIM_GATEWAY_URL"], "its one variable: the gateway")

    @WITH_COMPOSE
    def test_the_getting_started_page_shows_the_gateway_as_the_seed_service_sets_it(self):
        gateway = seed_settings()["APIM_GATEWAY_URL"]
        for key in KEYS:
            with self.subTest(api=key):
                shown = read(SEED / "apim" / "apis" / f"harbor-{key}.json", seed_settings())
                [page] = [p for p in shown["pages"] if p["type"] == "MARKDOWN"]
                self.assertIn(f"'{gateway}{shown['api']['listeners'][0]['paths'][0]['path']}", page["content"])

    def test_the_apis_have_their_own_ids_paths_and_plans(self):
        apis = [export(key) for key in KEYS]
        for field, values in (("api id", [a["api"]["id"] for a in apis]), ("plan id", [a["plans"][0]["id"] for a in apis]),
                              ("name", [a["api"]["name"] for a in apis]),
                              ("path", [a["api"]["listeners"][0]["paths"][0]["path"] for a in apis])):
            with self.subTest(field=field):
                self.assertEqual(len(set(values)), len(KEYS), values)
        for export_ in apis:
            with self.subTest(api=export_["api"]["name"]):
                [plan] = export_["plans"]
                self.assertEqual((plan["name"], plan["security"]["type"], plan["status"], plan["validation"]),
                                 ("Standard", "API_KEY", "PUBLISHED", "AUTO"))
                self.assertEqual([s["policy"] for s in plan["flows"][0]["request"]], ["rate-limit", "quota"])
                self.assertEqual((export_["api"]["type"], export_["api"]["definitionVersion"], export_["api"]["state"],
                                  export_["api"]["lifecycleState"], export_["api"]["visibility"]),
                                 ("PROXY", "V4", "STARTED", "PUBLISHED", "PUBLIC"))
                self.assertTrue(export_["api"]["analytics"]["enabled"])

    def test_the_description_of_a_step_says_its_limit(self):
        for key in KEYS:
            with self.subTest(api=key):
                for step in export(key)["plans"][0]["flows"][0]["request"]:
                    limit = step["configuration"].get("rate") or step["configuration"].get("quota")
                    self.assertTrue(step["description"].startswith(f"{limit['limit']:,} calls per"), step["description"])

    @WITH_COMPOSE
    def test_the_gateway_adds_the_secret_the_backends_require(self):
        settings = seed_settings()
        for key in KEYS:
            with self.subTest(api=key):
                [step] = [s for s in export(key)["api"]["flows"][0]["request"] if s["policy"] == "transform-headers"]
                [header] = step["configuration"]["addHeaders"]
                self.assertEqual(header["name"], "X-Gateway-Secret")
                self.assertRegex(header["value"], r"^\$\{\w+\}$", "the secret is a setting, never written out")
                name = header["value"][2:-1]
                _, backend = service_with("API", key)
                self.assertEqual(compose_value(backend["environment"]["GATEWAY_SECRET"]), settings[name])
                self.assertIn(name, backend["environment"]["GATEWAY_SECRET"], "one setting for the gateway and the backend")

    @WITH_COMPOSE
    def test_each_export_reaches_the_backend_that_serves_its_api(self):
        settings = seed_settings()
        for key in KEYS:
            with self.subTest(api=key):
                target = export(key)["api"]["endpointGroups"][0]["endpoints"][0]["configuration"]["target"]
                self.assertRegex(target, r"^\$\{\w+\}$")
                address = urlsplit(settings[target[2:-1]])
                name, backend = service_with("API", key)
                self.assertEqual((address.hostname, address.port), (name, 8000), "the port the container listens on")
                self.assertIn(name, backend["environment"]["ALLOWED_HOSTS"].split(","), "else the backend answers 421")


# --- The data of Harbor and the entities of the policies ------------------------------------

@PRESENT
class Entities(unittest.TestCase):

    def entities(self, kind):
        return {e["id"]: e["attributes"] for e in read(SEED / "authz" / "entities.yaml")["entities"] if e["type"] == kind}

    def test_the_entities_are_customers_and_orders_and_nothing_else(self):
        self.assertEqual({e["type"] for e in read(SEED / "authz" / "entities.yaml")["entities"]}, {"Customer", "Order"})

    def test_the_customers_are_those_of_the_harbor_data_with_their_region_and_tier(self):
        data = module(APP / "data.py")
        customers = self.entities("Customer")
        self.assertEqual(set(customers), {c["id"] for c in data.CUSTOMERS})
        for customer in data.CUSTOMERS:
            with self.subTest(customer=customer["id"]):
                attributes = customers[customer["id"]]
                self.assertEqual((attributes["region"], attributes["tier"], attributes["_displayName"]),
                                 (customer["region"], customer["tier"], customer["name"]))

    def test_each_order_is_that_of_the_customer_the_harbor_data_says(self):
        data = module(APP / "data.py")
        orders = self.entities("Order")
        self.assertEqual(set(orders), {o[0] for o in data.ORDERS})
        for order_id, customer_id, *_ in data.ORDERS:
            with self.subTest(order=order_id):
                self.assertEqual(orders[order_id]["customer"], {"ref": ["Customer", customer_id]})
                self.assertEqual(orders[order_id]["_displayName"], order_id)

    def test_the_regions_of_the_entities_are_those_the_apps_know(self):
        data = module(APP / "data.py")
        self.assertEqual({a["region"] for a in self.entities("Customer").values()}, set(data.REGIONS))

    def test_the_schema_declares_the_types_and_attributes_the_entities_use(self):
        declared = {}
        for name, body in re.findall(r"entity\s+(\w+)\s*(?:\{(.*?)\})?\s*;", read(SEED / "authz" / "schema.yaml")["schema"], re.S):
            declared[name] = {attribute: not optional for attribute, optional in re.findall(r"(\w+)(\??)\s*:", body)}
        for kind in ("Customer", "Order"):
            for entity_id, attributes in self.entities(kind).items():
                with self.subTest(entity=f"{kind} {entity_id}"):
                    self.assertIn(kind, declared)
                    used = set(attributes) - {"_displayName"}
                    self.assertLessEqual(used, set(declared[kind]), "an attribute the schema does not declare")
                    self.assertLessEqual({a for a, required in declared[kind].items() if required}, used, "a required attribute")


# --- The tools --------------------------------------------------------------------------------

@PRESENT
class Tools(unittest.TestCase):

    def test_every_api_tools_file_names_an_api_of_the_exports(self):
        names = api_names()
        files = [read(p)["api"] for p in sorted((SEED / "aim" / "api-tools").glob("*.yaml"))]
        self.assertEqual(sorted(files), sorted(names))

    def test_every_operation_of_an_api_tools_file_exists_in_the_openapi_of_that_api(self):
        names = api_names()
        for api, operation_id, _ in tools():
            with self.subTest(api=api, operation=operation_id):
                self.assertIn(operation_id, operations(openapi(names[api])[1]))

    def test_an_operation_is_listed_once(self):
        listed = [(api, operation_id) for api, operation_id, _ in tools()]
        self.assertEqual(len(listed), len(set(listed)))
        self.assertEqual(len({operation_id for _, operation_id, _ in tools()}), len(listed), "an operation id is a scope: one of each")

    def test_thirteen_operations_make_ten_tools_and_the_three_left_out_are_those_the_story_says(self):
        every = {i for key in KEYS for i in operations(openapi(key)[1])}
        imported = {operation_id for _, operation_id, _ in tools()}
        self.assertEqual((len(every), len(imported)), (13, 10))
        self.assertEqual(every - imported, HIDDEN_BY_DESIGN)

    def test_a_tailored_tool_takes_the_arguments_and_carries_the_hints_of_its_operation(self):
        names = api_names()
        tailored = [(api, i, o) for api, i, o in tools() if o]
        self.assertTrue(tailored, "createRefund is tailored")
        for api, operation_id, override in tailored:
            with self.subTest(operation=operation_id):
                document = openapi(names[api])[1]
                _, _, operation = operations(document)[operation_id]
                inputs = inputs_of(document, operation)
                schema = override["inputSchema"]
                self.assertEqual(set(schema["properties"]), set(inputs), "the arguments of the call")
                self.assertEqual(set(schema.get("required", [])), {n for n, (_, required) in inputs.items() if required})
                for name, prop in schema["properties"].items():
                    self.assertEqual(prop["type"], inputs[name][0], name)
                    self.assertTrue(prop.get("description"), f"{name} says what it is")
                for hint, value in override["annotations"].items():
                    self.assertEqual(value, operation["x-mcp-" + hint], hint)
                self.assertTrue(override["description"].strip())


@PRESENT
class Studio(unittest.TestCase):

    def test_every_tool_names_an_api_and_an_operation_of_the_api_tools_files(self):
        imported = {(api, operation_id) for api, operation_id, _ in tools()}
        for tool in studio()["tools"]:
            with self.subTest(tool=tool["alias"]):
                self.assertIn((tool["api"], tool["tool"]), imported)

    def test_every_imported_operation_is_a_tool(self):
        self.assertEqual({(api, i) for api, i, _ in tools()}, set(aliases().values()), "ten tools, one for each operation")
        self.assertEqual(len(studio()["tools"]), len(aliases()))

    def test_an_alias_is_unique_snake_case(self):
        listed = [t["alias"] for t in studio()["tools"]]
        self.assertEqual(len(listed), len(set(listed)))
        for alias in listed:
            with self.subTest(alias=alias):
                self.assertRegex(alias, SNAKE)

    def test_the_studio_calls_with_a_key_each_api_it_has_a_tool_of(self):
        upstream = {u["api"]: u for u in studio()["upstreamAuth"]}
        self.assertEqual(set(upstream), {t["api"] for t in studio()["tools"]})
        for api, auth in upstream.items():
            with self.subTest(api=api):
                self.assertEqual(auth["type"], "api-key")
                self.assertEqual(auth["apiKey"], upstream["Harbor Customers API"]["apiKey"], "the one key of the MCP server")

    def test_the_arguments_the_check_reads_are_arguments_of_some_tool(self):
        names = api_names()
        known = set()
        for api, operation_id in aliases().values():
            document = openapi(names[api])[1]
            override = next((o for a, i, o in tools() if (a, i) == (api, operation_id) and o), None)
            known |= set(override["inputSchema"]["properties"]) if override else set(inputs_of(document, operations(document)[operation_id][2]))
        for attribute in studio()["authorization"]["attributes"]:
            with self.subTest(attribute=attribute["name"]):
                self.assertLessEqual(set(ARGUMENT.findall(attribute["value"])), known)

    def test_what_the_context_reads_is_an_attribute_the_studio_makes(self):
        authorization = studio()["authorization"]
        made = {a["name"] for a in authorization["attributes"]}
        for name, expression in authorization["context"].items():
            with self.subTest(context=name):
                self.assertLessEqual(set(ATTRIBUTE.findall(expression)), made)

    def test_the_claims_the_check_reads_are_claims_of_the_token_made_from_the_fields_of_the_people(self):
        claims = {c["claimName"]: c["claimValue"] for c in read(SEED / "am" / "applications" / "care-copilot.yaml")["oauth"]["tokenCustomClaims"]}
        fields = [set(u["additionalInformation"]) for u in read(SEED / "am" / "domain.yaml")["users"]]
        for name, expression in studio()["authorization"]["subject"].items():
            with self.subTest(subject=name):
                self.assertLessEqual(set(CLAIM.findall(expression)), set(claims))
        for name, expression in claims.items():
            if USER_FIELD.search(expression):
                with self.subTest(claim=name):
                    for person in fields:
                        self.assertIn(USER_FIELD.search(expression).group(1), person, "every person has the field the claim reads")


# --- The policies and the approval rule ---------------------------------------------------------

@PRESENT
class Policies(unittest.TestCase):

    def text(self):
        return [(name, p["policy"]) for name, p in policies()]

    def test_every_action_of_a_policy_is_an_alias_of_the_studio(self):
        for name, policy in self.text():
            with self.subTest(file=name):
                found = ACTION.findall(policy)
                self.assertTrue(found)
                self.assertLessEqual(set(found), set(aliases()))

    def test_every_tool_of_the_studio_is_permitted_by_a_policy(self):
        permitted = {a for _, policy in self.text() for effect, actions in statements(policy) if effect == "permit" for a in actions}
        self.assertEqual(set(aliases()) - permitted, set(), "a tool that no policy permits is denied to everyone")

    def test_a_policy_reads_only_what_the_check_gives_it(self):
        authorization = studio()["authorization"]
        for name, policy in self.text():
            with self.subTest(file=name):
                self.assertLessEqual(set(CONTEXT.findall(policy)), set(authorization["context"]) | {"agent"}, "agent is the check's own")
                self.assertLessEqual(set(PRINCIPAL.findall(policy)), set(authorization["subject"]))

    def test_every_policy_is_deployed_and_named_once(self):
        files = policies()
        ids = [i for _, p in files for i in re.findall(r'@id\("([^"]+)"\)', p["policy"])]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len({p["name"] for _, p in files}), len(files))
        self.assertEqual({p["status"] for _, p in files}, {"DEPLOYED"})

    def test_the_agent_of_the_policies_is_the_agent_of_the_seed(self):
        client = read(SEED / "am" / "applications" / "care-copilot.yaml")["clientId"]
        self.assertEqual(read(SEED / "aim" / "agents" / "care-copilot.yaml")["identity"]["clientId"], client)
        for name, policy in self.text():
            with self.subTest(file=name):
                self.assertLessEqual(set(AGENT.findall(policy)), {client})

    def test_the_approval_rule_is_on_a_tool_and_an_argument_of_the_studio(self):
        names = api_names()
        for rule in read(SEED / "aim" / "governance" / "approvals.yaml")["rules"]:
            with self.subTest(rule=rule["name"]):
                # The pattern lists the name a client calls (the alias) and the tool's name in the catalog, which shows the rule there too
                [alias] = [name for name in rule["tool"].split("|") if name in aliases()]
                api, operation_id = aliases()[alias]
                self.assertIn(operation_id, rule["tool"].split("|"), "the tool page of the catalog counts the rule")
                document = openapi(names[api])[1]
                override = next((o for a, i, o in tools() if (a, i) == (api, operation_id) and o), None)
                arguments = set(override["inputSchema"]["properties"]) if override else set(inputs_of(document, operations(document)[operation_id][2]))
                for condition in rule["when"]:
                    self.assertIn(condition["path"], arguments)

    def test_a_refund_has_the_three_zones_the_story_tells(self):
        [rule] = read(SEED / "aim" / "governance" / "approvals.yaml")["rules"]
        [above] = rule["when"]
        [forbids] = [p for _, p in policies() if "context.amount >" in p["policy"]]
        [permits] = [p for _, p in policies() if "context.amount <=" in p["policy"]]
        cap = int(re.search(r"context\.amount > (\d+)", forbids["policy"]).group(1))
        allowed = int(re.search(r"context\.amount <= (\d+)", permits["policy"]).group(1))
        self.assertEqual((int(above["value"]), above["operator"], cap, allowed), (100, "GT", 1000, 1000))
        self.assertIn(f"EUR {int(above['value'])}", rule["name"], "the zones are said in the names the inbox and the decisions show")
        self.assertIn(f"EUR {cap:,}", forbids["name"])


# --- The scopes and the people ------------------------------------------------------------------

@PRESENT
class Scopes(unittest.TestCase):

    def imported(self):
        return [operation_id for _, operation_id, _ in tools()]

    def test_the_domain_has_one_scope_for_each_operation_that_is_a_tool_and_no_other(self):
        keys = [s["key"] for s in read(SEED / "am" / "domain.yaml")["scopes"]]
        self.assertEqual(sorted(keys), sorted(self.imported()))

    def test_a_scope_is_described_to_the_person_who_consents(self):
        for scope in read(SEED / "am" / "domain.yaml")["scopes"]:
            with self.subTest(scope=scope["key"]):
                self.assertTrue(scope["name"].strip() and scope["description"].strip())

    def test_the_application_may_ask_for_the_scopes_of_the_tools_and_those_of_the_sign_in(self):
        scopes = read(SEED / "am" / "applications" / "care-copilot.yaml")["scopes"]
        self.assertEqual(len(scopes), len(set(scopes)))
        self.assertEqual(set(scopes), set(self.imported()) | SIGN_IN_SCOPES)

    def test_harbor_care_asks_for_those_scopes(self):
        found = {}
        for node in ast.parse((COPILOT / "am.py").read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ("OPENID_SCOPES", "TOOL_SCOPES"):
                found[node.targets[0].id] = ast.literal_eval(node.value)
        self.assertEqual(set(found), {"OPENID_SCOPES", "TOOL_SCOPES"}, "apps/harbor-copilot/am.py no longer defines them: update this test")
        self.assertEqual(set(found["OPENID_SCOPES"]), SIGN_IN_SCOPES)
        self.assertEqual(sorted(found["TOOL_SCOPES"]), sorted(self.imported()))

    def test_the_people_of_harbor_care_are_the_users_of_the_domain(self):
        people = module(COPILOT / "people.py").PERSONAS.values()
        users = {u["username"]: u for u in read(SEED / "am" / "domain.yaml")["users"]}
        self.assertEqual({p.username for p in people}, set(users))
        for person in people:
            with self.subTest(person=person.name):
                self.assertEqual({"name": person.name, "region": person.region, "team": person.team}, users[person.username]["additionalInformation"])
                self.assertEqual(users[person.username]["email"], person.username)


# --- Names that two files must agree on -------------------------------------------------------------

@PRESENT
class Wiring(unittest.TestCase):

    def test_the_domain_of_the_link_the_jwks_and_the_sign_in_is_the_domain_the_seed_makes(self):
        domain = read(SEED / "am" / "domain.yaml")["name"]
        self.assertEqual(read(SEED / "am" / "gamma-link.yaml")["domain"], domain)
        self.assertRegex(studio()["access"]["agents"]["jwksUrl"], rf"/{domain}/oidc/\.well-known/jwks\.json$")

    def test_the_agent_of_the_catalog_the_governance_and_the_application_are_one(self):
        agent = read(SEED / "aim" / "agents" / "care-copilot.yaml")
        client = read(SEED / "am" / "applications" / "care-copilot.yaml")["clientId"]
        self.assertEqual(agent["identity"]["clientId"], client)
        self.assertEqual([c["agent"] for c in read(SEED / "aim" / "governance" / "compliance.yaml")["agents"]], [agent["entityId"]])

    def test_the_agent_has_access_to_the_plan_of_the_agents_of_the_studio(self):
        [access] = read(SEED / "aim" / "agents" / "care-copilot.yaml")["access"]
        self.assertEqual(access["studio"], studio()["name"])
        self.assertIn(access["plan"], [plan["name"] for plan in studio()["access"].values()])
        self.assertEqual(studio()["access"]["agents"]["type"], "JWT", "the plan that names the agent keeps the subscription required")

    def test_the_subscriptions_are_to_the_plans_of_the_exports_by_an_application_the_seed_makes(self):
        applications = {read(p)["name"] for p in (SEED / "apim" / "applications").glob("*.yaml")}
        subscriptions = [s for p in (SEED / "apim" / "subscriptions").glob("*.yaml") for s in read(p)["subscriptions"]]
        upstream = {u["api"]: u["apiKey"] for u in studio()["upstreamAuth"]}
        self.assertEqual(sorted(s["api"] for s in subscriptions), sorted(api_names()), "one subscription for each API")
        for subscription in subscriptions:
            with self.subTest(api=subscription["api"]):
                self.assertIn(subscription["application"], applications)
                self.assertEqual(subscription["plan"], export(api_names()[subscription["api"]])["plans"][0]["name"])
                self.assertEqual(subscription["apiKey"], upstream[subscription["api"]], "the key the MCP server sends")
        self.assertTrue(read(SEED / "apim" / "settings.yaml")["plan"]["security"]["customApiKey"]["enabled"], "else the key is generated")

    @WITH_COMPOSE
    def test_the_accounts_of_the_page_are_the_people_and_the_key_of_the_seed(self):
        settings = seed_settings()
        accounts = compose()["x-gravitee"]["accounts"]
        users = {u["username"] for u in read(SEED / "am" / "domain.yaml")["users"]}
        self.assertEqual({a["username"] for a in accounts if "username" in a}, users)
        for account in accounts:
            if "username" in account:
                self.assertEqual(compose_value(account["password"]), settings["AGENT_TOOLBOX_DEMO_PASSWORD"], account["username"])
        [key] = [a["key"] for a in accounts if "key" in a]
        self.assertEqual(compose_value(key), settings["AGENT_TOOLBOX_KEY"])

    @WITH_COMPOSE
    def test_harbor_care_and_the_seed_agree_on_the_agent_the_domain_and_the_addresses(self):
        settings, app = seed_settings(), compose()["services"]["harbor-copilot"]["environment"]
        domain = read(SEED / "am" / "domain.yaml")["name"]
        context_path = studio()["contextPath"]
        client = read(SEED / "am" / "applications" / "care-copilot.yaml")
        self.assertEqual(app["CARE_COPILOT_CLIENT_ID"], client["clientId"])
        self.assertEqual(compose_value(app["CARE_COPILOT_CLIENT_SECRET"]), settings["AGENT_TOOLBOX_CLIENT_SECRET"])
        self.assertEqual(compose_value(app["DEMO_PASSWORD"]), settings["AGENT_TOOLBOX_DEMO_PASSWORD"])
        self.assertEqual(app["PUBLIC_URL"], settings["CARE_COPILOT_URL"], "the redirect URI of the application is built from it")
        self.assertEqual(read(SEED / "am" / "applications" / "care-copilot.yaml", settings)["redirectUris"],
                         [f"{app['PUBLIC_URL']}/auth/callback"])
        self.assertEqual(app["CARD_URL"], settings["CARE_COPILOT_CARD_URL"])
        self.assertIn(urlsplit(settings["CARE_COPILOT_CARD_URL"]).hostname, app["ALLOWED_HOSTS"].split(","), "else the seed gets a 421")
        self.assertEqual(app["AM_AUTHORIZE_URL"], f"{settings['AM_PUBLIC_URL']}/{domain}/oauth/authorize")
        self.assertEqual(app["AM_TOKEN_URL"], f"{settings['AM_GATEWAY_URL']}/{domain}/oauth/token")
        self.assertEqual(urlsplit(app["MCP_URL"]).path, context_path)
        self.assertEqual(urlsplit(app["MCP_RESOURCE"]).path, context_path)
        self.assertEqual(app["MCP_URL"], f"{settings['GATEWAY_INTERNAL_URL']}{context_path}")
        self.assertEqual(app["MCP_RESOURCE"], f"{settings['APIM_GATEWAY_URL']}{context_path}", "the audience the token is made for")


# --- The files themselves -----------------------------------------------------------------------------

@PRESENT
class Files(unittest.TestCase):

    def parsed(self):
        return [p for p in seed_paths(PLAYGROUND) if p.suffix in (".yaml", ".yml", ".json")]

    def test_there_are_seed_files_to_check(self):
        self.assertGreaterEqual(len(self.parsed()), 20)

    def test_every_seed_file_parses_once_its_variables_are_replaced_with_dummy_values(self):
        for path in self.parsed():
            with self.subTest(file=str(path.relative_to(PLAYGROUND))):
                self.assertTrue(read(path))

    @WITH_COMPOSE
    def test_every_seed_file_parses_with_the_values_of_the_seed_service(self):
        for path in self.parsed():
            with self.subTest(file=str(path.relative_to(PLAYGROUND))):
                self.assertTrue(read(path, seed_settings()))

    @WITH_COMPOSE
    def test_every_variable_a_seed_file_uses_is_set_by_the_seed_service(self):
        for path in seed_paths(PLAYGROUND):
            with self.subTest(file=str(path.relative_to(PLAYGROUND))):
                self.assertEqual(unset_variables(path.read_text(encoding="utf-8"), seed_settings()), set(),
                                 "set it, with a value, in the environment of the seed service")

    def test_no_seed_file_writes_out_an_address(self):
        for path in seed_paths(PLAYGROUND):
            with self.subTest(file=str(path.relative_to(PLAYGROUND))):
                self.assertEqual(literal_addresses(path.read_text(encoding="utf-8")), set(),
                                 "an address is a ${VARIABLE} set by the seed service")

    @WITH_COMPOSE
    def test_no_seed_file_names_a_container(self):
        settings = seed_settings()
        hosts = set(compose()["services"]) | {urlsplit(v).hostname for v in settings.values() if v.startswith("http")}
        hosts |= {s["container_name"] for s in compose()["services"].values() if "container_name" in s}
        hosts -= {None, "localhost", "127.0.0.1"}
        for path in seed_paths(PLAYGROUND):
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=str(path.relative_to(PLAYGROUND))):
                self.assertEqual({h for h in hosts if re.search(rf"(?<![\w.-]){re.escape(h)}(?![\w-])", text)}, set())

    def test_no_secret_is_written_in_a_seed_file(self):
        for path in self.parsed():
            with self.subTest(file=str(path.relative_to(PLAYGROUND))):
                self.assertEqual(written_secrets(read(path)), [], "a secret is a ${SETTING}, never a literal")

    @WITH_COMPOSE
    def test_the_demo_values_of_the_secrets_are_written_only_in_the_compose_file(self):
        secrets = {compose_value(v) for v in compose()["x-agent-toolbox-secrets"].values()}
        secrets = {s for s in secrets if len(s) >= 8}   # `admin` is also a user name
        self.assertTrue(secrets)
        for path in seed_paths(PLAYGROUND):
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=str(path.relative_to(PLAYGROUND))):
                self.assertEqual({s for s in secrets if s in text}, set())


if __name__ == "__main__":
    unittest.main()
