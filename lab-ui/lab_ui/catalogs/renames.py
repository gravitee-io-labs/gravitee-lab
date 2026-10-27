"""A catalog renames an item, and everyone who has it keeps everything: the `renamed` block of catalog.yml, read and followed.

A catalog declares, in `x-gravitee-catalog.renamed`, each name it ever gave up for another: `items` ({kind:old: kind:new})
and `settings` ({OLD: NEW}). It keeps every pair, so that a Lab that skipped the release that renamed reads it in the newest
one, and a name renamed again (a to b, then b to c) is a chain that ends where the catalog is now.

    resolve(earlier, later, back)  pure: {part: {old: new}} between two versions' declarations; `back` for Go back
    between(store, frm, to, back)  the same between two commits of a catalog's store
    follow(ns, found)              on disk, for the units the install has now: what was the person's under the old names is
                                   theirs under the new ones

What follows an item: the machine files of its folder (`.env`, `secrets/`, `.state/`, keys, whatever a container wrote), moved
by `moves.relocate`, which never overwrites (an entry whose place is taken is kept aside in `.lab/migration/kept/<ns>/`, and
one that cannot be moved whole stops the operation: `stuck`); its
line in the on file, and the copy written for a platform it runs on; the includes of it in the items of this machine and of the
imported playgrounds; `copy_of` in the person's copies of it; its star and its tested sets (`moves.rekey`); the settings a share
renamed for it (`contribute.RENAMES`); the settings that wait for it in `.lab/held/`; and the copies written for a platform of
the items of this machine that include it. The settings of `settings` are renamed in the `.env` of every item of the catalog,
values kept, and never over a setting that is set under the new name, and so are those that wait for them in `.lab/held/`. An
update that renames an item waits while the item has a container (`service._plan`): nothing is moved under a container, and
none is left that the files no longer name.

Idempotent and judged by the disk: a run that stopped is run again by the operation that ran it (an Update or a Go back is
finished by `recover()`), and running it twice changes nothing more. Nothing here runs Docker or git, but for the read of a
catalog.yml from the store (`between`). It never asks the project for anything: it runs under the project lock."""

import json
import os
import posixpath
import re
import stat
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from ..docker import compose
from ..library import moves
from .. import paths
from . import manifest, registry
from . import store as bare
from .registry import CatalogError

PARTS = manifest.PARTS["renamed"]
NONE = {part: {} for part in PARTS}
KEPT = f"{moves.MIGRATION}/kept"   # where an entry whose place was taken is kept, by catalog, kind and old name
SHARED = f"{paths.LAB_DIR}/renames.json"   # contribute.RENAMES: what a share renamed, by the item as the catalog holds it
# A setting's line in a .env, as `contribute._rename_settings` reads it: the name, after `export` and spaces
_LINE = r"(?m)^(\s*(?:export\s+)?){}(?==)"
_COPY_OF = r"""(copy_of:\s*)(["']?){}\2(?=\s*(?:[,}}#]|$))"""

_read = {}   # (the reader, the bytes of a catalog.yml) -> its declarations: a file is read with Compose once


# ---------------------------------------------------------------------------------------------------------------------
# The declarations, and where each name goes
# ---------------------------------------------------------------------------------------------------------------------

def _end(mapping, name):
    """Where `name` ends when it is renamed again and again; a cycle (which the check refuses) ends where it would repeat."""
    seen = set()
    while name in mapping and name not in seen:
        seen.add(name)
        name = mapping[name]
    return name


def _steps(mapping, name):
    """How many renames `name` goes through to the end of its chain."""
    seen = set()
    while name in mapping and name not in seen:
        seen.add(name)
        name = mapping[name]
    return len(seen)


def _resolved(mapping):
    """{old: the name it ends at} of a part, the pairs that end where they start left out, the names nearest the end first: a
    .env that holds two names of one chain gives the last name the value of the newer one (`rename_settings`)."""
    olds = sorted((old for old in mapping if isinstance(old, str)), key=lambda old: _steps(mapping, old))
    return {old: _end(mapping, old) for old in olds if _end(mapping, old) != old}


def part(earlier: dict, later: dict, back: bool = False) -> Dict[str, str]:
    """{old: new} of one part of `renamed`, for a machine that goes from the version that declares `earlier` to the one that
    declares `later`. Forward, each old name of `later` goes where its chain ends. Going back (`back`), a name that `earlier`
    ended a chain at and `later` does not rename goes to the one name `later` gives what `earlier` renamed to it (a, which
    `earlier` renamed to b, is a again, or b when `later` renamed a to b); a name with two such places, or none, stays. Pure."""
    later_ends, found = _resolved(later or {}), {}
    found.update(later_ends)
    if back:
        earlier_ends = _resolved(earlier or {})
        for name in sorted(set(earlier_ends.values()) - set(later_ends)):
            places = {later_ends.get(old, old) for old, end in earlier_ends.items() if end == name} - {name}
            if len(places) == 1:
                found[name] = places.pop()
    return found


def resolve(earlier: Optional[dict], later: Optional[dict], back: bool = False) -> Dict[str, Dict[str, str]]:
    """{part: {old: new}} of the parts (`part`), from two `renamed` blocks as `manifest.normalize` gives them."""
    earlier, later = earlier or NONE, later or NONE
    return {name: part(earlier.get(name) or {}, later.get(name) or {}, back) for name in PARTS}


def declared(meta: Optional[dict]) -> dict:
    """The `renamed` block of a catalog.yml as read (`manifest.read_catalog`): each part a mapping, empty when it has none."""
    found = (meta or {}).get("renamed") or {}
    return {part: dict(found.get(part) or {}) for part in PARTS}


UNREAD = "What this version renamed could not be read, so nothing was changed. Fix: try again in a moment."
STUCK = ("Some files of {name} could not be moved to its new folder: they belong to another user, or are on another disk. "
         "Fix: make them yours again, click Repair, then Update again.")


def _read_at(store, commit: Optional[str]) -> dict:
    """{renamed, moved_from} of the catalog.yml of `commit` in a store, read with Compose as an Add reads it; nothing for a file
    that does not mention `renamed` (no Compose is asked then: there is nothing to follow) or that is not there. A file that
    does mention it and does not read now (Compose busy past its time, the CLI gone) is `renames_unread`, which the operation
    that asked is refused with before it changes anything, and which `recover()` takes for a moment's trouble: it tries again
    at the next start. Never taken for a version that renames nothing: that would leave what it renamed under the old names."""
    data = bare.read(store, commit, manifest.FILE) if commit else None
    if not data or b"renamed" not in data:
        return {"renamed": declared(None), "moved_from": []}
    key = (manifest.read_catalog, data)
    if key not in _read:
        with tempfile.TemporaryDirectory(dir=compose.scratch()) as folder:
            file = Path(folder) / manifest.FILE
            file.write_bytes(data)
            try:
                meta = manifest.read_catalog(str(file))
            except CatalogError as error:
                raise CatalogError("renames_unread", UNREAD, 503, output=str(error)) from None
            _read[key] = {"renamed": declared(meta), "moved_from": list(meta.get("moved_from") or ())}
    return _read[key]


def declared_at(store, commit: Optional[str]) -> dict:
    """The `renamed` block of the catalog.yml of `commit` in a store (`_read_at`): raises `renames_unread` when it cannot be
    read now."""
    return _read_at(store, commit)["renamed"]


def moved_from_at(store, commit: Optional[str]) -> list:
    """The `moved_from` of the catalog.yml of `commit`, for a run that has no reading of it (`recover()`): [] for a file that
    renames nothing, whose renames need none."""
    return _read_at(store, commit)["moved_from"]


def between(store, frm: Optional[str], to: str, back: bool = False) -> Dict[str, Dict[str, str]]:
    """`resolve` from the version `frm` to the version `to` of a catalog: `back` for Go back, `frm` None for an Add. Read
    from the store, so a run that `recover()` finishes reads what the run it finishes read."""
    return resolve(declared_at(store, frm) if frm and frm != to else None, declared_at(store, to), back)


# ---------------------------------------------------------------------------------------------------------------------
# On disk
# ---------------------------------------------------------------------------------------------------------------------

def _uid(ns, name):
    return name if ns == paths.default_ns() else f"@{ns}/{name}"


def _where(ns, kind, name):
    """An item's folder in its catalog, relative to the project; a setup's file."""
    file = paths.CATALOG_FILES[kind].format(ns=ns, name=name)
    return file if kind == "setup" else posixpath.dirname(file)


def _content(ns) -> set:
    """The paths of the install its manifest lists, and the folders on their way: what is the catalog's, not the machine's."""
    files = set(((registry.manifest(ns) or {}).get("files") or {}))
    return files | {"/".join(path.split("/")[:end]) for path in files for end in range(1, path.count("/") + 1)}


def _kept(ns, kind, name, rel, held=False):
    return paths.ROOT / KEPT / ns / ("held" if held else "") / f"{kind}s" / name / rel


def _aside(ns, kind, away, top, here, there, held=False):
    """An entry of the old folder whose place in the new one is taken: two folders are merged, what has no place is kept aside
    whole (a `.state` or a `secrets` folder is never mixed with another). Answers what is kept aside, relative to the project."""
    kept = []
    if top in moves.WHOLE or not (moves._plain(here) and moves._plain(there)):
        target = _kept(ns, kind, away, top, held)
        if moves.relocate(here, target, "rename-kept") in ("moved", "copied"):
            kept.append(os.path.relpath(target, paths.ROOT))
        return kept
    for found in moves.merge(here, there, "rename-held" if held else "rename"):
        if found["result"] == "conflict":
            target = _kept(ns, kind, away, f"{top}/{found['path']}", held)
            if moves.relocate(os.path.join(here, found["path"]), target, "rename-kept") in ("moved", "copied"):
                kept.append(os.path.relpath(target, paths.ROOT))
    return kept


def _emptied(folder, stop):
    """Removes the folders under and at `folder` that are empty, and its parents up to `stop`, never a folder that holds something."""
    if not moves._plain(folder):
        return
    for here, _, _ in os.walk(folder, topdown=False):
        try:
            os.rmdir(here)
        except OSError:
            continue
    parent = os.path.dirname(folder)
    while parent != stop and parent.startswith(stop + os.sep):
        try:
            os.rmdir(parent)
        except OSError:
            return
        parent = os.path.dirname(parent)


def _machine(ns, kind, away, home, step) -> dict:
    """The machine's entries of the old folder go to the new one (`_carry`), but the files the catalog still puts there."""
    if kind == "setup":
        return {"moved": [], "kept": []}
    root = registry.install_dir(ns)
    source, target = root / f"{kind}s" / away, root / f"{kind}s" / home
    content, base = _content(ns), f"{kind}s/{away}"
    return _carry(ns, kind, away, home, str(source), str(target), str(root), step,
                  skip=lambda top: f"{base}/{top}" in content)


def _carry(ns, kind, away, home, source, target, stop, step, skip=lambda top: False, held=False) -> dict:
    """The entries of the folder `source` go to `target`, each whole and never over what is there: what has no place is kept
    aside (`_aside`). Answers {moved, kept}: the entries moved and what was kept aside. One that cannot be moved whole (a right
    refused, another filesystem, busy) is not counted as moved: it stops the operation (`stuck`), what was copied of it left
    in both places, and so does one of the settings that wait in `.lab/held/` (`held`): the same fix makes both movable. The
    folders emptied go, up to `stop`."""
    found = {"moved": [], "kept": []}
    if not moves._plain(source) or not moves._plain(target) and os.path.lexists(target):
        return found
    for top in sorted(os.listdir(source)):
        if skip(top):
            continue
        here, there = os.path.join(source, top), os.path.join(target, top)
        step("rename:entry")
        answer = moves.relocate(here, there, "rename-held" if held else "rename")
        if answer == "conflict":
            found["kept"] += _aside(ns, kind, away, top, here, there, held)
            _emptied(here, source)   # what a merge moved leaves its folders behind
            answer = "moved" if not os.path.lexists(here) else "stayed"
        if answer not in ("moved", "gone"):
            raise stuck(kind, home)
        found["moved"].append(top)
    _emptied(source, stop)
    return found


def stuck(kind, home):
    """The refusal of a rename that could not move a file to the item `home`: its Repair puts back what moved (the caller names
    the item, `units`)."""
    return CatalogError("rename_stuck", STUCK.format(name="a renamed item"), 409, units=[f"{kind}:{home}"])


def adopts(ns, key, moved_from) -> bool:
    """Whether catalog `ns` takes the settings that wait in `.lab/held/` for its unit `key` (kind:name): every unit of the
    default catalog, and the units of another one that its `moved_from` names. The one test of `install.adopt` and `follow`."""
    return ns == paths.default_ns() or key in set(moved_from or ())

def _held(ns, kind, away, home, moved_from):
    """The settings that wait in `.lab/held/` under the old name wait under the new one, for the catalog that adopts them (the
    default one, or one whose `moved_from` names the item): `install.adopt` gives them to the item once it is there. Answers
    what was kept aside."""
    if kind == "setup" or not adopts(ns, f"{kind}:{home}", moved_from):
        return []
    try:
        old = moves._held_chain(kind, away)
    except moves.Refused:
        return []
    if old is None:
        return []
    return _carry(ns, kind, away, home, str(old), str(moves.held_dir(kind, home)), str(paths.ROOT / moves.HELD), _quiet,
                  held=True)["kept"]


def _write(path: Path, text: str):
    """A file of this machine replaced whole with the mode it has."""
    paths.write_atomic(path, text, mode=stat.S_IMODE(path.stat().st_mode))


def _on_file(old_file, new_file, step):
    """The line of the on file that turns the item on names its new file; a line that runs it on a platform names a copy written
    for the new file, and the old copy goes once nothing names it. Every other line is the person's and stays."""
    file = paths.ROOT / paths.ON_FILE
    try:
        text = file.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return
    lines, gone = [], []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        target = compose.TARGET_ITEM.fullmatch(body)
        if target and compose._original(target.group(1)) == old_file:
            copy, platform = target.group(1), target.group(2)
            fresh = compose.write_target(new_file, platform, compose.target_names(copy))
            body = body[:target.start(3)] + posixpath.dirname(new_file) + body[target.end(3):]
            body = body[:target.start(1)] + fresh + body[target.end(1):]
            gone.append(copy)
        else:
            plain = re.fullmatch(r"(\s*-\s*)([\"']?)([^\s\"'#]+)\2(\s*(?:#.*)?)", body)
            if plain and plain.group(3) == old_file:
                body = f"{plain.group(1)}{plain.group(2)}{new_file}{plain.group(2)}{plain.group(4)}"
        lines.append(body + line[len(line.rstrip("\r\n")):])
    written = "".join(lines)
    if written != text:
        step("rename:on")
        _write(file, written)
        moves.journal("repoint", path=paths.ON_FILE, old=old_file, new=new_file)
    for copy in gone:
        if copy not in written and os.path.lexists(paths.ROOT / copy):
            os.unlink(paths.ROOT / copy)


def _compose_files():
    """The compose files whose includes the Lab follows: those of the items of this machine, and those of the playgrounds
    imported from a file, which are the Lab's own copies. The files of a catalog are its owners', and a linked folder is its
    author's: neither is written."""
    found = list(moves._personal_files())
    for entry in registry.entries():
        if registry.imported(entry["id"]):
            base = paths.ROOT / paths.CATALOGS_DIR / entry["id"]
            found += sorted(base.glob("addons/*/compose.yml")) + sorted(base.glob("playgrounds/*/compose.yml")) \
                + sorted(base.glob("setups/*.yml"))
    return found


def _includes(old, new, step):
    """The includes of the item, and the other paths to it on their own lines, in the files `_compose_files` names, lead to its
    new place (`moves.repoint`). A path to it that is still there after that (a list on one line, a path in a command) is the
    person's to change: the Lab says it as a problem (`layers`). Answers the files written, relative to the project."""
    written_files = []
    for file in _compose_files():
        if file.is_symlink() or not file.is_file():
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        folder = os.path.relpath(file.parent, paths.ROOT).replace(os.sep, "/")
        rel = os.path.relpath(file, paths.ROOT).replace(os.sep, "/")
        written = moves.repoint(text, folder, folder, [(old, new)])
        if written != text:
            step("rename:include")
            _write(file, written)
            moves.journal("repoint", path=rel, old=old, new=new)
            written_files.append(rel)
    return written_files


def _retarget(files, step):
    """The copies written for a platform (`.lab/targets`) of the files whose includes were written again, made again from them:
    an item of this machine that runs on a platform and includes what moved."""
    on = paths.ROOT / paths.ON_FILE
    try:
        text = on.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return
    for line in text.splitlines():
        target = compose.TARGET_ITEM.fullmatch(line)
        if target and compose._original(target.group(1)) in files:
            step("rename:copy")
            compose.write_target(compose._original(target.group(1)), target.group(2), compose.target_names(target.group(1)))


def _copies(old_uid, new_uid, step):
    """The person's copies of the item say whose copy they are by its new id (`x-gravitee.copy_of`)."""
    pattern = re.compile(_COPY_OF.format(re.escape(old_uid)), re.M)
    for file in moves._personal_files():
        if file.is_symlink() or not file.is_file():
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        written = pattern.sub(lambda hit: f"{hit.group(1)}{hit.group(2)}{new_uid}{hit.group(2)}", text)
        if written != text:
            step("rename:copy")
            _write(file, written)
            moves.journal("copy_of", path=os.path.relpath(file, paths.ROOT), old=old_uid, new=new_uid)


def _shared(kind, old_uid, new_uid):
    """What a share renamed for the item, kept until the person uses the catalog's version, is kept under its new id."""
    file = paths.ROOT / SHARED
    try:
        kept = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(kept, dict) or f"{kind}:{old_uid}" not in kept:
        return
    kept.setdefault(f"{kind}:{new_uid}", kept.pop(f"{kind}:{old_uid}"))
    _write(file, json.dumps(kept, indent=2, sort_keys=True) + "\n")


def rename_settings(text: str, settings: Dict[str, str]) -> str:
    """The text of a .env with each setting of `settings` ({old: new}) under its new name, its value and its place kept. A
    setting that is set under its new name already keeps both lines: nothing is written over. Pure."""
    for old, new in settings.items():
        if re.search(_LINE.format(re.escape(new)), text):
            continue
        text = re.sub(_LINE.format(re.escape(old)), lambda hit, new=new: hit.group(1) + new, text)
    return text


def _settings(ns, units, settings, moved_from, step):
    """The settings of `settings` renamed in the .env of every add-on and playground of the catalog, and in the one that waits
    for it in `.lab/held/`, which the catalog adopts after (`adopts`)."""
    if not settings:
        return
    for kind in ("addon", "playground"):
        for name in units.get(kind, ()):
            files = [paths.ROOT / _where(ns, kind, name) / ".env"]
            try:
                held = moves._held_chain(kind, name) if adopts(ns, f"{kind}:{name}", moved_from) else None
            except moves.Refused:
                held = None
            for file in files + ([held / ".env"] if held else []):
                if file.is_symlink() or not file.is_file():
                    continue
                try:
                    text = file.read_text(encoding="utf-8")
                except (OSError, ValueError):
                    continue
                written = rename_settings(text, settings)
                if written != text:
                    step("rename:settings")
                    _write(file, written)
                    moves.journal("rename_settings", path=os.path.relpath(file, paths.ROOT),
                                  names=sorted(old for old in settings if old in text))


def _quiet(label):
    return None


def follow(ns: str, found: Dict[str, Dict[str, str]], moved_from=(), step=_quiet) -> List[dict]:
    """Follows the renames `found` (`resolve`, `between`) on disk for catalog `ns`, as its install is now: an item pair counts
    when the new item is a unit of the install and the old one is not. `moved_from` is the catalog's, for the settings that
    wait in `.lab/held/`; `step` the hook of the operation that runs it (`install.STEP`), called with the label of each change
    before it is made. Answers [{kind, from, to, moved, kept}] for the items followed, in the order of their old ids; an entry
    that cannot be moved whole raises (`stuck`)."""
    units = registry.units_of(ns)
    pairs = []
    for old, new in sorted(found.get("items", {}).items()):
        kind, _, away = old.partition(":")
        same, _, home = new.partition(":")
        if kind == same and kind in paths.KINDS and home in units.get(kind, ()) and away not in units.get(kind, ()):
            pairs.append((kind, away, home))
    done = []
    for kind, away, home in pairs:
        old_uid, new_uid = _uid(ns, away), _uid(ns, home)
        result = _machine(ns, kind, away, home, step)
        result["kept"] += _held(ns, kind, away, home, moved_from)
        _on_file(paths.CATALOG_FILES[kind].format(ns=ns, name=away), paths.CATALOG_FILES[kind].format(ns=ns, name=home), step)
        _retarget(_includes(_where(ns, kind, away), _where(ns, kind, home), step), step)
        _copies(old_uid, new_uid, step)
        moves.rekey({f"{kind}:{old_uid}": new_uid})
        _shared(kind, old_uid, new_uid)
        done.append({"kind": kind, "from": old_uid, "to": new_uid, **result})
    _settings(ns, units, found.get("settings") or {}, moved_from, step)
    return done


def files(ns, renamed) -> Dict[str, str]:
    """{old file: new file} of the items `renamed` ({kind:old: kind:new}) of catalog `ns`, relative to the project."""
    return {paths.CATALOG_FILES[old.partition(":")[0]].format(ns=ns, name=old.partition(":")[2]):
            paths.CATALOG_FILES[new.partition(":")[0]].format(ns=ns, name=new.partition(":")[2]) for old, new in renamed.items()}


def follows(file, moved) -> bool:
    """Whether the compose file `file` (relative to the project) leads to the new places of the files `moved` ({old file: new
    file}) once `follow` wrote it again: a file of this machine or of a playground imported from a file, in which no path to an
    old place is left once `moves.repoint` wrote it. A file of another catalog is its owners' to change, and a path that repoint
    does not reach (an include list on one line, `project_directory:`) still names the old place."""
    found = paths.unit_of(file)
    if not found or not (paths.personal(found[1]) or registry.imported(paths.origin(found[1]))):
        return False
    path = paths.ROOT / file
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return False
    folder, table = posixpath.dirname(file), []
    for old, new in moved.items():
        setup = old.endswith(".yml") and posixpath.basename(posixpath.dirname(old)) == "setups"
        table.append((old, new) if setup else (posixpath.dirname(old), posixpath.dirname(new)))
    written = "\n".join(compose.COMMENT.sub("", line) for line in moves.repoint(text, folder, folder, table).splitlines())
    left = {posixpath.normpath(posixpath.join(folder, one)) for one in moves.UPWARD.findall(written)}
    return not any(target == old or target.startswith(old + "/") for target in left for old, _ in table)
