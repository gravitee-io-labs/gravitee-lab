"""What a catalog says of itself and which of its versions this Lab can read: the `catalog.yml` that sits at its root, the
compatible pin that picks the newest commit this Lab reads, and the generated index.

A Lab uses what it understands: a version of a published catalog is read when every item of the contract it uses is one this
Lab lists (`understood`), which `pick` learns by reading that version's files (`readable`). `schema` and `lab` are the legacy
fields that older Gravitee Labs read: a catalog.yml does not need them, a `lab` that is there is a fast hint (a version
whose `lab` this Lab does not read is passed over without reading its files), and `with_legacy` keeps them true where
`catalog.py` writes the file.

`read_catalog` reads the file with Compose (`compose.raw`, no new parser), refuses what the table of 4.1 refuses and
answers the block cleaned for display: names cut and stripped of control characters, lists without repeats, every
optional key present. `validate_catalog` is the same table as a pure function over the mapping, which is how `catalog.py
check` and the template's tests use it. It does not judge a namespace against the reserved words or the catalogs the
person has (`registry.check_namespace`) nor a name against the address that serves it: the listed names are `borrowed`,
and the caller that knows the address says which.

`compat`, `pick`, `parse_version` and `at_least` are pure and tested over tables. `levels` is the git half of `pick`:
the versions of `catalog.yml` in the first-parent history of the tip, read from the bare store, newest first, and each version of
the units within each of them. `releases` is the other half, for a catalog that makes releases: its release tags that lead to the
tip and that its catalog.yml agrees with. `offer` says which of the two a catalog follows, and the commit it offers
([Catalogs](../../../docs/catalogs.md#versions)), each version read by its files (`readable`) before it is offered."""

import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Iterable, List, NamedTuple, Optional, Tuple

from ..docker import compose
from .. import paths
from . import gitx, registry, tree, understood
from . import store as bare
from .registry import CatalogError

SCHEMA = 1   # the format of catalog.yml and of the layout that this Lab reads
LEGACY = ("schema", "lab")   # what older Gravitee Labs need in catalog.yml, and this Lab does not
LEGACY_NOTE = "# For older Gravitee Labs: written by catalog.py"   # the comment of their lines in a file catalog.py writes
# The versions of a catalog whose files `pick` reads to find one this Lab understands, and the seconds it spends on them: a real
# history has its newest readable version among the first few, and a catalog this Lab cannot read is said once the budget is spent
READS, READ_SECONDS = 20, 10.0
UNITS = ("addons", "setups", "playgrounds")   # what holds the units: a version of them is a version of the catalog
BLOCK = "x-gravitee-catalog"
FILE = registry.CATALOG_FILE
# What a catalog.yml may weigh before it is not read: the store filter keeps the file under this, and a folder is looked
# at here
FILE_LIMIT = 1024 * 1024
# The keys of the block this Lab knows; any other is ignored by the Lab and reported by `catalog.py check`
KEYS = ("schema", "id", "name", "description", "version", "lab", "license", "images", "contribute", "shared_settings",
        "moved_from", "canary", "requires", "host_access")
REQUIRES_KEYS = ("catalogs",)   # the keys of `requires`
# The keys of the mappings of the block, as the contract lists them (x-gravitee-catalog.<key>.<part>)
PARTS = {"requires": REQUIRES_KEYS}
NEED_KEYS = ("name", "url", "min")   # the keys of a catalog of `requires.catalogs`
CONTRIBUTE = ("pull-request", "none")   # the values of `contribute`
_VALUE_SHAPE = re.compile(r"[a-z][a-z-]{0,30}")   # a word a newer Lab could give `contribute` or `host_access`
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


def pick(levels: list, contract: int, contract_min: int, readable=None, reads: Optional[int] = None,
         seconds: Optional[float] = None) -> tuple:
    """The compatible pin. `levels` is [(commit, lab)], newest first (`levels()`, or a catalog's releases): `levels[0]` is the
    tip with the `lab` its catalog.yml states. Answers (the first commit this Lab reads, or None; how the tip stands: "ok",
    "new" when it needs a newer Gravitee Lab, "old"). With nothing to choose from there is no commit and the tip is "old".
    `searched` gives the number of versions read as well, when the budget ran out before one was found.

    A `lab` is a fast hint: a version whose `lab` this Lab does not read (`compat`) is passed over, its files unread. Any other
    version is read when `readable` is given, `readable(commit)` saying whether this Lab understands every item of the contract
    that its files use (`understood`): what it does not is a version that needs a newer Gravitee Lab. At most `reads` versions
    are read (READS by default), and none starts once `seconds` (READ_SECONDS) have gone: a read that started goes on, so the
    budget is about that long; past it none is taken. Without `readable` (a caller that reads the version it stages) a
    `lab` of None, the form with no `lab` and the form the tolerant reader could not be sure of, is taken for a version this Lab
    may read, and the strict reading of the staged file decides. Pure but for `readable` and the clock."""
    commit, state, _ = searched(levels, contract, contract_min, readable, reads, seconds)
    return commit, state


def searched(levels: list, contract: int, contract_min: int, readable=None, reads: Optional[int] = None,
             seconds: Optional[float] = None) -> tuple:
    """`pick`, with a third answer: the number of versions read when the budget ran out with none found, else 0."""
    reads = READS if reads is None else reads
    seconds = READ_SECONDS if seconds is None else seconds
    if not levels:
        return None, "old", 0
    left, began, spent = [reads], time.monotonic(), [False]

    def fits(commit, lab):
        if lab is not None and compat(lab, contract, contract_min) != "ok":
            return False
        if readable is None:
            return True
        if left[0] <= 0 or time.monotonic() - began > seconds:
            spent[0] = True
            return False
        left[0] -= 1
        return bool(readable(commit))
    tip, lab = levels[0]
    if lab is not None and compat(lab, contract, contract_min) != "ok":
        tip_state = compat(lab, contract, contract_min)
    elif fits(tip, lab):
        return tip, "ok", 0
    else:
        tip_state = "new"
    for commit, lab in levels[1:]:
        if fits(commit, lab):
            return commit, tip_state, 0
    return None, tip_state, (reads - left[0]) if spent[0] else 0


NEWER = "This catalog needs a newer Gravitee Lab. Run make update, then add it again."


def compat_error(level, contract: int, contract_min: int, listed: bool = False) -> Optional[CatalogError]:
    """The refusal of the catalogue of errors for a catalog whose legacy `lab` is `level` (`lab_new`, `lab_old`), or None when
    this Lab reads it, or when it states none. `listed` words `lab_old` for a catalog the engine lists, where "ask its owners"
    is wrong."""
    if level is None:
        return None
    state = compat(level, contract, contract_min)
    if state == "new":
        return CatalogError("lab_new", NEWER, 400)
    if state == "old" and listed:
        return CatalogError("lab_old", "The catalog is not ready for this version of the Lab. Try again later.", 400)
    if state == "old":
        return CatalogError("lab_old", "Written for an older Gravitee Lab. Ask its owners to update it.", 400)
    return None


def unread(meta: dict) -> list:
    """What the catalog.yml of a published catalog, as `read_catalog(published=True)` gives it, holds that this Lab does not
    read: its keys and its values, as items of `understood`."""
    return [("catalog.key", key) for key in meta.get("unknown") or ()] + \
        [("catalog.value", value) for value in meta.get("unknown_values") or ()]


def not_understood(found: Iterable) -> CatalogError:
    """The refusal of a version of a published catalog that uses what this Lab does not read: it needs a newer Gravitee Lab.
    `found` are the items (`understood.unknown`), named in the detail and not in the line."""
    return CatalogError("lab_new", NEWER, 400, unknown=[understood.name(item) for item in found])


def no_commit(levels: list, contract: int, contract_min: int, listed: bool = False, searched: int = 0) -> CatalogError:
    """The refusal when `pick` found no commit (the one rule): `no_readable_version` when the tip needs a newer Lab, by its
    legacy `lab` or by what its files use (so does every version), `lab_old` when the catalog is older than this Lab reads,
    or there is nothing to choose from. `listed` words `lab_old` for a catalog the engine lists. `searched`: the versions read
    when the budget of `pick` ran out, so that the line says what was looked at and no more."""
    if searched:
        return CatalogError("no_readable_version", f"This Lab found no version of this catalog it reads among the newest {searched}. "
                            "Fix: update Gravitee Lab (make update), then add it again.", 400)
    level = levels[0][1] if levels else 0
    if level is None or compat(level, contract, contract_min) != "old":   # None: its files need a newer Lab (`pick`)
        return CatalogError("no_readable_version",
                            "Every version of this catalog needs a newer Gravitee Lab. Run make update, then add it again.", 400)
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
    """The checks of the fields every catalog.yml has (id, name, description, version) and of the legacy fields when they are
    there (schema, lab): older Gravitee Labs read them, this Lab does not need them."""
    if "schema" in meta and (not _whole(meta["schema"]) or meta["schema"] < 1):
        problems.append("schema must be a whole number, such as 1")
    elif "schema" in meta and meta["schema"] > SCHEMA:
        problems.append("schema says the catalog needs a newer Gravitee Lab")
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
    if "lab" in meta and (not _whole(meta["lab"]) or meta["lab"] < 0):
        problems.append("lab must be a whole number, such as 1: catalog.py upgrade --write writes it")


def _optional(meta, problems, lenient=False):
    """The checks of the fields a catalog.yml may leave out. `lenient`: a value of a newer Lab (`unknown_values`) is not a
    problem here, and the caller says the catalog needs a newer Gravitee Lab."""
    odd = set(unknown_values(meta)) if lenient else set()
    if meta.get("license") is not None and not (isinstance(meta["license"], str) and len(meta["license"]) <= 64
                                                and _LICENSE.fullmatch(meta["license"])):
        problems.append("license must be an SPDX identifier, such as Apache-2.0")
    if meta.get("images") is not None and not (isinstance(meta["images"], str) and len(meta["images"]) <= 200
                                               and _IMAGES.fullmatch(meta["images"])):
        problems.append("images must be a registry prefix without a tag, such as ghcr.io/acme/lab-hub")
    if meta.get("contribute") is not None and meta["contribute"] not in CONTRIBUTE and f"contribute={meta['contribute']}" not in odd:
        problems.append("contribute must be pull-request or none")
    if meta.get("shared_settings") is not None and _names(meta["shared_settings"], _SETTING, SETTINGS_LIMIT) is None:
        problems.append("shared_settings must be a list of setting names in capitals, such as GEMINI_API_KEY")
    if meta.get("host_access") is not None and not isinstance(meta["host_access"], bool) \
            and f"host_access={meta['host_access']}" not in odd:
        problems.append("host_access must be true or false")
    for key in ("moved_from", "canary"):
        value = meta.get(key)
        if isinstance(value, list) and odd:
            value = [one for one in value if f"{key}={one}" not in odd]
        if value is not None and _names(value, _UNIT, LIST_LIMIT) is None:
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


def validate_catalog(meta: dict, borrowed: Iterable[str] = (), lenient: bool = False) -> List[str]:
    """The problems of the mapping under `x-gravitee-catalog`, each a short phrase that follows "catalog.yml: " (the
    table of 4.1, in its order); [] for a valid one. `borrowed`: the names of the listed catalogs that this one is not,
    so a name that is one of them is refused as a name that is not its own. Pure. A key this Lab does not know is not a
    problem (`unknown_keys`), and neither is a namespace that is reserved or taken: the registry says that. `lenient`, for a
    published catalog: a value of a newer Lab (`unknown_values`) is not a problem either, it needs a newer Gravitee Lab."""
    if not isinstance(meta, dict):
        return [f"{BLOCK} must be a mapping"]
    problems = []
    _required(meta, problems)
    shown = registry.shown(meta["name"], NAME_LIMIT).lower() if _text(meta.get("name")) else ""
    taken = {registry.shown(one, NAME_LIMIT).lower() for one in borrowed if isinstance(one, str)}
    if shown and (shown in RESERVED_NAMES or shown in taken):
        problems.append(f"name cannot be {registry.shown(meta['name'], NAME_LIMIT)}")
    _optional(meta, problems, lenient)
    _required_catalogs(meta.get("requires"), problems)
    return problems


def unknown_keys(meta: dict) -> List[str]:
    """The keys of the block that this Lab does not read, sorted, those of `requires` and of each catalog it requires by their
    path (`requires.gpu`, `requires.catalogs.hub.mirror`): `catalog.py check` reports them, and a published catalog that has
    one needs a newer Gravitee Lab (`service`, `install.plan_update`)."""
    if not isinstance(meta, dict):
        return []
    found = [str(key) for key in meta if key not in KEYS]
    requires = meta.get("requires") if isinstance(meta.get("requires"), dict) else {}
    found += [f"requires.{key}" for key in requires if key not in REQUIRES_KEYS]
    declared = requires.get("catalogs") if isinstance(requires.get("catalogs"), dict) else {}
    found += [f"requires.catalogs.{ns}.{key}" for ns, want in declared.items() if isinstance(want, dict)
              for key in want if key not in NEED_KEYS]
    return sorted(found)


def unknown_values(meta: dict) -> List[str]:
    """The values of the block that a newer Lab could give and this one does not read, as `key=value`, sorted: a word for
    `contribute` or `host_access` that is not one of theirs, an item of `canary` or `moved_from` of a kind this Lab has no
    word for (`template:tour`). What is no value of any Lab (a number for `contribute`, a sentence) stays a problem of the
    file (`validate_catalog`)."""
    if not isinstance(meta, dict):
        return []
    found = []
    for key, known in (("contribute", CONTRIBUTE), ("host_access", ())):
        value = meta.get(key)
        if isinstance(value, str) and value not in known and _VALUE_SHAPE.fullmatch(value):
            found.append(f"{key}={value}")
    for key in ("canary", "moved_from"):
        for one in meta.get(key) if isinstance(meta.get(key), list) else ():
            kind, _, name = one.partition(":") if isinstance(one, str) else ("", "", "")
            if kind not in paths.KINDS and _VALUE_SHAPE.fullmatch(kind) and re.fullmatch(r"[a-z0-9-]+", name):
                found.append(f"{key}={one}")
    return sorted(set(found))


def _kinds_known(value) -> list:
    """The items of a list of kind:name of the kinds this Lab knows: those of a newer one are the caller's to say."""
    return [one for one in value if isinstance(one, str) and _UNIT.fullmatch(one)] if isinstance(value, list) else []


def normalize(meta: dict) -> dict:
    """A valid block (`validate_catalog` answered nothing) as the Lab keeps it: names and descriptions for a person
    (normalized, control characters out, white space collapsed, cut), lists without repeats, every optional key present.
    {schema, id, name, description, version, lab, license, images, contribute, shared_settings, moved_from, canary,
    host_access, requires: {catalogs: {ns: {name, url, min}}}}, `schema` and `lab` None when the file does not state them."""
    declared = (meta.get("requires") or {}).get("catalogs") or {}
    return {
        "schema": meta.get("schema"), "id": meta["id"], "name": registry.shown(meta["name"], NAME_LIMIT),
        "description": registry.shown(meta["description"], DESCRIPTION_LIMIT), "version": meta["version"],
        "lab": meta.get("lab"), "license": meta.get("license"), "images": meta.get("images"),
        "contribute": meta["contribute"] if meta.get("contribute") in CONTRIBUTE else "pull-request",
        "shared_settings": _names(meta.get("shared_settings") or [], _SETTING, SETTINGS_LIMIT),
        "moved_from": _names(_kinds_known(meta.get("moved_from")), _UNIT, LIST_LIMIT),
        "canary": _names(_kinds_known(meta.get("canary")), _UNIT, LIST_LIMIT),
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


def read_catalog(path: str, address: Optional[gitx.Address] = None, borrowed: Iterable[str] = (), published: bool = False) -> dict:
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
        raise CatalogError("schema_new", NEWER, 400)
    problems = validate_catalog(block, borrowed, lenient=published)
    if problems:
        raise CatalogError("catalog_invalid", f"catalog.yml: {problems[0]}.", 400, problems=problems)
    return {**normalize(block), "unknown": unknown_keys(block), "unknown_values": unknown_values(block) if published else []}


_LEGACY_LINE = re.compile(r"(?P<head>[ \t]+[\"']?(?P<key>schema|lab)[\"']?[ \t]*:[ \t]*)(?P<value>[^#\r]*?)"
                          r"(?P<tail>[ \t]*(?:#.*)?\r?)")
_BLOCK_LINE = re.compile(r"[\"']?x-gravitee-catalog[\"']?:[ \t]*(?:#.*)?\r?")


def with_legacy(text: str, lab: Optional[int]) -> str:
    """The text of a catalog.yml with the fields that older Gravitee Labs read made true: `schema: 1`, and `lab: <lab>`
    when `lab` is given (the true minimum, `check.needed_lab`), else the `lab` it has, or 1. A line that is there keeps its
    place and its comment, and only its value changes; a line that is missing is added in the block, with the comment that
    says who writes it (LEGACY_NOTE): `schema` first, `lab` after `version`. Everything else stays as it was, line ends too.
    A text with no block in the usual shape is answered as it is: the check says what is wrong with it."""
    lines = text.split("\n")
    start = next((at for at, line in enumerate(lines) if _BLOCK_LINE.fullmatch(line)), None)
    if start is None:
        return text
    end, indent, found = len(lines), None, {}
    for at in range(start + 1, len(lines)):
        line = lines[at]
        if not line.strip(" \t\r") or line.lstrip(" \t").startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            end = at
            break
        here = len(line) - len(line.lstrip(" \t"))
        indent = here if indent is None else min(indent, here)
        match = _LEGACY_LINE.fullmatch(line)
        if match and here == indent and match.group("key") not in found:
            found[match.group("key")] = (at, match)
    if indent is None:
        return text
    have = found.get("lab")
    if lab is None:
        written = have[1].group("value").strip().strip("\"'") if have else ""
        lab = int(written) if written.isdigit() else 1
    wanted = {"schema": SCHEMA, "lab": lab}
    for key, (at, match) in found.items():
        lines[at] = f"{match.group('head')}{wanted[key]}{match.group('tail')}"
    pad = " " * indent
    end_of_line = "\r" if lines[start].endswith("\r") else ""   # a file written with CRLF stays so
    if "lab" not in found:
        after = next((at for at in range(end - 1, start, -1) if re.match(rf"{pad}[\"']?version[\"']?[ \t]*:", lines[at])), None)
        place = after + 1 if after is not None else end
        while place > start + 1 and place == end and not lines[place - 1].strip():
            place -= 1   # before the blank lines that end the block
        lines.insert(place, f"{pad}lab: {lab}   {LEGACY_NOTE}{end_of_line}")
    if "schema" not in found:
        lines.insert(start + 1, f"{pad}schema: {SCHEMA}   {LEGACY_NOTE}{end_of_line}")
    return "\n".join(lines)


# ---------------------------------------------------------------- the versions of a catalog

def _lab_of(data) -> Optional[int]:
    """The `lab` a catalog.yml states, from its bytes, read the way the registry reads the file of an install; 0 (a
    level no Lab reads) for a file that is missing or damaged, or whose `lab` is no number. None when it states none, the
    form a catalog has now, and when the reader says it is not sure (`partial`: an anchor, a scalar on several lines, a
    number spelled another way, a block it did not find): the files of that version say whether this Lab reads it (`pick`
    with `readable`), and the strict reading of a catalog (`read_catalog`, at Add and at Update) has the last word, so a
    level is never invented here."""
    found = registry.scan(data) if data else {}
    if found.get("partial"):
        return None
    if type(found.get("lab")) is int:
        return found["lab"]
    return None if found and not _states_lab(data) else 0


def _states_lab(data) -> bool:
    """Whether the x-gravitee-catalog block of a catalog.yml has a `lab` line of its own, at its first level, whatever it says:
    one that is no number is no level. A `lab` deeper in the block (under `requires`) or in another block is not the catalog's."""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data or "")
    inside, indent = False, None
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line.strip() or line.lstrip(" \t").startswith("#"):
            continue
        if line[0] not in " \t":
            inside = bool(_BLOCK_LINE.fullmatch(line))
            indent = None
            continue
        if inside:
            here = len(line) - len(line.lstrip(" \t"))
            indent = here if indent is None else indent
            if here == indent and re.match(r"[\"']?lab[\"']?[ \t]*:", line.lstrip(" \t")):
                return True
    return False


def levels(store: Path, tip: str, limit: int = 50, address: Optional[gitx.Address] = None) -> list:
    """The versions of this catalog that `pick` chooses among: [(commit, lab)], newest first, at most `limit`.
    `levels[0]` is `tip` with the `lab` its `catalog.yml` states; for each older form of that file there is one more,
    the newest commit that still has it, which is the first parent of the commit that changed the file next. Since it is
    the files that say whether this Lab reads a version, each form is also a version for each change of its units: the
    first parent of each commit that changed `addons/`, `setups/` or `playgrounds/` since the form appeared, with the `lab`
    of the form, which `pick` reads as a hint. One `git log --first-parent` for the commits that touched the file, one for
    the changes of the units of each form, and one read of the file for each form. A form that states no `lab` is None, one
    that is damaged is `lab` 0, which no Lab reads, one the reader cannot be sure of is None (`_lab_of`), and a form that was
    deleted is left out. A tip with no `catalog.yml` is `not_catalog` (`address` names the repository in the line); a tip
    the store does not hold is `fsck`."""
    commit = bare.resolve(store, tip)
    if commit is None:
        raise bare.MissingObject(tip)
    here = bare.read(store, commit, FILE)
    if here is None:
        raise gitx.fail("not_catalog", address)
    cache = {here: _lab_of(here)}
    touching = bare.changes(store, commit, [FILE], limit)
    forms = [(commit, cache[here], touching[0][0] if touching else None)]
    for (newer, parent), (older, _) in zip(touching, touching[1:]):
        if parent is None:
            break
        data = bare.read(store, older, FILE)
        if data is None:   # the file was deleted at `older`: the commits between hold no catalog
            continue
        if data not in cache:
            cache[data] = _lab_of(data)
        forms.append((parent, cache[data], older))
    found = []
    for newest, lab, since in forms:
        found.append((newest, lab))
        if since is not None and len(found) < limit:
            found += [(parent, lab) for _, parent in bare.changes(store, newest, UNITS, limit, since=since)
                      if parent is not None and parent != newest]
        if len(found) >= limit:
            break
    return found[:limit]


def releases(store: Path, tip: str) -> list:
    """The releases of this catalog that lead to `tip`: [(commit, lab, version)], the greatest version first. A release is a
    tag `vX.Y.Z` (`store.release_tags`) whose commit has a catalog.yml that says `version: X.Y.Z`: a tag the file does not
    agree with is a name and not a release, and is left out. `lab` is read as `levels` reads it (`_lab_of`)."""
    found, cache = [], {}
    for _, name, commit in bare.release_tags(store, tip):
        data = cache.get(commit)
        if data is None:
            data = cache[commit] = bare.read(store, commit, FILE) or b""
        if data and registry.scan(data).get("version") == name[1:]:
            found.append((commit, _lab_of(data), name[1:]))
    return found


class Offer(NamedTuple):
    """What a catalog offers this Lab: `commit`, the version to install (None when it has none this Lab reads); `tip`, how
    the newest version stands with this Lab (`pick`'s second answer: "ok", "new" or "old"); `release`, the version of the
    release `commit` is, or None for a catalog that has no release, which is followed commit by commit; `levels`, what
    `no_commit` reads when there is no commit; `searched`, the versions read when the budget ran out with none found."""
    commit: Optional[str]
    tip: str
    release: Optional[str]
    levels: list
    searched: int = 0


def offer(store: Path, tip: str, contract: int, contract_min: int, address: Optional[gitx.Address] = None,
          readable=None) -> Offer:
    """The version of a catalog this Lab installs, from what the store holds of the branch it follows (`tip`). A catalog
    with a release on that branch is offered its greatest release this Lab reads, and the commits made since are not offered
    until they are released; one with none is offered the newest commit this Lab reads (`levels`), as it always was. A tip
    with no catalog.yml is `not_catalog`, released or not. `readable` is what `pick` asks of each version, released or not:
    whether this Lab reads all that its files use (`install.readable`, which every caller of a published catalog gives)."""
    found = releases(store, tip)
    if not found:
        history = levels(store, tip, address=address)
        commit, state, looked = searched(history, contract, contract_min, readable)
        return Offer(commit, state, None, history, looked)
    if bare.read(store, tip, FILE) is None:
        raise gitx.fail("not_catalog", address)
    history = [(commit, lab) for commit, lab, _ in found]   # a catalog that makes releases is offered its releases, and only them
    commit, state, looked = searched(history, contract, contract_min, readable)
    version = next((version for one, _, version in found if one == commit), None)
    return Offer(commit, state, version, history, looked)


def release_of(store: Path, commit: str, version: Optional[str]) -> Optional[str]:
    """`version` when the store's release tag of it names `commit`, else None: whether a commit, whose catalog.yml says
    `version`, is that release."""
    return version if version and commit and bare.release_commit(store, version) == commit else None


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
    """The generated `catalog.json` of catalog `ns`: {schema, catalog: {id, name, description, version, lab (when its
    catalog.yml states one), license}, standard (the level of the standard the catalog follows, from its `.github/standard.json`; the key is there only
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
             "catalog": {key: meta.get(key) for key in ("id", "name", "description", "version", "lab", "license")
                         if key != "lab" or meta.get("lab") is not None},
             "units": units}
    level = _standard(ns)
    if level is not None:
        found["standard"] = level
    return found


def render_index(index: dict) -> str:
    """The text of an index: sorted keys, two-space indent, LF, a final newline. The same index is the same bytes."""
    return json.dumps(index, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
