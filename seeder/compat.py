"""Measures, on the image of a management API of Gravitee, what the seeder relies on, and says whether lab_seeder/versions.py
holds for the version the image states.

    python3 seeder/compat.py IMAGE [IMAGE ...]     graviteeio/apim-management-api:4.13.5, graviteeio/am-management-api:4.13.5
    python3 seeder/compat.py --json IMAGE ...      one JSON line per image, for a matrix
    python3 seeder/compat.py --keep IMAGE          leaves the stack running, and a second run with --keep reuses it

Each image is started alone, on a Docker network of its own, with a throwaway MongoDB and the license of GRAVITEE_LICENSE
(from the environment or the lab's .env, never printed), then removed: nothing of the lab is touched. The version is the one
the image states on its technical API, asked inside its container. Each probe asks the platform what a seed meets there:

- a behavior of versions.RELEASES: the variant measured must be the one the tables give that version. A difference is a row
  to add, or a build to describe;
- an answer the seeder reads in more than one shape: the shape measured must be one it reads;
- each route of ROUTES, the others the seeder calls, must be served.

A route is served when its answer is not the framework's own 404 or 405, which a request with an unknown id still tells
apart from the 404 of an object that is not there. The exit status is 1 when a probe differs, 2 when an image cannot be
measured."""

import argparse
import base64
import contextlib
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "seeder"), str(ROOT / "lab-ui")]
from lab_seeder import aim_proxies, gamma, versions  # noqa: E402
from lab_ui.docker import envfiles  # noqa: E402

MONGO = "mongo:8.3.11"
INTERNAL = "compat"            # the password of the technical API, which no port of the probe's container publishes
READY = 420                    # seconds a management API is given to answer
GAMMA = "/gamma/organizations/DEFAULT/environments/DEFAULT"
AIM, AUTHZ = f"{GAMMA}/modules/aim", f"{GAMMA}/modules/authz"
V1 = "/management/organizations/DEFAULT/environments/DEFAULT"
V2 = "/management/v2/organizations/DEFAULT/environments/DEFAULT"
AM_ENV = "/management/organizations/DEFAULT/environments/DEFAULT"
UNKNOWN = "00000000-0000-0000-0000-000000000000"   # an id no object has, in a route whose presence is asked
PROTOCOL = "2025-11-25"


# --- The stack --------------------------------------------------------------------------

def docker(*args, check=True, env=None):
    done = subprocess.run(["docker", *args], capture_output=True, text=True, env=env)
    if check and done.returncode:
        raise RuntimeError(f"docker {' '.join(args[:2])}: {done.stderr.strip()[:300]}")
    return done.stdout.strip()


def license_key():
    """GRAVITEE_LICENSE of the environment, else of the lab's .env as Compose reads it."""
    return os.getenv("GRAVITEE_LICENSE") or envfiles.read(envfiles.ENV_FILE).get("GRAVITEE_LICENSE", "")


class Stack:
    """A management API alone with its MongoDB, on a network of its own. A container name is a host name: kept short, and
    the same for an image from one run to the next."""

    def __init__(self, image):
        self.image = image
        self.product = versions.AM if "am-management-api" in image else versions.APIM
        self.name = f"compat{zlib.crc32(image.encode()):08x}"
        self.http, self.technical = ("8083", "18083") if self.product == versions.APIM else ("8093", "18093")
        self.port = None

    def running(self):
        return docker("ps", "-q", "--filter", f"name=^{self.name}-mapi$", check=False) != ""

    def start(self, reuse=False):
        if not (reuse and self.running()):
            self.stop()
            docker("network", "create", self.name)
            docker("run", "-d", "--name", f"{self.name}-mongo", "--network", self.name, MONGO)
            settings = {"gravitee_ds_mongodb_host": f"{self.name}-mongo",
                        "gravitee_services_core_http_authentication_users_admin": INTERNAL}
            if self.product == versions.APIM:   # Gamma, as the lab's core runs it
                settings.update(gravitee_gamma_enabled="true", gravitee_analytics_type="none",
                                gravitee_installation_type="standalone")
            # the license goes through the environment of docker, never its command line, which any process can read
            docker("run", "-d", "--name", f"{self.name}-mapi", "--network", self.name, "-p", f"127.0.0.1::{self.http}",
                   *[arg for key, value in settings.items() for arg in ("-e", f"{key}={value}")], "-e", "gravitee_license_key",
                   self.image, env={**os.environ, "gravitee_license_key": license_key()})
        self.port = int(docker("port", f"{self.name}-mapi", self.http).splitlines()[0].rsplit(":", 1)[1])
        return self

    def stop(self):
        docker("rm", "-f", f"{self.name}-mapi", f"{self.name}-mongo", check=False)
        docker("network", "rm", self.name, check=False)

    def node(self):
        """The version the management API states on its technical API, asked inside its container."""
        said = docker("exec", f"{self.name}-mapi", "curl", "-s", "-u", f"admin:{INTERNAL}",
                      f"http://localhost:{self.technical}/_node", check=False)
        try:
            return json.loads(said)["version"]["MAJOR_VERSION"]
        except (ValueError, KeyError, TypeError):
            return None


# --- Talking to the platform -------------------------------------------------------------

class Client:
    def __init__(self, port, authorization):
        self.base, self.authorization = f"http://127.0.0.1:{port}", authorization
        self.version, self.studio = None, None

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers={"Authorization": self.authorization, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=60) as answer:
                return answer.status, parsed(answer.read())
        except urllib.error.HTTPError as e:
            return e.code, parsed(e.read())
        except (urllib.error.URLError, OSError) as e:
            return 0, str(e)


def parsed(raw):
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return raw.decode(errors="replace")


def basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def wait(check, what):
    deadline = time.time() + READY
    while time.time() < deadline:
        if check():
            return
        time.sleep(5)
    raise RuntimeError(f"{what} did not answer within {READY} s")


def served(answer):
    """Whether a route exists: the framework answers its own 404 for a path no resource has, and 405 for a method it lacks."""
    status, body = answer
    return status != 0 and not (status == 405 or (status == 404 and "HTTP 404 Not Found" in str(body)))


def refusal(answer):
    status, body = answer
    said = (body.get("technicalCode") or body.get("code") or body.get("message")) if isinstance(body, dict) else body
    return f"HTTP {status} {str(said or '')[:80]}".strip()


# --- Probes -----------------------------------------------------------------------------
# A probe gives what it measured, and may add notes for the docs. A probe of a behavior of versions.RELEASES is held to
# versions.variant; one registered with `reads`, to the shapes the seeder reads. Every probe of API Management but the first
# asks Gamma, and runs only where the first finds the modules the seeder fills.

PROBES = {versions.APIM: [], versions.AM: []}
READS = {}


def probe(product, name, reads=None):
    def register(function):
        PROBES[product].append((name, function))
        if reads is not None:
            READS[name] = reads
        return function
    return register


class Found:
    def __init__(self, measured, notes=""):
        self.measured, self.notes = measured, notes


@probe(versions.APIM, "gamma-modules")
def gamma_modules(api):
    """The modules the seeder fills, with the routes it fills them through: the first versions have no AI providers."""
    _, body = api.call("GET", "/gamma/organizations/DEFAULT/modules")
    listed = {m.get("id"): m.get("version") for m in body if isinstance(m, dict)} if isinstance(body, list) else {}
    notes = ", ".join(f"{name} {listed[name]}" for name in ("aim", "authz") if name in listed)
    if not {"aim", "authz"} <= set(listed):
        return Found("absent", notes)
    lacking = [path for path in (f"{AIM}/providers", f"{AUTHZ}/schema") if not served(api.call("GET", f"{path}?page=1&perPage=1"))]
    notes += "".join(f"; no {path.rsplit('/modules/', 1)[1]}" for path in lacking)
    return Found("early" if lacking else "served", notes)


@probe(versions.APIM, "agent-application")
def agent_application(api):
    return by_routes(api, "agent-application", "served", "absent")


@probe(versions.APIM, "governance")
def governance(api):
    """Absent where only some of its routes are served, as the approval rules alone of 4.14.0-alpha.1: the seed writes the
    approval settings first and the compliance answers last. The notes say what is lacking."""
    found = by_routes(api, "governance", "served", "absent")
    return Found("absent", found.measured) if found.measured.startswith("partly served") else found


@probe(versions.APIM, "studio-human-approval")
def studio_human_approval(api):
    """Whether a studio has the switch, and where its step goes once it is on: a flow of the API, which the seed writes the
    hold window on, or a flow of the platform's that the API does not show. Switched off again after."""
    found = by_routes(api, "studio-human-approval", "served", "absent")
    if found.measured != "served":
        return found
    made = studio(api)
    if not made.id:
        return Found(f"no studio: {refusal(made.answer)}")
    answer = api.call("PUT", f"{AIM}/mcp-proxies/{made.id}/human-approval", {"enabled": True})
    if answer[0] >= 300:
        return Found(f"not switched: {refusal(answer)}")
    _, live = api.call("GET", f"{V2}/apis/{made.id}")
    api.call("PUT", f"{AIM}/mcp-proxies/{made.id}/human-approval", {"enabled": False})
    flows = [flow.get("name") for flow in (live or {}).get("flows") or [] if isinstance(flow, dict)]
    return Found("served" if aim_proxies.APPROVAL_FLOW in flows else "switch")


@probe(versions.APIM, "authz-schema-documents")
def authz_schema_documents(api):
    return by_routes(api, "authz-schema-documents", "several", "one")


@probe(versions.APIM, "dashboard-module")
def dashboard_module(api):
    answer = api.call("POST", f"{GAMMA}/observability/dashboards", {"id": "compat-probe", "title": "Probe", "widgets": []})
    if answer[0] < 300:
        api.call("DELETE", f"{GAMMA}/observability/dashboards/compat-probe")
        return Found("none")
    return Found("required" if "module_required" in json.dumps(answer[1]) else refusal(answer))


class Studio:
    """A studio with no tool, made with the list of versions, else with one; deleted when the probes end."""
    BODY = {"name": "Compat probe", "contextPath": "/mcp/compat-probe", "specification": {"tools": []},
            "plans": [{"name": "Open", "security": {"type": "KEYLESS"}}]}

    def __init__(self, api):
        self.api, self.id, self.taken, self.answer = api, None, None, None
        for taken, keys in (("list", {"supportedProtocolVersions": [PROTOCOL]}), ("single", {"protocolVersion": PROTOCOL})):
            self.answer = api.call("POST", f"{AIM}/mcp-studios", {**self.BODY, **keys})
            if self.answer[0] < 300:
                self.id, self.taken = self.answer[1]["id"], taken
                return

    def delete(self):
        if self.id:   # as the seeder deletes one, through Gamma, which keeps a record of it beside the API
            self.api.call("DELETE", f"{AIM}/mcp-proxies/{self.id}")


def studio(api):
    if api.studio is None:
        api.studio = Studio(api)
    return api.studio


@probe(versions.APIM, "mcp-protocol-versions")
def mcp_protocol_versions(api):
    """What a studio is made with, and whether an MCP proxy has the route that sets its lists: the two must agree."""
    made = studio(api)
    route = served(api.call("PUT", f"{AIM}/mcp-proxies/{UNKNOWN}/protocol-versions", {}))
    if made.taken is None:
        return Found(f"no studio: {refusal(made.answer)}")
    if (made.taken == "list") != route:
        return Found(f"a studio takes the {made.taken}, and the route of an MCP proxy is {'' if route else 'not '}served")
    return Found(made.taken)


@probe(versions.APIM, "studio-keyless-name", reads={"KEYLESS read back as KEY_LESS", "KEYLESS read back as KEYLESS"})
def studio_keyless_name(api):
    made = studio(api)
    if not made.id:
        return Found(f"no studio: {refusal(made.answer)}")
    _, plans = api.call("GET", f"{V2}/apis/{made.id}/plans?page=1&perPage=10")
    types = sorted({(p.get("security") or {}).get("type") for p in (plans or {}).get("data", [])} - {None})
    return Found(f"KEYLESS read back as {', '.join(types) or 'nothing'}")


@probe(versions.APIM, "proxy-update-in-place", reads={"taken", "HTTP 404", "HTTP 405", "HTTP 501"})
def proxy_update_in_place(api):
    """The update of the entity of an MCP API, as a control switches its steps: a 404, 405 or 501 makes the seed import the
    API again (apim.UNSUPPORTED). Measured on the studio, an MCP API the probes make."""
    made = studio(api)
    if not made.id:
        return Found(f"no studio: {refusal(made.answer)}")
    _, live = api.call("GET", f"{V2}/apis/{made.id}")
    answer = api.call("PUT", f"{V2}/apis/{made.id}", {**live, "description": "updated by a probe"})
    return Found("taken" if answer[0] < 300 else f"HTTP {answer[0]}", "" if answer[0] < 300 else refusal(answer))


# The Gamma lists the seeder reads with gamma.ManagementApi.rows
LISTS = [f"{AIM}{path}" for path in ("/approval-rules", "/catalog/agents", "/catalog/api-tools/sources", "/catalog/mcp-servers",
                                     "/catalog/sources", "/catalog/mcp-tools", "/catalog/items", "/mcp-proxies", "/providers",
                                     "/ai-workspaces", "/auth-profiles", "/workflow-tools", "/catalog/skills", "/catalog/models",
                                     "/catalog/knowledge")] + [f"{AUTHZ}/entities", f"{AUTHZ}/policies",
                                                               f"{GAMMA}/observability/dashboards"]


@probe(versions.APIM, "gamma-lists", reads={"each read"})
def gamma_lists(api):
    """Each Gamma list the seeder reads, from page 0 and, when it refuses it, from page 1: the shape must be one gamma.page_of
    reads. The notes give the shapes and the lists that refuse page 0, for the docs."""
    unread, refuse, shapes = [], [], {}
    for path in LISTS:
        first = api.call("GET", f"{path}?page=0&perPage=1")
        answer = api.call("GET", f"{path}?page=1&perPage=1") if first[0] == 400 else first
        name = path.split("/environments/DEFAULT/", 1)[1].replace("modules/", "")
        if not served(answer):
            continue   # a list this version does not serve: the row of what needs it says so
        if first[0] == 400:
            refuse.append(name)
        body = answer[1]
        key = next((k for k in ("content", "data", "items") if isinstance(body, dict) and k in body), None)
        if answer[0] != 200 or key is None:
            unread.append(f"{name} ({refusal(answer)})")
            continue
        total = next((k for k in ("totalElements", "total") if k in body), None) or \
            ("pagination.totalCount" if "totalCount" in (body.get("pagination") or {}) else "no total")
        shapes.setdefault(f"{key} with {total}", []).append(name)
    notes = "; ".join(f"{shape}: {', '.join(names)}" for shape, names in shapes.items())
    notes += f"; page 0 refused by {', '.join(refuse)}" if refuse else ""
    return Found("unread: " + ", ".join(unread) if unread else "each read", notes)


@probe(versions.APIM, "authz-read-back", reads={"as written", "without active"})
def authz_read_back(api):
    """An access entity as the platform gives it back: the seeder takes an absent `active` as true."""
    entity = {"entityId": "compat-probe", "entityType": "MCPServer", "kind": "RESOURCE", "attributes": {"_displayName": "Probe"},
              "parents": [], "targets": [], "source": "local"}
    api.call("POST", f"{AUTHZ}/entities/_bulk", {"entities": [entity]})
    _, held = api.call("GET", f"{AUTHZ}/entities/compat-probe")
    api.call("DELETE", f"{AUTHZ}/entities/compat-probe")
    missing = sorted({"kind", "source", "active"} - set(held if isinstance(held, dict) else {}))
    if not missing:
        return Found("as written")
    return Found("without active" if missing == ["active"] else "without " + ", ".join(missing))


# The routes the seeder calls, as "METHOD base path", with an unknown id for each id. BEHAVIOR_ROUTES are those of a behavior of
# the tables, each served by every image or by none where its probe runs; ROUTES the others, which every image must serve. Two
# routes are in neither, because amp-demo lacks them and the seeder calls them only on the shape that has them: the update of
# the models of a provider in a workspace (a component that names a provider of the catalog, which amp-demo does not take)
# and of one model of a provider (a model with an id).
BASES = {"aim": AIM, "authz": AUTHZ, "gamma": GAMMA, "automation": "/automation/organizations/DEFAULT/environments/DEFAULT"}
BEHAVIOR_ROUTES = {
    "agent-application": """POST aim /catalog/agents/X/application, GET aim /catalog/agents/X/application,
        PUT aim /catalog/agents/X/application/identity, DELETE aim /catalog/agents/X/application/identity,
        GET aim /catalog/agents/X/access, POST aim /catalog/agents/X/access/_restore""",
    "governance": """GET aim /approval-settings, PUT aim /approval-settings, GET aim /approval-rules, POST aim /approval-rules,
        PUT aim /approval-rules/X, DELETE aim /approval-rules/X, GET aim /compliance/activations,
        POST aim /compliance/activations, DELETE aim /compliance/activations/X, PUT aim /compliance/agents/X/metadata,
        GET aim /compliance/agents/X/assessment, POST aim /compliance/agents/X/controls/X/attestation""",
    "studio-human-approval": "PUT aim /mcp-proxies/X/human-approval",
    "authz-schema-documents": "GET authz /schemas, PUT authz /schemas/X",
}
ROUTES = """GET aim /mcp-proxies, GET aim /mcp-proxies/X, DELETE aim /mcp-proxies/X, GET aim /mcp-proxies/X/plans,
    GET aim /mcp-proxies/X/subscriptions, POST aim /mcp-proxies/X/subscriptions, POST aim /mcp-proxies/X/subscriptions/X/_accept,
    POST aim /mcp-proxies/X/subscriptions/X/_close, POST aim /mcp-studios, PUT aim /mcp-studios/X/specification,
    PUT aim /mcp-studios/X/fga, GET aim /ai-workspaces, POST aim /ai-workspaces, GET aim /ai-workspaces/X,
    PUT aim /ai-workspaces/X, DELETE aim /ai-workspaces/X, GET aim /ai-workspaces/X/models,
    POST aim /ai-workspaces/X/models/_bulk, DELETE aim /ai-workspaces/X/models/X/X, GET aim /ai-workspaces/X/router,
    PUT aim /ai-workspaces/X/router, GET aim /ai-workspaces/X/analytics, PUT aim /ai-workspaces/X/analytics,
    GET aim /ai-workspaces/X/budgets, POST aim /ai-workspaces/X/budgets, PUT aim /ai-workspaces/X/budgets/X,
    GET aim /ai-workspaces/X/users, POST aim /ai-workspaces/X/users, PUT aim /ai-workspaces/X/users/X,
    GET aim /ai-workspaces/X/users/X/api-key, POST aim /ai-workspaces/X/_deploy, GET aim /providers, POST aim /providers,
    PUT aim /providers/X, DELETE aim /providers/X, GET aim /auth-profiles, POST aim /auth-profiles, PUT aim /auth-profiles/X,
    DELETE aim /auth-profiles/X, GET aim /workflow-tools, POST aim /workflow-tools, GET aim /workflow-tools/X,
    POST aim /workflow-tools/validate, POST aim /workflow-tools/X/publish, DELETE aim /workflow-tools/X,
    GET aim /catalog/sources, POST aim /catalog/sources, DELETE aim /catalog/sources/X,
    POST aim /catalog/sources/X/imports/mcp-server, GET aim /catalog/items, GET aim /catalog/agents, POST aim /catalog/agents,
    PUT aim /catalog/agents/X, DELETE aim /catalog/agents/X, GET aim /catalog/skills, POST aim /catalog/skills,
    DELETE aim /catalog/skills/X, GET aim /catalog/knowledge, POST aim /catalog/knowledge, PUT aim /catalog/knowledge/X,
    DELETE aim /catalog/knowledge/X, GET aim /catalog/models, POST aim /catalog/models, PUT aim /catalog/models/X,
    DELETE aim /catalog/models/X, GET aim /catalog/mcp-servers, PUT aim /catalog/mcp-servers/X,
    DELETE aim /catalog/mcp-servers/X, GET aim /catalog/mcp-tools, POST aim /catalog/mcp-tools/_importFromApis,
    GET aim /catalog/api-tools/sources, POST aim /catalog/api-tools/_generateCapabilities, GET authz /policies,
    POST authz /policies, PUT authz /policies/X, DELETE authz /policies/X, GET authz /entities, POST authz /entities/_bulk,
    DELETE authz /entities/X, GET authz /schema, PUT authz /schema, POST authz /schema/validate, POST authz /users/sync,
    GET authz /users/sync, GET gamma /observability/dashboards, POST gamma /observability/dashboards,
    GET gamma /observability/dashboards/X, PUT gamma /observability/dashboards/X, DELETE gamma /observability/dashboards/X,
    PUT automation /aim/mcp-proxies, GET automation /aim/mcp-proxies/X, DELETE automation /aim/mcp-proxies/X"""


def routes_of(text):
    """(method, path, name) of each route of a list, its unknown ids filled."""
    found = []
    for route in (part.split() for part in text.split(",")):
        method, base, path = route
        found.append((method, BASES[base] + path.replace("/X", f"/{UNKNOWN}"), f"{method} {base} {path}"))
    return found


def lacking(api, text):
    """The routes of a list this platform does not serve."""
    return [name for method, path, name in routes_of(text)
            if not served(api.call(method, path, None if method in ("GET", "DELETE") else {}))]


def by_routes(api, name, yes, no):
    """The variant of a behavior whose routes are served or not: `yes` with all of them, `no` with none."""
    missing, every = lacking(api, BEHAVIOR_ROUTES[name]), routes_of(BEHAVIOR_ROUTES[name])
    if not missing:
        return Found(yes)
    if len(missing) == len(every):
        return Found(no)
    return Found(f"partly served, without {', '.join(missing)}")


@probe(versions.APIM, "routes", reads={"each served"})
def routes(api):
    missing = lacking(api, ROUTES)
    return Found("lacking: " + ", ".join(missing) if missing else "each served")


# --- Access Management ------------------------------------------------------------------

def am_domain(api, name):
    answer = api.call("POST", f"{AM_ENV}/domains", {"name": name, "description": "", "dataPlaneId": "default"})
    if answer[0] >= 300:
        raise RuntimeError(f"a domain: {refusal(answer)}")
    return f"{AM_ENV}/domains/{answer[1]['id']}"


@probe(versions.AM, "am-version-stated", reads={"by its reporters"})
def am_version_stated(api):
    """versions.read takes the version of Access Management from its reporters: they must state the node's."""
    _, rows = api.call("GET", "/management/platform/plugins/reporters")
    stated = sorted({str(r.get("version")).split(" ")[0] for r in rows or [] if isinstance(r, dict)})
    return Found("by its reporters" if stated == [api.version] else f"reporters state {', '.join(stated) or 'nothing'}")


@probe(versions.AM, "am-protected-resource-delete", reads={"taken with its type"})
def am_protected_resource_delete(api):
    """The seed deletes an MCP server with `type=MCP_SERVER` (am.remove); the notes say what a delete without it gets."""
    domain = am_domain(api, "compat-resources")
    try:
        results = []
        for query in ("?type=MCP_SERVER", ""):
            n = len(results)
            answer = api.call("POST", f"{domain}/protected-resources",
                              {"name": f"Compat {n}", "description": "", "type": "MCP_SERVER",
                               "resourceIdentifiers": [f"https://compat{n}.test"], "clientId": f"compat{n}",
                               "clientSecret": "compat-probe-secret", "features": []})
            if answer[0] >= 300:
                return Found(f"create: {refusal(answer)}")
            results.append(api.call("DELETE", f"{domain}/protected-resources/{answer[1]['id']}{query}")[0])
        return Found("taken with its type" if results[0] < 300 else f"HTTP {results[0]} with its type",
                     f"without it: HTTP {results[1]}")
    finally:
        api.call("DELETE", domain)


@probe(versions.AM, "am-group-member", reads={"taken in the path"})
def am_group_member(api):
    """The seed adds a member to a group with the user in the path (am.py); the notes give what a body on /members gets."""
    domain = am_domain(api, "compat-groups")
    try:
        _, user = api.call("POST", f"{domain}/users", {"username": "compat", "password": "Compat-probe-1", "firstName": "C",
                                                        "lastName": "P", "email": "compat@example.test", "preRegistration": False})
        _, group = api.call("POST", f"{domain}/groups", {"name": "compat", "description": "compat"})
        body = api.call("POST", f"{domain}/groups/{group['id']}/members", {"members": [user["id"]]})[0]
        status = api.call("POST", f"{domain}/groups/{group['id']}/members/{user['id']}")[0]
        return Found("taken in the path" if status < 300 else f"HTTP {status} in the path", f"a body on /members: HTTP {body}")
    finally:
        api.call("DELETE", domain)


@probe(versions.AM, "am-scopes-shape", reads={"data with totalCount"})
def am_scopes_shape(api):
    """The scopes of a domain, as am.py reads them, by pages of SCOPES_PER_PAGE."""
    domain = am_domain(api, "compat-scopes")
    try:
        _, body = api.call("GET", f"{domain}/scopes?page=0&size=50")
        if isinstance(body, list):
            return Found("a bare list")
        key = next((k for k in ("data", "content") if k in (body or {})), "no rows")
        return Found(f"{key} with {'totalCount' if 'totalCount' in (body or {}) else 'no total'}")
    finally:
        api.call("DELETE", domain)


# --- Measuring --------------------------------------------------------------------------

def expected(product, version, name):
    """What the tables give `version` for a behavior, or the shapes the seeder reads."""
    if name in READS:
        return READS[name]
    said = seeded(product, version)
    if said != "seeded":
        return {f"({said})"}
    try:
        return {versions.variant(name)}
    except versions.SeedError as e:   # a release older than the behavior's first row
        return {f"({e})"}


def modules(api):
    """The ids of the Gamma modules the platform lists: none when it lists none, or answers no list."""
    status, body = api.call("GET", "/gamma/organizations/DEFAULT/modules")
    return {m.get("id") for m in body if isinstance(m, dict)} if status == 200 and isinstance(body, list) else set()


def seeded(product, version):
    """Whether a seed takes this version, and what it says when it does not."""
    versions._found.clear()
    try:
        with contextlib.redirect_stdout(io.StringIO()):   # the line a seed logs for its version
            versions.note(product, [version])
        return "seeded"
    except versions.SeedError as e:
        return str(e)


def connect(stack):
    if stack.product == versions.APIM:
        api = Client(stack.port, basic("admin", "admin"))
        wait(lambda: api.call("GET", "/management/organizations/DEFAULT/console")[0] == 200, "the management API")
        started = time.time()   # a 404 of the list of modules is the platform's word once it lasts, as gamma.require_modules
        wait(lambda: api.call("GET", "/gamma/organizations/DEFAULT/modules")[0] == 200 or time.time() > started + gamma.UNSERVED,
             "Gamma")
        # the routes of a module answer a moment after the list of modules names it: policies are in every version of authz
        wait(lambda: served(api.call("GET", f"{AUTHZ}/policies?page=1&perPage=1")) or "authz" not in modules(api),
             "the routes of the Gamma modules")
        time.sleep(5)
        return api
    login = Client(stack.port, basic("admin", "adminadmin"))
    wait(lambda: login.call("POST", "/management/auth/token")[0] == 200, "the management API")
    return Client(stack.port, "Bearer " + login.call("POST", "/management/auth/token")[1]["access_token"])


def measure(image, keep=False):
    stack = Stack(image)
    if stack.product == versions.APIM and not license_key():
        raise RuntimeError("GRAVITEE_LICENSE is set neither in the environment nor in the lab's .env: Gamma needs it")
    try:
        stack.start(reuse=keep)
        api = connect(stack)
        api.version = stack.node()
        if not api.version:
            raise RuntimeError("the image states no version on its technical API")
        said = seeded(stack.product, api.version)
        found = {"image": image, "product": stack.product, "version": api.version,
                 "probes": {"version": {"measured": said, "expected": ["seeded"], "notes": "", "holds": said == "seeded"}}}
        try:
            for name, function in PROBES[stack.product]:
                if stack.product == versions.APIM and name != "gamma-modules" and \
                        found["probes"]["gamma-modules"]["measured"] != "served":
                    found["probes"][name] = {"measured": "not measured", "expected": [], "notes": "Gamma is not filled here",
                                             "holds": True}
                    continue
                try:
                    got = function(api)
                except Exception as e:  # noqa: BLE001 - a probe that breaks says so and the others run
                    got = Found(f"error: {e}")
                wanted = expected(stack.product, api.version, name)
                found["probes"][name] = {"measured": got.measured, "expected": sorted(wanted), "notes": got.notes,
                                         "holds": got.measured in wanted}
        finally:
            if api.studio:
                api.studio.delete()
        if keep:
            found["address"] = f"http://127.0.0.1:{stack.port}"
        return found
    finally:
        if not keep:
            stack.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("images", nargs="+")
    parser.add_argument("--json", action="store_true", help="one JSON line per image")
    parser.add_argument("--keep", action="store_true", help="leave the stack running, and reuse one that runs")
    args = parser.parse_args(argv)
    worst = 0
    for image in args.images:
        try:
            found = measure(image, args.keep)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"image": image, "error": str(e)}) if args.json else f"{image}: {e}", flush=True)
            worst = 2
            continue
        worst = max(worst, 0 if all(p["holds"] for p in found["probes"].values()) else 1)
        if args.json:
            print(json.dumps(found), flush=True)
            continue
        print(f"{image}: {found['product']} {found['version']}" + (f" at {found['address']}" if args.keep else ""))
        for name, p in found["probes"].items():
            line = f"  {'ok' if p['holds'] else 'DIFFERS':7} {name}: {p['measured']}"
            line += "" if p["holds"] else f" (the seeder expects {' or '.join(p['expected'])})"
            print(line + (f"\n            {p['notes']}" if p["notes"] else ""), flush=True)
    return worst


if __name__ == "__main__":
    sys.exit(main())
