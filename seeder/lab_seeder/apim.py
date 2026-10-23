"""API Management: environment settings, APIs, applications and subscriptions, from seed/apim/ and, for each extra
environment, from seed/environments/<id>/.

    seed/apim/settings.yaml          merged into the environment settings (Portal Next, custom API keys...)
    seed/apim/apis/*.json            API exports (v4 definition, as the Console exports them); the ids of the API and of
                                     its plans are fixed in the file when something else refers to them
    seed/apim/applications/*.yaml    name, description, settings
    seed/apim/subscriptions/*.yaml   subscriptions: [{application, api, plan, apiKey}] by name

    seed/environments/<id>/          one folder for each extra environment, which is its id (lowercase letters, digits, dashes)
        environment.yaml             id (the folder's), name, description
        settings.yaml, apis/, applications/, subscriptions/    as in seed/apim/, for that environment
        users.yaml                   users: [{source, sourceId, email, firstname, lastname}]
        groups/<hrid>.yaml           a group of the Automation API (hrid, name, notifyMembers, members) and `defaults`

An API is found by its id when its definition has one, else by its first listener path. It is imported once, with the
digest of its definition recorded next to it. A run that finds the same digest on a started API leaves it alone: no
import, no deployment. A new digest updates the API from the file, or replaces it when its type cannot be updated from
a definition (LLM, MCP and A2A proxies), then publishes, deploys and starts it. An API the seed did not create holds
that id or path: the seed stops there, it never changes it. An import that fails after the platform made the API ("API imported
with error") leaves it, with its id in the error: the seed records it with no digest, so that the next run finds it as its own and
updates it from the file.
The controls of seed/controls.yaml (controls.py) are the one thing a run brings back whatever the digest says: the steps they
name are enabled or disabled as the mode says (SEED_PROTECTIONS), in the live API, even one someone switched in the console.
That is an update of the API entity then a deployment, never an import: the API keeps its keys and its plans, and the gateway
reloads it once. The digest is the file's, the mode is not part of it, so a run in the other mode replaces nothing. An API
whose live flows lack a step a control names is imported again from its file, and so is one on a platform that does not offer
the update (it answers 404, 405 or 501). A platform that does not answer (a connection that fails, a timeout, a 5xx, a 429) is
asked again a few times, and any other refusal stops the seed with the platform's message: the API is never replaced because the
platform was busy or because the seed sent something it found wrong.
Applications are found by name: one the seed did not create holds that name, and the seed stops there as well.
Subscriptions are found by application and plan, on the API the seed made under that name. A subscription with an apiKey
asks for that custom key, which the environment settings must allow. A `consumerConfiguration` is the callback of a push plan,
which an existing subscription is updated to when it differs. A `comment` is the message of the request and `pending: true`
leaves the subscription for someone to accept: only the Portal API creates those (the management API accepts at once what it
creates), so a subscription that has either goes through it.

An environment is never created by the seed (no API does that): the playground makes it, and the seed stops at one that is
absent. In it the seed registers users, writes groups through the Automation API and sets each API's groups and each
application's groups from the hrids of the group files (an API import knows groups by name only, and creates one it does not
find). What it records for an environment says which one (`env`), and the name of the record ends with `[<id>]`, so the same API
name in two environments is two records and a removal deletes what each record says, where it says. The controls of
seed/controls.yaml apply to seed/apim/ only. A memory user has no row in the platform before its first login: the seed gets the
row by adding the user to a group from the search of the identity provider, which is how the Console adds one. The files of an
environment name `${ENV_ID}` and `${ENV_NAME}`, which the engine gives them.

With GATEWAY_INTERNAL_URL set, a replaced API waits until the gateway has dropped the old one before it is imported
again, and the seed ends once the gateway serves every API, within GATEWAY_READY_TIMEOUT seconds in all: the AI models of
an LLM proxy are downloaded at its first deployment. With LLM_PROBE_KEY too, an API key of the plan of the LLM proxies
that has their semantic cache, it chats through every LLM proxy of the seed with that key, then asks the same generic
question again: the proxy must answer it from its cache (the id of its answer says so). Set it only for a playground
whose LLM proxies all cache that plan's questions: the seed fails on one that does not.
"""

import copy
import hashlib
import json
import os
import re
import threading
import time

import requests

from . import controls
from . import common
from .common import (Api, HttpError, SeedError, created, excerpt, files, folders, later, load, log, owned, parallel, pause, together,
                    record, recorded, save_created, seed_file, setting, wait_until)

SECTION = "apim"
DEPENDS = ()   # the modules whose work this one reads on the platform: none, so it runs at the start (schedule.py)
ORDER = ["subscription", "application", "api", "group", "user", "membership"]   # what depends on what: removed first
ENVIRONMENTS = "environments"   # the folder of seed/ that holds one folder for each extra environment
ENVIRONMENT = re.compile(r"[a-z0-9-]+")
# What the platform answers when it is asked to register a user of a source it holds no provider for: a memory user is no such
# user, the platform knows it from its configuration
NO_PROVIDER = "identityProvider.notFound"
# What a group file may say under `defaults`, and the field of the group entity of the Console API each is written to
DEFAULTS = {"roles": "roles", "lockApiRole": "lock_api_role", "lockApplicationRole": "lock_application_role",
            "maxInvitation": "max_invitation", "systemInvitation": "system_invitation", "emailInvitation": "email_invitation",
            "disableMembershipNotifications": "disable_membership_notifications", "eventRules": "event_rules"}
GROUP_UPDATE = ("name", "roles", "max_invitation", "lock_api_role", "lock_application_role", "system_invitation",
                "email_invitation", "disable_membership_notifications", "event_rules")   # what the update of a group takes
ENDED = (200, 202, 204, 400, 404)   # _stop and _close: done, or stopped or closed already (400), or gone (404)
IN_ENVIRONMENT = ("subscription", "application", "api", "group", "membership")   # the records of what an environment holds: users are the organization's

NO_API = "No context-path matches"   # what the gateway answers for a path no API serves
LOOK_AGAIN = 1   # seconds between two looks at the gateway
APPEARS = 20   # looks at the list of users, half a second apart, for one that a group has just made
RETRIES = 4   # times an update that got no answer (a connection that fails, a timeout) or a 5xx or a 429 is tried again, before the seed fails
RETRY_PAUSE = 8   # seconds before each: a management API that reloads is back within about half a minute
UNSUPPORTED = (404, 405, 501)   # what a platform answers for an update it does not offer on this API: the only refusal that has it imported again
SYNC_PAUSE = 10   # seconds: two sync periods of the gateway, for a replaced API when there is no gateway to look at
QUESTION = "In one short sentence, what is an API?"   # no digit: a cache condition may leave out a question that has one
CACHED = "resp_llmproxy"   # the id of an answer that comes from the semantic cache
CACHE_TRIES = 3   # an answer may take a moment to be stored


def merge(base, extra):
    """extra written over base, nested objects merged rather than replaced."""
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value
    return base


def covers(live, wanted):
    """Whether `live` holds everything `wanted` says: APIM adds what it defaults."""
    if isinstance(wanted, dict):
        return isinstance(live, dict) and all(covers(live.get(key), value) for key, value in wanted.items())
    return live == wanted


def digest_of(definition):
    """What identifies a definition once its variables are replaced: the plans, flows and resources are part of it."""
    return hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()


def made_by_seed(api_id):
    """The record of an API the seed created, None for any other."""
    return next((x for x in created() if (x["product"], x["kind"], x["id"]) == (SECTION, "api", api_id)), None)


def made_under(name):
    """The record of the API the seed created under this name, None for none: a subscription goes to that one only."""
    return next((x for x in created() if (x["product"], x["kind"], x["name"]) == (SECTION, "api", name)), None)


def code_of(answer):
    """The technical code of a refusal of the platform, None when it says none."""
    try:
        return answer.json().get("technicalCode")
    except (ValueError, AttributeError):
        return None


def made_by_error(error):
    """The id of the API that the platform made although its import failed: its answer carries it as `parameters.apiId`.
    None when the answer says none."""
    try:
        return error.body and json.loads(error.body).get("parameters", {}).get("apiId") or None
    except (TypeError, ValueError, AttributeError):
        return None


def lacking(state, members):
    """The members of a group file that the live group does not have, or has without a role the file gives them: the platform
    adds roles of its own, which does not matter."""
    live = {(m.get("source"), m.get("sourceId")): m.get("roles") or {} for m in state.get("members") or []}
    return [m for m in members
            if (m["source"], m["sourceId"]) not in live or not covers(live[(m["source"], m["sourceId"])], m.get("roles") or {})]


def choose(found, source_id, spec):
    """The reference of the answer of an identity provider that stands for this user: the one that names it (its id, or its first
    and last name), else the only one it gave. None when no one, or more than one, stands for it."""
    names = {source_id, " ".join(part for part in (spec.get("firstname"), spec.get("lastname")) if part)}
    named = [x for x in found if x.get("displayName") in names]
    candidates = named or (found if len(found) == 1 else [])
    return candidates[0].get("reference") if len(candidates) == 1 else None


class ApiManagement:
    """API Management as one environment sees it: the platform's own (APIM_ENV) unless `env` names another."""

    def __init__(self, env=None):
        self.api = Api(setting("APIM_MANAGEMENT_URL"))
        self.org = setting("APIM_ORG", "DEFAULT")
        self.path = setting("APIM_MANAGEMENT_PATH", "/management")   # /apim/rest on the Cloud Gate of Gravitee Cloud
        self.own, self.env = env is None, env or setting("APIM_ENV", "DEFAULT")
        # What the organization holds, the seed's roles and its users, is changed by one environment at a time: made at the same
        # time, users were found without an identity provider or not found after they were made
        self.organization = threading.RLock()
        self.bind()
        self.gateway, self.deadline = None, None   # what watch() sets: the gateway, and the time every wait for it ends

    def bind(self):
        """The addresses of the environment, and what the seed learns about it as it goes."""
        self.org_v1 = f"{self.path}/organizations/{self.org}"
        self.v1 = f"{self.org_v1}/environments/{self.env}"
        self.v2 = f"{self.path}/v2/organizations/{self.org}/environments/{self.env}"
        # The Portal API is served at the root of the management API, whatever the path of the management API itself is
        self.portal = f"{setting('APIM_PORTAL_PATH', '/portal')}/environments/{self.env}"
        # The Automation API is the one of Gamma (`AIM_AUTOMATION_URL`): where a platform serves it from another host
        root = setting("AIM_AUTOMATION_URL", setting("APIM_MANAGEMENT_URL"))
        self.automation = Api(f"{root}/automation/organizations/{self.org}/environments/{self.env}")
        self.automation.session = self.api.session   # one login for every address
        self.known = None   # the APIs of the environment, listed once and kept up to date as this seed makes them (apis())
        self.groups_by_hrid = {}   # hrid -> {"id", "name"} of the groups of this environment, as groups() finds or makes them
        self.waiting = {}   # (source, sourceId) -> the entry of users.yaml of a user the platform has no row for yet

    def at(self, env):
        """The same platform seen from another environment: it shares the login, the gateway and the deadline, so that
        the whole seed signs in once and waits for the gateway within one deadline."""
        other = copy.copy(self)
        other.own, other.env = False, env
        other.bind()
        return other

    def scoped(self, name):
        """What the records and the messages call something of this environment: the plain name in the platform's own
        environment, so that what a seed recorded there before stays valid, and `name [env]` in another."""
        return name if self.own else f"{name} [{self.env}]"

    def scope(self):
        """What a record of this environment says besides its name, so that a removal goes where the record says."""
        return {} if self.own else {"env": self.env}

    def connect(self):
        token = os.getenv("APIM_TOKEN")
        if token:
            self.api.session.headers["Authorization"] = f"Bearer {token}"
        else:
            self.api.session.auth = (setting("APIM_USERNAME"), setting("APIM_PASSWORD"))
        wait_until(SECTION, "API Management", lambda: self.api.get(f"{self.v2}/apis", params={"perPage": 1}))

    def watch(self):
        """Looks at the gateway of GATEWAY_INTERNAL_URL from now on, every wait for it ending at the same deadline. A
        playground that does not set it has no gateway to look at."""
        url = os.getenv("GATEWAY_INTERNAL_URL")
        if url:
            self.gateway, self.deadline = Api(url), time.time() + int(setting("GATEWAY_READY_TIMEOUT", "300"))

    # --- The environment, its users and its groups ----------------------------------------

    def has_environment(self):
        return self.api.get(self.v1, expect=(200, 400, 404)).status_code == 200

    def require_environment(self):
        """No API creates an environment: the playground makes it and the seed only finds it."""
        if not self.has_environment():
            raise SeedError(f"Environment {self.env} does not exist on this platform: create it, then Re-seed")

    def listed(self):
        """Whether the platform lists this environment for the seed's login: the Console lists the environments of those who
        have a role in them, and the data steward who signs in as this login picks a customer there."""
        return any(row.get("id") == self.env for row in self.api.get(f"{self.org_v1}/environments").json())

    def membership(self):
        """The seed's login has a role in the environment, so that the Console lists it. One made in the database has no member:
        the platform lets an administrator of the organization act on it all the same, but shows it to nobody. The login is
        made an administrator of it, which a removal takes back."""
        if self.listed():
            return
        me = self.api.get(f"{self.org_v1}/user").json()
        roles = self.api.get(f"{self.org_v1}/configuration/rolescopes/ENVIRONMENT/roles").json()
        role = next((r for r in roles if r.get("name") == "ADMIN"), None)
        if not role:
            raise SeedError(f"Environment {self.env}: the platform has no administrator role for an environment")
        self.api.put(f"{self.org_v1}/users/{me['id']}/roles",
                     json={"user": me["id"], "referenceType": "ENVIRONMENT", "referenceId": self.env, "roles": [role["id"]]})
        record(SECTION, "membership", "memberships", self.scoped(me.get("sourceId") or me["id"]), me["id"], **self.scope())
        if not self.listed():
            raise SeedError(f"Environment {self.env}: the platform does not list it for the seed's login")
        log(SECTION, f"✓ The seed's login is an administrator of {self.env}")

    def find_user(self, source, source_id):
        """The user of the platform with this source and id, None for none. An archived one (a user that was deleted) is none."""
        page = 1
        while True:
            body = self.api.get(f"{self.org_v1}/users", params={"q": source_id, "page": page, "size": 100}).json()
            for user in body.get("data", []):
                if (user.get("source"), user.get("sourceId")) == (source, source_id) and user.get("status") != "ARCHIVED":
                    return user
            if page >= body.get("page", {}).get("total_pages", 1):
                return None
            page += 1

    def appears(self, source, source_id, looks=APPEARS):
        """The user the platform has just made from a group, which its search may list a moment later; None when it never does."""
        for look in range(looks):
            user = self.find_user(source, source_id)
            if user or look == looks - 1:
                return user
            pause(LOOK_AGAIN / 2)

    def users(self, specs):
        """Registers the users of users.yaml in this environment: the URL of the environment gives a new user its default role
        in that environment only. A user that exists is adopted as it is. A user of a source the platform has no provider
        for to register it with (a memory user: the platform knows it from its configuration and makes its row at its first
        login) waits: groups() gets its row by adding it to a group."""
        for spec in specs:
            with self.organization:
                self.register(spec)

    def register(self, spec):
        source, source_id = spec["source"], spec["sourceId"]
        if self.find_user(source, source_id):
            log(SECTION, f"User exists: {source_id}")
            return
        body = {key: spec[key] for key in ("source", "sourceId", "email", "firstname", "lastname") if key in spec}
        answer = self.api.post(f"{self.v1}/users", json=body, expect=(200, 201, 404))
        if answer.status_code == 404:
            if code_of(answer) != NO_PROVIDER:
                raise HttpError(f"POST {self.v1}/users: HTTP 404 {excerpt(answer.text)}", 404, answer.text)
            self.waiting[(source, source_id)] = spec
            log(SECTION, f"User {source_id}: no row yet, its first login or its first group makes it")
            return
        record(SECTION, "user", "users", self.scoped(source_id), answer.json()["id"], sourceId=source_id, **self.scope())
        log(SECTION, f"✓ User registered: {source_id}")

    def groups(self, specs):
        parallel(self.group, specs)

    def group(self, spec):
        """A group of the Automation API: written when it is absent or lacks what its file says (a member, a role), found again
        by its hrid. What the platform adds (roles of its own, members someone added) is left. A member the platform does not
        know is ignored by the Automation API, so it is looked for after the write: a user with no row yet gets it from its
        identity provider (`enroll`), and any other is an error."""
        hrid, label = spec["hrid"], self.scoped(spec["hrid"])
        members = [{key: value for key, value in m.items() if key != "external"} for m in spec.get("members") or []]
        body = {**{key: value for key, value in spec.items() if key not in ("defaults", "members")}, "members": members}
        live, said = self.automation.get(f"/groups/{hrid}", expect=(200, 404)), []   # said: what the platform says about the write
        state = live.json() if live.status_code == 200 else None
        if state and not recorded(SECTION, "group", state["id"]):
            raise SeedError(f"Group {label}: the hrid is taken by a group the seed did not create")
        if not state or not covers(state, {key: body[key] for key in ("name", "notifyMembers") if key in body}) or lacking(state, members):
            written = self.asked(label, lambda: self.automation.put("/groups", json=body, expect=range(200, 600)), kind="Group").json()
            severe = (written.get("errors") or {}).get("severe")
            if severe:
                raise SeedError(f"Group {label}: {excerpt(severe, 300)}")
            said = (written.get("errors") or {}).get("warning") or []
            record(SECTION, "group", "groups", label, written["id"], hrid=hrid, **self.scope())
            state = self.automation.get(f"/groups/{hrid}").json()
            log(SECTION, f"✓ Group written: {label}")
        else:
            log(SECTION, f"Group exists: {label}")
        missing = lacking(state, members)
        for member in missing:
            if not self.find_user(member["source"], member["sourceId"]):
                self.enroll(state["id"], label, member)
        if missing:
            state = self.automation.get(f"/groups/{hrid}").json()
            for member in lacking(state, members):
                raise SeedError(f"Group {label}: {member['sourceId']} is not a member of it as its file says"
                                + (f": {excerpt('; '.join(said), 300)}" if said else ""))
        self.groups_by_hrid[hrid] = {"id": state["id"], "name": spec["name"]}
        if spec.get("defaults"):
            self.defaults(state["id"], label, spec["defaults"])

    def enroll(self, group_id, label, member):
        """Gets the row of a user that has none, by adding it to the group from the search of its identity provider, as the
        Console adds a member: the answer carries a reference that only the platform reads. The user gets the roles its provider
        gives it, in this environment only."""
        with self.organization:   # another environment may be making users
            self.make_user(group_id, label, member)

    def make_user(self, group_id, label, member):
        source_id = member["sourceId"]
        spec = self.waiting.get((member["source"], source_id)) or {}
        if self.find_user(member["source"], source_id):
            return   # made meanwhile, by a group of another environment
        found = self.api.get(f"{self.org_v1}/search/users", params={"q": source_id}).json()
        reference = choose(found, source_id, spec)
        if not reference:
            raise SeedError(f"Group {label}: {source_id} is not a user of the platform and no identity provider names exactly one "
                            "(a memory user is one of the configuration of the management API)")
        roles = [{"scope": scope, "name": name} for scope, name in (member.get("roles") or {}).items()]
        added = self.api.post(f"{self.v1}/configuration/groups/{group_id}/members", json=[{"reference": reference, "roles": roles}],
                              expect=range(200, 600))
        if not added.ok:
            raise SeedError(f"Group {label}: {source_id} could not be added to it: HTTP {added.status_code} {excerpt(added.text, 300)}")
        user = self.appears(member["source"], source_id)
        if not user:
            raise SeedError(f"Group {label}: {source_id} was added to it but the platform made no user of it")
        record(SECTION, "user", "users", self.scoped(source_id), user["id"], sourceId=source_id, **self.scope())
        log(SECTION, f"✓ User {source_id}: made by its group")

    def defaults(self, group_id, label, wanted):
        """What the Automation API has no field for, the roles a new member of the group gets and the like, with the update of
        the group of the Console API, written only when it differs."""
        fields = {DEFAULTS[key]: value for key, value in wanted.items()}
        if "event_rules" in fields:
            fields["event_rules"] = [{"event": event} for event in fields["event_rules"]]
        entity = self.api.get(f"{self.v1}/configuration/groups/{group_id}").json()
        if covers(entity, fields):
            return
        body = {key: entity[key] for key in GROUP_UPDATE if key in entity}
        self.api.put(f"{self.v1}/configuration/groups/{group_id}", json={**body, **fields})
        log(SECTION, f"✓ Group defaults: {label}")

    # --- Settings -------------------------------------------------------------------------

    def settings(self, extra):
        current = self.api.get(f"{self.v1}/settings").json()
        wanted = merge(copy.deepcopy(current), extra)
        if wanted != current:
            self.api.post(f"{self.v1}/settings", json=wanted)
        log(SECTION, f"✓ Environment settings: {', '.join(extra)}")

    # --- APIs -----------------------------------------------------------------------------

    def apis(self):
        """The APIs of the environment: listed the first time they are asked for, then as this seed leaves them."""
        with common._state:
            if self.known is None:
                self.known = self.all_apis()
            return list(self.known)

    def remember(self, api_id, definition, name, gone=None):
        """What the list of the environment holds after an API was made (and `gone` was dropped)."""
        with common._state:
            if self.known is not None:
                self.known = [a for a in self.known if a.get("id") not in (api_id, gone)]
                self.known.append({"id": api_id, "name": name, "listeners": definition.get("api", definition).get("listeners", [])})

    def all_apis(self):
        out, page = [], 1
        while True:
            body = self.api.get(f"{self.v2}/apis", params={"page": page, "perPage": 100}).json()
            out += body.get("data", [])
            if page >= body.get("pagination", {}).get("pageCount", 1):
                return out
            page += 1

    @staticmethod
    def first_path(api):
        for listener in api.get("listeners", []):
            for p in listener.get("paths", []):
                return p.get("path")
        return None

    @classmethod
    def address(cls, api):
        """Where an API is reached: its first path, or for a native Kafka API, which has none, the host of its Kafka listener."""
        path = cls.first_path(api)
        if path:
            return path
        for listener in api.get("listeners", []):
            if listener.get("type") == "KAFKA" and listener.get("host"):
                return f"kafka://{listener['host']}"
        return None

    def live_api(self, api):
        """The API of APIM that this definition stands for: the one with its id, else the one that holds its first path (its Kafka host, for a native API)."""
        if api.get("id"):
            r = self.api.get(f"{self.v2}/apis/{api['id']}", expect=(200, 404))
            if r.status_code == 200:
                return r.json()
        address = self.address(api)
        return next((a for a in self.apis() if address and self.address(a) == address), None)

    def import_api(self, definition, file_name):
        api = definition.get("api", {})
        plain = api.get("name", file_name)
        name, digest = self.scoped(plain), digest_of(definition)   # the file's: the mode is not part of it
        found = controls.of("api", plain) if self.own else None   # the controls name an API of seed/apim/
        wanted = self.as_mode_says(definition, found)
        groups = self.groups_of(api.get("groups"), f"API {name}")   # the ids, or None when the file names none
        imported = self.named_groups(wanted, groups)
        live = self.live_api(api)
        mine = live and made_by_seed(live["id"])
        if live and not mine:
            held = f"{api['id']} is the id" if live["id"] == api.get("id") else f"{self.address(api)} is the path"
            raise SeedError(f"API {name}: {held} of '{live['name']}', which the seed did not create")
        if not live:
            api_id = self.create(imported, name, digest)
            self.said(name, found)
        elif mine.get("digest") != digest:
            api_id = self.update(live, imported, name, digest)
            self.said(name, found)
        elif self.in_place(live["id"], name, found, deploy=live.get("state") == "STARTED"):
            self.restrict(live["id"], groups, name, known=live.get("groups"))
            if live.get("state") == "STARTED" and live.get("lifecycleState") == "PUBLISHED":
                log(SECTION, f"✓ API up to date: {name}")
            else:
                self.start(live["id"])   # the definition has not changed, but the API does not run: no new import
            return
        else:
            api_id = self.update(live, imported, name, digest)   # someone edited it beyond what a control puts back
            self.said(name, found)
        self.restrict(api_id, groups, name)
        self.start(api_id)

    def groups_of(self, hrids, what):
        """The live ids of the groups an API or an application of an environment names by hrid, in the order it names them.
        None when it names none, or in the platform's own environment, whose files hold the groups as the platform knows them."""
        if self.own or not hrids:
            return None
        missing = [hrid for hrid in hrids if hrid not in self.groups_by_hrid]
        if missing:
            raise SeedError(f"{what}: {', '.join(missing)} is no group of the environment {self.env}")
        return [self.groups_by_hrid[hrid]["id"] for hrid in hrids]

    def named_groups(self, definition, ids):
        """The definition to import: an import knows the groups of an API by name only (it takes an id for a name and creates
        a group of that name), so the hrids of the file become the names of the groups the seed has just written."""
        if not ids:
            return definition
        imported = copy.deepcopy(definition)
        by_id = {group["id"]: group["name"] for group in self.groups_by_hrid.values()}
        imported["api"]["groups"] = [by_id[i] for i in ids]
        return imported

    def restrict(self, api_id, ids, name, known=None):
        """Brings the groups of the API to the ones its file names, with the update of its entity: what an import leaves of
        them is the platform's to say. `known` is the list of groups the caller has just read, when it has."""
        if not ids or (known is not None and set(known) == set(ids)):
            return
        entity = self.entity(api_id, name)
        if set(entity.get("groups") or []) == set(ids):
            return
        if not self.update_entity(api_id, {**entity, "groups": ids}, name):
            raise SeedError(f"API {name}: the platform does not update its groups")
        log(SECTION, f"✓ Groups of {name}: {len(ids)}")

    @staticmethod
    def said(name, found):
        """What an import of the file did to the controls: it wrote them as the mode says."""
        if found:
            log(SECTION, f"  Controls of {name} are {'on' if controls.protected() else 'off'}: {controls.named(found)}")

    @staticmethod
    def as_mode_says(definition, found):
        """The definition with the steps its controls name enabled or not, as the mode says: a copy, for what is imported."""
        if not found:
            return definition
        wanted = copy.deepcopy(definition)
        controls.align(wanted["api"].get("flows"), found)
        return wanted

    def in_place(self, api_id, name, found, deploy):
        """Brings the steps the controls name to the state the mode says in the API as it is now, with the update of its
        entity (an import would replace an LLM proxy, drop its keys and cost the gateway a reload) and a deployment, which
        also puts on the gateway a state someone saved without deploying. False when the API lacks a step a control names,
        or when the platform does not offer the update of the entity (404, 405 or 501, a preview that does not take it): the
        API is imported again. A platform that cannot answer now (no answer, a 5xx, a 429) is asked again a few times, and any
        other refusal stops the seed (see update_entity): replacing an API because its platform was busy, or because the
        seed sent something wrong, would cost it a deletion and the gateway a reload."""
        if not found:
            return True
        live = self.entity(api_id, name)
        try:
            changed = controls.align(live.get("flows"), found)
        except controls.Missing as e:
            log(SECTION, f"! {name}: {e}")
            return False
        state = "on" if controls.protected() else "off"
        if changed:
            if not self.update_entity(api_id, live, name):
                return False
            log(SECTION, f"✓ Controls of {name} put back, {state}: {controls.named(found)}")
        else:
            log(SECTION, f"Controls of {name} are {state}: {controls.named(found)}")
        if deploy and (changed or live.get("deploymentState") != "DEPLOYED"):
            self.asked(name, lambda: self.api.post(f"{self.v2}/apis/{api_id}/deployments", json={"deploymentLabel": "Playground seed"},
                                                   expect=range(200, 600)))
            controls.touched()
            log(SECTION, "  ✓ Deployed")
        return True

    def asked(self, name, ask, passed=(), kind="API"):
        """The response of `ask()`, a call of the management API, asked again after a pause when the platform cannot answer now: a
        connection that fails or times out as much as a 5xx or a 429, RETRIES times, and then the seed stops with what it said. Any other
        refusal (a 400, 401, 403, 409, 422...) stops the seed at once with the platform's message: a request the platform finds wrong
        would be found wrong again. A status in `passed` is handed back to the caller, which says what it means."""
        for attempt in range(RETRIES + 1):
            try:
                response = ask()
            except requests.RequestException as e:
                short = said = f"no answer from the management API: {type(e).__name__}"   # its address is no help to a reader
            else:
                if response.ok or response.status_code in passed:
                    return response
                short, said = f"HTTP {response.status_code}", f"HTTP {response.status_code} {excerpt(response.text, 300)}"
                if response.status_code < 500 and response.status_code != 429:
                    raise SeedError(f"{kind} {name}: {said}")
            if attempt == RETRIES:
                raise SeedError(f"{kind} {name}: {said} ({RETRIES + 1} tries)")
            log(SECTION, f"! {name}: {short}, trying again")
            pause(RETRY_PAUSE)

    def entity(self, api_id, name):
        """The entity of an API as APIM holds it now. Read with the same patience as its update: a management API that reloads between two
        APIs of a seed would otherwise end it with a connection error, or with the replacement of an API that was only busy."""
        return self.asked(name, lambda: self.api.get(f"{self.v2}/apis/{api_id}", expect=range(200, 600))).json()

    def update_entity(self, api_id, live, name):
        """The update of an API from the entity APIM gave. True when it is done. False only when the platform does not offer
        it (UNSUPPORTED: 404, 405 or 501): the caller imports the API again. A platform that cannot answer now is asked again
        (see `asked`): replacing an API because its platform was busy, or because the seed sent something wrong, would cost it a
        deletion and the gateway a reload."""
        update = self.asked(name, lambda: self.api.put(f"{self.v2}/apis/{api_id}", json=live, expect=range(200, 600)), UNSUPPORTED)
        if update.ok:
            return True
        log(SECTION, f"! {name}: HTTP {update.status_code} {excerpt(update.text, 300)}: the platform does not update it in place")
        return False

    def imported(self, definition, name):
        """The id of the API the platform makes from a definition. An import can fail after the platform made the API, which it
        then keeps (STOPPED, with the error in its message): it is recorded, with no digest, so that the seed finds it as its own
        at the next run and updates it, where it would stop on an API it did not create."""
        try:
            return self.api.post(f"{self.v2}/apis/_import/definition", json=definition).json()["id"]
        except HttpError as e:
            left = made_by_error(e)
            if left:
                record(SECTION, "api", "APIs", name, left, **self.scope())
                log(SECTION, f"! {name}: the platform made the API but not all of it: the next run updates it")
            raise

    def create(self, definition, name, digest):
        api_id = self.imported(definition, name)
        record(SECTION, "api", "APIs", name, api_id, digest=digest, **self.scope())
        self.remember(api_id, definition, name)
        log(SECTION, f"✓ API imported: {name}")
        return api_id

    def update(self, live, definition, name, digest):
        api_id = live["id"]
        self.keep_plans(api_id, definition)
        r = self.api.put(f"{self.v2}/apis/{api_id}/_import/definition", json=definition, expect=range(200, 600))
        if r.ok:
            log(SECTION, f"✓ API updated: {name}")
        elif r.status_code in (404, 405, 500) and "conflict" not in r.text.lower():
            # Some API types cannot be updated from a definition (LLM and A2A proxies): replace them
            self.api.post(f"{self.v2}/apis/{api_id}/_stop", expect=ENDED)
            self.api.delete(f"{self.v2}/apis/{api_id}", params={"closePlans": "true"})
            self.until_dropped(name, self.first_path(live))
            api_id = self.imported(definition, name)
            log(SECTION, f"✓ API replaced: {name}")
        else:
            raise SeedError(f"API {name}: HTTP {r.status_code} {excerpt(r.text, 300)}")
        # Still the seed's, under its new id and digest. The record it had under the id this API had goes: it may hold another
        # name (the file renamed the API) or another id (the API was replaced), and the first record of an id is the one that is read
        with common._state:   # read, changed and written back by this step alone
            save_created([x for x in created() if (x["product"], x["kind"], x["id"]) != (SECTION, "api", live["id"])])
            record(SECTION, "api", "APIs", name, api_id, digest=digest, **self.scope())
        self.remember(api_id, definition, name, gone=live["id"])
        return api_id

    def until_dropped(self, name, path):
        """The gateway keeps serving a deleted API until it syncs. One imported again under the same id before then is
        deployed as the same revision, which the gateway takes for the one it has: it serves the old definition for good."""
        if not (self.gateway and path):
            pause(SYNC_PAUSE)   # nothing to look at
            return
        log(SECTION, f"Waiting for the gateway to drop {name}")
        while serves(self.gateway, path):
            if time.time() >= self.deadline:
                raise SeedError(f"API {name}: the gateway still serves it after its deletion")
            pause(LOOK_AGAIN)

    def start(self, api_id):
        """Publishes, deploys and starts the API."""
        current = self.api.get(f"{self.v2}/apis/{api_id}").json()
        if current.get("lifecycleState") != "PUBLISHED":
            self.api.put(f"{self.v2}/apis/{api_id}", json={**current, "lifecycleState": "PUBLISHED"})
        self.api.post(f"{self.v2}/apis/{api_id}/deployments", json={"deploymentLabel": "Playground seed"})
        if current.get("state") != "STARTED":
            self.api.post(f"{self.v2}/apis/{api_id}/_start", expect=(200, 202, 204, 400))
        log(SECTION, "  ✓ Published, deployed, started")

    def keep_plans(self, api_id, definition):
        """Exports carry no plan ids: give each plan of the file the id of the live plan with its name, so an update
        changes plans instead of replacing them (which their subscriptions forbid). With duplicate names, the plan
        that has subscriptions wins. A plan that has its id in the file keeps it."""
        live = self.api.get(f"{self.v2}/apis/{api_id}/plans", params={"perPage": 100}).json().get("data", [])
        def subscribed(plan):
            return bool(self.api.get(f"{self.v2}/apis/{api_id}/subscriptions", params={"planIds": plan["id"], "perPage": 1}).json().get("data"))
        for plan in definition.get("plans", []):
            same = [p for p in live if p.get("name") == plan.get("name")]
            if same and not plan.get("id"):
                plan["id"] = next((p for p in same if subscribed(p)), same[0])["id"]

    # --- Applications ---------------------------------------------------------------------

    def find_application(self, name):
        return next((a for a in self.api.get(f"{self.v1}/applications", params={"query": name}).json()
                     if a.get("name") == name), None)

    def application(self, spec):
        name = spec["name"]
        label = self.scoped(name)
        payload = {"name": name, "description": spec.get("description", ""),
                   "settings": spec.get("settings", {"app": {"type": "SIMPLE"}})}
        groups = self.groups_of(spec.get("groups"), f"Application {label}")
        if groups is not None:
            payload["groups"] = groups
        found = self.find_application(name)
        if not found:
            app = self.api.post(f"{self.v1}/applications", json=payload).json()
            record(SECTION, "application", "applications", label, app["id"], **self.scope())
            log(SECTION, f"✓ Application created: {label}")
        elif not recorded(SECTION, "application", found["id"]):
            raise SeedError(f"Application {label}: the name is taken by an application the seed did not create")
        elif (covers({"description": found.get("description") or "", "settings": found.get("settings")},
                     {"description": payload["description"], "settings": payload["settings"]})
              and (groups is None or set(found.get("groups") or []) == set(groups))):
            log(SECTION, f"Application exists: {label}")
        else:
            self.api.put(f"{self.v1}/applications/{found['id']}", json=payload)
            log(SECTION, f"✓ Application updated: {label}")

    # --- Subscriptions --------------------------------------------------------------------

    def subscription(self, spec):
        label = self.scoped(f"{spec['application']} → {spec['api']} ({spec['plan']})")
        key, comment, pending = spec.get("apiKey"), spec.get("comment"), spec.get("pending")
        if pending and key:
            raise SeedError(f"Subscription {label}: a subscription that stays pending takes no apiKey, the one who accepts it chooses")
        app, api = self.find_application(spec["application"]), made_under(self.scoped(spec["api"]))
        if not app:
            raise SeedError(f"Subscription {label}: application not found")
        if not api:
            raise SeedError(f"Subscription {label}: the seed made no API with this name")
        plan = next((p for p in self.api.get(f"{self.v2}/apis/{api['id']}/plans", params={"perPage": 100}).json().get("data", [])
                     if p.get("name") == spec["plan"]), None)
        if not plan:
            raise SeedError(f"Subscription {label}: plan not found")
        # The statuses are one comma-separated value: repeated, the platform reads the first only and misses a pending one
        existing = self.api.get(f"{self.v2}/apis/{api['id']}/subscriptions", params={
            "applicationIds": app["id"], "planIds": plan["id"], "statuses": "ACCEPTED,PENDING,PAUSED"}).json()
        if existing.get("data"):
            if self.callback(existing["data"][0], api["id"], spec, label):
                log(SECTION, f"✓ Subscription updated: {label}")
            else:
                log(SECTION, f"Subscription exists: {label}")
            return
        by_itself = plan.get("validation") == "AUTO"   # a plan that accepts what is asked for as soon as it is asked
        if pending and by_itself:
            raise SeedError(f"Subscription {label}: the plan accepts a subscription by itself, so it cannot stay pending")
        configuration = spec.get("consumerConfiguration")
        if pending or comment:
            if key and by_itself:
                raise SeedError(f"Subscription {label}: a comment goes with a request, which this plan accepts at once with a key of its own")
            body = {"application": app["id"], "plan": plan["id"]}
            if comment:
                body["request"] = comment
            if configuration:
                body["configuration"] = configuration
            sub = self.api.post(f"{self.portal}/subscriptions", json=body).json()
        else:
            body = {"applicationId": app["id"], "planId": plan["id"]}
            if key:
                body["customApiKey"] = key
            if configuration:
                body["consumerConfiguration"] = configuration
            sub = self.api.post(f"{self.v2}/apis/{api['id']}/subscriptions", json=body).json()
        record(SECTION, "subscription", "subscriptions", label, sub["id"], api=api["id"], **self.scope())
        if sub.get("status") == "PENDING" and not pending:
            self.api.post(f"{self.v2}/apis/{api['id']}/subscriptions/{sub['id']}/_accept", json={"customApiKey": key} if key else {})
        log(SECTION, f"✓ Subscription: {label}" + (" (pending)" if pending else ""))

    def callback(self, found, api_id, spec, label):
        """The callback of a push subscription that is not what its file says: the update of the subscription, which the Console
        does too, when the seed made it. True when it was updated."""
        wanted = spec.get("consumerConfiguration")
        if not wanted:
            return False
        live = self.api.get(f"{self.v2}/apis/{api_id}/subscriptions/{found['id']}").json()
        if covers(live.get("consumerConfiguration"), wanted):
            return False
        if not recorded(SECTION, "subscription", found["id"]):
            log(SECTION, f"! Subscription {label} is not one the seed made: its callback is left as it is")
            return False
        # The update replaces the dates of the subscription with what it is given: it gives back the ones it has
        body = {key: live[key] for key in ("metadata", "startingAt", "endingAt") if live.get(key) is not None}
        self.api.put(f"{self.v2}/apis/{api_id}/subscriptions/{found['id']}", json={**body, "consumerConfiguration": wanted})
        return True


# --- The gateway ----------------------------------------------------------------------------
# The gateway serves an API some seconds after its deployment, and the first time an LLM proxy is deployed it downloads
# its AI models: the seed waits, so that what starts after it finds the APIs answering.

def serves(gateway, path):
    """Whether an API answers on this path: the gateway's own 404 says none does."""
    try:
        r = gateway.send("GET", path, timeout=10)
    except requests.RequestException:
        return False
    return not (r.status_code == 404 and NO_API in r.text)


def first_model(api):
    for group in api.get("endpointGroups", []):
        for endpoint in group.get("endpoints", []):
            for model in (endpoint.get("configuration") or {}).get("models", []):
                return (model.get("aliases") or [model["name"]])[0]


def ask(gateway, api, key):
    """One chat completion on the first model of an LLM proxy; None when nothing answers."""
    path = ApiManagement.first_path(api).rstrip("/") + "/chat/completions"
    body = {"model": first_model(api), "messages": [{"role": "user", "content": QUESTION}]}
    try:
        return gateway.send("POST", path, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=60)
    except requests.RequestException:
        return None


def from_cache(answer):
    try:
        return answer.status_code == 200 and answer.json().get("id") == CACHED
    except ValueError:
        return False


def wait_for_api(gateway, api, deadline):
    name, path = api.get("name"), ApiManagement.first_path(api)
    if not path:
        return   # an API with no path is not one a client reaches over HTTP
    log(SECTION, f"Waiting for {name} on the gateway")
    while not serves(gateway, path):
        if time.time() >= deadline:
            models = any(r.get("type", "").startswith("ai-model") for r in api.get("resources", []))
            raise SeedError(f"{name} does not answer on the gateway"
                            + (": its AI models are downloaded from huggingface.co at the first deployment" if models else ""))
        pause(LOOK_AGAIN)
    log(SECTION, f"✓ {name} answers on the gateway")


def chat_through(gateway, api, key, deadline):
    """A real answer from the model with this key, then the same generic question again, which Redis must answer."""
    name, answer = api.get("name"), ask(gateway, api, key)
    while answer is None or answer.status_code != 200:
        if time.time() >= deadline:
            raise SeedError(f"{name} does not answer a chat with LLM_PROBE_KEY: "
                            + ("no answer" if answer is None else f"HTTP {answer.status_code} {excerpt(answer.text, 200)}"))
        pause(LOOK_AGAIN)
        answer = ask(gateway, api, key)
    log(SECTION, f"✓ {name} answers a chat")
    for _ in range(CACHE_TRIES):
        again = ask(gateway, api, key)
        if again is not None and from_cache(again):
            log(SECTION, f"✓ {name} answers a question asked twice from its cache")
            return
        pause(LOOK_AGAIN)
    raise SeedError(f"{name} did not answer a question asked twice from its cache: check the vector store of its "
                    "semantic cache (the Redis address and password the seed files give it)")


def wait_for_gateway(apim, definitions):
    """Waits for the gateway to serve every API, then, when LLM_PROBE_KEY is set, for that key to chat, all by the deadline
    that started when the seed began to look at the gateway. A playground that does not set GATEWAY_INTERNAL_URL does
    not wait."""
    if not apim.gateway:
        return
    key = os.getenv("LLM_PROBE_KEY")
    apis = [definition.get("api", {}) for definition in definitions]
    for api in apis:
        wait_for_api(apim.gateway, api, apim.deadline)
    for api in apis:
        if key and api.get("type") == "LLM_PROXY":
            chat_through(apim.gateway, api, key, apim.deadline)


def read_environment(folder):
    """What seed/environments/<folder>/ says, with its variables replaced and the files checked against one another: every
    refusal that the files alone can give is given here, before the platform has been asked for anything."""
    where = f"seed/{ENVIRONMENTS}/{folder}"
    if not ENVIRONMENT.fullmatch(folder):
        raise SeedError(f"{where}: an environment is named with lowercase letters, digits and dashes")
    if folder == setting("APIM_ENV", "DEFAULT").lower():
        raise SeedError(f"{where}: this is the environment of the platform (APIM_ENV), whose files are in seed/apim")
    parts = (ENVIRONMENTS, folder)
    path = seed_file(*parts, "environment.yaml")
    if not path:
        raise SeedError(f"{where}: environment.yaml is missing")
    identity = load(path, provided={"ENV_ID": folder})
    if identity.get("id") != folder:
        raise SeedError(f"{where}/environment.yaml: id is {identity.get('id')!r}, the folder's name is {folder!r}")
    if not identity.get("name"):
        raise SeedError(f"{where}/environment.yaml: name is missing")
    provided = {"ENV_ID": folder, "ENV_NAME": identity["name"]}
    settings, users = seed_file(*parts, "settings.yaml"), seed_file(*parts, "users.yaml")
    wanted = {"id": folder, "name": identity["name"], "settings": load(settings, provided) if settings else None,
              "users": (load(users, provided).get("users") or []) if users else [],
              "groups": [load(f, provided) for f in files(*parts, "groups")],
              "apis": [(load(f, provided), f.name) for f in files(*parts, "apis", suffixes=(".json",))],
              "applications": [load(f, provided) for f in files(*parts, "applications")],
              "subscriptions": [spec for f in files(*parts, "subscriptions") for spec in load(f, provided).get("subscriptions") or []]}
    check_environment(where, wanted)
    return wanted


def check_environment(where, wanted):
    hrids = set()
    for spec in wanted["users"]:
        if not (spec.get("source") and spec.get("sourceId")):
            raise SeedError(f"{where}/users.yaml: a user needs a source and a sourceId")
    for spec in wanted["groups"]:
        hrid = spec.get("hrid")
        if not (hrid and spec.get("name")):
            raise SeedError(f"{where}/groups: a group needs a hrid and a name")
        if hrid in hrids:
            raise SeedError(f"{where}/groups: two groups have the hrid {hrid}")
        hrids.add(hrid)
        for member in spec.get("members") or []:
            if not (member.get("source") and member.get("sourceId")):
                raise SeedError(f"{where}/groups/{hrid}: a member needs a source and a sourceId")
        unknown = set(spec.get("defaults") or {}) - set(DEFAULTS)
        if unknown:
            raise SeedError(f"{where}/groups/{hrid}: defaults has {', '.join(sorted(unknown))}, which the platform has no field for")
    names = set()
    for definition, file_name in wanted["apis"]:
        api = definition.get("api") or {}
        name = api.get("name", file_name)
        if name in names:
            raise SeedError(f"{where}/apis: two APIs are named {name}")
        names.add(name)
        unknown = set(api.get("groups") or []) - hrids
        if unknown:
            raise SeedError(f"{where}/apis/{file_name}: {', '.join(sorted(unknown))} is no group of the environment")
    applications = set()
    for spec in wanted["applications"]:
        if spec.get("name") in applications:
            raise SeedError(f"{where}/applications: two applications are named {spec['name']}")
        applications.add(spec.get("name"))
        unknown = set(spec.get("groups") or []) - hrids
        if unknown:
            raise SeedError(f"{where}/applications: {spec.get('name')} names {', '.join(sorted(unknown))}, which is no group of the environment")
    for spec in wanted["subscriptions"]:
        if not (spec.get("application") and spec.get("api") and spec.get("plan")):
            raise SeedError(f"{where}/subscriptions: a subscription needs an application, an api and a plan")
        if spec.get("pending") and spec.get("apiKey"):
            raise SeedError(f"{where}/subscriptions: {spec['application']} → {spec['api']} stays pending, so it takes no apiKey")
        # An unquoted ${NAME} whose value YAML reads as a number or a boolean would send the key changed: quote it in the file
        token = (((spec.get("consumerConfiguration") or {}).get("entrypointConfiguration") or {}).get("auth") or {}).get("token")
        for what, value in (("apiKey", spec.get("apiKey")), ("token", token.get("value") if isinstance(token, dict) else None)):
            if value is not None and not isinstance(value, str):
                raise SeedError(f"{where}/subscriptions: the {what} of {spec['application']} → {spec['api']} is read as {type(value).__name__}, "
                                "not as text: quote the variable in the file, '${NAME}'")
        if spec.get("consumerConfiguration") and not spec["consumerConfiguration"].get("entrypointId"):
            raise SeedError(f"{where}/subscriptions: the consumerConfiguration of {spec['application']} → {spec['api']} needs an entrypointId")


def seed_own(apim, settings, apis, applications, subscriptions, definitions):
    """The platform's own environment, from seed/apim/. The APIs and the applications do not depend on one another, so they are
    made at the same time (SEED_WORKERS), and the subscriptions, which name both, after them."""
    if settings:
        apim.settings(load(settings))
    loaded = [(load(path), path.name) for path in apis]
    definitions.extend(definition for definition, _ in loaded)
    together([lambda each=each: apim.import_api(*each) for each in loaded]
             + [lambda path=path: apim.application(load(path)) for path in applications])
    parallel(apim.subscription, [spec for path in subscriptions for spec in load(path).get("subscriptions", [])])
    common.milestone(f"apim:{apim.env}")   # what waits for this environment (the portal) goes on


def seed_environment(apim, wanted, definitions):
    """One extra environment, from seed/environments/<id>/, which exists: the seed's login must act in it, then its users,
    settings and groups, which the APIs and the applications name, then the APIs and the applications, then the subscriptions."""
    log(SECTION, f"Environment {wanted['name']} ({apim.env})")
    with apim.organization:   # the seed's login is one for the whole organization
        apim.membership()
    apim.users(wanted["users"])
    if wanted["settings"]:
        apim.settings(wanted["settings"])
    apim.groups(wanted["groups"])
    imports = []
    for definition, file_name in wanted["apis"]:
        # What is said while the gateway is waited for names the environment of the API
        api = definition.get("api", {})
        definitions.append({**definition, "api": {**api, "name": apim.scoped(api.get("name", file_name))}})
        imports.append(lambda each=(definition, file_name): apim.import_api(*each))
    together(imports + [lambda spec=spec: apim.application(spec) for spec in wanted["applications"]])
    parallel(apim.subscription, wanted["subscriptions"])
    common.milestone(f"apim:{apim.env}")


def seed():
    settings = seed_file("apim", "settings.yaml")
    apis = files("apim", "apis", suffixes=(".json",))
    applications, subscriptions = files("apim", "applications"), files("apim", "subscriptions")
    environments = [read_environment(folder) for folder in folders(ENVIRONMENTS)]   # nothing is changed before they are all read
    if not (settings or apis or applications or subscriptions or environments):
        return
    apim = ApiManagement()
    apim.connect()
    apim.watch()
    scopes = [apim.at(wanted["id"]) for wanted in environments]
    for scope in scopes:
        scope.require_environment()   # all of them, before anything is changed in any
    definitions = []
    # The environments do not depend on one another either: each is one step, and the platform's own is another
    together([lambda: seed_own(apim, settings, apis, applications, subscriptions, definitions)]
             + [lambda each=each: seed_environment(*each, definitions) for each in zip(scopes, environments)])
    if common.waits_for_gateway():
        later("the gateway", lambda: wait_for_gateway(apim, definitions))


def remove(records):
    """Deletes what the seed made. Returns the records of what the platform refused to delete: a user that owns an API or an
    application of its own cannot be deleted, and that stops neither the removal of the rest nor the record of that one."""
    mine = owned(records, SECTION, ORDER)
    if not mine:
        return []
    platform = ApiManagement()
    platform.connect()
    present = {}   # environment -> whether the platform still has it
    kept = []

    def delete(x):
        """Deletes one record; True when the platform kept it."""
        apim = platform.at(x["env"]) if x.get("env") else platform   # where the record says it was made
        if x.get("env") and x["kind"] in IN_ENVIRONMENT:
            if x["env"] not in present:
                present[x["env"]] = apim.has_environment()
            if not present[x["env"]]:   # an environment that is gone has nothing left in it to delete
                log(SECTION, f"✓ Deleted {x['kind']} '{x['name']}': its environment is gone")
                return False
        if x["kind"] == "subscription":
            apim.api.post(f"{apim.v2}/apis/{x['api']}/subscriptions/{x['id']}/_close", expect=ENDED)
        elif x["kind"] == "application":
            apim.api.delete(f"{apim.v1}/applications/{x['id']}")
        elif x["kind"] == "group":
            apim.api.delete(f"{apim.v1}/configuration/groups/{x['id']}")
        elif x["kind"] == "user":
            try:
                apim.api.delete(f"{apim.org_v1}/users/{x['id']}")
            except HttpError as e:
                log(SECTION, f"! User '{x['name']}' kept: HTTP {e.status} {excerpt(e.body, 200)}")
                return True
        elif x["kind"] == "membership":
            apim.api.put(f"{apim.org_v1}/users/{x['id']}/roles",
                         json={"user": x["id"], "referenceType": "ENVIRONMENT", "referenceId": apim.env, "roles": []})
        else:
            apim.api.post(f"{apim.v2}/apis/{x['id']}/_stop", expect=ENDED)
            apim.api.delete(f"{apim.v2}/apis/{x['id']}", params={"closePlans": "true"})
        log(SECTION, f"✓ Deleted {x['kind']} '{x['name']}'")
        return False

    for kind in ORDER:   # what depends on what is removed first, kind by kind; the records of one kind do not depend on one another
        batch = [x for x in mine if x["kind"] == kind]
        # The users and the seed's roles are the organization's: one at a time, as they were made
        refused = parallel(delete, batch) if kind not in ("user", "membership") else [delete(x) for x in batch]
        kept += [x for x, was_kept in zip(batch, refused) if was_kept]
    return kept
