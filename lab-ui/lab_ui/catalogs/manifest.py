"""What a catalog says of itself and which of its versions this Lab can read: the `catalog.yml` that sits at its root, the engine's contract levels and the one rule that compares them, the compatible pin that picks the
newest commit this Lab reads, and the generated index.

`read_catalog` reads the file with Compose (`compose.raw`, no new parser), refuses what the table of 4.1 refuses and
answers the block cleaned for display: names cut and stripped of control characters, lists without repeats, every
optional key present. `validate_catalog` is the same table as a pure function over the mapping, which is how `catalog.py
check` and the template's tests use it. It does not judge a namespace against the reserved words or the catalogs the
person has (`registry.check_namespace`) nor a name against the address that serves it: the listed names are `borrowed`,
and the caller that knows the address says which.

`compat`, `pick`, `parse_version` and `at_least` are pure and tested over tables. `levels` is the git half of `pick`:
the versions of `catalog.yml` in the first-parent history of the tip, read from the bare store, newest first."""

import json
import os
import re
import stat
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from ..docker import compose
from .. import paths
from . import gitx, registry, tree
from . import store as bare
from .registry import CatalogError

SCHEMA = 1   # the format of catalog.yml and of the layout that this Lab reads
BLOCK = "x-gravitee-catalog"
FILE = registry.CATALOG_FILE
# What a catalog.yml may weigh before it is not read: the store filter keeps the file under this, and a folder is looked
# at here
FILE_LIMIT = 1024 * 1024
# The keys of the block this Lab knows; any other is ignored by the Lab and reported by `catalog.py check`
KEYS = ("schema", "id", "name", "description", "version", "lab", "license", "images", "contribute", "shared_settings",
        "moved_from", "canary", "requires", "host_access")
# A name that a catalog may not take, whatever its address: it would read as a seal the Lab never gives
RESERVED_NAMES = ("official", "verified", "personal", "this machine")
NAME_LIMIT, DESCRIPTION_LIMIT = 40, 90
LIST_LIMIT = 50   # moved_from and canary: more than this is not a list a person wrote
SETTINGS_LIMIT = 100
# The file of the standard a catalog follows (governance 3.2): the index carries its level. The Lab never installs it, so it is
# found beside a catalog that is a linked folder (the CI of a catalog), not in an install
STANDARD_FILE = ".github/standard.json"
STANDARD_LIMIT = 1 << 16   # a standard.json is under 4 KB (SD04 says so): more than this is not read, and is no level
STANDARD_MAX = 1000000   # and a level of more is a number nobody wrote

# What a catalog.yml may not contain: the controls, and the line breaks that YAML takes and a line reading would not
# (next line, line and paragraph separators), and a byte order mark in the middle
_ODD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x85\u2028\u2029\ufeff]")
# A line at the left edge that is a key of the block (or any `x-` extension, which Compose ignores), quoted or not
_EXTENSION = re.compile(r"""["']?x-""")
_START = re.compile(r"---(?:[ \t]+#.*|[ \t]*)")   # the line that opens a document, alone or with a comment
_VERSION = re.compile(r"[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}")
_NS = re.compile(paths.NS)
_LICENSE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*(?: (?:AND|OR|WITH) [A-Za-z0-9][A-Za-z0-9.+-]*)*")
_IMAGES = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]{1,5})?(?:/[a-z0-9][a-z0-9._-]*)+")
_SETTING = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_UNIT = re.compile(r"(addon|setup|playground):[a-z0-9-]+")
_SHA = re.compile(r"[0-9a-f]{40}")

NAMESPACE_PROBLEM = "id must be 2 to 24 lowercase letters, digits and dashes"


# ---------------------------------------------------------------- versions and levels

def parse_version(text: str) -> Tuple[int, int, int]:
    """(major, minor, patch) of a version of three numbers with dots, `1.4.0`; ValueError for anything else (a fourth
    number, a suffix, a sign, a digit that is not an ASCII one)."""
    if not isinstance(text, str) or not _VERSION.fullmatch(text):
        raise ValueError(f"not a version of three numbers: {text!r}")
    major, minor, patch = (int(part) for part in text.split("."))
    return major, minor, patch


def at_least(have: str, need: str) -> bool:
    """Whether the version `have` is `need` or later (`min` of a required catalog, 5.3). False when either is not a
    version, so that a doubt never counts as enough."""
    try:
        return parse_version(have) >= parse_version(need)
    except ValueError:
        return False


def _level(value, default):
    return value if type(value) is int else default


def compat(level: int, contract: int, contract_min: int) -> str:
    """How a level `L` (a catalog's `lab`, or a unit's `requires.lab`) stands with this Lab: "ok" when
    `contract_min <= L <= contract`, "new" above `contract` (needs a newer Lab), "old" below `contract_min` (written for
    an older Lab). An absent `contract` or `contract_min` is 1; a level that is not a whole number is "old": it is no
    level this Lab reads, and it must not be taken for a newer one that an update would fix."""
    high, low = _level(contract, 1), _level(contract_min, 1)
    if type(level) is not int:
        return "old"
    return "new" if level > high else "old" if level < low else "ok"


def pick(levels: list, contract: int, contract_min: int) -> tuple:
    """The compatible pin, pure. `levels` is [(commit, lab)], newest first, one for each distinct `catalog.yml` in
    the first-parent history of the tip: `levels[0]` is the tip with the `lab` it states, `levels[i]` the newest commit
    that still has the `catalog.yml` of the i-th older version. Answers (the first commit whose `lab` this Lab reads, or
    None; `compat` of the tip). With nothing to choose from there is no commit and the tip is "old".

    A `lab` of None is a form of the file that the tolerant reader could not be sure of (`levels`): it is not "old", it is
    not known, and the strict reading of the staged file says (`read_catalog` at Add, `install.plan_update` at Update, which
    is given the level of this Lab). So it is taken for a version this Lab may read, and a tip of it is "ok" until that
    reading says otherwise: a refusal with the real level, never a catalog passed over for a level it was never found to
    have."""
    if not levels:
        return None, "old"
    tip_state = "ok" if levels[0][1] is None else compat(levels[0][1], contract, contract_min)
    for commit, lab in levels:
        if lab is None or compat(lab, contract, contract_min) == "ok":
            return commit, tip_state
    return None, tip_state


def compat_error(level: int, contract: int, contract_min: int, listed: bool = False) -> Optional[CatalogError]:
    """The refusal of the catalogue of errors for a catalog written for `level` (`lab_new`, `lab_old`), or None when this Lab reads
    it. `listed` words `lab_old` for a catalog the engine lists, where "ask its owners" is wrong."""
    state, high, low = compat(level, contract, contract_min), _level(contract, 1), _level(contract_min, 1)
    if state == "new":
        return CatalogError("lab_new", f"Needs Lab level {level}, this Lab reads {low} to {high}. Run make update, "
                            "then add it again.", 400)
    if state == "old" and listed:
        return CatalogError("lab_old", "The catalog is not ready for this version of the Lab. Try again later.", 400)
    if state == "old":
        return CatalogError("lab_old", f"Written for an older Lab (level {level}, this Lab reads {low} to {high}). Ask "
                            "its owners to update it.", 400)
    return None


def no_commit(levels: list, contract: int, contract_min: int, listed: bool = False) -> CatalogError:
    """The refusal when `pick` found no commit (the one rule): `no_readable_version` when the tip needs a newer Lab
    (so does every version), `lab_old` when the catalog is older than this Lab reads. `listed` words `lab_old` for a
    catalog the engine lists."""
    level = levels[0][1] if levels and levels[0][1] is not None else 0
    if compat(level, contract, contract_min) != "old":
        return CatalogError("no_readable_version",
                            "Every version of this catalog needs a newer Lab. Run make update, then add it again.", 400)
    return compat_error(level, contract, contract_min, listed)


# ---------------------------------------------------------------- catalog.yml

def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _whole(value) -> bool:
    return type(value) is int


def _names(value, pattern, limit):
    """The items of a list of names (`value`) that are one of `pattern`, once each, None when it is not a list of them
    or longer than `limit`."""
    if not isinstance(value, list) or len(value) > limit or not all(isinstance(one, str) and pattern.fullmatch(one)
                                                                     for one in value):
        return None
    return list(dict.fromkeys(value))


def _required(meta, problems):
    """The checks of the fields every catalog.yml has: schema, id, name, description, version, lab."""
    if "schema" not in meta:
        problems.append("schema is required")
    elif not _whole(meta["schema"]) or meta["schema"] < 1:
        problems.append("schema must be a whole number, such as 1")
    elif meta["schema"] > SCHEMA:
        problems.append(f"schema {meta['schema']} is newer than this Lab reads")
    if "id" not in meta:
        problems.append("id is required")
    elif not (isinstance(meta["id"], str) and _NS.fullmatch(meta["id"])):
        problems.append(NAMESPACE_PROBLEM)
    for key, what in (("name", "the name that is shown"), ("description", "one line that says what it holds")):
        if key not in meta:
            problems.append(f"{key} is required")
        elif not _text(meta[key]) or not registry.shown(meta[key], 1 << 16):
            problems.append(f"{key} must be text: {what}")
    if "version" not in meta:
        problems.append("version is required")
    elif not (isinstance(meta["version"], str) and _VERSION.fullmatch(meta["version"])):
        problems.append("version must be three numbers with dots, such as 1.0.0")
    if "lab" not in meta:
        problems.append("lab is required")
    elif not _whole(meta["lab"]) or meta["lab"] < 0:
        problems.append("lab must be a whole number: the Lab level the catalog needs")


def _optional(meta, problems):
    """The checks of the fields a catalog.yml may leave out."""
    if meta.get("license") is not None and not (isinstance(meta["license"], str) and len(meta["license"]) <= 64
                                                and _LICENSE.fullmatch(meta["license"])):
        problems.append("license must be an SPDX identifier, such as Apache-2.0")
    if meta.get("images") is not None and not (isinstance(meta["images"], str) and len(meta["images"]) <= 200
                                               and _IMAGES.fullmatch(meta["images"])):
        problems.append("images must be a registry prefix without a tag, such as ghcr.io/acme/lab-hub")
    if meta.get("contribute") is not None and meta["contribute"] not in ("pull-request", "none"):
        problems.append("contribute must be pull-request or none")
    if meta.get("shared_settings") is not None and _names(meta["shared_settings"], _SETTING, SETTINGS_LIMIT) is None:
        problems.append("shared_settings must be a list of setting names in capitals, such as GEMINI_API_KEY")
    if meta.get("host_access") is not None and not isinstance(meta["host_access"], bool):
        problems.append("host_access must be true or false")
    for key in ("moved_from", "canary"):
        if meta.get(key) is not None and _names(meta[key], _UNIT, LIST_LIMIT) is None:
            problems.append(f"{key} must be a list of kind:name, such as addon:echo, at most {LIST_LIMIT}")


def _required_catalogs(requires, problems):
    """The checks of `requires.catalogs`: a mapping of namespace to {name, url, min}."""
    if requires is None:
        return
    if not isinstance(requires, dict):
        problems.append("requires must be a mapping")
        return
    declared = requires.get("catalogs")
    if declared is None:
        return
    if not isinstance(declared, dict):
        problems.append("requires.catalogs must be a mapping of namespaces")
        return
    for ns, want in declared.items():
        where = f"requires.catalogs.{ns}"
        if not (isinstance(ns, str) and _NS.fullmatch(ns)):
            problems.append(f"{where}: the namespace must be 2 to 24 lowercase letters, digits and dashes")
        elif not isinstance(want, dict):
            problems.append(f"{where} must hold url and min")
        else:
            if not _address_ok(want.get("url")):
                problems.append(f"{where}.url must be a Git address without a user name, such as "
                                "https://github.com/owner/repo.git")
            if not (isinstance(want.get("min"), str) and _VERSION.fullmatch(want["min"])):
                problems.append(f"{where}.min must be three numbers with dots, such as 1.0.0")
            if want.get("name") is not None and not _text(want["name"]):
                problems.append(f"{where}.name must be text")


def _address_ok(value) -> bool:
    """Whether `value` is a git address the grammar takes and that holds no user name or password."""
    if not isinstance(value, str):
        return False
    try:
        return gitx.parse_address(value).kind == "git"
    except CatalogError:
        return False


def validate_catalog(meta: dict, borrowed: Iterable[str] = ()) -> List[str]:
    """The problems of the mapping under `x-gravitee-catalog`, each a short phrase that follows "catalog.yml: " (the
    table of 4.1, in its order); [] for a valid one. `borrowed`: the names of the listed catalogs that this one is not,
    so a name that is one of them is refused as a name that is not its own. Pure. A key this Lab does not know is not a
    problem (`unknown_keys`), and neither is a namespace that is reserved or taken: the registry says that."""
    if not isinstance(meta, dict):
        return [f"{BLOCK} must be a mapping"]
    problems = []
    _required(meta, problems)
    shown = registry.shown(meta["name"], NAME_LIMIT).lower() if _text(meta.get("name")) else ""
    taken = {registry.shown(one, NAME_LIMIT).lower() for one in borrowed if isinstance(one, str)}
    if shown and (shown in RESERVED_NAMES or shown in taken):
        problems.append(f"name cannot be {registry.shown(meta['name'], NAME_LIMIT)}")
    _optional(meta, problems)
    _required_catalogs(meta.get("requires"), problems)
    return problems


def unknown_keys(meta: dict) -> List[str]:
    """The keys of the block that this Lab does not read, sorted: `catalog.py check` reports them, the Lab ignores them.
    """
    return sorted(str(key) for key in meta if key not in KEYS) if isinstance(meta, dict) else []


def normalize(meta: dict) -> dict:
    """A valid block (`validate_catalog` answered nothing) as the Lab keeps it: names and descriptions for a person
    (normalized, control characters out, white space collapsed, cut), lists without repeats, every optional key present.
    {schema, id, name, description, version, lab, license, images, contribute, shared_settings, moved_from, canary,
    host_access, requires: {catalogs: {ns: {name, url, min}}}}."""
    declared = (meta.get("requires") or {}).get("catalogs") or {}
    return {
        "schema": meta["schema"], "id": meta["id"], "name": registry.shown(meta["name"], NAME_LIMIT),
        "description": registry.shown(meta["description"], DESCRIPTION_LIMIT), "version": meta["version"],
        "lab": meta["lab"], "license": meta.get("license"), "images": meta.get("images"),
        "contribute": meta.get("contribute") or "pull-request",
        "shared_settings": _names(meta.get("shared_settings") or [], _SETTING, SETTINGS_LIMIT),
        "moved_from": _names(meta.get("moved_from") or [], _UNIT, LIST_LIMIT),
        "canary": _names(meta.get("canary") or [], _UNIT, LIST_LIMIT),
        "host_access": meta.get("host_access") is True,
        "requires": {"catalogs": {ns: {"name": registry.shown(want.get("name") or ns, NAME_LIMIT),
                                       "url": gitx.parse_address(want["url"]).url, "min": want["min"]}
                                  for ns, want in declared.items()}}}


def _missing(address):
    """The refusal for a folder or a repository that has no catalog.yml."""
    if address is not None and address.kind == "folder":
        return gitx.fail("folder_not_catalog", address, path=address.display)
    return gitx.fail("not_catalog", address)


def _only_the_block(text: str) -> bool:
    """Whether the text of a catalog.yml holds nothing but `x-` blocks and comments, read line by line without a YAML
    parser: every line at the left edge is a comment, one `---` that opens the document, or a key that starts `x-`; a
    line that is indented belongs to the key above it, and none comes before the first key. Compose acts on what is
    at the top of a document (`include`, `services`, `extends`, `<<`, a flow mapping, a key written in the flow) and on
    nothing under an `x-` key, so what passes here is data for Compose and nothing else. A construct that this cannot
    place (a directive, a second document, an indented first line) is refused: this file is the one of a catalog that is
    read before the person has seen it. `text` has no byte order mark and no unusual line break (`_screen`)."""
    opened = False
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line.strip() or line.lstrip(" \t").startswith("#"):
            continue
        if line[0] in " \t":
            if not opened:
                return False
        elif _START.fullmatch(line) and not opened:
            continue
        elif _EXTENSION.match(line):
            opened = True
        else:
            return False
    return True


def _screen(file) -> None:
    """Reads the file without following a link, bounded, and refuses with `catalog_invalid` a file that is not text or
    holds more than its block (`_only_the_block`): Compose is asked nothing about it, since an `include` that names a pipe
    or a file without end would hold or fill the process that reads it."""
    try:
        fd = os.open(os.fspath(file), os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
                     | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        raise CatalogError("catalog_invalid", "catalog.yml: it could not be read.", 400) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise CatalogError("catalog_invalid", "catalog.yml: it could not be read.", 400)
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(FILE_LIMIT + 1)
    except OSError:
        raise CatalogError("catalog_invalid", "catalog.yml: it could not be read.", 400) from None
    finally:
        os.close(fd)
    if len(data) > FILE_LIMIT:
        raise CatalogError("catalog_invalid", "catalog.yml: it is larger than 1 MB.", 400)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise CatalogError("catalog_invalid", "catalog.yml: it could not be read.", 400) from None
    text = text.lstrip("\ufeff")
    if _ODD.search(text):
        raise CatalogError("catalog_invalid", "catalog.yml: it holds a control character or an unusual line break.", 400)
    if not _only_the_block(text):
        raise CatalogError("catalog_invalid", f"catalog.yml: it may hold only the {BLOCK} block.", 400)


def read_catalog(path: str, address: Optional[gitx.Address] = None, borrowed: Iterable[str] = ()) -> dict:
    """The validated `catalog.yml` at `path` (relative to the project, or absolute), as `normalize` gives it, plus
    `unknown`: the keys it has that this Lab does not read. Read with Compose, so the YAML is Compose's, and never
    through a link, and only after a line screen (`_only_the_block`): the file may hold the `x-gravitee-catalog` block
    and comments and nothing else, so no `include` of a file, a pipe or a device ever reaches Compose, which matters
    for the one file of a catalog that is read before the person has accepted it. Refuses with the code of the catalogue of errors:
    `not_catalog` (`folder_not_catalog` for a folder) when the file is not there, `schema_new` when its format is newer
    than this Lab's, `catalog_invalid` for a file that holds more than its block, for the first problem of
    `validate_catalog` (all of them are in `detail["problems"]`) and for a file Compose cannot read. `address` names the
    repository or folder in the line; `borrowed` is for `validate_catalog`."""
    file = paths.ROOT / path
    try:
        info = file.lstat()
    except OSError:
        raise _missing(address) from None
    if not stat.S_ISREG(info.st_mode):   # a regular file: a link in its place leads anywhere
        raise _missing(address)
    if info.st_size > FILE_LIMIT:
        raise CatalogError("catalog_invalid", "catalog.yml: it is larger than 1 MB.", 400)
    _screen(file)
    try:
        config = compose.raw(str(path), timeout=20)
    except compose.ComposeError as error:
        raise CatalogError("catalog_invalid", "catalog.yml: it could not be read.", 400, output=str(error)) from None
    block = config.get(BLOCK) if isinstance(config, dict) else None
    if block is None:
        raise CatalogError("catalog_invalid", f"catalog.yml: it has no {BLOCK} block.", 400)
    if not isinstance(block, dict):
        raise CatalogError("catalog_invalid", f"catalog.yml: {BLOCK} must be a mapping.", 400)
    if _whole(block.get("schema")) and block["schema"] > SCHEMA:
        raise CatalogError("schema_new", "This catalog uses a newer format. Run make update, then add it again.", 400)
    problems = validate_catalog(block, borrowed)
    if problems:
        raise CatalogError("catalog_invalid", f"catalog.yml: {problems[0]}.", 400, problems=problems)
    return {**normalize(block), "unknown": unknown_keys(block)}


# ---------------------------------------------------------------- the versions of a catalog

def _lab_of(data) -> Optional[int]:
    """The `lab` a catalog.yml states, from its bytes, read the way the registry reads the file of an install; 0 (a
    level no Lab reads) for a file that is missing, damaged or does not say. None when the reader says it is not sure
    (`partial`: an anchor, a scalar on several lines, a number spelled another way, a block it did not find): the answer is
    then not the file's, and the strict reading of a catalog (`read_catalog`, at Add and at Update) is. `pick` takes such
    a version for one this Lab may read, so that the strict reading decides and a level is never invented here."""
    found = registry.scan(data) if data else {}
    if found.get("partial"):
        return None
    return found["lab"] if type(found.get("lab")) is int else 0


def _first_parent(store, commit):
    """The first parent of `commit` in the store, None for a root commit."""
    out = gitx.require_ok(gitx.run(["--git-dir", str(store), "rev-list", "--first-parent", "--max-count=2", commit])).out
    lines = out.decode("utf-8", "replace").split()
    return lines[1] if len(lines) > 1 and _SHA.fullmatch(lines[1]) else None


def levels(store: Path, tip: str, limit: int = 50, address: Optional[gitx.Address] = None) -> list:
    """The versions of this catalog that `pick` chooses among: [(commit, lab)], newest first, at most `limit`.
    `levels[0]` is `tip` with the `lab` its `catalog.yml` states; for each older form of that file there is one more,
    the newest commit that still has it, which is the first parent of the commit that changed the file next. One `git
    log --first-parent` for the commits that touched the file, one read of the file for each, and one `rev-list` for
    each parent: a catalog whose file never changed costs four git commands. A form of the file that does not state a
    level is `lab` 0, which no Lab reads, one the reader cannot be sure of is `lab` None (`_lab_of`), and a form that was
    deleted is left out. A tip with no `catalog.yml` is `not_catalog` (`address` names the repository in the line); a tip
    the store does not hold is `fsck`."""
    commit = bare.resolve(store, tip)
    if commit is None:
        raise bare.MissingObject(tip)
    here = bare.read(store, commit, FILE)
    if here is None:
        raise gitx.fail("not_catalog", address)
    found, cache = [(commit, _lab_of(here))], {}
    touching = bare.touching(store, commit, FILE, limit)
    for newer, older in zip(touching, touching[1:]):
        parent = _first_parent(store, newer)
        if parent is None:
            break
        data = bare.read(store, older, FILE)
        if data is None:   # the file was deleted at `older`: the commits between hold no catalog
            continue
        if data not in cache:
            cache[data] = _lab_of(data)
        found.append((parent, cache[data]))
    return found[:limit]


# ---------------------------------------------------------------- the index

def _standard(ns) -> Optional[int]:
    """The level of the standard that catalog `ns` follows, from its `.github/standard.json`, or None: the file is not there,
    is not a regular file, is a link or lies behind one (`tree.read_inside` opens every folder of the path without following a
    link, so a hostile catalog cannot make the index read a file of this machine), is larger than STANDARD_LIMIT, is not
    UTF-8 JSON, or does not say an integer `standard` of at least 1. Read as data and nothing else: a key it does not know is
    not looked at, and nothing in it is run, imported or followed."""
    try:
        folder = registry.install_dir(ns)   # the one place of a catalog, which says what is not a namespace
    except ValueError:
        return None
    data = tree.read_inside(str(folder), STANDARD_FILE, STANDARD_LIMIT + 1)
    if data is None or len(data) > STANDARD_LIMIT:
        return None
    try:
        found = json.loads(data.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        return None
    level = found.get("standard") if isinstance(found, dict) else None
    return level if type(level) is int and 1 <= level <= STANDARD_MAX else None


def _builds(unit, services, raw=None) -> list:
    """What a unit builds, for the index: [{service, image}], the services of the unit whose file has a `build:` (`raw`, the
    files of the project as gather() read them), with the image each runs as Compose resolved it, or None for a build that
    names none. The file says it, and not the `built` of the project, which also marks a service that runs an image another
    service of the project builds, in this catalog or in another: that service builds nothing, and the same catalog would
    list other services with other catalogs installed. Without the files (a project given as it is) `built` is all there is.
    No digest and no time, so two runs give the same list, sorted by service. A setup defines no service, and a unit that is
    blocked lists none (the project clears them); the service built from the Lab's seeder is listed like any other, and
    whoever publishes images leaves it out."""
    defined = None
    if isinstance(raw, dict):
        file = raw.get(unit.get("file")) if isinstance(unit.get("file"), str) else None
        defined = file.get("services") if isinstance(file, dict) else None
        defined = defined if isinstance(defined, dict) else {}
    found = []
    for name in unit.get("services") or []:
        service = services.get(name) if isinstance(services, dict) and isinstance(name, str) else None
        if not isinstance(service, dict):
            continue
        if defined is None:
            builds = bool(service.get("built"))
        else:
            written = defined.get(name)
            builds = isinstance(written, dict) and bool(written.get("build"))
        if builds:
            image = registry.shown(service["image"], 300) if service.get("image") else ""
            found.append({"service": registry.shown(name, 100), "image": image or None})
    return sorted(found, key=lambda one: (one["service"], one["image"] or ""))


def _index_unit(ns, kind, uid, unit, project, services=None, raw=None):
    """One entry of the index for a unit of `ns`: its name inside the catalog and what it says of itself."""
    name = paths.split_ref(uid)[1]
    prefix = f"{paths.CATALOGS_DIR}/{ns}/"
    file = unit.get("file") or paths.unit_file(kind, uid)
    brings = []
    for other in (unit.get(project.BROUGHT[kind]) or []):
        mine = paths.origin(other) == ns
        brings.append(f"addon:{paths.split_ref(other)[1] if mine else other}")
    found = {"kind": kind, "id": name, "path": file[len(prefix):] if file.startswith(prefix) else file,
             "name": unit.get("name") or name, "description": unit.get("description") or "",
             "category": unit.get("category") or "", "tags": list(unit.get("tags") or []),
             "requires": dict(unit.get("requires") or {}), "brings": sorted(dict.fromkeys(brings)),
             "conflicts": dict(unit.get("conflicts") or {}), "builds": _builds(unit, services, raw)}
    if kind == "playground":
        found.update({"duration": unit.get("duration"), "first_start": unit.get("first_start"),
                      "source": unit.get("source")})
    return found


def build_index(ns: str, loaded: Optional[dict] = None, meta: Optional[dict] = None, raw: Optional[dict] = None) -> dict:
    """The generated `catalog.json` of catalog `ns`: {schema, catalog: {id, name, description, version, lab,
    license}, standard (the level of the standard the catalog follows, from its `.github/standard.json`; the key is there only
    when that file says one, _standard), units: [{kind, id, path, name, description, category, tags, requires, brings,
    conflicts, builds: [{service, image}]} and, for a playground, duration, first_start, source]}, the units sorted by kind
    and id. It reads the project as `project.build` gives it (`loaded`) and the catalog's own `catalog.yml` (`meta`, which
    `read_catalog` answers), so there is one reader and no second parser. When `loaded` is not given, the project is loaded
    (`project.loaded()`) with the files it was read from (`raw`, {project file: its services as written}), which say what each
    unit builds (_builds); a project given without them is listed by its own `built`. Nothing in it changes between two runs
    or with what else is installed: no time, no commit, no digest. What a unit says it was tested with is not in it, and was
    not before."""
    from ..library import project   # here and not above: the project reads this package
    if loaded is None:
        loaded, read = project.loaded()
        raw = read.get("raw") if raw is None else raw
    if meta is None:
        meta = read_catalog(f"{paths.CATALOGS_DIR}/{ns}/{FILE}")
    units = []
    for kind in paths.KINDS:
        for uid, unit in sorted((loaded.get(f"{kind}s") or {}).items()):
            if paths.origin(uid) == ns:
                units.append(_index_unit(ns, kind, uid, unit, project, loaded.get("services"), raw))
    units.sort(key=lambda one: (one["kind"], one["id"]))
    found = {"schema": 1,
             "catalog": {key: meta.get(key) for key in ("id", "name", "description", "version", "lab", "license")},
             "units": units}
    level = _standard(ns)
    if level is not None:
        found["standard"] = level
    return found


def render_index(index: dict) -> str:
    """The text of an index: sorted keys, two-space indent, LF, a final newline. The same index is the same bytes."""
    return json.dumps(index, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
