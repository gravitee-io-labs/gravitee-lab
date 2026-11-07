"""What the catalog seed takes from a file: the operations of an API that become tools,
with the words written for a model in place of the generated ones, and the metadata of an agent. Run against a memory
that these tests define: an import that refuses a tool entry with a key it does not take."""

import copy

from lab_seeder import aim_catalog
from lab_seeder import common
from tests.test_aim_catalog import AGENT, CARD, Catalog, World

ORDERS = "Orchid Orders API"
CUSTOMERS = "Orchid Customers API"
STUDIO = ("aim-proxies", "studio", "MCP studios", "Customer desk", "studio-1")


# --- The two APIs as the platform generates their tools ---------------------------------------
# A body is nested under bodySchema, which the endpoint does not unwrap, and every tool carries the output schema of its
# responses

def generated(name, method, route, schema, params=(), query=()):
    return {"toolDefinition": {"name": name, "description": f"{name} as the page says it", "inputSchema": schema,
                               "outputSchema": {"type": "object", "properties": {"bodySchema": {}}},
                               "annotations": {"title": name, "readOnlyHint": method == "GET",
                                               "destructiveHint": False}},
            "gatewayMapping": {"http": {"method": method, "path": route, "contentType": "application/json",
                                        "pathParams": list(params), "queryParams": list(query), "headers": []}}}


def order_tools():
    one = {"type": "object", "required": ["orderId"],
           "properties": {"orderId": {"type": "string", "description": "Order id"}}}
    refund = {"type": "object", "required": ["orderId", "bodySchema"],
              "properties": {"orderId": {"type": "string"}, "bodySchema": {"type": "object", "properties": {
                  "amount": {"type": "number"}, "reason": {"type": "string"}}}}}
    listing = {"type": "object", "properties": {"customerId": {"type": "string"}}}
    return [generated("listOrders", "GET", "/orders", listing, query=["customerId", "status"]),
            generated("getOrder", "GET", "/orders/:orderId", one, params=["orderId"]),
            generated("listRefunds", "GET", "/orders/:orderId/refunds", one, params=["orderId"]),
            generated("createRefund", "POST", "/orders/:orderId/refunds", refund, params=["orderId"]),
            generated("cancelOrder", "POST", "/orders/:orderId/cancel", one, params=["orderId"])]


def customer_tools():
    one = {"type": "object", "required": ["customerId"], "properties": {"customerId": {"type": "string"}}}
    return [generated("searchCustomers", "GET", "/customers", {"type": "object"}, query=["region", "name"]),
            generated("getCustomer", "GET", "/customers/:customerId", one, params=["customerId"]),
            generated("updateShippingAddress", "PATCH", "/customers/:customerId/address", one, params=["customerId"]),
            generated("deleteCustomer", "DELETE", "/customers/:customerId", one, params=["customerId"])]


class ToolWorld(World):
    """The catalog as Gamma keeps it where these tools are concerned: an import that takes the keys of a tool and no
    other. An API with `tools` gives them as its page generates them."""

    ENTRY = {"apiId", "gatewayMapping", "definition"}

    def generate(self, call):
        api_id = call.json["apiIds"][0]
        api = self.apis[api_id]
        if "tools" not in api:
            return super().generate(call)
        errors = [{"key": "invalidSpec", "message": m} for m in api.get("errors", [])]
        return 200, {"data": [{"apiId": api_id, "tools": copy.deepcopy(api["tools"]), "errors": errors}]}

    def import_tools(self, call):
        for tool in call.json["tools"]:
            if set(tool) != self.ENTRY:
                return 400, {"message": f"Unrecognized field {sorted(set(tool) ^ self.ENTRY)}"}
        return super().import_tools(call)

    def tool(self, operation):
        return next(i for i in self.of_kind("mcp-tool") if i["definition"]["name"] == operation)


class Tools(Catalog):
    """A seed run against it, with the two APIs of Orchid in it."""

    def setUp(self):
        super().setUp()
        self.platform.routes.clear()   # the routes of the world the catalog made are those of another
        self.world = ToolWorld()
        self.answer(self.world.routes())
        self.world.apis["orders"] = {"name": ORDERS, "tools": order_tools()}
        self.world.apis["customers"] = {"name": CUSTOMERS, "tools": customer_tools()}

    def imported(self):
        """The tools of the last run's imports, in the order they were sent, one list for each API."""
        return [[t["definition"]["name"] for t in call.json["tools"]]
                for call in self.sent("POST", "/catalog/mcp-tools/_importFromApis")]

    def writes(self):
        return [(m, p) for m, p in self.platform.changes() if not p.endswith("_generateCapabilities")]


REFUND = """\
api: Orchid Orders API
operations:
  - listOrders
  - getOrder
  - listRefunds
  - id: createRefund
    description: >-
      Refund part or all of a delivered order, in whole euros. The amount cannot exceed what is left to refund.
    inputSchema:
      type: object
      required: [orderId, amount, reason]
      properties:
        orderId:
          type: string
          description: Order id such as O-5001
        amount:
          type: integer
          minimum: 1
          description: Amount to refund, in whole euros
        reason:
          type: string
          description: Why the customer is refunded, in one sentence
    annotations:
      {title: Refund an order, readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: false}
  - cancelOrder
"""
CUSTOMER_FILE = """\
api: Orchid Customers API
operations:
  - getCustomer
  - searchCustomers
"""
PLAIN = "api: Orchid Customers API\n"


def customers(*operations):
    return "api: Orchid Customers API\noperations: [" + ", ".join(operations) + "]\n"


def orders(*entries):
    return "api: Orchid Orders API\noperations:\n" + "".join(f"  - {entry}\n" for entry in entries)


class Operations(Tools):
    """Only the operations a file lists become tools, in its order."""

    def setUp(self):
        super().setUp()
        self.seed_files({"aim/api-tools/customers.yaml": CUSTOMER_FILE})

    def test_only_the_listed_operations_are_imported_in_the_order_of_the_file(self):
        self.assertEqual(self.run_seed(), [("POST", "/catalog/mcp-tools/_importFromApis")])
        self.assertEqual(self.imported(), [["getCustomer", "searchCustomers"]])
        self.assertEqual([i["entityId"] for i in self.world.of_kind("mcp-tool")],
                         ["mcp-tool.orchid-customers-api-getcustomer", "mcp-tool.orchid-customers-api-searchcustomers"])

    def test_a_listed_operation_is_the_tool_the_page_gave_without_its_output_schema(self):
        self.run_seed()
        [call] = self.sent("POST", "/catalog/mcp-tools/_importFromApis")
        page = {t["toolDefinition"]["name"]: t for t in customer_tools()}
        for tool in call.json["tools"]:
            name = tool["definition"]["name"]
            self.assertEqual(tool["gatewayMapping"], page[name]["gatewayMapping"])
            self.assertEqual(tool["definition"], {k: v for k, v in page[name]["toolDefinition"].items() if k != "outputSchema"})

    def test_an_operation_written_as_a_mapping_with_only_its_id_is_the_same_as_its_name(self):
        self.seed_files({"aim/api-tools/customers.yaml": "api: Orchid Customers API\noperations:\n  - {id: getCustomer}\n"})
        self.run_seed()
        self.assertEqual(self.imported(), [["getCustomer"]])
        self.seed_files({"aim/api-tools/customers.yaml": customers("getCustomer")})
        self.assertEqual(self.run_seed(), [])

    def test_a_second_run_writes_nothing(self):
        self.run_seed()
        self.assertEqual(self.run_seed(), [])

    def test_an_operation_added_to_the_list_replaces_the_tools_and_one_taken_out_replaces_them_again(self):
        self.run_seed()
        old = next(iter(self.world.sources))
        self.seed_files({"aim/api-tools/customers.yaml": CUSTOMER_FILE + "  - deleteCustomer\n"})
        self.assertEqual(self.run_seed(), [("DELETE", f"/catalog/sources/{old}"),
                                           ("POST", "/catalog/mcp-tools/_importFromApis")])
        self.assertEqual(self.imported(), [["getCustomer", "searchCustomers", "deleteCustomer"]])
        self.assertEqual(self.run_seed(), [])
        self.seed_files({"aim/api-tools/customers.yaml": CUSTOMER_FILE})
        self.assertEqual([m for m, _ in self.run_seed()], ["DELETE", "POST"])
        names = sorted(i["definition"]["name"] for i in self.world.of_kind("mcp-tool"))
        self.assertEqual(names, ["getCustomer", "searchCustomers"])
        self.assertEqual(self.run_seed(), [])

    def test_the_order_of_the_file_is_part_of_what_is_asked(self):
        self.run_seed()
        self.seed_files({"aim/api-tools/customers.yaml": customers("searchCustomers", "getCustomer")})
        self.assertEqual([m for m, _ in self.run_seed()], ["DELETE", "POST"])
        self.assertEqual(self.imported(), [["searchCustomers", "getCustomer"]])

    def test_an_operation_the_page_does_not_give_stops_the_seed_before_any_change_naming_it_and_what_the_page_has(self):
        self.seed_files({"aim/api-tools/customers.yaml": CUSTOMER_FILE + "  - eraseCustomer\n"})
        with self.assertRaises(common.SeedError) as stopped:
            self.run_seed()
        self.assertEqual(str(stopped.exception),
                         "The OpenAPI page of 'Orchid Customers API' has no operation 'eraseCustomer': "
                         "it has searchCustomers, getCustomer, updateShippingAddress, deleteCustomer")
        self.assertEqual(self.writes(), [])

    def test_names_are_matched_whole_and_as_written(self):
        self.seed_files({"aim/api-tools/customers.yaml": customers("getcustomer")})
        with self.assertRaisesRegex(common.SeedError, "has no operation 'getcustomer'"):
            self.run_seed()

    def test_a_page_that_gives_no_operation_at_all_says_so(self):
        self.world.apis["customers"]["tools"] = []
        with self.assertRaisesRegex(common.SeedError, "has no operation 'getCustomer', 'searchCustomers': it has none"):
            self.run_seed()

    def test_a_page_that_gives_errors_stops_the_seed_naming_the_operations_asked(self):
        self.world.apis["customers"]["errors"] = ["Duplicate operationId getCustomer"]
        with self.assertRaises(common.SeedError) as stopped:
            self.run_seed()
        self.assertEqual(str(stopped.exception),
                         "The OpenAPI page of 'Orchid Customers API' gives errors that can concern "
                         "getCustomer, searchCustomers: Duplicate operationId getCustomer")
        self.assertEqual(self.writes(), [])

    def test_tools_of_the_api_that_the_seed_did_not_create_stop_the_seed_and_are_left_alone(self):
        theirs = {"id": "theirs", "sourceKind": "apim.api", "definition": {"apiId": "customers", "apiName": CUSTOMERS}}
        self.world.sources["theirs"] = theirs
        message = "The tools of the API 'Orchid Customers API' exists and was not created"
        with self.assertRaisesRegex(common.SeedError, message):
            self.run_seed()
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.world.sources, {"theirs": theirs})


class Mistakes(Tools):
    """A file that asks what cannot be done is refused with one line, before the platform is asked for anything."""

    def refused(self, text, message):
        self.seed_files({"aim/api-tools/orders.yaml": "api: Orchid Orders API\n" + text})
        with self.assertRaises(common.SeedError) as stopped:
            self.run_seed()
        self.assertRegex(str(stopped.exception), message)
        self.assertNotIn("\n", str(stopped.exception))
        self.assertEqual(self.sent("POST", "/catalog/api-tools/_generateCapabilities"), [])
        self.assertEqual(self.writes(), [])

    def test_operations_is_a_list_of_something(self):
        for text in ("operations: []\n", "operations: getOrder\n", "operations: {getOrder: 1}\n", "operations:\n"):
            with self.subTest(text=text):
                self.refused(text, "The operations of 'Orchid Orders API' are a list of operation ids, and it lists none")

    def test_an_operation_is_a_name_or_a_mapping_that_has_one(self):
        for entry in ("{description: Refund}", "7", "[getOrder]", "{id: 3}", "''"):
            with self.subTest(entry=entry):
                self.refused(f"operations: [{entry}]\n", "are operation ids, or mappings with an id")

    def test_an_operation_is_listed_once(self):
        self.refused("operations: [getOrder, {id: getOrder, description: Again}]\n",
                     "The operation getOrder of 'Orchid Orders API' is listed twice")

    def test_a_word_the_file_may_not_write_is_refused_naming_it_and_what_it_may_write(self):
        self.refused("operations:\n  - {id: getOrder, name: get_order}\n",
                     "The operation getOrder of 'Orchid Orders API' cannot say name: only id, description, inputSchema, annotations")
        self.refused("operations:\n  - {id: getOrder, outputSchema: {type: object}, gatewayMapping: {}}\n",
                     "cannot say outputSchema, gatewayMapping")

    def test_what_the_file_writes_has_the_type_the_platform_asks_for(self):
        wrong = [("description", "''", "text"), ("description", "[a]", "text"), ("inputSchema", "[a, b]", "a JSON schema"),
                 ("annotations", "readOnly", "a mapping of hints")]
        for key, value, wanted in wrong:
            with self.subTest(key=key, value=value):
                self.refused(f"operations:\n  - {{id: getOrder, {key}: {value}}}\n",
                             f"The operation getOrder of 'Orchid Orders API': {key} must be {wanted}")

    def test_a_mistake_in_the_second_file_stops_the_seed_before_the_first_is_imported(self):
        self.seed_files({"aim/api-tools/customers.yaml": CUSTOMER_FILE,
                         "aim/api-tools/orders.yaml": "api: Orchid Orders API\noperations: [getOrder, getOrder]\n"})
        with self.assertRaisesRegex(common.SeedError, "listed twice"):
            self.run_seed()
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.sent("POST", "/catalog/api-tools/_generateCapabilities"), [])


class Tailoring(Tools):
    """The words written for a model in place of the generated ones."""

    def setUp(self):
        super().setUp()
        self.seed_files({"aim/api-tools/orders.yaml": REFUND})

    def refund(self):
        return self.world.tool("createRefund")

    def test_the_tool_is_imported_with_the_words_of_the_file_in_place_of_those_of_the_page(self):
        self.run_seed()
        tool = self.refund()
        self.assertEqual(tool["entityId"], "mcp-tool.orchid-orders-api-createrefund")
        definition = tool["definition"]
        self.assertEqual(sorted(definition), ["annotations", "description", "inputSchema", "name"])
        self.assertEqual(definition["name"], "createRefund")
        self.assertTrue(definition["description"].startswith("Refund part or all of a delivered order"))
        self.assertEqual(definition["inputSchema"]["required"], ["orderId", "amount", "reason"])
        self.assertEqual(definition["inputSchema"]["properties"]["amount"],
                         {"type": "integer", "minimum": 1, "description": "Amount to refund, in whole euros"})
        self.assertNotIn("bodySchema", definition["inputSchema"]["properties"])
        self.assertEqual(definition["annotations"],
                         {"title": "Refund an order", "readOnlyHint": False, "destructiveHint": True,
                          "idempotentHint": False, "openWorldHint": False})

    def test_a_description_alone_leaves_the_rest_of_the_generated_definition(self):
        self.seed_files({"aim/api-tools/orders.yaml": orders("{id: getOrder, description: Read one order}")})
        self.run_seed()
        page = order_tools()[1]["toolDefinition"]
        self.assertEqual(self.world.tool("getOrder")["definition"],
                         {"name": "getOrder", "description": "Read one order", "inputSchema": page["inputSchema"],
                          "annotations": page["annotations"]})

    def test_a_second_run_writes_nothing(self):
        self.run_seed()
        self.assertEqual(self.run_seed(), [])

    def test_other_words_replace_the_tools_with_their_source_and_the_studios_that_hold_them(self):
        self.run_seed()
        common.record(*STUDIO, digest="x")
        old = next(iter(self.world.sources))
        self.seed_files({"aim/api-tools/orders.yaml": REFUND.replace("Refund part or all", "Give back part or all")})
        changes = self.run_seed()
        self.assertEqual(changes, [("DELETE", "/mcp-proxies/studio-1"), ("DELETE", f"/catalog/sources/{old}"),
                                   ("POST", "/catalog/mcp-tools/_importFromApis")])
        self.assertTrue(self.refund()["definition"]["description"].startswith("Give back part or all"))
        self.assertIn("Studio 'Customer desk' deleted: it is made again from the new tools", self.output())
        self.assertEqual(self.run_seed(), [])

    def test_the_tools_of_an_api_that_changed_are_replaced_and_keep_the_words_of_the_file(self):
        self.run_seed()
        self.world.apis["orders"]["tools"][3]["gatewayMapping"]["http"]["path"] = "/orders/:orderId/refund"
        self.assertEqual([m for m, _ in self.run_seed()], ["DELETE", "POST"])
        self.assertEqual(self.refund()["definition"]["inputSchema"]["required"], ["orderId", "amount", "reason"])


class Studios(Tools):
    """Two APIs, and the studios the seed made."""

    def setUp(self):
        super().setUp()
        self.seed_files({"aim/api-tools/customers.yaml": CUSTOMER_FILE, "aim/api-tools/orders.yaml": REFUND})

    def test_each_api_is_imported_with_its_own_tools(self):
        self.run_seed()
        self.assertEqual(self.imported(), [["getCustomer", "searchCustomers"],
                                           ["listOrders", "getOrder", "listRefunds", "createRefund", "cancelOrder"]])
        self.assertEqual([(x["name"], x["label"]) for x in self.records("api-tools")],
                         [(CUSTOMERS, "API tools"), (ORDERS, "API tools")])

    def test_a_studio_is_deleted_once_when_the_tools_of_two_apis_change(self):
        self.run_seed()
        common.record(*STUDIO, digest="x")
        self.seed_files({"aim/api-tools/customers.yaml": CUSTOMER_FILE + "  - deleteCustomer\n",
                         "aim/api-tools/orders.yaml": REFUND.replace("  - cancelOrder\n", "")})
        changes = self.run_seed()
        self.assertEqual([p for _, p in changes if p.startswith("/mcp-proxies")], ["/mcp-proxies/studio-1"])
        self.assertEqual(self.output().count("Studio 'Customer desk' deleted"), 1)
        self.assertEqual([m for m, p in changes if p.startswith("/catalog/sources")], ["DELETE", "DELETE"])
        self.assertEqual(self.run_seed(), [])

    def test_the_api_whose_file_changed_alone_is_the_one_replaced(self):
        self.run_seed()
        untouched = next(s["id"] for s in self.world.sources.values() if s["definition"]["apiName"] == CUSTOMERS)
        self.seed_files({"aim/api-tools/orders.yaml": REFUND.replace("  - cancelOrder\n", "")})
        self.assertNotIn(("DELETE", f"/catalog/sources/{untouched}"), self.run_seed())
        self.assertEqual(sorted(i["definition"]["name"] for i in self.world.of_kind("mcp-tool")),
                         ["createRefund", "getCustomer", "getOrder", "listOrders", "listRefunds", "searchCustomers"])


# --- Agents ---------------------------------------------------------------------------------

COPILOT = f"""\
entityId: desk-copilot
source: Orchid
url: {AGENT}
metadata:
  riskClassification: limited
identity:
  clientId: desk-copilot
access:
  - {{studio: Customer desk, plan: Agents}}
"""
BARE = f"entityId: desk-copilot\nsource: Orchid\nurl: {AGENT}\n"


class Metadata(Tools):
    """An agent is registered from its card with the metadata its file gives it."""

    def setUp(self):
        super().setUp()
        self.world.card = {**self.world.card, "name": "Desk copilot"}
        self.seed_files({"aim/agents/desk-copilot.yaml": COPILOT})

    def agent(self):
        return self.world.named("agent", "desk-copilot")

    def test_the_agent_is_made_with_its_card_and_its_metadata_under_the_entity_id_the_file_gives(self):
        self.assertEqual(self.run_seed(), [("POST", "/catalog/sources"), ("POST", "/catalog/agents")])
        [call] = self.sent("POST", "/catalog/agents")
        self.assertEqual(sorted(call.json), ["definition", "entityId", "metadata", "sourceId"])
        self.assertEqual((call.json["entityId"], call.json["metadata"]),
                         ("desk-copilot", {"riskClassification": "limited"}))
        self.assertEqual(self.agent()["entityId"], "agent.desk-copilot")
        self.assertEqual(self.agent()["metadata"], {"riskClassification": "limited"})
        self.assertEqual(self.agent()["definition"]["name"], "Desk copilot")
        self.assertEqual([x["name"] for x in self.records("agent")], ["Desk copilot"])

    def test_a_second_run_writes_nothing_and_a_change_of_what_another_module_reads_does_not_write_either(self):
        self.run_seed()
        self.assertEqual(self.run_seed(), [])
        self.seed_files({"aim/agents/desk-copilot.yaml": COPILOT.replace("clientId: desk-copilot", "clientId: other")})
        self.assertEqual(self.run_seed(), [])

    def test_a_risk_classification_the_catalog_does_not_know_stops_the_seed_before_the_card_is_read(self):
        for risk in ("critical", "Limited", "unacceptable", "''", "[high]", "3", ""):
            with self.subTest(risk=risk):
                self.seed_files({"aim/agents/desk-copilot.yaml": COPILOT.replace("limited", risk)})
                self.platform.calls.clear()
                with self.assertRaises(common.SeedError) as stopped:
                    aim_catalog.seed()
                self.assertRegex(str(stopped.exception),
                                 r"^The agent 'desk-copilot': riskClassification .* is not one of "
                                 r"negligible, limited, moderate, high$")
                self.assertEqual(self.platform.sent("GET", CARD), [])
                self.assertEqual(self.platform.changes(), [])

    def test_metadata_is_a_mapping(self):
        for text in ("metadata: high\n", "metadata: [limited]\n"):
            with self.subTest(text=text):
                file = COPILOT.replace("metadata:\n  riskClassification: limited\n", text)
                self.seed_files({"aim/agents/desk-copilot.yaml": file})
                message = "The agent 'desk-copilot': metadata is a mapping of keys to values"
                with self.assertRaisesRegex(common.SeedError, message):
                    self.run_seed()

    def test_an_agent_without_metadata_is_registered_as_before(self):
        self.seed_files({"aim/agents/desk-copilot.yaml": BARE})
        self.run_seed()
        [call] = self.sent("POST", "/catalog/agents")
        self.assertEqual(sorted(call.json), ["definition", "entityId", "sourceId"])
        self.assertEqual(self.run_seed(), [])

    def test_the_metadata_of_a_registered_agent_that_changed_is_replaced_with_the_card(self):
        self.run_seed()
        self.seed_files({"aim/agents/desk-copilot.yaml": COPILOT.replace("limited", "moderate")})
        self.assertEqual(self.run_seed(), [("PUT", f"/catalog/agents/{self.agent()['id']}")])
        self.assertEqual(self.agent()["metadata"], {"riskClassification": "moderate"})
        self.assertEqual(self.run_seed(), [])

    def test_a_replace_keeps_what_compliance_wrote_in_the_metadata_of_the_agent(self):
        self.run_seed()
        answers = {"compliance.intendedPurpose": "Helps the desk team", "compliance.riskAssessment": "RA-14"}
        self.agent()["metadata"].update(answers)
        self.assertEqual(self.run_seed(), [])   # what another module wrote is no change of the file
        self.world.card = {**self.world.card, "description": "Another description"}
        self.seed_files({"aim/agents/desk-copilot.yaml": COPILOT.replace("limited", "high")})
        self.run_seed()
        [call] = self.sent("PUT", "/catalog/agents/*")
        self.assertEqual(call.json["metadata"], {"riskClassification": "high", **answers})
        self.assertEqual(self.agent()["metadata"], {"riskClassification": "high", **answers})
        self.assertEqual(self.run_seed(), [])

    def test_a_replace_of_an_agent_whose_file_has_no_metadata_keeps_the_metadata_it_has(self):
        self.run_seed()
        self.agent()["metadata"]["compliance.riskAssessment"] = "RA-14"
        self.world.card = {**self.world.card, "description": "Another description"}
        self.seed_files({"aim/agents/desk-copilot.yaml": BARE})
        self.run_seed()
        [call] = self.sent("PUT", "/catalog/agents/*")
        self.assertEqual(call.json["metadata"], {"riskClassification": "limited", "compliance.riskAssessment": "RA-14"})
