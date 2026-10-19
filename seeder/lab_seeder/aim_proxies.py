"""MCP proxies and MCP studios of Agent Management.

    seed/aim/mcp-proxies/*.yaml   an MCP server of the catalog behind the gateway, with its sign-in, plan and flows
    seed/aim/studios/*.yaml       an MCP studio: tools of the catalog, from MCP servers and REST APIs, under one endpoint

An MCP proxy is set up in three steps, each writing only what differs from its file:

    1. The Automation API creates it, by its hrid, with its sign-in, plan and flows. The steps the controls of
       seed/controls.yaml name (controls.py) are in it enabled or disabled as the mode says, and put back so on every run.
    2. What that API cannot carry is set through APIM and Gamma: the plan's `extractPayload`, the response template of a
       refusal, the read timeout of the endpoint, the tracing and the protocol versions. One deployment follows when
       anything changed.
    3. The gateway must serve it: each person is offered the tools their role allows, which the mode decides: the tools of
       a role are unrestricted while their control is off.

A studio is only seeded when a file of seed/aim/studios says so: it needs the tools of the catalog (MCP servers and APIs
imported before it) and a gateway whose MCP libraries can serve a studio. Its file has one of two shapes, told apart by
its keys. A file with `plan`, `supportedProtocolVersions` and `fga` is created with its plan and has its authorization
check switched. A file with `access` is built in steps, each writing only what differs:

    1. It is created with the registration plan (the platform registers the studio in Access Management with it), the
       protocol version, the tools with their aliases and the key each API expects from the studio.
    2. The plan of the agents (JWT) is published, then the registration plan is closed: it lets in any token and
       attributes the call to no one, while the plan of the agents keeps the subscription required.
    3. The authorization check is switched on, then its flow is given the arguments of the call and the attributes of the
       person; human approval is switched on and given its hold window.
    4. One deployment follows when anything changed, and the gateway must serve the studio and refuse a caller without a token.
"""

import hashlib
import json
import os
import re
from urllib.parse import urlsplit

import requests

from . import controls
from . import gamma
from .aim_workspaces import subset
from .apim import merge
from .common import (Api, SeedError, created, files, has_seed, load, log, owned, record, recorded, setting, wait_until,
                    waits_for_gateway)

SECTION = "aim-proxies"
DEPENDS = ("aim_catalog", "am_link")   # the modules whose work this one reads on the platform (schedule.py)
# The folders of the seed it owns
FOLDERS = ("aim/mcp-proxies", "aim/studios")
# The Gamma modules it fills: seed.py checks that the platform serves them before it changes anything
GAMMA = ("aim",)
# What it records, dependents first
ORDER = ["mcp-proxy", "studio"]
# What APIM gives back that it does not take: an API and a plan are written from what they were read as, without these
API_READ_ONLY = ("id", "deployedAt", "createdAt", "updatedAt", "state", "lifecycleState", "deploymentState", "_links",
                 "primaryOwner", "originContext", "definitionContext", "plans", "pages")
PLAN_READ_ONLY = ("id", "apiId", "createdAt", "updatedAt", "publishedAt", "status", "order", "closedAt")
DEPLOYMENT = {"deploymentLabel": "Playground seed"}
# What a studio's API gives back that a v2 update does not take. Not `visibility` nor `lifecycleState`: an update without
# them puts the API back to PRIVATE and a stopped state
STUDIO_API_READ_ONLY = ("id", "deploymentState", "createdAt", "updatedAt", "state", "definitionContext", "links", "_links",
                        "primaryOwner", "originContext", "apiPicture", "apiBackground")
# A v4 plan is updated with what it was read as, keeping `definitionVersion` and `mode`, without these
V4_PLAN_READ_ONLY = ("id", "apiId", "createdAt", "updatedAt", "publishedAt", "closedAt", "status", "type", "referenceId",
                     "referenceType", "links", "_links")
# The flows the platform writes when the switches of a studio are turned on, and the policies a flow edit looks for
FGA_FLOW, APPROVAL_FLOW = "FGA Authorization", "Human approval"
PEP, ASSIGN, APPROVAL = "authz-pep", "policy-assign-attributes", "human-approval"
KINDS = {"mcp-proxy": "MCP proxy", "studio": "MCP studio"}


def fill(value, slug):
    """A file's `<slug>` replaced by the slug of the MCP server in the catalog, wherever it stands."""
    if isinstance(value, str):
        return value.replace("<slug>", slug)
    if isinstance(value, list):
        return [fill(v, slug) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, slug) for k, v in value.items()}
    return value


def public(spec):
    """The spec without what the Automation API never gives back: the secret of an identity provider."""
    if "identityProviders" not in spec:
        return spec
    return {**spec, "identityProviders": [{k: v for k, v in p.items() if k != "clientSecret"} for p in spec["identityProviders"]]}


def server_name(server):
    """The name an MCP server gave itself when it was discovered."""
    return ((server.get("definition") or {}).get("serverInfo") or {}).get("name")


def slugify(text):
    """How the catalog writes a name into an entity id."""
    return re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-")


def jsonrpc(answer):
    """The message of a JSON-RPC answer, plain or as the data line of an event stream."""
    if "event-stream" in answer.headers.get("Content-Type", ""):
        return json.loads(next(line[5:] for line in answer.text.splitlines() if line.startswith("data:")))
    return answer.json()


def split(url):
    """An address as the origin a client is made for and the path it calls."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}", parts.path


def one_line(text):
    """A message on one line: the Lab UI shows the last line the seed printed as the reason it stopped, and the
    body of an error answer spreads over many."""
    return " ".join(str(text).split())


def digest(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def pairs(value):
    """A list of {name, value} as the policies take it. A file writes it so, or as a mapping, name: value."""
    if value is None:
        return None
    if isinstance(value, dict):
        return [{"name": name, "value": text} for name, text in value.items()]
    return [{"name": entry["name"], "value": entry["value"]} for entry in value]


def flow_of(api, name, studio):
    """A flow of the API by its name: the platform writes it when the switch of the studio is turned on."""
    flow = next((f for f in api.get("flows") or [] if f.get("name") == name), None)
    if flow is None:
        raise SeedError(f"MCP studio {studio}: the API has no flow '{name}' although its switch is on")
    return flow


def position(steps, step):
    return next(i for i, s in enumerate(steps) if s is step)


def authorization_flow(api, authorization, studio):
    """The 'FGA Authorization' flow made to read the call: an assign-attributes step before the check puts the call's
    arguments where the check can read them, and the check is given the attributes of the person and the context it
    judges. Written over the flow as the switch made it, step by step, so that what the file does not say is kept.
    Whether anything changed."""
    steps = flow_of(api, FGA_FLOW, studio).setdefault("request", [])
    check = next((s for s in steps if s.get("policy") == PEP), None)
    if check is None:
        raise SeedError(f"MCP studio {studio}: the flow '{FGA_FLOW}' has no {PEP} step")
    changed = False
    attributes = pairs(authorization.get("attributes"))
    if attributes:
        wanted = {"scope": "REQUEST", "attributes": attributes}
        step = next((s for s in steps if s.get("policy") == ASSIGN), None)
        if step is None:
            step = {"name": "Call arguments", "description": "Makes the arguments of the call readable by the authorization check",
                    "enabled": True, "policy": ASSIGN, "configuration": wanted}
            steps.insert(position(steps, check), step)
            changed = True
        else:
            if not subset(wanted, step.get("configuration")) or not step.get("enabled", True):
                step["configuration"] = {**(step.get("configuration") or {}), **wanted}
                step["enabled"] = True
                changed = True
            if position(steps, step) > position(steps, check):
                steps.remove(step)
                steps.insert(position(steps, check), step)
                changed = True
    configuration = check.setdefault("configuration", {})
    for key, entries in (("subjectPropertiesExpr", pairs(authorization.get("subject"))),
                         ("contextExpr", pairs(authorization.get("context")))):
        if entries is not None and configuration.get(key) != entries:
            configuration[key] = entries
            changed = True
    return changed


def approval_flow(api, window, studio):
    """The hold window of the 'Human approval' flow, on the step of each phase: how long the gateway holds a call for
    a person. Whether anything changed."""
    flow, changed, found = flow_of(api, APPROVAL_FLOW, studio), False, False
    for phase in ("request", "response"):
        for step in flow.get(phase) or []:
            if step.get("policy") != APPROVAL:
                continue
            found = True
            configuration = step.setdefault("configuration", {})
            if configuration.get("holdWindowMs") != window:
                configuration["holdWindowMs"] = window
                changed = True
    if not found:
        raise SeedError(f"MCP studio {studio}: the flow '{APPROVAL_FLOW}' has no {APPROVAL} step")
    return changed


def authorize_before_approving(api):
    """A call is judged before it waits for a person, or a call that is going to be denied holds a person up: the
    authorization flow goes first, whichever the switches were turned on in. Whether anything changed."""
    flows = api.get("flows") or []
    names = [f.get("name") for f in flows]
    if FGA_FLOW not in names or APPROVAL_FLOW not in names or names.index(FGA_FLOW) < names.index(APPROVAL_FLOW):
        return False
    flows.insert(names.index(APPROVAL_FLOW), flows.pop(names.index(FGA_FLOW)))
    return True


def jwt_security(agents):
    """How the plan of the agents checks a token: signed by Access Management (its keys are fetched from the address
    the gateway reaches it at), the claims kept for the checks that follow, and the client id read from the token so that
    the call is attributed to the application that holds a subscription."""
    return {"type": "JWT", "configuration": {
        "signature": "RSA_RS256", "publicKeyResolver": "JWKS_URL", "resolverParameter": agents["jwksUrl"],
        "connectTimeout": 2000, "requestTimeout": 2000, "followRedirects": False, "useSystemProxy": False,
        "extractClaims": True, "propagateAuthHeader": True, "userClaim": "sub", "clientIdClaim": agents["clientIdClaim"],
        "checkRequiredScopes": False, "modeStrict": True}}


def needs(spec, studio, where, *keys):
    """A key of the file the studio cannot be made without, named by where it should stand."""
    for key in keys:
        if key not in spec:
            raise SeedError(f"MCP studio {studio}: {where}.{key} is missing")


def recorded_digest(studio_id):
    return next((x.get("digest") for x in created() if (x["product"], x["kind"], x["id"]) == (SECTION, "studio", studio_id)), None)


class McpProxies:
    def __init__(self):
        self.g = gamma.Gamma()
        self.slugs = {}
        self.sources = {}

    def connect(self):
        self.g.connect(SECTION, lambda: self.g.aim.get("/mcp-proxies", params={"perPage": 1}), "MCP proxies")

    def server_slug(self, name):
        """The slug the catalog gave an MCP server, from its name: it prefixes the tools in the access rules, and what
        the catalog derives it from is its own business."""
        if name not in self.slugs:
            servers = self.g.aim.rows("/catalog/mcp-servers", gamma.CATALOG)
            found = next((s for s in servers if server_name(s) == name), None)
            if found is None:
                raise SeedError(f"MCP server {name} is not in the catalog: seed/aim/mcp-servers imports it before the proxy")
            self.slugs[name] = found["slug"]
        return self.slugs[name]

    # --- An MCP proxy -------------------------------------------------------------------------

    def proxy(self, spec):
        automation = fill(spec["automation"], self.server_slug(spec["server"]))
        hrid, name = automation["hrid"], automation["name"]
        found_controls = controls.of("mcp-proxy", name)
        controls.align(automation.get("flows"), found_controls)   # written in both modes: a step someone switched is put back
        state = self.g.automation.get(f"/aim/mcp-proxies/{hrid}", expect=(200, 404))
        found = state.json() if state.status_code == 200 else None
        if found and not recorded(SECTION, "mcp-proxy", found["id"]):
            raise SeedError(f"MCP proxy {name}: {hrid} is the hrid of an API the seed did not create")
        if found is None or not subset(public(automation), found):
            created_now = found is None
            found = self.g.automation.put("/aim/mcp-proxies", json=automation).json()
            if created_now:
                record(SECTION, "mcp-proxy", "MCP proxy APIs", name, found["id"], hrid=hrid)
            log(SECTION, f"✓ MCP proxy {'created' if created_now else 'updated'}: {name}")
        api_id = found["id"]
        written = [self.plans(api_id, spec["securityConfiguration"]), self.api(api_id, spec), self.versions(api_id, spec)]
        self.deploy(api_id, any(written))
        if found_controls:
            log(SECTION, f"Controls of {name} are {'on' if controls.protected() else 'off'}: {controls.named(found_controls)}")
        self.wait_live(spec)

    def plans(self, api_id, wanted):
        """The keys the Automation API cannot set on the security of a plan, written over the plan as it was read."""
        changed = False
        live = self.g.v2.rows(f"/apis/{api_id}/plans", gamma.LISTS)
        for name, keys in wanted.items():
            plan = next((p for p in live if p["name"] == name and p["status"] == "PUBLISHED"), None)
            if plan is None:
                raise SeedError(f"MCP proxy {api_id}: the plan {name} is not published")
            if not subset(keys, plan["security"]["configuration"]):
                plan["security"]["configuration"].update(keys)
                self.g.v2.put(f"/apis/{api_id}/plans/{plan['id']}", json={k: v for k, v in plan.items() if k not in PLAN_READ_ONLY})
                changed = True
        return changed

    def api(self, api_id, spec):
        """The response template of a refusal, the read timeout of the endpoint and what the gateway records (tracing),
        written over the API as it was read."""
        api = self.g.v2.get(f"/apis/{api_id}").json()
        timeout, templates, analytics = spec["readTimeout"], spec["responseTemplates"], spec.get("analytics") or {}

        def waits(group):
            return ((group.get("sharedConfiguration") or {}).get("http") or {}).get("readTimeout") == timeout

        if (subset(templates, api.get("responseTemplates") or {}) and all(waits(g) for g in api["endpointGroups"])
                and subset(analytics, api.get("analytics") or {})):
            return False
        api["responseTemplates"] = {**(api.get("responseTemplates") or {}), **templates}
        api["analytics"] = merge(api.get("analytics") or {}, analytics)
        for group in api["endpointGroups"]:
            group["sharedConfiguration"] = group.get("sharedConfiguration") or {}
            group["sharedConfiguration"].setdefault("http", {})["readTimeout"] = timeout
        self.g.v2.put(f"/apis/{api_id}", json={k: v for k, v in api.items() if k not in API_READ_ONLY})
        return True

    def versions(self, api_id, spec):
        wanted = {key: spec[key] for key in ("supportedProtocolVersions", "endpointProtocolVersions")}
        if subset(wanted, self.g.aim.get(f"/mcp-proxies/{api_id}").json()):
            return False
        self.g.aim.put(f"/mcp-proxies/{api_id}/protocol-versions", json=wanted)
        return True

    def deploy(self, api_id, changed):
        """What these steps wrote reaches the gateway with a deployment, which is asked for once, and only when something
        was written or the API says it waits for one."""
        if changed or self.g.v2.get(f"/apis/{api_id}").json()["deploymentState"] != "DEPLOYED":
            self.g.v2.post(f"/apis/{api_id}/deployments", json=DEPLOYMENT)
            controls.touched()
            log(SECTION, "  ✓ Deployed")

    def wait_live(self, spec):
        """Waits until each person of the file is offered as many tools as their role allows: that needs the gateway to
        serve the proxy, to introspect the token at Access Management, and the token to carry its claims. A person's
        `tools` is a number, or {protected: n, unprotected: m} when the tools of their role are a control: m while it is
        off. Skipped when the seed does not know the gateway."""
        gateway, ready = os.getenv("GATEWAY_INTERNAL_URL"), spec.get("ready")
        if not gateway or not ready or not waits_for_gateway():
            return
        path, token = spec["automation"]["contextPath"], ready["token"]

        def offered(user):
            sign_in = {"grant_type": "password", "username": user["username"], "password": user["password"],
                       "scope": "openid profile email"}
            origin, endpoint = split(token["url"])
            access = Api(origin).post(endpoint, data=sign_in, auth=(token["client"], token["secret"]),
                                      headers={"Content-Type": None}, timeout=15).json()["access_token"]
            headers = {"Authorization": f"Bearer {access}", "Accept": "application/json, text/event-stream"}
            answer = Api(gateway).post(path, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                                       headers=headers, timeout=15)
            tools, wanted = jsonrpc(answer)["result"]["tools"], user["tools"]
            if isinstance(wanted, dict):
                wanted = wanted["protected" if controls.protected() else "unprotected"]
            if len(tools) != wanted:
                raise SeedError(f"{len(tools)} tools offered, {wanted} expected")

        def check():
            for user in ready["users"]:
                try:
                    offered(user)
                except SeedError as e:
                    raise SeedError(one_line(f"{user['username']}: {e}")) from None

        what = f"{spec['automation']['name']} on the gateway ({path})"
        wait_until(SECTION, what, check, int(setting("GATEWAY_READY_TIMEOUT", "300")))

    # --- An MCP studio ------------------------------------------------------------------------

    def tools(self, specs):
        """The catalog's tools a studio exposes, by their entity ids: `mcp-tool.<server slug>.<tool>` for an MCP server's
        and `mcp-tool.<api>-<operation>` for the ones made from a REST API."""
        catalog = {t["entityId"]: t["id"] for t in self.g.aim.rows("/catalog/mcp-tools", gamma.CATALOG)}
        entries = []
        for spec in specs:
            if "server" in spec:
                entity = f"mcp-tool.{self.server_slug(spec['server'])}.{slugify(spec['tool'])}"
            else:
                entity = f"mcp-tool.{slugify(spec['api'])}-{slugify(spec['tool'])}"
            if entity not in catalog:
                raise SeedError(f"MCP studio: the tool {entity} is not in the catalog")
            entries.append({"id": catalog[entity], **({"alias": spec["alias"]} if "alias" in spec else {})})
        return entries

    def api_source(self, name):
        """The catalog source an API of API Management gave its tools: a studio holds the key it calls that API with by it."""
        if name not in self.sources:
            api = next((a for a in self.g.aim.rows("/catalog/api-tools/sources", gamma.CATALOG, q=name) if a["name"] == name), None)
            sources = self.g.aim.rows("/catalog/sources", gamma.CATALOG, sourceKind="apim.api")
            source = api and next((s for s in sources if (s.get("definition") or {}).get("apiId") == api["id"]), None)
            if not source:
                raise SeedError(f"MCP studio: the API '{name}' has no tools in the catalog: seed/aim/api-tools imports them before the studio")
            self.sources[name] = source["id"]
        return self.sources[name]

    def existing(self, name, path):
        """The studio at this path, which must be one the seed made."""
        found = next((p for p in self.g.aim.rows("/mcp-proxies", gamma.WORKSPACES)
                      if p.get("mode") == "STUDIO" and (p.get("contextPath") or "").rstrip("/") == path.rstrip("/")), None)
        if found and not recorded(SECTION, "studio", found["id"]):
            raise SeedError(f"MCP studio {name}: {path} is the path of '{found['name']}', which the seed did not create")
        return found

    def studio(self, spec):
        """A studio made in one go, or in steps (`access` present): its keys tell apart."""
        (self.agents_studio if "access" in spec else self.plain_studio)(spec)

    def plain_studio(self, spec):
        """Created from the catalog's tools, and replaced when its file or its tools changed: neither its plan nor its
        tools can be edited in place. Its authorization check is switched as its file says, then deployed."""
        name, path = spec["name"], spec["contextPath"]
        body = {"name": name, "description": spec.get("description"), "contextPath": path,
                "supportedProtocolVersions": spec["supportedProtocolVersions"], "plans": [spec["plan"]],
                "specification": {"tools": self.tools(spec["tools"]), "promptIds": [], "resourceIds": []}}
        wanted = digest(body)
        found = self.existing(name, path)
        if found and recorded_digest(found["id"]) != wanted:
            self.g.aim.delete(f"/mcp-proxies/{found['id']}")
            found = None
        if found is None:
            found = self.g.aim.post("/mcp-studios", json=body).json()
            record(SECTION, "studio", "MCP studios", name, found["id"], digest=wanted)
            log(SECTION, f"✓ MCP studio created: {name}")
        enabled = bool(spec.get("fga"))
        if self.g.aim.get(f"/mcp-proxies/{found['id']}").json().get("fgaEnabled", False) != enabled:
            self.g.aim.put(f"/mcp-studios/{found['id']}/fga", json={"enabled": enabled})
            self.g.v2.post(f"/apis/{found['id']}/deployments", json=DEPLOYMENT)
            log(SECTION, f"  ✓ Authorization check {'on' if enabled else 'off'}")

    # --- A studio built in steps (a file with `access`) ----------------------------------------

    def agents_studio(self, spec):
        """Created with its registration plan, then set up in place, step by step, each writing only what differs. It is
        replaced only when what it is created from changed: the tools, their aliases, the key it calls the APIs with and
        its plan cannot be edited in place. What the seed did not create is never touched."""
        name, path, access = spec["name"], spec["contextPath"], spec["access"]
        needs(access, name, "access", "registration", "agents")
        needs(access["registration"], name, "access.registration", "name", "type")
        needs(access["agents"], name, "access.agents", "name", "type", "jwksUrl", "clientIdClaim")
        if access["agents"]["type"] != "JWT":
            raise SeedError(f"MCP studio {name}: access.agents.type is {access['agents']['type']}, only JWT keeps the subscription required")
        body = {"name": name, "contextPath": path, "protocolVersion": spec["protocolVersion"],
                "specification": {"tools": self.tools(spec["tools"])},
                "plans": [{"name": access["registration"]["name"], "security": {
                    "type": access["registration"]["type"], "configuration": access["registration"].get("configuration", {"scopes": []})}}],
                "fgaEnabled": False,
                "upstreamAuth": [{"catalogSourceId": self.api_source(u["api"]), "upstreamAuth": {k: v for k, v in u.items() if k != "api"}}
                                 for u in spec.get("upstreamAuth") or []]}
        if "description" in spec:
            body["description"] = spec["description"]
        wanted = digest(body)
        found = self.existing(name, path)
        if found and recorded_digest(found["id"]) != wanted:
            self.g.aim.delete(f"/mcp-proxies/{found['id']}")
            log(SECTION, f"  ✓ MCP studio deleted: {name} is made again from its file")
            found = None
        changed = found is None
        if changed:
            found = self.g.aim.post("/mcp-studios", json=body).json()
            record(SECTION, "studio", "MCP studios", name, found["id"], digest=wanted)
            log(SECTION, f"✓ MCP studio created: {name}")
        api_id = found["id"]
        changed = self.access(api_id, name, access) or changed
        changed = self.checks(api_id, name, spec) or changed
        self.deploy(api_id, changed)
        self.wait_served(spec)

    def access(self, api_id, studio, access):
        """The plan of the agents is published, then the registration plan is closed. The registration plan is what the
        platform needs to register the studio in Access Management, and it lets in any token and attributes the call to no
        one: once the plan of the agents holds, it only lets an unknown caller through. Closed already is nothing to do."""
        agents, registration, changed = access["agents"], access["registration"]["name"], False
        plans = self.g.v2.rows(f"/apis/{api_id}/plans", gamma.LISTS)
        security = jwt_security(agents)
        plan = next((p for p in plans if p["name"] == agents["name"] and p["status"] == "PUBLISHED"), None)
        if plan is None:
            plan = self.g.v2.post(f"/apis/{api_id}/plans", json={
                "definitionVersion": "V4", "name": agents["name"], "mode": "STANDARD", "validation": "AUTO",
                "description": agents.get("description", "Agents signed in by Access Management, one subscription each"),
                "characteristics": [], "tags": [], "excludedGroups": [], "commentRequired": False, "flows": [],
                "security": security}).json()
            self.g.v2.post(f"/apis/{api_id}/plans/{plan['id']}/_publish")
            log(SECTION, f"  ✓ Plan '{agents['name']}' published")
            changed = True
        elif plan["security"]["type"] != "JWT" or not subset(security["configuration"], plan["security"]["configuration"]):
            body = {k: v for k, v in plan.items() if k not in V4_PLAN_READ_ONLY}
            body["security"] = {"type": "JWT", "configuration": {**plan["security"]["configuration"], **security["configuration"]}}
            self.g.v2.put(f"/apis/{api_id}/plans/{plan['id']}", json=body)
            log(SECTION, f"  ✓ Plan '{agents['name']}' updated")
            changed = True
        open_plan = next((p for p in plans if p["name"] == registration and p["status"] not in ("CLOSED", "STAGING")), None)
        if open_plan:
            self.g.v2.post(f"/apis/{api_id}/plans/{open_plan['id']}/_close")
            log(SECTION, f"  ✓ Plan '{registration}' closed")
            changed = True
        return changed

    def checks(self, api_id, studio, spec):
        """The switches of the studio, then the flows they wrote. A switch is turned only when it is not as the file says:
        turning the authorization check on again erases what was written on its flow. The flows are written in one update
        of the API, as it was read, so that nothing else of it is lost."""
        authorization, approval, changed = spec.get("authorization"), spec.get("humanApproval"), False
        state = self.g.aim.get(f"/mcp-proxies/{api_id}").json()
        check = authorization is not None and bool(authorization.get("enabled", True))
        hold = approval is not None and bool(approval.get("enabled", True))
        if authorization is not None and state.get("fgaEnabled", False) != check:
            self.g.aim.put(f"/mcp-studios/{api_id}/fga", json={"enabled": check})
            log(SECTION, f"  ✓ Authorization check {'on' if check else 'off'}")
            changed = True
        if approval is not None and state.get("humanApprovalEnabled", False) != hold:
            self.g.aim.put(f"/mcp-proxies/{api_id}/human-approval", json={"enabled": hold})
            log(SECTION, f"  ✓ Human approval {'on' if hold else 'off'}")
            changed = True
        if not (check or hold):
            return changed
        api = self.g.v2.get(f"/apis/{api_id}").json()
        written = []
        if check:
            written.append(authorization_flow(api, authorization, studio))
        if hold and "holdWindowMs" in approval:
            written.append(approval_flow(api, approval["holdWindowMs"], studio))
        if check and hold:
            written.append(authorize_before_approving(api))
        if any(written):
            self.g.v2.put(f"/apis/{api_id}", json={k: v for k, v in api.items() if k not in STUDIO_API_READ_ONLY})
            log(SECTION, "  ✓ Flows written")
            changed = True
        return changed

    def wait_served(self, spec):
        """Waits until the gateway serves the studio and secures it: a call without a token is refused with a 401. A
        404 is a gateway that has not read the deployment yet. Skipped when the seed does not know the gateway."""
        gateway = os.getenv("GATEWAY_INTERNAL_URL")
        if not gateway or not waits_for_gateway():
            return
        path = spec["contextPath"]
        hello = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": str(spec["protocolVersion"]), "capabilities": {}, "clientInfo": {"name": "gravitee-lab-seed", "version": "1.0"}}}

        def check():
            try:
                answer = Api(gateway).send("POST", path, json=hello, timeout=15,
                                           headers={"Accept": "application/json, text/event-stream"})
            except requests.RequestException as e:
                raise SeedError(one_line(e)) from None
            if answer.status_code != 401:
                raise SeedError(f"HTTP {answer.status_code} without a token, 401 expected")

        wait_until(SECTION, f"{spec['name']} on the gateway ({path})", check, int(setting("GATEWAY_READY_TIMEOUT", "300")))


def seed():
    if not has_seed(*FOLDERS):
        return   # before any setting is read or any connection made: a playground that does not seed this is untouched
    proxies = McpProxies()
    proxies.connect()
    for path in files("aim", "mcp-proxies"):
        proxies.proxy(load(path))
    for path in files("aim", "studios"):
        proxies.studio(load(path))


def remove(records):
    """An MCP proxy through the Automation API and a studio through Gamma, both of which keep a record of what they made
    that APIM's own delete would leave behind."""
    mine = owned(records, SECTION, ORDER)
    if not mine:
        return
    proxies = McpProxies()
    proxies.connect()
    for x in mine:
        if x["kind"] == "mcp-proxy":
            proxies.g.automation.delete(f"/aim/mcp-proxies/{x['hrid']}")
        else:
            proxies.g.aim.delete(f"/mcp-proxies/{x['id']}")
        log(SECTION, f"✓ Deleted {KINDS[x['kind']]} '{x['name']}'")
