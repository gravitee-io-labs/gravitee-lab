"""The engine contract: what the units of a catalog may rely on. `docs/contract.md` says it, `contract.lock` (at the root of
the repository) pins it, one line per item with the level it appeared at, and this file keeps the two true to the files.

The lock is derived from the live files and never written by hand. From the root of the repository:

    cd lab-ui && python3 -m tests.test_contract                 # prints the lock it would write, and what stops it
    cd lab-ui && python3 -m tests.test_contract --write         # writes contract.lock

What it lists, and where each line comes from (`<kind>` is the first word of a line):

    core.service, core.network     the services of docker-compose.yml with their container ports (the `target` of each
                                   port, not the one published on this machine), whether they have a healthcheck and
                                   their networks; its networks
    core.variable, .image_variable every variable the core file refers to (the settings any unit may read), the image
                                   variables (`*_VERSION`, `*_REGISTRY`) apart
    core.category                  the categories a unit's `category` may take (x-gravitee.categories)
    catalog.key                    the keys of x-gravitee-catalog in a catalog.yml (`manifest.KEYS`), and the parts of the
                                   ones that are mappings (`manifest.PARTS`)
    unit.key                       the keys of `check.META` (with the kind that reads one) and of `check.REQUIRES`
    label                          the `io.gravitee.*` labels the Lab UI reads (the string constants of lab_ui/, and the
                                   console labels its pattern accepts: `io.gravitee.console.<id>.name|url|legacy`) and
                                   those the core carries
    label.value                    the values of a label the Lab acts on (`understood.LABEL_VALUES`), as label=value
    seed.folder, seed.file         what the seeder modules read of a seed (`files`, `seed_file`, `has_seed`, `FOLDERS`)
    seed.setting                   the settings they take from the environment (`setting`, `os.getenv`, `os.environ`)
    frame.message                  the `gravitee-lab:*` messages of the page and of lab-ui/frames/location.js
    anchor                         what a unit may reach: the seeder as a build context, `plugins/<product>`, the origin
                                   of the Lab UI
    guide.block                    what a guide may carry that no file here states (DECLARED below)

Changing the contract is a decision, so the test fails until it is written down:

- An item added needs `contract` raised in docker-compose.yml, then the lock written: the new lines get `since` equal
  to the new level. Every addition does, optional or not: a Lab released before it does not read a catalog that uses it,
  and the legacy `lab` of that catalog says so to the Labs that read levels. The one exception is a kind the lock lists for
  the first time, whose items were read before it listed them (the values of a label): each gets the level of the item it
  belongs to, once.
- An item removed or changed needs `contract_min` raised in docker-compose.yml first, an entry under "Removed" in
  docs/contract.md that names it between backticks, and the floor to stay two levels under `contract`, except while
  `contract` is 2 or lower (the policy of docs/contract.md). `contract_min` never passes `contract`: the first removal
  raises both. Any difference in a core service is a change, an added port or network too.
- A lock that is lost is restored from git, never started again: once `contract` is past 1, a new one would give every
  item the current level.

`LANDING` lists what level 1 holds and the files of this tree do not state yet; `DECLARED` what no file can state;
`UNREAD` the reads of the seeder that no scan can resolve (a name that comes from a variable): a new one is a decision.
A test says which rows of the first and the last the files no longer need."""

import ast
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from lab_ui.library import check, project
from lab_ui.catalogs import manifest
from lab_ui.docker import compose
from lab_ui.gravitee import frames
from lab_ui.catalogs import understood
from lab_ui import paths
from lab_ui.paths import CORE_FILE, ROOT

from . import Case

LOCK = "contract.lock"
DOC = "docs/contract.md"
COMMAND = "cd lab-ui && python3 -m tests.test_contract"
# The first word of a line, in the order the lock lists them
KINDS = ("core.service", "core.network", "core.variable", "core.image_variable", "core.category", "catalog.key", "unit.key",
         "label", "label.value", "seed.folder", "seed.file", "seed.setting", "frame.message", "anchor", "guide.block")
# Level 1 is the state of the tree at the split, with the work that was in flight. These items belong to it and the
# files cannot state them yet: the keys arrive with the terminal, the host declaration and the verification of `lab`
# (check.META, check.REQUIRES), the seed folders with their seeder modules. A row that the files state is the files'
# (their kind wins), so each row is deleted by whoever lands its source; a row left behind hides a later removal, and
# Live.test_no_row_of_the_tables_outlives_its_source names it. Whoever lands `terminal` also sets
# `KEY_OF["terminal"] = "playground"` in check.py: without it the live line reads `kinds=any`, a change that only a
# raise of `contract_min` could accept.
LANDING = {("unit.key", "x-gravitee.terminal"): "kinds=playground",
           ("unit.key", "x-gravitee.host_access"): "kinds=any",
           ("unit.key", "x-gravitee.requires.lab"): "kinds=any",
           ("seed.folder", "environments"): "",
           ("seed.folder", "k8s"): ""}
# What a guide may carry and no file states: a `run` block is run in the terminal pane from a button of the guide
DECLARED = {("guide.block", "run"): ""}
# The reads and paths of the seeder that a scan cannot resolve, because the name comes from a variable: (module, source of
# the read) and why it lists nothing. A new one is not listed in the lock: each is a decision, made here with its reason.
ENVIRONMENT = "the files of an environment folder, seed/environments/<id>/, which repeats the names of seed/apim/ and its portal/ (docs/contract.md, the seed)"
UNREAD = {("lab_seeder/aim_catalog.py", "files('aim', folder)"): "the folder is one of FOLDERS, which are listed",
          ("lab_seeder/aim_catalog.py", "os.getenv(spec['needs'])"): "a seed file names the variable in `needs:`",
          ("lab_seeder/common.py", "os.getenv(m.group(2))"): "a seed file names the variable in `${NAME}`",
          ("lab_seeder/common.py", "os.getenv(name)"): "`setting(name)` itself: each call names its setting",
          ("lab_seeder/seed.py", "has_seed(*getattr(module, 'FOLDERS', ()))"): "the FOLDERS of each module, which are listed",
          ("lab_seeder/apim.py", "files(*parts, 'apis', suffixes=('.json',))"): ENVIRONMENT,
          ("lab_seeder/apim.py", "files(*parts, 'applications')"): ENVIRONMENT,
          ("lab_seeder/apim.py", "files(*parts, 'groups')"): ENVIRONMENT,
          ("lab_seeder/apim.py", "files(*parts, 'subscriptions')"): ENVIRONMENT,
          ("lab_seeder/apim.py", "seed_file(*parts, 'environment.yaml')"): ENVIRONMENT,
          ("lab_seeder/apim.py", "seed_file(*parts, 'settings.yaml')"): ENVIRONMENT,
          ("lab_seeder/apim.py", "seed_file(*parts, 'users.yaml')"): ENVIRONMENT,
          ("lab_seeder/apim_portal.py", "files(*parts, 'apis', suffixes=('.json',))"): ENVIRONMENT,
          ("lab_seeder/apim_portal.py", "files(*self.parts, 'portal', DOCS, suffixes=('.yaml', '.yml'))"): ENVIRONMENT,
          ("lab_seeder/apim_portal.py", "files(*self.parts, 'portal', name)"): ENVIRONMENT,
          ("lab_seeder/apim_portal.py", "seed_file(*parts, 'environment.yaml')"): ENVIRONMENT,
          ("lab_seeder/apim_portal.py", "seed_file(*self.parts, 'portal', 'portal.yaml')"): ENVIRONMENT,
          ("lab_seeder/apim_portal.py", "seed_file(*self.parts, 'portal', 'theme.yaml')"): ENVIRONMENT,
          ("lab_seeder/apim_portal.py", "seed_file(*self.parts, 'portal', DETAILS)"): ENVIRONMENT,
          ("lab_seeder/apim_portal.py", "seed_file(*self.parts, 'portal', OVERVIEW)"): ENVIRONMENT}
LEVEL = "since"
TOKEN = re.compile(r'"[^"]*"|\S+')   # a word of a line: a name with a space is in double quotes
IMAGE_VARIABLE = re.compile(r"_(?:VERSION|REGISTRY)$")
LABEL = re.compile(r"io\.gravitee\.[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*")
CONSOLE = re.compile(r"io\.gravitee\.console\.[a-z0-9-]+\.(name|url|legacy)")
CONSOLE_START = r"io\.gravitee\.console\."   # how the Lab UI's pattern for a console label starts, as its source says it
FRAME = re.compile(r"""["'`](gravitee-lab:[a-z][a-z0-9-]*)["'`]""")
SETTING = re.compile(r"[A-Z][A-Z0-9_]*")
SEED_FILES = (".yaml", ".yml", ".json")   # a path of the seed that ends so is a file; the others are folders
NOT_MODULES = {"testkit.py"}   # the seeder's test harness reads the environment for the tests, not for a seed
SEED_CALLS = {"files": False, "seed_file": True, "has_seed": None}   # what each says its path is: folder, file, either


# ---------------------------------------------------------------------------------------------------------------------
# The listing: every line the live files state
# ---------------------------------------------------------------------------------------------------------------------

def _name(node, aliases=None):
    """What a call or an attribute is known by: `files` for files(...) and for common.files(...); `aliases` ({local name:
    name}, from `import ... as ...`) gives an import under another name its own."""
    name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
    return (aliases or {}).get(name, name)


def _aliases(tree):
    """{local name: name} of the imports that rename (`from common import files as f`), so that f(...) is read as files."""
    return {alias.asname: alias.name.split(".")[-1] for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names if alias.asname}


def _constants(tree):
    """{name: text} of the module-level lines `NAME = "text"` (annotated or not), so that `setting(MODE)` is read as the
    setting it names."""
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name) and isinstance(value, ast.Constant) and isinstance(value.value, str):
            found[target.id] = value.value
    return found


def _text(node, constants):
    """The text of a string, or of a name that stands for one; None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return constants.get(node.id) if isinstance(node, ast.Name) else None


def _leading(nodes, constants):
    """The texts at the start of these arguments, up to the first that is no text (`files("aim", folder)` is `aim`)."""
    found = []
    for node in nodes:
        text = _text(node, constants)
        if text is None:
            break
        found.append(text)
    return found


def _listed(node, constants):
    """The texts of a tuple or a list of texts, or of several joined with `+`; None stands for a part that is no text."""
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_text(element, constants) for element in node.elts]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _listed(node.left, constants) + _listed(node.right, constants)
    return [None]


def _read(node, aliases=None):
    """The argument that names the setting a node reads from the environment, or None for a node that reads none."""
    if isinstance(node, ast.Call) and node.args:
        func = node.func
        if _name(func, aliases) in ("setting", "getenv") or (isinstance(func, ast.Attribute) and func.attr == "get"
                                                              and _name(func.value, aliases) == "environ"):
            return node.args[0]
    if isinstance(node, ast.Subscript) and _name(node.value, aliases) == "environ":
        return node.slice
    return None


def _parsed(file):
    return ast.parse(file.read_text(encoding="utf-8"), filename=str(file))


def scan_seed(folder):
    """(items, unread): the folders and files of a seed that the modules under `folder` read, and the settings they take
    from the environment, found by scanning them (their syntax tree, never by importing: they need the seeder's
    requirements); then the reads and paths that no scan resolves, as (module, source of the read)."""
    folders, files, settings, unread = set(), set(), set(), set()

    def add(parts, is_file):
        parts = [part for part in "/".join(parts).split("/") if part]
        if not parts:
            return
        if is_file is None:
            is_file = parts[-1].endswith(SEED_FILES)
        if is_file:
            files.add("/".join(parts))
            parts = parts[:-1]
        folders.update("/".join(parts[:n]) for n in range(1, len(parts) + 1))

    root = Path(folder)
    for file in sorted(root.rglob("*.py")):
        module = file.relative_to(root)
        if file.name in NOT_MODULES or "tests" in module.parts[:-1]:
            continue
        tree = _parsed(file)
        constants, aliases = _constants(tree), _aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if node.value is not None and any(_name(target) == "FOLDERS" for target in targets):
                    for text in _listed(node.value, constants):
                        if text is None:
                            unread.add((module.as_posix(), f"FOLDERS = {ast.unparse(node.value)}"))
                        else:
                            add([text], None)
            elif isinstance(node, ast.Call) and _name(node.func, aliases) in SEED_CALLS:
                is_file = SEED_CALLS[_name(node.func, aliases)]
                if is_file is None:   # has_seed(a, b, *FOLDERS): each argument is a path
                    texts = [_text(argument, constants) for argument in node.args]
                    for text in texts:
                        add([text or ""], None)
                    lost = [argument for argument, text in zip(node.args, texts) if text is None and not (
                        isinstance(argument, ast.Starred) and _name(argument.value) == "FOLDERS")]
                else:
                    leading = _leading(node.args, constants)
                    add(leading, is_file)
                    lost = node.args[len(leading):]
                if lost:
                    unread.add((module.as_posix(), ast.unparse(node)))
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):   # SEED_DIR / "aim" / "skills"
                parts, base = [], node
                while isinstance(base, ast.BinOp) and isinstance(base.op, ast.Div):
                    parts.append(base.right)
                    base = base.left
                if _name(base) == "SEED_DIR":
                    leading = _leading(reversed(parts), constants)
                    add(leading, None)
                    if len(leading) < len(parts):
                        unread.add((module.as_posix(), ast.unparse(node)))
            asked = _read(node, aliases)
            if asked is not None:
                name = _text(asked, constants)
                if name is None:
                    unread.add((module.as_posix(), ast.unparse(node)))
                elif SETTING.fullmatch(name):
                    settings.add(name)
    items = {**{("seed.folder", name): "" for name in folders}, **{("seed.file", name): "" for name in files},
             **{("seed.setting", name): "" for name in settings}}
    return items, unread


def seed_items(folder):
    """The folders, files and settings of a seed that the seeder modules under `folder` read (see scan_seed)."""
    return scan_seed(folder)[0]


def console_labels(reader):
    """The labels of a console that the Lab UI's pattern (`reader`, a compiled regex) accepts: one per part it names,
    `io.gravitee.console.<id>.name` and so on. ValueError when the pattern is no longer of that shape."""
    found = re.search(r"\.\(([a-z]+(?:\|[a-z]+)*)\)$", reader.pattern)
    if not found or not reader.pattern.startswith(CONSOLE_START):
        raise ValueError("lab_ui/library/project.py CONSOLE_LABEL no longer reads io.gravitee.console.<id>.(part|part): teach "
                         "console_labels in tests/test_contract.py its new shape")
    return {("label", f"io.gravitee.console.<id>.{part}"): "" for part in found.group(1).split("|")}


def label_items(folder, config, reader=None):
    """The `io.gravitee.*` labels the Lab UI reads (the string constants of its modules, and the console labels that its
    pattern `reader` accepts) and those the core carries."""
    names = set()
    for file in sorted(Path(folder).rglob("*.py")):
        names.update(node.value for node in ast.walk(_parsed(file))
                     if isinstance(node, ast.Constant) and isinstance(node.value, str) and LABEL.fullmatch(node.value))
    for service in (config.get("services") or {}).values():
        names.update(key for key in service.get("labels") or {} if LABEL.fullmatch(key))
    items = {("label", CONSOLE.sub(r"io.gravitee.console.<id>.\1", name) if CONSOLE.fullmatch(name) else name): ""
             for name in names}
    if reader is not None:
        items.update(console_labels(reader))
    return items


def frame_items(folders):
    """The messages the page and the UIs it frames exchange: the `gravitee-lab:*` strings of their scripts."""
    found = set()
    for folder in folders:
        for file in sorted(Path(folder).rglob("*.js")):
            found.update(FRAME.findall(file.read_text(encoding="utf-8")))
    return {("frame.message", name): "" for name in found}


def key_items(meta, owners, requires):
    """The keys a unit may carry in x-gravitee and in its `requires`, with the kind that reads each (`any` when all)."""
    items = {("unit.key", f"x-gravitee.{key}"): f"kinds={owners.get(key, 'any')}" for key in meta}
    items.update({("unit.key", f"x-gravitee.requires.{key}"): "kinds=any" for key in requires})
    return items


def catalog_items(keys, parts):
    """The keys a catalog.yml may carry in x-gravitee-catalog, and the parts of those that are mappings."""
    items = {("catalog.key", f"{manifest.BLOCK}.{key}"): "" for key in keys}
    items.update({("catalog.key", f"{manifest.BLOCK}.{key}.{part}"): "" for key, names in parts.items() for part in names})
    return items


def core_items(config, variables):
    """The core's services (container ports, healthcheck, networks), networks, variables and categories."""
    items = {}
    for name, service in (config.get("services") or {}).items():
        ports = sorted({str(port.get("target")) for port in service.get("ports") or []},
                       key=lambda port: (not port.isdigit(), int(port) if port.isdigit() else 0, port))
        attributes = [f"port={','.join(ports)}"] if ports else []
        attributes.append(f"health={'yes' if (service.get('healthcheck') or {}).get('test') else 'no'}")
        if service.get("networks"):
            attributes.append(f"networks={','.join(sorted(service['networks']))}")
        items[("core.service", name)] = " ".join(attributes)
    items.update({("core.network", name): "" for name in config.get("networks") or {}})
    items.update({("core.image_variable" if IMAGE_VARIABLE.search(name) else "core.variable", name): ""
                  for name in variables})
    items.update({("core.category", name): "" for name in (config.get("x-gravitee") or {}).get("categories") or []})
    return items


def _under(source, roots):
    """`source` as a path under the first of `roots` that holds it, read as the text it is, or None. Compose gives a bind
    source unresolved, so a link in `plugins/` (a machine's own choice) or one on the way to the repository must not
    move it out from under the root."""
    if not isinstance(source, str):
        return None
    for root in roots:
        try:
            return Path(os.path.normpath(source)).relative_to(root).as_posix()
        except ValueError:
            continue
    return None


def anchor_items(config, root, pages):
    """What a unit may reach: the seeder as a build context (while the core names its image), `plugins/<product>`
    where the core mounts it, and the Lab UI's origin, the one thing the Lab must be (port and hosts of its pages)."""
    items = {}
    seeder = (config.get("x-gravitee") or {}).get("seeder")
    if isinstance(seeder, dict) and isinstance(seeder.get("image"), str) and (Path(root) / "seeder/Dockerfile").is_file():
        items[("anchor", "seeder")] = ""
    roots = list(dict.fromkeys([Path(os.path.normpath(root)), Path(root).resolve()]))   # as given, then as the disk has it
    for service in (config.get("services") or {}).values():
        for volume in service.get("volumes") or []:
            source = volume.get("source") if isinstance(volume, dict) and volume.get("type") == "bind" else None
            found = re.fullmatch(r"plugins/([a-z0-9-]+)", _under(source, roots) or "")
            if found:
                items[("anchor", f"plugins/{found.group(1)}")] = ""
    origins = [urlsplit(page) for page in pages]
    if origins:
        items[("anchor", "lab-ui")] = (f"port={'/'.join(sorted({str(o.port or 80) for o in origins}))} "
                                       f"hosts={','.join(sorted({o.hostname for o in origins}))}")
    return items


def stated(config=None, variables=None):
    """Every item the files state themselves: {(kind, name): attributes}. `config` is the core as Compose reads it
    (compose.raw), `variables` the names it refers to."""
    config = compose.raw(CORE_FILE) if config is None else config
    names = [found["key"] for found in compose.variables(CORE_FILE)] if variables is None else variables
    items = core_items(config, names)
    items.update(catalog_items(manifest.KEYS, manifest.PARTS))
    items.update(key_items(check.META, check.KEY_OF, check.REQUIRES))
    items.update(label_items(paths.LAB_UI_DIR / "lab_ui", config, project.CONSOLE_LABEL))
    items.update({("label.value", f"{label}={value}"): "" for label, values in understood.LABEL_VALUES.items() for value in values})
    items.update(seed_items(ROOT / "seeder"))
    items.update(frame_items([paths.WEB_DIR / "js", ROOT / "lab-ui" / "frames"]))
    items.update(anchor_items(config, ROOT, frames.PAGES))
    return items


def listing(config=None, variables=None):
    """Every item of level 1 and after: what the files state, then what LANDING and DECLARED add that they do not."""
    items = {**LANDING, **DECLARED}
    items.update(stated(config, variables))
    for kind, name in items:
        if not name or re.search(r'[\x00-\x1f\x7f"]', name):
            raise ValueError(f"{kind} {name!r}: a name is not empty and holds no double quote or control character")
    return items


# ---------------------------------------------------------------------------------------------------------------------
# The lock: its text, and what stands between it and the listing
# ---------------------------------------------------------------------------------------------------------------------

def _order(key):
    return KINDS.index(key[0]), key[1]


def _word(text):
    return f'"{text}"' if re.search(r"\s", text) else text


def line(key, attributes, since=None):
    """An item as the lock writes it: its kind, its name, its attributes, then its level."""
    words = [key[0], _word(key[1])] + attributes.split() + ([f"{LEVEL}={since}"] if since is not None else [])
    return " ".join(words)


def render(contract, floor, rows):
    """The text of a lock: its two levels, then every item with its level, grouped by kind. `rows` is
    {(kind, name): (attributes, since)}."""
    lines = [f"contract {contract}", f"contract_min {floor}"]
    lines += [line(key, rows[key][0], rows[key][1]) for key in sorted(rows, key=_order)]
    return "\n".join(lines) + "\n"


def parse(text):
    """(contract, contract_min, {(kind, name): (attributes, since)}) of a lock's text. ValueError names the line that
    is wrong: a missing header, an unknown kind, a missing or repeated level, an item listed twice."""
    lines = [(number, row) for number, row in enumerate(text.split("\n"), 1) if row.strip()]
    levels = []
    for expected, (number, row) in zip(("contract", "contract_min"), lines):
        words = row.split()
        if len(words) != 2 or words[0] != expected or not words[1].isdigit() or int(words[1]) < 1:
            raise ValueError(f"line {number}: expected `{expected} N` with N from 1, found `{row}`")
        levels.append(int(words[1]))
    if len(levels) < 2:
        raise ValueError("expected the two lines `contract N` and `contract_min N` first")
    rows = {}
    for number, row in lines[2:]:
        words = TOKEN.findall(row)
        levels_of = [word for word in words if word.startswith(f"{LEVEL}=")]
        if words[0] not in KINDS or len(words) < 3 or len(levels_of) != 1 or words[-1] != levels_of[0] \
                or not levels_of[0][len(LEVEL) + 1:].isdigit():
            raise ValueError(f"line {number}: expected `<kind> <name> [key=value ...] {LEVEL}=N`, found `{row}`")
        key = (words[0], words[1].strip('"'))
        if key in rows:
            raise ValueError(f"line {number}: {words[0]} {words[1]} is listed twice")
        rows[key] = (" ".join(words[2:-1]), int(levels_of[0][len(LEVEL) + 1:]))
    return levels[0], levels[1], rows


def differences(rows, live):
    """(added, removed, changed): the keys the live files state and the lock does not, those the lock lists and the
    live files no longer state, and those whose attributes differ."""
    added = sorted((key for key in live if key not in rows), key=_order)
    removed = sorted((key for key in rows if key not in live), key=_order)
    changed = sorted((key for key in live if key in rows and rows[key][0] != live[key]), key=_order)
    return added, removed, changed


def window(contract, floor):
    """What is wrong with the two levels of the core file, as lines: `contract_min` is from 1 to `contract`, and once
    `contract` is past 2 two levels stay readable under it."""
    if not 1 <= floor <= contract:
        return [f"{CORE_FILE}: contract_min {floor} must be from 1 to contract {contract}"]
    if floor > 1 and contract > 2 and floor > contract - 2:
        return [f"{CORE_FILE}: contract_min {floor} leaves fewer than two levels readable under contract {contract}: "
                f"raise contract first, or keep contract_min at {contract - 2} or lower"]
    return []


def verdict(text, live, contract, floor):
    """What is wrong between the lock's text and the live files, as the lines to show; none when they agree.
    `contract` and `floor` are the two levels docker-compose.yml states."""
    try:
        locked, locked_floor, rows = parse(text)
    except ValueError as error:
        return [f"{LOCK}: {error}"]
    found = window(contract, floor)
    if (locked, locked_floor) != (contract, floor):
        found.append(f"{LOCK} says contract {locked} and contract_min {locked_floor}, {CORE_FILE} says {contract} and "
                     f"{floor}: write the lock again")
    found += [f"{line(key, rows[key][0], rows[key][1])}: {LEVEL} must be from 1 to {contract}"
              for key in sorted(rows, key=_order) if not 1 <= rows[key][1] <= contract]
    added, removed, changed = differences(rows, live)
    if added:
        level = max(contract, locked + 1)
        found += [f"Added items need `contract` raised to {level} and `{LEVEL}={level}` written."]
        found += [f"  + {line(key, live[key])}" for key in added]
    if removed or changed:
        if floor <= locked_floor:
            found.append("Removed or changed items need `contract_min` raised.")
        found.append(f"Add an entry under Removed in {DOC} that names each of them between backticks.")
        found += [f"  - {line(key, rows[key][0])}" for key in removed]
        found += [f"  ~ {line(key, rows[key][0])}  ->  {line(key, live[key])}" for key in changed]
    if added or removed or changed or (locked, locked_floor) != (contract, floor):
        found.append(f"Write the lock again with: {COMMAND} --write")
    return found


def removed_section(doc):
    """The text of the page under `## Removed`, up to the next heading of its level; empty when there is none."""
    return doc.split("\n## Removed", 1)[1].split("\n## ", 1)[0] if "\n## Removed" in doc else ""


FORMAT_LEVEL = 1   # catalog.yml was read with its keys from the split on: what its keys belong to when the lock first lists them


def _first_level(key, rows):
    """The level of an item of a kind the lock lists for the first time, whose items were read before it listed them: a value
    of a label takes its label's (`rows`), a key of catalog.yml the level of the format it belongs to. None for any other: it is
    an addition."""
    if key[0] == "label.value":
        found = rows.get(("label", key[1].partition("=")[0]))
        return found[1] if found else None
    return FORMAT_LEVEL if key[0] == "catalog.key" else None


def plan(text, live, contract, floor, removed=None):
    """(the text of the lock to write, what stops writing it): the live items, each with the level it has in the lock
    (a new or changed item gets the level of `contract`). It stops at levels that are wrong, at a lock that is missing
    once `contract` is past 1, at a level that goes down or up by more than one, at an addition when `contract` was not
    raised, and at a removal when `contract_min` was not. A kind the lock has no line of yet (`_first_level`) is listed once at the
    level of the items it belongs to, and is no addition: they were read before the lock listed them. `removed` is the text
    under Removed in the page: when given, each item removed or changed must be named in it between backticks."""
    try:
        locked, locked_floor, rows = parse(text) if text is not None else (None, None, {})
    except ValueError as error:
        return None, [f"{LOCK}: {error}"]
    stops = window(contract, floor)
    if locked is None and contract > 1:
        stops.append(f"{LOCK} is missing and contract is {contract}: restore it from git, because a new lock would give "
                     f"every item {LEVEL}={contract}")
    if locked is not None:
        if contract < locked or floor < locked_floor:
            stops.append(f"{CORE_FILE}: a level never goes down (the lock has contract {locked} and contract_min "
                         f"{locked_floor})")
        if contract > locked + 1:
            stops.append(f"{CORE_FILE}: contract rises one level at a time (the lock has {locked}, not {contract - 1})")
        added, gone, changed = differences(rows, live)
        listed_kinds = {kind for kind, _ in rows}
        first = {key for key in added if key[0] not in listed_kinds and _first_level(key, rows) is not None}
        added = [key for key in added if key not in first]
        if added and contract == locked:
            stops.append(f"Added items need `contract` raised to {locked + 1} in {CORE_FILE} first")
        if (gone or changed) and floor <= locked_floor:
            stops.append(f"Removed or changed items need `contract_min` raised in {CORE_FILE} first, with an entry under "
                         f"Removed in {DOC}")
        unnamed = [key[1] for key in [*gone, *changed] if removed is not None and f"`{key[1]}`" not in removed]
        if unnamed:
            stops.append(f"{DOC} has no entry under Removed that names, between backticks: "
                         + ", ".join(f"`{name}`" for name in unnamed))
    def since(key):
        if key in rows and rows[key][0] == live[key]:
            return rows[key][1]
        if locked is not None and key[0] not in {kind for kind, _ in rows} and _first_level(key, rows) is not None:
            return _first_level(key, rows)
        return contract
    kept = {key: (live[key], since(key)) for key in live}
    return render(contract, floor, kept), stops


def core_levels(config):
    """(contract, contract_min) of the core file; ValueError when one is missing or is not a whole number from 1."""
    meta = config.get("x-gravitee") or {}
    levels = []
    for key in ("contract", "contract_min"):
        value = meta.get(key)
        if type(value) is not int or value < 1:
            raise ValueError(f"{CORE_FILE}: x-gravitee.{key} must be a whole number from 1")
        levels.append(value)
    return tuple(levels)


def main(arguments):
    """The command of the docstring: prints the lock it would write (or writes it with --write) and, on stderr, what
    stops it. Exit code 0 when the lock is written or would be; 1 when something stops it; 2 when the files cannot be
    read or the arguments are wrong."""
    flags = set(arguments)
    if flags - {"--write"}:
        print(f"usage: {COMMAND} [--write]", file=sys.stderr)
        return 2
    try:
        config = compose.raw(CORE_FILE)
        contract, floor = core_levels(config)
        live = listing(config)
    except (compose.ComposeError, ValueError, SyntaxError) as error:
        print(error, file=sys.stderr)
        return 2
    file, page = ROOT / LOCK, ROOT / DOC
    text, stops = plan(file.read_text(encoding="utf-8") if file.is_file() else None, live, contract, floor,
                       removed_section(page.read_text(encoding="utf-8")) if page.is_file() else "")
    for stop in stops:
        print(stop, file=sys.stderr)
    if stops:
        return 1
    if "--write" in flags:
        paths.write_atomic(file, text)
        print(f"{LOCK} written: contract {contract}, contract_min {floor}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


# ---------------------------------------------------------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------------------------------------------------------

SAMPLE = """contract 2
contract_min 1
core.service apim-gateway port=8082,8443 health=yes networks=edge,internal since=1
core.network edge since=1
core.category "Gravitee Platform" since=1
unit.key x-gravitee.terminal kinds=playground since=2
label io.gravitee.role since=1
anchor seeder since=1
"""


def compose_ready():
    """Whether Compose 5 reads the core: it is the one reader of the files, and needs no daemon."""
    if not shutil.which("docker"):
        return False
    code, out, _ = compose.run([*compose.COMPOSE, "version", "--short"], timeout=30)
    major = out.strip().lstrip("v").split(".")[0]
    return code == 0 and major.isdigit() and int(major) >= 5


class Lock(Case):

    def test_a_lock_is_read_and_written_the_same(self):
        contract, floor, rows = parse(SAMPLE)
        self.assertEqual((contract, floor), (2, 1))
        self.assertEqual(rows[("core.service", "apim-gateway")], ("port=8082,8443 health=yes networks=edge,internal", 1))
        self.assertEqual(rows[("core.category", "Gravitee Platform")], ("", 1), "a name with a space is in quotes")
        self.assertEqual(rows[("unit.key", "x-gravitee.terminal")], ("kinds=playground", 2))
        self.assertEqual(render(contract, floor, rows), SAMPLE)

    def test_a_line_that_is_wrong_is_named(self):
        for text, why in (("", "the two lines"), ("contract 1\n", "the two lines"),
                          ("contract_min 1\ncontract 1\n", "line 1: expected `contract N`"),
                          ("contract 0\ncontract_min 1\n", "line 1: expected `contract N` with N from 1"),
                          ("contract 1\ncontract_min x\n", "line 2: expected `contract_min N`"),
                          ("contract 1\ncontract_min 1\ncore.network edge\n", "line 3: expected `<kind> <name>"),
                          ("contract 1\ncontract_min 1\ncore.network edge since=x\n", "line 3"),
                          ("contract 1\ncontract_min 1\ncore.network since=1 edge\n", "line 3"),
                          ("contract 1\ncontract_min 1\ncore.thing edge since=1\n", "line 3"),
                          ("contract 1\ncontract_min 1\ncore.network edge since=1 since=1\n", "line 3"),
                          ("contract 1\ncontract_min 1\ncore.network edge since=1\ncore.network edge since=1\n",
                           "line 4: core.network edge is listed twice")):
            with self.subTest(text=text), self.assertRaises(ValueError) as caught:
                parse(text)
            self.assertIn(why, str(caught.exception))

    def test_the_lock_is_in_the_order_of_its_kinds_then_its_names(self):
        rows = {("label", "b"): ("", 1), ("core.network", "z"): ("", 1), ("label", "a"): ("", 1),
                ("core.service", "s"): ("health=no", 1)}
        self.assertEqual(render(1, 1, rows).split("\n")[2:-1],
                         ["core.service s health=no since=1", "core.network z since=1", "label a since=1",
                          "label b since=1"])


class Verdict(Case):
    """The test that fails, and the words it fails with."""

    def live(self):
        return {key: attributes for key, (attributes, _) in parse(SAMPLE)[2].items()}

    def test_a_lock_that_matches_says_nothing(self):
        self.assertEqual(verdict(SAMPLE, self.live(), 2, 1), [])

    def test_an_item_removed_asks_for_contract_min(self):
        live = self.live()
        del live[("label", "io.gravitee.role")]
        said = verdict(SAMPLE, live, 2, 1)
        self.assertEqual(said[0], "Removed or changed items need `contract_min` raised.")
        self.assertEqual(said[1], "Add an entry under Removed in docs/contract.md that names each of them between backticks.")
        self.assertIn("  - label io.gravitee.role", said)
        self.assertNotIn("Added", " ".join(said))
        self.assertEqual(said[-1], f"Write the lock again with: {COMMAND} --write")

    def test_a_removal_after_the_core_raised_contract_min_asks_only_for_the_entry_and_the_lock(self):
        live = self.live()
        del live[("label", "io.gravitee.role")]
        said = verdict(SAMPLE, live, 2, 2)
        self.assertNotIn("need `contract_min` raised", " ".join(said))
        self.assertEqual(said[0], f"{LOCK} says contract 2 and contract_min 1, {CORE_FILE} says 2 and 2: write the lock again")
        self.assertTrue(said[1].startswith(f"Add an entry under Removed in {DOC}"), said)
        self.assertIn("  - label io.gravitee.role", said)

    def test_an_item_changed_asks_for_contract_min_and_shows_both_lines(self):
        live = {**self.live(), ("core.service", "apim-gateway"): "port=8082 health=yes networks=edge,internal"}
        said = verdict(SAMPLE, live, 2, 1)
        self.assertIn("  ~ core.service apim-gateway port=8082,8443 health=yes networks=edge,internal  ->  "
                      "core.service apim-gateway port=8082 health=yes networks=edge,internal", said)
        self.assertTrue(any("`contract_min` raised" in one for one in said))

    def test_an_item_added_asks_for_contract_and_its_level(self):
        live = {**self.live(), ("core.service", "new-service"): "port=9000 health=no"}
        said = verdict(SAMPLE, live, 2, 1)
        self.assertEqual(said[0], "Added items need `contract` raised to 3 and `since=3` written.")
        self.assertNotIn("keep", " ".join(said), "every addition raises contract: an older Lab never reads a catalog that uses it")
        self.assertIn("  + core.service new-service port=9000 health=no", said)
        self.assertNotIn("Removed", " ".join(said))

    def test_an_addition_after_the_core_raised_contract_asks_for_that_level(self):
        text = SAMPLE.replace("contract 2", "contract 1").replace("kinds=playground since=2", "kinds=playground since=1")
        live = {**self.live(), ("core.service", "new-service"): "health=no"}
        self.assertEqual(verdict(text, live, 2, 1)[1], "Added items need `contract` raised to 2 and `since=2` written.")

    def test_the_levels_of_the_lock_are_the_levels_of_the_core(self):
        said = verdict(SAMPLE, self.live(), 3, 1)
        self.assertEqual(said[0], f"{LOCK} says contract 2 and contract_min 1, {CORE_FILE} says 3 and 1: write the lock again")

    def test_a_level_beyond_contract_is_refused(self):
        text = SAMPLE.replace("label io.gravitee.role since=1", "label io.gravitee.role since=5")
        self.assertEqual(verdict(text, self.live(), 2, 1), ["label io.gravitee.role since=5: since must be from 1 to 2"])

    def test_the_floor_stays_two_levels_under_contract_except_while_contract_is_two_or_lower(self):
        text = SAMPLE.replace("contract 2", "contract 3").replace("contract_min 1", "contract_min 2")
        said = verdict(text, self.live(), 3, 2)[0]
        self.assertIn("contract_min 2 leaves fewer than two levels readable under contract 3", said)
        self.assertIn("keep contract_min at 1 or lower", said)
        for contract, floor in ((1, 1), (2, 1), (2, 2), (3, 1), (4, 2), (5, 3)):
            text = SAMPLE.replace("contract 2", f"contract {contract}").replace("contract_min 1", f"contract_min {floor}")
            text = text.replace("since=2", f"since={contract}")
            self.assertEqual(verdict(text, self.live(), contract, floor), [], (contract, floor))
        self.assertIn("must be from 1 to contract", verdict(SAMPLE, self.live(), 2, 3)[0])

    def test_a_lock_that_does_not_parse_is_one_line(self):
        said = verdict("contract 1\n", {}, 1, 1)
        self.assertEqual((len(said), said[0].startswith(f"{LOCK}: expected the two lines")), (1, True))


class Plan(Case):
    """Writing the lock: what keeps its level, what gets the new one, and what stops the writing."""

    def live(self):
        return {key: attributes for key, (attributes, _) in parse(SAMPLE)[2].items()}

    def test_what_is_unchanged_keeps_its_level_and_what_is_new_gets_the_current_one(self):
        live = {**self.live(), ("label", "io.gravitee.new"): ""}
        text, stops = plan(SAMPLE, live, 3, 1)
        self.assertEqual(stops, [])
        rows = parse(text)[2]
        self.assertEqual((rows[("unit.key", "x-gravitee.terminal")][1], rows[("label", "io.gravitee.new")][1]), (2, 3))
        self.assertEqual(parse(text)[:2], (3, 1))

    def test_the_first_lock_has_the_current_level_everywhere(self):
        text, stops = plan(None, self.live(), 1, 1)
        self.assertEqual((stops, {since for _, since in parse(text)[2].values()}), ([], {1}))

    def test_an_addition_stops_until_contract_is_raised(self):
        live = {**self.live(), ("label", "io.gravitee.new"): ""}
        self.assertEqual(plan(SAMPLE, live, 2, 1)[1], ["Added items need `contract` raised to 3 in docker-compose.yml first"])
        self.assertEqual(main(["--write", "--keep-level"]), 2, "there is no level to keep: every addition raises it")

    def test_a_kind_listed_for_the_first_time_takes_the_level_of_what_it_belongs_to(self):
        """The values of a label were read before the lock listed them: they take the label's level, once, and are no
        addition; a value added after that is one."""
        live = {**self.live(), ("label.value", "io.gravitee.role=seed"): ""}
        text, stops = plan(SAMPLE, live, 2, 1)
        self.assertEqual((stops, parse(text)[2][("label.value", "io.gravitee.role=seed")][1]), ([], 1))
        later = {**live, ("label.value", "io.gravitee.role=tester"): ""}
        self.assertEqual(plan(text, later, 2, 1)[1], ["Added items need `contract` raised to 3 in docker-compose.yml first"])

    def test_a_removal_stops_until_contract_min_is_raised(self):
        live = self.live()
        del live[("anchor", "seeder")]
        self.assertIn("Removed or changed items need `contract_min` raised in docker-compose.yml first",
                      plan(SAMPLE, live, 2, 1)[1][0])
        self.assertEqual(plan(SAMPLE, live, 2, 2)[1], [])

    def test_levels_only_go_up_and_one_at_a_time(self):
        self.assertIn("never goes down", plan(SAMPLE, self.live(), 1, 1)[1][0])
        self.assertIn("one level at a time", plan(SAMPLE, self.live(), 4, 1)[1][0])

    def test_a_lock_that_is_missing_is_not_started_again_once_contract_is_past_one(self):
        """A new lock would give every item the current level: `since` is the history the verification of `lab` reads."""
        live = self.live()
        stops = plan(None, live, 2, 1)[1]
        self.assertEqual(stops, [f"{LOCK} is missing and contract is 2: restore it from git, because a new lock would give "
                                 "every item since=2"])
        self.assertEqual(plan(None, live, 1, 1)[1], [], "the first lock is written at level 1")

    def test_contract_min_never_passes_contract_and_the_window_holds_when_a_lock_is_written(self):
        """The first removal raises both: `contract_min` alone, at contract 1, is refused."""
        self.assertEqual(plan(None, self.live(), 1, 2)[1], [f"{CORE_FILE}: contract_min 2 must be from 1 to contract 1"])
        self.assertIn("fewer than two levels readable", plan(SAMPLE, self.live(), 3, 2)[1][0])

    def test_what_leaves_must_be_named_in_the_page_between_backticks(self):
        live = self.live()
        del live[("anchor", "seeder")]
        live[("core.service", "apim-gateway")] = "port=8082 health=yes networks=edge,internal"
        self.assertEqual(plan(SAMPLE, live, 2, 2)[1], [], "the page is not read unless it is given")
        stops = plan(SAMPLE, live, 2, 2, removed="- Nothing of `seeder` or apim-gateway")[1]
        self.assertEqual(stops, [f"{DOC} has no entry under Removed that names, between backticks: `apim-gateway`"])
        self.assertEqual(plan(SAMPLE, live, 2, 2, removed="- `seeder` and `apim-gateway` left")[1], [])
        self.assertEqual(plan(SAMPLE, live, 2, 2, removed="")[1],
                         [f"{DOC} has no entry under Removed that names, between backticks: `seeder`, `apim-gateway`"])

    def test_the_removed_section_is_the_text_under_its_heading(self):
        doc = "# T\n\n## A\n\nno\n\n## Removed\n\n- `x`\n\n## After\n\n`y`\n"
        self.assertEqual(removed_section(doc), "\n\n- `x`\n")
        self.assertEqual(removed_section("# T\n\n## A\n"), "")


class Scans(Case):
    """What is read from the files, on files of the test's own."""

    def test_the_seeder_modules_are_read_for_what_they_take_and_where(self):
        self.folder()
        self.write("seeder/mod.py", '''
import os
from common import files, has_seed, seed_file, setting
import common
MODE = "SEED_MODE"
FILE = "controls.yaml"
FOLDERS = ("aim/models", "am/link.yaml")
def go(kind, name):
    a = setting("API_URL"); b = os.getenv("API_TOKEN", "x"); c = os.environ.get("ORG"); d = os.environ["ENV"]
    e = setting(MODE, "on"); f = setting(name); g = os.getenv(kind["key"]); h = setting("lower_case")
    for p in files("apim", "apis"), files("aim", kind), files("openfga"):
        pass
    seed_file("apim", "settings.yaml"); seed_file(FILE); has_seed("authz/policies", *FOLDERS)
    return common.SEED_DIR / "aim" / "skills"
''')
        self.write("seeder/testkit.py", 'import os\nos.getenv("HARNESS_ONLY")\n')
        self.write("seeder/tests/test_mod.py", 'import os\nos.getenv("IN_A_TEST")\n')
        found = seed_items(self.root / "seeder")
        self.assertEqual(sorted(name for kind, name in found if kind == "seed.setting"),
                         ["API_TOKEN", "API_URL", "ENV", "ORG", "SEED_MODE"])
        self.assertEqual(sorted(name for kind, name in found if kind == "seed.file"),
                         ["am/link.yaml", "apim/settings.yaml", "controls.yaml"])
        self.assertEqual(sorted(name for kind, name in found if kind == "seed.folder"),
                         ["aim", "aim/models", "aim/skills", "am", "apim", "apim/apis", "authz", "authz/policies", "openfga"])

    def test_the_forms_of_a_module_that_a_plain_assignment_or_a_plain_import_would_miss(self):
        """An annotated or summed FOLDERS, an import under another name, a module in a subpackage: all are read."""
        self.folder()
        self.write("seeder/a.py", 'FOLDERS: tuple = ("one/x.yaml",)\nNAME: str = "A_SETTING"\n'
                                  'from common import setting as get, files as f\nget(NAME); f("two", "y")\n')
        self.write("seeder/b.py", 'BASE = ("three",)\nFOLDERS = BASE + ("four", "five/z.json")\n')
        self.write("seeder/sub/c.py", 'from os import environ as env, getenv as g\nenv.get("B_SETTING"); env["C_SETTING"]; '
                                      'g("D_SETTING")\n')
        self.write("seeder/sub/tests/test_c.py", 'import os\nos.getenv("IN_A_TEST")\nFOLDERS = ("in-a-test",)\n')
        found, unread = scan_seed(self.root / "seeder")
        self.assertEqual(sorted(name for kind, name in found if kind == "seed.setting"),
                         ["A_SETTING", "B_SETTING", "C_SETTING", "D_SETTING"])
        self.assertEqual(sorted(name for kind, name in found if kind == "seed.folder"),
                         ["five", "four", "one", "two", "two/y"])
        self.assertEqual(sorted(name for kind, name in found if kind == "seed.file"), ["five/z.json", "one/x.yaml"])
        self.assertEqual(unread, {("b.py", "FOLDERS = BASE + ('four', 'five/z.json')")})

    def test_a_read_or_a_path_that_no_scan_resolves_is_reported_with_its_module(self):
        self.folder()
        self.write("seeder/mod.py", '''
import os
from common import files, has_seed, seed_file, setting
FOLDERS = ("aim/models",)
def go(kind, name):
    setting(name); os.getenv("A_" + kind); os.environ[kind["key"]]; os.environ.get("RESOLVED")
    files("aim", kind); seed_file(name); has_seed("authz", *FOLDERS); has_seed(*kind); files("fine")
    return SEED_DIR / "aim" / name
''')
        self.write("seeder/sub/more.py", 'import os\nos.getenv(os.sep)\n')
        _, unread = scan_seed(self.root / "seeder")
        self.assertEqual(unread, {("mod.py", "setting(name)"), ("mod.py", "os.getenv('A_' + kind)"),
                                  ("mod.py", "os.environ[kind['key']]"), ("mod.py", "files('aim', kind)"),
                                  ("mod.py", "seed_file(name)"), ("mod.py", "has_seed(*kind)"),
                                  ("mod.py", "SEED_DIR / 'aim' / name"), ("sub/more.py", "os.getenv(os.sep)")})

    def test_a_seeder_module_that_does_not_parse_names_its_file(self):
        self.folder(files={"seeder/broken.py": "def (:\n"})
        with self.assertRaises(SyntaxError) as caught:
            seed_items(self.root / "seeder")
        self.assertIn("broken.py", str(caught.exception))

    def test_the_labels_the_lab_reads_and_the_core_carries_are_listed_with_the_console_pattern(self):
        self.folder(files={
            "lab_ui/a.py": 'ROLE = "io.gravitee.role"\nSAY = "the label io.gravitee.description is missing"\n'
                           'PREFIX = "io.gravitee.console."\nOTHER = "com.docker.compose.project"\n',
            "lab_ui/sub/b.py": 'x = "io.gravitee.console.portal.legacy"\ny = "io.gravitee.license"\n'})
        config = {"services": {"s": {"labels": {"io.gravitee.name": "S", "io.gravitee.console.gamma.url": "http://x",
                                                "other": "1"}}}}
        self.assertEqual(sorted(name for _, name in label_items(self.root / "lab_ui", config)),
                         ["io.gravitee.console.<id>.legacy", "io.gravitee.console.<id>.url", "io.gravitee.license",
                          "io.gravitee.name", "io.gravitee.role"])

    def test_the_console_labels_are_the_ones_the_lab_ui_pattern_reads_even_when_no_service_carries_them(self):
        self.folder(files={"lab_ui/a.py": 'ROLE = "io.gravitee.role"\n'})
        listed = label_items(self.root / "lab_ui", {"services": {}}, project.CONSOLE_LABEL)
        self.assertEqual(sorted(name for _, name in listed),
                         ["io.gravitee.console.<id>.legacy", "io.gravitee.console.<id>.name",
                          "io.gravitee.console.<id>.url", "io.gravitee.role"])
        self.assertEqual(sorted(console_labels(re.compile(r"io\.gravitee\.console\.([a-z0-9-]+)\.(name|extra)"))),
                         [("label", "io.gravitee.console.<id>.extra"), ("label", "io.gravitee.console.<id>.name")])
        for pattern in (r"io\.gravitee\.console\.([a-z0-9-]+)\.name", r"io\.gravitee\.consoles\.(a|b)", r"(name|url)"):
            with self.subTest(pattern=pattern), self.assertRaises(ValueError) as caught:
                console_labels(re.compile(pattern))
            self.assertIn("CONSOLE_LABEL", str(caught.exception))

    def test_the_frame_messages_are_the_names_in_quotes_in_the_scripts(self):
        self.folder(files={
            "web/js/a.js": 'const A = "gravitee-lab:action"; // `gravitee-lab:ready` is said when it listens\n'
                           'const B = \'gravitee-lab:open\'; const C = "gravitee-lab: not one";\n'
                           'const D = "other-lab:x"; const url = "gravitee-io-labs/gravitee-lab";\n',
            "ui/location.js": 'post({ type: "gravitee-lab:location", url });\n'})
        self.assertEqual(sorted(name for _, name in frame_items([self.root / "web", self.root / "ui"])),
                         ["gravitee-lab:action", "gravitee-lab:location", "gravitee-lab:open", "gravitee-lab:ready"])

    def test_a_name_the_lock_cannot_hold_is_refused(self):
        for name in ('a"b', "a\nb", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                listing({"services": {}, "x-gravitee": {"categories": [name]}}, [])

    def test_the_keys_come_with_the_kind_that_reads_them(self):
        self.assertEqual(key_items({"name", "gateway"}, {"gateway": "addon"}, {"images"}),
                         {("unit.key", "x-gravitee.name"): "kinds=any", ("unit.key", "x-gravitee.gateway"): "kinds=addon",
                          ("unit.key", "x-gravitee.requires.images"): "kinds=any"})

    def test_the_core_services_say_their_container_ports_healthcheck_and_networks(self):
        config = {"services": {
            "gateway": {"ports": [{"target": 8443}, {"target": 8082, "published": "8082"}, {"target": 8082}],
                        "healthcheck": {"test": ["CMD"]}, "networks": ["internal", "edge"]},
            "worker": {"healthcheck": {"disable": True}}},
            "networks": {"edge": {}, "internal": {}}, "x-gravitee": {"categories": ["AI", "Gravitee Platform"]}}
        found = core_items(config, ["INTERNAL_API_PASSWORD", "APIM_VERSION", "AE_REGISTRY"])
        self.assertEqual(found[("core.service", "gateway")], "port=8082,8443 health=yes networks=edge,internal")
        self.assertEqual(found[("core.service", "worker")], "health=no")
        self.assertEqual({key for key in found if key[0] != "core.service"},
                         {("core.network", "edge"), ("core.network", "internal"), ("core.variable", "INTERNAL_API_PASSWORD"),
                          ("core.image_variable", "APIM_VERSION"), ("core.image_variable", "AE_REGISTRY"),
                          ("core.category", "AI"), ("core.category", "Gravitee Platform")})

    def test_the_anchors_are_the_seeder_the_plugin_folders_and_the_origin_of_the_lab_ui(self):
        self.folder(files={"seeder/Dockerfile": "FROM scratch\n"})
        config = {"x-gravitee": {"seeder": {"image": "ghcr.io/o/r/seeder"}}, "services": {
            "a": {"volumes": [{"type": "bind", "source": str(self.root / "plugins/apim"), "target": "/p"},
                              {"type": "bind", "source": str(self.root / "certs/mtls/ca.pem"), "target": "/c"},
                              {"type": "volume", "source": "plugins/other", "target": "/d"}, "./plugins/am:/q"]},
            "b": {"volumes": [{"type": "bind", "source": str(self.root / "plugins/apim"), "target": "/p"},
                              {"type": "bind", "source": str(self.root / "plugins/am"), "target": "/p"}]}}}
        found = anchor_items(config, self.root, ("http://localhost:9000", "http://127.0.0.1:9000"))
        self.assertEqual(found, {("anchor", "seeder"): "", ("anchor", "plugins/apim"): "", ("anchor", "plugins/am"): "",
                                 ("anchor", "lab-ui"): "port=9000 hosts=127.0.0.1,localhost"})
        del config["x-gravitee"]["seeder"]
        self.assertNotIn(("anchor", "seeder"), anchor_items(config, self.root, ()), "the core must name the image")

    def test_a_link_in_plugins_or_on_the_way_to_the_repository_does_not_hide_an_anchor(self):
        """Compose gives a bind source unresolved: `plugins/apim` may be a link to wherever the machine keeps the plugins."""
        self.folder()
        elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere.cleanup)
        outside = Path(elsewhere.name).resolve()
        (outside / "apim").mkdir()
        (self.root / "plugins").mkdir()
        try:
            os.symlink(outside / "apim", self.root / "plugins/apim")
            os.symlink(self.root, outside / "repo")
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"no symbolic links here: {error}")

        def config(*sources):
            return {"services": {"a": {"volumes": [{"type": "bind", "source": str(source), "target": "/p"}
                                                   for source in sources]}}}
        found = anchor_items(config(self.root / "plugins/apim", self.root / "plugins/../plugins/am/", self.root / "plugins/x"),
                             self.root, ())
        self.assertEqual(set(found), {("anchor", "plugins/apim"), ("anchor", "plugins/am"), ("anchor", "plugins/x")})
        # the root as a link names, the source as the disk has it, and the other way round
        self.assertEqual(set(anchor_items(config(self.root / "plugins/apim"), outside / "repo", ())),
                         {("anchor", "plugins/apim")})
        self.assertEqual(set(anchor_items(config(outside / "repo/plugins/apim"), outside / "repo", ())),
                         {("anchor", "plugins/apim")})
        # a source that only passes through plugins/ on its way elsewhere, or is a sibling, is no anchor
        self.assertEqual(set(anchor_items(config(self.root / "plugins/apim/../../certs", self.root / "plugins",
                                                 self.root / "plugins-other/apim", outside / "apim"), self.root, ())), set())


class Core(Case):
    """The core file, as Compose reads it."""

    def test_the_levels_of_the_core_are_whole_numbers_from_one(self):
        self.assertEqual(core_levels({"x-gravitee": {"contract": 3, "contract_min": 2}}), (3, 2))
        for meta in ({}, {"contract": 1}, {"contract": 1, "contract_min": True}, {"contract": 0, "contract_min": 1},
                     {"contract": "1", "contract_min": 1}, {"contract": 1.0, "contract_min": 1}):
            with self.subTest(meta=meta), self.assertRaises(ValueError):
                core_levels({"x-gravitee": meta})


@unittest.skipUnless(compose_ready(), "Docker Compose 5 is not installed")
class Live(Case):
    """The lock against the files of this repository."""

    @classmethod
    def setUpClass(cls):
        cls.config = compose.raw(CORE_FILE)
        cls.contract, cls.floor = core_levels(cls.config)
        cls.text = (ROOT / LOCK).read_text(encoding="utf-8")

    def said(self):
        return verdict(self.text, listing(self.config), self.contract, self.floor)

    def test_the_lock_equals_the_listing_the_live_files_state(self):
        said = self.said()
        if said:
            self.fail("\n" + "\n".join(said))

    def test_the_command_of_the_docstring_writes_the_lock_that_is_there(self):
        text, stops = plan(self.text, listing(self.config), self.contract, self.floor)
        self.assertEqual((stops, text), ([], self.text))

    def test_the_core_names_its_levels_and_its_seeder_image(self):
        meta = self.config["x-gravitee"]
        self.assertRegex(meta["seeder"]["image"], r"^[a-z0-9.-]+(/[a-z0-9._-]+)+$", "a repository, without a tag")
        self.assertTrue((ROOT / "seeder/Dockerfile").is_file(), "the image is built from ./seeder")
        self.assertIn(("anchor", "seeder"), listing(self.config))

    def test_the_core_still_passes_check(self):
        self.assertEqual([message for where, message in check.problems() if where == CORE_FILE], [])

    def test_removing_a_listed_item_asks_for_contract_min(self):
        self.patch(check, "META", check.META - {"tested"})
        said = self.said()
        self.assertIn("  - unit.key x-gravitee.tested kinds=playground", said)
        self.assertIn("Removed or changed items need `contract_min` raised.", said)
        self.assertIn("Add an entry under Removed in docs/contract.md that names each of them between backticks.", said)

    def test_adding_an_item_asks_for_contract_and_its_level(self):
        self.patch(check, "REQUIRES", check.REQUIRES | {"memory"})
        said = self.said()
        self.assertIn("  + unit.key x-gravitee.requires.memory kinds=any", said)
        self.assertIn(f"Added items need `contract` raised to {self.contract + 1} and `since={self.contract + 1}` written.",
                      said)

    def test_a_core_service_that_goes_or_changes_in_any_way_is_a_removal(self):
        config = {**self.config, "services": {name: service for name, service in self.config["services"].items()
                                              if name != "redis"}}
        self.assertIn("  - core.service redis port=6379 health=yes networks=internal",
                      verdict(self.text, listing(config), self.contract, self.floor))
        moved = {**self.config, "services": {**self.config["services"], "redis": {
            **self.config["services"]["redis"], "ports": [{"target": 6380}]}}}
        self.assertTrue(any(one.startswith("  ~ core.service redis port=6379 ")
                            for one in verdict(self.text, listing(moved), self.contract, self.floor)))
        for name, change in (("alert-engine", {"networks": {**self.config["services"]["alert-engine"]["networks"],
                                                           "edge": None}}),
                             ("redis", {"ports": [{"target": 6379}, {"target": 6380}]}),
                             ("redis", {"healthcheck": {"disable": True}})):
            with self.subTest(name=name, change=sorted(change)):
                grown = {**self.config, "services": {**self.config["services"],
                                                     name: {**self.config["services"][name], **change}}}
                said = verdict(self.text, listing(grown), self.contract, self.floor)
                self.assertTrue(any(one.startswith(f"  ~ core.service {name} ") for one in said), "growing is a change too")
                self.assertIn("Removed or changed items need `contract_min` raised.", said)

    def test_a_console_label_that_no_service_carries_any_more_is_still_listed(self):
        """The Lab UI reads `legacy` whatever the core carries: the pattern it reads is what lists a console label."""
        bare = {**self.config, "services": {name: {**service, "labels": {
            key: value for key, value in (service.get("labels") or {}).items() if not key.startswith("io.gravitee.console.")}}
            for name, service in self.config["services"].items()}}
        found = listing(bare)
        for part in ("name", "url", "legacy"):
            self.assertIn(("label", f"io.gravitee.console.<id>.{part}"), found)
        self.assertEqual(verdict(self.text, found, self.contract, self.floor), [], "the lock does not move")

    def test_the_seeder_reads_that_no_scan_resolves_are_the_ones_the_table_gives_a_reason_for(self):
        unread = scan_seed(ROOT / "seeder")[1]
        new = sorted(unread - set(UNREAD))
        if new:
            self.fail("A read or a path of the seeder that no scan resolves is not listed in the lock. Name the setting or "
                      "the folder as a literal, or decide here, with its reason, in UNREAD:\n  "
                      + "\n  ".join(f"{module}: {source}" for module, source in new))

    def test_no_row_of_the_tables_outlives_its_source(self):
        """Not a failure, so that landing a source does not redden a package that does not own this file: it says which
        rows to delete. A row left behind hides the removal of its item."""
        states = stated(self.config)
        landed = sorted(f"LANDING {kind} {name}" for kind, name in LANDING if (kind, name) in states)
        gone = sorted(f"UNREAD {module}: {source}" for module, source in UNREAD
                      if (module, source) not in scan_seed(ROOT / "seeder")[1])
        if landed or gone:
            self.skipTest("delete these rows of tests/test_contract.py, the files state them now:\n  " + "\n  ".join(landed + gone))

    def test_a_label_a_seed_setting_and_a_frame_message_are_listed(self):
        found = listing(self.config)
        for key in (("label", "io.gravitee.role"), ("label", "io.gravitee.console.<id>.url"),
                    ("seed.setting", "APIM_MANAGEMENT_URL"), ("seed.setting", "SEED_PROTECTIONS"),
                    ("seed.folder", "aim/models"), ("seed.file", "controls.yaml"), ("frame.message", "gravitee-lab:action"),
                    ("frame.message", "gravitee-lab:location"), ("core.service", "apim-gateway"),
                    ("core.variable", "INTERNAL_API_PASSWORD"), ("core.image_variable", "APIM_VERSION"),
                    ("core.network", "edge"), ("core.category", "Gravitee Platform"), ("anchor", "plugins/apim")):
            self.assertIn(key, found)
        self.assertEqual(found[("core.service", "apim-gateway")], "port=8082,8443 health=yes networks=edge,internal")


class Level(Case):
    """The lock as it stands, read as text: no Docker needed."""

    @classmethod
    def setUpClass(cls):
        cls.contract, cls.floor, cls.rows = parse((ROOT / LOCK).read_text(encoding="utf-8"))

    def test_the_lock_is_in_the_form_the_command_writes(self):
        text = (ROOT / LOCK).read_bytes().decode("utf-8")
        self.assertEqual(render(self.contract, self.floor, self.rows), text, "sorted, LF, one newline at the end")

    def test_no_item_is_newer_than_the_lock_or_older_than_level_one(self):
        self.assertTrue(1 <= self.floor <= self.contract)
        self.assertTrue(all(1 <= since <= self.contract for _, since in self.rows.values()))

    def test_the_work_in_flight_is_in_level_one(self):
        for key in (("unit.key", "x-gravitee.terminal"), ("unit.key", "x-gravitee.host_access"),
                    ("unit.key", "x-gravitee.requires.lab"), ("guide.block", "run"), ("seed.folder", "environments"),
                    ("seed.folder", "k8s")):
            with self.subTest(key=key):
                self.assertEqual(self.rows[key][1], 1)

    def test_the_keys_of_catalog_yml_are_of_the_format_of_level_one(self):
        self.assertEqual({since for (kind, _), (_, since) in self.rows.items() if kind == "catalog.key"}, {FORMAT_LEVEL})

    def test_the_keys_of_catalog_yml_listed_for_the_first_time_take_the_level_of_the_format_and_a_new_one_raises_contract(self):
        text = "contract 1\ncontract_min 1\ncore.network edge since=1\n"
        live = {("core.network", "edge"): "", ("catalog.key", "x-gravitee-catalog.id"): ""}
        written, stops = plan(text, live, 1, 1)
        self.assertEqual((stops, parse(written)[2][("catalog.key", "x-gravitee-catalog.id")][1]), ([], 1),
                         "read since the split: no addition")
        later = {**live, ("catalog.key", "x-gravitee-catalog.badge"): ""}
        self.assertEqual(plan(written, later, 1, 1)[1], ["Added items need `contract` raised to 2 in docker-compose.yml first"])
        self.assertEqual(parse(plan(written, later, 2, 1)[0])[2][("catalog.key", "x-gravitee-catalog.badge")][1], 2)

    def test_a_key_is_read_by_one_kind_or_all(self):
        for (kind, name), (attributes, _) in self.rows.items():
            if kind == "unit.key":
                self.assertRegex(attributes, r"^kinds=(any|addon|setup|playground)$", name)

    def test_every_item_is_documented(self):
        doc = (ROOT / DOC).read_text(encoding="utf-8")
        missing = [f"{kind} {name}" for kind, name in sorted(self.rows, key=_order) if f"`{name}`" not in doc]
        if missing:
            self.fail(f"{DOC} must name each of these between backticks:\n  " + "\n  ".join(missing))

    def test_the_page_says_how_the_levels_move_and_what_was_removed(self):
        doc = (ROOT / DOC).read_text(encoding="utf-8")
        headings = re.findall(r"^#{2,3} (.+)$", doc, re.M)
        for heading in ("Levels", "Raising contract", "Raising contract_min", "Removed"):
            self.assertIn(heading, headings)
        if self.floor > 1:
            self.assertRegex(removed_section(doc), r"(?m)^[-|] ", f"contract_min is {self.floor}: Removed lists what left")

    def test_the_page_links_to_no_folder_that_leaves_the_engine(self):
        """The add-ons, setups and playgrounds go to a catalog: their rules are in docs/units.md."""
        doc = (ROOT / DOC).read_text(encoding="utf-8")
        leaving = [link for link in re.findall(r"\]\(([^)\s]+)\)", doc)
                   if re.match(r"(?:\.\./|/)?(?:addons|setups|playgrounds)(?:/|$)", link)]
        self.assertEqual(leaving, [], f"{DOC} points to what the engine no longer holds")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
