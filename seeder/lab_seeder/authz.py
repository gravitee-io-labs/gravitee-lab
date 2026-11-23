"""Authorization Management: what the policies are about, the policies, and a schema for the editor, from seed/authz/.

    seed/authz/entities.yaml      what the policies reason over, in one of two shapes (or both):
                                    `source` and `entities`: the data behind the tools, each {type, id, attributes,
                                      parents}, where an attribute `{ref: [Type, id]}` points at another entity
                                    `server`: the MCP server (by its name in the AI catalog), with its `tools`,
                                      `groups`, `users` and `agents`
                                    `sync: access-management`: the users, groups and agents of Access Management, as the
                                      Entities page's "Sync from Gravitee Access Management" brings them
                                    `catalog`: the MCP servers (with their tools), the models and the agents of the AI
                                      catalog, by name or slug, as the Entities page's "Import from AI Catalog" brings
                                      them: read only, with the id the catalog gives each, so that `entities` may name
                                      them as parents
    seed/authz/policies/*.yaml    name, status (DEPLOYED or DRAFT), kind (RESOURCE, the default, or GLOBAL), type (MCP,
                                  the default, MODEL, API, AGENT or CUSTOM: the page of the console that lists it) and
                                  text, in GAPL, of one policy, of any number of statements. A policy keeps the kind and
                                  the type it was made with, so a file that changes either makes the policy again
    seed/authz/schema.yaml        `schema`: the one schema document of the environment, for the editor
    seed/authz/schemas/*.yaml     name and text of a schema document, for a platform that keeps several: the editor
                                  reads them, decisions do not depend on them

A policy or a schema names the MCP server and an agent without knowing their ids: `<server>` stands for the slug the
catalog gave the server, which the seed reads back from the catalog, and `<agent:CLIENT_ID>` for the id of the agent
entity that the gateway makes of that client id (`AgentIdentity::"<agent:desk-copilot>"`). A policy names a synced group
by its name, `<group:NAME>` (`principal in Group::"<group:Support>"`): its id is the one Access Management gave it.

The first statement of a policy starts with an `@id`, the policy's own name, which Decisions shows as the reason of a
decision ("Forbidden by policy '...'"), and Decisions names the next ones `<name>#1`, `#2`. Only the first: an `@id` before
another statement leaves the policy in the console's Code view only.

What is there and the seed did not create is an error: it is never taken over. An entity someone imported from the same
catalog entry in the console is the one exception: it is what the seed would make, so it is left as it is and not recorded.
What the seed created is put back as the files say, a policy someone edited or undeployed as well as an entity whose
attributes changed; what is right is not saved.
The entities of a file are sent in one request, and only those that are missing or differ. They are recorded once for their
source, with their ids in the order they were saved, so that a removal deletes what points at another first.

A policy a control of seed/controls.yaml names (controls.py) is DEPLOYED while the mode protects (SEED_PROTECTIONS=on) and a
draft while it does not, whatever its file says and whoever deployed or took it out since: each run brings it back.
"""

import hashlib
import json
import re
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from urllib.parse import quote

from . import am, controls, versions
from .common import (Gone, HttpError, SeedError, adopted, contains, created, excerpt, files, forget, has_seed, items, load, log, making, notes, owned,
                     record, recorded, seed_file, slugify, unmade, wait_until)
from .gamma import CATALOG, LISTS, Gamma

SECTION = "authz"
# The folder of the seed it owns
FOLDERS = ("authz",)
# The Gamma modules it fills: seed.py checks that the platform serves them before it changes anything
GAMMA = ("authz",)
# What it records, dependents first. `entities` holds many entities in one record, `entity` one
ORDER = ["policy", "entities", "entity", "schema", "schema-document"]

SERVER = "<server>"
AGENT = re.compile(r"<agent:([^<>\s]+)>")
GROUP = re.compile(r"<group:([^<>]+)>")
# What `sync` may name, and how long the platform is given to finish it, in seconds
DIRECTORIES = ("access-management",)
# The source the platform gives what it synced from Access Management
SYNCED = "gravitee_am"
SYNC_WAIT = 60
# How a sync that did not succeed ends: the final statuses of a job of the platform other than SUCCESS
SYNC_FAILED = ("ERROR", "TIMEOUT")
# The kinds the platform gives a policy, and its types (AuthzPolicyType): the type is the page of the console that lists it
POLICY_KINDS = ("RESOURCE", "GLOBAL")
POLICY_TYPES = ("MCP", "MODEL", "API", "AGENT", "CUSTOM")
# What `catalog` may name, as the tabs of "Import from AI Catalog" do: the kind of the catalog's items and the entity type
# the console makes of each. The tools of an MCP server come with it
IMPORTS = {"mcp-servers": ("mcp-server", "MCPServer"), "models": ("model", "Model"), "agents": ("agent", "Agent")}
IMPORTED = "gravitee-catalog"   # the source the console gives what it imports from the catalog
PATHS = {"policy": "/policies/{}", "entity": "/entities/{}", "schema": "/schemas/{}", "schema-document": "/schema"}

ENTITIES = "seed/authz/entities.yaml"
SOURCE = "lab"          # where the platform says the entities of a file come from, when the file does not say
PRINCIPALS = ("User", "Group", "AgentIdentity")   # the entities that act; every other type is a resource
KINDS = ("PRINCIPAL", "RESOURCE")
LOOKUP = 50             # ids in one question about entities that another source may hold
# What the platform accepts (AuthzEntityIdFormat)
ENTITY_ID = re.compile(r"[A-Za-z0-9_:-]+(?:\.[A-Za-z0-9_:-]+)*")
ENTITY_TYPE = re.compile(r"[A-Za-z0-9_-]+(?:::[A-Za-z0-9_-]+)*")


def seed():
    if not has_seed(*FOLDERS):
        return   # before any setting is read or any connection made: a playground that does not seed this is untouched
    entities_file = seed_file("authz", "entities.yaml")
    spec = load(entities_file) if entities_file else {}
    gamma = Gamma()
    api = gamma.authz
    gamma.connect(SECTION, lambda: api.get("/policies", params={"page": 1, "perPage": 1}), "Authorization Management")
    slug = None
    if spec.get("server"):
        slug = server_slug(gamma, spec["server"])
        log(SECTION, f"MCP server '{spec['server']}' is '{slug}' in the catalog")
    schema_document(api, slug)
    named = files("authz", "schemas")
    if named and versions.variant("authz-schema-documents") == "one":
        log(SECTION, f"! {len(named)} schema documents of seed/authz/schemas skipped: this platform keeps one, "
                     "seed/authz/schema.yaml")
        named = []
    for path in named:
        schema(api, load(path), slug)
    for wanted in entities(spec, slug):
        entity(api, wanted)
    import_catalog(gamma, api, spec)   # before the entities of the file, which may name what it imports as parents
    put_entities(api, spec)
    groups = synced_groups(api, spec.get("sync"))
    policies = {p["entityId"]: p for p in api.rows("/policies", LISTS)}
    for path in files("authz", "policies"):
        policy(api, load(path), slug, policies, groups)


def server_slug(gamma, name):
    """The slug the catalog gave the MCP server: it is decided at the first import, so it is read, not written down. The
    server is the one that calls itself `name`, or whose slug is what the catalog makes of that name."""
    for server in gamma.aim.rows("/catalog/mcp-servers", CATALOG):
        called = ((server.get("definition") or {}).get("serverInfo") or {}).get("name")
        if called == name or server.get("slug") == slugify(name):
            return server["slug"]
    raise SeedError(f"The MCP server '{name}' is not in the AI catalog: the policies are about its tools")


def synced_groups(api, directory):
    """The ids of the groups by name, once Authorization Management has read the users, groups and agents of the directory
    `sync` names: the request the Entities page sends, then its end waited for. The principals of what seed/am made are
    recorded as the seed's, so that a removal deletes them: the platform keeps the principals of a domain that is gone."""
    if not directory:
        return {}
    if directory not in DIRECTORIES:
        raise SeedError(f"{ENTITIES}: sync is one of {', '.join(DIRECTORIES)}, not '{directory}'")
    mine = am_principals()

    def keep():
        """Records the principals of what seed/am made that Authorization Management holds, a group before its members,
        which a removal deletes first: also when the sync ended in an error after it upserted some."""
        held = {x["entityId"]: x for x in api.rows("/entities", LISTS, kind="PRINCIPAL", source=SYNCED)}
        brought = sorted((held[entity_id] for entity_id in mine if entity_id in held), key=lambda x: x["entityType"] != "Group")
        if brought:
            remember(SYNCED, brought)
        return held, brought
    try:
        sync(api)
    except SeedError:
        try:
            keep()
        except SeedError as e:   # the sync's own error is the one to say
            log(SECTION, f"! What the sync brought could not be read: {e}")
        raise
    held, brought = keep()
    missed = [entity_id for entity_id in mine if entity_id not in held]
    log(SECTION, f"✓ Access Management synced: {len(brought)} principals of this playground"
                 + (f", {len(missed)} not synced: {', '.join(missed)}" if missed else ""))
    # A group of the domain this seed made is known by the id Access Management gave it; another by its name, when one group has it
    groups, twice = {}, set()
    for found in held.values():
        name = (found.get("attributes") or {}).get("name")
        if found["entityType"] == "Group" and name:
            if name in groups:
                twice.add(name)
            groups[name] = found["entityId"]
    groups = {name: entity_id for name, entity_id in groups.items() if name not in twice}
    groups.update({x["name"]: x["id"] for x in created() if (x["product"], x["kind"]) == ("am", "group") and x["id"] in held})
    return groups


def sync(api):
    """Starts a sync of Access Management, as the Entities page does, and waits for its end. One that runs already (409) is
    waited for, then this one starts. The sync is a job of the platform, whose status is PENDING until it ends as SUCCESS,
    ERROR or TIMEOUT."""
    for _ in range(2):
        answer = api.post("/users/sync", expect=(202, 409, 503))
        if answer.status_code == 503:
            raise SeedError("Authorization Management cannot read Access Management: seed/am/gamma-link.yaml links Gamma to the "
                            "domain")
        job = (answer.json() or {}).get("jobId") if answer.status_code == 202 else None

        def ended():
            said = api.get("/users/sync").json()
            if job and said.get("jobId") not in (None, job):   # the status of an earlier sync, until this one is listed
                raise SeedError("the sync has not started")
            status = said.get("status")
            if status in SYNC_FAILED and answer.status_code == 202:
                raise Gone(f"The sync of Access Management into Authorization Management ended as {status}: "
                           f"{excerpt(said.get('error') or 'no reason given')}")
            if status != "SUCCESS" and status not in SYNC_FAILED:   # another sync that ended, well or not, lets this one start
                raise SeedError(f"the sync is {status or 'not started'}")
        wait_until(SECTION, "the sync of Access Management", ended, timeout=SYNC_WAIT)
        if answer.status_code == 202:
            return
    raise SeedError("The sync of Access Management into Authorization Management: another one kept running")


def am_principals():
    """The ids Authorization Management gives to what seed/am made, as its sync computes them: a group keeps its id, an agent
    takes the name UUID of its client id, a user the name UUID of "sourceId:externalId" (its sub: the id of its identity
    provider, which Access Management answers as `sourceId`, `source` being that provider's name), else its own id."""
    made = [x for x in created() if x["product"] == "am" and x["kind"] in ("group", "user", "agent")]
    ids = [x["id"] for x in made if x["kind"] == "group"]
    agents = {x["name"] for x in made if x["kind"] == "agent"}
    ids += [agent_id(spec["clientId"]) for spec in map(load, files("am", "applications"))
            if spec.get("type") == "AGENT" and spec.get("name") in agents]
    users = [x for x in made if x["kind"] == "user"]
    if users:
        manager = am.AccessManagement()
        manager.connect()
        for x in users:
            found = manager.api.get(f"{manager.env_url}/domains/{x['domain']}/users/{x['id']}", expect=(200, 404))
            if found.status_code == 404:   # deleted in the console: it has no principal of its own any more
                continue
            user = found.json()
            source, external = user.get("sourceId"), user.get("externalId")
            ids.append(agent_id(f"{source}:{external}") if source and external else user["id"])
    return ids


def fill(text, slug, what, groups=None):
    """A policy or a schema with its placeholders replaced: <server> by the slug of the MCP server, <agent:CLIENT_ID> by
    the id of the agent entity, <group:NAME> by the id of the synced group."""
    def group_id(found):
        if found.group(1) not in (groups or {}):
            raise SeedError(f"{what} names the group '{found.group(1)}', which the sync did not bring")
        return groups[found.group(1)]
    text = GROUP.sub(group_id, text)
    if SERVER in text:
        if not slug:
            raise SeedError(f"{what} names <server>, and {ENTITIES} has no server")
        text = text.replace(SERVER, slug)
    return AGENT.sub(lambda found: agent_id(found.group(1)), text)


def reason(body):
    """Why the platform refused a request, on one line: the message it gives, else what it sent."""
    try:
        said = json.loads(body)
    except ValueError:
        said = None
    return excerpt(said["message"] if isinstance(said, dict) and said.get("message") else body)


def explain(what, error):
    """The error of a request the platform refused (a policy it cannot read, a schema it cannot parse), on one line."""
    return SeedError(f"{what} refused by Authorization Management (HTTP {error.status}): {reason(error.body)}")


# --- Entities of the MCP server -------------------------------------------------------------

def agent_id(client_id):
    """The id of an agent entity: what the gateway makes of the client id in a token (Java's UUID.nameUUIDFromBytes). The sync
    of Access Management names a user the same way, from "sourceId:externalId"."""
    digest = bytearray(hashlib.md5(client_id.encode()).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def entity_body(entity_id, entity_type, kind, attributes, parents=()):
    return {"entityId": entity_id, "entityType": entity_type, "kind": kind, "attributes": attributes,
            "parents": [{"entityType": t, "entityId": i} for t, i in parents], "targets": []}


def entities(spec, slug):
    """What the policies are about when `server` names an MCP server, as the API takes it: a parent comes before the
    entities that name it."""
    if not spec.get("server"):
        return []
    out = [entity_body(slug, "MCPServer", "RESOURCE", {"_displayName": spec["server"]})]
    out += [entity_body(f"{slug}.{tool}", "MCPTool", "RESOURCE", {"_displayName": tool}, [("MCPServer", slug)])
            for tool in spec.get("tools", [])]
    out += [entity_body(group, "Group", "PRINCIPAL", {"_displayName": name}) for group, name in spec.get("groups", {}).items()]
    out += [entity_body(user["id"], "User", "PRINCIPAL", user["attributes"], [("Group", user["group"])] if user.get("group") else [])
            for user in spec.get("users", [])]
    out += [entity_body(agent_id(agent["clientId"]), "AgentIdentity", "PRINCIPAL",
                 {"clientId": agent["clientId"], **agent["attributes"]}) for agent in spec.get("agents", [])]
    return out


def as_wanted(current, wanted):
    parents = {(p["entityType"], p["entityId"]) for p in current["parents"]}
    return (current["entityType"] == wanted["entityType"] and current["attributes"] == wanted["attributes"]
            and parents == {(p["entityType"], p["entityId"]) for p in wanted["parents"]} and current.get("active", True))


def entity(api, wanted):
    """Saving an entity replaces the one with the same id, so what is there is looked at first."""
    entity_id = wanted["entityId"]
    found = api.get("/entities/" + quote(entity_id, safe=""), expect=(200, 404))
    current = found.json() if found.status_code == 200 else None
    if current and not recorded(SECTION, "entity", entity_id) and \
            not adopted(SECTION, "entity", entity_id, entity_id, type=wanted["entityType"]):
        raise SeedError(f"{wanted['entityType']} '{entity_id}' exists in Authorization Management and the seed did not "
                        "create it: it is left alone")
    if current and as_wanted(current, wanted):
        return
    with (nullcontext() if current else making(SECTION, "entity", "access subjects", entity_id, missing=True,
                                               type=wanted["entityType"])):
        api.post("/entities", json=wanted)
        if not current:
            record(SECTION, "entity", "access subjects", entity_id, entity_id, type=wanted["entityType"])
    log(SECTION, f"✓ {wanted['entityType']} '{entity_id}' {'updated' if current else 'created'}")


# --- Entities of the data -------------------------------------------------------------------

def as_text(value):
    """An id or a type as written in a file: a string, or a number YAML read."""
    if isinstance(value, bool):
        return None
    return str(value) if isinstance(value, (str, int)) and str(value) else None


def pair(value, where):
    """(type, id) of a `[Type, id]`."""
    if not (isinstance(value, (list, tuple)) and len(value) == 2 and all(as_text(v) for v in value)):
        raise SeedError(f"{where}: expected [Type, id]")
    return as_text(value[0]), as_text(value[1])


def reference(value, where):
    """An attribute as the engine takes it: `{ref: [Type, id]}` is a reference to that entity, wherever it stands in a
    list or a record."""
    if isinstance(value, dict):
        if list(value) == ["ref"]:
            entity_type, entity_id = pair(value["ref"], f"{where}, ref")
            return {"__entity": {"type": entity_type, "id": entity_id}}
        return {key: reference(v, where) for key, v in value.items()}
    if isinstance(value, list):
        return [reference(v, where) for v in value]
    return value


def pointed_at(value):
    """The (type, id) of every entity an attribute, once its references are made, points at."""
    if isinstance(value, dict):
        if list(value) == ["__entity"]:
            yield value["__entity"]["type"], value["__entity"]["id"]
        else:
            for v in value.values():
                yield from pointed_at(v)
    elif isinstance(value, list):
        for v in value:
            yield from pointed_at(v)


def data_entities(spec):
    """The source of the entities of `entities:`, and the entities as the bulk API takes them, in dependency order: an entity
    comes after its parents and after what its attributes point at."""
    source = spec.get("source") or SOURCE
    if not isinstance(source, str) or len(source) > 64:
        raise SeedError(f"{ENTITIES}: source is a name of at most 64 characters")
    bodies, points = {}, {}
    for number, item in enumerate(spec.get("entities") or [], 1):
        where = f"{ENTITIES}: entity {number}"
        if not isinstance(item, dict):
            raise SeedError(f"{where}: expected a type, an id and attributes")
        entity_type, entity_id = as_text(item.get("type")), as_text(item.get("id"))
        if not (entity_type and entity_id):
            raise SeedError(f"{where}: type and id are needed")
        where += f" ({entity_type} '{entity_id}')"
        if len(entity_type) > 255 or not ENTITY_TYPE.fullmatch(entity_type):
            raise SeedError(f"{where}: the type is made of letters, digits, _ and -")
        if len(entity_id) > 255 or not ENTITY_ID.fullmatch(entity_id):
            raise SeedError(f"{where}: the id is made of letters, digits, _, : and - with dots between parts")
        if entity_id in bodies:
            raise SeedError(f"{where}: the id is used twice")
        attributes = item.get("attributes") or {}
        if not isinstance(attributes, dict):
            raise SeedError(f"{where}: attributes are a mapping")
        kind = item.get("kind") or ("PRINCIPAL" if entity_type in PRINCIPALS else "RESOURCE")
        if kind not in KINDS:
            raise SeedError(f"{where}: kind is {' or '.join(KINDS)}")
        parents = [pair(parent, f"{where}, parent") for parent in item.get("parents") or []]
        attributes = reference(attributes, where)
        bodies[entity_id] = {"entityId": entity_id, "kind": kind, "entityType": entity_type, "attributes": attributes,
                             "parents": [{"entityType": t, "entityId": i} for t, i in parents],
                             "source": source, "targets": []}
        points[entity_id] = [*parents, *pointed_at(attributes)]
    return source, in_dependency_order(bodies, points)


def in_dependency_order(bodies, points):
    """The bodies with each entity after those it points at, and the others as the file has them. What a cycle cuts is
    left as the file has it: the platform refuses a cycle of parents and says so."""
    by_pair = {(body["entityType"], entity_id): entity_id for entity_id, body in bodies.items()}
    order, seen = [], set()
    for start in bodies:
        if start in seen:
            continue
        seen.add(start)
        stack = [(start, iter(points[start]))]
        while stack:
            current, pending = stack[-1]
            for target in pending:
                found = by_pair.get(target)
                if found and found not in seen:
                    seen.add(found)
                    stack.append((found, iter(points[found])))
                    break
            else:
                stack.pop()
                order.append(bodies[current])
    return order


def made_entities():
    """The ids of the entities the seed saved: the ones its records of kind `entities` list."""
    return {entity_id for x in created() if x["product"] == SECTION and x["kind"] == "entities"
            for entity_id in x.get("ids", [])}


def parents_of(entity):
    return {(p["entityType"], p["entityId"]) for p in entity.get("parents") or []}


def entity_is_as_wanted(current, wanted):
    """Whether the platform's entity says what the file says. The platform may add attributes of its own: they stay."""
    return (current.get("entityType") == wanted["entityType"] and current.get("kind", wanted["kind"]) == wanted["kind"]
            and current.get("source", wanted["source"]) == wanted["source"]
            and contains(current.get("attributes") or {}, wanted["attributes"])
            and parents_of(current) == parents_of(wanted))


def elsewhere(api, ids):
    """Among `ids`, the entities the platform holds whatever their source: those the list of a source did not show."""
    found = {}
    for start in range(0, len(ids), LOOKUP):
        for held in api.rows("/entities", LISTS, entityId=ids[start:start + LOOKUP]):
            found[held["entityId"]] = held
    return found


def put_entities(api, spec):
    """Saves the entities of the file that are missing or differ, in one request."""
    source, bodies = data_entities(spec)
    save_entities(api, source, bodies)


def same_import(current, body):
    """Whether an entity the seed did not make is the import of the same catalog entry: someone imported it in the console."""
    return (body["source"] == IMPORTED and current.get("source") == IMPORTED
            and (current.get("attributes") or {}).get("_catalogId") == body["attributes"].get("_catalogId"))


def save_entities(api, source, bodies, each=False):
    """Saves the entities of a source that are missing or differ: in one request, or `each` in a request of its own, as
    the console's import does. What an earlier run saved and lost the answer of is the seed's first."""
    mine = made_entities()
    noted = {entity_id for x in notes(SECTION, "entities") for entity_id in x.get("ids", [])}
    present = {held["entityId"]: held for held in api.rows("/entities", LISTS, source=source)} if bodies else {}
    present.update(elsewhere(api, [body["entityId"] for body in bodies if body["entityId"] not in present]))
    changed, lost, theirs = [], [], []
    for body in bodies:
        entity_id = body["entityId"]
        current = present.get(entity_id)
        if current and entity_id not in mine and entity_id not in noted:
            if same_import(current, body):
                theirs.append(entity_id)
                continue
            raise SeedError(f"{body['entityType']} '{entity_id}' exists in Authorization Management and the seed did "
                            "not create it: it is left alone")
        if current and entity_id not in mine:
            lost.append(body)
        if not current or not entity_is_as_wanted(current, body):
            changed.append(body)
    if theirs:
        log(SECTION, f"{len(theirs)} entities of '{source}' imported in the console already, left as they are")
    if lost:   # recorded before the old notes go
        remember(source, lost)
        log(SECTION, f"! {len(lost)} entities of '{source}': saved by an earlier seed that lost the platform's answer, "
                     "now known as the seed's")
    for x in notes(SECTION, "entities"):   # what it names is recorded now, or was never saved
        unmade(SECTION, "entities", x["name"])
    if not changed:
        if bodies:
            log(SECTION, f"{len(bodies) - len(theirs)} entities of '{source}' as the file says")
        return
    if each:
        send_each(api, source, changed)
        return
    try:
        with making(SECTION, "entities", "entities", source, missing=True, ids=[body["entityId"] for body in changed]):
            said = api.post("/entities/_bulk", json={"entities": changed}).json()
            # What the platform saved is recorded before the refusal of the rest is reported, so that a removal deletes it
            # and the next run finds it as the seed's: its `errors` are {index, message}
            refused = {e.get("index"): e.get("message") or str(e) for e in said.get("errors") or []}
            remember(source, [body for index, body in enumerate(changed) if index not in refused])
    except HttpError as e:
        raise explain("The entities", e) from e
    if refused or said.get("failed"):
        why = excerpt(next(iter(refused.values()), "no reason given"))
        raise SeedError(f"Authorization Management refused {len(refused) or said['failed']} of {len(changed)} entities: {why}")
    log(SECTION, f"✓ {len(changed)} entities of '{source}' saved")


def send_each(api, source, changed):
    """Saves the entities one request each, in their order, with the time of the import the console stamps on each. A
    refusal (a 4xx) records what was saved before it; any other trouble leaves the note of them all, so that the next run
    takes what it finds as the seed's."""
    saved = []
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    try:
        with making(SECTION, "entities", "entities", source, missing=True, ids=[body["entityId"] for body in changed]):
            for body in changed:
                api.post("/entities", json={**body, "attributes": {**body["attributes"], "_importedAt": stamp}})
                saved.append(body)
    except HttpError as e:
        if e.status < 500 and saved:
            remember(source, saved)
        raise explain(f"{body['entityType']} '{body['entityId']}'", e) from e
    remember(source, saved)
    log(SECTION, f"✓ {len(saved)} entities of '{source}' imported")


# --- Entities of the AI catalog -------------------------------------------------------------

def catalog_id(text):
    """What the console makes of a name to give an entity an id when a catalog entry has no slug."""
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9._-]+", "-", str(text).lower())) if text else ""


def first(*values):
    """The first value that is set."""
    return next((value for value in values if value is not None), None)


def entry_id(item, kind):
    """The id the console gives the entity of a catalog entry: its slug, else what it makes of its name."""
    if item.get("slug"):
        return item["slug"]
    definition = item.get("definition") or {}
    info = definition.get("serverInfo") or {}
    names = {"mcp-server": [(item.get("extensions") or {}).get("entityId"), info.get("name"), info.get("title")],
             "model": [definition.get("queryName"), definition.get("name")]}.get(kind, [definition.get("name")])
    return next((made for made in map(catalog_id, names) if made), item["id"])


def entry_name(item, kind):
    """The name the console shows for a catalog entry, and gives its entity."""
    definition = item.get("definition") or {}
    if kind == "mcp-server":
        info = definition.get("serverInfo") or {}
        return first(info.get("title"), info.get("name"), item["id"])
    if kind == "model":
        return first(definition.get("name"), definition.get("queryName"), item["id"])
    return first(definition.get("name"), item["id"])


def imported(entity_id, entity_type, name, item_id, description=None, parents=()):
    """An entity as the console's import saves it: read only, its source the catalog, the catalog's id in `_catalogId`."""
    attributes = {"_displayName": name, "_catalogId": item_id} if name else {"_catalogId": item_id}
    if description:
        attributes["description"] = description
    return {"entityId": entity_id, "entityType": entity_type, "kind": "RESOURCE", "attributes": attributes,
            "parents": [{"entityType": t, "entityId": i} for t, i in parents], "source": IMPORTED}


def catalog_entities(gamma, wanted):
    """The entities of what `catalog` names, as the console's import makes them: an entry, then the tools of an MCP server
    under it. An entry is named by its slug or by a name the catalog gives it."""
    if not isinstance(wanted, dict) or not set(wanted) <= set(IMPORTS):
        raise SeedError(f"{ENTITIES}: catalog names {', '.join(IMPORTS)}")
    bodies = []
    for key, names in wanted.items():
        kind, entity_type = IMPORTS[key]
        if not isinstance(names, list) or not all(isinstance(name, str) and name for name in names):
            raise SeedError(f"{ENTITIES}: catalog, {key}: a list of names")
        listed = gamma.aim.rows("/catalog/items", CATALOG, kind=kind) if names else []
        for name in names:
            found = [item for item in listed if name in called(item, kind)]
            if len(found) != 1:
                raise SeedError(f"{ENTITIES}: catalog, {key}: '{name}' is " + ("not in the AI catalog" if not found else
                                f"the name of {len(found)} entries of the AI catalog: name it by its slug"))
            item = found[0]
            definition = item.get("definition") or {}
            description = (item.get("extensions") or {}).get("description") if kind == "mcp-server" else definition.get("description")
            entity_id = entry_id(item, kind)
            bodies.append(imported(entity_id, entity_type, entry_name(item, kind), item["id"], description))
            if kind == "mcp-server":
                for tool in gamma.aim.rows("/catalog/mcp-tools", CATALOG, parentId=item["id"]):
                    said = tool.get("definition") or {}
                    bodies.append(imported(tool.get("slug") or catalog_id(said.get("name")) or tool["id"], "MCPTool",
                                           said.get("name"), tool["id"], said.get("description"), [(entity_type, entity_id)]))
    return bodies


def called(item, kind):
    """The names a file may give a catalog entry: its slug, the id the console gives it, and the names the catalog shows."""
    definition = item.get("definition") or {}
    info = definition.get("serverInfo") or {}
    return {name for name in (item.get("slug"), entry_id(item, kind), entry_name(item, kind), info.get("name"),
                              definition.get("name"), definition.get("queryName")) if name}


def import_catalog(gamma, api, spec):
    """`catalog`: what "Import from AI Catalog" of the Entities page saves, one request each, saved again when the catalog
    changed it."""
    if not spec.get("catalog"):
        return
    save_entities(api, IMPORTED, catalog_entities(gamma, spec["catalog"]), each=True)


def remember(source, saved):
    """Adds the entities just saved to the record of their source, after those saved before them: an entity is saved after what
    it points at (`data_entities`), and a removal deletes the last saved first."""
    earlier = next((x for x in created() if (x["product"], x["kind"], x["name"]) == (SECTION, "entities", source)), {})
    ids = list(dict.fromkeys([*earlier.get("ids", []), *(body["entityId"] for body in saved)]))
    record(SECTION, "entities", "entities", source, source, ids=ids, count=len(ids))


# --- Policies -------------------------------------------------------------------------------


def policy(api, spec, slug, policies, groups=None):
    name = spec["name"]
    kind = spec.get("kind", "RESOURCE")
    if kind not in POLICY_KINDS:
        raise SeedError(f"Policy '{name}': kind is {' or '.join(POLICY_KINDS)}, not '{kind}'")
    policy_type = spec.get("type", "MCP")
    if policy_type not in POLICY_TYPES:
        raise SeedError(f"Policy '{name}': type is one of {', '.join(POLICY_TYPES)}, not '{policy_type}'")
    text = fill(spec["policy"], slug, f"Policy '{name}'", groups)
    found = controls.of("policy", name)
    status = controls.status(found, spec.get("status", "DEPLOYED"))
    current = policies.get("policy." + slugify(name))
    if current and not recorded(SECTION, "policy", current["id"]) and not adopted(SECTION, "policy", name, current["id"]):
        raise SeedError(f"Policy '{name}' exists in Authorization Management and the seed did not create it: it is left alone")
    # A policy keeps the kind and the type it was made with: an update takes neither, so one whose kind or type changed is
    # made again
    held = current and (current.get("kind", kind), current.get("policyType") or policy_type)
    if current and held != (kind, policy_type):
        api.delete(PATHS["policy"].format(current["id"]))
        forget(*[x for x in created() if (x["product"], x["kind"], x["id"]) == (SECTION, "policy", current["id"])])
        log(SECTION, f"Policy '{name}' is {' '.join(held)}, its file says {kind} {policy_type}: made again")
        current = None
    # The platform reads a policy when it is saved: one it cannot read comes back with the line and the reason
    if not current:
        with making(SECTION, "policy", "access rules", name, missing=True):
            try:
                current = api.post("/policies", json={"name": name, "kind": kind, "policyType": policy_type,
                                                      "policyText": text, "targets": []}).json()
            except HttpError as e:
                if e.status < 500:
                    unmade(SECTION, "policy", name)
                raise explain(f"Policy '{name}'", e) from e
            record(SECTION, "policy", "access rules", name, current["id"])
        log(SECTION, f"✓ Policy '{name}' created")
    elif current["name"] != name or current["policyText"].strip() != text.strip():
        try:
            current = api.put(f"/policies/{current['id']}",
                              json={"name": name, "policyText": text, "targets": []}).json()
        except HttpError as e:
            raise explain(f"Policy '{name}'", e) from e
        log(SECTION, f"✓ Policy '{name}' put back")
    else:
        log(SECTION, f"Policy '{name}' exists")
    # A draft is not enforced: one that was deployed is taken out of service again, and the console then shows it Disabled:
    # there is no way back to a draft
    if status == "DEPLOYED" and current["status"] != "DEPLOYED":
        api.post(f"/policies/{current['id']}/deploy")
        controls.touched()
        log(SECTION, "  ✓ Deployed")
    elif status != "DEPLOYED" and current["status"] == "DEPLOYED":
        api.post(f"/policies/{current['id']}/disable")
        controls.touched()
        log(SECTION, "  ✓ Taken out of service: the console shows it Disabled")


# --- Schemas --------------------------------------------------------------------------------

def same_text(a, b):
    """Whether two texts say the same, whatever their spacing and line breaks."""
    return " ".join(a.split()) == " ".join(b.split())


def schema_document(api, slug):
    """seed/authz/schema.yaml: the one schema document of the environment. It is an aid of the editor, so one that
    cannot be saved is a warning, not a seed that stops. Another person's schema is never replaced, and the seed deletes
    only the one it made."""
    path = seed_file("authz", "schema.yaml")
    if not path:
        return
    spec = load(path)
    if not isinstance(spec.get("schema"), str):
        raise SeedError("seed/authz/schema.yaml: schema is the text of the schema")
    wanted = fill(spec["schema"], slug, "The schema")
    try:
        held = api.get("/schema", expect=(200, 404))
        current = held.json().get("schema") if held.status_code == 200 else None
        if current and same_text(current, wanted):
            log(SECTION, "Schema exists")
            return
        mine = recorded(SECTION, "schema-document", "schema")
        if current and not mine:
            raise SeedError("one exists and the seed did not create it")
        try:
            checked = api.post("/schema/validate", json={"schema": wanted}, expect=(200, 404, 405))
            verdict = checked.json() if checked.status_code == 200 else {}
            if isinstance(verdict, dict) and verdict.get("valid") is False:
                raise SeedError(why_invalid(verdict.get("errors")))
            api.put("/schema", json={"schema": wanted})
        except HttpError as e:
            raise explain("The schema", e) from e
        if not current:
            record(SECTION, "schema-document", "access schemas", "schema", "schema")
        log(SECTION, f"✓ Schema {'put back' if current else 'created'}")
    except SeedError as e:
        log(SECTION, f"! Schema skipped: {e}")


def why_invalid(errors):
    """What the validation of a schema found, on one line."""
    first = (errors or [None])[0]
    if isinstance(first, dict):
        where = f"line {first['line']}: " if first.get("line") else ""
        first = where + str(first.get("message") or first.get("error") or "")
    return excerpt(first) if first else "the platform cannot read it"


def schema(api, spec, slug):
    """A schema is an aid of the editor: one that cannot be saved is a warning, not a seed that stops."""
    name, text = spec["name"], fill(spec["schema"], slug, f"Schema '{spec['name']}'")
    try:
        current = next((d for d in items(api.get("/schemas").json()) if d["name"] == name), None)
        if current and not recorded(SECTION, "schema", current["id"]) and not adopted(SECTION, "schema", name, current["id"]):
            raise SeedError("one of that name exists and the seed did not create it")
        if not current:
            with making(SECTION, "schema", "access schemas", name, missing=True):
                made = api.post("/schemas", json={"name": name, "schema": text, "targets": []}).json()
                record(SECTION, "schema", "access schemas", name, made["id"])
            log(SECTION, f"✓ Schema '{name}' created")
        elif current["schema"].strip() != text.strip():
            api.put(f"/schemas/{current['id']}", json={"name": name, "schema": text, "targets": []})
            log(SECTION, f"✓ Schema '{name}' put back")
    except SeedError as e:
        log(SECTION, f"! Schema '{name}' skipped: {e}")


def remove(records):
    mine = owned(reversed(records), SECTION, ORDER)   # entities: the last made, a tool, before the first, its server
    if not mine:
        return
    gamma = Gamma()
    api = gamma.authz
    gamma.connect(SECTION, lambda: api.get("/policies", params={"page": 1, "perPage": 1}), "Authorization Management")
    for x in mine:
        if x["kind"] == "entities":
            for entity_id in reversed(x.get("ids", [])):   # a child before its parent
                api.delete(PATHS["entity"].format(quote(entity_id, safe="")))
            forget(x)
            log(SECTION, f"✓ Deleted {len(x.get('ids', []))} {x['label']} of '{x.get('source', x['name'])}'")
            continue
        api.delete(PATHS[x["kind"]].format(quote(x["id"], safe="")))
        forget(x)
        log(SECTION, f"✓ Deleted {x['kind'].replace('-', ' ')} '{x['name']}'")
