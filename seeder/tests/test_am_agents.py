"""Access Management for a playground whose agent acts for a person: the scopes of the domain, made before the applications
that ask for them; an agent that keeps what its kind gives it and is given its scopes, its claims, its redirect URIs and a
way to sign in with the secret of the file; users whose profile the tokens carry. The platform is a fake that answers as AM
does for an agent (a hosted or autonomous one starts with `private_key_jwt`; a scope an application names must exist; a list
of scopes has fifty a page at most). The files of the personal playground "agent-toolbox" run through it when they are there."""

import copy
import re
import unittest
from unittest import mock

from lab_seeder import am
from lab_seeder import common
from lab_seeder.testkit import PERSONAL, PLAYGROUNDS, Seed
from tests.test_am import DOMAINS, GRANTS_OF_AN_AGENT, LOGIN, AccessManagement

# The playground is personal until it is shared: the same seed in either place
HARBOR_SEED = next((seed for seed in (PERSONAL / "agent-toolbox" / "seed",
                                      PLAYGROUNDS / "agent-toolbox" / "seed") if seed.is_dir()),
                   PERSONAL / "agent-toolbox" / "seed")
SCOPES = re.compile(DOMAINS + "/[^/]+/scopes")
APPLICATIONS = re.compile(DOMAINS + "/[^/]+/applications")
SYSTEM_SCOPES = ("openid", "profile", "email")
SECRET = "care-copilot-secret"
CALLBACK = "http://localhost:8130/auth/callback"
CLAIM = "{#context.attributes['user']['additionalInformation']['region']}"

DOMAIN_FILE = """\
name: harbor
scopes:
  - {key: searchCustomers, name: Search customers, description: Find customers of your region}
  - {key: getCustomer, name: Read a customer profile, description: Read the profile of a customer of your region}
  - {key: createRefund, name: Refund an order, description: Refund an order within the limits of your team}
users:
  - username: dana@harbor.test
    email: dana@harbor.test
    firstName: Dana
    lastName: Reyes
    password: demo-password
    additionalInformation: {name: Dana Reyes, region: EU, team: support}
"""
AGENT_FILE = f"""\
name: Care copilot
description: Harbor's customer care copilot
type: AGENT
kind: HOSTED_DELEGATED
clientId: care-copilot
clientSecret: {SECRET}
redirectUris: ["{CALLBACK}"]
scopes: [openid, profile, email, searchCustomers, getCustomer, createRefund]
oauth:
  tokenCustomClaims:
    - {{claimName: region, claimValue: "{CLAIM}"}}
"""


def agent_file(**changes):
    """The agent file with some lines replaced: {"kind: HOSTED_DELEGATED": "kind: AUTONOMOUS"}, or removed with None."""
    text = AGENT_FILE
    for old, new in changes.items():
        assert old in text, old
        text = text.replace(old, new if new is not None else "")
    return text


class Am(AccessManagement):
    """AM as far as an agent needs it, on top of the fake of the other tests: a hosted or autonomous agent starts with a key
    pair to sign in with (`private_key_jwt`), an application can only name scopes the domain has, and the list of scopes
    answers fifty at most a page."""

    def __init__(self):
        super().__init__()
        self.at_creation = {}      # clientId -> the OAuth settings AM gave an application when it was made
        self.scope_pages = []      # (page, size) of each list of scopes that was asked
        self.hide_total = False

    def routes(self):
        routes = super().routes()
        secured = self.secured
        routes.update({
            ("GET", SCOPES): secured(self.list_scopes),
            ("POST", SCOPES): secured(self.create_scope),
            ("PATCH", re.compile(DOMAINS + "/[^/]+/scopes/[^/]+")): secured(self.patch_scope),
            ("DELETE", re.compile(DOMAINS + "/[^/]+/scopes/[^/]+")): secured(self.delete_scope),
        })
        return routes

    # A domain starts with the scopes of the sign-in
    def add_domain(self, name, scopes=()):
        """A domain that was there before the seed, with scopes that the platform or somebody made: (key, name, description)."""
        domain = {"id": self.new("domain"), "name": name}
        self.domains[domain["id"]] = domain
        self.start_scopes(domain["id"])
        for key, scope_name, description in scopes:
            self.add_scope(domain["id"], key, scope_name, description)
        return domain["id"]

    def start_scopes(self, domain_id):
        for key in SYSTEM_SCOPES:
            self.add_scope(domain_id, key, key, key, system=True)

    def add_scope(self, domain_id, key, name, description, system=False):
        scope = {"id": self.new("scope"), "key": key, "name": name, "description": description, "system": system}
        self.held.setdefault((domain_id, "scopes"), {})[scope["id"]] = scope
        return scope

    def create_domain(self, call):
        status, domain = super().create_domain(call)
        self.start_scopes(domain["id"])
        return status, domain

    def keys(self, call):
        return {scope["key"] for scope in self.kept(call, "scopes").values()}

    # Scopes
    def list_scopes(self, call):
        page, size = int(call.params.get("page", 0)), min(int(call.params.get("size", 50)), 50)
        self.scope_pages.append((page, size))
        rows = list(self.kept(call, "scopes").values())
        body = {"currentPage": page, "data": rows[page * size:(page + 1) * size]}
        if not self.hide_total:
            body["totalCount"] = len(rows)
        return 200, body

    def create_scope(self, call):
        key = (call.json.get("key") or "").replace(" ", "_")
        if not all(call.json.get(field) for field in ("key", "name", "description")):
            return 400, {"message": "key, name and description are needed"}
        if key in self.keys(call):
            return 400, {"message": f"Scope [{key}] already exists"}
        scope = {"id": self.new("scope"), "key": key, "name": call.json["name"], "description": call.json["description"], "system": False}
        self.kept(call, "scopes")[scope["id"]] = scope
        return 201, scope

    def patch_scope(self, call):
        scope = self.kept(call, "scopes")[call.path.rsplit("/", 1)[1]]
        scope.update({k: v for k, v in call.json.items() if k in ("name", "description")})
        return 200, scope

    def delete_scope(self, call):
        scope = self.kept(call, "scopes").get(call.path.rsplit("/", 1)[1])
        if scope and scope["system"]:
            return 400, {"message": "A system scope cannot be deleted"}
        self.kept(call, "scopes").pop(call.path.rsplit("/", 1)[1], None)
        return 204

    # Applications: an agent starts as its kind says
    def create_application(self, call):
        status, app = super().create_application(call)
        oauth = app["settings"]["oauth"]
        oauth.update(scopeSettings=[], tokenCustomClaims=[])
        if app["kind"] == "HOSTED_DELEGATED":
            oauth.update(tokenEndpointAuthMethod="private_key_jwt")
        elif app["kind"] == "AUTONOMOUS":
            oauth.update(tokenEndpointAuthMethod="private_key_jwt", redirectUris=None,
                         grantTypes=["client_credentials", "urn:ietf:params:oauth:grant-type:token-exchange"])
        elif app["kind"] == "USER_EMBEDDED":
            oauth.update(tokenEndpointAuthMethod="none", forcePKCE=True)
        self.at_creation[app["clientId"]] = copy.deepcopy(oauth)
        return status, app

    def patch_application(self, call):
        oauth = call.json.get("settings", {}).get("oauth", {})
        if any(entry["scope"] not in self.keys(call) for entry in oauth.get("scopeSettings", [])):
            return 400, {"message": "non valid scopes"}
        if {"password", "refresh_token", "implicit"} & set(oauth.get("grantTypes", [])):
            return 400, {"message": "An agent cannot use this grant type"}
        return super().patch_application(call)


class Case(Seed):

    def setUp(self):
        super().setUp()
        self.am = Am()
        self.platform.add(self.am.routes())

    def files(self, domain=DOMAIN_FILE, agent=AGENT_FILE):
        """The seed files: the domain, and the agent unless it is None (then the folder of applications is empty)."""
        self.seed_files({"am/domain.yaml": domain, **({"am/applications/care-copilot.yaml": agent} if agent else {})})
        if not agent:
            (common.SEED_DIR / "am" / "applications" / "care-copilot.yaml").unlink(missing_ok=True)

    def seed(self):
        am.seed({})

    def writes(self):
        """What the seed changed: signing in again is not a change."""
        return [change for change in self.platform.changes() if change != LOGIN]

    def agent(self):
        return self.am.one("applications", clientId="care-copilot")

    def oauth(self):
        return self.agent()["settings"]["oauth"]

    def patches(self):
        return [call.json for call in self.platform.sent("PATCH", re.compile(DOMAINS + f"/[^/]+/applications/{self.agent()['id']}"))]

    def domain_id(self):
        return next(iter(self.am.domains))

    def settle(self):
        """What AM does at the first sign-in of a person: fields of its own in a profile."""
        for user in self.am.objects("users"):
            user["additionalInformation"].update(sub=user["id"], given_name=user["firstName"], auth_time=1)


class Scopes(Case):
    """The scopes of the domain."""

    def setUp(self):
        super().setUp()
        self.files()

    def posts(self):
        return [call.json for call in self.platform.sent("POST", SCOPES)]

    def test_the_scopes_of_the_file_are_made_with_a_key_a_name_and_a_description(self):
        self.seed()
        self.assertEqual(self.posts(), [
            {"key": "searchCustomers", "name": "Search customers", "description": "Find customers of your region"},
            {"key": "getCustomer", "name": "Read a customer profile", "description": "Read the profile of a customer of your region"},
            {"key": "createRefund", "name": "Refund an order", "description": "Refund an order within the limits of your team"}])
        kept = {scope["key"]: scope for scope in self.am.objects("scopes")}
        self.assertEqual(kept["createRefund"]["name"], "Refund an order")
        self.assertIn("✓ Scopes: 3 created", self.output())

    def test_they_are_made_before_the_application_that_names_them(self):
        self.seed()
        order = [call.path.rsplit("/", 1)[1] for call in self.platform.calls
                 if call.method == "POST" and call.path.rsplit("/", 1)[1] in ("scopes", "applications")]
        self.assertEqual(order, ["scopes"] * 3 + ["applications"])
        self.assertEqual({entry["scope"] for entry in self.oauth()["scopeSettings"]} - set(SYSTEM_SCOPES),
                         {"searchCustomers", "getCustomer", "createRefund"})

    def test_a_scope_that_is_there_is_not_made_again_and_the_platform_s_description_stays(self):
        domain_id = self.am.add_domain("harbor", [("getCustomer", "Customers", "Made when the MCP server was registered")])
        self.seed()
        self.assertEqual([post["key"] for post in self.posts()], ["searchCustomers", "createRefund"])
        self.assertEqual(self.am.one("scopes", key="getCustomer")["description"], "Made when the MCP server was registered")
        self.assertEqual(self.platform.sent("PATCH", re.compile(DOMAINS + f"/{domain_id}/scopes/.*")), [])
        self.assertEqual(sorted(x["name"] for x in common.created() if x["kind"] == "scope"), ["createRefund", "searchCustomers"],
                         "recorded: the two it made")

    def test_every_scope_is_found_whatever_page_of_the_list_it_is_on(self):
        self.am.add_domain("harbor", [(f"scope-{number}", "x", "x") for number in range(120)] + [("getCustomer", "x", "x")])
        self.seed()
        self.assertEqual(self.am.scope_pages, [(0, 50), (1, 50), (2, 50)], "124 scopes, fifty a page")
        self.assertEqual([post["key"] for post in self.posts()], ["searchCustomers", "createRefund"])

    def test_a_list_that_gives_no_total_is_read_until_a_page_is_short(self):
        self.am.hide_total = True
        self.am.add_domain("harbor", [(f"scope-{number}", "x", "x") for number in range(47)])   # 50 with the system scopes
        self.seed()
        self.assertEqual(self.am.scope_pages, [(0, 50), (1, 50)], "a full page: the next one may have more")
        self.assertEqual(len(self.posts()), 3)

    def test_what_it_made_is_recorded_as_scopes_of_the_domain_and_nothing_else_about_them(self):
        self.seed()
        scopes = [x for x in common.created() if x["kind"] == "scope"]
        self.assertEqual([(x["label"], x["name"], x["domain"]) for x in scopes],
                         [("scopes", key, self.domain_id()) for key in ("searchCustomers", "getCustomer", "createRefund")])
        self.assertEqual({x["id"] for x in scopes}, {s["id"] for s in self.am.objects("scopes") if not s["system"]})

    def test_a_second_seed_saves_nothing(self):
        self.seed()
        self.settle()
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.writes(), [])
        self.assertIn("3 scopes as the file says", self.output())

    def test_a_scope_the_seed_made_and_someone_edited_is_put_back_and_only_that_one(self):
        self.seed()
        self.settle()
        scope = self.am.one("scopes", key="createRefund")
        scope["description"] = "Edited in the console"
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.writes(), [("PATCH", f"{DOMAINS}/{self.domain_id()}/scopes/{scope['id']}")])
        self.assertEqual(self.platform.sent("PATCH", re.compile(DOMAINS + "/[^/]+/scopes/[^/]+"))[0].json,
                         {"name": "Refund an order", "description": "Refund an order within the limits of your team"})
        self.assertEqual(scope["description"], "Refund an order within the limits of your team")

    def test_a_scope_someone_deleted_is_made_again(self):
        self.seed()
        self.settle()
        gone = self.am.one("scopes", key="getCustomer")
        del self.am.held[(self.domain_id(), "scopes")][gone["id"]]
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.writes(), [("POST", f"{DOMAINS}/{self.domain_id()}/scopes")])
        self.assertEqual(self.am.one("scopes", key="getCustomer")["name"], "Read a customer profile")

    def test_a_scope_can_be_written_as_its_key_alone(self):
        self.files(domain="name: harbor\nscopes: [alpha, beta]\n", agent=None)
        self.seed()
        self.assertEqual(self.posts(), [{"key": "alpha", "name": "alpha", "description": "alpha"},
                                        {"key": "beta", "name": "beta", "description": "beta"}])

    def test_a_scope_without_a_key_stops_the_seed_in_one_line(self):
        self.files(domain="name: harbor\nscopes: [{name: Nameless}]\n", agent=None)
        with self.assertRaisesRegex(common.SeedError, "^seed/am/domain.yaml: a scope needs a key$"):
            self.seed()

    def test_a_domain_that_lists_no_scope_asks_for_none(self):
        self.files(domain="name: harbor\n", agent=None)
        self.seed()
        self.assertEqual(self.platform.sent("GET", SCOPES), [])
        self.assertEqual(self.platform.sent("POST", SCOPES), [])

    def test_a_key_the_file_lists_twice_is_made_once(self):
        self.files(domain="name: harbor\nscopes: [{key: a, name: A, description: A}, {key: a, name: B, description: B}]\n", agent=None)
        self.seed()
        self.assertEqual(len(self.posts()), 1, "a key twice in the file is made once")

    def test_a_removal_deletes_the_scopes_it_made_after_the_agent_and_the_users_and_before_the_domain(self):
        self.seed()
        self.platform.calls.clear()
        am.remove(common.created())
        deletes = [path.split("/")[-2:] for method, path in self.platform.changes() if method == "DELETE"]
        self.assertEqual([kind for kind, _ in deletes], ["applications", "users", "scopes", "scopes", "scopes", "domains"])
        self.assertEqual(self.am.domains, {})

    def test_a_removal_leaves_the_scopes_it_did_not_make(self):
        domain_id = self.am.add_domain("harbor", [("getCustomer", "x", "x")])
        self.seed()
        records = [x for x in common.created() if x["kind"] == "scope"]
        am.remove(records)
        self.assertEqual([s["key"] for s in self.am.held[(domain_id, "scopes")].values()], [*SYSTEM_SCOPES, "getCustomer"])

    def test_scopes_are_the_kind_of_record_that_goes_before_the_domain(self):
        self.assertEqual(am.ORDER[-2:], ["scope", "domain"])
        self.assertEqual(am.PATHS["scope"], "scopes")


class Agent(Case):
    """An agent keeps what its kind gives it, and the file adds what it may ask for."""

    def setUp(self):
        super().setUp()
        self.files()

    def test_it_is_made_as_an_agent_with_the_secret_of_the_file(self):
        self.seed()
        sent = self.platform.sent("POST", APPLICATIONS)[0].json
        self.assertEqual(sent, {"name": "Care copilot", "type": "AGENT", "kind": "HOSTED_DELEGATED", "clientId": "care-copilot",
                                "clientSecret": SECRET, "redirectUris": [CALLBACK], "description": "Harbor's customer care copilot"})
        self.assertEqual((self.agent()["type"], self.agent()["secret"]), ("agent", SECRET))

    def test_the_secret_is_not_made_read_or_changed_anywhere_but_in_the_file(self):
        self.seed()
        self.assertEqual([call.path for call in self.platform.calls if "secret" in call.path.lower()], [])
        carrying = [(call.method, call.path.rsplit("/", 1)[1]) for call in self.platform.calls if SECRET in str(call.json)]
        self.assertEqual(carrying, [("POST", "applications")], "sent once, when the application is made")

    def test_the_secret_is_in_no_log_and_no_record(self):
        self.seed()
        self.assertNotIn(SECRET, self.output())
        self.assertNotIn(SECRET, self.state.read_text(encoding="utf-8"))
        self.assertNotIn("demo-password", self.output() + self.state.read_text(encoding="utf-8"))

    def test_it_signs_in_with_that_secret_where_its_kind_would_have_it_sign_in_with_a_key(self):
        self.seed()
        self.assertEqual(self.am.at_creation["care-copilot"]["tokenEndpointAuthMethod"], "private_key_jwt", "what AM gave it")
        self.assertEqual(self.oauth()["tokenEndpointAuthMethod"], "client_secret_basic")

    def test_it_keeps_the_grants_and_response_types_its_kind_gives_it(self):
        self.seed()
        self.assertEqual(self.oauth()["grantTypes"], GRANTS_OF_AN_AGENT)
        self.assertEqual(self.oauth()["responseTypes"], ["code"])
        self.assertTrue(all(set(patch.get("settings", {}).get("oauth", {})) <= {"scopeSettings", "tokenCustomClaims", "redirectUris",
                                                                                  "tokenEndpointAuthMethod"} for patch in self.patches()))

    def test_it_may_ask_a_person_for_the_scopes_of_the_file_and_nothing_else(self):
        self.seed()
        self.assertEqual(self.oauth()["scopeSettings"], [{"scope": scope, "defaultScope": False, "scopeApproval": 300}
                                                         for scope in ("openid", "profile", "email", "searchCustomers",
                                                                       "getCustomer", "createRefund")])

    def test_what_its_tokens_say_is_set_and_the_identity_provider_is_the_domains(self):
        self.seed()
        self.assertEqual(self.oauth()["tokenCustomClaims"], [{"tokenType": "access_token", "claimName": "region", "claimValue": CLAIM}])
        self.assertEqual(self.agent()["identityProviders"], [{"identity": "idp-1", "selectionRule": "", "priority": 0}])

    def test_one_save_for_the_settings_with_what_differs_and_one_for_the_identity_provider(self):
        self.seed()
        patches = self.patches()
        self.assertEqual(len(patches), 2)
        self.assertEqual(set(patches[0]["settings"]["oauth"]), {"scopeSettings", "tokenCustomClaims", "tokenEndpointAuthMethod"},
                         "the redirect URI was right: not sent")
        self.assertEqual(set(patches[1]), {"identityProviders"})

    def test_a_second_seed_saves_nothing(self):
        self.seed()
        self.settle()
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.writes(), [])
        self.assertIn("OAuth settings and identity provider as the file says", self.output())

    def test_the_order_am_keeps_the_scopes_in_is_no_difference_and_nor_are_the_fields_it_adds(self):
        self.seed()
        self.settle()
        entries = self.oauth()["scopeSettings"]
        entries.reverse()
        for entry in entries:
            entry.update(requiredScope=False)
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.writes(), [])

    def test_a_scope_it_lost_is_given_back_and_only_the_scopes_are_saved(self):
        self.seed()
        self.settle()
        self.oauth()["scopeSettings"].pop()
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual([set(patch["settings"]["oauth"]) for patch in self.patches()], [{"scopeSettings"}])
        self.assertEqual(len(self.oauth()["scopeSettings"]), 6)

    def test_a_scope_someone_added_to_the_agent_in_the_console_is_taken_away(self):
        self.seed()
        self.settle()
        self.am.add_scope(self.domain_id(), "offline_access", "Offline access", "Offline access")
        self.oauth()["scopeSettings"].append({"scope": "offline_access", "defaultScope": False, "scopeApproval": 0})
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(len(self.oauth()["scopeSettings"]), 6)
        self.assertNotIn("offline_access", {entry["scope"] for entry in self.oauth()["scopeSettings"]})

    def test_a_scope_made_a_default_in_the_console_is_set_back(self):
        self.seed()
        self.settle()
        entry = next(entry for entry in self.oauth()["scopeSettings"] if entry["scope"] == "createRefund")
        entry.update(defaultScope=True, scopeApproval=0)
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(len(self.writes()), 1)
        entry = next(entry for entry in self.oauth()["scopeSettings"] if entry["scope"] == "createRefund")
        self.assertEqual((entry["defaultScope"], entry["scopeApproval"]), (False, 300))

    def test_a_redirect_uri_that_moved_is_sent_again_and_nothing_else(self):
        self.seed()
        self.settle()
        self.files(agent=AGENT_FILE.replace("localhost:8130", "localhost:8140"))
        self.platform.calls.clear()
        self.seed()
        self.assertEqual([set(patch["settings"]["oauth"]) for patch in self.patches()], [{"redirectUris"}])
        self.assertEqual(self.oauth()["redirectUris"], ["http://localhost:8140/auth/callback"])

    def test_a_method_the_file_names_is_the_one_it_gets(self):
        self.files(agent=agent_file(**{"oauth:\n": "oauth:\n  tokenEndpointAuthMethod: client_secret_post\n"}))
        self.seed()
        self.assertEqual(self.oauth()["tokenEndpointAuthMethod"], "client_secret_post")

    def test_a_hosted_agent_without_a_secret_in_the_file_keeps_the_key_pair_it_starts_with(self):
        self.files(agent=agent_file(**{f"clientSecret: {SECRET}\n": None}))
        self.seed()
        self.assertEqual(self.oauth()["tokenEndpointAuthMethod"], "private_key_jwt")
        self.assertNotIn("clientSecret", self.platform.sent("POST", APPLICATIONS)[0].json)

    def test_an_autonomous_agent_with_a_secret_signs_in_with_it_and_has_nowhere_to_be_sent_back_to(self):
        self.files(agent=agent_file(**{"kind: HOSTED_DELEGATED": "kind: AUTONOMOUS"}))
        self.seed()
        oauth = self.oauth()
        self.assertEqual(oauth["tokenEndpointAuthMethod"], "client_secret_basic")
        self.assertEqual(oauth["grantTypes"], ["client_credentials", "urn:ietf:params:oauth:grant-type:token-exchange"])
        self.assertIsNone(oauth["redirectUris"], "AM gives an autonomous agent none, and the file's are not sent as settings")
        self.assertTrue(all("redirectUris" not in patch.get("settings", {}).get("oauth", {}) for patch in self.patches()))

    def test_an_agent_that_lives_in_an_application_keeps_the_method_it_was_given(self):
        self.files(agent=agent_file(**{"kind: HOSTED_DELEGATED": "kind: USER_EMBEDDED", f"clientSecret: {SECRET}\n": None}))
        self.seed()
        self.assertEqual(self.oauth()["tokenEndpointAuthMethod"], "none")
        self.assertTrue(self.oauth()["forcePKCE"])

    def test_an_agent_the_file_gives_no_scopes_is_given_none(self):
        self.files(agent=agent_file(**{"scopes: [openid, profile, email, searchCustomers, getCustomer, createRefund]\n": None}))
        self.seed()
        self.assertEqual(self.oauth()["scopeSettings"], [])
        self.assertEqual(set(self.patches()[0]["settings"]["oauth"]), {"tokenCustomClaims", "tokenEndpointAuthMethod"})

    def test_an_agent_without_an_oauth_block_still_gets_its_scopes_and_its_method(self):
        self.files(agent=agent_file(**{"oauth:\n  tokenCustomClaims:\n    - {claimName: region, claimValue: \"" + CLAIM + "\"}\n": None}))
        self.seed()
        self.assertEqual(self.oauth()["tokenCustomClaims"], [])
        self.assertEqual(self.oauth()["tokenEndpointAuthMethod"], "client_secret_basic")
        self.assertEqual(len(self.oauth()["scopeSettings"]), 6)

    def test_a_scope_the_domain_lacks_is_refused_by_am_and_the_seed_says_so(self):
        self.files(domain="name: harbor\n")
        with self.assertRaisesRegex(common.SeedError, "HTTP 400.*non valid scopes"):
            self.seed()

    def test_an_agent_the_seed_did_not_make_is_set_up_where_it_differs_and_never_recorded(self):
        domain_id = self.am.add_domain("harbor", [("searchCustomers", "x", "x"), ("getCustomer", "x", "x"), ("createRefund", "x", "x")])
        self.am.held[(domain_id, "applications")] = {"app-0": {
            "id": "app-0", "name": "Care copilot", "type": "agent", "kind": "HOSTED_DELEGATED", "clientId": "care-copilot",
            "identityProviders": [], "settings": {"oauth": {"clientId": "care-copilot", "redirectUris": [CALLBACK],
                                                            "tokenEndpointAuthMethod": "private_key_jwt"}, "advanced": {}}}}
        self.seed()
        self.assertEqual(self.platform.sent("POST", APPLICATIONS), [])
        self.assertEqual(self.oauth()["tokenEndpointAuthMethod"], "client_secret_basic")
        self.assertEqual([x["kind"] for x in common.created() if x["kind"] in ("agent", "application")], [])

    def test_it_is_recorded_as_an_agent_of_the_domain(self):
        self.seed()
        self.assertEqual([(x["kind"], x["label"], x["name"], x["domain"]) for x in common.created() if x["kind"] == "agent"],
                         [("agent", "AM agents", "Care copilot", self.domain_id())])


class Users(Case):
    """The people who sign in, with what the tokens say of them."""

    def setUp(self):
        super().setUp()
        self.files()

    def dana(self):
        return self.am.one("users", username="dana@harbor.test")

    def test_the_profile_is_applied_after_the_user_is_made(self):
        self.seed()
        self.assertEqual(self.dana()["additionalInformation"], {"name": "Dana Reyes", "region": "EU", "team": "support"})
        posted = self.platform.sent("POST", re.compile(DOMAINS + "/[^/]+/users"))[0].json
        self.assertNotIn("additionalInformation", posted)
        self.assertEqual(posted["password"], "demo-password")
        self.assertEqual(len(self.platform.sent("PUT", re.compile(DOMAINS + "/[^/]+/users/.*"))), 1)

    def test_a_second_seed_leaves_the_profile_and_the_fields_am_added_alone(self):
        self.seed()
        self.settle()
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.dana()["additionalInformation"]["given_name"], "Dana")

    def test_a_profile_someone_changed_is_put_back_and_the_fields_am_added_stay(self):
        self.seed()
        self.settle()
        self.dana()["additionalInformation"].update(region="US", team="trainee")
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.dana()["additionalInformation"]["region"], "EU")
        self.assertEqual(self.dana()["additionalInformation"]["given_name"], "Dana")
        self.assertEqual(len(self.writes()), 1)

    def test_the_flag_of_the_seed_is_not_sent_to_am_as_a_field_of_the_user(self):
        user = ("  - {username: sam@harbor.test, email: sam@harbor.test, firstName: Sam, lastName: Lopez, password: p, optional: true}\n"
                "  - {username: kim@harbor.test, email: kim@harbor.test, firstName: Kim, lastName: Ng, password: p, optional: true,\n"
                "     additionalInformation: {team: trainee}}\n")
        self.files(domain="name: harbor\nusers:\n" + user, agent=None)
        self.seed()
        for post in self.platform.sent("POST", re.compile(DOMAINS + "/[^/]+/users")):
            self.assertNotIn("optional", post.json)
        self.assertEqual(sorted(user["username"] for user in self.am.objects("users")), ["kim@harbor.test", "sam@harbor.test"])


@unittest.skipUnless(HARBOR_SEED.is_dir(), "the playground agent-toolbox is not on this machine")
class Harbor(Case):
    """The seed files of the playground, as they are, through the module and against the fake."""

    def setUp(self):
        super().setUp()
        self.env(AGENT_TOOLBOX_DEMO_PASSWORD="demo-password", AGENT_TOOLBOX_CLIENT_SECRET=SECRET,
                 CARE_COPILOT_URL="http://localhost:8130")
        patch = mock.patch.object(common, "SEED_DIR", HARBOR_SEED)
        patch.start()
        self.addCleanup(patch.stop)
        self.domain = common.load(HARBOR_SEED / "am" / "domain.yaml")
        self.application = common.load(HARBOR_SEED / "am" / "applications" / "care-copilot.yaml")

    def test_the_seed_runs_without_a_stop(self):
        self.seed()
        self.assertNotIn("! ", self.output(), "no warning")
        domain = next(iter(self.am.domains.values()))
        self.assertEqual((domain["name"], domain["enabled"]), ("harbor", True))
        self.assertEqual(domain["tokenExchangeSettings"]["allowImpersonation"], False)
        self.assertTrue(domain["tokenExchangeSettings"]["allowDelegation"])

    def test_every_tool_has_the_scope_the_file_lists_and_the_agent_asks_for_each_of_them(self):
        self.seed()
        keys = {scope["key"] for scope in self.am.objects("scopes") if not scope["system"]}
        self.assertEqual(keys, {scope["key"] for scope in self.domain["scopes"]})
        asked = {entry["scope"] for entry in self.oauth()["scopeSettings"]}
        self.assertEqual(asked, set(self.application["scopes"]))
        self.assertEqual(asked - set(SYSTEM_SCOPES), keys, "the agent may ask for the scope of every tool, and no other")

    def test_every_scope_the_agent_asks_for_exists_in_the_domain_before_it_asks(self):
        self.seed()
        self.assertEqual(self.platform.sent("PATCH", re.compile(".*/applications/.*"))[0].json["settings"]["oauth"]["scopeSettings"][0]["scope"],
                         "openid")
        self.assertEqual({entry["scope"] for entry in self.oauth()["scopeSettings"]} - {s["key"] for s in self.am.objects("scopes")}, set())

    def test_care_copilot_is_a_hosted_agent_that_signs_in_with_the_secret_of_the_seed(self):
        self.seed()
        agent = self.agent()
        self.assertEqual((agent["name"], agent["type"], agent["kind"], agent["secret"]), ("Care copilot", "agent", "HOSTED_DELEGATED", SECRET))
        self.assertEqual(self.oauth()["tokenEndpointAuthMethod"], "client_secret_basic")
        self.assertEqual(self.oauth()["grantTypes"], GRANTS_OF_AN_AGENT)
        self.assertEqual(self.oauth()["redirectUris"], ["http://localhost:8130/auth/callback"])
        self.assertNotIn(SECRET, self.output() + self.state.read_text(encoding="utf-8"))

    def test_its_tokens_say_the_username_the_name_the_region_and_the_team(self):
        self.seed()
        claims = {claim["claimName"]: claim["claimValue"] for claim in self.oauth()["tokenCustomClaims"]}
        self.assertEqual(sorted(claims), ["name", "region", "team", "username"])
        self.assertEqual(claims["region"], "{#context.attributes['user']['additionalInformation']['region']}")

    def test_the_people_carry_the_region_and_the_team_the_tokens_say(self):
        self.seed()
        profiles = {user["username"]: user["additionalInformation"] for user in self.am.objects("users")}
        self.assertEqual(sorted(profiles), sorted(user["username"] for user in self.domain["users"]))
        for profile in profiles.values():
            self.assertTrue({"name", "region", "team"} <= set(profile), profile)
        self.assertEqual({user["password"] for user in self.am.objects("users")}, {"demo-password"})

    def test_a_second_seed_saves_nothing(self):
        self.seed()
        self.settle()
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.writes(), [])

    def test_a_removal_deletes_what_it_made_and_the_domain_last(self):
        self.seed()
        self.platform.calls.clear()
        am.remove(common.created())
        kinds = [path.split("/")[-2] for method, path in self.platform.changes() if method == "DELETE"]
        self.assertEqual(kinds[0], "applications")
        self.assertEqual(kinds.count("users"), 3)
        self.assertEqual(kinds.count("scopes"), len(self.domain["scopes"]))
        self.assertEqual(kinds[-1], "domains")
        self.assertEqual(self.am.domains, {})


if __name__ == "__main__":
    unittest.main()
