"""The fixture catalogs of the tests, and the helpers that put them in a temporary project folder.

`data/catalogs/<ns>/` holds synthetic catalogs: `fixture` (the default catalog of the tests, so its ids are bare),
`partner` (its Private neighbour), `lab-new` and `lab-old` (a Lab level above and below the one this Lab reads);
`data/local/` is the personal area. Nothing in them is real: no item, address or organization. The helpers copy them,
never link them (unless a test asks for a link), because a container writes a `.env` or a `.state` beside a unit and a
test must not touch the repository.

    project(case, paths, compose)       a project folder: the core, its anchors, the default file, `fixture` installed
    default(case, ns)                   catalogs.default.json
    install(case, ns, kind="git", ...)  a catalog on disk, in catalogs.json and in catalogs.lock (and a bare remote and,
                                        on request, the Lab's bare store; a folder with a store is a shadow)
    remote(case, ns)                    the bare repository of a catalog, and nothing else
    declare(case, ns)                   a catalog the person added and whose download has not ended
    lock(case, ns, ...)                 the lock entry of a catalog, and its cached version and level
    local(case, name)                   a unit of data/local, in the personal area
    render(root, *files)                `docker compose config` of the core with unit files on top (never a daemon)

A patch that no state of the Lab can be (an unknown key, a value of the wrong type, `link` for a git catalog) is a
ValueError and writes nothing.

`python -m unittest tests.catalogs` (from lab-ui/) runs the checks of the fixtures and of these helpers. The file is not
named test_*, so discovery leaves them out of `make test`: they are run when the fixtures or the helpers change, and by
a test_* file that imports FixtureCatalogs, which belongs to the package that owns the test plan."""

import hashlib
import importlib
import io
import json
import os
import platform
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple, Optional
from unittest import mock

from lab_ui import paths

from . import Case

__all__ = ["CATALOGS", "DATA", "EXPECTED", "KINDS", "LOCAL", "Installed", "Remote", "RenderError", "catalog_meta",
           "closure", "core", "declare", "default", "expected", "have_compose", "have_git", "install", "local", "lock",
           "need_compose", "need_git", "project", "read_config", "read_lock", "remote", "render", "seeder_image",
           "unit_file", "unit_id", "unit_of", "unit_rel", "units"]

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
CATALOGS = DATA / "catalogs"
LOCAL = DATA / "local"
EXPECTED = DATA / "expected.json"
CORE = HERE.parent.parent / "docker-compose.yml"   # the engine's own core file: the one every project folder copies

KINDS = ("addon", "setup", "playground")
FOLDER = {"addon": "addons", "setup": "setups", "playground": "playgrounds"}
NS = re.compile(r"[a-z][a-z0-9-]{0,22}[a-z0-9]")   #
RESERVED_NS = {"local", "lab", "official", "core", "engine", "gravitee", "builtin", "api", "static", "all", "none",
               "this", "machine", "personal", "check", "new", "cache", "stage"}   #
SEEDER_PLACEHOLDER = "__SEEDER_IMAGE__"   # in a seed service of the fixtures; the core's seeder image takes its place
SEEDER_IMAGE = "registry.example.invalid/lab/seeder"   # that image while the core file does not say it
TEXT = (".yml", ".yaml", ".md", ".json")   # the files in which the placeholder is replaced
AUDIENCE = {"fixture": "public", "partner": "private", "lab-new": "public", "lab-old": "public"}   # what a lock says
BASE_MINUTES = 9 * 60   # every date is 2026-10-16 at 09:00 plus a count of minutes: a fixture is the same on every run
INSTALLED, CHECKED = "2026-10-16T09:00:30Z", "2026-10-16T09:01:00Z"

# What install() takes besides the catalog; each group says where its keys go
ENTRY_KEYS = {"url", "branch", "auto", "host", "added", "shadows"}   # the entry of catalogs.json
CATALOG_KEYS = {"name", "version", "lab"}   # catalog.yml, and the lock's cache of it
LOCK_KEYS = {"audience", "pin", "previous", "tip", "roots", "installed", "checked", "error", "pending", "via",
             "shadowed"}
CONTENT_KEYS = {"drop", "files"}   # paths left out of the copy; {path: text} added to it or replaced in it
PLACE_KEYS = {"link", "readonly", "store"}   # a folder catalog as a link; files read-only or not; the Lab's bare store
BOOLEAN = {"auto", "host", "link", "readonly", "store", "shadowed"}   # the keys whose value is true or false
TEXT_KEYS = {"url", "branch", "added"}   # the keys whose value is a text
LOCK_ORDER = ("pin", "previous", "tip", "roots", "audience", "name", "version", "lab", "installed", "checked", "error",
              "pending")   # the order of

# What is never installed from git. This is an independent statement of what
# `store.selected` does, and the checks below hold the two together
SEGMENT = re.compile(r"[A-Za-z0-9._+-]+")
UNIT_NAME = re.compile(r"[a-z0-9-]+")
AREAS = {"addons": "addon", "setups": "setup", "playgrounds": "playground"}
KEY_FILES = (".key", ".pem", ".p12", ".jks", ".crt", ".csr", ".srl")
AGENT_FILES = ("agents.md", "claude.md", "gemini.md")
RESERVED = ("secrets", ".state", ".git", ".claude", ".agents", ".gemini")
RESERVED_PREFIXES = (".cursor", ".windsurf")
SMALL, LARGE = 1024 * 1024, 2 * 1024 * 1024


class RenderError(RuntimeError):
    """Compose could not read the files: its last line."""


class Remote:
    """A bare git repository that serves a catalog (made by tests.gitfixture): its address, its tip, new commits. A test
    allows the `file` protocol with `gitfixture.isolate`; production never does."""

    def __init__(self, bare):
        self.bare = Path(bare)

    @property
    def url(self):
        return _gitfixture().url(self.bare)

    def git(self, *args):
        """The output of a git command run on the bare repository, without its final newline."""
        return _gitfixture().git(*args, repo=self.bare).strip()

    def head(self):
        return _gitfixture().tip(self.bare)

    def commit(self, files=None, message="Update the catalog", remove=()):
        """A new commit on main: `files` ({path: text}) laid over its tree, the paths of `remove` (a file, or a folder
        with all it holds) taken out of it. Answers its id."""
        changes = {_relative(rel): text for rel, text in (files or {}).items()}
        listed = self.git("ls-tree", "-r", "--name-only", "HEAD").splitlines()
        for rel in map(_relative, remove):
            gone = [path for path in listed if path == rel or path.startswith(rel + "/")]
            if not gone:
                raise ValueError(f"remove {rel!r}: the repository has no such path")
            changes.update(dict.fromkeys(gone))
        return _gitfixture().commit(self.bare, changes, message)


class Installed(NamedTuple):
    """What install() put on disk: the catalog and how, where, its two entries, and for git its remote and commit."""
    ns: str
    kind: str
    path: Path
    entry: dict
    lock: dict
    remote: Optional[Any]
    commit: Optional[str]


# ---------------------------------------------------------------------------------------------------------------------
# The fixtures as they are in the repository
# ---------------------------------------------------------------------------------------------------------------------

def _meta_of(text):
    """The scalars of the x-gravitee-catalog block of a catalog.yml, {id, name, description, version, license, schema,
    lab} (the last two as integers), without the comment a line ends with. Nothing else of the block is read."""
    found, inside = {}, False
    for line in text.split("\n"):
        if re.fullmatch(r"x-gravitee-catalog:[ \t]*", line):
            inside = True
        elif inside and line[:1] not in (" ", ""):
            break
        elif inside:
            # a value in quotes, or a plain one up to the comment a line may end with
            m = re.fullmatch(r"""  ([a-z_]+):[ \t]*("[^"]*"|'[^']*'|[^\s{\[#"'](?:[^#]*[^\s#])?)(?:[ \t]+#.*)?[ \t]*""", line)
            if m:
                key, value = m.groups()
                value = value[1:-1] if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"" else value
                found[key] = int(value) if key in ("schema", "lab") and value.lstrip("-").isdigit() else value
    return found


def catalog_meta(ns):
    """The block of the catalog.yml of a fixture catalog (_meta_of)."""
    return _meta_of((_base(ns) / "catalog.yml").read_text(encoding="utf-8"))


def _base(ns):
    """The folder of a fixture catalog, or of the personal area ("local")."""
    if ns == "local":
        return LOCAL
    if not (isinstance(ns, str) and NS.fullmatch(ns) and (CATALOGS / ns).is_dir()):
        known = ", ".join(sorted(p.name for p in CATALOGS.iterdir() if p.is_dir()))
        raise ValueError(f"{ns!r} is not a fixture catalog ({known})")
    return CATALOGS / ns


def unit_rel(kind, name):
    """The file of a unit inside its catalog: addons/<name>/compose.yml, setups/<name>.yml or
    playgrounds/<name>/compose.yml."""
    return f"setups/{name}.yml" if kind == "setup" else f"{FOLDER[kind]}/{name}/compose.yml"


def unit_file(ns, kind, name):
    """The project-relative file of a unit, the line the on file holds: .lab/catalogs/<ns>/..."""
    return f".lab/catalogs/{ns}/{unit_rel(kind, name)}"


def unit_id(ns, kind, name, default="fixture"):
    """The id the Lab gives a unit: bare in the default catalog, local/<name>, else @<ns>/<name>."""
    return name if ns == default else f"local/{name}" if ns == "local" else f"@{ns}/{name}"


UNIT_FILE = re.compile(rf"\.lab/catalogs/({NS.pattern})/(?:addons/([a-z0-9-]+)/compose\.yml"
                       r"|setups/([a-z0-9-]+)\.yml|playgrounds/([a-z0-9-]+)/compose\.yml)")


def unit_of(path, default="fixture"):
    """(kind, id) of a project-relative unit file under .lab/catalogs/<ns>/, None for any other path: a
    namespace is 2 to 24 characters of the NS grammar, and a reserved one (RESERVED_NS) is no namespace unless it is the
    default catalog's or the personal area's (local)."""
    found = UNIT_FILE.fullmatch(path) if isinstance(path, str) else None
    if not found:
        return None
    ns = found.group(1)
    if ns in RESERVED_NS and ns not in (default, "local"):
        return None
    kind, name = next((kind, name) for kind, name in zip(KINDS, found.groups()[1:]) if name)
    return kind, unit_id(ns, kind, name, default)


def units(ns):
    """[(kind, name)] of a fixture catalog or of the personal area ("local"), by kind then name: a folder that holds a
    compose.yml, or a setup file."""
    found = []
    for kind in KINDS:
        folder = _base(ns) / FOLDER[kind]
        for entry in sorted(folder.iterdir()) if folder.is_dir() else []:
            if kind == "setup" and entry.is_file() and entry.suffix == ".yml":
                found.append((kind, entry.stem))
            elif kind != "setup" and (entry / "compose.yml").is_file():
                found.append((kind, entry.name))
    return found


def expected():
    """expected.json: {"kind:id": {verdict, reasons, notes, surface, closure, ports, lint}} for fixture and partner."""
    return json.loads(EXPECTED.read_text(encoding="utf-8"))


def _includes(text):
    """The paths of the top-level `include:` list of a compose file written the way the fixtures write it."""
    found, inside = [], False
    for line in text.split("\n"):
        if line.rstrip() == "include:":
            inside = True
        elif inside and line[:1] not in (" ", ""):
            break
        elif inside:
            m = re.fullmatch(r"\s+-\s+(?:\"([^\"]+)\"|'([^']+)'|([^\s#'\"]+))\s*(?:#.*)?", line)
            if m:
                found.append(next(group for group in m.groups() if group))
    return found


def _direct(ns, kind, name, default="fixture"):
    """[(kind, id)] of the units a unit includes itself, read from its file."""
    file = unit_file(ns, kind, name)
    text = (_base(ns) / unit_rel(kind, name)).read_text(encoding="utf-8")
    targets = (posixpath.normpath(posixpath.join(posixpath.dirname(file), target)) for target in _includes(text))
    return [found for found in (unit_of(target, default) for target in targets) if found]


def closure(ns, kind, name, default="fixture"):
    """["kind:id", ...] of a unit and everything it includes, found in the files: the unit first, then its setups and
    then its add-ons, each sorted by id (the order layers.write turns them on in)."""
    first = unit_of(unit_file(ns, kind, name), default)
    seen, todo = {first}, [(ns, kind, name)]
    while todo:
        for one in _direct(*todo.pop(), default):
            if one not in seen:
                seen.add(one)
                space, _, unit = one[1].rpartition("/")
                todo.append(("local" if space == "local" else space[1:] if space else default, one[0], unit))
    rest = sorted(seen - {first}, key=lambda one: (one[0] != "setup", one[1]))
    return [f"{one[0]}:{one[1]}" for one in [first, *rest]]


# ---------------------------------------------------------------------------------------------------------------------
# Tools: a test that needs one and finds none says so on the screen
# ---------------------------------------------------------------------------------------------------------------------

def _skip(reason):
    """A skip that shows: a line on standard error, whether or not the run is verbose, then the skip itself."""
    print(f"SKIP {reason}", file=sys.stderr, flush=True)
    raise unittest.SkipTest(reason)


def have_git():
    """Whether git runs. On macOS /usr/bin/git is a shim that opens an installer when the Command Line Tools are
    missing: it is run only after `xcode-select -p` says they are there."""
    git = shutil.which("git")
    if not git:
        return False
    try:
        if platform.system() == "Darwin" and os.path.realpath(git) == "/usr/bin/git":
            tools = subprocess.run(["xcode-select", "-p"], capture_output=True, stdin=subprocess.DEVNULL, timeout=10)
            if tools.returncode:
                return False
        done = subprocess.run([git, "--version"], capture_output=True, stdin=subprocess.DEVNULL, timeout=10)
        return done.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


_compose = []   # the answer of have_compose(), once


def have_compose():
    """Whether `docker compose` is there and is version 5 or later. Rendering files needs no daemon."""
    if not _compose:
        found = False
        if shutil.which("docker"):
            try:
                done = subprocess.run(["docker", "compose", "version", "--short"], capture_output=True, text=True,
                                      stdin=subprocess.DEVNULL, timeout=20)
                number = re.search(r"(\d+)\.\d+", done.stdout)
                found = done.returncode == 0 and bool(number) and int(number.group(1)) >= 5
            except (OSError, subprocess.TimeoutExpired):
                found = False
        _compose.append(found)
    return _compose[0]


def _module(name, wp, *names):
    """The module `name` (importable as it stands), with the attributes `names` it must have. While the module does not
    exist the test is skipped, and says so (`wp` made it); once it exists, a name it lost, or an error inside it, is
    the failure it is: a check that went quiet because something was renamed is no check."""
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError as error:
        if not (error.name and (name == error.name or name.startswith(error.name + "."))):
            raise
        _skip(f"{name} does not exist yet ({wp})")
    gone = [one for one in names if not hasattr(module, one)]
    if gone:
        raise AssertionError(f"{name} has no {', '.join(gone)}: renamed or removed, and the check that reads it with it")
    return module


def _lab(name, wp, *names):
    """lab_ui.<name> (_module)."""
    return _module(f"lab_ui.{name}", wp, *names)


def _gitfixture():
    """tests.gitfixture, which makes the repositories: a skip that shows while it does not exist."""
    return _module(f"{__package__}.gitfixture", "the Lab", "make_repo", "git", "url", "tip", "commit")


def need_git():
    if not have_git():
        _skip("git is not installed, or is the macOS shim without the Command Line Tools")
    _gitfixture()


def need_compose():
    if not have_compose():
        _skip("the Docker CLI with Compose 5 or later is not installed")


# ---------------------------------------------------------------------------------------------------------------------
# The project folder
# ---------------------------------------------------------------------------------------------------------------------

def _root(case):
    root = getattr(case, "root", None)
    if root is None:
        raise RuntimeError("the case has no project folder: call project(case, ...) or case.folder(...) first")
    return Path(root)


def _scratch(case, name):
    """A folder of the case's own, outside its project folder and removed with it: the remotes of the git catalogs and
    the folders that linked catalogs point to. The same folder for the same name."""
    held = case.__dict__.setdefault("_fixture_scratch", {})
    if name not in held:
        holder = tempfile.TemporaryDirectory()
        case.addCleanup(holder.cleanup)
        held[name] = Path(holder.name).resolve()
    return held[name]


def _relative(rel):
    """A path inside a catalog, checked: relative, with no empty, `.` or `..` segment."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel or "\0" in rel:
        raise ValueError(f"{rel!r} is not a path inside a catalog")
    if any(part in ("", ".", "..") for part in rel.split("/")):
        raise ValueError(f"{rel!r} is not a path inside a catalog")
    return rel


def _stamp(minutes):
    total = BASE_MINUTES + minutes
    return f"2026-10-16T{total // 60 % 24:02d}:{total % 60:02d}:00Z"


def _write(file, text, mode=0o644):
    file.parent.mkdir(parents=True, exist_ok=True)
    paths.write_atomic(file, text, mode=mode)


def _json(value):
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _clear(path):
    """What stands at a path removed: a link, a file or a folder."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _core_seeder(text):
    """The x-gravitee.seeder.image of a core file, None when it does not say."""
    lines = text.split("\n")
    if "x-gravitee:" not in lines:
        return None
    for number in range(lines.index("x-gravitee:") + 1, len(lines)):
        line = lines[number]
        if line.strip() and line[:1] != " ":
            break
        flow = re.fullmatch(r"  seeder:[ \t]*\{[^}]*\bimage:[ \t]*([^\s,}]+)[^}]*\}[ \t]*", line)
        if flow:
            return flow.group(1)
        if re.fullmatch(r"  seeder:[ \t]*", line):
            for inner in lines[number + 1:]:
                if inner[:4] != "    ":
                    break
                m = re.fullmatch(r"    image:[ \t]*([^\s#]+)[ \t]*(?:#.*)?", inner)
                if m:
                    return m.group(1)
    return None


def _with_contract(text):
    """A core file with the keys of that it does not carry yet (contract 1, contract_min 1, the seeder
    image), added at the end of its x-gravitee block. What it carries is left as it is."""
    lines = text.split("\n")
    if "x-gravitee:" not in lines:
        raise ValueError("the core file has no x-gravitee block")
    start = lines.index("x-gravitee:")
    end = start + 1
    while end < len(lines) and (lines[end].startswith("  ") or not lines[end].strip()):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    have = {m.group(1) for line in lines[start + 1:end] for m in [re.match(r"  ([A-Za-z_][\w-]*):", line)] if m}
    wanted = (("contract", ["  contract: 1"]), ("contract_min", ["  contract_min: 1"]),
              ("seeder", ["  seeder:", f"    image: {SEEDER_IMAGE}"]))
    lines[end:end] = [line for key, block in wanted if key not in have for line in block]
    return "\n".join(lines)


def core(case, contract=True):
    """The engine's files a project folder holds: the real core (this repository's docker-compose.yml), with the keys of added when it does not carry them yet (`contract=False` copies it as it is), the anchors a unit may
    reach (seeder/Dockerfile, plugins/apim, plugins/am) and .lab/catalogs/. Answers the folder."""
    root = _root(case)
    text = CORE.read_text(encoding="utf-8")
    _write(root / paths.CORE_FILE, _with_contract(text) if contract else text)
    _write(root / "seeder" / "Dockerfile", "FROM scratch\n")
    for product in ("apim", "am"):
        (root / "plugins" / product).mkdir(parents=True, exist_ok=True)
    (root / paths.LAB_DIR / "catalogs").mkdir(parents=True, exist_ok=True)
    return root


def seeder_image(case):
    """The image a seed service has in the project folder: x-gravitee.seeder.image of its core file."""
    file = _root(case) / paths.CORE_FILE
    return (_core_seeder(file.read_text(encoding="utf-8")) if file.is_file() else None) or SEEDER_IMAGE


def project(case, *modules, ns="fixture", kind="folder", **patch):
    """A project folder for the case (case.folder(*modules): `modules` are the ones whose ROOT it replaces) with the
    engine's files (core), `ns` installed (install) and the default file naming it (default). For a catalog that comes
    from a bare repository (`kind="git"`) the default file lists the address of that repository and its branch, because
    the address of a listed catalog is the tracked one: the entry and the file agree. Answers the folder."""
    case.folder(*modules)
    core(case)
    done = install(case, ns, kind, **patch)
    served = (done.entry.get("shadows") if kind == "folder" else done.entry) if done.remote else None
    default(case, ns, **{key: served[key] for key in ("url", "branch") if served and served.get(key)})
    return case.root


def default(case, ns="fixture", also=(), **entry):
    """catalogs.default.json at the root of the project folder, listing `ns` (then those of `also`, in that order) as
    the default catalog: name and description come from its catalog.yml, the address is a synthetic https one that never
    answers (a test that serves the catalog gives `url=`). `entry` sets keys in every entry. Answers the path."""
    items = []
    for one in (ns, *also):
        meta = catalog_meta(one)
        items.append({"id": one, "name": meta.get("name", one), "description": meta.get("description", ""),
                      "url": f"https://example.invalid/{one}.git", "branch": "main", **entry})
    file = _root(case) / "catalogs.default.json"
    _write(file, _json({"schema": 1, "catalogs": items}))
    return file


def read_config(case):
    """.lab/catalogs.json as a dict, None while it does not exist."""
    file = _root(case) / paths.LAB_DIR / "catalogs.json"
    return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else None


def read_lock(case):
    """.lab/catalogs.lock as a dict, None while it does not exist."""
    file = _root(case) / paths.LAB_DIR / "catalogs.lock"
    return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else None


def _put_entry(case, entry, added=None):
    """The entry in catalogs.json: where an entry of the same id stands, else last. `added` is the minute
    of its place in the array unless given, so that array order is also the order of the dates. Answers the entry."""
    config = read_config(case) or {"schema": 1, "catalogs": []}
    entries = config["catalogs"]
    place = next((i for i, one in enumerate(entries) if one["id"] == entry["id"]), len(entries))
    entry = {**entry, "added": added or _stamp(place)}
    entries[place:place + 1] = [entry]
    _write(_root(case) / paths.LAB_DIR / "catalogs.json", _json(config), mode=0o600)
    return entry


def _put_lock(case, ns, fields):
    """The entry in catalogs.lock, its keys in the order of the spec. Answers the entry."""
    ordered = {key: fields[key] for key in LOCK_ORDER}
    ordered.update({key: value for key, value in fields.items() if key not in ordered})
    lock_ = read_lock(case) or {"schema": 1, "catalogs": {}}
    lock_["catalogs"][ns] = ordered
    _write(_root(case) / paths.LAB_DIR / "catalogs.lock", _json(lock_), mode=0o600)
    return ordered


def _check_patch(patch, allowed):
    """The keys of a patch are keys the call takes, and each value is of the type its key needs: a wrong one would be
    written to a file, and a test would then read a state no Lab makes."""
    unknown = sorted(set(patch) - allowed)
    if unknown:
        raise ValueError(f"no such key: {', '.join(unknown)}; the keys are {', '.join(sorted(allowed))}")
    for key in sorted(BOOLEAN & set(patch)):
        if not isinstance(patch[key], bool):
            raise ValueError(f"{key} is true or false, not {patch[key]!r}")
    for key in sorted(TEXT_KEYS & set(patch)):
        if not (isinstance(patch[key], str) and patch[key]):
            raise ValueError(f"{key} is a text, not {patch[key]!r}")
    if patch.get("lab") is not None and (isinstance(patch["lab"], bool) or not isinstance(patch["lab"], int)):
        raise ValueError(f"lab is a whole number, not {patch['lab']!r}")
    if patch.get("version") is not None and not (isinstance(patch["version"], str)
                                                 and re.fullmatch(r"\d+\.\d+\.\d+", patch["version"])):
        raise ValueError(f"version is three numbers with dots (1.4.0), not {patch['version']!r}")
    if patch.get("name") is not None and not (isinstance(patch["name"], str) and patch["name"]):
        raise ValueError(f"name is a text, not {patch['name']!r}")
    if "shadows" in patch and not isinstance(patch["shadows"], dict):
        raise ValueError(f"shadows is the git entry a folder stands in for ({{url, branch, added}}), not {patch['shadows']!r}")


def _blank_lock(ns):
    """A lock entry before anything was downloaded."""
    return {"pin": None, "previous": None, "tip": None, "roots": [], "audience": AUDIENCE.get(ns, "private"),
            "name": None, "version": None, "lab": None, "installed": None, "checked": None, "error": None,
            "pending": None}


def _tree(case, ns):
    """{path: bytes} of the files of a fixture catalog, the seeder placeholder replaced by the image of the core."""
    base, image, found = _base(ns), seeder_image(case).encode(), {}
    for file in sorted(base.rglob("*")):
        if file.is_file() and not file.is_symlink() and "__pycache__" not in file.parts:
            data = file.read_bytes()
            found[file.relative_to(base).as_posix()] = data.replace(SEEDER_PLACEHOLDER.encode(), image) \
                if file.suffix in TEXT else data
    return found


def _rewritten(text, fields):
    """A catalog.yml with these keys of its x-gravitee-catalog block set; each must be there once."""
    for key, value in fields.items():
        plain = isinstance(value, int) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .-]*", str(value))
        shown = str(value) if plain else json.dumps(value)
        text, count = re.subn(rf"(?m)^(  {key}:[ \t]*)[^\n]*$", lambda m: f"{m.group(1)}{shown}", text, count=1)
        if count != 1:
            raise ValueError(f"catalog.yml has no key {key}")
    return text


def _patched(tree, patch):
    """The tree with `drop` and `files` applied, then the name, version and lab of the patch written in catalog.yml."""
    tree = dict(tree)
    for rel in patch.get("drop", ()):
        rel = _relative(rel)
        gone = [path for path in tree if path == rel or path.startswith(rel + "/")]
        if not gone:
            raise ValueError(f"drop {rel!r}: the catalog has no such path")
        for path in gone:
            del tree[path]
    for rel, text in (patch.get("files") or {}).items():
        if not isinstance(text, (str, bytes)):
            raise ValueError(f"files {rel!r}: text or bytes (a link or a submodule is made with tests.gitfixture)")
        tree[_relative(rel)] = text if isinstance(text, bytes) else text.encode("utf-8")
    fields = {key: patch[key] for key in ("name", "version", "lab") if patch.get(key) is not None}
    if fields:
        if "catalog.yml" not in tree:
            raise ValueError(f"catalog.yml was dropped: {', '.join(fields)} cannot be written in it")
        tree["catalog.yml"] = _rewritten(tree["catalog.yml"].decode("utf-8"), fields).encode("utf-8")
    return tree


def _unit(parts):
    """(kind, name) of the unit a path belongs to, None for a path that is in none."""
    kind = AREAS.get(parts[0])
    if kind == "setup":
        return (kind, parts[1][:-4]) if len(parts) == 2 and parts[1].endswith(".yml") else None
    return (kind, parts[1]) if kind and len(parts) >= 3 else None


def _head(unit):
    """The file that makes a unit a unit."""
    kind, name = unit
    return f"setups/{name}.yml" if kind == "setup" else f"{FOLDER[kind]}/{name}/compose.yml"


def _refused(rel, parts, size):
    """Whether a file is never installed, whatever unit it is in (rule 5), apart from its mode: a fixture holds none
    but 100644."""
    if len(parts) > 8 or len(rel) > 200:
        return True
    for index, segment in enumerate(parts):
        low = segment.lower()
        if not SEGMENT.fullmatch(segment) or segment in (".", ".."):
            return True
        if low in RESERVED or low.startswith(RESERVED_PREFIXES):
            return True
        if low == ".env" or (low.startswith(".env.") and not (index == len(parts) - 1 and low == ".env.example")):
            return True
    base = parts[-1].lower()
    if base.endswith(KEY_FILES) or base in AGENT_FILES or rel.lower().endswith(".github/copilot-instructions.md"):
        return True
    return size > (SMALL if base.endswith(TEXT) else LARGE)


def _selected(tree):
    """(kept {path: bytes}, ignored count) of what the Lab installs from git (, rules 5 and 6): catalog.yml and
    the files of the units below addons/, setups/ and playgrounds/, but never a file of a machine, a key, the
    instructions of an agent, an odd or too deep name, a big file, a folder that is no unit name, a unit without its
    compose.yml, or a path that differs from another's only by case. The rest of the top level (README.md, .github/) is
    not installed and not counted."""
    candidates, ignored = [], 0
    for rel, data in sorted(tree.items()):
        parts = rel.split("/")
        if rel != "catalog.yml" and parts[0] not in AREAS:
            ignored += parts[0] in ("", ".", "..") or not SEGMENT.fullmatch(parts[0])
            continue
        unit = None if rel == "catalog.yml" else _unit(parts)
        bad = _refused(rel, parts, len(data)) or (rel != "catalog.yml" and not (unit and UNIT_NAME.fullmatch(unit[1])))
        if bad:
            ignored += 1
        else:
            candidates.append((rel, unit))
    spellings = Counter(rel.lower() for rel, _ in candidates)
    distinct = [(rel, unit) for rel, unit in candidates if spellings[rel.lower()] == 1]
    ignored += len(candidates) - len(distinct)
    heads = {_head(unit) for rel, unit in distinct if unit and rel == _head(unit)}
    kept = {}
    for rel, unit in distinct:
        if unit and _head(unit) not in heads:
            ignored += 1
        else:
            kept[rel] = tree[rel]
    return kept, ignored


def _blob(data):
    """The id git gives a file of this content."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _unit_name(rel):
    unit = _unit(rel.split("/"))
    return f"{unit[0]}:{unit[1]}" if unit else None


def _manifest(ns, commit, kept, ignored):
    """.installed.json. The digest of a unit is the sha256 of the sorted lines "<mode> <oid> <path>\\n" of
    its files: it comes from object ids alone, so any commit gives it without reading a blob."""
    files, lines = {}, {}
    for rel, data in kept.items():
        oid = _blob(data)
        files[rel] = {"mode": "100644", "oid": oid, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        if _unit_name(rel):
            lines.setdefault(_unit_name(rel), []).append(f"100644 {oid} {rel}\n")
    digests = {unit: "sha256:" + hashlib.sha256("".join(sorted(found)).encode()).hexdigest()
               for unit, found in sorted(lines.items())}
    return {"schema": 1, "catalog": ns, "commit": commit, "files": files, "units": digests, "ignored": ignored}


def _open(top, rels):
    """`top` and every folder between it and the files `rels` (relative to it), made 0755 whatever the umask of the run
    (, rule 7: the Lab UI writes a `.env` beside a unit, so a folder of an install is never closed to its
    owner)."""
    folders = {Path(top)}
    for rel in rels:
        folders.update(Path(top) / parent for parent in Path(rel).parents)
    for folder in folders:
        folder.chmod(0o755)


def _put_files(target, tree, readonly):
    """These files written under `target`: folders 0755, files 0644, or 0444 when `readonly` (, rule 7)."""
    target.mkdir(parents=True, exist_ok=True)
    for rel, data in tree.items():
        file = target / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        file.chmod(0o444 if readonly else 0o644)
    _open(target, tree)


def _remote(case, ns, tree):
    """A bare repository with `tree` committed on main, the address a git catalog is added from. gitfixture fixes the
    author and the dates, so the commit id is the same on every run."""
    bare = _scratch(case, "remotes") / f"{ns}.git"
    _clear(bare)
    return Remote(_gitfixture().make_repo(bare, tree, message=f"Add the {ns} catalog"))


def remote(case, ns, **patch):
    """A bare repository that serves fixture catalog `ns` (its address is `.url`), with nothing installed and no entry
    written: what the Add of a catalog and the first download start from. `patch`: name, version, lab, `drop` and
    `files`, as install() takes them. Answers a Remote."""
    _check_patch(patch, CATALOG_KEYS | CONTENT_KEYS)
    need_git()
    return _remote(case, ns, _patched(_tree(case, ns), patch))


STORE_SETTINGS = (("gc.auto", "0"), ("core.hooksPath", "/dev/null"), ("core.fsmonitor", "false"),
                  ("transfer.fsckObjects", "true"))   # what store.init sets on every store


def _make_store(root, ns, remote_, commit):
    """The Lab's own bare store of a catalog, .lab/cache/git/<ns>.git, made the way store.init and store.fetch make one:
    no templates, so no hooks, STORE_SETTINGS, `refs/lab/tip` fetched from the remote and `refs/lab/pin` at the commit.
    It has no origin and no branch of its own: the Lab names the address on each fetch."""
    git = _gitfixture().git
    store = root / paths.LAB_DIR / "cache" / "git" / f"{ns}.git"
    store.parent.mkdir(parents=True, exist_ok=True)
    _clear(store)
    git("init", "--bare", "--quiet", "--template=", "--", str(store))
    for key, value in STORE_SETTINGS:
        git("config", key, value, repo=store)
    git("fetch", "--quiet", "--no-tags", "--", str(remote_.bare), "+refs/heads/main:refs/lab/tip", repo=store)
    git("update-ref", "refs/lab/pin", commit, repo=store)
    return store


def install(case, ns, kind="folder", **patch):
    """Catalog `ns` of the fixtures installed in the case's project folder (project()) the way the Lab leaves one, with
    its entry in .lab/catalogs.json and its entry in .lab/catalogs.lock as writes them. Answers an Installed.

    kind="folder": the files are copied to .lab/catalogs/<ns>/, writable, and the entry is a folder entry. `link=True`
    copies them to a folder of the case's own instead and makes .lab/catalogs/<ns> a link to it, as a folder catalog is.
    `store=True` makes the folder the shadow of a git catalog: the catalog is also served by a bare repository,
    the Lab's bare store holds its commit, the lock names it and says `shadowed`, and the entry says what it
    `shadows` (the address of that repository, its branch). A `shadows` of the patch replaces what it says.

    kind="git" (needs git: a visible skip without): the files are committed to a bare repository, which is the entry's
    address (a file:// one: production accepts none), and installed the way the Lab installs them: what rule 5 of keeps, read-only, with .installed.json and a lock whose pin is that commit. `store=True` also makes the
    Lab's own bare store, .lab/cache/git/<ns>.git, with refs/lab/pin and refs/lab/tip (_make_store).

    `patch`: the keys of the entry (url, branch, auto, host, added, shadows); name, version and lab, which are written
    in catalog.yml, so that the repository and the manifest agree, and cached in the lock; the keys of the lock
    (audience: that of AUDIENCE, private for a catalog it does not know; pin, previous, tip, roots, installed, checked,
    error, pending, via, shadowed); `drop` (paths left out of the copy); `files` ({path: text} added or replaced, which
    is how a hostile file gets in); `readonly`. Every value is of the type its key needs, and `link` is for a folder
    catalog only: a value that is not is a ValueError, as is a key that does not exist. Installing again replaces
    everything of the first time and keeps the entry's place."""
    _check_patch(patch, ENTRY_KEYS | CATALOG_KEYS | LOCK_KEYS | CONTENT_KEYS | PLACE_KEYS)
    if kind not in ("folder", "git"):
        raise ValueError(f"kind is folder or git, not {kind!r}")
    if kind == "git" and patch.get("link"):
        raise ValueError("link is for a folder catalog: a git catalog is a copy of a commit, not a link to a folder")
    if ns == "local":
        raise ValueError("local is not a catalog: use local()")
    root = _root(case)
    tree = _patched(_tree(case, ns), patch)
    meta = _meta_of(tree["catalog.yml"].decode("utf-8")) if "catalog.yml" in tree else {}
    entry, remote_, commit = {"id": ns, "kind": kind}, None, None
    held = {**_blank_lock(ns), "name": meta.get("name"), "version": meta.get("version"), "lab": meta.get("lab")}
    shadow = kind == "folder" and bool(patch.get("store"))
    if kind == "git" or shadow:
        need_git()
        remote_ = _remote(case, ns, tree)   # before anything is replaced: a failure here leaves the folder as it was
        commit = remote_.head()
        held.update({"pin": commit, "tip": commit, "installed": INSTALLED, "checked": CHECKED,
                     "roots": remote_.git("rev-list", "--max-parents=0", commit).split()})
    target = root / paths.LAB_DIR / "catalogs" / ns
    target.parent.mkdir(parents=True, exist_ok=True)
    _clear(target)
    if kind == "folder":
        place = target
        if patch.get("link"):
            place = _scratch(case, "authors") / ns
            _clear(place)
            target.symlink_to(place, target_is_directory=True)
        _put_files(place, tree, bool(patch.get("readonly", False)))
        entry["path"] = str(place)
        if shadow:
            entry["shadows"] = {"url": remote_.url, "branch": "main", "added": _stamp(0)}
            held["shadowed"] = True
    else:
        kept, ignored = _selected(tree)
        _put_files(target, kept, bool(patch.get("readonly", True)))
        _write(target / ".installed.json", _json(_manifest(ns, commit, kept, ignored)))
        entry.update({"url": remote_.url, "branch": "main", "auto": True})
    if patch.get("store"):
        _make_store(root, ns, remote_, commit)
    entry.update({key: patch[key] for key in ENTRY_KEYS & set(patch) if key != "added"})
    held.update({key: patch[key] for key in LOCK_KEYS & set(patch)})
    held = _put_lock(case, ns, held)   # the entry is written last: it is what makes the catalog one the person has
    return Installed(ns, kind, target, _put_entry(case, entry, patch.get("added")), held, remote_, commit)


def declare(case, ns, **patch):
    """A catalog in .lab/catalogs.json and .lab/catalogs.lock that has never been downloaded: no files, `pin: null`, and
    the `error` (a code of the catalogue of errors) the last attempt ended with, if any. It is the row "Downloading" or "Try again".
    `patch`: url, branch, auto, host, added and the keys of the lock. Answers its entry."""
    _check_patch(patch, ENTRY_KEYS | LOCK_KEYS)
    catalog_meta(ns)   # a fixture catalog
    entry = {"id": ns, "kind": "git", "url": f"https://example.invalid/{ns}.git", "branch": "main", "auto": True}
    entry.update({key: patch[key] for key in ENTRY_KEYS & set(patch) if key != "added"})
    _put_lock(case, ns, {**_blank_lock(ns), **{key: patch[key] for key in LOCK_KEYS & set(patch)}})
    return _put_entry(case, entry, patch.get("added"))


def lock(case, ns, audience="public", lab=None, version=None, **fields):
    """The lock entry of `ns` set: its `audience` ("public" unless said: a call that gives only a version also makes the
    catalog Public), and `lab` and `version` when given. These two come from the catalog.yml of the install, so they are
    written there too, and in the manifest's record of that file when the install has one. The bare repository is not
    rewritten: give lab or version to install() when the history has to agree. `fields`: any other key of the lock. An
    entry that does not exist is made from nothing. Answers the entry."""
    _check_patch(fields, LOCK_KEYS)
    _check_patch({key: value for key, value in (("lab", lab), ("version", version)) if value is not None}, CATALOG_KEYS)
    if not (isinstance(ns, str) and NS.fullmatch(ns)):
        raise ValueError(f"{ns!r} is not a namespace")
    if audience not in ("public", "private"):
        raise ValueError(f"audience is public or private, not {audience!r}")
    current = (read_lock(case) or {"catalogs": {}})["catalogs"].get(ns) or _blank_lock(ns)
    current.update({"audience": audience, **fields})
    patched = {key: value for key, value in (("lab", lab), ("version", version)) if value is not None}
    if patched:
        _patch_install(case, ns, patched)
        current.update(patched)
    return _put_lock(case, ns, current)


def _patch_install(case, ns, fields):
    """The catalog.yml of an installed catalog rewritten, and its record in .installed.json made true again."""
    folder = _root(case) / paths.LAB_DIR / "catalogs" / ns
    file = folder / "catalog.yml"
    if not file.is_file():
        return
    data = _rewritten(file.read_text(encoding="utf-8"), fields).encode("utf-8")
    mode = file.stat().st_mode & 0o777
    file.chmod(0o644)
    file.write_bytes(data)
    file.chmod(mode)
    manifest = folder / ".installed.json"
    if manifest.is_file():
        record = json.loads(manifest.read_text(encoding="utf-8"))
        record["files"]["catalog.yml"].update(
            {"oid": _blob(data), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
        _write(manifest, _json(record))


def local(case, name, kind=None):
    """A unit of data/local copied to the personal area of the project folder, .lab/catalogs/local/: the folder of an
    add-on or a playground with everything in it, or the file of a setup. `kind` is for a name that two kinds share.
    Writable, with no manifest and no entry anywhere: the Lab never installs it and never removes it. Answers the path
    of the copy (the folder, or the setup's file)."""
    root = _root(case) / paths.LAB_DIR / "catalogs" / "local"
    found = [one for one, unit in units("local") if unit == name and kind in (None, one)]
    if len(found) != 1:
        listing = ", ".join(f"{k}:{n}" for k, n in units("local"))
        raise ValueError(f"{name!r} is {'not' if not found else 'more than'} a unit of the personal area"
                         f"{'' if not found else ' (give the kind)'}: {listing}")
    one, image = found[0], seeder_image(case).encode()
    base = LOCAL / unit_rel(one, name) if one == "setup" else LOCAL / FOLDER[one] / name
    copied = []
    for file in [base] if one == "setup" else sorted(f for f in base.rglob("*") if f.is_file()):
        target = root / file.relative_to(LOCAL)
        target.parent.mkdir(parents=True, exist_ok=True)
        _clear(target)
        data = file.read_bytes()
        target.write_bytes(data.replace(SEEDER_PLACEHOLDER.encode(), image) if file.suffix in TEXT else data)
        target.chmod(0o644)
        copied.append(file.relative_to(LOCAL))
    _open(root, copied)
    return root / base.relative_to(LOCAL)


# ---------------------------------------------------------------------------------------------------------------------
# Compose: files rendered, never run
# ---------------------------------------------------------------------------------------------------------------------

def _compose_env():
    env = {key: value for key, value in os.environ.items() if not key.startswith("COMPOSE_")}
    env.update({"COMPOSE_EXPERIMENTAL_GIT_REMOTE": "false", "COMPOSE_EXPERIMENTAL_OCI_REMOTE": "false"})
    return env


def render(root, *files, timeout=60):
    """`docker compose config --format json` of the core file of a project folder with these unit files
    (project-relative) included on top of it, the way the on file includes them. It reads files: no daemon, no
    container, no network. Answers the JSON; raises RenderError, with Compose's last line, when Compose cannot read
    them."""
    root = Path(root)
    command = ["docker", "compose", "--project-directory", str(root), "-f", str(root / paths.CORE_FILE)]
    with tempfile.TemporaryDirectory() as folder:
        if files:
            on = Path(folder) / "on.yml"
            lines = "".join(f"  - {json.dumps(str(root / file))}\n" for file in files)
            on.write_text("include:\n" + lines, encoding="utf-8")
            command += ["-f", str(on)]
        done = subprocess.run([*command, "config", "--format", "json"], env=_compose_env(), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout)
    if done.returncode:
        lines = [line.strip() for line in done.stderr.splitlines() if line.strip()]
        raise RenderError(lines[-1] if lines else "docker compose failed")
    return json.loads(done.stdout)


# ---------------------------------------------------------------------------------------------------------------------
# The checks of the fixtures and of the helpers
# ---------------------------------------------------------------------------------------------------------------------

CATALOG_IDS = ("fixture", "partner", "lab-new", "lab-old")
SURFACE = ("images", "builds", "ports", "core", "reads", "plugins", "seed", "shell", "uses_machine", "needs")   # 8.3
LISTING = {
    "fixture": [("addon", "cluster"), ("addon", "echo"), ("addon", "echo-core"), ("addon", "echo-twin"),
                ("addon", "future"), ("addon", "gateway"), ("addon", "port-core"), ("setup", "pair"),
                ("playground", "shop"), ("playground", "tour")],
    "partner": [("addon", "ledger")],
    "lab-new": [("addon", "newer")],
    "lab-old": [("addon", "older")],
    "local": [("addon", "scratch"), ("playground", "sandbox")],
}
INTERNAL = {"localhost", "127.0.0.1", "[::1]", "host.docker.internal"}   # what a URL may name besides a service
HOST_FEATURES = re.compile(r"privileged:|cap_add:|devices:|docker\.sock|network_mode:|pid:|security_opt:|sysctls:")
_rendered = {}   # what Compose says of every unit, once for the whole run: the files never change
ROOT_MARK = "<root>"   # the project folder in what _rendered holds
ACCENTED = "\u00e9t\u00e9"   # letters that are not ASCII, for a folder name that tests the hiding of the project folder


def _fixture_files():
    """The files of the catalogs this package owns, of the personal area, the oracle and the README (not the hostile
    catalog, which is the trust tests')."""
    return [file for file in sorted(DATA.rglob("*")) if file.is_file() and "__pycache__" not in file.parts
            and file.relative_to(DATA).parts[0] in ("catalogs", "local", "expected.json", "README.md")
            and file.relative_to(DATA).parts[:2] != ("catalogs", "hostile")]


DENYLIST = "LAB_FIXTURE_DENYLIST"   # the variable of the nightly job: the file of the ids of the real items


def denylist(path):
    """The words the fixtures may not hold, from the file the nightly job names: the default catalog's `catalog.json`
    (: the last part of the id of each unit), or a text with one id on each line (a line that starts with `#`
    is a comment). All lower case. A file that is not there, cannot be read or holds no id is a ValueError, because a
    variable that is set and guards nothing must be seen: an unset variable is the empty set, and only that."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError) as error:
        raise ValueError(f"{DENYLIST} names no file that can be read: {path} ({error})") from None
    try:
        listing = json.loads(text)
    except ValueError:
        listing = None
    if isinstance(listing, dict):
        found = listing.get("units")
        if not (isinstance(found, list) and all(isinstance(one, dict) and isinstance(one.get("id"), str)
                                                for one in found)):
            raise ValueError(f"{path} is JSON but not a catalog.json: it has no list of units with an id")
        ids = [one["id"] for one in found]
    else:
        ids = [line.strip() for line in text.splitlines() if not line.lstrip().startswith("#")]
    denied = {one.rsplit("/", 1)[-1].strip().lower() for one in ids if one.strip()}
    if not denied:
        raise ValueError(f"{DENYLIST} names a file that holds no id: {path}")
    return denied


def leaks(denied):
    """[(file under data/, word)] of the fixture files that hold one of the words `denied` (lower case) as a word: a
    hyphen ends a word, so `echo` is found in `fixture-echo`."""
    found = []
    for file in _fixture_files():
        text = file.read_text(encoding="utf-8").lower()
        found += [(file.relative_to(DATA).as_posix(), word) for word in sorted(denied)
                  if re.search(rf"\b{re.escape(word)}\b", text)]
    return found


def _text_of(ns, kind, name):
    return (_base(ns) / unit_rel(kind, name)).read_text(encoding="utf-8")


def _services(text):
    """{service: its keys} for the services a compose file writes in its own `services:` block, block style."""
    found, name, inside = {}, None, False
    for line in text.split("\n"):
        if line.rstrip() == "services:":
            inside = True
        elif inside and line[:1] not in (" ", "", "#"):
            break
        elif inside:
            service = re.fullmatch(r"  ([A-Za-z0-9_.-]+):[ \t]*(?:#.*)?", line)
            key = re.match(r"    ([A-Za-z0-9_.-]+):", line)
            if service:
                name = service.group(1)
                found[name] = []
            elif key and name:
                found[name].append(key.group(1))
    return found


def _description(text):
    """The x-gravitee.description of a compose file, written in flow style or in block style."""
    header = text.split("\nservices:")[0].split("\ninclude:")[0]
    m = re.search(r"\bdescription:[ \t]*(?:\"([^\"]*)\"|([^,}\n]+))", header)
    return (m.group(1) if m.group(1) is not None else m.group(2)).strip() if m else None


def _category(text):
    """The x-gravitee.category of a compose file, written in flow style or in block style: None when it says none."""
    header = text.split("\nservices:")[0].split("\ninclude:")[0]
    m = re.search(r"\bcategory:[ \t]*([^,}\n]+)", header)
    return m.group(1).strip() if m else None


def _replace(value, old, new):
    """A rendered configuration with `old` replaced by `new` in every text of it, at any depth. Not a round trip through
    JSON text: that writes a letter that is not ASCII as an escape and would miss it."""
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_replace(one, old, new) for one in value]
    if isinstance(value, dict):
        return {key: _replace(one, old, new) for key, one in value.items()}
    return value


def _hide(value, root):
    """A rendered configuration with the project folder written `<root>`, so that one case can read what another rendered."""
    return _replace(value, root, ROOT_MARK)


def _show(value, root):
    """_hide undone, for the project folder of this case."""
    return _replace(value, ROOT_MARK, root)


def _requires(ns):
    """The catalogs a fixture catalog declares under requires.catalogs of its catalog.yml, sorted."""
    found, inside = [], False
    for line in (_base(ns) / "catalog.yml").read_text(encoding="utf-8").split("\n"):
        if re.fullmatch(r"    catalogs:[ \t]*", line):
            inside = True
        elif inside:
            declared = re.match(r"      ([a-z][a-z0-9-]*):", line)
            if declared:
                found.append(declared.group(1))
            elif line[:6] != "      ":
                break
    return sorted(found)


def _ns_of(uid, default="fixture"):
    return uid.split("/")[0][1:] if uid.startswith("@") else "local" if uid.startswith("local/") else default


class FixtureCatalogs(Case):
    """The fixture catalogs and the helpers, checked: what the spec says the catalogs hold, that Compose reads every
    unit with the real core, that the helpers write what describes, and that the filter and the ids they
    restate are those of the Lab's own modules once these exist."""

    maxDiff = None

    def built(self, **patch):
        return project(self, paths, **patch)

    # -- what the fixtures hold ---------------------------------------------------------------------------------

    def test_the_catalogs_and_their_units_are_those_of_the_spec(self):
        for ns, listing in LISTING.items():
            self.assertEqual(units(ns), sorted(listing, key=lambda unit: (KINDS.index(unit[0]), unit[1])), ns)
        self.assertTrue(set(CATALOG_IDS) <= {p.name for p in CATALOGS.iterdir() if p.is_dir()})

    def test_a_catalog_yml_is_what_section_4_1_asks(self):
        for ns in CATALOG_IDS:
            meta = catalog_meta(ns)
            self.assertEqual((meta["id"], meta["schema"], meta["license"]), (ns, 1, "AGPL-3.0-only"))
            self.assertRegex(ns, NS.pattern)
            self.assertNotIn(ns, RESERVED_NS)
            self.assertRegex(meta["version"], r"\d+\.\d+\.\d+")
            self.assertLessEqual(len(meta["name"]), 40)
            self.assertLessEqual(len(meta["description"]), 90)
            self.assertIsInstance(meta["lab"], int)
        self.assertEqual([catalog_meta(ns)["lab"] for ns in CATALOG_IDS], [1, 1, 99, 0])
        self.assertEqual(catalog_meta("partner")["version"], "2.0.0")
        self.assertEqual(catalog_meta("fixture")["name"], "Fixture catalog")

    def test_fixture_declares_the_partner_it_includes_from(self):
        text = (CATALOGS / "fixture" / "catalog.yml").read_text(encoding="utf-8")
        self.assertIn('partner: {name: Partner fixture, url: "https://example.invalid/partner.git", min: 1.0.0}', text)
        self.assertEqual(catalog_meta("partner")["name"], "Partner fixture")

    def test_every_add_on_and_playground_has_a_readme_of_one_line_and_every_playground_a_guide(self):
        for ns, listing in LISTING.items():
            for kind, name in [(kind, name) for kind, name in listing if kind != "setup"]:
                folder = _base(ns) / FOLDER[kind] / name
                readme = (folder / "README.md").read_text(encoding="utf-8")
                self.assertEqual(len(readme.splitlines()), 1, f"{ns}/{name}")
                if kind == "playground":
                    self.assertTrue((folder / "guide.md").is_file(), name)
                    self.assertTrue((folder / "seed" / "apim" / "apis").is_dir(), name)

    def test_playgrounds_say_their_tags_duration_and_first_start(self):
        for ns, name in (("fixture", "shop"), ("fixture", "tour"), ("local", "sandbox")):
            for key in ("tags", "duration", "first_start"):
                self.assertRegex(_text_of(ns, "playground", name), rf"\b{key}:", f"{name} {key}")

    def test_shop_mirrors_a_full_playground_and_tour_an_apim_only_one(self):
        shop = CATALOGS / "fixture" / "playgrounds" / "shop"
        text = (shop / "compose.yml").read_text(encoding="utf-8")
        for key in ("requires:", "images:", "license:", "plugins:", "tested:", "consoles:", "accounts:",
                    "io.gravitee.role: seed"):
            self.assertIn(key, text)
        self.assertEqual(sorted(p.name for p in (shop / "seed").iterdir()), ["am", "apim"])
        self.assertTrue((shop / "apps" / "web" / "Dockerfile").is_file())
        guide = (shop / "guide.md").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"(?m)^## (\d+)\.", guide), ["1", "2", "3"])
        self.assertEqual(guide.count("(action:try-it)"), 1)
        tour = CATALOGS / "fixture" / "playgrounds" / "tour"
        self.assertEqual([p.name for p in (tour / "seed").iterdir()], ["apim"])
        self.assertEqual(re.findall(r"(?m)^## (\d+)\.", (tour / "guide.md").read_text(encoding="utf-8")), ["1", "2"])

    def test_the_seed_services_carry_the_placeholder_the_helper_replaces(self):
        for ns, name in (("fixture", "shop"), ("fixture", "tour"), ("local", "sandbox")):
            text = _text_of(ns, "playground", name)
            self.assertIn(f"image: {SEEDER_PLACEHOLDER}", text, name)
            self.assertIn("build: ../../../../../seeder", text, name)

    def test_every_include_follows_the_fixed_depth_of_section_5_2_and_reaches_a_unit(self):
        declared = {"fixture": {"partner"}, "partner": set(), "lab-new": set(), "lab-old": set()}
        for ns in LISTING:
            for kind, name in units(ns):
                for target in _includes(_text_of(ns, kind, name)):
                    where = posixpath.normpath(posixpath.join(posixpath.dirname(unit_file(ns, kind, name)), target))
                    found = UNIT_FILE.fullmatch(where)
                    self.assertTrue(found, f"{ns}:{name} includes {target}, which is {where}")
                    self.assertTrue((_base(found.group(1)) / where.split("/", 3)[3]).is_file(), where)
                    if ns != "local" and found.group(1) != ns:
                        self.assertIn(found.group(1), declared[ns], f"{ns}:{name} includes {where}: not declared")

    def test_the_files_are_ascii_lf_text_with_a_final_newline_and_no_tab(self):
        self.assertGreater(len(_fixture_files()), 40)
        for file in _fixture_files():
            data, where = file.read_bytes(), file.relative_to(DATA).as_posix()
            data.decode("ascii")
            self.assertNotIn(b"\r", data, where)
            self.assertNotIn(b"\t", data, where)
            self.assertTrue(data.endswith(b"\n") and not data.endswith(b"\n\n"), where)
            if file.suffix != ".md":
                self.assertFalse(any(line != line.rstrip() for line in data.decode().split("\n")), f"{where}: space")

    def test_nothing_in_the_fixtures_names_a_real_address_organization_or_image(self):
        listing = os.environ.get(DENYLIST)   # the nightly job: the default catalog's catalog.json, outside the repository
        self.assertEqual(leaks(denylist(listing)) if listing else [], [])
        for file in _fixture_files():
            text, where = file.read_text(encoding="utf-8"), file.relative_to(DATA).as_posix()
            for host in re.findall(r"https?://(?:[^/@\s]*@)?(\[[^\]]+\]|[^/:\s\"')]+)", text):
                own = host in INTERNAL or "." not in host or host.endswith((".invalid", ".test", ".example"))
                self.assertTrue(own, f"{where}: {host}")
            for domain in re.findall(r"[\w.+-]+@([\w.-]+\.\w+)", text):
                self.assertTrue(domain.endswith((".invalid", ".test", ".example")), f"{where}: {domain}")
            for image in re.findall(r"(?m)^\s*image:\s*(\S+)", text) + re.findall(r"(?m)^FROM\s+(\S+)", text):
                repository = image.split(":")[0]
                own = repository in ("busybox", SEEDER_PLACEHOLDER) or repository.startswith(
                    "registry.example.invalid/")
                self.assertTrue(own, f"{where}: {image}")

    def test_the_denylist_is_a_catalog_json_or_a_list_of_ids_and_a_file_that_guards_nothing_is_an_error(self):
        folder = _scratch(self, "denylist")
        index = folder / "catalog.json"
        index.write_text(json.dumps({"schema": 1, "catalog": {"id": "real-hub"}, "units": [
            {"kind": "addon", "id": "mock-bank", "path": "addons/mock-bank/compose.yml"},
            {"kind": "playground", "id": "@real-hub/Bank"}]}, indent=2) + "\n", encoding="utf-8")
        self.assertEqual(denylist(index), {"mock-bank", "bank"}, "the last part of each id, lower case")
        lines = folder / "ids.txt"
        lines.write_text("# the real items\nEcho\n\n  shop  \n", encoding="utf-8")
        self.assertEqual(denylist(lines), {"echo", "shop"})
        number = folder / "one.txt"
        number.write_text("123\n", encoding="utf-8")
        self.assertEqual(denylist(str(number)), {"123"}, "a text that happens to be JSON is a list of ids")
        empty, comments, bare, odd, none = (folder / name for name in ("empty", "comments", "bare.json", "odd.json", "no.json"))
        empty.write_text("", encoding="utf-8")
        comments.write_text("# nothing\n\n", encoding="utf-8")
        bare.write_text('{"schema": 1}', encoding="utf-8")
        odd.write_text('{"units": [{"kind": "addon"}]}', encoding="utf-8")
        none.write_text('{"units": []}', encoding="utf-8")
        for bad in (folder / "missing", folder, empty, comments, bare, odd, none):
            with self.assertRaises(ValueError, msg=str(bad)):
                denylist(bad)

    def test_a_denied_word_is_found_in_the_fixtures_as_a_word_and_not_inside_another(self):
        found = leaks({"echo"})
        self.assertIn(("catalogs/fixture/addons/echo/README.md", "echo"), found)
        self.assertIn(("catalogs/fixture/addons/echo-core/compose.yml", "echo"), found, "a hyphen ends a word")
        self.assertIn(("README.md", "echo"), found, "the README of the data is a file under data")
        self.assertEqual(leaks({"cho", "chos", "zzz-not-an-item"}), [])
        self.assertTrue(leaks({"fixture-echo"}))

    def test_the_nightly_guard_fails_on_a_real_id_given_as_the_catalog_json_and_on_a_file_that_is_not_there(self):
        folder = _scratch(self, "nightly")
        real, other = folder / "real.json", folder / "other.json"
        real.write_text(json.dumps({"units": [{"id": "echo"}, {"id": "shop"}]}), encoding="utf-8")
        other.write_text(json.dumps({"units": [{"id": "zzz-not-an-item"}]}), encoding="utf-8")
        guard = self.test_nothing_in_the_fixtures_names_a_real_address_organization_or_image
        with mock.patch.dict(os.environ, {DENYLIST: str(real)}):
            with self.assertRaises(AssertionError):
                guard()
        with mock.patch.dict(os.environ, {DENYLIST: str(other)}):
            guard()
        with mock.patch.dict(os.environ, {DENYLIST: str(folder / "missing.json")}):
            with self.assertRaisesRegex(ValueError, "names no file"):
                guard()
        env = {key: value for key, value in os.environ.items() if key != DENYLIST}
        with mock.patch.dict(os.environ, env, clear=True):
            guard()

    def test_a_variable_is_described_at_its_first_use_and_is_its_catalog_s_or_a_version_or_a_pull_policy(self):
        for ns in ("fixture", "partner", "lab-new", "lab-old", "local"):
            prefix = ns.upper().replace("-", "_") + "_"
            for kind, name in units(ns):
                seen = set()
                for line in _text_of(ns, kind, name).split("\n"):
                    code, _, comment = line.partition(" #")
                    for variable in re.findall(r"\$\{([A-Za-z_]\w*)", code):
                        if variable not in seen:
                            self.assertTrue(comment.strip(), f"{ns}:{name}: {variable} has no comment on its line")
                        seen.add(variable)
                        own = variable.startswith(prefix) or ns == "local" or variable.endswith(
                            ("_VERSION", "_PULL_POLICY"))
                        self.assertTrue(own, f"{ns}:{name}: {variable}")

    def test_a_seed_reads_settings_of_its_own_and_none_that_looks_like_a_secret(self):
        for ns, name in (("fixture", "shop"), ("fixture", "tour"), ("local", "sandbox")):
            for file in sorted((_base(ns) / "playgrounds" / name / "seed").rglob("*")):
                text = file.read_text(encoding="utf-8") if file.is_file() else ""
                for variable in re.findall(r"\$\{([A-Za-z_]\w*)", text):
                    self.assertTrue(variable.startswith((ns.upper(), name.upper())), f"{file.name}: {variable}")
                    self.assertNotRegex(variable, r"PASSWORD|SECRET|TOKEN|_KEY")

    def test_a_description_is_a_phrase_with_no_leading_article_and_no_final_period(self):
        for ns in ("fixture", "partner", "lab-new", "lab-old", "local"):
            for kind, name in units(ns):
                shown = _description(_text_of(ns, kind, name))
                self.assertTrue(shown, f"{ns}:{name}")
                self.assertFalse(shown.endswith("."), f"{ns}:{name}: {shown}")
                self.assertNotRegex(shown, r"^(?i:a|an|the) ", f"{ns}:{name}: {shown}")

    def test_every_unit_has_a_category_of_the_core_because_the_catalog_profile_asks_for_one(self):
        # Every item of a catalog has one. The setup `pair` lacked it and `catalog.py check` said so: the fixtures
        # state what they are, and the check agrees (tests/library/test_check.py compares the two, unit by unit)
        core = CORE.read_text(encoding="utf-8")
        listed = re.search(r"(?m)^  categories: \[([^\]]*)\]", core).group(1).split(", ")
        self.assertIn("Other", listed)
        for ns in ("fixture", "partner", "lab-new", "lab-old", "local"):
            for kind, name in units(ns):
                self.assertIn(_category(_text_of(ns, kind, name)), listed, f"{ns}:{kind}:{name}")

    def test_only_cluster_uses_this_machine_and_it_says_why(self):
        for ns in ("fixture", "partner", "lab-new", "lab-old"):
            for kind, name in units(ns):
                uses = bool(HOST_FEATURES.search(_text_of(ns, kind, name)))
                self.assertEqual(uses, (ns, name) == ("fixture", "cluster"), f"{ns}:{name}")
        text = _text_of("fixture", "addon", "cluster")
        self.assertRegex(text, r"host_access:\n    fixture-cluster: \S")
        self.assertIn("privileged: true", text)

    # -- expected.json ------------------------------------------------------------------------------------------

    def test_expected_json_has_every_unit_of_fixture_and_partner_and_nothing_else(self):
        names = {f"{kind}:{unit_id(ns, kind, name)}" for ns in ("fixture", "partner") for kind, name in units(ns)}
        self.assertEqual(set(expected()), names)

    def test_expected_json_entries_have_the_fields_and_shapes_of_the_spec(self):
        fields = {"verdict", "reasons", "notes", "surface", "closure", "ports", "lint"}
        for key, one in expected().items():
            self.assertEqual(set(one) - {"variants"}, fields, key)
            self.assertIn(one["verdict"], ("ok", "blocked"), key)
            self.assertEqual(bool(one["reasons"]), one["verdict"] == "blocked", key)
            self.assertEqual(tuple(one["surface"]), SURFACE, key)
            self.assertEqual(one["ports"], sorted(one["ports"]), key)
            self.assertEqual(one["closure"][0], key, key)
            for field in ("reasons", "notes", "lint", "closure"):
                self.assertTrue(all(isinstance(line, str) and line for line in one[field]), f"{key} {field}")
            for variant in one.get("variants", {}).values():
                self.assertTrue(set(variant) <= {"verdict", "reasons", "notes"}, key)
        self.assertEqual([key for key, one in expected().items() if one["lint"]], ["addon:port-core"],
                         "the only finding the check makes of a fixture unit, which tests/library/test_check.py compares with the "
                         "real check of every unit: a new one is a decision, not a drift")

    def test_expected_closures_are_what_the_include_lines_give_and_needs_are_the_other_catalogs_in_them(self):
        for ns in ("fixture", "partner"):
            for kind, name in units(ns):
                one = expected()[f"{kind}:{unit_id(ns, kind, name)}"]
                self.assertEqual(one["closure"], closure(ns, kind, name), f"{ns}:{name}")
                others = sorted({_ns_of(entry.split(":", 1)[1]) for entry in one["closure"]} - {ns})
                self.assertEqual(one["surface"]["needs"], others, f"{ns}:{name}")

    def test_expected_blocked_units_are_those_the_levels_and_the_audiences_block(self):
        self.built()
        core_text = (self.root / "docker-compose.yml").read_text(encoding="utf-8")
        level, floor = (int(re.search(rf"(?m)^  {key}: (\d+)", core_text).group(1))
                        for key in ("contract", "contract_min"))
        blocked = {}
        for ns in ("fixture", "partner"):
            for kind, name in units(ns):
                key = f"{kind}:{unit_id(ns, kind, name)}"
                asked = re.search(r"requires: \{lab: (\d+)\}", _text_of(ns, kind, name))
                needs = int(asked.group(1)) if asked else catalog_meta(ns)["lab"]
                if needs > level:
                    blocked[key] = ["Needs a newer Gravitee Lab"]
                elif needs < floor:
                    blocked[key] = ["Written for an older Gravitee Lab"]
                deeper = [entry for entry in expected()[key]["closure"]
                          if AUDIENCE[_ns_of(entry.split(":", 1)[1])] == "private" and AUDIENCE[ns] == "public"]
                if deeper:
                    blocked.setdefault(key, []).append("Depends on private content")
        found = {key: one["reasons"] for key, one in expected().items() if one["verdict"] == "blocked"}
        self.assertEqual(found, blocked)
        self.assertEqual(expected()["setup:pair"]["variants"]["partner public"], {"verdict": "ok", "reasons": []})

    def test_expected_json_agrees_with_what_the_policy_says_of_every_unit(self):
        """The oracle against the Lab's own judge: for each unit of fixture and partner the verdict, the reasons, the notes
        and the whole surface that trust.judge gives, fed the way the project feeds it (the unit as Compose reads it
        alone and as written, the services it owns, what it overlays, the roots it may mount, the settings of the core),
        are those of expected.json. The audience (T24) is the resolver's. `lint` is not compared: no check of a catalog
        exists yet to say it."""
        trust = _lab("catalogs.trust", "the Lab", "judge", "overlays", "build_roots", "reads", "seed_reads", "add_reason", "line")
        resolve = _lab("catalogs.resolve", "the Lab", "audience_blocked", "rank")
        compose = _lab("compose", "the engine", "raw", "includes", "ROOT")
        need_compose()
        found = self.rendered()
        install(self, "partner")
        self.patch(compose, "ROOT", self.root)
        root, core = str(self.root), _show(found["core"], str(self.root))
        text = (self.root / "docker-compose.yml").read_text(encoding="utf-8")
        level, floor = (int(re.search(rf"(?m)^  {key}: (\d+)", text).group(1)) for key in ("contract", "contract_min"))
        license_variable = re.search(r"gravitee_license_key:\s*\$\{(\w+)", text)
        core_names = set(core["services"])

        def parts(entry):   # "addon:@partner/ledger" -> (ns, kind, name)
            kind, _, uid = entry.partition(":")
            return _ns_of(uid), kind, uid.rsplit("/", 1)[-1]

        def written(ns, kind, name):   # the services a unit's own file declares that the core does not
            return [one for one in _services(_text_of(ns, kind, name)) if one not in core_names]

        for ns in ("fixture", "partner"):
            for kind, name in units(ns):
                key = f"{kind}:{unit_id(ns, kind, name)}"
                with self.subTest(unit=key):
                    file = unit_file(ns, kind, name)
                    unit_text, wanted = (self.root / file).read_text(encoding="utf-8"), expected()[key]
                    raw = compose.raw(file)
                    targets = compose.includes(file)
                    included = [compose.raw(target) for target in targets]
                    theirs = {one for other in included for one in other.get("services") or ()}
                    own = [one for one in written(ns, kind, name) if one not in theirs]
                    inside = [parts(entry) for entry in wanted["closure"][1:]]
                    meta = raw.get("x-gravitee") or {}
                    folder = (self.root / file).parent if kind != "setup" else None
                    seeds = sorted(file for file in (folder / "seed").rglob("*") if file.is_file()) \
                        if kind == "playground" else []
                    unit = {"kind": kind, "id": unit_id(ns, kind, name), "name": str(meta.get("name") or name), "meta": meta,
                            "references": trust.reads([unit_text], raw), "includes": targets, "seed": None,
                            "seed_references": trust.seed_reads([file.read_text(encoding="utf-8") for file in seeds])}
                    roots = trust.build_roots(
                        root, folder, [(self.root / unit_file(*one)).parent for one in inside if one[0] == ns and one[1] != "setup"],
                        ("apim", "am"))
                    listed = ns == "fixture"   # the default catalog: listed, and trusted for class H
                    context = {"ns": ns, "host_ok": listed, "listed": listed, "requires": _requires(ns), "shared_settings": [],
                               "core_variables": trust.reads([text]),
                               "license_variable": license_variable.group(1) if license_variable else None,
                               "closure_names": [one[2] for one in inside],
                               "closure_services": sorted({svc for one in inside for svc in written(*one)}),
                               "seeder_images": [seeder_image(self)], "home": "/home/fixture", "realpath": os.path.realpath,
                               "level": (meta.get("requires") or {}).get("lab") or catalog_meta(ns)["lab"],
                               "contract": level, "contract_min": floor}
                    verdict = trust.judge(unit, _show(found[key], root), core, own,
                                          trust.overlays(raw, core_names, included), roots, set(), context)
                    alone = (verdict["state"], verdict["reasons"])
                    for other in inside:   # T24: the audience of the catalog a unit brings, as the resolver ranks it
                        if other[0] != ns and resolve.audience_blocked(resolve.rank(AUDIENCE[ns]), resolve.rank(AUDIENCE[other[0]])):
                            verdict = trust.add_reason(verdict, "T24", trust.line("T24"))
                    self.assertEqual((verdict["state"], verdict["reasons"], verdict["notes"]),
                                     (wanted["verdict"], wanted["reasons"], wanted["notes"]))
                    self.assertEqual(verdict["surface"], wanted["surface"])
                    for variant in wanted.get("variants", {}).values():   # a unit that is judged the same without T24
                        self.assertEqual(alone, (variant["verdict"], variant["reasons"]))

    def test_the_closure_agrees_with_the_resolver_once_it_exists(self):
        resolve = _lab("catalogs.resolve", "the Lab", "closure")
        direct = {}
        for ns in ("fixture", "partner", "local"):
            for kind, name in units(ns):
                direct[(kind, unit_id(ns, kind, name))] = _direct(ns, kind, name)
        for ns in ("fixture", "partner", "local"):
            for kind, name in units(ns):
                found = [f"{one[0]}:{one[1]}" for one in resolve.closure(direct, kind, unit_id(ns, kind, name))]
                self.assertEqual(found, closure(ns, kind, name), f"{ns}:{name}")

    # -- Compose reads the fixtures -----------------------------------------------------------------------------

    def rendered(self):
        """{"kind:id": Compose's JSON of the core with that unit on top, "core": the core alone}, for every unit of the
        fixtures and of the personal area, read once. The project folder's path is `<root>`."""
        need_compose()
        self.built()
        if not _rendered:
            for ns in ("partner", "lab-new", "lab-old"):
                install(self, ns)
            for name in ("scratch", "sandbox"):
                local(self, name)
            found = {"core": render(self.root)}
            for ns in ("fixture", "partner", "lab-new", "lab-old", "local"):
                for kind, name in units(ns):
                    found[f"{kind}:{unit_id(ns, kind, name)}"] = render(self.root, unit_file(ns, kind, name))
            _rendered.update(_hide(found, str(self.root)))
        return _rendered

    def test_every_unit_renders_with_the_real_core(self):
        found = self.rendered()
        self.assertEqual(len(found), 1 + 10 + 1 + 1 + 1 + 2)
        for key, config in found.items():
            self.assertIn("apim-gateway", config["services"], key)

    def test_the_rendered_ports_are_those_of_expected_json(self):
        found = self.rendered()
        for key, one in expected().items():
            mine = {n: s for n, s in found[key]["services"].items() if n not in found["core"]["services"]}
            published = {int(port["published"]) for service in mine.values() for port in service.get("ports") or []}
            self.assertEqual(sorted(published), one["ports"], key)

    def test_the_rendered_own_services_match_the_surface_of_expected_json(self):
        found = self.rendered()
        core_names = set(found["core"]["services"])
        for ns in ("fixture", "partner"):
            for kind, name in units(ns):
                key = f"{kind}:{unit_id(ns, kind, name)}"
                surface = expected()[key]["surface"]
                written = _services(_text_of(ns, kind, name))
                services = found[key]["services"]
                own = sorted(n for n in written if n not in core_names)
                pulled = {services[n]["image"].split(":")[0] for n in own
                          if services[n].get("image") and not services[n].get("build")}
                self.assertEqual(sorted(pulled), surface["images"], key)
                built = sorted(n for n in own if services[n].get("build"))
                self.assertEqual(built, sorted(row["service"] for row in surface["builds"]), key)
                ports = {int(p["published"]) for n in own for p in services[n].get("ports") or []}
                self.assertEqual(sorted(ports), surface["ports"], key)
                overlay = sorted((n, tuple(sorted(keys))) for n, keys in written.items() if n in core_names)
                self.assertEqual(overlay, sorted((row["service"], tuple(row["keys"])) for row in surface["core"]), key)
                seeds = [n for n in own if (services[n].get("labels") or {}).get("io.gravitee.role") == "seed"]
                self.assertEqual("official" if seeds else None, surface["seed"], key)
                privileged = sorted(n for n in own if services[n].get("privileged"))
                self.assertEqual(privileged, sorted(row["service"] for row in surface["uses_machine"]), key)

    def test_each_service_follows_the_house_rules(self):
        found = self.rendered()
        for key, config in found.items():
            for name, service in config["services"].items():
                if name in found["core"]["services"]:
                    continue
                where, labels = f"{key}: {name}", service.get("labels") or {}
                self.assertTrue(labels.get("io.gravitee.description"), where)
                self.assertNotEqual(service.get("restart"), "always", where)
                self.assertTrue(all(p.get("host_ip") == "127.0.0.1" for p in service.get("ports") or []), where)
                self.assertTrue(service.get("container_name", "gio-").startswith("gio-"), where)
                self.assertTrue(labels.get("io.gravitee.name") or labels.get("io.gravitee.role") == "seed", where)

    def test_the_project_folder_is_hidden_in_a_rendering_whatever_its_name_holds(self):
        folder = f"/x/{ACCENTED}"
        config = {"services": {"a": {"build": {"context": f"{folder}/seeder"}, "command": [folder, 3, None, True]}},
                  "name": folder, "ports": [{"published": "8080"}]}
        hidden = _hide(config, folder)
        self.assertEqual(hidden["services"]["a"]["build"]["context"], "<root>/seeder")
        self.assertEqual((hidden["name"], hidden["services"]["a"]["command"]), ("<root>", ["<root>", 3, None, True]))
        self.assertEqual(hidden["ports"], config["ports"])
        self.assertEqual(_show(hidden, folder), config, "and back")
        self.assertEqual(config["name"], folder, "the original is not changed")
        need_compose()
        letters = _scratch(self, "letters") / f"folder of {ACCENTED}'s"
        letters.mkdir()
        self.patch(tempfile, "tempdir", str(letters))
        self.built()
        self.assertIn(ACCENTED, str(self.root))
        file = unit_file("fixture", "playground", "tour")
        self.assertIn(ACCENTED, render(self.root, file)["services"]["fixture-tour-seed"]["build"]["context"],
                      "Compose spells the folder with its letters")
        shown = _hide(render(self.root, file), str(self.root))
        self.assertEqual(shown["services"]["fixture-tour-seed"]["build"]["context"], "<root>/seeder")
        self.assertNotIn(ACCENTED, json.dumps(shown, ensure_ascii=False))

    def test_the_seed_services_build_the_seeder_of_the_core(self):
        found = self.rendered()
        for key in ("playground:shop", "playground:tour", "playground:local/sandbox"):
            seed = next(s for n, s in found[key]["services"].items() if n.endswith("-seed"))
            self.assertEqual(seed["image"], seeder_image(self), key)
            self.assertEqual(seed["build"]["context"], "<root>/seeder", key)
            self.assertEqual((seed["restart"], seed["labels"]["io.gravitee.role"]), ("no", "seed"))

    def test_the_units_set_what_the_spec_says_they_set(self):
        found = self.rendered()
        gateway = found["addon:echo-core"]["services"]["apim-gateway"]
        self.assertIn("fixture-echo", gateway["depends_on"])
        self.assertEqual(gateway["environment"]["FIXTURE_ECHO_URL"], "http://fixture-echo:80")
        self.assertTrue(found["addon:cluster"]["services"]["fixture-cluster"]["privileged"])
        port = found["addon:port-core"]["services"]["fixture-port-core"]["ports"][0]["published"]
        self.assertEqual(port, found["core"]["services"]["apim-gateway"]["ports"][0]["published"])
        twin = found["addon:echo-twin"]["services"]["fixture-echo-twin"]["ports"][0]["published"]
        self.assertEqual(twin, found["addon:echo"]["services"]["fixture-echo"]["ports"][0]["published"])
        pair = [n for n in found["setup:pair"]["services"] if n.startswith(("fixture", "partner"))]
        self.assertEqual(sorted(pair), ["fixture-echo", "partner-ledger"])
        shop = found["playground:shop"]["services"]["fixture-shop-web"]
        self.assertEqual(shop["build"]["context"], "<root>/.lab/catalogs/fixture/playgrounds/shop/apps/web")
        token = found["addon:gateway"]["services"]["fixture-gateway"]
        self.assertIn("FIXTURE_GATEWAY_TOKEN", token["environment"])
        self.assertEqual(sorted(token["networks"]), ["edge", "internal"])
        labels = found["addon:echo"]["services"]["fixture-echo"]["labels"]
        self.assertEqual((labels["io.gravitee.console.fixture-echo.name"], labels["io.gravitee.console.fixture-echo.url"]),
                         ("Echo", "http://localhost:8191"), "the console of the add-on")

    def test_the_declarations_the_spec_gives_the_units_are_in_their_files(self):
        """What 17.1 says of a unit and other packages read: a console, a conflict, a gateway, a level, a reason to use
        this machine. Each is a line the oracle and the policy depend on, so a unit that loses one fails here and not
        three packages away."""
        echo = _text_of("fixture", "addon", "echo")
        self.assertIn("io.gravitee.console.fixture-echo.name: Echo", echo)
        self.assertIn("io.gravitee.console.fixture-echo.url: http://localhost:8191", echo)
        self.assertRegex(_text_of("fixture", "addon", "echo-twin"), r"(?m)^  conflicts:\n    echo: \S")
        gateway = _text_of("fixture", "addon", "gateway")
        self.assertIn("gateway: {platform: cloud, service: fixture-gateway, token: FIXTURE_GATEWAY_TOKEN}", gateway)
        self.assertIn('"${FIXTURE_GATEWAY_TOKEN}"', gateway, "the token is required: no default")
        self.assertNotIn("FIXTURE_GATEWAY_TOKEN:-", gateway)
        self.assertIn("requires: {lab: 99}", _text_of("fixture", "addon", "future"))
        self.assertIn("ports: [\"127.0.0.1:8082:80\"]", _text_of("fixture", "addon", "port-core"))
        self.assertEqual(_includes(_text_of("fixture", "addon", "echo-core")), ["../../addons/echo/compose.yml"])
        self.assertEqual(_includes(_text_of("fixture", "setup", "pair")),
                         ["../addons/echo/compose.yml", "../../partner/addons/ledger/compose.yml"])
        self.assertEqual(_includes(_text_of("local", "addon", "scratch")), ["../../../fixture/addons/echo/compose.yml"])

    def test_a_setup_whose_catalog_is_missing_does_not_render(self):
        need_compose()
        self.built()
        with self.assertRaises(RenderError):
            render(self.root, unit_file("fixture", "setup", "pair"))
        install(self, "partner")
        self.assertIn("partner-ledger", render(self.root, unit_file("fixture", "setup", "pair"))["services"])
        install(self, "partner", drop=["addons/ledger"])
        with self.assertRaises(RenderError):
            render(self.root, unit_file("fixture", "setup", "pair"))

    # -- the helpers --------------------------------------------------------------------------------------------

    def test_default_writes_the_tracked_file_of_section_e1_4(self):
        self.folder(paths)
        file = default(self)
        self.assertEqual(file, self.root / "catalogs.default.json")
        self.assertEqual(json.loads(file.read_text(encoding="utf-8")), {"schema": 1, "catalogs": [{
            "id": "fixture", "name": "Fixture catalog", "description": "Synthetic units for the tests of the Lab",
            "url": "https://example.invalid/fixture.git", "branch": "main"}]})
        self.assertEqual(default(self).read_text(encoding="utf-8"), file.read_text(encoding="utf-8"), "twice")
        both = json.loads(default(self, "partner", also=("lab-new",), branch="trunk").read_text(encoding="utf-8"))
        self.assertEqual([c["id"] for c in both["catalogs"]], ["partner", "lab-new"])
        self.assertEqual({c["branch"] for c in both["catalogs"]}, {"trunk"})
        with self.assertRaises(ValueError):
            default(self, "nope")

    def test_the_default_address_and_the_catalog_ids_are_those_the_lab_accepts(self):
        gitx = _lab("catalogs.gitx", "the Lab", "parse_address")
        self.folder(paths)
        for ns in CATALOG_IDS:
            entry = json.loads(default(self, ns).read_text(encoding="utf-8"))["catalogs"][0]
            found = gitx.parse_address(entry["url"])
            self.assertEqual((found.kind, found.url), ("git", entry["url"]), ns)
            self.assertNotIn("@", entry["url"])

    def test_project_has_the_core_the_anchors_and_fixture_installed_as_the_default_catalog(self):
        self.built()
        real = CORE.read_text(encoding="utf-8").split("\n")
        copy = (self.root / "docker-compose.yml").read_text(encoding="utf-8").split("\n")
        self.assertEqual([line for line in copy if line in real], real, "the real core, line for line")
        for anchor in ("seeder/Dockerfile", "plugins/apim", "plugins/am", ".lab/catalogs/fixture/catalog.yml",
                       "catalogs.default.json", ".lab/catalogs.json", ".lab/catalogs.lock"):
            self.assertTrue((self.root / anchor).exists(), anchor)
        self.assertEqual(paths.ROOT, self.root, "the modules it was given have the folder as their ROOT")
        self.assertEqual(core(self, contract=False), self.root)
        self.assertEqual((self.root / "docker-compose.yml").read_bytes(), CORE.read_bytes())

    def test_the_core_gets_only_the_keys_it_lacks(self):
        base = "name: x\n\nx-gravitee:\n  name: P\n  groups: [A]\n\nservices: {}\n"
        full = base.replace("  groups: [A]\n", "  groups: [A]\n  contract: 4\n  contract_min: 3\n  seeder:\n"
                                                 "    image: r.example.invalid/s\n")
        self.assertEqual(_with_contract(full), full)
        text = _with_contract(base)
        self.assertEqual(text.count("contract: 1"), 1)
        added = f"  groups: [A]\n  contract: 1\n  contract_min: 1\n  seeder:\n    image: {SEEDER_IMAGE}\n\nservices"
        self.assertIn(added, text)
        self.assertEqual(_with_contract(text), text, "twice")
        self.assertEqual(_core_seeder(text), SEEDER_IMAGE)
        self.assertEqual(_core_seeder(full), "r.example.invalid/s")
        self.assertEqual(_core_seeder("x-gravitee:\n  seeder: {image: a.example.invalid/b}\n"), "a.example.invalid/b")
        self.assertIsNone(_core_seeder(base))
        self.assertIsNone(_core_seeder("services:\n  seeder:\n    image: x\n"))
        with self.assertRaises(ValueError):
            _with_contract("services: {}\n")

    def test_a_folder_install_copies_the_catalog_and_writes_the_entry_and_the_lock_of_section_2(self):
        self.built()
        done = install(self, "partner")
        self.assertEqual((done.ns, done.kind, done.remote, done.commit), ("partner", "folder", None, None))
        self.assertEqual(done.path, self.root / ".lab/catalogs/partner")
        self.assertFalse(done.path.is_symlink())
        ledger = "addons/ledger/compose.yml"
        self.assertEqual((done.path / ledger).read_bytes(), (CATALOGS / "partner" / ledger).read_bytes())
        self.assertEqual(read_config(self), {"schema": 1, "catalogs": [
            {"id": "fixture", "kind": "folder", "path": str(self.root / ".lab/catalogs/fixture"),
             "added": "2026-10-16T09:00:00Z"},
            {"id": "partner", "kind": "folder", "path": str(done.path), "added": "2026-10-16T09:01:00Z"}]})
        self.assertEqual(done.lock, {
            "pin": None, "previous": None, "tip": None, "roots": [], "audience": "private", "name": "Partner fixture",
            "version": "2.0.0", "lab": 1, "installed": None, "checked": None, "error": None, "pending": None})
        self.assertEqual(list(done.lock), list(LOCK_ORDER))
        self.assertEqual(read_lock(self)["catalogs"]["partner"], done.lock)
        self.assertEqual(read_lock(self)["catalogs"]["fixture"]["audience"], "public")
        for name in ("catalogs.json", "catalogs.lock"):
            self.assertEqual((self.root / ".lab" / name).stat().st_mode & 0o777, 0o600, name)
        self.assertFalse((done.path / ".installed.json").exists())

    def test_an_install_is_a_copy_the_repository_never_sees(self):
        self.built()
        unit = self.root / ".lab/catalogs/fixture/addons/echo"
        (unit / ".env").write_text("FIXTURE_ECHO_GREETING=hi\n", encoding="utf-8")
        (unit / ".state").mkdir()
        self.assertFalse((CATALOGS / "fixture/addons/echo/.env").exists())
        self.assertFalse((CATALOGS / "fixture/addons/echo/.state").exists())
        self.assertFalse(any(file.is_symlink() for file in (self.root / ".lab/catalogs/fixture").rglob("*")))

    def test_the_seeder_placeholder_is_replaced_by_the_image_of_the_core(self):
        self.built()
        tour = self.root / ".lab/catalogs/fixture/playgrounds/tour/compose.yml"
        shop = self.root / ".lab/catalogs/fixture/playgrounds/shop/compose.yml"
        for file in (tour, shop):
            self.assertNotIn(SEEDER_PLACEHOLDER, file.read_text(encoding="utf-8"))
            self.assertIn(f"image: {seeder_image(self)}", file.read_text(encoding="utf-8"))
        _write(self.root / "docker-compose.yml", "x-gravitee:\n  seeder:\n    image: other.example.invalid/seed\n")
        install(self, "fixture")
        self.assertIn("image: other.example.invalid/seed", tour.read_text(encoding="utf-8"))

    def test_installing_again_replaces_what_the_first_time_left_and_keeps_the_place_of_the_entry(self):
        self.built()
        install(self, "fixture", files={"addons/extra/compose.yml": "x-gravitee: {kind: addon}\n"})
        self.assertTrue((self.root / ".lab/catalogs/fixture/addons/extra/compose.yml").is_file())
        install(self, "partner")
        (self.root / ".lab/catalogs/fixture/stray").write_text("x", encoding="utf-8")
        done = install(self, "fixture")
        self.assertFalse((done.path / "addons/extra").exists())
        self.assertFalse((done.path / "stray").exists())
        entries = read_config(self)["catalogs"]
        self.assertEqual([c["id"] for c in entries], ["fixture", "partner"])
        self.assertEqual([c["added"] for c in entries], ["2026-10-16T09:00:00Z", "2026-10-16T09:01:00Z"])
        self.assertEqual(list(read_lock(self)["catalogs"]), ["fixture", "partner"])

    def test_the_patch_sets_the_entry_the_lock_and_the_catalog_file(self):
        self.built()
        done = install(self, "partner", audience="public", version="0.9.0", lab=2, name="Partner: old", host=True,
                       auto=False, error="unreachable", added="2026-01-01T00:00:00Z")
        self.assertEqual((done.entry["host"], done.entry["auto"]), (True, False))
        self.assertEqual(done.entry["added"], "2026-01-01T00:00:00Z")
        self.assertEqual((done.lock["audience"], done.lock["version"], done.lock["lab"], done.lock["error"]),
                         ("public", "0.9.0", 2, "unreachable"))
        meta = _meta_of((done.path / "catalog.yml").read_text(encoding="utf-8"))
        self.assertEqual((meta["version"], meta["lab"], meta["name"]), ("0.9.0", 2, "Partner: old"))
        self.assertEqual(done.lock["name"], "Partner: old")
        dropped = install(self, "partner", drop=["addons/ledger/README.md"])
        self.assertFalse((dropped.path / "addons/ledger/README.md").exists())
        self.assertTrue((dropped.path / "addons/ledger/compose.yml").exists())
        self.assertEqual([p.name for p in install(self, "partner", drop=["addons"]).path.iterdir()], ["catalog.yml"])

    def test_the_patch_refuses_what_it_does_not_know_and_what_leaves_the_catalog(self):
        self.built()
        for patch, error in (({"colour": 1}, "no such key"), ({"drop": ["../x"]}, "not a path"),
                             ({"drop": ["nothing"]}, "no such path"), ({"files": {"/etc/x": "x"}}, "not a path"),
                             ({"files": {"a//b": "x"}}, "not a path"), ({"files": {"a/../b": "x"}}, "not a path"),
                             ({"files": {"": "x"}}, "not a path"), ({"drop": ["a\\b"]}, "not a path"),
                             ({"files": {"a\0b": "x"}}, "not a path"), ({"files": {"a": None}}, "text or bytes")):
            with self.assertRaisesRegex(ValueError, error):
                install(self, "partner", **patch)
        for ns in ("../x", "nope", "Fixture", "", "local", "fixture/addons", None):
            with self.assertRaises(ValueError, msg=ns):
                install(self, ns)
        with self.assertRaises(ValueError):
            install(self, "partner", kind="symlink")
        self.assertEqual([c["id"] for c in read_config(self)["catalogs"]], ["fixture"], "a refusal leaves no entry")
        text = (install(self, "partner", name="Partner\nlab: 7").path / "catalog.yml").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"(?m)^  lab:", text)), 1, "a value never adds a key")
        self.assertEqual(len(re.findall(r"(?m)^  name:", text)), 1)
        self.assertIn('\n  name: "Partner\\nlab: 7"\n', text, "quoted, so that it stays the name and nothing else")

    def test_a_patch_that_does_not_fit_the_kind_or_the_type_of_its_key_is_refused(self):
        self.built()
        wrong = ({"link": True, "kind": "git"}, {"lab": "x"}, {"lab": True}, {"lab": 1.5}, {"version": 7},
                 {"version": "1.0"}, {"version": "1.0.0\nlab: 7"}, {"version": "v1.0.0"}, {"name": True}, {"name": ""},
                 {"name": 3}, {"auto": "yes"}, {"host": 1}, {"readonly": "no"}, {"link": 1}, {"store": "x"},
                 {"shadowed": "x"}, {"url": ""}, {"url": 7}, {"branch": None}, {"added": 3}, {"shadows": "x"},
                 {"shadows": [1]})
        for patch in wrong:
            patch = dict(patch)
            kind = patch.pop("kind", "folder")
            with self.assertRaises(ValueError, msg=f"{kind} {patch}"):
                install(self, "partner", kind, **patch)
            if set(patch) <= ENTRY_KEYS | LOCK_KEYS:
                with self.assertRaises(ValueError, msg=f"declare {patch}"):
                    declare(self, "partner", **patch)
        for patch in ({"lab": "x"}, {"version": 7}, {"name": True}):
            with self.assertRaises(ValueError, msg=f"remote {patch}"):
                remote(self, "partner", **patch)
        for patch in ({"lab": "x"}, {"lab": True}, {"version": 7}, {"version": "1.0"}):
            with self.assertRaises(ValueError, msg=f"lock {patch}"):
                lock(self, "partner", **patch)
        self.assertEqual([c["id"] for c in read_config(self)["catalogs"]], ["fixture"], "a refusal leaves no entry")
        self.assertFalse((self.root / ".lab/catalogs/partner").exists())
        done = install(self, "partner", lab=0, version="0.0.1", name="Zero", added="2026-01-01T00:00:00Z", auto=False,
                       host=False, readonly=True, shadows={"url": "https://example.invalid/partner.git"})
        self.assertEqual((done.lock["lab"], done.lock["version"]), (0, "0.0.1"), "zero is a level")
        self.assertEqual(done.entry["shadows"], {"url": "https://example.invalid/partner.git"})

    def test_dropping_catalog_yml_while_writing_a_field_of_it_is_refused_with_a_reason(self):
        self.built()
        with self.assertRaisesRegex(ValueError, "catalog.yml was dropped"):
            install(self, "lab-old", drop=["catalog.yml"], version="2.0.0")
        with self.assertRaisesRegex(ValueError, "catalog.yml was dropped"):
            install(self, "lab-old", drop=["catalog.yml"], lab=3)
        done = install(self, "lab-old", drop=["catalog.yml"])
        self.assertFalse((done.path / "catalog.yml").exists())
        self.assertEqual((done.lock["name"], done.lock["version"], done.lock["lab"]), (None, None, None))

    def test_a_linked_folder_catalog_is_a_link_to_a_folder_of_the_case(self):
        self.built()
        done = install(self, "partner", link=True)
        self.assertTrue(done.path.is_symlink())
        self.assertTrue((done.path / "catalog.yml").is_file())
        target = Path(os.readlink(done.path))
        self.assertEqual(done.entry["path"], str(target))
        self.assertFalse(str(target).startswith(str(self.root)))
        self.assertNotEqual(target, CATALOGS / "partner")
        install(self, "partner")
        self.assertFalse(done.path.is_symlink(), "replaced by a real folder")

    def test_a_shadow_is_a_linked_folder_with_the_store_and_the_lock_of_the_git_catalog_it_stands_in_for(self):
        need_git()
        self.built()
        done = install(self, "partner", link=True, store=True)
        self.assertTrue(done.path.is_symlink())
        self.assertRegex(done.commit, r"[0-9a-f]{40}")
        self.assertEqual(done.entry["kind"], "folder")
        self.assertEqual(done.entry["shadows"], {"url": done.remote.url, "branch": "main", "added": "2026-10-16T09:00:00Z"})
        self.assertNotIn("url", done.entry, "the entry is the folder")
        self.assertEqual((done.lock["pin"], done.lock["tip"], done.lock["shadowed"]), (done.commit, done.commit, True))
        self.assertEqual(done.lock["roots"], [done.commit])
        store = self.root / ".lab/cache/git/partner.git"
        self.assertEqual(_gitfixture().git("rev-parse", "refs/lab/pin", repo=store).strip(), done.commit)
        self.assertFalse((done.path / ".installed.json").exists(), "a folder catalog has no manifest")
        self.assertEqual(read_config(self)["catalogs"][-1], done.entry)
        mine = install(self, "partner", link=True, store=True, shadows={"url": "https://example.invalid/other.git"})
        self.assertEqual(mine.entry["shadows"], {"url": "https://example.invalid/other.git"}, "the patch replaces it")
        plain = install(self, "partner", link=True)
        self.assertNotIn("shadows", plain.entry)
        self.assertNotIn("shadowed", plain.lock)

    def test_the_project_lists_the_address_of_the_remote_it_installed_from(self):
        need_git()
        self.built(kind="git")
        listed = json.loads((self.root / "catalogs.default.json").read_text(encoding="utf-8"))["catalogs"][0]
        entry = read_config(self)["catalogs"][0]
        self.assertEqual((entry["kind"], listed["id"]), ("git", "fixture"))
        self.assertTrue(entry["url"].startswith("file://"))
        self.assertEqual((listed["url"], listed["branch"]), (entry["url"], entry["branch"]), "The tracked address wins")
        self.built(kind="folder")
        self.assertEqual(json.loads((self.root / "catalogs.default.json").read_text(encoding="utf-8"))["catalogs"][0]["url"],
                         "https://example.invalid/fixture.git", "no remote, the synthetic address")
        shadow = project(self, paths, ns="partner", store=True, link=True)
        found = json.loads((shadow / "catalogs.default.json").read_text(encoding="utf-8"))["catalogs"][0]
        self.assertEqual(found["url"], read_config(self)["catalogs"][0]["shadows"]["url"], "a shadow lists what it shadows")

    def test_every_folder_an_install_makes_is_0755_whatever_the_umask(self):
        need_git()
        before = os.umask(0o077)
        self.addCleanup(os.umask, before)
        self.built()
        for kind in ("folder", "git"):
            done = install(self, "fixture", kind)
            folders = [done.path, *(entry for entry in done.path.rglob("*") if entry.is_dir())]
            self.assertGreater(len(folders), 10, kind)
            self.assertEqual({oct(entry.stat().st_mode & 0o777) for entry in folders}, {"0o755"}, kind)
        linked = install(self, "partner", link=True)
        place = Path(os.readlink(linked.path))
        self.assertEqual({oct(entry.stat().st_mode & 0o777) for entry in [place, *place.rglob("*")] if entry.is_dir()},
                         {"0o755"}, "the linked folder")
        where = local(self, "sandbox")
        mine = [self.root / ".lab/catalogs/local", *(entry for entry in (self.root / ".lab/catalogs/local").rglob("*")
                                                     if entry.is_dir())]
        self.assertEqual({oct(entry.stat().st_mode & 0o777) for entry in mine}, {"0o755"}, "the personal area")
        self.assertEqual((where / "compose.yml").stat().st_mode & 0o777, 0o644)

    def test_installing_a_folder_catalog_takes_under_a_tenth_of_a_second(self):
        self.built()

        def once():
            # The time of this process, not of the clock on the wall: a machine at load 60 stretches the second and not the first
            start = time.process_time()
            install(self, "fixture")
            return time.process_time() - start

        best = min(once() for _ in range(5))
        self.assertLess(best, 0.1, f"{best * 1000:.0f} ms")

    def test_lock_sets_the_audience_and_patches_version_and_level_where_they_come_from(self):
        self.built()
        install(self, "partner")
        entry = lock(self, "partner")
        self.assertEqual((entry["audience"], entry["version"], entry["lab"]), ("public", "2.0.0", 1))
        entry = lock(self, "partner", audience="private", version="0.9.0", lab=3, error="x")
        self.assertEqual((entry["audience"], entry["version"], entry["lab"], entry["error"]),
                         ("private", "0.9.0", 3, "x"))
        meta = _meta_of((self.root / ".lab/catalogs/partner/catalog.yml").read_text(encoding="utf-8"))
        self.assertEqual((meta["version"], meta["lab"]), ("0.9.0", 3))
        self.assertEqual(lock(self, "partner", audience="private", version="0.9.0", lab=3, error="x"), entry, "twice")
        made = lock(self, "elsewhere-1", audience="private")
        self.assertEqual((made["audience"], made["pin"], made["roots"]), ("private", None, []))
        self.assertEqual(list(made), list(LOCK_ORDER))
        for ns, args in (("partner", {"audience": "open"}), ("partner", {"colour": 1}), ("../x", {})):
            with self.assertRaises(ValueError):
                lock(self, ns, **args)
        self.assertEqual(list(read_lock(self)["catalogs"]), ["fixture", "partner", "elsewhere-1"])

    def test_declare_is_a_catalog_added_and_never_downloaded(self):
        self.built()
        entry = declare(self, "partner")
        self.assertEqual(entry, {"id": "partner", "kind": "git", "url": "https://example.invalid/partner.git",
                                 "branch": "main", "auto": True, "added": "2026-10-16T09:01:00Z"})
        self.assertFalse((self.root / ".lab/catalogs/partner").exists())
        held = read_lock(self)["catalogs"]["partner"]
        self.assertEqual({key: value for key, value in held.items() if value is not None},
                         {"roots": [], "audience": "private"})
        declare(self, "partner", error="unreachable")
        self.assertEqual(read_lock(self)["catalogs"]["partner"]["error"], "unreachable")
        self.assertEqual([c["id"] for c in read_config(self)["catalogs"]], ["fixture", "partner"])
        with self.assertRaises(ValueError):
            declare(self, "nope")

    def test_each_case_of_needs_a_catalog_is_one_call(self):
        self.built()
        pair = unit_file("fixture", "setup", "pair")
        ledger = self.root / ".lab/catalogs/partner/addons/ledger/compose.yml"
        self.assertIn("../../partner/addons/ledger/compose.yml", (self.root / pair).read_text(encoding="utf-8"))
        self.assertNotIn("partner", [c["id"] for c in read_config(self)["catalogs"]], "missing: not added")
        declare(self, "partner")
        self.assertIsNone(read_lock(self)["catalogs"]["partner"]["pin"], "downloading: added, no pin")
        declare(self, "partner", error="unreachable")
        self.assertEqual(read_lock(self)["catalogs"]["partner"]["error"], "unreachable", "failed: Try again")
        install(self, "partner", drop=["addons/ledger"])
        self.assertTrue((self.root / ".lab/catalogs/partner/catalog.yml").is_file())
        self.assertFalse(ledger.exists(), "gone: installed, and the unit is not in it")
        install(self, "partner", version="0.9.0")
        self.assertTrue(ledger.is_file())
        have = tuple(int(n) for n in read_lock(self)["catalogs"]["partner"]["version"].split("."))
        floor = re.search(r"min: (\d+\.\d+\.\d+)", (CATALOGS / "fixture/catalog.yml").read_text(encoding="utf-8"))
        self.assertLess(have, tuple(int(n) for n in floor.group(1).split(".")), "old: below the minimum")

    def test_local_copies_a_unit_of_this_machine_to_the_personal_area(self):
        self.built()
        where = local(self, "scratch")
        self.assertEqual(where, self.root / ".lab/catalogs/local/addons/scratch")
        self.assertEqual(sorted(p.name for p in where.iterdir()), ["README.md", "compose.yml"])
        sandbox = local(self, "sandbox")
        self.assertIn(f"image: {seeder_image(self)}", (sandbox / "compose.yml").read_text(encoding="utf-8"))
        self.assertTrue((sandbox / "seed/apim/apis/sandbox.json").is_file())
        self.assertEqual((sandbox / "compose.yml").stat().st_mode & 0o777, 0o644)
        self.assertFalse((self.root / ".lab/catalogs/local/.installed.json").exists())
        self.assertEqual([c["id"] for c in read_config(self)["catalogs"]], ["fixture"], "the area has no entry")
        self.assertNotIn("local", read_lock(self)["catalogs"])
        (where / ".env").write_text("A=1\n", encoding="utf-8")
        self.assertEqual(local(self, "scratch"), where, "twice")
        self.assertTrue((where / ".env").is_file(), "what is not the unit's own stays")
        for bad, args in (("echo", {}), ("../scratch", {}), ("scratch", {"kind": "playground"}), ("", {})):
            with self.assertRaisesRegex(ValueError, "unit of the personal area"):
                local(self, bad, **args)

    def test_a_helper_asks_for_a_project_folder_first(self):
        for call in (lambda: install(self, "partner"), lambda: default(self), lambda: lock(self, "partner"),
                     lambda: local(self, "scratch"), lambda: core(self), lambda: declare(self, "partner")):
            with self.assertRaisesRegex(RuntimeError, "project folder"):
                call()

    def test_a_module_that_is_missing_is_a_skip_and_one_that_lost_a_name_or_cannot_be_imported_is_a_failure(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        folder = Path(holder.name).resolve()
        (folder / "wpf_probe_there.py").write_text("KEPT = 1\n", encoding="utf-8")
        (folder / "wpf_probe_broken.py").write_text("import wpf_probe_nothing_at_all\n", encoding="utf-8")
        self.patch(sys, "path", [str(folder), *sys.path])
        self.addCleanup(lambda: [sys.modules.pop(one, None) for one in ("wpf_probe_there", "wpf_probe_broken")])
        importlib.invalidate_caches()
        err = io.StringIO()
        self.patch(sys, "stderr", err)
        for missing in ("wpf_probe_absent", "wpf_probe_absent.child"):
            with self.assertRaises(unittest.SkipTest):
                _module(missing, "the Lab", "anything")
        self.assertRegex(err.getvalue(), r"\ASKIP wpf_probe_absent does not exist yet \(the Lab\)\nSKIP wpf_probe_absent.child")

        def fails(call, error):   # a skip here would be the quiet pass that this test is about
            try:
                call()
            except unittest.SkipTest as skipped:
                self.fail(f"skipped, and said {skipped}: it must fail with {error.__name__}")
            except error as raised:
                return raised
            self.fail(f"did not fail with {error.__name__}")

        self.assertIs(_module("wpf_probe_there", "the Lab", "KEPT"), sys.modules["wpf_probe_there"])
        self.assertIn("wpf_probe_there has no RENAMED, GONE",
                      str(fails(lambda: _module("wpf_probe_there", "the Lab", "KEPT", "RENAMED", "GONE"), AssertionError)))
        broken = fails(lambda: _module("wpf_probe_broken", "the Lab"), ModuleNotFoundError)
        self.assertEqual(broken.name, "wpf_probe_nothing_at_all", "the fault is shown, not skipped")
        self.assertIn("lab_ui.catalogs has no nothing_of_this_name",
                      str(fails(lambda: _lab("catalogs", "the Lab", "nothing_of_this_name"), AssertionError)))
        self.assertIs(_lab("catalogs", "the Lab"), sys.modules["lab_ui.catalogs"])

    def test_a_missing_tool_is_a_skip_that_shows(self):
        self.patch(sys.modules[__name__], "have_git", lambda: False)
        self.patch(sys.modules[__name__], "have_compose", lambda: False)
        for need in (need_git, need_compose):
            err = io.StringIO()
            self.patch(sys, "stderr", err)
            with self.assertRaises(unittest.SkipTest):
                need()
            self.assertRegex(err.getvalue(), r"\ASKIP .+\n\Z")

    def test_the_ids_of_the_units_are_those_of_section_3(self):
        self.assertEqual(unit_id("fixture", "addon", "echo"), "echo")
        self.assertEqual(unit_id("partner", "addon", "ledger"), "@partner/ledger")
        self.assertEqual(unit_id("local", "addon", "scratch"), "local/scratch")
        self.assertEqual(unit_id("partner", "addon", "ledger", default="partner"), "ledger")
        self.assertEqual(unit_of(".lab/catalogs/partner/playgrounds/p/compose.yml"), ("playground", "@partner/p"))
        self.assertEqual(unit_of(".lab/catalogs/fixture/setups/pair.yml"), ("setup", "pair"))
        self.assertEqual(unit_of(".lab/catalogs/local/addons/scratch/compose.yml"), ("addon", "local/scratch"))
        for path in (".lab/catalogs/.stage-x/addons/a/compose.yml", ".lab/catalogs/fixture/addons/a/certs/compose.yml",
                     "addons/a/compose.yml", ".lab/catalogs/fixture/addons/A/compose.yml",
                     ".lab/catalogs/fixture/setups/a/b.yml", ".lab/catalogs/a/addons/x/compose.yml",
                     ".lab/catalogs/a-/addons/x/compose.yml", ".lab/catalogs/-a/addons/x/compose.yml",
                     ".lab/catalogs/Fixture/addons/x/compose.yml", f".lab/catalogs/{'a' * 25}/addons/x/compose.yml",
                     ".lab/catalogs/gravitee/addons/x/compose.yml", ".lab/catalogs/stage/addons/x/compose.yml",
                     ".lab/catalogs/official/setups/x.yml", ".lab/catalogs/lab/playgrounds/x/compose.yml", None, 7):
            self.assertIsNone(unit_of(path), path)
        self.assertEqual(unit_of(f".lab/catalogs/{'a' * 24}/addons/x/compose.yml"), ("addon", f"@{'a' * 24}/x"), "24 is allowed")
        self.assertEqual(unit_of(".lab/catalogs/gravitee/addons/x/compose.yml", default="gravitee"), ("addon", "x"),
                         "the reserved word is the default catalog's when the default file says so")

    def test_the_ids_agree_with_paths_once_it_reads_catalogs(self):
        self.assertTrue(hasattr(paths, "default_ns"), "lab_ui.paths no longer reads the default namespace")
        self.built()
        install(self, "partner")
        local(self, "scratch")
        every = [(ns, kind, name) for ns in ("fixture", "partner") for kind, name in units(ns)]
        for ns, kind, name in every + [("local", "addon", "scratch")]:
            self.assertEqual(paths.unit_of(unit_file(ns, kind, name)), unit_of(unit_file(ns, kind, name)), name)
        spellings = ["fixture", "partner", "local", "gravitee", "stage", "lab-new", "a", "ab", "a-", "-a", "A1", "a" * 24,
                     "a" * 25, "x-y-z", "9a", ".stage-x", "official", "this", ""]
        shapes = ("addons/{n}/compose.yml", "setups/{n}.yml", "playgrounds/{n}/compose.yml", "addons/{n}/x/compose.yml",
                  "setups/{n}/x.yml", "addons/{n}/compose.yaml", "decks/{n}/compose.yml")
        names = ["echo", "a", "A", "a_b", "a-b", "", "x" * 30]
        rows = [f".lab/catalogs/{ns}/{shape.format(n=name)}" for ns in spellings for shape in shapes for name in names]
        self.assertGreater(len(rows), 900)
        for path in rows:
            self.assertEqual(paths.unit_of(path), unit_of(path), path)

    # -- git ----------------------------------------------------------------------------------------------------

    def git_built(self, **patch):
        need_git()
        self.built()
        return install(self, "partner", kind="git", **patch)

    def test_a_git_install_is_a_bare_remote_the_files_of_its_tree_and_the_manifest_of_section_2_3(self):
        done = self.git_built()
        remote_ = done.remote
        self.assertRegex(done.commit, r"[0-9a-f]{40}")
        self.assertEqual(remote_.head(), done.commit)
        self.assertEqual(done.entry, {"id": "partner", "kind": "git", "url": remote_.url, "branch": "main",
                                      "auto": True, "added": "2026-10-16T09:01:00Z"})
        self.assertEqual(remote_.git("rev-parse", "--is-bare-repository"), "true")
        tree = {line.split("\t")[1]: line.split()[2]
                for line in remote_.git("ls-tree", "-r", done.commit).splitlines()}
        manifest = json.loads((done.path / ".installed.json").read_text(encoding="utf-8"))
        self.assertEqual(list(manifest), ["schema", "catalog", "commit", "files", "units", "ignored"])
        self.assertEqual((manifest["schema"], manifest["catalog"], manifest["commit"], manifest["ignored"]),
                         (1, "partner", done.commit, 0))
        self.assertEqual({path: record["oid"] for path, record in manifest["files"].items()}, tree)
        for path, record in manifest["files"].items():
            data = (done.path / path).read_bytes()
            self.assertEqual(record, {"mode": "100644", "oid": _blob(data), "sha256": hashlib.sha256(data).hexdigest(),
                                      "size": len(data)}, path)
        self.assertEqual(list(manifest["units"]), ["addon:ledger"])
        self.assertRegex(manifest["units"]["addon:ledger"], r"sha256:[0-9a-f]{64}")
        self.assertEqual((done.lock["pin"], done.lock["tip"]), (done.commit, done.commit))
        self.assertEqual(done.lock["roots"], [done.commit])
        self.assertEqual((done.lock["installed"], done.lock["checked"], done.lock["error"], done.lock["pending"]),
                         (INSTALLED, CHECKED, None, None))
        self.assertEqual(manifest["commit"], read_lock(self)["catalogs"]["partner"]["pin"], "valid for the pin")

    def test_the_unit_digest_is_the_sha256_of_the_sorted_lines_of_its_files(self):
        done = self.git_built()
        manifest = json.loads((done.path / ".installed.json").read_text(encoding="utf-8"))
        lines = sorted(f"100644 {record['oid']} {path}\n" for path, record in manifest["files"].items()
                       if path.startswith("addons/ledger/"))
        digest = "sha256:" + hashlib.sha256("".join(lines).encode()).hexdigest()
        self.assertEqual(manifest["units"]["addon:ledger"], digest)
        changed = install(self, "partner", kind="git", files={"addons/ledger/README.md": "Other.\n"})
        after = json.loads((changed.path / ".installed.json").read_text(encoding="utf-8"))["units"]["addon:ledger"]
        self.assertNotEqual(after, digest)

    def test_a_git_install_is_read_only_with_room_left_for_machine_files(self):
        done = self.git_built()
        unit = done.path / "addons/ledger"
        self.assertEqual((unit / "compose.yml").stat().st_mode & 0o777, 0o444)
        self.assertEqual(unit.stat().st_mode & 0o777, 0o755)
        (unit / ".env").write_text("PARTNER_LEDGER_CURRENCY=USD\n", encoding="utf-8")
        with self.assertRaises(PermissionError):
            (unit / "compose.yml").write_text("x", encoding="utf-8")
        writable = install(self, "partner", kind="git", readonly=False)
        self.assertEqual((writable.path / "addons/ledger/compose.yml").stat().st_mode & 0o777, 0o644)

    def test_a_git_install_is_the_same_commit_every_time(self):
        first = self.git_built().commit
        self.assertEqual(install(self, "partner", kind="git").commit, first)
        self.assertNotEqual(install(self, "partner", kind="git", version="2.0.1").commit, first)

    def test_remote_serves_a_catalog_and_installs_nothing(self):
        need_git()
        self.built()
        served = remote(self, "partner", version="2.5.0")
        self.assertRegex(served.url, r"\Afile:///.+/partner\.git\Z")
        self.assertIn("version: 2.5.0", served.git("show", "HEAD:catalog.yml"))
        self.assertFalse((self.root / ".lab/catalogs/partner").exists())
        self.assertEqual([c["id"] for c in read_config(self)["catalogs"]], ["fixture"])
        same = install(self, "partner", kind="git").commit
        self.assertEqual(remote(self, "partner").head(), same, "the same tree, the same commit")
        with self.assertRaises(ValueError):
            remote(self, "partner", audience="public")

    def test_a_git_install_keeps_out_what_rule_5_keeps_out_but_the_repository_keeps_it(self):
        extra = {".env": "A=1\n", "addons/ledger/.env": "B=2\n", "AGENTS.md": "x\n", "README.md": "x\n",
                 "addons/ledger/k.pem": "k\n", "addons/ledger/secrets/s": "s\n",
                 "addons/Bad/compose.yml": "x-gravitee: {kind: addon}\n", "addons/ledger/data/ok.json": "{}\n",
                 "addons/orphan/notes.md": "x\n", ".github/ci.yml": "x\n"}
        done = self.git_built(files=extra)
        manifest = json.loads((done.path / ".installed.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(manifest["files"]), ["addons/ledger/README.md", "addons/ledger/compose.yml",
                                                     "addons/ledger/data/ok.json", "catalog.yml"])
        self.assertEqual(manifest["ignored"], 5, "refused inside the units; the top level is neither kept nor counted")
        listed = done.remote.git("ls-tree", "-r", "--name-only", done.commit).splitlines()
        self.assertTrue(set(extra) <= set(listed), "the repository holds all of it")
        for name in (".env", "AGENTS.md", "README.md", "addons/ledger/.env", "addons/orphan"):
            self.assertFalse((done.path / name).exists(), name)

    def test_the_filter_agrees_with_the_one_of_the_store(self):
        store = _lab("catalogs.store", "the Lab", "selected", "Entry")
        selected = store.selected
        names = ["README.md", "Makefile", ".github/ci.yml", ".env", "Notes.md", "bad name.md", "addons/ledger/.env",
                 "addons/ledger/.ENV.local", "addons/ledger/.env.example", "addons/.env.example/x",
                 "addons/ledger/Agents.MD", "addons/ledger/ca.PEM", "addons/ledger/.Cursor-rules/a",
                 "addons/ledger/.git/config", "addons/ledger/Secrets/s", "addons/ledger/data/ok.json",
                 "addons/ledger/.github/copilot-instructions.md", "addons/Bad/compose.yml", "addons/orphan/notes.md",
                 "addons/loose.md", "setups/solo.yml", "setups/sub/x.yml", "setups/notes.txt",
                 "playgrounds/p/compose.yml", "playgrounds/p/guide.md", "playgrounds/p/a/b/c/d/e/f/g.yml",
                 "addons/ledger/Case.md", "addons/ledger/case.md"]
        tree = {name: b"x\n" for name in names}
        tree.update({"catalog.yml": b"x\n", "addons/ledger/compose.yml": b"x\n",
                     "addons/ledger/big.json": b"j" * (SMALL + 1), "addons/ledger/big.bin": b"b" * (LARGE + 1),
                     "addons/ledger/ok.bin": b"b" * LARGE})
        entries = [store.Entry("100644", _blob(data), len(data), path) for path, data in sorted(tree.items())]
        kept, ignored = selected(entries)
        ours = _selected(tree)
        self.assertEqual((sorted(ours[0]), ours[1]), (sorted(entry.path for entry in kept), ignored))
        self.assertIn("addons/ledger/data/ok.json", ours[0])
        self.assertNotIn("addons/ledger/Agents.MD", ours[0])

    def test_the_manifest_agrees_with_the_store_once_it_exists(self):
        store = _lab("catalogs.store", "the Lab", "selected", "unit_digests", "listing")
        selected, digests, listing = store.selected, store.unit_digests, store.listing
        done = self.git_built(files={"addons/ledger/data/ok.json": "{}\n", "addons/ledger/.env": "B=2\n"})
        kept, ignored = selected(listing(done.remote.bare, done.commit))
        manifest = json.loads((done.path / ".installed.json").read_text(encoding="utf-8"))
        ours = {key: value for key, value in digests(kept).items() if key != "@catalog"}
        self.assertEqual((ours, ignored), (manifest["units"], manifest["ignored"]))
        self.assertEqual(digests(kept)["@catalog"], manifest["files"]["catalog.yml"]["oid"])
        self.assertEqual(sorted(entry.path for entry in kept), sorted(manifest["files"]))

    def test_the_lab_store_is_made_on_request_with_the_refs_and_the_settings_of_section_2(self):
        done = self.git_built(store=True)
        store = self.root / ".lab/cache/git/partner.git"
        git = _gitfixture().git
        for ref in ("pin", "tip"):
            self.assertEqual(git("rev-parse", f"refs/lab/{ref}", repo=store).strip(), done.commit)
        self.assertEqual(git("for-each-ref", "--format=%(refname)", repo=store).split(), ["refs/lab/pin", "refs/lab/tip"],
                         "no branch of its own")
        for key, value in STORE_SETTINGS:
            self.assertEqual(git("config", "--get", key, repo=store).strip(), value, key)
        self.assertNotIn("remote.", git("config", "--list", repo=store), "no origin: the Lab names the address on each fetch")
        self.assertEqual(git("rev-parse", "--is-bare-repository", repo=store).strip(), "true")
        self.assertEqual(os.listdir(store / "hooks") if (store / "hooks").exists() else [], [], "no templates, no hooks")
        self.assertFalse((self.root / ".lab/cache/git/fixture.git").exists())
        self.assertEqual(git("cat-file", "-t", done.commit, repo=store).strip(), "commit")
        again = install(self, "partner", kind="git", store=True)
        self.assertEqual(again.commit, done.commit, "again: the same store")
        self.assertEqual(git("for-each-ref", "--format=%(refname)", repo=store).split(), ["refs/lab/pin", "refs/lab/tip"])

    def test_the_lab_store_is_what_the_stores_module_makes_of_a_fetch(self):
        store_module = _lab("catalogs.store", "the Lab", "init", "resolve", "roots", "is_ancestor", "listing", "selected")
        done = self.git_built(store=True)
        store = self.root / ".lab/cache/git/partner.git"
        git = _gitfixture().git
        made = _scratch(self, "reference") / "reference.git"
        store_module.init(made)
        for key, value in STORE_SETTINGS:
            self.assertEqual(git("config", "--get", key, repo=made).strip(), value, "store.init sets it too")
        self.assertEqual(store_module.resolve(store, "pin"), done.commit)
        self.assertEqual(store_module.resolve(store, "tip"), done.commit)
        self.assertEqual(store_module.roots(store, "tip"), sorted(done.lock["roots"]))
        self.assertTrue(store_module.is_ancestor(store, "pin", "tip"))
        kept, ignored = store_module.selected(store_module.listing(store, "pin"))
        self.assertEqual((sorted(entry.path for entry in kept), ignored),
                         (sorted(json.loads((done.path / ".installed.json").read_text(encoding="utf-8"))["files"]), 0))

    def test_the_lab_fetches_the_remote_of_a_git_install_and_gets_the_pin_the_roots_and_the_files_the_lock_names(self):
        gitx = _lab("catalogs.gitx", "the Lab", "parse_address")
        store = _lab("catalogs.store", "the Lab", "fetch", "roots", "listing", "selected")
        done = self.git_built()
        _gitfixture().isolate(self)
        fetched = self.root / ".lab/cache/git/fetched.git"
        tip = store.fetch(gitx.parse_address(done.entry["url"]), done.entry["branch"], fetched)
        self.assertEqual(tip, done.lock["pin"])
        self.assertEqual(store.roots(fetched, tip), sorted(done.lock["roots"]))
        kept, ignored = store.selected(store.listing(fetched, tip))
        manifest = json.loads((done.path / ".installed.json").read_text(encoding="utf-8"))
        self.assertEqual((sorted(entry.path for entry in kept), ignored), (sorted(manifest["files"]), manifest["ignored"]))

    def test_a_remote_takes_new_commits_for_the_update_flows(self):
        done = self.git_built()
        before = done.remote.head()
        after = done.remote.commit({"addons/ledger/README.md": "Changed.\n"}, "Change the ledger",
                                   remove=["catalog.yml"])
        self.assertNotEqual(before, after)
        self.assertEqual(done.remote.git("rev-parse", "main"), after)
        self.assertNotIn("catalog.yml", done.remote.git("ls-tree", "-r", "--name-only", after).splitlines())
        self.assertEqual(done.remote.git("rev-list", "--count", "main"), "2")
        folder = done.remote.commit(remove=["addons/ledger"], message="Remove the ledger")
        self.assertEqual(done.remote.git("ls-tree", "-r", "--name-only", folder), "")
        for bad in ({"files": {"../x": "x"}}, {"remove": ["nothing"]}, {"remove": [""]}):
            with self.assertRaises(ValueError):
                done.remote.commit(**bad)

    def test_a_catalog_can_be_cloned_from_its_remote(self):
        done = self.git_built()
        clone = _scratch(self, "clones") / "partner"
        _gitfixture().git("clone", "--quiet", "--", done.entry["url"], str(clone))
        self.assertEqual((clone / "catalog.yml").read_bytes(), (done.path / "catalog.yml").read_bytes())

    def test_lock_on_a_git_install_keeps_the_manifest_true_to_the_file(self):
        done = self.git_built()
        lock(self, "partner", audience="private", version="2.1.0", lab=2)
        manifest = json.loads((done.path / ".installed.json").read_text(encoding="utf-8"))
        data = (done.path / "catalog.yml").read_bytes()
        self.assertEqual(manifest["files"]["catalog.yml"], {
            "mode": "100644", "oid": _blob(data), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
        self.assertEqual((done.path / "catalog.yml").stat().st_mode & 0o777, 0o444)
        self.assertEqual(manifest["commit"], done.commit, "the repository is not rewritten")
