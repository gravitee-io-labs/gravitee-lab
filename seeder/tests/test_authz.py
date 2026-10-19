"""Authorization Management: the files of Acme Bakery's seed are read as they are, and what they say is made in a platform
that keeps what it is given. A second seed saves nothing, what someone changed is put back, what is not the seed's is
never touched."""

import itertools
import re
import unittest
from unittest import mock

import yaml

from lab_seeder import authz
from lab_seeder import common
from lab_seeder import controls
from lab_seeder import seed
from lab_seeder.testkit import PLAYGROUNDS, Seed

ACME_SEED = PLAYGROUNDS / "acme-bakery" / "seed"
GAMMA = "/gamma/organizations/DEFAULT/environments/DEFAULT/modules"
AUTHZ, AIM = GAMMA + "/authz", GAMMA + "/aim"
SLUG = "acme-bakery-mcp"
POLICIES = ["Customers read their own accounts", "Customers move their own money", "Tellers help every customer",
            "Public tools for everyone signed in", "No transfer above 10,000", "Agents move at most EUR 500",
            "No zero or negative transfer", "Tellers may refund (draft)", "Open API tools for everyone signed in",
            "Customers see only their own accounts"]
API_TOOLS_POLICY = "Open API tools for everyone signed in"
ONLY_OWN_POLICY = "Customers see only their own accounts"
DRAFT_POLICY = "Tellers may refund (draft)"
CAP_POLICY = "Agents move at most EUR 500"
CATALOG = {"data": [{"id": "s0", "slug": "elsewhere", "definition": {"serverInfo": {"name": "Another server"}}},
                    {"id": "s1", "slug": SLUG, "definition": {"serverInfo": {"name": "Acme Bakery MCP"}}}],
           "pagination": {"totalCount": 2}}


def policy_files():
    return [common.load(path) for path in sorted((ACME_SEED / "authz" / "policies").iterdir())]


class Files(Seed):
    """What the seed says, before anything runs: the names, the statuses, the shape of each policy."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(common, "SEED_DIR", ACME_SEED)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_policies_are_the_ten_of_the_design_and_only_the_eighth_is_a_draft(self):
        specs = policy_files()
        self.assertEqual([spec["name"] for spec in specs], POLICIES)
        self.assertEqual([spec["status"] for spec in specs], ["DEPLOYED"] * 7 + ["DRAFT", "DEPLOYED", "DEPLOYED"])

    def test_every_statement_has_an_id_that_the_decisions_show_and_that_a_person_reads(self):
        """Decisions names a policy by the @id of the statement that matched: a name, the first statement of a policy
        has the policy's own."""
        ids = []
        for spec in policy_files():
            text = spec["policy"]
            statements = re.findall(r"^(permit|forbid) \(", text, re.M)
            found = re.findall(r'^@id\("([^"\\]+)"\)\n(?:permit|forbid) \(', text, re.M)
            with self.subTest(policy=spec["name"]):
                self.assertTrue(statements)
                self.assertEqual(len(found), len(statements), "each statement has its @id just above it")
                self.assertEqual(found[0], spec["name"])
                for name in found:
                    self.assertRegex(name, r"^[A-Z][^\s]* \S", "a name, not a slug")
            ids += found
        self.assertEqual(len(ids), len(set(ids)), "an @id names one statement")
        self.assertEqual(len(ids), 13)

    def test_a_text_is_whole_once_the_server_is_named(self):
        for spec in policy_files():
            text = spec["policy"].replace(authz.SERVER, SLUG)
            with self.subTest(policy=spec["name"]):
                self.assertEqual(re.findall(r"<[^>\s]*>", text), [], "a placeholder left")
                self.assertEqual((text.count("("), text.count("{"), text.count("[")),
                                 (text.count(")"), text.count("}"), text.count("]")))
                self.assertEqual(text.count(";"), len(re.findall(r"^(?:permit|forbid) \(", text, re.M)))
                if spec["name"] != API_TOOLS_POLICY:   # the tools of a REST API belong to no MCP server
                    self.assertIn(f'MCPServer::"{SLUG}"', text)

    def test_the_policies_name_tools_of_the_server_and_nothing_else(self):
        tools = set(common.load(ACME_SEED / "authz" / "entities.yaml")["tools"])
        for path in list((ACME_SEED / "authz" / "policies").iterdir()) + list((ACME_SEED / "authz" / "schemas").iterdir()):
            named = set(re.findall(r'Action::"<server>\.([a-z_]+)"', path.read_text(encoding="utf-8")))
            named |= set(re.findall(r'^\s*action "<server>\.([a-z_]+)"', path.read_text(encoding="utf-8"), re.M))
            with self.subTest(file=path.name):
                self.assertTrue(named <= tools, named - tools)

    def test_every_tool_of_the_studio_has_a_policy_that_permits_it_by_the_name_the_studio_asks_with(self):
        """The studio's authorization check denies what no policy permits: a tool of a server is asked as <server>.<tool>,
        one made from an API as the name a client sees, its alias. Both policies are for everyone signed in."""
        tools = yaml.safe_load((ACME_SEED / "aim" / "studios" / "acme-bakery-toolkit.yaml").read_text(encoding="utf-8"))["tools"]
        texts = {spec["name"]: spec["policy"] for spec in policy_files()}

        def permitted(name):
            self.assertNotIn(" when ", texts[name], "a condition would keep somebody out")
            return set(re.findall(r'Action::"([^"]+)"', texts[name]))

        for tool in tools:
            if "server" in tool:
                self.assertIn(f"<server>.{tool['tool']}", permitted("Public tools for everyone signed in"), tool)
            else:
                self.assertIn(tool["alias"], permitted(API_TOOLS_POLICY), tool)
        aliases = {tool["alias"] for tool in tools if "api" in tool}
        self.assertEqual(permitted(API_TOOLS_POLICY), aliases, "the policy permits the API tools of the studio and nothing else")

    def test_the_rules_the_design_states_are_in_the_policies(self):
        texts = {spec["name"]: spec["policy"] for spec in policy_files()}
        self.assertIn("context.amount > 10000", texts["No transfer above 10,000"])
        self.assertIn("context has agent && context has amount && context.amount > 500", texts["Agents move at most EUR 500"])
        self.assertIn("context.amount < 1", texts["No zero or negative transfer"])
        self.assertIn("principal.accounts.contains(context.account_id) && context has amount",
                      texts["Customers move their own money"])
        self.assertNotIn("transfer_funds", texts["Tellers help every customer"])
        self.assertIn("freeze_card", texts["Tellers help every customer"])
        self.assertNotIn("freeze_card", texts["Customers read their own accounts"])

    def test_a_customer_is_forbidden_by_name_what_belongs_to_somebody_else(self):
        """The forbids are for customers and the tools that name an account, and each is the opposite of a customer's
        permit, so none of them overrules one."""
        text = {spec["name"]: spec["policy"] for spec in policy_files()}[ONLY_OWN_POLICY]
        self.assertEqual(re.findall(r"^(permit|forbid) \(", text, re.M), ["forbid", "forbid"])
        listing, accounts = text.split("@id(")[1:]
        self.assertIn('Action::"<server>.list_accounts"', listing)
        self.assertIn("context.customer_id != principal.customer_id", listing)
        for tool in ("get_balance", "list_transactions", "transfer_funds"):
            self.assertIn(f'Action::"<server>.{tool}"', accounts)
        self.assertIn("context has account_id && !principal.accounts.contains(context.account_id)", accounts)
        self.assertEqual(text.count('principal.role == "customer"'), 2)
        for tool in ("freeze_card", "get_exchange_rate", "simulate_loan", "find_branches"):
            self.assertNotIn(tool, text)

    def test_the_draft_and_the_listing_forbid_are_written_in_the_forms_the_simulator_evaluates(self):
        """The Simulator evaluates a condition in the browser, where comparisons joined by && and || are read and `has`,
        `in` and a method call are not: written with one, a policy shows nothing in it."""
        texts = {spec["name"]: spec["policy"] for spec in policy_files()}
        for statement in (texts[DRAFT_POLICY], texts[ONLY_OWN_POLICY].split("@id(")[1]):
            condition = re.search(r"when \{(.*)\}", statement, re.S).group(1)
            with self.subTest(condition=condition):
                self.assertEqual(re.findall(r"\b(has|in|like|contains)\b", condition), [])
        self.assertIn('principal.role == "teller" && context.amount <= 100', texts[DRAFT_POLICY])

    def test_an_agent_has_the_id_the_gateway_makes_of_its_client_id(self):
        """The actor-id the gateway's decision log shows for the calls of Ace's application."""
        self.assertEqual(authz.agent_id("ace-agent"), "c1250c93-9871-3b36-9ae8-b3ed4e573f83")

    def test_a_policy_name_makes_the_entity_id_the_platform_gives_it(self):
        self.assertEqual([authz.slugify(name) for name in POLICIES[4:6] + POLICIES[7:]],
                         ["no-transfer-above-10-000", "agents-move-at-most-eur-500", "tellers-may-refund-draft",
                          "open-api-tools-for-everyone-signed-in", "customers-see-only-their-own-accounts"])

    def test_the_entities_are_made_of_the_slug_with_a_parent_before_what_names_it(self):
        made = authz.entities(common.load(ACME_SEED / "authz" / "entities.yaml"), SLUG)
        ids = [(x["entityType"], x["entityId"]) for x in made]
        self.assertEqual(len(made), 15)
        self.assertEqual(ids[:2], [("MCPServer", SLUG), ("MCPTool", f"{SLUG}.list_accounts")])
        self.assertIn(("User", "alice"), ids)
        self.assertIn(("AgentIdentity", authz.agent_id("ace-agent")), ids)
        seen = set()
        for x in made:
            self.assertTrue({(p["entityType"], p["entityId"]) for p in x["parents"]} <= seen, x["entityId"])
            seen.add((x["entityType"], x["entityId"]))
        alice = next(x for x in made if x["entityId"] == "alice")
        self.assertEqual(alice["attributes"]["accounts"], ["ACC-1001", "ACC-1002"])
        self.assertEqual(alice["parents"], [{"entityType": "Group", "entityId": "customers"}])
        tina = next(x for x in made if x["entityId"] == "tina")
        self.assertNotIn("accounts", tina["attributes"])


class World:
    """Authorization Management and the AI catalog, keeping what they are given and answering from it."""

    def __init__(self, servers=CATALOG):
        self.ids = itertools.count(1)
        self.entities, self.policies, self.schemas = {}, {}, {}
        self.servers = servers
        self.schema_refused = None

    def routes(self):
        return {
            ("GET", AIM + "/catalog/mcp-servers"): lambda call: (200, self.servers),
            ("GET", AUTHZ + "/policies"): lambda call: (200, {"data": list(self.policies.values()),
                                                              "total": len(self.policies)}),
            ("POST", AUTHZ + "/policies"): self.create_policy,
            ("PUT", re.compile(AUTHZ + "/policies/[^/]+")): self.change_policy,
            ("POST", re.compile(AUTHZ + "/policies/[^/]+/deploy")): self.set_status("DEPLOYED"),
            ("POST", re.compile(AUTHZ + "/policies/[^/]+/disable")): self.set_status("DISABLED"),
            ("DELETE", re.compile(AUTHZ + "/policies/[^/]+")): self.deleting(self.policies),
            ("GET", re.compile(AUTHZ + "/entities/[^/]+")): self.get_entity,
            ("POST", AUTHZ + "/entities"): self.save_entity,
            ("DELETE", re.compile(AUTHZ + "/entities/[^/]+")): self.deleting(self.entities),
            ("GET", AUTHZ + "/schemas"): lambda call: (200, list(self.schemas.values())),
            ("POST", AUTHZ + "/schemas"): self.create_schema,
            ("PUT", re.compile(AUTHZ + "/schemas/[^/]+")): self.change_schema,
            ("DELETE", re.compile(AUTHZ + "/schemas/[^/]+")): self.deleting(self.schemas),
        }

    @staticmethod
    def last(call):
        return call.path.rsplit("/", 1)[1]

    def deleting(self, collection):
        def delete(call):
            collection.pop(self.last(call), None)
            return 204
        return delete

    # Policies
    def create_policy(self, call):
        made = {"id": f"policy-{next(self.ids)}", "entityId": "policy." + authz.slugify(call.json["name"]),
                "status": "DRAFT", **{k: call.json[k] for k in ("name", "kind", "policyType", "policyText", "targets")}}
        self.policies[made["id"]] = made
        return 201, made

    def change_policy(self, call):
        policy = self.policies[self.last(call)]
        policy.update(name=call.json["name"], policyText=call.json["policyText"])
        return 200, policy

    def set_status(self, status):
        def change(call):
            policy = self.policies[call.path.split("/")[-2]]
            policy["status"] = status
            return 200, policy
        return change

    # Entities: saving one replaces the one with that id
    def get_entity(self, call):
        entity = self.entities.get(self.last(call))
        return (200, entity) if entity else (404, {"message": "EntityNotFound"})

    def save_entity(self, call):
        existed = call.json["entityId"] in self.entities
        self.entities[call.json["entityId"]] = {"id": f"entity-{next(self.ids)}", "active": True, **call.json}
        return (200 if existed else 201), self.entities[call.json["entityId"]]

    # Schemas
    def create_schema(self, call):
        if self.schema_refused:
            return 400, {"message": self.schema_refused}
        made = {"id": f"schema-{next(self.ids)}", **call.json}
        self.schemas[made["id"]] = made
        return 201, made

    def change_schema(self, call):
        self.schemas[self.last(call)].update(call.json)
        return 200, self.schemas[self.last(call)]


class Authorization(Seed):

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(common, "SEED_DIR", ACME_SEED)
        patch.start()
        self.addCleanup(patch.stop)
        self.world = World()
        self.platform.add(self.world.routes())

    def seeded(self):
        authz.seed()
        self.platform.calls.clear()

    def by_name(self, name):
        return next(p for p in self.world.policies.values() if p["name"] == name)

    def test_the_seed_makes_the_schema_the_entities_and_the_policies_and_records_them(self):
        authz.seed()
        self.assertEqual((len(self.world.schemas), len(self.world.entities), len(self.world.policies)), (1, 15, 10))
        self.assertEqual({x["label"] for x in common.created()}, {"access schemas", "access subjects", "access rules"})
        self.assertEqual({x["product"] for x in common.created()}, {"authz"})
        self.assertEqual(len(common.created()), 26)
        self.assertEqual(sorted(p["name"] for p in self.world.policies.values()), sorted(POLICIES))

    def test_the_ids_are_made_of_the_slug_the_catalog_gave_the_server(self):
        authz.seed()
        self.assertIn(f"{SLUG}.transfer_funds", self.world.entities)
        self.assertEqual(self.world.entities[f"{SLUG}.transfer_funds"]["parents"],
                         [{"entityType": "MCPServer", "entityId": SLUG}])
        self.assertIn(f'Action::"{SLUG}.freeze_card"', self.by_name("Tellers help every customer")["policyText"])
        self.assertIn(f'action "{SLUG}.get_balance"', next(iter(self.world.schemas.values()))["schema"])
        self.assertTrue(all(authz.SERVER not in p["policyText"] for p in self.world.policies.values()))

    def test_another_slug_is_followed(self):
        self.world.servers = {"data": [{"id": "s", "slug": "bank-2", "definition": {"serverInfo": {"name": "Acme Bakery MCP"}}}],
                              "pagination": {"totalCount": 1}}
        authz.seed()
        self.assertIn("bank-2.get_balance", self.world.entities)
        self.assertNotIn(SLUG, str(self.world.entities.keys()))

    def test_a_server_is_found_by_the_slug_the_catalog_makes_of_its_name_when_the_list_does_not_carry_its_name(self):
        self.world.servers = {"data": [{"id": "s", "slug": SLUG}], "pagination": {"totalCount": 1}}
        authz.seed()
        self.assertIn(f"{SLUG}.get_balance", self.world.entities)

    def test_nine_policies_are_deployed_and_the_draft_is_left_alone(self):
        authz.seed()
        self.assertEqual(sorted(p["status"] for p in self.world.policies.values()), ["DEPLOYED"] * 9 + ["DRAFT"])
        self.assertEqual(self.by_name(DRAFT_POLICY)["status"], "DRAFT")
        self.assertEqual(len(self.platform.sent("POST", re.compile(AUTHZ + "/policies/[^/]+/deploy"))), 9)

    def test_a_policy_is_made_for_the_mcp_tools_and_aimed_at_no_target(self):
        authz.seed()
        sent = self.platform.sent("POST", AUTHZ + "/policies")[0].json
        self.assertEqual((sent["kind"], sent["policyType"], sent["targets"]), ("RESOURCE", "MCP", []))

    def test_a_second_seed_saves_nothing(self):
        self.seeded()
        authz.seed()
        self.assertEqual(self.platform.changes(), [])
        self.assertEqual(len(common.created()), 26)

    def test_a_policy_someone_edited_is_put_back_and_stays_deployed(self):
        self.seeded()
        policy = self.by_name("No transfer above 10,000")
        policy["policyText"] = policy["policyText"].replace("10000", "1000000")
        authz.seed()
        self.assertEqual(self.platform.changes(), [("PUT", f"{AUTHZ}/policies/{policy['id']}")])
        self.assertIn("> 10000", policy["policyText"])
        self.assertEqual(policy["status"], "DEPLOYED")

    def test_policies_with_slug_ids_get_the_readable_ids_back_and_keep_their_status(self):
        self.seeded()
        for policy in self.world.policies.values():
            policy["policyText"] = re.sub(r'@id\("([^"]+)"\)', lambda m: f'@id("{authz.slugify(m.group(1))}")', policy["policyText"])
        authz.seed()
        changes = self.platform.changes()
        self.assertEqual(sorted(changes), sorted(("PUT", f"{AUTHZ}/policies/{p['id']}") for p in self.world.policies.values()))
        for spec in policy_files():
            self.assertEqual(self.by_name(spec["name"])["policyText"].strip(), spec["policy"].replace(authz.SERVER, SLUG).strip())
        self.assertEqual(sorted(p["status"] for p in self.world.policies.values()), ["DEPLOYED"] * 9 + ["DRAFT"])

    def test_a_policy_someone_undeployed_is_deployed_again(self):
        self.seeded()
        policy = self.by_name("Agents move at most EUR 500")
        policy["status"] = "DISABLED"
        authz.seed()
        self.assertEqual(self.platform.changes(), [("POST", f"{AUTHZ}/policies/{policy['id']}/deploy")])

    def test_the_draft_someone_deployed_is_taken_out_of_service(self):
        self.seeded()
        policy = self.by_name(DRAFT_POLICY)
        policy["status"] = "DEPLOYED"
        authz.seed()
        self.assertEqual(self.platform.changes(), [("POST", f"{AUTHZ}/policies/{policy['id']}/disable")])
        authz.seed()
        self.assertEqual(len(self.platform.changes()), 1, "a draft that is out of service stays so")

    def test_a_policy_that_was_deleted_is_made_again(self):
        self.seeded()
        gone = self.by_name("Public tools for everyone signed in")
        del self.world.policies[gone["id"]]
        authz.seed()
        self.assertEqual([c for c in self.platform.changes() if c[0] == "POST"][0], ("POST", AUTHZ + "/policies"))
        self.assertEqual(len(self.world.policies), 10)

    def test_an_entity_whose_attributes_changed_is_put_back_and_a_deleted_one_is_made_again(self):
        self.seeded()
        self.world.entities["alice"]["attributes"]["accounts"] = ["ACC-9999"]
        del self.world.entities["bob"]
        authz.seed()
        self.assertEqual(self.platform.changes(), [("POST", AUTHZ + "/entities")] * 2)
        self.assertEqual(self.world.entities["alice"]["attributes"]["accounts"], ["ACC-1001", "ACC-1002"])
        self.assertIn("bob", self.world.entities)

    def test_an_entity_the_seed_did_not_create_is_never_overwritten(self):
        self.world.entities["alice"] = {"id": "x", "entityId": "alice", "entityType": "User", "kind": "PRINCIPAL",
                                        "attributes": {"mine": True}, "parents": [], "active": True}
        with self.assertRaisesRegex(common.SeedError, "User 'alice' exists in Authorization Management and the seed did not create it"):
            authz.seed()
        self.assertEqual(self.world.entities["alice"]["attributes"], {"mine": True})
        saved = [call.json["entityId"] for call in self.platform.sent("POST", AUTHZ + "/entities")]
        self.assertNotIn("alice", saved)

    def test_a_policy_the_seed_did_not_create_is_never_changed(self):
        self.world.policies["p"] = {"id": "p", "name": "Customers read their own accounts", "status": "DEPLOYED",
                                    "entityId": "policy.customers-read-their-own-accounts", "policyText": "permit (principal, action, resource);"}
        with self.assertRaisesRegex(common.SeedError, "Policy 'Customers read their own accounts' exists"):
            authz.seed()
        self.assertEqual(self.platform.sent("PUT", re.compile(".*")), [])
        self.assertEqual(self.world.policies["p"]["policyText"], "permit (principal, action, resource);")

    def test_a_server_that_is_not_in_the_catalog_stops_the_seed_before_it_makes_anything(self):
        self.world.servers = {"data": [], "pagination": {"totalCount": 0}}
        with self.assertRaisesRegex(common.SeedError, "The MCP server 'Acme Bakery MCP' is not in the AI catalog"):
            authz.seed()
        self.assertEqual(self.platform.changes(), [])

    def test_a_schema_that_cannot_be_saved_is_a_warning_and_the_rest_is_made(self):
        self.world.schema_refused = "line 1: nope"
        authz.seed()
        self.assertIn("! Schema 'Acme Bakery' skipped", self.output())
        self.assertEqual((len(self.world.entities), len(self.world.policies)), (15, 10))
        self.assertEqual([x["kind"] for x in common.created() if x["kind"] == "schema"], [])

    def test_a_schema_of_that_name_the_seed_did_not_create_is_left_alone(self):
        self.world.schemas["m"] = {"id": "m", "name": "Acme Bakery", "schema": "entity Mine {};", "targets": []}
        authz.seed()
        self.assertIn("exists and the seed did not create it", self.output())
        self.assertEqual(self.world.schemas["m"]["schema"], "entity Mine {};")

    def test_a_removal_deletes_what_the_seed_made_rules_first_and_a_tool_before_its_server(self):
        self.seeded()
        self.assertEqual(self.main("--remove"), 0, self.output())
        deleted = [path.rsplit("/", 2)[1:] for method, path in self.platform.changes() if method == "DELETE"]
        kinds = [kind for kind, _ in deleted]
        self.assertEqual(kinds, ["policies"] * 10 + ["entities"] * 15 + ["schemas"])
        entities = [entity for kind, entity in deleted if kind == "entities"]
        self.assertEqual(entities[-1], SLUG)
        self.assertLess(entities.index(f"{SLUG}.get_balance"), entities.index(SLUG))
        self.assertLess(entities.index("alice"), entities.index("customers"))
        self.assertEqual((self.world.policies, self.world.entities, self.world.schemas), ({}, {}, {}))

    def test_a_removal_leaves_what_the_seed_did_not_make(self):
        self.world.entities["mine"] = {"id": "m", "entityId": "mine"}
        authz.seed()
        authz.remove(common.created())
        self.assertIn("mine", self.world.entities)

    def test_a_playground_that_seeds_no_authorization_makes_no_call(self):
        with mock.patch.object(common, "SEED_DIR", common.SEED_DIR / "nothing"), mock.patch.dict("os.environ", {}, clear=True):
            authz.seed()
            authz.remove([])
        self.assertEqual(self.platform.calls, [])

    def test_the_seed_runs_authorization_after_the_catalog_and_the_link_to_access_management(self):
        modules = [module.__name__.rsplit('.', 1)[-1] for module in seed.MODULES]
        self.assertLess(modules.index("aim_catalog"), modules.index("authz"))
        self.assertLess(modules.index("aim_governance"), modules.index("authz"))
        self.assertLess(modules.index("am_link"), modules.index("aim_catalog"))


class Protections(Authorization):
    """The cap on what an agent moves is a control: deployed while the mode protects, a draft while it does not."""

    def deploys(self):
        return len(self.platform.sent("POST", re.compile(AUTHZ + "/policies/[^/]+/deploy")))

    def statuses(self):
        return {p["name"]: p["status"] for p in self.world.policies.values()}

    def test_off_the_cap_is_made_a_draft_with_the_other_draft_and_the_other_eight_are_deployed(self):
        self.env(SEED_PROTECTIONS="off")
        authz.seed()
        self.assertEqual(sorted(name for name, status in self.statuses().items() if status == "DRAFT"), [CAP_POLICY, DRAFT_POLICY])
        self.assertEqual(self.deploys(), 8)
        self.assertEqual(self.by_name(CAP_POLICY)["policyText"].count("context.amount > 500"), 1, "its text is there to be read")

    def test_on_every_policy_but_the_draft_is_deployed(self):
        self.env(SEED_PROTECTIONS="on")
        authz.seed()
        self.assertEqual(sorted(self.statuses().values()), ["DEPLOYED"] * 9 + ["DRAFT"])
        self.assertEqual(self.statuses()[CAP_POLICY], "DEPLOYED")

    def test_a_second_seed_in_the_same_mode_saves_nothing(self):
        for mode in ("off", "on"):
            with self.subTest(mode=mode):
                self.env(SEED_PROTECTIONS=mode)
                authz.seed()
                self.platform.calls.clear()
                authz.seed()
                self.assertEqual(self.platform.changes(), [])

    def test_a_cap_someone_deployed_is_taken_out_again_and_nothing_else_changes(self):
        self.env(SEED_PROTECTIONS="off")
        self.seeded()
        policy = self.by_name(CAP_POLICY)
        text = policy["policyText"]
        policy["status"] = "DEPLOYED"
        authz.seed()
        self.assertEqual(self.platform.changes(), [("POST", f"{AUTHZ}/policies/{policy['id']}/disable")])
        self.assertEqual((policy["status"], policy["policyText"]), ("DISABLED", text))
        line = next(line for line in self.output().splitlines() if "Taken out of service" in line)
        self.assertEqual(line, "[authz]   ✓ Taken out of service: the console shows it Disabled", "the word the person reads")

    def test_deploying_the_cap_in_the_console_and_seeding_again_round_trips(self):
        """What a lesson does in the console, then what a Re-seed does, as many times as someone wants."""
        self.env(SEED_PROTECTIONS="off")
        authz.seed()
        self.assertEqual(self.by_name(CAP_POLICY)["status"], "DRAFT")
        for _ in range(3):
            self.by_name(CAP_POLICY)["status"] = "DEPLOYED"          # the person deploys it
            authz.seed()                                             # Re-seed: the lesson starts over
            self.assertNotEqual(self.by_name(CAP_POLICY)["status"], "DEPLOYED")
        self.env(SEED_PROTECTIONS="on")                              # a presenter who wants everything protected
        authz.seed()
        self.assertEqual(self.by_name(CAP_POLICY)["status"], "DEPLOYED")
        self.platform.calls.clear()
        authz.seed()
        self.assertEqual(self.platform.changes(), [])

    def test_the_other_mode_deploys_or_takes_out_the_cap_and_only_it(self):
        self.env(SEED_PROTECTIONS="off")
        authz.seed()
        self.platform.calls.clear()
        self.env(SEED_PROTECTIONS="on")
        authz.seed()
        self.assertEqual(self.platform.changes(), [("POST", f"{AUTHZ}/policies/{self.by_name(CAP_POLICY)['id']}/deploy")])
        self.platform.calls.clear()
        self.env(SEED_PROTECTIONS="off")
        authz.seed()
        self.assertEqual(self.platform.changes(), [("POST", f"{AUTHZ}/policies/{self.by_name(CAP_POLICY)['id']}/disable")])

    def test_the_draft_the_lessons_do_not_teach_stays_a_draft_in_both_modes(self):
        for mode in ("off", "on"):
            with self.subTest(mode=mode):
                self.env(SEED_PROTECTIONS=mode)
                authz.seed()
                self.assertEqual(self.statuses()[DRAFT_POLICY], "DRAFT")

    def test_a_cap_someone_edited_is_put_back_and_stays_a_draft_off(self):
        self.env(SEED_PROTECTIONS="off")
        self.seeded()
        policy = self.by_name(CAP_POLICY)
        policy["policyText"] = policy["policyText"].replace("500", "5000000")
        authz.seed()
        self.assertEqual(self.platform.changes(), [("PUT", f"{AUTHZ}/policies/{policy['id']}")])
        self.assertIn("context.amount > 500", policy["policyText"])
        self.assertEqual(policy["status"], "DRAFT")

    def test_a_policy_no_control_names_is_deployed_in_both_modes(self):
        self.env(SEED_PROTECTIONS="off")
        authz.seed()
        for name in POLICIES:
            if name not in (CAP_POLICY, DRAFT_POLICY):
                self.assertEqual(self.statuses()[name], "DEPLOYED", name)

    def test_the_deployment_of_a_control_is_waited_for(self):
        self.env(SEED_PROTECTIONS="off", GATEWAY_SYNC_SECONDS="5")
        self.seeded()
        self.by_name(CAP_POLICY)["status"] = "DEPLOYED"
        controls.reset()
        authz.seed()
        controls.settle()
        self.assertAlmostEqual(self.clock.elapsed, 11, delta=1)


if __name__ == "__main__":
    unittest.main()
