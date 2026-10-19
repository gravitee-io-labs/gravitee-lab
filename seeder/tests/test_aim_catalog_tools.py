"""What the catalog seed takes from a file since the Harbor playgrounds: the operations of an API that become tools,
with the words written for a model in place of the generated ones, and the metadata of an agent. Run against a memory
that these tests define: a tool that is read and saved whole as the platform does (the calls are those of the live scripts
that import the tools of an API and then curate one), and an import that refuses a tool entry with a key it does not take."""

import copy
import unittest

from lab_seeder import aim_catalog
from lab_seeder import common
from tests.test_aim_catalog import AGENT, CARD, SCOPE, Catalog, World, last, path

ORDERS = "Harbor Orders API"
CUSTOMERS = "Harbor Customers API"
STUDIO = ("aim-proxies", "studio", "MCP studios", "Customer care", "studio-1")
IMPORT = SCOPE + "/catalog/mcp-tools/_importFromApis"


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
    other, and stores it with the mapping to its API in its extensions; a tool read whole; a save that replaces the
    tool, its extensions with it, and refuses what the platform refuses. An API with `tools` gives them as its page
    generates them."""

    ENTRY = {"apiId", "gatewayMapping", "definition"}
    refuses = None   # what a save is refused with

    def generate(self, call):
        api_id = call.json["apiIds"][0]
        api = self.apis[api_id]
        if "tools" not in api:
            return super().generate(call)
        errors = [{"key": "invalidSpec", "message": m} for m in api.get("errors", [])]
        return 200, {"data": [{"apiId": api_id, "tools": copy.deepcopy(api["tools"]), "errors": errors}]}

    def import_tools(self, call):
        tools = copy.deepcopy(call.json["tools"])
        for tool in tools:
            if set(tool) != self.ENTRY:
                return 400, {"message": f"Unrecognized field {sorted(set(tool) ^ self.ENTRY)}"}
        code, body = super().import_tools(call)
        if code == 201:
            mappings = {t["definition"]["name"]: t["gatewayMapping"] for t in tools}
            for item in body["data"]:
                item.update(extensions={"gatewayMapping": mappings[item["definition"]["name"]]}, metadata={})
        return code, body

    def save_tool(self, call):
        body, item = copy.deepcopy(call.json), self.items[last(call)]
        if self.refuses:
            return 400, {"message": self.refuses}
        if not body.get("sourceId") or not body.get("entityId") or not isinstance(body.get("definition"), dict):
            return 400, {"message": "sourceId, entityId and definition are required"}
        if (body.get("extensions") or {}).get("referenceCost", 0) < 0:
            return 400, {"message": "referenceCost.negative"}
        item.update(sourceId=body["sourceId"], definition=body["definition"], extensions=body.get("extensions"),
                    metadata=body.get("metadata"))
        return 200, item

    def routes(self):
        return {**super().routes(), ("GET", path("/catalog/mcp-tools/*")): lambda call: (200, self.items[last(call)]),
                ("PUT", path("/catalog/mcp-tools/*")): self.save_tool}

    def tool(self, operation):
        return next(i for i in self.of_kind("mcp-tool") if i["definition"]["name"] == operation)


class Tools(Catalog):
    """A seed run against it, with the two APIs of Harbor in it."""

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

    def saved(self):
        """What the last run saved of the tools."""
        return [call.json for call in self.sent("PUT", "/catalog/mcp-tools/*")]

    def writes(self):
        return [(m, p) for m, p in self.platform.changes() if not p.endswith("_generateCapabilities")]


REFUND = """\
api: Harbor Orders API
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
api: Harbor Customers API
operations:
  - getCustomer
  - searchCustomers
"""
PLAIN = "api: Harbor Customers API\n"


def customers(*operations):
    return "api: Harbor Customers API\noperations: [" + ", ".join(operations) + "]\n"


def orders(*entries):
    return "api: Harbor Orders API\noperations:\n" + "".join(f"  - {entry}\n" for entry in entries)


class Operations(Tools):
    """Only the operations a file lists become tools, in its order."""

    def setUp(self):
        super().setUp()
        self.seed_files({"aim/api-tools/customers.yaml": CUSTOMER_FILE})

    def test_only_the_listed_operations_are_imported_in_the_order_of_the_file(self):
        self.assertEqual(self.run_seed(), [("POST", "/catalog/mcp-tools/_importFromApis")])
        self.assertEqual(self.imported(), [["getCustomer", "searchCustomers"]])
        self.assertEqual([i["entityId"] for i in self.world.of_kind("mcp-tool")],
                         ["mcp-tool.harbor-customers-api-getcustomer", "mcp-tool.harbor-customers-api-searchcustomers"])

    def test_a_listed_operation_is_the_tool_the_page_gave_without_its_output_schema(self):
        self.run_seed()
        [call] = self.sent("POST", "/catalog/mcp-tools/_importFromApis")
        page = {t["toolDefinition"]["name"]: t for t in customer_tools()}
        for tool in call.json["tools"]:
            name = tool["definition"]["name"]
            self.assertEqual(tool["gatewayMapping"], page[name]["gatewayMapping"])
            self.assertEqual(tool["definition"], {k: v for k, v in page[name]["toolDefinition"].items() if k != "outputSchema"})
        self.assertEqual(self.saved(), [])

    def test_an_operation_written_as_a_mapping_with_only_its_id_is_the_same_as_its_name(self):
        self.seed_files({"aim/api-tools/customers.yaml": "api: Harbor Customers API\noperations:\n  - {id: getCustomer}\n"})
        self.run_seed()
        self.assertEqual(self.imported(), [["getCustomer"]])
        self.assertEqual(self.saved(), [])
        self.seed_files({"aim/api-tools/customers.yaml": customers("getCustomer")})
        self.assertEqual(self.run_seed(), [])

    def test_a_second_run_writes_nothing(self):
        self.run_seed()
        self.assertEqual(self.run_seed(), [])

    def test_the_record_keeps_the_fingerprint_of_what_was_imported(self):
        self.run_seed()
        [call] = self.sent("POST", "/catalog/mcp-tools/_importFromApis")
        [record] = self.records("api-tools")
        self.assertEqual((record["name"], record["digest"]), (CUSTOMERS, aim_catalog.fingerprint(call.json["tools"])))

    def test_the_log_counts_the_tools_that_were_imported(self):
        self.run_seed()
        self.assertIn("[aim-catalog] ✓ API tool imported: Harbor Customers API (2 tools)", self.output())

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
                         "The OpenAPI page of 'Harbor Customers API' has no operation 'eraseCustomer': "
                         "it has searchCustomers, getCustomer, updateShippingAddress, deleteCustomer")
        self.assertEqual(self.writes(), [])

    def test_every_operation_that_is_missing_is_named(self):
        self.seed_files({"aim/api-tools/customers.yaml": customers("getCustomers", "erase")})
        with self.assertRaisesRegex(common.SeedError, "has no operation 'getCustomers', 'erase': it has"):
            self.run_seed()

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
                         "The OpenAPI page of 'Harbor Customers API' gives errors that can concern "
                         "getCustomer, searchCustomers: Duplicate operationId getCustomer")
        self.assertEqual(self.writes(), [])

    def test_a_page_that_gives_errors_stops_a_file_that_lists_nothing_too_as_before(self):
        self.seed_files({"aim/api-tools/customers.yaml": PLAIN})
        self.world.apis["customers"]["errors"] = ["no servers"]
        with self.assertRaisesRegex(common.SeedError, "gives no tools: no servers"):
            self.run_seed()

    def test_a_file_without_operations_imports_every_operation_of_the_page(self):
        self.seed_files({"aim/api-tools/customers.yaml": PLAIN})
        self.run_seed()
        every = ["searchCustomers", "getCustomer", "updateShippingAddress", "deleteCustomer"]
        self.assertEqual(self.imported(), [every])

    def test_tools_of_the_api_that_the_seed_did_not_create_stop_the_seed_and_are_left_alone(self):
        theirs = {"id": "theirs", "sourceKind": "apim.api", "definition": {"apiId": "customers", "apiName": CUSTOMERS}}
        self.world.sources["theirs"] = theirs
        message = "The tools of the API 'Harbor Customers API' exists and was not created"
        with self.assertRaisesRegex(common.SeedError, message):
            self.run_seed()
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.world.sources, {"theirs": theirs})


class Mistakes(Tools):
    """A file that asks what cannot be done is refused with one line, before the platform is asked for anything."""

    def refused(self, text, message):
        self.seed_files({"aim/api-tools/orders.yaml": "api: Harbor Orders API\n" + text})
        with self.assertRaises(common.SeedError) as stopped:
            self.run_seed()
        self.assertRegex(str(stopped.exception), message)
        self.assertNotIn("\n", str(stopped.exception))
        self.assertEqual(self.sent("POST", "/catalog/api-tools/_generateCapabilities"), [])
        self.assertEqual(self.writes(), [])

    def test_operations_is_a_list_of_something(self):
        for text in ("operations: []\n", "operations: getOrder\n", "operations: {getOrder: 1}\n", "operations:\n"):
            with self.subTest(text=text):
                self.refused(text, "The operations of 'Harbor Orders API' are a list of operation ids, and it lists none")

    def test_an_operation_is_a_name_or_a_mapping_that_has_one(self):
        for entry in ("{description: Refund}", "7", "[getOrder]", "{id: 3}", "''"):
            with self.subTest(entry=entry):
                self.refused(f"operations: [{entry}]\n", "are operation ids, or mappings with an id")

    def test_an_operation_is_listed_once(self):
        self.refused("operations: [getOrder, {id: getOrder, description: Again}]\n",
                     "The operation getOrder of 'Harbor Orders API' is listed twice")

    def test_a_word_the_file_may_not_write_is_refused_naming_it_and_what_it_may_write(self):
        self.refused("operations:\n  - {id: getOrder, name: get_order}\n",
                     "The operation getOrder of 'Harbor Orders API' cannot say name: only id, description, inputSchema, "
                     "annotations, referenceCost, referenceValue")
        self.refused("operations:\n  - {id: getOrder, outputSchema: {type: object}, gatewayMapping: {}}\n",
                     "cannot say outputSchema, gatewayMapping")

    def test_what_the_file_writes_has_the_type_the_platform_asks_for(self):
        number = "a number, 0 or more"
        wrong = [("description", "''", "text"), ("description", "[a]", "text"), ("inputSchema", "[a, b]", "a JSON schema"),
                 ("annotations", "readOnly", "a mapping of hints"), ("referenceCost", "-1", number),
                 ("referenceCost", "true", number), ("referenceValue", "cheap", number)]
        for key, value, wanted in wrong:
            with self.subTest(key=key, value=value):
                self.refused(f"operations:\n  - {{id: getOrder, {key}: {value}}}\n",
                             f"The operation getOrder of 'Harbor Orders API': {key} must be {wanted}")

    def test_a_mistake_in_the_second_file_stops_the_seed_before_the_first_is_imported(self):
        self.seed_files({"aim/api-tools/customers.yaml": CUSTOMER_FILE,
                         "aim/api-tools/orders.yaml": "api: Harbor Orders API\noperations: [getOrder, getOrder]\n"})
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

    def test_the_tools_are_imported_as_the_page_gave_them_and_the_one_with_words_written_is_saved_after(self):
        self.assertEqual(self.run_seed(), [("POST", "/catalog/mcp-tools/_importFromApis"),
                                           ("PUT", f"/catalog/mcp-tools/{self.refund()['id']}")])
        self.assertEqual(self.imported(), [["listOrders", "getOrder", "listRefunds", "createRefund", "cancelOrder"]])
        [call] = self.sent("POST", "/catalog/mcp-tools/_importFromApis")
        sent = next(t for t in call.json["tools"] if t["definition"]["name"] == "createRefund")
        self.assertEqual(sorted(sent), ["apiId", "definition", "gatewayMapping"])   # the words are not part of the import
        self.assertEqual(sent["definition"]["inputSchema"], order_tools()[3]["toolDefinition"]["inputSchema"])

    def test_the_save_is_the_tool_as_read_with_the_words_of_the_file_in_place_of_those_of_the_page(self):
        self.run_seed()
        [body] = self.saved()
        tool = self.refund()
        self.assertEqual(sorted(body), ["definition", "entityId", "extensions", "metadata", "sourceId"])
        self.assertEqual((body["sourceId"], body["entityId"]),
                         (tool["sourceId"], "mcp-tool.harbor-orders-api-createrefund"))
        definition = body["definition"]
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
        self.assertEqual(tool["definition"], definition)

    def test_the_tool_keeps_its_mapping_to_the_api_which_a_save_would_otherwise_lose(self):
        self.run_seed()
        mapping = self.refund()["extensions"]["gatewayMapping"]
        self.assertEqual(mapping, order_tools()[3]["gatewayMapping"])
        self.assertEqual(mapping["http"]["pathParams"], ["orderId"])

    def test_a_tool_with_nothing_written_for_it_is_not_saved(self):
        self.run_seed()
        for operation in ("listOrders", "getOrder", "listRefunds", "cancelOrder"):
            tool = self.world.tool(operation)
            self.assertEqual(tool["definition"]["description"], f"{operation} as the page says it")
            self.assertNotIn("outputSchema", tool["definition"])
        self.assertEqual(len(self.saved()), 1)

    def test_a_description_alone_leaves_the_rest_of_the_generated_definition(self):
        self.seed_files({"aim/api-tools/orders.yaml": orders("{id: getOrder, description: Read one order}")})
        self.run_seed()
        page = order_tools()[1]["toolDefinition"]
        self.assertEqual(self.world.tool("getOrder")["definition"],
                         {"name": "getOrder", "description": "Read one order", "inputSchema": page["inputSchema"],
                          "annotations": page["annotations"]})

    def test_the_references_go_in_the_extensions_of_the_tool_beside_its_mapping(self):
        self.seed_files({"aim/api-tools/orders.yaml": orders("{id: getOrder, referenceCost: 0.002, referenceValue: 0}")})
        self.run_seed()
        tool = self.world.tool("getOrder")
        self.assertEqual(tool["extensions"], {"gatewayMapping": order_tools()[1]["gatewayMapping"],
                                              "referenceCost": 0.002, "referenceValue": 0})
        self.assertNotIn("referenceCost", tool["definition"])
        self.assertEqual(tool["definition"]["description"], "getOrder as the page says it")

    def test_a_second_run_writes_nothing(self):
        self.run_seed()
        self.assertEqual(len(self.saved()), 1)
        self.assertEqual(self.run_seed(), [])

    def test_the_log_counts_the_tools_that_were_tailored(self):
        self.run_seed()
        self.assertIn("[aim-catalog] ✓ API tool imported: Harbor Orders API (5 tools, 1 tailored)", self.output())

    def test_the_record_is_the_source_with_the_fingerprint_of_the_operations_and_the_words_written(self):
        self.run_seed()
        [record] = self.records("api-tools")
        [source] = self.world.sources.values()
        self.assertEqual((record["id"], record["name"], record["label"]), (source["id"], ORDERS, "API tools"))
        refund = common.load(common.SEED_DIR / "aim/api-tools/orders.yaml")["operations"][3]
        wording = {k: v for k, v in refund.items() if k != "id"}
        entries = [{"apiId": "orders", "gatewayMapping": tool["gatewayMapping"],
                    "definition": {k: v for k, v in tool["toolDefinition"].items() if k != "outputSchema"}}
                   for tool in order_tools()]
        entries[3]["tailoring"] = wording
        self.assertEqual(record["digest"], aim_catalog.fingerprint(entries))

    def test_other_words_replace_the_tools_with_their_source_and_the_studios_that_hold_them(self):
        self.run_seed()
        common.record(*STUDIO, digest="x")
        old = next(iter(self.world.sources))
        self.seed_files({"aim/api-tools/orders.yaml": REFUND.replace("Refund part or all", "Give back part or all")})
        changes = self.run_seed()
        self.assertEqual(changes, [("DELETE", "/mcp-proxies/studio-1"), ("DELETE", f"/catalog/sources/{old}"),
                                   ("POST", "/catalog/mcp-tools/_importFromApis"),
                                   ("PUT", f"/catalog/mcp-tools/{self.refund()['id']}")])
        self.assertTrue(self.refund()["definition"]["description"].startswith("Give back part or all"))
        self.assertIn("Studio 'Customer care' deleted: it is made again from the new tools", self.output())
        self.assertEqual(self.run_seed(), [])

    def test_a_new_schema_a_new_hint_and_a_new_reference_each_replace_the_tools(self):
        self.run_seed()
        changes = {"inputSchema": REFUND.replace("minimum: 1", "minimum: 5"),
                   "annotations": REFUND.replace("destructiveHint: true", "destructiveHint: false"),
                   "reference": REFUND.replace("    annotations:", "    referenceCost: 0.01\n    annotations:")}
        for what, text in changes.items():
            with self.subTest(what):
                self.seed_files({"aim/api-tools/orders.yaml": text})
                self.assertEqual([m for m, _ in self.run_seed()], ["DELETE", "POST", "PUT"])
                self.assertEqual(self.run_seed(), [])
        self.assertEqual(self.refund()["extensions"]["referenceCost"], 0.01)

    def test_the_tools_of_an_api_that_changed_are_replaced_and_tailored_again(self):
        self.run_seed()
        self.world.apis["orders"]["tools"][3]["gatewayMapping"]["http"]["path"] = "/orders/:orderId/refund"
        self.assertEqual([m for m, _ in self.run_seed()], ["DELETE", "POST", "PUT"])
        self.assertEqual(self.refund()["extensions"]["gatewayMapping"]["http"]["path"], "/orders/:orderId/refund")
        self.assertEqual(self.refund()["definition"]["inputSchema"]["required"], ["orderId", "amount", "reason"])

    def test_a_refused_save_stops_the_seed_naming_the_tool_and_the_next_run_replaces_the_tools(self):
        self.world.refuses = "inputSchema.invalid"
        with self.assertRaises(common.SeedError) as stopped:
            self.run_seed()
        self.assertRegex(str(stopped.exception),
                         r"^The tool createRefund of 'Harbor Orders API' could not be tailored: "
                         r"PUT /catalog/mcp-tools/\S+: HTTP 400 .*inputSchema.invalid")
        self.assertNotIn("\n", str(stopped.exception))
        [record] = self.records("api-tools")   # the tools are the seed's, so that its next run can replace them
        self.assertNotIn("digest", record)
        self.world.refuses = None
        old = next(iter(self.world.sources))
        changes = self.run_seed()
        self.assertEqual(changes, [("DELETE", f"/catalog/sources/{old}"),
                                   ("POST", "/catalog/mcp-tools/_importFromApis"),
                                   ("PUT", f"/catalog/mcp-tools/{self.refund()['id']}")])
        self.assertIn("digest", self.records("api-tools")[0])
        self.assertEqual(self.refund()["definition"]["annotations"]["title"], "Refund an order")
        self.assertEqual(self.run_seed(), [])

    def test_a_negative_reference_is_refused_by_the_file_and_not_left_for_the_platform(self):
        self.seed_files({"aim/api-tools/orders.yaml": orders("{id: getOrder, referenceCost: -0.5}")})
        message = "getOrder of 'Harbor Orders API': referenceCost must be a number, 0 or more"
        with self.assertRaisesRegex(common.SeedError, message):
            self.run_seed()
        self.assertEqual(self.writes(), [])

    def test_a_tool_the_import_did_not_give_back_is_named(self):
        def partial(call):
            code, body = ToolWorld.import_tools(self.world, call)
            body["data"] = [t for t in body["data"] if t["definition"]["name"] != "createRefund"]
            return code, body
        self.platform.add({("POST", IMPORT): partial})
        message = "The platform did not give back the tool createRefund of 'Harbor Orders API'"
        with self.assertRaisesRegex(common.SeedError, message):
            self.run_seed()

    def test_a_tool_that_the_platform_gave_an_output_schema_back_does_not_send_it_again(self):
        def keeps(call):
            code, body = ToolWorld.import_tools(self.world, call)
            for tool in body["data"]:
                tool["definition"]["outputSchema"] = {"type": "object"}
            return code, body
        self.platform.add({("POST", IMPORT): keeps})
        self.run_seed()
        self.assertNotIn("outputSchema", self.saved()[0]["definition"])


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
        self.assertEqual(self.output().count("Studio 'Customer care' deleted"), 1)
        self.assertEqual([m for m, p in changes if p.startswith("/catalog/sources")], ["DELETE", "DELETE"])
        self.assertEqual(self.run_seed(), [])

    def test_a_studio_is_left_alone_while_the_tools_are_up_to_date(self):
        self.run_seed()
        common.record(*STUDIO, digest="x")
        self.assertEqual(self.run_seed(), [])

    def test_the_api_whose_file_changed_alone_is_the_one_replaced(self):
        self.run_seed()
        untouched = next(s["id"] for s in self.world.sources.values() if s["definition"]["apiName"] == CUSTOMERS)
        self.seed_files({"aim/api-tools/orders.yaml": REFUND.replace("  - cancelOrder\n", "")})
        self.assertNotIn(("DELETE", f"/catalog/sources/{untouched}"), self.run_seed())
        self.assertEqual(sorted(i["definition"]["name"] for i in self.world.of_kind("mcp-tool")),
                         ["createRefund", "getCustomer", "getOrder", "listOrders", "listRefunds", "searchCustomers"])


class AcmeBakery(Catalog):
    """The file of Acme Bakery lists no operation: its tools are what they were."""

    def setUp(self):
        super().setUp()
        self.platform.routes.clear()
        self.world = ToolWorld()
        self.answer(self.world.routes())
        self.real_files()
        operations = ["listBranches", "getBranch", "listRates"]
        self.world.apis["api-1"] = {"name": "Acme Bakery Open API", "operations": operations}

    def test_every_operation_of_the_page_is_imported_as_before_and_nothing_is_read_or_saved(self):
        self.run_seed()
        [call] = self.sent("POST", "/catalog/mcp-tools/_importFromApis")
        self.assertEqual([t["definition"]["name"] for t in call.json["tools"]],
                         ["listBranches", "getBranch", "listRates"])
        self.assertEqual(self.sent("PUT", "/catalog/mcp-tools/*"), [])
        self.assertEqual(self.sent("GET", "/catalog/mcp-tools/*"), [])
        self.assertIn("[aim-catalog] ✓ API tool imported: Acme Bakery Open API (3 tools)", self.output())

    def test_the_fingerprint_is_that_of_the_tools_sent_so_that_a_record_made_before_still_counts(self):
        self.run_seed()
        [call] = self.sent("POST", "/catalog/mcp-tools/_importFromApis")
        [record] = self.records("api-tools")
        self.assertEqual(record["digest"], aim_catalog.fingerprint(call.json["tools"]))
        self.assertEqual(self.run_seed(), [])

    def test_the_file_has_no_operations_and_so_none_are_asked(self):
        [spec] = list(aim_catalog.specs("api-tools", "api"))
        self.assertNotIn("operations", spec)
        self.assertIsNone(aim_catalog.operations_of(spec))


# --- Agents ---------------------------------------------------------------------------------

COPILOT = f"""\
entityId: care-copilot
source: Harbor
url: {AGENT}
metadata:
  riskClassification: limited
identity:
  clientId: care-copilot
access:
  - {{studio: Customer care, plan: Agents}}
"""
BARE = f"entityId: care-copilot\nsource: Harbor\nurl: {AGENT}\n"


class Metadata(Tools):
    """An agent is registered from its card with the metadata its file gives it."""

    def setUp(self):
        super().setUp()
        self.world.card = {**self.world.card, "name": "Care copilot"}
        self.seed_files({"aim/agents/care-copilot.yaml": COPILOT})

    def agent(self):
        return self.world.named("agent", "care-copilot")

    def test_the_agent_is_made_with_its_card_and_its_metadata_under_the_entity_id_the_file_gives(self):
        self.assertEqual(self.run_seed(), [("POST", "/catalog/sources"), ("POST", "/catalog/agents")])
        [call] = self.sent("POST", "/catalog/agents")
        self.assertEqual(sorted(call.json), ["definition", "entityId", "metadata", "sourceId"])
        self.assertEqual((call.json["entityId"], call.json["metadata"]),
                         ("care-copilot", {"riskClassification": "limited"}))
        self.assertEqual(self.agent()["entityId"], "agent.care-copilot")
        self.assertEqual(self.agent()["metadata"], {"riskClassification": "limited"})
        self.assertEqual(self.agent()["definition"]["name"], "Care copilot")
        self.assertEqual([x["name"] for x in self.records("agent")], ["Care copilot"])

    def test_what_another_module_reads_is_not_sent_and_does_not_make_the_seed_fail(self):
        self.run_seed()
        [call] = self.sent("POST", "/catalog/agents")
        for key in ("identity", "access"):
            self.assertNotIn(key, call.json)
            self.assertNotIn(key, call.json["definition"])
            self.assertNotIn(key, call.json["metadata"])

    def test_a_second_run_writes_nothing_and_a_change_of_what_another_module_reads_does_not_write_either(self):
        self.run_seed()
        self.assertEqual(self.run_seed(), [])
        self.seed_files({"aim/agents/care-copilot.yaml": COPILOT.replace("clientId: care-copilot", "clientId: other")})
        self.assertEqual(self.run_seed(), [])

    def test_every_risk_classification_the_catalog_knows_is_accepted(self):
        for risk in ("negligible", "limited", "moderate", "high"):
            with self.subTest(risk=risk):
                self.world.__init__()
                self.world.card = {**self.world.card, "name": "Care copilot"}
                common.save_created([])
                self.seed_files({"aim/agents/care-copilot.yaml": COPILOT.replace("limited", risk)})
                self.run_seed()
                self.assertEqual(self.agent()["metadata"], {"riskClassification": risk})

    def test_a_risk_classification_the_catalog_does_not_know_stops_the_seed_before_the_card_is_read(self):
        for risk in ("critical", "Limited", "unacceptable", "''", "[high]", "3", ""):
            with self.subTest(risk=risk):
                self.seed_files({"aim/agents/care-copilot.yaml": COPILOT.replace("limited", risk)})
                self.platform.calls.clear()
                with self.assertRaises(common.SeedError) as stopped:
                    aim_catalog.seed()
                self.assertRegex(str(stopped.exception),
                                 r"^The agent 'care-copilot': riskClassification .* is not one of "
                                 r"negligible, limited, moderate, high$")
                self.assertEqual(self.platform.sent("GET", CARD), [])
                self.assertEqual(self.platform.changes(), [])

    def test_metadata_is_a_mapping(self):
        for text in ("metadata: high\n", "metadata: [limited]\n"):
            with self.subTest(text=text):
                file = COPILOT.replace("metadata:\n  riskClassification: limited\n", text)
                self.seed_files({"aim/agents/care-copilot.yaml": file})
                message = "The agent 'care-copilot': metadata is a mapping of keys to values"
                with self.assertRaisesRegex(common.SeedError, message):
                    self.run_seed()

    def test_an_agent_without_metadata_is_registered_as_before(self):
        self.seed_files({"aim/agents/care-copilot.yaml": BARE})
        self.run_seed()
        [call] = self.sent("POST", "/catalog/agents")
        self.assertEqual(sorted(call.json), ["definition", "entityId", "sourceId"])
        self.assertEqual(self.run_seed(), [])

    def test_the_metadata_of_a_registered_agent_that_changed_is_replaced_with_the_card(self):
        self.run_seed()
        self.seed_files({"aim/agents/care-copilot.yaml": COPILOT.replace("limited", "moderate")})
        self.assertEqual(self.run_seed(), [("PUT", f"/catalog/agents/{self.agent()['id']}")])
        self.assertEqual(self.agent()["metadata"], {"riskClassification": "moderate"})
        self.assertEqual(self.run_seed(), [])

    def test_a_replace_keeps_what_compliance_wrote_in_the_metadata_of_the_agent(self):
        self.run_seed()
        answers = {"compliance.intendedPurpose": "Helps the care team", "compliance.riskAssessment": "RA-14"}
        self.agent()["metadata"].update(answers)
        self.assertEqual(self.run_seed(), [])   # what another module wrote is no change of the file
        self.world.card = {**self.world.card, "description": "Another description"}
        self.seed_files({"aim/agents/care-copilot.yaml": COPILOT.replace("limited", "high")})
        self.run_seed()
        [call] = self.sent("PUT", "/catalog/agents/*")
        self.assertEqual(call.json["metadata"], {"riskClassification": "high", **answers})
        self.assertEqual(self.agent()["metadata"], {"riskClassification": "high", **answers})
        self.assertEqual(self.run_seed(), [])

    def test_a_replace_of_an_agent_whose_file_has_no_metadata_keeps_the_metadata_it_has(self):
        self.run_seed()
        self.agent()["metadata"]["compliance.riskAssessment"] = "RA-14"
        self.world.card = {**self.world.card, "description": "Another description"}
        self.seed_files({"aim/agents/care-copilot.yaml": BARE})
        self.run_seed()
        [call] = self.sent("PUT", "/catalog/agents/*")
        self.assertEqual(call.json["metadata"], {"riskClassification": "limited", "compliance.riskAssessment": "RA-14"})

    def test_an_agent_that_the_seed_did_not_create_is_left_alone(self):
        theirs = self.world.add_item("agent", "agent.care-copilot", "s", {"name": "Theirs"},
                                     metadata={"riskClassification": "high"})
        message = "The A2A agent 'agent.care-copilot' exists and was not created by this playground"
        with self.assertRaisesRegex(common.SeedError, message):
            self.run_seed()
        self.assertEqual((theirs["definition"], theirs["metadata"]),
                         ({"name": "Theirs"}, {"riskClassification": "high"}))
        self.assertEqual([m for m, _ in self.platform.changes() if m in ("PUT", "DELETE")], [])


if __name__ == "__main__":
    unittest.main()
