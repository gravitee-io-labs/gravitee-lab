"""The project as its Compose files describe it: the platform's groups, the add-ons, setups and playgrounds of its
catalogs and of this machine, every service and console, and where each playground can be deployed. Nothing here knows a
product but the one each folder of a seed fills (SEEDED): it all comes from x-gravitee blocks, labels, includes and
mounts.

A unit of a catalog is read as hostile input (the resolver). `gather` screens its text before Compose is ever
asked, reads it with a time limit, resolves it alone with the core (so one broken unit never blanks the project) and
keeps what it learned; `build` judges each unit with the trust policy, decides who keeps a name, and ships what a
person sees: where a unit comes from, whether it can start, which catalogs it lacks. The units of this machine are the
person's own: they are read together and never judged. With no `catalogs.default.json` the layout is the one before
catalogs, every unit is read together as it always was, and nothing here changes."""

import contextlib
import copy
import datetime
import errno
import hashlib
import json
import os
import re
import stat
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from ..docker import compose, envfiles
from . import layout
from ..gravitee import frames, license, platforms
from .. import paths
from ..catalogs import registry, resolve, trust
from ..paths import CORE_FILE, ENV_FILE, KINDS, LAB_DIR, ROOT

SECRET = re.compile(r"TOKEN|SECRET|PASSWORD|LICENSE|_KEY")
LICENSE_KEY = "gravitee_license_key"   # where a Gravitee component reads its license, in its environment
LICENSE_LABEL = "io.gravitee.license"   # `required` on a service that exits without a usable license
CONSOLE_LABEL = re.compile(r"io\.gravitee\.console\.([a-z0-9-]+)\.(name|url|legacy)")
CHAPTER = re.compile(r"^## \d+\.\s*(.+)$", re.M)   # the guide's numbered sections: "## 1. Semantic caching"
URL_PORT = re.compile(r"\w+://[^/:]+:(\d+)")
BROUGHT = {"addon": "needs", "setup": "members", "playground": "addons"}   # where a unit lists what it brings
GATEWAY_KEYS = ("platform", "service", "token")   # what an add-on says of the gateway it runs, each a text
# The product that each entry of a playground's seed/ fills, in the seeder's layout (seeder/README.md): what a reason
# names when a platform does not run it, in the order it lists them
SEEDED = {"apim": "API Management", "am": "Access Management", "aim": platforms.GAMMA, "authz": platforms.GAMMA}
# What a deploy to a platform seeds on this machine, whichever platform runs API Management: Access Management and
# OpenFGA run here, and the seeder reaches them as it does for a start on this machine
SEEDED_HERE = ("am", "openfga")
LOCAL_PRODUCTS = ("Access Management",)   # the products that a platform does not have to run: they run here
PLUGIN_KINDS = ("entrypoint", "endpoint", "policy", "resource")   # what an API definition names that a plugin provides
URL_HOST = re.compile(r"://(?:[^/?#\s@]*@)?([\w.-]+)")   # the host a URL names, after the user and password it may hold
LOCK_FILE = "contract.lock"   # what the engine promises units: its anchors say which plugin folders a unit may mount
ANCHOR = re.compile(r"anchor[ \t]+plugins/([a-z0-9-]+)(?:[ \t]|$)")   # one line of it: `anchor plugins/apim since=1`


# ---------------------------------------------------------------------------------------------------------------------
# Reading: everything that touches the disk or runs docker compose
# ---------------------------------------------------------------------------------------------------------------------

TEXT_LIMIT = trust.MAX_FILE   # the largest file of a catalog that is read at all: 1 MB (T19)
UNIT_TIMEOUT = 15   # seconds Compose has for one unit of a catalog, to read it and to resolve it
REACH_LIMIT = 300   # the files an include closure may reach before it is taken for a bomb
SEED_FILES, SEED_BYTES, SEED_DEPTH = 5000, 32 << 20, 12   # the most the screen reads of a seed folder
UNREADABLE = {"missing": "the file is gone", "unreadable": "not UTF-8 text", "refused": "the system refused to read it"}
# The tiers of a unit: the default catalog's, any other catalog's, this machine's, and a unit of the layout before
# catalogs, which is read and shown as it always was. Only a catalog's units are judged.
JUDGED = ("default", "other")


def _catalog_files():
    """The files that say which catalogs there are and what they say of themselves: the default file, the intent,
    the lock, the migration's marker, and for each catalog folder the folder itself (a link may be repointed), its
    manifest and its catalog.yml. A staging folder starts with a dot and is no catalog."""
    base = paths.CATALOGS_DIR
    files = [paths.DEFAULT_FILE, f"{LAB_DIR}/catalogs.json", f"{LAB_DIR}/catalogs.lock", f"{LAB_DIR}/layout.json"]
    try:
        names = sorted(name for name in os.listdir(os.path.join(ROOT, base)) if not name.startswith("."))
    except OSError:
        names = []
    for name in names:
        files += [f"{base}/{name}", f"{base}/{name}/{paths.MANIFEST}", f"{base}/{name}/catalog.yml"]
    return files


def fingerprint():
    """Changes when a file the project is read from changes, appears or goes: compose files, .env files, guides, and
    what says which catalogs there are. It runs at every state poll, so it is a stat of a few files for each unit
    and nothing else."""
    files = [CORE_FILE, ENV_FILE, LOCK_FILE, *_catalog_files()]
    for file in paths.files():
        found = paths.unit_of(file)
        files += [path for path in (file, *((paths.unit_env(*found), paths.unit_guide(*found), _seed_folder(*found))
                                            if found else ())) if path]
    root, entries = os.fspath(ROOT), []
    for path in files:
        try:
            info = os.stat(f"{root}/{path}")
        except OSError:   # not there, or gone since the folders were read: not read either way
            continue
        entries.append((path, info.st_mtime_ns, info.st_size))
    return hashlib.sha256(repr(sorted(entries)).encode()).hexdigest()[:16]


def _raw(path, skip=(), timeout=None):
    """compose.raw of a project file: (config, None), or (None, the problem). `skip` and `timeout` are for a unit of a
    catalog: the includes of it that are not there, and the seconds Compose has."""
    more = {key: value for key, value in (("skip", skip), ("timeout", timeout)) if value}
    try:
        return compose.raw(path, **more), None
    except (compose.ComposeError, ValueError) as error:   # Compose names the file by its full path: the problem already says which
        return None, {"file": path, "message": str(error).replace(f"validating {ROOT / path}: ", "")}


def _seed_folder(kind, uid):
    """Where a playground keeps what its seed creates: the folder whose entries decide where it can be deployed."""
    return f"{paths.unit_folder(kind, uid)}/seed" if kind == "playground" else None


def _seed_names(uid, safe=False):
    """What the seed folder of a playground holds, by name: a folder for each product it fills and the files of its
    controls and refills. Names that start with a dot are not meant to be seen. `safe`: the playground is a catalog's, so
    its seed folder is not listed if it is a link."""
    folder = ROOT / _seed_folder("playground", uid)
    try:
        if safe and folder.is_symlink():
            return []
        return sorted(found.name for found in folder.iterdir() if not found.name.startswith("."))
    except OSError:
        return []


def _steps(found, node):
    """The plugins of the policies that the steps of a flow name, wherever a flow is."""
    if isinstance(node, dict):
        if isinstance(node.get("policy"), str) and "enabled" in node:
            found["policy"].add(node["policy"])
        for value in node.values():
            _steps(found, value)
    elif isinstance(node, list):
        for value in node:
            _steps(found, value)


def _uses(definition):
    """The plugins a v4 API definition needs, by kind: {entrypoint, endpoint, policy, resource: {ids}}. Its listeners'
    entrypoints, its endpoint groups and their endpoints, the policies of its flows and plans, its resources."""
    found = {kind: set() for kind in PLUGIN_KINDS}
    api = definition.get("api", definition) if isinstance(definition, dict) else {}
    for listener in api.get("listeners") or []:
        found["entrypoint"].update(item["type"] for item in listener.get("entrypoints") or [] if isinstance(item.get("type"), str))
    for group in api.get("endpointGroups") or []:
        if isinstance(group.get("type"), str):
            found["endpoint"].add(group["type"])
        found["endpoint"].update(item["type"] for item in group.get("endpoints") or [] if isinstance(item.get("type"), str))
    found["resource"].update(item["type"] for item in api.get("resources") or [] if isinstance(item.get("type"), str))
    _steps(found, definition)
    return found


def _seed_uses(uid, safe=False):
    """What the APIs of a playground's seed need, by kind of plugin: the answer of _uses over every API definition the
    seed imports, those of the platform's environment and of each environment it adds (paths.seed_apis). Nothing for
    a definition that cannot be read: it is the seed's own failure to say. `safe`: the playground is a catalog's, so a
    definition that is a link, leads out of its folder or is over 1 MB is not read."""
    found = {kind: set() for kind in PLUGIN_KINDS}
    folder = paths.unit_folder("playground", uid)
    try:
        files = paths.seed_apis(ROOT / folder)
    except OSError:
        files = []
    for file in files:
        try:
            if safe:
                text, why = _read(file.relative_to(ROOT).as_posix()) if paths.contained(file, folder) else (None, "link")
                if why:
                    continue
            else:
                text = file.read_text(encoding="utf-8")
            more = _uses(json.loads(text))
        except (OSError, ValueError, AttributeError, TypeError, RecursionError):
            continue
        for kind in found:
            found[kind] |= more[kind]
    return {kind: sorted(ids) for kind, ids in found.items()}


def _resolved(raw, problems, only=None):
    """Images, ports and labels with variables replaced: one configuration of the core with every add-on and
    playground that loads, whether it is on or not. `only`: the files to put on top of the core, which in a project with
    catalogs are the units of this machine, since the units of a catalog are resolved one by one."""
    on_top = [paths.unit_file(kind, uid) for kind in ("addon", "playground") for uid in _ids(raw, kind)]
    if only is not None:
        on_top = [file for file in on_top if file in only]
    try:
        return compose.resolved(compose.all_files(on_top)) if CORE_FILE in raw else {}
    except compose.ComposeError as error:
        problems.append({"file": CORE_FILE, "message": str(error)})
        return {}


def _env_values():
    """The variables of the .env files: {file: {name: value}} for the core's and each add-on's and playground's own. The
    .env of a unit of a catalog is read as a plain file of at most 1 MB in the catalog's own folder (_read). One that is a
    link, a pipe, too large or not UTF-8 text, of a catalog or of this machine, is read as none: never an error that
    stops the project."""
    files = [ENV_FILE, *(paths.unit_env(kind, uid) for kind in ("addon", "playground") for uid in paths.ids(kind))]
    return {path: _dotenv(path) for path in files}


def _dotenv(path):
    """The variables of one .env file, {} when there is none or it cannot be read."""
    if _catalog_path(path) and _read(path)[1]:
        return {}
    try:
        return envfiles.read(path)
    except (OSError, ValueError):   # a file that is not UTF-8 text, or is gone since it was looked at
        return {}


# -- A catalog is hostile input ----------------------------------------------------------------------------------

def _tier(file, default):
    """The tier of a unit file (JUDGED): `default` and `other` are a catalog's, `local` this machine's, `legacy` a unit of
    the layout before catalogs."""
    found = resolve.target_of(file)
    if found is None or default is None:
        return "legacy"
    return "local" if found[0] == paths.LOCAL else "default" if found[0] == default else "other"


def _own_file(file):
    """The core and what belongs to this machine: the person's own files, which the screen does not look at."""
    return file == CORE_FILE or file.startswith(f"{paths.CATALOGS_DIR}/{paths.LOCAL}/")


def _catalog_path(path):
    """Whether a path is inside the folder of a catalog that is not this machine's: .lab/catalogs/<ns>/..."""
    parts = path.split("/")
    return len(parts) > 3 and f"{parts[0]}/{parts[1]}" == paths.CATALOGS_DIR and parts[2] != paths.LOCAL


_REAL = {}   # the real place of a folder, for the length of one gather: the same few are asked for by every file of a catalog


def _real(folder):
    """The folder of the project with every link followed, remembered until the next gather."""
    key = (ROOT, folder)
    if key not in _REAL:
        _REAL[key] = os.path.realpath(os.path.join(ROOT, folder))
    return _REAL[key]


def _outside(path):
    """Whether a file of a catalog that is not this machine's leads out of the catalog's folder once the links on the way
    are followed: a link in place of a unit's folder, which the look at the last name of a file does not see. A catalog
    folder that is itself a link, as a folder catalog may be, holds its files all the same: both sides are resolved. The
    last name is not followed here: a file that is a link is refused by its own look (`_read`)."""
    if not _catalog_path(path) or "\0" in path:
        return False
    parts = path.split("/")
    try:
        top = _real("/".join(parts[:3]))
        where = os.path.join(_real("/".join(parts[:-1])), parts[-1])
        return os.path.commonpath([where, top]) != top
    except (OSError, ValueError):
        return True   # a doubt refuses


def _read(path, limit=TEXT_LIMIT, strict=True):
    """The text of a project file that is a plain file, at most `limit` bytes of it: (text, None), or (None, why): `link`
    for a link, a pipe, a device or a folder, which could lead anywhere or never end (also a file of a catalog whose folder
    is a link out of it), `large`, `missing`, `refused` or `unreadable` (not UTF-8; unless `strict` is false, then what
    is not is replaced). Opened without following a link, so one put in place after the look is not followed either."""
    full = os.path.join(ROOT, path)
    if _outside(path):
        return None, "link"
    try:
        if not stat.S_ISREG(os.lstat(full).st_mode):
            return None, "link"
        descriptor = os.open(full, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None, "missing"
    except OSError as error:
        return None, "link" if error.errno == errno.ELOOP else "refused"
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return None, "link"
        data = handle.read(limit + 1)
    if len(data) > limit:
        return None, "large"
    try:
        return data.decode("utf-8", "strict" if strict else "replace"), None
    except UnicodeDecodeError:
        return None, "unreadable"


def _screen(file):
    """What the text screen says of a unit of a catalog before Compose is asked about it: (text, reasons,
    error). `reasons`: the lines of T19, from trust.prescreen, or for a file that is a link or over 1 MB (it reads files
    outside its folder); `error`: why the text cannot be read at all (T27); `text`: what was read."""
    text, why = _read(file)
    if why in ("link", "large"):
        return None, [trust.line("T19", 1)], None
    if why:
        return None, [], UNREADABLE.get(why, why)
    return text, trust.prescreen(text, file), None


def _seed_scan(folder):
    """The text of the files under a playground's seed folder, for the settings its seed reads (N02), and the lines of T19
    that hold: ([text], [reason]). A file over 1 MB, or more files than SEED_FILES or bytes than SEED_BYTES, is a reason: the
    screen cannot read all of it. A link is not followed and a name that starts with a dot is not read: a seed folder that is
    itself a link is a reason, for the screen would read what is outside the unit's folder."""
    if os.path.islink(os.path.join(ROOT, folder)):
        return [], [trust.line("T19", 1)]
    texts, size, count, todo = [], 0, 0, [(folder, 0)]
    while todo:
        base, depth = todo.pop()
        try:
            entries = sorted(os.scandir(os.path.join(ROOT, base)), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            path = f"{base}/{entry.name}"
            if entry.is_symlink() or entry.name.startswith("."):
                continue
            if entry.is_dir(follow_symlinks=False):
                if depth >= SEED_DEPTH:
                    return texts, [trust.line("T19", 1)]
                todo.append((path, depth + 1))
            elif entry.is_file(follow_symlinks=False):
                text, why = _read(path, strict=False)
                count, size = count + 1, size + len(text or "")
                if why == "large" or count > SEED_FILES or size > SEED_BYTES:
                    return texts, [trust.line("T19", 1)]
                if text is not None:
                    texts.append(text)
    return texts, []


def _includes(path):
    """compose.includes of a file that may be anything: nothing when it cannot be read."""
    try:
        return compose.includes(path)
    except (OSError, ValueError):
        return []


def _direct(path, screen):
    """The project files that a unit file includes itself, for `inputs["includes"]`: those the closure follows. A file of a
    catalog is read as the screen reads it (trust.include_targets, from the text `screen` read), so that the judgement sees
    every include the walk followed; any other file is compose.includes'. A file whose includes the screen cannot name is
    one the walk holds (`_reach`) and Compose is never asked about, so for it the literal names of compose.includes are all
    the judgement is given."""
    found = trust.include_targets(screen[path][0] or "", path) if path in screen else None
    return _includes(path) if found is None else found


def _plain_file(path):
    """True for a plain file, False for a link or a special file, None when there is nothing at the path."""
    try:
        return stat.S_ISREG(os.lstat(os.path.join(ROOT, path)).st_mode)
    except OSError:
        return None


# The key `include` wherever a YAML text can put one: at the start of a line (after the dashes, question marks and properties
# a key may have) or after the brace, bracket or comma of a flow collection
INCLUDE_KEY = re.compile(r"""(?:^|[{\[,])[ \t]*(?:[-?][ \t]+)*(?:[&!][^\s,\[\]{}]*[ \t]+)*["']?include["']?[ \t]*:""", re.M)
BREAKS = re.compile(r"\r(?!\n)|[\u0085\u2028\u2029]")   # the line breaks of YAML that are not \n or \r\n: a text with one is not read by line


def _reach(start, bad, clean=()):
    """What the include closure of a unit file reaches: {files: those that exist, targets: every one it names,
    missing: those under .lab/catalogs/ that are not files, unsafe: those it must not be given to Compose with}. A catalog's
    file is one that its author controls, as is anything that a file of a catalog includes. It is followed only when it is a
    unit that the screen read and found clean (`clean`), whose includes the screen can name (trust.include_targets: a block
    or flow list of plain file names that YAML reads as their letters say, and no other), in a folder whose `.env`, which
    Compose reads with it, is a plain file. Anything else is unsafe: a unit that the screen refused (`bad`), a link or a
    special file (it could lead anywhere, or never end), a file that is no unit (it was never looked at, and Compose reads
    what its `include` says, `env_file` included), a file whose includes the screen cannot name (a name that Compose reads
    other than its letters, a key that an escape or a fold spells: the file it reads is one nobody screened), or a closure so
    big that it is taken for a bomb (`bomb`: its last file). The core and the files of this machine are the person's own,
    and so is what they include outside the catalogs: they are followed and only a link or a special file is unsafe."""
    seen, todo = set(), [(start, False)]
    found = {"files": [], "targets": [], "missing": [], "unsafe": []}
    while todo:
        file, via = todo.pop(0)   # via: whether a file of a catalog includes it
        if (file, via) in seen:
            continue
        seen.add((file, via))
        if len(seen) > REACH_LIMIT:
            found["unsafe"].append(file)
            found["bomb"] = file
            break
        plain = _plain_file(file)
        if plain is None:   # not there: the target is missing, or it is the unit's own file and gone
            continue
        theirs = not _own_file(file) and (via or _catalog_path(file))
        if file != start and (file in bad or plain is False and not _own_file(file) or theirs and file not in clean):
            found["unsafe"].append(file)
            continue
        targets = None
        if theirs:
            dotenv = f"{file.rsplit('/', 1)[0]}/.env"
            text, why = _read(file)
            if _plain_file(dotenv) is False:   # the .env next to a file is read by Compose with it
                found["unsafe"].append(dotenv)
                continue
            targets = None if why else trust.include_targets(text, file)
            if targets is None:
                found["unsafe"].append(file)
                if not why:   # read, and the screen cannot say what it includes: a spelling it does not read (see trust.include_targets)
                    found.setdefault("unnamed", []).append(file)
                continue
        found["files"].append(file)
        targets = _includes(file) if targets is None else targets
        found["targets"] += [target for target in targets if target not in found["targets"]]
        todo += [(target, theirs) for target in targets]
    prefix = f"{paths.CATALOGS_DIR}/"
    found["missing"] = [target for target in found["targets"]
                        if target.startswith(prefix) and not os.path.isfile(os.path.join(ROOT, target))]
    return found


def held_why(found, bad):
    """[(file, kind)] for each file the closure of a unit reached that must not be given to Compose (`_reach`'s `unsafe`), with
    what keeps it from being read: `refused` (a unit the screen refused), `special` (a link or a special file), `unnamed`
    (a file whose includes the screen cannot name: it is written in a spelling the Lab does not read, see trust.include_targets),
    `bomb` (a closure too big), `outside` (a file that is no unit of the catalog)."""
    why = []
    for file in found["unsafe"]:
        if file in bad and paths.unit_of(file):
            kind = "refused"
        elif file != found.get("bomb") and _plain_file(file) is False:
            kind = "special"
        elif file in found.get("unnamed", ()):
            kind = "unnamed"
        else:
            kind = "bomb" if file == found.get("bomb") else "outside"
        why.append((file, kind))
    return why


def held_reasons(found, bad):
    """The lines that keep a unit from being read at all, when its closure reaches what must not be given to Compose (`held_why`
    says which kind): "Needs {name}, which is blocked" for a unit that was refused, the line of T19 for a link or a special file
    (it reads files outside its folder), and for anything else, a file that is no unit, one that hides an include or a closure
    too big (it includes content from outside the catalog)."""
    lines = []
    for file, kind in held_why(found, bad):
        if kind == "refused":
            lines.append(trust.line("T25", name=paths.unit_of(file)[1].rsplit("/", 1)[-1]))
        else:
            lines.append(trust.line("T19", 1 if kind == "special" else 0))
    return list(dict.fromkeys(lines))


# -- What the pool reads of a unit, and what it remembers --------------------------------------------------------------

_memo, _memo_lock = {}, threading.Lock()   # what was read of each unit of a catalog, by the stamp of what it was read from
_no_env = True   # Compose is asked `config --no-env-resolution` () until one that does not know it answers


def _stamp(files, skip):
    """What a unit was read from: the size and time of the files of its closure, their .env files, the root
    .env, the core and the day. Not the environment of the process: `forget()` is for that."""
    root = os.fspath(ROOT)
    names = [CORE_FILE, ENV_FILE, *files, *(f"{file.rsplit('/', 1)[0]}/.env" for file in files if file.endswith("/compose.yml"))]
    parts = [str(datetime.date.today()), *sorted(skip)]
    for name in names:
        try:
            info = os.stat(f"{root}/{name}")
            parts.append(f"{name}:{info.st_mtime_ns}:{info.st_size}")
        except OSError:
            parts.append(f"{name}:-")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _remembered(key, stamp, make, keep=None):
    """What `make` gives for `key`, kept while `stamp` is the same, as a copy that nobody else holds. `keep`: whether an
    answer is worth keeping: a read that failed for a reason that is not in the files (a time limit, a Compose that did not
    answer) is made again at the next gather, not held until a file changes or the day ends."""
    key = (os.fspath(ROOT), key)
    with _memo_lock:
        hit = _memo.get(key)
    if hit is None or hit[0] != stamp:
        value = make()
        if keep and not keep(value):
            with _memo_lock:
                _memo.pop(key, None)
            return value
        hit = (stamp, value)
        with _memo_lock:
            _memo[key] = hit
    return copy.deepcopy(hit[1])


_option, _asking = {"known": None}, threading.Lock()   # whether Compose knows `config --no-env-resolution`, once it was asked (_knows)


def _knows():
    """Whether this Compose knows `config --no-env-resolution`, asked of its own help: an answer that comes from Compose and
    not from the text of an error, which a unit's author can write. Asked once, for the pool asks together. True while the
    help cannot be had."""
    with _asking:
        if _option["known"] is None:
            code, out, _ = compose.run([*compose.COMPOSE, "config", "--help"], timeout=UNIT_TIMEOUT)
            if code != 0:
                return True
            _option["known"] = "--no-env-resolution" in out
        return _option["known"]


def _config(files, timeout=UNIT_TIMEOUT):
    """compose._config, whole and with `--no-env-resolution`: `env_file` and `label_file` stay in the answer, where the
    policy reads their real places (). A Compose that does not know the option is asked without it from then on: the
    text screen refuses an env_file whatever the answer says. An error that says so is only a reason to ask the help of
    Compose (_knows): a unit can make Compose print anything."""
    global _no_env
    try:
        return compose._config(files, *(["--no-env-resolution"] if _no_env else []), timeout=timeout)
    except compose.ComposeError as error:
        if _no_env and "unknown flag" in str(error) and not _knows():
            _no_env = False
            return _config(files, timeout)
        raise


def _alone(path, timeout=UNIT_TIMEOUT):
    """A unit with the core as Compose runs it, variables replaced: (config, None), or (None, the last line it printed)."""
    try:
        return _config(compose.all_files([path]), timeout), None
    except (compose.ComposeError, ValueError) as error:
        return None, str(error)


# What Compose says when it was not asked, or did not answer, and not when it read the files: the time limit, the end of the
# Lab UI, a docker that cannot be run. A unit that failed so is read again at the next gather.
UNANSWERED = re.compile(r"docker (?:did not answer within \d+ s|was not run: .*)|\[Errno \d+\] .*")


def _answered(found):
    """Whether what the pool read of a unit is Compose's answer on its files, which stays true until they change: not a time
    limit, a docker that did not run, or a failure of the Lab's own (`transient`)."""
    said = [(found.get("problem") or {}).get("message"), found.get("error")]
    return not found.get("transient") and not any(isinstance(text, str) and UNANSWERED.fullmatch(text.strip()) for text in said)


def _merged(file, reach):
    """The texts of the files that the closure of a unit reached, besides its own: Compose merges them into its configuration,
    and `trust.reads` needs to see how they write the names in it to tell an empty value of their own from one they leave
    for the shell to give. What cannot be read again is left out, which can only make a name count as read."""
    others = (_read(other) for other in reach["files"] if other != file)
    return [text for text, why in others if why is None]


def _read_unit(file, text, reach):
    """What the pool reads of one unit of a catalog: {raw, problem, config, error, references}. Its file as written
    (compose.raw, the includes that are not there left out, a time limit), then, when it loads and misses no catalog, as
    Compose runs it with the core. A unit that misses a catalog is judged when the catalog arrives. What is
    kept of it is Compose's answer on its files (_answered)."""
    def read():
        try:
            started = time.monotonic()
            raw, problem = _raw(file, skip=reach["missing"], timeout=UNIT_TIMEOUT)
            config = error = None
            if raw is not None and not reach["missing"]:   # one budget for both: a hostile unit costs one timeout
                config, error = _alone(file, max(1, UNIT_TIMEOUT - int(time.monotonic() - started)))
            return {"raw": raw, "problem": problem, "config": config, "error": error,
                    "references": trust.reads([text], raw, _merged(file, reach)) if raw is not None else []}
        except Exception as failure:   # noqa: BLE001 - what a hostile file does to the reading stops at its own unit
            return {"raw": None, "problem": {"file": file, "message": f"unexpected {type(failure).__name__}"},
                    "config": None, "error": None, "references": [], "transient": True}
    return _remembered(file, _stamp(reach["files"], reach["missing"]), read, _answered)


OWN_KINDS = re.compile(r"/(?:addons|playgrounds)/")   # the units of this machine that are judged for their notes: not a setup


def _read_own(file, reach):
    """What the pool reads of a unit of this machine besides its file: {own_config}, the unit with the core as Compose runs it
    (None when Compose cannot), for the notes of the policy (N01 to N03, N05: what it restricts), which it judges and never
    blocks. It is not the unit's `config`, which is what a catalog's unit is run as: the person's own are run as the aggregate
    (`resolved`) says. Kept as long as the files it was read from stay as they are, and not when Compose did not answer
    (`_answered`)."""
    def read():
        try:
            config, error = _alone(file)
            return {"own_config": config, "error": error}
        except Exception:   # noqa: BLE001 - what a file does to the reading stops at its own unit
            return {"own_config": None, "error": "unexpected failure", "transient": True}
    found = _remembered(f"own:{file}", _stamp(reach["files"], reach.get("missing") or ()), read, _answered)
    return {"own_config": found["own_config"]}


def _own_seeds(files):
    """{file: the settings its seed reads} for the playgrounds of this machine among these unit files, for N02: a seed that reads
    a token is not handed the Cloud Token of a place, whoever wrote it. The folder is read as a catalog's is (`_seed_scan`) and
    never holds the playground back; one that cannot be read whole (a link, over the limits) reads as a seed that takes the
    token, for what the screen cannot see it cannot clear."""
    found = {}
    for file in files:
        unit = paths.unit_of(file)
        if unit and unit[0] == "playground":
            texts, reasons = _seed_scan(_seed_folder(*unit))
            found[file] = sorted({*trust.seed_reads(texts), *([trust.TOKEN_NAME] if reasons else [])})
    return found


def _core_config():
    """The core alone as Compose runs it, for the policy to compare a unit with: (config, None), or (None, why). Only a read
    that worked is kept: a unit is judged against the core, and judged blocked while it cannot be read."""
    def read():
        try:
            return _config(compose.all_files([])), None
        except (compose.ComposeError, ValueError) as error:
            return None, str(error)
    return _remembered("", _stamp([], ()), read, lambda found: found[0] is not None)


def _shared_settings(ns):
    """The names a catalog says its units share (`shared_settings` of its catalog.yml, 4.1): a list of setting names. Read
    with Compose, after the same screen as a unit and only when the file includes nothing (it is no unit: nothing screens
    what it would include); [] when the file says none or cannot be read, and a failure of Compose is not kept."""
    file = f"{paths.CATALOGS_DIR}/{ns}/catalog.yml"

    def read():
        text, why = _read(file)
        if why or trust.prescreen(text, file) or not re.search(r"^\s*shared_settings\s*:", text, re.M) \
                or BREAKS.search(text) or INCLUDE_KEY.search(trust.unescaped(text)):
            return []
        config, problem = _raw(file, timeout=UNIT_TIMEOUT)
        if problem:
            return None if UNANSWERED.fullmatch(problem["message"].strip()) else []
        names = ((config or {}).get("x-gravitee-catalog") or {}).get("shared_settings")
        return [name for name in names if isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)] \
            if isinstance(names, list) else []
    return _remembered(f"catalog:{ns}", _stamp([file], ()), read, lambda found: found is not None) or []


def _plain(message):
    """A message of Compose for a person: the project's folder and the catalog's place in it out of any path."""
    text = str(message).replace(os.fspath(ROOT) + "/", "")
    text = re.sub(r"\.lab/catalogs/[a-z0-9-]+/", "", text)
    return re.sub(r"(?<![\w.~:/-])(?:/[\w.@+~-]+)+/([\w.@+~-]+)", r"\1", text)


def _variables(path):
    """compose.variables of a file that loaded; nothing for one that cannot be read again."""
    try:
        return compose.variables(path)
    except (OSError, ValueError):
        return []


def _guides(ids, tiers, stubs):
    """The guide of each playground that has one: the text of its guide.md. A catalog's is read as a plain file of at most
    1 MB, and a stub's is not read."""
    found = {}
    for pid in ids:
        file, guide = paths.unit_file("playground", pid), paths.unit_guide("playground", pid)
        if file in stubs:
            continue
        if tiers.get(file) in JUDGED:
            text, why = _read(guide)
            if why is None:
                found[pid] = text
        elif (ROOT / guide).is_file():
            found[pid] = (ROOT / guide).read_text(encoding="utf-8")
    return found


def _contract(core):
    """What the core says of the level of everything units rely on: {contract, contract_min, seeder_image}. A level
    that is no whole number from 1 is 1, as is one the core does not carry."""
    meta = core.get("x-gravitee") if isinstance(core.get("x-gravitee"), dict) else {}

    def level(key):
        return meta[key] if type(meta.get(key)) is int and meta[key] >= 1 else 1
    seeder = meta.get("seeder") if isinstance(meta.get("seeder"), dict) else {}
    return {"contract": level("contract"), "contract_min": level("contract_min"),
            "seeder_image": seeder["image"] if isinstance(seeder.get("image"), str) else ""}


def _anchored_plugins():
    """The plugin folders the contract names as anchors (contract.lock: `anchor plugins/apim`, `anchor plugins/am`): the places
    in the engine that a unit may mount to give a service the plugins of a product. A fresh clone has not made them yet (`make
    init` does) and a catalog's CI run has no plugins/ folder at all; a unit that binds one where it is meant to be is no unit
    that mounts files outside its folder, whether the folder is there or not. Only a plain file is read; [] when it is not."""
    text, why = _read(LOCK_FILE)
    if why:
        return []
    return [found.group(1) for found in (ANCHOR.match(row) for row in text.splitlines()) if found]


def _plugin_folders():
    """The names of the products whose plugin folders a unit may reach (the roots of inputs["products"]): the folders of
    plugins/, and the anchors the contract names, which are there for the Lab and for a catalog's check alike."""
    base = os.path.join(ROOT, "plugins")
    try:
        here = [name for name in os.listdir(base) if os.path.isdir(os.path.join(base, name))]
    except OSError:
        here = []
    return sorted({*here, *_anchored_plugins()})


def gather():
    """Everything build() needs, read from the disk and from docker compose. A file that does not load is left out
    and reported in `problems`; the others still load. The units of catalogs are read as hostile input: their
    text is screened first, and a unit that fails the screen, or whose closure reaches a file that is not a clean unit
    (_reach), is never given to Compose; the others are read with a time limit and resolved alone with the core, in the
    pool, and what the pool learned of a unit is kept while its files stay as they are, unless Compose did not answer
    (that is read again at the next gather). A problem of such a file is the catalog's (`catalog_problems`), not the
    banner's. The units of this machine, and every unit while no default file exists, are read together as they always
    were; an add-on or a playground of this machine is also resolved alone with the core (`own_configs`) and its seed read
    (`seed_references`), for the notes of the policy, which judges them and blocks none."""
    _REAL.clear()
    default = paths.default_ns()
    unit_files = paths.files()
    tiers = {file: _tier(file, default) for file in unit_files}
    judged = [file for file in unit_files if tiers[file] in JUDGED]
    screen = {file: _screen(file) for file in judged}
    seeds = {}
    for file in judged:   # a playground's seed is read for the settings it reads (N02), and held to the size too
        found = paths.unit_of(file)
        if found and found[0] == "playground" and not screen[file][1] and not screen[file][2]:
            seeds[file] = _seed_scan(_seed_folder(*found))
    screened = {file: [*screen[file][1], *seeds.get(file, ((), ()))[1]] for file in judged}
    unreadable = {file: screen[file][2] for file in judged if screen[file][2]}
    bad = {file for file in judged if screened[file] or file in unreadable}
    clean = set(judged) - bad   # the files of a catalog that the closure of a unit may follow: units the screen read, and found clean
    own_seeds = _own_seeds([file for file in unit_files if tiers[file] == "local"])
    reach = {file: _reach(file, bad, clean) for file in unit_files if tiers[file] != "legacy" and file not in bad}
    held = {file: lines for file, found in reach.items() for lines in [held_reasons(found, bad)] if lines}
    stubs = bad | set(held)

    def read(file):
        if tiers.get(file) in JUDGED:
            return file, _read_unit(file, screen[file][0], reach[file])
        skip = reach[file]["missing"] if file in reach else ()
        raw, problem = _raw(file, skip=skip) if skip else _raw(file)
        found = {"raw": raw, "problem": problem}
        if raw is not None and not skip and tiers.get(file) == "local" and OWN_KINDS.search(file):
            found.update(_read_own(file, reach.get(file) or {"files": [file], "missing": []}))
        return file, found

    with ThreadPoolExecutor(max_workers=8) as pool:
        loaded = dict(pool.map(read, [CORE_FILE, *(file for file in unit_files if file not in stubs)]))
    raw = {path: found["raw"] for path, found in loaded.items() if found["raw"] is not None}
    problems, catalog_problems = [], {}
    for path, found in loaded.items():
        problem = found["problem"]
        if problem and tiers.get(path) in JUDGED:   # the catalog's, which says so: not the banner's
            ns = resolve.target_of(path)[0]
            catalog_problems.setdefault(ns, []).append(
                {"file": path, "display": path.split("/", 3)[3], "message": "Cannot be read: " + _plain(problem["message"])})
        elif problem:
            problems.append(problem)
    plain = {file for file in unit_files if file in raw and tiers[file] in ("local", "legacy")
             and not reach.get(file, {}).get("missing")}
    core, core_error = _core_config() if judged else (None, None)
    by_kind = {kind: [found[1] for found in map(paths.unit_of, unit_files) if found and found[0] == kind] for kind in KINDS}
    # Whether the license of the core's .env lets the services that need one start: read here with the other inputs
    usable = license.usable(license.status_of(_license_var(raw.get(CORE_FILE) or {})))
    catalogs = registry.inputs()
    return {"raw": raw, "license_usable": usable,
            "includes": {path: _direct(path, screen) for path in raw},
            "variables": {path: _variables(path) for path in raw},
            "resolved": _resolved(raw, problems, plain if default is not None else None),
            "environ": dict(os.environ), "env": _env_values(),
            "guides": _guides(by_kind["playground"], tiers, stubs),
            "seeds": {pid: _seed_names(pid, tiers.get(paths.unit_file("playground", pid)) in JUDGED)
                      for pid in by_kind["playground"]},
            "uses": {pid: _seed_uses(pid, tiers.get(paths.unit_file("playground", pid)) in JUDGED)
                     for pid in by_kind["playground"] if paths.unit_file("playground", pid) not in stubs},
            "problems": problems,
            # What the catalogs add: where each unit comes from, what the screen and Compose said of it, the
            # catalogs as the disk says them, the level of the core, and the state of the migration
            "default": default, "catalogs": catalogs, "reach": reach,
            "screened": {**{file: screened[file] for file in bad if screened[file]}, **held}, "unreadable": unreadable,
            "configs": {path: found["config"] for path, found in loaded.items() if found.get("config") is not None},
            "own_configs": {path: found["own_config"] for path, found in loaded.items() if found.get("own_config") is not None},
            "errors": {path: found["error"] for path, found in loaded.items() if found.get("error")},
            "core_config": core, "core_error": core_error,
            "references": {path: found["references"] for path, found in loaded.items() if found.get("references")},
            "seed_references": {**{file: trust.seed_reads(seeds[file][0]) for file in seeds if file in raw},
                                **{file: names for file, names in own_seeds.items() if file in raw}},
            "shared_settings": {ns: _shared_settings(ns) for ns, row in catalogs.items() if row.get("kind") != "machine"},
            "catalog_problems": catalog_problems, "contract": _contract(raw.get(CORE_FILE) or {}),
            "layout": layout.status(), "realpath": os.path.realpath, "home": os.path.expanduser("~"),
            "products": _plugin_folders()}


# ---------------------------------------------------------------------------------------------------------------------
# Building: pure functions over what gather() read
# ---------------------------------------------------------------------------------------------------------------------

def _ids(files, kind):
    """The ids of the add-ons, setups or playgrounds among these files, in their order."""
    units = (paths.unit_of(file) for file in files)
    return [unit[1] for unit in units if unit and unit[0] == kind]


def _configs(inputs, addon_ids):
    files = (paths.unit_file("addon", uid) for uid in addon_ids)
    return [inputs["raw"][file] for file in files if file in inputs["raw"]]


def _brought(includes, path):
    """The add-ons a file brings through its includes, and theirs: an add-on includes the ones it needs, a setup the
    ones it groups."""
    seen, todo = set(), list(includes.get(path, []))
    while todo:
        file = todo.pop()
        if file not in seen and file != path:
            seen.add(file)
            todo += includes.get(file, [])
    return sorted(_ids(seen, "addon"))


def _services_of(config):
    return config.get("services") or {}


def _labels(service):
    return service.get("labels") or {}


def _written(path):
    """The services a file names under its own `services:`, in the order it writes them: Compose gives them back
    alphabetically, which says nothing the author meant."""
    try:
        text = (ROOT / path).read_text(encoding="utf-8")
    except (OSError, ValueError):   # not there, or not text
        return []
    start = re.search(r"^services:[ \t]*(#.*)?$", text, re.M)
    block = re.split(r"^(?=[A-Za-z])", text[start.end():], maxsplit=1, flags=re.M)[0] if start else ""
    return re.findall(r"^  ([\w.-]+):[ \t]*(?:#.*)?$", block, re.M)


def _own(path, config, others, core_names):
    """The services a file runs itself: not the core's, nor those of the add-ons it includes, in the order the file
    writes them."""
    theirs = {name for other in others for name in _services_of(other)}
    names = [name for name in _services_of(config) if name not in core_names and name not in theirs]
    written = _written(path) if names else []
    return sorted(names, key=lambda name: written.index(name) if name in written else len(written))


def _added(value, theirs):
    """Whether a definition holds something that none of these, the ones merged into it, holds: key by key in a
    mapping, item by item in a list."""
    if isinstance(value, dict):
        return any(_added(v, [t[k] for t in theirs if isinstance(t, dict) and k in t]) for k, v in value.items())
    if isinstance(value, list):
        return any(not any(isinstance(t, list) and item in t for t in theirs) for item in value)
    return value not in theirs


def _restarts(config, others, core_names):
    """The core services a file changes itself: Compose recreates them when the file comes or goes. What an add-on
    it includes sets is that add-on's change, also when two of them change the same service."""
    mine = _services_of(config)
    return [name for name in sorted(core_names & set(mine))
            if _added(mine[name], [_services_of(other)[name] for other in others if name in _services_of(other)])]


def _image_variables(config):
    return {key for service in _services_of(config).values()
            for key, _, _ in compose.references(service.get("image") or "")}


def _described(variables, skip):
    return [dict(v, secret=bool(SECRET.search(v["key"]))) for v in variables if v["key"] not in skip]


def _conflicts(meta, path, problems):
    """{add-on id: why they cannot run together}, the keys as the file writes them (`build` settles them to ids)."""
    declared = meta.get("conflicts") or {}
    if isinstance(declared, dict):
        return {uid: reason or "" for uid, reason in declared.items()}
    problems.append({"file": path, "message": "conflicts must map each add-on id to a reason"})
    return {}


def conflict_id(other, catalog, known):
    """The id of the add-on that a `conflicts` key names, among the ids in `known`: a bare id is the add-on of the same
    catalog if there is one, else the default catalog's; `@ns/id` and `local/id` name one of that catalog, and so does
    `@<default>/id`. None when nothing has that id. The one definition: the layers read the conflicts of a project with it,
    and the check of an author says which key names nothing."""
    if not isinstance(other, str):
        return None
    candidates = [paths.canonical(other)]
    if paths.is_default(other) and catalog not in (None, paths.default_ns()):
        candidates.insert(0, f"{paths.LOCAL}/{other}" if catalog == paths.LOCAL else f"@{catalog}/{other}")
    return next((one for one in candidates if one in known), None)


def _settle_conflicts(addons):
    """The `conflicts` of every add-on with its keys settled to ids, so that two add-ons of a catalog that is not the
    first conflict whichever way their files say it: `echo` in an add-on of `@acme-hub` is `@acme-hub/echo`, and the layers,
    which compare ids, find it. A key that names nothing stays as it was written (the check of an author says so); when two
    keys settle to one id the first reason that says something is kept. Changes the add-ons `_addon` has just made."""
    known = set(addons)
    for addon in addons.values():
        settled = {}
        for other, why in addon["conflicts"].items():
            name = conflict_id(other, addon.get("catalog"), known) or other
            settled[name] = settled.get(name) or why
        addon["conflicts"] = settled


def _strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _misshapen(key, value):
    """How a kind of requirement must be written, when this value is written otherwise; None when it is fine. The
    Lab UI reads each kind that way."""
    if key == "license" and not (_strings(value) or isinstance(value, dict) and all(
            isinstance(part, str) or part is None for part in value.values())):
        return "a list of license features, or a mapping of each feature to what needs it"
    if key == "plugins" and not (isinstance(value, dict) and all(_strings(names) for names in value.values())):
        return "a mapping of each product to a list of plugins"
    if key == "images" and not (isinstance(value, dict) and _strings(list(value.values()))):
        return "a mapping of each version variable to the version it needs at least, in quotes"
    if key == "host" and not (isinstance(value, list) and all(
            isinstance(spec, dict) and isinstance(spec.get("name"), str) and isinstance(spec.get("check"), str)
            for spec in value)):
        return "a list of checks, each with a name and a check URL"
    return None


def license_features(requires):
    """The license features a unit requires, {feature: what needs it}: None for the whole unit (the list form), a part
    of it in the mapping form ("Step 4" of a playground's guide)."""
    wanted = requires.get("license") or []
    return {feature: part or None for feature, part in wanted.items()} if isinstance(wanted, dict) \
        else dict.fromkeys(wanted)


def _apim_features(requires):
    """The license features of API Management that a unit requires: those a platform running it must have. The others
    (Access Management's, Gamma's) are for the machines they run on."""
    return sorted(feature for feature in license_features(requires or {}) if feature.startswith("apim-"))


def _meta(config, path, problems):
    """The x-gravitee block of a file. It and its requires must be mappings, and each requirement written as it is
    read: Compose does not look inside. What is not is reported and left out."""
    meta = config.get("x-gravitee") or {}
    if not isinstance(meta, dict):
        problems.append({"file": path, "message": "x-gravitee must be a mapping"})
        return {}
    requires = meta.get("requires") or {}
    if not isinstance(requires, dict):
        problems.append({"file": path, "message": "x-gravitee.requires must be a mapping"})
        return {**meta, "requires": {}}
    tested = meta.get("tested")
    if tested is not None and not (isinstance(tested, list) and all(
            isinstance(one, dict) and one and all(isinstance(k, str) and isinstance(v, str) for k, v in one.items())
            for one in tested)):
        problems.append({"file": path, "message": "x-gravitee.tested must be a list of sets, each a mapping of a "
                                                  "version variable to the version it was tested with, in quotes"})
        meta = {**meta, "tested": []}
    kept = {}
    for key, value in requires.items():
        shape = _misshapen(key, value)
        if shape:
            problems.append({"file": path, "message": f"x-gravitee.requires.{key} must be {shape}"})
        else:
            kept[key] = value
    return {**meta, "requires": kept}


def _domains(config, service):
    """How the gateway of an add-on is reached, from its first published port, so that no address is written twice:
    from this machine (localhost:8090) and from a container of the lab (apim-hybrid-gateway:8082). Empty when it
    publishes none."""
    for port in _services_of(config).get(service, {}).get("ports") or []:
        published = str(port.get("published") or "")
        if published.isdigit():
            return [f"localhost:{published}", f"{service}:{port.get('target')}"]
    return []


def _gateway(meta, config, path, problems):
    """The gateway of a platform that an add-on runs on this machine: {platform (its kind), service, token (the
    setting that takes the gateway's token), domains}, and `group` once the groups are known (_gateway_groups); None for
    an add-on that runs none. What is written otherwise is reported and left out."""
    declared = meta.get("gateway")
    if declared is None:
        return None
    if not (isinstance(declared, dict) and all(isinstance(declared.get(key), str) and declared[key]
                                               for key in GATEWAY_KEYS)):
        problems.append({"file": path, "message": "x-gravitee.gateway must be a mapping of platform, service and "
                                                  "token, each a text"})
        return None
    return {**{key: declared[key] for key in GATEWAY_KEYS}, "domains": _domains(config, declared["service"])}


def _unit(kind, uid, inputs, problems):
    """What every add-on, setup and playground says about itself in its x-gravitee block."""
    path = paths.unit_file(kind, uid)
    config = inputs["raw"][path]
    meta = _meta(config, path, problems)
    return config, meta, {"id": uid, "kind": kind, "name": str(meta.get("name") or uid), "file": path,
                          "personal": paths.personal(uid), "category": meta.get("category") or "",
                          "description": meta.get("description") or "", **_placed(uid, path, inputs),
                          # What the judgement settles (_judge_units): nothing is against a unit that is not judged
                          "blocked": None, "needs_catalogs": [], "compat": "ok", "twin": None, "uses_machine": [],
                          "verdict": None}


def _addon(uid, inputs, core_names, problems):
    config, meta, unit = _unit("addon", uid, inputs, problems)
    needs = _brought(inputs["includes"], unit["file"])
    others = _configs(inputs, needs)
    shared = {v["key"] for v in inputs["variables"].get(CORE_FILE, [])}
    return {**unit, "requires": meta.get("requires") or {}, "conflicts": _conflicts(meta, unit["file"], problems),
            "gateway": _gateway(meta, config, unit["file"], problems),
            "needs": needs, "services": _own(unit["file"], config, others, core_names),
            "restarts": _restarts(config, others, core_names), "env_file": paths.unit_env("addon", uid),
            # Its own settings: image variables belong to the Images page, the core's variables to the core
            "settings": _described(inputs["variables"].get(unit["file"], []), _image_variables(config) | shared)}


def _setup(uid, inputs, problems):
    _, _, unit = _unit("setup", uid, inputs, problems)
    return {**unit, "includes": _ids(inputs["includes"].get(unit["file"], []), "addon"),
            "members": _brought(inputs["includes"], unit["file"])}


def _accounts(uid, accounts, inputs):
    """The accounts of a playground, with the settings in their values replaced by what they are now, as Compose would
    read them: the Lab UI's environment, then the root .env, then the playground's own .env (Compose gives the root
    one precedence over an included file's: the playground's only applies where the root sets nothing), then the
    default the value gives. Only text is read; what else a file writes stays as it is."""
    sources = [inputs["environ"], inputs["env"].get(ENV_FILE) or {},
               inputs["env"].get(paths.unit_env("playground", uid)) or {}]

    def lookup(name):
        return next((values[name] for values in sources if name in values), None)

    def read(value):
        return compose.interpolated(value, lookup) if isinstance(value, str) else value
    if not isinstance(accounts, list):
        return accounts
    return [{key: read(value) for key, value in account.items()} if isinstance(account, dict) else account
            for account in accounts]


def _playground(uid, inputs, core_names, problems):
    config, meta, unit = _unit("playground", uid, inputs, problems)
    addons = _brought(inputs["includes"], unit["file"])
    others = _configs(inputs, addons)
    own = _own(unit["file"], config, others, core_names)
    seeds = [n for n in own if _labels(_services_of(config)[n]).get("io.gravitee.role") == "seed"]
    services = [n for n in own if n not in seeds] + seeds   # the seed runs once, at the end: it is listed last
    guide = inputs["guides"].get(uid)
    shared = {v["key"] for v in inputs["variables"].get(CORE_FILE, [])}
    return {**unit, "env_file": paths.unit_env("playground", uid),
            # Its own settings, as an add-on's: how its apps come (built here or pulled), its demo values
            "settings": _described(inputs["variables"].get(unit["file"], []), _image_variables(config) | shared),
            "tags": meta.get("tags") or [], "source": meta.get("source"),
            "duration": meta.get("duration"), "first_start": meta.get("first_start"),
            "requires": meta.get("requires") or {}, "tested": meta.get("tested") or [],
            "accounts": _accounts(uid, meta.get("accounts") or [], inputs),
            # Its UIs next to the guide, in order: services, each shown by its first console
            "consoles": meta.get("consoles") or [],
            # The service whose container holds the terminal beside its guide, if it has one
            "terminal": meta.get("terminal"),
            # The setups and the add-ons it includes itself, and every add-on it brings: those, theirs and its setups'
            "setups": _ids(inputs["includes"].get(unit["file"], []), "setup"),
            "includes": _ids(inputs["includes"].get(unit["file"], []), "addon"), "addons": addons,
            # Its own services: the apps, and the seed that creates its data in Gravitee
            "services": services, "apps": [n for n in services if n not in seeds],
            "seed": seeds[0] if seeds else None, "restarts": _restarts(config, others, core_names),
            # Its guide by a version of its text, which changes with it; None without one
            "guide": hashlib.sha256(guide.encode()).hexdigest()[:12] if guide is not None else None,
            "chapters": [c.strip() for c in CHAPTER.findall(guide or "")]}


def _owners(core, addons, playgrounds):
    """Who runs each service, where its links are listed, and the file that defines it: (owner, section, file). A service that two
    units write, the twins of one item, has the last one for its owner and every one of them in `claims`: {service: [owner]}."""
    owners, claims = {}, {}
    for unit in [*addons.values(), *playgrounds.values()]:
        if unit["blocked"]:   # it cannot start, so it never shows in Containers
            continue
        owner = {"kind": unit["kind"], "id": unit["id"], "name": unit["name"]}
        section = unit["category"] if unit["kind"] == "addon" else "Playground"
        owners.update({name: (owner, section, unit["file"]) for name in unit["services"]})
        for name in unit["services"]:
            claims.setdefault(name, []).append(owner)
    for name, service in _services_of(core).items():
        group = _labels(service).get("io.gravitee.group") or ""
        owners[name] = ({"kind": "platform", "id": paths.slug(group), "name": group}, group, CORE_FILE)
        claims[name] = [owners[name][0]]
    return owners, claims


def _consoles(labels):
    """The consoles a service declares with io.gravitee.console.<id>.name / .url (and .legacy) labels; one whose url is
    no web address (http or https) is none: the page opens it as a link, and `make check` says it."""
    found = {}
    for key, value in labels.items():
        m = CONSOLE_LABEL.fullmatch(key)
        if m:
            found.setdefault(m.group(1), {})[m.group(2)] = value
    consoles = [{"label": c["name"], "url": c["url"], "legacy": c.get("legacy") == "true"}
                for c in found.values() if "name" in c and re.match(r"https?://", str(c.get("url", "")), re.I)]
    return sorted(consoles, key=lambda c: (c["legacy"], c["label"]))


def tag(image):
    """The tag an image names: what follows the colon of its last segment, `latest` without one. An image that Docker
    reports as its id or digest (what a tag that a pull moved becomes: `sha256:3f2a...`, `redis@sha256:...`) names
    none: ""."""
    if image.startswith("sha256:") or "@sha256:" in image:
        return ""
    last = image.split("/")[-1]
    if ":" in last:
        return last.split(":")[-1]
    return "latest" if image else ""


def _registry(raw_image, image):
    """The registry an image is pulled from, when a variable chooses it: ${REGISTRY}/repository:${VERSION}."""
    if not raw_image.startswith("${"):
        return None
    segments = len(compose.outside(raw_image, "/"))
    return "/".join(image.split("/")[:-segments]) if segments else None


def _repository(service):
    """What image a service runs, whatever registry and tag it is taken with: the repository after the registry a
    variable chooses (graviteeio/apim-gateway:4.12.21 and graviteeio.azurecr.io/apim-gateway:4.13.0 are both
    apim-gateway), else the whole name without its tag (redis). "" without an image."""
    image, registry = service.get("image") or "", service.get("registry")
    name = image[len(registry) + 1:] if registry and image.startswith(registry + "/") else image
    name = name.split("@")[0]
    last = name.rsplit("/", 1)[-1]
    return name[:len(name) - len(last)] + last.split(":")[0]


def _gateway_groups(addons, services, groups):
    """Sets `group` on the gateway of each add-on that runs one (x-gravitee.gateway): the group of the platform one of
    whose services runs the same image as the gateway's service, None when none does. The hybrid gateway runs API
    Management's gateway image: it is a gateway of API Management, so the pages list it, and the add-on's other
    services with it, in that group's section. It stays an add-on: on, off, its Start and its Stop are its own."""
    for addon in addons.values():
        gateway = addon["gateway"]
        if not gateway:
            continue
        image = _repository(services.get(gateway["service"]) or {})
        gateway["group"] = next((group["id"] for group in groups for name in group["services"]
                                 if image and _repository(services[name]) == image), None)


def _service(name, written, running, owner, project_name):
    """A service: `written` is its definition in its own file, `running` the one Compose runs."""
    labels = _labels(running)
    image, raw_image = running.get("image") or "", written.get("image") or ""
    # Its own ports: the configuration of everything also holds the ones add-ons publish on a core service
    own = {p.get("target") for p in written.get("ports") or []}
    ports = {str(p["published"]) for p in running.get("ports") or [] if p.get("published") and p.get("target") in own}
    # The port inside the container for each one it publishes: how another container of the lab reaches it
    targets = {str(p["published"]): str(p["target"]) for p in running.get("ports") or []
               if p.get("published") and p.get("target") in own}
    restart = running.get("restart") or "no"
    command = running.get("command") or ""
    return {"label": labels.get("io.gravitee.name") or name, "description": labels.get("io.gravitee.description") or "",
            "container": running.get("container_name") or f"{project_name}-{name}-1", "owner": owner,
            "image": image, "raw_image": raw_image, "tag": tag(image), "registry": _registry(raw_image, image),
            "image_tags": labels.get("io.gravitee.image.tags"),   # the repository that lists its tags, when not its own
            "ports": sorted(ports, key=lambda p: (len(p), p)), "targets": targets, "consoles": _consoles(labels),
            # Runs once then exits (migrations, seeds): done, not stopped, when it exits with 0
            "oneshot": restart == "no" and not ports,
            "depends_on": sorted(running.get("depends_on") or {}),   # what Compose starts first
            "command": " ".join(command) if isinstance(command, list) else str(command),
            # A setting of its own names the Lab UI's pages: how a UI that is configured, not patched, lets them frame it
            "lets_frame": any(all(page in str(value) for page in frames.PAGES) for value in (running.get("environment") or {}).values()),
            "built": bool(running.get("build")), "pull_policy": running.get("pull_policy"),
            "role": labels.get("io.gravitee.role"),
            "needs_license": labels.get(LICENSE_LABEL) == "required"}


def _products(services, running):
    """The products that load plugins: a service that mounts plugins/<product> loads that product's. The bundled
    plugins sit next to the mount (plugins-ext -> plugins), the license model in lib/."""
    products = {}
    for name in sorted(services):
        for volume in running[name].get("volumes") or []:
            source, target = Path(volume.get("source") or ""), volume.get("target") or ""
            # Compose gives the path as written, the project's own is resolved: /var and /tmp are links on macOS
            if volume.get("type") == "bind" and source.resolve().parent == ROOT / "plugins":
                product = products.setdefault(source.name, {"name": services[name]["owner"]["name"], "services": {},
                                                            "folder": f"plugins/{source.name}", "lib": {}})
                product["services"][name] = target[:-len("-ext")] if target.endswith("-ext") else target
                product["lib"][name] = str(PurePosixPath(target).parent / "lib")
    return products


def _shared_image(services):
    """The registry and version most Gravitee images of a group share (third-party images have no registry)."""
    shared = Counter((s["registry"], s["tag"]) for s in services if s["registry"]).most_common(1)
    return shared[0][0] if shared else (None, None)


def _groups(names, core, services, products, core_variables):
    groups = []
    for name in names:
        members = sorted(n for n in _services_of(core) if services[n]["owner"]["name"] == name)
        used = {key for n in members for key, _, _ in compose.references(json.dumps(core["services"][n]))}
        registry, version = _shared_image([services[n] for n in members])
        groups.append({"id": paths.slug(name), "name": name, "services": members, "version": version,
                       "registry": registry, "file": CORE_FILE, "env_file": ENV_FILE,
                       "product": next((p for p in products if set(products[p]["services"]) & set(members)), None),
                       "settings": [v["key"] for v in core_variables if v["key"] in used]})
    return groups


def _license_readers(core):
    """The core services that read the license, {service: the variable it comes from}: their environment sets
    LICENSE_KEY from a variable."""
    found = {}
    for name in sorted(_services_of(core)):
        environment = core["services"][name].get("environment") or {}
        references = compose.references(environment.get(LICENSE_KEY) or "")
        if references:
            found[name] = references[0][0]
    return found


def _license_var(core):
    """The variable that holds the license: the one the first core service that reads the license reads."""
    return next(iter(_license_readers(core).values()), None)


def _port(url):
    m = URL_PORT.match(url)
    return m.group(1) if m else None


def _links(services, owners):
    """Every console, the current ones first: the section is the platform group, the add-on's category or Playground."""
    links = [{"label": c["label"], "url": c["url"], "port": _port(c["url"]), "service": name,
              "section": owners[name][1], "legacy": c["legacy"]}
             for name, service in services.items() for c in service["consoles"]]
    return sorted(links, key=lambda link: (link["legacy"], link["label"]))


# ---------------------------------------------------------------------------------------------------------------------
# Where a playground can be deployed: judged from what it is, never declared
# ---------------------------------------------------------------------------------------------------------------------

def _core_services(services):
    """The services of the core."""
    return {name for name, service in services.items() if service["owner"]["kind"] == "platform"}


def _control_plane(project):
    """The services of API Management on this machine that a platform in the Cloud runs for it: its group of the core, but
    the gateway, which a platform runs here for itself (platforms.retarget_names)."""
    local = platforms.local_gateway(project)
    owner = (project["services"].get(local) or {}).get("owner") if local else None
    return {name for name, service in project["services"].items()
            if owner and service["owner"] == owner and name != local}


def apart(uids, project, strict=False):
    """What these add-ons do to this machine's platform, which a deploy to another platform does not do: (the add-on, the
    services of the control plane it changes, [(its service, the core service it waits for)]), for each one that does
    either. What an add-on sets on the gateway, or waits for it, is no such thing: it is written for the gateway of the
    platform. `strict`: for the add-on that runs a platform's gateway, which touches no service of the core, the gateway
    included."""
    core, plane = _core_services(project["services"]), _control_plane(project)
    for uid in uids:
        addon = project["addons"].get(uid)
        if not addon:   # its file does not load
            continue
        waits = [(name, needed) for name in addon["services"]
                 for needed in sorted(set(project["services"][name]["depends_on"]) & (core if strict else plane))]
        changes = [name for name in addon["restarts"] if name in (core if strict else plane)]
        if changes or waits:
            yield uid, changes, waits


def _hosts(service):
    """The host of each URL that the environment and the command of a service (as its file writes it) name."""
    command = service.get("command") or ""
    texts = [*(str(value) for value in (service.get("environment") or {}).values()),
             " ".join(command) if isinstance(command, list) else str(command)]
    return [host for text in texts for host in URL_HOST.findall(text)]


def _uses_the_core(playground, project, raw):
    """Whether an app waits for a service of this machine's API Management that a platform in the Cloud runs, or calls
    one: its Management API and its consoles. What it uses of the rest of the platform (Access Management, the
    databases) runs here with the deploy (layers.local_needs), and the gateway is the platform's own."""
    services = project["services"]
    away = _control_plane(project)
    written = (raw.get(playground["file"]) or {}).get("services") or {}
    return any(set(services[app]["depends_on"]) & away or away & set(_hosts(written.get(app) or {}))
               for app in playground["apps"])


def _minor(version):
    """(major, minor) of a version: 4.13.0-alpha.3 is (4, 13); None for text without them."""
    found = re.match(r"(\d+)\.(\d+)", version or "")
    return tuple(map(int, found.groups())) if found else None


def asks(playground, project, raw, seed, uses=None):
    """What a playground asks of a platform it is deployed to, from its files: {deploys: its seed has something to
    deploy; products: those its seed fills or its images name, in the order of SEEDED; versions: [product, version] of
    the images it requires; fills: the products its seed fills; plugins: it requires plugins; changes: it, or what it
    brings, changes the control plane of this machine (what it sets on the gateway goes to the gateway of the platform);
    uses: its apps use a service of this machine's platform but its gateway; more: its seed
    fills something that is no product}. The core's group names the product of an image. `seed` is what its seed folder
    holds, `raw` the files as written, `uses` the plugins its seed's APIs need, by kind (_seed_uses): `needs`."""
    asked, wanted = [SEEDED[name] for name in seed if name in SEEDED], []
    for variable, version in (playground["requires"].get("images") or {}).items():
        owner = next((service["owner"]["name"] for _, service in sorted(project["services"].items())
                      if service["owner"]["kind"] == "platform" and "${" + variable in service["raw_image"]), None)
        if owner in SEEDED.values():
            asked.append(owner)
            wanted.append([owner, version])
    # The platform has to run what runs on it, not what runs here
    asked = [product for product in asked if product not in LOCAL_PRODUCTS]
    wanted = [[product, version] for product, version in wanted if product not in LOCAL_PRODUCTS]
    rank = list(dict.fromkeys(SEEDED.values()))
    brought = list(apart(playground["addons"], project))
    return {"deploys": bool(playground["seed"] and seed), "products": sorted(set(asked), key=rank.index),
            "versions": wanted, "fills": sorted({SEEDED[name] for name in seed if name in SEEDED}, key=rank.index),
            "plugins": any((playground["requires"].get("plugins") or {}).values()),
            "changes": bool(set(playground["restarts"]) & _control_plane(project) or any(changes for _, changes, _ in brought)),
            "uses": any(waits for _, _, waits in brought) or _uses_the_core(playground, project, raw),
            "more": any(name not in ("apim", *SEEDED_HERE) for name in seed),
            "features": _apim_features(playground["requires"]),
            "needs": {kind: list((uses or {}).get(kind, [])) for kind in PLUGIN_KINDS}}


def unit_asks(kind, unit, project):
    """What an add-on or a setup asks of a platform it runs on, from its files, in the shape of what a playground asks
    (asks): it seeds nothing and has no APIs, so what it asks is that the platform's control plane be left alone (`changes`:
    it, or what it brings, sets a service of API Management's control plane) and that it wait for none (`uses`). What it
    sets on the gateway, or waits for it, goes to the gateway of the platform. The add-on that runs a platform's gateway
    asks for nothing: it is the gateway."""
    ids = [unit["id"]] if kind == "addon" else list(unit.get("members") or [])
    ids = [*ids, *(uid for uid in (brought(unit) if kind == "addon" else ()) if uid not in ids)]
    gateway = any((project["addons"].get(uid) or {}).get("gateway") for uid in ids)
    found = [] if gateway else list(apart(ids, project))
    features = sorted({feature for uid in ids for feature in _apim_features((project["addons"].get(uid) or {}).get("requires"))})
    return {"deploys": True, "products": [], "versions": [], "fills": [], "plugins": False,
            "changes": any(changes for _, changes, _ in found), "uses": any(waits for _, _, waits in found),
            "more": False, "features": features, "needs": {kind_: [] for kind_ in PLUGIN_KINDS}}


def _lacks(asked, products):
    """What a playground asks (asks) of the products an environment runs, as reasons: the products it does not run,
    then those it runs at an older version than asked. A version the environment does not say is not compared."""
    older = [f"Needs {product} {'.'.join(map(str, _minor(version)))}" for product, version in asked["versions"]
             if product in products and _minor(version) and _minor(products[product])
             and _minor(products[product]) < _minor(version)]
    return [*(f"Needs {product}" for product in asked["products"] if product not in products), *dict.fromkeys(older)]


def _lacks_plugins(asked, installed):
    """What the plugins that a playground's APIs use lack in the Management API of an environment (`installed`: by kind,
    {id: whether the license of the organization deploys it}): the ones it has not, then the ones it has and the license
    does not deploy. Reasons, in the order the kinds of plugin are named."""
    missing, undeployed = [], []
    for kind in PLUGIN_KINDS:
        have = installed.get(kind) or {}
        for plugin in asked.get("needs", {}).get(kind, []):
            if plugin not in have:
                missing.append(f"Needs the {plugin} {kind}")
            elif have[plugin] is False:
                undeployed.append(f"The license lacks the {plugin} {kind}")
    return missing + undeployed


def judge(asked, offers):
    """Why a playground that asks this (asks) cannot be deployed to an environment that offers this
    (platforms.capabilities: `products`, `plugins`), the most basic reason first: nothing to deploy, a product or a
    version the environment does not run, a plugin, this machine's core changed or used, more than a deploy fills. []
    when it can go. `products` None: what the environment runs is not known (it has no gateway, or its platform cannot
    be read), and nothing is compared to it. Every deploy to a platform leaves this machine's core alone, so the rules
    about the core hold for each kind."""
    products = offers.get("products")
    found = [] if asked["deploys"] else ["Nothing to deploy"]
    if products is not None:
        found += _lacks(asked, products)
    if offers.get("installed"):   # what the Management API of the environment has, when its Cloud Token says
        found += _lacks_plugins(asked, offers["installed"])
    allowed = (offers.get("license") or {}).get("features")
    if allowed is not None:   # what the license of the environment's organization has, when its Management API says it
        missing = [feature for feature in asked.get("features", []) if feature not in allowed]
        if missing:
            found.append("The license lacks " + ", ".join(license.named(missing)))
    if asked["plugins"] and not offers.get("plugins"):
        found.append("Needs plugins on the gateway")
    if asked["changes"]:
        found.append("Changes this machine's Management API")
    if asked["uses"]:
        found.append("Its apps use this machine's Management API")
    # What only this machine's API Management serves: Gamma, controls, refills, a folder that is no product
    if asked["more"]:
        found.append("Seeds more than API Management and Access Management")
    return found


def reasons(playground, project, raw, seed, offers, uses=None):
    """Why a playground cannot be deployed to an environment that offers this (judge), from its files."""
    return judge(asks(playground, project, raw, seed, uses), offers)


def _deployable(playgrounds, project, inputs):
    """What each playground, add-on and setup asks of a platform (`asks`, `unit_asks`, which places.py judges against
    what each environment runs), and where it can be deployed besides this machine, judged on what does not depend on the
    environment: `deploys_to` the kinds of platform it can go to and `cannot` the first reason it cannot go to each of the
    others."""
    def judged(unit):
        found = {kind: judge(unit["asks"], {"products": None, "plugins": platforms.plugins(project, kind)})
                 for kind in platforms.KINDS}
        unit["deploys_to"] = [kind for kind, why in found.items() if not why]
        unit["cannot"] = {kind: why[0] for kind, why in found.items() if why}

    for playground in playgrounds.values():
        seed = (inputs.get("seeds") or {}).get(playground["id"], [])
        playground["asks"] = asks(playground, project, inputs["raw"], seed, (inputs.get("uses") or {}).get(playground["id"]))
        judged(playground)
    for kind, units in (("addon", project["addons"]), ("setup", project.get("setups") or {})):
        for unit in units.values():
            unit["asks"] = unit_asks(kind, unit, project)
            judged(unit)
            if unit.get("gateway"):   # the add-on that runs a platform's gateway is where it runs: the platform's pages
                unit["deploys_to"], unit["cannot"] = [], {}


# ---------------------------------------------------------------------------------------------------------------------
# Judging: what the units of a catalog may do, who keeps a name, what a unit lacks
# ---------------------------------------------------------------------------------------------------------------------

def _compat(level, contract, floor):
    """Whether this Lab reads a level: `ok` from `floor` to `contract`, `new` above, `old` below. A level that is not
    known is read."""
    if not isinstance(level, int) or isinstance(level, bool):
        return "ok"
    return "new" if level > contract else "old" if level < floor else "ok"


def _sound(name=""):
    """The verdict of a unit that nothing is against, or that is not judged: ok, with nothing to say."""
    return {"state": "ok", "reasons": [], "notes": [], "surface": trust.empty_surface(), "codes": [], "name": name}


def _with_stubs(inputs, default):
    """The inputs with the units that were not read (a text the screen refused, a closure that reaches one) loaded as empty
    files, so that they are listed with their reasons and bring no service; and with the default namespace the project is
    built for. Nothing of the given inputs is changed."""
    raw = dict(inputs["raw"])
    for file in (*(inputs.get("screened") or ()), *(inputs.get("unreadable") or ())):
        found = paths.unit_of(file)
        if found and file not in raw:
            raw[file] = {"x-gravitee": {"kind": found[0], "name": found[1].rsplit("/", 1)[-1]}, "services": {}}
    return {**inputs, "raw": raw, "default": default}


IMPORTED = "imported"   # the origin the items of every imported playground say: one place, whichever file they came from


def _placed(uid, path, inputs):
    """Where a unit comes from: its catalog, `origin` (the catalog's name and version, for a unit of a catalog that is not
    the first, and for one of the first once there is another catalog to tell it from; the units of this machine say none),
    whether it is `readonly` (a git catalog's), and the
    path a person reads, relative to its catalog: this machine's too (`playgrounds/p/compose.yml`, never the place of the
    local catalog on the disk). A unit of the layout before catalogs has no catalog and says its file."""
    tier = _tier(path, inputs.get("default"))
    if tier not in JUDGED:
        return {"catalog": paths.LOCAL if tier == "local" else None, "origin": None, "readonly": False,
                "display_file": path.split("/", 3)[3] if tier == "local" else path}
    ns = resolve.target_of(path)[0]
    row = (inputs.get("catalogs") or {}).get(ns) or {}
    several = len([name for name in (inputs.get("catalogs") or {}) if name != paths.LOCAL]) > 1
    origin = {"id": ns, "name": row.get("name") or ns, "version": row.get("version")} if tier == "other" or several else None
    if origin and registry.imported(ns):   # the imported playgrounds are one place to the person: Imported
        origin = {"id": IMPORTED, "name": "Imported", "version": None}
    return {"catalog": ns, "origin": origin, "readonly": row.get("kind") == "git", "display_file": path.split("/", 3)[3]}


def _rank(unit, catalogs):
    """How private what a unit is part of is: the audience its catalog measured; this machine's own is private."""
    return resolve.rank(None if unit["catalog"] == paths.LOCAL else (catalogs.get(unit["catalog"]) or {}).get("audience"))


def _judge_units(inputs, raw, core, addons, setups, playgrounds):
    """Judges the units of the catalogs and settles what the project says of each among the others (and the
    first half of 10): its verdict (`verdict`, and `blocked` when it cannot start), its level (`compat`), the catalogs it
    lacks (`needs_catalogs`), what of this machine it uses (`uses_machine`), the unit of this machine it is also (`twin`),
    and `gateway` cleared when its catalog may not declare one (N04). A unit that is blocked brings no service, and so is a
    unit of a catalog while the core cannot be read: it has nothing to be compared with, so nothing is let through. An add-on
    or a playground of this machine is judged too, as `local`: the policy blocks none of what the person wrote, but its notes
    (N01 to N03, N05) and its surface remain, so that a personal playground with a seed of its own is refused a Cloud Token as a
    catalog's is. Changes the units, which build() has just made. Answers {unit key: its own verdict, a unit key being (kind,
    id)}, for the summaries of the catalogs. Nothing is judged before catalogs."""
    default, catalogs = inputs["default"], inputs.get("catalogs") or {}
    units = {(kind, uid): unit for kind, group in (("addon", addons), ("setup", setups), ("playground", playgrounds))
             for uid, unit in group.items()}
    if default is None:
        return {}
    contract = inputs.get("contract") or {}
    level, floor = contract.get("contract", 1), contract.get("contract_min", 1)
    closures = {key: resolve.closure({"addon": addons, "setup": setups, "playground": playgrounds}, *key) for key in units}
    core_names, core_config = set(_services_of(core)), inputs.get("core_config")
    screened, unreadable, errors = inputs.get("screened") or {}, inputs.get("unreadable") or {}, inputs.get("errors") or {}
    configs, realpath = inputs.get("configs") or {}, inputs.get("realpath") or (lambda path: path)
    own_configs = inputs.get("own_configs") or {}   # the units of this machine alone with the core, for their notes
    core_variables = sorted({*(v["key"] for v in inputs["variables"].get(CORE_FILE, [])), *trust.reads([], core)})
    # What each unit defines itself, which the others' judgement reads (the services of what a unit includes)
    own_of = {key: list(unit["services"]) if "services" in unit else
              _own(unit["file"], raw[unit["file"]], _configs(inputs, _brought(inputs["includes"], unit["file"])), core_names)
              for key, unit in units.items()}
    tiers, names, owned, verdicts = {}, {}, {}, {}
    for key, unit in units.items():
        kind, uid, file, own = key[0], key[1], unit["file"], own_of[key]
        tier, meta = _tier(file, default), _meta(raw[file], file, [])
        tiers[key], row = tier, catalogs.get(unit["catalog"]) or {}
        listed = tier in JUDGED and bool(row.get("listed"))
        host_ok = tier in JUDGED and bool(trust.host_ok(listed, {"host": row.get("host") == "allowed"}))
        direct = inputs["includes"].get(file, [])
        names[key] = str(meta.get("name") or "")
        reached = (inputs.get("reach") or {}).get(file) or {}
        unit["needs_catalogs"] = resolve.catalog_needs(
            {"ns": unit["catalog"], "includes": reached.get("targets") or direct}, catalogs, reached.get("missing") or [])
        asked = (meta.get("requires") or {}).get("lab")
        wanted = asked if isinstance(asked, int) and not isinstance(asked, bool) else row.get("lab")
        unit["compat"] = _compat(wanted, level, floor)
        config = configs.get(file)
        owned[key] = {**resolve.owned_names(config or raw[file], core_config or core, own,
                                            [configs.get(t) or raw[t] for t in direct if t in raw]),
                      "name": unit["name"], "id": uid, "kind": kind, "tier": "other" if tier == "other" else
                      "default" if tier == "default" else "local",
                      "copy_of": meta.get("copy_of") if tier == "local" and isinstance(meta.get("copy_of"), str) else None}
        context = {"ns": unit["catalog"], "host_ok": host_ok, "listed": listed, "local": tier == "local",
                   "requires": sorted(row.get("requires_catalogs") or ()),
                   "shared_settings": (inputs.get("shared_settings") or {}).get(unit["catalog"]) or [],
                   "core_variables": core_variables, "license_variable": _license_var(core),
                   "closure_names": [other[1].rsplit("/", 1)[-1] for other in closures[key][1:]],
                   "closure_services": sorted({name for other in closures[key][1:] for name in own_of.get(other, ())}),
                   "seeder_images": [contract["seeder_image"]] if contract.get("seeder_image") else [],
                   "home": inputs.get("home"), "realpath": realpath, "level": wanted, "contract": level,
                   "contract_min": floor}
        if file in screened:
            context["screened"] = screened[file]
        if file in unreadable or file in errors:
            context["error"] = unreadable.get(file) or errors[file]
        if "gateway" in unit and not trust.declared_ok(context):   # N04: the key is cleared before any reader sees it
            unit["gateway"] = None
        if file in screened or file in unreadable:
            verdicts[key] = trust.judge({"kind": kind, "id": uid, "name": unit["name"], "meta": meta}, None, core_config,
                                        [], {}, {}, (), context)
        elif (config or own_configs.get(file)) is None and (tier == "local" or unit["needs_catalogs"]):
            # Not judged: the person's own that Compose did not read alone (a setup is not asked), or the unit was not
            # resolved for want of a catalog, and is judged when the catalog arrives
            verdicts[key] = _sound(unit["name"])
        else:
            config = config or own_configs.get(file)   # a unit of this machine: read alone, for its notes
            if core_config is None:   # nothing to compare it with, so nothing is let through: it cannot be read until the core can
                config, context["error"] = None, inputs.get("core_error") or "the platform cannot be read"
            if reached.get("missing") and "error" not in context:
                context["error"] = "it includes a file that is not there"
            # the units it brings that sit in its catalog, or in the default one: the source everyone has, judged on its own
            same = [other for other in closures[key][1:]
                    if other in units and units[other]["catalog"] in (unit["catalog"], paths.default_ns())]
            folder = ROOT / paths.unit_folder(kind, uid) if kind != "setup" else None
            roots = trust.build_roots(ROOT, folder, [ROOT / paths.unit_folder(*other) for other in same if other[0] != "setup"],
                                      inputs.get("products") or (), realpath=realpath)
            view = {"kind": kind, "id": uid, "name": unit["name"], "meta": meta, "includes": direct, "seed": None,
                    "references": (inputs.get("references") or {}).get(file, []),
                    "seed_references": (inputs.get("seed_references") or {}).get(file, [])}
            used = set(inputs["env"].get(paths.unit_env(kind, uid)) or ()) | set(inputs["env"].get(ENV_FILE) or ())
            overlaid = trust.overlays(raw[file], core_names, [raw[t] for t in direct if t in raw])
            verdicts[key] = trust.judge(view, config, core_config, own, overlaid, roots, used, context)
    order = _claims(inputs, core, units, tiers, owned, closures, verdicts)
    for key, unit in units.items():
        _settle(unit, [verdicts[other] for other in closures[key] if other in verdicts])
    _twins(units, tiers, own_of, names, order, catalogs, owned)
    return verdicts


def _claims(inputs, core, units, tiers, owned, closures, verdicts):
    """Who keeps a name (T21) and the audience of what a unit brings (T24): the lines are added to the verdicts.
    The platform claims first, then the default catalog's units, this machine's, and the other catalogs' in the order they
    were added; only a unit of another catalog loses a name, and a unit that is blocked already claims none. Answers the keys of
    the units in that order, which is also the order of precedence of everything else."""
    catalogs, default = inputs.get("catalogs") or {}, inputs["default"]
    core_config = inputs.get("core_config") or {}
    platform = ("platform", "core")
    owned[platform] = {"name": str(_meta(core, CORE_FILE, []).get("name") or "the platform"), "tier": "default",
                       "services": sorted(_services_of(core)),
                       "containers": sorted(service["container_name"] for service in _services_of(core_config or core).values()
                                            if isinstance(service.get("container_name"), str)),
                       "volumes": sorted(core_config.get("volumes") or core.get("volumes") or ())}
    by_namespace = {}
    for key, unit in units.items():
        by_namespace.setdefault(unit["catalog"], []).append(key)
    known = [default, paths.LOCAL, *(ns for ns in catalogs if ns not in (default, paths.LOCAL))]
    order = resolve.precedence_order(by_namespace, known)
    blocked = {key for key, verdict in verdicts.items() if verdict["state"] == "blocked"}
    for key, line in resolve.collisions([platform, *order], owned, closures, blocked).items():
        verdicts[key] = trust.add_reason(verdicts[key], "T21", line)
    for key, unit in units.items():
        if tiers[key] in JUDGED and any(
                other in units and resolve.audience_blocked(_rank(unit, catalogs), _rank(units[other], catalogs))
                for other in closures[key][1:]):
            verdicts[key] = trust.add_reason(verdicts[key], "T24", trust.line("T24"))
    return order


def _settle(unit, closure):
    """The verdict of a unit as a whole (T25): its own with a line for each unit it brings that is blocked. It says what a
    person sees: `blocked` (the lines, or None) and `uses_machine` (what the unit and what it brings use of this machine, with
    the reason each gave). A unit that is blocked brings no service: it never starts, so it is never in Containers."""
    found = trust.combine(closure)
    unit["verdict"] = found
    unit["blocked"] = {"reasons": list(found["reasons"])} if found["state"] == "blocked" else None
    unit["uses_machine"] = [{"service": use["service"], "what": list(use["what"]), "why": use["why"]}
                            for use in found["surface"]["uses_machine"]]
    if unit["blocked"]:
        unit.update({key: [] for key in ("services", "apps", "restarts") if key in unit})
        if "seed" in unit:
            unit["seed"] = None


def _twins(units, tiers, own_of, names, order, catalogs, owned=None):
    """The unit of this machine and the unit of a catalog that are the same item: each says the other as `twin`, {id, name}. The
    person's copy of a catalog unit (`copy_of`: resolve.copy_of) is the twin of that unit, and says so (`copy`: true); else a
    personal unit takes the first unit of a catalog, in the order of precedence, that is the same item (resolve.same_item). Nothing
    is stored."""
    def seen(key):
        return {"id": key[1], "kind": key[0], "name": names[key], "services": own_of[key],
                "copy_of": ((owned or {}).get(key) or {}).get("copy_of")}
    for key in order:
        if tiers[key] != "local":
            continue
        copied = next((other for other in order if tiers[other] in JUDGED and resolve.copy_of(seen(key), seen(other))), None)
        twin = copied or next((other for other in order if tiers[other] in JUDGED and other[0] == key[0]
                               and resolve.same_item(seen(key), seen(other))), None)
        if twin:
            there, copy = units[twin], {"copy": True} if copied else {}
            units[key]["twin"] = {"id": there["id"], "name": (catalogs.get(there["catalog"]) or {}).get("name") or there["catalog"], **copy}
            there["twin"] = there["twin"] or {"id": units[key]["id"], "name": "Personal", **copy}


def _apart(problems, default, given):
    """The problems of the files of a catalog's units, which are the catalog's and not the banner's: ([the banner's],
    {namespace: [the catalog's]}). A problem found while reading (`given`) is kept as it is."""
    banner, catalog = [], {ns: list(found) for ns, found in (given or {}).items()}
    for problem in problems:
        found = resolve.target_of(problem.get("file")) if default is not None else None
        if found and found[0] != paths.LOCAL:
            catalog.setdefault(found[0], []).append(dict(problem, display=problem["file"].split("/", 3)[3]))
        else:
            banner.append(problem)
    return banner, catalog


def _summaries(units, verdicts, catalogs, problems):
    """What the units found out about each catalog: {namespace: {id, name, kind, counts, blocked, uses_machine, needs,
    problems}}, for each catalog that has a unit, and for this machine's own area when it has one. The registry says the rest
    of a row (version, state, address); this is what only the project knows. `blocked` lists the units that cannot start
    and why, `uses_machine` the services that take more of this machine than a container does and the reason each gave, `needs`
    the catalogs its units include from that are not here."""
    found = {}
    for key, unit in units.items():
        ns = unit["catalog"]
        if ns is None:
            continue
        row = catalogs.get(ns) or {}
        one = found.setdefault(ns, {"id": ns, "name": row.get("name") or ns, "kind": row.get("kind"),
                                    "counts": {"playground": 0, "setup": 0, "addon": 0}, "blocked": [], "uses_machine": [],
                                    "needs": [], "problems": list(problems.get(ns) or [])})
        one["counts"][key[0]] += 1
        if unit["blocked"]:
            one["blocked"].append({"kind": key[0], "id": unit["id"], "name": unit["name"], "reasons": list(unit["blocked"]["reasons"])})
        one["uses_machine"] += [{"id": unit["id"], "name": use["label"], "service": use["service"], "what": list(use["what"]),
                                 "why": use["why"]} for use in ((verdicts.get(key) or {}).get("surface") or {}).get("uses_machine", [])]
        for need in unit["needs_catalogs"]:
            if not any(seen["id"] == need["id"] for seen in one["needs"]):
                one["needs"].append({field: need.get(field) for field in ("id", "name", "url", "min")})
    return found


def _opened(units, services):
    """N03: a console of a unit of a catalog whose address is not on this machine is not offered, for a view never opens an
    address that a catalog chose. Only the units whose verdict says so (they have such an address, or a check of one), and
    only the consoles that are not on this machine. A unit of this machine has the note too, and keeps all its consoles."""
    line = trust.line("N03")
    for unit in units:
        if unit.get("personal"):   # the person's own is not held back: its note is said, its consoles are all opened
            continue
        if line in ((unit.get("verdict") or {}).get("notes") or ()):
            for name in unit.get("services") or ():
                if name in services:
                    services[name]["consoles"] = [found for found in services[name]["consoles"]
                                                  if trust.local_address(found["url"])]


def _consoles_added(added, held):
    """The console labels (io.gravitee.console.<id>.name, .url, .legacy) that a unit of a catalog puts on a service of the
    core, those whose address is on this machine (N03): ({label: value}, whether it left out an address that is not). What the
    service already says is not the unit's."""
    consoles = {}
    for key, value in added.items():
        found = CONSOLE_LABEL.fullmatch(key)
        if found:
            consoles.setdefault(found.group(1), {})[key] = value
    kept, left = {}, False
    for labels in consoles.values():
        url = next((value for key, value in labels.items() if key.endswith(".url")), None)
        if url is not None and not trust.local_address(url):
            left = left or any(key not in held for key in labels)
        else:
            kept.update(labels)
    return kept, left


def _run_as(inputs, units, core_names):
    """The services as Compose runs them: the configuration of the units of this machine and of the core (the aggregate,
    `resolved`), and, for each service a unit of a catalog defines itself, the one its own configuration gave.
    What the pages read of a core service that a unit adds to is what the unit says of what it waits for and of how it is
    labeled, so those are put together as the aggregate of everything had them. A unit that cannot start brings nothing. The
    unit of a catalog is held to what it may add: what it waits for is a service of the project, and the labels it puts on a
    service of the core are the consoles on this machine (N03), which are all a view opens; the line of N03 is said of it
    when one was left out."""
    found, configs = dict(inputs["resolved"]), inputs.get("configs") or {}
    known = core_names | {name for unit in units for name in unit["services"]}
    line = trust.line("N03")
    for unit in units:
        if unit.get("blocked"):
            continue
        theirs = _tier(unit["file"], inputs.get("default")) in JUDGED   # a catalog's: the person's own is not held to anything
        services = _services_of(configs.get(unit["file"]) or {})
        found.update({name: services[name] for name in unit["services"] if name in services})
        for name in core_names & set(services) & set(found):
            service, mine = found[name], services[name]
            waits, held = service.get("depends_on") or {}, service.get("labels") or {}
            if isinstance(mine.get("depends_on"), dict) and isinstance(waits, dict):
                added = {key: value for key, value in mine["depends_on"].items() if key in known or not theirs}
                service = {**service, "depends_on": {**added, **waits}}
            if isinstance(mine.get("labels"), dict) and isinstance(held, dict):
                added, left = _consoles_added(mine["labels"], held) if theirs else (mine["labels"], False)
                service = {**service, "labels": {**added, **held}}
                notes = (unit.get("verdict") or {}).get("notes")
                if left and isinstance(notes, list) and line not in notes:
                    notes.append(line)
            found[name] = service
    return found


def _gateways(addons, default, catalogs):
    """State.platform.gateways: for each kind of platform, the id of the add-on that runs its gateway on this machine, or
    None. The first, in the order of precedence, that says it runs one and can start: not blocked, read by this Lab,
    missing no catalog (resolve.gateway_addon, the one definition)."""
    by_namespace = {}
    for uid, addon in addons.items():
        by_namespace.setdefault(paths.LOCAL if addon["personal"] else addon["catalog"] or "", []).append(("addon", uid))
    known = [default or "", paths.LOCAL, *(ns for ns in catalogs if ns not in (default, paths.LOCAL))]
    order = [uid for _, uid in resolve.precedence_order(by_namespace, known)]
    return {kind: resolve.gateway_addon(addons, kind, order) for kind in platforms.KINDS}


def _units_of(inputs, core_names, problems):
    """The add-ons, setups and playgrounds of the inputs, each as `_addon`, `_setup` and `_playground` make
    it, the keys of the conflicts of the add-ons settled to ids: ({id: add-on}, {id: setup}, {id: playground})."""
    ids = {kind: _ids(sorted(inputs["raw"]), kind) for kind in KINDS}
    addons = {uid: _addon(uid, inputs, core_names, problems) for uid in ids["addon"]}
    setups = {uid: _setup(uid, inputs, problems) for uid in ids["setup"]}
    playgrounds = {uid: _playground(uid, inputs, core_names, problems) for uid in ids["playground"]}
    _settle_conflicts(addons)
    return addons, setups, playgrounds


def build(inputs):
    """The Project, from what gather() read. Pure: no disk, no Docker, but for the default namespace (paths) and for the
    `realpath` that gather hands in, through which the policy follows a link in a bind. A unit of a catalog is judged;
    one that is blocked brings no service."""
    default = inputs["default"] if "default" in inputs else paths.default_ns()
    inputs = _with_stubs(inputs, default)
    raw, problems = inputs["raw"], list(inputs["problems"])
    core = raw.get(CORE_FILE) or {}
    meta, core_names = _meta(core, CORE_FILE, problems), set(_services_of(core))
    addons, setups, playgrounds = _units_of(inputs, core_names, problems)
    verdicts = _judge_units(inputs, raw, core, addons, setups, playgrounds)

    owners, claims = _owners(core, addons, playgrounds)
    written = {name: raw[file]["services"][name] for name, (_, _, file) in owners.items()}
    # As Compose runs it; as written when the project does not load as a whole
    resolved = _run_as(inputs, [*addons.values(), *playgrounds.values()], core_names)
    running = {name: resolved.get(name) or written[name] for name in owners}
    services = {name: {**_service(name, written[name], running[name], owners[name][0], core.get("name") or ""),
                       "owners": claims[name]} for name in sorted(owners)}
    _opened([*addons.values(), *playgrounds.values()], services)
    # A service that runs the image another one builds runs a build of here too
    built = {service["image"] for service in services.values() if service["built"] and service["image"]}
    for service in services.values():
        service["built"] = service["built"] or service["image"] in built
    products = _products(services, running)
    _deployable(playgrounds, {"services": services, "addons": addons, "setups": setups, "products": products}, inputs)
    names = meta.get("groups") or []
    problems += [{"file": CORE_FILE, "message": f"{name}: io.gravitee.group must be one of x-gravitee.groups"}
                 for name in sorted(core_names) if services[name]["owner"]["name"] not in names]
    core_variables = _described(inputs["variables"].get(CORE_FILE, []), _image_variables(core))
    groups = _groups(names, core, services, products, core_variables)
    _gateway_groups(addons, services, groups)
    catalogs = inputs.get("catalogs") or {}
    banner, apart = _apart(problems, default, inputs.get("catalog_problems"))
    units = {(unit["kind"], unit["id"]): unit for group in (addons, setups, playgrounds) for unit in group.values()}
    return {"name": meta.get("name") or core.get("name") or "", "categories": meta.get("categories") or [],
            # Compose reads every other file on top of the core's: without it, nothing starts or stops
            "core_loaded": CORE_FILE in raw, "license_var": _license_var(core),
            "license_services": sorted(_license_readers(core)), "license_usable": inputs["license_usable"],
            "groups": groups, "products": products,
            "addons": addons, "setups": setups, "playgrounds": playgrounds,
            "services": services, "links": _links(services, owners),
            "core_variables": core_variables, "problems": banner,
            # What the catalogs add: what the units found out about each catalog, the problems of their files, which
            # are not the banner's, and the add-on that runs the gateway of each kind of platform
            "catalogs": _summaries(units, verdicts, catalogs, apart) if default is not None else {},
            "catalog_problems": apart, "platform": {"gateways": _gateways(addons, default, catalogs)}}


# ---------------------------------------------------------------------------------------------------------------------
# Judging a tree before it is installed: what the Add and the Update preview
# ---------------------------------------------------------------------------------------------------------------------

def judge_inputs(inputs):
    """The units of the inputs and the own verdict of each: ({kind: {id: unit}}, {(kind, id): verdict}).
    It is the first half of `build`, which a preview needs and the state does not: the policy applied to every unit, the
    names settled among them, what they lack, with the verdict of each unit alone (its surface is what it does itself, not
    what it brings). Nothing of `inputs` is changed."""
    default = inputs["default"] if "default" in inputs else paths.default_ns()
    inputs = _with_stubs(inputs, default)
    raw = inputs["raw"]
    core = raw.get(CORE_FILE) or {}
    addons, setups, playgrounds = _units_of(inputs, set(_services_of(core)), [])
    verdicts = _judge_units(inputs, raw, core, addons, setups, playgrounds)
    return {"addon": addons, "setup": setups, "playground": playgrounds}, verdicts


def _alias(ns):
    """A function that gives the name a catalog will have to every path and text it is read under while it is staged:
    `.lab/catalogs/.stage-<ns>` becomes `.lab/catalogs/<ns>`, in a key, a text, a list or a mapping, whether the path is the
    project's or an absolute one that Compose printed. The stage and the install are at the same depth (rule 1 of),
    so nothing else of a path differs, and the policy judges the unit where it will be."""
    stage = re.compile(rf"\.lab/catalogs/\.stage-{re.escape(ns)}(?![\w.-])")
    final = f".lab/catalogs/{ns}"

    def swap(value):
        if isinstance(value, str):
            return stage.sub(lambda _: final, value)
        if isinstance(value, list):
            return [swap(item) for item in value]
        if isinstance(value, dict):
            return {swap(key): swap(item) for key, item in value.items()}
        return value
    return swap


def staged_units(ns):
    """[(kind, name, file)] of the units of the staged tree of `ns` (`.lab/catalogs/.stage-<ns>`), by listing its folders: the
    stage holds exactly the files that were judged worth writing, a unit that does not read included."""
    folder = f".stage-{ns}"
    return [(kind, name, paths.CATALOG_FILES[kind].format(ns=folder, name=name))
            for kind in KINDS for name in paths.names(folder, kind)]


def _read_staged(ns, units):
    """The first half of what `gather` reads of units, read of the staged ones: the screen of each text, and of a
    playground's seed folder. Answers (the screen of each file, the seed scans, the lines of T19 for each file that failed,
    why a file cannot be read at all). The same functions as `gather`'s, so that a catalog is read the same before it is
    added and after."""
    files = [file for _, _, file in units]
    screen = {file: _screen(file) for file in files}
    seeds = {}
    for kind, name, file in units:
        if kind == "playground" and not screen[file][1] and not screen[file][2]:
            seeds[file] = _seed_scan(f"{paths.CATALOGS_DIR}/.stage-{ns}/playgrounds/{name}/seed")
    screened = {file: [*screen[file][1], *seeds.get(file, ((), ()))[1]] for file in files}
    unreadable = {file: screen[file][2] for file in files if screen[file][2]}
    return screen, seeds, screened, unreadable


def _staged_inputs(ns, meta, row, replacing):
    """The inputs of the project (`gather`'s) as they would be with the staged catalog installed as `ns`: the live ones, with
    the units of the stage read as `gather` reads a catalog's and put under the names they will have, and the catalog's
    row in `catalogs`. With `replacing`, the units the catalog has now are taken out first (an Update judges the new version in
    their place). Nothing of the live inputs is changed."""
    live = loaded()[1]
    swap, units = _alias(ns), staged_units(ns)
    final = f"{paths.CATALOGS_DIR}/{ns}/"
    screen, seeds, screened, unreadable = _read_staged(ns, units)
    own_bad = {file for _, _, file in units if screened[file] or file in unreadable}
    world = {file for file in live["raw"] if _catalog_path(file) and not (replacing and file.startswith(final))}
    world_bad = {file for file in {*(live.get("screened") or ()), *(live.get("unreadable") or ())}
                 if not (replacing and file.startswith(final))}
    bad, clean = own_bad | world_bad, ({file for _, _, file in units} - own_bad) | (world - world_bad)
    reach = {file: _reach(file, bad, clean) for _, _, file in units if file not in own_bad}
    held_by = {file: lines for file, found in reach.items() for lines in [held_reasons(found, bad)] if lines}
    stubs = own_bad | set(held_by)
    todo = [file for _, _, file in units if file not in stubs]
    with ThreadPoolExecutor(max_workers=8) as pool:
        read = dict(pool.map(lambda file: (file, _read_unit(file, screen[file][0], reach[file])), todo))

    def kept(key):
        return {path: value for path, value in (live.get(key) or {}).items() if not (replacing and path.startswith(final))}
    merged = {key: kept(key) for key in ("raw", "includes", "variables", "configs", "errors", "references",
                                         "seed_references", "reach", "screened", "unreadable")}
    problems = []
    for _, _, file in units:
        ours = swap(file)
        found = read.get(file)
        if found is None:
            continue
        if found["raw"] is not None:
            merged["raw"][ours] = swap(found["raw"])
            merged["includes"][ours] = [swap(target) for target in _direct(file, screen)]
            merged["variables"][ours] = []
            if file in seeds:
                merged["seed_references"][ours] = trust.seed_reads(seeds[file][0])
        elif found.get("problem"):
            problems.append({"file": ours, "display": ours.split("/", 3)[3],
                             "message": "Cannot be read: " + _plain(found["problem"]["message"])})
        if found.get("config") is not None:
            merged["configs"][ours] = swap(found["config"])
        if found.get("error"):
            merged["errors"][ours] = found["error"]
        if found.get("references"):
            merged["references"][ours] = found["references"]
    for file, found in reach.items():
        merged["reach"][swap(file)] = swap(found)
    merged["screened"].update({swap(file): lines for file, lines in screened.items() if file in own_bad and lines})
    merged["screened"].update({swap(file): lines for file, lines in held_by.items()})
    merged["unreadable"].update({swap(file): why for file, why in unreadable.items()})
    shared = {**(live.get("shared_settings") or {}), ns: list(meta.get("shared_settings") or [])}
    core, core_error = live.get("core_config"), live.get("core_error")
    if core is None and core_error is None:   # no unit of a catalog was there to judge, so the core was not read: it is now
        core, core_error = _core_config()
    return {**live, **merged, "catalogs": {**(live.get("catalogs") or {}), ns: row}, "shared_settings": shared,
            "catalog_problems": {**(live.get("catalog_problems") or {}), ns: problems}, "core_config": core,
            "core_error": core_error}


def judge_staged(ns, meta, row, replacing=False):
    """The judgement of the staged tree of catalog `ns` as the project would judge it installed under that name, against
    everything installed: ({kind: {id: unit}}, {(kind, id): own verdict}), the units
    of every catalog and of this machine, and the verdict of each unit alone. `meta` is the validated catalog.yml of the stage
    and `row` the catalog's row as `registry.inputs()` gives it (`staged_units` are the units to count in it). With
    `replacing`, the units the catalog has now are left out of the world first: an Update judges the new version in their
    place. Reads the disk and asks Compose, as `gather` does; changes nothing."""
    return judge_inputs(_staged_inputs(ns, meta, row, replacing))


def brought(unit):
    """The add-ons an add-on, a setup or a playground brings through its includes, and theirs: an add-on the ones it
    needs, a setup its members, a playground those it includes and those of its setups."""
    return unit[BROUGHT[unit["kind"]]]


_cache = {"key": None, "project": None, "inputs": None}
_lock = threading.RLock()   # reentrant: whoever holds it exclusively (exclusive) loads the project too


def loaded():
    """(the project, what gather() read to build it), read again only when one of its files changed, and each day: a
    license runs out with time, not with a file. Shared by every caller: never change them."""
    with _lock:
        key = f"{fingerprint()}:{datetime.date.today()}"
        if _cache["key"] != key:
            inputs = gather()
            _cache.update(project=build(inputs), inputs=inputs, key=key)
        return _cache["project"], _cache["inputs"]


def load():
    """The project, read again as loaded() says. Shared by every caller: never change it."""
    return loaded()[0]


def forget():
    """The project is read again at the next load: what Compose reads changed where no file did (a variable of the process).
    What was learned of each unit of a catalog is forgotten too, as it was read with that variable."""
    with _lock:
        _cache["key"] = None
        _option["known"] = None
        with _memo_lock:
            _memo.clear()


@contextlib.contextmanager
def exclusive():
    """The project's one lock, held: no one reads the project (loaded) or forgets it while a change of the catalogs is made.
    The lock order is the catalogs' (registry.LOCK, then its flock), then this one; whoever holds it can load and forget."""
    with _lock:
        yield


def _on_stamp():
    try:
        stat = (ROOT / paths.ON_FILE).stat()
    except FileNotFoundError:   # nothing is on
        return "none"
    return f"{stat.st_mtime_ns}-{stat.st_size}"


def config_key():
    """Changes when what Compose reads changes: a file of the project, or the on file."""
    return f"{fingerprint()}:{_on_stamp()}"


def saved(project):
    """What each service of what is on is, as saved: {hashes: {service: the hash of the configuration Compose would
    create it from}, images: {service: the id of the image its configured tag names here}}. A container that runs
    something else runs behind what is saved (model.behind). The hashes are asked again when a file of the project or
    the on file changed, and every few minutes for what else Compose may read; when they could not be had, at the
    next ask. The images after a job, or after a few seconds. Empty while what Compose reads does not load, or Docker
    does not answer: nothing is known to run behind."""
    if not project["core_loaded"]:
        return {"hashes": {}, "images": {}}
    hashes = compose.CACHE.get(f"config-hash:{config_key()}", 300, compose.config_hashes) or {}   # a failure is not kept
    held = compose.image_ids() or {}
    images = {name: held.get(compose.reference(service["image"])) for name, service in project["services"].items()
              if service["image"]}
    return {"hashes": hashes, "images": {name: image for name, image in images.items() if image}}
