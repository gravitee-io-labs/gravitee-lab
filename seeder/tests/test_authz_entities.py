"""Authorization Management for a playground whose policies are about its own data: the entities of a file (customers and
the orders that point at them), saved in bulk and only when they differ, the placeholder that names an agent in a policy,
the one schema document of the environment, and the message of a policy the platform cannot read. The platform is a fake
that keeps what it is given; the files of the personal playground "agent-toolbox" run through it when they are there."""

import json
import re
import unittest
from unittest import mock

from lab_seeder import authz
from lab_seeder import common
from lab_seeder.testkit import PERSONAL, PLAYGROUNDS, Seed
from tests.test_authz import AIM, AUTHZ, World

# The playground is personal until it is shared: the same seed in either place
HARBOR_SEED = next((seed for seed in (PERSONAL / "agent-toolbox" / "seed",
                                      PLAYGROUNDS / "agent-toolbox" / "seed") if seed.is_dir()),
                   PERSONAL / "agent-toolbox" / "seed")
CARE_COPILOT = "67be365c-85ec-3ae7-a61d-9d9021c0ad0f"   # what the gateway makes of "care-copilot" (UUID.nameUUIDFromBytes)
BULK = AUTHZ + "/entities/_bulk"
SCHEMA = AUTHZ + "/schema"
LISTING = AUTHZ + "/entities"

SCHEMA_TEXT = "entity User { region?: String };\nentity Customer { region: String };\n"
ENTITIES = """\
source: harbor
entities:
  - {type: Order, id: O-5001, attributes: {_displayName: O-5001, customer: {ref: [Customer, C-1001]}}}
  - {type: Customer, id: C-1001, attributes: {_displayName: Léa Martin, region: EU, tier: gold}}
  - {type: Customer, id: C-2001, attributes: {_displayName: Emma Walker, region: US, tier: platinum}}
  - {type: Order, id: O-6001, attributes: {_displayName: O-6001, customer: {ref: [Customer, C-2001]}}}
"""
POLICY = """\
name: Support reads customers of their own region
status: DEPLOYED
policy: |
  @id("Support reads customers of their own region")
  permit (principal, action == Action::"get_customer", resource is MCPTool)
    when { context has agent && context.agent == AgentIdentity::"<agent:care-copilot>" };

  @id("Support searches customers of their own region")
  permit (principal, action == Action::"search_customers", resource is MCPTool)
    when { context has agent && context.agent == AgentIdentity::"<agent:care-copilot>" };
"""


def syntax_error(text):
    """What the platform says of a policy it cannot read, as far as a fake can tell: brackets that do not close and a
    statement that does not end. None when the text can be read."""
    pairs = {")": "(", "}": "{", "]": "["}
    stack = []
    for character in text:
        if character in "({[":
            stack.append(character)
        elif character in pairs and (not stack or stack.pop() != pairs[character]):
            return f"line {text[:text.index(character)].count(chr(10)) + 1}: unexpected '{character}'"
    if stack:
        return "line 1:20\n  missing ')' at the end\n  the statement is not complete"
    statements = len(re.findall(r"^(?:permit|forbid) \(", text, re.M))
    if text.count(";") != statements:
        return "a statement ends with ';'"
    return None


class Platform(World):
    """Authorization Management as the live build answers it: the bulk save of entities, the list of a source, the one
    schema document, and the policies it reads when they are saved."""

    def __init__(self):
        super().__init__()
        self.schema_text = None          # the document of the environment: None when there is none
        self.schema_problem = None       # what validation says of any schema: a list of errors, or None for a good one
        self.schema_validation = (200,)  # the status of the validation route
        self.refused = {}                # entityId -> why the bulk save refuses it
        self.bulks = []                  # the ids of each bulk request
        self.listed = []                 # the parameters of each listing

    def routes(self):
        routes = super().routes()
        routes.update({
            ("GET", LISTING): self.list_entities,
            ("POST", BULK): self.bulk,
            ("GET", SCHEMA): self.read_schema,
            ("PUT", SCHEMA): self.write_schema,
            ("DELETE", SCHEMA): self.delete_schema,
            ("POST", SCHEMA + "/validate"): self.validate_schema,
        })
        return routes

    # Entities
    def list_entities(self, call):
        self.listed.append(dict(call.params))
        wanted = call.params.get("entityId")
        wanted = [wanted] if isinstance(wanted, str) else wanted
        rows = [entity for entity in self.entities.values()
                if (not call.params.get("source") or entity.get("source") == call.params["source"])
                and (wanted is None or entity["entityId"] in wanted)]
        page, size = int(call.params.get("page", 1)), int(call.params.get("perPage", 20))
        return 200, {"data": rows[(page - 1) * size:page * size], "total": len(rows), "page": page, "perPage": size}

    def bulk(self, call):
        entities = call.json["entities"]
        if len(entities) > 10000:
            return 400, {"message": "too many entities"}
        self.bulks.append([entity["entityId"] for entity in entities])
        errors, results = [], []
        for index, entity in enumerate(entities):
            if entity["entityId"] in self.refused:
                errors.append({"index": index, "message": self.refused[entity["entityId"]]})
                continue
            results.append({"entityId": entity["entityId"], "status": "REPLACED" if entity["entityId"] in self.entities else "CREATED"})
            self.entities[entity["entityId"]] = {"id": f"entity-{next(self.ids)}", **entity}
        return 200, {"status": "PARTIAL" if errors else "OK", "created": sum(r["status"] == "CREATED" for r in results),
                     "replaced": sum(r["status"] == "REPLACED" for r in results), "failed": len(errors), "results": results,
                     "errors": errors}

    # The schema document of the environment
    def read_schema(self, call):
        if self.schema_text is None:
            return 404, {"message": "No schema"}
        return 200, {"schema": self.schema_text, "updatedAt": "2026-10-13T08:00:00Z"}

    def validate_schema(self, call):
        status = self.schema_validation[0]
        if status != 200:
            return status, {"message": "validation said " + str(status)} if status == 400 else {}
        problems = self.schema_problem
        return 200, {"valid": not problems, "errors": problems or []}

    def write_schema(self, call):
        self.schema_text = call.json["schema"]
        return 200, {"schema": self.schema_text, "updatedAt": "2026-10-13T08:01:00Z"}

    def delete_schema(self, call):
        self.schema_text = None
        return 204

    # Policies are read when they are saved
    def create_policy(self, call):
        problem = syntax_error(call.json["policyText"])
        if problem:
            return 400, {"error": "InvalidArgument", "message": problem}
        return super().create_policy(call)

    def change_policy(self, call):
        problem = syntax_error(call.json["policyText"])
        if problem:
            return 400, {"error": "InvalidArgument", "message": problem}
        return super().change_policy(call)

    # What a test looks at
    def by_type(self, entity_type):
        return sorted(e["entityId"] for e in self.entities.values() if e.get("entityType") == entity_type)


class Case(Seed):

    def setUp(self):
        super().setUp()
        self.world = Platform()
        self.platform.add(self.world.routes())

    def seeded(self):
        authz.seed()
        self.platform.calls.clear()

    def bulk_calls(self):
        return self.platform.sent("POST", BULK)

    def records(self):
        return [x for x in common.created() if x["kind"] == "entities"]


def customers_and_orders(customers):
    """The entities of a file with that many customers, each with an order: the file as YAML (JSON is YAML)."""
    entities = []
    for number in range(customers):
        entities.append({"type": "Order", "id": f"O-{number}", "attributes": {"customer": {"ref": ["Customer", f"C-{number}"]}}})
        entities.append({"type": "Customer", "id": f"C-{number}", "attributes": {"region": "EU" if number % 2 else "US"}})
    return json.dumps({"source": "harbor", "entities": entities})


class References(unittest.TestCase):
    """An attribute that is {ref: [Type, id]} is a reference to an entity, wherever it stands."""

    def test_a_reference_becomes_the_entity_reference_of_the_engine(self):
        self.assertEqual(authz.reference({"customer": {"ref": ["Customer", "C-1001"]}}, "x"),
                         {"customer": {"__entity": {"type": "Customer", "id": "C-1001"}}})

    def test_a_reference_in_a_list_or_a_record_is_one_too(self):
        out = authz.reference({"all": [{"ref": ["Order", "O-1"]}, {"ref": {"type": "Order", "id": "O-2"}}],
                               "nested": {"last": {"ref": ["Order", 3]}}}, "x")
        self.assertEqual(out["all"], [{"__entity": {"type": "Order", "id": "O-1"}}, {"__entity": {"type": "Order", "id": "O-2"}}])
        self.assertEqual(out["nested"]["last"], {"__entity": {"type": "Order", "id": "3"}}, "a number is an id too")

    def test_plain_values_stay_what_they_are(self):
        plain = {"region": "EU", "total": 158, "ok": True, "tags": ["a", "b"], "ref": "not a mapping", "deep": {"ref": 1, "also": 2}}
        self.assertEqual(authz.reference(plain, "x"), plain)

    def test_a_reference_that_is_not_a_type_and_an_id_is_an_error_that_says_where(self):
        for bad in ({"ref": "C-1001"}, {"ref": ["Customer"]}, {"ref": ["Customer", ""]}, {"ref": [1, 2, 3]}, {"ref": None}):
            with self.subTest(bad=bad), self.assertRaisesRegex(common.SeedError, "where it stands, ref: expected \\[Type, id\\]"):
                authz.reference({"customer": bad}, "where it stands")


class Entities(unittest.TestCase):
    """The entities of a file as the bulk API takes them."""

    @staticmethod
    def read(text):
        import yaml
        return authz.data_entities(yaml.safe_load(text))

    def test_a_parent_and_what_an_attribute_points_at_come_before_the_entity(self):
        source, bodies, ranks = self.read(ENTITIES)
        self.assertEqual(source, "harbor")
        self.assertEqual([b["entityId"] for b in bodies], ["C-1001", "O-5001", "C-2001", "O-6001"],
                         "O-5001 is first in the file and points at C-1001: its customer comes first")
        self.assertEqual(ranks, {"Order": 1, "Customer": 0}, "a type that points at another is deleted before it")

    def test_an_entity_is_a_resource_unless_it_is_a_user_a_group_or_an_agent(self):
        _, bodies, _ = self.read("""
entities:
  - {type: Group, id: care, attributes: {}}
  - {type: User, id: dana, attributes: {region: EU}, parents: [[Group, care]]}
  - {type: AgentIdentity, id: copilot}
  - {type: Order, id: O-1}
  - {type: Order, id: O-2, kind: PRINCIPAL}
""")
        self.assertEqual({b["entityId"]: b["kind"] for b in bodies},
                         {"care": "PRINCIPAL", "dana": "PRINCIPAL", "copilot": "PRINCIPAL", "O-1": "RESOURCE", "O-2": "PRINCIPAL"})
        self.assertEqual(next(b for b in bodies if b["entityId"] == "dana")["parents"], [{"entityType": "Group", "entityId": "care"}])

    def test_the_body_is_what_the_bulk_api_takes(self):
        _, bodies, _ = self.read(ENTITIES)
        order = next(b for b in bodies if b["entityId"] == "O-5001")
        self.assertEqual(order, {
            "entityId": "O-5001", "kind": "RESOURCE", "entityType": "Order", "source": "harbor", "targets": [], "parents": [],
            "attributes": {"_displayName": "O-5001", "customer": {"__entity": {"type": "Customer", "id": "C-1001"}}}})

    def test_a_file_that_names_no_source_has_one_of_the_lab(self):
        source, _, _ = self.read("entities: [{type: Customer, id: C-1}]")
        self.assertEqual(source, authz.SOURCE)

    def test_a_cycle_is_cut_and_every_entity_stays(self):
        _, bodies, ranks = self.read("""
entities:
  - {type: A, id: a1, attributes: {other: {ref: [B, b1]}}}
  - {type: B, id: b1, attributes: {other: {ref: [A, a1]}}}
""")
        self.assertEqual(sorted(b["entityId"] for b in bodies), ["a1", "b1"])
        self.assertEqual(sorted(ranks), ["A", "B"])

    def test_a_file_that_is_not_right_stops_in_one_line_that_says_which_entity(self):
        for text, why in [
            ("entities: [3]", "entity 1: expected a type, an id and attributes"),
            ("entities: [{type: Customer}]", "entity 1: type and id are needed"),
            ("entities: [{id: C-1}]", "entity 1: type and id are needed"),
            ("entities: [{type: 'Cust omer', id: C-1}]", "entity 1 \\(Cust omer 'C-1'\\): the type is made of"),
            ("entities: [{type: Customer, id: 'C 1'}]", "entity 1 \\(Customer 'C 1'\\): the id is made of"),
            ("entities: [{type: Customer, id: C-1}, {type: Order, id: C-1}]", "entity 2 \\(Order 'C-1'\\): the id is used twice"),
            ("entities: [{type: Customer, id: C-1, attributes: [1]}]", "entity 1 \\(Customer 'C-1'\\): attributes are a mapping"),
            ("entities: [{type: Customer, id: C-1, kind: THING}]", "entity 1 \\(Customer 'C-1'\\): kind is PRINCIPAL or RESOURCE"),
            ("entities: [{type: Customer, id: C-1, parents: [Group]}]", "parent: expected \\[Type, id\\]"),
            ("source: " + "x" * 65 + "\nentities: []", "source is a name of at most 64 characters"),
        ]:
            with self.subTest(text=text), self.assertRaisesRegex(common.SeedError, why) as raised:
                self.read(text)
            self.assertNotIn("\n", str(raised.exception))
            self.assertTrue(str(raised.exception).startswith("seed/authz/entities.yaml: "))


class Bulk(Case):
    """The entities of the file are saved together, and only those that are missing or differ."""

    def setUp(self):
        super().setUp()
        self.seed_files({"authz/entities.yaml": ENTITIES})

    def test_all_of_them_go_in_one_request_with_a_customer_before_its_orders(self):
        authz.seed()
        self.assertEqual(self.world.bulks, [["C-1001", "O-5001", "C-2001", "O-6001"]])
        sent = self.bulk_calls()[0].json["entities"]
        self.assertEqual([e["entityType"] for e in sent], ["Customer", "Order", "Customer", "Order"])
        self.assertEqual(sent[1]["attributes"]["customer"], {"__entity": {"type": "Customer", "id": "C-1001"}})
        self.assertEqual(self.world.entities["C-1001"]["source"], "harbor")
        self.assertEqual(self.platform.sent("POST", AUTHZ + "/entities"), [], "not one request for each entity")

    def test_a_request_holds_at_most_a_thousand_entities(self):
        self.seed_files({"authz/entities.yaml": customers_and_orders(1250)})   # 2,500 entities
        authz.seed()
        self.assertEqual([len(ids) for ids in self.world.bulks], [1000, 1000, 500])
        self.assertEqual(len(self.world.entities), 2500)
        self.assertEqual(authz.BULK, 1000)
        order = [entity for ids in self.world.bulks for entity in ids]
        for number in (0, 624, 1249):
            self.assertLess(order.index(f"C-{number}"), order.index(f"O-{number}"))

    def test_what_the_platform_holds_of_the_source_is_listed_before_anything_is_sent(self):
        authz.seed()
        listing = self.platform.sent("GET", LISTING)
        self.assertEqual([call.params.get("source") for call in listing[:1]], ["harbor"])

    def test_a_second_seed_saves_nothing(self):
        self.seeded()
        authz.seed()
        self.assertEqual(self.platform.changes(), [])
        self.assertIn("4 entities of 'harbor' as the file says", self.output())

    def test_a_file_without_entities_asks_the_platform_for_none(self):
        self.seed_files({"authz/entities.yaml": "source: harbor\nentities: []\n"})
        authz.seed()
        self.assertEqual(self.platform.sent("GET", LISTING), [])
        self.assertEqual(self.bulk_calls(), [])

    def test_a_second_seed_of_thousands_lists_them_by_pages_and_saves_nothing(self):
        self.seed_files({"authz/entities.yaml": customers_and_orders(1250)})
        self.seeded()
        self.world.listed.clear()
        authz.seed()
        self.assertEqual(self.platform.changes(), [])
        self.assertEqual([call["page"] for call in self.world.listed], list(range(1, 26)), "2,500 entities, a hundred a page")

    def test_only_the_entity_that_changed_and_the_one_that_is_gone_are_sent(self):
        self.seeded()
        self.world.entities["C-1001"]["attributes"]["tier"] = "standard"
        del self.world.entities["O-6001"]
        authz.seed()
        self.assertEqual(self.world.bulks[-1], ["C-1001", "O-6001"])
        self.assertEqual(len(self.bulk_calls()), 1)
        self.assertEqual(self.world.entities["C-1001"]["attributes"]["tier"], "gold")
        self.assertIn("O-6001", self.world.entities)
        self.assertIn("Customer entities of 'harbor': 1 put back", self.output())
        self.assertIn("Order entities of 'harbor': 1 created", self.output())

    def test_a_parent_someone_changed_and_a_source_someone_changed_are_put_back(self):
        self.seed_files({"authz/entities.yaml": """
source: harbor
entities:
  - {type: Group, id: care, attributes: {}}
  - {type: User, id: dana, attributes: {region: EU}, parents: [[Group, care]]}
"""})
        self.seeded()
        self.world.entities["dana"]["parents"] = []
        self.world.entities["care"]["source"] = "someone"
        authz.seed()
        self.assertEqual(self.world.bulks[-1], ["care", "dana"])
        self.assertEqual((self.world.entities["care"]["source"], len(self.world.entities["dana"]["parents"])), ("harbor", 1))
        self.assertEqual(self.world.entities["dana"]["kind"], "PRINCIPAL")

    def test_attributes_the_platform_adds_are_left_alone(self):
        self.seeded()
        self.world.entities["C-1001"]["attributes"]["_importedAt"] = "2026-10-13"
        authz.seed()
        self.assertEqual(self.platform.changes(), [])

    def test_the_records_are_one_for_each_type_with_the_ids_in_the_order_they_were_saved(self):
        authz.seed()
        by_name = {x["name"]: x for x in self.records()}
        self.assertEqual(sorted(by_name), ["harbor/Customer", "harbor/Order"])
        customers, orders = by_name["harbor/Customer"], by_name["harbor/Order"]
        self.assertEqual((customers["ids"], customers["count"], customers["rank"]), (["C-1001", "C-2001"], 2, 0))
        self.assertEqual((orders["ids"], orders["count"], orders["rank"]), (["O-5001", "O-6001"], 2, 1))
        self.assertEqual((customers["label"], orders["label"]), ("Customer entities", "Order entities"))
        self.assertEqual({x["product"] for x in common.created()}, {"authz"})
        self.assertEqual(len(common.created()), 2)

    def test_a_second_seed_leaves_the_records_as_they_are(self):
        authz.seed()
        before = common.created()
        authz.seed()
        self.assertEqual(common.created(), before)

    def test_a_new_entity_of_a_known_type_joins_the_record_of_its_type(self):
        authz.seed()
        self.seed_files({"authz/entities.yaml": ENTITIES + "  - {type: Customer, id: C-1002, attributes: {region: EU}}\n"})
        authz.seed()
        customers = next(x for x in self.records() if x["name"] == "harbor/Customer")
        self.assertEqual((customers["ids"], customers["count"]), (["C-1001", "C-2001", "C-1002"], 3))
        self.assertEqual(self.world.bulks[-1], ["C-1002"])

    def test_an_entity_the_file_no_longer_has_stays_in_the_record_so_that_a_removal_deletes_it(self):
        authz.seed()
        self.seed_files({"authz/entities.yaml": """
source: harbor
entities:
  - {type: Customer, id: C-1001, attributes: {_displayName: Léa Martin, region: EU, tier: gold}}
  - {type: Customer, id: C-3001, attributes: {region: US}}
"""})
        authz.seed()
        customers = next(x for x in self.records() if x["name"] == "harbor/Customer")
        self.assertEqual(customers["ids"], ["C-2001", "C-1001", "C-3001"], "the stale id first: it is deleted last")

    def test_no_secret_or_attribute_is_recorded(self):
        authz.seed()
        self.assertNotIn("Léa", self.state.read_text(encoding="utf-8"))
        self.assertNotIn("EU", self.state.read_text(encoding="utf-8"))


class Strangers(Case):
    """What is there and the seed did not create is never taken over."""

    def setUp(self):
        super().setUp()
        self.seed_files({"authz/entities.yaml": ENTITIES})

    def test_an_entity_of_another_source_with_an_id_of_the_file_stops_the_seed_before_anything_is_saved(self):
        self.world.entities["C-2001"] = {"id": "x", "entityId": "C-2001", "entityType": "Customer", "kind": "RESOURCE",
                                         "attributes": {"mine": True}, "parents": [], "source": "someone"}
        with self.assertRaisesRegex(common.SeedError, "Customer 'C-2001' exists in Authorization Management and the seed did not "
                                                      "create it: it is left alone"):
            authz.seed()
        self.assertEqual(self.platform.changes(), [])
        self.assertEqual(self.world.entities["C-2001"]["attributes"], {"mine": True})
        asked = [call.params["entityId"] for call in self.platform.sent("GET", LISTING) if "entityId" in call.params]
        self.assertEqual(asked, [["C-1001", "O-5001", "C-2001", "O-6001"]], "the ids the source does not show, in one question")

    def test_an_entity_of_the_source_that_the_record_does_not_have_stops_the_seed_too(self):
        authz.seed()
        self.state.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(common.SeedError, "exists in Authorization Management and the seed did not create it"):
            authz.seed()
        self.assertEqual(len(self.bulk_calls()), 1, "nothing more was saved")

    def test_an_entity_the_seed_made_and_someone_gave_another_source_is_put_back(self):
        authz.seed()
        self.world.entities["C-2001"]["source"] = "someone"
        self.platform.calls.clear()
        authz.seed()
        self.assertEqual(self.world.bulks[-1], ["C-2001"])
        self.assertEqual(self.world.entities["C-2001"]["source"], "harbor")

    def test_the_ids_that_are_asked_after_are_asked_fifty_at_a_time(self):
        self.seed_files({"authz/entities.yaml": customers_and_orders(60)})   # 120 entities
        authz.seed()
        asked = [call.params["entityId"] for call in self.platform.sent("GET", LISTING) if "entityId" in call.params]
        self.assertEqual([len(ids) for ids in asked], [50, 50, 20])

    def test_a_removal_leaves_what_the_seed_did_not_make(self):
        self.world.entities["mine"] = {"id": "m", "entityId": "mine", "entityType": "Order", "source": "someone"}
        authz.seed()
        authz.remove(common.created())
        self.assertEqual(list(self.world.entities), ["mine"])


class Refusals(Case):
    """The platform refuses an entity: what it took is the seed's, and the reason is one line."""

    def setUp(self):
        super().setUp()
        self.seed_files({"authz/entities.yaml": ENTITIES})

    def test_a_refused_entity_stops_the_seed_after_the_rest_is_saved_and_recorded(self):
        self.world.refused["O-6001"] = "attribute 'customer' is not an entity\nline 2"
        with self.assertRaisesRegex(common.SeedError, r"^Authorization Management refused 1 of 4 entities: attribute 'customer' "
                                                     r"is not an entity line 2$"):
            authz.seed()
        self.assertEqual(self.world.by_type("Customer"), ["C-1001", "C-2001"])
        self.assertEqual(self.world.by_type("Order"), ["O-5001"])
        orders = next(x for x in self.records() if x["name"] == "harbor/Order")
        self.assertEqual(orders["ids"], ["O-5001"], "recorded: what the platform took, and nothing it refused")

    def test_the_next_seed_saves_the_one_that_was_refused_and_no_other(self):
        self.world.refused["O-6001"] = "no"
        with self.assertRaises(common.SeedError):
            authz.seed()
        self.world.refused.clear()
        authz.seed()
        self.assertEqual(self.world.bulks[-1], ["O-6001"])
        self.assertEqual(next(x for x in self.records() if x["name"] == "harbor/Order")["ids"], ["O-5001", "O-6001"])

    def test_a_request_the_platform_answers_with_an_error_says_what_it_said_in_one_line(self):
        self.platform.add({("POST", BULK): (400, {"message": "entity C-1001: the type 'Customer' is not\nallowed"})})
        with self.assertRaisesRegex(common.SeedError, r"^The entities refused by Authorization Management \(HTTP 400\): "
                                                     r"entity C-1001: the type 'Customer' is not allowed$"):
            authz.seed()
        self.assertEqual(self.records(), [])

    def test_a_failed_count_without_the_errors_is_still_a_refusal_and_the_request_is_taken_as_saved(self):
        self.platform.add({("POST", BULK): (200, {"status": "PARTIAL", "created": 3, "failed": 1, "results": [], "errors": []})})
        with self.assertRaisesRegex(common.SeedError, "^Authorization Management refused 1 of 4 entities: no reason given$"):
            authz.seed()
        self.assertEqual(sum(x["count"] for x in self.records()), 4, "recorded: deleting what is not there is no error")

    def test_a_file_that_is_wrong_changes_nothing(self):
        self.seed_files({"authz/entities.yaml": "source: harbor\nentities: [{type: Customer, id: C-1}, {type: Customer, id: C-1}]\n"})
        with self.assertRaisesRegex(common.SeedError, "the id is used twice"):
            authz.seed()
        self.assertEqual(self.platform.changes(), [])


class Removal(Case):

    def setUp(self):
        super().setUp()
        self.seed_files({"authz/entities.yaml": ENTITIES, "authz/policies/01-reads.yaml": POLICY})

    def deleted(self):
        return [path.rsplit("/", 2)[1:] for method, path in self.platform.changes() if method == "DELETE"]

    def test_the_orders_are_deleted_before_the_customers_they_point_at(self):
        authz.seed()
        self.platform.calls.clear()
        self.assertEqual(self.main("--remove"), 0, self.output())
        self.assertEqual(self.deleted(), [["policies", self.deleted()[0][1]], ["entities", "O-6001"], ["entities", "O-5001"],
                                          ["entities", "C-2001"], ["entities", "C-1001"]])
        self.assertEqual(self.world.entities, {})
        self.assertIn("✓ Deleted 2 Order entities of 'harbor'", self.output())
        self.assertIn("✓ Deleted 2 Customer entities of 'harbor'", self.output())

    def test_a_type_that_was_recorded_first_is_still_deleted_after_the_one_that_points_at_it(self):
        authz.seed()
        records = common.created()
        records.sort(key=lambda x: x["name"] != "harbor/Order")   # the record of the orders after that of the customers
        authz.remove(records)
        ids = [entity for kind, entity in self.deleted() if kind == "entities"]
        self.assertEqual(ids, ["O-6001", "O-5001", "C-2001", "C-1001"])

    def test_a_child_is_deleted_before_its_parent_of_the_same_type(self):
        self.seed_files({"authz/entities.yaml": """
source: harbor
entities:
  - {type: Folder, id: leaf, parents: [[Folder, middle]]}
  - {type: Folder, id: middle, parents: [[Folder, root]]}
  - {type: Folder, id: root}
"""})
        authz.seed()
        self.assertEqual(self.world.bulks[0], ["root", "middle", "leaf"])
        self.platform.calls.clear()
        authz.remove(common.created())
        self.assertEqual([entity for kind, entity in self.deleted() if kind == "entities"], ["leaf", "middle", "root"])

    def test_an_entity_that_is_already_gone_is_no_error(self):
        authz.seed()
        del self.world.entities["C-1001"]
        authz.remove(common.created())
        self.assertEqual(self.world.entities, {})

    def test_the_kinds_the_seed_records_are_in_the_order_of_a_removal(self):
        self.assertEqual(authz.ORDER, ["policy", "entities", "entity", "schema", "schema-document"])


class Server(Case):
    """A file may name an MCP server, or none: the server is optional wherever it is read."""

    def test_a_file_without_a_server_never_asks_the_catalog(self):
        self.seed_files({"authz/entities.yaml": ENTITIES, "authz/policies/01-reads.yaml": POLICY})
        authz.seed()
        self.assertEqual(self.platform.sent("GET", AIM + "/catalog/mcp-servers"), [])
        self.assertEqual(len(self.world.policies), 1)

    def test_a_playground_with_policies_only_needs_no_entities_file(self):
        self.seed_files({"authz/policies/01-reads.yaml": POLICY})
        authz.seed()
        self.assertEqual(len(self.world.policies), 1)
        self.assertEqual(self.bulk_calls(), [])

    def test_a_file_with_a_server_and_entities_makes_both_and_the_server_slug_fills_the_policies(self):
        self.seed_files({"authz/entities.yaml": ENTITIES.replace("source: harbor", "server: Customer care\ntools: [get_customer]\nsource: harbor"),
                         "authz/policies/01-reads.yaml": POLICY.replace('Action::"get_customer"', 'Action::"<server>.get_customer"')})
        self.world.servers = {"data": [{"id": "s", "slug": "customer-care", "definition": {"serverInfo": {"name": "Customer care"}}}],
                              "pagination": {"totalCount": 1}}
        authz.seed()
        self.assertIn("customer-care.get_customer", self.world.entities)
        self.assertIn("C-1001", self.world.entities)
        self.assertEqual(sorted(x["kind"] for x in common.created() if x["kind"] != "policy"), ["entities", "entities", "entity", "entity"])
        self.assertIn('Action::"customer-care.get_customer"', next(iter(self.world.policies.values()))["policyText"])

    def test_a_policy_that_names_the_server_of_a_file_without_one_stops_the_seed_in_one_line(self):
        self.seed_files({"authz/entities.yaml": ENTITIES, "authz/policies/01-reads.yaml": POLICY.replace(
            'Action::"get_customer"', 'Action::"<server>.get_customer"')})
        with self.assertRaisesRegex(common.SeedError, r"^Policy 'Support reads customers of their own region' names <server>, "
                                                     r"and seed/authz/entities.yaml has no server$"):
            authz.seed()

    def test_a_playground_that_seeds_no_authorization_makes_no_call(self):
        authz.seed()
        authz.remove([])
        self.assertEqual(self.platform.calls, [])


class Agents(Case):
    """<agent:CLIENT_ID> in a policy is the id the gateway makes of the client id."""

    def test_the_id_is_the_one_the_gateway_makes(self):
        self.assertEqual(authz.agent_id("care-copilot"), CARE_COPILOT)

    def test_the_placeholder_is_replaced_in_every_statement_of_a_policy_that_has_several(self):
        self.seed_files({"authz/policies/01-reads.yaml": POLICY})
        authz.seed()
        text = next(iter(self.world.policies.values()))["policyText"]
        self.assertEqual(text.count(f'AgentIdentity::"{CARE_COPILOT}"'), 2)
        self.assertNotIn("<agent:", text)
        self.assertEqual(len(self.world.policies), 1, "several statements, one policy")
        self.assertEqual(self.platform.sent("POST", AUTHZ + "/policies")[0].json["name"], "Support reads customers of their own region")

    def test_two_agents_in_one_policy_get_each_their_own_id(self):
        two = POLICY.replace("<agent:care-copilot>", "<agent:care-copilot>\" || context.agent == AgentIdentity::\"<agent:ace-agent>", 1)
        self.seed_files({"authz/policies/01-reads.yaml": two})
        authz.seed()
        text = next(iter(self.world.policies.values()))["policyText"]
        self.assertIn(f'"{CARE_COPILOT}" || context.agent == AgentIdentity::"{authz.agent_id("ace-agent")}"', text)

    def test_a_client_id_may_be_an_address(self):
        self.assertEqual(authz.fill('AgentIdentity::"<agent:https://agents.example.test/care>"', None, "x"),
                         f'AgentIdentity::"{authz.agent_id("https://agents.example.test/care")}"')

    def test_a_comparison_that_looks_like_a_placeholder_is_not_one(self):
        text = "when { context.amount < 5 && context.amount > 1 }"
        self.assertEqual(authz.fill(text, None, "x"), text)

    def test_a_second_seed_saves_nothing_and_an_edited_policy_is_put_back(self):
        self.seed_files({"authz/policies/01-reads.yaml": POLICY})
        self.seeded()
        authz.seed()
        self.assertEqual(self.platform.changes(), [])
        policy = next(iter(self.world.policies.values()))
        policy["policyText"] = policy["policyText"].replace(CARE_COPILOT, "0" * 8)
        authz.seed()
        self.assertEqual(self.platform.changes(), [("PUT", f"{AUTHZ}/policies/{policy['id']}")])
        self.assertIn(CARE_COPILOT, policy["policyText"])

    def test_the_schema_may_name_an_agent_too(self):
        self.seed_files({"authz/schema.yaml": "schema: |\n  // <agent:care-copilot>\n  entity User;\n"})
        authz.seed()
        self.assertIn(f"// {CARE_COPILOT}", self.world.schema_text)


class Policies(Case):
    """A policy is read by the platform when it is saved: what it says of one it cannot read is the reason the seed stops."""

    def test_a_policy_the_platform_cannot_read_stops_the_seed_with_its_message_in_one_line(self):
        broken = POLICY.replace("resource is MCPTool)\n    when", "resource is MCPTool\n    when", 1)
        self.seed_files({"authz/policies/01-reads.yaml": broken})
        with self.assertRaises(common.SeedError) as raised:
            authz.seed()
        message = str(raised.exception)
        self.assertEqual(message, "Policy 'Support reads customers of their own region' refused by Authorization Management "
                                  "(HTTP 400): line 1:20 missing ')' at the end the statement is not complete")
        self.assertNotIn("\n", message)
        self.assertEqual(self.world.policies, {})

    def test_the_same_when_an_edited_file_is_put_over_a_policy_that_was_good(self):
        self.seed_files({"authz/policies/01-reads.yaml": POLICY})
        authz.seed()
        self.seed_files({"authz/policies/01-reads.yaml": POLICY.replace("resource is MCPTool)\n    when", "resource is MCPTool\n    when", 1)})
        with self.assertRaisesRegex(common.SeedError, r"^Policy '.*' refused by Authorization Management \(HTTP 400\): "):
            authz.seed()
        self.assertIn("get_customer", next(iter(self.world.policies.values()))["policyText"])

    def test_the_other_ways_a_platform_words_a_refusal(self):
        for body, said in [({"error": "Bad request", "path": "/policies"}, "Bad request"),
                           ({"errors": [{"message": "no ';'"}]}, "no ';'"),
                           ({"errors": ["no ';'"]}, "no ';'"),
                           ({"title": "Invalid policy", "status": 400}, "Invalid policy"),
                           ("plain text\nover two lines", "plain text over two lines"),
                           ({"unknown": 1}, '{"unknown": 1}')]:
            with self.subTest(body=body):
                self.platform.add({("POST", AUTHZ + "/policies"): (400, body)})
                self.seed_files({"authz/policies/01-reads.yaml": POLICY})
                with self.assertRaises(common.SeedError) as raised:
                    authz.seed()
                self.assertTrue(str(raised.exception).endswith(f"(HTTP 400): {said}"), str(raised.exception))

    def test_a_server_that_is_down_is_not_reported_as_a_policy_that_is_wrong_but_still_in_one_line(self):
        self.platform.add({("POST", AUTHZ + "/policies"): (503, {"message": "restarting"})})
        self.seed_files({"authz/policies/01-reads.yaml": POLICY})
        with mock.patch("lab_seeder.gamma.PAUSE", 0), self.assertRaisesRegex(common.SeedError, r"\(HTTP 503\)"):
            authz.seed()

    def test_the_text_of_the_policies_is_not_changed_but_by_its_placeholders(self):
        self.seed_files({"authz/policies/01-reads.yaml": POLICY})
        authz.seed()
        sent = next(iter(self.world.policies.values()))["policyText"]
        self.assertEqual(sent.replace(CARE_COPILOT, "<agent:care-copilot>"), common.load(
            common.SEED_DIR / "authz/policies/01-reads.yaml")["policy"])


class SchemaDocument(Case):
    """The one schema document of the environment, for the editor."""

    def setUp(self):
        super().setUp()
        self.seed_files({"authz/schema.yaml": f"schema: |\n  {SCHEMA_TEXT.replace(chr(10), chr(10) + '  ').rstrip()}\n"})

    def test_it_is_validated_then_saved_and_recorded(self):
        authz.seed()
        self.assertEqual(self.world.schema_text.strip(), SCHEMA_TEXT.strip())
        self.assertEqual([(c.method, c.path) for c in self.platform.calls if "/schema" in c.path],
                         [("GET", SCHEMA), ("POST", SCHEMA + "/validate"), ("PUT", SCHEMA)])
        self.assertEqual([c.json for c in self.platform.calls if c.path == SCHEMA + "/validate"][0]["schema"].strip(), SCHEMA_TEXT.strip())
        recorded = [x for x in common.created() if x["kind"] == "schema-document"]
        self.assertEqual([(x["label"], x["id"]) for x in recorded], [("access schemas", "schema")])
        self.assertIn("✓ Schema created", self.output())

    def test_a_second_seed_saves_nothing_and_does_not_validate_what_is_there(self):
        self.seeded()
        authz.seed()
        self.assertEqual(self.platform.changes(), [])
        self.assertEqual(self.platform.sent("POST", SCHEMA + "/validate"), [])
        self.assertIn("Schema exists", self.output())

    def test_spacing_and_line_breaks_are_no_difference(self):
        self.seeded()
        self.world.schema_text = " ".join(SCHEMA_TEXT.split()) + "\n\n"
        authz.seed()
        self.assertEqual(self.platform.changes(), [])

    def test_a_schema_that_does_not_validate_is_a_warning_and_the_rest_is_made(self):
        self.world.schema_problem = [{"line": 3, "message": "unexpected token 'entity'\nexpected ';'"}]
        self.seed_files({"authz/entities.yaml": ENTITIES, "authz/policies/01-reads.yaml": POLICY})
        authz.seed()
        warning = next(line for line in self.output().splitlines() if "Schema skipped" in line)
        self.assertEqual(warning, "[authz] ! Schema skipped: line 3: unexpected token 'entity' expected ';'")
        self.assertIsNone(self.world.schema_text)
        self.assertEqual(self.platform.sent("PUT", SCHEMA), [])
        self.assertEqual((len(self.world.entities), len(self.world.policies)), (4, 1))
        self.assertEqual([x for x in common.created() if x["kind"] == "schema-document"], [])

    def test_errors_as_plain_text_are_a_warning_too(self):
        self.world.schema_problem = ["unexpected end of input"]
        authz.seed()
        self.assertIn("! Schema skipped: unexpected end of input", self.output())

    def test_a_validation_that_answers_400_is_a_warning_with_its_message(self):
        self.world.schema_validation = (400,)
        authz.seed()
        self.assertIn("! Schema skipped: The schema refused by Authorization Management (HTTP 400): validation said 400", self.output())
        self.assertIsNone(self.world.schema_text)

    def test_a_platform_without_the_validation_route_still_gets_the_schema(self):
        self.world.schema_validation = (404,)
        authz.seed()
        self.assertEqual(self.world.schema_text.strip(), SCHEMA_TEXT.strip())

    def test_a_save_the_platform_refuses_is_a_warning(self):
        self.platform.add({("PUT", SCHEMA): (400, {"message": "line 1: cannot parse"})})
        authz.seed()
        self.assertIn("! Schema skipped: The schema refused by Authorization Management (HTTP 400): line 1: cannot parse", self.output())

    def test_a_platform_that_answers_200_with_no_text_holds_no_schema(self):
        self.platform.add({("GET", SCHEMA): [(200, {"schema": "", "updatedAt": None}), (200, {}), (200, None)]})
        for _ in range(3):
            authz.seed()
            self.world.schema_text = None
        self.assertEqual(len(self.platform.sent("PUT", SCHEMA)), 3)

    def test_a_schema_of_somebody_else_is_left_alone_with_a_warning(self):
        self.world.schema_text = "entity Mine;"
        authz.seed()
        self.assertIn("! Schema skipped: one exists and the seed did not create it", self.output())
        self.assertEqual(self.world.schema_text, "entity Mine;")
        self.assertEqual(self.platform.changes(), [])

    def test_a_schema_that_is_the_same_but_not_the_seeds_is_left_and_not_recorded(self):
        self.world.schema_text = SCHEMA_TEXT
        authz.seed()
        self.assertEqual(self.platform.changes(), [])
        self.assertEqual(common.created(), [])

    def test_a_schema_the_seed_made_and_someone_edited_is_put_back(self):
        self.seeded()
        self.world.schema_text = "entity Edited;"
        authz.seed()
        self.assertEqual(self.platform.changes(), [("POST", SCHEMA + "/validate"), ("PUT", SCHEMA)])
        self.assertEqual(self.world.schema_text.strip(), SCHEMA_TEXT.strip())
        self.assertIn("✓ Schema put back", self.output())

    def test_a_removal_deletes_the_schema_it_made_and_not_another(self):
        authz.seed()
        authz.remove(common.created())
        self.assertIsNone(self.world.schema_text)
        self.assertEqual(len(self.platform.sent("DELETE", SCHEMA)), 1)
        self.world.schema_text = "entity Mine;"
        authz.remove([])
        authz.remove([x for x in common.created() if x["kind"] != "schema-document"])
        self.assertEqual(self.world.schema_text, "entity Mine;")

    def test_the_file_must_say_the_text(self):
        self.seed_files({"authz/schema.yaml": "name: Harbor\n"})
        with self.assertRaisesRegex(common.SeedError, "^seed/authz/schema.yaml: schema is the text of the schema$"):
            authz.seed()

    def test_a_schema_of_the_folder_of_documents_is_still_made_beside_it(self):
        self.seed_files({"authz/schemas/old.yaml": "name: Old\nschema: entity Old;\n"})
        self.platform.add({("GET", AUTHZ + "/schemas"): (200, []),
                           ("POST", AUTHZ + "/schemas"): (201, {"id": "s1"})})
        authz.seed()
        self.assertEqual(self.platform.sent("POST", AUTHZ + "/schemas")[0].json["name"], "Old")
        self.assertEqual(self.world.schema_text.strip(), SCHEMA_TEXT.strip())
        self.assertEqual(sorted(x["kind"] for x in common.created()), ["schema", "schema-document"])


@unittest.skipUnless(HARBOR_SEED.is_dir(), "the playground agent-toolbox is not on this machine")
class Harbor(Case):
    """The seed files of the playground, as they are, through the module and against the fake."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(common, "SEED_DIR", HARBOR_SEED)
        patch.start()
        self.addCleanup(patch.stop)
        self.spec = common.load(HARBOR_SEED / "authz" / "entities.yaml")
        self.policies = [common.load(path) for path in sorted((HARBOR_SEED / "authz" / "policies").iterdir())]

    def test_the_seed_runs_without_a_stop_and_makes_every_entity_policy_and_the_schema(self):
        authz.seed()
        self.assertEqual(len(self.world.entities), len(self.spec["entities"]))
        self.assertEqual(sorted(p["name"] for p in self.world.policies.values()), sorted(p["name"] for p in self.policies))
        self.assertEqual({p["status"] for p in self.world.policies.values()}, {"DEPLOYED"})
        self.assertTrue(self.world.schema_text)
        self.assertNotIn("! ", self.output(), "no warning: the schema is valid and every entity is taken")

    def test_the_entities_come_in_one_request_with_the_customers_first(self):
        authz.seed()
        self.assertEqual(len(self.world.bulks), 1)
        ids = self.world.bulks[0]
        customers = [i for i in ids if i.startswith("C-")]
        orders = [i for i in ids if i.startswith("O-")]
        self.assertEqual(ids[:len(customers)], customers, "the customers, then the orders")
        self.assertEqual(len(customers) + len(orders), len(ids))

    def test_every_order_points_at_a_customer_of_the_file_and_every_reference_is_an_engine_reference(self):
        authz.seed()
        for entity in self.world.entities.values():
            if entity["entityType"] == "Order":
                reference = entity["attributes"]["customer"]["__entity"]
                self.assertEqual(reference["type"], "Customer")
                self.assertIn(reference["id"], self.world.entities, entity["entityId"])
            self.assertEqual((entity["kind"], entity["source"], entity["targets"]), ("RESOURCE", self.spec["source"], []))

    def test_a_policy_that_names_the_agent_gets_the_id_the_gateway_makes_and_no_placeholder_is_left(self):
        authz.seed()
        named = {p["name"] for p in self.policies if "<agent:care-copilot>" in p["policy"]}
        self.assertTrue(named, "some policy is about the agent")
        for policy in self.world.policies.values():
            with self.subTest(policy=policy["name"]):
                self.assertEqual(re.findall(r"<[^>\s]*>", policy["policyText"]), [])
                self.assertEqual(f'AgentIdentity::"{CARE_COPILOT}"' in policy["policyText"], policy["name"] in named)

    def test_every_statement_has_an_id_that_a_person_reads(self):
        ids = []
        for policy in self.policies:
            text = policy["policy"]
            statements = re.findall(r"^(permit|forbid) \(", text, re.M)
            found = re.findall(r'^@id\("([^"\\]+)"\)\n(?:permit|forbid) \(', text, re.M)
            with self.subTest(policy=policy["name"]):
                self.assertTrue(statements)
                self.assertEqual(len(found), len(statements), "each statement has its @id just above it")
                for name in found:
                    self.assertRegex(name, r"^[A-Z][^\s]* \S", "a name, not a slug")
            ids += found
        self.assertEqual(len(ids), len(set(ids)), "an @id names one statement")

    def test_a_second_seed_saves_nothing(self):
        self.seeded()
        authz.seed()
        self.assertEqual(self.platform.changes(), [])

    def test_a_removal_leaves_nothing_of_it(self):
        authz.seed()
        self.assertEqual(self.main("--remove"), 0, self.output())
        self.assertEqual((self.world.entities, self.world.policies), ({}, {}))
        self.assertIsNone(self.world.schema_text)
        self.assertEqual(common.created(), [])


if __name__ == "__main__":
    unittest.main()
