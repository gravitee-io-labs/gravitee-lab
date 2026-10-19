"""Access Management: one security domain and what it holds, from seed/am/.

    seed/am/domain.yaml                 name, description, settings (sent as is to PATCH /domains/{id}),
                                        scopes ({key, name, description}: what a person is asked to allow, made before
                                        the applications that name them), users, groups, authorizationEngines
    seed/am/applications/*.yaml         one application each (OAuth settings, redirect URIs, scopes, claims, and
                                        `skipConsent: true` for a first-party app, whose sign-in then has no consent page);
                                        `type: AGENT` with a `kind` is an agent, the ones Agent Identity lists
    seed/am/protected-resources/*.yaml  MCP servers and their tools

Everything is found by name or client ID before being created, so re-running is safe, and what already says what the
file says is not saved again: AM reloads a domain or an application on every save. A user with `additionalInformation`
(what the tokens carry) is found first and kept as the file says, next to the fields AM adds at the first sign-in.
An item marked `optional: true` only warns when it fails (typically a feature the license does not include).

An agent keeps what its kind gives it (grant types, response types, validities): the file adds its `scopes` (the ones it
may ask a person for), its claims and its redirect URIs. A hosted or autonomous agent starts with a key pair to sign in
with, which nothing here holds: when the file gives it a `clientSecret`, it signs in with that secret. The secret is the
file's: it is never generated, read back or logged.
"""

import json
import os

from .common import Api, SeedError, files, items, load, log, owned, record, recorded, seed_file, setting, wait_until

SECTION = "am"
DEPENDS = ("openfga",)   # the modules whose work this one reads on the platform (schedule.py)
# What it records, dependents first
ORDER = ["authorization-engine", "protected-resource", "agent", "application", "group", "user", "scope", "domain"]
# Where each kind lives under its domain; the domain itself lives under the environment
PATHS = {"authorization-engine": "authorization-engines", "protected-resource": "protected-resources",
         "agent": "applications", "application": "applications", "group": "groups", "user": "users", "scope": "scopes"}
# AM keeps a person's approval of a scope for this long, so that signing in again soon asks nothing more
SCOPE_APPROVAL_SECONDS = 300
# The kinds of agent that AM starts with `private_key_jwt`; a file that gives one a secret has it sign in with it
KEYED_KINDS = ("HOSTED_DELEGATED", "AUTONOMOUS")
SCOPES_PER_PAGE = 50   # AM answers no more, whatever the request asks


def contains(actual, wanted):
    """Whether `actual` holds everything `wanted` says: the platform adds defaults and ids of its own."""
    if isinstance(wanted, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in wanted.items())
    if isinstance(wanted, list):
        return isinstance(actual, list) and len(actual) == len(wanted) and all(contains(a, w) for a, w in zip(actual, wanted))
    return actual == wanted


def same_scopes(actual, wanted):
    """Whether the scopes of an application are those the file lists, whatever order AM keeps them in."""
    held = {entry.get("scope"): entry for entry in actual or [] if isinstance(entry, dict)}
    return len(held) == len(wanted) and all(w["scope"] in held and contains(held[w["scope"]], w) for w in wanted)


def scope_settings(scopes):
    """The scopes an application may ask for, as AM keeps them."""
    return [{"scope": scope, "defaultScope": False, "scopeApproval": SCOPE_APPROVAL_SECONDS}
            for scope in dict.fromkeys(scopes)]


def holds(key, actual, wanted):
    """Whether the OAuth setting `key` of an application already says what the file says."""
    return same_scopes(actual, wanted) if key == "scopeSettings" else contains(actual, wanted)


class AmApi(Api):
    """The AM management API. A token from a login lasts about a minute, so a request it refuses is sent again after
    logging in once more."""

    def __init__(self, base_url):
        super().__init__(base_url)
        self.credentials = None   # none with a token (AM_TOKEN): there is nothing to log in with

    def login(self):
        r = self.post("/management/auth/token", auth=self.credentials, headers={"Content-Type": None})
        self.session.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    def send(self, method, path, **kwargs):
        answer = super().send(method, path, **kwargs)
        if answer.status_code == 401 and self.credentials and "auth" not in kwargs:
            self.login()
            answer = super().send(method, path, **kwargs)
        return answer


class AccessManagement:
    def __init__(self):
        self.api = AmApi(setting("AM_MANAGEMENT_URL"))
        self.org, self.env = setting("AM_ORG", "DEFAULT"), setting("AM_ENV", "DEFAULT")
        self.org_url = f"/management/organizations/{self.org}"
        self.env_url = f"{self.org_url}/environments/{self.env}"
        self.domain_id = self.domain_url = None

    # --- Connection -----------------------------------------------------------------------

    def connect(self):
        token = os.getenv("AM_TOKEN")
        if token:
            self.api.session.headers["Authorization"] = f"Bearer {token}"
            wait_until(SECTION, "Access Management", lambda: self.api.get(f"{self.env_url}/domains"))
            return
        self.api.credentials = (setting("AM_USERNAME"), setting("AM_PASSWORD"))
        wait_until(SECTION, "Access Management", self.api.login)

    # --- Domain ---------------------------------------------------------------------------

    def domains(self):
        return items(self.api.get(f"{self.env_url}/domains", params={"size": 200}).json())

    def find_domain(self, name):
        """The domain with this name or hrid: a domain that is not there is an error."""
        found = next((d for d in self.domains() if name in (d["name"], d.get("hrid"))), None)
        if not found:
            raise SeedError(f"AM domain '{name}' not found")
        return found

    def domain(self, spec):
        name = spec["name"]
        found = next((d for d in self.domains() if d["name"] == name), None)
        if found:
            domain_id = found["id"]
            log(SECTION, f"Domain '{name}' exists")
        else:
            domain_id = self.api.post(f"{self.env_url}/domains", json={
                "name": name, "description": spec.get("description", ""), "dataPlaneId": spec.get("dataPlaneId", "default"),
            }).json()["id"]
            record(SECTION, "domain", "domains", name, domain_id)
            log(SECTION, f"✓ Domain '{name}' created")
        self.domain_id = domain_id
        self.domain_url = f"{self.env_url}/domains/{domain_id}"
        body = {"enabled": True, **spec.get("settings", {})}
        if contains(self.api.get(self.domain_url).json(), body):
            log(SECTION, "  Settings as the file says")
            return
        self.api.patch(self.domain_url, json=body)
        log(SECTION, "  ✓ Enabled and configured")

    # --- Scopes ---------------------------------------------------------------------------

    def _scopes(self):
        """Every scope of the domain: AM answers its list by pages, of SCOPES_PER_PAGE at most."""
        found, page = [], 0
        while True:
            body = self.api.get(f"{self.domain_url}/scopes", params={"page": page, "size": SCOPES_PER_PAGE}).json()
            rows = items(body)
            found += rows
            total = body.get("totalCount") if isinstance(body, dict) else None
            if not rows or (len(found) >= total if total is not None else len(rows) < SCOPES_PER_PAGE):
                return found
            page += 1

    def scopes(self, wanted):
        """The scopes of the file, made in the domain when it lacks them: an application can only name a scope that
        exists. One that is there is left as it is, unless the seed made it, which then follows the file."""
        if not wanted:
            return
        url = f"{self.domain_url}/scopes"
        held = {scope["key"]: scope for scope in self._scopes()}
        made = changed = 0
        for spec in wanted:
            spec = {"key": spec} if isinstance(spec, str) else spec
            if not spec.get("key"):
                raise SeedError("seed/am/domain.yaml: a scope needs a key")
            key, current = spec["key"], held.get(spec["key"])
            said = {"name": spec.get("name", key), "description": spec.get("description", spec.get("name", key))}
            if not current:
                held[key] = self.api.post(url, json={"key": key, **said}).json()
                record(SECTION, "scope", "scopes", key, held[key]["id"], domain=self.domain_id)
                made += 1
            elif recorded(SECTION, "scope", current["id"]) and not contains(current, said):
                self.api.patch(f"{url}/{current['id']}", json=said)
                changed += 1
        done = ([f"{made} created"] if made else []) + ([f"{changed} put back"] if changed else [])
        log(SECTION, f"✓ Scopes: {', '.join(done)}" if done else f"{len(wanted)} scopes as the file says")

    # --- Applications ---------------------------------------------------------------------

    def application(self, spec):
        name, client_id = spec["name"], spec["clientId"]
        found = self._find_application(client_id)
        if found:
            app_id = found["id"]
            log(SECTION, f"Application '{name}' exists")
        else:
            payload = {k: spec[k] for k in ("name", "type", "kind", "clientId", "clientSecret", "redirectUris", "description", "agentCardUrl") if k in spec}
            payload.setdefault("type", "BROWSER")
            app_id = self.api.post(f"{self.domain_url}/applications", json=payload).json()["id"]
            if payload["type"] == "AGENT":
                record(SECTION, "agent", "AM agents", name, app_id, domain=self.domain_id)
            else:
                record(SECTION, "application", "applications", name, app_id, domain=self.domain_id)
            log(SECTION, f"✓ Application '{name}' created")
            # What AM made of it, which the file is compared with: a kind of agent brings settings of its own
            found = self.api.get(f"{self.domain_url}/applications/{app_id}").json()
        current, patched, settings = found, False, {}
        wanted = self._oauth(spec) if spec.get("oauth") or spec.get("kind") else {}
        if wanted:
            held = (current.get("settings") or {}).get("oauth") or {}
            if spec.get("kind"):
                # An agent is saved only where it differs: what its kind gives it is not the file's to say
                wanted = {key: value for key, value in wanted.items() if not holds(key, held.get(key), value)}
            elif contains(held, wanted):
                wanted = {}
            if wanted:
                settings["oauth"] = wanted
        if "skipConsent" in spec:
            advanced = {"skipConsent": spec["skipConsent"]}
            if not contains((current.get("settings") or {}).get("advanced"), advanced):
                settings["advanced"] = advanced
        if settings:
            self.api.patch(f"{self.domain_url}/applications/{app_id}", json={"settings": settings})
            patched = True
        # Users of the domain sign in with its default identity provider
        system = next((i for i in self.api.get(f"{self.domain_url}/identities").json() if i.get("system")), None)
        if system:
            providers = [{"identity": system["id"], "selectionRule": "", "priority": 0}]
            if not contains(current.get("identityProviders"), providers):
                self.api.patch(f"{self.domain_url}/applications/{app_id}", json={"identityProviders": providers})
                patched = True
        log(SECTION, "  ✓ OAuth settings and identity provider" if patched else "  OAuth settings and identity provider as the file says")

    def _find_application(self, client_id):
        for app in items(self.api.get(f"{self.domain_url}/applications", params={"q": client_id, "size": 200}).json()):
            detail = self.api.get(f"{self.domain_url}/applications/{app['id']}").json()
            if detail.get("settings", {}).get("oauth", {}).get("clientId") == client_id:
                return detail
        return None

    @staticmethod
    def _oauth(spec):
        """The friendly `oauth` block of an application file, as the AM API expects it."""
        oauth = spec.get("oauth") or {}
        validity = oauth.get("tokenValidity", {})
        claims = [{"tokenType": c.get("tokenType", "access_token"), "claimName": c["claimName"], "claimValue": c["claimValue"]}
                  for c in oauth.get("tokenCustomClaims", [])]
        if spec.get("kind"):
            # An agent keeps what its kind gives it (grant types, response types, validity): the file adds what it may
            # ask a person for, what its tokens say, where it is sent back to and how it proves who it is
            settings = {}
            if spec.get("oauth"):
                settings["tokenCustomClaims"] = claims
            if spec.get("scopes"):
                settings["scopeSettings"] = scope_settings(spec["scopes"])
            if spec.get("redirectUris") and spec["kind"] != "AUTONOMOUS":   # it has no one to send back
                settings["redirectUris"] = spec["redirectUris"]
            method = oauth.get("tokenEndpointAuthMethod")
            if not method and spec.get("clientSecret") and spec["kind"] in KEYED_KINDS:
                method = "client_secret_basic"
            if method:
                settings["tokenEndpointAuthMethod"] = method
            return settings
        settings = {
            "grantTypes": oauth.get("grantTypes", []),
            "responseTypes": oauth.get("responseTypes", ["code", "code id_token token", "code id_token", "code token"]),
            "redirectUris": spec.get("redirectUris", []),
            "tokenEndpointAuthMethod": oauth.get("tokenEndpointAuthMethod", "client_secret_basic"),
            "forcePKCE": oauth.get("pkce", False),
            "forceS256CodeChallengeMethod": oauth.get("pkce", False),
            "accessTokenValiditySeconds": validity.get("accessToken", 7200),
            "refreshTokenValiditySeconds": validity.get("refreshToken", 14400),
            "idTokenValiditySeconds": validity.get("idToken", 14400),
            "scopeSettings": scope_settings(spec.get("scopes", [])),
            "tokenCustomClaims": claims,
        }
        if oauth.get("tokenExchange"):
            settings["tokenExchangeOAuthSettings"] = {"inherited": oauth["tokenExchange"].get("inherited", True),
                                                      "scopeHandling": oauth["tokenExchange"].get("scopeHandling", "downscoping")}
        return settings

    # --- Users ----------------------------------------------------------------------------

    def user(self, spec):
        spec = {key: value for key, value in spec.items() if key != "optional"}   # the seed's flag, not a user's field
        if "additionalInformation" in spec:
            self._user_with_profile(spec)
            return
        r = self.api.post(f"{self.domain_url}/users", expect=(200, 201, 202, 400), json={
            "forceResetPassword": False, "preRegistration": False, **spec})
        if r.status_code == 400 and "already exists" not in r.text.lower():
            raise SeedError(f"User '{spec['username']}': {r.text[:300]}")
        if r.status_code != 400:
            record(SECTION, "user", "users", spec["username"], r.json()["id"], domain=self.domain_id)
        log(SECTION, f"{'✓ User created' if r.status_code != 400 else 'User exists'}: {spec['username']}")

    def _find_user(self, username):
        users = items(self.api.get(f"{self.domain_url}/users", params={"q": username, "size": 50}).json())
        return next((u for u in users if u["username"] == username), None)

    def _user_with_profile(self, spec):
        """A user whose profile (`additionalInformation`) the tokens carry: created when missing, then its profile made to
        hold what the file says. AM adds fields of its own to it at the first sign-in, which are kept."""
        username, profile = spec["username"], spec["additionalInformation"]
        found = self._find_user(username)
        if found:
            user = self.api.get(f"{self.domain_url}/users/{found['id']}").json()
            log(SECTION, f"User exists: {username}")
        else:
            body = {k: v for k, v in spec.items() if k != "additionalInformation"}
            user = self.api.post(f"{self.domain_url}/users", json={
                "forceResetPassword": False, "preRegistration": False, **body}).json()
            record(SECTION, "user", "users", username, user["id"], domain=self.domain_id)
            log(SECTION, f"✓ User created: {username}")
        current = user.get("additionalInformation") or {}
        if not contains(current, profile):
            self.api.put(f"{self.domain_url}/users/{user['id']}", json={
                "email": spec["email"], "firstName": spec["firstName"], "lastName": spec["lastName"],
                "additionalInformation": {**current, **profile}})
            log(SECTION, f"  ✓ Profile of {username}")

    # --- Groups ---------------------------------------------------------------------------

    def group(self, spec):
        name, url = spec["name"], f"{self.domain_url}/groups"
        found = next((g for g in items(self.api.get(url, params={"size": 200}).json()) if g["name"] == name), None)
        if found:
            group = self.api.get(f"{url}/{found['id']}").json()
            log(SECTION, f"Group '{name}' exists")
        else:
            group = self.api.post(url, json={"name": name, "description": spec.get("description", name)}).json()
            record(SECTION, "group", "groups", name, group["id"], domain=self.domain_id)
            log(SECTION, f"✓ Group '{name}' created")
        for username in spec.get("members", []):
            user = self._find_user(username)
            if not user:
                raise SeedError(f"Group '{name}': user '{username}' is not in the domain")
            if user["id"] not in (group.get("members") or []):
                # The member is in the path: a POST with a body on /members is refused (405)
                self.api.post(f"{url}/{group['id']}/members/{user['id']}")
                log(SECTION, f"  ✓ {username} joined '{name}'")

    # --- Protected resources (MCP servers) ------------------------------------------------

    def protected_resource(self, spec):
        kind = spec.get("type", "MCP_SERVER")
        existing = items(self.api.get(f"{self.domain_url}/protected-resources", params={"type": kind, "size": 200}).json())
        if any(r.get("clientId") == spec["clientId"] for r in existing):
            log(SECTION, f"Protected resource '{spec['name']}' exists")
            return
        created_resource = self.api.post(f"{self.domain_url}/protected-resources", json={
            "name": spec["name"], "description": spec.get("description", ""), "type": kind,
            "resourceIdentifiers": spec.get("resourceIdentifiers", []),
            "clientId": spec["clientId"], "clientSecret": spec.get("clientSecret"),
            "features": [{"key": t["key"], "description": t.get("description", ""), "type": t.get("type", "MCP_TOOL"),
                          "scopes": t.get("scopes", [])} for t in spec.get("tools", [])],
        }).json()
        record(SECTION, "protected-resource", "MCP servers", spec["name"], created_resource["id"], domain=self.domain_id)
        log(SECTION, f"✓ Protected resource '{spec['name']}' created ({len(spec.get('tools', []))} tools)")

    # --- Authorization engines ------------------------------------------------------------

    def authorization_engine(self, spec, stores):
        """An engine of that type is the one. The seed's own follows the seed: an OpenFGA model gets a new id when it
        changes, a store when it is made again."""
        configuration = {"connectionUri": spec["connectionUri"]}
        if spec.get("store"):
            store = stores.get(spec["store"])
            if not store:
                raise SeedError(f"OpenFGA store '{spec['store']}' is not in seed/openfga")
            configuration.update(storeId=store["id"], authorizationModelId=store["model_id"])
        url = f"{self.domain_url}/authorization-engines"
        engine = next((e for e in self.api.get(url).json() if e.get("type") == spec["type"]), None)
        if engine:
            current = json.loads(engine.get("configuration") or "{}")
            if {key: current.get(key) for key in configuration} == configuration:
                log(SECTION, f"Authorization engine '{spec['name']}' exists")
            elif not recorded(SECTION, "authorization-engine", engine["id"]):
                raise SeedError(f"Authorization engine '{engine.get('name')}' has another configuration, "
                                "and the seed did not create it")
            else:
                self.api.put(f"{url}/{engine['id']}", json={"name": spec["name"], "configuration": json.dumps(configuration)})
                log(SECTION, f"✓ Authorization engine '{spec['name']}' updated")
            return
        engine = self.api.post(url, json={"type": spec["type"], "name": spec["name"], "configuration": json.dumps(configuration)}).json()
        record(SECTION, "authorization-engine", "authorization engines", spec["name"], engine["id"],
               domain=self.domain_id)
        log(SECTION, f"✓ Authorization engine '{spec['name']}' created")


def optional(spec, step):
    """Runs a step; an optional one only warns when it fails."""
    try:
        step()
    except SeedError as e:
        if not spec.get("optional"):
            raise
        log(SECTION, f"! Skipped '{spec.get('name', spec.get('username', ''))}': {e}")


def seed(stores):
    domain_file = seed_file("am", "domain.yaml")
    if not domain_file:
        return
    domain = load(domain_file)
    am = AccessManagement()
    am.connect()
    am.domain(domain)
    am.scopes(domain.get("scopes", []))
    for path in files("am", "applications"):
        spec = load(path)
        optional(spec, lambda: am.application(spec))
    for spec in domain.get("users", []):
        optional(spec, lambda: am.user(spec))
    for spec in domain.get("groups", []):
        optional(spec, lambda: am.group(spec))
    for path in files("am", "protected-resources"):
        spec = load(path)
        optional(spec, lambda: am.protected_resource(spec))
    for spec in domain.get("authorizationEngines", []):
        optional(spec, lambda: am.authorization_engine(spec, stores))


def remove(records):
    mine = owned(records, SECTION, ORDER)
    if not mine:
        return
    am = AccessManagement()
    am.connect()
    for x in mine:
        path = f"{am.env_url}/domains/{x['id']}" if x["kind"] == "domain" else f"{am.env_url}/domains/{x['domain']}/{PATHS[x['kind']]}/{x['id']}"
        am.api.delete(path)
        log(SECTION, f"✓ Deleted {x['kind'].replace('-', ' ')} '{x['name']}'")
