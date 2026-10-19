"""The AI catalog of Agent Management, from seed/aim/: what the agents of a playground are made of.

    credentials/*.yaml    a credential of the vault: name, service and the secret
    providers/*.yaml      an AI provider with its declared models and their prices
    models/*.yaml         a model of the catalog: context window, capabilities, price
    mcp-servers/*.yaml    an MCP server, discovered from here and imported
    knowledge/*.yaml      an inline document
    skills/<name>/        a skill: SKILL.md and the files next to it, zipped here
    agents/*.yaml         an A2A agent, registered from its card, with the `metadata` the file gives it
    api-tools/*.yaml      the tools of an API of API Management that publishes an OpenAPI page

An agent file may carry `metadata`, stored with the card: `riskClassification` (negligible, limited, moderate or
high) and any key the catalog keeps about an agent. The keys `identity` and `access` are read by aim_agents and ignored
here.

An api-tools file names the API, and the operations of its OpenAPI page that become tools. Without `operations` every
operation does, as the page gives it. With them only those do, in the order of the file; an operation the page does not
give stops the seed, and so does a page that reports errors. An entry is an operation id, or a mapping of the id with
what the file writes in place of what the page gave, to tailor the tool for a model:

    api: Harbor Orders API
    operations:
      - listOrders
      - id: createRefund
        description: ...         # description, inputSchema and annotations replace those of the generated definition;
        inputSchema: {...}       # referenceCost and referenceValue (USD, 0 or more) go in the extensions of the tool
        annotations: {...}

A file may say `needs: NAME`: the seed skips it, and says so, while the environment variable NAME is empty. A provider of a
service that lists its models (Gemini, Anthropic) is tested against the service with its real key as it is created, so
a placeholder key cannot stand in for one.

Gamma has no upsert and keeps no name unique, so each object is looked up first, by the name, slug or entity id the file
gives it. What the seed finds and did not create is never adopted, changed or deleted: the seed stops and names it.

The platform keeps the fields it knows, adds its own and never gives a secret back, so what it stores cannot be compared
with what the seed sends. The record of an object keeps a fingerprint of what the seed sent last (`digest`): the same
fingerprint writes nothing. A provider is the exception: the platform keeps what it is sent, so it is compared with what
it holds. A secret is sent once, when its credential is created.
"""

import hashlib
import io
import json
import os
import re
import zipfile

import requests
import yaml

from . import aim_proxies
from . import common
from .common import Api, SeedError, excerpt, files, has_seed, load, log, owned, parallel, record, wait_until
from .gamma import CATALOG, Gamma

SECTION = "aim-catalog"
LISTED = 60   # seconds the catalog has to list an API that API Management has just made
DEPENDS = ("apim",)   # the modules whose work this one reads on the platform (schedule.py)
# The folders of the seed it owns
FOLDERS = ("aim/credentials", "aim/providers", "aim/models", "aim/mcp-servers", "aim/knowledge", "aim/skills",
           "aim/agents", "aim/api-tools")
# The Gamma modules it fills: seed.py checks that the platform serves them before it changes anything
GAMMA = ("aim",)
# What it records, dependents first
ORDER = ["api-tools", "agent", "skill", "knowledge", "mcp-server", "model", "provider", "credential"]
# What the Lab UI calls each kind when it asks whether to delete them: plural, ending in an s
LABELS = {"api-tools": "API tools", "agent": "A2A agents", "skill": "skills", "knowledge": "knowledge documents",
          "mcp-server": "MCP servers", "model": "AI models", "provider": "AI providers", "credential": "credentials"}
PATHS = {"api-tools": "/catalog/sources", "agent": "/catalog/agents", "skill": "/catalog/skills",
         "knowledge": "/catalog/knowledge", "mcp-server": "/catalog/mcp-servers", "model": "/catalog/models",
         "provider": "/providers", "credential": "/auth-profiles"}
MANUAL = ("agent", "knowledge", "model")   # the kinds a manual source holds
# Where a credential may be used from: Gamma asks for at least one and only records them, so the seed allows them all
USAGE_SURFACES = ["LLM_PROXY", "AI_WORKSPACE", "AGENT", "MCP_PROXY", "WORKFLOW_TOOL"]
STRATEGIES = {"api-key": "API_KEY", "bearer": "BEARER", "basic": "BASIC", "oauth2": "OAUTH2", "service-account": "SERVICE_ACCOUNT"}
SLUG = re.compile(r"[a-z0-9_-]+")
MCP_VERSION = "2025-11-25"   # asked at the start: the server answers with the version it speaks
CARD_PATH = "/.well-known/agent-card.json"
RISKS = ("negligible", "limited", "moderate", "high")   # what the catalog accepts as an agent's risk classification
TOOL_PATH = "/catalog/mcp-tools"
# What an operation of an api-tools file may write in place of what the OpenAPI page gave, and what each must be: the
# keys of the tool's definition, then the references (USD) that the platform keeps in the extensions of the tool
TAILORED = {"description": ("text", lambda v: isinstance(v, str) and bool(v.strip())),
            "inputSchema": ("a JSON schema", lambda v: isinstance(v, dict)),
            "annotations": ("a mapping of hints", lambda v: isinstance(v, dict))}
REFERENCES = {key: ("a number, 0 or more", lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0)
              for key in ("referenceCost", "referenceValue")}
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


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def own(kind, rows, what):
    """The row of the seed among those that have the key it wants, with its record: (None, None) when there is none. A row
    it did not create stops the seed, which names it and leaves it alone."""
    if not rows:
        return None, None
    ours = {x["id"]: x for x in common.created() if (x["product"], x["kind"]) == (SECTION, kind)}
    found = next((row for row in rows if row["id"] in ours), None)
    if found is None:
        raise SeedError(f"{what} exists and was not created by this playground: rename or delete it, the seed leaves it alone")
    return found, ours[found["id"]]


def contains(actual, wanted):
    """Whether `actual` has everything `wanted` says: the platform adds its own fields and stamps to what it stores."""
    if isinstance(wanted, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in wanted.items())
    return actual == wanted


# --- Credentials ----------------------------------------------------------------------------

def find_credential(aim, name):
    """The credential of the seed with this name, None when there is none: Gamma allows several, so one the seed did not
    create is none of its business."""
    rows = [c for c in aim.rows(PATHS["credential"], CATALOG) if c["name"] == name]
    return own("credential", rows, f"The credential '{name}'")[0]


def seed_credentials(aim):
    for spec in specs("credentials", "name", "credentials"):
        name, secret = spec["name"], spec["credentials"]
        if find_credential(aim, name):
            say("credential", "up to date", name)   # a secret is never readable: it stays what it was when created
            continue
        body = {"name": name, "strategy": STRATEGIES[secret["type"]], "credentials": secret,
                "allowedUsageSurfaces": USAGE_SURFACES}
        if "service" in spec:
            body["serviceId"] = spec["service"]
        created = aim.post(PATHS["credential"], json=body).json()
        record(SECTION, "credential", LABELS["credential"], name, created["id"])
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
        found, _ = own("provider", rows, f"The provider '{name}' ({slug})")
        body = {"name": name, "slug": slug, "visibility": spec.get("visibility", "RESTRICTED"), "connection": spec["connection"]}
        if "credential" in spec:
            body["authProfileId"] = credential_id(aim, spec["credential"])
        if found is None:
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


def with_stored(body, found, keep):
    """What a replace sends: the body, and for each key of `keep` its mapping over the one the platform holds. A replace
    drops what it does not send, and the catalog's metadata of an agent is also written by Compliance."""
    sent = dict(body)
    for key in keep:
        merged = {**(found.get(key) or {}), **body.get(key, {})}
        if merged:
            sent[key] = merged
    return sent


def seed_item(aim, kind, spec, name, body, source, keep=()):
    """Creates the item when it is missing, and replaces it, a PUT being a full replace, when what the seed sends
    changed. The fingerprint is of the body alone: a key of `keep` that someone else wrote does not make the item
    differ."""
    slug = spec["entityId"]
    if not SLUG.fullmatch(slug):
        raise SeedError(f"{slug}: an entity id is made of lower case letters, digits, - and _")
    body = {"sourceId": source["id"], **body}
    wanted = f"{kind}.{slug}"
    rows = [i for i in aim.rows(PATHS[kind], CATALOG) if i["entityId"] == wanted]
    found, ours = own(kind, rows, f"The {noun(kind)} '{wanted}'")
    digest = fingerprint(body)
    if found is None:
        found, did = aim.post(PATHS[kind], json={**body, "entityId": slug}).json(), "created"
    elif ours.get("digest") != digest:
        aim.put(f"{PATHS[kind]}/{found['id']}", json=with_stored(body, found, keep))
        did = "updated"
    else:
        did = "up to date"
    if did != "up to date":
        record(SECTION, kind, LABELS[kind], name, found["id"], source=spec["source"], digest=digest)
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
    except (SeedError, requests.RequestException, ValueError) as e:
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
        seed_item(aim, "agent", spec, definition["name"], body, manual_source(aim, spec["source"]), keep=("metadata",))


# --- MCP servers: discovered from here, because the platform's own discovery refuses private addresses ---------

class McpServer:
    """Just enough of the MCP protocol, over streamable HTTP, to list what a server offers."""

    def __init__(self, url):
        self.url, self.api = url, Api(url)
        self.api.session.headers["Accept"] = "application/json, text/event-stream"
        self.headers = {"MCP-Protocol-Version": MCP_VERSION}

    def call(self, method, params=None, notification=False):
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            body["id"] = 1
        try:
            answer = self.api.send("POST", "", json=body, headers=self.headers, timeout=30)
        except requests.RequestException as e:
            raise SeedError(f"MCP {method} at {self.url}: {e}")
        if answer.status_code >= 400:
            raise SeedError(f"MCP {method} at {self.url}: HTTP {answer.status_code} {excerpt(answer.text, 200)}")
        if "Mcp-Session-Id" in answer.headers:
            self.headers["Mcp-Session-Id"] = answer.headers["Mcp-Session-Id"]
        if notification:
            return None
        if "json" in answer.headers.get("Content-Type", ""):
            payload = answer.json()
        else:   # an event stream: the answer is its data line
            payload = next((json.loads(line[5:]) for line in answer.text.splitlines() if line.startswith("data:")), None)
        if not payload or "result" not in payload:
            raise SeedError(f"MCP {method} at {self.url}: no result in {str(payload or answer.text)[:200]}")
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
        found, ours = own("mcp-server", rows, f"The MCP server '{spec['name']}' ({spec['endpoint']})")
        digest = fingerprint({"endpoint": spec["endpoint"], "discovered": discovered})   # the endpoint is stored with the import
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
    meta = yaml.safe_load(head.group(1)) if head else None
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
        found, ours = own("skill", rows, f"The skill '{name}'")
        digest = fingerprint(texts)
        if found and ours.get("digest") == digest:
            say("skill", "up to date", name)
            continue
        if found:
            aim.delete(f"{PATHS['skill']}/{found['id']}")
        uploaded = aim.post(PATHS["skill"], files={"file": (f"{name}.zip", skill_package(name, texts), "application/zip")},
                            headers={"Content-Type": None}).json()
        record(SECTION, "skill", LABELS["skill"], name, uploaded["id"], digest=digest)
        say("skill", "replaced" if found else "uploaded", name)


# --- Tools made of an API of API Management --------------------------------------------------

def release_studios(aim, released):
    """A tool cannot be deleted while a studio holds it: the studios the seed made go first, and aim_proxies makes them
    again from the new tools, which is what a studio needs anyway, its tools being fixed when it is made. `released`
    holds those already deleted in this run: a second API whose tools changed does not delete them again."""
    for x in common.created():
        if (x["product"], x["kind"]) == (aim_proxies.SECTION, "studio") and x["id"] not in released:
            aim.delete(f"/mcp-proxies/{x['id']}")
            released.add(x["id"])
            log(SECTION, f"  ✓ Studio '{x['name']}' deleted: it is made again from the new tools")


def checked(name, operation, overrides):
    """What a file writes in place of an operation's generated tool, checked now: the platform validates a tool when it
    is saved, after the tools of the API were replaced."""
    rules = {**TAILORED, **REFERENCES}
    unknown = [key for key in overrides if key not in rules]
    if unknown:
        raise SeedError(f"The operation {operation} of '{name}' cannot say {', '.join(map(str, unknown))}: "
                        f"only id, {', '.join(rules)}")
    for key, value in overrides.items():
        wanted, valid = rules[key]
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
    without its output schema. An operation with something written for it carries it as `tailoring`, which the import
    does not take."""
    problems = "; ".join(e["message"] for e in generated["errors"])
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
    tools = []
    for tool, overrides in chosen:
        entry = {"apiId": api_id, "gatewayMapping": tool["gatewayMapping"],
                 "definition": {k: v for k, v in tool["toolDefinition"].items() if k != "outputSchema"}}
        tools.append({**entry, "tailoring": overrides} if overrides else entry)
    return tools


def tailor(aim, name, imported, tools):
    """Writes into the imported tools what their file says. A save replaces the tool, so it sends back the tool as it
    was read: the source, the entity id, the definition, and the extensions, which hold the mapping to the API."""
    stored = {t["definition"]["name"]: t for t in imported}
    count = 0
    for tool in tools:
        overrides, operation = tool.get("tailoring"), tool["definition"]["name"]
        if not overrides:
            continue
        if operation not in stored:
            raise SeedError(f"The platform did not give back the tool {operation} of '{name}'")
        try:
            current = aim.get(f"{TOOL_PATH}/{stored[operation]['id']}").json()
            definition = {k: v for k, v in current["definition"].items() if k != "outputSchema"}
            definition.update({k: overrides[k] for k in TAILORED if k in overrides})
            extensions = {**(current.get("extensions") or {}),
                          **{k: overrides[k] for k in REFERENCES if k in overrides}}
            aim.put(f"{TOOL_PATH}/{current['id']}",
                    json={"sourceId": current["sourceId"], "entityId": current["entityId"], "definition": definition,
                          "extensions": extensions, "metadata": current.get("metadata") or {}})
        except SeedError as e:   # the platform's refusal does not say which tool it was
            raise SeedError(f"The tool {operation} of '{name}' could not be tailored: {e}") from e
        count += 1
    return count


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
    releases the studios, which are made again from the new tools. The words are written right after the import, and
    the tools are recorded without their fingerprint until they are: a seed that stops between the two replaces the
    tools on its next run."""
    # Every file is read first: one that asks what cannot be done stops the seed before anything changes
    asked = [(spec["api"], operations_of(spec)) for spec in specs("api-tools", "api")]
    released = set()
    for name, operations in asked:
        api = api_source(aim, name)
        generated = aim.post("/catalog/api-tools/_generateCapabilities", json={"apiIds": [api["id"]]}).json()["data"][0]
        tools = tools_of(name, api["id"], generated, operations)
        digest = fingerprint(tools)
        sources = aim.rows("/catalog/sources", CATALOG, sourceKind="apim.api")
        source = next((s for s in sources if s["definition"].get("apiId") == api["id"]), None)
        found, ours = own("api-tools", [source] if source else [], f"The tools of the API '{name}'")
        if found and ours.get("digest") == digest:
            say("api-tools", "up to date", name)
            continue
        if found:
            release_studios(aim, released)
            aim.delete(f"{PATHS['api-tools']}/{found['id']}")
        body = {"sources": [{"apiId": api["id"], "apiName": name}],
                "tools": [{k: v for k, v in t.items() if k != "tailoring"} for t in tools]}
        imported = aim.post("/catalog/mcp-tools/_importFromApis", json=body).json()["data"]
        record(SECTION, "api-tools", LABELS["api-tools"], name, imported[0]["sourceId"])
        tailored = tailor(aim, name, imported, tools)
        record(SECTION, "api-tools", LABELS["api-tools"], name, imported[0]["sourceId"], digest=digest)
        say("api-tools", "replaced" if found else "imported",
            f"{name} ({len(imported)} tools{f', {tailored} tailored' if tailored else ''})")


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
                 seed_api_tools):
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
        log(SECTION, f"✓ Deleted {x['kind']} '{x['name']}'")
    release_sources(aim, {x["source"] for x in mine if x["kind"] in MANUAL})
