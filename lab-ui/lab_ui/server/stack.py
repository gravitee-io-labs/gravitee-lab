"""The actions of the Containers page: start, apply, stop, restart, update, pull, remove and reset of everything that is
on, and what an apply recreates; start, stop and restart of a platform group, of the containers of an add-on or a
playground, or of a service."""

from ..docker import compose
from ..gravitee import core, places, platforms
from ..library import layers, model, playgrounds, project as projects
from .. import paths
from ..docker.compose import COMPOSE
from ..docker.jobs import JOBS, awaited

PULL = [*COMPOSE, "pull", "--ignore-buildable"]   # the images of what is on, but those built here


def _units(state):
    """What is on, as the [kind, id] pairs a job says it starts or stops."""
    units = [["addon", uid] for uid in state["effective"]]
    units += [["playground", state["playground"]]] if state["playground"] else []
    return [["platform", "platform"], *units]


def _pulled(project, units):
    """The images `pull --ignore-buildable` pulls: those of the services that are on, but the ones built here."""
    services = project["services"]
    return sorted({services[name]["image"] for name in layers.changing(project, {"start": units})
                   if not services[name]["built"]})


def _running_on(project, state):
    """The services `up` runs: those of what is on, or, with an on file with the user's own changes, every one
    Compose reads in it, but a seed: a seed runs when its playground is set up, or seeded again. Never what needs a
    license that is not usable."""
    if state["locked"]:
        return sorted(name for name, service in compose.resolved([]).items()
                      if (service.get("labels") or {}).get("io.gravitee.role") != "seed"
                      and not layers.held_back(project, name))
    return layers.up_services(project, state["playground"], state["effective"])


def _states(project, names, containers):
    """The state of these services (model.service_state), from their containers."""
    services = project["services"]
    return {name: model.service_state(containers.get(name), (services.get(name) or {}).get("oneshot"), False)
            for name in names}


def _start(project):
    """Runs the platform and everything that is on, what was stopped on purpose too, but a seed; what was started
    outside the Lab UI is left as it is. It waits for the containers of what is on that it names."""
    state = layers.current(project)
    units, services = _units(state), _running_on(project, state)
    return JOBS.start("Start the stack", [[*COMPOSE, "up", "-d", *services]], key="platform", verb="start",
                      changes={"start": units}, waits=[layers.awaits(project, {"start": units}, services)])


def _restart(project):
    """Restarts what runs of what is on, as it is: what was stopped (from the Stack, a group or the whole) stays
    stopped, what failed or ended is not run again, nothing is made again. Its job starts and waits for those
    containers only: a restart shows as starting, its containers stopping on the way, which is not an issue."""
    state, containers = layers.current(project), compose.ps()
    names = [name for name in _running_on(project, state)
             if model.service_state(containers.get(name), False, False)["running"]]
    if not names:   # Compose reads an empty list as every service
        raise RuntimeError("Nothing runs")
    services = project["services"]
    # A service that only the user's own on file adds is not one of the project's: it is not measured
    return JOBS.start("Restart the stack", [[*COMPOSE, "restart", *names]], key="platform", verb="restart",
                      changes={"start": [["service", name] for name in names]},
                      waits=[awaited(services, start=[name for name in names if name in services])])


def _applies(project, saved=None, maybe=()):
    """layers.applies() now: what an apply makes again and starts again, and whether what runs behind what is saved
    was known (`exact`). What it makes again: what runs behind what is saved (project.saved(), or `saved` in its
    place: a change not saved yet), and what of `maybe` runs (what a change may make again, when that cannot be
    known). Without the hashes Compose makes each service from, what runs behind is not known: every service that
    runs is named, Compose recreating what changed, and that is not exact."""
    state, containers = layers.current(project), compose.ps()
    on, saved = _running_on(project, state), saved or projects.saved(project)
    late = model.behind(project, containers, saved) if saved["hashes"] else None
    # What runs behind what is saved is made again, a service the Lab UI does not start for want of a license included
    # (one that kept running after the license went): the banner says so
    on = sorted({*on, *(late or ())})
    states = _states(project, on, containers)
    if maybe:
        late = (late or set()) | {name for name in maybe if states.get(name, {}).get("running")}
    return {**layers.applies(project, on, states, late), "exact": bool(saved["hashes"])}


def _apply(project):
    """Applies what was saved to what runs (layers.applies): `up --no-deps` makes again what runs behind what is saved
    and starts again what failed, after the one-shots they depend on. What was stopped (from the Stack, a group or the
    whole) stays stopped, what has no container is not created, what was started outside the Lab UI stays: nothing is
    removed as an orphan. While nothing of what is on runs, nothing does: what was saved applies at the next start. Not
    while the old layout is being moved (layers.ready): the Stack's Start, Stop and Remove need no file and stay."""
    layers.ready()
    found = _applies(project)
    names, services = found["names"], project["services"]

    def say(job):
        for part, verb in (("restarts", "Restarts"), ("starts", "Starts")):
            if found[part]:
                job.lines.append(f"{verb}: {', '.join(row['name'] for row in found[part])}")
    if not found["runs"]:
        steps = [lambda job: job.lines.append("Nothing runs: applied on next start")]
    elif not names:   # Compose reads an empty list as every service
        steps = [lambda job: job.lines.append("Nothing to apply")]
    else:
        steps = [say, [*COMPOSE, "up", "-d", "--no-deps", *names]]
    # A service that only the user's own on file adds is not one of the project's: it is not measured
    waits = [awaited(services, start=[name for name in names if name in services])] if names else []
    return JOBS.start("Apply changes", steps, key="platform", verb="apply",
                      changes={"start": [["service", n] for n in names]}, waits=waits)


def _update(project):
    """Pulls the images of what is on, then makes again what runs with them, as an apply would once they are here:
    which ones changed is not known before the pull, so every service that runs is named (layers.applies, what runs
    behind not known), and Compose makes again what changed, building or pulling what a playground builds as its pull
    policy says. What was stopped stays stopped; what failed starts again, as an apply starts it. Refused while nothing
    of what is on runs: it would only pull, which Pull images does."""
    state, containers = layers.current(project), compose.ps()
    on = _running_on(project, state)
    found = layers.applies(project, on, _states(project, on, containers), None)
    if not found["runs"]:
        raise RuntimeError("Nothing runs")
    units, names, services = _units(state), found["names"], project["services"]
    # A service that only the user's own on file adds is not one of the project's: it is not measured
    return JOBS.start("Update the stack", [PULL, [*COMPOSE, "up", "-d", "--no-deps", *names]],
                      key="platform", verb="update", changes={"start": [["service", n] for n in names]},
                      waits=[awaited(services, images=_pulled(project, units)),
                             awaited(services, start=[name for name in names if name in services])])


def effects(project, hashes=None, maybe=()):
    """What an apply would make again and start again now: {restarts, starts, runs, exact} (layers.applies), with
    `hashes` in place of what is saved when given (files.draft_hashes, files.env_hashes: what a Save & apply saves
    first), and `maybe` (files.affected) counted as behind when they run: what a change may make again, when that
    cannot be known. `exact` is false when what Compose makes each service from could not be asked: every service that
    runs then counts."""
    saved = projects.saved(project)
    found = _applies(project, {**saved, "hashes": hashes} if hashes is not None else saved, maybe)
    return {key: found[key] for key in ("restarts", "starts", "runs", "exact")}


def stays(project):
    """What a reset does not reach: what the seeds created in the platforms, which is still there and still recorded,
    one line for each place that holds a record, "Stays: Acme Hostels in Development (My Account)"."""
    held = {pid: places.deployed(pid, playgrounds.created) for pid in project["playgrounds"]}
    if not any(held.values()):
        return []
    names = {record["id"]: record["name"] for record in platforms.read()}
    lines = []
    for pid, found in held.items():
        for record in found:
            uid = record["place"].partition("/")[0]
            where = places.label(names.get(uid, uid), record["environment"])
            lines.append(f"Stays: {project['playgrounds'][pid]['name']} in {where}")
    return lines


def _forget(project):
    """The step after a reset: its volumes gone, nothing any seed created on this machine is left in Gravitee. The
    playground that is on is turned off, its next start sets it up again with its seed, and what every seed recorded
    here is emptied; what it created in a platform stays, with its record, and the job says so. An on file with the
    user's own changes is not written."""
    def forget(job):
        state = layers.current(project)
        if state["playground"] and not state["locked"]:
            layers.write(None, state["explicit"])
            job.lines.append(f"Turned off {project['playgrounds'][state['playground']]['name']}")
        for pid, playground in project["playgrounds"].items():
            if not playgrounds.forget(pid):
                job.lines.append(f"Could not empty what {playground['name']} recorded: its state folder leads out of its folder")
        job.lines.extend(stays(project))
    return forget


def _core_start(project, accept):
    """Start all with API Management in the Cloud (core.plan, made again now, the platform read again): the hybrid gateway
    of the chosen environment, its token written first when the add-on holds another one, then the rest of the platform
    and what is on, with what they depend on. Nothing of API Management starts, nor what exists only for it. What it
    stops or turns off besides must be what the page showed (`accept`)."""
    state, containers = layers.current(project), compose.ps()
    found = core.plan(project, state, containers, projects.saved(project), live=True)
    gateway, place, others = found["gateway"], found["place"], found["others"]
    layers.check(project, gateway, accept)
    if not found["needed"]:
        raise RuntimeError("Everything runs")
    uid = place["id"].partition("/")[0]

    def prepare(job):
        layers.write(gateway["playground"], gateway["explicit"], blocked=layers.blocked_files(project))
        layers.make_binds(project, gateway)
        if not place["here"]:
            platforms.swap_gateway(project, uid, found["gateway_id"])
        starts = [row["name"] for row in found["dialog"]["starts"]]
        for verb, names in (("Starts", starts), ("Restarts", [row["name"] for row in gateway["restarts"]]),
                            ("Serves", [place["name"]])):
            if names:
                job.lines.append(f"{verb}: {', '.join(names)}")
    changes = layers.changes(project, gateway)
    changes["start"] += [["service", name] for name in others]
    steps = [prepare, *layers.commands(gateway), *([[*COMPOSE, "up", "-d", "--no-deps", *others]] if others else []),
             lambda job: places.invalidate(uid)]
    waits = layers.waits(project, gateway) + ([awaited(project["services"], start=others)] if others else [])
    return JOBS.start(f"Start all for {place['name']}", steps, key="platform", verb="start", changes=changes, waits=waits)


def _core_stop(project):
    """Stop all with API Management in the Cloud: stops what Start all starts and runs (core.stopping), and leaves API
    Management on this machine as it is. What stays on starts again with the next Start all."""
    names = core.stopping(project, layers.current(project), compose.ps())
    if not names:   # Compose reads an empty list as every service
        raise RuntimeError("Nothing runs")
    return JOBS.start(f"Stop all for {core.name(project)}", [[*compose.by_name(), "stop", *names]], key="platform", verb="stop",
                      changes={"stop": [["service", service] for service in names]},
                      waits=[awaited(project["services"], stop=names)])


def action(project, name, for_core=False, accept=()):
    """One of the platform's actions, on everything that is on. Each command waits for the containers of what is on:
    those it names when it starts them, all of them when it stops them; a pull for their images. A start runs all of
    it, what was stopped on purpose too, and leaves alone what was started outside the Lab UI; an apply, a restart or
    an update acts on what runs (_apply, _restart, _update). Stop, Remove containers and Reset go by the project's
    name: they reach every container of the lab, whatever the files say. `for_core`: the Dashboard's Start all and Stop
    all, which follow where API Management runs: in the Cloud, its gateway and the rest of the platform and what is on
    without API Management (_core_start, _core_stop, `accept` what its dialog showed); on this machine, the start and
    the stop above."""
    if for_core and name in ("start", "stop") and core.current(project)["place"] != core.MACHINE:
        return _core_start(project, accept) if name == "start" else _core_stop(project)
    acting = {"start": _start, "apply": _apply, "restart": _restart, "update": _update}
    if name in acting:
        return acting[name](project)
    units, by_name = _units(layers.current(project)), compose.by_name()
    stops = layers.awaits(project, {"stop": units})
    actions = {
        "stop":  ("Stop the stack", [[*by_name, "stop"]], "stop", [stops]),
        "pull":  ("Pull images", [PULL], None, [awaited(project["services"], images=_pulled(project, units))]),
        "down":  ("Remove containers", [[*by_name, "down"]], "stop", [stops]),
        "reset": ("Reset the stack", [[*by_name, "down", "-v"], _forget(project)], "stop", [stops]),
    }
    if name not in actions:
        raise paths.NotFound(f"No such action: {name}")
    title, steps, change, waits = actions[name]
    return JOBS.start(title, steps, key="platform", verb=name, changes={change: units} if change else None,
                      waits=waits)


def _idle_dependencies(project, names):
    """What `up` starts first with these services: what they depend on that does not run now (layers.dependencies),
    which the state says before the click (`brings`)."""
    running = {name for name, found in compose.ps().items() if model.service_state(found, False, False)["running"]}
    return sorted(layers.dependencies(project, layers.current(project), running, names))


def _containers(project, name, services, do, kind, uid):
    """Starts, stops or restarts some services of a group, a service, an add-on or a playground (`kind`, `uid`). A
    start runs them as the files say now, after what they depend on that does not run: the job starts and waits for
    that too. A stop or a restart acts on their containers, whatever the files say; a restart on those that run, as
    they are: what was stopped stays stopped."""
    if not services:   # Compose reads an empty list as every service
        raise ValueError(f"{name} has no service of its own")
    if do == "start":   # what needs a license that is not usable would only exit again
        services = [service for service in services if not layers.held_back(project, service)]
        if not services:
            raise RuntimeError(f"{name} needs a license")
    if do != "start":   # by project name, Compose refuses a service that has no container: a one-shot pruned since
        existing = compose.ps()
        services = [service for service in services if service in existing]
        if not services:
            raise RuntimeError(f"{name} has no container")
    if do == "restart":   # Compose would start again a container that was stopped
        services = [service for service in services
                    if model.service_state(existing[service], False, False)["running"]]
        if not services:
            raise RuntimeError(f"{name} is not running")
    command = [*COMPOSE, "up", "-d", *services] if do == "start" else [*compose.by_name(), do, *services]
    # A restart shows as starting: the containers stop on the way, which is not an issue
    change = "stop" if do == "stop" else "start"
    changes, waited = {change: [[kind, uid]]}, list(services)
    if do == "start":
        idle = _idle_dependencies(project, services)
        changes["start"] += [["service", dependency] for dependency in idle]
        waited += idle
    return JOBS.start(f"{do.capitalize()} {name}", [command], key=f"{kind}:{uid}", verb=do, changes=changes,
                      waits=[awaited(project["services"], **{change: waited})])


def group_action(project, gid, do):
    group = next((g for g in project["groups"] if g["id"] == gid), None)
    if not group:
        raise paths.NotFound(f"No such group: {gid}")
    return _containers(project, group["name"], group["services"], do, "group", gid)


def service_action(project, name, do):
    """Starts, stops or restarts one service. A playground's seed is run again by its Re-seed, which records what it
    creates and follows it: it is only stopped here."""
    if name not in project["services"]:
        raise paths.NotFound(f"No such service: {name}")
    if project["services"][name]["role"] == "seed" and do != "stop":
        raise RuntimeError("Re-seed runs it again")
    return _containers(project, name, [name], do, "service", name)


def unit_action(project, kind, uid, do):
    """Starts, stops or restarts the containers of an add-on or a playground, as a platform group's: it stays on or
    off (turning it on or off is layers' job). The catalog's Restart of an add-on is this one. A playground's seed runs
    at its start and with Re-seed: a start or a restart leaves it alone."""
    unit = layers.lookup(project, kind, uid)
    services = unit["apps"] if kind == "playground" and do != "stop" else unit["services"]
    return _containers(project, unit["name"], services, do, kind, uid)
