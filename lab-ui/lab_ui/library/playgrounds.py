"""Playgrounds: what starting one needs, its guide, what its seed created, and the jobs that start it, seed it again
and stop it, on This machine or on a place: an environment of a registered platform (places.py). A place runs none of
the core: the playground's apps and the gateway of the platform start here, and its seed runs apart and fills the
environment. What the seed created there is recorded in a folder of its own, so that a place and This machine never
share a record."""

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from ..docker import compose, images, plugins
from . import layers, model, project as projects, tested
from ..gravitee import license, places, platforms
from .. import paths
from ..catalogs import trust
from ..docker.compose import COMPOSE
from ..docker.jobs import JOBS, JobFailed, awaited
from ..paths import ENV_FILE, ROOT

CREATED_FILE = f"{platforms.STATE}/{places.RECORD}"   # where the seed records what it created (docs/playgrounds.md)
APPS, WITH_PLATFORM = "Apps and services", "Platform, apps and services"   # the phase of a start that runs them
GATEWAY, SEED = "Gateway", "Seed"   # the phases of a deploy to a place besides: the gateway connects, the seed runs
GATEWAY_WAIT, GATEWAY_POLL = 120, 3   # seconds a deploy waits for its gateway to connect to the platform; between asks
NOT_CONNECTED = "Hybrid gateway did not connect to Gravitee Cloud"
NOT_WEB = "The address to check must be an http or https URL"
NOT_CHECKED = "Not checked"   # a host requirement whose address a catalog chose and is not on this machine (N03)


def _folder(pid):
    return ROOT / paths.unit_folder("playground", pid)


def guide(pid):
    """The guide's markdown, read from disk, also for a playground whose compose file does not load; empty when it
    has none. It is read as a catalog's file is (project._read): a guide that is a link, a pipe or over a megabyte, or is
    not text, is none the Lab reads (ValueError)."""
    if pid not in paths.ids("playground"):
        raise paths.NotFound(f"No such playground: {pid}")
    text, why = projects._read(paths.unit_guide("playground", pid))
    if why and why != "missing":
        raise ValueError("The guide cannot be read: it is not a plain text file of the playground's folder")
    return text or ""


def _remote(place):
    """Whether a place is a remote one, an environment of a platform: anything but none and This machine."""
    return bool(place) and place != layers.MACHINE


def _segments(place):
    """The platform and the environment a remote place names, '<platform id>/<environment id>', which make the folder of
    its record: only letters, digits and dashes, so that a request cannot reach another folder."""
    uid, _, environment = place.partition("/") if isinstance(place, str) else ("", "", "")
    if not (platforms.ID.fullmatch(uid) and platforms.ID.fullmatch(environment)):
        raise paths.NotFound(f"No place {place}" if isinstance(place, str) else "No place")
    return uid, environment


def _record(pid, place=None):
    """The file where the seed records what it created: This machine's, or the one in the folder of a place, which
    the seed reaches as its STATE_DIR."""
    if not _remote(place):
        return _folder(pid) / CREATED_FILE
    return _folder(pid) / platforms.STATE / Path(*_segments(place)) / places.RECORD


def created(pid, place=None):
    """What the seed recorded as created in Gravitee, and only that: [{what, count}], under the label the seed gives
    each kind of thing. On This machine, or in the place given, each with a record of its own. A record that is no plain
    file of the playground's own folder is none (places.read_state): a container wrote it. Nor is it taken for more than
    what it can be: a list of objects with a text `label` and a whole `count` above zero (one when it says none); any other
    shape, or entry, is none. The state of the Lab is made of this for every playground, and must not end on a file."""
    file = places.RECORD if not _remote(place) else str(Path(*_segments(place)) / places.RECORD)
    try:
        items = json.loads(places.read_state(pid, file) or "")
    except (ValueError, RecursionError):   # nothing recorded, or text that is no record (a nest of brackets is too deep to read)
        return []
    counts = Counter()
    for item in items if isinstance(items, list) else ():
        label = item.get("label") if isinstance(item, dict) else None
        count = item.get("count", 1) if isinstance(item, dict) else None   # a record may stand for many: the entities of one type
        if isinstance(label, str) and type(count) is int and count > 0:
            counts[label] += count
    return [{"what": what, "count": count} for what, count in counts.items()]


def forget(pid):
    """Empties what a playground's seed recorded on This machine, as the seed itself does once it deleted it: after a
    reset of the stack, nothing it created is left in Gravitee. What it created in a place stays there, and so does its
    record. A record behind a link a container planted, which leads out of the playground's folder, is not written to:
    false, and the record stays; true otherwise, also when there was nothing to empty."""
    record = _folder(pid) / CREATED_FILE
    if not os.path.lexists(record):
        return True
    if not places.inside(pid, record):
        return False
    if record.is_file():
        paths.write_atomic(record, "[]")
    return True


def _remember(pid, place, environment):
    """Makes the folder of a place's record, which Docker would make as root, and writes beside the record the name of
    its environment, which nothing offline holds (places.deployed reads it). Not through a link a container planted:
    the job fails and nothing is written."""
    folder = _record(pid, place).parent
    if not places.inside(pid, folder):
        raise JobFailed("The state folder of the playground leads out of its folder: nothing was written there")
    folder.mkdir(parents=True, exist_ok=True)
    paths.write_atomic(folder / places.META, json.dumps({"environment": environment}) + "\n")


# ---------------------------------------------------------------------------------------------------------------------
# Before you start: one line per requirement
# ---------------------------------------------------------------------------------------------------------------------

def _line(name, state, detail, target=None, file=None, reason=None):
    """A line of the checks. `warn` is what needs you, `auto` what the start does for you, `ok` what is in place.
    `target`: the item it names ({kind, id}); `file`: the settings that answer it (a .env); `reason`: why, for what
    runs behind what is saved, which the page draws as the badge."""
    return {"name": name, "state": state, "detail": detail, **({"target": target} if target else {}),
            **({"file": file} if file else {}), **({"reason": reason} if reason else {})}


def _now(project, variable=None):
    """What runs now, read from Docker: (model.up(), what runs behind what is saved, the containers, with the
    digest of `variable` in their environment when asked)."""
    containers = compose.ps(variable) if variable else compose.ps()
    return model.up(project, containers), model.behind(project, containers, projects.saved(project)), containers


def _fresh(project, state, playground, now):
    """Whether its start now sets the playground up (layers.fresh): its seed recorded nothing, or failed. `now`:
    _now()."""
    seed = playground["seed"]
    states = {seed: model.service_state(now[2].get(seed), True, False)} if seed else {}
    return layers.fresh(project, state, playground["id"], created(playground["id"]),
                        model.seed_failed(states, playground))


def _start_plan(project, state, pid, now):
    """The plan of the playground's start now (`now`: _now()): its first, which sets it up, or one that runs it
    again."""
    return layers.plan(project, state, now[0], "playground", pid, "start", behind=now[1],
                       fresh=_fresh(project, state, project["playgrounds"][pid], now))


def _of_platform(project, row):
    """The services of the platform a row of a plan names: all of them, a group's, one; none for a row of anything
    else."""
    services = project["services"]
    if row["kind"] == "platform":
        return [name for name, service in services.items() if service["owner"]["kind"] == "platform"]
    if row["kind"] == "group":
        return next((group["services"] for group in project["groups"] if group["id"] == row["id"]), [])
    return [row["id"]] if row["kind"] == "service" and services[row["id"]]["owner"]["kind"] == "platform" else []


def _platform_lines(project, plan, up):
    """The platform first: what of it the start runs, the whole platform while nothing of it runs, else each group
    with something to start ("Created at start" when nothing of it has a container yet, "Started at start"); or that
    it runs."""
    starting = {name for row in plan["starts"] for name in _of_platform(project, row)}
    if not starting:
        return [_line(project["name"], "ok", "Running")] if up["platform"] else []
    if up["platform"]:
        units = [(group["name"], group["services"]) for group in project["groups"]
                 if starting & set(group["services"])]
    else:
        units = [(project["name"], _of_platform(project, {"kind": "platform"}))]
    return [_line(name, "auto", "Started at start" if set(names) & up["created"] else "Created at start")
            for name, names in units]


def _addon_lines(project, playground, state, up):
    """The add-ons it brings: one that lacks a setting keeps it from starting, and opens its settings; one that is
    off is turned on, one on that does not run all of it is started, one on that runs is running."""
    names = {aid: project["addons"][aid]["name"] for aid in playground["addons"] if aid in project["addons"]}   # one of a catalog that is gone is said by the note, not here
    lacking, on = set(names) & set(state["missing"]), set(state["effective"])

    def running(aid):
        lasting = [n for n in project["addons"][aid]["services"] if not project["services"][n]["oneshot"]]
        return bool(lasting) and set(lasting) <= up["services"]

    def line(aid, level, detail, file=None):
        return _line(names[aid], level, detail, {"kind": "addon", "id": aid}, file)
    return ([line(aid, "warn", "Needs settings", project["addons"][aid]["env_file"]) for aid in sorted(lacking)]
            + [line(aid, "auto", "Turned on at start") for aid in sorted(set(names) - on - lacking)]
            + [line(aid, "ok" if running(aid) else "auto", "Running" if running(aid) else "Started at start")
               for aid in sorted((set(names) & on) - lacking)])


def _addon_checks(project, playground, now=None, restarts=(), plan=None):
    """What its start does before anything else: the platform it runs with, the add-ons it brings, what it stops or
    turns off, what it restarts (the core services it changes, and `restarts`: the services a plugin install stops
    and starts again). One add-on that lacks a setting keeps it from starting. `plan`: its start's (_start_plan)."""
    state, pid = layers.current(project), playground["id"]
    now = now or _now(project)
    plan = plan or _start_plan(project, state, pid, now)
    restarting = [row["name"] for row in plan["restarts"]]
    for name in sorted(restarts):   # what a plugin install stops and starts again, said once
        label = layers.service_name(project, name)
        if label not in restarting:
            restarting.append(label)
    return (_platform_lines(project, plan, now[0]) + _addon_lines(project, playground, state, now[0])
            + _effect_lines(plan, restarting))


def _effect_lines(plan, restarting):
    """What a start stops, turns off and restarts, besides what it starts: `restarting` are the names of what
    restarts."""
    return ([_line(row["name"], "auto", "Stops at start", {"kind": row["kind"], "id": row["id"]})
             for row in plan["stops"]]
            + [_line(row["name"], "auto", "Turned off at start", {"kind": row["kind"], "id": row["id"]})
               for row in plan["turns_off"]]
            + [_line(name, "auto", "Restarts at start") for name in restarting])


def _plugin_checks(project, requires, up=None, fresh=True):
    """The plugins it needs, and the services an install would stop to load them, then start again: those of the
    product that run (`up`, model.up()). Only a start that sets the playground up installs them (`fresh`): one that
    runs it again, once it is set up, says what is missing, and stops nothing."""
    checks, restarts = [], set()
    for product, names in (requires.get("plugins") or {}).items():
        try:
            present = plugins.installed(project, product)
        except paths.NotFound:   # its folder is not mounted as the project's files say: the other checks still show
            checks += [_line(name, "warn", "Could not be checked") for name in names]
            continue
        for name in names:
            if name in present["bundled"] or name in present["ext"]:
                checks.append(dict(_line(name, "ok", "Installed"), plugin=name))
                continue
            if not fresh:
                checks.append(dict(_line(name, "warn", present["bundled_error"] or "Not installed"), plugin=name))
                continue
            if present["bundled_error"]:   # what the images bring is not known: the start pulls them, then sees
                checks.append(dict(_line(name, "auto", "Checked at start"), plugin=name))
            else:
                checks.append(dict(_line(name, "auto", "Installed at start"), plugin=name))
            loading = (project["products"].get(product) or {}).get("services") or {}
            restarts |= set(loading) & (up or {}).get("services", set())
    return checks, restarts


def _plugin_ids(definition):
    """Plugin ids an API definition uses: entrypoints, endpoints, policies, resources, plan security."""
    ids = set()

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("policy"), str):
                ids.add(node["policy"])
            for key in ("entrypoints", "endpointGroups", "endpoints", "resources"):
                items = [x for x in node.get(key) or [] if isinstance(x, dict)]
                ids.update(x["type"] for x in items if isinstance(x.get("type"), str))
            security = node.get("security")
            if isinstance(security, dict) and isinstance(security.get("type"), str):
                ids.add(security["type"].lower().replace("_", "-"))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(definition)
    return ids


def _features(project, playground):
    """The license features the playground needs, {feature: [what uses it]}: the ones it declares, and the ones of
    the plugins its APIs use."""
    by_id = {}
    for product in project["products"]:
        by_id.update(plugins.installed(project, product)["feature_by_id"])
    needed = {feature: [part] if part else []
              for feature, part in projects.license_features(playground["requires"]).items()}
    for path in paths.seed_apis(_folder(playground["id"])):
        text = projects._read(path.relative_to(ROOT).as_posix())[0]   # a link, a pipe or a huge file is no definition
        if text is None:
            continue
        definition = json.loads(text)
        name = definition.get("api", {}).get("name", path.stem)
        for plugin_id in sorted(_plugin_ids(definition)):
            if by_id.get(plugin_id):
                users = needed.setdefault(by_id[plugin_id], [])
                # An API published in several environments is one user: its name is said once
                if f"{name}: {plugin_id}" not in users:
                    users.append(f"{name}: {plugin_id}")
    return needed


def _license_checks(project, playground, containers=None):
    """What the license gives of the features the playground uses, in the words of its row: "Needs a license" (none,
    or one that cannot be used), "License expired", "Not in the license" (each feature it lacks), "Apply needed"
    (another license is saved than the one that runs: `containers`, as compose.ps(project.LICENSE_KEY) reads them);
    each opens the core's settings, where the license is."""
    result = license.info(project)
    needed = _features(project, playground)
    usable = result.get("present") and result.get("readable") and result.get("valid")
    # The parts of its guide that need it, when it is not the whole playground: "Needs a license for Step 4"
    parts = sorted({user for users in needed.values() for user in users if ":" not in user})
    whole = any(not users for users in needed.values())
    needs = "Needs a license" + ("" if whole or not parts else " for " + ", ".join(parts))
    # The line names what needs the license, the packs that give it: "License" and "Needs a license" would say one thing twice
    what = ", ".join(license.named(sorted(needed))) or "License"
    if needed and not usable:   # none, or one that cannot be read or is not GraviteeSource's
        return [_line(what, "warn", needs, file=ENV_FILE)]
    if needed and result.get("expired"):
        return [_line(what, "warn", "License expired", file=ENV_FILE)]
    allowed = license.allowed(result)
    if allowed is None:   # no product image to read the license model from: the other checks still show
        return [_line("License", "warn", result["model_error"])]
    lacking = [(feature, users) for feature, users in sorted(needed.items()) if feature not in allowed]
    if lacking:
        return [_line(", ".join(license.named([feature])), "warn",
                      "Not in the license" + (": " + ", ".join(users) if users else ""), file=ENV_FILE)
                for feature, users in lacking]
    found = license.status(project)
    unapplied = license.unapplied(project, containers, found["digest"]) if found and containers is not None else []
    if unapplied:
        return [_line("License", "warn", model.APPLY, file=ENV_FILE, reason=model.unapplied_line(project, unapplied))]
    return [_line("License", "ok", "Every feature used is included")]


def _tested_help(one):
    """What to do about an image it was not tested with: the version it was tested with, else any release."""
    return f"Tested with {one['tested']}" if one["tested"] else "Choose a release on the Images page"


def _image_checks(project, playground):
    """The image versions a playground needs at least (`images`: {version variable: version}), against the ones
    saved: below, what to choose on the Images page, before a start that would end on its seed. Then those it was
    never tested with (layers.untested), each with the version it was."""
    found = {component["id"]: component for component in images.components(project)}
    untested = {one["variable"]: one for one in (tested.untested(project, playground) or {}).get("images", [])}
    for key, wanted in (playground["requires"].get("images") or {}).items():
        component = found.get(key)
        if not component:   # make check says so
            continue
        tag = project["services"][component["services"][0]]["tag"]
        name = f"{component['detail'] or component['owner']['name']} {wanted}"
        if not layers.enough(playground, key, tag, wanted):
            yield dict(_line(name, "warn", f"Current: {tag}"), help=f"Choose {wanted}{layers.or_later(wanted)} on the Images page",
                       variable=key)
        elif key in untested:
            yield dict(_line(name, "warn", f"Not tested: {tag}"), help=_tested_help(untested.pop(key)), variable=key)
        else:
            yield dict(_line(name, "ok", tag), variable=key)
    for key, one in untested.items():   # an image it was tested with that it does not ask for a minimum of
        component = found.get(key)
        if component:
            yield dict(_line(f"{component['detail'] or component['owner']['name']}", "warn", f"Not tested: {one['have']}"),
                       help=_tested_help(one), variable=key)


def _memory_checks():
    """Docker's memory, when it is short: "6.0 GB, 8 GB+ recommended"."""
    short = model.memory_warning(compose.docker_memory())
    return [_line("Docker memory", "warn", short)] if short else []


def _host_check(spec):
    """A prerequisite on this machine, checked from here (the browser cannot reach it). Only an http or https address
    is opened: urllib also opens file: and ftp:. A proxy of the environment is not asked for this machine's own
    address, which it cannot reach."""
    try:
        address = urlsplit(spec["check"])
        local = address.hostname in ("localhost", "127.0.0.1", "::1")
    except ValueError:   # not an address
        return False, NOT_WEB
    if address.scheme not in ("http", "https"):
        return False, NOT_WEB
    opener = urllib.request.build_opener(*([urllib.request.ProxyHandler({})] if local else []))
    try:
        with opener.open(spec["check"], timeout=3) as response:
            body = response.read(200_000).decode(errors="replace")
    except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
        return False, f"Not reachable ({getattr(error, 'reason', error)})"
    if spec.get("expect") and spec["expect"] not in body:
        return False, f"Reachable, but {spec['expect']} is missing"
    return True, "Ready"


def _dialog(project, plan, restarts):
    """What its Start says before it runs (layers.ROWS): the rows of its plan, with the services a plugin install
    stops and starts again (`restarts`) among what restarts, and where it comes from when it comes from a catalog
    (layers.FROM); None when it does no more than the playground."""
    named = {row["id"] for row in plan["restarts"]}
    loading = layers.service_rows(project, set(restarts) - named)
    if not (layers.asks(plan) or loading):
        return None
    return {**{key: plan[key] for key in (*layers.ROWS, layers.FROM, "hints") if key in plan},
            "restarts": sorted(plan["restarts"] + loading, key=lambda row: row["id"])}


def _host_checks(playground):
    """Each host requirement, one that is not met with the `help` that says what to do. In a playground the policy judged,
    an address that is not on this machine (N03) is not opened, for a view never makes a request that a catalog chose: the
    line says it was not checked, with the help, for the person to see to it."""
    held = trust.line("N03") in ((playground.get("verdict") or {}).get("notes") or ())
    checks = []
    for spec in playground["requires"].get("host") or []:
        if held and not trust.local_address(spec.get("check")):
            checks.append(dict(_line(spec["name"], "warn", NOT_CHECKED), help=spec.get("help")))
            continue
        ok, detail = _host_check(spec)
        checks.append(dict(_line(spec["name"], "ok" if ok else "warn", detail), help=None if ok else spec.get("help")))
    return checks


def check(project, pid, place=None):
    """What starting the playground does, and what will not work: {checks: [{name, state, detail, target?, file?}],
    dialog}: what the start does to the platform, the add-ons and the rest, then the plugins, the license, Docker's
    memory when it is short, and each host requirement, one that is not met with the `help` that says what to do.
    `dialog` is what its Start says before it runs: what the state's says (its `action`), and what installing its
    plugins restarts, which only these checks read. For a place besides This machine, it is that place's
    (_place_checks)."""
    if _remote(place):
        return _place_checks(project, pid, place)
    playground = layers.lookup(project, "playground", pid)
    now = _now(project, projects.LICENSE_KEY)
    plan = _start_plan(project, layers.current(project), pid, now)
    needs, restarts = _plugin_checks(project, playground["requires"], now[0], plan["fresh"])
    checks = [*_addon_checks(project, playground, now, restarts, plan), *_image_checks(project, playground),
              *needs,
              *_license_checks(project, playground, now[2]), *_memory_checks(), *_host_checks(playground)]
    return {"checks": checks, "dialog": _dialog(project, plan, restarts)}


# ---------------------------------------------------------------------------------------------------------------------
# Before you start, on a place
# ---------------------------------------------------------------------------------------------------------------------

CLOUD_TOKEN = {"unset": "Not set", "expired": "Expired", "refused": "Refused"}   # what a Cloud Token lacks, in a line


def _gateway_addon(project, found):
    """The add-on that runs, on this machine, the gateway of the platform of a place."""
    addon = platforms.gateway_addon(project, found["platform"]["kind"])
    if not addon:
        raise paths.NotFound(f"No add-on runs a gateway of {found['platform']['name']}")
    return addon


def _serving(project):
    """{id, name} of the place that the gateway running here serves now; None when no platform says so yet (one that is
    still loading says nothing, which is not to say that none runs)."""
    served = places.serving(project)
    found = served and places.place(project, f"{served['platform']['id']}/{served['environment']['id']}")
    return {"id": found["id"], "name": found["name"]} if found else None


def _arrival(project, found, addon):
    """A place as layers.plan takes it: where it is, the add-on that runs its gateway and whether the gateway the add-on
    is set to run is this place's (`here`), and the place that one serves now."""
    return {"id": found["id"], "name": found["name"], "gateway": addon["id"],
            "here": bool(found["gateway"] and found["gateway"]["here"]), "serving": _serving(project)}


def _items(project, playground):
    """The files of a playground and of the add-ons it brings: what runs where it runs."""
    return [playground["file"], *(project["addons"][aid]["file"] for aid in playground["addons"] if aid in project["addons"])]


def _on_this_machine(project, playground):
    """What a start on this machine says of where the playground and its add-ons run: here."""
    return {file: None for file in _items(project, playground)}


def _on_platform(project, playground, kind, gateway):
    """What a deploy to a platform says of where the playground and its add-ons run (layers.write): there, written for the
    gateway that the platform runs here, which is not one of them."""
    return ({file: kind for file in _items(project, playground) if file != gateway["file"]},
            {kind: platforms.retarget_names(project, kind)})


def _unit_items(project, kind, unit, gateway):
    """The files of an add-on or a setup and of what it brings: what runs where it runs. A setup is its add-ons; the add-on
    of the gateway is none of them."""
    ids = list(unit["members"]) if kind == "setup" else [unit["id"]]
    ids += [uid for uid in layers.closure(project, [*ids, *layers.brought(unit)]) if uid not in ids]
    return [project["addons"][uid]["file"] for uid in ids if uid in project["addons"] and uid != gateway["id"]]


def _unit_plan(project, kind, uid, found, addon, now, state):
    """The plan of a start of an add-on or a setup that runs on a place: its job being the plan's."""
    return layers.plan(project, state, now[0], kind, uid, "start", behind=now[1], place=_arrival(project, found, addon))


def unit_checks(project, kind, uid, place):
    """The checks of a start of an add-on or a setup on a place: its platform, its Cloud Token, its gateway, and what the
    start stops, turns off and restarts. The core is not part of it. A platform that cannot be used is one line with the
    reason, and no dialog. Nothing is asked of the platform: it is what places.py last read."""
    unit = layers.lookup(project, kind, uid)
    found = places.place(project, place)
    if not found:
        raise paths.NotFound(f"No place {place}")
    target = {"kind": "platform", "id": found["platform"]["id"]}
    if found["state"] == "unusable":
        return {"checks": [_line(found["platform"]["name"], "warn", found["reason"], target)], "dialog": None}
    addon, state, now = _gateway_addon(project, found), layers.current(project), _now(project)
    plan = _unit_plan(project, kind, uid, found, addon, now, state)
    restarting = [row["name"] for row in plan["restarts"] if row["id"] != addon["id"]]
    checks = [_line(found["platform"]["name"], "ok", "Connected", target), _token_line(found),
              _gateway_line(found, addon, plan, state), *_effect_lines(plan, restarting)]
    return {"checks": checks, "dialog": layers.dialog(plan), "name": unit["name"]}


def deploy_unit(project, kind, uid, accept, place):
    """Starts an add-on or a setup on a place: the gateway of the platform and what the item needs of this machine's
    platform, then the item, written for that gateway (layers.write), none of the core. The place is asked now
    (places.verify) before any job; a gateway that serves another place takes this one's token first
    (platforms.swap_gateway). Waits for the platform to say that the gateway is connected."""
    unit = layers.lookup(project, kind, uid)
    state = layers.current(project)
    found = places.verify(project, place)
    why = found.get("cannot", {}).get(f"{kind}:{uid}")
    if why or found["platform"]["kind"] not in unit.get("deploys_to", []):
        why = why or unit.get("cannot", {}).get(found["platform"]["kind"])
        raise ValueError(f"{unit['name']} cannot go to {found['name']}" + (f": {why}" if why else ""))
    addon, now = _gateway_addon(project, found), _now(project)
    plan = _unit_plan(project, kind, uid, found, addon, now, state)
    layers.check(project, plan, accept)
    gateway, pid = found["gateway"], found["platform"]["id"]
    title = f"{plan['label']} {unit['name']} in {found['name']}"
    files = _unit_items(project, kind, unit, addon)
    where = ({file: found["platform"]["kind"] for file in files},
             {found["platform"]["kind"]: platforms.retarget_names(project, found["platform"]["kind"])})

    def addons(job):
        job.step("Add-ons")
        if not gateway["here"]:
            try:
                platforms.swap_gateway(project, pid, gateway["id"])
            except (ValueError, paths.NotFound) as error:
                raise JobFailed(str(error)) from None
        layers.write(plan["playground"], plan["explicit"], *where, blocked=layers.blocked_files(project))
        layers.make_binds(project, plan)
        for part, said in (("starts", "Starts"), ("stops", "Stops"), ("turns_off", "Turns off"), ("restarts", "Restarts")):
            if plan[part]:
                job.lines.append(f"{said}: " + ", ".join(row["name"] for row in plan[part]))
        job.lines.append(f"Runs on: {found['name']}")

    named = plan["services"]["start"]

    def apps(job):
        job.step(APPS)
        here = [name for name in named if project["services"].get(name, {}).get("owner", {}).get("kind") == "platform"]
        if here:
            job.exec([*COMPOSE, "up", "-d", *here])
        apart = [name for name in named if name not in here]
        if apart:
            job.exec([*COMPOSE, "up", "-d", "--no-deps", *apart])

    def connected(job):
        job.step(GATEWAY)
        _wait_connected(job, pid, gateway["id"])

    running = bool(named)
    steps = [addons, *([apps] if running else []), connected]
    phases = ([APPS] if running else []) + [GATEWAY]
    waits = [awaited(project["services"], start=named)] if running else []
    return JOBS.start(title, steps, key=f"{kind}:{uid}", verb="start", changes=layers.changes(project, plan),
                      phases=phases, waits=waits)


def _place_plan(project, pid, found, addon, now, state, seeds):
    """The plan of a start that deploys to a place, `seeds` whether its seed runs there, its job being the plan's."""
    return layers.plan(project, state, now[0], "playground", pid, "start", fresh=seeds, behind=now[1],
                       place=_arrival(project, found, addon))


def _token_line(found):
    """The Cloud Token of the place: valid, or what is wrong with it, which the line offers to fix (`fix`)."""
    state = found["token"]["state"]
    if state == "valid":
        return _line("Cloud Token", "ok", "Valid")
    return dict(_line("Cloud Token", "warn", CLOUD_TOKEN[state]), fix="token")


def _gateway_line(found, addon, plan, state):
    """The gateway of the place, the add-on that runs it: what a start does to it, or what is wrong with it. With none
    in the environment, the line offers to create one (`fix`)."""
    gateway, target = found["gateway"], {"kind": "addon", "id": addon["id"]}
    if not gateway:
        return dict(_line(addon["name"], "warn", f"None in {found['environment']['name']}"), fix="gateway")
    if not gateway["kept"]:
        return _line(addon["name"], "warn", "Its token is not kept here", target, addon["env_file"])
    if any(row["id"] == addon["id"] for row in plan["restarts"]):
        return _line(addon["name"], "auto", f"Switches to {found['environment']['name']}", target)
    if any(row["id"] == addon["id"] for row in plan["starts"]):
        return _line(addon["name"], "auto", "Started at start" if addon["id"] in state["effective"]
                     else "Turned on at start", target)
    if gateway["connection"] != "CONNECTED":
        return _line(addon["name"], "warn", "Not connected", target)
    return _line(addon["name"], "ok", "Running", target)


def _place_checks(project, pid, place):
    """The checks of a start that deploys to a place: its platform, its Cloud Token, its gateway, the add-ons the
    playground brings and the host requirements, and what the start stops. The core is not part of it, so none of its
    lines is here. A platform that cannot be used is one line with the reason, and no dialog. Nothing is asked of the
    platform: it is what places.py last read."""
    playground = layers.lookup(project, "playground", pid)
    found = places.place(project, place)
    if not found:
        raise paths.NotFound(f"No place {place}")
    target = {"kind": "platform", "id": found["platform"]["id"]}
    if found["state"] == "unusable":
        return {"checks": [_line(found["platform"]["name"], "warn", found["reason"], target)], "dialog": None}
    addon, state, now = _gateway_addon(project, found), layers.current(project), _now(project)
    plan = _place_plan(project, pid, found, addon, now, state, not created(pid, place))
    restarting = [row["name"] for row in plan["restarts"] if row["id"] != addon["id"]]
    checks = [_line(found["platform"]["name"], "ok", "Connected", target), _token_line(found),
              _gateway_line(found, addon, plan, state), *_addon_lines(project, playground, state, now[0]),
              *_effect_lines(plan, restarting), *_host_checks(playground)]
    return {"checks": checks, "dialog": layers.dialog(plan)}


# ---------------------------------------------------------------------------------------------------------------------
# Jobs: start, re-seed, stop
# ---------------------------------------------------------------------------------------------------------------------

def _plan(project, state, now, pid, do, accept, fresh=False):
    """The plan of a start or a stop (`now`: _now()), refused when it cannot run as the page showed it: blocked, or
    stopping or turning off something else that was not accepted."""
    plan = layers.plan(project, state, now[0], "playground", pid, do, fresh=fresh, behind=now[1])
    layers.check(project, plan, accept)
    return plan


def _seed_waits(project, state, up, playground):
    """Refuses to run the seed while what it needs does not run: it would wait for it, then fail."""
    reason = model.seed_reason(project, state, up, playground)
    if reason:
        raise RuntimeError(reason)


def _job(title, steps, pid, verb, phases, waits, changes=None):
    """A job on a playground: `verb` is start, reseed or stop. `phases` are the ones that take time: its progress
    counts them, each by what it waits for (`waits`, in their order). `changes` is what it starts and stops: their
    rows say so meanwhile."""
    return JOBS.start(title, steps, key=f"playground:{pid}", verb=verb, changes=changes, phases=phases, waits=waits)


def _seed_reason(line):
    """Why the seed failed, from the last line it printed: what follows "✗" in "[seed] ✗ <why>", or the end of a
    traceback; "The seed failed" when it printed nothing."""
    return line.split("✗", 1)[-1].strip() or "The seed failed"


def _follow_seed(project, playground):
    """A step that streams the seed's output until it ends, and fails the job when the seed failed, with the reason
    it printed last (`_seed_reason`). Only the output of its last start: a seed that failed runs again in the same
    container, which still holds the output of the run before."""
    container = project["services"][playground["seed"]]["container"]

    def follow(job):
        if job.phase != "Seed":
            job.step("Seed")
        start = len(job.lines)
        rc, out, _ = compose.run(["docker", "inspect", "-f", "{{.State.StartedAt}}", container])
        since = ["--since", out.strip()] if rc == 0 and out.strip() else []
        job.exec(["docker", "logs", "-f", *since, container], quiet=True)
        rc, out, _ = compose.run(["docker", "inspect", "-f", "{{.State.ExitCode}}", container])
        if rc != 0 or out.strip() != "0":
            raise JobFailed(_seed_reason(job.last_line(start)))
    return follow


def start(project, pid, accept, place=None, remove=False):
    """Starts a playground. While it is off, or on and never seeded, or its seed failed (layers.fresh), the start
    sets it up: its add-ons, the plugins it needs, its apps and services with what of the platform they need, the
    whole platform while none of it runs ("Platform, apps and services" when the platform starts too), then its
    seed. On and seeded, it runs again like an add-on: what of it does not run, without a build, without its seed
    (Re-seed seeds it again). Its job is named as its button: "Start Acme Bakery", "Turn on …". For a place besides
    This machine it is _deploy's. `remove`: the playground that this one stops has what it created deleted first, as its
    own stop would (it is kept otherwise)."""
    if _remote(place):
        return _deploy(project, pid, accept, place, False, remove)
    playground = layers.lookup(project, "playground", pid)
    state, now = layers.current(project), _now(project)
    fresh = _fresh(project, state, playground, now)
    plan = _plan(project, state, now, pid, "start", accept, fresh)
    delete = _leaving(project, state, plan, remove)
    title = f"{plan['label']} {playground['name']}"
    if not fresh:
        return layers.apply(project, plan, title)
    phase = WITH_PLATFORM if any(_of_platform(project, row) for row in plan["starts"]) else APPS

    def addons(job):
        job.step("Add-ons")
        layers.write(plan["playground"], plan["explicit"], _on_this_machine(project, playground),
                     blocked=layers.blocked_files(project))
        for part, verb in (("stops", "Stops"), ("turns_off", "Turns off")):
            if plan[part]:
                job.lines.append(f"{verb}: " + ", ".join(row["name"] for row in plan[part]))
        names = [project["addons"][aid]["name"] for aid in playground["addons"] if aid in project["addons"]]
        job.lines.append("Add-ons: " + (", ".join(names) or "none"))

    installs, changes, loads, stopped = [], layers.changes(project, plan), {}, []
    for product, names in (playground["requires"].get("plugins") or {}).items():
        needed, halted, waits = plugins.install_steps(project, product, names)
        installs += needed
        loads.update(waits)
        stopped += halted
        # A product stopped to load its plugins starts again with the apps: it shows as starting
        changes["start"] += [["service", name] for name in halted]
    # What goes is removed by name; then, by name too, what the plan starts and restarts, the seed included, and what
    # was stopped to load its plugins. Nothing else: what was started outside the Lab UI stays
    remove, named = plan["services"]["remove"], sorted(set(plan["services"]["start"]) | set(stopped))

    def apps(job):
        job.step(phase)
        # The seed records what it creates in a folder it mounts: made here, Docker would create it as root, and so is
        # every other folder a bind of its units names (layers.make_binds)
        (_folder(pid) / CREATED_FILE).parent.mkdir(exist_ok=True)
        layers.make_binds(project, plan)
        if remove:
            job.exec([*compose.by_name(), "rm", "--stop", "--force", *remove])
        if named:   # Compose reads an empty list as every service
            job.exec([*COMPOSE, "up", "-d", "--no-deps", *named])

    seed = [_follow_seed(project, playground)] if playground["seed"] else []
    # Once all of it ended well: the versions it started on are the ones that worked here
    kept = [lambda job: tested.record(project, pid)]
    steps = [addons, lambda job: job.step("Plugins"), *installs, apps, *seed, *kept]
    phases = (["Plugins"] if installs else []) + [phase] + (["Seed"] if seed else [])
    # The apps' phase waits for what it removes and what it starts, the seed ready once it ended well. Following the
    # seed has nothing to count
    waits = ([loads] if installs else []) + [awaited(project["services"], start=named, stop=remove)] \
        + ([None] if seed else [])
    steps, phases, waits = _before(delete, steps, phases, waits)
    return _job(title, steps, pid, "start", phases, waits, changes)


def reseed(project, pid, place=None, accept=None):
    """Runs the seed of the playground that is on again, once what it needs runs. For a place besides This machine it
    is _deploy's, which brings the gateway first if it does not run: `accept` is what the page showed that it stops."""
    if _remote(place):
        return _deploy(project, pid, accept or [], place, True)
    playground = layers.lookup(project, "playground", pid)
    state = layers.current(project)
    if state["playground"] != pid:
        raise RuntimeError(f"{playground['name']} is off")
    if not playground["seed"]:
        raise RuntimeError(f"{playground['name']} has no seed")
    _seed_waits(project, state, model.up(project, compose.ps()), playground)
    # Its image built and its container made again, which Compose measures; the container is ready once the seed
    # ended well, which following its output has nothing more to count for
    # Once the seed ended well, the playground works on the versions it runs on: what a start that failed on its seed, then fixed, leaves
    steps = [lambda job: job.step("Start the seed"),
             [*COMPOSE, "up", "-d", "--force-recreate", "--no-deps", playground["seed"]],
             _follow_seed(project, playground),
             lambda job: tested.record(project, pid)]
    waits = [awaited(project["services"], start=[playground["seed"]]), None]
    return _job(f"Re-seed {playground['name']}", steps, pid, "reseed", ["Start the seed", "Seed"], waits)


def _gone(place):
    """Why a place that holds a record cannot be reached any more."""
    uid = _segments(place)[0]
    return "Account not connected" if not any(record["id"] == uid for record in platforms.read()) \
        else "Environment not found"


def _reachable(project, playground, state):
    """The places besides This machine that hold a record of what the seed created, each as (place, its name), once the
    platform says now that it can be reached (places.verify). The first that cannot be gives its reason. Deleting
    from a place runs the seed, which is a service of the project only while the playground is on."""
    recorded = [place for place in places.recorded(playground["id"]) if created(playground["id"], place)]
    if recorded and state["playground"] != playground["id"]:
        raise RuntimeError(f"{playground['name']} is off")
    found = []
    for place in recorded:
        try:
            found.append((place, places.verify(project, place)["name"]))
        except paths.NotFound:
            raise ValueError(_gone(place)) from None
    return found


def _delete_created(project, playground, elsewhere):
    """The step that deletes what the seed of a playground created in Gravitee, on This machine and in the places that
    hold a record (`elsewhere`, _reachable): the seed runs with --remove, which replaces or deletes only what its record says
    it created. The image that seeded is the one it runs from (--pull=missing): a delete never builds, whatever the item's
    pull policy says of a start. A stop and a start that stops a playground share it."""
    def delete(job):
        job.step("Delete what it created")
        pid = playground["id"]
        local, recorded = created(pid), [(where, name) for where, name in elsewhere if created(pid, where)]
        if not (local or recorded):
            job.lines.append("Nothing recorded as created by this playground")
            return
        if local:
            try:   # its own file loaded, so that it deletes whatever else is on: a playground that is off has no seed service in the files that are
                job.exec([*COMPOSE, *compose.all_files([playground["file"]]), "run", "--rm", "--no-deps", "--pull=missing",
                          playground["seed"], "--remove"])
            except JobFailed as failed:   # "[seed] ✗ <why>": its reason, as a start says it
                raise JobFailed(_seed_reason(str(failed))) from None
        for where, name in recorded:
            job.lines.append(f"Deletes from {name}")
            _seed_apart(job, project, playground, where, remove=True)
    return delete


def _leaving(project, state, plan, remove):
    """What a start that stops another playground does with what that one created, when asked to delete it (`remove`): the
    step that deletes it, first in the job, as a stop would; else None, which keeps it. Refused as a stop is: while what
    its seed needs does not run, or when a place that holds a record cannot be reached."""
    row = next((row for row in plan["stops"] + plan["turns_off"] if row["kind"] == "playground"), None)
    leaving = project["playgrounds"].get(row["id"]) if row else None
    if not (remove and leaving and leaving["seed"]):
        return None
    if created(leaving["id"]):
        _seed_waits(project, state, model.up(project, compose.ps()), leaving)
    return _delete_created(project, leaving, _reachable(project, leaving, state))


def _before(step, steps, phases, waits):
    """The job's steps, its phases and its waits with a step that deletes what a playground created before them: it says
    nothing of how far it is."""
    return ([step] + steps if step else steps), (["Delete what it created"] + phases if step else phases), \
        ([None] + waits if step else waits)


def stop(project, pid, remove, accept, place=None):
    """Stops a playground: its apps, and the add-ons only it uses, removed by name; the core services it changed
    restart. Off after a stop that failed, it stops again what of it still runs. `remove` first deletes what its seed
    created in Gravitee, on This machine and in every place that holds a record, refused while what the seed needs
    does not run, or when a place cannot be reached, with the reason of the first one; when a delete fails the job ends
    there and nothing else changes. Its job is named as its button: "Stop …", "Turn off …". A stop is the same for
    every place: `place` is the one the page was on, and changes nothing of it."""
    playground = layers.lookup(project, "playground", pid)
    state, now = layers.current(project), _now(project)
    plan = _plan(project, state, now, pid, "stop", accept)
    if remove and playground["seed"] and created(pid):
        _seed_waits(project, state, now[0], playground)
    elsewhere = _reachable(project, playground, state) if remove and playground["seed"] else []

    delete = _delete_created(project, playground, elsewhere)

    def stop_apps(job):
        job.step("Stop the apps")
        layers.write(plan["playground"], plan["explicit"])
        for command in layers.commands(plan):
            job.exec(command)

    deletes = bool(remove and playground["seed"])
    steps = ([delete] if deletes else []) + [stop_apps]
    phases = (["Delete what it created"] if deletes else []) + ["Stop the apps"]
    # The delete is one run of the seed, which says nothing of how far it is
    stops = {unit: how for found in layers.waits(project, plan) for unit, how in found.items()}
    waits = ([None] if deletes else []) + [stops]
    return _job(f"{plan['label']} {playground['name']}", steps, pid, "stop", phases, waits,
                layers.changes(project, plan))


# ---------------------------------------------------------------------------------------------------------------------
# Jobs: deploy to a place, delete from one
# ---------------------------------------------------------------------------------------------------------------------

def _seed_apart(job, project, playground, place, remove=False):
    """Runs the seed of a playground on a place, apart from the containers of this machine: `docker compose run --rm
    --no-deps`, which starts nothing it depends on, with the settings of the place passed by name (`-e NAME`) and
    their values, the Cloud Token among them, in the environment of that one command, never in the command, a line of
    the job or a record. A name passed so replaces the author's own value of it, and a seed that must not hold the token
    (places.seed_refused) is refused before any is read. The names a proxy and a trust store are taken from are passed empty
    (`-e NAME=`, places.CLEARED): they hold no secret, and an empty value is the one that is certain to replace the
    author's. Its record is in the folder of the place (_remember). Fails with the seed's last line."""
    try:
        values = places.seed_env(project, place, playground)
    except (ValueError, paths.NotFound) as error:   # a seed that must not hold the token, a Cloud Token that expired since the request, a platform gone
        raise JobFailed(str(error)) from None
    named = [word for name in sorted(values) for word in ("-e", name)]
    named += [word for name in places.CLEARED if name not in values for word in ("-e", f"{name}=")]
    try:
        job.exec([*COMPOSE, "run", "--rm", "--no-deps", *(["--pull=missing"] if remove else []), *named, playground["seed"],
                  *(["--remove"] if remove else [])],
                 env=values)
    except JobFailed as failed:   # "[seed] ✗ <why>": its reason, as a start says it
        raise JobFailed(_seed_reason(str(failed))) from None


def _connected(uid, gateway):
    """Whether the platform says now that a gateway is connected to it. A read that fails says no, for now."""
    try:
        found = platforms.fetch(platforms.find(uid))
    except paths.NotFound as error:   # the platform was forgotten since
        raise JobFailed(str(error)) from None
    return any(item["id"] == gateway and item["connection"] == "CONNECTED" for item in found["gateways"])


def _wait_connected(job, uid, gateway):
    """Waits for the platform to say that the gateway is connected, which it is some seconds after it started, when it
    has pulled its APIs: the seed ends with the API served, so it would wait for it. Gives up after GATEWAY_WAIT."""
    if _connected(uid, gateway):
        return
    job.lines.append("Waiting for the hybrid gateway to connect to Gravitee Cloud")
    end = time.monotonic() + GATEWAY_WAIT
    while time.monotonic() < end and not job.canceled:
        time.sleep(GATEWAY_POLL)
        if _connected(uid, gateway):
            return
    if not job.canceled:
        raise JobFailed(NOT_CONNECTED)


def _cannot(playground, place):
    """Refuses a playground that cannot go to a place, with the reason that the picker gives: what the place says of it
    (judged against what its environment runs), else what its files say of the kind of its platform."""
    why = place.get("cannot", {}).get(playground["id"])
    if why or place["platform"]["kind"] not in playground.get("deploys_to", []):
        why = why or playground.get("cannot", {}).get(place["platform"]["kind"])
        raise ValueError(f"{playground['name']} cannot go to {place['name']}" + (f": {why}" if why else ""))


def _deploy(project, pid, accept, place, reseed, remove=False):
    """Deploys a playground to a place: a start (`reseed` false), or a Re-seed or Deploy of the one that is on. The
    place is asked now (places.verify) before any job. The job starts the apps and the gateway of the platform here,
    none of the core, with the gateway set to serve this place (platforms.swap_gateway) unless it is already, waits for
    the platform to say it is connected, then runs the seed apart. A start seeds only a place that holds no record, as
    the start of a playground that is set up runs it again without its seed; a Re-seed or a Deploy always seeds. The
    job is a "deploy" when the place holds no record and the playground is on, else a "reseed" (the page's button says
    which)."""
    playground = layers.lookup(project, "playground", pid)
    if not playground["seed"]:
        raise RuntimeError(f"{playground['name']} has no seed")
    state = layers.current(project)
    if reseed and state["playground"] != pid:
        raise RuntimeError(f"{playground['name']} is off")
    found = places.verify(project, place)
    _cannot(playground, found)
    addon, recorded, now = _gateway_addon(project, found), bool(created(pid, place)), _now(project)
    seeds = reseed or not recorded
    plan = _place_plan(project, pid, found, addon, now, state, seeds)
    layers.check(project, plan, accept)
    delete = _leaving(project, state, plan, remove and not reseed)
    gateway, uid = found["gateway"], found["platform"]["id"]
    if not reseed:
        verb, title = "start", f"{plan['label']} {playground['name']} in {found['name']}"
    elif recorded:
        verb, title = "reseed", f"Re-seed {playground['name']} in {found['name']}"
    else:
        verb, title = "deploy", f"Deploy {playground['name']} to {found['name']}"

    def addons(job):
        job.step("Add-ons")
        if not gateway["here"]:   # the add-on is set to run another gateway, or none that exists: it takes this one's
            try:
                platforms.swap_gateway(project, uid, gateway["id"])
            except (ValueError, paths.NotFound) as error:
                raise JobFailed(str(error)) from None
        layers.write(plan["playground"], plan["explicit"], *_on_platform(project, playground, found["platform"]["kind"], addon),
                     blocked=layers.blocked_files(project))
        layers.make_binds(project, plan)
        for part, said in (("starts", "Starts"), ("stops", "Stops"), ("turns_off", "Turns off"),
                           ("restarts", "Restarts")):
            if plan[part]:
                job.lines.append(f"{said}: " + ", ".join(row["name"] for row in plan[part]))
        job.lines.append(f"Deploys to: {found['name']}")

    remove, named = plan["services"]["remove"], plan["services"]["start"]

    def apps(job):
        job.step(APPS)
        if remove:
            job.exec([*compose.by_name(), "rm", "--stop", "--force", *remove])
        # What of this machine's platform the playground needs (Access Management and its database) starts with what it
        # depends on, as it does on this machine: Compose waits for each to be healthy before the next
        here = [name for name in named if project["services"].get(name, {}).get("owner", {}).get("kind") == "platform"]
        if here:
            job.exec([*COMPOSE, "up", "-d", *here])
        named_apart = [name for name in named if name not in here]
        if named_apart:   # Compose reads an empty list as every service
            job.exec([*COMPOSE, "up", "-d", "--no-deps", *named_apart])

    def connected(job):
        job.step(GATEWAY)
        _wait_connected(job, uid, gateway["id"])

    def seed(job):
        job.step(SEED)
        _remember(pid, place, found["environment"]["name"])
        _seed_apart(job, project, playground, place)

    running = bool(remove or named)
    steps = [addons, *([apps] if running else []), connected, *([seed] if seeds else [])]
    phases = ([APPS] if running else []) + [GATEWAY] + ([SEED] if seeds else [])
    waits = [awaited(project["services"], start=named, stop=remove)] if running else []
    steps, phases, waits = _before(delete, steps, phases, waits)
    return _job(title, steps, pid, verb, phases, waits, None if reseed else layers.changes(project, plan))


def delete_place(project, pid, place):
    """Deletes what the seed of a playground created in one place besides This machine, and empties its record there.
    Refused when the playground is off, because the seed is a service of the project only while it is on, when the place
    holds no record, and with the reason the place cannot be reached (places.verify, asked now). When it fails the job
    ends with the seed's last line, and the record stays."""
    playground = layers.lookup(project, "playground", pid)
    _segments(place)
    if not playground["seed"]:
        raise RuntimeError(f"{playground['name']} has no seed")
    if layers.current(project)["playground"] != pid:
        raise RuntimeError(f"{playground['name']} is off")
    if not created(pid, place):
        raise RuntimeError("Nothing recorded as created there")
    try:
        found = places.verify(project, place)
    except paths.NotFound:
        raise ValueError(_gone(place)) from None

    def delete(job):
        job.step("Delete what it created")
        _seed_apart(job, project, playground, place, remove=True)
    return _job(f"Delete what {playground['name']} created in {found['name']}", [delete], pid, "delete",
                ["Delete what it created"], [None])
