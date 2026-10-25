"""Move to Personal, and Copy to Personal. Move: an item of a playground imported from a file and kept as a catalog of its own (Imported) becomes the person's
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
and the move must be asked with (`allow`); an item blocked for anything else does not move (`held`), nor one that waits for a
catalog this Lab has not added, which nothing could judge (`waiting`). A move and a copy are changes of catalogs: they wait for
none under way, and one that fails midway takes out what it wrote in Personal.

Copy: the person's own version of an item of any catalog, to change as they like. The copy keeps the names of the original's
services, which its neighbours and its seed call it by, and says whose copy it is (`x-gravitee.copy_of`): the Lab then takes it for
the original's twin, so neither blocks the other, only one of them runs at a time, and "Back to the catalog version" drops the copy.
An item of a catalog that includes the original cannot be changed to include the copy: with `users`, it is copied too, up to the
playgrounds, and the items of Personal that included one of them include the copy. The copy takes the settings and secrets of the
original, not what its seed recorded. The same checks as a move: blocked for more than this machine, no copy; what takes more of
this machine, once allowed.

    plan(kind, uid)              {alone, together}: what each choice moves, keeps, replaces, and what holds it
    move(kind, uid, together)    the move: {moved: [{kind, id}], id (the item's id in Personal)}
    copy_plan(kind, uid)         {alone, with_users}: what Copy to Personal copies, switches to the copy, and what holds it
    copy(kind, uid, users)       the person's copy of an item of a catalog (x-gravitee.copy_of), and of what uses it
"""

import contextlib
import os
import posixpath
import secrets
import shutil
from pathlib import Path

from .. import paths
from ..library import moves, personal as personals, project
from . import registry, resolve, service
from .contribute import keep_private, mark as _mark, unmark
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


# What a catalog that an item needs still lacks, in the words of a reason: the item cannot run until then (resolve.catalog_needs)
UNTIL = {"missing": "it needs {name}, which is not added", "downloading": "it needs {name}, which is not downloaded yet",
         "failed": "it needs {name}, which is not downloaded yet", "old": "it needs a newer version of {name}",
         "gone": "it needs {unit} of {name}, which is no longer there"}
FIXES = {"missing": "add {name} on the Catalogs page first", "old": "update {name} on the Catalogs page first"}


def waiting(unit):
    """Why an item cannot run until a catalog it needs is there, ("it needs X, which is not added", the fix), or None. Compose
    cannot read such an item, so nothing has judged it: what it would do is not known until the catalog comes."""
    need = next(iter(unit.get("needs_catalogs") or ()), None)
    if not need:
        return None
    reason = need.get("reason") if need.get("reason") in UNTIL else "missing"
    words = {"name": need.get("name") or need.get("id") or "a catalog", "unit": need.get("unit") or "an item"}
    return UNTIL[reason].format(**words), FIXES.get(reason, "wait until {name} is downloaded").format(**words)


def _judged(units, moving):
    """([names] of the moving items that take more of this machine, which the person must allow), and why one cannot move at all
    ("X cannot run: reason"), or "". Personal is judged by nothing: an item that was not judged either, since it waits for a
    catalog or the Lab has not read it yet, cannot go there, whatever it would do."""
    uses, refused = [], ""
    for node in moving:
        unit = (units.get(node[0]) or {}).get(node[1]) or {}
        verdict = unit.get("verdict") or {}
        codes = set(verdict.get("codes") or ())
        until = waiting(unit)
        if until:
            refused = refused or f"{_name(units, node)} cannot run: {until[0]}. Fix: {until[1]}."
        elif not unit.get("verdict"):
            refused = refused or f"{_name(units, node)} cannot run: the Lab has not judged it yet. Fix: try again in a moment."
        if verdict.get("state") == "blocked" and not codes <= HOST_CODES | {NEEDS_BLOCKED}:
            refused = refused or f"{_name(units, node)} cannot run: {(verdict.get('reasons') or [''])[0]}"
        if codes & HOST_CODES:
            uses.append(_name(units, node))
    return uses, refused


def _held(found, moving):
    """Why the move waits: an item that is on (Turn off X first), the item of Personal it replaces that is on, or one that cannot
    run for more than what it takes of this machine."""
    reason = _judged(_units(found), moving)[1]
    if reason:
        return reason
    for kind, uid in moving:
        try:
            personals._not_held(found, kind, uid)
        except RuntimeError as error:
            return str(error)
    return next(filter(None, (replaced_on(found, kind, _slug(uid)) for kind, uid in moving)), "")


def replaced_on(found, kind, slug):
    """Why the item of Personal named `slug`, which an item coming there replaces, keeps it from coming: it is on; else ""."""
    uid = f"{paths.LOCAL}/{slug}"
    if uid not in (found.get(f"{kind}s") or {}):
        return ""
    try:
        personals._not_held(found, kind, uid)
    except RuntimeError as error:
        return f"{_name(_units(found), (kind, uid))} in Personal is on and would be replaced. Fix: {str(error)[:1].lower()}{str(error)[1:]}."
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


def move(kind, uid, together, allow=False):
    """Moves the item, and with `together` what it brings from its import that nothing else uses, to Personal; `allow`: the person
    allowed what takes more of this machine, which nothing judges there. Answers {moved: [{kind, id}], id}. A change of catalogs:
    it waits for none under way. The import loses its items only once every one is in Personal: a move that fails midway takes
    out what it wrote there, puts back whole the items of Personal it was replacing (set aside first), and the import is as it
    was. What Docker made in the import and cannot be deleted then is said (`warning`), and the move completes."""
    with service._changing():
        recovered = recover()
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
        texts = {}
        for k, one in moving:   # read first: what cannot be read stops the move before anything is written
            compose_rel = _rel(k, _slug(one)) if k == "setup" else f"{_rel(k, _slug(one))}/compose.yml"
            here = posixpath.dirname(compose_rel)
            text = keep_private((source / compose_rel).read_text(encoding="utf-8"))   # whose copy it was, if private content
            texts[(k, one)] = moves.repoint(text, f"{paths.CATALOGS_DIR}/{ns}/{here}", f"{paths.CATALOGS_DIR}/{paths.LOCAL}/{here}", table)
        # The items of Personal it replaces are put aside first, whole, and come back whole if the move fails midway
        aside, put = set_aside([_rel(k, _slug(one)) for k, one in moving])
        made = []
        try:
            for k, one in moving:
                rel = _rel(k, _slug(one))
                target = home / rel
                made.append(target)
                if k == "setup":
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(texts[(k, one)], encoding="utf-8")
                    continue
                target.mkdir(parents=True, exist_ok=True)
                for path in sorted(p for p in (source / rel).rglob("*") if not p.is_symlink()):
                    inside = path.relative_to(source / rel)
                    if path.is_dir():
                        (target / inside).mkdir(parents=True, exist_ok=True)
                        continue
                    (target / inside).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target / inside)
                (target / "compose.yml").write_text(texts[(k, one)], encoding="utf-8")
        except BaseException:
            put_back(aside, put, made)
            raise
        warnings = [recovered, merge_back(aside, put, _names(units, moving))]
        # The import loses what moved; what Docker made there and cannot be deleted is said, and the move completes
        stuck = False
        for k, one in moving:
            rel = _rel(k, _slug(one))
            try:
                (source / rel).unlink() if k == "setup" else shutil.rmtree(source / rel)
            except OSError:
                stuck = True
                if k != "setup":
                    shutil.rmtree(source / rel, ignore_errors=True)
                    with contextlib.suppress(OSError):
                        (source / rel / "compose.yml").unlink()
        moves.rekey({f"{k}:{one}": f"{paths.LOCAL}/{_slug(one)}" for k, one in moving})
        left = [path for path in (*source.glob("playgrounds/*/compose.yml"), *source.glob("addons/*/compose.yml"),
                                  *source.glob("setups/*.yml"))]
        project.forget()
        if not left and registry.entry(ns):
            warnings.append(service.remove(ns, keep_settings=False).get("warning"))   # what it held moved: nothing of it is kept
        elif stuck:
            warnings.append(STUCK.format(where=f"{paths.IMPORTS_DIR}/{ns}"))
        project.forget()
    warning = " ".join(one for one in warnings if one)
    return {"moved": [{"kind": k, "id": f"{paths.LOCAL}/{_slug(one)}"} for k, one in moving], "id": f"{paths.LOCAL}/{_slug(uid)}",
            **({"warning": warning} if warning else {})}


def _names(units, nodes):
    """{place in Personal: name} of the items of a change, for the words of a warning."""
    return {_rel(k, _slug(one)): _name(units, (k, one)) for k, one in nodes}


# ---------------------------------------------------------------------------------------------------------------------
# What a change replaces in Personal: set aside whole, put back whole when it fails, the person's files merged back once done
# ---------------------------------------------------------------------------------------------------------------------

ASIDE = ".move-"   # a folder of .lab that holds the items of Personal a change replaces, until it is done
NOT_BACK = ("Some settings of {names} could not come back and are in {where}: copy them into the item in Personal, then remove "
            "that folder with administrator rights.")


RECOVERED = "A change that stopped midway had set {names} aside: they are in Personal again."


def recover():
    """What a change of Personal set aside and did not finish, since the Lab stopped in the middle of it: an item that is not in
    Personal comes back whole, one that is there (the change had written it) gets the person's files back file by file. Called
    at the start of the next change, under its lock, when no other change can be at work. Answers what it says of it, or None."""
    home, back, said = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL, [], []
    for aside in sorted((paths.ROOT / paths.LAB_DIR).glob(f"{ASIDE}*")):
        if not aside.is_dir() or aside.is_symlink():
            continue
        put = [f"{area}/{one.name}" for area in ("addons", "playgrounds") for one in sorted((aside / area).glob("*")) if one.is_dir()]
        put += [f"setups/{one.name}" for one in sorted((aside / "setups").glob("*.yml"))]
        there = [rel for rel in put if os.path.lexists(home / rel)]
        for rel in put:
            if rel not in there:
                (home / rel).parent.mkdir(parents=True, exist_ok=True)
                os.rename(aside / rel, home / rel)
        said.append(merge_back(aside, there, {}))
        back += [posixpath.basename(rel)[:-4] if rel.endswith(".yml") else posixpath.basename(rel) for rel in put]
    if not back:
        return None
    return " ".join([RECOVERED.format(names=", ".join(back)), *filter(None, said)])


def set_aside(rels):
    """The items of Personal at `rels` (their folder, or the file of a setup, relative to Personal) put aside whole, in a folder
    of the Lab's own beside Personal: answers (that folder, [the rels put aside]). What exists is renamed, never copied."""
    home = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL
    aside, put = paths.ROOT / paths.LAB_DIR / f"{ASIDE}{secrets.token_hex(4)}", []
    try:
        for rel in rels:
            if os.path.lexists(home / rel):
                (aside / rel).parent.mkdir(parents=True, exist_ok=True)
                os.rename(home / rel, aside / rel)
                put.append(rel)
    except BaseException:
        put_back(aside, put, [])
        raise
    return aside, put


def put_back(aside, put, made):
    """A change that failed midway: what it wrote in Personal goes, and what it set aside comes back as it was."""
    home = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL
    _take_back(made)
    for rel in put:
        os.rename(aside / rel, home / rel)
    shutil.rmtree(aside, ignore_errors=True)


def merge_back(aside, put, names):
    """A change that is done: the person's settings, secrets and state of each item it replaced come back file by file into the
    new item, and the person's file is the one kept where both have the same; the rest of what was set aside goes. Answers the
    warning of what could not come back, or could not be deleted, or None. `names`: {rel: name}, for its words."""
    home, failed = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL, []
    for rel in put:
        if (aside / rel).is_dir() and not (aside / rel).is_symlink() and (home / rel).is_dir():
            failed += [rel for name in MACHINE_FILES if _merge(aside / rel / name, home / rel / name)]
    if failed:
        return NOT_BACK.format(names=", ".join(dict.fromkeys(names.get(rel, rel) for rel in failed)),
                               where=f"{paths.LAB_DIR}/{aside.name}")
    shutil.rmtree(aside, ignore_errors=True)
    return STUCK.format(where=f"{paths.LAB_DIR}/{aside.name}") if os.path.lexists(aside) else None


def _merge(source: Path, target: Path):
    """The person's file or folder at `source` brought to `target`, file by file, theirs kept where both have one: answers what
    could not be brought ([paths]), which stays where it is."""
    if not os.path.lexists(source):
        return []
    if source.is_dir() and not source.is_symlink():
        if not os.path.lexists(target):
            with contextlib.suppress(OSError):
                os.rename(source, target)
                return []
        if os.path.lexists(target) and not target.is_dir():
            target.unlink()
        target.mkdir(parents=True, exist_ok=True)
        return [one for child in sorted(source.iterdir()) for one in _merge(child, target / child.name)]
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target, ignore_errors=True)
    try:
        os.replace(source, target)
        return []
    except OSError:
        pass
    try:
        shutil.copy2(source, target)   # what Docker made as root may be read, not moved
    except OSError:
        return [source]
    return []


STUCK = "Some files were created by Docker and could not be deleted. Remove the folder {where} with administrator rights."



def _take_back(made):
    """What a change that failed midway wrote in Personal where nothing was: removed, so that no half item stays there."""
    for target in reversed(made):
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target, ignore_errors=True)
        elif os.path.lexists(target):
            target.unlink()


# ---------------------------------------------------------------------------------------------------------------------
# Copy to Personal: the person's own version of an item of a catalog
# ---------------------------------------------------------------------------------------------------------------------

def _not_copied(folder, names):
    """What Copy to Personal leaves of an item's folder: what its seed recorded, and a link, whose target is no file of the item
    (a folder catalog is a folder of this machine, whose links may lead anywhere)."""
    return {name for name in names if name == ".state" or os.path.islink(os.path.join(folder, name))}


def _copies(found, kind, uid, users):
    """(copies [(kind, id)], switches [(kind, id)]): the item, and with `users` every item of a catalog that includes it, directly
    or not, up to the playgrounds; and the items of Personal that include one of those, which then include the copy."""
    units = _units(found)
    nodes = [(k, one) for k, found_units in units.items() for one in found_units]
    direct = {node: set(resolve._direct(units, node)) for node in nodes}
    copies, switches, frontier = [(kind, uid)], [], [(kind, uid)] if users else []
    while frontier:
        node = frontier.pop(0)
        for other in nodes:
            if node in direct[other] and other not in copies and other not in switches:
                if paths.personal(other[1]):
                    switches.append(other)
                else:
                    copies.append(other)
                    frontier.append(other)
    return copies, switches


def _copy_check(found, copies, switches):
    """Why the copy cannot be made now, or ""."""
    units = _units(found)
    home = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL
    for k, one in copies:
        if (home / _rel(k, _slug(one))).exists():
            return f"{_name(units, (k, one))} is in Personal already: delete or rename that one first"
    reason = _judged(units, copies)[1]
    if reason:
        return reason
    for k, one in switches:
        try:
            personals._not_held(found, k, one)
        except RuntimeError as error:
            return str(error)
    return ""


def copy_plan(kind, uid):
    """What Copy to Personal would do: {alone, with_users}, each {copies: [names], switches: [names], uses_machine: [names],
    held}; `with_users` is None when nothing else uses the item."""
    found = project.load()
    if kind not in paths.KINDS or uid not in (found.get(f"{kind}s") or {}):
        raise paths.NotFound(f"No {kind} {uid}")
    units = _units(found)
    if paths.personal(uid):
        raise _fail("personal", f"{_name(units, (kind, uid))} is yours already.")
    answers = {}
    for users in (False, True):
        copies, switches = _copies(found, kind, uid, users)
        answers["with_users" if users else "alone"] = {
            "copies": [_name(units, node) for node in copies], "switches": [_name(units, node) for node in switches],
            "uses_machine": _judged(units, copies)[0], "held": _copy_check(found, copies, switches)}
    if answers["with_users"]["copies"] == answers["alone"]["copies"] and not answers["with_users"]["switches"]:
        answers["with_users"] = None
    return answers


def copy(kind, uid, users=False, allow=False):
    """Copies the item to Personal, as the person's copy of it (copy_of), with its settings and secrets but not what its seed
    recorded; with `users`, every item of a catalog that includes it too, up to the playgrounds, and the items of Personal that
    include one of them then include the copy. Answers {copies: [{kind, id}], id}. A change of catalogs: it waits for none under
    way, and a copy that fails midway takes out what it wrote."""
    with service._changing():
        recovered = recover()
        found = project.load()
        plan = copy_plan(kind, uid)
        chosen = plan["with_users"] if users and plan["with_users"] else plan["alone"]
        if chosen["held"]:
            raise _fail("held", chosen["held"])
        if chosen["uses_machine"] and not allow:
            raise _fail("allow_machine", f"{', '.join(chosen['uses_machine'])} uses this machine: allow it to copy it to Personal.")
        copies, switches = _copies(found, kind, uid, bool(users and plan["with_users"]))
        home = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL
        places = {node: (paths.unit_file(*node) if node[0] == "setup" else paths.unit_folder(*node)) for node in copies}
        table = [(places[node], f"{paths.CATALOGS_DIR}/{paths.LOCAL}/{_rel(node[0], _slug(node[1]))}") for node in copies]
        texts = {}
        for node in copies:   # written for its place first: what cannot be stops the copy before anything is written
            k, one = node
            compose_file = paths.ROOT / places[node] if k == "setup" else paths.ROOT / places[node] / "compose.yml"
            here = posixpath.dirname(places[node] if k == "setup" else f"{places[node]}/compose.yml")
            there = posixpath.dirname(f"{paths.CATALOGS_DIR}/{paths.LOCAL}/{_rel(k, _slug(one))}" + ("" if k == "setup" else "/compose.yml"))
            texts[node] = _mark(moves.repoint(unmark(compose_file.read_text(encoding="utf-8")), here, there, table), one)
            if texts[node] is None:
                raise _fail("unmarked", f"{_name(_units(found), node)} has no x-gravitee block to say it is a copy.")
        made = []
        try:
            for node in copies:
                k, one = node
                target = home / _rel(k, _slug(one))
                target.parent.mkdir(parents=True, exist_ok=True)
                made.append(target)
                if k == "setup":
                    target.write_text(texts[node], encoding="utf-8")
                    continue
                shutil.copytree(paths.ROOT / places[node], target, ignore=_not_copied)
                for path in target.rglob("*"):   # a catalog's files are read only where they are installed: the copy is the person's
                    path.chmod(path.stat().st_mode | 0o200)
                target.chmod(target.stat().st_mode | 0o200)
                (target / "compose.yml").write_text(texts[node], encoding="utf-8")
        except BaseException:
            _take_back(made)
            raise
        for old, new in table if switches else ():
            personals.follow(old, new)
        project.forget()
    return {"copies": [{"kind": k, "id": f"{paths.LOCAL}/{_slug(one)}"} for k, one in copies], "id": f"{paths.LOCAL}/{_slug(uid)}",
            **({"warning": recovered} if recovered else {})}
