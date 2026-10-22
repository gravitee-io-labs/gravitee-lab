"""Move to Personal: an item of a playground imported from a file and kept as a catalog of its own (Imported) becomes the person's
own, in .lab/catalogs/local, as if they had made it.

A playground moves alone, or with the setups and add-ons it brings from the same import. Whatever another item still includes
stays where it is, and the item that moved includes it there: an item of a catalog never includes one of Personal, so what is
used by an item that does not move cannot move (`stays`, with whom it is used by). An item that is itself included by one that
does not move does not move at all. What moves takes along its files, its settings, its secrets and what its seed recorded
(the whole folder), its star and its tested sets; the includes of one another follow it, and so do its includes of what stays.
An item of Personal with the same name is replaced (`replaces`), its settings kept where the moving one has none. Nothing moves
while one of the items is on. The import whose items all moved is removed with its catalog.

Personal is the last check, as for an import: nothing judges what is there. An item blocked only for what it takes of this
machine (a privileged container, say; and what needs it) moves once the person allowed it, which the plan says (`uses_machine`)
and the move must be asked with (`allow`); an item blocked for anything else does not move (`held`).

    plan(kind, uid)              {alone, together}: what each choice moves, keeps, replaces, and what holds it
    move(kind, uid, together)    the move: {moved: [{kind, id}], id (the item's id in Personal)}
"""

import posixpath
import shutil
from pathlib import Path

from .. import paths
from ..library import moves, personal as personals, project
from . import registry, resolve, service
from .registry import CatalogError

MACHINE_FILES = (".env", ".state", "secrets")   # what is the person's in an item's folder, never the item's files
# The rules of the policy that only say an item takes more of this machine (class H), and the one that says it needs such an item
HOST_CODES = frozenset({"T03", "T04", "T05", "T06", "T07", "T08", "T09", "T10", "T11"})
NEEDS_BLOCKED = "T25"


def _fail(code, message, status=409):
    return CatalogError(code, message, status)


def _units(found):
    return {"addon": found["addons"], "setup": found["setups"], "playground": found["playgrounds"]}


def _name(units, node):
    unit = (units.get(node[0]) or {}).get(node[1]) or {}
    return unit.get("name") or node[1].rsplit("/", 1)[-1]


def _slug(uid):
    return uid.rsplit("/", 1)[-1]


def _rel(kind, slug):
    """The place of an item in its catalog: its folder, or the file of a setup."""
    return f"setups/{slug}.yml" if kind == "setup" else f"{kind}s/{slug}"


def _choice(found, kind, uid, together):
    """(moving [(kind, id)], stays [(node, user)]): the item first, then what it brings from its import when `together`, less what
    an item that does not move includes, to a fixed point."""
    units, ns = _units(found), paths.origin(uid)
    nodes = [(k, one) for k, found_units in units.items() for one in found_units]
    direct = {node: set(resolve._direct(units, node)) for node in nodes}
    root = (kind, uid)
    candidates = [node for node in resolve.closure(units, kind, uid)[1:] if paths.origin(node[1]) == ns and node in direct] if together else []
    moving, stays = [root, *candidates], []
    changed = True
    while changed:
        changed = False
        for node in moving[1:]:
            user = next((other for other in nodes if other not in moving and node in direct[other]), None)
            if user:
                moving.remove(node)
                stays.append((node, user))
                changed = True
                break
    return moving, stays


def _users_of_root(found, kind, uid):
    units = _units(found)
    root = (kind, uid)
    return [other for k, found_units in units.items() for other in ((k, one) for one in found_units)
            if other != root and root in set(resolve._direct(units, other))]


def _judged(units, moving):
    """([names] of the moving items that take more of this machine, which the person must allow), and why one cannot move at all
    ("X cannot run: reason"), or ""."""
    uses, refused = [], ""
    for node in moving:
        unit = (units.get(node[0]) or {}).get(node[1]) or {}
        verdict = unit.get("verdict") or {}
        codes = set(verdict.get("codes") or ())
        if verdict.get("state") == "blocked" and not codes <= HOST_CODES | {NEEDS_BLOCKED}:
            refused = refused or f"{_name(units, node)} cannot run: {(verdict.get('reasons') or [''])[0]}"
        if codes & HOST_CODES:
            uses.append(_name(units, node))
    return uses, refused


def _held(found, moving):
    """Why the move waits: an item that is on (Turn off X first), or one that cannot run for more than what it takes of this machine."""
    reason = _judged(_units(found), moving)[1]
    if reason:
        return reason
    for kind, uid in moving:
        try:
            personals._not_held(found, kind, uid)
        except RuntimeError as error:
            return str(error)
    return ""


def _replaces(units, moving):
    home = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL
    return [_name(units, node) for node in moving if (home / _rel(node[0], _slug(node[1]))).exists()]


def _check(kind, uid):
    found = project.load()
    if kind not in paths.KINDS or uid not in (found.get(f"{kind}s") or {}):
        raise paths.NotFound(f"No {kind} {uid}")
    if not registry.imported(paths.origin(uid)):
        raise _fail("not_imported", f"{_name(_units(found), (kind, uid))} is not an imported item: only those move to Personal.")
    return found


def plan(kind, uid):
    """What Move to Personal would do, for each choice: {alone, together}, each {moves: [names], stays: [{name, why}], replaces:
    [names], held}. `together` is None when it would move no more than `alone`, which then says what stays and why. `used_by` is the item that includes this
    one and does not move with it: then nothing moves."""
    found = _check(kind, uid)
    units = _units(found)
    users = _users_of_root(found, kind, uid)
    answers = {}
    for together in (False, True):
        moving, stays = _choice(found, kind, uid, together)
        answers["together" if together else "alone"] = {
            "moves": [_name(units, node) for node in moving],
            "stays": [{"name": _name(units, node), "why": f"Used by {_name(units, user)}"} for node, user in stays],
            "replaces": _replaces(units, moving), "held": _held(found, moving),
            "uses_machine": _judged(units, moving)[0]}
    if answers["together"]["moves"] == answers["alone"]["moves"]:   # one choice: alone, saying why nothing comes along
        answers["alone"]["stays"], answers["together"] = answers["together"]["stays"], None
    return {**answers, "used_by": _name(units, users[0]) if users else ""}


def _folder_of(ns):
    entry = registry.entry(ns) or {}
    return Path(entry.get("path") or "")


def _clear(target: Path):
    for child in target.iterdir():
        if child.name in MACHINE_FILES:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def move(kind, uid, together, allow=False):
    """Moves the item, and with `together` what it brings from its import that nothing else uses, to Personal; `allow`: the person
    allowed what takes more of this machine, which nothing judges there. Answers {moved: [{kind, id}], id}."""
    found = _check(kind, uid)
    units = _units(found)
    users = _users_of_root(found, kind, uid)
    if users:
        raise _fail("used", f"Used by {_name(units, users[0])}: move that one to Personal instead.")
    moving, _ = _choice(found, kind, uid, together)
    reason = _held(found, moving)
    if reason:
        raise _fail("held", reason)
    uses = _judged(units, moving)[0]
    if uses and not allow:
        raise _fail("allow_machine", f"{', '.join(uses)} uses this machine: allow it to move it to Personal.")
    ns = paths.origin(uid)
    source, home = _folder_of(ns), paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL
    table = [(f"{paths.CATALOGS_DIR}/{ns}/{_rel(k, _slug(one))}", f"{paths.CATALOGS_DIR}/{paths.LOCAL}/{_rel(k, _slug(one))}")
             for k, one in moving]
    for k, one in moving:
        rel = _rel(k, _slug(one))
        compose_rel = rel if k == "setup" else f"{rel}/compose.yml"
        here = posixpath.dirname(compose_rel)
        text = (source / compose_rel).read_text(encoding="utf-8")
        written = moves.repoint(text, f"{paths.CATALOGS_DIR}/{ns}/{here}", f"{paths.CATALOGS_DIR}/{paths.LOCAL}/{here}", table)
        target = home / rel
        if k == "setup":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(written, encoding="utf-8")
            (source / rel).unlink()
            continue
        if target.is_dir():
            _clear(target)
        target.mkdir(parents=True, exist_ok=True)
        for path in sorted(p for p in (source / rel).rglob("*") if not p.is_symlink()):
            inside = path.relative_to(source / rel)
            if path.is_dir():
                (target / inside).mkdir(parents=True, exist_ok=True)
                continue
            (target / inside).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target / inside)
        (target / "compose.yml").write_text(written, encoding="utf-8")
        shutil.rmtree(source / rel)
    moves.rekey({f"{k}:{one}": f"{paths.LOCAL}/{_slug(one)}" for k, one in moving})
    left = [path for path in (*source.glob("playgrounds/*/compose.yml"), *source.glob("addons/*/compose.yml"),
                              *source.glob("setups/*.yml"))]
    project.forget()
    if not left and registry.entry(ns):
        service.remove(ns)
    project.forget()
    return {"moved": [{"kind": k, "id": f"{paths.LOCAL}/{_slug(one)}"} for k, one in moving], "id": f"{paths.LOCAL}/{_slug(uid)}"}
