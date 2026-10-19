"""Where a playground deploys besides this machine: a place for each environment of each registered platform, named
'<environment> (<platform>)', what it lacks to be used (a hybrid gateway, a Cloud Token), what its environment runs and
why each playground cannot go there. What a platform says is
read in the background and kept for a minute, so that the state of the Lab never waits for a Cloud: a platform not read
yet is 'Loading'. What this machine keeps (the tokens, the gateway that runs here) is read at each call. A place is
given by its id, '<platform id>/<environment id>'; the id of a platform that cannot be read is its own. The records of
what a playground deployed in a place are in its own folder, under that id."""

import json
import os
import stat
import threading
import time

from . import cloud, platforms
from .. import paths
from ..library import project as projects
from ..catalogs import trust

TTL = 60   # seconds a read of a platform is served before it is made again
RECORD, META = "created.json", "meta.json"   # in a place's folder: what the seed created, and the name of its environment
LOADING = {"state": "loading", "message": "Loading", "environments": [], "gateways": []}
WORDS = {"invalid": "Invalid token", "unreachable": "Unreachable"}   # why a platform cannot be used; else it is loading
# What an environment without a hybrid gateway of the Lab lacks: (the short words of a picker, the reason a deploy cannot
# go when it has no gateway at all, which is not set up, and when it has others only)
NO_GATEWAY = ("No hybrid gateway", "Set up a gateway in {} first", "Create a hybrid gateway for {}")
# What a Cloud Token lacks, by its state: (the short words of a picker, the reason a deploy cannot go)
NO_TOKEN = {"unset": ("No Cloud Token", "Cloud Token for {} is not set"),
            "expired": ("Cloud Token expired", "Cloud Token for {} has expired"),
            "refused": ("Cloud Token refused", "Gravitee Cloud refuses the Cloud Token of {}")}
# Besides the addresses it is given, the official seeder sends the Cloud Token where two more things lead. PINNED: the path
# of the Portal API, which it joins to an address, so that a value that begins with '@' is the address of another host; the
# seeder's own default is passed. CLEARED: the names its HTTP library takes a proxy and a trust store from, which a seed
# service of the author's may set to see the token pass; each is passed empty, in both spellings of a proxy.
PINNED = {"APIM_PORTAL_PATH": "/portal"}
CLEARED = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy", "REQUESTS_CA_BUNDLE",
           "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR")
STATE_LIMIT = 1 << 20   # the most a file of a state folder is read for: a container wrote it
_lock = threading.Lock()
_cache = {}     # platform id -> (when it was read, what platforms.fetch answered)
_reading = {}   # platform id -> the thread that reads it


# ---------------------------------------------------------------------------------------------------------------------
# What a platform said
# ---------------------------------------------------------------------------------------------------------------------

def _load(uid):
    """Reads a platform now and keeps what it said."""
    record = next((record for record in platforms.read() if record["id"] == uid), None)
    try:
        found = platforms.fetch(record) if record else None
    except Exception as error:  # noqa: BLE001 - the read must end with the platform's word, whatever went wrong
        found = {"state": "unreachable", "message": type(error).__name__, "environments": [], "gateways": []}
    with _lock:
        if found:
            _cache[uid] = (time.monotonic(), found)
        else:
            _cache.pop(uid, None)


def _background(uid):
    try:
        _load(uid)
    finally:
        with _lock:
            _reading.pop(uid, None)


def _read(record):
    """What the platform last said, or LOADING before it said anything. A read that is a minute old is made again in the
    background and this one is served."""
    with _lock:
        entry = _cache.get(record["id"])
        if (entry is None or time.monotonic() - entry[0] >= TTL) and record["id"] not in _reading:
            _reading[record["id"]] = threading.Thread(target=_background, args=(record["id"],), daemon=True)
            _reading[record["id"]].start()
    return entry[1] if entry else LOADING


def invalidate(uid):
    """Forgets what a platform said and reads it again now: the page asks for the places right after an action on a
    platform. A platform that is gone is forgotten."""
    with _lock:
        _cache.pop(uid, None)
    _load(uid)


# ---------------------------------------------------------------------------------------------------------------------
# The places
# ---------------------------------------------------------------------------------------------------------------------

def _gateway_of(environment, gateways):
    """The hybrid gateway that a deploy to an environment goes by: the one that runs here when it is of the environment,
    else the first whose token is kept, else none."""
    kept = [gateway for gateway in gateways
            if gateway["environmentId"] == environment and gateway["type"] == "HYBRID" and gateway["kept"]]
    return min(kept, key=lambda gateway: not gateway["here"], default=None)


def label(platform, environment):
    """The name of a place, in the page, the dialogs and the records: '<environment> (<platform>)'."""
    return f"{environment} ({platform})"


def seed_refused(playground):
    """Why the seed of a playground gets no Cloud Token, or None: it brings its own seed code (N01), or its seed data reads a
    token (N02), which the policy found when it judged the playground. A seed of its own would hold the token of a place
    that nobody checked it with, and the Lab cannot see what it does with it. Every place other than This machine is off
    for such a playground, with this reason."""
    notes = (playground.get("verdict") or {}).get("notes") or []
    return next((line for line in (trust.SEED_OWN, trust.SEED_TOKEN) if line in notes), None)


def _cannot(project, offers, kind):
    """{what cannot be deployed to an environment that offers this: the first reason}, for each playground (by its id), add-on
    ("addon:<id>") and setup ("setup:<id>") that cannot (project.judge, on what it asks, and a seed that must not hold the
    token: seed_refused). Offers None: what the environment runs is not known, and only what does not depend on it is
    judged."""
    offers = offers or {"products": None, "plugins": platforms.plugins(project, kind)}
    found = {}
    for pid, playground in sorted(project.get("playgrounds", {}).items()):
        why = seed_refused(playground)
        if not why and playground.get("asks"):
            why = next(iter(projects.judge(playground["asks"], offers)), None)
        if why:
            found[pid] = why
    for unit_kind in ("addon", "setup"):
        for uid, unit in sorted(project.get(f"{unit_kind}s", {}).items()):
            why = projects.judge(unit["asks"], offers) if unit.get("asks") and not unit.get("gateway") else []
            if why:
                found[f"{unit_kind}:{uid}"] = why[0]
    return found


def _place(record, environment, found, project):
    name = label(record["name"], environment["name"])
    gateway = _gateway_of(environment["id"], found["gateways"])
    offers = platforms.capabilities(project, found, environment["id"], record["kind"])
    missing = NO_TOKEN.get(environment["token"]["state"]) if gateway else \
        (NO_GATEWAY[0], NO_GATEWAY[2] if offers else NO_GATEWAY[1])
    return {"id": f"{record['id']}/{environment['id']}", "name": name,
            "platform": {key: record[key] for key in ("id", "name", "kind")},
            "environment": {"id": environment["id"], "name": environment["name"],
                            "organization": environment["organizationId"]},
            "state": "missing" if missing else "ready", "missing": missing[0] if missing else None,
            "reason": missing[1].format(name) if missing else "", "token": environment["token"],
            "gateway": None if not gateway else {
                "id": gateway["id"], "name": gateway["name"], "state": gateway["status"], "here": gateway["here"],
                "kept": gateway["kept"], "connection": gateway["connection"]},
            "capabilities": offers,
            "cannot": _cannot(project, offers, record["kind"])}


def _places(record, found, project):
    found = platforms.annotate(record, found, project)
    if found["state"] != "ok":   # the platform is one place that cannot be used
        return [{"id": record["id"], "name": record["name"],
                 "platform": {key: record[key] for key in ("id", "name", "kind")}, "environment": None,
                 "state": "unusable", "missing": None, "reason": WORDS.get(found["state"], "Loading"), "token": None,
                 "gateway": None, "capabilities": None,
                 "cannot": _cannot(project, None, record["kind"])}]
    return [_place(record, environment, found, project) for environment in found["environments"]]


def places(project):
    """The places of every platform, by platform name and the account's order of environments, from what each platform
    last said (nothing is waited for). [{id, name, platform {id, name, kind}, environment {id, name, organization},
    state, missing, reason, token {state, expires?}, gateway {id, name, state, here, kept, connection}, capabilities,
    cannot}]. `state` is ready, missing (`missing` says the first thing, `reason` the sentence for a disabled button) or
    unusable: its platform is loading, refuses the Account Token or does not answer, and `reason` says which. That
    platform is then one place without environment, token and gateway, and its own id. `capabilities` is what the
    environment runs (platforms.capabilities), None when that is not known: no gateway there, or a platform that
    cannot be used. `cannot` is {playground id: the first reason it cannot be deployed there}."""
    return [found for record in sorted(platforms.read(), key=lambda record: (record["name"].casefold(), record["id"]))
            for found in _places(record, _read(record), project)]


def place(project, place_id):
    """One place by its id, None when there is none. A platform that cannot be read answers for its places."""
    uid = place_id.partition("/")[0] if isinstance(place_id, str) else None
    return next((found for found in places(project) if found["id"] in (place_id, uid)), None)


def serving(project):
    """{platform, environment, gateway {id, name}} that the gateway running here serves, from the claims of the token
    the add-on holds: None when it holds none, or when no platform read has that gateway yet."""
    for found in places(project):
        if found["gateway"] and found["gateway"]["here"]:
            return {"platform": found["platform"], "environment": found["environment"],
                    "gateway": {key: found["gateway"][key] for key in ("id", "name")}}
    return None


def gateway(project, uid, gateway_id):
    """The place a gateway of a platform serves, as layers.plan takes it for the start of that gateway: {id, name,
    gateway (the id of the add-on that runs it), here (it is the one that runs here), serving ({id, name} of the place
    the gateway that runs here serves now: None when that is not known yet, which is not to say that none runs)}. The
    platform is read again now, because a click asks. Raises paths.NotFound for a platform, a gateway or an add-on that
    is not there, ValueError with the platform's own words when it cannot be read and 'Its token is not kept here'
    for a gateway the Lab cannot start."""
    record = platforms.find(uid)
    invalidate(uid)
    with _lock:
        said = _cache.get(uid)
    found = platforms.annotate(record, said[1], project) if said else LOADING
    if found["state"] != "ok":
        raise ValueError(found["message"])
    # A gateway that Cloud hosts is none that runs here
    mine = next((item for item in found["gateways"] if item["id"] == gateway_id and item["type"] == "HYBRID"), None)
    where = next((item for item in found["environments"] if mine and item["id"] == mine["environmentId"]), None)
    if not (mine and where):
        raise paths.NotFound(f"No gateway {gateway_id} in {record['name']}")
    addon = platforms.gateway_addon(project, record["kind"])
    if not addon:
        raise paths.NotFound(f"No add-on runs a gateway of {record['name']}")
    if not mine["kept"]:
        raise ValueError("Its token is not kept here")
    return {"id": f"{uid}/{where['id']}", "name": label(record["name"], where["name"]), "gateway": addon["id"],
            "here": mine["here"], "serving": served(project)}


def served(project):
    """{id, name} of the place the gateway that runs here serves now (serving), as a plan names it; None when that is
    not known."""
    found = serving(project)
    return found and {"id": f"{found['platform']['id']}/{found['environment']['id']}",
                      "name": label(found["platform"]["name"], found["environment"]["name"])}


# ---------------------------------------------------------------------------------------------------------------------
# What a playground deployed
# ---------------------------------------------------------------------------------------------------------------------

def _folder(pid):
    return paths.ROOT / paths.unit_folder("playground", pid) / platforms.STATE


def inside(pid, path=None):
    """Whether `path`, the state folder of a playground when none is given, is in the state folder of the playground once
    every link is followed: it stays in the playground's own folder (paths.contained), and it is below the state folder
    followed the same way. A container that ran there may have planted a link, and the Lab writes nothing through one,
    nor reads what it leads to: not a link out of the playground's folder, and not one to the playground's seed or
    to its root either, where a write would reach what the policy judged and the seed reads. A state folder that is itself a
    link is none, which paths.contained cannot say of a folder it takes as the root."""
    unit, where = paths.ROOT / paths.unit_folder("playground", pid), _folder(pid) if path is None else path
    if not paths.contained(where, unit):
        return False
    state = os.path.join(os.path.realpath(unit), platforms.STATE)
    followed = os.path.realpath(os.path.join(paths.ROOT, where))
    return followed == state or followed.startswith(state + os.sep)


def read_state(pid, file):
    """The text of a file of the state folder of a playground (`file` is relative to it), or None when there is none or it
    cannot be taken for what a seed wrote: it is not a plain file inside the playground's own folder (a link, a pipe, a path
    that leads out of it), it is over a megabyte, or it is not text. A container wrote it, and it may have planted anything."""
    target = _folder(pid) / file
    if not inside(pid, target):
        return None
    flags = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)   # a pipe would wait for a writer
    try:
        with open(target, "rb", opener=lambda name, how: os.open(name, how | flags)) as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            data = handle.read(STATE_LIMIT + 1)
        return data.decode("utf-8") if len(data) <= STATE_LIMIT else None
    except (OSError, UnicodeDecodeError):
        return None


def recorded(pid):
    """The places that hold a record of what the seed of a playground created: '<platform id>/<environment id>' for each
    folder of its state that holds one, platform by platform. This machine's record is not among them. A state folder that
    leads out of the playground's own, or a folder in it that does, holds none."""
    base, found = _folder(pid), []
    if not base.is_dir() or not inside(pid):
        return found
    for platform in sorted(base.iterdir()):
        if platform.is_dir() and platforms.ID.fullmatch(platform.name):
            found += [f"{platform.name}/{where.name}" for where in sorted(platform.iterdir())
                      if where.is_dir() and platforms.ID.fullmatch(where.name) and inside(pid, where)
                      and (where / RECORD).is_file()]
    return found


def _named(pid, place_id):
    """The name of the environment of a place, as the record says it: the Lab writes it beside what the seed created,
    because nothing offline holds it. None without one."""
    try:
        meta = json.loads(read_state(pid, f"{place_id}/{META}") or "")
    except (ValueError, RecursionError):   # a nest of brackets is too deep to read
        return None
    name = meta.get("environment") if isinstance(meta, dict) else None
    return name if isinstance(name, str) and name else None


def _last_said():
    """{place id: environment name} of every place the platforms said, in the order places() gives them: platforms by
    name, environments as each account lists them. Nothing is read: a platform not read yet has none."""
    with _lock:
        said = dict(_cache)
    found = {}
    for record in sorted(platforms.read(), key=lambda record: (record["name"].casefold(), record["id"])):
        entry = said.get(record["id"])
        if entry and entry[1]["state"] == "ok":
            found.update({f"{record['id']}/{where['id']}": where["name"] for where in entry[1]["environments"]})
    return found


def deployed(pid, created):
    """What a playground holds in the places besides This machine: [{place, environment (its name), created: [{what,
    count}]}] for each place that holds a record, in the order of the accounts (platforms by name, environments as each
    lists them, as last said: nothing is asked of a platform here, so the state never waits for one); a place not
    known yet comes after, by its id. `created` is playgrounds.created, which reads the record of a place (it takes
    this module, so this one cannot take it). The environment is named by the record's own file, else by its platform
    as last said, else by its id."""
    held = recorded(pid)
    if not held:
        return []
    said = _last_said()
    order = {place_id: rank for rank, place_id in enumerate(said)}
    found = [(place_id, created(pid, place_id)) for place_id in held]
    return [{"place": place_id, "environment": _named(pid, place_id) or said.get(place_id) or place_id.split("/")[1],
             "created": records}
            for place_id, records in sorted(found, key=lambda item: (order.get(item[0], len(order)), item[0]))
            if records]


# ---------------------------------------------------------------------------------------------------------------------
# A deploy
# ---------------------------------------------------------------------------------------------------------------------

def _split(place_id):
    """The platform and the environment a place id names, '<platform id>/<environment id>'. What a request sends that is no
    text is no place."""
    if not isinstance(place_id, str):
        raise paths.NotFound("No place")
    uid, _, environment = place_id.partition("/")
    return uid, environment


def verify(project, place_id):
    """The place as the platform says it now, for a deploy to check before any job: the platform is read again and Cloud
    Gate is asked about the Cloud Token. Answers the place, or raises with the reason that a deploy cannot go."""
    uid, environment = _split(place_id)
    invalidate(uid)
    found = place(project, place_id)
    if not found:
        raise paths.NotFound(f"No place {place_id}")
    if found["state"] == "unusable":
        with _lock:
            said = _cache.get(uid)
        raise ValueError(said[1]["message"] if said else found["reason"])
    if found["state"] == "missing":
        raise ValueError(found["reason"])
    try:
        platforms.prove(uid, environment)
    except cloud.CloudError as error:
        if error.status not in (401, 403):
            raise
        raise ValueError(NO_TOKEN["refused"][1].format(found["name"])) from None
    return found


def seed_env(project, place_id, playground):
    """What the seed of a playground needs to fill a place, as the settings of the seeder: the values, which hold the
    Cloud Token, for the caller to pass by name in the environment of one command, never in an argument, an answer, a
    log or a record. The platform and the environment are those of the place, the organization is the token's.

    The token reaches the place chosen and nothing else. A seed of its own, or seed data that reads a token, gets none
    (seed_refused: ValueError, before the token is read). And of an official seeder, which sends the token wherever the
    address it was given leads, none of the addresses is the author's: the management API, and the two it defaults to,
    where Gamma and the Automation API are served, are the place's Cloud Gate, and the path the seeder joins to them is its
    own (PINNED), so that the author's own value of any of them, which the policy cannot see the use of, is replaced by the
    name passed with the command. The proxy and the trust store that its HTTP library would honor are emptied by the caller
    in the same way (CLEARED)."""
    why = seed_refused(playground)
    if why:
        raise ValueError(f"{playground.get('name') or 'This playground'} cannot go to a place: {why}")
    uid, environment = _split(place_id)
    record = platforms.find(uid)
    token, claims = platforms.cloud_token(uid, environment)
    addon = platforms.gateway_addon(project, record["kind"])
    domains = addon["gateway"]["domains"] if addon else []
    gate = platforms.gate(record, claims)
    return {"APIM_MANAGEMENT_URL": gate, "AIM_AUTOMATION_URL": gate, "AIM_GAMMA_URL": gate,
            "APIM_MANAGEMENT_PATH": cloud.GATE, **PINNED,
            "APIM_TOKEN": token, "APIM_ORG": claims["organization"], "APIM_ENV": claims["environment"],
            **({"APIM_GATEWAY_URL": f"http://{domains[0]}"} if domains else {}),
            **({"GATEWAY_INTERNAL_URL": f"http://{domains[1]}"} if len(domains) > 1 else {}),
            "STATE_DIR": f"/state/{uid}/{environment}"}
