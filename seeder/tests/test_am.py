"""Access Management as Acme Bakery needs it: the domain with token exchange for delegation, the web app (authorization code
flow with PKCE), the traffic generator's application (password grant) and the agent, which carry the same claims, users whose
profile the tokens carry, groups, the introspection client. The files of the seed are read as they are and made in an AM that
keeps what it is given; a second seed saves nothing."""

import itertools
import json
import re
import unittest
from unittest import mock

from lab_seeder import am
from lab_seeder import common
from lab_seeder.testkit import PLAYGROUNDS, Seed

ACME_SEED = PLAYGROUNDS / "acme-bakery" / "seed"
ENV = "/management/organizations/DEFAULT/environments/DEFAULT"
DOMAINS = ENV + "/domains"
DOMAIN = DOMAINS + "/[^/]+"
LOGIN = ("POST", "/management/auth/token")
SECRETS = {"BANK_DEMO_PASSWORD": "demo-password", "WEB_CLIENT_SECRET": "web-secret", "TRAFFIC_CLIENT_SECRET": "traffic-secret",
           "ACE_CLIENT_SECRET": "ace-secret", "MCP_RESOURCE_SECRET": "mcp-secret", "BAKERY_WEB_URL": "http://localhost:8110",
           "BAKERY_WEB_LOOPBACK_URL": "http://127.0.0.1:8110", "APIM_GATEWAY_URL": "http://localhost:8082"}
CLAIMS = ["username", "role", "customer_id", "accounts", "name"]
GRANTS_OF_AN_AGENT = ["authorization_code", "client_credentials", "urn:ietf:params:oauth:grant-type:token-exchange"]


class AccessManagement:
    """AM as far as the seed needs it: a login that gives a token that expires, and a domain that holds applications, users,
    groups and protected resources, which it keeps and answers from."""

    def __init__(self):
        self.ids = itertools.count(1)
        self.token, self.logins = None, 0
        self.requests, self.expires_at = 0, None   # the request at which the token stops being good
        self.domains = {}
        self.held = {}        # (domain id, kind) -> {id: object}

    def routes(self):
        secured = self.secured
        return {
            LOGIN: self.login,
            ("GET", DOMAINS): secured(lambda call: (200, {"data": [{"id": d["id"], "name": d["name"]} for d in self.domains.values()]})),
            ("POST", DOMAINS): secured(self.create_domain),
            ("GET", re.compile(DOMAIN)): secured(self.read_domain),
            ("PATCH", re.compile(DOMAIN)): secured(self.patch_domain),
            ("DELETE", re.compile(DOMAIN)): secured(self.delete(self.domains)),
            ("GET", re.compile(DOMAIN + "/identities")): secured(lambda call: (200, [{"id": "idp-1", "system": True}])),
            ("GET", re.compile(DOMAIN + "/applications")): secured(self.list_of("applications", "clientId")),
            ("GET", re.compile(DOMAIN + "/applications/[^/]+")): secured(self.read("applications")),
            ("POST", re.compile(DOMAIN + "/applications")): secured(self.create_application),
            ("PATCH", re.compile(DOMAIN + "/applications/[^/]+")): secured(self.patch_application),
            ("DELETE", re.compile(DOMAIN + "/applications/[^/]+")): secured(self.delete_in("applications")),
            ("GET", re.compile(DOMAIN + "/users")): secured(self.list_of("users", "username")),
            ("GET", re.compile(DOMAIN + "/users/[^/]+")): secured(self.read("users")),
            ("POST", re.compile(DOMAIN + "/users")): secured(self.create_user),
            ("PUT", re.compile(DOMAIN + "/users/[^/]+")): secured(self.put_user),
            ("DELETE", re.compile(DOMAIN + "/users/[^/]+")): secured(self.delete_in("users")),
            ("GET", re.compile(DOMAIN + "/groups")): secured(self.list_of("groups", "name")),
            ("GET", re.compile(DOMAIN + "/groups/[^/]+")): secured(self.read("groups")),
            ("POST", re.compile(DOMAIN + "/groups")): secured(self.create_group),
            ("POST", re.compile(DOMAIN + "/groups/[^/]+/members")): secured(lambda call: (405, {"message": "Method Not Allowed"})),
            ("POST", re.compile(DOMAIN + "/groups/[^/]+/members/[^/]+")): secured(self.add_member),
            ("DELETE", re.compile(DOMAIN + "/groups/[^/]+")): secured(self.delete_in("groups")),
            ("GET", re.compile(DOMAIN + "/protected-resources")): secured(self.list_of("protected-resources", "clientId")),
            ("POST", re.compile(DOMAIN + "/protected-resources")): secured(self.create_resource),
            ("DELETE", re.compile(DOMAIN + "/protected-resources/[^/]+")): secured(self.delete_in("protected-resources")),
        }

    # Signing in: a token is good until it expires
    def login(self, call):
        self.logins += 1
        self.token = f"token-{self.logins}"
        return 200, {"access_token": self.token}

    def expire(self):
        self.token = "expired"

    def secured(self, answer):
        def guarded(call):
            self.requests += 1
            if self.requests == self.expires_at:
                self.expire()
            if call.headers.get("Authorization") != f"Bearer {self.token}":
                return 401, {"message": "Unauthorized"}
            return answer(call)
        return guarded

    # What a domain holds
    @staticmethod
    def domain_of(call):
        return call.path.split("/")[7]

    def kept(self, call, kind):
        return self.held.setdefault((self.domain_of(call), kind), {})

    def new(self, kind):
        return f"{kind}-{next(self.ids)}"

    def list_of(self, kind, key):
        def listing(call):
            wanted = call.params.get("q")
            return 200, {"data": [{"id": x["id"], "name": x.get("name"), key: x.get(key)} for x in self.kept(call, kind).values()
                                  if not wanted or wanted in str(x.get(key))]}
        return listing

    def read(self, kind):
        return lambda call: (200, self.kept(call, kind)[call.path.rsplit("/", 1)[1]])

    def delete(self, collection):
        def remove(call):
            collection.pop(call.path.rsplit("/", 1)[1], None)
            return 204
        return remove

    def delete_in(self, kind):
        def remove(call):
            if kind == "protected-resources" and call.params.get("type") != "MCP_SERVER":
                return 500, {"message": "Internal error", "http_status": 500}   # AM 4.12: the type is read, and null is refused
            self.kept(call, kind).pop(call.path.rsplit("/", 1)[1], None)
            return 204
        return remove

    # The domain
    def create_domain(self, call):
        domain = {"id": self.new("domain"), "name": call.json["name"]}
        self.domains[domain["id"]] = domain
        return 201, domain

    def read_domain(self, call):
        return 200, self.domains[call.path.rsplit("/", 1)[1]]   # the whole domain: the list only names them

    def patch_domain(self, call):
        self.domains[call.path.rsplit("/", 1)[1]].update(call.json)
        return 200, self.domains[call.path.rsplit("/", 1)[1]]

    # Applications: the kind of an agent, and a type, bring grants of their own
    def create_application(self, call):
        body = call.json
        oauth = {"clientId": body["clientId"], "redirectUris": body.get("redirectUris", []),
                 "tokenEndpointAuthMethod": "client_secret_basic"}
        if body.get("kind") == "HOSTED_DELEGATED":
            oauth.update(grantTypes=list(GRANTS_OF_AN_AGENT), responseTypes=["code"])
        else:
            oauth.update(grantTypes=["authorization_code"], responseTypes=["code"])
        app = {"id": self.new("application"), "name": body["name"], "type": body["type"].lower(), "clientId": body["clientId"],
               "secret": body.get("clientSecret"), "kind": body.get("kind"), "identityProviders": [],
               "settings": {"oauth": oauth, "advanced": {"skipConsent": False, "flowsInherited": True}}}
        self.kept(call, "applications")[app["id"]] = app
        return 201, app

    def patch_application(self, call):
        app = self.kept(call, "applications")[call.path.rsplit("/", 1)[1]]
        app["settings"]["oauth"].update(call.json.get("settings", {}).get("oauth", {}))
        app["settings"]["advanced"].update(call.json.get("settings", {}).get("advanced", {}))
        app["identityProviders"] = call.json.get("identityProviders", app["identityProviders"])
        return 200, app

    # Users
    def create_user(self, call):
        held = self.kept(call, "users")
        if any(user["username"] == call.json["username"] for user in held.values()):
            return 400, {"message": "User already exists"}
        user = {"id": self.new("user"), **call.json}
        held[user["id"]] = user
        return 201, user

    def put_user(self, call):
        user = self.kept(call, "users")[call.path.rsplit("/", 1)[1]]
        user.update({k: call.json[k] for k in ("email", "firstName", "lastName", "additionalInformation")})
        return 200, user

    # Groups and resources
    def create_group(self, call):
        group = {"id": self.new("group"), "name": call.json["name"], "members": []}
        self.kept(call, "groups")[group["id"]] = group
        return 201, group

    def add_member(self, call):
        parts = call.path.split("/")
        group = self.kept(call, "groups")[parts[9]]
        group["members"].append(parts[11])
        return 200, group

    def create_resource(self, call):
        resource = {"id": self.new("resource"), **call.json}
        self.kept(call, "protected-resources")[resource["id"]] = resource
        return 201, resource

    # What a test looks at
    def objects(self, kind):
        return [x for (_, k), held in self.held.items() if k == kind for x in held.values()]

    def one(self, kind, **match):
        return next(x for x in self.objects(kind) if all(x.get(k) == v for k, v in match.items()))


class Case(Seed):

    def setUp(self):
        super().setUp()
        self.env(**SECRETS)
        self.am = AccessManagement()
        self.platform.add(self.am.routes())

    def use_bank_files(self):
        patch = mock.patch.object(common, "SEED_DIR", ACME_SEED)
        patch.start()
        self.addCleanup(patch.stop)

    def writes(self):
        """What the seed changed: signing in again is not a change."""
        return [change for change in self.platform.changes() if change != LOGIN]

    def seed(self):
        am.seed({})

    def patches(self, object_id):
        return [call.json for call in self.platform.sent("PATCH", re.compile(DOMAINS + f"/[^/]+/applications/{object_id}"))]


class Bank(Case):
    """What the files of the seed make."""

    def setUp(self):
        super().setUp()
        self.use_bank_files()
        self.seed()

    def test_the_domain_has_token_exchange_for_delegation_and_not_for_impersonation(self):
        domain = next(iter(self.am.domains.values()))
        self.assertEqual((domain["name"], domain["enabled"]), ("acme-bakery", True))
        exchange = domain["tokenExchangeSettings"]
        self.assertEqual((exchange["enabled"], exchange["allowDelegation"], exchange["allowImpersonation"]), (True, True, False))
        self.assertEqual(exchange["tokenExchangeOAuthSettings"], {"inherited": False, "scopeHandling": "downscoping"})
        self.assertIn("urn:ietf:params:oauth:token-type:access_token", exchange["allowedActorTokenTypes"])
        self.assertEqual(domain["oidc"]["clientRegistrationSettings"],
                         {"allowLocalhostRedirectUri": True, "allowHttpSchemeRedirectUri": True})
        self.assertTrue(domain["oidc"]["redirectUriStrictMatching"], "a redirect URI must be one of the registered, exactly")

    def test_ace_is_an_agent_with_a_secret_of_the_seed(self):
        ace = self.am.one("applications", clientId="ace-agent")
        self.assertEqual((ace["name"], ace["type"], ace["kind"], ace["secret"]), ("Ace", "agent", "HOSTED_DELEGATED", "ace-secret"))
        created = self.platform.sent("POST", re.compile(DOMAINS + "/[^/]+/applications"))
        sent = next(call.json for call in created if call.json["clientId"] == "ace-agent")
        self.assertEqual((sent["type"], sent["kind"], sent["redirectUris"]), ("AGENT", "HOSTED_DELEGATED", ["http://localhost:8110/callback"]))
        self.assertEqual(ace["identityProviders"], [{"identity": "idp-1", "selectionRule": "", "priority": 0}])

    def test_ace_keeps_the_grants_its_kind_gives_it_and_only_the_claims_are_added(self):
        ace = self.am.one("applications", clientId="ace-agent")
        self.assertEqual(ace["settings"]["oauth"]["grantTypes"], GRANTS_OF_AN_AGENT)
        self.assertEqual([set(p["settings"]["oauth"]) for p in self.patches(ace["id"]) if "settings" in p], [{"tokenCustomClaims"}])

    def test_the_web_app_signs_in_with_the_code_flow_and_pkce_and_never_with_the_password(self):
        web = self.am.one("applications", clientId="acme-bakery-web")
        oauth = web["settings"]["oauth"]
        self.assertEqual((web["type"], web["secret"]), ("web", "web-secret"))
        self.assertEqual((oauth["grantTypes"], oauth["responseTypes"]), (["authorization_code", "refresh_token"], ["code"]))
        self.assertEqual((oauth["forcePKCE"], oauth["forceS256CodeChallengeMethod"]), (True, True))
        self.assertEqual(oauth["tokenEndpointAuthMethod"], "client_secret_basic")
        self.assertEqual(web["settings"]["advanced"], {"skipConsent": True, "flowsInherited": True},
                         "the bank's own app: no consent page after the login page")
        self.assertEqual([s["scope"] for s in oauth["scopeSettings"]], ["openid", "profile", "email"])
        self.assertEqual(web["identityProviders"][0]["identity"], "idp-1")

    def test_the_redirect_uris_of_the_web_app_are_the_two_ways_to_open_it_and_nothing_else(self):
        oauth = self.am.one("applications", clientId="acme-bakery-web")["settings"]["oauth"]
        self.assertEqual(oauth["redirectUris"], ["http://localhost:8110/callback", "http://127.0.0.1:8110/callback"])

    def test_the_traffic_generator_has_an_application_of_its_own_with_the_password_grant(self):
        traffic = self.am.one("applications", clientId="acme-bakery-traffic")
        oauth = traffic["settings"]["oauth"]
        self.assertEqual((traffic["name"], traffic["type"], traffic["secret"]), ("Acme Bakery traffic", "web", "traffic-secret"))
        self.assertEqual((oauth["grantTypes"], oauth["responseTypes"]), (["password"], []))
        self.assertEqual([s["scope"] for s in oauth["scopeSettings"]], ["openid", "profile", "email"])
        self.assertEqual(traffic["identityProviders"][0]["identity"], "idp-1")
        self.assertFalse(traffic["settings"]["advanced"]["skipConsent"], "a file that does not say skipConsent leaves it as AM has it")

    def test_no_application_but_the_traffic_generators_has_the_password_grant(self):
        grants = {a["clientId"]: a["settings"]["oauth"]["grantTypes"] for a in self.am.objects("applications")}
        self.assertEqual([client for client, g in grants.items() if "password" in g], ["acme-bakery-traffic"])

    def test_the_web_app_the_traffic_generator_and_ace_put_the_same_five_claims_in_a_token(self):
        claims = {}
        for client in ("acme-bakery-web", "acme-bakery-traffic", "ace-agent"):
            claims[client] = self.am.one("applications", clientId=client)["settings"]["oauth"]["tokenCustomClaims"]
        self.assertEqual(claims["acme-bakery-web"], claims["ace-agent"])
        self.assertEqual(claims["acme-bakery-traffic"], claims["ace-agent"])
        self.assertEqual([c["claimName"] for c in claims["ace-agent"]], CLAIMS)
        self.assertTrue(all(c["tokenType"] == "access_token" for c in claims["ace-agent"]))
        by_name = {c["claimName"]: c["claimValue"] for c in claims["ace-agent"]}
        self.assertEqual(by_name["username"], "{#context.attributes['user']['username']}")
        self.assertEqual(by_name["accounts"], "{#context.attributes['user']['additionalInformation']['accounts']}")
        self.assertEqual(by_name["name"], "{#context.attributes['user']['additionalInformation']['name']}")

    def test_the_users_carry_what_the_tokens_say_with_a_password_of_the_seed(self):
        profiles = {u["username"]: u["additionalInformation"] for u in self.am.objects("users")}
        self.assertEqual(profiles, {
            "alice@acme-bakery.test": {"role": "customer", "customer_id": "C-1001", "accounts": ["ACC-1001", "ACC-1002"], "name": "Alice Martin"},
            "bob@acme-bakery.test": {"role": "customer", "customer_id": "C-2001", "accounts": ["ACC-2001"], "name": "Bob Chen"},
            "tina@acme-bakery.test": {"role": "teller", "name": "Tina Torres"}})
        self.assertEqual({u["password"] for u in self.am.objects("users")}, {"demo-password"})
        self.assertEqual({u["forceResetPassword"] for u in self.am.objects("users")}, {False})

    def test_the_groups_have_their_members_added_through_the_path_of_the_member(self):
        users = {u["id"]: u["username"] for u in self.am.objects("users")}
        members = {g["name"]: sorted(users[m] for m in g["members"]) for g in self.am.objects("groups")}
        self.assertEqual(members, {"customers": ["alice@acme-bakery.test", "bob@acme-bakery.test"], "tellers": ["tina@acme-bakery.test"]})
        self.assertEqual(self.platform.sent("POST", re.compile(DOMAINS + "/[^/]+/groups/[^/]+/members")), [])

    def test_the_gateway_has_a_client_to_introspect_tokens_with(self):
        resource = self.am.one("protected-resources", clientId="acme-bakery-mcp")
        self.assertEqual((resource["type"], resource["clientSecret"]), ("MCP_SERVER", "mcp-secret"))
        self.assertEqual(resource["resourceIdentifiers"], ["http://localhost:8082/acme-bakery/mcp"])
        self.assertEqual(len(resource["features"]), 8)

    def test_what_it_made_is_recorded_by_kind(self):
        kinds = sorted((x["kind"], x["label"], x["name"]) for x in common.created())
        self.assertEqual(kinds, [
            ("agent", "AM agents", "Ace"), ("application", "applications", "Acme Bakery traffic"),
            ("application", "applications", "Acme Bakery web"), ("domain", "domains", "acme-bakery"),
            ("group", "groups", "customers"), ("group", "groups", "tellers"), ("protected-resource", "MCP servers", "Acme Bakery MCP"),
            ("user", "users", "alice@acme-bakery.test"), ("user", "users", "bob@acme-bakery.test"), ("user", "users", "tina@acme-bakery.test")])
        self.assertTrue(all(x["label"].endswith("s") and not x["label"].endswith("ies") for x in common.created()))
        self.assertEqual({x["kind"] for x in common.created()} - set(am.ORDER), set())

    def test_the_secret_of_a_user_or_an_application_is_not_recorded(self):
        self.assertNotIn("secret", self.state.read_text(encoding="utf-8"))
        self.assertNotIn("demo-password", self.state.read_text(encoding="utf-8"))

    def test_a_second_seed_saves_nothing(self):
        for user in self.am.objects("users"):   # AM adds fields of its own to a profile at the first sign-in
            user["additionalInformation"].update(sub=user["id"], given_name=user["firstName"], auth_time=1)
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.writes(), [])
        self.assertEqual(len(common.created()), 10)

    def test_the_log_says_what_it_saved_for_an_application_and_what_it_left_as_the_file_says(self):
        self.assertEqual(self.output().count("✓ OAuth settings and identity provider"), 3, "the three applications were saved")
        for user in self.am.objects("users"):
            user["additionalInformation"].update(sub=user["id"], given_name=user["firstName"], auth_time=1)
        self.stdout.seek(0)
        self.stdout.truncate()
        self.seed()
        self.assertNotIn("✓ OAuth settings and identity provider", self.output())
        self.assertEqual(self.output().count("OAuth settings and identity provider as the file says"), 3)

    def test_a_domain_someone_changed_is_made_as_the_file_says_and_nothing_else_is_saved(self):
        domain = next(iter(self.am.domains.values()))
        domain["enabled"] = False
        domain["tokenExchangeSettings"].update(enabled=False, allowImpersonation=True)
        domain["oidc"]["clientRegistrationSettings"]["allowLocalhostRedirectUri"] = False
        domain["oidc"]["redirectUriStrictMatching"] = False
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.writes(), [("PATCH", f"{DOMAINS}/{self.domain_id()}")])
        self.assertEqual((domain["enabled"], domain["tokenExchangeSettings"]["enabled"],
                          domain["tokenExchangeSettings"]["allowImpersonation"]), (True, True, False))
        self.assertTrue(domain["oidc"]["clientRegistrationSettings"]["allowLocalhostRedirectUri"])
        self.assertTrue(domain["oidc"]["redirectUriStrictMatching"])

    def test_an_application_someone_changed_is_saved_again_and_only_that_one(self):
        ace = self.am.one("applications", clientId="ace-agent")
        ace["settings"]["oauth"]["tokenCustomClaims"] = ace["settings"]["oauth"]["tokenCustomClaims"][:2]
        ace["identityProviders"] = []
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(len(self.writes()), 2)
        self.assertEqual(len(ace["settings"]["oauth"]["tokenCustomClaims"]), 5)
        self.assertEqual(ace["identityProviders"][0]["identity"], "idp-1")

    def test_a_web_app_made_with_the_password_grant_is_changed_in_place_to_the_code_flow_and_then_left_alone(self):
        web = self.am.one("applications", clientId="acme-bakery-web")
        web["settings"]["oauth"].update(grantTypes=["password", "refresh_token"], responseTypes=[], forcePKCE=False,
                                        forceS256CodeChallengeMethod=False, redirectUris=["http://localhost:8110/callback"])
        web["settings"]["advanced"]["skipConsent"] = False
        applications = len(self.am.objects("applications"))
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.writes(), [("PATCH", f"{DOMAINS}/{self.domain_id()}/applications/{web['id']}")])
        oauth = web["settings"]["oauth"]
        self.assertEqual((oauth["grantTypes"], oauth["responseTypes"], oauth["forcePKCE"], oauth["forceS256CodeChallengeMethod"]),
                         (["authorization_code", "refresh_token"], ["code"], True, True))
        self.assertEqual(oauth["redirectUris"], ["http://localhost:8110/callback", "http://127.0.0.1:8110/callback"])
        self.assertTrue(web["settings"]["advanced"]["skipConsent"])
        self.assertEqual(len(self.am.objects("applications")), applications, "changed, not made again")
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.writes(), [])

    def test_a_profile_someone_changed_is_made_as_the_file_says_with_the_fields_of_am_kept(self):
        tina = self.am.one("users", username="tina@acme-bakery.test")
        tina["additionalInformation"] = {"role": "customer", "sub": "from-am", "given_name": "Tina"}
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(tina["additionalInformation"], {"role": "teller", "sub": "from-am", "given_name": "Tina", "name": "Tina Torres"})
        self.assertEqual(self.writes(), [("PUT", f"{DOMAINS}/{self.domain_id()}/users/{tina['id']}")])

    def test_a_group_that_lost_a_member_gets_it_back_and_the_others_stay(self):
        customers = self.am.one("groups", name="customers")
        customers["members"].pop()
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(len(customers["members"]), 2)
        self.assertEqual(len(self.writes()), 1)

    def domain_id(self):
        return next(iter(self.am.domains))


class Settings(Case):
    """Applications and domain settings."""

    SPEC = {"name": "Website", "type": "BROWSER", "clientId": "site", "clientSecret": "s", "redirectUris": ["http://site/"],
            "scopes": ["openid"], "oauth": {"grantTypes": ["authorization_code"], "pkce": True,
                                            "tokenCustomClaims": [{"claimName": "email", "claimValue": "{x}"}]}}

    def test_an_application_keeps_the_defaults_of_its_type(self):
        self.assertEqual(am.AccessManagement._oauth(self.SPEC), {
            "grantTypes": ["authorization_code"], "responseTypes": ["code", "code id_token token", "code id_token", "code token"],
            "redirectUris": ["http://site/"], "tokenEndpointAuthMethod": "client_secret_basic", "forcePKCE": True,
            "forceS256CodeChallengeMethod": True, "accessTokenValiditySeconds": 7200, "refreshTokenValiditySeconds": 14400,
            "idTokenValiditySeconds": 14400, "scopeSettings": [{"scope": "openid", "defaultScope": False, "scopeApproval": 300}],
            "tokenCustomClaims": [{"tokenType": "access_token", "claimName": "email", "claimValue": "{x}"}]})

    def test_an_application_is_made_then_given_its_settings_and_its_identity_provider(self):
        self.seed_files({"am/domain.yaml": "name: example\n", "am/applications/site.yaml": json.dumps(self.SPEC)})   # JSON is YAML
        self.seed()
        self.assertEqual(self.writes(),
                         [("POST", DOMAINS), ("PATCH", f"{DOMAINS}/domain-1"), ("POST", f"{DOMAINS}/domain-1/applications"),
                          ("PATCH", f"{DOMAINS}/domain-1/applications/application-2"),
                          ("PATCH", f"{DOMAINS}/domain-1/applications/application-2")])
        self.assertEqual([x["kind"] for x in common.created()], ["domain", "application"])

    def test_a_domain_is_patched_with_its_settings_as_the_file_gives_them(self):
        self.seed_files({"am/domain.yaml": "name: example\nsettings: {tokenExchangeSettings: {enabled: true}}\n"})
        self.seed()
        self.assertEqual(self.platform.sent("PATCH", f"{DOMAINS}/domain-1")[0].json,
                         {"enabled": True, "tokenExchangeSettings": {"enabled": True}})


class Variables(unittest.TestCase):
    """What the seed service of the playground must set for these files."""

    @staticmethod
    def used_in(folder):
        return {m.group(2) for path in sorted((ACME_SEED / folder).rglob("*.yaml")) for m in common.VARIABLE.finditer(path.read_text(encoding="utf-8"))
                if not m.group(1)}

    def test_the_files_of_access_management_use_the_secrets_the_apps_share_and_the_urls(self):
        self.assertEqual(self.used_in("am"), set(SECRETS))

    def test_the_files_of_authorization_use_no_variable_the_slug_is_read_from_the_catalog(self):
        self.assertEqual(self.used_in("authz"), set())


class Kinds(Case):
    """The records and the removal."""

    def test_a_removal_deletes_each_kind_where_it_lives_and_the_domain_last(self):
        self.use_bank_files()
        self.seed()
        am.remove(common.created())
        deletes = [path.split("/")[-2:] for method, path in self.platform.changes() if method == "DELETE"]
        kinds = [kind for kind, _ in deletes]
        self.assertEqual(kinds[0], "protected-resources")
        self.assertEqual(kinds[1:4], ["applications"] * 3)   # Ace too: an agent is an application of the domain
        self.assertEqual(kinds[4:6], ["groups", "groups"])
        self.assertEqual(kinds[6:9], ["users"] * 3)
        self.assertEqual(kinds[9], "domains")
        self.assertEqual(self.am.domains, {})

    def test_a_removal_leaves_the_kinds_of_other_products_alone(self):
        am.remove([{"product": "apim", "kind": "api", "name": "x", "id": "x"}])
        self.assertEqual(self.platform.calls, [])

    def test_a_domain_the_seed_did_not_create_is_configured_never_taken_over(self):
        self.seed_files({"am/domain.yaml": "name: mine\nsettings: {oidc: {x: 1}}\n"})
        self.am.domains["d"] = {"id": "d", "name": "mine"}
        self.seed()
        self.seed()
        self.assertEqual(self.writes(), [("PATCH", f"{DOMAINS}/d")], "configured once, as it says, then left alone")
        self.assertEqual(self.am.domains["d"]["oidc"], {"x": 1})
        self.assertEqual(common.created(), [])

    def test_a_domain_that_says_what_the_file_says_is_not_saved_again(self):
        self.seed_files({"am/domain.yaml": "name: bank\nsettings: {oidc: {x: 1}}\n"})
        self.seed()
        self.seed()
        self.assertEqual(self.writes(), [("POST", DOMAINS), ("PATCH", f"{DOMAINS}/domain-1")])
        self.assertIn("Settings as the file says", self.output())

    def test_a_changed_setting_is_sent_again(self):
        self.seed_files({"am/domain.yaml": "name: bank\nsettings: {oidc: {x: 1}}\n"})
        self.seed()
        self.seed_files({"am/domain.yaml": "name: bank\nsettings: {oidc: {x: 2}}\n"})
        self.seed()
        self.assertEqual(len([c for c in self.writes() if c[0] == "PATCH"]), 2)
        self.assertEqual(self.am.domains["domain-1"]["oidc"], {"x": 2})
        self.assertEqual(common.created(), [{"product": "am", "kind": "domain", "label": "domains", "name": "bank", "id": "domain-1"}],
                         "one record, written when the domain was made and holding nothing about its settings")

    def test_a_user_without_a_profile_is_posted_as_the_file_says_and_found_again_by_the_refusal(self):
        self.seed_files({"am/domain.yaml": "name: bank\nusers: [{username: john, email: j@x.test, firstName: J, lastName: D, password: p}]\n"})
        self.seed()
        self.seed()
        posts = self.platform.sent("POST", re.compile(DOMAINS + "/[^/]+/users"))
        self.assertEqual(len(posts), 2, "a user without a profile is not looked for: the platform refuses it the second time")
        self.assertEqual(posts[0].json, {"forceResetPassword": False, "preRegistration": False, "username": "john",
                                         "email": "j@x.test", "firstName": "J", "lastName": "D", "password": "p"})
        self.assertEqual([x["name"] for x in common.created() if x["kind"] == "user"], ["john"])

    def test_the_member_of_a_group_must_exist(self):
        self.seed_files({"am/domain.yaml": "name: bank\ngroups: [{name: g, members: [nobody]}]\n"})
        with self.assertRaisesRegex(common.SeedError, "Group 'g': user 'nobody' is not in the domain"):
            self.seed()

    def test_a_group_can_be_optional_like_the_rest(self):
        self.seed_files({"am/domain.yaml": "name: bank\ngroups: [{name: g, optional: true, members: [nobody]}]\n"})
        self.seed()
        self.assertIn("! Skipped 'g'", self.output())


class Session(Case):
    """The token of the management API lasts about a minute."""

    def connected(self):
        access = am.AccessManagement()
        access.connect()
        return access

    def test_a_request_that_is_refused_for_a_token_that_expired_is_sent_again_after_a_new_login(self):
        access = self.connected()
        self.am.expire()
        self.assertEqual(access.api.get(DOMAINS).status_code, 200)
        self.assertEqual(self.am.logins, 2)
        self.assertEqual(len(self.platform.sent("GET", DOMAINS)), 2)

    def test_a_token_that_holds_is_not_replaced(self):
        access = self.connected()
        access.api.get(DOMAINS)
        access.api.get(DOMAINS)
        self.assertEqual(self.am.logins, 1)

    def test_the_second_login_uses_the_same_credentials_as_the_first(self):
        access = self.connected()
        self.am.expire()
        access.api.get(DOMAINS)
        self.assertEqual([call.auth for call in self.platform.sent(*LOGIN)], [("admin", "adminadmin")] * 2)

    def test_a_seed_that_lasts_longer_than_the_token_goes_on(self):
        self.use_bank_files()
        self.am.expires_at = 15
        am.seed({})
        self.assertEqual(self.am.logins, 2)
        self.assertEqual((len(self.am.objects("users")), len(self.am.objects("applications"))), (3, 3))
        self.assertEqual(len(common.created()), 10)

    def test_a_token_given_by_the_environment_is_not_exchanged_and_a_refusal_is_an_error(self):
        self.env(AM_TOKEN="given", AM_USERNAME=None, AM_PASSWORD=None)
        self.platform.add({("GET", DOMAINS): (401, {"message": "Unauthorized"})})
        with mock.patch.object(common.time, "sleep"):
            with self.assertRaises(common.SeedError):
                am.AccessManagement().api.get(DOMAINS)
        self.assertEqual(self.platform.sent(*LOGIN), [])

    def test_a_login_that_is_refused_is_asked_again_until_the_platform_answers(self):
        self.platform.add({LOGIN: [(503, {}), (200, {"access_token": "ok"})]})
        access = am.AccessManagement()
        access.connect()
        self.assertEqual(access.api.session.headers["Authorization"], "Bearer ok")


class Gateway(Case):
    """With AM_GATEWAY_URL, the seed ends once the gateway serves the domain and knows each application that has a secret:
    an agent that starts after the seed signs in at once, and the gateway takes them only at its next synchronization."""

    DOMAIN = "http://am-gateway:8092/acme-bakery"
    DISCOVERY = DOMAIN + "/oidc/.well-known/openid-configuration"
    TOKEN = DOMAIN + "/oauth/token"

    def setUp(self):
        super().setUp()
        self.env(AM_GATEWAY_URL="http://am-gateway:8092/")

    def gateway(self, discovery, token, domain=DOMAIN):
        self.platform.add({("GET", domain + "/oidc/.well-known/openid-configuration"): discovery,
                           ("POST", domain + "/oauth/token"): token})

    def asked(self, token=TOKEN):
        return self.platform.sent("POST", token)

    def test_it_waits_for_the_domain_then_for_each_application_with_its_secret(self):
        known = set()

        def token(call):   # the gateway does not know an application the first time it is asked
            if call.auth in known:
                return 200, {"access_token": "a token"}
            known.add(call.auth)
            return 401, {"error": "invalid_client"}
        self.use_bank_files()
        self.gateway([404, 404, (200, {})], token)
        am.seed({})
        calls = [call.url for call in self.platform.calls]
        self.assertEqual(calls.count(self.DISCOVERY), 3)
        self.assertLess(max(i for i, url in enumerate(calls) if url == self.DISCOVERY), calls.index(self.TOKEN),
                        "the domain comes first")
        self.assertEqual([call.auth for call in self.asked()][1::2], [("ace-agent", "ace-secret"),
                         ("acme-bakery-traffic", "traffic-secret"), ("acme-bakery-web", "web-secret")])
        self.assertEqual({str(call.data) for call in self.asked()}, {str({"grant_type": "client_credentials"})})

    def test_a_grant_the_application_may_not_use_still_says_the_gateway_knows_it(self):
        self.use_bank_files()
        self.gateway((200, {}), (400, {"error": "unauthorized_client"}))
        am.seed({})
        self.assertEqual(len(self.asked()), 3)

    def test_a_domain_the_gateway_never_serves_ends_the_seed_with_an_error_that_names_it(self):
        self.use_bank_files()
        self.gateway(404, (200, {}))
        with self.assertRaisesRegex(common.SeedError, "Domain 'acme-bakery' on the gateway not ready"):
            am.seed({})
        self.assertEqual(self.asked(), [])

    def test_the_domain_is_reached_on_its_path_and_a_secret_sent_in_the_form_is_sent_there(self):
        self.am.domains["domain-0"] = {"id": "domain-0", "name": "kiosk", "path": "/kiosk-orders"}
        self.seed_files({"am/domain.yaml": "name: kiosk\n",
                         "am/applications/till.yaml": "name: Till\ntype: SERVICE\nclientId: till\nclientSecret: till-secret\n"
                                                      "oauth:\n  grantTypes: [client_credentials]\n"
                                                      "  tokenEndpointAuthMethod: client_secret_post\n",
                         "am/applications/screen.yaml": "name: Screen\ntype: BROWSER\nclientId: screen\n"})
        self.gateway((200, {}), (200, {"access_token": "a token"}), "http://am-gateway:8092/kiosk-orders")
        am.seed({})
        [call] = self.asked("http://am-gateway:8092/kiosk-orders/oauth/token")
        self.assertEqual((call.auth, call.data), (None, {"grant_type": "client_credentials", "client_id": "till",
                                                         "client_secret": "till-secret"}))


if __name__ == "__main__":
    unittest.main()
