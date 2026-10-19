"""What /api/state answers: the state, the note and the primary action of the platform and of every add-on, setup
and playground, what runs behind what is saved, the license and Docker as the banner says them. Pure: everything is
given, nothing is read here but the names of the license's packs, as license.named() last knew them."""

import re

from ..docker import compose, images
from ..gravitee import frames, license, platforms
from . import layers, personal, reasons, tested
from .. import paths
from ..catalogs import resolve
from .project import license_features, tag

# A running container, by its health check: (state, label)
HEALTH = {"healthy": ("healthy", "Healthy"), "starting": ("starting", "Starting"), "unhealthy": ("issue", "Unhealthy")}
# 0, and 130 / 143 (SIGINT / SIGTERM): what a stop leaves. 137: killed, a failure only when out of memory
STOP_CODES = (0, 130, 137, 143)
# Docker's words for a duration, and the unit the page writes
DURATIONS = (("second", "s"), ("minute", "min"), ("hour", "h"), ("day", "d"), ("month", "mo"), ("year", "y"))
LABELS = {"running": "Running", "partly": "Partly running", "starting": "Starting", "stopping": "Stopping",
          "stopped": "Stopped", "issue": "Issue", "off": ""}
# Where a service is as it should be: it runs, healthy or without a health check, or it is a one-shot that ended well
FINE = ("healthy", "running", "done")
# Of a service, what the page shows as it is. `role` is "seed" for a playground's seed: its Re-seed runs it again.
# `needs_license`: it exits without a usable license
SERVICE_KEYS = ("label", "description", "container", "image", "tag", "registry", "built", "role", "needs_license")
PLAYGROUND_KEYS = ("tags", "duration", "first_start", "source", "chapters", "guide", "accounts", "requires", "services",
                   "apps", "seed")
LOW_MEMORY = 7.5 * 2 ** 30   # under 8 GB, Docker struggles once add-ons run with the platform
APPLY = "Apply needed"   # what runs behind what is saved, as a row says it
NEWER = "Needs newer versions"   # a playground whose images are older than it needs
SETTINGS = "Needs settings"   # what lacks a required setting, or brings an add-on that does
# Why what needs a license cannot have it, as its tooltip says it
UNUSABLE = {"unreadable": "The license cannot be read", "invalid": "The license is not signed by GraviteeSource"}


# ---------------------------------------------------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------------------------------------------------

def _state(key, label, running=False):
    return {"key": key, "label": label, "running": running}


def service_state(container, oneshot, job_running):
    """The state of a service from its container: {key, label, running}. `running` says the container runs now:
    a one-shot that ended well is "done", without running."""
    if not container:
        return _state("absent", "Not created")
    status, code = container.get("state") or "unknown", container.get("exit_code")
    if status == "running":
        # Docker started it again more than once and it is not healthy since: it dies again and again
        if container.get("restarts", 0) > 1 and container.get("health") != "healthy":
            return _state("issue", "Keeps restarting", running=True)
        return _state(*HEALTH.get(container.get("health"), ("running", "Running")), running=True)
    if status in ("restarting", "paused"):
        return _state("issue", status.capitalize(), running=True)
    if status == "created":   # on its way while a job runs, made and never started otherwise
        return _state("starting", "Starting") if job_running else _state("stopped", "Not started")
    if container.get("oom"):
        return _state("issue", "Out of memory")
    if status != "exited":
        return _state("issue", status.capitalize())
    if code == 0 and oneshot:
        return _state("done", "Done")
    return _state("stopped", "Stopped") if code in STOP_CODES else _state("issue", f"Exited ({code})")


def container_steps(project, containers):
    """Where the container of every service is on its way, as a job measures it (jobs.Job.observe): {container name:
    step}. "ready" as the Lab UI judges it: healthy, running without a health check, a one-shot that ended well;
    "running" while it runs on its way there (its health check runs, a one-shot is not done); "issue" while it runs
    and is not on its way (unhealthy, it keeps restarting, Docker restarts it, paused); "created", made and not
    started; "stopped" for any other container; "absent" without one."""
    found = {}
    for name, service in project["services"].items():
        container = containers.get(name)
        state = service_state(container, service["oneshot"], False)
        if not container:
            step = "absent"
        elif state["running"] and state["key"] == "issue":
            step = "issue"
        elif state["running"]:
            step = "ready" if state["key"] in ("healthy", "running") and not service["oneshot"] else "running"
        elif state["key"] == "done":
            step = "ready"
        else:
            step = "created" if container.get("state") == "created" else "stopped"
        found[service["container"]] = step
    return found


def uptime(status):
    """Docker's status text, in the short units of the page: "Up 5 minutes (healthy)" -> "5 min", "Up 2 weeks" ->
    "14 d", "Exited (0) About an hour ago" -> "1 h ago"."""
    text = re.sub(r"^Up |^(Exited|Restarting) \(\d+\) | \((healthy|unhealthy|health: starting)\)$", "", status or "")
    text = re.sub(r"Less than a second", "1 s", text, flags=re.I)
    text = re.sub(r"About an? ", "1 ", text, flags=re.I)
    text = re.sub(r"(\d+) weeks?", lambda weeks: f"{int(weeks.group(1)) * 7} d", text)
    for word, unit in DURATIONS:
        text = re.sub(rf" {word}s?", f" {unit}", text)
    return text


def up(project, containers):
    """What runs, from the containers: {platform: whether a service of it runs, groups: the platform groups whose
    services all run (a one-shot aside), addons: the ids of those with a running container, playgrounds: those with
    a running app, services: the services whose container runs, created: those that have a container, done: the
    one-shots that ended well}. A seed runs only while it seeds."""
    alive = {name for name, container in containers.items() if service_state(container, False, False)["running"]}
    services = project["services"]
    return {"platform": any(services[name]["owner"]["kind"] == "platform" for name in alive & set(services)),
            "groups": {group["id"] for group in project["groups"]
                       if all(name in alive or services[name]["oneshot"] for name in group["services"])},
            "addons": {aid for aid, addon in project["addons"].items() if alive & set(addon["services"])},
            "playgrounds": {pid for pid, playground in project["playgrounds"].items()
                            if alive & set(playground["apps"])},
            "services": alive, "created": set(containers),
            "done": {name for name, container in containers.items() if name in services
                     and service_state(container, services[name]["oneshot"], False)["key"] == "done"}}


def lateness(project, containers, saved):
    """The services whose container runs behind what is saved (project.saved()), each with why: `config`, it was made
    from another configuration than the one Compose would make it from now, or `image`, it runs another image than its
    tag names here. Only a container that runs is compared: one that starts takes what is saved. A one-shot runs once:
    it is left out, and so is what Compose does not read (an add-on that is off, whose container runs all the same): an
    apply leaves it as it is. Nothing is known to run behind while what Compose reads is not known."""
    hashes, held = (saved or {}).get("hashes") or {}, (saved or {}).get("images") or {}
    found, other = {}, {}
    for name, service in project["services"].items():
        container = containers.get(name)
        if service["oneshot"] or name not in hashes or not container \
                or not service_state(container, False, False)["running"]:
            continue
        configured, image = hashes.get(name), held.get(name)
        if configured and container.get("config_hash") and configured != container["config_hash"]:
            found[name] = "config"
        elif image and container.get("image_id") and image != container["image_id"]:
            other[name] = (image, container["image_id"])
    # Another id may name the same image: one built again from unchanged sources, which Compose keeps running
    return {**found, **{name: "image" for name, (image, running) in other.items()
                        if not compose.same_image(project["services"][name]["container"], image, running)}}


def behind(project, containers, saved):
    """The services whose container runs behind what is saved: lateness() without the why."""
    return set(lateness(project, containers, saved))


def held(project, containers):
    """The services the Lab UI does not start for want of a usable license (layers.held_back) that do not run. One
    that runs, from a license since removed, is what it is."""
    return {name for name in project["services"] if layers.held_back(project, name)
            and not service_state(containers.get(name), False, False)["running"]}


def _services(project, state, containers, job, late, waiting):
    """Every service with its state. `on`: it is part of what should run. `running_tag`: the tag of the image its
    container runs, next to `tag`, the saved one. `behind`: it runs behind what is saved (`late`, with why); its note
    says so, with the reason in its tooltip. `waiting`: the note of the ones held (`held`) that
    do not run: they are not on, and not an issue, however their container ended."""
    services = {}
    for name, service in project["services"].items():
        container = containers.get(name)
        found = service_state(container, service["oneshot"], bool(job))
        running = tag(container["image"]) if found["running"] and container.get("image") else None
        on = any(layers.is_on(one["kind"], one["id"], state["playground"], state["effective"]) for one in layers.owners_of(service))
        note = _late_note(reasons.line({name: late[name]}), reasons.causes({name: late[name]})) if name in late else _said()
        if name in waiting:
            found, on, note = _calm(found), False, waiting[name]
        services[name] = {**{key: service[key] for key in SERVICE_KEYS},
                          # As a person reads it, with what it belongs to when another has its label
                          "name": layers.service_name(project, name),
                          # What its container publishes: an add-on that is on may add a port to a core service
                          "ports": (container or {}).get("ports") or service["ports"],
                          # The port inside it for each one it publishes, as another container reaches it
                          "targets": service.get("targets") or {},
                          "state": found, "uptime": uptime(container.get("status")) if container else "",
                          "on": on, "running_tag": running, "behind": name in late, **note}
    return services


def _calm(found):
    """The state of a service that is held and does not run: how its container ended is not an issue."""
    return _state("stopped", "Stopped") if found["key"] == "issue" else found


# ---------------------------------------------------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------------------------------------------------

def _labeled(key):
    return {"key": key, "label": LABELS[key]}


def fold(states, on, change=None):
    """The state of what a set of services makes up: an add-on, a platform group, a playground. `change` is "start"
    or "stop" when the running job starts or stops it. First match wins. What did not run yet or stopped cleanly is
    not an issue: running mixed with it is partly running. A one-shot that ended well did its part: it neither runs
    nor makes a stop, nor does it make anything run. Where nothing runs, nothing is running: it is stopped."""
    keys, runs = [s["key"] for s in states], any(s["running"] for s in states)
    if change:
        return _labeled("starting" if change == "start" else "stopping")
    if not on and not runs:
        return _labeled("off")
    if "issue" in keys:
        return _labeled("issue")
    if "starting" in keys:
        return _labeled("starting")
    if not runs:
        return _labeled("stopped")
    down = [key for key in keys if key not in FINE]   # stopped cleanly, or never created
    return _labeled("partly" if down else "running")


def setup_fold(members):
    """A setup has no state of its own: it is its add-ons, whoever turned them on. Running when they all run;
    otherwise, while they are not all on, off (its note says when some are). On: an issue of one is its issue,
    stopped when none runs, partly running when some do."""
    keys = [member["state"]["key"] for member in members]
    for key in ("starting", "stopping"):
        if key in keys:
            return _labeled(key)
    if keys and all(key == "running" for key in keys):
        return _labeled("running")
    if not (members and all(member["on"] for member in members)):
        return _labeled("off")
    if "issue" in keys:
        return _labeled("issue")
    return _labeled("stopped" if all(key == "stopped" for key in keys) else "partly")


def platform_fold(groups):
    """The platform is its groups: one that was stopped on purpose is not an issue of the whole, nor one partly
    running: it runs while one of them does."""
    keys = {group["state"]["key"] for group in groups}
    key = next((key for key in ("starting", "stopping", "issue") if key in keys), None)
    return _labeled(key or ("running" if keys & {"running", "partly"} else "stopped"))


def _states(c, names):
    return [c["services"][name]["state"] for name in names]


def _counted(c, names):
    """The services that make a state. A one-shot without a container is left out: it has not run yet, or it ended
    and was pruned. So is a service held for want of a license (`held`). When nothing else is left, they all count."""
    services = c["project"]["services"]
    kept = [n for n in names if n not in c["held"]
            and not (services[n]["oneshot"] and c["services"][n]["state"]["key"] == "absent")]
    return kept or names


def _folded(c, names, on):
    """The state these services make up; starting or stopping while the running job starts or stops one of them that
    it has not brought where it goes yet: a container it waits for is there once it measures it so (ready, or
    stopped), one it does not wait for once the job ends."""
    measured, services = (c["job"] or {}).get("containers") or {}, c["project"]["services"]
    changes = {c["changing"].get(name) for name in names if measured.get(services[name]["container"], 0) < 1}
    change = "start" if "start" in changes else "stop" if "stop" in changes else None
    return fold(_states(c, _counted(c, names)), on, change)


def _progress(c, key, names, state):
    """How far the last job is with these services, what `key` names, from 0 to 1. What the job is for has the job's
    own progress: its button says the same from the click until the item gets there. Anything else it starts or stops
    has the mean of how far each of its containers the job waits for is along it. Measured while the item is on its
    way (`state`: starting or stopping), as the job runs, then, once it ended well, while its health checks run. None
    when the job leaves these services alone, measures nothing, failed, or once they got where it took them."""
    job = c["last"]
    if not job or not any(name in c["moved"] for name in names):
        return None
    if state["key"] not in ("starting", "stopping"):
        return None
    if job["for"] == key:
        return job["progress"]
    found = job["containers"]
    shares = [found[container] for container in (c["services"][name]["container"] for name in names)
              if container in found]
    return sum(shares) / len(shares) if shares else None


def _culprit(c, names):
    """The service that makes a state, as a note: the one with an issue ("Kafka UI exited (1)"), else the first that
    does not run ("Developer Portal stopped", "Kafka UI not created")."""
    services = [c["services"][name] for name in _counted(c, names)]
    culprit = next((s for s in services if s["state"]["key"] == "issue"), None) \
        or next((s for s in services if s["state"]["key"] not in FINE), None)
    return f"{culprit['label']} {culprit['state']['label'].lower()}" if culprit else ""


# ---------------------------------------------------------------------------------------------------------------------
# Notes and actions
# ---------------------------------------------------------------------------------------------------------------------

def _late_note(reason, causes):
    """The note of what runs behind what is saved: the page draws it as a badge, the reason its name for a screen reader
    and `note_causes` (reasons.causes) its popover."""
    return {**_said(APPLY, "warn", reason), "note_apply": True, "note_causes": causes}


def _said(text="", tone="", title="", target=None, file=None):
    """A note as a row says it: its text, its tone, what its tooltip says instead, the item it links to ({kind, id})
    and the settings it opens (a .env)."""
    return {"note": text, "note_tone": tone, "note_title": title, "note_target": target, "note_file": file}


def _via(c, unit, on):
    """What brought an item that is on without being turned on itself, and the row it links to: the playground that
    is on, else the add-on turned on that needs it. A setup is brought when all its add-ons are, none turned on."""
    project, explicit = c["project"], set(c["state"]["explicit"])
    if unit["kind"] == "playground" or not on:
        return None
    mine = {unit["id"]} if unit["kind"] == "addon" else set(unit["members"])
    if mine & explicit:
        return None
    playground = project["playgrounds"].get(c["state"]["playground"])
    if playground and mine <= set(playground["addons"]):
        return playground["name"], {"kind": "playground", "id": playground["id"]}
    needing = sorted((project["addons"][e]["name"], e) for e in explicit if mine <= set(project["addons"][e]["needs"]))
    return (needing[0][0], {"kind": "addon", "id": needing[0][1]}) if needing else None


def _late(c, names, owner=""):
    """Why these services, the ones of an owner, run behind what is saved, as one line (reasons.line): "4.12.20 is saved,
    4.13.0-alpha.4 still runs." on the owner's own row, "Gateway, Management API: ..." when only some of them do, and
    prefixed with the `owner`'s name ("API Management: ...") for a line that names it; empty when none does."""
    late = {name: c["why"][name] for name in names if name in c["behind"]}
    return reasons.line(late, {name: c["services"][name]["label"] for name in late}, owner, whole=len(late) == len(names))


def _late_causes(c, names):
    """The same as data (reasons.causes): the causes of these services that run behind, the ones of one owner."""
    late = {name: c["why"][name] for name in names if name in c["behind"]}
    return reasons.causes(late, {name: c["services"][name]["label"] for name in late}, whole=len(late) == len(names))


def _lower(part):
    """A part as a sentence says it: "Step 4" reads "step 4", "OpenFGA step" stays as it is."""
    first = part.split(" ", 1)[0]
    return part[:1].lower() + part[1:] if first[1:] == first[1:].lower() else part


def _parts(wanted, missing):
    """What needs these features when each is for a part of the unit ("step 4", "step 2 and step 4"); None when one
    is for the whole unit."""
    parts = [wanted.get(feature) if isinstance(wanted, dict) else None for feature in missing]
    if not parts or None in parts:
        return None
    named = [_lower(part) for part in dict.fromkeys(parts)]
    return " and ".join([", ".join(named[:-1]), named[-1]] if len(named) > 1 else named)


def license_note(found, features, wanted):
    """What a row needing these license features says of the license, None when it allows them, or cannot tell: none,
    one that cannot be read or is not GraviteeSource's ("Needs a license", the tooltip says why or what it uses), one
    that expired ("License expired"), one that lacks some ("Not in the license", the tooltip says what it uses). What
    is used is said by the packs' names (license.named). When every feature missing is for a part of the unit
    (`wanted` as project.license_features() gives it), the note says that part: "License needed for step 4". It
    opens the core's settings, where the license is. `found` is the license as license.status() reads it (None
    without one to read), `features` what it allows (None when not known). The rows of the lists and of Plugins say
    it so."""
    status = (found or {}).get("status")
    if status == "expired":
        return _said("License expired", "err", f"Expired on {found['date']}", file=paths.ENV_FILE)
    if status in ("none", *UNUSABLE):
        missing = list(wanted)
        word, why = "Needs a license", UNUSABLE.get(status) or "Uses: " + ", ".join(license.named(missing))
    else:
        missing = [feature for feature in wanted if features is not None and feature not in features]
        if not missing:
            return None
        word, why = "Not in the license", "Uses: " + ", ".join(license.named(missing))
    part = _parts(wanted, missing)
    if part:   # the rest of the unit works without them: the note names the part, the tooltip what is missing
        if word != "Needs a license":
            why = f"{word}: " + ", ".join(license.named(missing))
        word = f"License needed for {part}"
    # What it uses, as data for its popover: the packs by name, and the form that takes a license as its way on
    uses = [] if status in UNUSABLE else [{"kind": "license", "features": license.named(missing)}]
    return {**_said(word, "warn", why, file=paths.ENV_FILE), "note_causes": uses}


def _license_block(c, unit):
    """Why a unit cannot start for want of the license, in the word of its note ("Needs a license", "Not in the
    license", "License expired"); None when the license allows it, cannot be told, or the unit works without it but for
    a part (its note then says which part: "License needed for step 4")."""
    wanted = license_features(unit.get("requires") or {})
    found = license_note(c["license"], c["features"], wanted) if wanted else None
    return found["note"] if found and not found["note"].startswith("License needed for") else None


def _outside(c, unit, is_up):
    """What of an item that is not on runs without being on, by name: an add-on or a playground that runs; the add-ons
    of a setup that run and are not on."""
    if unit["kind"] != "setup":
        return [unit["name"]] if is_up else []
    return sorted(c["project"]["addons"][aid]["name"] for aid in unit["members"]
                  if aid in c["up"]["addons"] and aid not in c["state"]["effective"])


def _lacks_gateway(c, unit):
    """The note of an add-on that runs the gateway of a platform (its `gateway`), when a platform of that kind is
    registered and the token of the gateway is the one setting it lacks: it needs a hybrid gateway, which the Gravitee Cloud
    page creates and writes there. None otherwise: it needs settings."""
    gateway = unit.get("gateway")
    required = [setting["key"] for setting in unit.get("settings", []) if setting["required"]]
    if gateway and gateway["platform"] in c["kinds"] and required == [gateway["token"]]:
        return _said("Needs a hybrid gateway", target={"kind": "platform", "id": None})
    return None


def _unusable(unit):
    """Why a unit cannot start, in one line: the first reason it is blocked, else the first catalog it lacks. None when
    nothing is against it."""
    reasons = (unit.get("blocked") or {}).get("reasons") or []
    needs = unit.get("needs_catalogs") or []
    return str(reasons[0]) if reasons else layers.need_line(needs[0]) if needs else None


def _blocked_note(c, unit):
    """The note of a unit the policy blocks: Blocked, in the error tone, and in `note_causes` the reasons and who to ask
    (the owner of its catalog, or the person for a folder catalog); None for a unit that is not."""
    reasons = [str(reason) for reason in (unit.get("blocked") or {}).get("reasons") or []]
    if not reasons:
        return None
    there = (c["project"].get("catalogs") or {}).get(unit.get("catalog")) or {}
    owner = "" if unit["personal"] else there.get("name") or ""
    return {**_said("Blocked", "err", reasons[0]),
            "note_causes": [{"kind": "blocked", "reasons": reasons, "owner": owner, "folder": there.get("kind") == "folder"}]}


def _needs_note(unit):
    """The note of a unit that lacks a catalog, by the reason of the first one: "Needs a catalog" leading to the
    Catalogs page when it is not there, the line of its reason leading to its row otherwise. The causes name each of them."""
    needs = unit.get("needs_catalogs") or []
    if not needs:
        return None
    first, line = needs[0], layers.need_line(needs[0])
    missing = first.get("reason") not in ("downloading", "failed", "gone", "old")
    causes = [{key: need[key] for key in ("id", "name", "min", "reason", "unit") if key in need} for need in needs]
    return {**_said("Needs a catalog" if missing else line, "" if first.get("reason") == "downloading" else "warn", line,
                    target={"kind": "catalog", "id": None if missing else first.get("id")}),
            "note_causes": [dict(cause, kind="catalog") for cause in causes]}


def _note(c, unit, state, on, is_up, plan, failure, services):
    """One short fact about a row, the first that applies, as _said() gives it."""
    key = state["key"]
    if key in ("starting", "stopping"):   # on its way, after a failure too: it is said once it is there
        return _said()
    if failure:   # Show output opens it; its reason is the tooltip of a failure
        if failure["error"] == "Canceled":
            return _said("Canceled")
        if failure.get("login"):   # a registry refused the pull: logging in is what it takes, its note says it
            return _said(failure["error"], "err")
        return _said("Failed", "err", failure["error"] or "")
    if key == "issue":
        return _said(_culprit(c, services), "err")
    # What the catalogs say comes before what the plan blocks: the policy's refusal, then a catalog that is not here
    said = _blocked_note(c, unit) or _needs_note(unit)
    if said:
        return said
    # A locked on file is said once, in the banner, and a move of the old layout that is not over once, by the button of each
    # row (its reason): neither is the note of every row that could start
    if plan and plan["do"] == "start" and plan["blocked"] and plan["blocked"] not in (c["state"]["locked"], layers.WAITING):
        # A playground on images older than it needs: a short label, the versions it needs in its explanation
        # leads to the Images page, where they are chosen
        if unit["kind"] == "playground" and plan["blocked"] == layers.too_old(c["project"], unit):
            needs = [{"kind": "needs", "name": name, "version": wanted} for name, wanted in layers.needed(c["project"], unit)]
            return {**_said(NEWER, "warn", plan["blocked"], target={"kind": "image", "id": None}), "note_causes": needs}
        # An add-on it brings lacks settings: a short label that leads to that add-on's row, its name in the explanation
        lacking = [aid for aid, addon in c["project"]["addons"].items() if plan["blocked"] == f"{addon['name']} needs settings"]
        if lacking:
            return _said(SETTINGS, "warn", plan["blocked"], target={"kind": "addon", "id": lacking[0]})
        return _said(plan["blocked"])
    if plan is None:   # an add-on that lacks settings: its note names them, and opens them
        unset = c["state"].get("unset", {}).get(unit["id"], [])
        return _lacks_gateway(c, unit) or {**_said(SETTINGS, "warn", "Not set: " + ", ".join(unset) if unset else ""),
                                           "note_causes": [{"kind": "unset", "settings": unset}] if unset else []}
    # What is on is read from the files: without Docker, it still starts with the platform. Without the core file,
    # what is on is not known either
    if on and key == "stopped" and (not c["blind"] or c["docker_down"]):
        return _said("Stopped" if c["up"]["platform"] else "Starts with the platform")
    if unit["kind"] == "setup" and not on:
        names = sorted(c["project"]["addons"][aid]["name"] for aid in unit["members"]
                       if aid in c["state"]["effective"])
        if names:
            return _said("Partly on", title="On: " + ", ".join(names))
    late = _late(c, services)
    if late:   # Apply recreates it as saved, as does its own start
        return _late_note(late, _late_causes(c, services))
    if key == "partly":
        return _said(_culprit(c, services))
    via = _via(c, unit, on)
    if via:
        return _said(f"via {via[0]}", target=via[1])
    outside = [] if on else _outside(c, unit, is_up)
    if outside:   # it runs, but the platform does not start it: its explanation says so, a setup's names the add-ons that run
        note = layers.outside(unit["kind"], outside)
        return _said(note["note"], title=note["note_title"])
    wanted = license_features(unit.get("requires") or {})
    twin = unit.get("twin") if unit["personal"] else None   # the catalog holds this item too
    return (license_note(c["license"], c["features"], wanted) if wanted else None) \
        or (_said(f"Also in {twin['name']}", target={"kind": unit["kind"], "id": twin["id"]}) if twin else _said())


def _action(c, plan, blocked=None):
    """An action of a row, from the plan of its start or its stop. Its label says what the click does to the item:
    Start, Stop, Turn on, Turn off. It asks first (`dialog`: layers.dialog, the rows of its plan) when it does more than
    that (layers.asks). `blocked`: why it cannot run, in the words of the row's own note (the settings an add-on lacks)."""
    reason = blocked or plan["blocked"] or (None if c["project"]["core_loaded"] else f"{paths.CORE_FILE} does not load") \
        or c["wait"]
    return {"do": plan["do"], "label": plan["label"], "enabled": not reason, "reason": reason,
            "dialog": layers.dialog(plan) if layers.asks(plan) else None}


def seed_failed(states, playground):
    """Whether a playground's seed ended in an issue (it exited with an error): its start runs it again. `states`: the
    state of its services, {name: service_state()}."""
    seed = playground["seed"]
    return bool(seed) and states[seed]["key"] == "issue" and not states[seed]["running"]


def _plan(c, kind, uid, do):
    """layers.plan() of an item. A playground's start sets it up when it is off, on and never seeded, or its seed
    failed."""
    fresh = False
    if kind == "playground":
        playground = c["project"]["playgrounds"][uid]
        states = {name: c["services"][name]["state"] for name in playground["services"]}
        fresh = layers.fresh(c["project"], c["state"], uid, c["created"].get(uid), seed_failed(states, playground))
    return layers.plan(c["project"], c["state"], c["up"], kind, uid, do, fresh=fresh, behind=c["behind"])


def _brings(c, names):
    """What the Stack's Start of these services starts first besides them (layers.dependencies), as rows: said before
    the click when it starts anything."""
    return layers.start_rows(c["project"], layers.dependencies(c["project"], c["state"], c["up"]["services"], names))


def _recreates(c, names):
    """What the Stack's Start of these services makes again on its way (layers.recreates), as rows: said before the
    click with what it brings."""
    return layers.recreates(c["project"], c["state"], c["behind"], names)


def _stuck(c, names):
    """Whether a start would change nothing of these services: each runs, or ended well, as saved. What keeps
    restarting, or runs unhealthy, is stopped rather than started again."""
    found = [c["services"][name] for name in _counted(c, names)]
    return bool(found) and not any(name in c["behind"] for name in names) \
        and all(service["state"]["running"] or service["state"]["key"] == "done" for service in found)


def _links(c, *groups):
    """The consoles of these services, those of each group of services in turn (a service once), the legacy last."""
    names = dict.fromkeys(name for group in groups for name in group)
    links = [link for name in names for link in c["links"] if link["service"] == name]
    return [{key: link[key] for key in ("label", "url", "port", "up", "service")}
            for link in sorted(links, key=lambda link: link["legacy"])]


def _tabs(c, uid):
    """The UIs a playground shows next to its guide (frames.declared), each with the state of the service behind it
    and whether it runs (`up`, as a link of the project says it)."""
    return [{**tab, "up": (state := c["services"][tab["service"]]["state"])["running"], "state": state}
            for tab in frames.declared(c["project"], uid)]


def _terminal(c, playground):
    """The shell beside the guide: the service whose container holds it, and whether that runs. None when the playground
    has none."""
    service = c["services"].get(playground.get("terminal") or "")
    if not service:
        return None
    return {"service": playground["terminal"], "label": service["label"], "up": service["state"]["running"]}


def _failure(c, unit):
    """The failed or canceled last job on an item (jobs.Jobs.failed_job), else, for a setup, the failed stop of one of
    its add-ons: what it turned off runs still, and was not started outside the Lab UI."""
    found = c["failures"](unit["kind"], unit["id"])
    if found or unit["kind"] != "setup":
        return found
    members = (c["failures"]("addon", aid) for aid in unit["members"])
    return next((failure for failure in members if failure and failure["verb"] == "stop"), None)


def _runs_on(c, kind, unit):
    """The kind of platform an item runs on when it is not this machine, from the on file; a setup's when all its add-ons
    run on the same. None for This machine."""
    where = c["state"].get("targets", {})
    if kind != "setup":
        return where.get(layers.key(kind, unit["id"]))
    kinds = {where.get(layers.key("addon", member)) for member in unit.get("members") or []}
    return kinds.pop() if len(kinds) == 1 else None


def _item(c, unit, state, on, is_up, services):
    """What every row shows. What is on and runs gets Stop, and so does what still runs after a stop of it failed: the
    retry, and what is on with an issue that a start would not change (_stuck: it keeps restarting). Anything else gets
    Start, or Turn on for what runs without being on. An add-on that is off and lacks a required setting cannot be
    turned on: it gets Stop while it runs, else a Start that waits for its settings, which its note names and opens. What
    is on or runs, and does not get Stop, keeps Stop or Turn off in its menu (`secondary`); so does a setup some add-ons
    of which are on. `login`: the registry to log in to, when its last job failed for want of it."""
    kind, uid = unit["kind"], unit["id"]
    lacking = kind == "addon" and not on and uid in c["state"]["missing"]
    failure = _failure(c, unit)
    if failure and failure["verb"] != "stop" and state["key"] == "running":
        failure = None   # what failed to start runs since
    retry = bool(failure) and failure["verb"] == "stop" and not on and is_up
    stuck = on and state["key"] == "issue" and _stuck(c, services)
    # What a job is stopping is not on, and is not something to start either
    do = "stop" if state["key"] == "stopping" or retry or stuck or (lacking and is_up) \
        or (on and state["key"] in ("running", "starting", "partly")) else "start"
    plan = _plan(c, kind, uid, do)
    # Its own note names the settings it lacks (_note, without a plan), and is why its Start waits
    note = _note(c, unit, state, on, is_up, None if lacking else plan, failure, services)
    unset, gateway = c["state"].get("unset", {}).get(uid, []), lacking and _lacks_gateway(c, unit)
    # Why a start waits, in the order of the note: the policy, a catalog that is not here, the settings, the license
    blocked = _unusable(unit) if do == "start" else None
    if blocked is None and lacking and do == "start":
        blocked = gateway["note"] if gateway else f"{SETTINGS}: {', '.join(unset)}" if unset else SETTINGS
    if blocked is None and do == "start":   # what would not work without the license is not started
        blocked = _license_block(c, unit)
    members_on = kind == "setup" and any(aid in c["state"]["effective"] for aid in unit["members"])
    offers = do == "start" and (on or is_up or members_on)
    # Why what is of this machine cannot be shared or deleted now: its menu says it before the click
    held = {what: personal.held(c["project"], c["state"], kind, uid, deleting=what == "delete", running=is_up)
            for what in ("share", "delete")} if unit["personal"] else None
    return {"kind": kind, "id": uid, "name": unit["name"], "personal": unit["personal"], "held": held,
            "category": unit["category"], "description": unit["description"],
            "file": unit["file"], "env_file": unit.get("env_file"),
            # Where it comes from and whether it can start: the policy's verdict, what it lacks, the level it was
            # written for, the item of this machine or of a catalog that it also is, and what it uses of this machine
            "origin": unit.get("origin"), "readonly": bool(unit.get("readonly")),
            "display_file": unit.get("display_file") or unit["file"], "blocked": unit.get("blocked"),
            "needs_catalogs": list(unit.get("needs_catalogs") or []), "compat": unit.get("compat") or "ok",
            "twin": unit.get("twin"), "uses_machine": list(unit.get("uses_machine") or []),
            "settings": [setting["key"] for setting in unit.get("settings", [])],
            "on": on, "up": is_up, "state": state, "progress": _progress(c, layers.key(kind, uid), services, state),
            # The kinds of platform it can run on besides this machine, as its files say (each place says why it cannot),
            # and where it runs now when that is not this machine: the kind of platform, from the on file
            "deploys_to": unit.get("deploys_to") or [], "runs_on": _runs_on(c, kind, unit),
            **note, "links": _links(c, services), "action": _action(c, plan, blocked),
            "secondary": _action(c, _plan(c, kind, uid, "stop")) if offers else None,
            "failed_job": failure["id"] if failure else None, "login": (failure or {}).get("login")}


# ---------------------------------------------------------------------------------------------------------------------
# The body
# ---------------------------------------------------------------------------------------------------------------------

def _named(units, ids):
    """These add-ons or setups by name; one that does not load is left out, its problem says why."""
    return [{"id": uid, "name": units[uid]["name"]} for uid in ids if uid in units]


def _conflicts(project):
    """Per add-on, the ones it cannot run with and why, whichever of the two says so."""
    found = {aid: {} for aid in project["addons"]}
    for clash in layers.conflicts(project, project["addons"]):
        found[clash["a"]][clash["b"]] = found[clash["b"]][clash["a"]] = clash["reason"]
    return found


def _runs(c, names):
    return any(c["services"][name]["state"]["running"] for name in names)


def _addon(c, addon, conflicts):
    """An add-on is its services; one without any is what it needs, while it is on."""
    uid, project = addon["id"], c["project"]
    on = uid in c["state"]["effective"]
    names = layers.unit_services(project, addon, on)
    is_up = uid in c["up"]["addons"] if addon["services"] else on and _runs(c, names)
    # The core services whose settings it changes, itself or what it needs, which comes with it: they restart when it
    # comes or goes
    restarts = sorted({name for aid in layers.closure(project, [uid]) for name in project["addons"][aid]["restarts"]})
    return {**_item(c, addon, _folded(c, names, on), on, is_up, names),
            "services": addon["services"], "needs": _named(project["addons"], addon["needs"]),
            # The gateway of a platform that it runs here: {platform, service, token, domains}; None for the others
            "gateway": addon.get("gateway"),
            "conflicts": conflicts[uid], "brings": _brings(c, addon["services"]),
            "recreates": _recreates(c, addon["services"]),
            "restarts": [layers.service_name(project, name) for name in restarts]}


def _setup(c, setup, addons):
    members = [addons[aid] for aid in setup["members"] if aid in addons]
    on = bool(members) and all(member["on"] for member in members)
    services = [name for member in members for name in member["services"]]
    return {**_item(c, setup, setup_fold(members), on, any(member["up"] for member in members), services),
            "includes": setup["includes"], "members": _named(c["project"]["addons"], setup["members"])}


def seed_reason(project, state, up, playground):
    """Why a playground's seed cannot reach what it needs now: the first service it depends on, among what is on with
    the playground, that neither runs nor ended well ("Management API (Access Management) is not running"), what it
    depends on itself first, then what those depend on in turn. None when all of it runs, or it has no seed. `up` is
    up()."""
    seed = playground["seed"]
    if not seed:
        return None
    on = set(layers.up_services(project, playground["id"],
                                layers.closure(project, [*state["explicit"], *playground["addons"]])))
    direct = project["services"][seed]["depends_on"]
    for name in [*direct, *sorted(layers.depends(project, on, [seed]) - set(direct))]:
        if name in on and name not in up["services"] and name not in up["done"]:
            return f"{layers.service_name(project, name)} is not running"
    return None


def _playground(c, playground, addons, created):
    """A playground is its apps, and its seed once it ran: done when it ended well. One without apps is what it
    brings, while it is on, with its seed. Re-seed, and the stop that deletes what its seed created, wait for what the
    seed needs to run."""
    uid, project = playground["id"], c["project"]
    on = c["state"]["playground"] == uid
    names = layers.unit_services(project, playground, on)
    is_up = uid in c["up"]["playgrounds"] if playground["apps"] else on and _runs(c, names)
    item = _item(c, playground, _folded(c, names, on), on, is_up, names)
    brought = [name for aid in playground["addons"] if aid in addons for name in addons[aid]["services"]]
    stop, needs = _plan(c, "playground", uid, "stop"), seed_reason(project, c["state"], c["up"], playground)
    reseed = c["wait"] or (None if on else f"{playground['name']} is off") or needs
    return {**item, **{key: playground[key] for key in PLAYGROUND_KEYS},
            # Its apps, then the consoles of its add-ons, then those it lists
            "links": _links(c, playground["services"], brought, playground.get("consoles") or []),
            "tabs": _tabs(c, uid), "terminal": _terminal(c, playground),
            "setups": _named(project["setups"], playground["setups"]),
            "addons": _named(project["addons"], playground["addons"]), "includes": playground["includes"],
            "created": created.get(uid, []),
            # The images it would start on that it was never tested with, and the versions it was (layers.untested)
            "untested": tested.untested(project, playground),
            # What it holds in the places besides This machine (`created` is This machine's)
            "deployed": c["deployed"].get(uid, []), "brings": _brings(c, playground["apps"]),
            "recreates": _recreates(c, playground["apps"]),
            "stop": {"stops": stop["stops"], "turns_off": stop["turns_off"], "keeps": stop["keeps"],
                     "delete": {"enabled": not needs, "reason": needs} if playground["seed"] else None},
            "reseed": {"enabled": not reseed, "reason": reseed} if playground["seed"] else None}


def _registry_label(registry):
    """Where a group's images come from, as Images names it; None for Docker Hub, where they come from otherwise."""
    return images.registry_label(registry) if registry and images.registry_host(registry) else None


def _unlicensed(found):
    """The note of what the Lab UI does not start for want of a usable license, in the words of license_note(): it
    opens the form that takes one, its tooltip says why the license saved does not do. `found` is the license as
    license.status() reads it."""
    status = (found or {}).get("status")
    why = UNUSABLE.get(status) or (f"Expired on {found['date']}" if status == "expired" else "Not started without one")
    return _said("Needs a license", "warn", why, file=paths.ENV_FILE)


def _all_held(c, names):
    """Whether these services are all held (`held`): none of them can be started."""
    return bool(names) and c["held"].issuperset(names)


def _group_note(c, group, state):
    """What a platform group says of itself, as _said() gives it. That it needs a license, when no service of it can
    start without one; the service that makes its issue; what of it runs behind what is saved; while it runs partly,
    the first that does not run, muted."""
    if _all_held(c, group["services"]):
        return c["unlicensed"]
    if state["key"] == "issue":
        return _said(_culprit(c, group["services"]), "err")
    late = _late(c, group["services"]) if state["key"] not in ("starting", "stopping") else ""
    if late:
        return _late_note(late, _late_causes(c, group["services"]))
    if state["key"] == "partly":
        return _said(_culprit(c, group["services"]))
    return _said()


def _reason(c):
    """Why the platform's actions cannot run now: docker-compose.yml does not load, Docker does not answer, a job
    runs; None when they can."""
    return (None if c["project"]["core_loaded"] else f"{paths.CORE_FILE} does not load") or c["wait"]


def _dialog(starts=(), restarts=(), consequences=None):
    """What an action of the platform says before it runs, as the rows of a plan (layers.ROWS): what it starts, what
    it makes again. It stops, turns off and keeps nothing. `consequences`: what making them again does besides
    (_consequences), said with the rest."""
    rows = {key: list({"starts": starts, "restarts": restarts}.get(key, [])) for key in layers.ROWS}
    return {**rows, **(consequences or {})}


def _consequences(c, behind):
    """What making again what runs behind what is saved does beyond a restart, as rows of a plan, for the dialog of an
    Apply or a Start to say before the click: what goes back to an older version than the one that runs (`goes_back`,
    "Elasticsearch 9.5.3 → 9.5.2": data a newer version wrote may not open in an older one), and what loses the license
    it runs when none is saved (`loses_license`: Enterprise features stop). `behind`: the rows of _behind_rows."""
    back = []
    for row in behind:
        for cause in row["causes"]:
            if cause["kind"] == "tag" and cause.get("older"):
                who = ", ".join(cause["services"]) if cause["services"] and not cause["services"][0].endswith(" containers") else row["name"]
                back.append({"kind": row["kind"], "id": row["id"], "name": f"{who} {cause['running']} → {cause['saved']}"})
    lost = [name for name in c["unlicensing"] if name in c["behind"]]
    found = {"goes_back": back, "loses_license": layers.owner_rows(c["project"], lost) if lost else []}
    return {key: rows for key, rows in found.items() if rows}


def _platform_start(c, behind):
    """The Stack's Start, for its head and the banner: what it runs beyond what runs (`starts`, layers.platform_starts),
    whether there is any (`needed`: the Stack shows Start while there is), what it makes again on its way (`restarts`:
    what runs behind what is saved, which its `up` recreates), why it cannot run, and a dialog when it starts more than
    the core, or makes anything again."""
    rows = layers.platform_starts(c["project"], c["state"], c["up"])
    restarts = layers.start_rows(c["project"], sorted(c["behind"]))
    core = all(row["kind"] in ("platform", "group") for row in rows) and not restarts
    reason = _reason(c)
    return {"needed": bool(rows), "starts": rows, "restarts": restarts, "enabled": not reason, "reason": reason,
            "dialog": None if core else _dialog(rows, restarts, _consequences(c, behind))}


def _on_runs(c):
    """Whether anything of what is on runs: what the Stack's Restart and Update act on. With an on file that has the
    user's own changes, a service only it adds counts too: Compose reads it, the project does not know it."""
    project, state = c["project"], c["state"]
    on = set(layers.up_services(project, state["playground"], state["effective"]))
    if state["locked"]:
        on |= c["up"]["services"] - set(project["services"])
    return bool(on & c["up"]["services"])


def _platform_runs(c, runs):
    """An action of the platform that acts on what runs, for the Stack's head: whether anything of what is on runs
    (`needed`: the head shows it while it does, `runs`), and why it cannot run now, "Nothing runs" when nothing does."""
    reason = _reason(c) or (None if runs else "Nothing runs")
    return {"needed": runs, "enabled": not reason, "reason": reason}


def _platform_update(c, runs):
    """The Stack's Update, for its head: a pull, then what runs made again with the images pulled (stack.action), as
    an apply would make it. It starts nothing that was stopped, and what it makes again is not known before the pull:
    it never asks first."""
    return {**_platform_runs(c, runs), "dialog": None}


def _platform_apply(c, states, behind):
    """The Stack's Apply, for its head, the banner, the editor and Images: whether it makes again anything that runs
    behind what is saved (`needed`: not while nothing of what is on runs, when it does nothing), what it makes again
    and starts again (layers.applies), why it cannot run, and its dialog, which it always shows: "Restarts: …", with
    what goes back to an older version and what loses its license (_consequences). What runs behind is among what
    Compose reads, also in an on file with the user's own changes."""
    project, state = c["project"], c["state"]
    on = sorted(set(layers.up_services(project, state["playground"], state["effective"])) | c["behind"])
    found = layers.applies(project, on, states, c["behind"])
    reason = _reason(c)
    return {"needed": bool(found["restarts"]), "restarts": found["restarts"], "starts": found["starts"],
            "enabled": not reason, "reason": reason, "dialog": _dialog(found["starts"], found["restarts"], _consequences(c, behind))}


def _containers(c, groups):
    """How many of the containers of these groups run, of those that are meant to: {running, total}; the platform's own
    count is that of all its groups, a group's that of its own. A service held for want of a license is not meant to,
    and a one-shot only while it runs (it did its part once it ended)."""
    services, found = c["project"]["services"], c["services"]
    names = [name for group in groups for name in group["services"]
             if name not in c["held"] and (found[name]["state"]["running"] or not services[name]["oneshot"])]
    return {"running": sum(found[name]["state"]["running"] for name in names), "total": len(names)}


def _behind_rows(c):
    """What owns the services that run behind what is saved (layers.owner_rows), each with why in a line (`reason`), the
    same as data (`causes`, reasons.causes) and how many of its containers run behind (`count`)."""
    project = c["project"]
    owned = {"group": lambda row: next(g["services"] for g in project["groups"] if g["id"] == row["id"]),
             "addon": lambda row: project["addons"][row["id"]]["services"],
             "playground": lambda row: [name for name, service in project["services"].items()
                                        if service["owner"]["kind"] == "playground" and service["owner"]["id"] == row["id"]]}
    rows = []
    for row in layers.owner_rows(project, c["behind"]):
        names = owned[row["kind"]](row)
        rows.append(dict(row, reason=_late(c, names, row["name"]), causes=_late_causes(c, names),
                         count=sum(name in c["behind"] for name in names)))
    return rows


def _platform(c):
    project, groups = c["project"], []
    for group in project["groups"]:
        # A group of services that cannot start for want of a license is off, as what is not on is
        state = _folded(c, group["services"], not _all_held(c, group["services"]))
        progress = _progress(c, f"group:{group['id']}", group["services"], state)
        groups.append({**group, "state": state, "progress": progress, **_group_note(c, group, state),
                       "registry_label": _registry_label(group["registry"]), "containers": _containers(c, [group]),
                       "brings": _brings(c, group["services"]), "recreates": _recreates(c, group["services"])})
    state, runs = platform_fold(groups), _on_runs(c)
    states = {name: service["state"] for name, service in c["services"].items()}
    behind = _behind_rows(c)
    playground = project["playgrounds"].get(c["state"]["playground"])
    found = {"name": project["name"], "state": state, "containers": _containers(c, groups),
            "progress": _progress(c, "platform", [name for group in groups for name in group["services"]], state),
            "license_var": project["license_var"], "groups": groups, "start": _platform_start(c, behind),
            "update": _platform_update(c, runs), "restart": _platform_runs(c, runs),
            # The playground that Reset turns off as it deletes what its seed created; not in an on file with the
            # user's own changes, which is never written
            "reset_turns_off": playground["name"] if playground and not c["state"]["locked"] else None,
            # What runs behind what is saved, by what owns it, and the Apply that makes it again as saved; why, in a line
            "behind": behind, "apply": _platform_apply(c, states, behind),
            # Its files, like a group's: the core's compose file, and its .env with every setting of the core
            "file": paths.CORE_FILE, "env_file": paths.ENV_FILE,
            "settings": [variable["key"] for variable in project["core_variables"]]}
    # The add-on that runs the gateway of each kind of platform here, as the project chose it: the page reads it
    gateways = (project.get("platform") or {}).get("gateways")
    return {**found, "gateways": {kind: gateways.get(kind) for kind in platforms.KINDS}} if gateways is not None else found


def _problem(problem, readonly=()):
    """A file that does not load, or loads with something wrong. `editable`: the editor opens it, the on file too; not one
    of a git catalog (`readonly`: their namespaces), which is read and not changed."""
    file = problem["file"]
    found = resolve.target_of(file)
    problem = dict(problem, message=str(problem.get("message", "")).replace(f"{paths.ROOT}/", ""))   # the project's folder says nothing to the reader
    return dict(problem, editable=(file in (paths.CORE_FILE, paths.ON_FILE) or paths.unit_of(file) is not None)
                and not (found and found[0] in readonly))


def _by_name(items):
    """By name as a person reads it, a number by its value: "Lab 2" before "Lab 10"."""
    def key(item):
        parts = re.split(r"(\d+)", item["name"].lower())   # text, then number and text in turn
        return [int(part) if i % 2 else part for i, part in enumerate(parts)], item["id"]
    return sorted(items, key=key)


def _wait(docker, job, loaded):
    """Why nothing can be started now, as what waits says it; None when it can."""
    if docker["error"]:
        return "Docker is not reachable"
    if not loaded:
        return f"{paths.CORE_FILE} does not load"
    return f"Wait for {job['title']}" if job else None


def memory_warning(memory):
    """What to know of the memory Docker has, whatever is on: None when it is enough, or not known."""
    if memory and memory < LOW_MEMORY:
        return f"{memory / 2 ** 30:.1f} GB, 8 GB+ recommended"
    return None


def _docker(docker):
    """What the page says of Docker: why it does not answer, and a warning when its memory is short."""
    short = memory_warning(docker["memory"])
    return {"error": docker["error"], "warning": f"Docker memory: {short}" if short else None}


def unapplied_line(project, names, saved=True):
    """Why the license saved is not applied, as one line naming the services that run another one; empty when it is.
    `saved`: a license is saved, else the services still run one that was removed."""
    labels = reasons.spelled(project["services"][name]["label"] for name in names)
    if not names:
        return ""
    return f"The license saved differs from the one that runs in {labels}." if saved \
        else f"No license is saved, but {labels} still {'runs' if len(names) == 1 else 'run'} one."


def _license(c, containers):
    """The license as the banner and the core's chip say it: {status, expires, days_left, problem, tone, applied}.
    `problem`: the banner's line, only when it cannot be read, is not GraviteeSource's, expired or expires within 30
    days; without a license, the edition says it. `applied`: every service that reads the license runs the one saved;
    false once another one is saved, until it is applied, `reason` saying where. None without a license to read."""
    found = c["license"]
    if not found:
        return None
    unapplied = license.unapplied(c["project"], containers, found["digest"])
    applied = not unapplied
    status = found["status"]
    tone = "err" if status in ("unreadable", "invalid", "expired") else "warn" if status == "expiring" or not applied \
        else ""
    return {"status": status, "expires": found["expires"], "days_left": found["days_left"],
            "problem": found["problem"], "tone": tone, "applied": applied,
            "reason": unapplied_line(c["project"], unapplied, status != "none")}


def git_state(version):
    """state.git, from the version of git that gitx.version() found (a tuple of numbers) or None for none:
    {available, version}."""
    return {"available": version is not None, "version": ".".join(str(part) for part in version) if version else None}


def build(project, state, containers, job, failures, license_features, docker, created, saved=None,
          license_status=None, platforms=(), deployed=None, changed=None, git=None, catalogs=None, content=None,
          layout=None):
    """The body of /api/state. `state` is layers.current(), `containers` compose.ps() ({} when Docker does not
    answer), `job` the summary of the last job or None, `failures(kind, id)` the failed or canceled last job on an
    item ({id, verb, error, login}, jobs.Jobs.failed_job) or None, `license_features` the features the license allows
    or None when unknown, `docker` {error, memory}: why Docker does not answer, and the memory it has; `created` what
    each playground's seed recorded: {id: [{what, count}]}; `saved` what each service is as saved
    (project.saved()), what runs behind it saying "Apply needed"; `license_status` the license as license.status()
    reads it, None without one to read; `platforms` the platforms registered here (platforms.read()), of which the
    page gets {id, name, kind, url}; `deployed` what each playground holds in the places besides This machine:
    {id: [{place, environment, created: [{what, count}]}]} (places.deployed); `changed` the settings that differ in
    what runs behind by configuration ({service: {settings: [name], labels}}, drift.changes). Of the job, the page gets all but
    `changes`, `containers` and `settled`: they are in the state and the progress of every item. Of Docker, it gets why
    it does not answer and what to know of its memory; `wait` says why nothing can be started now. `blind`: what runs
    is not known, Docker not answering or docker-compose.yml not loading: no state is said, but, while only Docker
    does not answer, what is on, which the files say. The consoles come in their order, which puts the legacy ones
    last. The state of the catalogs is read by the caller, who has the disk: `git` (git_state), `catalogs` (the rows of the
    Catalogs page), `content` (what the first start says of the content) and `layout` (where the migration of an
    existing machine is) are put in the body as they are given, and left out when they are not."""
    platforms = list(platforms)
    loaded = project["core_loaded"]
    readonly = {ns for ns, found in (project.get("catalogs") or {}).items() if found.get("kind") == "git"}
    forwarded = {key: value for key, value in (("git", git), ("catalogs", catalogs), ("content", content),
                                               ("layout", layout)) if value is not None}
    containers = containers if loaded else {}   # without the core, Compose reads nothing: what runs is not known
    running_job = job if job and not job["done"] else None
    # The last job, while what it starts or stops is on its way: as it runs, then, once it ended well, until every
    # container it waits for is ready, or it settled short of that (one is unhealthy, keeps restarting, exited). One
    # that failed or was canceled measures nothing more
    settling = job and job["done"] and job["ok"] and not job["settled"] and job["progress"] is not None \
        and job["progress"] < 1
    last = job if running_job or settling else None
    changing = layers.changing(project, running_job["changes"]) if running_job else {}
    # What runs behind what is saved; what the running job starts or stops is on its way, not behind
    causes = {name: cause for name, cause in lateness(project, containers, saved).items() if name not in changing}
    late = {name: reasons.why(project["services"][name], containers[name], cause,
                              *[(changed or {}).get(name, {}).get(key, default) for key, default in (("settings", ()), ("labels", False))])
            for name, cause in causes.items()}
    waiting = {name: _unlicensed(license_status) for name in held(project, containers)}
    services = _services(project, state, containers, running_job, late, waiting)
    running = up(project, containers)
    # What the job is stopping does not count as running: no other row says that its start would stop it
    stopping = (running_job or {}).get("changes", {}).get("stop", [])
    running["addons"] -= {uid for kind, uid in stopping if kind == "addon"}
    # What the functions above work from, in one dict. `changing`: what the running job starts or stops, which says
    # so in its state; `moved`: what the last job did, which its progress measures; `blind`: what runs is not known,
    # `docker_down` because Docker does not answer, what is on being known all the same; `behind`: what runs behind
    # what is saved; `held`: what the Lab UI does not start for want of a usable license, and `unlicensed` its note;
    # `unlicensing`: with no license saved, what still runs one, which loses it once it is made again
    c = {"project": project, "state": state, "up": running, "job": running_job,
         "wait": _wait(docker, running_job, loaded), "failures": failures, "features": license_features,
         "services": services, "created": created, "blind": bool(docker["error"]) or not loaded,
         "docker_down": bool(docker["error"]) and loaded,
         "changing": changing, "behind": set(late), "why": late, "license": license_status, "held": set(waiting),
         "unlicensed": _unlicensed(license_status), "kinds": {record["kind"] for record in platforms},
         "unlicensing": license.unapplied(project, containers, license_status["digest"])
         if license_status and license_status["status"] == "none" else [],
         "deployed": deployed or {},
         "last": last, "moved": layers.changing(project, last["changes"]) if last else {},
         "links": [dict(link, up=services[link["service"]]["state"]["running"]) for link in project["links"]]}
    for name, service in services.items():   # what its own Start on the Stack starts first, and makes again
        service["brings"] = _brings(c, [name])
        service["recreates"] = _recreates(c, [name])
    conflicts = _conflicts(project)
    addons = {aid: _addon(c, addon, conflicts) for aid, addon in project["addons"].items()}
    return {"docker": _docker(docker), "wait": c["wait"], "blind": c["blind"], "license": _license(c, containers),
            "job": {key: value for key, value in job.items() if key not in ("changes", "containers", "settled")}
            if job else None,
            "platform": _platform(c),
            "platforms": [{key: record[key] for key in ("id", "name", "kind", "url")} for record in platforms],
            "products": [{"id": group["product"], "name": project["products"][group["product"]]["name"]}
                         for group in project["groups"] if group["product"]],
            "categories": project["categories"],
            "playgrounds": _by_name(_playground(c, p, addons, created) for p in project["playgrounds"].values()),
            "setups": _by_name(_setup(c, setup, addons) for setup in project["setups"].values()),
            "addons": _by_name(addons.values()),
            "services": services,
            "links": [{key: link[key] for key in ("label", "url", "port", "service", "section", "legacy", "up")}
                      for link in c["links"]],
            "problems": [_problem(problem, readonly) for problem in project["problems"] + state["problems"]],
            "running": {"explicit": state["explicit"]}, **forwarded}
