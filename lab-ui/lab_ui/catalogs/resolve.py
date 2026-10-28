"""The pure helpers of the resolver: the closure of a unit, the order in which names are claimed and who
loses a clash, the audience rule, what a unit needs from other catalogs, the add-on that runs the gateway of a platform,
and whether a personal unit and a catalog unit are the same item. Nothing here reads a disk, runs a command or knows a
product: `project.build` hands in what `gather` read and gets answers back.

A unit is named by a key, `(kind, id)`, because an add-on and a playground of one catalog may share an id. The ids are
the Lab's own: bare for the first default catalog, `local/name`, `@ns/name`. A project file is a string relative to the
project (`.lab/catalogs/<ns>/addons/<name>/compose.yml`); `target_of` is the one reader of that shape."""

import re

# The geometry of a catalog: the only places a unit file may sit. `local` is the personal catalog, a namespace like
# the others in this shape; a path in any other place (a stage folder, a nested file, the project root) is no unit.
TARGET = re.compile(r"\.lab/catalogs/([a-z][a-z0-9-]{0,22}[a-z0-9])/"
                    r"(?:(addons|playgrounds)/([a-z0-9-]+)/compose\.yml|(setups)/([a-z0-9-]+)\.yml)")
KIND_OF = {"addons": "addon", "setups": "setup", "playgrounds": "playground"}
ORDER = {"setup": 0, "addon": 1, "playground": 2}   # what layers.write puts first after the item itself
AUDIENCE = {"public": 0, "private": 1}   # a unit may depend on a unit of its own rank or a lower one
FAILED = ("unreachable", "noaccess", "needs_git", "needs_docker", "wrong_catalog", "rewritten", "lab_new", "lab_old", "damaged",
          "missing_folder")   # the states of a catalog that are a failure, not progress


def shown(text, limit=200):
    """An author's text for a person: control characters out, white space collapsed, cut. A name goes into a reason line and
    from there into the state the page polls, so it never goes in as written."""
    text = " ".join("".join(c if c.isprintable() else " " for c in str(text)).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "\u2026"


def target_of(path):
    """(namespace, kind, name) of a project-relative unit file, or None for anything else: a file outside the catalogs
    (None itself, an include that leaves the project), a stage folder, a file nested deeper, a text that is no path."""
    found = TARGET.fullmatch(path) if isinstance(path, str) else None
    if not found:
        return None
    namespace, folder, name, setups, setup_name = found.groups()
    return namespace, KIND_OF[folder or setups], name or setup_name


def _key(item):
    """A unit key as a tuple, whether it came as a tuple or as the list of a JSON file."""
    return tuple(item) if isinstance(item, (list, tuple)) else item


def _direct(units, node):
    """The units that a unit includes itself. `units` is either {(kind, id): [(kind, id), ...]}, or the project's own
    grouping, {kind: {id: unit}}, whose units say what they include in `setups` and `includes` (a playground), `includes`
    (a setup) and `needs` (an add-on, already every add-on it brings)."""
    if node in units:
        return [_key(item) for item in units[node] or ()]
    kind, uid = node
    unit = (units.get(kind) or {}).get(uid) if isinstance(units.get(kind), dict) else None
    if not isinstance(unit, dict):
        return []
    return ([("setup", other) for other in unit.get("setups") or ()]
            + [("addon", other) for other in unit.get("includes") or () if kind != "addon"]
            + [("addon", other) for other in unit.get("needs") or () if kind == "addon"])


def closure(units, kind, uid):
    """[(kind, id)] of a unit and everything it includes, directly or not: the unit first, then its setups sorted, then its
    add-ons sorted, the order layers.write turns them on in. Safe on a cycle (a unit is listed once and the walk goes on
    from the units it has not met), and on an include the project does not know: it is listed, not followed."""
    start = (kind, uid)
    seen, todo = {start}, [start]
    while todo:
        for other in _direct(units, todo.pop()):
            if other not in seen:
                seen.add(other)
                todo.append(other)
    return [start] + sorted(seen - {start}, key=lambda node: (ORDER.get(node[0], len(ORDER)), node[1]))


def precedence_order(units, catalogs):
    """The units in the order they claim names: the namespaces of `catalogs` in the order given (the default
    catalog, local, then the others as they were added), each one's units sorted. `units` is {namespace: [key, ...]}. A
    namespace that `catalogs` does not list claims last, in alphabetical order: losing is the safe side."""
    known = list(catalogs)
    order = known + sorted(ns for ns in units if ns not in known)
    return [key for ns in order for key in sorted(_key(item) for item in units.get(ns) or ())]


def same_item(local, other):
    """Whether a personal unit and a catalog unit are the same item: the same `x-gravitee.name` and the same set of
    own services, nothing stored. It is what exists after a person shares a personal item and updates the catalog. A unit
    is never the twin of itself, and a unit with no name is nobody's twin. Units of two kinds are two items."""
    if not isinstance(local, dict) or not isinstance(other, dict):
        return False
    name = str(local.get("name") or "").strip()
    if not name or (local.get("id") is not None and local.get("id") == other.get("id")):
        return False
    if local.get("kind") and other.get("kind") and local["kind"] != other["kind"]:
        return False
    return name == str(other.get("name") or "").strip() \
        and set(local.get("services") or ()) == set(other.get("services") or ())


def collisions(order, owned, closures, blocked=()):
    """{key: "Same name as {owner}"} for the units that clash with an earlier one (T21). `order`: the keys in
    precedence_order. `owned[key]`: {name (the unit's display name), tier (default, local or other; unknown is other),
    services, containers, volumes (the names it defines), and x-gravitee.name through `name` for the twin test}.
    `closures[key]`: the keys of its closure. `blocked`: keys that something else blocked already: they claim nothing and
    are not judged again.

    The default catalog's units and the personal ones claim first and are never blocked here. A unit of another catalog
    is blocked by the first earlier unit that holds a name it defines, unless that unit is in its own closure (an add-on
    it includes defines the name, it is not a clash) or is its personal twin (same_item). Only the first clash is said.
    A blocked unit claims nothing, so adding a catalog never blocks an earlier unit, and never the default catalog.

    A service name and a container name are one namespace: both are names that the lab network answers to. So are the
    aliases of a service on a network, which owned_names counts with the containers. The platform's own services are a
    tier-default owner like any other: a caller that wants a clash with them said gives them a key and a record
    {name, tier: default, services, containers} and puts it first in `order`.

    The keys are the keys of `order`, whatever the callers made them: (kind, id) for the units of the project."""
    claimed = {"services": {}, "containers": {}, "volumes": {}}   # kind of name -> name -> (owner's key, its name, its record)
    answers = {"services": ("services", "containers"), "containers": ("services", "containers"), "volumes": ("volumes",)}
    hit, skip = {}, {_key(key) for key in blocked}
    for key in map(_key, order):
        mine = owned.get(key) or {}
        found = None
        if mine.get("tier", "other") == "other" and key not in skip:
            together = {_key(other) for other in closures.get(key) or ()}
            found = next((owner for group in claimed for name in mine.get(group) or ()
                          for owner in (claimed[other].get(name) for other in answers[group])
                          if owner and owner[0] not in together and not _twins(owner[2], mine)), None)
        if found:
            hit[key] = f"Same name as {shown(found[1], 80)}"
        elif key not in skip:
            display = mine.get("name") or (key[-1] if isinstance(key, tuple) else str(key))
            for group in claimed:
                for name in mine.get(group) or ():
                    claimed[group].setdefault(name, (key, display, mine))
    return hit


def _twins(owner, mine):
    """The owner is the personal twin of the unit that clashes with it: the same item, or the person's copy of it (`copy_of`)."""
    return owner.get("tier") == "local" and (copy_of(owner, mine) or same_item(owner, mine))


def copy_of(local, other):
    """Whether a personal unit is the person's copy of a catalog unit: its `copy_of` names that unit, of the same kind. A copy
    keeps the names of the original's services, which its neighbours and its seed call it by: it is a twin of it."""
    if not isinstance(local, dict) or not isinstance(other, dict):
        return False
    said = local.get("copy_of")
    return isinstance(said, str) and bool(said) and said == other.get("id") and local.get("kind") == other.get("kind")


def owned_names(unit_config, core_config, own, included=()):
    """What a unit defines that others can clash with: {services: its `own` ones, containers: their
    `container_name` and the aliases they take on a network, volumes: the named volumes it declares that neither the core
    nor an included unit declares}. `included`: the resolved configurations of the units it includes. From the resolved
    JSON, so a name written with a variable is the name it takes."""
    config, core = unit_config or {}, core_config or {}
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    kept = [name for name in own if name in services]
    containers = [services[name].get("container_name") for name in kept if isinstance(services[name], dict)]
    for name in kept:
        networks = services[name].get("networks") if isinstance(services[name], dict) else None
        for options in networks.values() if isinstance(networks, dict) else ():
            aliases = options.get("aliases") if isinstance(options, dict) else None
            containers += aliases if isinstance(aliases, list) else []
    declared = config.get("volumes") if isinstance(config.get("volumes"), dict) else {}
    theirs = set(core.get("volumes") or ()) | {name for other in included for name in (other or {}).get("volumes") or ()}
    return {"services": list(kept), "containers": list(dict.fromkeys(name for name in containers if isinstance(name, str) and name)),
            "volumes": sorted(name for name in declared if name not in theirs)}


def rank(audience):
    """0 for a public catalog, 1 for anything else: a folder, a personal unit and any doubt are private."""
    return AUDIENCE.get(audience, AUDIENCE["private"])


def audience_blocked(unit_rank, dependency_rank):
    """Whether a unit may not depend on a unit of this rank: a public unit depends on public units only (T24)."""
    return dependency_rank > unit_rank


def _parse(version):
    """A version as three numbers; (0, 0, 0) for a text that is none, so it is below any floor that asks for more."""
    found = re.fullmatch(r"\s*(\d+)\.(\d+)\.(\d+)\s*", str(version or ""))
    return tuple(int(part) for part in found.groups()) if found else (0, 0, 0)


def _state_key(state):
    return state.get("key") if isinstance(state, dict) else state


def catalog_needs(unit, catalogs, missing):
    """The `needs_catalogs` entries of a unit: [{id, name, url, min, have, reason}], the catalogs its include closure
    reaches that it cannot use yet, in the order of their namespaces.

    `unit`: {ns (its own catalog, "local" for a personal unit), includes (the project-relative include targets of its
    closure files, as compose.includes gives them)}. `catalogs`: registry.inputs(), {ns: {name, version, state, requires_catalogs
    (what that catalog declares of others: {ns: {name, url, min}}), ...}}. `missing`: the targets under .lab/catalogs/ that
    are not files.

    The reason decides the note and the button: `missing` (the catalog is not in the config), `downloading` or `failed`
    (it is, with no pin yet), `gone` (installed, and the file is no longer in it; the entry also carries `unit`, the name
    that went), `old` (installed, its version below the `min` its includer declares). A unit includes only catalogs its own
    declares (T23 blocks the rest), so a namespace that is not declared gives no entry: there is nothing to add. A personal
    unit declares nothing, so what it needs is described by the catalog's own entry alone."""
    unit, catalogs = unit if isinstance(unit, dict) else {}, catalogs if isinstance(catalogs, dict) else {}
    own = unit.get("ns")
    declared = ((catalogs.get(own) or {}).get("requires_catalogs") or {}) if own != "local" else {}
    absent = {}
    for target in missing or ():
        found = target_of(target) or _loose(target)
        if found:
            absent.setdefault(found[0], found[2])
    wanted = []
    for target in unit.get("includes") or ():
        found = target_of(target) or _loose(target)
        if found and found[0] not in (own, *wanted):
            wanted.append(found[0])
    needs = []
    for ns in sorted(wanted):
        want = declared.get(ns) if isinstance(declared.get(ns), dict) else None
        if want is None and own != "local":
            continue
        installed = catalogs.get(ns)
        entry = _need(ns, want or {}, installed if isinstance(installed, dict) else None, absent.get(ns))
        if entry:
            needs.append(entry)
    return needs


def _loose(path):
    """(namespace, kind, name) of a target under .lab/catalogs/<ns>/ that is not a unit file in the shape of the catalogs
    (a missing include can name a file Compose would not find either way), else None."""
    found = re.match(r"\.lab/catalogs/([a-z][a-z0-9-]{0,22}[a-z0-9])/(?:[a-z]+/)?([a-z0-9-]+)", path) \
        if isinstance(path, str) else None
    return (found.group(1), None, found.group(2)) if found else None


def _need(ns, want, installed, gone):
    base = {"id": ns, "name": want.get("name") or (installed or {}).get("name") or ns, "url": want.get("url"),
            "min": want.get("min"), "have": None}
    if installed is None:
        return dict(base, reason="missing")
    base["have"] = installed.get("version")
    state = _state_key(installed.get("state"))
    copy = installed["pin"] if "pin" in installed else installed.get("version")   # a copy exists when it has a pin, else a version
    if state == "downloading" or not copy:   # no copy yet: under way, or it failed
        failed = state in FAILED or bool(installed.get("error"))
        return dict(base, reason="failed" if failed and state != "downloading" else "downloading")
    if gone:
        return dict(base, reason="gone", unit=gone)
    if want.get("min") and _parse(installed.get("version")) < _parse(want.get("min")):
        return dict(base, reason="old")
    return None


def gateway_addon(units, kind, order=None):
    """The id of the add-on that runs the gateway of this kind of platform on this machine, or None (the only
    definition). `units`: {id: add-on}, each with `gateway` ({platform, service, token}, already cleared for a catalog that
    is neither listed, allowed nor local: N04), `blocked` (None or {reasons}) or `verdict`, `compat` and `needs_catalogs`.
    A candidate says it runs the gateway of this kind, is not blocked, is readable by this Lab and misses no catalog. The
    first in the precedence of 6.5 wins, so the answer never depends on an alphabet: `order` lists the ids that way, else
    the order of `units`."""
    for uid in (order if order is not None else list(units)):
        unit = units.get(uid)
        if not isinstance(unit, dict):
            continue
        gateway = unit.get("gateway")
        verdict = unit.get("verdict") if isinstance(unit.get("verdict"), dict) else {}
        if (isinstance(gateway, dict) and gateway.get("platform") == kind and not unit.get("blocked")
                and verdict.get("state") != "blocked" and unit.get("compat", "ok") == "ok"
                and not unit.get("needs_catalogs")):
            return uid
    return None
