"""The link from Gamma to Access Management, in the three cases of what Gamma has saved: none, one the seed may point at
its own domain, one it must leave. The token that gives Gamma its access goes from AM to Gamma and is never shown or kept."""

import json
import re
import unittest
from unittest import mock

from lab_seeder import common
from lab_seeder import am_link
from lab_seeder.testkit import PLAYGROUNDS, Seed

ACME_SEED = PLAYGROUNDS / "acme-bakery" / "seed"
PLATFORM = "/gamma/organizations/DEFAULT/environments/DEFAULT/modules/platform"
AM_ORG = "/management/organizations/DEFAULT"
LOGIN = ("POST", "/management/auth/token")
TOKEN = "pat-0123456789-never-shown"
OWN = {"id": "domain-1", "name": "acme-bakery", "hrid": "acme-bakery"}
OWNER = {"id": "role-owner", "name": "ORGANIZATION_OWNER", "assignableType": "organization"}
USER_ROLE = {"id": "role-user", "name": "ORGANIZATION_USER", "assignableType": "organization"}
MADE = ("am-link", "am-service-account", "AM service accounts", "gamma-service-account", "user-1")
SAVED = {"baseUrl": "http://am-management-api:8093", "hasAccessToken": True, "amOrganizationId": "DEFAULT",
         "environmentId": "DEFAULT", "gatewayUrl": "http://localhost:8092"}
AIMED_AT_OWN = {**SAVED, "defaultDomainId": "domain-1", "defaultDomainHrid": "acme-bakery"}


class World:
    """Access Management (the domains, the organization's users, roles and tokens) and the link Gamma keeps to it."""

    def __init__(self, domains=(OWN,), config=None, org="DEFAULT", env="DEFAULT"):
        self.org, self.env = f"/management/organizations/{org}", env
        self.domains, self.users, self.members, self.tokens = list(domains), [], [], []
        self.config = dict(config or {"baseUrl": "", "hasAccessToken": False})
        self.rights_after = 0     # reads of the domain that Gamma is refused before the new role counts
        self.reads = 0

    def routes(self):
        org = self.org
        return {
            LOGIN: (200, {"access_token": "am-session"}),
            ("GET", f"{org}/environments/{self.env}/domains"): lambda call: (200, {"data": self.domains}),
            ("GET", org + "/users"): lambda call: (200, {"data": [u for u in self.users if u["username"] == call.params["q"]]}),
            ("POST", org + "/users"): self.create_user,
            ("GET", org + "/roles"): (200, {"data": [USER_ROLE, OWNER]}),
            ("GET", org + "/members"): lambda call: (200, {"memberships": self.members}),
            ("POST", org + "/members"): self.give_role,
            ("GET", re.compile(org + "/users/[^/]+/tokens")): lambda call: (200, self.tokens),
            ("DELETE", re.compile(org + "/users/[^/]+/tokens/[^/]+")): self.revoke,
            ("POST", re.compile(org + "/users/[^/]+/tokens")): self.mint,
            ("GET", PLATFORM + "/am/am-config"): lambda call: (200, self.config),
            ("POST", PLATFORM + "/am/am-config/_test"): self.test,
            ("PUT", PLATFORM + "/am/am-config"): self.save,
            ("GET", re.compile(PLATFORM + f"/am/environments/{self.env}/domains/[^/]+")): self.read_domain,
        }

    def create_user(self, call):
        user = {"id": "user-1", "username": call.json["username"], "serviceAccount": call.json["serviceAccount"]}
        self.users.append(user)
        self.members.append({"memberId": "user-1", "roleId": USER_ROLE["id"]})   # a new user has a membership already
        return 201, user

    def give_role(self, call):
        self.members = [m for m in self.members if m["memberId"] != call.json["memberId"]]
        self.members.append({"memberId": call.json["memberId"], "roleId": call.json["role"]})
        return 201, {}

    def revoke(self, call):
        self.tokens = [t for t in self.tokens if t["tokenId"] != call.path.rsplit("/", 1)[1]]
        return 204

    def mint(self, call):
        self.tokens.append({"tokenId": f"token-{len(self.tokens) + 1}"})
        return 201, {"tokenId": self.tokens[-1]["tokenId"], "token": TOKEN, "name": call.json["name"]}

    @staticmethod
    def test(call):
        return 200, {"ok": True, "status": 200}

    def save(self, call):
        """What is saved replaces what was, and a body without a token keeps the one Gamma has, as Gamma does."""
        token = call.json.get("serviceAccountAccessToken")
        self.config = {**{k: v for k, v in call.json.items() if k != "serviceAccountAccessToken"},
                       "hasAccessToken": bool(token) or self.config["hasAccessToken"]}
        return 200, self.config

    def read_domain(self, call):
        self.reads += 1
        if self.reads <= self.rights_after:
            return 403, {"message": "Permission denied"}
        return 200, {"id": call.path.rsplit("/", 1)[1]}


class Link(Seed):

    def setUp(self):
        super().setUp()
        self.empty_seed = common.SEED_DIR   # the temporary folder `seed_files` writes to
        patch = mock.patch.object(common, "SEED_DIR", ACME_SEED)
        patch.start()
        self.addCleanup(patch.stop)
        self.env(GAMMA_AM_URL="http://am-management-api:8093", AM_PUBLIC_URL="http://localhost:8092")
        self.world = World()

    def platform_of(self, world):
        self.world = world
        self.platform.add(world.routes())

    def seed(self, world=None):
        if world:
            self.platform_of(world)
        am_link.seed()

    def writes(self):
        """What the seed changes: signing in to AM again is not a change."""
        return [change for change in self.platform.changes() if change != LOGIN]

    def puts(self):
        return [call.json for call in self.platform.sent("PUT", PLATFORM + "/am/am-config")]

    def test_nothing_is_linked_so_a_service_account_with_the_owner_role_gives_gamma_its_access(self):
        self.seed(self.world)
        self.assertEqual([u["username"] for u in self.world.users], ["gamma-service-account"])
        self.assertTrue(self.world.users[0]["serviceAccount"])
        self.assertEqual(self.world.members, [{"memberId": "user-1", "roleId": "role-owner"}])
        self.assertEqual(self.puts(), [{
            "baseUrl": "http://am-management-api:8093", "amOrganizationId": "DEFAULT", "environmentId": "DEFAULT",
            "defaultDomainId": "domain-1", "defaultDomainHrid": "acme-bakery", "gatewayUrl": "http://localhost:8092",
            "serviceAccountAccessToken": TOKEN}])
        self.assertTrue(self.world.config["hasAccessToken"])
        self.assertEqual([(r["kind"], r["name"], r["id"]) for r in common.created()],
                         [("am-service-account", "gamma-service-account", "user-1"), ("am-link", "Gamma to Access Management", "domain-1")])

    def test_the_link_is_tested_with_the_token_before_it_is_saved(self):
        self.seed(self.world)
        order = [(call.method, call.path.rsplit("/", 1)[1]) for call in self.platform.calls if "am-config" in call.path]
        self.assertEqual([step for step in order if step[0] != "GET"], [("POST", "_test"), ("PUT", "am-config")])
        self.assertEqual(self.platform.sent("POST", PLATFORM + "/am/am-config/_test")[0].json["serviceAccountAccessToken"], TOKEN)

    def test_gamma_that_cannot_reach_access_management_stops_the_seed_before_it_saves_the_link(self):
        self.platform_of(self.world)
        self.platform.add({("POST", PLATFORM + "/am/am-config/_test"): (200, {"ok": False, "status": 500, "message": "Failed to resolve 'am'"})})
        with self.assertRaisesRegex(common.SeedError, "Gamma cannot reach Access Management at http://am-management-api:8093: Failed to resolve 'am'"):
            am_link.seed()
        self.assertEqual(self.puts(), [])

    def test_the_token_is_neither_shown_nor_kept(self):
        self.seed(self.world)
        self.assertNotIn(TOKEN, self.output())
        self.assertNotIn(TOKEN, self.state.read_text(encoding="utf-8"))
        sent_with_it = [(call.method, call.path) for call in self.platform.calls if TOKEN in json.dumps(call.json or {})]
        self.assertEqual(sent_with_it, [("POST", PLATFORM + "/am/am-config/_test"), ("PUT", PLATFORM + "/am/am-config")])

    def test_a_service_account_the_seed_made_is_kept_and_only_its_tokens_are_renewed(self):
        """A seed that stopped after making the account, or a Gamma that lost its token, comes back to it."""
        common.record(*MADE)
        world = World()
        world.users = [{"id": "user-1", "username": "gamma-service-account", "serviceAccount": True}]
        world.members = [{"memberId": "user-1", "roleId": "role-owner"}]
        world.tokens = [{"tokenId": "old"}]
        self.seed(world)
        self.assertEqual([(c.method, c.path.rsplit("/", 1)[1]) for c in self.platform.calls
                          if c.method in ("POST", "DELETE") and AM_ORG in c.path], [("DELETE", "old"), ("POST", "tokens")])
        self.assertEqual(world.tokens, [{"tokenId": "token-1"}])

    def test_a_service_account_the_seed_made_with_a_lower_role_gets_the_owner_role(self):
        common.record(*MADE)
        world = World()
        world.users = [{"id": "user-1", "username": "gamma-service-account", "serviceAccount": True}]
        world.members = [{"memberId": "user-1", "roleId": "role-user"}]
        self.seed(world)
        self.assertEqual(world.members, [{"memberId": "user-1", "roleId": "role-owner"}])

    def test_a_service_account_of_that_name_the_seed_did_not_create_stops_the_seed_and_is_left_as_it_is(self):
        """Its tokens may be in use by someone else, and its role is theirs: the seed never adopts what it did not make."""
        world = World()
        world.users = [{"id": "user-1", "username": "gamma-service-account", "serviceAccount": True}]
        world.members = [{"memberId": "user-1", "roleId": "role-user"}]
        world.tokens = [{"tokenId": "theirs"}]
        self.platform_of(world)
        with self.assertRaisesRegex(common.SeedError, "AM already has a service account 'gamma-service-account' that the "
                                                      "seed did not create.*AM_TOKEN"):
            am_link.seed()
        self.assertEqual(self.writes(), [])
        self.assertEqual((world.tokens, world.members), ([{"tokenId": "theirs"}], [{"memberId": "user-1", "roleId": "role-user"}]))
        self.assertEqual(common.created(), [])

    def test_a_service_account_recorded_under_another_id_is_not_the_one(self):
        common.record("am-link", "am-service-account", "AM service accounts", "gamma-service-account", "another-user")
        world = World()
        world.users = [{"id": "user-1", "username": "gamma-service-account", "serviceAccount": True}]
        self.platform_of(world)
        with self.assertRaisesRegex(common.SeedError, "did not create"):
            am_link.seed()

    def test_a_user_of_that_name_that_is_not_a_service_account_is_not_the_one(self):
        world = World()
        world.users = [{"id": "person", "username": "gamma-service-account", "serviceAccount": False}]
        self.seed(world)
        self.assertEqual(len(self.platform.sent("POST", AM_ORG + "/users")), 1)

    def test_the_service_account_can_be_named_by_the_file(self):
        with mock.patch.object(common, "SEED_DIR", self.empty_seed):
            self.seed_files({"am/gamma-link.yaml": "domain: acme-bakery\nserviceAccount: gamma-bot\n"})
            self.seed(self.world)
        self.assertEqual([u["username"] for u in self.world.users], ["gamma-bot"])

    def test_with_a_token_for_access_management_nothing_is_made_there_and_the_token_is_gamma_s(self):
        self.env(AM_TOKEN=TOKEN, AM_USERNAME=None, AM_PASSWORD=None)
        self.seed(self.world)
        self.assertEqual([c for c in self.writes() if AM_ORG in c[1]], [])
        self.assertEqual(self.world.users, [])
        self.assertEqual(self.puts()[0]["serviceAccountAccessToken"], TOKEN)
        self.assertNotIn(TOKEN, self.output() + self.state.read_text(encoding="utf-8"))
        self.assertEqual([r["kind"] for r in common.created()], ["am-link"], "no account was made, so none is recorded")

    def test_a_link_to_no_domain_is_pointed_at_the_playground_s_without_a_token(self):
        self.seed(World(config=SAVED))
        self.assertEqual(self.puts(), [{k: v for k, v in AIMED_AT_OWN.items() if k != "hasAccessToken"}])
        self.assertEqual([c for c in self.writes() if AM_ORG in c[1]], [])
        self.assertTrue(self.world.config["hasAccessToken"], "the token Gamma has stays")
        self.assertEqual(self.platform.sent("POST", PLATFORM + "/am/am-config/_test"), [])

    def test_a_link_to_a_domain_that_is_gone_is_pointed_at_the_playground_s(self):
        self.seed(World(config={**SAVED, "defaultDomainId": "deleted", "defaultDomainHrid": "example"}))
        self.assertEqual(self.world.config["defaultDomainId"], "domain-1")
        self.assertNotIn("serviceAccountAccessToken", self.puts()[0])

    def test_a_link_to_the_playground_s_own_domain_made_again_is_pointed_at_the_new_one(self):
        self.seed(World(config={**SAVED, "defaultDomainId": "domain-removed-since", "defaultDomainHrid": "acme-bakery"}))
        self.assertEqual(self.world.config["defaultDomainId"], "domain-1")

    def test_a_link_to_another_domain_that_works_is_left_alone_with_a_warning(self):
        other = {"id": "domain-2", "name": "Example", "hrid": "example"}
        link = {**SAVED, "defaultDomainId": "domain-2", "defaultDomainHrid": "example"}
        self.seed(World(domains=(OWN, other), config=link))
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.world.config, link)
        self.assertEqual(common.created(), [])
        self.assertIn("! Gamma is linked to the AM domain 'example', which the seed leaves alone", self.output())
        self.assertIn("Choose 'acme-bakery' in Platform > Access Management", self.output())

    def test_a_link_to_another_access_management_is_left_alone(self):
        link = {**AIMED_AT_OWN, "baseUrl": "https://am.example.com", "defaultDomainId": "elsewhere", "defaultDomainHrid": "other"}
        self.seed(World(config=link))
        self.assertEqual(self.writes(), [])
        self.assertIn("which the seed leaves alone", self.output())

    def test_a_link_that_is_right_changes_nothing_and_a_second_seed_neither(self):
        self.seed(World(config=AIMED_AT_OWN))
        self.assertEqual(self.writes(), [])
        self.assertIn("Gamma is linked to the domain 'acme-bakery'", self.output())

    def test_the_second_seed_after_a_first_that_made_the_link_saves_nothing(self):
        self.seed(self.world)
        self.platform.calls.clear()
        am_link.seed()
        self.assertEqual(self.writes(), [])
        self.assertEqual(len(common.created()), 2)

    def test_the_seed_waits_for_the_role_to_count_and_then_reads_the_domain(self):
        self.world.rights_after = 2
        self.seed(self.world)
        self.assertEqual(self.world.reads, 3)
        self.assertEqual(len(common.created()), 2)

    def test_a_role_that_never_counts_is_an_error(self):
        self.world.rights_after = 10 ** 6
        self.platform_of(self.world)
        with self.assertRaisesRegex(common.SeedError, "Gamma's access to the domain not ready after 60 s"):
            am_link.seed()
        self.assertEqual(len(common.created()), 2, "the link was saved: a removal says it stays")

    def test_the_link_and_the_service_account_are_recorded_as_notes_with_no_token(self):
        self.seed(self.world)
        self.assertEqual(common.created(), [
            {"product": "am-link", "kind": "am-service-account", "label": "AM service accounts",
             "name": "gamma-service-account", "id": "user-1", "role": "ORGANIZATION_OWNER"},
            {"product": "am-link", "kind": "am-link", "label": "Gamma links", "name": "Gamma to Access Management",
             "id": "domain-1", "domain": "acme-bakery"}])

    def test_a_domain_that_is_not_in_access_management_is_an_error(self):
        self.platform_of(World(domains=()))
        with self.assertRaisesRegex(common.SeedError, "AM domain 'acme-bakery' not found"):
            am_link.seed()
        self.assertEqual(self.writes(), [])

    def test_the_organization_environment_and_urls_are_settings(self):
        self.env(AM_ORG="acme", AM_ENV="prod", GAMMA_AM_URL=None)
        self.platform_of(World(org="acme", env="prod"))
        am_link.seed()
        saved = self.puts()[0]
        self.assertEqual((saved["amOrganizationId"], saved["environmentId"], saved["baseUrl"]), ("acme", "prod", "http://am"))

    def test_a_removal_says_the_link_stays_and_calls_no_one(self):
        am_link.remove([{"product": "am-link", "kind": "am-link", "name": "Gamma to Access Management", "id": "domain-1"}])
        self.assertIn("cannot be undone through the API", self.output())
        self.assertNotIn("service account", self.output(), "none was made, with a token for Access Management")
        self.assertEqual(self.platform.calls, [])

    def test_a_removal_says_what_the_service_account_is_and_that_it_is_the_users_to_delete(self):
        self.seed(self.world)
        self.platform.calls.clear()
        am_link.remove(common.created())
        removal = self.output().splitlines()[-1]
        self.assertIn("the service account 'gamma-service-account' with the ORGANIZATION_OWNER role and its token", removal)
        self.assertIn("does not expire", removal)
        self.assertIn("delete the account in Access Management", removal)
        self.assertEqual(self.platform.calls, [])

    def test_a_removal_of_a_playground_that_made_no_link_says_nothing(self):
        am_link.remove([{"product": "am", "kind": "domain", "name": "d", "id": "d"}])
        am_link.remove([])
        self.assertEqual((self.output(), self.platform.calls), ("", []))

    def test_a_playground_that_does_not_seed_the_link_reads_no_setting_and_makes_no_call(self):
        with mock.patch.object(common, "SEED_DIR", common.SEED_DIR / "nothing"), mock.patch.dict("os.environ", {}, clear=True):
            am_link.seed()
        self.assertEqual(self.platform.calls, [])

    def test_the_label_of_the_note_is_plural_and_its_kind_is_removed_in_the_modules_order(self):
        self.assertEqual(am_link.ORDER, ["am-link", "am-service-account"])
        self.seed(self.world)
        self.assertEqual({r["kind"] for r in common.created()}, set(am_link.ORDER))
        self.assertTrue(all(r["label"].endswith("s") for r in common.created()))


if __name__ == "__main__":
    unittest.main()
