"""Authorization Management: what the policies are about, the policies, and a schema for the editor, from seed/authz/.

    seed/authz/entities.yaml      what the policies reason over, in one of two shapes (or both):
                                    `source` and `entities`: the data behind the tools, each {type, id, attributes,
                                      parents}, where an attribute `{ref: [Type, id]}` points at another entity
                                    `server`: the MCP server (by its name in the AI catalog), with its `tools`,
                                      `groups`, `users` and `agents`
    seed/authz/policies/*.yaml    name, status (DEPLOYED or DRAFT) and text, in GAPL, of one policy, of any number of
                                  statements
    seed/authz/schema.yaml        `schema`: the one schema document of the environment, for the editor
    seed/authz/schemas/*.yaml     name and text of a schema document, for a platform that keeps several: the editor
                                  reads them, decisions do not depend on them

A policy or a schema names the MCP server and an agent without knowing their ids: `<server>` stands for the slug the
catalog gave the server, which the seed reads back from the catalog, and `<agent:CLIENT_ID>` for the id of the agent
entity that the gateway makes of that client id (`AgentIdentity::"<agent:care-copilot>"`).

Each statement of a policy starts with an `@id`, which Decisions shows as the reason of a decision ("Forbidden by policy
'...'"): it is written as a name a person reads, the first statement of a policy carrying the policy's own name.

What is there and the seed did not create is an error: it is never taken over. What the seed created is put back as the
files say, a policy someone edited or undeployed as well as an entity whose attributes changed; what is right is not saved.
The entities of a file are sent in one request for each 1,000 of them, and only those that are missing or differ. They
are recorded once for each type, with their ids, so that a removal deletes what points at another type first.

A policy a control of seed/controls.yaml names (controls.py) is DEPLOYED while the mode protects (SEED_PROTECTIONS=on) and a
draft while it does not, whatever its file says and whoever deployed or took it out since: each run brings it back.
"""

import hashlib
import json
import re
import unicodedata
import uuid
from urllib.parse import quote

from . import controls
from .am import contains
from .common import (HttpError, SeedError, created, excerpt, files, has_seed, items, load, log, owned, record, recorded,
                    seed_file)
from .gamma import CATALOG, LISTS, Gamma

SECTION = "authz"
DEPENDS = ("aim_catalog", "aim_proxies", "aim_agents")   # the modules whose work this one reads on the platform (schedule.py)
# The folder of the seed it owns
FOLDERS = ("authz",)
# The Gamma modules it fills: seed.py checks that the platform serves them before it changes anything
GAMMA = ("authz",)
# What it records, dependents first. `entities` holds many entities in one record, `entity` one
ORDER = ["policy", "entities", "entity", "schema", "schema-document"]

SERVER = "<server>"
AGENT = re.compile(r"<agent:([^<>\s]+)>")
PATHS = {"policy": "/policies/{}", "entity": "/entities/{}", "schema": "/schemas/{}", "schema-document": "/schema"}

ENTITIES = "seed/authz/entities.yaml"
SOURCE = "lab"          # where the platform says the entities of a file come from, when the file does not say
PRINCIPALS = ("User", "Group", "AgentIdentity")   # the entities that act; every other type is a resource
KINDS = ("PRINCIPAL", "RESOURCE")
BULK = 1000             # entities in one request: the platform takes 10,000
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
    for path in files("authz", "schemas"):
        schema(api, load(path), slug)
    for wanted in entities(spec, slug):
        entity(api, wanted)
    put_entities(api, spec)
    policies = {p["entityId"]: p for p in api.rows("/policies", LISTS)}
    for path in files("authz", "policies"):
        policy(api, load(path), slug, policies)


def server_slug(gamma, name):
    """The slug the catalog gave the MCP server: it is decided at the first import, so it is read, not written down. The
    server is the one that calls itself `name`, or whose slug is what the catalog makes of that name."""
    for server in gamma.aim.rows("/catalog/mcp-servers", CATALOG):
        called = ((server.get("definition") or {}).get("serverInfo") or {}).get("name")
        if called == name or server.get("slug") == slugify(name):
            return server["slug"]
    raise SeedError(f"The MCP server '{name}' is not in the AI catalog: the policies are about its tools")


def fill(text, slug, what):
    """A policy or a schema with its placeholders replaced: <server> by the slug of the MCP server, <agent:CLIENT_ID> by
    the id of the agent entity."""
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
        return excerpt(body)
    if isinstance(said, dict):
        for key in ("message", "error", "detail", "title"):
            if isinstance(said.get(key), str) and said[key].strip():
                return excerpt(said[key])
        errors = said.get("errors")
        first = errors[0] if isinstance(errors, list) and errors else None
        first = first.get("message") if isinstance(first, dict) else first
        if isinstance(first, str) and first.strip():
            return excerpt(first)
    return excerpt(body)


def explain(what, error):
    """The error of a request the platform refused (a policy it cannot read, a schema it cannot parse), on one line."""
    return SeedError(f"{what} refused by Authorization Management (HTTP {error.status}): {reason(error.body)}")


# --- Entities of the MCP server -------------------------------------------------------------

def agent_id(client_id):
    """The id of an agent entity: what the gateway makes of the client id in a token (Java's UUID.nameUUIDFromBytes)."""
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
    if current and not recorded(SECTION, "entity", entity_id):
        raise SeedError(f"{wanted['entityType']} '{entity_id}' exists in Authorization Management and the seed did not "
                        "create it: it is left alone")
    if current and as_wanted(current, wanted):
        return
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
    """(type, id) of a `[Type, id]`, or of a `{type, id}`."""
    if isinstance(value, dict):
        value = [value.get("type"), value.get("id")]
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
    """The entities of `entities:` as the bulk API takes them, in dependency order: an entity comes after its parents and
    after what its attributes point at. With the source they have, and the rank of each type: the more types a type
    points at, the higher, and a higher type is deleted first."""
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
    return source, in_dependency_order(bodies, points), type_ranks(bodies, points)


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


def type_ranks(bodies, points):
    """{type: rank}: 0 for a type that points at no other, else one more than the highest of the types it points at."""
    below = {}
    for entity_id, body in bodies.items():
        below.setdefault(body["entityType"], set()).update(t for t, _ in points[entity_id] if t != body["entityType"])

    def rank(entity_type, path=()):
        others = [other for other in below[entity_type] if other in below and other not in path]
        return max((rank(other, (*path, entity_type)) for other in others), default=-1) + 1

    return {entity_type: rank(entity_type) for entity_type in below}


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
    """Saves the entities of the file that are missing or differ, in requests of at most BULK."""
    source, bodies, ranks = data_entities(spec)
    if not bodies:
        return
    mine = made_entities()
    present = {held["entityId"]: held for held in api.rows("/entities", LISTS, source=source)}
    present_elsewhere = elsewhere(api, [body["entityId"] for body in bodies if body["entityId"] not in present])
    changed, new = [], set()
    for body in bodies:
        entity_id = body["entityId"]
        current = present.get(entity_id) or present_elsewhere.get(entity_id)
        if current and entity_id not in mine:
            raise SeedError(f"{body['entityType']} '{entity_id}' exists in Authorization Management and the seed did "
                            "not create it: it is left alone")
        if not current:
            new.add(entity_id)
        if not current or not entity_is_as_wanted(current, body):
            changed.append(body)
    if not changed:
        log(SECTION, f"{len(bodies)} entities of '{source}' as the file says")
        return
    for start in range(0, len(changed), BULK):
        send_entities(api, source, changed[start:start + BULK], bodies, ranks)
    for entity_type in dict.fromkeys(body["entityType"] for body in changed):
        of_type = [body["entityId"] for body in changed if body["entityType"] == entity_type]
        made = sum(entity_id in new for entity_id in of_type)
        counts = {"created": made, "put back": len(of_type) - made}
        said = ", ".join(f"{count} {what}" for what, count in counts.items() if count)
        log(SECTION, f"✓ {entity_type} entities of '{source}': {said}")


def send_entities(api, source, chunk, bodies, ranks):
    """One bulk request. What the platform saved is recorded before the refusal of the rest is reported, so that a
    removal deletes it and the next run finds it as the seed's."""
    try:
        answer = api.post("/entities/_bulk", json={"entities": chunk})
    except HttpError as e:
        raise explain("The entities", e) from e
    try:
        said = answer.json()
    except ValueError:
        said = {}
    refused = refusals(said)
    remember(source, [body for index, body in enumerate(chunk) if index not in refused], bodies, ranks)
    if refused or (isinstance(said, dict) and said.get("failed")):
        count = len(refused) or said["failed"]
        why = excerpt(next(iter(refused.values()), "no reason given"))
        raise SeedError(f"Authorization Management refused {count} of {len(chunk)} entities: {why}")


def refusals(said):
    """{index in the request: why} of the entities a bulk answer says it refused: its `errors` are {index, message}."""
    if not isinstance(said, dict):
        return {}
    return {e.get("index"): e.get("message") or str(e) for e in said.get("errors") or [] if isinstance(e, dict)}


def remember(source, saved, bodies, ranks):
    """Adds the entities just saved to the record of their type: one record for each type, with its ids in dependency
    order (the ids of earlier runs that the file no longer has come first), and the rank that orders a removal."""
    position = {body["entityId"]: index for index, body in enumerate(bodies)}
    for entity_type in dict.fromkeys(body["entityType"] for body in saved):
        name = f"{source}/{entity_type}"
        earlier = next((x.get("ids", []) for x in created()
                        if (x["product"], x["kind"], x["name"]) == (SECTION, "entities", name)), [])
        ids = sorted({*earlier, *(body["entityId"] for body in saved if body["entityType"] == entity_type)},
                     key=lambda entity_id: position.get(entity_id, -1))
        record(SECTION, "entities", f"{entity_type} entities", name, name, ids=ids, count=len(ids),
               rank=ranks[entity_type], source=source)


# --- Policies -------------------------------------------------------------------------------

def slugify(name):
    """What the platform makes of a policy name in the entity id of the policy: accents dropped, lower case, a-z 0-9 _ -."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9_-]+", "-", ascii_name).strip("-")


def policy(api, spec, slug, policies):
    name = spec["name"]
    text = fill(spec["policy"], slug, f"Policy '{name}'")
    found = controls.of("policy", name)
    status = controls.status(found, spec.get("status", "DEPLOYED"))
    current = policies.get("policy." + slugify(name))
    if current and not recorded(SECTION, "policy", current["id"]):
        raise SeedError(f"Policy '{name}' exists in Authorization Management and the seed did not create it: it is left alone")
    # The platform reads a policy when it is saved: one it cannot read comes back with the line and the reason
    if not current:
        try:
            current = api.post("/policies", json={"name": name, "kind": "RESOURCE", "policyType": "MCP",
                                                  "policyText": text, "targets": []}).json()
        except HttpError as e:
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
        current = held_schema(api.get("/schema", expect=(200, 404)))
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


def held_schema(answer):
    """The text of the schema the platform holds, empty when it holds none (a 404, or an answer that has no text)."""
    if answer.status_code != 200:
        return ""
    try:
        said = answer.json()
    except ValueError:
        return ""
    return (said.get("schema") or "") if isinstance(said, dict) else ""


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
        if current and not recorded(SECTION, "schema", current["id"]):
            raise SeedError("one of that name exists and the seed did not create it")
        if not current:
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
    # Of the entities of the data, the types that point at others go first; sorted is stable, the rest keeps its order
    mine = sorted(mine, key=lambda x: (ORDER.index(x["kind"]), -x.get("rank", 0) if x["kind"] == "entities" else 0))
    gamma = Gamma()
    api = gamma.authz
    gamma.connect(SECTION, lambda: api.get("/policies", params={"page": 1, "perPage": 1}), "Authorization Management")
    for x in mine:
        if x["kind"] == "entities":
            for entity_id in reversed(x.get("ids", [])):   # a child before its parent
                api.delete(PATHS["entity"].format(quote(entity_id, safe="")))
            log(SECTION, f"✓ Deleted {len(x.get('ids', []))} {x['label']} of '{x.get('source', x['name'])}'")
            continue
        api.delete(PATHS[x["kind"]].format(quote(x["id"], safe="")))
        log(SECTION, f"✓ Deleted {x['kind'].replace('-', ' ')} '{x['name']}'")
