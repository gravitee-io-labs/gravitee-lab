"""Where API Management runs, as this machine keeps it in .lab/core.json, out of git: This machine, the default (the
platform of docker-compose.yml, as it always was), or one environment of a registered Gravitee Cloud account, given as a
place ('<platform id>/<environment id>', places.py), whose hybrid gateway runs here. Only API Management can run in the
Cloud: its group of the platform is the one whose product is the scope of the gateways the Lab creates there
(`group`), and the rest of the platform stays on this machine. The choice is the place the page proposes first for what
it deploys, and what the Dashboard's Start all and Stop all act on. Choosing it starts and stops nothing. A Cloud choice
is an environment the Lab is linked to (it keeps its Cloud Token or a hybrid gateway of it); one kept whose platform or
environment is gone is This machine."""

import json

from . import cloud, places, platforms
from ..library import layers, model
from .. import paths

FILE = ".lab/core.json"   # {place, chosen, environment: its name, which nothing offline holds}, relative to the project
MACHINE = layers.MACHINE
NOT_LINKED = "Set up {} first"   # an environment without a Cloud Token or a hybrid gateway of the Lab


# ---------------------------------------------------------------------------------------------------------------------
# What is kept
# ---------------------------------------------------------------------------------------------------------------------

def _stored():
    """{place, chosen, environment} as the file says it; This machine, never chosen, when it is missing or reads as nothing."""
    try:
        found = json.loads((paths.ROOT / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        found = {}
    found = found if isinstance(found, dict) else {}
    place, name = found.get("place"), found.get("environment")
    return {"place": place if isinstance(place, str) and place else MACHINE, "chosen": found.get("chosen") is True,
            "environment": name if isinstance(name, str) else ""}


def _write(place, environment=""):
    target = paths.ROOT / FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    body = {"place": place, "chosen": True, **({"environment": environment} if environment else {})}
    paths.write_atomic(target, json.dumps(body, indent=2) + "\n")


def group(project):
    """The group of the platform that can run in Gravitee Cloud: the one whose product is the scope of the hybrid
    gateways the Lab creates there (API Management). None when the platform has no such group."""
    return platforms.product_group(project, cloud.SCOPE)


def linked(place):
    """Whether the Lab is linked to the environment of a place: it keeps its Cloud Token, or a hybrid gateway of it."""
    return bool(place and place.get("environment") and (place["token"]["state"] != "unset" or place["gateway"]))


def _place(project, place_id):
    """The place of a Cloud choice as its platform last said it (places.place): one that cannot be used yet answers for
    it. None when it is gone: its platform is no longer registered, or said it has no such environment."""
    uid = place_id.partition("/")[0]
    if not any(record["id"] == uid for record in platforms.read()):
        return None
    return places.place(project, place_id)


def current(project):
    """Where API Management runs: {place, chosen, environment}. A Cloud choice that is gone is This machine, still chosen."""
    stored = _stored()
    if stored["place"] != MACHINE and not _place(project, stored["place"]):
        return {**stored, "place": MACHINE, "environment": ""}
    return stored


def choose(project, place):
    """Keeps where API Management runs: This machine (None or `machine`), or the place of an environment the Lab is
    linked to, which its platform is asked about now. Starts and stops nothing. Answers {place, name}; refuses what is
    not a linked environment in the words a person reads."""
    if place in (None, MACHINE):
        _write(MACHINE)
        return {"place": MACHINE, "name": None}
    if not isinstance(place, str) or "/" not in place:
        raise ValueError("Choose an environment")
    uid, _, environment = place.partition("/")
    record = next((record for record in platforms.read() if record["id"] == uid), None)
    if not record:
        raise ValueError("Connect a Gravitee Cloud account first")
    places.invalidate(uid)
    found = places.place(project, place)
    if found and found["state"] == "unusable":
        raise ValueError(found["reason"])
    if not found:
        raise paths.NotFound(f"No environment {environment} in {record['name']}")
    if not linked(found):
        raise ValueError(NOT_LINKED.format(found["name"]))
    _write(place, found["environment"]["name"])
    return {"place": place, "name": found["name"]}


# ---------------------------------------------------------------------------------------------------------------------
# Start all and Stop all with API Management in the Cloud
# ---------------------------------------------------------------------------------------------------------------------

def _split(project, state):
    """What runs on this machine while API Management runs in the Cloud, as sets of services.

    `on`: what an `up` names (layers.up_services: never a seed, nor what needs a license that is not usable), the add-on
    of the hybrid gateway (`addon`, its services `own`) and what it needs turned on with what is on. `moved`: the
    services of API Management's group (`group`). `needs`: the services of the add-ons and of the playground that depend
    on one of those, themselves or through what they depend on: they stay as they are. `out`: the other services of the
    platform that exist only for what does not start, those whose every dependent among `on` is moved, needs one or is
    out itself; a database that a product of this machine or an add-on uses is not. `starts`: the rest of `on`, the
    gateway's add-on aside. All of it is read from the depends_on that Compose reads, add-ons merged in: no service is
    named here."""
    services, found = project["services"], group(project)
    addon = platforms.gateway_addon(project, "cloud")
    own = set(addon["services"]) if addon else set()
    brought = layers.closure(project, [addon["id"]]) if addon else set()
    on = set(layers.up_services(project, state["playground"], set(state["effective"]) | brought))
    moved = set(found["services"]) if found else set()
    platform = {name for name in on if services[name]["owner"]["kind"] == "platform"}
    others = on - platform - own
    needs = {name for name in others if layers.depends(project, on, [name]) & moved}
    users = {name: {user for user in on if name in (services.get(user) or {}).get("depends_on", ())}
             for name in platform - moved}
    out = set()
    while True:
        gone = moved | needs | out
        more = {name for name, using in users.items() if name not in gone and using and using <= gone}
        if not more:
            break
        out |= more
    return {"on": on, "moved": moved, "needs": needs, "out": out, "starts": (platform - moved - out) | (others - needs),
            "own": own, "group": found}


def _rows(project, names, on):
    """These services as rows (layers.start_rows): the platform's by group, an add-on or a playground by its name when
    all it has among `on` is there, else each of its services, so that a row never says an item starts and does not."""
    services, names = project["services"], set(names)

    def owner(name):
        return services[name]["owner"]["kind"], services[name]["owner"]["id"]
    alone = {name for name in names if owner(name)[0] != "platform"
             and not {other for other in on if owner(other) == owner(name)} <= names}
    return layers.start_rows(project, sorted(names - alone)) + layers.service_rows(project, alone)


def plan(project, state, containers, saved=None, live=False):
    """What Start all does with API Management in the Cloud: it starts the hybrid gateway of the chosen environment
    (`gateway`, the plan of its add-on for that place, layers.plan, which turns it on and makes it again with that
    environment's token when it serves another one), then what does not run of the rest of the platform and of what is
    on (`others`, _split's `starts`): the products that stay on this machine with what they depend on, the services of
    the playground and of the add-ons that are on. It does not start the services of API Management, what exists only
    for them, and what is on and needs them (`not_started`, rows of what does not run, its reason in `hints`).
    {gateway, place, gateway_id, others, not_started, dialog, needed, blocked}. `live`: the platform is read again
    first, as a click asks. Raises ValueError with the reason it cannot start: the platform cannot be used, the
    environment has no hybrid gateway whose token the Lab keeps, nothing runs its gateway here."""
    chosen, here = current(project), f"{(group(project) or {}).get('name') or 'It'} runs on this machine"
    if chosen["place"] == MACHINE:
        raise ValueError(here)
    uid = chosen["place"].partition("/")[0]
    if live:
        places.invalidate(uid)
    found = _place(project, chosen["place"])
    if not found:
        raise ValueError(here)
    if found["state"] == "unusable":
        raise ValueError(found["reason"])
    addon = platforms.gateway_addon(project, found["platform"]["kind"])
    if not addon:
        raise ValueError(f"No add-on runs a gateway of {found['platform']['name']}")
    if not found["gateway"]:
        raise ValueError(found["reason"])
    where = {"id": found["id"], "name": found["name"], "gateway": addon["id"], "here": found["gateway"]["here"],
             "serving": places.served(project)}
    up = model.up(project, containers)
    behind = model.behind(project, containers, saved) if saved else frozenset()
    gateway = layers.plan(project, state, up, "addon", addon["id"], "start", behind=behind, place=where)
    split = _split(project, state)
    ran = up["services"] | up["done"]   # what runs, or ended well, is left as it is
    others = sorted(name for name in split["starts"] if name not in ran)
    left = split["moved"] | split["needs"] | split["out"]
    not_started = _rows(project, {name for name in left & split["on"] if name not in ran}, split["on"])
    serves = {"kind": "environment", "id": found["id"], "name": found["name"]}
    hints = {"not_started": f"{split['group']['name']} runs in Gravitee Cloud"} if split["group"] else {}
    # The hints the gateway's own plan carries stay (the version of the catalog its add-on comes from, under the row From), with
    # the one that says why API Management does not start
    shown = layers.dialog(gateway)
    dialog = {**shown, "starts": gateway["starts"] + _rows(project, others, split["on"]),
              "serves": gateway["serves"] or [serves], "not_started": not_started,
              "hints": {**shown.get("hints", {}), **hints}}
    return {"gateway": gateway, "place": where, "gateway_id": found["gateway"]["id"], "others": others,
            "not_started": not_started, "dialog": dialog, "blocked": gateway["blocked"],
            "needed": bool(gateway["starts"] or gateway["serves"] or others)}


def stopping(project, state, containers):
    """What Stop all stops with API Management in the Cloud: what runs of the hybrid gateway's add-on, of the add-ons
    that are on and of the playground, and of the rest of the platform that Start all starts. The services of API
    Management, and what exists only for them, stay as they are: Containers stops them."""
    up, split = model.up(project, containers), _split(project, state)
    names = split["starts"] | split["needs"] | (split["own"] & split["on"])
    return sorted(name for name in names if name in up["services"])


def _named(project, chosen):
    """The platform {id, name} of a Cloud choice and the name of its environment: as its platform last said it, else as
    it was kept, else its id."""
    found = _place(project, chosen["place"])
    record = found["platform"] if found else {"id": chosen["place"].partition("/")[0], "name": ""}
    environment = ((found or {}).get("environment") or {}).get("name") or chosen["environment"] \
        or chosen["place"].partition("/")[2]
    return record, environment


def name(project):
    """The name of the place of a Cloud choice, 'Development (My Account)'; None for This machine."""
    chosen = current(project)
    if chosen["place"] == MACHINE:
        return None
    record, environment = _named(project, chosen)
    return places.label(record["name"], environment)


def describe(project, state, containers, saved, wait):
    """What /api/state says of where API Management runs: {place, chosen, group}, `group` the {id, name} of API
    Management's group of the platform (None without one: nothing can run in the Cloud), and for a Cloud choice its
    `name` ('Development (My Account)'), `platform` {id, name}, `environment` {id, name}, and the Dashboard's Start all
    and Stop all as the platform's actions say theirs: `start` {needed, enabled, reason}, `stop` {needed, enabled,
    reason}, from what the platform last said (nothing is waited for). `wait`: why nothing can be started now (the
    state's), else None."""
    chosen, found = current(project), group(project)
    named = {"id": found["id"], "name": found["name"]} if found else None
    if chosen["place"] == MACHINE:
        return {"place": MACHINE, "chosen": chosen["chosen"], "group": named}
    record, environment = _named(project, chosen)
    try:
        planned = plan(project, state, containers, saved)
        needed, reason = planned["needed"], wait or planned["blocked"] or None
    except ValueError as error:
        needed, reason = True, wait or str(error)
    runs = bool(stopping(project, state, containers)) if containers else False
    return {"place": chosen["place"], "chosen": chosen["chosen"], "group": named,
            "name": places.label(record["name"], environment),
            "platform": {"id": record["id"], "name": record["name"]},
            "environment": {"id": chosen["place"].partition("/")[2], "name": environment},
            "start": {"needed": needed, "enabled": not reason, "reason": reason},
            "stop": {"needed": runs, "enabled": not wait, "reason": wait}}
