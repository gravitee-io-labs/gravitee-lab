"""The catalog repository standard: the template that `catalog.py new` renders, the levels it
ships by, the drift of a repository from it (SD01 to SD05, SD08 to SD10), and the upgrade that brings a repository back.

    LEVEL                         the level this Lab ships: the highest whose CI stages are all here (`manifest.json`)
    manifest() -> dict            the template's table of files and level notes
    render_new(params) -> dict    {path: bytes} of a new catalog at LEVEL
    normalize(text) -> str        the form two managed files are compared in: a Dependabot bump is no difference
    drift(tree) -> list           SD01 to SD05 and SD08 to SD10 of a repository, at the Lab's level
    upgrade_plan(tree, options)   what an upgrade would do; upgrade_apply(root, plan) does it and answers what it wrote
    main_new(argv), main_upgrade(argv)   the commands (`catalog.py new`, `catalog.py upgrade`), exit codes 0, 1 and 2

The template is `lab-ui/templates/catalog/`: `managed/` holds the machine-owned files as the last level ships them (real,
valid workflows with real pins), `seeded/` the files that are rendered once and are then the catalog's own, `licenses/` the
texts, and `manifest.json` says which file arrives with which level.
The managed files carry no placeholder and no per-catalog value: what differs between catalogs is in `catalog.yml` and in
`.github/standard.json`. A seeded file has `__PLACEHOLDERS__`, replaced by exact text in one pass.

To ship a level: change the managed files, raise `level` in `manifest.json`, add its note, and refresh `golden.json` with
`python3 -m tests.catalogs.test_standard --write` (from `lab-ui`); the golden test fails a change of the content without that. A level
adds files or only changes them, and a change to a managed file always raises it: a catalog below the Lab's level whose file differs is
behind (SD03, upgrade), one at it was edited by hand (SD02). A level
puts its number in the marker of the files it renders, but the number is not compared: a file that no level changed is current
at every level, and `upgrade` rewrites only the files whose content changed.

Nothing here runs what it reads. A repository is read through a `Tree`, never opened by a path of its own; it is written by
`upgrade_apply` only, through a temporary file and `os.replace`, never through a link, and a failed run puts back what it
wrote. Standard library only."""

import copy
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

from .. import paths
from . import defaults, gitx
from .report import ERROR, INFO, WARNING, Problem, printable
from .rules import catalog_meta, say, table
from .rules.codeowners import OwnerError, maintainers as parse_maintainers, parse as parse_owners
from .tree import FolderTree, TreeError

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "catalog"
STANDARD = ".github/standard.json"
STANDARD_LIMIT = 4096                  # the size SD04 allows `.github/standard.json`
TEXT_LIMIT = 1024 * 1024               # the most of a file that is read to compare it
PLATFORMS = ("linux/amd64", "linux/arm64")
MODULES = ("e2e",)
LICENSES = {"agpl-3.0-only": "AGPL-3.0-only", "apache-2.0": "Apache-2.0", "mit": "MIT"}
SHOWN = 80                             # characters of a stranger's word that a message shows
LOG_LINES = 50                         # the lines of the Lab's own log that a moved pin brings

MARKER = re.compile(r"# Managed by the Lab catalog standard (\d{1,3}): change it with catalog\.py upgrade, in a pull request a "
                    r"maintainer reads\.")
JSON_MARKER = re.compile(r'\s{0,8}"_managed":\s{0,4}"Managed by the Lab catalog standard (\d{1,3}): change it with catalog\.py upgrade, '
                         r'in a pull request a maintainer reads\.",?')
PLACEHOLDERS = ("CATALOG_ID", "CATALOG_ENV", "CATALOG_NAME", "DESCRIPTION", "LICENSE", "IMAGES", "MAINTAINERS", "SECURITY_CONTACT",
                "LAB_REPOSITORY", "LAB_REF")
PLACEHOLDER = re.compile("__(" + "|".join(PLACEHOLDERS) + ")__")

# The fixed parameters of the golden hashes of the seeded files: nothing here is a real name or address
GOLDEN_PARAMS = {"id": "acme-hub", "name": "Acme Hub", "description": "Demos for workshops", "license": "Apache-2.0",
                 "images": "ghcr.io/acme/lab-hub", "maintainers": "@acme/maintainers", "lab_repository": "acme/gravitee-lab",
                 "lab_ref": "0123456789abcdef0123456789abcdef01234567", "with": ["e2e"]}

# The rules of the standard itself. SD07 and SD11 are raised where the Lab is fetched (the `lab` action, `check --online`)
RULES = table([
    ("SD01", ERROR, "A managed file is missing", "{path} is missing from the shared files. Fix: catalog.py upgrade --write."),
    ("SD02", ERROR, "A managed file differs from the Lab's render at the repository's own level",
     "{path} was edited by hand. Fix: git checkout -- {path}. A change to a shared file is read by a maintainer."),
    ("SD03", WARNING, "A managed file is behind: the repository's level is below the Lab's",
     "{path} is from standard {have}; this Lab has {now}. Fix: catalog.py upgrade --write."),
    ("SD04", ERROR, "standard.json is unreadable, has a bad value, or is over 4 KB (a warning for an unknown key)",
     ".github/standard.json: {problem}."),
    ("SD05", WARNING, "The repository's level is above the Lab's",
     ".github/standard.json says standard {n}; this Lab knows {m}. Fix: update the Lab with git pull, then run again."),
    ("SD07", ERROR, "The Lab could not be fetched (CI)",
     "The Lab could not be fetched. This is the repository's settings, not your change: a maintainer will run it again. Fix "
     "(maintainers): while the Lab is private this repository needs the secret LAB_DEPLOY_KEY (also as a Dependabot secret), "
     "and .github/standard.json must name a commit that exists."),
    ("SD08", INFO, "A managed path has no marker: the catalog owns it",
     "{path} is owned by this catalog; upgrade skips it. WF01 to WF21 still apply."),
    ("SD09", WARNING, "The catalog owns dependabot.yml and it lacks github-actions or a cooldown",
     ".github/dependabot.yml has no {ecosystem}. Fix: add it."),
    ("SD10", ERROR, "The Makefile does not include .catalog/standard.mk",
     "Makefile does not include .catalog/standard.mk. Fix: add the line: include .catalog/standard.mk"),
    ("SD11", ERROR, "lab.ref is not the commit of a release of the Lab (a tag vX.Y.Z on its main) (CI)",
     "The Lab commit {sha7} is not a release of the Lab. Fix: pin the latest release (catalog.py upgrade --lab-ref latest)."),
])
GOLDEN_MESSAGE = "The template changed. Raise level in manifest.json, add a note under levels, and refresh golden.json."


class TemplateError(Exception):
    """The template, or what `new` was given, cannot be rendered: one line for the person. `missing` names the parameters
    that were not given (3.4: a missing parameter stops `new` with the list of what is missing)."""

    def __init__(self, message: str, missing: Iterable[str] = ()):
        super().__init__(message)
        self.missing = list(missing)


# ---------------------------------------------------------------------------------------------------------------------
# The template: manifest, levels, files
# ---------------------------------------------------------------------------------------------------------------------

def _clean(path) -> bool:
    return isinstance(path, str) and bool(path) and all(part not in ("", ".", "..") for part in path.split("/")) \
        and "\\" not in path and "\0" not in path and not path.startswith("/")


def manifest() -> dict:
    """The template's `manifest.json` as a dict: `level`; `files`, each {path, class, since, when, scope} (and `local` for the
    source a folder catalog uses, `tool`, `license`); `notes`, one for each level."""
    return json.loads((TEMPLATE / "manifest.json").read_text(encoding="utf-8"))


LEVEL = manifest()["level"]


def entries(local: bool = False) -> List[dict]:
    """The files of the manifest in its order, for a full catalog or a folder catalog."""
    return [each for each in manifest()["files"] if each["scope"] in (("local", "both") if local else ("full", "both"))]


def managed_paths() -> Tuple[str, ...]:
    """Every path a Lab upgrade may write: the managed files of every level and `.github/standard.json` (the fleet checks an
    upgrade against this)."""
    return tuple(sorted({each["path"] for each in manifest()["files"] if each["class"] == "managed"} | {STANDARD}))


def managed_roots() -> Tuple[str, ...]:
    """The folders the managed files and `standard.json` are in, each ending in a slash: where an upgrade may change anything
    (the fleet stops a repository whose upgrade changed a file outside them). Derived from the template, so a level that puts a
    managed file in a new folder widens it without an edit here."""
    return tuple(sorted({path.rpartition("/")[0] + "/" for path in managed_paths() if "/" in path}))


def _source(folder: str, relative: str) -> str:
    """The text of a file of the template, as UTF-8."""
    try:
        return (TEMPLATE / folder / relative).read_text(encoding="utf-8")
    except (OSError, ValueError):
        raise TemplateError(f"The template file {folder}/{relative} cannot be read.") from None


# ---------------------------------------------------------------------------------------------------------------------
# Comparing: normalize, markers, pins
# ---------------------------------------------------------------------------------------------------------------------

# The comment after a pin is what Dependabot writes, a tag: printable ASCII and tabs. Nothing else is part of it, so that no character
# that a YAML reader takes for a line break (U+0085, U+2028, U+2029) hides a line after a `#`
_USES = re.compile(r"([A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}(?:/[A-Za-z0-9_./-]{1,200})?)@([0-9a-f]{40})(?:[ \t]+(#[\t\x20-\x7e]*))?")
_USES_LIMIT = 600


def _uses(line: str) -> Optional[Tuple[str, str, str, str]]:
    """(the line up to its `uses:`, the action, the commit, the comment) of a line `uses: owner/repo[/path]@<40 hex>` with an
    optional `# comment`, or None. A list dash before `uses:` is part of the head."""
    body = line.lstrip(" ")
    head = line[:len(line) - len(body)]
    if body.startswith("- "):
        rest = body[2:].lstrip(" ")
        head, body = head + body[:len(body) - len(rest)], rest
    if not body.startswith("uses:") or len(body) > _USES_LIMIT:
        return None
    found = _USES.fullmatch(body[5:].strip(" "))
    return (head, found.group(1), found.group(2), found.group(3) or "") if found else None


def normalize(text: str) -> str:
    """The form in which two copies of a managed file are compared: LF line ends, no trailing blanks, exactly one final
    newline, and in every line `uses: owner/repo[/path]@<40 hex>` with an optional comment the commit and the comment are
    `<pin>`. A Dependabot bump (a commit and a version comment) is no difference; a swapped action is, for its name stays."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    for line in lines:
        line = line.rstrip(" \t")
        found = _uses(line)
        out.append(f"{found[0]}uses: {found[1]}@<pin>" if found else line)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def comparable(text: str) -> str:
    """`normalize(text)` with the number of the marker taken out: the form in which a managed file is compared with the Lab's.
    The number says the level that last wrote the file, so a file that no level changed is the same file at every level, and an
    upgrade leaves it alone."""
    lines = normalize(text).split("\n")
    for index in range(min(4, len(lines))):   # a text file's marker is its first line, a JSON file's is a key near the top
        if (index == 0 and MARKER.fullmatch(lines[0])) or JSON_MARKER.fullmatch(lines[index]):
            lines[index] = re.sub(r"standard \d{1,3}:", "standard N:", lines[index], count=1)
            break
    return "\n".join(lines)


def pins(text: str) -> Dict[str, Tuple[str, str]]:
    """{action: (commit, comment)} of the `uses:` lines of a text, the first one for each action."""
    held: Dict[str, Tuple[str, str]] = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        found = _uses(line.rstrip(" \t"))
        if found:
            held.setdefault(found[1], (found[2], found[3]))
    return held


def carry_pins(rendered: str, current: str) -> str:
    """The rendered text with the pins that `current` already holds for the same actions: a commit that Dependabot bumped is
    the catalog's, and an upgrade keeps it."""
    held = pins(current)
    out = []
    for line in rendered.split("\n"):
        found = _uses(line)
        if found and found[1] in held:
            commit, comment = held[found[1]]
            line = f"{found[0]}uses: {found[1]}@{commit}" + (f" {comment}" if comment else "")
        out.append(line)
    return "\n".join(out)


def marker_level(text: str, path: str) -> Optional[int]:
    """The level a managed file says it was written at (its marker), or None when it has no marker: the catalog owns it. A byte
    order mark before the marker is no way to take the file out of the standard: the file has its marker, and differs from the
    Lab's by that character, which `comparable` keeps."""
    lines = text.removeprefix("\ufeff").split("\n", 4)
    if path.endswith(".json"):
        for line in lines[:4]:
            found = JSON_MARKER.fullmatch(line.rstrip(" \t\r"))
            if found:
                return int(found.group(1))
        return None
    found = MARKER.fullmatch(lines[0].rstrip(" \t\r")) if lines else None
    return int(found.group(1)) if found else None


# ---------------------------------------------------------------------------------------------------------------------
# What `new` is given
# ---------------------------------------------------------------------------------------------------------------------

_LICENSE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}")
_IMAGES = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?(?::[0-9]{1,5})?(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*){1,10}")
_REPOSITORY = re.compile(r"[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_ID = re.compile(paths.NS)
_BAD_CATEGORIES = ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")


def _listed() -> List[str]:
    """The ids the Lab's default file lists: a word the Lab keeps is a catalog id for the catalog it names there."""
    return [one["id"] for one in defaults.read()]


def _name_taken(name: str, ident: str) -> bool:
    """Whether the Lab refuses this name for the catalog `ident`, as `check` does: a name that reads as a seal the Lab never
    gives (`Official`), or the name of a catalog the Lab lists that this one is not. Case and look-alikes do not matter."""
    from . import manifest, registry   # here and not above: the template is rendered without them
    shown = registry.shown(name, manifest.NAME_LIMIT).lower()
    listed = {registry.shown(one["name"], manifest.NAME_LIMIT).lower() for one in defaults.read() if one["id"] != ident}
    return shown in manifest.RESERVED_NAMES or shown in listed


def lab_repository_ok(value) -> bool:
    return isinstance(value, str) and _REPOSITORY.fullmatch(value) is not None and ".." not in value


def _words(value, limit: int, what: str) -> str:
    """A name or a sentence for a file: one line, NFKC, no control or invisible character, none of < > ` \\ (a name is shown in
    Markdown and written in YAML), at most `limit` characters."""
    if not isinstance(value, str):
        raise TemplateError(f"{what} is text.")
    if len(value) > 8 * limit:
        raise TemplateError(f"{what} is at most {limit} characters.")
    text = unicodedata.normalize("NFKC", value)
    if any(unicodedata.category(char) in _BAD_CATEGORIES or char in "<>`\\" for char in text):
        raise TemplateError(f"{what} holds a character that cannot be written in a file: use letters, digits and punctuation, "
                            "without < > ` or a backslash.")
    text = " ".join(text.split())
    if not text:
        raise TemplateError(f"{what} is empty.", [what])
    if len(text) > limit:
        raise TemplateError(f"{what} is at most {limit} characters.")
    return text


def values(params: dict, local: Optional[bool] = None) -> dict:
    """The parameters of `new`, checked and in the form the template wants: every value the placeholders need, `local`, `with`,
    `platforms`, `maintainers` (a list), `lab_repository` and `lab_ref`. A TemplateError says what is wrong in one
    line, and what is missing in `missing`."""
    params = dict(params or {})
    local = bool(params.get("local")) if local is None else local
    need = ("id", "name") if local else ("id", "name", "maintainers", "lab_repository", "lab_ref")
    missing = [key for key in need if not params.get(key)]
    if missing:
        raise TemplateError("Missing: " + ", ".join(missing) + ".", missing)
    out: Dict[str, object] = {"local": local}
    ident = params["id"]
    if not isinstance(ident, str) or not _ID.fullmatch(ident) or (ident in paths.RESERVED_NS and ident not in _listed()):
        raise TemplateError(f"{printable(str(ident))[:SHOWN]} is not a catalog id: 2 to 24 letters, digits and dashes, the first a "
                            "letter, and not a word the Lab keeps.")
    out["id"] = ident
    out["env"] = ident.upper().replace("-", "_")
    out["name"] = _words(params["name"], 40, "The name")
    if _name_taken(out["name"], ident):
        raise TemplateError(f"{printable(out['name'])[:SHOWN]} cannot be the name of a catalog: the Lab keeps it for itself or for "
                            "a catalog it lists. Fix: choose another name with --name.")
    out["description"] = _words(params.get("description") or "Demos for Gravitee Lab", 90, "The description")
    licence = params.get("license") or "UNLICENSED"
    if not isinstance(licence, str) or len(licence) > 64 or not _LICENSE.fullmatch(licence):
        raise TemplateError(f"{printable(str(licence))[:SHOWN]} is not a license identifier: use an SPDX id such as Apache-2.0.")
    out["license"] = LICENSES.get(licence.lower(), licence)
    images = params.get("images") or ""
    if images and (not isinstance(images, str) or len(images) > 300 or not _IMAGES.fullmatch(images)):
        raise TemplateError(f"{printable(str(images))[:SHOWN]} is not a registry prefix: write host/path, in lower case, with no "
                            "tag, for example ghcr.io/acme/lab-hub.")
    out["images"] = images
    extra = params.get("with") or ()
    if isinstance(extra, str):
        extra = [extra]
    for word in extra:
        if word not in MODULES:
            raise TemplateError(f"--with takes {', '.join(MODULES)}, not {printable(str(word))[:SHOWN]}.")
    out["with"] = tuple(dict.fromkeys(extra))
    if local and out["with"]:
        raise TemplateError("A folder catalog has no workflows: --local takes no --with.")
    platforms = params.get("platforms") or ["linux/amd64"]
    if isinstance(platforms, str):
        platforms = [part for part in platforms.split(",") if part]
    if not platforms or any(each not in PLATFORMS for each in platforms) or len(set(platforms)) != len(platforms):
        raise TemplateError("--platforms is linux/amd64, linux/arm64 or both.")
    out["platforms"] = list(platforms)
    if local:
        out.update({"maintainers": [], "lab_repository": "", "lab_ref": "", "security_contact": ""})
        return out
    owners = params["maintainers"]
    try:
        out["maintainers"] = parse_maintainers(owners) if isinstance(owners, str) else parse_maintainers(",".join(owners))
    except OwnerError as error:
        raise TemplateError(str(error)) from None
    if not lab_repository_ok(params["lab_repository"]):
        raise TemplateError("The Lab address is OWNER/NAME, with letters, digits and . _ - only. Give --lab-repo OWNER/REPO.")
    out["lab_repository"] = params["lab_repository"]
    if not isinstance(params["lab_ref"], str) or not _COMMIT.fullmatch(params["lab_ref"]):
        raise TemplateError("--lab-ref takes the full commit of the Lab: 40 hexadecimal characters.")
    out["lab_ref"] = params["lab_ref"]
    contact = params.get("security_contact")
    out["security_contact"] = _words(contact, 120, "The security contact") if contact else out["maintainers"][0]
    return out


def _escape(text: str, path: str) -> str:
    """A value inside a double-quoted YAML scalar: a double quote is escaped."""
    return text.replace('"', '\\"') if path.endswith((".yml", ".yaml")) else text


def fill(text: str, given: dict, path: str) -> str:
    """The seeded text with each placeholder replaced by exact text, in one pass (a value that looks like a placeholder is
    not read again). A line with `__IMAGES__` is left out when the catalog names no registry."""
    if not given["images"]:
        text = "\n".join(line for line in text.split("\n") if "__IMAGES__" not in line)
    mapping = {"CATALOG_ID": given["id"], "CATALOG_ENV": given["env"], "CATALOG_NAME": given["name"],
               "DESCRIPTION": given["description"], "LICENSE": given["license"], "IMAGES": given["images"],
               "MAINTAINERS": " ".join(given["maintainers"]), "SECURITY_CONTACT": given["security_contact"],
               "LAB_REPOSITORY": given["lab_repository"], "LAB_REF": given["lab_ref"]}
    return PLACEHOLDER.sub(lambda found: _escape(str(mapping[found.group(1)]), path), text)


def license_text(given: dict) -> str:
    """The text for LICENSE: the standard text for AGPL-3.0-only, Apache-2.0 and MIT, else the stub that says to add it."""
    name = given["license"] if given["license"] in LICENSES.values() else "other"
    return fill(_source("licenses", f"{name}.txt"), given, "LICENSE")


# ---------------------------------------------------------------------------------------------------------------------
# standard.json
# ---------------------------------------------------------------------------------------------------------------------

def _dump(value, indent: int = 0) -> str:
    """JSON with sorted keys, two-space indent and a list of scalars on one line, as 3.2 shows the file."""
    pad = "  " * (indent + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        rows = [f"{pad}{json.dumps(key)}: {_dump(value[key], indent + 1)}" for key in sorted(value)]
        return "{\n" + ",\n".join(rows) + "\n" + "  " * indent + "}"
    if isinstance(value, list):
        if all(not isinstance(each, (dict, list)) for each in value):
            return "[" + ", ".join(json.dumps(each) for each in value) + "]"
        return "[\n" + ",\n".join(pad + _dump(each, indent + 1) for each in value) + "\n" + "  " * indent + "]"
    return json.dumps(value)


def dump_standard(data: dict) -> str:
    """`.github/standard.json` as text: sorted keys, 2-space indent, LF, one final newline."""
    return _dump(data) + "\n"


def standard_for(given: dict) -> dict:
    return {"form": "inline", "lab": {"ref": given["lab_ref"], "repository": given["lab_repository"]},
            "platforms": list(given["platforms"]), "standard": LEVEL}


def _no_twins(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"{printable(key)[:SHOWN]} is written twice")
        out[key] = value
    return out


def _constant(name):
    raise ValueError("it holds a number that is not one")


def parse_standard(raw: bytes) -> Tuple[Optional[dict], List[Tuple[str, str]]]:
    """(the data, [(severity, problem)]) of the bytes of `.github/standard.json`. The problems are the SD04 phrases; data is
    None when the file cannot be used at all (not JSON, not an object, over 4 KB)."""
    if len(raw) > STANDARD_LIMIT:
        return None, [(ERROR, "the file is over 4 KB")]
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_twins, parse_constant=_constant)
    except RecursionError:
        return None, [(ERROR, "the file is nested too deep to be read")]
    except UnicodeDecodeError:
        return None, [(ERROR, "the file is not UTF-8 text")]
    except ValueError as error:
        message = str(error)
        if "written twice" in message or "not one" in message:
            return None, [(ERROR, message)]
        return None, [(ERROR, "the file is not JSON")]
    if not isinstance(data, dict):
        return None, [(ERROR, "the file is not a JSON object")]
    found: List[Tuple[str, str]] = []
    level = data.get("standard")
    if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 999:
        found.append((ERROR, "standard is a whole number from 1: the level of the managed files in this repository"))
    if data.get("form") != "inline":
        found.append((ERROR, "form is inline"))
    lab = data.get("lab")
    if not isinstance(lab, dict):
        found.append((ERROR, "lab is an object with repository and ref"))
    else:
        if not lab_repository_ok(lab.get("repository")):
            found.append((ERROR, "lab.repository must be OWNER/NAME of the Lab, letters, digits and . _ - only"))
        if not isinstance(lab.get("ref"), str) or not _COMMIT.fullmatch(lab["ref"]):
            found.append((ERROR, "lab.ref must be a full commit of the Lab, 40 hexadecimal characters"))
        for key in sorted(set(lab) - {"repository", "ref"}, key=str)[:5]:
            found.append((WARNING, f"lab.{printable(str(key))[:40]} is not a key of the standard"))
    platforms = data.get("platforms")
    if not isinstance(platforms, list) or not platforms or any(each not in PLATFORMS for each in platforms) \
            or len(set(map(str, platforms))) != len(platforms):
        found.append((ERROR, "platforms is a list of linux/amd64, linux/arm64 or both"))
    if "strict" in data:
        strict = data["strict"]
        if not isinstance(strict, list) or len(strict) > 50 or any(not isinstance(each, str) or not re.fullmatch(
                r"[A-Z]{2,3}[0-9]{2}", each) for each in strict):
            found.append((ERROR, "strict is a list of rule codes such as DF22"))
    if "needs" in data:
        needs = data["needs"]
        if not isinstance(needs, dict) or len(needs) > 10 or any(
                not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,38}", name) or not isinstance(what, dict)
                or not lab_repository_ok(what.get("repository")) or not isinstance(what.get("ref"), str)
                or not _COMMIT.fullmatch(what["ref"]) for name, what in needs.items()):
            found.append((ERROR, "needs is a map of catalog ids to {repository: OWNER/NAME, ref: a full commit}"))
    for key in sorted(set(data) - {"standard", "form", "lab", "platforms", "strict", "needs"}, key=str)[:5]:
        found.append((WARNING, f"{printable(str(key))[:40]} is not a key of the standard"))
    return data, found


class Declared(NamedTuple):
    """What `.github/standard.json` says: `present` whether the file is in the tree, `data` its content as far as it can be
    used (None when it cannot), `problems` the SD04 phrases, `level` the declared level when it is a good one."""
    present: bool
    data: Optional[dict]
    problems: List[Tuple[str, str]]
    level: Optional[int]


def declared(tree) -> Declared:
    entry = tree.entry(STANDARD)
    if entry is None:
        return Declared(False, None, [], None)
    raw = tree.read(STANDARD, STANDARD_LIMIT + 1) if entry.mode in ("100644", "100755") else None
    if raw is None:
        return Declared(True, None, [(ERROR, "it is not a file that can be read")], None)
    data, problems = parse_standard(raw)
    level = data.get("standard") if data and isinstance(data.get("standard"), int) and not isinstance(data.get("standard"), bool) \
        and 1 <= data["standard"] <= 999 else None
    return Declared(True, data, problems, level)


# ---------------------------------------------------------------------------------------------------------------------
# Rendering a new catalog
# ---------------------------------------------------------------------------------------------------------------------

def wanted(entry: dict, given: dict) -> bool:
    """Whether a file of the manifest belongs to a catalog with these parameters."""
    if entry["when"] == "images":
        return bool(given["images"])
    if entry["when"] == "module:e2e":
        return "e2e" in given["with"]
    return True


def render_new(params: dict) -> Dict[str, bytes]:
    """{path: bytes} of a new catalog at the Lab's level: the managed files verbatim, the seeded files with their placeholders
    replaced, `.github/standard.json`, the two CODEOWNERS lines and the sample add-on. A folder catalog (`local`) gets the
    files of 3.1 for one. Deterministic: the same parameters give the same bytes. A TemplateError says what is wrong."""
    given = values(params)
    local = given["local"]
    found: Dict[str, bytes] = {}
    for entry in entries(local):
        if not wanted(entry, given):
            continue
        path, kind = entry["path"], entry["class"]
        if kind == "managed":
            text = _source("managed", path)
        elif entry.get("tool") == "standard.json":
            text = dump_standard(standard_for(given))
        elif entry.get("license"):
            text = license_text(given)
        else:
            source = entry["local"] if local and entry.get("local") else entry.get("source") or path
            text = fill(_source("seeded", source), given, path)
        found[path] = text.encode("utf-8")
    return found


def _is_empty(folder: Path) -> bool:
    try:
        return not any(folder.iterdir())
    except OSError:
        return False


def create(root, params: dict) -> List[str]:
    """Renders a new catalog into `root`, which does not exist or is empty, and answers the paths written. What was made is
    taken away again when a write fails."""
    files = render_new(params)
    root = Path(root)
    if os.path.lexists(root) and (not root.is_dir() or not _is_empty(root)):
        raise TemplateError(f"{printable(str(root))[:200]} is not empty. Choose a new folder.")
    made_folders: List[Path] = []   # the folders this call makes, outermost first
    done: List[Path] = []
    try:
        for path, data in files.items():
            target = root.joinpath(*path.split("/"))
            missing = []
            for folder in [target.parent, *target.parent.parents]:
                if folder.exists():
                    break
                missing.append(folder)
            for folder in reversed(missing):
                folder.mkdir()
                made_folders.append(folder)
            paths.write_atomic(target, data.decode("utf-8"))
            done.append(target)
        return sorted(files)
    except BaseException:
        for target in reversed(done):
            try:
                target.unlink()
            except OSError:
                pass
        for folder in reversed(made_folders):
            try:
                folder.rmdir()
            except OSError:
                pass
        raise


# ---------------------------------------------------------------------------------------------------------------------
# Drift: SD01 to SD05 and SD08 to SD10
# ---------------------------------------------------------------------------------------------------------------------

def _file(tree, path: str) -> Tuple[str, Optional[str]]:
    """('missing', None), ('odd', None) for something that is not a file that can be read, or ('file', the text)."""
    entry = tree.entry(path)
    if entry is None:
        return "missing", None
    if entry.mode not in ("100644", "100755") or entry.size > TEXT_LIMIT:
        return "odd", None
    data = tree.read(path, TEXT_LIMIT)
    return ("file", data.decode("utf-8", "replace")) if data is not None else ("odd", None)


def _images_set(tree) -> bool:
    data = tree.read("catalog.yml", TEXT_LIMIT)
    return bool(catalog_meta(data.decode("utf-8", "replace")).get("images")) if data is not None else False


def _issue(code: str, path: str, line: int = 0, **fields) -> Problem:
    rule = RULES[code]
    return Problem(code, rule.severity, path, line, say(rule.message, path=path, **fields))


def drift(tree) -> List[Problem]:
    """SD01 to SD05 and SD08 to SD10 of the repository in `tree`, against the Lab's render: the files are compared
    as `comparable` makes them, so a Dependabot bump of a pin and the number of the marker are no difference. Pure: it reads the tree
    and writes nothing."""
    found: List[Problem] = []
    state = declared(tree)
    for severity, problem in state.problems:
        rule = RULES["SD04"]
        found.append(Problem("SD04", severity, STANDARD, 0, say(rule.message, problem=problem)))
    have = state.level
    if have is not None and have > LEVEL:
        found.append(_issue("SD05", STANDARD, n=have, m=LEVEL))
        return found + _owned_checks(tree)
    asked = have if have is not None else LEVEL
    for entry in entries():
        if entry["class"] != "managed":
            continue
        path = entry["path"]
        kind, text = _file(tree, path)
        if entry["when"] == "images" and not _images_set(tree) and kind == "missing":
            continue
        if entry["when"] == "module:e2e" and kind == "missing":
            continue
        if kind == "missing":
            found.append(_issue("SD03", path, have=asked, now=LEVEL) if asked < entry["since"] else _issue("SD01", path))
            continue
        if kind == "odd":
            found.append(_issue("SD02", path))
            continue
        if marker_level(text, path) is None:
            found.append(_issue("SD08", path))
            continue
        if comparable(text) == comparable(_source("managed", path)):
            continue
        if asked >= LEVEL:
            found.append(_issue("SD02", path))
        else:
            found.append(_issue("SD03", path, have=asked, now=LEVEL))
    return found + _owned_checks(tree)


_ECOSYSTEM = re.compile(r"^[ \t]*(?:-[ \t]+)?package-ecosystem:", re.M)
_ACTIONS = re.compile(r"[ \t]*[\"']?github-actions[\"']?[ \t]*(?:#.*)?(?:\n|$)")
_COOLDOWN = re.compile(r"^[ \t]*cooldown:", re.M)   # a key of a line of its own, not a word in a comment
_INCLUDE = re.compile(r"^-?include[ \t]+(?:\./)?\.catalog/standard\.mk[ \t]*(?:#.*)?$", re.M)


def _owned_checks(tree) -> List[Problem]:
    """SD09 for a Dependabot file the catalog owns and SD10 for a Makefile that leaves the standard's targets out."""
    found: List[Problem] = []
    kind, text = _file(tree, ".github/dependabot.yml")
    if kind == "file" and marker_level(text, ".github/dependabot.yml") is None:
        starts = [found_.end() for found_ in _ECOSYSTEM.finditer(text)]
        blocks = [text[start:(starts[i + 1] if i + 1 < len(starts) else len(text))] for i, start in enumerate(starts)]
        mine = [block for block in blocks if _ACTIONS.match(block)]
        if not mine:
            found.append(_issue("SD09", ".github/dependabot.yml", ecosystem="github-actions"))
        elif any(not _COOLDOWN.search(block) for block in mine):
            found.append(_issue("SD09", ".github/dependabot.yml", ecosystem="cooldown"))
    kind, text = _file(tree, "Makefile")
    if kind != "file" or not _INCLUDE.search(text.replace("\r\n", "\n")):
        found.append(_issue("SD10", "Makefile"))
    return found


# ---------------------------------------------------------------------------------------------------------------------
# Golden hashes of the template
# ---------------------------------------------------------------------------------------------------------------------

def _sha(text) -> str:
    return hashlib.sha256(text if isinstance(text, bytes) else text.encode("utf-8")).hexdigest()


def golden_now() -> dict:
    """The hashes of the template as it is: the sha256 of every managed file (normalized) and of every seeded file rendered with
    the fixed parameters, as the full catalog and as the folder catalog."""
    rendered = render_new(GOLDEN_PARAMS)
    known = {each["path"]: each for each in manifest()["files"]}
    local = render_new({"id": "acme-hub", "name": "Acme Hub", "local": True})
    return {"level": LEVEL,
            "managed": {path: _sha(normalize(data.decode("utf-8"))) for path, data in sorted(rendered.items())
                        if known[path]["class"] == "managed"},
            "seeded": {path: _sha(data) for path, data in sorted(rendered.items()) if known[path]["class"] != "managed"},
            "local": {path: _sha(data) for path, data in sorted(local.items())}}


def golden_file() -> Path:
    return TEMPLATE / "golden.json"


def _differences(before, now) -> List[str]:
    """The paths whose hash is not the recorded one, in either direction, sorted."""
    before, now = before if isinstance(before, dict) else {}, now if isinstance(now, dict) else {}
    return sorted(path for path in set(before) | set(now) if before.get(path) != now.get(path))


def golden_problems() -> List[str]:
    """The sentences that say the template changed without a bump: empty when `golden.json` is what the template renders, else
    the message of 3.4 as it is, followed by the files that differ, indented."""
    try:
        recorded = json.loads(golden_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [GOLDEN_MESSAGE, "  golden.json cannot be read"]
    if not isinstance(recorded, dict):
        return [GOLDEN_MESSAGE, "  golden.json is not an object"]
    now = golden_now()
    found: List[str] = []
    if recorded.get("level") != now["level"]:
        found.append(f"  golden.json is for level {recorded.get('level')}, the template says {now['level']}")
    found.extend(f"  {path}" for kind in ("managed", "seeded") for path in _differences(recorded.get(kind), now[kind])[:20])
    found.extend(f"  folder catalog: {path}" for path in _differences(recorded.get("local"), now["local"])[:20])
    return [GOLDEN_MESSAGE, *found] if found else []


def write_golden() -> Path:
    """Refreshes `golden.json` from the template: what a maintainer runs after raising the level."""
    target = golden_file()
    target.write_text(json.dumps(golden_now(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------------------------------------------------------------
# Upgrade: the plan and the writing
# ---------------------------------------------------------------------------------------------------------------------

ADD, UPDATE, CURRENT, OWNED = "add", "update", "current", "owned"


def _entry(path: str, action: str, kind: str, why: str = "", content: Optional[bytes] = None) -> dict:
    return {"path": path, "action": action, "class": kind, "why": why, "content": content}


RELEASE_TAG = re.compile(r"v(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})")   # a release of the Lab


def _lab_git(lab):
    """What runs git in the Lab checkout `lab`: its Result, None when it cannot be asked."""
    def run(args):
        try:
            return gitx.run(args, cwd=str(lab), timeout=30)
        except (gitx.CatalogError, OSError):
            return None
    return run


def release_state(lab, ref: str, git=None) -> str:
    """SD11 of a pin, as the Lab checkout `lab` knows it with no fetch: "yes" when `ref` is the commit of a release of the Lab, a
    tag vX.Y.Z on its `origin/main`; "no" when it is not; else why the checkout cannot say. A catalog is checked with a Lab that
    people have: a commit of main that no release names is one nobody runs, and a commit of a fork is not the Lab's. `git` (args ->
    a Result or None) is the seam of a caller that runs git its own way."""
    run = git or _lab_git(lab)
    have = run(["cat-file", "-e", f"{ref}^{{commit}}"])
    if have is None:
        return "the Lab checkout cannot be asked"
    if have.code != 0:
        return f"the Lab checkout does not hold {ref[:7]}: update it with git pull, then run again"
    main = run(["rev-parse", "--verify", "--quiet", "origin/main"])
    if main is None or main.code != 0:
        return "the Lab checkout has no origin/main to compare with"
    on_main = run(["merge-base", "--is-ancestor", ref, "origin/main"])
    tags = run(["tag", "--points-at", ref])
    named = tags is not None and tags.code == 0 and any(RELEASE_TAG.fullmatch(line.strip())
                                                         for line in tags.out.decode("utf-8", "replace").splitlines())
    return "yes" if on_main is not None and on_main.code == 0 and named else "no"


MANIFEST_IN_LAB = "lab-ui/templates/catalog/manifest.json"   # where a Lab says the level of the standard it ships
NOT_RELEASED = "Standard {n} is not in a released Gravitee Lab yet. Fix: release the Lab first, then run this again."


def release_level(lab, ref: str, git=None) -> Optional[int]:
    """The level of the catalog standard that the Lab at `ref` ships (`level` of its manifest.json, 0 when it ships none), as the
    Lab checkout `lab` holds it; None when the checkout does not hold `ref`. A catalog written at a level is checked by the Lab
    it pins, which must know that level: its workflows call what that level added."""
    run = git or _lab_git(lab)
    have = run(["cat-file", "-e", f"{ref}^{{commit}}"])
    if have is None or have.code != 0:
        return None
    shown = run(["show", f"{ref}:{MANIFEST_IN_LAB}"])
    try:
        level = json.loads(shown.out.decode("utf-8")).get("level") if shown is not None and shown.code == 0 else 0
    except (ValueError, AttributeError):
        level = 0
    return level if type(level) is int and level >= 0 else 0


def pin_problem(lab, ref: str, level: int, git=None) -> str:
    """Why the Lab `ref` cannot be the pin of a catalog written at standard `level`, or "": the release it is ships an older
    standard. When no release of the Lab ships `level` yet, the line says so: the Lab is released before a catalog moves to its
    standard."""
    have = release_level(lab, ref, git)
    if have is None or have >= level:
        return ""
    newest = latest_release(lab, git)
    if newest and newest != ref and (release_level(lab, newest, git) or 0) >= level:
        return (f"The Lab release {ref[:7]} has standard {have}; this writes standard {level}. Fix: pin the latest release "
                "(catalog.py upgrade --lab-ref latest).")
    return NOT_RELEASED.format(n=level)


def _is_release(lab, ref: str) -> bool:
    """Whether `ref` is a release of the Lab checkout `lab` (SD11, `release_state`)."""
    return release_state(lab, ref) == "yes"


def latest_release(lab, git=None) -> str:
    """The commit of the latest release of the Lab, the greatest tag vX.Y.Z on the `origin/main` of the Lab checkout `lab`, as
    that checkout knows it with no fetch; '' when it has none. What `--lab-ref latest` pins, and what `new` pins by default.
    `git` is the seam of a caller that runs git its own way (the fleet)."""
    run = git or _lab_git(lab)
    listed = run(["tag", "--list", "v[0-9]*.[0-9]*.[0-9]*", "--merged", "origin/main"])
    if listed is None or listed.code != 0:
        return ""
    names = [line.strip() for line in listed.out.decode("utf-8", "replace").splitlines() if RELEASE_TAG.fullmatch(line.strip())]
    if not names:
        return ""
    newest = max(names, key=lambda name: tuple(int(part) for part in RELEASE_TAG.fullmatch(name).groups()))
    found = run(["rev-parse", "--verify", "--quiet", f"refs/tags/{newest}^{{commit}}"])
    commit = found.out.decode("utf-8", "replace").strip() if found is not None and found.code == 0 else ""
    return commit if _COMMIT.fullmatch(commit) else ""


def lab_log(lab, old: str, new: str) -> List[str]:
    """The Lab's own log of `lab-ui` and `.github` between two pins: at most LOG_LINES lines, each cut and made safe for a
    terminal. Empty when the Lab checkout does not hold both commits."""
    try:
        result = gitx.run(["log", "--oneline", "--no-decorate", "-n", str(LOG_LINES), f"{old}..{new}", "--", "lab-ui", ".github"],
                          cwd=str(lab), timeout=30)
    except (gitx.CatalogError, OSError):
        return []
    if result.code != 0:
        return []
    lines = [printable(gitx.redact(line))[:200].strip() for line in result.out.decode("utf-8", "replace").splitlines()]
    return [line for line in lines if line][:LOG_LINES]


def _derived(tree, state: Declared, options: dict) -> dict:
    """The parameters `--adopt` renders the missing seeded files with, read from the repository: `catalog.yml` for the id, the
    name, the description, the license and the registry; the `*` line of CODEOWNERS (or `--maintainers`) for the owners; the
    Lab from `standard.json` (or the options)."""
    data = tree.read("catalog.yml", TEXT_LIMIT)
    meta = catalog_meta(data.decode("utf-8", "replace")) if data is not None else {}
    owners = options.get("maintainers")
    if not owners:
        raw = tree.read(".github/CODEOWNERS", TEXT_LIMIT)
        if raw is not None:
            for line in parse_owners(raw.decode("utf-8", "replace")):
                if line.pattern == "*" and line.owners:
                    owners = ",".join(line.owners)
    lab = (state.data or {}).get("lab") if state.data else None
    lab = lab if isinstance(lab, dict) else {}
    return {"id": meta.get("id"), "name": meta.get("name"), "description": meta.get("description"), "license": meta.get("license"),
            "images": meta.get("images"), "maintainers": owners,
            "lab_repository": options.get("lab_repository") or lab.get("repository"),
            "lab_ref": options.get("lab_ref") or lab.get("ref"),
            "platforms": (state.data or {}).get("platforms") if state.data else None, "with": options.get("with") or (),
            "security_contact": options.get("security_contact")}


def upgrade_plan(tree, options: Optional[dict] = None) -> dict:
    """What an upgrade of the repository in `tree` would do, as a dict:

        level       the Lab's level          have        the repository's (None when it has no standard.json)
        files       [{path, action, class, why, content}], action one of add, update, current, owned
        owned       the paths the catalog owns (no marker): skipped, listed
        notes       [{level, summary, manual, settings}] for the levels the repository has not reached
        lab         {repository, old, new, compare, log}: the Lab pin, and what moving it brings
        refused     "" or why nothing can be done (the plan has no files then)
        pending     whether anything would be written

    `options`: lab_ref (40 hex: the new pin), adopt, with (modules), maintainers / lab_repository / security_contact (for what adopt adds),
    lab (the Lab checkout: SD11 and the log of a moved pin are asked of it), legacy_lab (when the key is there, catalog.yml gets
    the lines older Gravitee Labs read, made true: `schema`, and `lab` as given, or as the file has it when None). Pure:
    it reads the tree and runs `git log` in the Lab checkout; it writes nothing."""
    options = dict(options or {})
    adopt = bool(options.get("adopt"))
    state = declared(tree)
    plan: dict = {"level": LEVEL, "have": state.level, "files": [], "owned": [], "notes": [], "refused": "", "pending": False,
                  "lab": {"repository": "", "old": "", "new": "", "compare": "", "log": []}, "name": ""}

    def refuse(why: str) -> dict:
        plan["refused"] = why
        plan["files"], plan["pending"] = [], False
        return plan

    meta_raw = tree.read("catalog.yml", TEXT_LIMIT)
    if meta_raw is None:
        return refuse("catalog.yml is missing: this folder is not a catalog.")
    meta = catalog_meta(meta_raw.decode("utf-8", "replace"))
    plan["name"] = printable(meta.get("name", ""))[:60]
    if state.present and (state.data is None or any(severity == ERROR for severity, _ in state.problems)):
        problem = state.problems[0][1] if state.problems else "it cannot be read"
        return refuse(f".github/standard.json is not usable ({problem}). Fix: correct it, or restore it with git checkout.")
    if not state.present and not adopt:
        return refuse("There is no .github/standard.json: this repository was not made from the template. "
                      "Fix: catalog.py upgrade --adopt.")
    if state.level is not None and state.level > LEVEL:
        return refuse(say(RULES["SD05"].message, n=state.level, m=LEVEL))
    ref_new = options.get("lab_ref") or ""
    if ref_new and not _COMMIT.fullmatch(ref_new):
        return refuse("--lab-ref takes the full commit of the Lab: 40 hexadecimal characters.")
    lab_checkout = options.get("lab")
    if ref_new and lab_checkout is not None and not _is_release(lab_checkout, ref_new):
        return refuse(say(RULES["SD11"].message, sha7=ref_new[:7]))
    old = (state.data or {}).get("lab", {}) if state.data else {}
    pinned = ref_new or (old.get("ref") if isinstance(old.get("ref"), str) else "")
    if lab_checkout is not None and _COMMIT.fullmatch(pinned or ""):
        problem = pin_problem(lab_checkout, pinned, LEVEL)
        if problem:
            return refuse(problem)
    repo = options.get("lab_repository") or old.get("repository") or ""
    ref = ref_new or old.get("ref") or ""
    if not lab_repository_ok(repo) or not _COMMIT.fullmatch(ref):
        return refuse("The Lab address could not be read. Give --lab-repo OWNER/REPO" + (" and --lab-ref COMMIT." if not ref else "."))
    extra = tuple(options.get("with") or ())
    if any(word not in MODULES for word in extra):
        return refuse(f"--with takes {', '.join(MODULES)}.")
    images = bool(meta.get("images"))
    data = manifest()
    # A module the catalog has, by any of its files: a file the new standard adds to that module comes with the upgrade
    extra = tuple(dict.fromkeys((*extra, *(entry["when"][len("module:"):] for entry in data["files"]
                                           if entry["when"].startswith("module:") and _file(tree, entry["path"])[0] != "missing"))))
    files: List[dict] = []
    for entry in data["files"]:
        if entry["class"] != "managed":
            continue
        path = entry["path"]
        kind, text = _file(tree, path)
        if entry["when"] == "images" and not images and kind == "missing":
            continue
        if entry["when"] == "module:e2e" and kind == "missing" and "e2e" not in extra:
            continue
        want = _source("managed", path)
        if kind == "missing":
            files.append(_entry(path, ADD, "managed", "missing", want.encode("utf-8")))
        elif kind == "odd":
            files.append(_entry(path, UPDATE, "managed", "it is not a file", want.encode("utf-8")))
        elif marker_level(text, path) is None:
            plan["owned"].append(path)
            files.append(_entry(path, OWNED, "managed", "the catalog removed the marker"))
        elif comparable(text) == comparable(want):
            files.append(_entry(path, CURRENT, "managed"))
        else:
            files.append(_entry(path, UPDATE, "managed", "behind" if (state.level or LEVEL) < LEVEL else "differs",
                                carry_pins(want, text).encode("utf-8")))
    # standard.json: the level goes up, the pin moves when asked; every other key stays as the catalog wrote it
    new = copy.deepcopy(state.data) if state.data else {"platforms": list(options.get("platforms") or ["linux/amd64"])}
    new.update({"standard": LEVEL, "form": "inline"})
    new["lab"] = {**(new.get("lab") if isinstance(new.get("lab"), dict) else {}), "repository": repo, "ref": ref}
    if not state.present:
        files.append(_entry(STANDARD, ADD, "seeded", "missing", dump_standard(new).encode("utf-8")))
    elif new != state.data:
        files.append(_entry(STANDARD, UPDATE, "seeded", "level" if new.get("standard") != state.data.get("standard") else "pin",
                            dump_standard(new).encode("utf-8")))
    else:
        files.append(_entry(STANDARD, CURRENT, "seeded"))
    if "legacy_lab" in options:   # what `catalog.py check` names as the fix of a `lab` that is not true
        from .manifest import with_legacy   # here and not above: the reader of catalog.yml, which nothing else here needs
        text = meta_raw.decode("utf-8", "replace")
        legacy = with_legacy(text, options["legacy_lab"])
        if legacy != text:
            files.append(_entry("catalog.yml", UPDATE, "seeded", "the lines older Gravitee Labs read",
                                legacy.encode("utf-8")))
    if adopt:
        problem = _adopt(tree, state, options, repo, ref, files, extra)
        if problem:
            return refuse(problem)
    plan["files"] = files
    plan["pending"] = any(each["action"] in (ADD, UPDATE) for each in files)
    plan["notes"] = [{"level": number, **data["notes"][str(number)]} for number in range((state.level or 0) + 1, LEVEL + 1)]
    moved = bool(ref_new) and old.get("ref") not in (None, ref_new)
    plan["lab"] = {"repository": repo, "old": old.get("ref") or "", "new": ref if moved else "", "compare": "", "log": []}
    if moved:
        plan["lab"]["compare"] = f"https://github.com/{repo}/compare/{old['ref']}...{ref}"
        if lab_checkout is not None:
            plan["lab"]["log"] = lab_log(lab_checkout, old["ref"], ref)
    return plan


def _adopt(tree, state: Declared, options: dict, repo: str, ref: str, files: List[dict], extra: tuple) -> str:
    """Adds the seeded files a repository made by hand lacks (never overwrites one): '' when it worked, else why not."""
    given = _derived(tree, state, {**options, "lab_repository": repo, "lab_ref": ref})
    given["with"] = list(extra)
    missing = [each["path"] for each in entries() if each["class"] == "seeded" and each["path"] not in (
        "catalog.yml", STANDARD) and tree.entry(each["path"]) is None]
    if not missing:
        return ""
    if not given.get("maintainers") and any(path in (".github/CODEOWNERS", "SECURITY.md") for path in missing):
        return ("--maintainers is required to add .github/CODEOWNERS: name the people or the team that own this catalog, for "
                "example --maintainers @acme/maintainers.")
    try:
        rendered = values({**given, "maintainers": given.get("maintainers") or ["@OWNER"]})
    except TemplateError as error:
        return f"catalog.yml cannot be used to add the missing files: {error}"
    for each in entries():
        if each["path"] in missing and each["class"] == "seeded":
            if each.get("license"):
                text = license_text(rendered)
            else:
                text = fill(_source("seeded", each.get("source") or each["path"]), rendered, each["path"])
            files.append(_entry(each["path"], ADD, "seeded", "missing", text.encode("utf-8")))
    return ""


class UpgradeError(Exception):
    """An upgrade that cannot be done or could not be finished: one line for the person."""


MAX_REWRITE = 8 * 1024 * 1024   # a managed file larger than this is not read back to be put back; it is not a managed file


def _inside(root: str, relative: str) -> str:
    """The path of `relative` below `root`, after checking that no folder on the way is a link, a file or the work tree of another
    repository (a write through a link would leave the repository, and one into a nested repository or a submodule would put the
    standard's files in that one). Folders that are missing are made. The last name may be a link: it is replaced."""
    if not _clean(relative):
        raise UpgradeError(f"{printable(relative)[:SHOWN]} is not a path inside the repository.")
    here = root
    parts = relative.split("/")
    for part in parts[:-1]:
        here = os.path.join(here, part)
        try:
            os.lstat(here)
        except FileNotFoundError:
            os.mkdir(here)
            continue
        if os.path.islink(here) or not os.path.isdir(here):
            raise UpgradeError(f"{printable(here[len(root) + 1:])[:SHOWN]} is a link or a file, not a folder: the upgrade does not "
                               "write through it.")
        if os.path.lexists(os.path.join(here, ".git")):
            raise UpgradeError(f"{printable(here[len(root) + 1:])[:SHOWN]} is the work tree of another repository (a nested "
                               "repository or a submodule): the upgrade does not write into it.")
    return os.path.join(here, parts[-1])


def _snapshot(target: str) -> tuple:
    """What is at a path now, so that it can be put back: ('none',), ('link', where it leads) or ('file', bytes, mode)."""
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        return ("none", target)
    if stat.S_ISLNK(info.st_mode):
        return ("link", target, os.readlink(target))
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_REWRITE:
        raise UpgradeError(f"{printable(os.path.basename(target))[:SHOWN]} is not a file the upgrade can rewrite.")
    return ("file", target, Path(target).read_bytes(), info.st_mode & 0o777)


def _write(target: Path, text: str, mode: int) -> None:
    paths.write_atomic(target, text, mode)


def _put_back(record: tuple) -> None:
    kind, target = record[0], record[1]
    if kind == "none":
        if os.path.lexists(target):
            os.unlink(target)
        return
    if kind == "link":
        if os.path.lexists(target):
            os.unlink(target)
        os.symlink(record[2], target)
        return
    handle, tmp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".restore.")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(record[2])
        os.chmod(tmp, record[3])
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def upgrade_apply(root, plan: dict) -> List[str]:
    """Writes a plan in `root` and answers the paths it wrote, sorted. Only the plan's own paths are touched, none
    through a link; each file is written through a temporary file and `os.replace`, keeps its execute bit, and is LF. When a
    write fails, what was written is put back and the error goes on. It refuses a folder that is a submodule or holds a
    repository of its own: the standard's files do not go into another repository."""
    if plan.get("refused"):
        raise UpgradeError(plan["refused"])
    base = os.path.realpath(str(root))
    modules = gitlinks(base, [each["path"] for each in plan["files"] if each["action"] in (ADD, UPDATE)])
    if modules:
        raise UpgradeError("The upgrade does not write into a submodule: " + ", ".join(modules) + ". Fix: make it a folder of this "
                           "repository, or take it out of the standard's paths.")
    done: List[tuple] = []
    out: List[str] = []
    try:
        for each in plan["files"]:
            if each["action"] not in (ADD, UPDATE):
                continue
            target = _inside(base, each["path"])
            before = _snapshot(target)
            mode = 0o755 if before[0] == "file" and before[3] & 0o111 else 0o644
            done.append(before)
            _write(Path(target), each["content"].decode("utf-8"), mode)
            out.append(each["path"])
    except BaseException:
        rollback(done)
        raise
    return sorted(out)


def rollback(done: list) -> None:
    """Puts back every file `upgrade_apply` wrote, last first."""
    for record in reversed(done):
        try:
            _put_back(record)
        except OSError:
            pass


# ---- git around an upgrade

def _asked(root, *args):
    """git for a question whose answer is short: a repository that makes git say more is one the upgrade does not touch."""
    try:
        return gitx.run(list(args), cwd=str(root), timeout=60, limit=GIT_LIMIT)
    except gitx.OutputLimit:
        raise UpgradeError("git said far more than the few files of the standard can account for: this repository is not one "
                           "the upgrade can judge.") from None


def _is_work_tree(root) -> bool:
    return os.path.lexists(os.path.join(str(root), ".git"))


GIT_LIMIT = 4 * 1024 * 1024   # the most that git may say about the few paths an upgrade asks about


def dirty_files(root, relative: Iterable[str]) -> List[str]:
    """Which of these paths have changes that are not committed (the upgrade refuses to rewrite them): a tracked file that
    was changed, and a file that git does not track yet or ignores, for there is no commit to take it back from. Empty for a
    folder that is not a git work tree."""
    relative = sorted(set(relative))
    if not relative or not _is_work_tree(root):
        return []
    result = _asked(root, "status", "--porcelain", "-z", "--untracked-files=all", "--ignored=matching", "--", *relative)
    if result.code != 0:
        raise UpgradeError("git could not say which files have changes: " + printable(result.err)[:200])
    found = []
    for record in result.out.split(b"\0"):
        if len(record) > 3:
            found.append(os.fsdecode(record[3:]))
    return sorted(found)


def gitlinks(root, relative: Iterable[str]) -> List[str]:
    """Which folders on the way to these paths are submodules (an entry of mode 160000 in the index, there is no repository in
    it when it was not initialised): the upgrade does not write into another repository. Empty for a folder that is not a work
    tree."""
    folders = sorted({"/".join(parts[:cut]) for parts in (path.split("/") for path in set(relative)) for cut in range(1, len(parts))})
    if not folders or not _is_work_tree(root):
        return []
    result = _asked(root, "ls-files", "--stage", "-z", "--", *(":(literal)" + folder for folder in folders))
    if result.code != 0:
        raise UpgradeError("git could not say which folders are submodules: " + printable(result.err)[:200])
    found = []
    for record in result.out.split(b"\0"):
        mode, _, rest = record.partition(b" ")
        name = os.fsdecode(rest.partition(b"\t")[2])
        if mode == b"160000" and name in folders:
            found.append(name)
    return sorted(found)


# ---------------------------------------------------------------------------------------------------------------------
# The commands
# ---------------------------------------------------------------------------------------------------------------------

class Usage(Exception):
    """The command cannot be run as asked (exit 2): the line says what to do."""


def _parser(prog: str):
    import argparse

    class Parser(argparse.ArgumentParser):
        def error(self, message):   # one line and exit 2, where argparse prints its usage and exits on its own
            raise Usage(f"{prog}: {message}")
    return Parser(prog=prog, add_help=False)


def _say(text: str = "", out=None) -> None:
    print(printable(text, keep=("\n",)), file=out or sys.stdout, flush=True)


def _complain(text: str) -> None:
    print(printable(text, keep=("\n",)), file=sys.stderr, flush=True)


def _lab_checkout() -> Path:
    return Path(paths.ROOT)


def _lab_head(lab: Path) -> str:
    """The commit the Lab checkout is at, or ''."""
    try:
        result = gitx.run(["rev-parse", "HEAD"], cwd=str(lab), timeout=20)
    except (gitx.CatalogError, OSError):
        return ""
    found = result.out.decode("utf-8", "replace").strip() if result.code == 0 else ""
    return found if _COMMIT.fullmatch(found) else ""


def _lab_origin(lab: Path) -> str:
    """OWNER/NAME of the `origin` of the Lab checkout, or ''."""
    try:
        result = gitx.run(["remote", "get-url", "origin"], cwd=str(lab), timeout=20)
        if result.code != 0:
            return ""
        return gitx.parse_address(result.out.decode("utf-8", "replace").strip()).repo or ""
    except (gitx.CatalogError, OSError, ValueError):
        return ""


def _new_arguments(argv: List[str]):
    parser = _parser("catalog.py new")
    parser.add_argument("dir")
    parser.add_argument("--id")
    parser.add_argument("--name")
    parser.add_argument("--maintainers")
    parser.add_argument("--license", default="UNLICENSED")
    parser.add_argument("--images")
    parser.add_argument("--description")
    parser.add_argument("--security-contact", dest="security_contact")
    parser.add_argument("--lab-repo", dest="lab_repo")
    parser.add_argument("--lab-ref", dest="lab_ref")
    parser.add_argument("--platforms")
    parser.add_argument("--with", dest="with_", action="append", default=[])
    parser.add_argument("--local", action="store_true")
    return parser.parse_args(argv)


def _next_steps(args, folder: str, base: str) -> List[str]:
    """The numbered steps that `new` prints after it created a catalog."""
    if args.local:
        return [f"  1. cd {folder} && make dev     open it in the Lab"]
    width = len("make settings APPLY=1 YES=1") + 2
    return [f'  1. cd {folder} && git init -b main && git add . && git commit -m "Create the catalog"',
            f"  2. gh repo create OWNER/{base} --private --source . --push",
            f"  3. {'make settings':<{width}}see what GitHub still has to set",
            f"     {'make settings APPLY=1':<{width}}show the requests that set it",
            f"     {'make settings APPLY=1 YES=1':<{width}}make them (needs admin rights)",
            "  4. Give the repository its key to the Lab: make settings prints the commands",
            f"  5. {'make dev':<{width}}open it in the Lab"]


def main_new(argv: List[str], lab: Optional[Path] = None) -> int:
    """`catalog.py new DIR --id ID --name NAME --maintainers @A[,@B or @ORG/TEAM] [--license SPDX] [--images PREFIX]
    [--lab-repo OWNER/REPO] [--lab-ref COMMIT] [--with e2e] [--local]`: renders the
    template into an empty DIR. Exit 0, 2 for what is missing or wrong (nothing is rendered)."""
    try:
        args = _new_arguments(list(argv))
        for flag, value in (("--id", args.id), ("--name", args.name)):
            if not value:
                raise Usage(f"catalog.py new: {flag} is required")
        lab = lab or _lab_checkout()
        repository, ref, warning = args.lab_repo or "", args.lab_ref or "", ""
        if not args.local:
            if not args.maintainers:
                raise Usage("--maintainers is required: name the people or the team that own this catalog, for example "
                            "--maintainers @acme/maintainers.")
            repository = repository or _lab_origin(lab)
            if not repository:
                raise Usage("The Lab address could not be read. Give --lab-repo OWNER/REPO.")
            if not ref:
                ref = latest_release(lab)
                if not ref:
                    ref = _lab_head(lab)
                    warning = (f"The Lab checkout has no release, so CI refuses its commit {ref[:7]} (SD11). Fetch its tags "
                               "(git fetch --tags), then run catalog.py upgrade --lab-ref latest --write.")
                elif pin_problem(lab, ref, LEVEL):
                    raise Usage(pin_problem(lab, ref, LEVEL))
        params = {"id": args.id, "name": args.name, "maintainers": args.maintainers, "license": args.license, "images": args.images,
                  "description": args.description, "security_contact": args.security_contact, "lab_repository": repository,
                  "lab_ref": ref, "platforms": args.platforms, "with": args.with_, "local": args.local}
        written = create(args.dir, params)
        given = values(params)
    except Usage as error:
        _complain(str(error))
        return 2
    except TemplateError as error:
        _complain(str(error))
        return 2
    except OSError as error:
        _complain(f"catalog.py new: {error.strerror or error}")
        return 2
    steps = _next_steps(args, shlex_quote(args.dir), os.path.basename(os.path.abspath(args.dir)))
    kind = "a folder catalog" if args.local else f"standard {LEVEL}"
    _say(f"Created {given['name']} in {args.dir} ({kind}, {len(written)} files)")
    if warning:
        _complain(warning)
    _say("")
    _say("Next")
    for line in steps:
        _say(line)
    return 0


def shlex_quote(text: str) -> str:
    import shlex
    return shlex.quote(text)


def _upgrade_arguments(argv: List[str]):
    parser = _parser("catalog.py upgrade")
    parser.add_argument("dir", nargs="?", default=".")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--lab-ref", dest="lab_ref")
    parser.add_argument("--adopt", action="store_true")
    parser.add_argument("--with", dest="with_", action="append", default=[])
    parser.add_argument("--maintainers")
    parser.add_argument("--lab-repo", dest="lab_repo")
    return parser.parse_args(argv)


def describe(plan: dict) -> str:
    """The plan as text for a person."""
    name = plan["name"] or "This catalog"
    lines: List[str] = []
    if plan["have"] is not None:
        lines.append(f"{name} is on standard {plan['have']}. This Lab has standard {plan['level']}.")
    else:
        lines.append(f"{name} has no standard yet. This Lab has standard {plan['level']}.")
    if plan["notes"] and plan["pending"]:
        for note in plan["notes"]:
            lines += ["", f"What is new in standard {note['level']}", f"  {note['summary']}"]
            lines += [f"  {each}" for each in note["manual"] + note["settings"]]
    groups = {ADD: [], UPDATE: [], OWNED: []}
    for each in plan["files"]:
        if each["action"] in groups:
            groups[each["action"]].append(each["path"])
    changing = [(action, path) for action in (ADD, UPDATE) for path in sorted(groups[action])]
    if changing:
        lines += ["", "Will change"] + [f"  {action:<8} {path}" for action, path in changing]
    if groups[OWNED]:
        lines += ["Owned by this catalog, left as it is"] + [f"  {OWNED:<8} {path}" for path in sorted(groups[OWNED])]
    lab = plan["lab"]
    if lab["new"]:
        lines += ["", f"The Lab pin moves from {lab['old'][:7]} to {lab['new'][:7]}. What it brings:", f"  {lab['compare']}"]
        lines += [f"  {each}" for each in lab["log"]]
    if not plan["pending"]:
        lines += ["", "Nothing to upgrade"]
    return "\n".join(lines)


def apply_command(args) -> str:
    """The command that makes what the plan says, as the person would type it: the Makefile's when it carries every option that
    was given (it is run in the catalog and takes `LAB_REF`), else `catalog.py`'s own with all of them."""
    plain = not (args.adopt or args.with_ or args.maintainers or args.lab_repo)
    if plain and args.dir == ".":
        return "make upgrade WRITE=1" + (f" LAB_REF={shlex_quote(args.lab_ref)}" if args.lab_ref else "")
    words = ["catalog.py", "upgrade"] + ([] if args.dir == "." else [shlex_quote(args.dir)])
    for flag, value in (("--lab-ref", args.lab_ref), ("--maintainers", args.maintainers), ("--lab-repo", args.lab_repo)):
        words += [flag, shlex_quote(value)] if value else []
    words += ["--adopt"] if args.adopt else []
    for word in args.with_:
        words += ["--with", shlex_quote(word)]
    return " ".join(words + ["--write"])


def legacy_lab(root) -> Optional[int]:
    """The `lab` that older Gravitee Labs need in the catalog.yml of the folder `root` (`check.folder_lab`), or None when
    this Lab cannot say it here: the upgrade then keeps the one the file has."""
    from ..library import check   # here and not above: the units are read as `check` reads them, which nothing else here does
    try:
        return check.folder_lab(root)
    except (check.Unavailable, OSError, ValueError, gitx.CatalogError):
        return None


def main_upgrade(argv: List[str], lab: Optional[Path] = None) -> int:
    """`catalog.py upgrade [DIR] [--write] [--lab-ref COMMIT or latest] [--adopt]
    [--with e2e]`. Without `--write` it prints the plan and writes nothing (exit 1 when anything is pending, 0 when nothing is)."""
    try:
        args = _upgrade_arguments(list(argv))
        lab = lab or _lab_checkout()
        root = Path(args.dir)
        if not root.is_dir():
            raise Usage(f"{printable(args.dir)[:200]} is not a folder.")
        ref = args.lab_ref or ""
        if ref == "latest":
            ref = latest_release(lab)
            if not ref:
                raise Usage("--lab-ref latest: the Lab checkout that runs this has no release to name. Fix: git fetch --tags, then "
                            "run again.")
        tree = FolderTree(root)
        if args.adopt and not ref and tree.entry(STANDARD) is None:
            # a repository made by hand has no pin yet: it gets the latest release of the Lab this command runs from
            ref = latest_release(lab) or _lab_head(lab)
        plan = upgrade_plan(tree, {"lab_ref": ref, "adopt": args.adopt, "with": args.with_,
                                   "maintainers": args.maintainers,
                                   "lab_repository": args.lab_repo or (_lab_origin(lab) if args.adopt else ""),
                                   "lab": lab, "legacy_lab": legacy_lab(root)})
    except Usage as error:
        _complain(str(error))
        return 2
    except TreeError as error:
        _complain(str(error))
        return 2
    except (TemplateError, UpgradeError, OSError) as error:
        _complain(f"catalog.py upgrade: {error}")
        return 2
    if plan["refused"]:
        _complain(plan["refused"])
        return 2
    _say(describe(plan))
    if not plan["pending"]:
        return 0
    if not args.write:
        _say("")
        _say(f"Nothing was written. To apply: {apply_command(args)}")
        return 1
    try:
        touched = [each["path"] for each in plan["files"] if each["action"] in (ADD, UPDATE)]
        dirty = dirty_files(root, touched)
        if dirty:
            raise UpgradeError("Uncommitted changes in files the upgrade rewrites: " + ", ".join(dirty) + ". Commit or restore "
                               "them, then run again.")
        written = upgrade_apply(root, plan)
    except (UpgradeError, gitx.CatalogError) as error:
        _complain(getattr(error, "message", None) or str(error))
        return 2
    except OSError as error:
        _complain(f"catalog.py upgrade: {error.strerror or error}")
        return 2
    _say("")
    _say(f"Wrote {len(written)} files.")
    return 0


__all__ = ["LEVEL", "RULES", "TemplateError", "UpgradeError", "apply_command", "carry_pins", "comparable", "create", "declared", "describe",
           "dirty_files", "drift", "dump_standard", "gitlinks", "golden_now", "golden_problems", "main_new", "main_upgrade",
           "manifest", "managed_paths", "managed_roots", "marker_level", "normalize", "render_new",
           "upgrade_apply", "upgrade_plan", "values", "write_golden"]
