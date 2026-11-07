"""`make check` and `catalog.py check`: what the engine must be before it is shared, and what a catalog must be before it
is published.

The engine profile (`problems`, `run`) is `make check`: the core file (images, restart policies, labels, comments, literal
secrets, loopback ports, the seeder key), the skills and their stubs, the `CLAUDE.md` next to each `AGENTS.md`. It loops over
no unit: what an installed catalog holds is that catalog's to check, and with none installed it prints All good. An item of this machine about to be shared (`sharing`) is the one exception, checked as what it will be.

The catalog profile (`catalog_problems`, `catalog_files`) is the second half of `catalog.py check`, after the rules of
`catalogs.audit` (structure, secrets, commits, workflows, Dockerfiles, guides, images): the per-item rules for each unit of
the catalog, its ports, what its units use that this Lab does not read (`understood`: a published catalog that holds one needs a newer Gravitee Lab), what its `catalog.yml` says
and whether a `lab` written in it, for older Gravitee Labs, is the true minimum, and the verdict of the trust policy on
each unit (a Blocked reason is an error). It reads, and never writes. Problems are (file, what is wrong), the file relative to the catalog, which is what a CI
annotation needs. The images a catalog builds are IM02 and IM04 of the audit, with the Lab's seeder as the one named anchor.

A folder is read where it is (`temporary_link`): the items need the folder as the Lab sees it, under `.lab/catalogs/`, so it is
linked into a copy of the Lab's project made in a temporary folder of this process, which is removed when the check ends. The
Lab itself, its `.lab/` and its `catalogs.json` are not touched. Compose reads what a stranger wrote with the scrubbed
environment (`scrubbed`)."""

import contextlib
import os
import posixpath
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

from ..docker import compose, images
from ..gravitee import frames, platforms
from . import layers
from .. import paths
from ..catalogs import defaults, gitx, manifest, registry, trust, understood
from ..catalogs import store as bare
from ..catalogs.registry import CatalogError
from ..catalogs.report import ERROR, WARNING, Problem, render
from ..catalogs.rules import secrets
from ..catalogs.tree import ListTree, read_inside
from ..paths import CORE_FILE, KINDS, NOUNS, ROOT
from .project import PAGES, REACH_LIMIT, TEXT_LIMIT, _read as read_project_file, apart, brought, conflict_id, forget, loaded

# What x-gravitee can say, of any item: a key outside it is read by nothing, and so does nothing (a typo, or a key
# the Lab UI does not know)
META = {"kind", "name", "description", "category", "conflicts", "requires", "tags", "source", "duration", "first_start",
        "accounts", "consoles", "terminal", "gateway", "tested", "host_access", "copy_of", "private_from", "private_id"}
KEY_OF = {"gateway": "addon", "tested": "playground", "terminal": "playground"}   # the keys that only one kind of item reads
PARTS = {"consoles": ("console", "as")}   # the keys of an entry of a list written as a mapping: a persona's console
# How long a guide takes or a first start takes, as the page says it: "35 min", "1 h", "1 h 30 min"
DURATION = re.compile(r"(?:\d+ h(?: \d{1,2} min)?|\d+ min)")
# What x-gravitee.requires can ask for: add-ons and setups are included instead
REQUIRES = {"plugins", "license", "host", "images", "lab"}
LOOPBACK = "127.0.0.1"   # the only address the core publishes a port on
STUB_FILE = ".claude/skills/{}/SKILL.md"    # where Claude Code looks for a skill
FRONTMATTER = re.compile(r"\A\s*---\n(.*?)\n---[ \t]*(?:\n|\Z)", re.S)   # the header of a skill file
AGENTS, CLAUDE = "AGENTS.md", "CLAUDE.md"   # what an agent reads, and the file that sends Claude Code to it
NOT_SCANNED = {"local", "plugins", "node_modules", "venv", "__pycache__"}   # folders without files of the project
FEATURE = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")   # a license feature, as the license model writes it
STEP = re.compile(r"^## (\d+)\.", re.M)   # a numbered section of a guide: one of its steps
# An action link of a guide, [text](action:id), a title after it too
ACTION = re.compile(r'\]\(action:([^)"]*?)\s*(?:"[^"]*")?\)')
ACTION_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")   # as split.js reads it
ACCOUNT = re.compile(r'\]\(account:([^)"]*?)\s*(?:"[^"]*")?\)')   # an account link of a guide, [text](account:username)
# A fenced block or a code span: it shows a syntax, it is no link
CODE = re.compile(r"^(```|~~~).*?^\1|`[^`\n]*`", re.M | re.S)
SEEDER_IMAGE = re.compile(r"[a-z0-9][a-z0-9.-]*(?::[0-9]{1,5})?(?:/[a-z0-9][a-z0-9._-]*)+")   # host/path, no tag
LOCK_FILE = understood.LOCK_FILE   # what the engine promises units, with the level each item appeared at
KEYWORD = re.compile(r"""(?<![\w.$-])["']?(include|extends|env_file|label_file)["']?[ \t]*:""")   # the words the text screen reads
RUN_BLOCK = re.compile(r"^(?:```|~~~)[ \t]*run\b", re.M)   # a guide's block that the terminal pane runs
# Where a depth mistake in an anchor shows: a unit's folder sits five folders under the project
SEEDER_FROM_A_UNIT, PLUGINS_FROM_A_UNIT = "../../../../../seeder", "../../../../../plugins/{}"
SEED_ENTRIES = understood.SEED_ENTRIES   # as many entries of a seed folder as the screen reads


class Unavailable(Exception):
    """The catalog cannot be checked here: it, or one that it includes from, is neither installed nor linked in this
    engine. The message says which, and what to do (`catalog.py check` exits 2)."""


class ComposeGone(Unavailable):
    """Docker Compose stopped answering while the units were read: what the loader said of every unit is the machine's and not
    the catalog's. `catalog.py check` says it did not check them."""


def _includes(kind, path, catalog=None, declared=()):
    """A file includes add-ons, a playground setups too, and they are there. It stands alone: it includes nothing of this
    machine. In a catalog, `catalog` is the namespace the file sits in and `declared`
    those its catalog.yml declares: what comes from a catalog that is neither is not shared into this one, and an
    include that is not there says where the unit it means is from here."""
    allowed, nouns = (("addon", "setup"), "add-ons and setups") if kind == "playground" else (("addon",), "add-ons")
    for included in compose.includes(path):
        unit = paths.unit_of(included) or ("", "")
        shown = _shown(included, catalog) if catalog else included
        if not (ROOT / included).is_file():
            yield f"includes {shown}, which does not exist" + (_depth(kind, included) if catalog else "")
        elif unit[0] not in allowed:
            yield f"includes {shown}: only {nouns} can be included"
        elif paths.personal(unit[1]):
            yield f"includes {shown}: what belongs to this machine is not shared with the project"
        elif catalog is not None and _namespace(included) not in (catalog, *declared):
            yield f"includes {shown}: what comes from a catalog is not shared into another"


def _depth(kind, included):
    """Where the unit that an include that is not there seems to mean is from the file that includes it, as a
    tail for the line: ': from here a unit of this catalog is ../../addons/<name>/compose.yml'. Nothing when the include
    names no unit."""
    found = re.search(r"(addons|playgrounds)/([a-z0-9-]+)/compose\.yml$|setups/([a-z0-9-]+)\.yml$", included)
    if not found:
        return ""
    name = found.group(3) or f"{found.group(1)}/{found.group(2)}/compose.yml"
    target = f"setups/{name}.yml" if found.group(3) else name
    return f": from here a unit of this catalog is {'../' if kind == 'setup' else '../../'}{target}"


def _namespace(path):
    """The catalog a project file sits in (.lab/catalogs/<ns>/...), None for anything else."""
    found = re.match(rf"{re.escape(paths.CATALOGS_DIR)}/([a-z][a-z0-9-]*)/", path or "")
    return found.group(1) if found else None


def _shown(path, catalog):
    """A project file as an author names it: relative to its catalog, or with the catalog first for another's."""
    base = f"{paths.CATALOGS_DIR}/"
    if not (isinstance(path, str) and path.startswith(base)):
        return path
    return path[len(base) + len(catalog) + 1:] if path.startswith(f"{base}{catalog}/") else path[len(base):]


def _metadata(kind, meta, categories):
    """What the x-gravitee block must say, and nothing it says is ignored. Every item has a category: the catalogs group
    by it."""
    for key in sorted(set(meta) - META):
        yield f"x-gravitee.{key} is not a key of x-gravitee: {', '.join(sorted(META))}"
    for key, owner in sorted(KEY_OF.items()):
        if key in meta and owner != kind:
            yield f"x-gravitee.{key} is a key of {NOUNS[owner]}s only"
    if meta.get("kind") != kind:
        yield f"x-gravitee.kind must be {kind}"
    if not meta.get("name"):
        yield "x-gravitee.name is missing"
    if kind != "setup" and not meta.get("description"):
        yield "x-gravitee.description is missing"
    category = meta.get("category")
    if not category:
        yield "x-gravitee.category is missing"
    elif categories and category not in categories:   # no categories: the core file cannot be read
        yield f"x-gravitee.category must be one of: {', '.join(categories)}"


def _listing(meta):
    """What a playground that is shared with the project says besides what any item says: its tags (the catalog is searched by
    them), how long its guide takes and how long its first start takes (a person plans a demo with them). They are what a person
    reads before starting one, so that none is shared without."""
    tags = meta.get("tags")
    if tags and not (isinstance(tags, list) and all(isinstance(tag, str) for tag in tags)):
        pass   # written otherwise than a list of labels: the project says how it must be (project._lists)
    elif not (tags and all(tag.strip() for tag in tags)):
        yield "x-gravitee.tags is missing: a list of short labels, the first one the most telling"
    elif len(set(tags)) != len(tags):
        yield "x-gravitee.tags lists a tag twice"
    for key, what in (("duration", "how long its guide takes"), ("first_start", "how long its first start takes")):
        value = meta.get(key)
        if not value:
            yield f"x-gravitee.{key} is missing: {what}, as 20 min or 1 h 30 min"
        elif not (isinstance(value, str) and DURATION.fullmatch(value)):
            yield f"x-gravitee.{key} is {value}: write it as 20 min, 1 h or 1 h 30 min"


def _readme(kind, uid):
    """What is shared says what it is and how to use it, in a README next to its file."""
    if not (ROOT / paths.unit_folder(kind, uid) / "README.md").is_file():
        yield "README.md is missing next to it: what it adds and how to use it"


def _labeled(config, names):
    """A service says what it is: the label io.gravitee.description, which the Lab UI shows with its name. The address of
    a console it declares is a web address: the Lab UI opens it in a tab."""
    for name in names:
        labels = config["services"][name].get("labels") or {}
        if not labels.get("io.gravitee.description"):
            yield f"{name}: the label io.gravitee.description is missing"
        for key, value in labels.items():
            if key.startswith("io.gravitee.console.") and key.endswith(".url") and not re.match(r"https?://", str(value), re.I):
                yield f"{name}: {key} must start with http:// or https://"


def _images(config, names):
    """An image variable has a default, so that a fresh clone runs without .env."""
    for name in names:
        for key, _, required in compose.references(config["services"][name].get("image") or ""):
            if required:
                yield f"{name}: ${{{key}}} needs a default: ${{{key}:-<value>}}"


def _restart(config, names):
    """What keeps running restarts unless stopped: a container stopped stays stopped when Docker restarts."""
    for name in names:
        if config["services"][name].get("restart") == "always":
            yield f"{name}: restart: always starts a stopped container again when Docker restarts: use unless-stopped"


def _meta(config):
    """The x-gravitee block of a compose file as a mapping: nothing when it is absent or written otherwise (a list, a text,
    a number), which the project says and nothing of it is read."""
    meta = config.get("x-gravitee") if isinstance(config, dict) else None
    return meta if isinstance(meta, dict) else {}


def _loopback(ports):
    """A port the core publishes is reachable from this machine only, so that nobody else on the network reaches a database
    or a console of the lab: it is written 127.0.0.1:<host port>:<container port>."""
    for port in ports:
        address = port.get("host_ip")
        if address == LOOPBACK:
            continue
        host, target = port.get("published") or "", port.get("target")
        where = "every interface" if address in (None, "", "0.0.0.0", "::") else address
        what = f"port {host}" if host else f"a port for {target}"
        yield f"publishes {what} on {where}: write {LOOPBACK}:{host}:{target}"


def _included(unit):
    """The files a unit includes, directly or through what it includes: the add-ons it brings, and its setups."""
    return [*(paths.unit_file("addon", aid) for aid in brought(unit)),
            *(paths.unit_file("setup", sid) for sid in unit.get("setups", []))]


def _roots(path, closure=(), products=()):
    """The roots the binds of the unit whose file is `path` are judged against (trust.build_roots), real: its folder, the
    folders of what it includes in the same catalog, the plugin folders and the seeder. What is read from a place beyond them
    is a service that uses this machine."""
    mine = _namespace(path)
    same = [ROOT / posixpath.dirname(file) for file in closure if _namespace(file) == mine]
    return trust.build_roots(ROOT, ROOT / posixpath.dirname(path), same, products, realpath=os.path.realpath)


def _described(variables):
    """The comment is what the settings form shows next to a variable."""
    for variable in variables:
        if not variable["comment"]:
            yield f"${{{variable['key']}}}: describe it with a comment on its line or heading its block"


def _brings(unit):
    """The add-ons a unit brings, an add-on itself among them."""
    return [unit["id"], *brought(unit)] if unit["kind"] == "addon" else brought(unit)


def _together(project, addon_ids):
    """The add-ons a file brings can run together."""
    for clash in layers.conflicts(project, layers.closure(project, addon_ids)):
        why = f": {clash['reason']}" if clash["reason"] else ""
        yield f"brings {clash['a']} and {clash['b']}, which cannot run together{why}"


def _conflicts(addon):
    """Conflicts name add-ons and say why. The list form is among the project's problems, and leaves none here."""
    known = set(paths.ids("addon"))
    for other, reason in addon["conflicts"].items():
        if conflict_id(other, addon.get("catalog"), known) is None:
            yield f"conflicts with {other}, which is not an add-on"
        elif not reason:
            yield f"conflicts with {other}: say why"


def _apart(uids, project):
    """What these add-ons do to the core, which a deploy that brings them does not do: (the add-on, what it does)."""
    for uid, changes, waits in apart(uids, project, strict=True):
        if changes:
            yield uid, f"which changes {', '.join(changes)} of the core, which a deploy does not restart"
        for name, needed in waits:
            yield uid, f"whose {name} depends_on {needed}, a service of the core, which a deploy does not start"


def _gateway(addon, project):
    """What an add-on says of the gateway it runs holds: a kind of platform that exists, a service of its own that
    publishes a port (the domains come from it), and a token that is a setting it requires. The Lab UI runs one gateway
    for each kind of platform, so one add-on says it. A deploy brings it alone, so it and what it includes leave the
    core as it is."""
    gateway = addon["gateway"]
    if not gateway:   # none, or written otherwise: the project says so
        return
    kind, service, token = gateway["platform"], gateway["service"], gateway["token"]
    if kind not in platforms.KINDS:
        yield f"x-gravitee.gateway.platform: {kind} is not a kind of platform: {', '.join(platforms.KINDS)}"
    if service not in addon["services"]:
        yield f"x-gravitee.gateway.service: {service} is not a service of this add-on"
    elif not gateway["domains"]:
        yield f"x-gravitee.gateway.service: {service} publishes no port"
    if not any(setting["key"] == token and setting["required"] for setting in addon["settings"]):
        yield f"x-gravitee.gateway.token: {token} must be a setting of this add-on without a default: ${{{token}}}"
    for uid, what in _apart(_brings(addon), project):
        yield f"x-gravitee.gateway: a deploy brings {uid}, {what}"
    # One add-on of a catalog says it. Another catalog's second provider is not an error: the Lab uses one, and
    # the author is told which
    same = [other for other in project["addons"].values()
            if (other["gateway"] or {}).get("platform") == kind and other.get("catalog") == addon.get("catalog")]
    first = min(same, key=lambda other: (other["personal"], other["file"]))
    if first["id"] != addon["id"]:
        yield f"x-gravitee.gateway: {first['id']} runs the gateway of {kind} already: one add-on does"
    used = ((project.get("platform") or {}).get("gateways") or {}).get(kind)
    other = project["addons"].get(used) if used else None
    if other and other["id"] != addon["id"] and other.get("catalog") != addon.get("catalog"):
        name = (project.get("catalogs") or {}).get(other.get("catalog"), {}).get("name") or other.get("catalog") or "another"
        yield f"x-gravitee.gateway: also provided by {name}, which is used"


def _setup(setup):
    if not setup["includes"]:
        yield "a setup includes at least one add-on"


def _license(requires):
    """The license features a unit requires are the model's ids, not the names of its packs; in the mapping form,
    each says what needs it."""
    wanted = requires.get("license") or []
    for feature in wanted:
        if not FEATURE.fullmatch(str(feature)):
            yield f"x-gravitee.requires.license: {feature} is not the id of a license feature"
        elif isinstance(wanted, dict) and not str(wanted[feature] or "").strip():
            yield f"x-gravitee.requires.license: {feature}: say what needs it"


def _tested(sets, project):
    """The versions a playground was tested with: each variable of a set is the version or the registry variable of an image."""
    components = images.components(project)
    known = {component["id"] for component in components} | {component["registry_key"] for component in components
                                                             if component.get("registry_key")}
    for variable in sorted({variable for tested in sets for variable in tested} - known):
        yield f"x-gravitee.tested: {variable} is not the version or the registry variable of an image"


def _level(requires):
    """The legacy level an item says it was written for, for older Gravitee Labs, is a whole number from 1, whatever the
    kind of the item: the Lab reads it from an add-on, a setup and a playground alike as a hint, and ignores one written otherwise
    at run time."""
    if isinstance(requires, dict) and "lab" in requires and not (type(requires["lab"]) is int and requires["lab"] >= 1):
        yield "x-gravitee.requires.lab must be a whole number from 1: older Gravitee Labs read it, and an item does without it"


def _requires(requires, project):
    """What a playground requires is what its checks and its start can act on. Their shapes are the project's: one
    written otherwise is among its problems, and left out here. Its `lab` is `_level`'s, said for every kind."""
    unknown = sorted(set(requires) - REQUIRES)
    if unknown:
        yield f"x-gravitee.requires: unknown {', '.join(unknown)} (add-ons and setups are included)"
    if project["core_loaded"]:   # without the core, no product is known
        for product in sorted(set(requires.get("plugins") or {}) - set(project["products"])):
            yield f"x-gravitee.requires.plugins: {product} is not a product that loads plugins"
    known = {component["id"] for component in images.components(project)}
    for key in sorted(set(requires.get("images") or {}) - known):
        yield f"x-gravitee.requires.images: {key} is not the version variable of an image"
    for spec in requires.get("host") or []:
        if not spec["check"].startswith(("http://", "https://")):
            yield f"x-gravitee.requires.host: the check of {spec['name']} must be an http or https URL"
    yield from _license(requires)


def _guide(folder, accounts=()):
    """The guide's steps are numbered 1, 2, 3 in order: its page shows one at a time, at the address of its number,
    and the playground's details lead to each by its place. Its action links name an id the page can draw as a
    button, and its account links a user of its `accounts`, whose sign-in the page draws. A guide that is no plain file,
    or is over 1 MB, is not read (`_not_read` says so)."""
    text = _read(folder / "guide.md") or ""
    numbers = [int(n) for n in STEP.findall(text)]
    if numbers != list(range(1, len(numbers) + 1)):
        yield f"guide.md: number its steps 1, 2, 3 in order, not {', '.join(map(str, numbers))}"
    # An action link whose id is none is shown as plain text: say so, before it is shared
    for found in dict.fromkeys(ACTION.findall(CODE.sub("", text))):
        if not ACTION_ID.fullmatch(found):
            yield f"guide.md: action:{found} is no action, an id is lowercase letters, digits and hyphens"
    users = {a.get("username") for a in accounts or () if isinstance(a, dict)}
    for found in dict.fromkeys(ACCOUNT.findall(CODE.sub("", text))):
        if found not in users:
            yield f"guide.md: account:{found} is no user of x-gravitee.accounts, so the page cannot show its sign-in"


def _frameable(service):
    """Whether the Lab UI's pages may frame a console of the platform: its command lets them (x-ui-frames) and drops
    X-Frame-Options, which would refuse every page but its own, or a setting of its own names them as the pages that may."""
    command = service.get("command") or ""
    return bool(service.get("lets_frame")) or (all(page in command for page in PAGES) and "X-Frame-Options" in command)


def _consoles(playground, project):
    """Its `consoles` are the UIs it shows next to its guide: services it runs (of the core, of itself or of an add-on
    it brings), each with a console, once. The platform's are images the lab does not write: their command must let the
    Lab UI frame them. The app of a playground and an add-on's allow it in their own headers, which only the page can
    see: it says when one refuses."""
    consoles = playground["consoles"]   # names and personas' entries: the project says one written otherwise (project._lists)
    entries = frames.entries(consoles)
    users = {account.get("username") for account in playground.get("accounts") or [] if isinstance(account, dict)}
    for name, user in dict.fromkeys(entries):
        if entries.count((name, user)) > 1:
            yield f"x-gravitee.consoles: {name} is listed twice" + (f" as {user}" if user else "")
        if user is not None and user not in users:
            yield f"x-gravitee.consoles: {name} is shown as {user}, which is no username of its accounts"
    mine = {("playground", playground["id"]), *(("addon", aid) for aid in playground["addons"])}
    for name in frames.names(consoles):
        service = project["services"].get(name)
        owner = service and (service["owner"]["kind"], service["owner"]["id"])
        if not project["core_loaded"]:   # without the core, no service is known: its problem is the one to fix
            continue
        if not service or not service["consoles"] or not (owner[0] == "platform" or owner in mine):
            yield f"x-gravitee.consoles: {name} is not a service with a console that this playground runs"
        elif owner[0] == "platform" and not _frameable(service):
            yield (f"x-gravitee.consoles: {name} is shown in a frame, so its command must name "
                   f"{' and '.join(PAGES)} and remove X-Frame-Options (x-ui-frames in docker-compose.yml), "
                   "or a setting of it must name them as the pages that may frame it")


def _terminal(playground, project):
    """Its `terminal` is the service whose container holds the shell beside its guide: one of its own, which runs for as long
    as the playground does."""
    name = playground["terminal"]
    if name is None or not project["core_loaded"]:
        return
    service = project["services"].get(name)
    mine = {("playground", playground["id"]), *(("addon", aid) for aid in playground["addons"])}
    if not service or (service["owner"]["kind"], service["owner"]["id"]) not in mine:
        yield "x-gravitee.terminal must name a service that this playground runs"
    elif service["role"] == "seed":
        yield "x-gravitee.terminal cannot be the seed, which ends once it ran"


def _playground(playground, project, inert=False):
    """What a playground says of its guide, its UIs, what it requires. `inert`: it cannot start, so none of its
    services is among the project's and what is asked of them cannot be."""
    if not playground["guide"]:
        yield "guide.md is missing next to it"
    if not inert:
        yield from _consoles(playground, project)
        yield from _terminal(playground, project)
    yield from _requires(playground["requires"], project)
    yield from _tested(playground["tested"], project)
    folder = ROOT / paths.unit_folder("playground", playground["id"])
    if not inert:
        seeds = [name for name in playground["services"] if project["services"][name]["role"] == "seed"]
        if (folder / "seed").is_dir() and len(seeds) != 1:
            yield "seed/ needs one service with the label io.gravitee.role: seed"
    yield from _guide(folder, playground.get("accounts"))


def _unit(kind, uid, project, raw, context=None):
    """What is wrong with one add-on, setup or playground, part of a catalog or about to be. `context` is the catalog's,
    when one is checked: {catalog (its namespace), declared (the namespaces its catalog.yml declares)}."""
    context = context or {}
    path = paths.unit_file(kind, uid)
    yield from _includes(kind, path, context.get("catalog"), context.get("declared") or ())
    if path not in raw:
        return   # Compose could not read it
    meta = _meta(raw[path])   # not a mapping: the project says so, and nothing of it is read
    unit = project[f"{kind}s"][uid]
    inert = bool(unit.get("blocked"))   # it cannot start: it has no services in the project, and what it writes is read here
    if inert and not unit.get("services"):
        unit = {**unit, "services": _written(unit, raw)}
    yield from _metadata(kind, meta, project["categories"])
    yield from _level(meta.get("requires"))
    if not inert:
        yield from _together(project, _brings(unit))
    yield from _images(raw[path], unit.get("services", []))
    if kind != "setup":
        yield from _readme(kind, uid)
    if kind == "playground":
        yield from _listing(meta)
    yield from _restart(raw[path], unit.get("services", []))
    yield from _labeled(raw[path], unit.get("services", []))
    if kind == "addon":
        yield from _conflicts(unit)
        yield from _license(unit["requires"])
        yield from _described(unit["settings"])
        if not inert:
            yield from _gateway(unit, project)
    elif kind == "setup":
        yield from _setup(unit)
    else:
        yield from _playground(unit, project, inert)


def _written(unit, raw):
    """The services a unit writes itself, as its file says them: a unit that cannot start has none in the project, but it is
    still checked for what it wrote. Not the core's, and not those of the units it includes."""
    core = set((raw.get(CORE_FILE) or {}).get("services") or {})
    theirs = {name for file in _included(unit) if file in raw for name in (raw[file].get("services") or {})}
    return [name for name in ((raw.get(unit["file"]) or {}).get("services") or {}) if name not in core | theirs]


def _alone(playground):
    """A playground runs on the core with what it includes and nothing else: an add-on it relies on without
    including it, itself or through a setup, is found here."""
    try:
        compose.resolved(compose.all_files([playground["file"]]))
    except compose.ComposeError as error:
        yield f"does not load on the core alone: {error}"


def _published(config, names):
    """The host ports a file publishes for these services, as written in it."""
    ports = (port for name in names for port in config["services"][name].get("ports") or [])
    return {str(port["published"]) for port in ports if port.get("published")}


def _publishers(project, raw, sharing=None, order=None):
    """What publishes ports: (file, kind, the add-ons it brings, its ports). An add-on or a playground publishes the
    ports of its own services and the ones it adds to core services. A port published twice is the problem of the later
    one: what is shared comes first, then the item about to be shared (`sharing`), then what belongs to this machine. In a
    catalog `order` is the namespaces in the order they claim names, and a unit comes after those of the catalogs before
    its own."""
    core = raw.get(CORE_FILE) or {}
    rank = {ns: number for number, ns in enumerate(order or ())}
    units = sorted([*project["addons"].values(), *project["playgrounds"].values()],
                   key=lambda unit: (rank.get(unit.get("catalog"), len(rank)) if order else unit["personal"],
                                     unit["file"] != sharing, unit["file"]))
    units = [unit for unit in units if unit["file"] in raw]
    # Read alone, a file shows the ports the add-ons it includes add to core services too: those are theirs
    added = {unit["file"]: _published(raw[unit["file"]], unit["restarts"]) for unit in units}
    yield CORE_FILE, "platform", [], _published(core, core.get("services") or {})
    for unit in units:
        theirs = set().union(*(added.get(paths.unit_file("addon", uid), set()) for uid in brought(unit)))
        names = unit["services"] or (_written(unit, raw) if unit.get("blocked") else [])
        yield unit["file"], unit["kind"], _brings(unit), _published(raw[unit["file"]], names) | (added[unit["file"]] - theirs)


def publishers():
    """The file that publishes each host port: {port: file}. What a new add-on is asked at creation, so that
    `make check` has nothing to say of its port afterwards."""
    project, inputs = loaded()
    found = {}
    for file, _, _, ports in _publishers(project, inputs["raw"]):
        found.update(dict.fromkeys(ports - set(found), file))
    return found


def _ports(project, raw, sharing=None, only=None, order=None):
    """No port is published twice by things that can run together: the core with anything, add-ons and playgrounds
    unless the add-ons they bring conflict. Two playgrounds never run together. `only`: the namespace of a catalog, whose
    units are the ones told (the later of two is the one with the problem)."""
    rows = list(_publishers(project, raw, sharing, order))
    belongs = {unit["file"]: unit.get("catalog") for unit in [*project["addons"].values(), *project["playgrounds"].values()]}
    for i, (file, kind, brings, ports) in enumerate(rows):
        for other, other_kind, other_brings, other_ports in rows[i + 1:]:
            common = ports & other_ports
            if not common or kind == other_kind == "playground":
                continue
            if only is not None and belongs.get(other) != only:
                continue
            if layers.conflicts(project, layers.closure(project, [*brings, *other_brings])):
                continue
            for port in sorted(common, key=lambda p: (len(p), p)):
                yield other, f"publishes port {port}, like {file}"


def _noticed(project, raw, found, only):
    """What the project noticed while loading: a core service without a group, files that do not load together, and
    Compose's own words on a file it cannot read, unless an include found wrong already says why. `only`: the files that
    are of interest (the engine's check leaves the problems of a file of this machine to the page that shows them)."""
    explained = {path for path, _ in found if path not in raw}
    for problem in project["problems"]:
        if problem["file"] not in only:
            continue
        if problem["file"] in raw:
            yield problem["file"], problem["message"]
        elif problem["file"] not in explained:
            yield problem["file"], f"Compose cannot read it: {problem['message']}"


def _skill(file):
    """What a skill file says of itself: {name, description} of its frontmatter, None without one. A value that goes on over
    several lines is read as one."""
    found = FRONTMATTER.match(file.read_text(encoding="utf-8").replace("\r\n", "\n"))
    if not found:
        return None
    fields, key = {}, None
    for line in found.group(1).split("\n"):
        start = re.match(r"([A-Za-z][\w-]*):\s*(.*)$", line)
        if start:
            key = start.group(1)
            fields[key] = start.group(2)
        elif key and line.strip():
            fields[key] += " " + line.strip()
    fields = {key: re.sub(r"^[>|][+-]?\s*", "", value).strip().strip("\"'") for key, value in fields.items()}
    return {key: " ".join(fields.get(key, "").split()) for key in ("name", "description")}


def _skills():
    """A skill is written once, in .agents/skills/<name>/, where most AI agents look for it, under the name of its
    folder. Claude Code looks elsewhere: a stub there sends it to the skill."""
    for skill in sorted(ROOT.glob(paths.SKILL_FILE.format("*"))):
        name = skill.parent.name
        path, stub = skill.relative_to(ROOT).as_posix(), STUB_FILE.format(name)
        fields = _skill(skill)
        if fields is None:
            yield path, f"begin it with a frontmatter between two --- lines, with name: {name} and a description"
        elif fields["name"] != name:
            yield path, f"name must be {name}, the name of its folder, not {fields['name'] or 'nothing'}"
        elif not fields["description"]:
            yield path, "description is missing: say what the skill does and when to use it"
        if not (ROOT / stub).is_file():
            yield path, f"{stub} is missing: the stub that sends Claude Code to this skill"


def _agent_files():
    """The AGENTS.md files of the project, where an AI agent finds how to work in a folder."""
    for folder, folders, names in os.walk(ROOT):
        folders[:] = sorted(name for name in folders if name not in NOT_SCANNED and not name.startswith("."))
        if AGENTS in names:
            yield Path(folder) / AGENTS


def _agents():
    """Claude Code reads CLAUDE.md and not AGENTS.md: next to each AGENTS.md, a CLAUDE.md that only imports it."""
    for agents in _agent_files():
        claude = agents.with_name(CLAUDE)
        path = claude.relative_to(ROOT).as_posix()
        if not claude.is_file():
            yield agents.relative_to(ROOT).as_posix(), f"{path} is missing: create it with the single line @{AGENTS}"
        elif claude.read_text(encoding="utf-8").strip() != f"@{AGENTS}":
            yield path, f"it must hold only the line @{AGENTS}, so that Claude Code reads {AGENTS}"


def _contract_keys(core):
    """What the core says of the contract it keeps with the catalogs: the seeder image, the one image a seed service
    may have (N01), a repository without a tag."""
    meta = core.get("x-gravitee") if isinstance(core.get("x-gravitee"), dict) else {}
    seeder = meta.get("seeder") if isinstance(meta.get("seeder"), dict) else {}
    if not (isinstance(seeder.get("image"), str) and SEEDER_IMAGE.fullmatch(seeder["image"])):
        yield ("x-gravitee.seeder.image is missing or is no repository: write the seeder's image without a tag, such as "
               "ghcr.io/owner/repo/seeder, the only image a seed service may have")


def _within(path, root):
    return bool(path) and bool(root) and (path == root or path.startswith(root.rstrip("/") + "/"))


def _geometry(unit, config, folder, declared, anchors):
    """Where the paths of a unit's own services lead: a bind source or a build context that is neither in the unit's
    folder nor an anchor (the seeder, a plugin folder, and for a bind the folder of a unit it includes) is outside, which the
    policy refuses with its own line; this one says the fix. It is the copied `../../seeder`, three folders short, that would
    otherwise be a Compose error or an empty folder that Docker makes as root. What a service says why it takes
    (`host_access`) is the policy's. Only a path that a depth mistake could have made is told: one in the project, or one
    named like an anchor."""
    unit_root = os.path.realpath(folder)
    for name in unit.get("services") or []:
        service = ((config or {}).get("services") or {}).get(name) or {}
        if name in declared:
            continue
        build = service.get("build")
        context = build if isinstance(build, str) else build.get("context") if isinstance(build, dict) else None
        found = [("build context", context, False)] if isinstance(context, str) else []
        found += [("bind", volume.get("source"), True) for volume in service.get("volumes") or []
                  if isinstance(volume, dict) and volume.get("type") == "bind" and isinstance(volume.get("source"), str)]
        for what, source, bind in found:
            if "://" in source or source.startswith("git@"):   # a source of git: the policy pins it (T18), no folder is meant
                continue
            real = os.path.realpath(source)
            if _within(real, unit_root) or _within(real, anchors["seeder"]) \
                    or any(_within(real, root) for root in anchors["plugins"] + (anchors["closure"] if bind else [])):
                continue
            fix = _fix(real, anchors)
            if fix:
                yield f"{name}: {what} {_relative(source, os.fspath(folder))} is outside the folder of this unit: {fix}"


def _fix(real, anchors):
    """What to write in place of a path that leaves the unit's folder, when it can be known: the anchor it was meant to
    reach, or, for any other place in the project, the rule."""
    base = os.path.basename(real)
    if base == "seeder":
        return f"the seeder is {SEEDER_FROM_A_UNIT} from the folder of a unit"
    if os.path.basename(os.path.dirname(real)) == "plugins":
        return f"the plugin folder of {base} is {PLUGINS_FROM_A_UNIT.format(base)} from the folder of a unit"
    if _within(real, anchors["project"]):
        return ("keep what it reads in its own folder, or reach a place of the engine that units may use: the seeder, "
                f"{SEEDER_FROM_A_UNIT}, or a plugin folder, {PLUGINS_FROM_A_UNIT.format('<product>')}")
    return None


def _relative(source, root):
    """A path as the file of the unit would write it: relative to the unit's folder."""
    try:
        return os.path.relpath(source, root)
    except ValueError:
        return source


def _anchors(products):
    """The places a unit may reach beyond its own folder, real: the seeder and each plugin folder; `project` is the
    whole of the project, where a depth mistake lands."""
    return {"project": os.path.realpath(ROOT), "seeder": os.path.realpath(ROOT / "seeder"),
            "plugins": [os.path.realpath(ROOT / "plugins" / name) for name in products]}


def _read(file):
    """The text of a file that is a plain file of at most 1 MB, else None: a link or a pipe is not followed or waited for."""
    return read_project_file(os.fspath(file))[0]


def _not_read(folder, shown):
    """What the Lab would not read where it reads (T19): a link or a pipe in place of the unit's folder, of its file, its
    `.env`, its guide or its seed folder, or of a file in it; a file over 1 MB; a seed folder of more than 5000 entries. The
    unit is refused for it with a line that does not say which, so the author is told here. Never follows a link."""
    base = Path(folder)
    if os.path.islink(base):
        yield f"{shown} is a link: the Lab does not follow links, so the unit is refused"
        return
    for rel in ("compose.yml", ".env", "guide.md"):
        try:
            info = os.lstat(base / rel)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            yield f"{shown}/{rel} is a link or a pipe, not a plain file: the Lab does not read it, so the unit is refused"
        elif info.st_size > TEXT_LIMIT:
            yield f"{shown}/{rel} is over 1 MB: the Lab does not read more than that of a file, so the unit is refused"
    seed, count = base / "seed", 0
    if os.path.islink(seed):
        yield f"{shown}/seed is a link: the Lab does not follow links, so the unit is refused"
        return
    for top, folders, names in os.walk(seed):
        folders[:] = sorted(name for name in folders if not name.startswith("."))
        for name in sorted(names) + list(folders):
            if name.startswith("."):
                continue
            count += 1
            where = os.path.join(top, name)
            rel = f"{shown}/seed/{os.path.relpath(where, seed)}"
            try:
                info = os.lstat(where)
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                yield f"{rel} is a link or a pipe: the Lab does not follow it, so the unit is refused"
            elif stat.S_ISREG(info.st_mode) and info.st_size > TEXT_LIMIT:
                yield f"{rel} is over 1 MB: the Lab does not read more than that of a file, so the unit is refused"
        if count > SEED_ENTRIES:
            yield f"{shown}/seed holds more than {SEED_ENTRIES} entries: the Lab does not read more, so the unit is refused"
            return


def _screen_notes(text, file):
    """What an author is told when the text screen refuses a unit file (T19). The screen reads words, not YAML: the
    words `include`, `extends`, `env_file` and `label_file` followed by a colon are the keys they spell wherever they are
    written (in a quote or a command too), and an `env_file` or a `label_file` is refused whatever file it names."""
    reasons = trust.prescreen(text, file) if text is not None else []
    if not reasons:
        return
    include, _ = trust.RULES["T19"]["lines"]
    spelled = set(KEYWORD.findall(trust.unescaped(text)))
    files = sorted(spelled & {"env_file", "label_file"})
    if files:
        yield (f"{' and '.join(files)} {'is' if len(files) == 1 else 'are'} refused, whatever file {'it names' if len(files) == 1 else 'they name'}: "
               "Compose reads that file into the environment, where the Lab cannot see it. Write the settings under "
               "environment: instead")
    if "include" in spelled and include in reasons:
        yield ("an include is a list under include: of plain file names, one on each line (- ../../addons/other/compose.yml), "
               "each the file of a unit of this catalog or of one it declares: no absolute path, address, ~ or variable, "
               "no list in braces and no other file")
    if "extends" in spelled:
        yield "extends of another file is refused: include the unit that has the service, or write the service out"
    yield ("the screen reads words, not YAML: include:, extends:, env_file: and label_file: are read as the keys they spell "
           "wherever they are written, in a quoted text or a command too: spell the word another way, or move the text "
           "out of the unit file")


def _unsafe_notes(file, reach, shown):
    """What an author is told when a unit is refused for what its closure reaches (T19, T25): the file that the Lab does not
    follow, and why. A unit that is itself the file (its own include list is not one the Lab can follow) or whose own .env is
    no plain file is told so; an included unit that is blocked is told by T25."""
    include = ("an include is a list under include: of plain file names, one on each line (- ../../addons/other/compose.yml), "
               "each the file of a unit of this catalog or of one it declares: no absolute path, address, ~ or variable, "
               "no list in braces and no other file")
    for found in (reach or {}).get("unsafe") or ():
        name = shown(found)
        mine = posixpath.dirname(found) == posixpath.dirname(file)
        if found == (reach or {}).get("bomb"):
            yield f"its includes reach more than {REACH_LIMIT} files in all, which is taken for a bomb: include fewer"
        elif found == file:
            yield include
        elif found.endswith("/.env"):
            yield ("its .env is a link or a pipe: make it a plain file" if mine else
                   f"includes a unit whose .env ({name}) is a link or a pipe: make it a plain file")
        else:
            yield (f"includes {name}, which the Lab does not follow: it reads an included file only when it is a unit written "
                   "with lists of plain file names, in a folder whose .env is a plain file")


# ---------------------------------------------------------------------------------------------------------------------
# The contract: is `lab` the true minimum?
# ---------------------------------------------------------------------------------------------------------------------

def _lock():
    """(contract, contract_min, {(kind, name): since}) of contract.lock, or None when the file is not there or not the
    engine's: one line for each item with the level it appeared at (`understood.lock`)."""
    found = understood.lock(ROOT)
    return (found.contract, found.contract_min, found.rows) if found else None


def _used(units, raw, inputs, rows):
    """{item: the level it appeared at} of the contract items that these units use (`understood.items`: the keys of their
    x-gravitee block, the labels of their services, the core services and networks they name, their category, the places of
    the engine they build or bind, the folders and files of their seed and the settings of their seed service), with the
    variables they refer to and the guide blocks they run. Only what the lock lists: the rest is no part of the contract."""
    found = set()
    for kind, uid, unit in units:
        folder = ROOT / paths.unit_folder(kind, uid) if kind == "playground" else None
        found |= understood.items(raw.get(unit["file"]) or {}, folder, kind, ROOT)
        found |= {("core.variable", name) for name in (inputs.get("references") or {}).get(unit["file"], ())}
        found |= {("core.image_variable", name) for name in (inputs.get("references") or {}).get(unit["file"], ())}
        if kind == "playground" and RUN_BLOCK.search((inputs.get("guides") or {}).get(uid) or ""):
            found.add(("guide.block", "run"))
    return {item: rows[item] for item in found if item in rows}


def _catalog_keys(meta, rows):
    """{item: the level it appeared at} of the keys of catalog.yml that the catalog uses, as the lock names them
    (`catalog.key x-gravitee-catalog.<key>`, and `.<key>.<part>` for a part of a mapping): a key whose value is empty is not
    used, but a mapping of parts (`renamed`), which an older Lab refuses as soon as it is there, even empty."""
    found = set()
    for key in manifest.KEYS:
        value = (meta or {}).get(key)
        if value is None or value in ("", [], False) or value == {} and key not in manifest.PARTS:
            continue
        parts = [part for part in manifest.PARTS.get(key, ()) if isinstance(value, dict) and value.get(part)]
        found.add(("catalog.key", f"{manifest.BLOCK}.{key}"))
        found |= {("catalog.key", f"{manifest.BLOCK}.{key}.{part}") for part in parts}
    return {item: rows[item] for item in found if item in rows}


def _not_understood(kind, uid, config, partial=False):
    """What a unit uses that this Lab does not read, each said where the other rules do not say it already: the keys of
    x-gravitee (`_metadata`), a playground's keys of `requires` (`_requires`) and the category (`_metadata`) have their own
    lines. A published catalog that holds one is not used by this Lab and is shown as needing a newer Gravitee Lab, so the
    author is told before anyone installs it."""
    folder = ROOT / paths.unit_folder(kind, uid) if kind == "playground" else None
    for item in understood.of_unit(config or {}, folder, kind, root=ROOT, partial=partial):
        what, name = item
        if what == "core.category" or what == "unit.key" and (name.count(".") == 1 or kind == "playground"):
            continue
        yield (f"{understood.name(item)} is not read by this Gravitee Lab: remove it, or check with the Gravitee Lab "
               "release that reads it (catalog.py upgrade --lab-ref latest)")


def needed_lab(units, raw, inputs, meta=None):
    """The `lab` that older Gravitee Labs need in catalog.yml for these units and the keys of its catalog.yml (`meta`, as
    `manifest.read_catalog` reads it): the highest level among the contract items they use (`_used`, `_catalog_keys`), 1 when
    they use none, None without the lock."""
    locked = _lock()
    if locked is None:
        return None
    return max({**_used(units, raw, inputs, locked[2]), **_catalog_keys(meta, locked[2])}.values(), default=1)


def folder_lab(folder):
    """The `lab` that older Gravitee Labs need in the catalog.yml of a folder (`needed_lab`), for `catalog.py` to write. The
    folder is read where it is, as `catalog.py check` reads it (`temporary_link`, the scrubbed environment). None when it cannot
    be said here: no lock, a catalog.yml without an id, no Compose, a catalog it includes from that is not here."""
    summary = folder_summary(folder)
    ns = summary.get("id")
    if _lock() is None or not ns or compose_missing():
        return None
    needs = sorted(summary.get("requires_catalogs") or {})
    try:
        needs_here(ns, needs, registry.inputs())
    except Unavailable:
        return None
    with temporary_link(folder, ns, needs), scrubbed():
        built, inputs = loaded()
        try:
            meta = manifest.read_catalog(f"{paths.CATALOGS_DIR}/{ns}/{manifest.FILE}")
        except CatalogError:
            meta = None   # the check says what is wrong with it
        return needed_lab(catalog_units(ns, built), inputs["raw"], inputs, meta)


WARNING_MARK = "Warning: "   # a line of `catalog_problems` that is a warning: the catalog works here, and something is left to do
NO_LEGACY = ("catalog.yml has no lines for older Gravitee Labs, which then cannot read it. Fix: catalog.py upgrade --write")


def _lab_lines(meta, units, raw, inputs):
    """The verdict on `lab` in catalog.yml, when it is there. This Lab reads a catalog by what it uses and needs no `lab`; older Gravitee
    Labs read this line instead, so a `lab` that is written is the true minimum: the highest level among the contract
    items the catalog uses, no lower (an older Lab would start it without what it needs) and no higher (one that could run it would
    refuse it), and never above what this engine provides. `catalog.py upgrade --write` writes it."""
    level = meta.get("lab")
    if level is None or meta.get("schema") is None:
        yield WARNING_MARK + NO_LEGACY   # this Lab reads it; one that reads levels refuses it with a line that misleads
    if level is None:
        return
    locked = _lock()
    if locked is None:
        yield f"{LOCK_FILE} is not there or cannot be read, so `lab` cannot be verified"
        return
    contract, _, rows = locked
    used = {**_used(units, raw, inputs, rows), **_catalog_keys(meta, rows)}
    needed = max(used.values(), default=1)
    fix = "older Gravitee Labs read it. Fix: run catalog.py upgrade --write, which writes it"
    if type(level) is int and level > contract:
        yield f"lab is {level}, more than any Gravitee Lab reads: {fix}"
    elif level != needed:
        at = [item for item, since in used.items() if since == needed]
        yield f"lab is {level} but must be {needed}" + (f", for {understood.named(at)}" if at else "") + f": {fix}"


# ---------------------------------------------------------------------------------------------------------------------
# A catalog
# ---------------------------------------------------------------------------------------------------------------------

UNREADABLE = ("damaged", "downloading")   # a git install in these states has no units in the project (registry.units_of)


def _present(row):
    """Whether a catalog row (registry.inputs) has files to read: an install with a copy that the Lab reads (a damaged one,
    or one that is still downloading, has no units in the project, so a check of it would read nothing), or a folder that is
    there."""
    return isinstance(row, dict) and ((row.get("kind") == "git" and row.get("version") is not None
                                       and row.get("state") not in UNREADABLE)
                                      or (row.get("kind") == "folder" and row.get("state") == "linked"))


def _unread(row):
    """Why an install that is here cannot be read, as what follows its name, and what to do; None when nothing is wrong."""
    state = row.get("state") if isinstance(row, dict) and row.get("kind") == "git" else None
    if state == "damaged":
        return "is damaged: repair it from the menu of its row in the Lab UI, or remove it and add it again"
    if state == "downloading":
        return "is still downloading: check it once it is installed (catalog.py provision waits for the downloads)"
    return None


def available(ns, catalogs=None):
    """Raises Unavailable, with what to do, when catalog `ns` cannot be checked here: it is not installed or linked, or a
    catalog it includes from is not. Returns the catalogs as the registry reads them."""
    catalogs = registry.inputs() if catalogs is None else catalogs
    if ns == paths.LOCAL:
        raise Unavailable(f"{ns} is not a catalog: it holds the items of this machine")
    if not _present(catalogs.get(ns)):
        unread = _unread(catalogs.get(ns))
        raise Unavailable(f"{ns} {unread}" if unread else
                          f"{ns} is neither installed nor linked here: link its folder first, with catalog.py link PATH")
    needs_here(ns, sorted(((catalogs.get(ns) or {}).get("requires_catalogs") or {})), catalogs)
    return catalogs


def needs_here(ns, needs, catalogs):
    """Raises Unavailable, with what to do, when a catalog that `ns` includes from (`needs`: the namespaces its catalog.yml
    declares) is neither installed nor linked here, or cannot be read. Of a folder that is read where it is, `needs` are what its
    catalog.yml says; of an added catalog, what its install says."""
    lacking = sorted(dep for dep in needs if not _present(catalogs.get(dep)))
    for dep in lacking:
        if _unread(catalogs.get(dep)):
            raise Unavailable(f"{ns} includes from {dep}, which {_unread(catalogs.get(dep))}")
    if lacking:
        raise Unavailable(f"{ns} includes from {', '.join(lacking)}, which {'is' if len(lacking) == 1 else 'are'} neither "
                          "installed nor linked here: check them together, after catalog.py link PATH or catalog.py add "
                          "ADDRESS --yes")


def _read_yml(ns, listed):
    """(catalog.yml as read, [what is wrong with it]) for catalog `ns`: the refusals of manifest.read_catalog (all the
    problems of its table, a file that holds more than its block, an include in it), the namespace it declares (the folder's
    name, not a reserved word unless the engine lists it), and the keys the Lab does not read."""
    file = f"{paths.CATALOGS_DIR}/{ns}/{manifest.FILE}"
    borrowed = [one["name"] for one in defaults.read() if one["id"] != ns]
    try:
        meta = manifest.read_catalog(file, borrowed=borrowed)
    except CatalogError as error:
        if compose.missing(str((error.detail or {}).get("output") or "")):
            raise ComposeGone(NO_COMPOSE) from None
        said = [f"{problem}" for problem in error.detail.get("problems") or ()] or [_unprefixed(error.message)]
        text = _read(ROOT / file) or ""
        if error.code == "catalog_invalid" and re.search(r"^[ \t]*include[ \t]*:", text, re.M):
            said.append("an include, a service or any key beside x-gravitee-catalog is refused: the file is data for the "
                        "Lab, not a Compose project")
        return None, said
    said = []
    if meta["id"] != ns:
        said.append(f"id is {meta['id']}, not {ns}, the name this catalog is installed or linked under: link it again")
    try:
        registry.check_namespace(meta["id"], listed=listed)
    except CatalogError as error:
        said.append(error.message)
    for key in meta["unknown"]:
        if key.startswith("requires.catalogs."):
            said.append(f"{key} is not a key of a catalog it requires: {', '.join(manifest.NEED_KEYS)}")
        elif key.startswith("requires."):
            said.append(f"{key} is not a key of requires: {', '.join(manifest.REQUIRES_KEYS)}")
        else:
            said.append(f"{key} is not a key of x-gravitee-catalog: {', '.join(manifest.KEYS)}")
    return meta, said


def _unprefixed(message):
    """A line of the catalog's refusals without the file it starts with: the finding says the file."""
    return message[len("catalog.yml: "):] if message.startswith("catalog.yml: ") else message


def _dependencies(meta, units, inputs, catalog):
    """The consistency of what a catalog includes from others: every namespace it includes from is declared in
    requires.catalogs (the units say it, `_includes`), and every declaration is used by a unit."""
    declared = set(((meta or {}).get("requires") or {}).get("catalogs") or {})
    used = {ns for _, _, unit in units for target in (inputs.get("includes") or {}).get(unit["file"], ())
            for ns in [_namespace(target)] if ns and ns != catalog}
    if catalog in declared:
        yield f"requires.catalogs.{catalog} is this catalog itself: remove it"
    for ns in sorted(declared - used - {catalog}):
        yield f"requires.catalogs.{ns} is declared and no unit includes from it: remove it"


def _canary(meta, units):
    """Each item that catalog.yml names as a canary is an item of the catalog."""
    have = {f"{kind}:{paths.split_ref(uid)[1]}" for kind, uid, _ in units}
    for item in (meta or {}).get("canary") or ():
        if item not in have:
            yield f"canary: {item} is not an item of this catalog"


def _history(ns):
    """A reader of what the files of catalog `ns` were: `read(path, also=None)` finds the newest commit that removed `path`, and
    answers (`path` just before it, `also` in the release before it, `also` at it), None for a file that is not there; None
    when no commit removed `path` or there is nothing to read it from. The release before is the greatest version among the
    release tags that lead to that commit (`store.release_tags`); with release tags but none before, there was nothing yet;
    with no release tag at all, the commit just before stands for it: what a Lab that follows the branch had. From the git of
    its folder when it is a repository of its own (a working copy linked here, the catalog's CI), else from the Lab's store
    of it (an install), never from the network."""
    folder = os.path.realpath(paths.ROOT / paths.CATALOGS_DIR / ns)
    store = registry.store_dir(ns)
    def git(*args):
        try:
            return gitx.run(list(args), timeout=20)
        except CatalogError:   # no usable git: the history is not read, and nothing is said of it
            return None

    def said(found):
        return found.out.decode("utf-8", "replace").strip() if found is not None and found.code == 0 else ""
    top, _, found = said(git("-C", folder, "rev-parse", "--show-toplevel", "--absolute-git-dir")).partition("\n")
    if found and os.path.realpath(top) == folder:   # its own repository, not one it lies in (the Lab's own clone)
        where, tip, git_dir, prefix = ["-C", folder], "HEAD", found, "refs/tags/"
    elif store.is_dir():
        where, tip, git_dir, prefix = ["--git-dir", str(store)], (registry.lock_of(ns).get("pin") or ""), str(store), bare.RELEASES
    else:
        return lambda path, also=None: None

    def show(at, path):
        shown = git(*where, "show", f"{at}:{path}") if at and path else None
        return shown.out.decode("utf-8", "replace") if shown is not None and shown.code == 0 else None

    def releases(commit):
        try:
            return bare.release_tags(git_dir, commit, prefix) if re.fullmatch(r"[0-9a-f]{40}", commit or "") else []
        except CatalogError:
            return []

    def read(path, also=None):
        commit = said(git(*where, "log", "-1", "--format=%H", "--no-renames", "--diff-filter=D", tip, "--", path))
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            return None
        parent = said(git(*where, "rev-parse", "--verify", "--quiet", f"{commit}^"))
        before = releases(parent)
        release = before[0][2] if before else None if releases(said(git(*where, "rev-parse", tip))) else parent
        return show(parent, path), show(release, also), show(commit, also)
    return read


def _in_catalog(kind, name):
    """The file of an item in its catalog, relative to the catalog."""
    return f"setups/{name}.yml" if kind == "setup" else f"{kind}s/{name}/compose.yml"


def _renamed_lines(meta, units, reads=None, history=None, outside=()):
    """What catalog.yml renames, against what the catalog holds: an old item is no item of it, and the item a chain of renames
    ends at is one. An old setting is read by no item any more (`reads`: {unit file: [settings]}), since the Lab renames it in
    every item's .env, and no setting of the map is one of `outside` (the core's and the shared ones, in the Lab's own .env,
    which the Lab never renames). Then what the history of the catalog (`history`, `_history`) says of each pair, across the
    commit that made it (the old file just before, the new one there): the new item was no item before it (the Lab keeps two
    items apart: the old one would be removed, not renamed), its volumes keep their names (a volume with another name is a
    new, empty one, and the person's data stays in the old one), and a setting the old item read that the new one does not is
    in the map (the person's value would be left behind). A rename into a name an earlier release had for an item since
    removed is a warning: who has that release has both. The shape of the map is manifest.py's."""
    renamed = (meta or {}).get("renamed") or {}
    have = {f"{kind}:{paths.split_ref(uid)[1]}" for kind, uid, _ in units}
    pairs = renamed.get("items") or {}
    for old in sorted(pairs):
        if old in have:
            yield f"renamed.items: {old} is an item of this catalog: what is renamed away cannot be there too"
    for new in sorted({manifest._chain_end(pairs, old) for old in pairs} - {None}):
        if new not in have:
            yield f"renamed.items: {new} is not an item of this catalog"
    settings = renamed.get("settings") or {}
    for name in sorted({*settings, *settings.values()} & set(outside)):
        yield (f"renamed.settings: {name} is a setting of the Lab's own .env (the core's, or shared by the catalog's items): "
               "the Lab renames only the settings of each item's .env")
    for old in sorted(settings):
        readers = sorted(paths.split_ref(uid)[1] for _, uid, unit in units if old in ((reads or {}).get(unit["file"]) or ()))
        if readers:
            yield (f"renamed.settings: {old} is still read by {', '.join(readers)}: the Lab renames it in the .env of every item "
                   f"of the catalog, so read {settings[old]} there")
    for old, new in sorted((renamed.get("items") or {}).items()):
        if history is None or new not in have:
            continue
        kind, _, away = old.partition(":")
        if history(_in_catalog(kind, new.partition(":")[2])) is not None:
            yield (f"{WARNING_MARK}renamed.items: {new} is a name an earlier release gave another item: who updates from that "
                   f"release has both, so {old} is removed there, not renamed")
        found = history(_in_catalog(kind, away), _in_catalog(kind, new.partition(":")[2]))
        if found is None:
            continue
        before, there, now = found
        if there is not None:
            yield (f"renamed.items: {new} was an item already when {old} was renamed into it: the Lab keeps the two apart, so "
                   f"{old} is removed, not renamed. Fix: rename it into a name that is new")
        if before is None or now is None:
            continue
        for volume in sorted(compose.top_keys(before, "volumes") - compose.top_keys(now, "volumes")):
            yield (f"renamed.items: {old} had the volume {volume}, which {new} does not: keep the name, or the people who have "
                   "it lose its data")
        dropped = {name for name, _, _ in compose.references(before)} - {name for name, _, _ in compose.references(now)}
        for name in sorted(dropped - set(settings) - set(outside)):
            yield (f"renamed.settings: {old} read {name}, which {new} no longer reads: map it to the name {new} reads, or the "
                   "value people set is left behind")


def catalog_units(ns, project):
    """[(kind, id, unit)] of the units of catalog `ns` in the project, by kind and id."""
    return [(kind, uid, unit) for kind in KINDS for uid, unit in sorted(project[f"{kind}s"].items())
            if unit.get("catalog") == ns]


def catalog_files(ns):
    """The files that `catalog_problems(ns)` is about, relative to the catalog: catalog.yml, then each unit's file."""
    project, _ = loaded()
    base = f"{paths.CATALOGS_DIR}/{ns}/"
    return [manifest.FILE, *(unit["file"][len(base):] for _, _, unit in catalog_units(ns, project))]


def catalog_problems(ns, project=None, inputs=None):
    """Every problem found in catalog `ns`, as (file, what is wrong), the file relative to the catalog: what its
    catalog.yml says; for each unit the rules of every item (`_unit`), where its paths lead, what the screen and the policy
    say of it (a Blocked reason is an error: "Blocked: ..."); then the ports its units publish, and whether its declarations of other catalogs and its `lab` are true. The images it builds are the audit's (IM01 to IM04, `audit_units`). It loads
    the project as the Lab does, with the catalog installed or linked in this engine, and never writes. Unavailable when it, or
    a catalog it includes from, is not here."""
    if project is None or inputs is None:
        project, inputs = loaded()
    available(ns, inputs["catalogs"])
    gone = f"Cannot be read: {compose.NO_COMPOSE}"
    if any(compose.missing(problem.get("message")) for problem in (project.get("catalog_problems") or {}).get(ns, ())) \
            or any(gone in ((unit.get("blocked") or {}).get("reasons") or ()) for _, _, unit in catalog_units(ns, project)):
        raise ComposeGone(NO_COMPOSE)
    raw, found = inputs["raw"], []
    products = inputs.get("products") or ()

    def shown(path):
        return _shown(path, ns)
    meta, said = _read_yml(ns, [one["id"] for one in defaults.read()])
    found += [(manifest.FILE, line) for line in said]
    units = catalog_units(ns, project)
    declared = sorted(((meta or {}).get("requires") or {}).get("catalogs") or {})
    context = {"catalog": ns, "declared": declared}
    stubs = {*(inputs.get("screened") or {}), *(inputs.get("unreadable") or {})}
    for kind, uid, unit in units:
        file, where = unit["file"], shown(unit["file"])
        here = []
        if unit.get("blocked"):
            here += [f"Blocked: {reason}" for reason in unit["blocked"]["reasons"]]
        here += _screen_notes(_read(ROOT / file), file)
        here += _unsafe_notes(file, (inputs.get("reach") or {}).get(file), shown)
        if kind != "setup":
            here += _not_read(ROOT / paths.unit_folder(kind, uid), shown(paths.unit_folder(kind, uid)))
        if file not in stubs:
            try:
                here += list(_unit(kind, uid, project, raw, context))
                here += list(_not_understood(kind, uid, raw.get(file),
                                             bool(((inputs.get("reach") or {}).get(file) or {}).get("missing"))))
            except RecursionError:   # a file nested deeper than anyone writes one: it is said, not left to stop the check
                here.append("is nested too deep to be checked")
            if kind != "setup":
                written = unit if unit.get("services") or not unit.get("blocked") else {**unit, "services": _written(unit, raw)}
                roots = _roots(file, _included(unit), products)
                said_why = _meta(raw.get(file)).get("host_access")
                here += _geometry(written, (inputs.get("configs") or {}).get(file), ROOT / paths.unit_folder(kind, uid),
                                  said_why if isinstance(said_why, dict) else {},
                                  {**_anchors(products), "closure": roots["closure"]})
        found += [(where, line) for line in here]
    for problem in (project.get("catalog_problems") or {}).get(ns, ()):
        found.append((problem.get("display") or shown(problem["file"]), problem["message"]))
    found += [(shown(file), line) for file, line in _ports(project, raw, None, ns, list(inputs["catalogs"]))]
    if meta is not None:
        found += [(manifest.FILE, line) for line in _dependencies(meta, units, inputs, ns)]
        found += [(manifest.FILE, line) for line in _canary(meta, units)]
        outside = {*(one["key"] for one in (inputs.get("variables") or {}).get(CORE_FILE, ())), *(meta.get("shared_settings") or ())}
        history = _history(ns) if (meta.get("renamed") or {}).get("items") else None
        found += [(manifest.FILE, line) for line in _renamed_lines(meta, units, inputs.get("references") or {}, history,
                                                                    outside)]
        found += [(manifest.FILE, line) for line in _lab_lines(meta, units, raw, inputs)]
    return found


# ---------------------------------------------------------------------------------------------------------------------
# A folder, where it is: Compose, the units for the audit, the temporary link
# ---------------------------------------------------------------------------------------------------------------------

NO_COMPOSE = "Docker Compose 5 not found"   # what a check that could not read the units says it did not do ("Not checked: ...")
# What the temporary copy of the Lab's project holds of the Lab itself: the files the project reads as text, and the places a unit
# names (the seeder, the plugin folder of each product) as empty folders, because a unit only names them. Not the `.env` (the
# settings of this machine: a fresh clone has none), not `.lab/` (made again below), and
# not the personal area, which a check of someone else's catalog has no use for. So nothing of the Lab is linked into it
COPIED = (CORE_FILE, paths.DEFAULT_FILE, LOCK_FILE)
ANCHORS = ("seeder", "plugins")   # the folders of the project that a unit names and that stand, in the copy, for the Lab's own
PRODUCT = re.compile(r"[a-z0-9][a-z0-9-]*")


def compose_missing():
    """Why Docker Compose cannot read the units of a catalog here, as the words that follow `Not checked:`, or None when it
    can. Compose is looked for in the real environment, in the places the Docker CLI looks (compose.plugin), so that a
    Mac with Docker Desktop finds it; it is then asked for its version in the scrubbed environment, which links to the plugin
    that was found, as it will be run. A Docker that has no Compose, no Docker at all and a Compose older than 5 say the same
    thing: the answer is not about the catalog. A daemon that does not run is not asked for."""
    if compose.plugin() is None:
        return NO_COMPOSE
    code, out, _ = compose.run([*compose.COMPOSE, "version", "--short"], timeout=20, environ=compose.scrubbed_environment())
    found = re.match(r"v?(\d+)\.", out.strip()) if code == 0 else None
    return None if found and int(found.group(1)) >= 5 else NO_COMPOSE


@contextlib.contextmanager
def scrubbed():
    """Compose, for the length of the block, reads the files of a catalog in the scrubbed environment: no token of the
    shell, no `ACTIONS_*`, no agent socket, a `HOME` that is an empty folder, and the Compose plugin of the real environment
    linked in. The project reads its units through `compose.run`, which takes its environment from `compose.environment`;
    that one is replaced for the block, and put back."""
    before = compose.environment
    compose.environment = compose.scrubbed_environment
    try:
        yield
    finally:
        compose.environment = before


@contextlib.contextmanager
def _root_is(new):
    """Every module of the Lab UI that holds the project folder (`ROOT`) holds `new` for the length of the block, and the folder it
    was after it: also a module that is first imported while the block runs, which would keep `new` for good. A command line runs
    one command, so this is the one place the project is moved."""
    old = paths.ROOT

    def holders(folder):
        return [module for name, module in list(sys.modules.items())
                if (name == "lab_ui" or name.startswith("lab_ui.")) and getattr(module, "ROOT", None) == folder]
    try:
        for module in holders(old):
            module.ROOT = new
        yield
    finally:
        for module in holders(new):
            module.ROOT = old


def folder_summary(folder):
    """What the `catalog.yml` of a folder says at a glance (registry.scan): {} when there is none or it cannot be read as a plain
    file, and never through a link."""
    data = read_inside(os.fspath(folder), manifest.FILE, registry.SUMMARY_LIMIT)
    return registry.scan(data) if data is not None else {}


class Linked(NamedTuple):
    """A folder read as the Lab reads a catalog: the project folder it is linked into (temporary), the Lab's own project folder,
    and the folder as it is."""
    root: Path
    lab: Path
    folder: Path


def _lay_out(project_folder, lab):
    """The files of the Lab that its project reads, in a folder of this process: the core, the list of the default catalogs and the
    contract as copies, the seeder and the plugin folder of each product as empty folders (the places a unit may name), and
    `.lab/catalogs`."""
    for name in COPIED:
        if (lab / name).is_file():
            shutil.copyfile(lab / name, project_folder / name)
    for name in ANCHORS:
        if (lab / name).is_dir():
            (project_folder / name).mkdir(exist_ok=True)
    try:
        products = sorted(name for name in os.listdir(lab / "plugins") if PRODUCT.fullmatch(name) and (lab / "plugins" / name).is_dir())
    except OSError:
        products = []
    for name in products:
        (project_folder / "plugins" / name).mkdir(parents=True)
    (project_folder / paths.CATALOGS_DIR).mkdir(parents=True)


@contextlib.contextmanager
def temporary_link(folder, ns, needs=(), audiences=None):
    """Reads the folder of a catalog as the Lab does, without linking it into the Lab. A copy of the Lab's project is made
    in a temporary folder of this process (`_lay_out`); in it `.lab/catalogs/<ns>` is a link to the folder, `catalogs.json` lists
    it as a catalog linked from there, and the catalogs it needs (`requires.catalogs`) are the Lab's own, installed or linked (a
    link to each), with the settings the Lab holds of them. Nothing of the personal area is in it, nor any other catalog. For
    the length of the block every module reads that folder as its project; the Lab's own folder, its `.lab/` and its
    `catalogs.json` are not touched, the folder is not written, and the temporary folder is removed at the end, links and all,
    without following one. `audiences`: {namespace: public or private}, what the lock says of a catalog for the policy (T24),
    measured by `--online`. A catalog.yml that says `host_access: true` is read with the allowance a person gives at Add: the
    rules judge what its items take of this machine by the reasons they give, as they do for a catalog the person allowed. Answers a Linked."""
    lab = Path(paths.ROOT)
    folder = Path(os.path.realpath(folder))   # a link to a relative path would be read from the temporary folder
    host = bool(folder_summary(folder).get("host_access"))   # the catalog says its items use this machine: the rules read it as allowed
    config, lock = registry.read_config() or {"catalogs": []}, registry.read_lock()
    entries = [dict(one) for one in config["catalogs"] if one["id"] in needs and one["id"] not in (ns, paths.LOCAL)]
    locks = {one["id"]: lock["catalogs"][one["id"]] for one in entries if one["id"] in lock["catalogs"]}
    holder = Path(tempfile.mkdtemp(prefix="gravitee-lab-check-")).resolve()
    try:
        _lay_out(holder, lab)
        with _root_is(holder):
            for one in entries:
                installed = lab / paths.CATALOGS_DIR / one["id"]
                if os.path.lexists(installed):
                    os.symlink(os.path.realpath(installed), holder / paths.CATALOGS_DIR / one["id"], target_is_directory=True)
            os.symlink(os.fspath(folder), holder / paths.CATALOGS_DIR / ns, target_is_directory=True)
            registry.write_config({"schema": 1, "catalogs": [*entries, {"id": ns, "kind": "folder", "path": os.fspath(folder),
                                                                       **({"host": True} if host else {})}]})
            registry.write_lock({"schema": 1, "catalogs": locks})
            for name, audience in (audiences or {}).items():
                registry.set_lock(name, audience=audience)
            forget()
            try:
                yield Linked(holder, lab, Path(folder))
            finally:
                forget()
    finally:
        shutil.rmtree(holder, ignore_errors=True)


def _spelled(service, folder):
    """A service as its file wrote it, for the rules of the images: Compose prints the build context of a service as a path from the
    filesystem root, and the rules compare what the author wrote (`../../../../../seeder`), so a local context is given back as
    the path from the folder of the unit. A remote context, and one that is not a path, stay."""
    build = service.get("build")
    context = build if isinstance(build, str) else build.get("context") if isinstance(build, dict) else None
    if not (isinstance(context, str) and os.path.isabs(context)):
        return service
    here = context[len(folder) + 1:] if context.startswith(folder + "/") else os.path.relpath(context, folder)
    return {**service, "build": here if isinstance(build, str) else {**build, "context": here}}


def audit_units(ns, project, inputs):
    """The units of catalog `ns` as the rules of the images read them (`audit.Options.units`): [{kind, id, file, config}]
    with `file` from the top of the catalog, `id` the name of the unit's folder and `config` the services the unit defines itself,
    as its file wrote them, with every local build context spelled relative to the unit (`_spelled`): what a unit includes is
    the other unit's to judge."""
    raw, base = inputs["raw"], f"{paths.CATALOGS_DIR}/{ns}/"
    rows = []
    for kind, uid, unit in catalog_units(ns, project):
        written = (raw.get(unit["file"]) or {}).get("services")
        if not isinstance(written, dict):
            continue
        names = unit.get("services") or (_written(unit, raw) if unit.get("blocked") else [])
        folder = os.path.join(os.fspath(ROOT), posixpath.dirname(unit["file"]))
        rows.append({"kind": kind, "id": paths.split_ref(uid)[1], "file": unit["file"][len(base):],
                     "config": {"services": {name: _spelled(written[name], folder) for name in names
                                             if isinstance(written.get(name), dict)}}})
    return rows


# ---------------------------------------------------------------------------------------------------------------------
# What a unit that is Blocked is told (T and N, with their fixes)
# ---------------------------------------------------------------------------------------------------------------------

def _pattern(line):
    """The reason line of a rule of the policy ("Same name as {owner}") as an expression that reads a reason of that kind."""
    parts = re.split(r"\{(\w+)\}", line)
    return re.compile("".join(re.escape(part) if number % 2 == 0 else f"(?P<{part}>.+?)" for number, part in enumerate(parts)) + r"\Z")


def _blocked(reason, codes, ns, file):
    """(code, the words of the problem) for a reason of a unit that cannot run: the code of the rule of the policy that says that
    line among those the unit's verdict names, the reason as the Lab says it, then `Fix:` from the table of fixes (trust.FIXES)
    with its blanks filled from the reason and the catalog. A reason that two of those rules say alike, with different fixes,
    has no code: it would send the author to the wrong one."""
    fixes = trust.FIXES
    found = []
    for code in codes:
        for line in (trust.RULES.get(code) or {}).get("lines") or ():
            match = _pattern(line).match(reason)
            if match:
                found.append((code, match.groupdict()))
                break
    if not found or len({fixes.get(code, "") for code, _ in found}) > 1:
        return "", reason
    code, fields = found[0]
    fields = {"NS": ns.upper().replace("-", "_"), "file": file, **fields}
    fix = re.sub(r"\{(\w+)\}", lambda blank: str(fields.get(blank.group(1), blank.group(0))), fixes.get(code, ""))
    return code, f"{reason}. Fix: {fix}" if fix and "{" not in fix else reason


def _hidden(text, folders):
    """A line of a finding without the places of this machine it names: a path below one of `folders` is given from there, and
    the folder itself is `.`. What a check says is read by others (the log of a CI run): it holds no path outside the catalog."""
    for folder in sorted({os.fspath(one).rstrip("/") for one in folders if one}, key=len, reverse=True):
        if folder and folder != "/":
            text = re.sub(re.escape(folder) + r"(?:/|(?![\w.-]))", lambda found: "" if found.group().endswith("/") else ".", text)
    return text


def as_problems(found, project, ns, hide=()):
    """The findings of `catalog_problems` as `Problem`s for the report of the command line: a unit that cannot run is the code of
    the rule of the Lab's policy that refuses it and the Lab's reason, with the fix; what the rules of the items say has no code
    (the standard has none for it yet) and no line. All of them are errors but a line that starts with WARNING_MARK. `hide`: the folders of this machine whose place in a
    line is taken out (the temporary project of a check, the folder that was linked into it)."""
    verdicts = {_shown(unit["file"], ns): (unit.get("verdict") or {}).get("codes") or ()
                for _, _, unit in catalog_units(ns, project)}
    out = []
    for where, line in found:
        code, text, severity = "", line, ERROR
        if line.startswith("Blocked: "):
            code, text = _blocked(line[len("Blocked: "):], verdicts.get(where, ()), ns, where)
        elif line.startswith(WARNING_MARK):
            text, severity = line[len(WARNING_MARK):], WARNING
        out.append(Problem(code, severity, where, 0, _hidden(text, hide)))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------------------------------------------------

def problems(sharing=None):
    """Every problem found by the engine profile, as (file, what is wrong): the core file, the skills, the agents' files. It
    loops over no unit. `sharing` is the file of an item of this machine to check as what it is once shared with a catalog:
    its own rules, its ports among the project's, and, for a playground, that it loads alone. The project is the one the
    Lab UI holds, read again when a file changed."""
    project, inputs = loaded()
    raw = inputs["raw"]
    core = raw.get(CORE_FILE) or {}
    services = core.get("services") or {}
    found = [(CORE_FILE, message) for message in _images(core, services)]
    found += [(CORE_FILE, message) for message in _restart(core, services)]
    found += [(CORE_FILE, message) for message in _labeled(core, services)]
    found += [(CORE_FILE, message) for message in _described(project["core_variables"])]
    found += [(CORE_FILE, one.message) for one in secrets.check(ListTree.from_files({CORE_FILE: _read(ROOT / CORE_FILE) or ""}))]
    found += [(CORE_FILE, message) for message in _loopback(
        port for service in services.values() for port in service.get("ports") or [])]
    found += [(CORE_FILE, message) for message in _contract_keys(core)]
    extra = []
    if sharing:
        kind, uid = paths.unit_of(sharing) or (None, None)
        if kind:
            found += [(sharing, message) for message in _unit(kind, uid, project, raw)]
            if kind == "playground":
                found += [(sharing, message) for message in _alone(project["playgrounds"][uid])]
            extra = list(_ports(project, raw, sharing))
    return [*_noticed(project, raw, found, {CORE_FILE, sharing}), *found, *extra, *_skills(), *_agents()]


def run():
    """Prints the files with their problems, then the verdict. Returns the exit code: 1 when something is wrong."""
    found = problems()
    print(render([Problem("", ERROR, where, 0, message) for where, message in found], checked=[CORE_FILE]), end="")
    return 1 if found else 0
