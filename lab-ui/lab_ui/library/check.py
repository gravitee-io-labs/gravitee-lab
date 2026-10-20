"""`make check` and `catalog.py check`: what the engine must be before it is shared, and what a catalog must be before it
is published.

The engine profile (`problems`, `run`) is `make check`: the core file (images, restart policies, labels, comments, literal
secrets, loopback ports, the seeder key), the skills and their stubs, the `CLAUDE.md` next to each `AGENTS.md`, Gemini's
settings. It loops over no unit: what an installed catalog holds is that catalog's to check, and with none installed it
prints All good. An item of this machine about to be shared (`sharing`) is the one exception, checked as what it will be.

The catalog profile (`catalog_problems`, `catalog_files`) is the second half of `catalog.py check`, after the rules of
`catalogs.audit` (structure, secrets, commits, workflows, Dockerfiles, guides, images): the per-item rules for each unit of
the catalog, the README tables that its folders keep when they keep any, the names it gives, its ports, what its `catalog.yml`
says and whether `lab` in it is the true minimum, and the verdict of the trust policy on each unit (a Blocked reason is an
error). It reads, and never writes. Problems are (file, what is wrong), the file relative to the catalog, which is what a CI
annotation needs. The images a catalog builds are IM02 and IM04 of the audit, with the Lab's seeder as the one named anchor.

A folder is read where it is (`temporary_link`): the items need the folder as the Lab sees it, under `.lab/catalogs/`, so it is
linked into a copy of the Lab's project made in a temporary folder of this process, which is removed when the check ends. The
Lab itself, its `.lab/` and its `catalogs.json` are not touched. Compose reads what a stranger wrote with the scrubbed
environment (`scrubbed`)."""

import contextlib
import json
import os
import posixpath
import re
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

from ..docker import compose, images
from ..gravitee import frames, platforms
from . import layers
from .. import paths
from ..catalogs import defaults, manifest, registry, trust
from ..catalogs.registry import CatalogError
from ..catalogs.report import ERROR, Problem
from ..catalogs.tree import read_inside
from ..paths import CORE_FILE, KINDS, NOUNS, ROOT
from .project import REACH_LIMIT, apart, brought, forget, loaded

# What x-gravitee can say, of any item: a key outside it is read by nothing, and so does nothing (a typo, or a key
# the Lab UI does not know)
META = {"kind", "name", "description", "category", "conflicts", "requires", "tags", "source", "duration", "first_start",
        "accounts", "consoles", "terminal", "gateway", "tested", "host_access"}
KEY_OF = {"gateway": "addon", "tested": "playground", "terminal": "playground"}   # the keys that only one kind of item reads
# How long a guide takes or a first start takes, as the page says it: "35 min", "1 h", "1 h 30 min"
DURATION = re.compile(r"(?:\d+ h(?: \d{1,2} min)?|\d+ min)")
# The keys that were read once and are not any more, and why: a file that still has one is told so, not just that the
# key is unknown
GONE = {"deploys_to": "compatibility with a platform is computed"}
# What x-gravitee.requires can ask for: add-ons and setups are included instead
REQUIRES = {"plugins", "license", "host", "images", "lab"}
LOOPBACK = "127.0.0.1"   # the only address a shared file publishes a port on
STUB_FILE = ".claude/skills/{}/SKILL.md"    # where Claude Code looks for a skill
STUB_BODY = "Read `{}` and follow it."   # all a stub says: the skill is written once
FRONTMATTER = re.compile(r"\A\s*---\n(.*?)\n---[ \t]*(?:\n|\Z)(.*)\Z", re.S)   # a skill file: its header, then its text
AGENTS, CLAUDE = "AGENTS.md", "CLAUDE.md"   # what an agent reads, and the file that sends Claude Code to it
NOT_SCANNED = {"local", "plugins", "node_modules", "venv", "__pycache__"}   # folders without files of the project
GEMINI_FILE = ".gemini/settings.json"   # where Gemini CLI is told to read AGENTS.md
# The listing of each kind (its README table, in the folder of the kind at the root of a catalog) and the row it holds
# for an item, written from the item's x-gravitee block. A catalog that keeps no table has no finding
LISTINGS = {"addon": ("addons/README.md", "| [{name}]({id}/) | {description} |"),
            "setup": ("setups/README.md", "| [{name}]({id}.yml) | {description} |"),
            "playground": ("playgrounds/README.md", "| [{name}]({id}/) | {description} | {duration} |")}
AREA = {"addon": "addons", "setup": "setups", "playground": "playgrounds"}   # the folder of each kind in a catalog
ROW = re.compile(r"^\|\s*\[[^\]]*\]\(([^)\s]*)\)\s*\|")   # a row of a table that starts with a link: where it leads
# The names of what is secret, in an environment, a label, an extension block or an account (an API key, not a public
# one); then the endings of a name that says where a secret is, what kind it is, how it is reached or whether it is on,
# which is none itself (AM_TOKEN_URL, KAFKA_TLS_TYPE, SECRET_MANAGER_HOST, TOKEN_TIMEOUT)
SECRET_NAME = re.compile(r"PASSWORD|PASSWD|PASSPHRASE|SECRET|API[_.-]?KEY|(?:^|[_.-])TOKEN$|(?<!PUBLIC)[_.-]KEY$", re.I)
NOT_SECRET = re.compile(r"(?:URL|URI|PATH|FILE|TYPE|ENDPOINT|ENABLED|HOST|PORT|METHOD|POLICY|REGEX|NAMESPACE|PROVIDER"
                        r"|TIMEOUT|TIMEOUTMS)$", re.I)
URI_USER = re.compile(r"://([^/?#\s@]*)@")   # what a URI holds before the @: user, or user:password
FEATURE = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")   # a license feature, as the license model writes it
STEP = re.compile(r"^## (\d+)\.", re.M)   # a numbered section of a guide: one of its steps
# An action link of a guide, [text](action:id), a title after it too
ACTION = re.compile(r'\]\(action:([^)"]*?)\s*(?:"[^"]*")?\)')
ACTION_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")   # as split.js reads it
# A fenced block or a code span: it shows a syntax, it is no link
CODE = re.compile(r"^(```|~~~).*?^\1|`[^`\n]*`", re.M | re.S)
# A `key: value` line of a YAML file at its top level: the key, then the value in quotes (2, 3) or without (4), then a comment
FLAT_LINE = re.compile(r"([A-Za-z_][\w-]*):[ \t]*(?:([\"'])(.*?)\2|([^#\"'\s][^#]*?))?[ \t]*(?:#.*)?")
SEEDER_IMAGE = re.compile(r"[a-z0-9][a-z0-9.-]*(?::[0-9]{1,5})?(?:/[a-z0-9][a-z0-9._-]*)+")   # host/path, no tag
LOCK_FILE = "contract.lock"   # what the engine promises units, with the level each item appeared at
LOCK_TOKEN = re.compile(r'"[^"]*"|\S+')
LOCK_LEVEL = re.compile(r"since=(\d+)")
KEYWORD = re.compile(r"""(?<![\w.$-])["']?(include|extends|env_file|label_file)["']?[ \t]*:""")   # the words the text screen reads
DEFAULTED = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-([^}$]+)\}")   # ${NAME:-default}, a default that is a plain text
CONSOLE_LABEL = re.compile(r"io\.gravitee\.console\.[a-z0-9-]+\.(name|url|legacy)")
RUN_BLOCK = re.compile(r"^(?:```|~~~)[ \t]*run\b", re.M)   # a guide's block that the terminal pane runs
# Where a depth mistake in an anchor shows: a unit's folder sits five folders under the project
SEEDER_FROM_A_UNIT, PLUGINS_FROM_A_UNIT = "../../../../../seeder", "../../../../../plugins/{}"
BIG_FILE = 1 << 20   # the largest file of a unit that the Lab reads (T19)
SEED_ENTRIES = 5000   # as many entries of a seed folder as the screen reads
SHOWN = 3   # the items a line spells out before it counts the rest
# How an item of the lock is named in a line: the keys, the labels and the anchors by their own name, the others by what they are
ITEM = {"core.service": "core service {}", "core.network": "network {}", "core.variable": "setting {}",
        "core.image_variable": "setting {}", "core.category": "category {}", "seed.folder": "seed folder {}",
        "seed.file": "seed file {}", "seed.setting": "seed setting {}", "guide.block": "{} block of a guide"}


class Unavailable(Exception):
    """The catalog cannot be checked here: it, or one that it includes from, is neither installed nor linked in this
    engine. The message says which, and what to do (`catalog.py check` exits 2)."""


class ComposeGone(Unavailable):
    """Docker Compose stopped answering while the units were read: what the loader said of every unit is the machine's and not
    the catalog's. `catalog.py check` says it did not check them."""


def _includes(kind, path, shared, catalog=None, declared=()):
    """A file includes add-ons, a playground setups too, and they are there. What is shared with the project stands
    alone: it includes nothing of this machine. In a catalog, `catalog` is the namespace the file sits in and `declared`
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
        elif shared and paths.personal(unit[1]):
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


def _metadata(kind, meta, categories, shared):
    """What the x-gravitee block must say, and nothing it says is ignored. An add-on, and whatever is shared with the
    project, has a category: the catalogs group by it."""
    for key in sorted(set(meta) - META):
        if key in GONE:
            yield f"x-gravitee.{key} is no longer a key: {GONE[key]}"
        else:
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
    if (kind == "addon" or shared) and not category:
        yield "x-gravitee.category is missing"
    elif category and categories and category not in categories:   # no categories: the core file cannot be read
        yield f"x-gravitee.category must be one of: {', '.join(categories)}"


def _listing(meta):
    """What a playground that is shared with the project says besides what any item says: its tags (the catalog is searched by
    them), how long its guide takes and how long its first start takes (a person plans a demo with them). They are what a person
    reads before starting one, so that none is shared without."""
    tags = meta.get("tags")
    if not (isinstance(tags, list) and tags and all(isinstance(tag, str) and tag.strip() for tag in tags)):
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


def _is_account(path):
    return len(path) == 2 and path[0] == "x-gravitee" and path[1].startswith("accounts[")


def _text(value):
    """A value as the text it is, None for what is none: a number is one, a password written 1234."""
    return str(value) if isinstance(value, (str, int, float)) and not isinstance(value, bool) else None


def _texts(node, path=()):
    """(the path of the mapping or list that holds it, its key, the text) of every text a compose file holds, nested
    mappings and lists included. A text in a list has no key."""
    if isinstance(node, dict):
        for name, value in node.items():
            if isinstance(value, (dict, list)):
                yield from _texts(value, (*path, str(name)))
            elif _text(value) is not None:
                yield path, str(name), _text(value)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            if isinstance(item, (dict, list)):
                yield from _texts(item, (*path[:-1], f"{path[-1]}[{i}]") if path else (f"[{i}]",))
            elif _text(item) is not None:
                yield path, None, _text(item)


def _names_a_secret(path, key):
    """Whether the key of an entry says that it is a secret: by its name, or `key` in an account. Of x-gravitee only the
    accounts hold secrets: the Lab UI reads the rest, and a license feature may be named after a secret manager."""
    account = _is_account(path)
    if path[:1] == ("x-gravitee",) and not account:
        return False
    return bool(SECRET_NAME.search(key) and not NOT_SECRET.search(key)) or (account and key.lower() == "key")


def _secrets(config):
    """Where a compose file writes a secret itself: (the path of the mapping, its key, the text, why) for an entry whose
    name says it is a secret and whose text names no setting ("literal"), and for a URI with a password in it that is
    no setting ("uri")."""
    for path, key, text in _texts(config):
        if key is not None and text and "${" not in text and _names_a_secret(path, key):
            yield path, key, text, "literal"
            continue
        for user in URI_USER.findall(text):
            colons = compose.outside(user, ":")
            password = user[colons[0] + 1:] if colons else ""
            if password and not password.startswith("${"):
                yield path, key, text, "uri"


def _where_in(path):
    """A path in a compose file as a message names it: a service by its name, then what of it; the rest as written."""
    if path[:1] == ("services",) and len(path) > 1:
        return " ".join([path[1], ".".join(path[2:])]).strip()
    return ".".join(path) or "top level"


def _literals(raw, path, included):
    """No secret is written in a shared file: a password, a secret, a key or a token is a setting, ${NAME:-default}
    (the default may be a public demo value), so that nobody edits a shared file to change it. What the files it includes
    write is theirs: read alone, a file shows it too."""
    theirs = {found for file in included if file in raw for found in _secrets(raw[file])}
    for where, key, _, why in dict.fromkeys(found for found in _secrets(raw.get(path) or {}) if found not in theirs):
        if why == "uri":
            what = f"{key} has" if key else "a URI has"
            yield (f"{_where_in(where)}: {what} a literal password in its URI: write ${{NAME:-default}} for the password "
                   "so that it is a setting (no password in a compose file)")
        else:
            yield (f"{_where_in(where)}: {key} is a literal: write ${{NAME:-default}} so that it is a setting "
                   "(no password in a compose file)")


def _loopback(ports):
    """A port a shared file publishes is reachable from this machine only, so that nobody else on the network reaches
    a database or a console of the lab: it is written 127.0.0.1:<host port>:<container port>."""
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


def _own_ports(raw, path, included):
    """The ports a file publishes itself, on its services and on the core's: read alone, a file shows the ports of the
    files it includes too, which are theirs."""
    def ports(file):
        return [(name, port) for name, service in (raw[file].get("services") or {}).items()
                for port in service.get("ports") or []]
    theirs = [found for file in included if file in raw for found in ports(file)]
    return [port for name, port in ports(path) if (name, port) not in theirs]


def _path_like(source):
    """Whether the source of a volume names a place on this machine, whichever type Compose gave it: read without
    interpolation, `${HOME}/.kube:/k` is typed a named volume, the text of its source untouched."""
    return isinstance(source, str) and (source.startswith(("/", ".", "~")) or "${" in source)


def _source(source, folders):
    """The place a bind's source names, as the text trust.elevations can compare: an absolute path, or (for a variable
    that has no default, which is outside the project whatever it holds) the text as written. Compose puts the folder of the
    file in front of a variable it did not interpolate (`<folder>/${NAME}`); `folders` are the real folders of the files
    read, which tell that prefix from a path the author wrote. A variable with a default is that default (a plain text)."""
    at = source.find("${")
    if at < 0:
        return source
    before, expr = source[:at], source[at:]
    prefix = None   # the folder Compose put in front, when that is what `before` is
    if before.endswith("/") and os.path.isabs(before) and os.path.realpath(before.rstrip("/") or "/") in folders:
        prefix = before.rstrip("/")
    unknown = expr if prefix is not None else source
    found = DEFAULTED.match(expr)
    if found is None:
        return unknown
    wanted = found.group(1) + expr[found.end():]
    if "$" in wanted:
        return unknown
    if os.path.isabs(wanted):
        return wanted
    if prefix is not None:
        return posixpath.join(prefix, wanted)
    return posixpath.join(before.rstrip("/") or "/", wanted) if before else wanted


def _mount(volume, folder, folders):
    """A volume as trust.elevations reads it: a mapping, a bind when its source names a place on this machine, whichever
    way the file wrote it (the short `source:target` form, or a source Compose could not type), with a relative source
    taken from the folder of the unit."""
    if isinstance(volume, str):
        source, _, rest = volume.partition(":")
        volume = {"type": "bind" if _path_like(source) else "volume", "source": source, "target": rest.split(":")[0]}
    if not isinstance(volume, dict):
        return {}
    volume = dict(volume)
    source = volume.get("source")
    if volume.get("type") in ("bind", "volume") and _path_like(source):
        source = _source(source, folders)
        if not os.path.isabs(source) and "${" not in source and not source.startswith("~"):
            source = posixpath.join(folder, source)
        volume.update(type="bind", source=source)
    return volume


def _roots(path, closure=(), products=()):
    """The roots the binds of the unit whose file is `path` are judged against (trust.build_roots), real: its folder, the
    folders of what it includes in the same catalog, the plugin folders and the seeder. What is read from a place beyond them
    is a service that uses this machine."""
    mine = _namespace(path)
    same = [ROOT / posixpath.dirname(file) for file in closure if _namespace(file) == mine]
    return trust.build_roots(ROOT, ROOT / posixpath.dirname(path), same, products, realpath=os.path.realpath)


def _elevations(service, roots, folders=(), members=None):
    """What a service takes of this machine beyond its own container, in words: trust.elevations, the one vocabulary
    (what a person reads in the Lab and what an author reads here are one text), over the service as written. Binds are
    compared by their real place and the project's folder by its real place, so that a link in the way cannot hide where a
    bind leads and a project that is itself reached through a link says nothing; a source that holds a variable with no
    default is outside the project, so that the declaration of what binds it is never unneeded."""
    mounts = [_mount(volume, roots["unit"] or "", set(folders)) for volume in (service or {}).get("volumes") or []]
    return trust.elevations({**(service or {}), "volumes": mounts}, roots=roots, realpath=os.path.realpath,
                            home=os.path.expanduser("~"), members=members)


def _host_access(meta, raw, path, included, own, products=()):
    """A service of a shared file that takes more of this machine than its container says so, with the reason: x-gravitee.host_access
    maps its name to why, so that whoever reviews the file, and whoever starts it, reads what it asks for before it runs. A service
    that says it and does not need it is told too: the declaration stays true. What the files it includes ask is theirs."""
    declared = meta.get("host_access") or {}
    if not (isinstance(declared, dict) and all(isinstance(name, str) and isinstance(why, str) and why.strip()
                                               for name, why in declared.items())):
        yield "x-gravitee.host_access must map a service to the reason it takes more of this machine"
        return
    services = (raw.get(path) or {}).get("services") or {}
    folders = {os.path.realpath(ROOT / posixpath.dirname(file)) for file in (path, *included)}
    members = set(own) | {name for file in included if file in raw for name in (raw[file].get("services") or {})}
    roots = _roots(path, included, products)
    theirs = set()
    for file in (file for file in included if file in raw):
        reach = _roots(file, (), products)
        theirs |= {(name, what) for name, service in (raw[file].get("services") or {}).items()
                   for what in _elevations(service, reach, folders, members)}
    for name in own:
        needed = [what for what in _elevations(services.get(name), roots, folders, members) if (name, what) not in theirs]
        if needed and name not in declared:
            yield (f"{name}: {', '.join(needed)}: say why in x-gravitee.host_access ({name}: <reason>), "
                   "so that it is read before it runs")
        elif name in declared and not needed:
            yield f"x-gravitee.host_access: {name} takes nothing of this machine beyond its container: remove it"
    for name in sorted(set(declared) - set(own)):
        yield f"x-gravitee.host_access: {name} is not a service of this file"


def _described(variables):
    """The comment is what the settings form shows next to a variable."""
    for variable in variables:
        if not variable["comment"]:
            yield f"${{{variable['key']}}}: describe it with a comment on its line or heading its block"


def _brings(unit):
    """The add-ons a unit brings, an add-on itself among them."""
    return [unit["id"], *brought(unit)] if unit["kind"] == "addon" else brought(unit)


def _settled(project):
    """The project with the keys of every add-on's `conflicts` settled as ids: a bare id names the add-on of the same
    catalog if there is one, else the default catalog's, so that two add-ons of a catalog that is not the first one are
    known to conflict whichever way their file says it. Nothing else of the project changes."""
    known = set(project["addons"])
    addons = {uid: {**addon, "conflicts": {(_conflict_id(other, addon.get("catalog"), known) or other): why
                                           for other, why in addon["conflicts"].items()}}
              for uid, addon in project["addons"].items()}
    return {**project, "addons": addons}


def _together(project, addon_ids):
    """The add-ons a file brings can run together."""
    project = _settled(project)
    for clash in layers.conflicts(project, layers.closure(project, addon_ids)):
        why = f": {clash['reason']}" if clash["reason"] else ""
        yield f"brings {clash['a']} and {clash['b']}, which cannot run together{why}"


def _conflict_id(other, catalog, known):
    """The id of the add-on that a `conflicts` key names: a bare id is the add-on of the same catalog if there is one,
    else the one of the first default catalog; `@ns/id` and `local/id` name one of that catalog, and so does
    `@<default>/id`. None when nothing has that id."""
    if not isinstance(other, str):
        return None
    candidates = [paths.canonical(other)]
    if paths.is_default(other) and catalog not in (None, paths.default_ns()):
        candidates.insert(0, f"{paths.LOCAL}/{other}" if catalog == paths.LOCAL else f"@{catalog}/{other}")
    return next((one for one in candidates if one in known), None)


def _conflicts(addon):
    """Conflicts name add-ons and say why. The list form is among the project's problems, and leaves none here."""
    known = set(paths.ids("addon"))
    for other, reason in addon["conflicts"].items():
        if _conflict_id(other, addon.get("catalog"), known) is None:
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
    """The level an item says it was written for is a whole number from 1, whatever the kind of the item: the Lab
    reads it from an add-on, a setup and a playground alike, and ignores one written otherwise at run time."""
    if isinstance(requires, dict) and "lab" in requires and not (type(requires["lab"]) is int and requires["lab"] >= 1):
        yield "x-gravitee.requires.lab must be a whole number from 1: the contract level this item was written for"


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


def _guide(folder):
    """The guide's steps are numbered 1, 2, 3 in order: its page shows one at a time, at the address of its number,
    and the playground's details lead to each by its place. Its action links name an id the page can draw as a
    button. A guide that is no plain file, or is over 1 MB, is not read (`_not_read` says so)."""
    text = _read(folder / "guide.md") or ""
    numbers = [int(n) for n in STEP.findall(text)]
    if numbers != list(range(1, len(numbers) + 1)):
        yield f"guide.md: number its steps 1, 2, 3 in order, not {', '.join(map(str, numbers))}"
    # An action link whose id is none is shown as plain text: say so, before it is shared
    for found in dict.fromkeys(ACTION.findall(CODE.sub("", text))):
        if not ACTION_ID.fullmatch(found):
            yield f"guide.md: action:{found} is no action, an id is lowercase letters, digits and hyphens"


def _flat(text):
    """The top-level `key: value` lines of a YAML file whose values are plain text: a quote around a value and a
    comment after it are dropped. All that an environment's file holds, and the Lab UI reads no YAML otherwise."""
    found = {}
    for line in text.splitlines():
        m = FLAT_LINE.fullmatch(line)
        if m:
            found[m.group(1)] = m.group(3) if m.group(2) else (m.group(4) or "")
    return found


def _environments(folder):
    """An environment the seed adds is a folder of seed/environments, which is its id: lowercase letters, digits and
    dashes, never `local` and never `default`, the platform's own, which the seed and the playground's service that creates
    the environments both refuse. Its environment.yaml gives the same id and a name, because that service reads them
    there, written once."""
    for found in sorted((folder / "seed" / "environments").glob("*")):
        if not found.is_dir() or found.name.startswith("."):
            continue
        where = found.relative_to(folder).as_posix()
        if not re.fullmatch(r"[a-z0-9-]+", found.name) or found.name in ("local", "default"):
            yield f"{where}: an environment is named by an id, lowercase letters, digits and dashes, and not `local` or `default`"
            continue
        file = found / "environment.yaml"
        if not file.is_file():
            yield f"{where}/environment.yaml is missing"
            continue
        said = _flat(_read(file) or "")
        if said.get("id") != found.name:
            yield f"{where}/environment.yaml: id must be {found.name}, the name of its folder"
        if not said.get("name"):
            yield f"{where}/environment.yaml: name is missing"


def _seed(folder):
    """The API definitions the seed imports, those of the platform's environment and of each one it adds, are JSON
    objects: the checks read the plugins they use. The environments it adds are named and described."""
    for file in paths.seed_apis(folder):
        name = file.relative_to(folder).as_posix()
        text = _read(file)
        if text is None:   # a link, a pipe or a file too large: `_not_read` says so
            continue
        try:
            definition = json.loads(text)
        except ValueError as error:
            yield f"{name} is not valid JSON: {error}"
            continue
        if not isinstance(definition, dict):
            yield f"{name} must hold a JSON object"
    yield from _environments(folder)


def _pull_policy(unit, project):
    """What an add-on or a playground builds says whether it is built here or pulled: the Lab UI leaves it to that,
    never forcing a build."""
    for name in unit.get("services", []):
        service = project["services"].get(name) or {}
        if service.get("built") and not service.get("pull_policy"):
            yield f"{name} builds its image: say how it comes with pull_policy (build or never, or always to pull it)"


def _frameable(service):
    """Whether the Lab UI's pages may frame a console of the platform: its command lets them (x-ui-frames) and drops
    X-Frame-Options, which would refuse every page but its own, or a setting of its own names them as the pages that may."""
    command = service.get("command") or ""
    return bool(service.get("lets_frame")) or (all(page in command for page in frames.PAGES) and "X-Frame-Options" in command)


def _consoles(playground, project):
    """Its `consoles` are the UIs it shows next to its guide: services it runs (of the core, of itself or of an add-on
    it brings), each with a console, once. The platform's are images the lab does not write: their command must let the
    Lab UI frame them. The app of a playground and an add-on's allow it in their own headers, which only the page can
    see: it says when one refuses."""
    consoles = playground["consoles"]
    if not (isinstance(consoles, list) and all(isinstance(name, str) for name in consoles)):
        yield "x-gravitee.consoles must be a list of services"
        return
    mine = {("playground", playground["id"]), *(("addon", aid) for aid in playground["addons"])}
    for name in dict.fromkeys(consoles):
        service = project["services"].get(name)
        owner = service and (service["owner"]["kind"], service["owner"]["id"])
        if consoles.count(name) > 1:
            yield f"x-gravitee.consoles: {name} is listed twice"
        if not project["core_loaded"]:   # without the core, no service is known: its problem is the one to fix
            continue
        if not service or not service["consoles"] or not (owner[0] == "platform" or owner in mine):
            yield f"x-gravitee.consoles: {name} is not a service with a console that this playground runs"
        elif owner[0] == "platform" and not _frameable(service):
            yield (f"x-gravitee.consoles: {name} is shown in a frame, so its command must name "
                   f"{' and '.join(frames.PAGES)} and remove X-Frame-Options (x-ui-frames in docker-compose.yml), "
                   "or a setting of it must name them as the pages that may frame it")


def _terminal(playground, project):
    """Its `terminal` is the service whose container holds the shell beside its guide: one of its own, which runs for as long
    as the playground does."""
    name = playground["terminal"]
    if name is None or not project["core_loaded"]:
        return
    service = project["services"].get(name) if isinstance(name, str) else None
    mine = {("playground", playground["id"]), *(("addon", aid) for aid in playground["addons"])}
    if not service or (service["owner"]["kind"], service["owner"]["id"]) not in mine:
        yield "x-gravitee.terminal must name a service that this playground runs"
    elif service["role"] == "seed":
        yield "x-gravitee.terminal cannot be the seed, which ends once it ran"


def _playground(playground, project, inert=False):
    """What a playground says of its guide, its UIs, what it requires, its seed. `inert`: it cannot start, so none of its
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
    yield from _guide(folder)
    yield from _seed(folder)


def _unit(kind, uid, project, raw, shared, context=None):
    """What is wrong with one add-on, setup or playground. `shared`: it is part of the project, or about to be. `context`
    is the catalog's, when one is checked: {catalog (its namespace), declared (the namespaces its catalog.yml declares),
    products (the plugin folders)}."""
    context = context or {}
    path = paths.unit_file(kind, uid)
    yield from _includes(kind, path, shared, context.get("catalog"), context.get("declared") or ())
    if path not in raw:
        return   # Compose could not read it
    meta = _meta(raw[path])   # not a mapping: the project says so, and nothing of it is read
    unit = project[f"{kind}s"][uid]
    inert = bool(unit.get("blocked"))   # it cannot start: it has no services in the project, and what it writes is read here
    if inert and not unit.get("services"):
        unit = {**unit, "services": _written(unit, raw)}
    yield from _metadata(kind, meta, project["categories"], shared)
    yield from _level(meta.get("requires"))
    if not inert:
        yield from _together(project, _brings(unit))
    yield from _images(raw[path], unit.get("services", []))
    if shared:
        if kind != "setup":
            yield from _readme(kind, uid)
            yield from _host_access(meta, raw, path, _included(unit), unit.get("services", []),
                                    context.get("products") or ())
        if kind == "playground":
            yield from _listing(meta)
        yield from _restart(raw[path], unit.get("services", []))
        yield from _labeled(raw[path], unit.get("services", []))
        yield from _loopback(_own_ports(raw, path, _included(unit)))
        yield from _literals(raw, path, _included(unit))
    if not inert and not context.get("audited"):   # a catalog that was audited has IM01 for it
        yield from _pull_policy(unit, project)
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
    project = _settled(project)
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


def _noticed(project, raw, found, only=None):
    """What the project noticed while loading: a core service without a group, files that do not load together, and
    Compose's own words on a file it cannot read, unless an include found wrong already says why. `only`: the files that
    are of interest (the engine's check leaves the problems of a file of this machine to the page that shows them)."""
    explained = {path for path, _ in found if path not in raw}
    for problem in project["problems"]:
        if only is not None and problem["file"] not in only:
            continue
        if problem["file"] in raw:
            yield problem["file"], problem["message"]
        elif problem["file"] not in explained:
            yield problem["file"], f"Compose cannot read it: {problem['message']}"


def _misnamed(folder):
    """The files of a catalog whose name is not one the Lab UI sees: [(file, what is wrong)], relative to the catalog.
    A unit is a folder with a compose.yml, or a setup file, named in lowercase letters, digits and dashes; `local` would read
    as the items of this machine. Dot files are not meant to be seen."""
    for kind in KINDS:
        base = Path(folder) / AREA[kind]
        try:
            names = sorted(entry for entry in os.listdir(base) if not entry.startswith("."))
        except OSError:
            continue
        for entry in names:
            if kind == "setup":
                stem = entry[:-4] if entry.endswith(".yml") else None
                if stem is None or not (base / entry).is_file():
                    continue
                path = f"{AREA[kind]}/{entry}"
            else:
                stem = entry
                if not (base / entry / "compose.yml").is_file():
                    continue
                path = f"{AREA[kind]}/{entry}/compose.yml"
            if stem == paths.LOCAL:
                yield path, f"a {NOUNS[kind]} cannot be named local: it would read as one of this machine"
            elif not re.fullmatch(paths.NAME, stem):
                yield path, "the Lab UI does not see it: name it with lowercase letters, digits and dashes"


def _skill(file):
    """What a skill file says of itself: ({name, description} of its frontmatter, the text after it); (None, its
    text) without a frontmatter. A value that goes on over several lines is read as one."""
    text = file.read_text(encoding="utf-8").replace("\r\n", "\n")
    found = FRONTMATTER.match(text)
    if not found:
        return None, text
    fields, key = {}, None
    for line in found.group(1).split("\n"):
        start = re.match(r"([A-Za-z][\w-]*):\s*(.*)$", line)
        if start:
            key = start.group(1)
            fields[key] = start.group(2)
        elif key and line.strip():
            fields[key] += " " + line.strip()
    fields = {key: re.sub(r"^[>|][+-]?\s*", "", value).strip().strip("\"'") for key, value in fields.items()}
    return {key: " ".join(fields.get(key, "").split()) for key in ("name", "description")}, found.group(2)


def _skills():
    """A skill is written once, in .agents/skills/<name>/, where most AI agents look for it, under the name of its
    folder. Claude Code looks elsewhere: a stub there, with the same name and description, sends it to the skill."""
    names = []
    for skill in sorted(ROOT.glob(paths.SKILL_FILE.format("*"))):
        name = skill.parent.name
        names.append(name)
        path, stub = skill.relative_to(ROOT).as_posix(), STUB_FILE.format(name)
        fields, _ = _skill(skill)
        if fields is None:
            yield path, f"begin it with a frontmatter between two --- lines, with name: {name} and a description"
        elif fields["name"] != name:
            yield path, f"name must be {name}, the name of its folder, not {fields['name'] or 'nothing'}"
        elif not fields["description"]:
            yield path, "description is missing: say what the skill does and when to use it"
        if not (ROOT / stub).is_file():
            yield path, f"{stub} is missing: the stub that sends Claude Code to this skill"
            continue
        said, body = _skill(ROOT / stub)
        if fields is not None and said != fields:
            yield path, f"{stub} must have the same name and description"
        if body.strip() != STUB_BODY.format(path):
            yield stub, f"after its frontmatter, write only: {STUB_BODY.format(path)}"
    for stub in sorted(ROOT.glob(STUB_FILE.format("*"))):
        if stub.parent.name not in names:
            skill = paths.SKILL_FILE.format(stub.parent.name)
            yield stub.relative_to(ROOT).as_posix(), f"sends Claude Code to {skill}, which does not exist: remove it"


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


def _gemini():
    """Gemini CLI reads GEMINI.md unless it is told to read AGENTS.md."""
    expected = {"context": {"fileName": [AGENTS]}}
    file = ROOT / GEMINI_FILE
    if not file.is_file():
        yield GEMINI_FILE, f"is missing: create it with {json.dumps(expected)}"
        return
    try:
        settings = json.loads(file.read_text(encoding="utf-8"))
    except ValueError as error:
        yield GEMINI_FILE, f"is not valid JSON: {error}"
        return
    names = (settings.get("context") or {}).get("fileName") if isinstance(settings, dict) else None
    if names != [AGENTS]:
        yield GEMINI_FILE, f"context.fileName must be {json.dumps([AGENTS])}, so that Gemini CLI reads {AGENTS}"


def _contract_keys(core):
    """What the core says of the contract it keeps with the catalogs: the seeder image, the one image a seed service
    may have (N01), a repository without a tag, and the two levels, whole numbers from 1 with the floor not above the top.
    A core without the levels reads as level 1, so only a level that is written wrong is told."""
    meta = core.get("x-gravitee") if isinstance(core.get("x-gravitee"), dict) else {}
    seeder = meta.get("seeder") if isinstance(meta.get("seeder"), dict) else {}
    if not (isinstance(seeder.get("image"), str) and SEEDER_IMAGE.fullmatch(seeder["image"])):
        yield ("x-gravitee.seeder.image is missing or is no repository: write the seeder's image without a tag, such as "
               "ghcr.io/owner/repo/seeder, the only image a seed service may have")
    levels = {key: meta[key] for key in ("contract", "contract_min") if key in meta}
    for key, value in levels.items():
        if type(value) is not int or value < 1:
            yield f"x-gravitee.{key} must be a whole number from 1"
    if all(type(value) is int for value in levels.values()) and len(levels) == 2 \
            and levels["contract_min"] > levels["contract"]:
        yield "x-gravitee.contract_min is above contract: the oldest level still read cannot pass the newest"


# ---------------------------------------------------------------------------------------------------------------------
# The listings: what the units of a catalog have in common
# ---------------------------------------------------------------------------------------------------------------------

def _row(kind, unit):
    """The row of an item in the listing of its kind, written from its x-gravitee block. Its link leads to the name of its
    folder in the catalog."""
    said = {key: " ".join(str(unit.get(key) or "").split()) for key in ("name", "description", "duration")}
    return LISTINGS[kind][1].format(id=paths.split_ref(unit["id"])[1], **said)


def _table(kind, units, folder):
    """The README of a kind, in the folder of a catalog, when the catalog keeps one: it lists each item once, in the row
    written from its x-gravitee block, and no item that does not exist: (the README, what is wrong). No table, no finding."""
    path, noun = LISTINGS[kind][0], NOUNS[kind]
    file = Path(folder) / path
    if not file.is_file():
        return
    listed = {}
    for line in (_read(file) or "").splitlines():
        found = ROW.match(line)
        if found:
            listed.setdefault(found.group(1), []).append(line.rstrip())
    wanted = set()
    for unit in units:
        row = _row(kind, unit)
        target = ROW.match(row).group(1)
        wanted.add(target)
        rows = listed.get(target, [])
        if not rows:
            yield path, f"does not list {unit['name']}: add the row {row}"
        elif len(rows) > 1:
            yield path, f"lists {unit['name']} {len(rows)} times: keep one row, {row}"
        elif rows[0] != row:
            yield path, f"the row of {unit['name']} must read {row}"
    for target in sorted(set(listed) - wanted):
        yield path, f"lists {target}, which is no {noun} of this catalog: remove its row"


def _naming(uid, unit, raw, catalog, listed):
    """The names a unit gives: every service of its own starts with the catalog's `<ns>-` or is named like the unit or
    starts with `<unit>-`; a container is `gio-<service>`; a volume it declares is `data-<name>`. The prefix is what keeps
    another catalog's unit from losing a name to this one at run time (T21 is the guard), so a listed catalog, whose units
    claim first and are never blocked for a name, owes none; the other two are the Lab's own conventions, owed by all."""
    path = unit["file"]
    config = raw.get(path) or {}
    services = config.get("services") or {}
    name = paths.split_ref(uid)[1]
    for service in unit.get("services") or []:
        if not listed and not (service == catalog or service.startswith(f"{catalog}-") or service == name
                               or service.startswith(f"{name}-")):
            yield f"{service}: name it {catalog}-{service}, or start it with the name of the unit, {name}-"
        named = (services.get(service) or {}).get("container_name")
        if isinstance(named, str) and named != f"gio-{service}":
            yield f"{service}: container_name is {named}: write gio-{service}"
    theirs = {volume for file in _included(unit) if file in raw for volume in (raw[file].get("volumes") or {})}
    declared = config.get("volumes") if isinstance(config.get("volumes"), dict) else {}
    for volume in sorted(set(declared) - theirs):
        if not volume.startswith("data-") and not (isinstance(declared[volume], dict) and declared[volume].get("external")):
            yield f"volume {volume}: name a volume it declares data-<what it keeps>"


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
        return ("keep what it reads in its own folder, or reach an anchor of the contract: the seeder, "
                f"{SEEDER_FROM_A_UNIT}, or a plugin folder, {PLUGINS_FROM_A_UNIT.format('<product>')}")
    return None


def _relative(source, root):
    """A path as the file of the unit would write it: relative to the unit's folder."""
    try:
        return os.path.relpath(source, root)
    except ValueError:
        return source


def _anchored(products):
    """The plugin folders a unit may reach: the ones the project has and the ones the contract names as anchors
    (contract.lock: plugins/am, plugins/apim), which a fresh clone has not made yet (`make init` does). A unit that binds one
    of them where it is meant to be is not told where the anchor is, whether the folder is there or not."""
    locked = _lock()
    named = {name.partition("/")[2] for kind, name in (locked[2] if locked else ()) if kind == "anchor" and name.startswith("plugins/")}
    return sorted({*products, *named})


def _anchors(products):
    """The places a unit may reach beyond its own folder, real: the seeder and each plugin folder; `project` is the
    whole of the project, where a depth mistake lands."""
    return {"project": os.path.realpath(ROOT), "seeder": os.path.realpath(ROOT / "seeder"),
            "plugins": [os.path.realpath(ROOT / "plugins" / name) for name in products]}


def _read(file):
    """The text of a file that is a plain file of at most 1 MB, else None: a link or a pipe is not followed or waited for."""
    try:
        if not stat.S_ISREG(os.lstat(file).st_mode):
            return None
        descriptor = os.open(file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as handle:
        data = handle.read(BIG_FILE + 1)
    try:
        return data.decode("utf-8") if len(data) <= BIG_FILE else None
    except UnicodeDecodeError:
        return None


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
        elif info.st_size > BIG_FILE:
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
            elif stat.S_ISREG(info.st_mode) and info.st_size > BIG_FILE:
                yield f"{rel} is over 1 MB: the Lab does not read more than that of a file, so the unit is refused"
        if count > SEED_ENTRIES:
            yield f"{shown}/seed holds more than {SEED_ENTRIES} entries: the Lab does not read more, so the unit is refused"
            return


def _screen_notes(text, file):
    """What an author is told when the text screen refuses a unit file (, T19). The screen reads words, not YAML: the
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
    engine's: one line for each item with the level it appeared at."""
    try:
        text = (ROOT / LOCK_FILE).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    levels, rows = {}, {}
    for row in text.splitlines():
        words = LOCK_TOKEN.findall(row)
        if len(words) == 2 and words[0] in ("contract", "contract_min") and words[1].isdigit():
            levels[words[0]] = int(words[1])
        elif len(words) >= 3 and LOCK_LEVEL.fullmatch(words[-1]):
            rows[(words[0], words[1].strip('"'))] = int(LOCK_LEVEL.fullmatch(words[-1]).group(1))
    return (levels["contract"], levels["contract_min"], rows) if len(levels) == 2 else None


def _label_of(key):
    """The item of the contract that a label stands for: the console labels are listed with their id as <id>."""
    found = CONSOLE_LABEL.fullmatch(key)
    return f"io.gravitee.console.<id>.{found.group(1)}" if found else key


def _seed_names(folder):
    """The folders and files of a seed as the contract names them, relative to the seed folder, to two levels of folders."""
    base, found = Path(folder) / "seed", set()
    if os.path.islink(base):
        return found
    for top, folders, names in os.walk(base):
        depth = len(Path(top).relative_to(base).parts)
        folders[:] = [name for name in folders if not name.startswith(".")] if depth < 3 else []
        for name in folders:
            found.add(("seed.folder", (Path(top) / name).relative_to(base).as_posix()))
        for name in names:
            if not name.startswith("."):
                found.add(("seed.file", (Path(top) / name).relative_to(base).as_posix()))
        if len(found) > SEED_ENTRIES:   # the screen reads no more, and this is no place to walk a bomb
            break
    return found


def _used(units, raw, inputs, rows):
    """{item: the level it appeared at} of the contract items that these units use: the keys of their x-gravitee block,
    the labels of their services, the core services, networks, variables and categories they refer to, the folders and files
    of their seed and the settings their seed service reads, the seeder and the plugin folders they build or bind, and the
    guide blocks they run. Only what the lock lists: the rest is no part of the contract."""
    core = raw.get(CORE_FILE) or {}
    core_services = set(core.get("services") or {})
    seeder, found = os.path.realpath(ROOT / "seeder"), set()
    plugins = ROOT / "plugins"
    for kind, uid, unit in units:
        config = raw.get(unit["file"]) or {}
        meta = _meta(config)
        found |= {("unit.key", f"x-gravitee.{key}") for key in meta}
        requires = meta.get("requires")
        found |= {("unit.key", f"x-gravitee.requires.{key}") for key in requires} if isinstance(requires, dict) else set()
        found.add(("core.category", str(meta.get("category") or "")))
        shown = meta.get("consoles") if isinstance(meta.get("consoles"), list) else []
        found |= {("core.service", name) for name in [*shown, meta.get("terminal")]
                  if isinstance(name, str) and name in core_services}
        for name, service in (config.get("services") or {}).items():
            if not isinstance(service, dict):
                continue
            if name in core_services:
                found.add(("core.service", name))
            found |= {("label", _label_of(key)) for key in service.get("labels") or {} if str(key).startswith("io.gravitee.")}
            nets = service.get("networks")
            found |= {("core.network", net) for net in (nets if isinstance(nets, (list, dict)) else [])}
            waits = service.get("depends_on")
            found |= {("core.service", other) for other in (waits if isinstance(waits, (list, dict)) else [])
                      if other in core_services}
            build = service.get("build")
            context = build if isinstance(build, str) else (build or {}).get("context") if isinstance(build, dict) else None
            if isinstance(context, str) and os.path.realpath(context) == seeder:
                found.add(("anchor", "seeder"))
            for volume in service.get("volumes") or []:
                source = volume.get("source") if isinstance(volume, dict) else None
                if isinstance(source, str):
                    real = os.path.realpath(source)
                    for product in (inputs.get("products") or ()):
                        if _within(real, os.path.realpath(plugins / product)):
                            found.add(("anchor", f"plugins/{product}"))
            if (service.get("labels") or {}).get("io.gravitee.role") == "seed":
                found |= {("seed.setting", key) for key in (service.get("environment") or {})}
        found |= {("core.variable", name) for name in (inputs.get("references") or {}).get(unit["file"], ())}
        found |= {("core.image_variable", name) for name in (inputs.get("references") or {}).get(unit["file"], ())}
        if kind == "playground":
            found |= _seed_names(ROOT / paths.unit_folder(kind, uid))
            if RUN_BLOCK.search((inputs.get("guides") or {}).get(uid) or ""):
                found.add(("guide.block", "run"))
    return {item: rows[item] for item in found if item in rows}


def _lab_lines(meta, units, raw, inputs):
    """The verdict on `lab` in catalog.yml: it is the highest level among the contract items the catalog uses, no
    lower (the Lab would start it on an engine that lacks what it needs) and no higher (it would be refused by a Lab that
    could run it), and never above the `contract` this engine provides."""
    locked = _lock()
    level = meta.get("lab")
    if locked is None:
        yield f"{LOCK_FILE} is not there or cannot be read, so `lab` cannot be verified"
        return
    contract, _, rows = locked
    used = _used(units, raw, inputs, rows)
    needed = max(used.values(), default=1)
    if type(level) is int and level > contract:
        yield f"lab is {level}, above contract {contract}, the newest level this engine provides"
    elif level != needed:
        at = sorted(ITEM.get(kind, "{}").format(name) for (kind, name), since in used.items() if since == needed)
        said = ", ".join(f"{name} ({needed})" for name in at[:SHOWN]) + (f" and {len(at) - SHOWN} more" if len(at) > SHOWN else "")
        yield f"lab is {level} but {needed} is needed" + (f": {said}" if said else "")


# ---------------------------------------------------------------------------------------------------------------------
# A catalog
# ---------------------------------------------------------------------------------------------------------------------

UNREADABLE = ("damaged", "downloading")   # a git install in these states has no units in the project (registry.sources)


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


def missing(ns, catalogs=None):
    """The catalogs that `ns` declares in requires.catalogs and that are neither installed nor linked here, [id]; read from
    the catalog.yml of `ns` through the registry (a file that cannot be read declares none: the check says it)."""
    catalogs = registry.inputs() if catalogs is None else catalogs
    return sorted(dep for dep in ((catalogs.get(ns) or {}).get("requires_catalogs") or {})
                  if not _present(catalogs.get(dep)))


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
    said += [f"{key} is not a key of x-gravitee-catalog: {', '.join(manifest.KEYS)}" for key in meta["unknown"]]
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


def catalog_units(ns, project):
    """[(kind, id, unit)] of the units of catalog `ns` in the project, by kind and id."""
    return [(kind, uid, unit) for kind in KINDS for uid, unit in sorted(project[f"{kind}s"].items())
            if unit.get("catalog") == ns]


def catalog_files(ns):
    """The files that `catalog_problems(ns)` is about, relative to the catalog: catalog.yml, then each unit's file."""
    project, _ = loaded()
    base = f"{paths.CATALOGS_DIR}/{ns}/"
    return [manifest.FILE, *(unit["file"][len(base):] for _, _, unit in catalog_units(ns, project))]


def catalog_problems(ns, project=None, inputs=None, audited=False):
    """Every problem found in catalog `ns`, as (file, what is wrong), the file relative to the catalog: what its
    catalog.yml says; for each unit the rules of every item (`_unit`), the names it gives, where its paths lead, what the
    screen and the policy say of it (a Blocked reason is an error: "Blocked: ..."); then the README tables the catalog
    keeps, the ports its units publish, the files whose name the Lab does not see, and whether its declarations of other
    catalogs and its `lab` are true. The images it builds are the audit's (IM01 to IM04, `audit_units`): `audited` is for a
    caller that runs `catalogs.audit` on the same catalog, and leaves out the line about `pull_policy` that IM01 says. It loads
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
    folder = ROOT / paths.CATALOGS_DIR / ns
    products = _anchored(inputs.get("products") or ())

    def shown(path):
        return _shown(path, ns)
    meta, said = _read_yml(ns, [one["id"] for one in defaults.read()])
    found += [(manifest.FILE, line) for line in said]
    units = catalog_units(ns, project)
    declared = sorted(((meta or {}).get("requires") or {}).get("catalogs") or {})
    context = {"catalog": ns, "declared": declared, "products": products, "audited": audited}
    listed = bool((inputs["catalogs"].get(ns) or {}).get("listed"))
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
                here += list(_unit(kind, uid, project, raw, True, context))
            except RecursionError:   # a file nested deeper than anyone writes one: it is said, not left to stop the check
                here.append("is nested too deep to be checked")
            if kind != "setup":
                written = unit if unit.get("services") or not unit.get("blocked") else {**unit, "services": _written(unit, raw)}
                roots = _roots(file, _included(unit), products)
                said_why = _meta(raw.get(file)).get("host_access")
                here += _naming(uid, written, raw, ns, listed)
                here += _geometry(written, (inputs.get("configs") or {}).get(file), ROOT / paths.unit_folder(kind, uid),
                                  said_why if isinstance(said_why, dict) else {},
                                  {**_anchors(products), "closure": roots["closure"]})
        found += [(where, line) for line in here]
    for problem in (project.get("catalog_problems") or {}).get(ns, ()):
        found.append((problem.get("display") or shown(problem["file"]), problem["message"]))
    for kind in KINDS:
        found += list(_table(kind, [unit for one, _, unit in units if one == kind], folder))
    found += list(_misnamed(folder))
    found += [(shown(file), line) for file, line in _ports(project, raw, None, ns, list(inputs["catalogs"]))]
    if meta is not None:
        found += [(manifest.FILE, line) for line in _dependencies(meta, units, inputs, ns)]
        found += [(manifest.FILE, line) for line in _canary(meta, units)]
        found += [(manifest.FILE, line) for line in _lab_lines(meta, units, raw, {**inputs, "products": products})]
    return found


# ---------------------------------------------------------------------------------------------------------------------
# A folder, where it is: Compose, the units for the audit, the temporary link
# ---------------------------------------------------------------------------------------------------------------------

NO_COMPOSE = "Docker Compose 5 not found"   # what a check that could not read the units says it did not do ("Not checked: ...")
# What the temporary copy of the Lab's project holds of the Lab itself: the files the project reads as text, and the places a unit
# names (the seeder, the plugin folder of each product) as empty folders, because a unit only names them. Not the `.env` (the
# settings of this machine: a fresh clone has none), not `.lab/` (made again below), not the folders of the old layout, and
# not the personal area, which a check of someone else's catalog has no use for. So nothing of the Lab is linked into it
COPIED = (CORE_FILE, paths.DEFAULT_FILE, LOCK_FILE)
ANCHORS = ("seeder", "plugins")   # the folders of the project that a unit names and that stand, in the copy, for the Lab's own
PRODUCT = re.compile(r"[a-z0-9][a-z0-9-]*")


def compose_missing():
    """Why Docker Compose cannot read the units of a catalog here, as the words that follow `Not checked:`, or None when it
    can (). Compose is looked for in the real environment, in the places the Docker CLI looks (compose.plugin), so that a
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
    """A folder read as the Lab reads a catalog: its namespace, the project folder it is linked into (temporary), the Lab's own
    project folder, and the folder as it is."""
    ns: str
    root: Path
    lab: Path
    folder: Path


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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
                                                                       "added": _utc(), **({"host": True} if host else {})}]})
            registry.write_lock({"schema": 1, "catalogs": locks})
            for name, audience in (audiences or {}).items():
                registry.set_lock(name, audience=audience)
            forget()
            try:
                yield Linked(ns, holder, lab, Path(folder))
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
    line among those the unit's verdict names, the reason as the Lab says it, then `Fix:` from the table of fixes (trust.FIXES,
    else the standard's, explain.T_FIXES) with its blanks filled from the reason and the catalog. A reason that two of those rules
    say alike, with different fixes, has no code: it would send the author to the wrong one."""
    from ..catalogs import explain   # here and not above: it reads the rule modules, which the engine's check has no use for
    fixes = getattr(trust, "FIXES", None) or explain.T_FIXES
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
    locked = _lock()
    fields = {"NS": ns.upper().replace("-", "_"), "file": file, **({"min": locked[1], "max": locked[0]} if locked else {}), **fields}
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
    (the standard has none for it yet) and no line. All of them are errors. `hide`: the folders of this machine whose place in a
    line is taken out (the temporary project of a check, the folder that was linked into it)."""
    verdicts = {_shown(unit["file"], ns): (unit.get("verdict") or {}).get("codes") or ()
                for _, _, unit in catalog_units(ns, project)}
    out = []
    for where, line in found:
        code, text = "", line
        if line.startswith("Blocked: "):
            code, text = _blocked(line[len("Blocked: "):], verdicts.get(where, ()), ns, where)
        out.append(Problem(code, ERROR, where, 0, _hidden(text, hide)))
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
    found += [(CORE_FILE, message) for message in _literals(raw, CORE_FILE, [])]
    found += [(CORE_FILE, message) for message in _loopback(
        port for service in services.values() for port in service.get("ports") or [])]
    found += [(CORE_FILE, message) for message in _contract_keys(core)]
    extra = []
    if sharing:
        kind, uid = paths.unit_of(sharing) or (None, None)
        if kind:
            context = {"products": _anchored(inputs.get("products") or ())}   # a bind of a plugin folder is an anchor, as in a catalog
            found += [(sharing, message) for message in _unit(kind, uid, project, raw, True, context)]
            if kind == "playground":
                found += [(sharing, message) for message in _alone(project["playgrounds"][uid])]
            extra = list(_ports(project, raw, sharing))
    return [*_noticed(project, raw, found, {CORE_FILE, sharing}), *found, *extra, *_skills(), *_agents(), *_gemini()]


# ---------------------------------------------------------------------------------------------------------------------
# Saying it
# ---------------------------------------------------------------------------------------------------------------------

def _data(text):
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _property(text):
    return _data(text).replace(":", "%3A").replace(",", "%2C")


def _printable(text):
    """A line for a terminal: what an author's file name or a message holds that is not printable (a control character, an
    escape sequence) is a space, so that no name can write to the screen of the person who reads the report."""
    return "".join(c if c.isprintable() else " " for c in str(text))


def printable(text):
    """A text for a terminal, line by line: what an author's file, name or message holds that is not printable (a control
    character, an escape sequence, a carriage return) is a space, the line breaks kept. Everything the command line prints
    goes through it, so that no catalog can write to the screen of the person who reads."""
    return "\n".join(_printable(line) for line in str(text).replace("\r\n", "\n").split("\n"))


def render(found, files, how="text"):
    """The report of a check, as the text to print. `found` is [(file, what is wrong)], `files` the files to list even when
    they are fine. `text`: a line for each file with a mark, its problems under it, then the verdict (what `make check`
    prints); `github`: one `::error file=...::message` line for each problem, which a CI shows on the file; `json`: the list
    of {file, message}."""
    if how == "json":
        return json.dumps([{"file": where, "message": message} for where, message in found], indent=2) + "\n"   # ASCII: a name that is no UTF-8 stays printable
    found = [(_printable(where), _printable(message) if how == "text" else _printable_keeping_breaks(message))
             for where, message in found]
    if how == "github":
        return "".join(f"::error file={_property(where)}::{_data(message)}\n" for where, message in found)
    lines = []
    for path in files + sorted({where for where, _ in found} - set(files)):
        mine = [message for where, message in found if where == path]
        lines.append(f"{'✗' if mine else '✓'} {path}")
        lines += [f"    {message}" for message in mine]
    lines.append(f"\n{len(found)} problem{'s' if len(found) != 1 else ''}" if found else "\nAll good")
    return "\n".join(lines) + "\n"


def _printable_keeping_breaks(text):
    """As _printable, the line breaks of a message kept: an annotation of GitHub says them itself."""
    return "".join(c if c.isprintable() or c in "\r\n" else " " for c in str(text))


def run():
    """Prints the files with their problems, then the verdict. Returns the exit code: 1 when something is wrong."""
    found = problems()
    print(render(found, [CORE_FILE]), end="")
    return 1 if found else 0
