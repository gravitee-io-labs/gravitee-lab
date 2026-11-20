"""The AI catalog of Agent Management, from seed/aim/: what the agents of a playground are made of.

    credentials/*.yaml    a credential of the vault: name, service and the secret
    providers/*.yaml      an AI provider with its declared models and their prices
    models/*.yaml         a model of the catalog: context window, capabilities, price
    mcp-servers/*.yaml    an MCP server, discovered from here and imported
    knowledge/*.yaml      an inline document
    skills/<name>/        a skill: SKILL.md and the files next to it, zipped here
    agents/*.yaml         an A2A agent, registered from its card, with the `metadata` the file gives it
    api-tools/*.yaml      the tools of an API of API Management that publishes an OpenAPI page
    workflow-tools/*.yaml a workflow tool: an Arazzo document over APIs of API Management, published as one MCP tool

An agent file may carry `metadata`, stored with the card: `riskClassification` (negligible, limited, moderate or
high) and any key the catalog keeps about an agent. The keys `identity` and `access` are read by aim_agents and ignored
here.

An api-tools file names the API, and the operations of its OpenAPI page that become tools. Without `operations` every
operation does, as the page gives it. With them only those do, in the order of the file; an operation the page does not
give stops the seed, and so does a page that reports errors. An entry is an operation id, or a mapping of the id with
what the file writes in place of what the page gave, to tailor the tool for a model:

    api: Orchid Orders API
    operations:
      - listOrders
      - id: createRefund
        description: ...         # description, inputSchema and annotations replace those of the generated definition
        inputSchema: {...}
        annotations: {...}

A workflow-tools file is what Gamma's "Import Arazzo specification" makes, then publishes:

    name: refund_order        # the name of the MCP tool: a studio takes only letters, digits, _ and -
    description: ...
    sources:                 # per sourceDescription of the document: its API, and the key the workflow sends it, if any
      - {source: orders_api, api: Orders API, headerName: X-Gravitee-Api-Key, apiKey: "${ORDERS_TOOL_KEY}"}
    arazzo: |                # the Arazzo document; a source is apim://api/<api:Orders API>
      ...
    canvas: |                # the canvas the builder draws (graphState), as the import writes it: without it the
      ...                    # builder shows the document alone

`<api:NAME>` stands for the id of that API of API Management, in the document and the canvas. Each source's key becomes a
credential named "<workflow> · <source>", as the console names the one it makes; a source without `apiKey` (a keyless plan)
has none. A workflow that changed is deleted with its
credentials and made again, as the tools of an API are: a studio keeps a copy of each tool it was made with.

A file may say `needs: NAME`: the seed skips it, and says so, while the environment variable NAME is empty. A provider of a
service that lists its models (Gemini, Anthropic) is tested against the service with its real key as it is created, so
a placeholder key cannot stand in for one.

Gamma has no upsert and keeps no name unique, so each object is looked up first, by the name, slug or entity id the file
gives it. What the seed finds and did not create is never adopted, changed or deleted: the seed stops and names it.

The platform keeps the fields it knows, adds its own and never gives a secret back, so what it stores cannot be compared
with what the seed sends. The record of an object keeps a fingerprint of what the seed sent last (`digest`): the same
fingerprint writes nothing. A provider is the exception: the platform keeps what it is sent, so it is compared with what
it holds. A credential's secret is sent when it is created and again when the file gives another (a key fixed or rotated in
the playground's settings): its record keeps the fingerprint of the secret, never the secret.
"""

import io
import os
import re
import zipfile

import yaml

from . import aim_proxies
from . import common
from .apim import digest_of
from .common import Api, SeedError, contains, excerpt, files, has_seed, load, log, owned, parallel, record, wait_until
from .gamma import CATALOG, Gamma

SECTION = "aim-catalog"
LISTED = 60   # seconds the catalog has to list an API that API Management has just made
# The folders of the seed it owns
FOLDERS = ("aim/credentials", "aim/providers", "aim/models", "aim/mcp-servers", "aim/knowledge", "aim/skills",
           "aim/agents", "aim/api-tools", "aim/workflow-tools")
# The Gamma modules it fills: seed.py checks that the platform serves them before it changes anything
GAMMA = ("aim",)
# What it records, dependents first
ORDER = ["workflow-tool", "workflow-credential", "api-tools", "agent", "skill", "knowledge", "mcp-server", "model", "provider", "credential"]
# What the Lab UI calls each kind when it asks whether to delete them: plural, ending in an s
LABELS = {"workflow-tool": "workflow tools", "workflow-credential": "workflow credentials", "api-tools": "API tools", "agent": "A2A agents", "skill": "skills", "knowledge": "knowledge documents",
          "mcp-server": "MCP servers", "model": "AI models", "provider": "AI providers", "credential": "credentials"}
PATHS = {"workflow-tool": "/workflow-tools", "workflow-credential": "/auth-profiles", "api-tools": "/catalog/sources", "agent": "/catalog/agents", "skill": "/catalog/skills",
         "knowledge": "/catalog/knowledge", "mcp-server": "/catalog/mcp-servers", "model": "/catalog/models",
         "provider": "/providers", "credential": "/auth-profiles"}
MANUAL = ("agent", "knowledge", "model")   # the kinds a manual source holds
# Where a credential may be used from: Gamma asks for at least one and only records them, so the seed allows them all
USAGE_SURFACES = ["LLM_PROXY", "AI_WORKSPACE", "AGENT", "MCP_PROXY", "WORKFLOW_TOOL"]
SLUG = re.compile(r"[a-z0-9_-]+")
MCP_VERSION = "2025-11-25"   # asked at the start: the server answers with the version it speaks
CARD_PATH = "/.well-known/agent-card.json"
RISKS = ("negligible", "limited", "moderate", "high")   # what the catalog accepts as an agent's risk classification
# What an operation of an api-tools file may write in place of what the OpenAPI page gave, and what each must be
TAILORED = {"description": ("text", lambda v: isinstance(v, str) and bool(v.strip())),
            "inputSchema": ("a JSON schema", lambda v: isinstance(v, dict)),
            "annotations": ("a mapping of hints", lambda v: isinstance(v, dict))}
API_ID = re.compile(r"<api:([^<>]+)>")   # an API of API Management, in a workflow's document and canvas
TOOL_NAME = re.compile(r"[a-zA-Z0-9_-]+")   # what a studio takes as the name of a tool: a workflow is named as its tool
# What the catalog keeps of an agent card: it drops the rest
CARD_FIELDS = ("name", "description", "url", "provider", "version", "documentationUrl", "capabilities",
               "defaultInputModes", "defaultOutputModes", "skills", "securitySchemes", "security")
# What the catalog keeps of what a server lists
MCP_FIELDS = {"tools": ("name", "description", "inputSchema", "outputSchema", "annotations"),
              "prompts": ("name", "description", "arguments"),
              "resources": ("uri", "name", "description", "mimeType")}


# --- What a seed file asks, and what the seed owns ------------------------------------------

def noun(kind):
    return LABELS[kind][:-1]


def say(kind, did, name):
    log(SECTION, f"✓ {noun(kind)[0].upper()}{noun(kind)[1:]} {did}: {name}")


def specs(folder, *keys):
    """The files of a folder of the seed, read, each with the keys it must have."""
    for path in files("aim", folder):
        spec = load(path)
        if spec.get("needs") and not os.getenv(spec["needs"]):
            log(SECTION, f"Skipped {spec.get('name', path.stem)}: {spec['needs']} is not set")
            continue
        missing = [key for key in keys if key not in spec]
        if missing:
            raise SeedError(f"{path.name}: {', '.join(missing)} missing")
        yield spec


def own(kind, rows, what, name):
    """The row of the seed among those that have the key it wants, with its record: (None, None) when there is none. A row
    it did not create stops the seed, which names it and leaves it alone, unless it is the one row an earlier seed was making
    under `name` when it lost the platform's answer (`common.making`)."""
    if not rows:
        return None, None
    ours = {x["id"]: x for x in common.created() if (x["product"], x["kind"]) == (SECTION, kind)}
    found = next((row for row in rows if row["id"] in ours), None)
    if found is None and len(rows) == 1 and common.adopted(SECTION, kind, name, rows[0]["id"]):
        return own(kind, rows, what, name)
    if found is None:
        raise SeedError(f"{what} exists and was not created by this playground: rename or delete it, the seed leaves it alone")
    return found, ours[found["id"]]


# --- Credentials ----------------------------------------------------------------------------

def find_credential(aim, name, with_record=False):
    """The credential of the seed with this name, None when there is none: Gamma allows several, so one the seed did not
    create is none of its business. With its record, `with_record`."""
    rows = [c for c in aim.rows(PATHS["credential"], CATALOG) if c["name"] == name]
    found = own("credential", rows, f"The credential '{name}'", name)
    return found if with_record else found[0]


def seed_credentials(aim):
    """A secret is never readable: the fingerprint of the one the seed sent last says whether the file gives another, which
    is then sent in an update of the credential. The providers that name it keep it, by its id."""
    for spec in specs("credentials", "name", "credentials"):
        name, secret = spec["name"], spec["credentials"]
        digest = digest_of({"credentials": secret, "service": spec.get("service")})
        found, ours = find_credential(aim, name, with_record=True)
        if found and ours.get("digest") == digest:
            say("credential", "up to date", name)
            continue
        service = {"serviceId": spec["service"]} if "service" in spec else {}
        if found:
            aim.put(f"{PATHS['credential']}/{found['id']}", json={"name": name, "credentials": secret, **service})
            record(SECTION, "credential", LABELS["credential"], name, found["id"], digest=digest)
            say("credential", "updated", name)
            continue
        body = {"name": name, "strategy": secret["type"].upper().replace("-", "_"), "credentials": secret,
                "allowedUsageSurfaces": USAGE_SURFACES, **service}
        with common.making(SECTION, "credential", LABELS["credential"], name, missing=True):
            created = aim.post(PATHS["credential"], json=body).json()
            record(SECTION, "credential", LABELS["credential"], name, created["id"], digest=digest)
        say("credential", "created", name)


# --- Providers and the models they declare --------------------------------------------------

def credential_id(aim, name):
    found = find_credential(aim, name)
    if found is None:
        raise SeedError(f"The credential '{name}' is not there: a provider uses it, give it a file in aim/credentials")
    return found["id"]


def provider_differs(found, body, models):
    """Whether the provider the platform holds is not the one the file describes."""
    connection, shown = body["connection"], found["connection"]
    return ((shown["type"], shown["targetUri"]) != (connection["type"], connection["targetUri"])
            or (found["name"], found["slug"], found["visibility"], found.get("authProfileId"))
            != (body["name"], body["slug"], body["visibility"], body.get("authProfileId"))
            or {m["name"] for m in found["models"]} != {m["name"] for m in models})


def sync_models(aim, provider, wanted):
    """A provider's save keeps the task type, summary and price of the models it has: those change one model at a time."""
    stored = {m["name"]: m for m in provider["models"]}
    for model in wanted:
        current = stored[model["name"]]
        if "id" not in current:
            continue   # a Gamma that keeps a name and a source id for a model: its price and summary are the catalog model's
        change = {key: model[key] for key in ("taskType", "summary") if key in model and model[key] != current.get(key)}
        if "pricingOverride" in model and not contains(current.get("pricingOverride", {}), model["pricingOverride"]):
            change["pricingOverride"] = model["pricingOverride"]
        if change:
            aim.put(f"{PATHS['provider']}/{provider['id']}/models/{current['id']}", json=change)
            log(SECTION, f"  ✓ Model updated: {provider['slug']}:{model['name']} ({', '.join(change)})")


def seed_providers(aim):
    """The models are declared, not scanned: the platform refuses to call a private address, and Ollama is on one."""
    for spec in specs("providers", "name", "slug", "connection", "models"):
        name, slug, models = spec["name"], spec["slug"], spec["models"]
        rows = [p for p in aim.rows(PATHS["provider"], CATALOG) if p["slug"] == slug or p["name"] == name]
        found, _ = own("provider", rows, f"The provider '{name}' ({slug})", name)
        body = {"name": name, "slug": slug, "visibility": spec.get("visibility", "RESTRICTED"), "connection": spec["connection"]}
        if "credential" in spec:
            body["authProfileId"] = credential_id(aim, spec["credential"])
        if found is None:
            with common.making(SECTION, "provider", LABELS["provider"], name, missing=True):
                found = aim.post(PATHS["provider"], json={**body, "models": models}).json()
                record(SECTION, "provider", LABELS["provider"], name, found["id"])
            say("provider", "created", name)
        elif provider_differs(found, body, models):
            found = aim.put(f"{PATHS['provider']}/{found['id']}", json={**body, "models": models}).json()
            say("provider", "updated", name)
        else:
            say("provider", "up to date", name)
        sync_models(aim, found, models)


# --- What a manual source holds: models, documents and agents -------------------------------

def manual_source(aim, name):
    """The source of what is made by hand. Gamma has a single one in the environment, named after the first item it held:
    `name` is for the seed to give it when there is none yet."""
    found = next(iter(aim.rows("/catalog/sources", CATALOG, sourceKind="manual")), None)
    return found or aim.post("/catalog/sources", json={"sourceKind": "manual", "definition": {"type": "manual", "name": name}}).json()


def seed_item(aim, kind, spec, name, body, source):
    """Creates the item when it is missing, and replaces it when what the seed sends changed. A PUT is a full replace, so it
    sends the file's `metadata` over what the platform holds, which Compliance also writes. The fingerprint is of the body
    alone: metadata that someone else wrote does not make the item differ."""
    slug = spec["entityId"]
    if not SLUG.fullmatch(slug):
        raise SeedError(f"{slug}: an entity id is made of lower case letters, digits, - and _")
    body = {"sourceId": source["id"], **body}
    wanted = f"{kind}.{slug}"
    rows = [i for i in aim.rows(PATHS[kind], CATALOG) if i["entityId"] == wanted]
    found, ours = own(kind, rows, f"The {noun(kind)} '{wanted}'", name)
    digest = digest_of(body)
    if found is None:
        with common.making(SECTION, kind, LABELS[kind], name, missing=True, source=spec["source"]):
            found, did = aim.post(PATHS[kind], json={**body, "entityId": slug}).json(), "created"
            record(SECTION, kind, LABELS[kind], name, found["id"], source=spec["source"], digest=digest)
    elif ours.get("digest") != digest:
        metadata = {**(found.get("metadata") or {}), **body.get("metadata", {})}
        aim.put(f"{PATHS[kind]}/{found['id']}", json={**body, **({"metadata": metadata} if metadata else {})})
        record(SECTION, kind, LABELS[kind], name, found["id"], source=spec["source"], digest=digest)
        did = "updated"
    else:
        did = "up to date"
    say(kind, did, name)


def seed_models(aim):
    for spec in specs("models", "entityId", "source", "definition"):
        seed_item(aim, "model", spec, spec["definition"]["name"], {"definition": spec["definition"]},
                  manual_source(aim, spec["source"]))


def seed_knowledge(aim):
    for spec in specs("knowledge", "entityId", "source", "name", "description", "content"):
        source = manual_source(aim, spec["source"])
        definition = {"name": spec["name"], "description": spec["description"], "type": "document.inline", "content": spec["content"]}
        # What the Console reads to fill the row of the list
        metadata = {"family": "documents", "providerLabel": "Inline", "sourceName": source["definition"]["name"], "origin": "inline",
                    "resourceShape": "individual", "documentType": "Individual document", "technicalLabel": spec["entityId"],
                    "delivery": "direct-resource", "location": "Inline content", "inlineBytes": str(len(spec["content"].encode()))}
        seed_item(aim, "knowledge", spec, spec["name"], {"definition": definition, "metadata": metadata}, source)


def catalog_card(card):
    """An agent card as the catalog stores it. A card of A2A 1.0 has no `url`: its address is in supportedInterfaces, and
    its security in securityRequirements."""
    card = dict(card)
    interfaces = card.get("supportedInterfaces") or []
    if not card.get("url") and interfaces:
        card["url"] = next((i["url"] for i in interfaces if i.get("protocolBinding", "").upper() == "JSONRPC"), interfaces[0]["url"])
    if "security" not in card and card.get("securityRequirements"):
        card["security"] = card["securityRequirements"]
    return {key: value for key, value in card.items() if key in CARD_FIELDS}


def fetch_card(url):
    try:
        return Api(url).get(CARD_PATH).json()
    except (SeedError, ValueError) as e:
        raise SeedError(f"The agent card of {url}: {e}")


def agent_metadata(spec):
    """The `metadata` of an agent file, checked before anything is asked of the platform, which would refuse a risk
    classification it does not know without saying which file wrote it."""
    metadata = spec.get("metadata")
    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise SeedError(f"The agent '{spec['entityId']}': metadata is a mapping of keys to values")
    if "riskClassification" in metadata and metadata["riskClassification"] not in RISKS:
        risk = excerpt(metadata["riskClassification"], 40)
        raise SeedError(f"The agent '{spec['entityId']}': riskClassification {risk!r} is not one of {', '.join(RISKS)}")
    return metadata


def seed_agents(aim):
    """The platform reads a card itself only from a public address, so the seed reads it and registers what it says. The
    file's `metadata` is stored with the card. `identity` and `access` are for aim_agents, which gives the agent what it
    needs to sign in and to call a server once the catalog holds it."""
    asked = list(specs("agents", "entityId", "source", "url"))
    metadata_of = [agent_metadata(spec) for spec in asked]
    cards = parallel(lambda spec: catalog_card(fetch_card(spec["url"])), asked)   # the cards are read together
    for spec, metadata, definition in zip(asked, metadata_of, cards):
        body = {"definition": definition, **({"metadata": metadata} if metadata else {})}
        seed_item(aim, "agent", spec, definition["name"], body, manual_source(aim, spec["source"]))


# --- MCP servers: discovered from here, because the platform's own discovery refuses private addresses ---------

class McpServer:
    """Just enough of the MCP protocol, over streamable HTTP, to list what a server offers."""

    def __init__(self, url):
        self.url, self.api = url, Api(url, SECTION)
        self.api.session.headers["Accept"] = "application/json, text/event-stream"
        self.headers = {"MCP-Protocol-Version": MCP_VERSION}

    def call(self, method, params=None, notification=False):
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            body["id"] = 1
        answer = self.api.post("", json=body, headers=self.headers, what=f"MCP {method} at {self.url}")
        if "Mcp-Session-Id" in answer.headers:
            self.headers["Mcp-Session-Id"] = answer.headers["Mcp-Session-Id"]
        if notification:
            return None
        payload = aim_proxies.jsonrpc(answer)
        if not payload or "result" not in payload:
            raise SeedError(f"MCP {method} at {self.url}: no result in {excerpt(payload or answer.text, 200)}")
        return payload["result"]

    def listing(self, key):
        """Every item of tools, prompts or resources: a server may page them."""
        found, cursor = [], None
        while True:
            result = self.call(f"{key}/list", {"cursor": cursor} if cursor else None)
            found += [{k: v for k, v in item.items() if k in MCP_FIELDS[key] and v is not None} for item in result.get(key, [])]
            cursor = result.get("nextCursor")
            if not cursor:
                return found


def discover(url):
    """What a server exposes, as POST /catalog/sources/_discover would answer: what the import takes in `discoverResult`."""
    server = McpServer(url)
    init = server.call("initialize", {"protocolVersion": MCP_VERSION, "capabilities": {},
                                      "clientInfo": {"name": "gravitee-lab-seed", "version": "1"}})
    server.headers["MCP-Protocol-Version"] = init["protocolVersion"]
    server.call("notifications/initialized", notification=True)
    capabilities = init.get("capabilities", {})
    found = {"serverInfo": {key: init["serverInfo"][key] for key in ("name", "version") if key in init["serverInfo"]},
             "protocolVersion": init["protocolVersion"], "capabilities": capabilities,
             "tools": server.listing("tools"),
             "prompts": server.listing("prompts") if "prompts" in capabilities else [],
             "resources": server.listing("resources") if "resources" in capabilities else []}
    if init.get("instructions"):
        found["instructions"] = init["instructions"]
    return found


def mcp_source(aim, endpoint):
    """The source a server is registered under, one for each server: it is named after the endpoint."""
    sources = aim.rows("/catalog/sources", CATALOG, sourceKind="mcp.server")
    found = next((s for s in sources if s["definition"].get("name") == endpoint), None)
    if found:
        return found   # left by an import that failed: nothing is registered under it
    return aim.post("/catalog/sources", json={"sourceKind": "mcp.server", "definition": {"type": "mcp-gateway", "name": endpoint}}).json()


def import_server(aim, spec, discovered, found, digest):
    """An import replaces the endpoint, tools, prompts and resources of the server and keeps its id and its source."""
    source_id = found["sourceId"] if found else mcp_source(aim, spec["endpoint"])["id"]
    with common.making(SECTION, "mcp-server", LABELS["mcp-server"], spec["name"], missing=found is None, source=source_id):
        result = aim.post(f"/catalog/sources/{source_id}/imports/mcp-server",
                          json={"endpoint": spec["endpoint"], "transport": "http", "authType": "none", "discoverResult": discovered}).json()
        server = result["server"]
        record(SECTION, "mcp-server", LABELS["mcp-server"], spec["name"], server["id"], source=source_id, digest=digest)
    say("mcp-server", "imported", f"{spec['name']} ({result['toolCount']} tools, {result['promptCount']} prompts, "
                                  f"{result['resourceCount']} resources)")
    return server


def seed_mcp_servers(aim):
    """The entity id of the server is made of the name it gives itself, at its first import and for ever: the access
    policies are written for the one the file says, and the seed stops when the server says another."""
    asked = list(specs("mcp-servers", "name", "entityId", "endpoint", "description"))
    for spec, discovered in zip(asked, parallel(lambda spec: discover(spec["endpoint"]), asked)):   # what the servers say, asked together
        rows = [s for s in aim.rows(PATHS["mcp-server"], CATALOG)
                if s["entityId"] == spec["entityId"] or s.get("extensions", {}).get("endpoint") == spec["endpoint"]]
        found, ours = own("mcp-server", rows, f"The MCP server '{spec['name']}' ({spec['endpoint']})", spec["name"])
        digest = digest_of({"endpoint": spec["endpoint"], "discovered": discovered})   # the endpoint is stored with the import
        if found is None or ours.get("digest") != digest:
            found = import_server(aim, spec, discovered, found, digest)
        else:
            say("mcp-server", "up to date", spec["name"])
        if found["entityId"] != spec["entityId"]:
            raise SeedError(f"The MCP server calls itself '{discovered['serverInfo']['name']}', so its entity id is "
                            f"{found['entityId']} and not {spec['entityId']}: the access policies are written for the second")
        if found["extensions"].get("description") != spec["description"]:   # an import wipes it
            extensions = {**found["extensions"], "description": spec["description"]}
            aim.put(f"{PATHS['mcp-server']}/{found['id']}",
                    json={"sourceId": found["sourceId"], "definition": found["definition"], "extensions": extensions})
            say("mcp-server", "described", spec["name"])


# --- Skills ---------------------------------------------------------------------------------

def skill_files(folder):
    """The text files of a skill folder, by their path in it, and the name SKILL.md gives the skill."""
    texts = {path.relative_to(folder).as_posix(): path.read_text(encoding="utf-8") for path in sorted(folder.rglob("*"))
             if path.is_file() and not path.name.startswith(".")}
    head = re.match(r"---\n(.*?)\n---", texts.get("SKILL.md", ""), re.S)
    try:
        meta = common.parse_yaml(head.group(1), f"{folder.name}/SKILL.md") if head else None
    except (yaml.YAMLError, ValueError) as e:   # ValueError: a value that YAML reads as a date that is none
        raise SeedError(common.unreadable(f"{folder.name}/SKILL.md", e)) from None
    if not isinstance(meta, dict) or not meta.get("name") or not meta.get("description"):
        raise SeedError(f"{folder.name}/SKILL.md must start with a front matter that gives a name and a description")
    return texts, meta["name"]


def skill_package(name, texts):
    """The zip a skill is uploaded as: its files inside one folder, with fixed dates so the same files make the same zip."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        for path, text in texts.items():
            entry = zipfile.ZipInfo(f"{name}/{path}", date_time=(2026, 1, 1, 0, 0, 0))
            entry.external_attr = 0o644 << 16
            package.writestr(entry, text, compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


def seed_skills(aim):
    """A package cannot change in place: a skill whose files changed is deleted and uploaded again."""
    folder = common.SEED_DIR / "aim" / "skills"
    for path in sorted(p for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []:
        texts, name = skill_files(path)
        rows = [s for s in aim.rows(PATHS["skill"], CATALOG) if s["entityId"] == f"skill.{name}"]
        found, ours = own("skill", rows, f"The skill '{name}'", name)
        digest = digest_of(texts)
        if found and ours.get("digest") == digest:
            say("skill", "up to date", name)
            continue
        if found:
            aim.delete(f"{PATHS['skill']}/{found['id']}")
            common.forget(ours)   # gone: its upload is noted
        with common.making(SECTION, "skill", LABELS["skill"], name, missing=True):
            uploaded = aim.post(PATHS["skill"], files={"file": (f"{name}.zip", skill_package(name, texts), "application/zip")},
                                headers={"Content-Type": None}).json()
            record(SECTION, "skill", LABELS["skill"], name, uploaded["id"], digest=digest)
        say("skill", "replaced" if found else "uploaded", name)


# --- Tools made of an API of API Management --------------------------------------------------

def release_studios(aim):
    """A tool cannot be deleted while a studio holds it: the studios the seed made go first, with their records, and
    aim_proxies makes them again from the new tools, which is what a studio needs anyway, its tools being fixed when it
    is made."""
    for x in common.created():
        if (x["product"], x["kind"]) == (aim_proxies.SECTION, "studio"):
            aim.delete(f"/mcp-proxies/{x['id']}")
            common.forget(*aim_proxies.of_studio(x["id"]))   # gone, with its subscriptions: its create is noted
            log(SECTION, f"  ✓ Studio '{x['name']}' deleted: it is made again from the new tools")


def checked(name, operation, overrides):
    """What a file writes in place of an operation's generated tool, checked now: the platform validates a tool when it
    is saved, after the tools of the API were replaced."""
    unknown = [key for key in overrides if key not in TAILORED]
    if unknown:
        raise SeedError(f"The operation {operation} of '{name}' cannot say {', '.join(map(str, unknown))}: "
                        f"only id, {', '.join(TAILORED)}")
    for key, value in overrides.items():
        wanted, valid = TAILORED[key]
        if not valid(value):
            raise SeedError(f"The operation {operation} of '{name}': {key} must be {wanted}")
    return overrides


def operations_of(spec):
    """The operations an api-tools file lists, as [(operation id, what the file writes for it)] in the order of the
    file: None when it has no `operations`, which means every operation of the page."""
    if "operations" not in spec:
        return None
    name, listed = spec["api"], spec["operations"]
    if not isinstance(listed, list) or not listed:
        raise SeedError(f"The operations of '{name}' are a list of operation ids, and it lists none")
    asked = []
    for entry in listed:
        if isinstance(entry, dict):
            overrides = dict(entry)
            operation = overrides.pop("id", None)
        else:
            operation, overrides = entry, {}
        if not isinstance(operation, str) or not operation:
            raise SeedError(f"The operations of '{name}' are operation ids, or mappings with an id: "
                            f"{excerpt(entry, 60)}")
        if any(operation == done for done, _ in asked):
            raise SeedError(f"The operation {operation} of '{name}' is listed twice")
        asked.append((operation, checked(name, operation, overrides)))
    return asked


def tools_of(name, api_id, generated, operations):
    """What the import takes for an API, in the order of the file: {apiId, gatewayMapping, definition}, the definition
    without its output schema and with the words the file writes for a model. They are not saved after the import, because
    API Management 4.13.1 refuses every save of a tool made from an API ("parentId is required for this kind": such a tool has
    no parent server)."""
    problems = excerpt("; ".join(e["message"] for e in generated["errors"]))
    names = [t["toolDefinition"]["name"] for t in generated["tools"]]
    if operations is None:
        if generated["errors"] or not generated["tools"]:
            raise SeedError(f"The OpenAPI page of '{name}' gives no tools: {problems or 'it has no operation'}")
        chosen = [(tool, {}) for tool in generated["tools"]]
    else:
        if generated["errors"]:   # an error says little of which operation it concerns: none of them is trusted
            asked = ", ".join(operation for operation, _ in operations)
            raise SeedError(f"The OpenAPI page of '{name}' gives errors that can concern {asked}: {problems}")
        by_name = {}
        for tool_name, tool in zip(names, generated["tools"]):
            by_name.setdefault(tool_name, tool)
        missing = [f"'{operation}'" for operation, _ in operations if operation not in by_name]
        if missing:
            raise SeedError(f"The OpenAPI page of '{name}' has no operation {', '.join(missing)}: it has "
                            f"{', '.join(names) or 'none'}")
        chosen = [(by_name[operation], overrides) for operation, overrides in operations]
    return [{"apiId": api_id, "gatewayMapping": tool["gatewayMapping"],
             "definition": {**{k: v for k, v in tool["toolDefinition"].items() if k != "outputSchema"}, **overrides}}
            for tool, overrides in chosen]


def api_source(aim, name):
    """The API of API Management that the catalog offers as a source of tools. The catalog lists an API a moment after API
    Management has made it, so it is looked for until it is, within LISTED seconds."""
    found = []

    def look():
        api = next((a for a in aim.rows("/catalog/api-tools/sources", CATALOG, q=name) if a["name"] == name), None)
        if api is None:
            raise SeedError(f"{name} is not listed yet")
        found.append(api)
    try:
        wait_until(SECTION, f"the catalog to list the API '{name}'", look, timeout=LISTED)
    except SeedError:
        raise SeedError(f"No API named '{name}' can give tools: it must be a v4 HTTP proxy API that publishes an OpenAPI page") from None
    return found[0]


def seed_api_tools(aim):
    """The tools of an API that changed are deleted with their source and imported again, never edited in place. They
    are imported without the output schema the API's responses give them: a studio answers a tool made from an API with
    text only, and a client that checks the answer against a schema (the official SDKs, MCP Lens) refuses every
    answer of a tool that advertises one, "has an output schema but did not return structured content".

    What the file asks counts as a change of the API: another list of operations, or other words written for a tool,
    are a new fingerprint, and the tools are replaced. They could be edited in place, but a studio keeps a copy of each
    tool it was made with: a tool edited in place would leave the studio serving the old words, where a replacement
    releases the studios, which are made again from the new tools."""
    # Every file is read first: one that asks what cannot be done stops the seed before anything changes
    asked = [(spec["api"], operations_of(spec)) for spec in specs("api-tools", "api")]
    for name, operations in asked:
        api = api_source(aim, name)
        generated = aim.post("/catalog/api-tools/_generateCapabilities", json={"apiIds": [api["id"]]}).json()["data"][0]
        tools = tools_of(name, api["id"], generated, operations)
        digest = digest_of(tools)
        sources = aim.rows("/catalog/sources", CATALOG, sourceKind="apim.api")
        source = next((s for s in sources if s["definition"].get("apiId") == api["id"]), None)
        found, ours = own("api-tools", [source] if source else [], f"The tools of the API '{name}'", name)
        if found and ours.get("digest") == digest:
            say("api-tools", "up to date", name)
            continue
        if found:
            release_studios(aim)
            aim.delete(f"{PATHS['api-tools']}/{found['id']}")
            common.forget(ours)   # gone: its import is noted
        body = {"sources": [{"apiId": api["id"], "apiName": name}], "tools": tools}
        with common.making(SECTION, "api-tools", LABELS["api-tools"], name, missing=True):
            imported = aim.post("/catalog/mcp-tools/_importFromApis", json=body).json()["data"]
            record(SECTION, "api-tools", LABELS["api-tools"], name, imported[0]["sourceId"], digest=digest)
        say("api-tools", "replaced" if found else "imported", f"{name} ({len(imported)} tools)")


# --- Workflow tools -------------------------------------------------------------------------

def workflow_text(text, ids, name, what):
    """A workflow's document or canvas with each <api:NAME> replaced by the id of that API."""
    def api_id(match):
        if match.group(1) not in ids:
            raise SeedError(f"Workflow '{name}': the {what} names the API '{match.group(1)}', which no source of the file is")
        return ids[match.group(1)]
    return API_ID.sub(api_id, text)


def workflow_sources(spec, ids, document):
    """The sources of a workflow file, each one of the document's sourceDescriptions and read from the API it names."""
    name = spec["name"]
    try:
        parsed = common.parse_yaml(document, f"The document of the workflow '{name}'")
    except (yaml.YAMLError, ValueError) as e:   # ValueError: a value that YAML reads as a date that is none
        raise SeedError(common.unreadable(f"The document of the workflow '{name}'", e)) from None
    if not isinstance(parsed, dict):
        raise SeedError(f"Workflow '{name}': its arazzo is no Arazzo document")
    listed = parsed.get("sourceDescriptions") or []
    if not isinstance(listed, list) or not all(isinstance(s, dict) and isinstance(s.get("name"), str) for s in listed):
        raise SeedError(f"Workflow '{name}': sourceDescriptions is a list of sources, each with its name and url")
    described = {s["name"]: s.get("url") for s in listed}
    for source in spec["sources"]:
        if not isinstance(source, dict):
            raise SeedError(f"Workflow '{name}': each source is a mapping (source, api, headerName, apiKey)")
        missing = [key for key in ("source", "api", *(("headerName",) if source.get("apiKey") else ())) if not source.get(key)]
        if missing:
            raise SeedError(f"Workflow '{name}': a source lacks {', '.join(missing)}")
        if described.get(source["source"]) != f"apim://api/{ids[source['api']]}":
            raise SeedError(f"Workflow '{name}': the document has no source '{source['source']}' at apim://api/<api:{source['api']}>")
    return spec["sources"]


def seed_workflow_tools(aim):
    """A workflow tool is made as the console makes one from an Arazzo document: its credentials, then the tool with its
    document and canvas, validated, then published to the catalog, where a studio picks it as `{workflow: NAME}`."""
    for spec in specs("workflow-tools", "name", "sources", "arazzo"):
        name = spec["name"]
        if not TOOL_NAME.fullmatch(name):
            raise SeedError(f"Workflow '{name}': its name is the name of its MCP tool, which a studio refuses unless it is "
                            "made of letters, digits, _ and - (refund_order)")
        if not all(isinstance(spec.get(key, ""), str) for key in ("arazzo", "canvas")) or not isinstance(spec["sources"], list):
            raise SeedError(f"Workflow '{name}': arazzo and canvas are texts (a | block) and sources a list")
        ids = {s["api"]: api_source(aim, s["api"])["id"] for s in spec["sources"] if isinstance(s, dict) and s.get("api")}
        document = workflow_text(spec["arazzo"], ids, name, "document")
        canvas = workflow_text(spec["canvas"], ids, name, "canvas") if spec.get("canvas") else None
        sources = workflow_sources(spec, ids, document)
        digest = digest_of({"name": name, "description": spec.get("description"), "arazzo": document, "canvas": canvas,
                            "sources": sources})
        rows = [w for w in aim.rows(PATHS["workflow-tool"], CATALOG) if w["name"] == name]
        found, ours = own("workflow-tool", rows, f"The workflow tool '{name}'", name)
        if found and ours.get("digest") == digest:
            if found.get("status") != "ACTIVE":   # made, but its publication was not answered
                aim.post(f"{PATHS['workflow-tool']}/{found['id']}/publish", json={}, idempotent=True)
            say("workflow-tool", "up to date", name)
            continue
        checked = aim.post(f"{PATHS['workflow-tool']}/validate", json={"draftArazzo": document}).json()
        if not checked.get("valid"):
            issues = "; ".join(f"{i.get('path')}: {i.get('message')}" for i in checked.get("issues") or [])
            raise SeedError(f"Workflow '{name}': the platform refuses its document: {issues}")
        if found:
            release_studios(aim)
            drop_workflow(aim, found["id"])
        auth = [{"sourceRef": s["source"], "apiEntityId": ids[s["api"]],
                 **({"authProfileId": workflow_credential(aim, f"{name} · {s['source']}", s)} if s.get("apiKey") else {})}
                for s in sources]
        body = {"name": name, "draftArazzo": document, "sourceAuth": auth,
                **({"description": spec["description"]} if spec.get("description") else {}),
                **({"graphState": canvas} if canvas else {})}
        with common.making(SECTION, "workflow-tool", LABELS["workflow-tool"], name, missing=True):
            made = aim.post(PATHS["workflow-tool"], json=body).json()
            record(SECTION, "workflow-tool", LABELS["workflow-tool"], name, made["id"], digest=digest)
        aim.post(f"{PATHS['workflow-tool']}/{made['id']}/publish", json={}, idempotent=True)
        say("workflow-tool", "replaced" if found else "published", name)


def workflow_credential(aim, name, source):
    """The key a workflow sends one of its APIs, as the console keeps it: a credential of the product's own (SYSTEM). It is
    made with its workflow, which drops the earlier one, so one found under its name is left by a workflow that was not made
    (refused, or its answer lost): it is the seed's, or taken as the seed's when a lost answer made it, and goes, since the
    key it holds may have changed. One the seed did not create stops the seed."""
    rows = [c for c in aim.rows(PATHS["workflow-credential"], CATALOG) if c["name"] == name]
    found, ours = own("workflow-credential", rows, f"The workflow credential '{name}'", name)
    if found:
        aim.delete(f"{PATHS['workflow-credential']}/{found['id']}")
        common.forget(ours)
    body = {"name": name, "strategy": "API_KEY", "origin": "SYSTEM", "allowedUsageSurfaces": ["WORKFLOW_TOOL"],
            "credentials": {"type": "api-key", "headerName": source["headerName"], "value": source["apiKey"]}}
    with common.making(SECTION, "workflow-credential", LABELS["workflow-credential"], name, missing=True):
        made = aim.post(PATHS["workflow-credential"], json=body).json()
        record(SECTION, "workflow-credential", LABELS["workflow-credential"], name, made["id"])
    return made["id"]


def drop_workflow(aim, workflow_id):
    """A workflow of the seed and the credentials it was made with, each record dropped as its object goes."""
    workflow = aim.get(f"{PATHS['workflow-tool']}/{workflow_id}").json()
    aim.delete(f"{PATHS['workflow-tool']}/{workflow_id}")
    common.forget(*[x for x in common.created() if (x["product"], x["kind"], x["id"]) == (SECTION, "workflow-tool", workflow_id)])
    profiles = {a.get("authProfileId") for a in workflow.get("sourceAuth") or []}
    for x in [x for x in common.created() if (x["product"], x["kind"]) == (SECTION, "workflow-credential") and x["id"] in profiles]:
        aim.delete(f"{PATHS['workflow-credential']}/{x['id']}")
        common.forget(x)


# --- The module -----------------------------------------------------------------------------

def connect():
    gamma = Gamma()
    gamma.connect(SECTION, lambda: gamma.aim.get("/providers", params={"perPage": 1}), "AI Management")
    return gamma.aim


def seed():
    if not has_seed(*FOLDERS):
        return   # before any setting is read or any connection made: a playground that does not seed this is untouched
    aim = connect()
    for step in (seed_credentials, seed_providers, seed_models, seed_mcp_servers, seed_knowledge, seed_skills, seed_agents,
                 seed_api_tools, seed_workflow_tools):
        step(aim)


def delete(aim, x):
    if x["kind"] == "mcp-server":
        aim.delete(f"/catalog/sources/{x['source']}")   # a source takes its server, tools, prompts and resources with it
    else:
        aim.delete(f"{PATHS[x['kind']]}/{x['id']}")


def release_sources(aim, names):
    """The manual source, when it carries the name the seed gave it and holds nothing now. It is the one source of what is
    made by hand in the environment, so it stays while it holds an item the seed did not make."""
    for source in aim.rows("/catalog/sources", CATALOG, sourceKind="manual"):
        if source["definition"].get("name") in names:
            items = aim.get("/catalog/items", params={"sourceId": source["id"], "perPage": 1}).json()["data"]
            if not items:
                aim.delete(f"/catalog/sources/{source['id']}")


def remove(records):
    """Deletes what the records say the seed created, dependents first, and nothing else."""
    mine = owned(records, SECTION, ORDER)
    if not mine:
        return
    aim = connect()
    for x in mine:
        delete(aim, x)
        common.forget(x)
        log(SECTION, f"✓ Deleted {x['kind']} '{x['name']}'")
    release_sources(aim, {x["source"] for x in mine if x["kind"] in MANUAL})
