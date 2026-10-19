"""What is starred on this machine: the add-ons, setups and playgrounds kept at hand, in a file out of git. An item that
became a catalog's, or was renamed, keeps its star; one deleted loses it."""

import json

from . import moves
from .. import paths

FILE = ".lab/starred.json"   # {kind: [ids]}, relative to the project


def read():
    """{kind: set of ids}; nothing starred when the file is missing or does not read as such."""
    try:
        found = json.loads((paths.ROOT / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        found = {}
    found = found if isinstance(found, dict) else {}
    ids = {kind: found.get(kind) if isinstance(found.get(kind), list) else [] for kind in paths.KINDS}
    return {kind: {uid for uid in ids[kind] if isinstance(uid, str)} for kind in paths.KINDS}


def _write(found):
    target = paths.ROOT / FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    paths.write_atomic(target, json.dumps({kind: sorted(ids) for kind, ids in found.items() if ids}, indent=2) + "\n")


def mark(state):
    """Says of every item of the state whether it is starred."""
    found = read()
    for kind in paths.KINDS:
        for item in state[kind + "s"]:
            item["starred"] = item["id"] in found[kind]


def star(project, kind, uid, starred):
    """Stars an item, or takes its star away."""
    if uid not in project[kind + "s"]:
        raise paths.NotFound(f"No {paths.NOUNS[kind]} {uid}")
    found = read()
    (found[kind].add if starred else found[kind].discard)(uid)
    _write(found)
    return {}


def moved(kind, uid, new=None):
    """An item that moved to the id `new` keeps its star there (`moves.rekey`: where `new` is starred already the two are one);
    without `new`, it was deleted and loses it."""
    if new:
        moves.rekey({f"{kind}:{uid}": new})
        return
    found = read()
    if uid not in found[kind]:
        return
    found[kind].discard(uid)
    _write(found)
