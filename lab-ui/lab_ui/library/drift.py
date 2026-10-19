"""What differs between what Compose would give a service and what its container has: the settings (the environment) and
the labels. A container that runs behind what is saved because its configuration changed (model.lateness) has a hash that
says so and nothing more: this reads the environment and the labels Compose would give each service and the ones the
container has, and names the settings that differ. The labels are said as a kind only (their names are for Compose, not
for a person). It compares digests and keeps nothing else (compose.digest): a value is never held, and never said."""

import json

from ..docker import compose
from . import project

_found = {}   # (container, the hash it was made from, the hash Compose makes now) -> {settings, labels}: for good


def _wanted(key):
    """What Compose makes each service with now: {service: {"env": {variable: digest}, "labels": {label: digest}}};
    None when Compose cannot say."""
    def ask():
        try:
            services = compose.resolved([])
        except compose.ComposeError:
            return None
        return {name: {"env": _digests(service.get("environment"), True), "labels": _digests(service.get("labels"), True)}
                for name, service in services.items()}
    return compose.CACHE.get(f"environment:{key}", 300, ask)


def _digests(pairs, escaped=False):
    """{name: digest of its value} of what Compose says of a mapping, or of a list of "name=value"; nothing unset.
    `escaped`: what `compose config` prints, which writes a dollar sign that is no variable's as two ("$${gravitee.home}"
    for the "${gravitee.home}" the container has): it is read as the container has it."""
    if isinstance(pairs, list):
        pairs = dict(item.partition("=")[::2] for item in pairs)
    return {name: compose.digest(str(value).replace("$$", "$") if escaped else str(value))
            for name, value in (pairs or {}).items() if value is not None}


def _running(container):
    """What a container has: {"env": {variable: digest}, "labels": {label: digest}}; None when Docker cannot say."""
    rc, out, _ = compose.run(["docker", "container", "inspect", "--format",
                              "{{json .Config.Env}}{{println}}{{json .Config.Labels}}", container], timeout=20)
    if rc != 0:
        return None
    env, _, labels = out.strip().partition("\n")
    return {"env": _digests(json.loads(env) or []), "labels": _digests(json.loads(labels or "null"))}


def _named(raw, service, variable):
    """What a person edits for this variable of a service: the setting its value refers to, when one file sets it and
    refers to one; else the variable's own name."""
    written = [found for config in raw.values()
               for found in [((config.get("services") or {}).get(service) or {}).get("environment") or {}]
               if variable in found]
    refs = {key for text in written[:1] for key, _, _ in compose.references(str(text[variable]))}
    return sorted(refs) if len(written) == 1 and refs else [variable]


def changes(p, raw, containers, causes, saved):
    """{service: {"settings": [the settings that differ, by name], "labels": whether a label differs}} for the services
    of `causes` (model.lateness) that run behind by their configuration, whose container differs from what Compose would
    give it in its environment or its labels. A service whose difference is in neither, or cannot be read, is left out:
    its line then says the configuration changed. Only the labels Compose sets are compared: a container also carries
    those of its image."""
    found = {}
    for name in (name for name, cause in causes.items() if cause == "config"):
        container, made = containers[name], saved["hashes"][name]
        key = (container["name"], container["config_hash"], made)
        if key not in _found:
            wanted, running = _wanted(project.config_key()), _running(container["name"])
            if wanted is None or running is None:
                continue   # not known now: asked again next time
            now = wanted.get(name) or {"env": {}, "labels": {}}
            variables = [variable for variable, digest in now["env"].items() if running["env"].get(variable) != digest]
            _found[key] = {"settings": sorted({setting for variable in variables for setting in _named(raw, name, variable)}),
                           "labels": any(running["labels"].get(label) != digest for label, digest in now["labels"].items())}
        if _found[key]["settings"] or _found[key]["labels"]:
            found[name] = _found[key]
    return found
