"""Move to Personal, and Copy to Personal. Move: an item of a playground imported from a file and kept as a catalog of its own (Imported) becomes the person's
own, in .lab/catalogs/local, as if they had made it.

A playground moves alone, or with the setups and add-ons it brings from the same import. Whatever another item still includes
stays where it is, and the item that moved includes it there: an item of a catalog never includes one of Personal, so what is
used by an item that does not move cannot move (`stays`, with whom it is used by). An item that is itself included by one that
does not move does not move at all. What moves takes along its files, its settings, its secrets and what its seed recorded
(the whole folder), its star and its tested sets; the includes of one another follow it, and so do its includes of what stays.
An item of Personal with the same name is replaced (`replaces`). The moving item's settings are those it ran with, so they win:
its `.env` and its files of `secrets/`, and its `.state` as a whole folder (a seed's record is never mixed file by file). What of
the replaced item's would be overwritten goes to the kept settings (kept.py), and is said; the rest of its settings, secrets and
state comes to the new item. Nothing moves while one of the items is on. The import whose items all moved is removed with its
catalog.

A new version of items of Personal (a move, a copy, an import to Personal) is written whole beside Personal first (`stage`), then
swapped in, all of its items or none (`swap_in`): every old one renamed aside, then every new one renamed in, a journal in the swap
folder saying each phase before it starts. At any moment an item is the old one or the new one, whole. What a stop of the Lab leaves
is put right from the journal alone, at the next change and at the Lab UI's start (`recover`), and said; a swap folder that holds
the person's files is never deleted.

Personal is the last check, as for an import: nothing judges what is there. An item blocked only for what it takes of this
machine (a privileged container, say; and what needs it) moves once the person allowed it, which the plan says (`uses_machine`)
and the move must be asked with (`allow`); an item blocked for anything else does not move (`held`), nor one that waits for a
catalog this Lab has not added, which nothing could judge (`waiting`). A move and a copy are changes of catalogs: they wait for
none under way, and one that fails while it writes changes nothing in Personal.

Copy: the person's own version of an item of any catalog, to change as they like. The copy keeps the names of the original's
services, which its neighbours and its seed call it by, and says whose copy it is (`x-gravitee.copy_of`, on this Lab only) and the
private catalog what it holds comes from (`x-gravitee.private_from`, resolved when it is copied): the Lab takes it for the original's
twin, so neither blocks the other, only one of them runs at a time, and "Back to the catalog version" drops the copy.
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
import json
import os
import posixpath
import secrets
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import paths
from ..library import moves, personal as personals, project
from . import registry, resolve, service
from .contribute import labelled, mark as _mark, private_of, rewritten, unmark
from .registry import CatalogError

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
    claim = next(((kind, uid) for kind, uid in moving if _claims(kind, uid)), None)
    if claim:
        return f"{_name(_units(found), claim)} {CLAIMED}"
    return next(filter(None, (replaced_on(found, kind, _slug(uid)) for kind, uid in moving)), "")


CLAIMED = "says it is a copy of an item of this Lab, which only this Lab writes: it cannot run. Fix: ask for its file again, without copy_of."


def _claims(kind, uid):
    """Whether an item says it is a copy (x-gravitee.copy_of) as the Lab reads it (Compose, the project's own reading), the twin
    of an item that only this Lab makes, when it copies an item to Personal."""
    said = ((project.loaded()[1].get("raw") or {}).get(paths.unit_file(kind, uid)) or {}).get("x-gravitee")
    return isinstance(said, dict) and bool(said.get("copy_of"))


UNREAD = "{names} could not be read once in Personal ({why}), so Personal is as it was. Fix: keep it in Imported, where the Lab says why."


def placed_without_claims(rels):
    """The check of a swap from a file (a move, an import to Personal), once its items are in Personal: each of them read as the
    project reads it (project.read_own, the one reader of twins), side by side, and none says it is a copy. An item it cannot read,
    or a claim, undoes the swap (CatalogError, `swap_in`) and says why. Only the items placed are read: not the whole project."""
    def one(rel):
        kind, slug = ("setup", posixpath.basename(rel)[:-4]) if rel.startswith("setups/") else (rel.split("/")[0][:-1], posixpath.basename(rel))
        return rel, project.read_own(paths.unit_file(kind, f"{paths.LOCAL}/{slug}"))

    def check():
        with ThreadPoolExecutor(max_workers=8) as pool:
            read = list(pool.map(one, rels))
        for rel, (config, why) in read:
            if config is None:
                said = why if why.startswith("Compose") else why[:1].lower() + why[1:]   # within a sentence
                raise _fail("placed_unread", UNREAD.format(names=_names_of([rel]), why=said), 409)
            said = config.get("x-gravitee")
            if isinstance(said, dict) and said.get("copy_of"):
                raise _fail("twin_claimed", f"{_names_of([rel])} {CLAIMED}")
    return check


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
    allowed what takes more of this machine, which nothing judges there. Answers {moved: [{kind, id}], id, warning}. A change of
    catalogs: it waits for none under way. The items are written whole beside Personal, then swapped in (`replace`): an item of
    Personal is at any moment the old one or the new one, whole. The import loses its items only once they are in Personal; what
    Docker made there and cannot be deleted then is said (`warning`), and the move completes."""
    with service._changing():
        recovered = recover()
        with telling(recovered):
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
        source = _folder_of(ns)
        table = [(f"{paths.CATALOGS_DIR}/{ns}/{_rel(k, _slug(one))}", f"{paths.CATALOGS_DIR}/{paths.LOCAL}/{_rel(k, _slug(one))}")
                 for k, one in moving]

        def write(root):
            for k, one in moving:
                rel = _rel(k, _slug(one))
                compose_rel = rel if k == "setup" else f"{rel}/compose.yml"
                here = posixpath.dirname(compose_rel)
                text = (source / compose_rel).read_text(encoding="utf-8")
                text = moves.repoint(text, f"{paths.CATALOGS_DIR}/{ns}/{here}", f"{paths.CATALOGS_DIR}/{paths.LOCAL}/{here}", table)
                if k != "setup":
                    shutil.copytree(source / rel, root / rel, symlinks=True, ignore=_links)
                (root / compose_rel).parent.mkdir(parents=True, exist_ok=True)
                (root / compose_rel).write_text(text, encoding="utf-8")
        rels = [_rel(k, _slug(one)) for k, one in moving]
        warnings = [recovered, replace(write, rels, placed_without_claims(rels))]
        # The import loses what moved; what Docker made there and cannot be deleted is said, and the move completes
        stuck = []
        for k, one in moving:
            rel = _rel(k, _slug(one))
            try:
                (source / rel).unlink() if k == "setup" else shutil.rmtree(source / rel)
            except OSError:
                stuck.append(rel)
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
        else:   # the import stays, with its other items: what is said to remove is the moved item's own folder
            warnings += [STUCK.format(where=f"{paths.IMPORTS_DIR}/{ns}/{rel}") for rel in stuck]
        project.forget()
    warning = " ".join(one for one in warnings if one)
    return {"moved": [{"kind": k, "id": f"{paths.LOCAL}/{_slug(one)}"} for k, one in moving], "id": f"{paths.LOCAL}/{_slug(uid)}",
            **({"warning": warning} if warning else {})}


def _links(folder, names):
    """What an item's copy leaves: a link, whose target is no file of the item."""
    return {name for name in names if os.path.islink(os.path.join(folder, name))}


@contextlib.contextmanager
def telling(said):
    """An error of a change says too what the start of that change put right (`recover`), which is said either way."""
    try:
        yield
    except CatalogError as error:
        if said:
            error.message = f"{error.message} {said}"
        raise


# ---------------------------------------------------------------------------------------------------------------------
# A new version of items of Personal, swapped in whole
# ---------------------------------------------------------------------------------------------------------------------

SWAP = ".swap-"   # a folder of .lab, beside Personal, for one change: new/ the items it writes, old/ those they replace, its journal
LEFTOVER = ".leftover-"   # what a change could not delete: never read again
JOURNAL = "journal.json"   # in the swap folder: its items and its phase, written before each step, which a recovery reads alone
STUCK = "Some files were created by Docker and could not be deleted. Remove the folder {where} with administrator rights."
REPLACED = "Settings of {names} that the new version replaced are kept in {where}."
NOT_KEPT = ("Settings of {names} could not be kept and are still in {where}: copy them out, then remove that folder with "
            "administrator rights.")
NOT_IN = ("{names} could not be put in Personal ({why}): Personal is as it was. Fix: see that the disk has room and that .lab "
          "can be written, then try again.")
UNDONE = "A change of Personal that stopped midway was undone for {names}."
FINISHED = "A change of Personal that stopped midway was finished for {names}."
NOT_DONE = "A change of Personal that stopped midway could not be put right ({why}): it is kept in {where}."
UNKNOWN = ("A change of Personal left files of yours in {where}, and no journal that says what they are: they are kept there. "
           "Fix: copy what you need into your items, then delete that folder.")
STALE = 3600   # seconds after which a swap being written, whose writer does not answer as such, is a leftover


def replace(write, rels, check=None):
    """The items at `rels` (their folder, or the file of a setup, relative to Personal) replaced by those `write(root)` writes
    whole at `root/<rel>` (`stage`, then `swap_in`). Answers what is said of it, or None."""
    return swap_in(stage(write), rels, check)


def stage(write):
    """The new versions of items written whole beside Personal (`write(root)`), in a folder of their own that nothing reads, its
    journal saying it is being written and by which process (a recovery leaves it alone while that process lives): answers it.
    A failure here changes nothing in Personal."""
    swap = paths.ROOT / paths.LAB_DIR / f"{SWAP}{secrets.token_hex(4)}"
    (swap / "new").mkdir(parents=True)
    _note(swap, phase="staging", pid=os.getpid(), started=time.time())
    try:
        write(swap / "new")
    except BaseException:
        shutil.rmtree(swap, ignore_errors=True)
        raise
    return swap


def discard(swap):
    """What `stage` wrote, let go when the change is not made. Only a swap still being written goes: one whose items went aside
    holds the person's files, and is the recovery's."""
    if swap.name.startswith(SWAP) and (_journal(swap) or {}).get("phase") == "staging":
        shutil.rmtree(swap, ignore_errors=True)


def swap_in(swap, rels, check=None):
    """The new versions (`stage`) of the items at `rels` swapped into Personal, all or none: every item there renamed aside, then
    every new one renamed in, the journal saying each phase before it starts. Renames on one file system: at any moment an item is
    the old one or the new one, whole. A failure undoes, in reverse, every rename made, and says so; once all are in, the person's
    files come to the new ones (`_settle`). Called under the lock of the change. Answers what is said of it, or None."""
    home = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL
    names = _names_of(rels, swap / "new")
    try:
        _note(swap, phase="aside", items=rels)
        for rel in rels:
            if os.path.lexists(home / rel):
                (swap / "old" / rel).parent.mkdir(parents=True, exist_ok=True)
                os.rename(home / rel, swap / "old" / rel)
        _note(swap, phase="in", items=rels)
        for rel in rels:
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            os.rename(swap / "new" / rel, home / rel)
        if check:
            check()   # what is in Personal now, read as the Lab reads it: a refusal undoes the swap
        _note(swap, phase="done", items=rels)
    except OSError as error:
        _undo(swap, rels)
        raise CatalogError("swap_failed", NOT_IN.format(names=names, why=(error.strerror or str(error)).lower()), 500) from None
    except BaseException:   # a refusal of the check, or anything else: what it swapped in is undone before it is said
        _undo(swap, rels)
        raise
    return _settle(swap, rels, recovering=False)


def recover():
    """What a change of Personal left when the Lab stopped in the middle of it, put right from its journal alone: what was still
    being written (by no process that lives) goes, never seen; a swap stopped before all its new items were in is undone; one
    with all of them in is finished (`_settle`). Each swap is put right on its own: one that cannot be never keeps the others, nor
    the Lab UI's start, from being. Called at the Lab UI's start and at the start of each change of Personal, under the lock of
    the catalogs. Answers what is said of it, or None."""
    said = []
    for swap in sorted((paths.ROOT / paths.LAB_DIR).glob(f"{SWAP}*")):
        if not swap.is_dir() or swap.is_symlink():
            continue
        try:
            journal = _journal(swap) or {}
            rels = [rel for rel in journal.get("items") or () if isinstance(rel, str) and _place_ok(rel)]
            phase = journal.get("phase")
            if phase in (None, "staging"):
                if _holds_people(swap):   # no journal to say what it is, and the person's files in it: never deleted
                    said.append(UNKNOWN.format(where=f"{paths.LAB_DIR}/{swap.name}"))
                elif phase is None or _stale(journal):
                    shutil.rmtree(swap, ignore_errors=True)
            elif phase in ("aside", "in"):
                names = _names_of(rels, swap / "old") or _names_of(rels, swap / "new")
                _undo(swap, rels)
                said.append(UNDONE.format(names=names))
            elif phase == "done":
                said.append(FINISHED.format(names=_names_of(rels)))
                said.append(_settle(swap, rels, recovering=True))
        except Exception as error:  # noqa: BLE001 - one swap that cannot be put right keeps no other, nor the Lab, from being
            said.append(NOT_DONE.format(why=str(error)[:120] or type(error).__name__, where=f"{paths.LAB_DIR}/{swap.name}"))
    return " ".join(one for one in said if one) or None


def recover_at_start():
    """`recover` at the Lab UI's start, under the lock of the catalogs, a lock of the files that every Lab UI of this folder
    takes: a second one never puts right what the first is doing (and what a live process writes is left alone, `stage`)."""
    with service._changing():
        return recover()


def _undo(swap, rels):
    """The renames of a swap undone in reverse: each new item that went in back to new/, each old one back to its place. The
    folder goes once it holds nothing of the person's (old/ empty); else it stays for the recovery, its journal as it was."""
    home = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL
    for rel in reversed(rels):
        if not os.path.lexists(swap / "new" / rel) and os.path.lexists(home / rel):
            (swap / "new" / rel).parent.mkdir(parents=True, exist_ok=True)
            os.rename(home / rel, swap / "new" / rel)
        if os.path.lexists(swap / "old" / rel) and not os.path.lexists(home / rel):
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            os.rename(swap / "old" / rel, home / rel)
    if not any(path.is_file() or path.is_symlink() for path in (swap / "old").rglob("*")):
        shutil.rmtree(swap, ignore_errors=True)


def _settle(swap, rels, recovering):
    """The end of a swap whose new items are all in: each gets the person's files of the item it replaced (`_bring`), and what it
    replaced of them is kept (kept.py). Then the folder goes; a setting that could be neither brought nor kept stays where it is,
    and the folder with it is renamed apart, never read again. The journal says done until then, so that a stop here is finished
    by the recovery."""
    home, old = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL, swap / "old"
    shutil.rmtree(swap / "new", ignore_errors=True)
    owner = {source: rel for rel in rels if (old / rel).is_dir() and not (old / rel).is_symlink()
             for source, _ in _bring(old / rel, home / rel, rel)}
    pairs = [(source, f"{owner[source]}/{source.relative_to(old / owner[source]).as_posix()}") for source in owner]
    where, failed = _kept().keep(_kept().LOCAL, None, pairs, {rel: _names_of([rel]) for rel in set(owner.values())})
    said = [REPLACED.format(names=_names_of(sorted({owner[one] for one in owner if one not in failed})), where=where) if where else ""]
    _kept().remove_but(swap, [*failed, swap / JOURNAL])   # the journal goes last: until then a stop is finished from it
    if not failed:
        with contextlib.suppress(OSError):
            (swap / JOURNAL).unlink()
            swap.rmdir()
    if os.path.lexists(swap):
        left = swap.with_name(f"{LEFTOVER}{swap.name[len(SWAP):]}")
        os.rename(swap, left)
        here = f"{paths.LAB_DIR}/{left.name}"
        said.append(NOT_KEPT.format(names=_names_of(sorted({owner[one] for one in failed})), where=here) if failed
                    else STUCK.format(where=here))
    return " ".join(one for one in said if one) or None


def _note(swap, **journal):
    """The journal of a swap written whole and to the disk, its folder too, before the step it announces: a stop of the machine
    never leaves a step done that the journal does not say."""
    temporary = swap / f".{JOURNAL}.tmp"
    with open(temporary, "w", encoding="utf-8") as out:
        out.write(json.dumps(journal, sort_keys=True) + "\n")
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, swap / JOURNAL)
    folder = os.open(swap, os.O_RDONLY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


def _holds_people(swap):
    """Whether a swap folder holds a file of the person's: anything in old/, where the items it replaced went."""
    return any(path.is_file() or path.is_symlink() for path in (swap / "old").rglob("*"))


def _stale(journal):
    """Whether a swap being written was left: its writer runs no more, or (a process that took its number, one that does not
    answer) it began more than STALE seconds ago."""
    started = journal.get("started")
    return not _alive(journal.get("pid")) or not isinstance(started, (int, float)) or time.time() - started > STALE


def _journal(swap):
    try:
        found = json.loads((swap / JOURNAL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def _alive(pid):
    """Whether the process `pid` runs: the Lab UI that is writing a swap."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True   # it runs, as another user
    return True


def _place_ok(rel):
    parts = rel.split("/")
    return len(parts) == 2 and parts[0] in ("addons", "playgrounds", "setups") and parts[1] not in ("", ".", "..")


def _bring(old, new, rel):
    """The person's files of the item of Personal that a new version replaced (`old`) brought to the new one (`new`): `.env` and
    the files of `secrets/` where the new one has none of that path, `.state` whole when the new one has none (a seed's record is
    one whole, never mixed). Answers [(source, its place among kept settings)] of what the new one had already, or what could not
    come: the new one's are those it ran with, so they stay, and the replaced ones are kept."""
    pairs = []
    files = [old / ".env"] if os.path.lexists(old / ".env") else []
    if (old / "secrets").is_dir() and not (old / "secrets").is_symlink():
        files += sorted(path for path in (old / "secrets").rglob("*") if path.is_file() or path.is_symlink())
    for path in files:
        inside = path.relative_to(old)
        if os.path.lexists(new / inside) or not _kept().carry(path, new / inside, new):
            pairs.append((path, f"{rel}/{inside.as_posix()}"))
    if os.path.lexists(old / ".state"):
        if os.path.lexists(new / ".state") or not _kept().carry(old / ".state", new / ".state", new):
            pairs.append((old / ".state", f"{rel}/.state"))
    return pairs


def _names_of(rels, root=None):
    """The names of the items at `rels` (in Personal, or under `root`), as their x-gravitee says them, for the words of a message."""
    from . import exports   # here and not above: exports reads this module
    home, names = root or paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL, []
    for rel in rels:
        file = home / rel if rel.endswith(".yml") else home / rel / "compose.yml"
        slug = posixpath.basename(rel)[:-4] if rel.endswith(".yml") else posixpath.basename(rel)
        try:
            names.append(exports._named(file.read_text(encoding="utf-8")[:exports.HEAD_LIMIT * 2], slug))
        except (OSError, ValueError):
            names.append(slug)
    return ", ".join(names)


def _kept():
    from . import kept   # the settings a change keeps
    return kept


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
    recorded, and the name of the private catalog what it holds comes from (private_from), resolved now; with `users`, every item
    of a catalog that includes it too, up to the playgrounds, and the items of Personal that include one of them then include the
    copy. Answers {copies: [{kind, id}], id, warning}. A change of catalogs: it waits for none under way, and the copies are
    written whole beside Personal before they are in it."""
    with service._changing():
        recovered = recover()
        with telling(recovered):
            found = project.load()
            plan = copy_plan(kind, uid)
            chosen = plan["with_users"] if users and plan["with_users"] else plan["alone"]
            if chosen["held"]:
                raise _fail("held", chosen["held"])
            if chosen["uses_machine"] and not allow:
                raise _fail("allow_machine", f"{', '.join(chosen['uses_machine'])} uses this machine: allow it to copy it to Personal.")
            copies, switches = _copies(found, kind, uid, bool(users and plan["with_users"]))
            places = {node: (paths.unit_file(*node) if node[0] == "setup" else paths.unit_folder(*node)) for node in copies}
            table = [(places[node], f"{paths.CATALOGS_DIR}/{paths.LOCAL}/{_rel(node[0], _slug(node[1]))}") for node in copies]
            texts = {}
            for node in copies:   # written for its place first: what cannot be stops the copy before anything is written
                k, one = node
                compose_file = paths.ROOT / places[node] if k == "setup" else paths.ROOT / places[node] / "compose.yml"
                here = posixpath.dirname(places[node] if k == "setup" else f"{places[node]}/compose.yml")
                there = posixpath.dirname(f"{paths.CATALOGS_DIR}/{paths.LOCAL}/{_rel(k, _slug(one))}" + ("" if k == "setup" else "/compose.yml"))
                text = compose_file.read_text(encoding="utf-8")   # what it holds says its private catalog, resolved now, and whose copy it is
                private = private_of(text, one, compose_file.parent)
                # Changed as it is and checked as Compose reads it where it is, then written for its new place
                edited = _mark(labelled(unmark(text), private), one) or text
                shown = _name(_units(found), node)
                rewritten(text, edited, {"copy_of": one, "private_from": private}, _slug(one) if shown == one else shown,
                          "that it is your copy" + (" and its private label" if private else ""), compose_file.parent)
                texts[node] = moves.repoint(edited, here, there, table)

        def write(root):
            for node in copies:
                k, one = node
                target = root / _rel(k, _slug(one))
                target.parent.mkdir(parents=True, exist_ok=True)
                if k == "setup":
                    target.write_text(texts[node], encoding="utf-8")
                    continue
                shutil.copytree(paths.ROOT / places[node], target, ignore=_not_copied)
                for path in target.rglob("*"):   # a catalog's files are read only where they are installed: the copy is the person's
                    path.chmod(path.stat().st_mode | 0o200)
                target.chmod(target.stat().st_mode | 0o200)
                (target / "compose.yml").write_text(texts[node], encoding="utf-8")
        said = replace(write, [_rel(k, _slug(one)) for k, one in copies])
        for old, new in table if switches else ():
            personals.follow(old, new)
        project.forget()
    warning = " ".join(one for one in (recovered, said) if one)
    return {"copies": [{"kind": k, "id": f"{paths.LOCAL}/{_slug(one)}"} for k, one in copies], "id": f"{paths.LOCAL}/{_slug(uid)}",
            **({"warning": warning} if warning else {})}
