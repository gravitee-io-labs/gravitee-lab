"""What a playground started on, on this machine: each time one starts well, the versions of its images are kept, so that
starting it again on them is not a warning (layers.untested). The versions its files declare as tested are the authors'; these
are what worked here, kept in `.lab/tested.json`, which is not shared. A tag that is not a release (a demo build, `latest`)
is kept with the image it was, since it is rebuilt under the same name: the same name on another image is not the version
that worked."""

import json
import time

from ..docker import compose
from . import layers, moves
from .. import paths

FILE = f"{paths.LAB_DIR}/tested.json"   # relative to the project
KEPT = 20   # per playground: the latest ones


def read():
    """{playground id: [{versions, images, at}]}, oldest first; nothing when the file is not there or is not readable."""
    try:
        found = json.loads((paths.ROOT / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _kept(pid):
    kept = read().get(pid)
    return [one for one in kept if isinstance(one, dict) and isinstance(one.get("versions"), dict)] if isinstance(kept, list) else []


def sets(pid):
    """What started well on this machine, for a playground: [{versions, images, at}], those that name a version."""
    return [one for one in _kept(pid) if one["versions"]]


def ran(pid):
    """Whether the playground ever started well on this machine, its seed ended without an error included: a playground that
    judges no image has nothing to name, and its start is kept all the same, as a set with no version."""
    return bool(_kept(pid))


def image_id(service):
    """The image a service runs, or would run: the id that tells a tag rebuilt from the one that was. Kept a few seconds, and
    dropped when a job ends (`local:`)."""
    return compose.CACHE.get(f"local:image-id:{service['container']}", 30, lambda: compose.image_key(service))


def untested(project, playground):
    """layers.untested, with what started well on this machine."""
    return layers.untested(project, playground, sets(playground.get("id")), image_id)


def variables(playground):
    """The version variables whose images a playground is judged on: those it asks a minimum of and those it declares tested."""
    return list(dict.fromkeys([*(playground["requires"].get("images") or {}),
                               *(variable for tested in playground.get("tested") or [] for variable in tested)]))


def record(project, pid):
    """Keeps what the playground is started on, once it started well: the saved tag of each image it is judged on, and the
    image it is. The same as the last time changes nothing but its date."""
    playground = layers.lookup(project, "playground", pid)
    versions, images = {}, {}
    for variable in variables(playground):
        found = layers.saved_setting(project, variable)
        if found:
            versions[variable] = found[0]
            if not variable.endswith("_REGISTRY"):
                images[variable] = image_id(layers.service_of(project, variable)[1])
    held = read()
    others = [one for one in sets(pid) if (one["versions"], one.get("images")) != (versions, images)]
    held[pid] = (others + [{"versions": versions, "images": images, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}])[-KEPT:]
    _write(held)


def _write(held):
    """Replaces the file whole, private as it always was."""
    target = paths.ROOT / FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    paths.write_atomic(target, json.dumps(held, indent=2, sort_keys=True), mode=0o600)


def moved(kind, old, new=None):
    """A playground that became a catalog's, or was renamed, takes what started well on this machine with it to the id `new`
    (`moves.rekey`: where `new` has sets already the two are joined, the same versions once); one deleted without `new` loses
    its sets. Only a playground has tested sets: any other kind is no business of this file."""
    if kind != "playground":
        return
    if new:
        moves.rekey({f"{kind}:{old}": new})
        return
    held = read()
    if old in held:
        del held[old]
        _write(held)
