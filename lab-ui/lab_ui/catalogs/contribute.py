"""Share to a catalog, and the end of a share: "Use the catalog version".

A share is a COPY. The personal item stays, runs and keeps its settings. What leaves is an allow-list of its folder: the
compose file, README and guide, `seed/` and `apps/`, and the files the compose file names by a relative path inside the
folder; never what is the machine's (`.env`, `.state`, `secrets`, keys), nothing `store.rejections` refuses, no scratch file
of an editor. The compose file is written again for the place it will sit (`moves.repoint`: a personal item and a catalog
unit are at one depth, so only an include of the catalog itself changes).

Before anything leaves it is judged the way a consumer and the catalog's CI will judge it: its includes are in the target
or in a catalog the target declares, no secret is in it and, for a Public target, no name of a private catalog; the text
screen is clean, Compose loads it alone with the core, the trust policy lets it run, the rules every item of a catalog keeps
(`check.problems(sharing=...)`: a category, a README, `unless-stopped`, a description on each service, ports on the loopback)
are kept, and the rules of the catalog standard (ST, SE, DF, GD, IM) find nothing in the files that would be copied. The copy
is judged in a hidden folder beside the installs, `.lab/catalogs/.stage-share~<token>`, at the depth of a unit so that its
relative paths resolve as they will; an include of the target's own units is written there as the include of another catalog,
which reaches the same files. The sweeper of the Lab removes what a crash leaves, like any staging folder. Nothing is written
inside an install.

A catalog that is a repository gets a branch: the files go in a commit built with plumbing in the catalog's bare store (no
working tree, no hook) on top of the tip just fetched, as the person (their `user.name` and `user.email`, no trailer, no
tool), and pushed to `lab/<name>-<yyyymmdd>`, never forced, never to the default branch. A refused push leaves the item where
it was. A folder catalog gets a plain copy of the same files (`sweep` removes the half copy a stop leaves). `moved` and
`use_catalog_version` are the other half: once the catalog has the item and the person has updated, the item of this machine
and the catalog's are the same item, and this replaces the first by the second, taking its settings along; what else
its folder holds is set aside in `.lab/migration/kept/`, and nothing is deleted while anything could not be moved.

Every function reads `paths.ROOT` at call time. The catalogs lock is held while a copy is judged and while a personal item is
replaced, never while the network is asked."""

import contextlib
import json
import os
import posixpath
import re
import shutil
import stat
import tempfile
import time
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple
from urllib.parse import quote

from ..library import check, layers, moves, personal, project as projects, tested
from ..docker import compose
from .. import paths
from . import defaults, gitx, install, manifest, meta as block, registry, resolve, store as bare, trust
from .registry import CatalogError

LOCAL = paths.LOCAL
PROBE_AGE = 600.0   # seconds an answer of the push probe is believed
LOAD_WAIT = 20   # seconds Compose has to load a copy alone with the core
# The seconds the network is given in a share (a request has 120 in all): the probe (gitx's own 20, believed for ten
# minutes), the fetch of the tip, the question of the names of the branches, the push
FETCH_CAP, LIST_WAIT, PUSH_WAIT = 40, 15, 40
NAME_LIMIT = 60   # characters of an item's name that go into a subject or a line
SWEEP_AGE = 24 * 3600.0   # seconds after which a half copy in a person's folder is a leftover
# Registries everyone pulls from: the host of one of them says nothing of a private catalog, and its images are in public items
PUBLIC_REGISTRIES = frozenset({"docker.io", "index.docker.io", "registry-1.docker.io", "ghcr.io", "quay.io", "gcr.io",
                               "registry.k8s.io", "k8s.gcr.io", "mcr.microsoft.com", "public.ecr.aws", "registry.gitlab.com"})

# What the catalogue of errors says of a share, with its status
LINES = {
    "contribute_none": (400, "{name} does not take contributions from the Lab."),
    "no_write_access": (400, "You cannot push to {name}. Ask for access."),
    "share_exists": (409, "{name} already exists in {catalog}."),
    "share_includes": (400, "Includes {item}, which is not in {catalog}."),
    "share_use_catalog": (400, "Includes {item}, which {catalog} has already: use the catalog version of it, then share this."),
    "share_secret": (400, "{file} holds what looks like a secret. Remove it and share again."),
    "share_private": (400, "{file} names {private}, which is private. Remove it and share again."),
    "share_private_copy": (400, "{name} is a copy of an item of {private}, which is private. Fix: share it to a private catalog."),
    "no_identity": (400, "Set your name and email in Git, then share again."),
    "push_rejected": (502, "{host} refused the branch. Check that you can push, then share again."),
}
UNDECLARED = "Includes {item} ({owner}), which {catalog} does not declare."   # the reason of a disabled destination


def _refusal(code, **fields) -> CatalogError:
    status, line = LINES[code]
    return CatalogError(code, line.format(**fields), status)


def _clock() -> float:
    return time.monotonic()


def _today() -> str:
    return time.strftime("%Y%m%d", time.gmtime())


# ---------------------------------------------------------------------------------------------------------------------
# The catalogs a share can go to
# ---------------------------------------------------------------------------------------------------------------------

def _own(project, kind, uid, done="shared"):
    """(the file of the personal item, its name for a person, its name as a folder): the id is `local/<name>` and its file is
    there, else the refusal of personal.py ("Only your own add-ons can be `done` here"). The name a person reads is
    `x-gravitee.name`, else the folder's."""
    if kind not in paths.KINDS:
        raise ValueError(f"{kind} is not a kind of item")
    file = personal._own(kind, uid, done)
    slug = uid.split("/", 1)[1]
    unit = (project.get(kind + "s") or {}).get(uid) or {}
    return file, resolve.shown(unit.get("name") or slug, NAME_LIMIT), slug


def _catalog(ns, rows):
    """(entry, row) of the added catalog `ns` that can take a share, else NotFound; `rows` is registry.inputs(). `local` is the
    source of a share and never its destination; a catalog with no copy yet has nothing to judge the item against."""
    entry = registry.entry(ns) if isinstance(ns, str) else None
    row = rows.get(ns) if isinstance(ns, str) else None
    if ns == LOCAL or entry is None or row is None:
        raise paths.NotFound(f"No catalog {resolve.shown(ns, 40)}")
    if row["kind"] == "git" and registry.manifest(ns) is None:
        raise paths.NotFound(f"{row['name']} is not downloaded")
    if row["kind"] == "folder" and row["state"] != "linked":
        raise paths.NotFound(f"The folder of {row['name']} is not there")
    return entry, row


_CATALOG_YML = {}   # ns -> (the stamp of its catalog.yml, what the Lab reads in it)


def _yml(ns) -> dict:
    """The validated catalog.yml of an added catalog ({} when it cannot be read: its own review is the real gate), read again
    when the file changes."""
    relative = f"{paths.CATALOGS_DIR}/{ns}/{registry.CATALOG_FILE}"
    try:
        info = os.stat(paths.ROOT / relative)
    except OSError:
        return {}
    held = _CATALOG_YML.get(ns)
    if not held or held[0] != (info.st_mtime_ns, info.st_size):
        try:
            found = manifest.read_catalog(relative)
        except CatalogError:
            found = {}
        held = _CATALOG_YML[ns] = ((info.st_mtime_ns, info.st_size), found)
    return held[1]


def _takes(ns, row) -> bool:
    """Whether the catalog takes contributions from the Lab: a folder always does, a repository unless its catalog.yml says
    `contribute: none`."""
    return row["kind"] != "git" or _yml(ns).get("contribute") != "none"


def _address(entry, held):
    """(the Address a repository catalog is pushed to, its branch): the tracked address for a listed catalog, the stored one
    for any other, in the form (https or ssh) that answered the last time."""
    url, branch = defaults.effective(entry)
    address = gitx.parse_address(url or "")
    if held.get("via") == "ssh":
        address = gitx.ssh_form(address) or address
    return address, branch


_PROBES = {}   # (url, branch) -> (when, "yes" | "no")


def _believed(address, branch) -> Optional[str]:
    """What the push probe said of this address within PROBE_AGE, else None."""
    found = _PROBES.get((address.url, branch))
    return found[1] if found and _clock() - found[0] < PROBE_AGE else None


def _probe(ns, address, branch) -> str:
    """"yes", "no" (the host refused the sign-in or the permission) or "unknown" (the network did not answer,
    which is no reason to refuse). A dry run of a push of the catalog's own tip to a branch that does not exist, believed for
    ten minutes. The network is asked without a lock."""
    known = _believed(address, branch)
    if known:
        return known
    held = registry.lock_of(ns)
    answer = gitx.push_probe(address, held.get("tip") or held.get("pin") or "", store=registry.store_dir(ns))
    if answer in ("yes", "no"):
        _PROBES[(address.url, branch)] = (_clock(), answer)
    return answer


# ---------------------------------------------------------------------------------------------------------------------
# What the item includes
# ---------------------------------------------------------------------------------------------------------------------

def _includes(project, file):
    """([(kind, id, namespace, name) of the units the personal file includes], [the other files it includes]), as the screen
    reads its `include` keys; None when it cannot say for all of them."""
    try:
        text = (paths.ROOT / file).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    found = trust.include_targets(text, file)
    if found is None:
        return None
    units, others = [], []
    for target in found:
        place, unit = resolve.target_of(target), paths.unit_of(target)
        if place is None or unit is None:
            others.append(posixpath.basename(target))
            continue
        known = (project.get(unit[0] + "s") or {}).get(unit[1]) or {}
        units.append((unit[0], unit[1], place[0], resolve.shown(known.get("name") or place[2], NAME_LIMIT)))
    return units, others


def _outside(row, ns, units, rows):
    """(the item, the catalog it is in, whether it is only on this machine, whether the target has it already) of the first include
    the target does not allow, or None: an include is in the target itself or in a catalog the target declares; a personal item is
    never in one. An item of this machine that was shared to the target already is the one to replace by the catalog's (`use the
    catalog version`) before this one is shared. `rows` is registry.inputs()."""
    declared = set(row.get("requires_catalogs") or ())
    for kind, uid, where, name in units:
        if where != ns and (where == LOCAL or where not in declared):
            slug = uid.split("/", 1)[1] if where == LOCAL and "/" in uid else None
            shared = bool(slug) and slug in (registry.units_of(ns).get(kind) or ())
            return name, (rows.get(where) or {}).get("name") or where, where == LOCAL, shared
    return None


def _outside_refusal(outside, row) -> CatalogError:
    """What keeps a share from going when an include is not in the target."""
    code = "share_use_catalog" if outside[3] else "share_includes"
    return _refusal(code, item=outside[0], catalog=row["name"])


# ---------------------------------------------------------------------------------------------------------------------
# destinations
# ---------------------------------------------------------------------------------------------------------------------

def destinations(project: dict, kind: str, uid: str) -> list:
    """[{id, name, kind, writable, audience, reason}] of the catalogs the personal item `uid` can be shared to: each
    added repository that takes contributions and each folder, the default catalog first, in the order they claim names. A
    destination that cannot take this item is there with its `reason`: it takes no contributions from the Lab, the item
    includes what it does not declare, the push was refused (what the probe said in the last ten minutes: the network is not
    asked here), the folder cannot be written; `writable` is false for the last two. A catalog with no copy is none, and so is an
    imported playground's, which the Lab keeps. What only this machine has goes with the item (`_bundle`): an include of it keeps
    no catalog from taking it. Reads the disk only."""
    file, _, _ = _own(project, kind, uid)
    included, rows, found = _includes(project, file), registry.inputs(), []
    for ns in registry.precedence():
        if registry.imported(ns):
            continue
        try:
            entry, row = _catalog(ns, rows)
        except paths.NotFound:
            continue
        writable, reason = True, ""
        members, reused = _bundle(project, kind, uid, ns)
        allowed = {one for _, one, _, _ in members} | {one for _, one, _ in reused}
        outside = _outside(row, ns, [one for one in included[0] if one[1] not in allowed], rows) if included else None
        if not _takes(ns, row):
            reason = _refusal("contribute_none", name=row["name"]).message
        elif outside:
            reason = _outside_refusal(outside, row).message if outside[2] \
                else UNDECLARED.format(item=outside[0], owner=outside[1], catalog=row["name"])
        elif row["kind"] == "git":
            try:
                address, branch = _address(entry, registry.lock_of(ns))
            except CatalogError:
                address = None
            if address is not None and _believed(address, branch) == "no":
                writable, reason = False, _refusal("no_write_access", name=row["name"]).message
        elif not os.access(os.path.realpath(registry.install_dir(ns)), os.W_OK | os.X_OK):
            writable, reason = False, f"{row['name']} cannot be written to."
        found.append({"id": ns, "name": row["name"], "kind": row["kind"], "writable": writable,
                      "audience": row["audience"], "reason": reason})
    return found


# ---------------------------------------------------------------------------------------------------------------------
# What is copied: the allow-list
# ---------------------------------------------------------------------------------------------------------------------

TOP = ("compose.yml", "README.md", "guide.md", ".env.example")   # an item's own files; `seed/` and `apps/` come whole
TREES = ("seed", "apps")
SCRATCH_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
SCRATCH_ENDS = ("~", ".swp", ".swo", ".tmp", ".bak", ".orig", ".rej", ".pyc", ".pyo")
SCRATCH_FOLDERS = frozenset({"__pycache__", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea",
                             ".vscode", ".venv", "venv", ".git", ".github", ".svn", ".hg"})
VERSIONED = frozenset({".git", ".github", ".svn", ".hg"})   # a share leaves them out; deleting an item does not delete them
CHECKING = re.compile(r"\.[^/]+\.(?:check|effects)\.yml")   # the hidden copies the editor makes to have Compose check a file
MACHINE = frozenset(bare.MACHINE_DIRS)   # folders of the machine's own: not entered, not shared
# The machine's files and an agent's: left out without a word, unless the compose file names them
QUIET = ("ST05", "ST06", "ST07")
STATE = ".state"   # where a seed writes what it created: bound by the compose file, never shared, never missed
LOOK = 2 * bare.UNIT_FILES   # entries of a folder looked at before it is too large to be one item


class Copy(NamedTuple):
    """What a share would put in a catalog: `files` {path in the catalog: (git mode, bytes)}, the compose file as it will be
    written (`text`) and as it was (`kept`), and what keeps the item from being shared as it is (`problems`). `folder`: where the
    compose file is, which Compose reads `kept` in; `place`: (was, now, table), how `text` was written from `kept` (moves.repoint)."""
    files: Dict[str, Tuple[str, bytes]]
    text: str
    kept: str
    problems: List[str]
    folder: str = ""
    place: tuple = ()


def _scratch(name) -> bool:
    return name in SCRATCH_NAMES or name.endswith(SCRATCH_ENDS) or bool(CHECKING.fullmatch(name))


def _walk(base, rel, problems, machine):
    """[(path relative to `base`, git mode, size)] of the files at `base/rel` (a file, or a folder and all below it) without
    following a link: a link is a problem and is left out, so is a scratch file or folder (silently), a folder of the machine's
    own is not entered and goes to `machine`. Stops with a problem when a folder holds more entries than an item may."""
    found, seen, todo = [], 0, [rel]
    parts = rel.split("/") if rel else []
    for number in range(1, len(parts)):   # a link on the way to it leads out of the folder
        if os.path.islink(os.path.join(base, *parts[:number])):
            problems.append(f"{'/'.join(parts[:number])} is a link. Fix: copy the folder itself; a catalog installs "
                            "no link.")
            return found
    while todo:
        here = todo.pop()
        full = os.path.join(base, here) if here else base
        try:
            info = os.lstat(full)
        except OSError:
            continue
        name = posixpath.basename(here)
        if stat.S_ISLNK(info.st_mode):
            problems.append(f"{here} is a link. Fix: copy the file itself; a catalog installs no link.")
        elif stat.S_ISDIR(info.st_mode):
            if name in MACHINE:
                machine.append(here)
            elif name not in SCRATCH_FOLDERS:
                try:
                    names = sorted(os.listdir(full), reverse=True)
                except OSError:
                    continue
                seen += len(names)
                if seen > LOOK:
                    problems.append("It holds more files than a catalog item may. Fix: split it.")
                    return found
                todo.extend(f"{here}/{one}" if here else one for one in names)
        elif stat.S_ISREG(info.st_mode) and here and not _scratch(name):
            found.append((here, "100755" if info.st_mode & 0o111 else "100644", info.st_size))
    return found


def _named(text) -> List[str]:
    """The paths inside the folder that a compose file names by a relative path, sorted ("" is the folder itself): the value
    of a path key (`moves._naming_files` finds the lines that name files), or, in a list of paths, the source of a bind, which
    is written `./something` (a name without it is a volume). No variable in it, nothing absolute or remote, no way out."""
    found = set()
    for line, names in moves._naming_files(text):
        if not names:
            continue
        code = line.split("#", 1)[0]
        key = moves.PATH_KEY.match(code)
        value = code[key.end():] if key else code.lstrip(" \t").lstrip("-").strip()
        for token in re.split(r"[\s,\[\]{}]+", value):
            token = token.strip("\"'")
            if not key:
                token = token.split(":", 1)[0]
            if not token or "$" in token or "://" in token or token.startswith(("/", "~", "..")):
                continue
            if not key and not token.startswith("./") and token != ".":
                continue
            where = posixpath.normpath(token)
            if not where.startswith(".."):
                found.add("" if where == "." else where)
    return sorted(found)


def _read(file, limit=bare.OTHER_FILE + 1) -> Optional[bytes]:
    """The bytes of a regular file without following a link, at most `limit` of them; None when it cannot be read."""
    try:
        descriptor = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return None
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read(limit)
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _place(kind, slug, ns, source=LOCAL):
    """(the folder the compose file is in now, the folder it will be in, its path in the catalog). `source`: the catalog
    it is in now, this machine's own by default."""
    here, there = f"{paths.CATALOGS_DIR}/{source}", f"{paths.CATALOGS_DIR}/{ns}"
    if kind == "setup":
        return f"{here}/setups", f"{there}/setups", f"setups/{slug}.yml"
    return f"{here}/{kind}s/{slug}", f"{there}/{kind}s/{slug}", f"{kind}s/{slug}/compose.yml"


MISSED = "{rel} is named by compose.yml and is not shared. Fix: make it a setting, or create it when the item starts."
REFUSED = {"ST03": "{rel} has a name the Lab cannot install. Fix: use letters, digits and . _ + - only.",
           "ST04": "{rel} is too deep or too long. Fix: at most 8 folders and 200 characters.",
           "ST09": "{rel} is larger than a catalog keeps. Fix: shrink it, or leave it out.",
           "ST10": "It holds more files, or more bytes, than a catalog item may. Fix: split it.",
           "ST13": "{rel} differs from another file only by case. Fix: rename one; a Mac cannot hold both."}


def _uid(source, slug):
    """The id of the item `slug` of the catalog `source`: local/<slug>, a bare name in the default catalog, else @<ns>/<slug>."""
    return f"{LOCAL}/{slug}" if source == LOCAL else slug if source == paths.default_ns() else f"@{source}/{slug}"


def collect(kind, slug, ns, source=LOCAL, moved=()) -> Copy:
    """The copy of the item `<source>/<slug>` (the personal item `local/<slug>` by default) for the catalog `ns`: the
    allow-list less what `store.rejections` refuses (what the Lab never installs), the compose file written again for its new
    place. `moved`: what else goes to `ns` with it, [(old path, new path)] relative to the project, so that an include of one
    of them follows it (an export carries an item and what it includes). The disk is read and nothing is written. What is left
    out and would be missed is a problem: a link, a file too large, a name a catalog cannot hold, a `.env` or a key that the
    compose file names."""
    before, after, head = _place(kind, slug, ns, source)
    uid = _uid(source, slug)
    data, problems = _read(paths.ROOT / paths.unit_file(kind, uid), bare.TEXT_FILE + 1), []
    if data is None:
        problems.append("compose.yml cannot be read.")
    elif len(data) > bare.TEXT_FILE:
        data = None
        problems.append(REFUSED["ST09"].format(rel="compose.yml"))
    try:
        kept = (data or b"").decode("utf-8")
    except ValueError:
        kept = ""
        problems.append("compose.yml is not UTF-8 text.")
    table = [(paths.unit_file(kind, uid), f"{paths.CATALOGS_DIR}/{ns}/{head}")] if kind == "setup" else [(before, after)]
    table += list(moved)
    text = moves.repoint(kept, before, after, table)
    where, place = str((paths.ROOT / paths.unit_file(kind, uid)).parent), (before, after, table)
    if kind == "setup":
        return Copy({head: ("100644", text.encode("utf-8"))}, text, kept, problems, where, place)
    base, named, machine, listing = str(paths.ROOT / before), _named(kept), [], {}
    for rel in sorted({*TOP, *TREES, *named}):
        listing.update({one: (mode, size) for one, mode, size in _walk(base, rel, problems, machine)})
    folder = f"{kind}s/{slug}/"
    entries = [bare.Entry(mode, "0" * 40, size, folder + one) for one, (mode, size) in sorted(listing.items())]
    refused = {entry.path: code for entry, code in bare.rejections(entries)}
    files = {head: ("100644", text.encode("utf-8"))}
    for entry in entries:
        rel, code = entry.path[len(folder):], refused.get(entry.path)
        if code in QUIET:   # the machine's own: said only when the compose file names this very file, and it is not state
            if rel in named and rel.split("/", 1)[0] != STATE:
                problems.append(MISSED.format(rel=rel))
        elif code:
            problems.append(REFUSED.get(code, "{rel} is not kept by a catalog.").format(rel=rel))
        elif rel != "compose.yml":
            content = _read(os.path.join(base, rel))
            if content is None:
                problems.append(f"{rel} cannot be read.")
            else:
                files[entry.path] = (entry.mode, content)
    problems += [MISSED.format(rel=one) for one in machine
                 if one != STATE and any(also == one or also.startswith(one + "/") for also in named)]
    return Copy(files, text, kept, list(dict.fromkeys(problems)), where, place)


# ---------------------------------------------------------------------------------------------------------------------
# The pre-flight
# ---------------------------------------------------------------------------------------------------------------------

# The rules of the standard whose finding is a secret in the copy (SE06 is `_named_private`)
SECRETS = ("SE01", "SE02", "SE04", "SE05")
LEAKS = (("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
         ("a token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}"
                                r"|xox[abprs]-[A-Za-z0-9-]{10,}"
                                r"|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})")),
         ("a license", re.compile(r"(?i)licen[sc]e[^\n]{0,40}[:=][ \t]*[\"']?[A-Za-z0-9+/_=-]{120,}")))


def _texts(files):
    """[(path, text)] of the files that are text (no NUL in the first 8 KB)."""
    for path, (_, data) in sorted(files.items()):
        if b"\0" not in data[:8192]:
            yield path, data.decode("utf-8", "replace")


def _shown(path, kind, slug):
    """A path of the catalog as the item's folder has it: the file a person opens."""
    prefix = f"{kind}s/{slug}/"
    return posixpath.basename(path) if kind == "setup" else path[len(prefix):] if path.startswith(prefix) else path


def _leaked(copy, kind, slug):
    """The CatalogError share_secret for the first file that holds a private key, a token of a known shape or a license, else
    None. What the SE rules find is added by `_rules`."""
    for path, text in _texts(copy.files):
        if any(pattern.search(text) for _, pattern in LEAKS):
            return _refusal("share_secret", file=_shown(path, kind, slug))
    return None


def _identifiers(ns, rows) -> Dict[str, str]:
    """{identifier: the name of the catalog it belongs to} for every other catalog of this machine that is not Public: its name,
    the address and the `owner/repo` of a repository, the prefix of the images its CI publishes and the host of that prefix
    (a registry of its own: not one everyone pulls from). Each is looked for as a whole word. SE06."""
    found = {}
    for other, row in rows.items():
        if other in (ns, LOCAL) or row["audience"] == "public":
            continue
        prefix = _yml(other).get("images")
        entry, wanted = registry.entry(other), [row["name"], prefix]
        host = prefix.partition("/")[0].lower() if isinstance(prefix, str) else ""
        if "." in host and host not in PUBLIC_REGISTRIES:
            wanted.append(host)
        if entry and entry["kind"] == "git":
            host, _, repo = registry.display_address(defaults.effective(entry)[0] or "").partition("/")
            wanted += [f"{host}/{repo}", repo if "." in host and "/" in repo else ""]
        for one in wanted:
            if isinstance(one, str) and len(one) >= 5:
                found[one] = row["name"]
    return found


def _ids(ns, rows) -> Dict[str, str]:
    """{id: the name of the catalog} for the other private catalogs whose id is long enough to be one thing. An id is a word of
    the language as often as not (`partner`, `internal`: `networks: [internal]` is no leak), so it is looked for where it names a
    catalog (`_named_private`)."""
    return {other: row["name"] for other, row in rows.items()
            if other not in (ns, LOCAL) and row["audience"] != "public" and len(other) >= 5}


def _named_private(copy, ns, kind, slug, rows):
    """The CatalogError share_private for the first file that names a private catalog (target Public), else None. A name, an
    address, an image prefix or a registry host is named as a whole word. An id is named in a unit id (`@<id>/name`), in a
    phrase that says it is a catalog (`the <id> catalog`, `catalog <id>`) and in a path (`../../../<id>/addons/`), except in
    the unit's own compose file, whose includes were judged already: a catalog the target does not declare is `share_includes`
    and private content under a Public one is Blocked."""
    head = _place(kind, slug, ns)[2]
    patterns = [(re.compile(r"(?i)(?<![\w.-])" + re.escape(one) + r"(?![\w-])"), owner, True)
                for one, owner in _identifiers(ns, rows).items()]
    for one, owner in _ids(ns, rows).items():
        word = re.escape(one)
        patterns.append((re.compile(rf"(?i)(?<=@){word}(?=/)|(?<![\w.-]){word}(?= catalog(?![\w-]))"
                                    rf"|(?<![\w-])catalogs? {word}(?![\w-])"), owner, True))
        patterns.append((re.compile(rf"(?i)(?<=/){word}(?=/)"), owner, False))
    for path, text in _texts(copy.files):
        for pattern, owner, everywhere in patterns:
            if (everywhere or path != head) and pattern.search(text):
                return _refusal("share_private", file=_shown(path, kind, slug), private=owner)
    return None


def _rules(copy, kind, slug, ns, row):
    """The errors of the rules of the catalog standard (ST, SE, DF, GD, IM) over the files that would be copied, as the
    catalog's CI reads them (`ListTree`): [Problem]. A part of a catalog: the files a whole one has are not asked for."""
    from . import audit, rules, tree   # here and not above: the rules are read only for a share
    from .rules import dockerfiles, guides, images, secrets, structure
    modules = [structure, secrets, dockerfiles, guides, images]
    listing = tree.ListTree.from_files(copy.files)
    meta = {key: value for key, value in _yml(ns).items() if isinstance(value, str)}
    ctx = rules.context(listing, partial=True, git=row["kind"] == "git", catalog=meta,
                        seeder_images=audit.lab_seeder_images())
    found = []
    for module in modules:
        found += [each for each in module.check(listing, ctx) if each.severity == "error"]
    return found


def _scrub(message, top, kind, slug) -> str:
    """A line of Compose without the place of the copy it read: the file of the copy is the item's own, `compose.yml`."""
    for base in (str(paths.ROOT / top), top):
        message = message.replace(f"{base}/setups/", "").replace(f"{base}/{kind}s/{slug}/", "")
    return message


def _claim(services, name, tier, names) -> dict:
    """What a unit holds, in the shape resolve.collisions reads: its services and their containers."""
    held = [one for one in names if one in services]
    return {"name": name, "tier": tier, "services": list(names), "volumes": [],
            "containers": [services[one]["container"] for one in held if services[one].get("container")]}


def _clash(project, rows, ns, key, mine, closure) -> Optional[str]:
    """T21: "Same name as X" when the copy defines a service, a container or a volume that the platform, a unit of the
    default catalog or a unit of a catalog added before `ns` already holds. The default catalog's units claim first and are
    never blocked by it; what the copy includes is no clash; the items of this machine claim nothing here, a consumer has none.
    `mine` is what the copy defines (resolve.owned_names), `closure` the units it includes."""
    default, services = paths.default_ns(), project["services"]
    order = [other for other in rows if other != LOCAL]
    before = {default, *order[:order.index(ns)]} if ns in order else {default}
    platform = [name for name, one in services.items() if one["owner"]["kind"] == "platform"]
    owned = {("platform", "core"): _claim(services, "the platform", "default", platform)}
    for kind in ("addon", "playground"):
        for uid, unit in project[kind + "s"].items():
            place = paths.origin(uid)
            if place in before:
                owned[(kind, uid)] = _claim(services, unit["name"], "default" if place == default else "other",
                                            unit.get("services") or ())
    owned[key] = {**mine, "tier": "default" if ns == default else "other"}
    return resolve.collisions([*owned], owned, {key: closure}).get(key)


def _judged(project, kind, uid, slug, ns, row, copy, stage, folder, aliases=None, bundle=()):
    """The reasons the trust policy gives for the copy as a consumer of catalog `ns` would meet it, in the words a Blocked
    item shows; [] when it may run. It is the judgement project.build makes of a unit of a catalog, over the staged copy:
    the same function, the same context (the catalog's listing, allowance and declarations), what Compose says of the copy
    alone with the core, and what it defines itself against the units it includes; then the rules that need the other units,
    as the project adds them: a name another unit holds (T21), a private unit under a public catalog (T24) and an included
    unit that is blocked (T25). What goes with it in the same share is staged beside it: its includes of those are judged at the
    place they will have in the catalog (`aliases`: {stage path: catalog path}), and their folders are the catalog's (`bundle`)."""
    inputs = projects.loaded()[1]
    core, core_config = inputs["raw"].get(paths.CORE_FILE) or {}, inputs.get("core_config")
    if core_config is None:
        return [f"The platform cannot be read: {inputs.get('core_error') or 'Compose did not answer'}"]
    config, raw = compose.config_json(compose.all_files([stage]), timeout=LOAD_WAIT), compose.raw(stage, timeout=LOAD_WAIT)
    names, direct = set((core.get("services") or {})), compose.includes(stage)
    others = [compose.raw(target, timeout=LOAD_WAIT) for target in direct if (paths.ROOT / target).is_file()]
    theirs = {name for other in others for name in (other.get("services") or {})}
    own = [name for name in (raw.get("services") or {}) if name not in names and name not in theirs]
    units = {"addon": project["addons"], "setup": project["setups"], "playground": project["playgrounds"]}
    closure = list(dict.fromkeys(node for target in direct if paths.unit_of(target)
                                 for node in resolve.closure(units, *paths.unit_of(target))))
    same = [paths.ROOT / paths.unit_folder(*node) for node in closure if node[0] != "setup" and paths.origin(node[1]) == ns]
    contract, listed, meta = inputs.get("contract") or {}, bool(row.get("listed")), raw.get("x-gravitee") or {}
    asked = (meta.get("requires") or {}).get("lab")
    context = {"ns": ns, "host_ok": bool(trust.host_ok(listed, {"host": row.get("host") == "allowed"})), "listed": listed,
               "local": False, "requires": sorted(row.get("requires_catalogs") or ()),
               "shared_settings": (inputs.get("shared_settings") or {}).get(ns) or [],
               "core_variables": sorted({*(v["key"] for v in inputs["variables"].get(paths.CORE_FILE, [])),
                                         *trust.reads([], core)}),
               "license_variable": projects._license_var(core),
               "closure_names": [node[1].rsplit("/", 1)[-1] for node in closure],
               "closure_services": sorted({name for node in closure
                                           for name in ((units[node[0]].get(node[1]) or {}).get("services") or ())}),
               "seeder_images": [contract["seeder_image"]] if contract.get("seeder_image") else [],
               "home": inputs.get("home"), "realpath": os.path.realpath,
               "level": asked if isinstance(asked, int) and not isinstance(asked, bool) else row.get("lab"),
               "contract": contract.get("contract", 1), "contract_min": contract.get("contract_min", 1)}
    roots = trust.build_roots(paths.ROOT, folder if kind != "setup" else None, [*same, *bundle], inputs.get("products") or (),
                              realpath=os.path.realpath)
    seeds = [data.decode("utf-8", "replace") for path, (_, data) in sorted(copy.files.items())
             if path.startswith(f"{kind}s/{slug}/seed/") and not any(part.startswith(".") for part in path.split("/"))]
    view = {"kind": kind, "id": uid, "name": meta.get("name") or slug, "meta": meta,
            "includes": [(aliases or {}).get(one, one) for one in direct], "seed": None,
            "references": trust.reads([(paths.ROOT / stage).read_text(encoding="utf-8")], raw, []),
            "seed_references": trust.seed_reads(seeds)}
    verdict = trust.judge(view, config, core_config, own, trust.overlays(raw, names, others), roots,
                          set(inputs["env"].get(paths.ENV_FILE) or ()), context)
    rows, key = inputs.get("catalogs") or {}, ("candidate", uid)
    mine = dict(resolve.owned_names(config, core_config, own, others), name=view["name"])
    # A unit that is blocked already claims nothing, and is not judged again
    clash = None if verdict["state"] == "blocked" else _clash(project, rows, ns, key, mine, closure)
    if clash:
        verdict = trust.add_reason(verdict, "T21", clash)
    ranks = [resolve.rank((rows.get(paths.origin(node[1])) or {}).get("audience")) for node in closure]
    if any(resolve.audience_blocked(resolve.rank(row["audience"]), rank) for rank in ranks):
        verdict = trust.add_reason(verdict, "T24", trust.line("T24"))
    below = [one for node in closure for one in [(units[node[0]].get(node[1]) or {}).get("verdict")]
             if one and any(code != "T25" for code in one.get("codes") or ())]   # blocked on its own, not by what it brings
    return list(trust.combine([verdict, *below])["reasons"])


@contextlib.contextmanager
def _staged_all(items, ns):
    """The files of several copies in one hidden folder beside the installs, laid out as the target holds them, so that what one of
    them includes of the others is found there: yields ({(kind, slug): (the project path of its compose file, its folder)}, the
    project path of the hidden folder). Removed when the block ends, whatever happens."""
    name = f".stage-share~{os.urandom(4).hex()}"
    top, stage = paths.ROOT / paths.CATALOGS_DIR / name, f"{paths.CATALOGS_DIR}/{name}"
    table, places = [], {}
    for kind, slug, _ in items:
        _, after, head = _place(kind, slug, ns)
        there = f"{stage}/setups" if kind == "setup" else f"{stage}/{kind}s/{slug}"
        here = f"{paths.CATALOGS_DIR}/{ns}/{head}" if kind == "setup" else after
        table.append((here, f"{stage}/{head}" if kind == "setup" else there))
        places[(kind, slug)] = (after, there, head)
    try:
        os.mkdir(top, 0o755)
        for kind, slug, copy in items:
            after, there, head = places[(kind, slug)]
            text = moves.repoint(copy.text, after, there, table)
            for path, (mode, data) in copy.files.items():
                install.safe_write(top, path, text.encode("utf-8") if path == head else data, int(mode[-3:], 8))
        yield ({key: (f"{stage}/{head}", None if key[0] == "setup" else paths.ROOT / there)
                for key, (_, there, head) in places.items()}, stage,
               {f"{stage}/{head}": f"{paths.CATALOGS_DIR}/{ns}/{head}" for _, there, head in places.values()})
    finally:
        shutil.rmtree(top, ignore_errors=True)


FLOW = ("It includes {item} in a form that cannot be written again for {catalog}. Fix: write each include as a list item, "
        "- ../../addons/<name>/compose.yml.")


def _unwritten(copy, file, kind, slug, ns, row, skip=frozenset()) -> List[str]:
    """The includes of the target's own units that `moves.repoint` left as the personal file spells them: one written in a
    flow list keeps the long path through the catalogs folder, which a catalog checked on its own does not have. A path to a unit
    of another catalog is the same at both places and is not looked at."""
    before, after, _ = _place(kind, slug, ns)
    found = []
    for target in trust.include_targets(copy.kept, file) or ():
        if (paths.unit_of(target) or ("", ""))[1] in skip:   # what goes with it, or the target has: written for it already
            continue
        old, new = (posixpath.relpath(f"/{target}", f"/{folder}") for folder in (before, after))   # absolute: no working folder
        if old != new and old in copy.text:
            unit = paths.unit_of(target)
            found.append(FLOW.format(item=(unit[1] if unit else posixpath.basename(target)).rsplit("/", 1)[-1],
                                     catalog=row["name"]))
    return found


def _own_rules(file) -> List[str]:
    """What the rules of an item of a catalog find in the personal compose file `file` (`check.problems(sharing=...)`: a
    category, a README, `unless-stopped`, a description on each service, ports on the loopback, no literal secret, its ports
    among the project's, a playground that loads alone), the lines the catalog's own check gives the copy."""
    return [message for where, message in check.problems(sharing=file) if where == file]


def _declared(text, versions) -> Optional[str]:
    """The compose file with `x-gravitee.tested` holding the set `versions` ({VARIABLE: "version"}), first under `x-gravitee`, as
    the file is indented; None when the file has no `x-gravitee` block in the form this reads, or a value is not a version."""
    lines = text.split("\n")
    start = next((i for i, one in enumerate(lines) if re.fullmatch(r"x-gravitee:\s*(#.*)?", one)), None)
    if start is None or not versions or not all(re.fullmatch(r"[A-Z][A-Z0-9_]*", name) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", str(version))
                                for name, version in versions.items()):
        return None
    below = next((one for one in lines[start + 1:] if one.strip() and not one.lstrip().startswith("#")), "")
    step = " " * (len(below) - len(below.lstrip()))
    if not step:
        return None
    names = list(versions.items())
    added = [f"{step}tested:", f'{step}{step}- {names[0][0]}: "{names[0][1]}"',   # one set: the first variable opens it, the others follow it
             *(f'{step}{step}  {name}: "{version}"' for name, version in names[1:])]
    return "\n".join(lines[:start + 1] + added + lines[start + 1:])


def _vouched(project, uid, copy) -> Copy:
    """A playground is shared once it has started well here, its seed ended without an error included: that start is kept in
    `.lab/tested.json` (tested.record). What it started on goes into the copy as its `x-gravitee.tested` when the file declares no
    set, so that a catalog never holds a playground that nobody saw run. Else the copy says what keeps it from being shared."""
    if not tested.ran(uid):
        return copy._replace(problems=[*copy.problems, "It has not run yet. Fix: start it here and let its seed end without an error."])
    if (project["playgrounds"].get(uid) or {}).get("tested") or not tested.sets(uid):
        return copy
    text = _declared(copy.text, tested.sets(uid)[-1]["versions"])
    if text is None:
        return copy._replace(problems=[*copy.problems, "Its file has no x-gravitee block to say the versions it ran on. Fix: add "
                                                      "x-gravitee.tested with the versions it started on."])
    files = dict(copy.files)
    head = next(path for path in files if path.endswith("/compose.yml"))
    files[head] = (files[head][0], text.encode("utf-8"))
    return copy._replace(files=files, text=text)


def _preflight(project, kind, uid, file, slug, ns, row, rows, copy, along=(), allowed=frozenset()):
    """Raises the refusal of an include the target does not allow, of a secret, of the name of a private
    catalog on a Public target; answers the other problems, [str] ("Blocked: ..." for what the policy gives a reason for). `along`:
    [(kind, id, slug, copy)] of what goes with the item, each checked the same way, and judged with it in one stage; `allowed`: the
    ids of what goes with it or the target has, which its includes may name."""
    problems = []
    if along:   # the rules of the standard read the files together, as the catalog will hold them: a link of one to another resolves
        merged = dict(copy.files)
        for _, _, _, other in along:
            merged.update(other.files)
        found = _rules(copy._replace(files=merged), kind, slug, ns, row)
        for each in found:
            if each.code in SECRETS:
                raise _refusal("share_secret", file=each.path)
        problems += [each.message for each in found]
    rules = None if not along else []
    for k, one, s, other in along:
        problems += [f"{one.rsplit('/', 1)[-1]}: {line}" for line in _checked(project, k, one, paths.unit_file(k, one), s, ns, row,
                                                                              rows, other, allowed, rules)]
    problems += _checked(project, kind, uid, file, slug, ns, row, rows, copy, allowed, rules)
    every = [(kind, uid, slug, copy), *along]
    # What the screen refuses is not given to Compose, nor what includes in a form the check cannot follow
    if any(trust.prescreen(c.text, f"{paths.CATALOGS_DIR}/{ns}/{_place(k, s, ns)[2]}") for k, _, s, c in every) \
            or any(_includes(project, paths.unit_file(k, one)) is None for k, one, _, _ in every):
        return list(dict.fromkeys(problems))
    with _staged_all([(k, s, c) for k, _, s, c in every], ns) as (stages, top, aliases):
        for k, one, s, c in every:
            stage, folder = stages[(k, s)]
            others = [there for key, (_, there) in stages.items() if key != (k, s) and there is not None]
            try:
                lines = _judged(project, k, one, s, ns, row, c, stage, folder, aliases, others)
            except (compose.ComposeError, ValueError) as error:   # Compose's last line, or an answer that is not JSON
                lines = [f"Does not load with the platform: {_scrub(str(error), top, k, s)}"]
                problems += lines if (k, s) == (kind, slug) else [f"{one.rsplit('/', 1)[-1]}: {line}" for line in lines]
                continue
            problems += [f"Blocked: {reason}" if (k, s) == (kind, slug) else f"{one.rsplit('/', 1)[-1]}: Blocked: {reason}"
                         for reason in lines]
    return list(dict.fromkeys(problems))


def _checked(project, kind, uid, file, slug, ns, row, rows, copy, allowed=frozenset(), found=None):
    """The checks of one item of a share that need no Compose: its includes, a secret, a private catalog named on a Public target,
    the rules of a catalog's item (`found`: given when they were read of all the files together), what the screen refuses."""
    included = _includes(project, file)
    problems = list(copy.problems)
    if included is None:
        problems.append("It includes in a form the check cannot follow. Fix: write each include as a list item, "
                        "- ../../addons/<name>/compose.yml.")
    else:
        outside = _outside(row, ns, [one for one in included[0] if one[1] not in allowed], rows)
        if outside:
            raise _outside_refusal(outside, row)
        problems += [f"Includes {name}, which is not an item of a catalog." for name in included[1]]
        if paths.origin(uid) == LOCAL:
            problems += _unwritten(copy, file, kind, slug, ns, row, allowed)
    leak = _leaked(copy, kind, slug)
    if leak:
        raise leak
    found = _rules(copy, kind, slug, ns, row) if found is None else found
    for each in found:
        if each.code in SECRETS:
            raise _refusal("share_secret", file=_shown(each.path, kind, slug))
    if row["audience"] == "public":
        private = _named_private(copy, ns, kind, slug, rows)
        if private:
            raise private
    problems += [each.message for each in found]
    # An include of what goes with it, or the target has, is no part of this machine left behind
    going = {_here(*paths.unit_of(target)) for target in (trust.include_targets(copy.kept, file) or ())
             if paths.unit_of(target) and paths.unit_of(target)[1] in allowed}
    problems += [line for line in _own_rules(file)
                 if not any(line.startswith(f"includes {where}") for where in going)]
    head = _place(kind, slug, ns)[2]
    problems += [f"Blocked: {reason}" for reason in trust.prescreen(copy.text, f"{paths.CATALOGS_DIR}/{ns}/{head}")]
    return problems


# ---------------------------------------------------------------------------------------------------------------------
# A repository gets a branch
# ---------------------------------------------------------------------------------------------------------------------

def _identity() -> Dict[str, str]:
    """{name, email} of the person, from their Git configuration, else no_identity."""
    found = {}
    for key in ("name", "email"):
        done = gitx.run(["config", "--get", f"user.{key}"], cwd=str(paths.ROOT), timeout=15)
        found[key] = done.out.decode("utf-8", "replace").strip().replace("\n", " ") if done.code == 0 else ""
    if not (found["name"] and found["email"]):
        raise _refusal("no_identity")
    return found


def _commit(store, base, files, subject, who) -> str:
    """The commit of `files` on top of `base`, built in the bare store with plumbing and an index of its own: no
    working tree, no hook, the person as author and committer, `base` as the only parent, the subject as the whole message."""
    scratch = tempfile.mkdtemp(prefix="gravitee-lab-share-")
    env = {"GIT_INDEX_FILE": os.path.join(scratch, "index"), "GIT_AUTHOR_NAME": who["name"],
           "GIT_AUTHOR_EMAIL": who["email"], "GIT_COMMITTER_NAME": who["name"], "GIT_COMMITTER_EMAIL": who["email"]}
    git = ["-c", "commit.gpgsign=false", "--git-dir", str(store)]

    def run(args, **how):
        return gitx.require_ok(gitx.run([*git, *args], env=env, **how)).out.decode("utf-8", "replace").strip()
    try:
        run(["read-tree", base])
        lines = "".join(f"{mode} {run(['hash-object', '-w', '--no-filters', '--stdin'], input=data)}\t{path}\n"
                        for path, (mode, data) in sorted(files.items()))
        run(["update-index", "--add", "--index-info"], input=lines)
        return run(["commit-tree", run(["write-tree"]), "-p", base, "-m", subject])
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _branch(address, slug) -> str:
    """`lab/<name>-<yyyymmdd>`, with -2, -3 when the repository already has it."""
    start = f"lab/{slug}-{_today()}"
    done = gitx.run(["ls-remote", "--heads", "--", address.url, f"refs/heads/{start}*"], timeout=LIST_WAIT,
                    env=gitx.address_env(address))
    gitx.require_ok(done, address)
    taken = {line.partition("\t")[2][len("refs/heads/"):] for line in done.out.decode("utf-8", "replace").splitlines()}
    return next(name for name in (start, *(f"{start}-{number}" for number in range(2, 1000))) if name not in taken)


def _push(address, store, commit, branch, name):
    """The commit pushed to the new branch, never forced. A host that refuses the sign-in or the permission is
    `no_write_access`; one that refuses the branch is `push_rejected`; the network's failures keep their own line."""
    done = gitx.run(["--git-dir", str(store), "push", "--", address.url, f"{commit}:refs/heads/{branch}"], timeout=PUSH_WAIT,
                    env=gitx.address_env(address))
    if done.code == 0:
        return
    error = gitx.classify(done.err, address)
    if error.code in ("no_access", "ssh_denied", "not_found"):
        raise _refusal("no_write_access", name=name)
    if error.code == "git_failed":
        raise CatalogError("push_rejected", LINES["push_rejected"][1].format(host=address.host or address.display),
                           LINES["push_rejected"][0], output=gitx.redact(done.err).strip()[-2000:])
    raise error


def _pull_request(address, branch) -> Optional[str]:
    """The address that opens a pull request for the branch, built from the catalog's own address; None for a host the Lab does
    not know (the dialog then gives the branch only)."""
    if address.kind != "git" or not address.repo:
        return None
    if address.host == "github.com":
        return f"https://github.com/{address.repo}/compare/{branch}?expand=1"
    if address.host == "gitlab.com":
        return (f"https://gitlab.com/{address.repo}/-/merge_requests/new?merge_request[source_branch]="
                f"{quote(branch, safe='')}")
    return None


def _needed(project, items) -> int:
    """The `lab` that older Gravitee Labs need for the units shared (`items`, [(kind, uid)]): the highest level among the
    items of the contract they use (`check.needed_lab`), 1 when this Lab cannot say."""
    raw = projects.loaded()[1].get("raw") or {}
    units = [(k, uid, project[f"{k}s"][uid]) for k, uid in items if uid in project[f"{k}s"]]
    return check.needed_lab(units, raw, projects.loaded()[1]) or 1


def _raised(text, needed) -> Optional[str]:
    """The text of a catalog.yml whose legacy `lab` is raised to `needed` when it is lower (`manifest.with_legacy`, the line
    alone), None when nothing changes: a share never lowers it, and never adds the lines a catalog does without."""
    have = registry.scan(text.encode("utf-8")).get("lab")
    return manifest.with_legacy(text, needed) if isinstance(have, int) and have < needed else None


def _publish(ns, catalog, name, kind, slug, address, branch, who, copy, also=(), needed=1):
    """The target's tip is fetched (the network, no lock), the commit is built on it, the branch is
    pushed. The tip may have the item by now, or one of what goes with it (`also`): `share_exists`. The legacy `lab` of the
    catalog is raised in the same commit when the item needs more (`needed`), so that an older Lab refuses it cleanly."""
    store = registry.store_dir(ns)
    base = bare.fetch(address, branch, store, stall=30, cap=FETCH_CAP, releases=False)
    for k, s in [(kind, slug), *also]:
        if bare.read(store, base, _place(k, s, ns)[2]) is not None:
            raise _refusal("share_exists", name=name if (k, s) == (kind, slug) else s, catalog=catalog)
    yml = bare.read(store, base, manifest.FILE)
    raised = _raised(yml.decode("utf-8", "replace"), needed) if yml else None
    if raised is not None:
        copy = copy._replace(files={**copy.files, manifest.FILE: ("100644", raised.encode("utf-8"))})
    commit = _commit(store, base, copy.files, f"Add {name}", who)
    where = _branch(address, slug)
    try:
        _push(address, store, commit, where, catalog)
    except CatalogError as error:
        if error.code == "no_write_access":   # what the host said now is believed over what the probe said before
            _PROBES[(address.url, branch)] = (_clock(), "no")
        raise
    return {"branch": where, "commit": commit[:7], "repository": address.repo or address.display,
            "pull_request": _pull_request(address, where)}


# ---------------------------------------------------------------------------------------------------------------------
# A folder gets a copy
# ---------------------------------------------------------------------------------------------------------------------

def _copy_into(ns, catalog, name, kind, slug, copy):
    """Writes the files into the folder of catalog `ns`, beside the place of the item and under a hidden name, and renames them
    to it: the author never sees half an item. Nothing is overwritten (`share_exists`), every folder on the way is looked at
    without following a link, and what is made is theirs to edit (not read only)."""
    root, suffix = Path(os.path.realpath(registry.install_dir(ns))), ".yml" if kind == "setup" else ""
    hidden, final = f"{kind}s/.share-{slug}{suffix}", f"{kind}s/{slug}{suffix}"
    if os.path.lexists(root / final):
        raise _refusal("share_exists", name=name, catalog=catalog)
    _clear(root / hidden)
    try:
        for path, (mode, data) in sorted(copy.files.items()):
            relative = hidden if kind == "setup" else f"{hidden}/{path.split('/', 2)[2]}"
            install.safe_write(root, relative, data, int(mode[-3:], 8))
            os.chmod(root / relative, int(mode[-3:], 8))
        for here, folders, _ in os.walk(root / hidden) if kind != "setup" else ():
            for one in folders:
                os.chmod(os.path.join(here, one), 0o755)
        os.rename(root / hidden, root / final)
    except BaseException:
        _clear(root / hidden)
        raise


def _clear(path):
    """Removes a file or a folder that the Lab made and left, never following a link."""
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def sweep() -> List[str]:
    """Swept at start, for the folder catalogs: the half copies `_copy_into` leaves in a person's folder when the
    Lab is stopped between writing and renaming (`<folder>/<kind>s/.share-<name>`, hidden, never an item) that are older than a
    day are removed, without following a link. `install.sweep` looks inside the installs of the Lab, which a folder is not.
    Answers the places removed, `<catalog id>/<kind>s/<name>`."""
    done = []
    for ns, row in registry.inputs().items():
        if ns == LOCAL or row["kind"] != "folder" or row["state"] != "linked":
            continue
        root = os.path.realpath(registry.install_dir(ns))
        for kind in paths.KINDS:
            base = os.path.join(root, f"{kind}s")
            if os.path.islink(base):
                continue
            try:
                names = sorted(os.listdir(base))
            except OSError:
                continue
            for name in names:
                try:
                    old = time.time() - os.lstat(os.path.join(base, name)).st_mtime > SWEEP_AGE
                except OSError:
                    continue
                if old and name.startswith(".share-"):
                    _clear(os.path.join(base, name))
                    done.append(f"{ns}/{kind}s/{name}")
    return done


# ---------------------------------------------------------------------------------------------------------------------
# share
# ---------------------------------------------------------------------------------------------------------------------

def _machine_only(uid) -> bool:
    """Whether an item is only on this machine: Personal's, or a playground's imported from a file."""
    ns = paths.origin(uid)
    return ns == LOCAL or registry.imported(ns)


def _bundle(project, kind, uid, to):
    """What a share of `uid` to `to` takes along: (members [(kind, id, slug, source)], the items it includes that are only on this
    machine and that the target does not have, which go with it in the same branch or copy; reused [(kind, id, slug)], those the
    target has already (a share of before): the target's is included instead, and what they include is the target's business)."""
    units = {"addon": project["addons"], "setup": project["setups"], "playground": project["playgrounds"]}
    have = registry.units_of(to)
    members, reused, seen, todo = [], [], {(kind, uid)}, [(kind, uid)]
    while todo:
        for node in resolve._direct(units, todo.pop(0)):
            if node in seen or not _machine_only(node[1]):
                continue
            seen.add(node)
            slug = node[1].rsplit("/", 1)[-1]
            if slug in (have.get(node[0]) or ()):
                reused.append((node[0], node[1], slug))
                continue
            members.append((node[0], node[1], slug, paths.origin(node[1])))
            todo.append(node)
    return members, reused


def _target(kind, slug, ns):
    """The place of an item in catalog `ns`, relative to the project: its folder, or the file of a setup."""
    return f"{paths.CATALOGS_DIR}/{ns}/setups/{slug}.yml" if kind == "setup" else f"{paths.CATALOGS_DIR}/{ns}/{kind}s/{slug}"


def _here(kind, uid):
    return paths.unit_file(kind, uid) if kind == "setup" else paths.unit_folder(kind, uid)


def unmark(text):
    """A compose text without `x-gravitee.copy_of`: whose copy an item of this machine is, which says nothing true elsewhere. Every
    copy_of of the block goes, and a block that takes keys from elsewhere says none (meta.drop)."""
    return block.drop(text, "copy_of", override=True)


CHAIN = 8   # the copies of copies a private origin is looked for through, at most: a longer or a looping chain says nothing


def private_source(text, folder=None, _seen=()):
    """(key, name, original) of the private catalog what an item holds comes from, else None. Its label says it
    (`x-gravitee.private_from`, written with the original when it was copied, moved, shared, exported or imported). An item made
    before there was a label says only whose copy it is (`x-gravitee.copy_of`), which may be a copy itself: that chain is followed
    through the items this machine has, to the first item of a private catalog of Git. A copy of what this machine does not have,
    or cannot read, and a chain too long or that loops, are taken as private: nothing says otherwise. `folder`: where the file is,
    which Compose reads it in (_said)."""
    return _private_said(_said(text or "", folder), _seen)


def _private_said(said, _seen=()):
    """private_source of an item whose x-gravitee says (private_from, copy_of) `said`, or is the block `said` itself."""
    if isinstance(said, dict):
        said = ((str(said["private_from"]) if said.get("private_from") else None), (str(said["copy_of"]) if said.get("copy_of") else None))
    said, uid = said
    if said:
        name = said[:NAME_LIMIT]
        return name, name, None
    if not uid:
        return None
    try:
        ns = paths.origin(uid)
    except paths.NotFound:
        ns = None
    if not ns or ns == LOCAL or uid in _seen or len(_seen) >= CHAIN:
        return (ns or "unknown"), _catalog_name(ns) if ns and ns != LOCAL else "an item of unknown origin", uid
    entry = registry.entry(ns)
    if entry is not None and entry.get("kind") == "git" and registry.lock_of(ns).get("audience") != "public":
        return ns, _catalog_name(ns), uid
    # The original as the project read it, which held, linked or special files never reach: no other folder is read for it
    read = projects.loaded()[1].get("raw") or {}
    said = None
    for kind in paths.KINDS if entry is not None else ():
        with contextlib.suppress(paths.NotFound, ValueError):
            said = said or (read.get(paths.unit_file(kind, uid)) or {}).get("x-gravitee")
    if not isinstance(said, dict):
        return ns, _catalog_name(ns), uid   # the original is not on this machine, or the Lab does not read it
    return _private_said(said, (*_seen, uid))


def _said(text, folder=None):
    """(private_from, copy_of) of an item's x-gravitee block as Compose reads it in `folder`, where the item is, the reader the Lab
    reads every item with; as the Lab's own reading of its own writing (meta.py) only when Compose cannot read the file there.
    Without a folder it is read where nothing is, as a file that includes nothing."""
    try:
        found = compose.x_gravitee(text, folder or compose.scratch())
    except compose.ComposeError:
        return block.value(text, "private_from"), block.value(text, "copy_of")
    found = found if isinstance(found, dict) else {}
    return (str(found["private_from"]) if found.get("private_from") else None), (str(found["copy_of"]) if found.get("copy_of") else None)


def private_of(text, uid, folder=None):
    """The name of the private catalog what the item `uid` (its compose `text`, in `folder`) holds comes from: its label or its
    chain, else its own catalog when that is a private catalog of Git; None when it is nobody's private content."""
    source = private_source(text, folder)
    if source:
        return source[1]
    try:
        ns = paths.origin(uid)
    except paths.NotFound:
        return None
    entry = registry.entry(ns) if ns != LOCAL else None
    return _catalog_name(ns) if entry and entry.get("kind") == "git" and registry.lock_of(ns).get("audience") != "public" else None


def _catalog_name(ns):
    return (registry.inputs().get(ns) or {}).get("name") or registry.lock_of(ns).get("name") or ns


def labelled(text, name):
    """The compose text whose x-gravitee block says the private catalog what it holds comes from (`private_from`), or says none."""
    text = block.drop(text, "private_from")
    return (block.put(text, "private_from", name) or text) if name else text


def mark(text, uid):
    """The compose text whose x-gravitee block says it is a copy of `uid` (copy_of); None when it has no block."""
    return block.put(text, "copy_of", uid)


def _unmarked(copy, kind, slug, private=None, name=None):
    """The copy without `x-gravitee.copy_of`: whose copy an item of this machine is, which an item of a catalog never says; and
    with `private`, the name of the private catalog what it holds comes from, as its label, which then goes with it. The change is
    made to the file as it is (`kept`) and checked as Compose reads it there (`rewritten`), also when the Lab's reader sees nothing
    to change, then written for its new place as `text` was (`place`): else the refusal naming the item (`name`)."""
    edited = labelled(unmark(copy.kept), private) if private else unmark(copy.kept)
    rewritten(copy.kept, edited, {"copy_of": None, **({"private_from": private} if private else {})}, name or slug,
              "its private label and whose copy it is" if private else "whose copy it is", copy.folder)
    if edited == copy.kept:
        return copy
    text = moves.repoint(edited, *copy.place) if copy.place else edited
    head = next(path for path in copy.files if path == f"setups/{slug}.yml" or path == f"{kind}s/{slug}/compose.yml")
    files = dict(copy.files)
    files[head] = (files[head][0], text.encode("utf-8"))
    return copy._replace(files=files, text=text)


def _unmarked_or_said(copy, kind, slug, private, name):
    """_unmarked for a share: a file Compose cannot read as it is is a problem of the copy, said with the others, which stops the
    share as they do."""
    try:
        return _unmarked(copy, kind, slug, private, name)
    except CatalogError as error:
        if error.code != "label_unread":
            raise
        return copy._replace(problems=[*copy.problems, error.message])


NOT_WRITTEN = ("{name}: the Lab cannot write {what} in this file. Fix: write its x-gravitee as one block of plain key: value "
               "lines, in a file of one YAML document (in a catalog, ask its author to), then try again.")
NOT_READ = "{name}: its compose file cannot be read ({error}). Fix: correct it so that docker compose config reads it, then try again."


def _relative(error):
    """What Compose said, the paths of the Lab's folder written from it."""
    text = str(error)
    for root in sorted({str(paths.ROOT), os.path.realpath(paths.ROOT)}, key=len, reverse=True):
        text = text.replace(root + "/", "")
    return text


UNIT_FILE = re.compile(r"\.lab/catalogs/[^\s:'\"]+?(?:/compose\.yml|/setups/[^/\s:'\"]+\.yml)")


def _mode(file):
    try:
        return os.lstat(file).st_mode
    except OSError:
        return 0


def _unread(name, error):
    """The refusal of an item whose file, or a file it includes, Compose cannot read: named by the item whose file Compose names
    (an add-on it includes, say), else by `name`."""
    cause, culprit = compose.cause(error), name
    for found in UNIT_FILE.finditer(_relative(error)):
        unit = paths.unit_of(found.group())
        if unit:   # its name as its own block says it, else its folder's
            file = paths.ROOT / found.group()
            data = (_read(file, bare.TEXT_FILE + 1) if stat.S_ISREG(_mode(file)) else None) or b""
            culprit = block.value(data.decode("utf-8", "replace"), "name") or unit[1].rsplit("/", 1)[-1]
            break
    return CatalogError("label_unread", NOT_READ.format(name=culprit, error=cause), 409)


def rewritten(before, after, changed, name, what="its private label and whose copy it is", folder=None):
    """What the Lab wrote in a compose text (`after`, from `before`), checked as the Lab reads it, with Compose in `folder`, where
    the file is: its x-gravitee is the one of `before` with the keys of `changed` set to their values (None: gone, or empty) and
    nothing else, and it still loads. Else the refusal that names the item and says why, decided on what is true now: a file
    Compose cannot read as it is (`label_unread`, _unread), or a label or a copy_of the Lab could not write, or whose writing broke
    a file that reads (`label_failed`, NOT_WRITTEN). A reading remembered (compose.x_gravitee) says a text was readable, not that
    it is: when what the Lab wrote cannot be read, the file as it is is read again before the Lab takes the fault."""
    where = folder or compose.scratch()
    try:
        old = compose.x_gravitee(before, where)
    except compose.ComposeError as error:
        raise _unread(name, error) from None
    try:
        new = old if after == before else compose.x_gravitee(after, where)
    except compose.ComposeError:
        try:
            compose.x_gravitee(before, where, fresh=True)
        except compose.ComposeError as error:   # the file itself cannot be read now: not what the Lab wrote
            raise _unread(name, error) from None
        raise CatalogError("label_failed", NOT_WRITTEN.format(name=name, what=what), 409) from None   # what the Lab wrote broke it
    old, new = (old if isinstance(old, dict) else {}), ({} if new is None else new)   # no block: nothing it says
    wanted = {**{key: value for key, value in old.items() if key not in changed},
              **{key: value for key, value in changed.items() if value is not None}}
    if not isinstance(new, dict) or {key: value for key, value in new.items() if value != "" or key not in changed} != wanted:
        raise CatalogError("label_failed", NOT_WRITTEN.format(name=name, what=what), 409)


IMAGE_SETTINGS = ("_VERSION", "_REGISTRY", "_PULL_POLICY")   # what any item may read under any name (T20): never renamed
RENAMES = f"{paths.LAB_DIR}/renames.json"   # {"kind:id in the catalog": {old: new}}: the settings a share renamed, until the item is used


def _prefix(ns) -> str:
    return ns.upper().replace("-", "_") + "_"


def _reads(kind, slug, copy):
    """The settings a copy reads: what its compose file refers to, and what its seed files do."""
    names = {key for key, _, _ in compose.references(copy.text)}
    seeds = [data.decode("utf-8", "replace") for path, (_, data) in sorted(copy.files.items())
             if path.startswith(f"{kind}s/{slug}/seed/") and b"\0" not in data[:8192]]
    return names | set(trust.seed_reads(seeds))


def _renames(items, to) -> Dict[str, str]:
    """{old: new} of the settings the items of a share read ([(kind, slug, copy)]) that are their own and not named for `to`: an item of a catalog reads
    the settings named for its catalog (T20), and one that was the person's, or came from another catalog, is named for where it
    was (GRAVITEE_INTERNAL_GKO_LOG_LEVEL). A setting is an item's own when the name of one of the items is in it (GKO): it then
    starts there, after the prefix of `to` (TEAM_DEMOS_GKO_LOG_LEVEL). The platform's settings, the shared ones, the image's
    (*_VERSION, *_REGISTRY, *_PULL_POLICY) and a name that holds no item's are left as they are: the check says it if it is wrong."""
    inputs = projects.loaded()[1]
    core = {one["key"] for one in (inputs.get("variables") or {}).get(paths.CORE_FILE, [])}
    shared = {name for names in (inputs.get("shared_settings") or {}).values() for name in names or ()}
    prefix = _prefix(to)
    tokens = sorted({slug.upper().replace("-", "_") for _, slug, _ in items}, key=len, reverse=True)
    found = {}
    for kind, slug, copy in items:
        for name in sorted(_reads(kind, slug, copy)):
            if name.startswith(prefix) or name.endswith(IMAGE_SETTINGS) or name in core or name in shared:
                continue
            starts = [hit.start(1) for token in tokens for hit in re.finditer(rf"(?:^|_)({re.escape(token)})_", name)]
            if starts:
                found[name] = prefix + name[min(starts):]
    return {old: new for old, new in found.items() if old != new}


def _renamed(copy, kind, slug, mapping):
    """The copy with the settings of `mapping` renamed where they are settings: the references of its compose file (${NAME},
    $NAME), those of its seed that read a setting by its own name, and its guides, README and .env.example. A name the compose file
    gives a container (an environment key, outside a reference) is what the program in it, and the seed of the service, read: it
    stays, and the reference that fills it is the one renamed. Another file is what a container reads, not a setting."""
    if not mapping:
        return copy
    names = "|".join(map(re.escape, sorted(mapping, key=len, reverse=True)))
    word = re.compile(rf"(?<![A-Za-z0-9_])({names})(?![A-Za-z0-9_])")
    reference = re.compile(rf"(?<!\$)(\$\{{?)({names})(?![A-Za-z0-9_])")   # $$ is a dollar sign, not a reference
    head = f"setups/{slug}.yml" if kind == "setup" else f"{kind}s/{slug}/compose.yml"
    compose_text = _text_of(copy.files.get(head)) or ""
    given = set(word.findall(reference.sub("", compose_text)))
    files = {}
    for path, (mode, data) in copy.files.items():
        text, base = _text_of((mode, data)), path.rsplit("/", 1)[-1]
        if text is None:
            files[path] = (mode, data)
            continue
        if path == head:
            text = reference.sub(lambda hit: hit.group(1) + mapping[hit.group(2)], text)
        elif path.startswith(f"{kind}s/{slug}/seed/"):
            text = reference.sub(lambda hit: hit.group(1) + (hit.group(2) if hit.group(2) in given else mapping[hit.group(2)]), text)
        elif base.lower().endswith(".md") or base == ".env.example":
            text = word.sub(lambda hit: mapping[hit.group(1)], text)
        files[path] = (mode, text.encode("utf-8"))
    return copy._replace(files=files, text=files[head][1].decode("utf-8") if head in files else copy.text)


def _text_of(entry):
    """The text of a file of a copy, None when it is not UTF-8 text."""
    if not entry or b"\0" in entry[1][:8192]:
        return None
    try:
        return entry[1].decode("utf-8")
    except ValueError:
        return None


def _remember_renames(to, items, mapping):
    """Keeps on this machine what a share renamed, for each item as the catalog will hold it, until it is used (`moved`)."""
    if not mapping:
        return
    path = paths.ROOT / RENAMES
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = {}
    for kind, _, slug in items:
        kept[f"{kind}:{_uid(to, slug)}"] = dict(mapping)
    paths.write_atomic(path, json.dumps(kept, indent=2, sort_keys=True) + "\n")


def _rename_settings(kind, uid):
    """The person's values under the names the catalog's item reads: what a share renamed is renamed in its .env, then forgotten."""
    path = paths.ROOT / RENAMES
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    mapping = kept.pop(f"{kind}:{uid}", None)
    env = paths.unit_env(kind, uid)
    if isinstance(mapping, dict) and mapping and env and (paths.ROOT / env).is_file():
        file = paths.ROOT / env
        text = file.read_text(encoding="utf-8")
        for old, new in mapping.items():
            text = re.sub(rf"(?m)^(\s*(?:export\s+)?){re.escape(old)}(?==)", lambda hit, new=new: hit.group(1) + new, text)
        paths.write_atomic(file, text, mode=stat.S_IMODE(file.stat().st_mode))
    paths.write_atomic(path, json.dumps(kept, indent=2, sort_keys=True) + "\n")


def _raise_lab(ns, needed):
    """The legacy `lab` of the catalog.yml of folder catalog `ns`, raised to what a shared item needs (`_raised`), the file
    written whole. A file that is not a plain one, or not text, is left as it is: `catalog.py check` says what it lacks."""
    file = Path(os.path.realpath(registry.install_dir(ns))) / manifest.FILE
    data = _read(file)
    if data is None:
        return
    try:
        raised = _raised(data.decode("utf-8"), needed)
    except UnicodeDecodeError:
        return
    if raised is not None:
        paths.write_atomic(file, raised)


def share(project: dict, kind: str, uid: str, to: str, check_only: bool = False) -> dict:
    """Shares the personal item `uid` to catalog `to`. With `check_only`, what it would do and what keeps it: ShareCheck
    {destinations, to, audience, problems, changes, identity}, nothing written. Else the share: a repository gets a branch,
    ShareDone {branch, commit, repository, pull_request}; a folder gets a copy, {copied}. The item stays as it is, and runs.
    Refused with a CatalogError (the catalogue of errors): `contribute_none`, `no_write_access`, `share_exists`, `share_includes`,
    `share_secret`, `share_private`, `no_identity`, `push_rejected`; the other problems of the copy answer in `problems` and,
    when a share is asked, refuse it with the first (a ValueError). NotFound for a catalog this machine does not have."""
    file, name, slug = _own(project, kind, uid)
    rows = registry.inputs()
    entry, row = _catalog(to, rows)
    if not _takes(to, row):
        raise _refusal("contribute_none", name=row["name"])
    if slug in (registry.units_of(to).get(kind) or ()):
        raise _refusal("share_exists", name=name, catalog=row["name"])
    address = branch = who = None
    if row["kind"] == "git":
        address, branch = _address(entry, registry.lock_of(to))
        if _probe(to, address, branch) == "no":
            raise _refusal("no_write_access", name=row["name"])
        who = _identity()
    members, reused = _bundle(project, kind, uid, to)
    moved = [(_here(k, one), _target(k, s, to)) for k, one, s, _ in members] + [(_here(k, one), _target(k, s, to)) for k, one, s in reused]
    allowed = {one for _, one, _, _ in members} | {one for _, one, _ in reused}
    units = {"addon": project["addons"], "setup": project["setups"], "playground": project["playgrounds"]}
    named = lambda k, one: (units[k].get(one) or {}).get("name") or one.rsplit("/", 1)[-1]   # noqa: E731
    with registry.flock():
        copy = collect(kind, slug, to, moved=moved)
        along = [(k, one, s, collect(k, s, to, source, moved=moved)) for k, one, s, source in members]
        for k, one, _, other in [(kind, uid, slug, copy), *along] if row["audience"] == "public" else ():
            source = private_source(other.kept, other.folder)   # the copy is the private catalog's content, whatever it names
            if source:
                raise _refusal("share_private_copy", name=named(k, one), private=source[1])
        # Whose copy it is goes with a copy of a private catalog's content into another catalog (not Public: refused above), so that
        # it is still that catalog's content there; else an item of a catalog says nothing of it
        # What is private content says so in the catalog it goes to, unless it goes back to that very catalog
        private = {(k, s): private_of(other.kept, one, other.folder) for k, one, s, other in [(kind, uid, slug, copy), *along]}
        own = row["name"]
        copy = _unmarked_or_said(copy, kind, slug, None if private[(kind, slug)] == own else private[(kind, slug)], name)
        along = [(k, one, s, _unmarked_or_said(other, k, s, None if private[(k, s)] == own else private[(k, s)], named(k, one)))
                 for k, one, s, other in along]
        renames = _renames([(kind, slug, copy), *[(k, s, c) for k, _, s, c in along]], to)
        copy = _renamed(copy, kind, slug, renames)
        along = [(k, one, s, _renamed(c, k, s, renames)) for k, one, s, c in along]
        if kind == "playground":
            copy = _vouched(project, uid, copy)
        problems = _preflight(project, kind, uid, file, slug, to, row, rows, copy, along, allowed)
    answer = {"destinations": destinations(project, kind, uid), "to": to, "audience": row["audience"], "problems": problems,
              "changes": [_shown(_place(kind, slug, to)[2], kind, slug)] if copy.text != copy.kept else [],
              "identity": who["email"] if who and row["audience"] == "public" else None,
              "carries": [named(k, one) for k, one, _, _ in members], "reuses": [named(k, one) for k, one, _ in reused],
              "renames": [{"from": old, "to": new} for old, new in sorted(renames.items())]}
    if check_only:
        return answer
    if problems:
        raise ValueError(problems[0])
    shared = [(kind, uid, slug), *[(k, one, s) for k, one, s, _ in along]]
    needed = _needed(project, [(k, one) for k, one, _ in shared])
    if address is not None:
        files = dict(copy.files)
        for _, _, _, other in along:
            files.update(other.files)
        done = _publish(to, row["name"], name, kind, slug, address, branch, who, copy._replace(files=files),
                        also=[(k, s) for k, _, s, _ in along], needed=needed)
        _remember_renames(to, shared, renames)
        return done
    with registry.flock():
        for k, one, s, other in along:
            _copy_into(to, row["name"], named(k, one), k, s, other)
        _copy_into(to, row["name"], name, kind, slug, copy)
        _raise_lab(to, needed)
    _remember_renames(to, shared, renames)
    return {"copied": row["name"]}


# ---------------------------------------------------------------------------------------------------------------------
# The end of a share: the catalog has the item
# ---------------------------------------------------------------------------------------------------------------------

def _machine(folder) -> List[str]:
    """The machine's entries of a folder with no manifest (`install._scan`): `.env` and `.env.*` but `.env.example`, a
    `.state` or `secrets` folder, a key, as the top-most paths that hold nothing else."""
    return install._scan(folder, None)[1]


def _kept(kind, slug, rel) -> str:
    return os.path.join(str(paths.ROOT), moves.MIGRATION, "kept", "local", f"{kind}s", slug, rel)


def _stuck(kind, old, new, tops) -> ValueError:
    """The refusal of a move that left entries where they were: they are named, whole, so that the person can move them."""
    shown = [f"{paths.unit_folder(kind, old)}/{top}" for top in tops]
    return ValueError(f"{', '.join(shown)} could not be moved to {paths.unit_folder(kind, new)}. "
                      f"Move {'it' if len(shown) == 1 else 'them'} by hand, then try again.")


def _meet(kind, old, top, here, there) -> str:
    """The catalog's item has the entry `top` too, and moves lost: its answer is "conflict". Two folders are one folder, as
    `moves.merge` makes them (`apps/web` with the settings of an app, in an item whose `apps/web` holds the app): what the
    personal item's holds goes in beside what is there, and a file the catalog's item has wins and the personal one is kept
    aside. A `.state` or a `secrets` folder, at any depth, is never mixed with another (records are not meant to be), nor is
    anything that is not a folder on both sides: it is kept aside whole. Answers like `moves.relocate` ("moved" when all of it has a place now)."""
    kept = _kept(kind, old.split("/", 1)[1], top)
    if os.path.basename(top) in moves.WHOLE or not (os.path.isdir(here) and os.path.isdir(there) and not os.path.islink(here)
                                                    and not os.path.islink(there)):
        return moves.relocate(here, kept, "use-catalog-conflict")
    stayed = False
    for found in moves.merge(here, there, "use-catalog", everywhere=True):
        answer = found["result"]
        if answer == "conflict":
            answer = moves.relocate(os.path.join(here, found["path"]), os.path.join(kept, found["path"]),
                                    "use-catalog-conflict")
        stayed = stayed or answer not in ("moved", "copied", "gone")
    return "stayed" if stayed else "moved"


def _there(kind, uid) -> bool:
    """Whether the unit `uid` of a catalog is on disk."""
    return (paths.ROOT / paths.unit_file(kind, uid)).is_file()


def moved(kind: str, old: str, new: str) -> None:
    """A personal item becomes a catalog's: its settings and records move to the same place in the folder of `new`
    (`.env`, `.state` with the records of what it deployed, `secrets`, keys: `moves.relocate`, which never overwrites; a
    file the catalog's item already has wins and the personal one is kept in `.lab/migration/kept/`; a folder both have, such
    as `apps/web`, is merged: only the files whose place is taken are kept, `_meet`), the items of this
    machine that include it include the catalog's, and its star and its tested sets are `new`'s (`moves.rekey`). Idempotent:
    a run that stopped is run again. `old` is `local/<name>`, `new` the id of a unit that is there; the files of the catalog
    are not touched but for those. An entry that stays where it is (a folder on another filesystem, one that is busy, a place
    in `kept/` that is taken too) is a ValueError that names it, raised before the star and the places that include the item
    follow: the item is as it was and the move is run again once it is done by hand."""
    if kind not in paths.KINDS or not (isinstance(old, str) and personal.OWN.fullmatch(old)):
        raise ValueError(f"Only your own {paths.NOUNS.get(kind, 'item')}s can be moved here")
    if not (isinstance(new, str) and paths.is_id(new)) or paths.personal(new):
        raise ValueError(f"{new} is not the id of an item of a catalog")
    if not _there(kind, new):
        raise ValueError(f"{new} is not an item of a catalog you have")
    stuck = []
    if kind != "setup":
        source, target = (str(paths.ROOT / paths.unit_folder(kind, one)) for one in (old, new))
        for top in _machine(source) if os.path.isdir(source) else ():
            answer = moves.relocate(os.path.join(source, top), os.path.join(target, top), "use-catalog")
            if answer == "conflict":
                answer = _meet(kind, old, top, os.path.join(source, top), os.path.join(target, top))
            if answer not in ("moved", "copied", "gone"):   # "stayed", or the place in kept/ is taken as well
                stuck.append(top)
    if stuck:
        raise _stuck(kind, old, new, stuck)
    personal.follow(*(paths.unit_folder(kind, one) or paths.unit_file(kind, one) for one in (old, new)))
    moves.rekey({f"{kind}:{old}": new})
    _rename_settings(kind, new)


def _unshared(folder, text, skip) -> List[str]:
    """The top-most entries of the personal item's folder that a share would not have taken and that are not scratch, as paths
    inside it: what the compose file names and the allow-list are the catalog's version now (`collect` reads the same), and
    `skip` (the machine's entries, which `moved` takes to the catalog's item) are not looked at. A folder that leads to a
    path the compose file names or to a machine entry is entered, not taken whole; the folder itself (`.`) is all of it."""
    covered = {*TOP, *TREES, *_named(text)}
    if "" in covered:
        return []
    found, ways = [], covered | skip

    def look(rel):
        try:
            names = sorted(os.listdir(os.path.join(folder, rel) if rel else folder))
        except OSError:
            return
        for name in names:
            path = f"{rel}/{name}" if rel else name
            if path in skip or any(path == one or path.startswith(one + "/") for one in covered):
                continue
            if _scratch(name) or (name in SCRATCH_FOLDERS and name not in VERSIONED):
                continue
            try:
                is_dir = stat.S_ISDIR(os.lstat(os.path.join(folder, path)).st_mode)
            except OSError:
                continue
            if is_dir and any(one.startswith(path + "/") for one in ways):
                look(path)   # on the way to a path the compose file names, or to a setting
            else:
                found.append(path)
    look("")
    return found


def _set_aside(kind, uid, slug) -> None:
    """What the personal item's folder holds that is neither the catalog's version nor a setting (`data/`, notes, a file the
    compose file does not name) is moved to `.lab/migration/kept/local/<kind>s/<name>/`, where the settings that lost their
    place went: deleting the folder then takes nothing the person made. An entry that cannot be moved there (its place is
    taken, another filesystem, busy) is a ValueError that names it, before anything is deleted."""
    if kind == "setup":
        return
    folder = str(paths.ROOT / paths.unit_folder(kind, uid))
    if not os.path.isdir(folder):
        return
    text = _read(os.path.join(folder, "compose.yml"), bare.TEXT_FILE + 1)
    stuck = []
    for rel in _unshared(folder, (text or b"").decode("utf-8", "replace"), set(_machine(folder))):
        if moves.relocate(os.path.join(folder, rel), _kept(kind, slug, rel), "use-catalog-leftover") not in ("moved", "copied", "gone"):
            stuck.append(rel)
    if stuck:
        raise ValueError(f"{', '.join(f'{paths.unit_folder(kind, uid)}/{one}' for one in stuck)} could not be set aside in "
                         f"{moves.MIGRATION}/kept. Move {'it' if len(stuck) == 1 else 'them'} by hand, then try again.")


def _off(project, kind, name, *ids):
    """Refuses with `held` while any of these items is on or runs: the personal item and its twin are the same item, and one
    that is up is not replaced."""
    on = layers.current(project)
    for one in ids:
        if personal._turned_on(on, kind, one):
            verb = "Stop" if personal._running(project, kind, one) else "Turn off"
            raise CatalogError("held", f"{verb} {name} first.", 409)


def _cannot_run(unit) -> str:
    """Why the unit of a catalog cannot run on this machine (what its verdict says, the catalogs it lacks, a level this Lab does
    not read), "" when it can."""
    why = list((unit.get("blocked") or {}).get("reasons") or ())
    needs = [one.get("name") or one.get("id") for one in unit.get("needs_catalogs") or () if isinstance(one, dict)]
    if needs:
        why.append(f"Needs {', '.join(str(one) for one in needs)}")
    if unit.get("compat") == "new":
        why.append("Needs a newer Gravitee Lab")
    elif unit.get("compat") == "old":
        why.append("Written for an older Gravitee Lab")
    return "; ".join(why)


def use_catalog_version(project: dict, kind: str, uid: str) -> dict:
    """"Use the catalog version": the personal item `uid` and the unit of a catalog that is the same item (its `twin`,
    6.5) are one item twice, and this keeps the catalog's. What the personal folder holds besides the item is set aside in
    `.lab/migration/kept/`, `moved` takes the settings to the catalog's item and re-keys the star and the tested set, and the
    personal copy is deleted. Answers {"id": the catalog's id}. `held` (409) while either is on or runs. ValueError when the
    personal item has no twin or its file is gone, when the twin cannot run here (the personal copy is the one that works),
    and when anything of the folder could not be moved to where it is kept: then nothing is deleted. RuntimeError
    (`layers.WAITING`) while the old layout is being moved: an item the old on file turns on would not be seen as on."""
    layers.ready()
    file, name, slug = _own(project, kind, uid, "replaced")
    twin = ((project.get(kind + "s") or {}).get(uid) or {}).get("twin")
    there = twin.get("id") if isinstance(twin, dict) else None
    missing = ValueError(f"{name} is not in a catalog you have. Update the catalog that holds it first.")
    if not (isinstance(there, str) and paths.is_id(there)) or paths.personal(there) or not _there(kind, there):
        raise missing
    why = _cannot_run((project.get(kind + "s") or {}).get(there) or {})
    if why:
        raise ValueError(f"{name} in {twin.get('name') or there} cannot run on this machine: {why}. Your copy stays until it can.")
    with registry.flock(), projects.exclusive():
        _off(project, kind, name, uid, there)
        if not _there(kind, there):   # the catalog dropped it since
            raise missing
        try:
            _set_aside(kind, uid, slug)
            moved(kind, uid, there)
        except moves.Refused as error:   # a move the Lab will not make: the item stays as it is
            raise ValueError(str(error)) from None
        target = paths.ROOT / file
        try:
            target.unlink() if kind == "setup" else shutil.rmtree(target.parent)
        except OSError:
            raise RuntimeError(f"Some files of {name} could not be deleted. Remove them by hand.") from None
    return {"id": there}
