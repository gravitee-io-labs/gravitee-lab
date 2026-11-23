"""MCP proxies and MCP studios of Agent Management.

    seed/aim/mcp-proxies/*.yaml   an MCP server of the catalog behind the gateway, with its sign-in, plan and flows
    seed/aim/studios/*.yaml       an MCP studio: tools of the catalog, from MCP servers and REST APIs, under one endpoint

An MCP proxy is set up in three steps, each writing only what differs from its file:

    1. The Automation API creates it, by its hrid, with its sign-in, plan and flows. The steps the controls of
       seed/controls.yaml name (controls.py) are in it enabled or disabled as the mode says, and put back so on every run.
    2. What that API cannot carry is set through APIM and Gamma: the keys of a plan's security (`securityConfiguration`:
       `requireSubscription`, `propagateAuthHeader`...), the response template of a refusal, the read timeout of the
       endpoint, what the gateway records and the protocol versions. One deployment follows when anything changed.
    3. Its `subscriptions` are made once it is deployed, as a studio's are (below): an MCP proxy plan with
       `requireSubscription` lets in only the token of an application subscribed to it.
    4. The gateway must serve it: each person is offered the tools their role allows, which the mode decides: the tools of
       a role are unrestricted while their control is off.

A studio is only seeded when a file of seed/aim/studios says so: it needs the tools of the catalog (MCP servers and APIs
imported before it) and a gateway whose MCP libraries can serve a studio. Its file has one of two shapes, told apart by
its keys. A file with `plan` and `fga` is created with its plan and has its authorization check switched. Either shape sends
each API of its tools the key its `upstreamAuth` lists, and gives its protocol versions as the platform takes them
(versions.RELEASES, mcp-protocol-versions): the list `supportedProtocolVersions`, from either key, or the one value of
`protocolVersion`. Either shape writes the file's `analytics` (what the gateway records: logs, their payloads, traces) on
the API of the studio, as an MCP proxy's. A file with `access` is built in steps, each
writing only what differs:

    1. It is created with the registration plan (the platform registers the studio in Access Management with it), the
       protocol versions, the tools with their aliases and the key each API expects from the studio.
    2. The plan of the agents (JWT) is published, then the registration plan is closed: it lets in any token and
       attributes the call to no one, while the plan of the agents keeps the subscription required.
    3. The authorization check is switched on, then its flow is given the arguments of the call, the attributes of the
       person and, with `agentIdentityResolution`, the agent the token names (authz-pep 3.0.2 leaves it out by default);
       human approval is switched on and given its hold window.
    4. One deployment follows when anything changed, and the gateway must serve the studio and refuse a caller without a token.

Re-seed starts the lessons over, and a lesson may add tools to a studio with Edit tools in Gamma: on every run, the tools of a
studio of either shape are brought back to its file, with the keys of the APIs, by a write of its specification (one the
platform does not take has the studio made again). The checks and their flows follow that write, because saving the tools may
rebuild the flow of the authorization check.

A file of either shape, and an MCP proxy file, may list `subscriptions`: [{application, plan, apiKey}], each an application of
API Management (by its name) subscribed to a published plan of the studio or the proxy (by its name; `plan` may be left out
when it has one), with the custom key `apiKey` on a plan of API keys. They are made once the studio or the proxy is
deployed, through the management API (both are APIs: apim.subscribe, the one way that takes a custom key), and again for a
studio made again. One that holds the access already (accepted, pending or paused) is left as it is, but for one the seed
made and left pending, which it accepts. A subscription a control of seed/controls.yaml names
(`<application> → <studio or proxy> (<plan>)`) is paused while the control is on and resumed while it is off, as one of
seed/apim/subscriptions is: the kill switch of an agent reaches its tools too.
"""

import copy
import json
import os
from urllib.parse import urlsplit

from . import controls
from . import gamma
from . import versions
from .apim import ENDED, UNSUPPORTED, accept, digest_of, find_application, held_subscription, merge, subscribe
from .common import (OK, Api, SeedError, adopted, contains, created, excerpt, files, forget, has_seed, load, log, making, owned, record,
                     recorded, setting, slugify, wait_until, waits_for_gateway)

SECTION = "aim-proxies"
# The folders of the seed it owns
FOLDERS = ("aim/mcp-proxies", "aim/studios")
# The Gamma modules it fills: seed.py checks that the platform serves them before it changes anything
GAMMA = ("aim",)
# What it records, dependents first
ORDER = ["subscription", "mcp-proxy", "studio"]
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
KINDS_PLURAL = {"MCP proxy": "MCP proxies", "MCP studio": "MCP studios"}   # of the label of a subscription's record
# The keys of a studio's file that give its protocol versions, one of the two: which one depends on the build, and a release
# takes the first
VERSION_KEYS = ("supportedProtocolVersions", "protocolVersion")
PROXY_VERSION_KEYS = ("supportedProtocolVersions", "endpointProtocolVersions")   # of an MCP proxy file
# What a subscription of a studio's file may say
SUBSCRIPTION_KEYS = ("application", "plan", "apiKey")


def gateway_target(target, internal):
    """The tools-http address the gateway can call, keeping the API path when Gravitee copied a loopback portal port."""
    url = urlsplit(target)
    if url.hostname not in ("localhost", "127.0.0.1"):
        return target
    suffix = url.path or ""
    if url.query:
        suffix += "?" + url.query
    return internal.rstrip("/") + suffix


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


def protocol_versions(spec):
    """A studio's protocol versions as the platform takes them (versions.RELEASES, mcp-protocol-versions): the list, from
    either key of the file, or the one value the file gives, which a build that refuses the list takes."""
    given = {key: spec[key] for key in VERSION_KEYS if key in spec}
    if len(given) != 1:
        raise SeedError(f"MCP studio {spec['name']}: it gives {' or '.join(VERSION_KEYS)}, one of the two")
    values = next(iter(given.values()))
    values = values if isinstance(values, list) else [values]
    if not all(isinstance(value, str) for value in values):
        raise SeedError(f"MCP studio {spec['name']}: a protocol version is text: quote it")
    if versions.variant("mcp-protocol-versions") == "list":
        return {VERSION_KEYS[0]: values}
    if "protocolVersion" not in given or len(values) != 1:
        raise SeedError(f"MCP studio {spec['name']}: this platform takes one protocol version: write protocolVersion")
    return {"protocolVersion": values[0]}


def server_name(server):
    """The name an MCP server gave itself when it was discovered."""
    return ((server.get("definition") or {}).get("serverInfo") or {}).get("name")


def jsonrpc(answer):
    """The message of a JSON-RPC answer, plain or as the data line of an event stream: None when it has no such line."""
    if "json" in answer.headers.get("Content-Type", ""):
        return answer.json()
    return next((json.loads(line[5:]) for line in answer.text.splitlines() if line.startswith("data:")), None)


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
            steps.insert(steps.index(check), step)
            changed = True
        else:
            if not contains(step.get("configuration"), wanted) or not step.get("enabled", True):
                step["configuration"] = {**(step.get("configuration") or {}), **wanted}
                step["enabled"] = True
                changed = True
            if steps.index(step) > steps.index(check):
                steps.remove(step)
                steps.insert(steps.index(check), step)
                changed = True
    configuration = check.setdefault("configuration", {})
    for key, entries in (("subjectPropertiesExpr", pairs(authorization.get("subject"))),
                         ("contextExpr", pairs(authorization.get("context")))):
        if entries is not None and configuration.get(key) != entries:
            configuration[key] = entries
            changed = True
    resolution = authorization.get("agentIdentityResolution")
    if resolution is not None and configuration.get("agentIdentityResolution") != resolution:
        configuration["agentIdentityResolution"] = resolution
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


def read_authorization(spec, studio):
    """The `authorization` of a studio's file, checked before anything is made."""
    authorization = spec.get("authorization")
    if authorization is not None and not isinstance(authorization, dict):
        raise SeedError(f"MCP studio {studio}: authorization is a mapping")
    resolution = (authorization or {}).get("agentIdentityResolution")
    if resolution is not None and not isinstance(resolution, bool):
        raise SeedError(f"MCP studio {studio}: authorization.agentIdentityResolution is true or false")


def of_studio(api_id):
    """The records of a studio and of the subscriptions to it: what goes when the studio is deleted."""
    return [x for x in created() if x["product"] == SECTION
            and ((x["kind"], x["id"]) == ("studio", api_id) or (x["kind"], x.get("api")) == ("subscription", api_id))]


def read_subscriptions(spec, studio, kind="MCP studio"):
    """The `subscriptions` of a studio's file, or of an MCP proxy's (`kind`), checked before anything is made:
    [{application, plan, apiKey}]."""
    listed = spec.get("subscriptions")
    if listed is None:
        return []
    where = f"{kind} {studio}: subscriptions"
    if not isinstance(listed, list) or not all(isinstance(s, dict) for s in listed):
        raise SeedError(f"{where} is a list of {{application, plan, apiKey}}")
    for sub in listed:
        unknown = sorted(set(sub) - set(SUBSCRIPTION_KEYS), key=str)
        if unknown:
            raise SeedError(f"{where}: {', '.join(map(str, unknown))} is not a key of a subscription ({', '.join(SUBSCRIPTION_KEYS)})")
        if not (isinstance(sub.get("application"), str) and sub["application"]):
            raise SeedError(f"{where}: each names its application")
        if "plan" in sub and not (isinstance(sub["plan"], str) and sub["plan"]):
            raise SeedError(f"{where}: the plan of {sub['application']} is the name of a plan of the {kind.split()[-1]}")
        if "apiKey" in sub and not isinstance(sub["apiKey"], str):
            # An unquoted ${NAME} whose value YAML reads as a number or a boolean would send the key changed
            raise SeedError(f"{where}: the apiKey of {sub['application']} is read as {type(sub['apiKey']).__name__}, not as "
                            "text: quote the variable in the file, '${NAME}'")
        if "apiKey" in sub and not sub["apiKey"]:
            raise SeedError(f"{where}: the apiKey of {sub['application']} is empty")
    return listed


def live_tools(detail):
    """The tools a studio serves, as Gamma gives the studio: each endpoint of its API holds the specification of the tools
    of one source, each tool with its catalog id, its entity id and its alias. The same tool once."""
    found, seen = [], set()
    for group in detail.get("endpointGroups") or []:
        for endpoint in group.get("endpoints") or []:
            for tool in (((endpoint.get("configuration") or {}).get("specification") or {}).get("tools") or []):
                if tool.get("id") and tool["id"] not in seen:
                    seen.add(tool["id"])
                    found.append(tool)
    return found


def same_tools(live, wanted, entities):
    """Whether the studio serves the tools its file asks for, no more, no fewer, under the same aliases. A tool the platform
    gives is the file's when it carries its catalog id or its entity id (`entities`: catalog id -> entity id); its alias is
    compared when the platform says one, and an alias that is the tool's own name is none."""
    def alias(value, name):
        return None if not value or value == name else value

    def matches(tool, entry):
        return tool["id"] in (entry["id"], entities.get(entry["id"])) and (
            "alias" not in tool or alias(tool.get("alias"), tool.get("name")) == alias(entry.get("alias"), tool.get("name")))
    return (all(any(matches(t, e) for e in wanted) for t in live)
            and all(any(matches(t, e) for t in live) for e in wanted))


def needs(spec, studio, where, *keys):
    """A key of the file the studio cannot be made without, named by where it should stand."""
    for key in keys:
        if key not in spec:
            raise SeedError(f"MCP studio {studio}: {where}.{key} is missing")


def recorded_digest(studio_id):
    return next((x.get("digest") for x in created() if (x["product"], x["kind"], x["id"]) == (SECTION, "studio", studio_id)), None)



def response_templates(spec, what):
    """The `responseTemplates` of a file: each key a mapping of media types to a template, as the platform keeps them."""
    templates = spec.get("responseTemplates") or {}
    if not isinstance(templates, dict) or not all(isinstance(media, dict) and all(isinstance(t, dict) for t in media.values())
                                                  for media in templates.values()):
        raise SeedError(f"{what}: responseTemplates maps each key to its media types, each to a template "
                        "(GAPL_AUTHZ: {\"*/*\": {statusCode: 200, body: ...}})")
    return templates


def with_templates(live, templates):
    """The platform's templates with the file's: a key's media types merge, and one the file names replaces its own."""
    live = live or {}
    return {**live, **{key: {**(live.get(key) or {}), **media} for key, media in templates.items()}}


def read_analytics(spec, what):
    """The `analytics` of a file: what the gateway records of its API (logging, tracing, otelLogs), as the API keeps it."""
    analytics = spec.get("analytics") or {}
    if not isinstance(analytics, dict):
        raise SeedError(f"{what}: analytics is a mapping, as the API keeps it (logging, tracing, otelLogs)")
    return analytics


def with_analytics(api, analytics):
    """The file's `analytics` written over the API's, as it was read. Whether anything changed."""
    if not analytics or contains(api.get("analytics") or {}, analytics):
        return False
    api["analytics"] = merge(api.get("analytics") or {}, copy.deepcopy(analytics))
    return True

class McpProxies:
    def __init__(self):
        self.g = gamma.Gamma()
        self.entities = {}   # catalog id of a tool -> its entity id, as the catalog lists them

    def connect(self):
        self.g.connect(SECTION, lambda: self.g.aim.get("/mcp-proxies", params={"perPage": 1}), "MCP proxies")

    def server_slug(self, name):
        """The slug the catalog gave an MCP server, from its name: it prefixes the tools in the access rules, and what
        the catalog derives it from is its own business."""
        servers = self.g.aim.rows("/catalog/mcp-servers", gamma.CATALOG)
        found = next((s for s in servers if server_name(s) == name), None)
        if found is None:
            raise SeedError(f"MCP server {name} is not in the catalog: seed/aim/mcp-servers imports it before the proxy")
        return found["slug"]

    # --- An MCP proxy -------------------------------------------------------------------------

    def proxy(self, spec):
        automation = fill(spec["automation"], self.server_slug(spec["server"]))
        hrid, name = automation["hrid"], automation["name"]
        response_templates(spec, f"MCP proxy {name}")   # read before anything is made, written on its API
        read_analytics(spec, f"MCP proxy {name}")
        subscriptions = read_subscriptions(spec, name, "MCP proxy")
        found_controls = controls.of("mcp-proxy", name)
        controls.align(automation.get("flows"), found_controls)   # written in both modes: a step someone switched is put back
        state = self.g.automation.get(f"/aim/mcp-proxies/{hrid}", expect=(200, 404))
        found = state.json() if state.status_code == 200 else None
        if found and not recorded(SECTION, "mcp-proxy", found["id"]) and not adopted(SECTION, "mcp-proxy", name, found["id"], hrid=hrid):
            raise SeedError(f"MCP proxy {name}: {hrid} is the hrid of an API the seed did not create")
        if found is None:
            with making(SECTION, "mcp-proxy", "MCP proxy APIs", name, missing=True, hrid=hrid):
                found = self.g.automation.put("/aim/mcp-proxies", json=automation).json()
                record(SECTION, "mcp-proxy", "MCP proxy APIs", name, found["id"], hrid=hrid)
            log(SECTION, f"✓ MCP proxy created: {name}")
        elif not contains(found, public(automation)):
            found = self.g.automation.put("/aim/mcp-proxies", json=automation).json()
            log(SECTION, f"✓ MCP proxy updated: {name}")
        api_id = found["id"]
        written = [self.plans(api_id, spec.get("securityConfiguration") or {}), self.api(api_id, spec), self.versions(api_id, spec)]
        self.deploy(api_id, any(written))
        if found_controls:
            log(SECTION, f"Controls of {name} are {'on' if controls.protected() else 'off'}: {controls.named(found_controls)}")
        self.subscribe(api_id, name, subscriptions, "MCP proxy")   # before the wait: a plan may let in subscribed callers only
        self.wait_live(spec)

    def plans(self, api_id, wanted):
        """The keys the Automation API cannot set on the security of a plan, written over the plan as it was read."""
        changed = False
        live = self.g.v2.rows(f"/apis/{api_id}/plans", gamma.LISTS)
        for name, keys in wanted.items():
            plan = next((p for p in live if p["name"] == name and p["status"] == "PUBLISHED"), None)
            if plan is None:
                raise SeedError(f"MCP proxy {api_id}: the plan {name} is not published")
            if not contains(plan["security"]["configuration"], keys):
                plan["security"]["configuration"].update(keys)
                self.g.v2.put(f"/apis/{api_id}/plans/{plan['id']}", json={k: v for k, v in plan.items() if k not in PLAN_READ_ONLY})
                changed = True
        return changed

    def api(self, api_id, spec):
        """The response template of a refusal, the read timeout of the endpoint and what the gateway records (tracing),
        written over the API as it was read."""
        api = self.g.v2.get(f"/apis/{api_id}").json()
        timeout, analytics = spec["readTimeout"], read_analytics(spec, f"MCP proxy {spec['automation']['name']}")
        templates = response_templates(spec, f"MCP proxy {spec['automation']['name']}")

        def waits(group):
            return ((group.get("sharedConfiguration") or {}).get("http") or {}).get("readTimeout") == timeout

        if (contains(api.get("responseTemplates") or {}, templates) and all(waits(g) for g in api["endpointGroups"])
                and contains(api.get("analytics") or {}, analytics)):
            return False
        api["responseTemplates"] = with_templates(api.get("responseTemplates"), templates)
        api["analytics"] = merge(api.get("analytics") or {}, analytics)
        for group in api["endpointGroups"]:
            group["sharedConfiguration"] = group.get("sharedConfiguration") or {}
            group["sharedConfiguration"].setdefault("http", {})["readTimeout"] = timeout
        self.g.v2.put(f"/apis/{api_id}", json={k: v for k, v in api.items() if k not in API_READ_ONLY})
        return True

    def versions(self, api_id, spec):
        wanted = {key: spec[key] for key in PROXY_VERSION_KEYS if key in spec}
        if not wanted:
            return False
        if versions.variant("mcp-protocol-versions") == "single":
            log(SECTION, f"! MCP proxy {spec['automation']['name']}: {' and '.join(wanted)} left out, this platform sets no "
                         "protocol versions on an MCP proxy")
            return False
        if contains(self.g.aim.get(f"/mcp-proxies/{api_id}").json(), wanted):
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
            access = Api(token["url"]).post("", data=sign_in, auth=(token["client"], token["secret"]), what=f"POST {token['url']}",
                                            headers={"Content-Type": None}, timeout=15).json()["access_token"]
            headers = {"Authorization": f"Bearer {access}", "Accept": "application/json, text/event-stream"}
            answer = Api(gateway).post(path, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                                       headers=headers, timeout=15)
            tools, wanted = ((jsonrpc(answer) or {}).get("result") or {}).get("tools", []), user["tools"]
            if isinstance(wanted, dict):
                wanted = wanted["protected" if controls.protected() else "unprotected"]
            if len(tools) != wanted:
                raise SeedError(f"{len(tools)} tools offered, {wanted} expected")

        def check():
            for user in ready["users"]:
                try:
                    offered(user)
                except SeedError as e:
                    raise SeedError(excerpt(f"{user['username']}: {e}")) from None

        what = f"{spec['automation']['name']} on the gateway ({path})"
        wait_until(SECTION, what, check, int(setting("GATEWAY_READY_TIMEOUT", "300")))

    # --- An MCP studio ------------------------------------------------------------------------

    def tools(self, specs):
        """The catalog's tools a studio exposes, by their entity ids: `mcp-tool.<server slug>.<tool>` for an MCP server's,
        `mcp-tool.<api>-<operation>` for the ones made from a REST API and `mcp-tool.<workflow>` for a workflow tool."""
        catalog = {t["entityId"]: t["id"] for t in self.g.aim.rows("/catalog/mcp-tools", gamma.CATALOG)}
        self.entities.update({i: e for e, i in catalog.items()})
        entries = []
        for spec in specs:
            if "server" in spec:
                entity = f"mcp-tool.{self.server_slug(spec['server'])}.{slugify(spec['tool'])}"
            elif "workflow" in spec:
                entity = f"mcp-tool.{slugify(spec['workflow'])}"
            else:
                entity = f"mcp-tool.{slugify(spec['api'])}-{slugify(spec['tool'])}"
            if entity not in catalog:
                raise SeedError(f"MCP studio: the tool {entity} is not in the catalog")
            entries.append({"id": catalog[entity], **({"alias": spec["alias"]} if "alias" in spec else {})})
        return entries

    def upstream_auth(self, spec):
        """The key the studio sends to each API of its tools: `upstreamAuth` of its file, [{api, type, ...}], each by the
        catalog source of its API, for either shape of studio."""
        return [{"catalogSourceId": self.api_source(u["api"]), "upstreamAuth": {k: v for k, v in u.items() if k != "api"}}
                for u in spec.get("upstreamAuth") or []]

    def api_source(self, name):
        """The catalog source an API of API Management gave its tools: a studio holds the key it calls that API with by it."""
        api = next((a for a in self.g.aim.rows("/catalog/api-tools/sources", gamma.CATALOG, q=name) if a["name"] == name), None)
        sources = self.g.aim.rows("/catalog/sources", gamma.CATALOG, sourceKind="apim.api")
        source = api and next((s for s in sources if (s.get("definition") or {}).get("apiId") == api["id"]), None)
        if not source:
            raise SeedError(f"MCP studio: the API '{name}' has no tools in the catalog: seed/aim/api-tools imports them before the studio")
        return source["id"]

    def existing(self, name, path):
        """The studio at this path, which must be one the seed made."""
        found = next((p for p in self.g.aim.rows("/mcp-proxies", gamma.WORKSPACES)
                      if p.get("mode") == "STUDIO" and (p.get("contextPath") or "").rstrip("/") == path.rstrip("/")), None)
        if found and not recorded(SECTION, "studio", found["id"]) and not adopted(SECTION, "studio", name, found["id"]):
            raise SeedError(f"MCP studio {name}: {path} is the path of '{found['name']}', which the seed did not create")
        return found

    def studio(self, spec):
        """A studio made in one go, or in steps (`access` present): its keys tell apart."""
        (self.agents_studio if "access" in spec else self.plain_studio)(spec)

    def plain_studio(self, spec):
        """Created from the catalog's tools, and replaced when its file changed: its plan cannot be edited in place. Tools
        someone changed are brought back to the file. Its authorization check is switched as its file says, then deployed,
        then its applications are subscribed."""
        name, path = spec["name"], spec["contextPath"]
        if "responseTemplates" in spec:
            raise SeedError(f"MCP studio {name}: responseTemplates is written for a studio built in steps (access), with the "
                            "flows it writes: give this one its access")
        if "authorization" in spec:   # read by a studio built in steps, which writes its check's flow; this one only switches it
            raise SeedError(f"MCP studio {name}: authorization is read for a studio built in steps (access); this one switches "
                            "its check with fga")
        subscriptions = read_subscriptions(spec, name)
        analytics = read_analytics(spec, f"MCP studio {name}")
        body = {"name": name, "description": spec.get("description"), "contextPath": path, **protocol_versions(spec),
                "plans": [spec["plan"]],
                "specification": {"tools": self.tools(spec["tools"]), "promptIds": [], "resourceIds": []}}
        if spec.get("upstreamAuth"):
            body["upstreamAuth"] = self.upstream_auth(spec)
        wanted = digest_of(body)
        found, recomposed = self.kept(name, path, wanted, body)
        generated = found is None or recomposed
        if found is None:
            with making(SECTION, "studio", "MCP studios", name, missing=True):
                found = self.g.aim.post("/mcp-studios", json=body).json()
                record(SECTION, "studio", "MCP studios", name, found["id"], digest=wanted)
            log(SECTION, f"✓ MCP studio created: {name}")
        enabled = bool(spec.get("fga"))
        toggled = self.g.aim.get(f"/mcp-proxies/{found['id']}").json().get("fgaEnabled", False) != enabled
        if toggled:
            self.g.aim.put(f"/mcp-studios/{found['id']}/fga", json={"enabled": enabled})
        recording = self.studio_analytics(found["id"], analytics, generated)   # after the switch, which may rewrite the API
        if toggled or recomposed or recording:
            self.g.v2.post(f"/apis/{found['id']}/deployments", json=DEPLOYMENT)
        if toggled:
            log(SECTION, f"  ✓ Authorization check {'on' if enabled else 'off'}")
        self.subscribe(found["id"], name, subscriptions)

    def studio_analytics(self, api_id, analytics, generated=False):
        """The file's `analytics` on the API of a studio made in one go, written over it as it was read. Whether it wrote."""
        internal = os.getenv("GATEWAY_INTERNAL_URL", "").strip() if generated else ""
        if not (analytics or internal):
            return False
        api = self.g.v2.get(f"/apis/{api_id}").json()
        changed = route_http_backends(api, internal)
        if not (with_analytics(api, analytics) or changed):
            return False
        self.g.v2.put(f"/apis/{api_id}", json={k: v for k, v in api.items() if k not in STUDIO_API_READ_ONLY})
        log(SECTION, "  ✓ Studio API settings written")
        return True

    def kept(self, name, path, wanted, body):
        """The studio at this path, as the seed keeps it: (None, False) when there is none, or when it was made from another
        file (its digest) and is deleted to be made again; else (the studio, whether its tools were written back to the
        file). A platform that does not take that write has the studio made again too."""
        found = self.existing(name, path)
        if found and recorded_digest(found["id"]) != wanted:
            self.drop(found, f"{name} is made again from its file")
            return None, False
        if found is None:
            return None, False
        recomposed = self.recompose(found["id"], name, body)
        if recomposed is None:
            self.drop(found, f"{name} is made again with the tools of its file")
            return None, False
        return found, recomposed

    def drop(self, found, why):
        """Deletes a studio to make it again, and its record with it, as soon as it is gone."""
        self.g.aim.delete(f"/mcp-proxies/{found['id']}")
        forget(*of_studio(found["id"]))
        log(SECTION, f"  ✓ MCP studio deleted: {why}")

    def recompose(self, api_id, name, body):
        """Brings the tools of the studio back to its file when they differ (a lesson added some with Edit tools), with the
        keys of the APIs, by a write of its specification. Whether it wrote; None when the platform does not offer the
        write (the caller makes the studio again). A studio whose tools the platform does not show is left as it is."""
        live = live_tools(self.g.aim.get(f"/mcp-proxies/{api_id}").json())
        wanted = body["specification"]["tools"]
        if not live or same_tools(live, wanted, self.entities):
            return False
        answer = self.g.aim.call("PUT", f"/mcp-studios/{api_id}/specification", expect=(*OK, *UNSUPPORTED),
                                 json={"specification": body["specification"], "upstreamAuth": body.get("upstreamAuth") or []})
        if answer.status_code in UNSUPPORTED:
            return None
        log(SECTION, f"  ✓ Tools brought back to the file: {len(wanted)} in {name}")
        return True

    def subscribe(self, api_id, name, subscriptions, kind="MCP studio"):
        """Subscribes each application of the file to its plan of the studio or the proxy (`kind`), unless it holds a
        subscription to it, then pauses or resumes one a control names as the mode says."""
        if not subscriptions:
            return
        plans = [p for p in self.g.v2.rows(f"/apis/{api_id}/plans", gamma.LISTS) if p.get("status") == "PUBLISHED"]
        plural = f"subscriptions to {KINDS_PLURAL[kind]}"
        for sub in subscriptions:
            plan = self.plan_of(plans, name, sub, kind)
            application = find_application(self.g.v1, "", sub["application"])
            if application is None:
                raise SeedError(f"{kind} {name}: no application is named '{sub['application']}': seed/apim/applications makes it")
            label = f"{sub['application']} → {name} ({plan['name']})"
            found = controls.of("subscription", label)
            held = held_subscription(self.g.v2, "", api_id, application["id"], plan["id"])
            if held:
                if not recorded(SECTION, "subscription", held["id"]):   # one an earlier seed made and lost the answer of, if any
                    adopted(SECTION, "subscription", label, held["id"], api=api_id)
                if held.get("status") == "PENDING" and recorded(SECTION, "subscription", held["id"]):
                    accept(self.g.v2, "", api_id, held["id"], sub.get("apiKey"))
                    held["status"] = "ACCEPTED"
                    log(SECTION, f"  ✓ Subscription accepted: {label}")
                else:
                    log(SECTION, f"  Subscription exists: {label}")
                self.paused_as_mode_says(api_id, held, found, label)
                continue
            with making(SECTION, "subscription", plural, label, missing=True, api=api_id):
                made = subscribe(self.g.v2, "", api_id, application["id"], plan["id"], sub.get("apiKey"))
                record(SECTION, "subscription", plural, label, made["id"], api=api_id)
            if made.get("status") == "PENDING":
                accept(self.g.v2, "", api_id, made["id"], sub.get("apiKey"))
            log(SECTION, f"  ✓ Subscribed: {label}")
            self.paused_as_mode_says(api_id, {**made, "status": "ACCEPTED"}, found, label)

    def paused_as_mode_says(self, api_id, sub, found, label):
        """A subscription the controls name, paused while they are on and resumed while they are off, with the routes the
        Console calls: a paused subscription lets no call of its application through and keeps its keys. Only one the
        seed made is changed."""
        route = controls.align_subscription(sub.get("status"), found)
        state = "on" if controls.protected() else "off"
        if found and not route:
            log(SECTION, f"  Controls of {label} are {state}: {controls.named(found)}")
        if not route:
            return
        if not recorded(SECTION, "subscription", sub["id"]):
            log(SECTION, f"! Subscription {label} is not one the seed made: control {controls.named(found)} leaves it as it is")
            return
        self.g.v2.post(f"/apis/{api_id}/subscriptions/{sub['id']}/{route}", what=f"Subscription {label}")
        controls.touched()
        log(SECTION, f"  ✓ Controls of {label} put back, {state}: {controls.named(found)} "
                     f"(subscription {'paused' if route == '_pause' else 'resumed'})")

    @staticmethod
    def plan_of(plans, name, sub, kind="MCP studio"):
        """The published plan of the studio or the proxy a subscription of its file names, or its only one."""
        if "plan" in sub:
            plan = next((p for p in plans if p["name"] == sub["plan"]), None)
            if plan is None:
                raise SeedError(f"{kind} {name}: no published plan is named '{sub['plan']}'")
        elif len(plans) == 1:
            plan = plans[0]
        else:
            raise SeedError(f"{kind} {name}: {len(plans)} plans are published: the subscription of "
                            f"{sub['application']} names its plan")
        security = plan.get("security") or {}
        if security.get("type") == "KEY_LESS":
            raise SeedError(f"{kind} {name}: the plan {plan['name']} is keyless, which takes no subscription")
        if sub.get("apiKey") and security.get("type") != "API_KEY":
            raise SeedError(f"{kind} {name}: the subscription of {sub['application']} has an apiKey, which needs a plan "
                            f"of API keys, and {plan['name']} is {security.get('type')}")
        return plan

    # --- A studio built in steps (a file with `access`) ----------------------------------------

    def agents_studio(self, spec):
        """Created with its registration plan, then set up in place, step by step, each writing only what differs. It is
        replaced only when what it is created from changed: the tools, their aliases, the key it calls the APIs with and
        its plan cannot be edited in place. What the seed did not create is never touched."""
        name, path, access = spec["name"], spec["contextPath"], spec["access"]
        read_authorization(spec, name)
        response_templates(spec, f"MCP studio {name}")   # read before anything is made, written with the flows
        read_analytics(spec, f"MCP studio {name}")
        subscriptions = read_subscriptions(spec, name)
        needs(access, name, "access", "registration", "agents")
        needs(access["registration"], name, "access.registration", "name", "type")
        needs(access["agents"], name, "access.agents", "name", "type", "jwksUrl", "clientIdClaim")
        if access["agents"]["type"] != "JWT":
            raise SeedError(f"MCP studio {name}: access.agents.type is {access['agents']['type']}, only JWT keeps the subscription required")
        body = {"name": name, "contextPath": path, **protocol_versions(spec),
                "specification": {"tools": self.tools(spec["tools"])},
                "plans": [{"name": access["registration"]["name"], "security": {
                    "type": access["registration"]["type"], "configuration": access["registration"].get("configuration", {"scopes": []})}}],
                "fgaEnabled": False,
                "upstreamAuth": self.upstream_auth(spec)}
        if "description" in spec:
            body["description"] = spec["description"]
        wanted = digest_of(body)
        found, changed = self.kept(name, path, wanted, body)
        if found is None:
            with making(SECTION, "studio", "MCP studios", name, missing=True):
                found = self.g.aim.post("/mcp-studios", json=body).json()
                record(SECTION, "studio", "MCP studios", name, found["id"], digest=wanted)
            log(SECTION, f"✓ MCP studio created: {name}")
            changed = True
        api_id = found["id"]
        generated = changed
        changed = self.access(api_id, access) or changed
        changed = self.checks(api_id, name, spec, generated) or changed   # after the tools: writing them may rebuild the check's flow
        self.deploy(api_id, changed)
        self.subscribe(api_id, name, subscriptions)
        self.wait_served(spec)

    def access(self, api_id, access):
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
                "description": agents.get("description", "Callers signed in with Access Management, one subscription each"),
                "characteristics": [], "tags": [], "excludedGroups": [], "commentRequired": False, "flows": [],
                "security": security}).json()
            self.g.v2.post(f"/apis/{api_id}/plans/{plan['id']}/_publish")
            log(SECTION, f"  ✓ Plan '{agents['name']}' published")
            changed = True
        elif plan["security"]["type"] != "JWT" or not contains(plan["security"]["configuration"], security["configuration"]):
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

    def checks(self, api_id, studio, spec, generated=False):
        """The switches of the studio, then the flows they wrote, the file's `responseTemplates` (what a client reads when
        the check refuses, GAPL_AUTHZ) and its `analytics`. A switch is turned only when it is not as the file says: turning
        the authorization check on again erases what was written on its flow. The flows, the templates and the analytics are
        written in one update of the API, as it was read, so that nothing else of it is lost."""
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
        templates = response_templates(spec, f"MCP studio {spec.get('name')}")
        analytics = read_analytics(spec, f"MCP studio {spec.get('name')}")
        internal = os.getenv("GATEWAY_INTERNAL_URL", "").strip() if generated else ""
        if not (check or hold or templates or analytics or internal):
            return changed
        api = self.g.v2.get(f"/apis/{api_id}").json()
        written = [with_analytics(api, analytics), route_http_backends(api, internal)]
        if not contains(api.get("responseTemplates") or {}, templates):
            api["responseTemplates"] = with_templates(api.get("responseTemplates"), templates)
            written.append(True)
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
        first = spec.get("protocolVersion") or spec["supportedProtocolVersions"]
        hello = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": str(first[0] if isinstance(first, list) else first), "capabilities": {}, "clientInfo": {"name": "gravitee-lab-seed", "version": "1.0"}}}

        def check():
            answer = Api(gateway).send("POST", path, json=hello, timeout=15, headers={"Accept": "application/json, text/event-stream"})
            if answer.status_code != 401:
                raise SeedError(f"HTTP {answer.status_code} without a token, 401 expected")

        wait_until(SECTION, f"{spec['name']} on the gateway ({path})", check, int(setting("GATEWAY_READY_TIMEOUT", "300")))


def check():
    """The studio files, against what this version serves: the protocol versions they give, and human approval."""
    if not has_seed(*FOLDERS):
        return
    for path in files("aim", "studios"):
        spec = load(path)
        protocol_versions(spec)
        if spec.get("humanApproval") is not None and versions.variant("studio-human-approval") == "absent":
            raise SeedError(f"MCP studio {spec.get('name')}: this platform has no human approval on a studio, "
                            f"{versions.where('studio-human-approval', 'served')} has: leave humanApproval out")


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
    """A subscription to a studio is closed first, then an MCP proxy goes through the Automation API and a studio through
    Gamma, both of which keep a record of what they made that APIM's own delete would leave behind. A subscription whose
    studio is gone is closed already (404)."""
    mine = owned(records, SECTION, ORDER)
    if not mine:
        return
    proxies = McpProxies()
    proxies.connect()
    for x in mine:
        if x["kind"] == "subscription":
            proxies.g.v2.post(f"/apis/{x['api']}/subscriptions/{x['id']}/_close", expect=ENDED, idempotent=True)
            forget(x)
            log(SECTION, f"✓ Closed subscription '{x['name']}'")
            continue
        if x["kind"] == "mcp-proxy":
            proxies.g.automation.delete(f"/aim/mcp-proxies/{x['hrid']}")
        else:
            proxies.g.aim.delete(f"/mcp-proxies/{x['id']}")
        forget(x)
        log(SECTION, f"✓ Deleted {KINDS[x['kind']]} '{x['name']}'")


def route_http_backends(api, internal):
    """Repair generated loopback tools-http targets in an already loaded API."""
    changed = False
    if not internal:
        return changed
    for group in api.get("endpointGroups") or []:
        if group.get("type") != "tools-http":
            continue
        for endpoint in group.get("endpoints") or []:
            configuration = endpoint.get("configuration") or {}
            target = configuration.get("target")
            if not isinstance(target, str):
                continue
            updated = gateway_target(target, internal)
            if updated != target:
                configuration["target"] = updated
                changed = True
    return changed
