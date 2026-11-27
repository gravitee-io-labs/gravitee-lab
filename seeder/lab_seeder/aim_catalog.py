"""The AI catalog of Agent Management, from seed/aim/: what the agents of a playground are made of.

    credentials/*.yaml    a credential of the vault: name, service and the secret
    providers/*.yaml      an AI provider with its declared models and their prices
    model-sources/*.yaml  a model source, what the console's "AI Models > Add models" makes: where its models are served
    models/*.yaml         a model of the catalog: context window, capabilities, price; in a model source or the manual source
    llm-proxies/*.yaml    the providers of an LLM proxy of seed/apim/apis, taken from model sources ("From catalog")
    mcp-servers/*.yaml    an MCP server, discovered from here and imported
    knowledge/*.yaml      an inline document
    skills/<name>/        a skill: SKILL.md and the files next to it, zipped here
    agents/*.yaml         an agent: an A2A agent registered from its card (`url`), or one entered by hand (`definition`)
    api-tools/*.yaml      the tools of an API of API Management that publishes an OpenAPI page
    workflow-tools/*.yaml a workflow tool: an Arazzo document over APIs of API Management, published as one MCP tool

An agent file may carry `metadata`, stored with the card: `riskClassification` (negligible, limited, moderate or
high) and any key the catalog keeps about an agent. Where the platform reads the card itself (versions.RELEASES,
agent-registration: card), it may name its `sponsor`, the user of API Management accountable for the agent apart from
its owner (the user the seed signs in as): `{user: NAME}`, with its `source` where two sources have that name.
It signs in once first, or seed/apim/users.yaml makes it. The keys `identity` and `access` are read by aim_agents and
ignored here.

An agent that serves no card is an entry made by hand, as Add agent > Custom makes it (origin CUSTOM), from its
`definition`: a `name`, a `description`, and any other key of an agent's definition (instructions, models, tools). The
platform keeps such entries where it reads cards itself (versions.RELEASES, agent-registration: card):

    entityId: triage-agent
    source: Support
    definition: {name: Triage agent, description: Sets the priority and the team of new helpdesk tickets}
    sponsor: {user: carol}
    metadata: {riskClassification: limited}

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

A model-sources file names a source of models and how they are reached; a models file whose `source` is the name of one
goes into it, any other into the manual source:

    name: Anthropic           # what the console shows
    kind: anthropic           # the source is llm.provider.<kind>: Gamma keeps one source of each kind in an environment
    baseUrl: ${ANTHROPIC_URL}
    format: ANTHROPIC         # OPEN_AI, OPEN_AI_COMPATIBLE, GEMINI, ANTHROPIC, BEDROCK or VERTEX_AI
    auth: {type: API_KEY, headerName: x-api-key}   # NONE, API_KEY (with its header), BEARER or SERVICE_ACCOUNT

Gamma keeps of an auth its type and the header of an API_KEY, drops any other key and refuses a header with any other type:
the seed refuses both before it writes anything.

An llm-proxies file gives an LLM proxy that seed/apim/apis imports its providers, in order, each from a model source:

    api: LLM Gateway          # the name of the API
    providers:
      - source: Anthropic     # a model source of the seed
        credentials: {apiKey: "${ANTHROPIC_API_KEY}"}   # apiKey for API_KEY, bearer for BEARER, none for NONE
        models:               # models of that source, by the entity id of their file in aim/models
          - {model: claude-haiku, aliases: [fast]}
          - {model: claude-sonnet, aliases: [smart], parametersOverride: '{"temperature": 0}'}
        governance: {aliasOnly: false, prefixPolicy: {policy: NO_PREFIX}, modelPattern: ""}   # what Gamma calls modelGovernance
        secondary: true       # the endpoint is a secondary one, which the failover of the API goes to

Gamma writes one endpoint for each provider, named after the id of its source, whatever routing name it is sent: a policy of
the API that names an endpoint (the candidates of ai-routing) names that id, or names no provider at all. Every endpoint it
writes is primary, and it keeps the `secondary` of an endpoint of that name: the seed sets the one the file gives.

A governance needs its `prefixPolicy` (PREFIXED_BOTH, PREFIXED_ALIAS, PREFIXED_MODEL or NO_PREFIX): Gamma fails on one
without. It keeps two shapes: with no `modelPattern` (none, or blank) only the models of the provider are served, by their
aliases alone when `aliasOnly`, and it gives the pattern back as ""; with one, any model that matches is served too, and it
drops `aliasOnly`, which the seed then refuses. The seed sends and compares the governance in the shape Gamma keeps, so a
second run writes nothing. Without `governance` Gamma's default applies (PREFIXED_BOTH), and the seed leaves what the
platform holds.

The API is apim.py's, which records it. Gamma keeps a copy of the providers of its own from their first save, with the
credentials in clear, which a delete of the API through API Management leaves behind: the seed records that copy under the
API's id (`llm-proxy`, counted as nothing, the API being counted already), and a removal deletes the proxy as the console
does, through Gamma, which deletes the API and its copy. apim.py then finds the API gone, which it counts as deleted.

A file may say `needs: NAME`: the seed skips it, and says so, while the environment variable NAME is empty. A model or a
provider of a model source that is skipped is skipped too, rather than seeded elsewhere. A provider of a
service that lists its models (Gemini, Anthropic) is tested against the service with its real key as it is created, so
a placeholder key cannot stand in for one.

Gamma has no upsert and keeps no name unique, so each object is looked up first, by the name, slug or entity id the file
gives it. What the seed finds and did not create is never adopted, changed or deleted: the seed stops and names it.

The platform keeps the fields it knows, adds its own and never gives a secret back, so what it stores cannot be compared
with what the seed sends. The record of an object keeps a fingerprint of what the seed sent last (`digest`): the same
fingerprint writes nothing. A provider and a model source are the exception: the platform keeps what it is sent, so they are
compared with what it holds. So are the providers of an LLM proxy, credentials included, with the endpoints of its API as
well: Gamma keeps a copy of the providers of its own, which an API deleted and imported again under its id (apim.py
replaces an LLM proxy so) leaves as it was, and a model source that changed reaches the endpoints only with the next save
of the providers. A credential's secret is sent when it is created and again when the file gives another (a key fixed or rotated in
the playground's settings): its record keeps the fingerprint of the secret, never the secret.
"""

import io
import json
import os
import re
import zipfile

import yaml

from . import aim_proxies
from . import common
from . import controls
from . import versions
from .apim import ENDED, SECTION as APIM, digest_of, user_id, user_of
from .common import Api, HttpError, SeedError, contains, excerpt, files, has_seed, load, log, owned, parallel, record, wait_until
from .gamma import CATALOG, Gamma

SECTION = "aim-catalog"
LISTED = 60   # seconds the catalog has to list an API that API Management has just made
# The folders of the seed it owns
FOLDERS = ("aim/credentials", "aim/providers", "aim/model-sources", "aim/models", "aim/llm-proxies", "aim/mcp-servers",
           "aim/knowledge", "aim/skills", "aim/agents", "aim/api-tools", "aim/workflow-tools")
# The Gamma modules it fills: seed.py checks that the platform serves them before it changes anything
GAMMA = ("aim",)
# What it records, dependents first. An LLM proxy goes first: it takes models of the catalog. A model source goes before the
# models: its delete takes its models with it, even one an LLM proxy uses, whose own delete the platform refuses (409), so the
# records of its models are dropped with it
ORDER = ["llm-proxy", "workflow-tool", "workflow-credential", "api-tools", "agent", "skill", "knowledge", "mcp-server",
         "model-source", "model", "provider", "credential"]
# What the Lab UI calls each kind when it asks whether to delete them: plural, ending in an s
LABELS = {"llm-proxy": "LLM proxies", "workflow-tool": "workflow tools", "workflow-credential": "workflow credentials", "api-tools": "API tools", "agent": "agents", "skill": "skills", "knowledge": "knowledge documents",
          "mcp-server": "MCP servers", "model-source": "model sources", "model": "AI models", "provider": "AI providers",
          "credential": "credentials"}
PATHS = {"llm-proxy": "/deployable", "workflow-tool": "/workflow-tools", "workflow-credential": "/auth-profiles", "api-tools": "/catalog/sources", "agent": "/catalog/agents", "skill": "/catalog/skills",
         "knowledge": "/catalog/knowledge", "mcp-server": "/catalog/mcp-servers", "model-source": "/catalog/sources",
         "model": "/catalog/models", "provider": "/providers", "credential": "/auth-profiles"}
# What a model source may say of how its models are reached, as Gamma names it
FORMATS = ("OPEN_AI", "OPEN_AI_COMPATIBLE", "GEMINI", "ANTHROPIC", "BEDROCK", "VERTEX_AI")
AUTHS = ("NONE", "API_KEY", "BEARER", "SERVICE_ACCOUNT")
PROVIDER_KEYS = ("source", "credentials", "models", "governance", "secondary")   # what a provider of an llm-proxies file says
MODEL_KEYS = ("model", "aliases", "parametersOverride")                          # and each of its models
GOVERNANCE_KEYS = ("aliasOnly", "prefixPolicy", "modelPattern")                  # and its governance
PREFIX_POLICIES = ("PREFIXED_BOTH", "PREFIXED_ALIAS", "PREFIXED_MODEL", "NO_PREFIX")
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


def specs(folder, *keys, quiet=False):
    """The files of a folder of the seed, read, each with the keys it must have. A file skipped is said, unless `quiet`."""
    for path in files("aim", folder):
        spec = load(path)
        if spec.get("needs") and not os.getenv(spec["needs"]):
            if not quiet:
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


def seed_item(aim, kind, spec, name, body, source, **extra):
    """Creates the item when it is missing, and replaces it when what the seed sends changed. A PUT is a full replace, so it
    sends the file's `metadata` over what the platform holds, which Compliance also writes. The fingerprint is of the body
    alone: metadata that someone else wrote does not make the item differ. The body names the source, so an item whose
    source changed is moved by the same PUT, and keeps its id: an LLM proxy that uses a model still finds it. `extra` goes
    into the record."""
    slug = spec["entityId"]
    if not SLUG.fullmatch(slug):
        raise SeedError(f"{slug}: an entity id is made of lower case letters, digits, - and _")
    body = {"sourceId": source["id"], **body}
    wanted = f"{kind}.{slug}"
    rows = [i for i in aim.rows(PATHS[kind], CATALOG) if i["entityId"] == wanted]
    found, ours = own(kind, rows, f"The {noun(kind)} '{wanted}'", name)
    digest = digest_of(body)
    if found is None:
        with common.making(SECTION, kind, LABELS[kind], name, missing=True, source=spec["source"], **extra):
            found, did = aim.post(PATHS[kind], json={**body, "entityId": slug}).json(), "created"
            record(SECTION, kind, LABELS[kind], name, found["id"], source=spec["source"], digest=digest, **extra)
    elif ours.get("digest") != digest:
        metadata = {**(found.get("metadata") or {}), **body.get("metadata", {})}
        aim.put(f"{PATHS[kind]}/{found['id']}", json={**body, **({"metadata": metadata} if metadata else {})})
        record(SECTION, kind, LABELS[kind], name, found["id"], source=spec["source"], digest=digest, **extra)
        did = "updated"
    else:
        did = "up to date"
    say(kind, did, name)


def seed_models(aim):
    """A model whose `source` is the name of a model source of the seed goes into it, and its record says which
    (`modelSource`); any other goes into the manual source."""
    sources, skipped = model_sources()
    for spec in specs("models", "entityId", "source", "definition"):
        name = spec["definition"]["name"]
        if spec["source"] in skipped:
            log(SECTION, f"Skipped {name}: its model source {spec['source']} is skipped")
        elif spec["source"] not in sources:
            seed_item(aim, "model", spec, name, {"definition": spec["definition"]}, manual_source(aim, spec["source"]))
        else:
            source = existing_source(aim, sources[spec["source"]])
            seed_item(aim, "model", spec, name, {"definition": spec["definition"]}, source, modelSource=source["id"])


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


def seed_agents(aim, users):
    """The platform reads a card itself only from a public address, so the seed reads it and registers what it says. The
    file's `metadata` is stored with the card. `identity` and `access` are for aim_agents, which gives the agent what it
    needs to sign in and to call a server once the catalog holds it. `users`: the organization's Management API, where a
    sponsor is looked up."""
    asked = [agent_spec(spec) for spec in specs("agents", "entityId", "source")]
    metadata_of = [agent_metadata(spec) for spec in asked]
    # The cards are read together
    cards = parallel(lambda spec: fetch_card(spec["url"]) if "url" in spec else None, asked)
    from_card = versions.variant("agent-registration") == "card"
    for spec, metadata, card in zip(asked, metadata_of, cards):
        if card is None:   # an entry made by hand, as Add agent > Custom makes it
            body = registered(spec["definition"], "CUSTOM", metadata)
        elif from_card:   # as Add agent > From agent card makes it: the agent keeps the card whole, with its endpoints
            definition = aim.post("/catalog/agents/_parseCard", json={"card": json.dumps(card)}, what="the agent card",
                                  idempotent=True).json()
            body = registered(definition, "AGENT_CARD", metadata)
        else:
            body = {"definition": catalog_card(card), **({"metadata": metadata} if metadata else {})}
        if "sponsor" in spec:
            body["sponsorId"] = user_id(users, *user_of(spec["sponsor"]))
        seed_item(aim, "agent", spec, body["definition"]["name"], body, manual_source(aim, spec["source"]))


def agent_spec(spec):
    """An agent file: a card to read (`url`) or a definition of its own (`definition`, with a `name`), one or the other,
    and a `sponsor` that is `{user: NAME}`, with its `source` where two sources have that name."""
    if ("url" in spec) == ("definition" in spec):
        raise SeedError(f"The agent '{spec['entityId']}': its file gives the url of its card or its definition, one of "
                        "them")
    definition = spec.get("definition")
    if "definition" in spec and not (isinstance(definition, dict) and isinstance(definition.get("name"), str)
                                     and definition["name"]):
        raise SeedError(f"The agent '{spec['entityId']}': its definition is a mapping with a name")
    if "sponsor" in spec and user_of(spec["sponsor"]) is None:
        raise SeedError(f"The agent '{spec['entityId']}': its sponsor is {{user: NAME}} or {{user: NAME, source: SOURCE}}")
    return spec


def registered(definition, origin, metadata):
    """An agent as Add agent makes it, by hand (origin CUSTOM) or from its card (AGENT_CARD): its risk classification is
    a field of its own, and the rest of the file's `metadata` is stored with it."""
    rest = {key: value for key, value in metadata.items() if key != "riskClassification"}
    risk = metadata.get("riskClassification")
    return {"definition": definition, "origin": origin, **({"riskClassification": risk.upper()} if risk else {}),
            **({"metadata": rest} if rest else {})}


# --- Model sources: where the models an LLM proxy takes from the catalog are served ---------

def model_sources():
    """The model sources the files give, by name, each with the name of its file (`file`), and the setting each one that is
    skipped (`needs`) waits for, by name: a model or a provider that names one is skipped too, rather than seeded elsewhere.
    check() reads them before any module changes anything."""
    found, skipped, kinds = {}, {}, {}
    for path in files("aim", "model-sources"):
        spec = load(path)
        name = spec.get("name", path.stem)
        if spec.get("needs") and not os.getenv(spec["needs"]):
            skipped[name] = spec["needs"]
            continue
        missing = [key for key in ("name", "kind", "baseUrl", "format", "auth") if key not in spec]
        if missing:
            raise SeedError(f"{path.name}: {', '.join(missing)} missing")
        auth = spec["auth"]
        if not isinstance(spec["kind"], str) or not SLUG.fullmatch(spec["kind"]):
            raise SeedError(f"The model source '{name}': its kind is made of lower case letters, digits, - and _ (anthropic)")
        if spec["format"] not in FORMATS:
            raise SeedError(f"The model source '{name}': format {excerpt(spec['format'], 40)!r} is not one of {', '.join(FORMATS)}")
        if not isinstance(auth, dict) or auth.get("type") not in AUTHS:
            raise SeedError(f"The model source '{name}': auth is {{type, headerName}}, its type one of {', '.join(AUTHS)}")
        unknown = [str(key) for key in auth if key not in ("type", "headerName")]
        if unknown:   # Gamma drops them, so the source would differ from the file at every run
            raise SeedError(f"The model source '{name}': auth cannot say {', '.join(unknown)}: only type, and headerName for API_KEY")
        if auth["type"] == "API_KEY" and not (isinstance(auth.get("headerName"), str) and auth["headerName"].strip()):
            raise SeedError(f"The model source '{name}': an API_KEY auth names the header of the key (headerName)")
        if auth["type"] != "API_KEY" and "headerName" in auth:
            raise SeedError(f"The model source '{name}': only an API_KEY auth names a header, a {auth['type']} one does not")
        if spec["kind"] in kinds:   # a create of a second source of a kind gives back the first one
            raise SeedError(f"The model sources '{kinds[spec['kind']]}' and '{name}' are both of kind {spec['kind']}: "
                            "Gamma keeps one source of each kind in an environment")
        kinds[spec["kind"]] = name
        found[name] = {**spec, "file": path.name}
    return found, skipped


def source_kind(spec):
    return f"llm.provider.{spec['kind']}"


def source_definition(spec):
    return {"type": "llm-provider", "name": spec["name"], "baseUrl": spec["baseUrl"], "format": spec["format"],
            "authMethod": spec["auth"]}


def find_model_source(aim, spec):
    """The source of the seed of that kind, with its record, (None, None) when there is none. It is looked up by its kind,
    not its name: Gamma keeps one source of each kind, and a create of another answers with the one it has, unchanged."""
    rows = aim.rows("/catalog/sources", CATALOG, sourceKind=source_kind(spec))
    return own("model-source", rows, f"The model source {source_kind(spec)} ({spec['name']})", spec["name"])


def existing_source(aim, spec):
    found, _ = find_model_source(aim, spec)
    if found is None:
        raise SeedError(f"The model source '{spec['name']}' is not there: aim/model-sources makes it")
    return found


def seed_model_sources(aim):
    """The platform keeps the definition it is sent, so it is compared with what it holds, and replaced whole (a PUT)
    when it differs. A source that changed reaches the endpoints of an LLM proxy with the next save of its providers,
    which seed_llm_proxies makes when they differ."""
    sources, skipped = model_sources()
    for name, needs in skipped.items():
        log(SECTION, f"Skipped {name}: {needs} is not set")
    for name, spec in sources.items():
        kind, definition, file = source_kind(spec), source_definition(spec), spec["file"]
        found, ours = find_model_source(aim, spec)
        if found is None:
            # The one this file or this name made before, of another kind, goes: its models are made again in the new one
            for x in [x for x in common.created() if (x["product"], x["kind"]) == (SECTION, "model-source")
                      and (x["name"] == name or x.get("file") == file)]:
                drop_model_source(aim, x)
            with common.making(SECTION, "model-source", LABELS["model-source"], name, missing=True, file=file):
                made = aim.post(PATHS["model-source"], json={"sourceKind": kind, "definition": definition}).json()
                record(SECTION, "model-source", LABELS["model-source"], name, made["id"], file=file)
            say("model-source", "created", name)
            continue
        if found["definition"] == definition:
            if ours.get("file") != file:   # recorded before the record named its file
                record(SECTION, "model-source", LABELS["model-source"], name, found["id"], file=file)
            say("model-source", "up to date", name)
            continue
        aim.put(f"{PATHS['model-source']}/{found['id']}", json={"sourceKind": kind, "definition": definition})
        if ours["name"] != name:   # renamed: its record goes under the new name
            common.forget(ours)
        record(SECTION, "model-source", LABELS["model-source"], name, found["id"], file=file)
        say("model-source", "updated", name)


def drop_model_source(aim, x):
    """A model source of the seed, deleted with its models (Gamma deletes them with it, even one an LLM proxy uses), each
    record dropped as its object goes."""
    aim.delete(f"{PATHS['model-source']}/{x['id']}")
    common.forget(x, *[m for m in common.created() if (m["product"], m.get("modelSource")) == (SECTION, x["id"])])


# --- LLM proxies: their providers, taken from the model sources -----------------------------

def checked_governance(api, source, governance):
    """The governance of a provider, which Gamma fails on without a prefix policy (a 500), and keeps without `aliasOnly`
    when it has a `modelPattern`."""
    where = f"The provider {source} of '{api}': governance"
    if not isinstance(governance, dict):
        raise SeedError(f"{where} is a mapping ({', '.join(GOVERNANCE_KEYS)})")
    unknown = [str(key) for key in governance if key not in GOVERNANCE_KEYS]
    if unknown:
        raise SeedError(f"{where} cannot say {', '.join(unknown)}: only {', '.join(GOVERNANCE_KEYS)}")
    prefix = governance.get("prefixPolicy")
    if not isinstance(prefix, dict) or list(prefix) != ["policy"] or prefix["policy"] not in PREFIX_POLICIES:
        raise SeedError(f"{where} needs its prefixPolicy, {{policy: one of {', '.join(PREFIX_POLICIES)}}}")
    if not isinstance(governance.get("aliasOnly", False), bool) or not isinstance(governance.get("modelPattern") or "", str):
        raise SeedError(f"{where}: aliasOnly is true or false, and modelPattern a text")
    if governance.get("aliasOnly") and (governance.get("modelPattern") or "").strip():
        raise SeedError(f"{where}: aliasOnly and a modelPattern do not go together, Gamma drops aliasOnly when it serves "
                        "the models that match a pattern")
    return governance


def stored_governance(governance):
    """A governance in the shape Gamma keeps it: a blank or missing pattern is "", and aliasOnly is false unless given."""
    pattern = governance.get("modelPattern") or ""
    return {"aliasOnly": bool(governance.get("aliasOnly", False)), "prefixPolicy": {"policy": governance["prefixPolicy"]["policy"]},
            "modelPattern": pattern if pattern.strip() else ""}


def provider_entries(spec, sources, skipped):
    """The providers of an llm-proxies file that the seed saves, checked: those of a model source that is skipped are left
    out. check() reads them before any module changes anything."""
    api, listed = spec["api"], spec["providers"]
    if not isinstance(listed, list) or not listed:
        raise SeedError(f"The LLM proxy '{api}': providers is a list of providers, and it lists none")
    kept = []
    for entry in listed:
        if not isinstance(entry, dict) or not isinstance(entry.get("source"), str):
            raise SeedError(f"The LLM proxy '{api}': each provider is a mapping with the source it takes its models from")
        source = entry["source"]
        unknown = [str(key) for key in entry if key not in PROVIDER_KEYS]
        if unknown:   # a routing name above all: Gamma names the endpoint after the source, whatever it is sent
            raise SeedError(f"The provider {source} of '{api}' cannot say {', '.join(unknown)}: only {', '.join(PROVIDER_KEYS)}")
        if any(source == done["source"] for done in kept):   # Gamma refuses a source declared twice
            raise SeedError(f"The provider {source} of '{api}' is listed twice: one provider takes every model of a source")
        if source in skipped:
            continue
        if source not in sources:
            raise SeedError(f"The provider {source} of '{api}' names no model source of aim/model-sources")
        credentials, models = entry.get("credentials") or {}, entry.get("models")
        if not isinstance(credentials, dict) or not all(isinstance(v, str) for v in credentials.values()):
            raise SeedError(f"The provider {source} of '{api}': credentials maps each key to a text (quote a variable)")
        if not isinstance(models, list) or not models or not all(isinstance(m, dict) and isinstance(m.get("model"), str) for m in models):
            raise SeedError(f"The provider {source} of '{api}': models is a list of {{model, aliases, parametersOverride}}")
        for model in models:
            unknown = [str(key) for key in model if key not in MODEL_KEYS]
            if unknown or not isinstance(model.get("aliases", []), list) or not isinstance(model.get("parametersOverride", ""), str):
                raise SeedError(f"The model {model['model']} of the provider {source} of '{api}': only a model, its aliases "
                                "(a list) and its parametersOverride (JSON text)")
        if "governance" in entry:
            checked_governance(api, source, entry["governance"])
        if "secondary" in entry and not isinstance(entry["secondary"], bool):
            raise SeedError(f"The provider {source} of '{api}': secondary is true or false")
        kept.append(entry)
    return kept


def llm_proxy(aim, name):
    """The LLM proxy that apim.py imported under this name, with the providers Gamma holds of it, read by the id of apim.py's
    record: a copy a console makes under the same name is left alone. It is not looked for in Gamma's list, whose count
    still holds, for seconds, an API that was just deleted (apim.py replaces an LLM proxy so), and which then refuses the
    page it announces (400)."""
    found = next((x["id"] for x in common.created() if (x["product"], x["kind"], x["name"]) == (APIM, "api", name)), None)
    try:
        if found:
            return aim.get(f"/llm-proxies/{found}").json()
    except HttpError as e:
        if e.status != 404:
            raise
    raise SeedError(f"No LLM proxy named '{name}' was imported by this seed: aim/llm-proxies gives its providers to an "
                    "LLM proxy of seed/apim/apis")


def model_fits(api, source, model, put):
    """Stops the seed on a model of a provider that aim/models does not put into the provider's source: `put` is the source
    it is in, None when the seed makes no such model."""
    if put is None:
        raise SeedError(f"The LLM proxy '{api}' uses the model {model}, which aim/models does not make")
    if put != source:
        raise SeedError(f"The LLM proxy '{api}' takes the model {model} from {source}, and its file in aim/models puts it in "
                        "another source")


def catalog_models(aim, sources, entries, api):
    """The catalog model of each model an entry names, by entity id: one of the seed's, in the source of the entry."""
    rows = {m["entityId"]: m for m in aim.rows(PATHS["model"], CATALOG)}
    found = {}
    for entry in entries:
        for model in entry["models"]:
            row = rows.get(f"model.{model['model']}")
            mine = row is not None and common.recorded(SECTION, "model", row["id"])
            put = next((n for n, s in sources.items() if s["id"] == row["sourceId"]), "another source") if mine else None
            model_fits(api, entry["source"], model["model"], put)
            found[model["model"]] = row
    return found


def wanted_providers(entries, sources, models):
    """What PUT /llm-proxies/{id}/providers takes, in the order of the file, the governance in the shape Gamma keeps it. No
    routing name: Gamma names each endpoint after its source's id, whatever it is sent."""
    return [{"kind": "catalog", "catalogSourceId": sources[entry["source"]]["id"], "credentials": entry.get("credentials") or {},
             "models": [{"catalogId": models[m["model"]]["id"], "aliases": m.get("aliases") or [],
                         **({"parametersOverride": m["parametersOverride"]} if m.get("parametersOverride") is not None else {})}
                        for m in entry["models"]],
             **({"modelGovernance": stored_governance(entry["governance"])} if "governance" in entry else {})}
            for entry in entries]


def compared(provider, governance):
    """A provider as Gamma gives it back, with what the seed sends of it: a parametersOverride it has none of is left out,
    and the governance only when the file gives one."""
    return {"kind": provider.get("kind"), "catalogSourceId": provider.get("catalogSourceId"),
            "credentials": provider.get("credentials") or {},
            "models": [{"catalogId": m.get("catalogId"), "aliases": m.get("aliases") or [],
                        **({"parametersOverride": m["parametersOverride"]} if m.get("parametersOverride") is not None else {})}
                       for m in provider.get("models") or []],
            **({"modelGovernance": provider.get("modelGovernance")} if governance else {})}


def endpoints_of(api):
    return [endpoint for group in api.get("endpointGroups") or [] for endpoint in group.get("endpoints") or []]


def endpoints_differ(api, wanted, sources, models, entries):
    """Whether the endpoints of the API are not those the providers make: one for each, named after its source's id, with
    the source's address, format and authentication, the credentials, and the name each model is asked under."""
    endpoints = endpoints_of(api)
    if [e["name"] for e in endpoints] != [p["catalogSourceId"] for p in wanted]:
        return True
    for endpoint, provider, entry in zip(endpoints, wanted, entries):
        source, configuration = sources[entry["source"]], endpoint.get("configuration") or {}
        auth = {**{k: v for k, v in source["auth"].items() if k in ("type", "headerName")}, **provider["credentials"]}
        queried = [models[m["model"]]["definition"].get("queryName") for m in entry["models"]]
        if ((configuration.get("provider"), configuration.get("target")) != (source["format"], source["baseUrl"])
                or not contains(configuration.get("authentication") or {}, auth)
                or [m.get("name") for m in configuration.get("models") or []] != queried):
            return True
    return False


def align_secondary(v2, api_id, entries, wanted):
    """Gamma writes every endpoint of a provider as a primary one, and keeps the `secondary` of an endpoint of that name:
    the one a provider of the file gives is written over the API as it was read. True when it was."""
    wanted = {p["catalogSourceId"]: entry["secondary"] for entry, p in zip(entries, wanted) if "secondary" in entry}
    if not wanted:
        return False
    api = v2.get(f"/apis/{api_id}").json()
    off = [e for e in endpoints_of(api) if e["name"] in wanted and bool(e.get("secondary")) != wanted[e["name"]]]
    if not off:
        return False
    for endpoint in off:
        endpoint["secondary"] = wanted[endpoint["name"]]
    v2.put(f"/apis/{api_id}", json=api)
    return True


def keep_proxy(name, api_id):
    """The record of an LLM proxy whose providers the seed saves: one for each API, under its name. It counts as nothing (the
    API is counted by apim.py's record)."""
    ours = [x for x in common.created() if (x["product"], x["kind"]) == (SECTION, "llm-proxy")]
    if any((x["name"], x["id"]) == (name, api_id) for x in ours):
        return
    common.forget(*[x for x in ours if x["id"] == api_id])   # renamed: one record under the new name
    record(SECTION, "llm-proxy", LABELS["llm-proxy"], name, api_id, count=0)


def seed_llm_proxies(gamma):
    """The providers of an LLM proxy are saved whole (PUT), and only when what Gamma holds of them, or the endpoints of the
    API, differ from what the file asks: Gamma rewrites the endpoints of the API from them and keeps its flows. What it
    writes, and an API that waits for a deployment, reach the gateway with one deployment. The proxy is recorded before its
    first save, which makes Gamma's copy of the providers, so that a removal deletes it."""
    sources, skipped = model_sources()
    aim, v2 = gamma.aim, gamma.v2
    for spec in specs("llm-proxies", "api", "providers"):
        name = spec["api"]
        entries = provider_entries(spec, sources, skipped)
        for source in [entry["source"] for entry in spec["providers"] if entry["source"] in skipped]:
            log(SECTION, f"Skipped the provider {source} of {name}: its model source is skipped")
        if not entries:
            log(SECTION, f"Skipped the providers of {name}: every model source they take from is skipped")
            continue
        live_sources = {entry["source"]: existing_source(aim, sources[entry["source"]]) for entry in entries}
        proxy = llm_proxy(aim, name)
        models = catalog_models(aim, live_sources, entries, name)
        keep_proxy(name, proxy["id"])
        wanted = wanted_providers(entries, live_sources, models)
        held = proxy.get("providers") or []
        api = v2.get(f"/apis/{proxy['id']}").json()
        same = (len(held) == len(wanted)
                and all(compared(h, "modelGovernance" in w) == compared(w, "modelGovernance" in w) for h, w in zip(held, wanted)))
        written = False
        if not same or endpoints_differ(api, wanted, sources, models, entries):
            aim.put(f"/llm-proxies/{proxy['id']}/providers", json={"providers": wanted})
            log(SECTION, f"✓ Providers of the LLM proxy {name} saved: {', '.join(e['source'] for e in entries)}")
            written = True
        else:
            log(SECTION, f"✓ Providers of the LLM proxy {name} up to date")
        written = align_secondary(v2, proxy["id"], entries, wanted) or written
        if written or api.get("deploymentState") != "DEPLOYED":
            v2.post(f"/apis/{proxy['id']}/deployments", json=aim_proxies.DEPLOYMENT, idempotent=True)
            controls.touched()
            log(SECTION, "  ✓ Deployed")


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
    return gamma


def check():
    """The files of the model sources, the models and the LLM proxies, read before any module changes anything: what Gamma
    would refuse of them comes after the objects they need were written."""
    if not has_seed(*FOLDERS):
        return
    agents = [agent_spec(spec) for spec in specs("agents", "entityId", quiet=True)]
    unkept = [spec["entityId"] for spec in agents if {"sponsor", "definition"} & set(spec)]
    if unkept and versions.variant("agent-registration") != "card":
        raise SeedError(f"The agent '{unkept[0]}' names a sponsor or is entered by hand: "
                        f"{versions.where('agent-registration', 'card')} keeps them, this platform registers an agent "
                        "from its card")
    sources, skipped = model_sources()
    models = {spec["entityId"]: spec["source"] for spec in specs("models", "entityId", "source", "definition", quiet=True)}
    for spec in specs("llm-proxies", "api", "providers", quiet=True):
        for entry in provider_entries(spec, sources, skipped):
            for model in entry["models"]:
                model_fits(spec["api"], entry["source"], model["model"], models.get(model["model"]))


def seed():
    if not has_seed(*FOLDERS):
        return   # before any setting is read or any connection made: a playground that does not seed this is untouched
    gamma = connect()
    for step in (seed_credentials, seed_providers, seed_model_sources, seed_models, seed_mcp_servers, seed_knowledge,
                 seed_skills, lambda aim: seed_agents(aim, gamma.org_v1), seed_api_tools, seed_workflow_tools):
        step(gamma.aim)
    seed_llm_proxies(gamma)   # an LLM proxy takes models of the catalog, and its API is deployed through API Management


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


def drop_llm_proxy(gamma, x):
    """An LLM proxy whose providers the seed saved, deleted as the console deletes one: through Gamma, which deletes the API
    (its plans closed) and its own copy of the providers. The API is stopped first, as apim.py stops one, and one that is gone
    already counts as deleted. The record of the API stays apim.py's, which then finds the API gone."""
    gamma.v2.post(f"/apis/{x['id']}/_stop", expect=ENDED, idempotent=True)
    gamma.aim.delete(f"{PATHS['llm-proxy']}/{x['id']}", params={"kind": "LLM"})
    common.forget(x)


def remove(records):
    """Deletes what the records say the seed created, dependents first, and nothing else."""
    mine = owned(records, SECTION, ORDER)
    if not mine:
        return
    gamma, gone = connect(), set()
    aim = gamma.aim
    for x in mine:
        if x.get("modelSource") in gone:
            log(SECTION, f"✓ Deleted {x['kind']} '{x['name']}' with its model source")
            continue   # forgotten with it
        if x["kind"] == "llm-proxy":
            drop_llm_proxy(gamma, x)
        elif x["kind"] == "model-source":   # its models go with it
            drop_model_source(aim, x)
            gone.add(x["id"])
        else:
            delete(aim, x)
            common.forget(x)
        log(SECTION, f"✓ Deleted {x['kind']} '{x['name']}'")
    release_sources(aim, {x["source"] for x in mine if x["kind"] in MANUAL and "modelSource" not in x})
