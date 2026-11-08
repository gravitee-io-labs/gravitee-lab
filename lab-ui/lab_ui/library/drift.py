"""What differs between what Compose would give a service and what its container has: the settings (the environment). A
container that runs behind what is saved because its configuration changed (model.lateness) has a hash that says so and
nothing more: this reads the environment Compose would give each service and the one the container has, and names the
settings that differ. It compares digests and keeps nothing else (compose.digest): a value is never held, and never said."""

import json

from ..docker import compose
from . import project

_found = {}   # (container, the hash it was made from, the hash Compose makes now) -> [settings]: for good


def _wanted(key):
    """What Compose makes each service with now: {service: {variable: digest}}; None when Compose cannot say."""
    def ask():
        return {name: _digests(service.get("environment"), True) for name, service in compose.resolved([]).items()}
    try:
        return compose.CACHE.get(f"environment:{key}", 300, ask)
    except compose.ComposeError:
        return None


def _digests(pairs, escaped=False):
    """{name: digest of its value} of what Compose says of a mapping, or of a list of "name=value"; nothing unset.
    `escaped`: what `compose config` prints, which writes a dollar sign that is no variable's as two ("$${gravitee.home}"
    for the "${gravitee.home}" the container has): it is read as the container has it."""
    if isinstance(pairs, list):
        pairs = dict(item.partition("=")[::2] for item in pairs)
    return {name: compose.digest(str(value).replace("$$", "$") if escaped else str(value))
            for name, value in (pairs or {}).items() if value is not None}


def _running(container):
    """What a container has: {variable: digest}; None when Docker cannot say."""
    rc, out, _ = compose.run(["docker", "container", "inspect", "--format", "{{json .Config.Env}}", container], timeout=20)
    return _digests(json.loads(out) or []) if rc == 0 else None


def _named(raw, service, variable):
    """What a person edits for this variable of a service: the setting its value refers to, when one file sets it and
    refers to one; else the variable's own name."""
    written = [found for config in raw.values()
               for found in [((config.get("services") or {}).get(service) or {}).get("environment") or {}]
               if variable in found]
    refs = {key for text in written[:1] for key, _, _ in compose.references(str(text[variable]))}
    return sorted(refs) if len(written) == 1 and refs else [variable]


def changes(raw, containers, causes, saved):
    """{service: [the settings whose value differs, by name]} for the services of `causes` (model.lateness) that run behind by
    their configuration, whose container differs from what Compose would give it in its environment. A service whose
    difference is elsewhere, or cannot be read, is left out: its line then says the configuration changed."""
    found = {}
    for name in (name for name, cause in causes.items() if cause == "config"):
        container, made = containers[name], saved["hashes"][name]
        key = (container["name"], container["config_hash"], made)
        if key not in _found:
            wanted, running = _wanted(project.config_key()), _running(container["name"])
            if wanted is None or running is None:
                continue   # not known now: asked again next time
            # A variable the container lacks came with a changed file (a Lab update), not with a setting: the configuration
            variables = [variable for variable, digest in (wanted.get(name) or {}).items()
                         if variable in running and running[variable] != digest]
            _found[key] = sorted({setting for variable in variables for setting in _named(raw, name, variable)})
        if _found[key]:
            found[name] = _found[key]
    return found
