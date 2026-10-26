"""What this Lab understands of a catalog: the items of the contract (`contract.lock`, at the root of the engine) that the files of
a catalog use, and those among them that this Lab does not list. A Lab uses what it understands, and says when it needs a newer
version: a published catalog (a git catalog the person added) that uses anything this Lab does not list is not used, and shown
as needing a newer Gravitee Lab (`project._judge_units`, `manifest.pick`), while `catalog.py check` names each such item as a
mistake of its author ([the contract](../../../docs/contract.md)).

What the lock lists of each kind is closed, so a name that is not there is something this Lab does not read: a key of
`x-gravitee` or of its `requires`, an `io.gravitee` label, a core service or network that a unit names and does not define, a
category, a place of the engine a unit builds or mounts (an anchor), a folder or a file of a seed where the lock lists the
entries (the top of `seed/`, and a folder whose entries it names: `apim/` has `apis` and `settings.yaml`, while what is in
`apim/apis/` is data), and a setting of the seeder's run (`SEED_*`). The other settings of a seed service are what its seed
files read, and the settings of the core are open: neither says anything of the Lab that reads them.

`lock` reads the file once for each of its versions. `items` is pure but for the seed folder it walks and the paths it resolves;
`unknown` and `named` are pure. With no lock (a Lab whose file is lost, or a test that has none), nothing is judged unknown:
`make check` says the file is missing."""

import os
import posixpath
import re
import threading
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

from .. import paths

LOCK_FILE = "contract.lock"   # what the engine promises units, with the level each item appeared at
_TOKEN = re.compile(r'"[^"]*"|\S+')
_SINCE = re.compile(r"since=(\d+)")
# A console label, whatever its id and its attribute: the lock lists them with <id> for the id
CONSOLE = re.compile(r"io\.gravitee\.console\.[a-z0-9-]+\.([A-Za-z0-9_-]+)")
# The keys that were read once and are not any more, and why: no sign of a newer Lab, and the check tells the file so
GONE = {"deploys_to": "compatibility with a platform is computed"}
SEED_RUN = "SEED_"   # the settings of the seeder's run start with it: one that the lock does not list is a newer seeder's
SEED_ENTRIES = 5000   # as many entries of a seed folder as the screen reads
SHOWN = 3   # the items a line spells out before it counts the rest
# How an item of the lock is named in a line: the keys, the labels and the anchors by their own name, the others by what they are
ITEM = {"core.service": "core service {}", "core.network": "network {}", "core.variable": "setting {}",
        "core.image_variable": "setting {}", "core.category": "category {}", "seed.folder": "seed folder {}",
        "seed.file": "seed file {}", "seed.setting": "seed setting {}", "guide.block": "{} block of a guide",
        "label": "label {}", "anchor": "anchor {}", "catalog.key": "catalog.yml key {}"}

Item = Tuple[str, str]


class Lock(NamedTuple):
    """`contract.lock` as this Lab reads it: its two levels and {(kind, name): the level it appeared at}."""
    contract: int
    contract_min: int
    rows: Dict[Item, int]


_cache = {"key": None, "lock": None}
_cache_lock = threading.Lock()


def parse(text: str) -> Optional[Lock]:
    """The lock of a text, or None when it does not say its two levels."""
    levels, rows = {}, {}
    for row in text.splitlines():
        words = _TOKEN.findall(row)
        if len(words) == 2 and words[0] in ("contract", "contract_min") and words[1].isdigit():
            levels[words[0]] = int(words[1])
        elif len(words) >= 3 and _SINCE.fullmatch(words[-1]):
            rows[(words[0], words[1].strip('"'))] = int(_SINCE.fullmatch(words[-1]).group(1))
    return Lock(levels["contract"], levels["contract_min"], rows) if len(levels) == 2 else None


def lock(root: Optional[Path] = None) -> Optional[Lock]:
    """The lock of the engine at `root` (the project's by default), or None when the file is not there or not the engine's.
    Read again only when the file changed."""
    file = Path(root or paths.ROOT) / LOCK_FILE
    try:
        info = file.stat()
    except OSError:
        return None
    key = (str(file), info.st_mtime_ns, info.st_size)
    with _cache_lock:
        if _cache["key"] == key:
            return _cache["lock"]
    try:
        found = parse(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        found = None
    with _cache_lock:
        _cache.update(key=key, lock=found)
    return found


def fingerprint(root: Optional[Path] = None) -> str:
    """What a memory of a verdict is valid for: the lock as it is now (a Lab that is updated understands more)."""
    found = lock(root)
    return "" if found is None else f"{found.contract}:{found.contract_min}:{len(found.rows)}:{hash(frozenset(found.rows))}"


def label_of(key: str) -> str:
    """The item of the contract that a label stands for: the console labels are listed with their id as <id>."""
    found = CONSOLE.fullmatch(key)
    return f"io.gravitee.console.<id>.{found.group(1)}" if found else key


def seed_names(folder) -> Set[Item]:
    """The folders and files of the seed of a playground's `folder` as the contract names them, relative to `seed/`, to two
    levels of folders, hidden ones left out."""
    base, found = Path(folder) / "seed", set()
    if os.path.islink(base) or not base.is_dir():
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


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _names(value) -> List[str]:
    """The names of a list or of the keys of a mapping (depends_on, networks), as text."""
    if isinstance(value, dict):
        return [str(name) for name in value]
    return [str(name) for name in value] if isinstance(value, list) else []


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _anchor(source, root: str, lab: str) -> Optional[str]:
    """The place of the engine that a build context or a bind source names (`seeder`, `plugins/apim`, or the path of another),
    None for a path that is not in the engine: in the unit's folder, in a catalog, or outside the project."""
    if not isinstance(source, str) or not os.path.isabs(source) or "${" in source:   # Compose gives a bind's path whole
        return None
    real = os.path.realpath(source)
    if not _within(real, root) or _within(real, lab):
        return None
    parts = Path(os.path.relpath(real, root)).as_posix().split("/")
    return "/".join(parts[:2]) if parts[0] == "plugins" and len(parts) > 1 else parts[0]


def items(config: dict, folder=None, kind: str = "", root: Optional[Path] = None) -> Set[Item]:
    """The items of the contract that a unit uses, from its compose file as `compose.raw` reads it (what it includes merged in):
    the keys of its x-gravitee block and of its `requires`, its category, the `io.gravitee` labels of its services, the networks
    and the core services it names and does not define, the places of the engine it builds or mounts, and, for a playground,
    the folders and files of its seed (`folder` is the playground's) and the settings of its seed service."""
    config = _mapping(config)
    meta = _mapping(config.get("x-gravitee"))
    found = {("unit.key", f"x-gravitee.{key}") for key in meta}
    found |= {("unit.key", f"x-gravitee.requires.{key}") for key in _mapping(meta.get("requires"))}
    if isinstance(meta.get("category"), str) and meta["category"]:
        found.add(("core.category", meta["category"]))
    services = _mapping(config.get("services"))
    declared = set(_names(config.get("networks")))
    project_root = os.path.realpath(str(root or paths.ROOT))
    lab = os.path.join(project_root, paths.LAB_DIR)
    named = [name for name in [*_names(meta.get("consoles")), meta.get("terminal")] if isinstance(name, str)]
    for name, service in services.items():
        service = _mapping(service)
        if not service.get("image") and not service.get("build"):   # it adds to a service it does not define: the core's
            found.add(("core.service", str(name)))
        named += _names(service.get("depends_on"))
        found |= {("core.network", net) for net in _names(service.get("networks")) if net not in declared}
        labels = _mapping(service.get("labels"))
        found |= {("label", label_of(str(key))) for key in labels if str(key).startswith("io.gravitee.")}
        build = service.get("build")
        context = build if isinstance(build, str) else _mapping(build).get("context")
        sources = [context] + [_mapping(volume).get("source") for volume in (service.get("volumes") or [])
                               if _mapping(volume).get("type", "bind") == "bind"]
        found |= {("anchor", place) for place in (_anchor(source, project_root, lab) for source in sources) if place}
        if labels.get("io.gravitee.role") == "seed":
            found |= {("seed.setting", str(key)) for key in _mapping(service.get("environment"))}
    found |= {("core.service", name) for name in named if name not in services}
    if kind == "playground" and folder is not None:
        found |= seed_names(folder)
    return found


def _parents(rows: Iterable[Item]) -> Set[str]:
    """The folders of a seed whose entries the lock names: the top of `seed/` ("") and every folder above a listed entry."""
    return {posixpath.dirname(name) for kind, name in rows if kind in ("seed.folder", "seed.file")}


def unknown(found: Iterable[Item], held: Optional[Lock]) -> List[Item]:
    """The items of `found` that this Lab does not read, sorted: those of a closed kind that the lock does not list. None of
    them with no lock."""
    if held is None:
        return []
    rows, parents, out = held.rows, _parents(held.rows), set()
    for kind, name in found:
        if (kind, name) in rows:
            continue
        if kind == "unit.key" and name.split(".")[-1] in GONE and name.count(".") == 1:
            continue
        if kind in ("unit.key", "label", "core.service", "core.network", "core.category", "anchor") \
                or kind in ("seed.folder", "seed.file") and posixpath.dirname(name) in parents \
                or kind == "seed.setting" and name.startswith(SEED_RUN):
            out.add((kind, name))
    return sorted(out)


def name(item: Item) -> str:
    """How a line names an item: a key by itself, the others by what they are ("label io.gravitee.badge")."""
    kind, value = item
    return ITEM.get(kind, "{}").format(value)


def named(found: Iterable[Item]) -> str:
    """The items of a line, the first SHOWN spelled out: "x-gravitee.badge, label io.gravitee.badge and 2 more"."""
    said = sorted(name(one) for one in found)
    shown = ", ".join(said[:SHOWN])
    return shown + (f" and {len(said) - SHOWN} more" if len(said) > SHOWN else "")


def of_unit(config: dict, folder=None, kind: str = "", held: Optional[Lock] = None, root: Optional[Path] = None) -> List[Item]:
    """What a unit uses that this Lab does not read (`items`, then `unknown` against the lock of this Lab)."""
    held = lock(root) if held is None else held
    return unknown(items(config, folder, kind, root), held) if held is not None else []
