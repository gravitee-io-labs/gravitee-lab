"""What runs on top of the platform: the on file, what an add-on needs and conflicts with, the plan of a start
or a stop and its job: the services it changes and what its commands wait for, for This machine or for a remote
place, which leaves the platform alone; what an apply recreates.

An item of a catalog is read here as any other (its id says where it is), and three gates stand between it and the
platform. The plan refuses a start for what the policy blocks, a catalog it lacks, its twin that is on, a port that is
taken, and a layout still being moved (`_blocked`). `check` asks the catalogs to verify what is installed before a start
runs (the preflight). `write`, the only writer of the on file, adds no entry for a unit that is blocked."""

import importlib
import os
import re
import stat

from ..docker import compose, envfiles
from .. import paths
from ..docker.jobs import JOBS, awaited
from ..gravitee import platforms
from ..catalogs import resolve
from .project import _control_plane, _dotenv, brought

LOCKED = f"{paths.ON_FILE} has your own changes"
# Why nothing starts while an old layout is being moved: the line of `layout_waiting`
WAITING = "Your settings are being moved. Try again in a moment."
# A line of the on file in the layout before catalogs, which the move translates: addons/<id>/compose.yml, the personal
# ones under local/
LEGACY = re.compile(r"(?:addons|playgrounds)/(?:local/)?[a-z0-9-]+/compose\.yml|setups/(?:local/)?[a-z0-9-]+\.yml")
# What a plan says besides ROWS and PLACE_ROWS when its item comes from a catalog: the row From, and the words under it
FROM = "from"
# Said once more in .env when the first catalog is added (prepare_catalogs)
NO_REMOTE_COMMENT = ("\n# Compose never fetches an include from a repository or a registry: nothing a catalog's files "
                     "name is read with your sign-ins\n")
# What .env says of the files Compose reads, as .env.example has it
COMPOSE_FILE_LINES = ("\n# The files Compose reads: the core, and what is on. "
                      "Keep it: without it, docker compose runs the core alone\n"
                      f"COMPOSE_FILE={paths.COMPOSE_FILE}\n")
OFF = "the catalog's Start and Stop are off"   # what a locked on file keeps from running, as its problem says
# What a plan says before it runs, in the order of its dialog: what starts, stops (it runs), is turned off (it is on
# and does not run), is kept, and restarts
ROWS = ("starts", "stops", "turns_off", "keeps", "restarts")
# The place of the core and of every container; any other place is a remote one, an environment of a platform
MACHINE = "machine"
# What the plan of a remote place says besides ROWS, in the same rows: where it deploys, what its gateway serves from
# now on, and what it stops serving
PLACE_ROWS = ("deploys_to", "serves", "stops_serving")


# ---------------------------------------------------------------------------------------------------------------------
# What is on
# ---------------------------------------------------------------------------------------------------------------------

def _known(project, ids):
    """An add-on the project does not have (its file does not load) is left out of everything."""
    return set(ids) & set(project["addons"])


def closure(project, ids):
    """These add-ons and every add-on they need."""
    known = _known(project, ids)
    return known | {n for aid in known for n in _known(project, project["addons"][aid]["needs"])}


def _reason(project, a, b):
    """Why two add-ons cannot run together, or None when they can. Either of them may say so."""
    for one, other in ((a, b), (b, a)):
        declared = project["addons"][one]["conflicts"]
        if other in declared:
            return declared[other]
    return None


def conflicts(project, ids):
    """The pairs among these add-ons that cannot run together: [{a, b, reason}]."""
    ids, found = sorted(_known(project, ids)), []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            reason = _reason(project, a, b)
            if reason is not None:
                found.append({"a": a, "b": b, "reason": reason})
    return found


def _unset(project):
    """The add-ons with a required setting left without a value in their .env: {id: [the names of those settings]}. The .env
    of a catalog's add-on is read as a catalog's file is (project._dotenv): one that is a link or a pipe holds none."""
    found = {}
    for aid, addon in sorted(project["addons"].items()):
        required = [setting["key"] for setting in addon["settings"] if setting["required"]]
        if required:
            values = _dotenv(addon["env_file"])
            lacking = [key for key in required if not values.get(key)]
            if lacking:
                found[aid] = lacking
    return found


def _effective(project, playground, explicit):
    """Every add-on that is on: the explicit ones, what the playground includes, and what those need."""
    brought = project["playgrounds"][playground]["addons"] if playground else []
    return closure(project, [*explicit, *brought])


def is_on(kind, uid, playground, effective):
    """Whether what owns a service runs with what is on: the platform always, an add-on that is on, the playground
    that is on. A setup owns nothing: its add-ons do."""
    return kind == "platform" or (kind == "addon" and uid in effective) or (kind == "playground" and uid == playground)


def owners_of(service):
    """Every unit that writes a service: the twins of one item share its services, and whichever of them is on, or asked to start,
    runs them. Its one `owner` (the last) when a service says no more."""
    return service.get("owners") or [service["owner"]]


# The note of what runs without being on: the fact, as its badge says it wherever it is drawn (a row, a component of Images)
OUTSIDE = "Started by hand"


def outside(kind="", names=()):
    """The note of what runs without being on, {note, note_title}: the platform does not start it, and for a setup which of
    its add-ons run so. What its Turn on does is the button's to say, not the note's."""
    if kind == "setup":
        verb, them = ("was", "it") if len(names) == 1 else ("were", "them")
        title = f"{', '.join(names)} {verb} not started from the Lab UI, so the platform does not start {them}."
    else:
        title = "Not from the Lab UI, so the platform does not start it."
    return {"note": OUTSIDE, "note_title": title}


def held_back(project, name):
    """Whether the Lab UI does not start a service: it exits without a usable license (`needs_license`) and there is
    none. A service the project does not know (one only the user's own on file adds) is never held back."""
    service = project["services"].get(name)
    return bool(service) and service["needs_license"] and not project["license_usable"]


def up_services(project, playground, effective):
    """The services `up` names: the platform's and those of what is on, except a seed, which only runs when its
    playground is set up or seeded again, and what needs a license that is not usable (held_back): it would only exit
    again and again."""
    return sorted(name for name, service in project["services"].items() if service["role"] != "seed"
                  and not held_back(project, name)
                  and any(is_on(one["kind"], one["id"], playground, effective) for one in owners_of(service)))


def _addon_services(project, ids):
    return [name for aid in ids if aid in project["addons"] for name in project["addons"][aid]["services"]]


def unit_services(project, unit, on):
    """The services that say what an item does: an add-on's own, a playground's apps and its seed. One without any
    (an add-on without a service, a playground without apps) is what it brings while it is on, a playground's seed
    with it: a seed only seeds, what it brings says whether it runs; an add-on that brings nothing either, the core
    services it changes. While it is off, only its own: what it brings may
    run for something else. A setup has none of its own: it is its add-ons."""
    if unit["kind"] == "setup":
        return _addon_services(project, unit["members"])
    if (unit["apps"] if unit["kind"] == "playground" else unit["services"]) or not on:
        return list(unit["services"])
    found = [*unit["services"], *_addon_services(project, brought(unit))]
    # An add-on that only changes core services is them: it runs while they run with it
    return found or (list(unit["restarts"]) if unit["kind"] == "addon" else [])


def service_name(project, name):
    """A service as a person reads it: its label, with what it belongs to when another service has the same label
    ("Gateway (API Management)", "Gateway (Access Management)")."""
    services = project["services"]
    label, owner = services[name]["label"], services[name]["owner"]
    if sum(service["label"] == label for service in services.values()) > 1:
        return f"{label} ({owner.get('name') or owner['id']})"
    return label


def _waiting():
    """Whether the layout of an old machine is still being moved to the catalogs (layout.waiting()). The module is looked
    up when asked, because the move reads the on file with this one."""
    from . import layout
    return bool(layout.waiting())


def ready():
    """Raises RuntimeError(WAITING) while the old layout is being moved: what the Lab starts or applies would be read from
    files that are not where the on file says."""
    if _waiting():
        raise RuntimeError(WAITING)


def current(project):
    """What the on file turns on: {playground, explicit, effective, setups, targets, missing, unset, problems, locked}.
    `targets` says where each item runs that does not run on this machine. `unset`
    names the required settings each add-on of `missing` lacks. A setup
    listed there stands for the add-ons it includes; `setups` names it. An entry whose file is gone is ignored and
    reported. `locked` is the reason the file must not be rewritten: it holds more than the Lab UI writes, or an entry
    whose file does not load, which a rewrite would drop. An item of a catalog is an entry like any other, and one the
    policy blocks is still one: it is loaded, so it locks nothing. While the old layout is being moved an entry
    of that layout is neither a problem nor a lock: it is off until the move translates it."""
    file = paths.ROOT / paths.ON_FILE
    entries = compose.includes(paths.ON_FILE)
    lines = [line for line in (file.read_text(encoding="utf-8").split("\n") if file.is_file() else [])
             if line.strip() and not line.lstrip().startswith("#")]
    # The Lab UI writes `include:` and one line per entry: any other line is the user's
    own = bool(lines) and (lines[0].split("#")[0].strip() != "include:" or len(lines) != len(entries) + 1)
    playgrounds, setups, explicit, broken, problems = [], [], set(), [], []
    moving = None   # whether the layout is being moved: asked once, and only for a line that looks like the old layout's
    for path in entries:
        kind, uid = paths.unit_of(path) or (None, None)
        if not kind and LEGACY.fullmatch(path):
            moving = _waiting() if moving is None else moving
            if moving:
                continue
        if not (paths.ROOT / path).is_file():
            problems.append({"file": paths.ON_FILE, "message": f"{path} does not exist"})
        elif kind == "addon" and uid in project["addons"]:
            explicit.add(uid)
        elif kind == "setup" and uid in project["setups"]:
            setups.append(uid)
            explicit |= _known(project, project["setups"][uid]["includes"])
        elif kind == "playground" and uid in project["playgrounds"]:
            playgrounds.append(uid)
        elif kind:   # an add-on, a setup or a playground that does not load: the project's problems say why
            broken.append(path)
        else:
            own = True
    playground, locked = (playgrounds[0] if playgrounds else None), None
    if own or len(playgrounds) > 1:
        locked = LOCKED
        problems.append({"file": paths.ON_FILE, "message": f"has your own changes: {OFF}"})
    elif broken:
        locked = f"{broken[0]} does not load"
        problems += [{"file": paths.ON_FILE, "message": f"{path} does not load: {OFF}"} for path in broken]
    unset = _unset(project)
    # Where an item runs when it is not this machine: {"<kind>:<id>": the kind of platform}
    where = {f"{unit[0]}:{unit[1]}": kind for path, kind in compose.targets(paths.ON_FILE).items()
             if (unit := paths.unit_of(path))}
    return {"playground": playground, "explicit": sorted(explicit), "setups": sorted(setups),
            "effective": sorted(_effective(project, playground, explicit)), "targets": where,
            "missing": list(unset), "unset": unset, "problems": problems, "locked": locked}


# ---------------------------------------------------------------------------------------------------------------------
# Plans: what a start or a stop changes, said before it runs
# ---------------------------------------------------------------------------------------------------------------------

def lookup(project, kind, uid):
    """An add-on, a setup or a playground of the project, by its id."""
    units = project[kind + "s"]
    if uid not in units:
        raise paths.NotFound(f"No such {paths.NOUNS[kind]}: {uid}")
    return units[uid]


def key(kind, uid):
    """What names a row where an id alone is not enough, an id being unique within its kind only: "<kind>:<id>". The
    page sends back the rows it showed (`accept`) this way."""
    return f"{kind}:{uid}"


def _row(kind, uid, name):
    return {"kind": kind, "id": uid, "name": name}


def _addon_rows(project, ids):
    rows = [_row("addon", aid, project["addons"][aid]["name"]) for aid in ids]
    return sorted(rows, key=lambda row: (row["name"].lower(), row["id"]))


def _service_row(project, name):
    return _row("service", name, service_name(project, name))


def service_rows(project, names):
    """These services as rows, by id: what restarts."""
    return [_service_row(project, name) for name in sorted(names)]


def _apps_row(project, pid):
    """The row of a playground's own containers: its apps, or the playground itself when it has none."""
    if not pid:
        return []
    playground = project["playgrounds"][pid]
    return [_row("playground", pid, playground["name"] + (" apps" if playground["apps"] else ""))]


def _data_row(project, pid):
    """What a playground's seed created in Gravitee, which a stop keeps. Without a seed, it created nothing."""
    if not (pid and project["playgrounds"][pid]["seed"]):
        return []
    return [_row("data", pid, f"What {project['playgrounds'][pid]['name']} created in Gravitee")]


def _targets(project, kind, unit, do):
    """The add-ons an action is about. A setup starts the add-ons it includes, and stops all its members."""
    if kind == "addon":
        return {unit["id"]}
    if kind == "setup":
        return _known(project, unit["includes"] if do == "start" else unit["members"])
    return _known(project, unit["addons"])


def _without(project, state, addons):
    """What stays on once these add-ons go: (playground, explicit). What needs one of them goes with it."""
    explicit = {e for e in state["explicit"] if not closure(project, [e]) & addons}
    playground = state["playground"]
    if playground and set(project["playgrounds"][playground]["addons"]) & addons:
        playground = None
    return playground, explicit


def _users(project, state, setup):
    """What else that is on uses the add-ons of a setup, {add-on: the names of what uses it}: the playground that
    brings one, an add-on turned on that needs one, another setup that was turned on (all it includes is turned on,
    and not only through this one). A setup's stop keeps them on."""
    members, explicit, found = _known(project, setup["members"]), set(state["explicit"]), {}

    def use(ids, name):
        for aid in members & set(ids):
            found.setdefault(aid, set()).add(name)
    playground = project["playgrounds"].get(state["playground"])
    if playground:
        use(playground["addons"], playground["name"])
    for aid in explicit - members:
        use(closure(project, [aid]), project["addons"][aid]["name"])
    for other in project["setups"].values():
        includes = set(other["includes"])
        if other["id"] != setup["id"] and includes and includes <= explicit and not includes <= set(setup["includes"]):
            use(other["members"], other["name"])
    return found


def _after(project, state, kind, unit, do, kept=frozenset()):
    """The on file once the action is done: (playground, explicit). A start is never refused for a conflict: what
    is in conflict with what comes stops, with everything that needs it. A setup's stop turns its add-ons off but
    the ones something else that is on uses (`kept`)."""
    targets = _targets(project, kind, unit, do)
    if (kind, do) == ("playground", "stop"):
        return (None if state["playground"] == unit["id"] else state["playground"]), set(state["explicit"])
    if do == "stop":
        return _without(project, state, targets - set(kept))
    if kind != "playground" and targets <= set(state["effective"]):
        return state["playground"], set(state["explicit"])   # a retry: the on file stays, what does not run starts
    coming = closure(project, targets)
    clashing = {y for y in state["effective"] if any(_reason(project, y, t) is not None for t in coming)}
    playground, explicit = _without(project, state, clashing)
    return (unit["id"], explicit) if kind == "playground" else (playground, explicit | targets)


def _version(text):
    """A version as it orders: (its numbers, its pre-release identifiers or None). "4.13.0-alpha.3-debian" is
    ((4, 13, 0), ["alpha", "3"]); None for a tag without numbers."""
    found = re.match(r"(\d+(?:\.\d+)*)(?:-([0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*))?", text or "")
    if not found:
        return None
    return tuple(map(int, found.group(1).split("."))), found.group(2) and found.group(2).split(".")


def at_least(tag, wanted):
    """Whether an image tag is that version or a later one, as semantic versioning orders them: a pre-release comes before
    its release, so 4.13.0-alpha.5 is not a 4.13 (nor a 4.13.0), and a version asked in release form is met by a release.
    A wanted pre-release orders the previews of its version too: 4.13.0-alpha.2 is before 4.13.0-alpha.3, itself before
    4.13.0-beta.1 and 4.13.0; a later version, a preview included, is after them all (4.14.0-alpha.1 is a 4.13). A tag
    without numbers (latest) says nothing of its version: it is taken as fine. A version asked that has no numbers
    (amp-demo-latest, a build and not a release) is not ordered: it is that build or it is not."""
    have, want = _version(tag), _version(wanted)
    if not want:
        return VARIANT.sub("", str(tag)) == VARIANT.sub("", str(wanted))
    if not have:
        return True
    size = max(len(have[0]), len(want[0]))
    numbers = [version[0] + (0,) * (size - len(version[0])) for version in (have, want)]
    if numbers[0] != numbers[1]:
        return numbers[0] > numbers[1]
    if not want[1]:
        return not have[1]   # a preview comes before its release
    if not have[1]:
        return True   # the release comes after its previews
    # Identifier by identifier, as semantic versioning orders them: numbers by value, before words
    def key(part):
        return (0, int(part), "") if part.isdigit() else (1, 0, part)
    return [key(part) for part in have[1]] >= [key(part) for part in want[1]]


def service_of(project, variable):
    """The first service whose image a version variable sets, as (its name, it); None when none does. A service whose image
    only follows it (`${OWN_VERSION:-${VARIABLE:-1.2}}`, as the portals of a playground follow API Management's) does not set
    it: the first of those is the answer only when no service sets it, so that what a variable belongs to is its component's
    owner, as the Images page says, whatever the names sort like."""
    from ..docker import images   # images imports this module
    found = sorted(project["services"].items())
    sets = ((name, service) for name, service in found
            if variable in {(key or (None,))[0] for key in images._parts(service.get("raw_image") or "")[::2]})
    return next(sets, None) or next(((name, service) for name, service in found
                                     if "${" + variable in (service.get("raw_image") or "")), None)


def saved_setting(project, variable):
    """What is saved for a version variable (the tag of its image) or a registry variable (its host), and the name of what it
    belongs to; None when no service's image uses it."""
    found = service_of(project, variable)
    if not found:
        return None
    return (found[1]["registry"] if variable.endswith("_REGISTRY") else found[1]["tag"]), found[1]["owner"]["name"]


def saved_image(project, variable):
    """The tag saved for the image a version variable sets, and the name of what it belongs to ("API Management");
    None when no service's image uses it."""
    found = service_of(project, variable)
    return (found[1]["tag"], found[1]["owner"]["name"]) if found else None


def enough(unit, variable, tag, wanted):
    """Whether an image tag meets the minimum a playground asks of it (`requires.images`): it is that version or a later one
    (at_least), or the playground lists it among the versions it was tested with, which is the playground saying it works on it
    (a preview such as 4.13.0-alpha.5 is before 4.13, and is fine only so)."""
    return at_least(tag, wanted) or any(VARIANT.sub("", tag) == VARIANT.sub("", str(version[variable]))
                                        for version in unit.get("tested") or [] if version.get(variable))


def _lacking(project, unit):
    """[(variable, the name of its component, the version it needs at least)] of every image a playground needs a later version of
    than the one saved (`requires.images`), in the order of its `requires`."""
    found = []
    for variable, wanted in ((unit.get("requires") or {}).get("images") or {}).items():
        saved = saved_image(project, variable)
        if saved and not enough(unit, variable, saved[0], wanted):
            found.append((variable, saved[1], wanted))
    return found


def needed(project, unit):
    """Every image a playground needs a later version of than the one saved (`requires.images`), as [(the name of its
    component, the version it needs at least)], in the order of its `requires`."""
    return [(name, wanted) for _, name, wanted in _lacking(project, unit)]


def switch(project, unit, chosen=False):
    """The versions a playground's start takes the stack to, {variable: value}: those of the latest set it was tested with that differ
    from the ones saved, registries included. Always when the images saved are older than it asks (`requires.images`); when they only
    differ, if the person chose it (`chosen`). That set must give each image it lacks, and meet what it asks of each. None when there
    is nothing to go to."""
    lacking, tested = _lacking(project, unit), next((one for one in reversed(unit.get("tested") or []) if one), {})
    asked = ((unit.get("requires") or {}).get("images") or {}).items()
    if not (lacking or chosen) or not all(tested.get(variable) for variable, _, _ in lacking) \
            or not all(enough(unit, variable, str(tested[variable]), wanted) for variable, wanted in asked if tested.get(variable)):
        return None
    to = {}
    for variable, value in tested.items():
        found = saved_setting(project, variable)
        if found and str(found[0]) != str(value):
            to[variable] = str(value)
    return to or None


def switch_rows(project, to):
    """The rows of what a switch changes, one for each image ("API Management 4.12.20 → 4.13.0-alpha.3", with the registry it comes from
    when that changes too), as (what goes forward or sideways, what goes back: _goes_back). `component` and `to` are for whoever says it
    in other words (the checks of a playground's page)."""
    from ..docker import images   # images imports this module
    rows, back = {}, set()
    for variable in sorted(to):
        image = re.sub(r"_(VERSION|REGISTRY)$", "", variable)
        version, registry = f"{image}_VERSION", f"{image}_REGISTRY"
        found = saved_setting(project, version) or saved_setting(project, registry)
        if image in rows or not found:
            continue
        words = found[1]
        if version in to:
            have = saved_setting(project, version)[0]
            words += f" {have} → {to[version]}"
            if _goes_back(have, to[version]):
                back.add(image)
        if registry in to:
            words += f" from {images.registry_label(to[registry])}"
        rows[image] = {**_row("version", image, words), "component": found[1], "to": to.get(version) or to.get(registry),
                       "have": (saved_setting(project, version) if version in to else saved_setting(project, registry) or (None,))[0]}
    return [row for key, row in rows.items() if key not in back], [row for key, row in rows.items() if key in back]


VARIANT = re.compile(r"-(?:debian|alpine|slim)$")   # the base an image is built on: not part of its version


def _tested(have, tested):
    """Whether an image tag is the version a playground was tested with, or taken for it: a release of the same minor
    version (4.13.7 for 4.13.0), when the version it was tested with is a release too. A preview (4.13.0-alpha.3), a demo
    build and a tag without numbers (latest) are tested only as they are: they say nothing of what comes after them, and a
    moving one nothing of what it holds today. Another minor or major version is not taken for it: Gravitee changes
    things between them."""
    have, tested = VARIANT.sub("", have), VARIANT.sub("", tested)
    if have == tested:
        return True
    wanted, found = _version(tested), _version(have)
    if not wanted or not found or wanted[1] or found[1]:
        return False
    return found[0][:2] == wanted[0][:2] and at_least(have, tested)


def _goes_back(have, version):
    """Whether going from the version that is saved (`have`) to another is going back, so that what the newer one wrote may not open: a
    release before it, or a release after a build that says nothing of its numbers (latest, master-latest, amp-demo-latest), which is
    taken as the newest. Going to such a build is going forward."""
    found, wanted = _version(VARIANT.sub("", have or "")), _version(VARIANT.sub("", version or ""))
    if found and wanted:
        return VARIANT.sub("", have) != VARIANT.sub("", version) and at_least(have, version)
    return not found and bool(wanted)


def _recorded(project, versions, images, image_id):
    """Whether what a machine started a playground on well is what is saved: each image it kept has the tag it had, and a tag
    that is not a release, which is rebuilt under the same name, is the same image (`image_id` says which one a service
    has). A registry is the same host."""
    for variable, wanted in versions.items():
        found = saved_setting(project, variable)
        if not found:
            continue
        if variable.endswith("_REGISTRY"):
            if found[0] != wanted:
                return False
            continue
        have = found[0]
        if VARIANT.sub("", have) != VARIANT.sub("", wanted):
            return False
        version = _version(VARIANT.sub("", have))
        if not (version and not version[1]) and (not image_id or image_id(service_of(project, variable)[1]) != images.get(variable)):
            return False
    return any(saved_setting(project, variable) for variable in versions)


CONFORM = re.compile(r"\d+\.\d+\.\d+")   # major.minor.patch: a release, the only version taken as it is


def conform(tag):
    """Whether an image tag is a release (4.13.7, 4.13.7-debian). Anything else is not safe to assume of: a pre-release
    (4.13.0-alpha.5), a demo build (amp-demo-latest), a moving tag (latest, 4.13), whatever its numbers say."""
    return bool(CONFORM.fullmatch(VARIANT.sub("", tag or "")))


def _listed(unit, variable, tag):
    """Whether a playground names this version of the image itself: among the versions it was tested with, or as the one it
    requires. A variant (-debian) is not part of a version."""
    named = [version[variable] for version in unit.get("tested") or [] if version.get(variable)]
    named.append(((unit.get("requires") or {}).get("images") or {}).get(variable))
    return any(VARIANT.sub("", tag) == VARIANT.sub("", str(one)) for one in named if one)


def untested(project, unit, recorded=(), image_id=None):
    """What a playground would start on that it was never tested with: None when there is none, else {"images": [{variable,
    name, have, tested, goes_back}], "to": {variable: version}}. An image the playground tells of (`tested`, the sets of versions
    it was built and checked on, each a mapping of version variables, the latest last; and `requires.images`) is untested when
    the saved images are not those of one of its sets (_tested), `tested` the version of the latest set to go to. `goes_back`:
    what is there is no older than the version it was tested with, or says nothing of its version, so that the data it wrote
    may not open on it. An image it asks a minimum of that no set covers is untested when its version is not a release
    (`conform`) and the playground does not name it (`_listed`): `tested` is then None, there is no version to go to. What
    it was started on well on this machine (`recorded`: {versions, images}, see tested.py) is tested too."""
    sets = [tested for tested in unit.get("tested") or [] if tested]
    asked = (unit.get("requires") or {}).get("images") or {}
    saved = {variable: saved_setting(project, variable) for variable in [*(v for tested in sets for v in tested), *asked]}
    judged, found = None, []
    for tested in sets:
        known = [variable for variable in tested if saved[variable]]
        if known and all(_tested(saved[variable][0], tested[variable]) for variable in known):
            judged = tested
    if any(_recorded(project, one["versions"], one.get("images") or {}, image_id) for one in recorded):
        return None
    latest = judged or next((tested for tested in reversed(sets) if any(saved[variable] for variable in tested)), None)
    if latest and not judged:
        found = [{"variable": variable, "name": saved[variable][1], "have": saved[variable][0], "tested": version,
                  "goes_back": not variable.endswith("_REGISTRY") and _goes_back(saved[variable][0], version)}
                 for variable, version in latest.items() if saved[variable] and not _tested(saved[variable][0], version)]
    for variable in asked:
        if saved[variable] and variable not in (latest or {}) and not conform(saved[variable][0]) \
                and not _listed(unit, variable, saved[variable][0]):
            found.append({"variable": variable, "name": saved[variable][1], "have": saved[variable][0], "tested": None,
                          "goes_back": False})
    if not found:
        return None
    return {"images": found, "to": {one["variable"]: one["tested"] for one in found if one["tested"]}}


def or_later(wanted):
    """The words after a minimum: a release is asked "or later", a build that is no release (amp-demo-latest) is asked as it is."""
    return " or later" if _version(wanted) else ""


def too_old(project, unit):
    """Every image a playground needs a later version of than the one saved (`requires.images`), in one sentence, as its
    Start says it; None when every one is recent enough."""
    lacking = [f"{name} {wanted}{or_later(wanted)}" for name, wanted in needed(project, unit)]
    if not lacking:
        return None
    listed = " and ".join([", ".join(lacking[:-1]), lacking[-1]] if len(lacking) > 1 else lacking)
    return f"Needs {listed}: choose {'it' if len(lacking) == 1 else 'them'} on the Images page"


def _minimum(version):
    """A version of three numbers as a person says a floor: "2.0.0" is 2.0, "2.1.3" stays."""
    return re.sub(r"^(\d+\.\d+)\.0$", r"\1", str(version or ""))


def need_line(need):
    """What a unit says of one catalog it lacks (an entry of `needs_catalogs`), by its reason: the words of the row's
    note and of Before you start."""
    name = need.get("name") or need.get("id") or "a catalog"
    return {"downloading": f"Downloading {name}", "failed": f"Needs {name}",
            "gone": f"Needs {need.get('unit') or 'an item'}, which is no longer in {name}",
            "old": f"Needs {' '.join(part for part in (name, _minimum(need.get('min'))) if part)} or later"
            }.get(need.get("reason"), f"Needs the catalog {name}")


def _why_not(unit):
    """Why a unit cannot start, in one line: the first reason it is blocked, else the first catalog it lacks. None when
    nothing is against it. A unit that does not say (a hand-made one) has nothing against it."""
    reasons = (unit.get("blocked") or {}).get("reasons") or []
    needs = unit.get("needs_catalogs") or []
    return str(reasons[0]) if reasons else need_line(needs[0]) if needs else None


def _units(project):
    """The units of the project as the resolver takes them: {kind: {id: unit}}."""
    return {"addon": project["addons"], "setup": project["setups"], "playground": project["playgrounds"]}


def _reach(project, kind, uid):
    """An item and everything it includes, directly or not, the item first: [(kind, id)] of those the project has, in the
    order the on file turns them on in."""
    units = _units(project)
    return [node for node in resolve.closure(units, kind, uid) if node[1] in units.get(node[0], {})]


def _unusable(project, kind, uid):
    """Why an item cannot start for what the catalogs say of it or of anything it includes: blocked by the policy, or a
    catalog it lacks. The item's own reason comes first; a unit it brings that is blocked blocks it too (T25), and a
    hand-made unit may say it of the units it brings alone."""
    for node in _reach(project, kind, uid):
        why = _why_not(project[node[0] + "s"][node[1]])
        if why:
            return why
    return None


def blocked_files(project):
    """{unit file: why it cannot be turned on} for every unit the policy blocks or that lacks a catalog: the `blocked` map
    that `write` takes, so that no entry is added for a unit that must not start."""
    return {unit["file"]: why for group in ("addons", "setups", "playgrounds") for unit in project[group].values()
            if unit.get("file") and (why := _why_not(unit))}


def _on_or_up(project, state, up, kind, uid):
    """Whether an item is on (the on file turns it on), or runs. A setup is on once all its add-ons are."""
    unit = project[kind + "s"].get(uid)
    if not unit:
        return False
    if kind == "addon":
        return uid in state["effective"] or bool(up) and uid in up["addons"]
    if kind == "playground":
        return state["playground"] == uid or bool(up) and uid in up["playgrounds"]
    members = _known(project, unit["members"])
    return bool(members) and (members <= set(state["effective"]) or bool(up) and bool(members & up["addons"]))


def _twin_on(project, state, up, kind, unit):
    """"Also in {name}: turn it off first" while the other half of the same item is on or up, or None: a personal
    item and the item of a catalog it became have the same service names, so they cannot run together."""
    twin = unit.get("twin")
    if isinstance(twin, dict) and twin.get("id") and _on_or_up(project, state, up, kind, twin["id"]):
        return f"Also in {twin.get('name') or 'a catalog'}: turn it off first"
    return None


def _twins_on(project, state, up, kind, unit, going=frozenset()):
    """The first _twin_on of the item and of everything it brings, the item's first, or None: the other half of a unit that
    starts with the item is as much in the way as the other half of the item, since both would be in the on file with the
    same service names, and the first file Compose reads would win without a word. The item's own other half is in the way
    whatever the plan does with it; that of a unit it brings is not when this very plan turns it off (`going`: [(kind,
    id)], a playground that is replaced takes what it brought), nor when the item brings both halves itself, which is its
    own doing."""
    nodes = _reach(project, kind, unit["id"])
    why, aside = _twin_on(project, state, up, kind, unit), {*nodes, *going}
    for node in nodes:
        member = project[node[0] + "s"][node[1]]
        twin = member.get("twin")
        if not why and node != (kind, unit["id"]) and isinstance(twin, dict) and (node[0], twin.get("id")) not in aside:
            why = _twin_on(project, state, up, node[0], member)
    return why


def _published(project, names):
    """{host port: the name of what owns the service that publishes it} for these services, whatever the port's spelling."""
    found = {}
    for name in names:
        service = project["services"].get(name)
        for port in (service or {}).get("ports") or ():
            if str(port).isdigit():
                found.setdefault(int(port), service["owner"].get("name") or service["owner"]["id"])
    return found


def _port_clash(project, state, up, kind, unit, going=frozenset()):
    """"Port 8140 is used by Acme Bakery" when a host port the item's services publish is published by something else
    that is on or runs, the first by port; None otherwise. Only an item that brings a unit of a catalog (that is every item
    but the core) is asked. What the item brings itself, its twin and what this very plan turns off (`going`: [(kind, id)])
    do not count: they do not run with it."""
    nodes = _reach(project, kind, unit["id"])
    members = [project[node[0] + "s"][node[1]] for node in nodes]
    if not any(member.get("catalog") for member in members):
        return None
    mine = set(nodes) | going | {(node[0], member["twin"]["id"]) for node, member in zip(nodes, members)
                                 if isinstance(member.get("twin"), dict)}
    wanted = _published(project, [name for member in members for name in member.get("services") or ()])
    if not wanted:
        return None
    on = [project["playgrounds"].get(state["playground"]) or {}] + [project["addons"].get(aid) or {}
                                                                     for aid in state["effective"]]
    names = {name for one in on for name in one.get("services") or ()} | set((up or {}).get("services") or ())
    others = [name for name in sorted(names) if name in project["services"]
              and (project["services"][name]["owner"]["kind"], project["services"][name]["owner"]["id"]) not in mine]
    taken = _published(project, others)
    clash = sorted(set(wanted) & set(taken))
    return f"Port {clash[0]} is used by {taken[clash[0]]}" if clash else None


def _blocked(project, state, kind, unit, do, gateway=None, up=None, going=frozenset()):
    """Why the action cannot run, or None: the on file is locked; the old layout is still being moved; the policy blocks
    the item or something it brings, or it lacks a catalog; the twin of the item or of something it brings is on
    (_twins_on); an add-on it brings lacks a setting; a playground is on images older than it needs, which would end on its
    seed, after the whole setup, so its Start waits for them; a host port it publishes is taken. A stop is refused
    only by a locked file: a unit that is blocked never keeps anything from stopping. `gateway`: the add-on of a remote place's gateway, whose token the job writes, so it
    is not asked for; the images are the core's, which a remote place does not run, and the gateway is judged with the
    item. `up`: what runs (model.up()), for the twin and the ports. `going`: [(kind, id)] this plan turns off."""
    if state["locked"]:
        return state["locked"]
    if do != "start":
        return None
    if _waiting():
        return WAITING
    why = _unusable(project, kind, unit["id"]) or (_unusable(project, "addon", gateway) if gateway else None)
    if why:
        return why
    twin = _twins_on(project, state, up, kind, unit, going)
    if twin:
        return twin
    lacking = (closure(project, _targets(project, kind, unit, do)) & set(state["missing"])) - {gateway}
    names = sorted(project["addons"][aid]["name"] for aid in lacking)
    if names:
        return f"{names[0]} needs settings"
    old = too_old(project, unit) if kind == "playground" and not gateway and not switch(project, unit) else None
    return old or _port_clash(project, state, up, kind, unit, going)


def _runs(project, up, playground):
    """Whether something of a playground runs: one of its apps, or, without any, one of what it brings. Its seed runs
    only while it seeds."""
    names = playground["apps"] or _addon_services(project, playground["addons"])
    return bool(set(names) & up["services"])


def _strays(project, state, up, kind, unit, do):
    """What a stop takes besides what it turns off: the add-ons of the item that run without being on (a stop that
    failed, or what was started outside the Lab UI); of a playground, the add-ons it brings."""
    if do != "stop":
        return set()
    if kind == "addon":
        mine = {unit["id"]}
    elif kind == "setup":
        mine = _known(project, unit["members"])
    else:
        mine = closure(project, unit["addons"])
    return (mine - set(state["effective"])) & up["addons"]


def _removed(project, up, leaving, going):
    """The services a plan removes by name: those of the playground and of the add-ons that go, which have a
    container."""
    units = ([project["playgrounds"][leaving]] if leaving else []) + [project["addons"][aid] for aid in going]
    return sorted({name for unit in units for name in unit["services"]} & up["created"])


def _restarts(project, up, addons, playgrounds):
    """The core services Compose recreates: the ones configured by an add-on or a playground that comes or goes,
    among those that run. One that does not run takes the change when it starts."""
    units = [project["addons"][a] for a in addons] + [project["playgrounds"][p] for p in playgrounds]
    names = sorted({name for unit in units for name in unit["restarts"] if name in up["services"]})
    return [_service_row(project, name) for name in names]


def depends(project, on, names, past=()):
    """What these services depend on, and what that depends on in turn, among what is on (`on`): the configuration
    Compose reads has every add-on merged in, with what an add-on makes a core service depend on. The walk does not go
    into what `past` names, which it leaves out. A service the project does not know (one only the user's own
    on file adds) depends on nothing it can tell."""
    services, found, todo = project["services"], set(), list(names)
    while todo:
        for dependency in (services.get(todo.pop()) or {}).get("depends_on", ()):
            if dependency in on and dependency not in found and dependency not in past:
                found.add(dependency)
                todo.append(dependency)
    return found


def _idle(project, up, on, names):
    """What these services depend on, and what that depends on in turn, among what is on, that does not run: what
    Compose would start first, which `up --no-deps` names. What runs, or ended well, is left as it is, with what it
    depends on."""
    return depends(project, on, names, past=up["services"] | up["done"]) - set(names)


def _once(project, on, names):
    """The one-shots these services depend on, themselves or through what they depend on, among what is on: Compose
    would run them first (a migration before what it migrates), which `up --no-deps` leaves out. Run again, one that
    ended well has nothing more to do: a migration finds the database migrated."""
    services = project["services"]
    return {name for name in depends(project, on, names) if (services.get(name) or {}).get("oneshot")}


def dependencies(project, state, running, names):
    """What an `up` of these services starts first, as Compose does without `--no-deps`: what they depend on, and
    what that depends on in turn, among what is on, that does not run (`running`: the services that run); a one-shot
    that ended runs again. The Stack's Start of a group, of a service or of the containers of an add-on or a
    playground runs so."""
    on = set(up_services(project, state["playground"], state["effective"]))
    return {name for name in depends(project, on, names) - set(names) if name not in running}


def recreates(project, state, behind, names):
    """What an `up` of these services makes again on its way, as rows: what of them and of what they depend on, among
    what is on, runs behind what is saved (`behind`, model.behind). Compose makes again what it starts that changed,
    what it starts first too. The Stack's Start of the platform, of a group, of a service or of the containers of an
    add-on or a playground says it before the click."""
    on = set(up_services(project, state["playground"], state["effective"]))
    return start_rows(project, sorted((set(names) | depends(project, on, names)) & set(behind)))


def _playground_rows(project, pid, names):
    """A playground's services among these, as rows: its apps as one row when they all are (the playground itself
    when it has none), else each of them, its seed too, by its own."""
    services = project["services"]
    if set(project["playgrounds"][pid]["apps"]) <= set(names):
        return _apps_row(project, pid)
    return [_service_row(project, name) for name in sorted(names)
            if (services[name]["owner"]["kind"], services[name]["owner"]["id"]) == ("playground", pid)]


def start_rows(project, names):
    """What starts, as rows: the platform when all of it starts (a group that needs a license that is not usable is
    left out of it: held_back), else each of its groups that starts whole and each service of the others; then the
    playground, its apps as one row only when they all start; then the add-ons, by name."""
    services, rows, whole, held = project["services"], [], 0, 0
    for group in project["groups"]:
        starting = [name for name in group["services"] if name in names]
        lasting = [name for name in group["services"] if not services[name]["oneshot"]] or group["services"]
        if starting and set(lasting) <= set(starting):
            rows.append(_row("group", group["id"], group["name"]))
            whole += 1
        else:
            rows += [_service_row(project, name) for name in starting]
            held += all(held_back(project, name) for name in group["services"])
    if whole and whole + held == len(project["groups"]):
        rows = [_row("platform", "platform", project["name"])]
    # A core service of no declared group (the core's problems say so) is named alone
    grouped = {name for group in project["groups"] for name in group["services"]}
    rows += [_service_row(project, name) for name in sorted(names)
             if services[name]["owner"]["kind"] == "platform" and name not in grouped]
    owners = {(services[name]["owner"]["kind"], services[name]["owner"]["id"]) for name in names}
    rows += [row for kind, uid in sorted(owners) if kind == "playground"
             for row in _playground_rows(project, uid, names)]
    return rows + _addon_rows(project, [uid for kind, uid in owners if kind == "addon"])


def owner_rows(project, names):
    """What owns these services, as rows: the platform's groups in their order, the playground's apps, then the
    add-ons by name. The banner names what runs behind what is saved so."""
    services, names = project["services"], set(names)
    rows = [_row("group", group["id"], group["name"]) for group in project["groups"] if names & set(group["services"])]
    owners = {(services[name]["owner"]["kind"], services[name]["owner"]["id"]) for name in names}
    rows += [row for kind, uid in sorted(owners) if kind == "playground" for row in _apps_row(project, uid)]
    return rows + _addon_rows(project, [uid for kind, uid in owners if kind == "addon"])


def _restarting(project, rows):
    """The services these rows of restarts name: a service by its id, an add-on by its own services."""
    return {name for row in rows
            for name in (project["addons"][row["id"]]["services"] if row["kind"] == "addon" else [row["id"]])}


def _starts(project, up, kind, unit, playground, effective, restarts, fresh, gateway=None):
    """What a start runs, as rows and as the services `up --no-deps` names: those of the item and of the add-ons it
    brings that do not run, for a playground the platform too while none of it runs (once part of it runs, what its
    apps and its seed depend on); its seed when the start sets the playground up (`fresh`); what it restarts
    (`restarts`: the core services it changes, what of it runs behind what is saved, an add-on made again); the
    one-shots all that depends on, run again first (_once); and what these depend on among what is on and does not
    run, which Compose starts first. A one-shot of the item that ended well is not run again for itself."""
    services, on = project["services"], set(up_services(project, playground, effective))
    if kind == "playground":
        platform = [] if up["platform"] else [name for name, s in services.items() if s["owner"]["kind"] == "platform"]
        mine, addons = [*unit["apps"], *platform], closure(project, unit["addons"])
    else:
        mine, addons = [], closure(project, _targets(project, kind, unit, "start"))
    if gateway:   # a start on a place runs the gateway of its platform, whatever the item is
        addons = addons | closure(project, [gateway])
    wanted = {name for name in [*mine, *_addon_services(project, addons)] if name in on}
    start = {name for name in wanted if name not in up["services"] and name not in up["done"]}
    if fresh and unit.get("seed"):
        start.add(unit["seed"])
    restart = _restarting(project, restarts)
    start |= _once(project, on, start | restart)
    start |= _idle(project, up, on, start | restart)
    return start_rows(project, start), sorted(start | restart)


def _label(project, state, up, kind, unit, do):
    """The word of an item's button: a stop is "Stop" while something of the item runs, "Turn off" while it is on and
    does not run; a start is "Turn on" while something of it runs without being on, "Start" otherwise. Of a setup,
    that is one of its add-ons that runs and is not on, as for an add-on."""
    if kind == "addon":
        on, runs = unit["id"] in state["effective"], unit["id"] in up["addons"]
        outside = runs
    elif kind == "setup":
        members = _known(project, unit["members"])
        on, runs = bool(members) and members <= set(state["effective"]), bool(members & up["addons"])
        outside = bool(members & up["addons"] - set(state["effective"]))
    else:
        on = state["playground"] == unit["id"]
        runs = (on or bool(unit["apps"])) and _runs(project, up, unit)
        outside = runs
    if do == "stop":
        return "Stop" if runs else "Turn off"
    return "Turn on" if outside and not on else "Start"


def fresh(project, state, pid, created, seed_failed=False):
    """Whether a playground's start sets it up, as its first one does (its plugins, a build of its apps, its seed):
    while it is off, and while it is on with a seed that recorded nothing it created (it never seeded, or a reset
    emptied its record) or that failed (`seed_failed`: its container ended in an issue), which the start runs again.
    Otherwise its start runs it again like an add-on's. `created`: what its seed recorded."""
    seed = project["playgrounds"][pid]["seed"]
    return state["playground"] != pid or bool(seed and (not created or seed_failed))


def _kept_by(users):
    """Why a setup's stop has nothing to do: what else uses all of it ("Used by Acme Bakery")."""
    return "Used by " + ", ".join(sorted({name for names in users.values() for name in names}))


def _late(project, kind, unit, behind):
    """The services of an item that run behind what is saved (`behind`, model.behind): its start makes them again as
    saved. An add-on's own, a setup's add-ons', a playground's apps and the add-ons it brings, and the core services
    each of them configures: also when the item is on already, as a start that failed after the on file named it
    leaves them as they were. Not the rest of the platform a playground runs with, nor an add-on it needs: applying
    those is the Stack's."""
    if kind == "addon":
        addons, mine = [unit["id"]], [*unit["services"]]
    elif kind == "setup":
        addons = _known(project, unit["members"])
        mine = _addon_services(project, addons)
    else:
        addons = _known(project, unit["addons"])
        mine = [*unit["apps"], *_addon_services(project, addons), *unit["restarts"]]
    mine += [name for aid in addons for name in project["addons"][aid]["restarts"]]
    return sorted(set(mine) & set(behind))


def _switch_parts(project, to):
    """What a plan says of a switch of versions: its rows, in `versions` and `goes_back` (only the parts that have some), and `switch`,
    the values the start saves."""
    forward, back = switch_rows(project, to)
    return {**({"versions": forward} if forward else {}), **({"goes_back": back} if back else {}), "switch": to}


def _remote(place):
    """Whether a place is a remote one: anything but None and This machine, given by its id or as a place."""
    return bool(place) and (place if isinstance(place, str) else place["id"]) != MACHINE


def local_needs(project, unit):
    """The services of this machine's platform that a deploy to another platform starts for a playground, an add-on or a
    setup: those its apps, its seed and the services of what it brings depend on, and what they depend on in turn, but the
    gateway and the control plane of API Management, which the platform runs (Access Management with its database, for
    example). Sorted."""
    services = project["services"]
    core = {name for name, service in services.items() if service["owner"]["kind"] == "platform"}
    core -= {platforms.local_gateway(project)} | _control_plane(project)
    mine = [*unit.get("services", []), *_addon_services(project, brought(unit))]
    found, frontier = set(), {need for name in mine if name in services for need in services[name]["depends_on"]} & core
    while frontier:
        found |= frontier
        frontier = {need for name in frontier for need in services[name]["depends_on"]} & core - found
    return sorted(found)


def _remote_view(project, kind, uid, gateway):
    """The project as a deploy to a remote place sees it: a lab with the services of this machine's platform that the
    item needs (local_needs) and no other. The platform's other services and its groups are not in it, and no add-on or
    playground restarts a core service, so the plan cannot restart or remove one; what one sets on the gateway restarts the
    gateway of the platform instead, which runs. A playground brings the add-on of the gateway as one it includes. A
    remote place is for a playground, an add-on or a setup, and for the gateway."""
    lookup(project, "addon", gateway)
    if (kind, uid) == ("addon", gateway):
        needed = set()
    else:
        needed = set(local_needs(project, lookup(project, kind, uid)))
    local, service = platforms.local_gateway(project), (project["addons"][gateway].get("gateway") or {}).get("service")

    def quiet(units):
        return {unit_id: {**unit, "restarts": [service] if service and local in unit["restarts"] else []}
                for unit_id, unit in units.items()}
    # No group: what of the platform it starts is named service by service, never as a whole or as a group
    view = {**project, "groups": [], "addons": quiet(project["addons"]), "playgrounds": quiet(project["playgrounds"]),
            "services": {name: service_ for name, service_ in project["services"].items()
                         if service_["owner"]["kind"] != "platform" or name in needed}}
    if kind == "playground" and uid in view["playgrounds"]:
        playground = view["playgrounds"][uid]
        playground["addons"] = [*playground["addons"], gateway]
    return view


def _place_rows(place, itself, swap):
    """What the plan of a remote place says of it (PLACE_ROWS): where a playground deploys, and, when the gateway is
    made again for it (`swap`), what it serves from now on and what it stops serving."""
    here, serving = _row("environment", place["id"], place["name"]), place.get("serving")
    return {"deploys_to": [] if itself else [here], "serves": [here] if swap else [],
            "stops_serving": [_row("environment", serving["id"], serving["name"])] if swap and serving else []}


def _from(unit):
    """The row From of the plan of a start, when the item comes from a catalog that is not the first: the catalog's
    name, and under it its version (`hints`), never a commit. Nothing for the first catalog's items and this machine's, which
    say no origin. The words are the author's: cut, with the control characters out."""
    origin = unit.get("origin")
    if not (isinstance(origin, dict) and origin.get("name")):
        return {}
    version = origin.get("version")
    return {FROM: [{"kind": "catalog", "id": str(origin.get("id") or ""), "name": resolve.shown(origin["name"], 80)}],
            **({"hints": {FROM: f"Version {resolve.shown(version, 40)}"}} if version else {})}


def _on_their_platforms(project, state):
    """The project as a plan sees it when some items run on a platform besides this machine (`state["targets"]`): what such
    an item sets on the gateway restarts the gateway of that platform, not this machine's."""
    local, found = platforms.local_gateway(project), {}
    for where, platform in (state.get("targets") or {}).items():
        kind, uid = where.split(":", 1)
        addon = platforms.gateway_addon(project, platform)
        service = (addon.get("gateway") or {}).get("service") if addon else None
        unit = project[kind + "s"].get(uid)
        if unit and local in unit["restarts"]:
            found.setdefault(kind + "s", {})[uid] = {**unit, "restarts": [service] if service else []}
    return {**project, **{key: {**project[key], **units} for key, units in found.items()}} if found else project


def plan(project, state, up, kind, uid, do, fresh=False, behind=frozenset(), place=None, also=frozenset(), switching=False):
    """What starting or stopping an add-on, a setup or a playground changes, said before it runs (ROWS): rows of {kind,
    id, name} that start, stop (they run), are turned off (they are on and do not run), are kept or restart; the
    services its commands remove and start (`services`); the reason it cannot run (`blocked`); the on file it leads
    to (`playground`, `explicit`); the keys of the rows that are the item itself (`itself`): a setup's add-ons are the
    setup, but those that were turned on by themselves when it stops; and the word of its button (`label`). `state`
    is current(), `up` what runs (model.up()). `fresh`: a playground's start that sets it up, its seed included.
    `behind`: the services that run behind what is saved (model.behind). `also`: services a start makes again besides its own: the
    platform that runs behind the versions a playground's start just switched to (playgrounds.start). `switching`: the person chose to
    take a playground to the versions it was tested with (switch); it goes there anyway when the saved ones are older than it asks.

    `place`: where a start deploys. None, or This machine (MACHINE, by its id or as a place), is the plan above, as it
    always was, and so is every stop. A remote place, {id, name, gateway, here, serving}, makes the plan of a lab
    without the core (_remote_view): the platform never starts, no core service starts, restarts or goes, and the seed
    is not among what `up` names, because it runs apart (`fresh`, given per place, says whether it runs). `gateway` is
    the id of the add-on that runs the gateway of that kind of platform: the plan turns it on like an explicit add-on,
    so it outlives the playground's stop. It starts when it does not run. It restarts, with the rows `serves` and
    `stops_serving`, when it runs and is not this place's (`here` false); `serving` is {id, name} of the place it
    serves now, None when none is known. Its token is the job's to write, so the plan does not ask for it. A remote
    place always asks (PLACE_ROWS: `deploys_to` names it). A place is for a playground, and for its gateway (`kind`
    addon, `uid` the gateway), whose start is then the swap alone.

    It does only what it says: a start runs the item, what it brings and needs, restarts the core services it changes
    and makes again what of it runs behind what is saved; a stop removes what goes by name and restarts the core
    services it changed. What was stopped on purpose, or started outside the Lab UI, is left as it is."""
    remote = do == "start" and _remote(place)
    if remote:
        project = _remote_view(project, kind, uid, place["gateway"])
    else:
        project = _on_their_platforms(project, state)
    unit = lookup(project, kind, uid)
    users = _users(project, state, unit) if (kind, do) == ("setup", "stop") else {}
    playground, explicit = _after(project, state, kind, unit, do, set(users))
    if remote and (kind, uid) != ("addon", place["gateway"]):
        explicit = explicit | {place["gateway"]}
    was, before, effective = state["playground"], set(state["effective"]), _effective(project, playground, explicit)
    changed = {was, playground} - {None} if playground != was else set()   # the playgrounds that come or go
    leaving = uid if (kind, do) == ("playground", "stop") else was if was in changed else None
    going = sorted((before - effective) | _strays(project, state, up, kind, unit, do))
    leaves = bool(leaving) and _runs(project, up, project["playgrounds"][leaving])
    restarts = _restarts(project, up, before ^ effective, changed)
    gateway = project["addons"][place["gateway"]] if remote else None
    swap = remote and gateway["id"] in up["addons"] and not place.get("here")
    if do == "start":
        named = {row["id"] for row in restarts} | set(gateway["services"] if swap else ())
        later = dict.fromkeys([*_late(project, kind, unit, behind), *sorted(set(also) & set(project["services"]))])
        restarts = sorted(restarts + [_service_row(project, name) for name in later if name not in named], key=lambda row: row["id"])
        if swap:   # made again with this place's token, as one row
            restarts.insert(0, _row("addon", gateway["id"], gateway["name"]))
    # A playground on images older than it asks: its start goes to the versions it was tested with first (`switch`), and what runs on
    # those images is made again with it
    to = switch(project, unit, switching) if (kind, do) == ("playground", "start") and not remote else None
    if to:
        from ..docker import images   # images imports this module
        named = {row["id"] for row in restarts}
        restarts = sorted(restarts + [_service_row(project, name) for name in images.changing(project, to)
                                      if name in up["services"] and name not in named], key=lambda row: row["id"])
    fresh = fresh and (kind, do) == ("playground", "start")
    starts, start = _starts(project, up, kind, unit, playground, effective, restarts, fresh and not remote,
                            place["gateway"] if remote and kind != "playground" else None) \
        if do == "start" else ([], sorted(row["id"] for row in restarts))
    stops = (_apps_row(project, leaving) if leaves else []) + _addon_rows(project, set(going) & up["addons"])
    turns_off = (_apps_row(project, leaving) if not leaves else []) + _addon_rows(project, set(going) - up["addons"])
    itself, members = {key(kind, uid)}, _known(project, unit["members"]) if kind == "setup" else set()
    if kind == "playground":   # one of its apps, or its seed, named alone: start_rows
        itself |= {key("service", name) for name in unit["services"]}
    if do == "start":
        itself |= {key("addon", aid) for aid in members}
    elif members and members <= before:
        # On, its add-ons are the setup, but those turned on by themselves: they are said first
        alone = (set(state["explicit"]) & members) - set(unit["includes"])
        itself |= {key("addon", aid) for aid in members - alone}
    departing = {("addon", aid) for aid in going} | ({("playground", leaving)} if leaving else set())
    blocked = _blocked(project, state, kind, unit, do, place["gateway"] if remote else None, up, departing)
    if not blocked and (kind, do) == ("setup", "stop") and not (stops or turns_off):
        blocked = _kept_by(users) if users else f"{unit['name']} is off"
    return {"do": do, "kind": kind, "id": uid, "label": _label(project, state, up, kind, unit, do),
            "starts": starts, "stops": stops, "turns_off": turns_off,
            "keeps": _data_row(project, leaving) + _addon_rows(project, users),
            "restarts": restarts,
            "services": {"remove": _removed(project, up, leaving, going), "start": start},
            "blocked": blocked, "itself": itself, "fresh": fresh,
            "playground": playground, "explicit": sorted(explicit),
            **(_place_rows(place, (kind, uid) == ("addon", place["gateway"]), swap) if remote else {}),
            **(_from(unit) if do == "start" else {}),
            **(_switch_parts(project, to) if to else {})}


def asks(plan):
    """Whether an action says first what it does: when it does more than the item itself (it stops or turns off
    anything else or a playground, starts anything else, keeps an add-on that something else uses) or restarts core
    services."""
    def other(rows):
        return any(key(row["kind"], row["id"]) not in plan["itself"] for row in rows)
    return other(plan["stops"]) or other(plan["turns_off"]) or other(plan["starts"]) or bool(plan["restarts"]) \
        or any(row["kind"] == "playground" for row in plan["stops"] + plan["turns_off"]) \
        or any(row["kind"] == "addon" for row in plan["keeps"]) \
        or bool(plan.get("deploys_to")) or bool(plan.get("switch"))   # a remote place and a switch of versions always ask


def dialog(plan):
    """What a plan says before it runs, as the page draws it: its ROWS, the versions a start switches to, and PLACE_ROWS when it is for a
    remote place; when the
    item comes from a catalog, the row FROM and the `hints` under it. Those two never make a dialog open (asks)."""
    return {key: plan[key] for key in (*ROWS, "versions", "goes_back", *PLACE_ROWS, FROM, "hints") if key in plan}


def platform_starts(project, state, up):
    """What the Stack's Start, and its Update, run beyond what runs: the platform when none of it runs, else each of
    its groups with a service that does not run; the apps of the playground that is on; the add-ons that are on with a
    service that does not run. A one-shot is left out: it runs once."""
    services = project["services"]
    idle = {name for name in up_services(project, state["playground"], state["effective"])
            if not services[name]["oneshot"] and name not in up["services"]}
    if up["platform"]:
        rows = [_row("group", group["id"], group["name"]) for group in project["groups"]
                if set(group["services"]) & idle]
    else:
        rows = [_row("platform", "platform", project["name"])]
    pid = state["playground"]
    if pid in project["playgrounds"] and set(project["playgrounds"][pid]["services"]) & idle:
        rows += _apps_row(project, pid)
    return rows + _addon_rows(project, [aid for aid in state["effective"]
                                        if set(project["addons"][aid]["services"]) & idle])


def applies(project, on, states, behind):
    """What applying what was saved does: {names, restarts, starts, runs}. `names`: what its `up --no-deps` names;
    `restarts` and `starts` (rows): what it makes again, what it starts again; `runs`: whether anything of what is on
    runs, without which it does nothing, what was saved applying at the next start. `on`: the services `up` would
    run, `states` their states (model.service_state), `behind` the services that run behind what is saved
    (model.behind), None when that is not known: then everything that runs is named, and Compose makes again what
    changed.

    What runs behind what is saved is made again. What failed starts again, and so do the one-shots all that depends
    on (_once): Compose would run them first. Either waits for what it depends on among what is on: what neither runs,
    nor ended well, nor starts with it would fail again alone. And what needs a license, which was held back
    (held_back) for want of one and has no container: the license is usable now, and a start would have started it.
    Nothing else: what was stopped stays stopped, what has no container is not created. A one-shot is said with what
    needs it: made again with what is made again."""
    services, on = project["services"], list(on)

    def state(name):
        return states.get(name) or {"key": "absent", "running": False}
    running = [name for name in on if state(name)["running"]]
    if not running:
        return {"names": [], "restarts": [], "starts": [], "runs": False}
    recreate = set(running) if behind is None else {name for name in running if name in behind}
    failed = {name for name in on if state(name)["key"] == "issue" and not state(name)["running"]}
    unlocked = {name for name in on if (services.get(name) or {}).get("needs_license") and project["license_usable"]
                and state(name)["key"] == "absent"}
    ready = set(running) | {name for name in on if state(name)["key"] == "done"}
    alone = set()   # what would fail again alone: the one-shots only it needed are not run either
    while True:
        starting = (failed | unlocked | _once(project, set(on), recreate | failed | unlocked)) - alone
        left = {name for name in starting
                if set((services.get(name) or {}).get("depends_on", ())) & set(on) - ready - recreate - starting}
        if not left:
            break
        alone |= left
        failed -= left
    known, again = set(services), recreate | (starting & _once(project, set(on), recreate))
    return {"names": sorted(recreate | starting), "restarts": start_rows(project, sorted(again & known)),
            "starts": start_rows(project, sorted((starting - again) & known)), "runs": True}


def _on_the_file(project, plan):
    """The units a plan puts in the on file, and everything they bring: [(kind, id)] the project has, the item first."""
    units = _units(project)
    asked = [(plan["kind"], plan["id"])] if plan.get("kind") and plan.get("id") else []
    asked += [("playground", plan["playground"])] if plan.get("playground") else []
    asked += [("addon", aid) for aid in plan.get("explicit") or ()]
    found = []
    for start in asked:
        found += [node for node in resolve.closure(units, *start) if node[1] in units.get(node[0], {}) and node not in found]
    return found


def check(project, plan, accept):
    """Refuses a plan that cannot run as the page showed it: blocked, or stopping or turning off something that is
    neither the item itself nor accepted, by its key. A start that puts a unit of a catalog in the on file (this machine's
    own is no catalog's) is then handed to the catalogs' preflight: it judges the units again on the live model and
    verifies that their installed files are the ones that were installed, and refuses with its own line. What it raises is
    not changed here. Every caller passes the project, and what the page sent back (`accept`), even when it sent nothing:
    a form that left either out would skip the preflight without a word."""
    if plan["blocked"]:
        raise RuntimeError(plan["blocked"])
    for part, verb in (("stops", "stop"), ("turns_off", "turn off"), ("versions", "change"), ("goes_back", "take back")):
        for row in plan.get(part, []):
            named = key(row["kind"], row["id"])
            if named not in plan["itself"] and named not in accept:
                raise RuntimeError(f"Would also {verb} {row['name']}")
    if plan.get("do") != "start":
        return
    of_catalogs = [node for node in _on_the_file(project, plan)
                   if project[node[0] + "s"][node[1]].get("catalog") not in (None, paths.LOCAL)]
    if of_catalogs:
        # Looked up when asked: the service reads the project, which reads this module
        importlib.import_module("lab_ui.catalogs.service").preflight(project, of_catalogs)


# ---------------------------------------------------------------------------------------------------------------------
# Applying a plan
# ---------------------------------------------------------------------------------------------------------------------

def _called(file):
    """What a unit is called in a line about its file: the last part of its id, or the file itself when it is no unit's."""
    found = paths.unit_of(file)
    return found[1].rsplit("/", 1)[-1] if found else file


def _legacy_lines():
    """The lines of the on file that name a file of the old layout (LEGACY), plain or as the copy of an item that runs on
    another platform, as the file has them; none unless that layout is being moved. current() leaves them out, so a file
    written from it alone would drop what the move is about to translate (it reads the live file, not the backup)."""
    file = paths.ROOT / paths.ON_FILE
    if not file.is_file() or not _waiting():   # the cheap look first: nothing on, nothing to keep
        return []
    try:
        text = file.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    found = []
    for line in text.split("\n"):
        target = compose.TARGET_ITEM.fullmatch(line)
        item = None if target else compose.INCLUDE_ITEM.fullmatch(line)
        if target:
            named = compose._original(target.group(1))
        elif item:
            named = os.path.normpath(next(filter(None, item.groups())))
        else:
            continue
        if LEGACY.fullmatch(named):
            found.append(line.rstrip())
    return found


def write(playground, explicit, targets=None, names=None, blocked=None):
    """Writes the on file: the playground first (with include, the first file to set a key wins), then the
    add-ons turned on one by one. Nothing on: an empty file, the core runs alone; Compose needs the file all the
    same, COMPOSE_FILE names it. An item that runs on another platform is written as its copy for it
    (compose.write_target), made again from its file each time. `targets` says where items run, {unit file: platform kind,
    or None for this machine}, and what it leaves out keeps what the file says; `names`, {platform kind: {the service as the
    files say it: the service of that platform}}, how a copy is made, which the copy already says for an item that stays.

    `blocked`, {unit file: why it must not start} (blocked_files), is the gate: an entry that is not in the file yet is
    not added for a unit it names, and writing it raises. One already there stays, and removing entries never raises, so a
    stop works for whatever is on. While the old layout is being moved the lines that name its files stay after the others
    (_legacy_lines): the move translates the file as it finds it."""
    files = [paths.unit_file("playground", playground)] if playground else []
    files += [paths.unit_file("addon", aid) for aid in sorted(set(explicit))]
    if blocked:
        there = set(compose.includes(paths.ON_FILE))
        for file in files:
            if file in blocked and file not in there:
                raise RuntimeError(f"{_called(file)} is blocked: {blocked[file]}")
    target = paths.ROOT / paths.ON_FILE
    target.parent.mkdir(exist_ok=True)
    kept = compose.targets(paths.ON_FILE)
    where = {file: kind for file, kind in {**kept, **(targets or {})}.items() if file in files and kind}
    lines = []
    for file in files:
        kind = where.get(file)
        if not kind:
            lines.append(f"  - {file}\n")
            continue
        old = compose.target_names(compose.target_copy(file, kind))
        copy = compose.write_target(file, kind, (names or {}).get(kind) or old)
        lines.append(f'  - {{path: "{copy}", project_directory: "{os.path.dirname(file)}"}}\n')
    lines += [f"{line}\n" for line in _legacy_lines()]
    paths.write_atomic(target, "include:\n" + "".join(lines) if lines else "")


def prepare():
    """What this machine needs before Compose runs: .lab/ with an empty on file when nothing is on yet, and
    COMPOSE_FILE in .env, for a `docker compose` typed by hand to read what is on."""
    root = paths.ROOT
    (root / paths.LAB_DIR).mkdir(exist_ok=True)
    if not (root / paths.ON_FILE).exists():
        write(None, [])
    env = root / paths.ENV_FILE
    if env.is_file() and "COMPOSE_FILE" not in envfiles.read(paths.ENV_FILE):
        text = env.read_text(encoding="utf-8")
        paths.write_atomic(env, text + ("" if text.endswith("\n") or not text else "\n") + COMPOSE_FILE_LINES,
                           mode=0o600)


def prepare_catalogs():
    """What this machine needs once a catalog is added: the two settings that keep Compose from fetching an include by
    itself (compose.NO_REMOTE, which the Lab sets for every command it runs) in .env too, so that a `docker compose` typed by
    hand reads a catalog's files as the Lab does. Appended once, with the mode the file has (0600); a value the person set
    is theirs. Nothing without a .env, which make init makes from .env.example and would find made. A lab with no catalog
    never has them. Answers whether it wrote. It is asked after a catalog is in place, by a caller that must not fail for it: a
    .env that is not UTF-8 text is none this reads, and is left as it is (False), like one that is not there; a file that cannot
    be read or written raises OSError."""
    env = paths.ROOT / paths.ENV_FILE
    if not env.is_file():
        return False
    try:
        held = envfiles.read(paths.ENV_FILE)
        text = env.read_text(encoding="utf-8")
    except ValueError:   # the person's own bytes: nothing is added to what cannot be read whole
        return False
    missing = [f"{name}={value}" for name, value in compose.NO_REMOTE.items() if name not in held]
    if not missing:
        return False
    paths.write_atomic(env, text + ("" if text.endswith("\n") or not text else "\n") + NO_REMOTE_COMMENT
                       + "\n".join(missing) + "\n", mode=0o600)
    return True


def _folders(roots):
    """The folders a bind may be made in: each root that is a real folder, and the folder of each product under plugins/."""
    found = [root for root in roots if os.path.isdir(root) and not os.path.islink(root)]
    plugins = os.path.join(paths.ROOT, "plugins")
    try:
        found += [os.path.join(plugins, name) for name in sorted(os.listdir(plugins))
                  if stat.S_ISDIR(os.lstat(os.path.join(plugins, name)).st_mode)]
    except OSError:
        pass
    return found


def _make(root, parts):
    """Makes the folders `parts` below `root`, one at a time, as the user with mode 0755. A part that is a link, or a file,
    ends it, and nothing is replaced: what is made is below `root` and nowhere else. Answers the folders made."""
    here, made = root, []
    for part in parts:
        here = os.path.join(here, part)
        try:
            info = os.lstat(here)
        except FileNotFoundError:
            try:
                os.mkdir(here, 0o755)
                os.chmod(here, 0o755)   # whatever the umask took
            except (OSError, ValueError):
                return made
            made.append(here)
            continue
        except (OSError, ValueError):   # a name the system cannot take
            return made
        if not stat.S_ISDIR(info.st_mode):
            return made
    return made


def make_binds(project, plan):
    """Makes, before a start, the folders a bind of its units names that are not there: Docker would make them
    as root, and the person could not remove them. Of the resolved configuration of what the plan puts in the on file, a
    bind whose source is inside the folder of one of those units, or of a product under plugins/. A path outside them is
    never made, nor one under a link; an existing path is never replaced; a source with a file extension is a file the
    person provides, and one that says `create_host_path: false` is the author's word that Docker makes nothing. Nothing is
    said when Compose cannot read the files: the start says why. Answers the folders made."""
    if plan.get("do") != "start":
        return []
    nodes = [node for node in _on_the_file(project, plan) if node[0] != "setup"
             and project[node[0] + "s"][node[1]].get("catalog")]
    if not nodes:
        return []
    try:
        config = compose.config_json(compose.all_files([project[node[0] + "s"][node[1]]["file"] for node in nodes]))
    except (compose.ComposeError, ValueError):
        return []
    roots = _folders([os.path.normpath(paths.ROOT / paths.unit_folder(*node)) for node in nodes])
    made = []
    for service in (config.get("services") or {}).values():
        for volume in (service or {}).get("volumes") or ():
            if not isinstance(volume, dict) or volume.get("type") != "bind" or not isinstance(volume.get("source"), str):
                continue
            if ((volume.get("bind") or {}).get("create_host_path")) is False:
                continue
            source = os.path.normpath(volume["source"])
            for root in sorted(roots, key=len, reverse=True):
                parts = os.path.relpath(source, root).split(os.sep) if source.startswith(root + os.sep) else None
                if parts and not os.path.splitext(parts[-1])[1]:
                    made += _make(root, parts)
                    break
                if parts:
                    break
    return made


def commands(plan):
    """The Compose runs of a plan, each only when it has something to do (with no service, Compose would take them
    all): what goes is removed by name, Compose asked by project name, whatever the files include; then what starts
    and what restarts, by name, without what they depend on (`--no-deps`), which the plan names when it does not
    run. What runs is left as it is, what was stopped stays stopped, what was started outside the Lab UI stays."""
    remove, start = plan["services"]["remove"], plan["services"]["start"]
    found = [[*compose.by_name(), "rm", "--stop", "--force", *remove]] if remove else []
    return found + ([[*compose.COMPOSE, "up", "-d", "--no-deps", *start]] if start else [])


def _target_pairs(project, plan):
    """The item a plan's job is about, as [kind, id] pairs: a setup is the add-ons it starts or stops."""
    if plan["kind"] == "playground":
        return [["playground", plan["id"]]]
    unit = lookup(project, plan["kind"], plan["id"])
    return [["addon", aid] for aid in sorted(_targets(project, plan["kind"], unit, plan["do"]))]


def changes(project, plan):
    """What a plan's job starts and stops, as [kind, id] pairs: their rows say so meanwhile. A core service that
    restarts shows as starting: it stops on the way, which is not an issue. What is turned off is removed: it shows
    as stopping."""
    pairs = {part: [[row["kind"], row["id"]] for row in plan[part]] for part in ROWS}
    found = {"start": pairs["starts"] + pairs["restarts"], "stop": pairs["stops"] + pairs["turns_off"]}
    targets = _target_pairs(project, plan)
    # Started again while part of it runs, nothing of the item is among what starts: its row says so all the same
    if plan["do"] == "start" and not any(pair in found["start"] for pair in targets):
        found["start"] += targets
    return found


def _covers(kind, uid, name, owner):
    """Whether a [kind, id] pair of a job's changes covers a service: the platform, one of its groups, an add-on,
    a playground, or the service itself."""
    if kind == "service":
        return uid == name
    if kind == "platform":
        return owner["kind"] == "platform"
    return (owner["kind"], owner["id"]) == ("platform" if kind == "group" else kind, uid)


def changing(project, pairs):
    """The services a job's changes ({start, stop}: [kind, id] pairs) start or stop: {service: "start" | "stop"}. One
    both stopped and started restarts: it counts as a start. A service held back (held_back) is not started."""
    found = {}
    for do in ("stop", "start"):
        for kind, uid in (pairs or {}).get(do, []):
            found.update({name: do for name, service in project["services"].items()
                          if any(_covers(kind, uid, name, one) for one in owners_of(service))
                          and not (do == "start" and held_back(project, name))})
    return found


def awaits(project, pairs, named=()):
    """What a Compose command of a job waits for (jobs.awaited): the containers of what the job's changes (`pairs`)
    start among the services the command names, all of them when it names none (it runs the whole project), and of
    what they stop."""
    moves = changing(project, pairs)
    start = [name for name, do in moves.items() if do == "start" and (not named or name in named)]
    return awaited(project["services"], start=start, stop=[name for name, do in moves.items() if do == "stop"])


def waits(project, plan):
    """What each command of a plan waits for, in the order of commands(): the containers it removes, then those it
    starts or restarts."""
    services, remove, start = project["services"], plan["services"]["remove"], plan["services"]["start"]
    return ([awaited(services, stop=remove)] if remove else []) + ([awaited(services, start=start)] if start else [])


def apply(project, plan, title):
    """Runs the plan of an add-on, a setup or a playground already set up as a job, once check() let it through.
    When it fails the on file stays as written: the item is on, does not run, and can be started again or stopped."""

    def write_on_file(job):
        write(plan["playground"], plan["explicit"], blocked=blocked_files(project))
        make_binds(project, plan)
        for part, verb in (("starts", "Starts"), ("stops", "Stops"), ("turns_off", "Turns off"),
                           ("restarts", "Restarts")):
            if plan[part]:
                job.lines.append(f"{verb}: {', '.join(row['name'] for row in plan[part])}")

    return JOBS.start(title, [write_on_file, *commands(plan)], key=f"{plan['kind']}:{plan['id']}",
                      verb=plan["do"], changes=changes(project, plan), waits=waits(project, plan))
