"""The project's layout: the core file, and the add-ons, setups and playgrounds of its catalogs.
The folder decides what a file is and gives its id. Also how a file is replaced whole, never read half-written."""

import json
import os
import re
import stat
import tempfile
from pathlib import Path

LAB_UI_DIR = Path(__file__).resolve().parent.parent
ROOT = LAB_UI_DIR.parent
WEB_DIR = LAB_UI_DIR / "web"

CORE_FILE = "docker-compose.yml"
ENV_FILE = ".env"
# What the Lab UI keeps of this machine, out of sight and out of git: what is on, what is starred, the registered platforms
LAB_DIR = ".lab"
# What runs on top of the core, an `include` list; empty when nothing is on. Compose reads it with the core through
# COMPOSE_FILE: in .env for a `docker compose` typed by hand, set by the Lab UI and the Makefile for theirs
ON_FILE = f"{LAB_DIR}/on.yml"
COMPOSE_FILE = f"{CORE_FILE}:{ON_FILE}"

# Where the units are: one folder per catalog under .lab/catalogs, all at the same depth, so a relative path in a unit
# resolves the same wherever the unit sits. `local` holds what belongs to this machine, as a catalog of its own.
CATALOGS_DIR = f"{LAB_DIR}/catalogs"
LOCAL = "local"
# The catalogs this Lab is configured with, tracked at the root. The first owns the bare ids; without the file there is
# no default catalog (default_ns) and the units are where they were before catalogs (_LEGACY_ID, below)
DEFAULT_FILE = "catalogs.default.json"
# What the Lab writes in a git install, and so the list of what is content in it: a file is, and only if it is listed
MANIFEST = ".installed.json"
CATALOG_FILES = {"addon": f"{CATALOGS_DIR}/{{ns}}/addons/{{name}}/compose.yml",
                 "setup": f"{CATALOGS_DIR}/{{ns}}/setups/{{name}}.yml",
                 "playground": f"{CATALOGS_DIR}/{{ns}}/playgrounds/{{name}}/compose.yml"}

# An id is a name in lowercase letters, digits and dashes, in the catalog it comes from. The first catalog of the
# default file owns the bare ids (bank); what belongs to this machine has local/<name>; any other catalog acme-hub has
# @acme-hub/<name>. A leading @ can never start a bare id, so an address that holds an id reads without a lookup. The
# pattern keeps one capture group, because a route passes its groups on.
NS = r"[a-z][a-z0-9-]{0,22}[a-z0-9]"   # a catalog's namespace: 2 to 24 characters, the first a letter
NAME = r"[a-z0-9-]+"
# No catalog takes these as its namespace, and no id is written with one: the registry accepts the reserved word of the
# default catalog (gravitee) for the entry the default file lists, whose items have bare ids.
RESERVED_NS = frozenset({"local", "lab", "official", "core", "engine", "gravitee", "builtin", "api", "static",
                         "all", "none", "this", "machine", "personal", "check", "new", "cache", "stage"})
ID = rf"(local/{NAME}|@{NS}/{NAME}|(?!local/)(?!@){NAME})"
_ID, _NS, _NAME = re.compile(ID), re.compile(NS), re.compile(NAME)
# The unit files of a catalog folder, by kind, and the folder of each kind
_IN_CATALOG = {kind: re.compile(template.replace(".", r"\.").format(ns=f"({NS})", name=f"({NAME})"))
               for kind, template in CATALOG_FILES.items()}
_FOLDER = {kind: template.split("/{ns}/")[1].split("/")[0] for kind, template in CATALOG_FILES.items()}

# Before catalogs, which is while no default file exists: the units sit in the project's own folders, the personal ones
# in the local/ folder of their kind, out of git, with the id local/<name>: that folder is not an add-on or a
# playground itself. The suites that have not moved to catalogs still write this layout.
_LEGACY_ID = "(local/[a-z0-9-]+|(?!local/)[a-z0-9-]+)"
# kind: (the file of an id, the files that are of this kind)
UNITS = {
    "addon": ("addons/{}/compose.yml", re.compile(rf"addons/{_LEGACY_ID}/compose\.yml")),
    "setup": ("setups/{}.yml", re.compile(rf"setups/{_LEGACY_ID}\.yml")),
    "playground": ("playgrounds/{}/compose.yml", re.compile(rf"playgrounds/{_LEGACY_ID}/compose\.yml")),
}
KINDS = tuple(UNITS)
NOUNS = {"addon": "add-on", "setup": "setup", "playground": "playground"}   # a kind, in a sentence
SKILL_FILE = ".agents/skills/{}/SKILL.md"   # what tells an AI agent how to do one thing in this project
# In a playground's folder: the API definitions its seed imports, those of the platform's own environment (seed/apim)
# and those of each environment the seed adds (seed/environments/<id>)
SEED_API_GLOBS = ("seed/*/apis/*.json", "seed/environments/*/apis/*.json")


class NotFound(LookupError):
    """Something asked for by id or path that the project does not have."""


_default = (None, None)   # (what the default file looked like, the namespace read from it)
_DEFAULT_SCHEMA, _DEFAULT_LIMIT = 1, 1 << 16   # a few entries; a bigger file is not one, and parsing one is not free


def _read_default(file):
    """The id of the first entry of the default file, or None for anything that is not one: unreadable, not a regular
    file, over 64 KB, not JSON, an unknown schema, no entry, an id outside the NS grammar, or `local`, which would put
    the bare ids where the items of this machine are. Only the first entry is looked at: it owns the bare ids, and a
    damaged one must not hand them to the second."""
    try:
        with open(file, "rb") as f:
            raw = f.read(_DEFAULT_LIMIT + 1)
        data = json.loads(raw.decode("utf-8-sig")) if len(raw) <= _DEFAULT_LIMIT else None
    except (OSError, ValueError, RecursionError):
        return None
    schema = data.get("schema") if isinstance(data, dict) else None
    entries = data.get("catalogs") if type(schema) is int and schema == _DEFAULT_SCHEMA else None
    first = entries[0] if isinstance(entries, list) and entries else None
    uid = first.get("id") if isinstance(first, dict) else None
    return uid if isinstance(uid, str) and _NS.fullmatch(uid) and uid != LOCAL else None


def default_ns():
    """The id of the first entry of catalogs.default.json, which owns the bare ids; None without a usable file. Read
    again only when the file changes (path, modified time, size, inode), because every state poll asks. This is the one
    reader of that id. The rest of the file (the addresses, the other entries) is
    defaults.read()'s, which checks what it uses."""
    global _default
    file = ROOT / DEFAULT_FILE
    try:
        info = file.stat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):   # a pipe would block the read
        return None
    seen = (str(file), info.st_mtime_ns, info.st_size, info.st_ino)
    if _default[0] != seen:
        _default = (seen, _read_default(file))
    return _default[1]


def _is_id(uid, default):
    """is_id, for a caller that has read the default namespace and will not read it again."""
    if not isinstance(uid, str) or not _ID.fullmatch(uid):
        return False
    if uid.startswith("@"):
        ns = uid[1:].split("/", 1)[0]
        return ns not in RESERVED_NS and ns != default
    return True


def is_id(uid):
    """Whether `uid` is the id of a unit: bare, local/<name> or @<namespace>/<name>. Not `@<default namespace>/<name>`,
    which is the bare id of that item, nor a reserved namespace (@local/x would be a second spelling of local/x). Only
    the form is judged, never whether the unit exists. While no default file exists a bare id is still an id, because
    the layout before catalogs serves it. A bare `local` is one too, as the grammar has it, though it shares its
    address with the items of this machine (#/playgrounds/local/x is local/x, never `local` at a step x): the checks of
    `make check` refuse a shared unit of that name, so that none exists."""
    return _is_id(uid, default_ns() if isinstance(uid, str) and uid.startswith("@") else None)


def _canonical(uid, default):
    if default and isinstance(uid, str) and uid.startswith(f"@{default}/") and _NAME.fullmatch(uid[len(default) + 2:]):
        return uid[len(default) + 2:]
    return uid


def canonical(uid):
    """`@<default namespace>/x` as the bare id x it names, anything else as it is. To read input (a key of a conflict, a
    pasted address), never to write: nothing writes the long form."""
    return _canonical(uid, default_ns())


def _split(uid, default):
    """(namespace, name) of an id _is_id accepted; the namespace of a bare id is the default, None without one."""
    if uid.startswith("@"):
        return tuple(uid[1:].split("/"))
    if uid.startswith(f"{LOCAL}/"):
        return LOCAL, uid[len(LOCAL) + 1:]
    return default, uid


def split_ref(uid):
    """(namespace, name) of an id, `@<default namespace>/x` too: "@acme-hub/bank" is ("acme-hub", "bank"), "local/x" is
    ("local", "x"), "bank" is (the default namespace, "bank"), which is None while no default file exists. NotFound
    for what is not an id."""
    default = default_ns()
    uid = _canonical(uid, default)
    if not _is_id(uid, default):
        raise NotFound("Not an id")
    return _split(uid, default)


def origin(uid):
    """The namespace an item sits in: local, the default namespace for a bare id, else its catalog's."""
    return split_ref(uid)[0]


def is_default(uid):
    """Whether an id is bare, which is to say an item of the default catalog."""
    return isinstance(uid, str) and _NAME.fullmatch(uid) is not None


# The only wildcards unit_file turns an id into: every unit of a catalog, and those whose name is no dot file
_GLOBS = ("*", "[!.]*")


def _place(uid, default):
    """(namespace, name) a path is made of, for an id that is one, under the default namespace `default`. The name may
    be one of the wildcards of _GLOBS, which is how a caller lists the units of a catalog. NotFound otherwise, for the
    second spelling of an id, and for a bare id with no default catalog."""
    glob = next((g for g in _GLOBS if isinstance(uid, str) and (uid == g or uid.endswith(f"/{g}"))), None)
    probe = uid[:-len(glob)] + "x" if glob else uid
    if default is None or not _is_id(probe, default):
        raise NotFound("Not an id")
    ns, name = _split(probe, default)
    return ns, (name[:-1] + glob if glob else name)


def unit_file(kind, uid):
    """The compose file of an add-on, setup or playground, relative to the project: in the folder of its catalog,
    .lab/catalogs/<namespace>/. NotFound for what is not an id. While no default file exists, the layout before
    catalogs: addons/<name>/compose.yml, with no @<namespace>/ id."""
    default = default_ns()
    if default is None:   # before catalogs
        if isinstance(uid, str) and uid.startswith("@"):
            raise NotFound("Not an id")
        return UNITS[kind][0].format(uid)
    ns, name = _place(uid, default)
    return CATALOG_FILES[kind].format(ns=ns, name=name)


def unit_of(path):
    """(kind, id) of a project-relative path, or None when it is not an add-on, setup or playground file: only
    .lab/catalogs/<namespace>/addons/<name>/compose.yml, setups/<name>.yml and playgrounds/<name>/compose.yml, nothing
    deeper, nothing in a staging folder (it starts with a dot, which a namespace cannot), nothing in a folder of the
    project itself. While no default file exists, the layout before catalogs."""
    default = default_ns()
    if default is None:   # before catalogs
        for kind, (_, pattern) in UNITS.items():
            m = pattern.fullmatch(path)
            if m:
                return kind, m.group(1)
        return None
    for kind, pattern in _IN_CATALOG.items():
        m = pattern.fullmatch(path)
        if m:
            ns, name = m.groups()
            if ns == LOCAL:
                return kind, f"{LOCAL}/{name}"
            if ns == default:
                return kind, name
            return None if ns in RESERVED_NS else (kind, f"@{ns}/{name}")
    return None


def names(ns, kind):
    """The names of the units of a kind in one folder of `.lab/catalogs`, sorted: a folder that holds a compose.yml, or a setup
    file whose name is a unit's. By listing, which is how a folder catalog, this machine's own and a staged tree
    (`.stage-<ns>`: `project.staged_units`) are read. `ns` is the name of the folder, which the caller has checked: a
    namespace or a staging folder."""
    base = os.path.join(ROOT, CATALOGS_DIR, ns, _FOLDER[kind])
    try:
        listing = os.listdir(base)
    except OSError:
        return []
    if kind == "setup":
        stems = (name[:-4] for name in listing if name.endswith(".yml"))
        return sorted(stem for stem in stems
                      if _NAME.fullmatch(stem) and os.path.isfile(os.path.join(base, f"{stem}.yml")))
    return sorted(name for name in listing
                  if _NAME.fullmatch(name) and os.path.isfile(os.path.join(base, name, "compose.yml")))


def _catalogs(default):
    """The namespaces of the other catalogs on disk, sorted: a folder of .lab/catalogs (or a link to one, which is how a
    folder catalog is there) whose name can be a namespace. A staging folder, a leftover or a reserved word is none."""
    base = os.path.join(ROOT, CATALOGS_DIR)
    try:
        listing = os.listdir(base)
    except OSError:
        return []
    return sorted(name for name in listing if _NS.fullmatch(name) and name not in RESERVED_NS and name != default
                  and os.path.isdir(os.path.join(base, name)))


def _vouched(ns):
    """The names of each kind that a git install's manifest vouches for, as the registry says them ({kind: set}); None
    for any catalog read by listing: this machine's own, a link to a folder, and a folder that holds no manifest and is no
    git entry of the person's catalogs (a git install without its manifest is damaged and vouches for nothing). A
    doubt vouches for nothing, because a unit carries its catalog's host allowance into the trust layer: a registry that
    raises, or answers in another shape, vouches for no unit."""
    base = os.path.join(ROOT, CATALOGS_DIR, ns)
    if ns == LOCAL or os.path.islink(base):
        return None
    try:
        from .catalogs import registry   # here and not above: the registry reads this module
        if not os.path.isfile(os.path.join(base, MANIFEST)) and not registry.is_git(ns):
            return None
        return {kind: set(listing) for kind, listing in registry.units_of(ns).items()}
    except Exception:  # noqa: BLE001 - whatever the registry fails with, or answers wrongly, vouches for nothing
        return {}


def _found(kinds, default):
    """{kind: [ids]} on disk for these kinds: the default catalog's, this machine's, then each other catalog's by
    namespace, each group sorted. One pass, so that the registry is asked once per git install."""
    found = {kind: [] for kind in kinds}
    for ns in [default, LOCAL, *_catalogs(default)]:
        listed = {kind: names(ns, kind) for kind in kinds}
        if not any(listed.values()):
            continue
        vouched = _vouched(ns)
        prefix = "" if ns == default else f"{LOCAL}/" if ns == LOCAL else f"@{ns}/"
        for kind, listing in listed.items():
            found[kind] += [prefix + name for name in listing if vouched is None or name in vouched.get(kind, ())]
    return found


def ids(kind):
    """The ids of a kind found on disk, by catalog: the default catalog's (bare), those of this machine (local/), then
    each other catalog's (@<namespace>/) by namespace, each group sorted. It is the set of units, not who wins a name:
    that is the precedence of registry.sources(), in the order catalogs were added. A git install lists the units its
    manifest vouches for, as registry.units_of says it, and only those; a folder catalog, a link and this machine's own
    folder are listed as they are. While no default file exists, the layout before catalogs (unit_ids)."""
    default = default_ns()
    if default is None:   # before catalogs
        return unit_ids(kind)
    return _found((kind,), default)[kind]


def files():
    """The file of every add-on, setup and playground found on disk, kind by kind, in the order of ids."""
    default = default_ns()
    if default is None:   # before catalogs
        return unit_files()
    found = _found(KINDS, default)
    return [CATALOG_FILES[kind].format(ns=ns, name=name) for kind in KINDS
            for ns, name in (_place(uid, default) for uid in found[kind])]


def unit_ids(kind):
    """The ids found on disk, sorted. A name outside [a-z0-9-] is not a unit: dot files."""
    patterns = [unit_file(kind, "*"), unit_file(kind, "local/*")]
    found = (unit_of(f.relative_to(ROOT).as_posix()) for pattern in patterns for f in ROOT.glob(pattern))
    return sorted(unit[1] for unit in found if unit)


def unit_files():
    """The file of every add-on, setup and playground found on disk."""
    return [unit_file(kind, uid) for kind in KINDS for uid in unit_ids(kind)]


def personal(uid):
    """Whether an id is of this machine, not shared with the project."""
    return uid.startswith("local/")


def contained(path, root):
    """Whether `path` is `root` or inside it once every link is followed, the way the system would resolve it (a
    relative path is the project's). To ask before the Lab writes under a unit's state folder: a container that ran
    there may have planted a link, and `..` or a link must not lead a write out of it. False for anything it cannot
    resolve, for a NUL and for a blank root, so a doubt refuses. The root must be a folder no container writes in, the
    unit's folder or the install, never the state folder itself: a link planted in the place of that folder leads out
    of it, and the root would be resolved to the same place."""
    try:
        if "\0" in os.fspath(path) or "\0" in os.fspath(root) or not os.fspath(root).strip():
            return False
        where, top = os.path.realpath(os.path.join(ROOT, path)), os.path.realpath(os.path.join(ROOT, root))
        return os.path.commonpath([where, top]) == top
    except (OSError, ValueError, TypeError):
        return False


def unit_folder(kind, uid):
    """The folder of an add-on or a playground, which holds its compose file; a setup is a single file."""
    return unit_file(kind, uid).rsplit("/", 1)[0] if kind != "setup" else None


def unit_guide(kind, uid):
    """The guide of a playground, next to its compose file; other units have none."""
    return f"{unit_folder(kind, uid)}/guide.md" if kind == "playground" else None


def unit_env(kind, uid):
    """The .env that holds a unit's secrets and settings: add-ons and playgrounds have one, setups do not."""
    return f"{unit_folder(kind, uid)}/.env" if kind in ("addon", "playground") else None


def seed_apis(folder):
    """The API definitions the seed of a playground imports, sorted: `folder` is the playground's own folder."""
    return sorted(file for pattern in SEED_API_GLOBS for file in folder.glob(pattern))


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def write_atomic(file, text, mode=0o644):
    """Writes a file whole as UTF-8 with Unix line ends, through a copy next to it under a name of its own, so that a
    command running at the same time never reads it half-written and two saves never share the copy. The copy has its
    mode before it holds anything: a .env is 0600 from the start. A copy that fails is removed, and the file stays as
    it was."""
    handle, tmp = tempfile.mkstemp(dir=file.parent, prefix=f".{file.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as f:
            if hasattr(os, "fchmod"):
                os.fchmod(f.fileno(), mode)
            else:
                os.chmod(tmp, mode)
            f.write(text)
        os.replace(tmp, file)
    except BaseException:
        os.unlink(tmp)
        raise
