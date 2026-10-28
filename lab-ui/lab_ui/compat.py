"""The versions of Gravitee a playground is tried on, so that what it says it was tested with (`x-gravitee.tested`) follows
Gravitee's releases, and the record of what passed. The catalogs' workflow *Gravitee versions* runs it every night, through
`.github/scripts/tested_versions.py`: the plan here, a run of each set (`server.py --e2e ITEM --versions JSON`), then the record.

A playground says the versions it was tested with as sets of version variables (`APIM_VERSION`, `AM_VERSION`) and their
registries; a minor version of Gravitee (4.13) is a line. What it is tried on is, for each of the MINORS newest lines that every
image of its sets publishes, the newest release of that line, and the newest pre-release above every release, when each of its
images has one of the same line. A line below what its `requires.images` asks is left out, and so is a set it has already.

What passes replaces the set of its line, or adds one. A line that falls out of the MINORS newest goes, and so does a pre-release
once a release of its line is there. A set of a build that is no version (amp-demo-latest), and a registry that is not Docker
Hub's, are the playground's own: never tried here, never touched. The sets are written with the builds first, then the
pre-releases, then the releases oldest first: the last set is the one a Start offers to switch to (`layers.untested`), a release."""

import re

from . import paths
from .library import layers
from .server.dashboard import parse, _order, _text

MINORS = 5   # the minor versions of Gravitee a playground keeps up with: the newest five
VERSION_KEY = re.compile(r"[A-Z][A-Z0-9_]*_VERSION")
HUB = ("graviteeio", "docker.io/graviteeio")   # where Gravitee publishes its releases: a set from elsewhere is a build of its own


# ---------------------------------------------------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------------------------------------------------

def _versions(found):
    """The version variables of a set and their parsed versions, {variable: (numbers, pre-release)}; None when one of them is
    no version (a build that moves, amp-demo-latest) or when it names a registry Gravitee does not publish to."""
    parsed = {}
    for key, value in found.items():
        if key.endswith("_REGISTRY") and str(value) not in HUB:
            return None
        if VERSION_KEY.fullmatch(key):
            version = parse(str(value))
            if version is None:
                return None
            parsed[key] = version
    return parsed or None


def _numbered(asked):
    """Whether what `requires.images` asks is a version (4.12, 4.13.0-alpha.3), and not a build of its own (amp-demo-latest)."""
    return bool(re.match(r"\d+(?:\.\d+)*(?:-|$)", str(asked)))


def _line(version):
    """The line a version is of: its minor version, and whether it is a pre-release."""
    numbers, pre = version
    return numbers[:2], pre is not None


def newest(tags):
    """{(major, minor): the newest release of that line} among tag names, and the newest pre-release above every release
    (None for none). A system (4.13.1-debian) is no other version."""
    releases, pres = {}, []
    for version in filter(None, map(parse, tags)):
        if version[1] is None:
            line = version[0][:2]
            if line not in releases or _order(version) > _order(releases[line]):
                releases[line] = version
        else:
            pres.append(version)
    top = max(releases.values(), key=_order, default=None)
    pre = max(pres, key=_order, default=None)
    return releases, (pre if pre and (top is None or _order(pre) > _order(top)) else None)


# ---------------------------------------------------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------------------------------------------------

def variables(unit):
    """The version variables a playground's versions are about, in the order its sets give them: those of its sets of
    releases, else those its `requires.images` asks a release of. Empty when it says nothing of a release (only builds of
    its own): it is not followed."""
    found = []
    for tested in unit.get("tested") or []:
        if tested and _versions(tested):
            found += [key for key in tested if VERSION_KEY.fullmatch(key) and key not in found]
    if not found:
        asked = (unit.get("requires") or {}).get("images") or {}
        found = [key for key, value in asked.items() if VERSION_KEY.fullmatch(key) and _numbered(value)]
    return found


def registries(unit, keys):
    """The registry each version variable is written with in the playground's sets of releases, {REGISTRY variable: value}."""
    found = {}
    for tested in unit.get("tested") or []:
        if tested and _versions(tested):
            for key, value in tested.items():
                if key.endswith("_REGISTRY") and key[:-len("_REGISTRY")] + "_VERSION" in keys:
                    found.setdefault(key, str(value))
    return found


def candidates(unit, tags):
    """The sets of versions to try a playground on: [{variable: version}], oldest first. `tags(variable)` gives the tag names of
    the image a version variable says."""
    keys = variables(unit)
    if not keys:
        return []
    asked = (unit.get("requires") or {}).get("images") or {}
    if any(key in asked and not _numbered(asked[key]) for key in keys):
        return []   # it asks a build of its own: only that build runs it
    lines = {key: newest(tags(key)) for key in keys}
    common = set.intersection(*(set(releases) for releases, _ in lines.values()))
    chosen = [{key: lines[key][0][line] for key in keys} for line in sorted(common)[-MINORS:]]
    pres = [lines[key][1] for key in keys]
    if all(pres) and len({pre[0][:2] for pre in pres}) == 1:
        chosen.append(dict(zip(keys, pres)))
    have = [found for found in map(_versions, unit.get("tested") or []) if found]
    extra = registries(unit, keys)
    sets = []
    for versions in chosen:
        if any(key in asked and not layers.at_least(_text(version), str(asked[key])) for key, version in versions.items()):
            continue
        if any(all(found.get(key) == version for key, version in versions.items()) for found in have):
            continue
        sets.append({**{key: _text(version) for key, version in versions.items()}, **extra})
    return sets


def plan(built, ns=None, tags=None):
    """The jobs of a night: [{item, versions (a JSON object as text), name}] for each shared playground of the catalog `ns`
    (every catalog when None) and each set it is to be tried on. `tags(built, variable, registry)` lists the tags of an image."""
    import json
    from .docker import images
    tags = tags or (lambda project, key, registry: [tag["name"] for tag in images.tags(project, key, registry)])
    jobs = []
    for uid, unit in sorted(built["playgrounds"].items()):
        if paths.personal(uid) or (ns is not None and unit.get("catalog") != ns):
            continue
        hub = registries(unit, variables(unit))

        def listed(key, unit=unit, hub=hub):
            return tags(built, key, hub.get(key[:-len("_VERSION")] + "_REGISTRY", "graviteeio"))
        for versions in candidates(unit, listed):
            said = ", ".join(f"{key} {value}" for key, value in versions.items() if not key.endswith("_REGISTRY"))
            jobs.append({"item": f"playground:{uid}", "versions": json.dumps(versions, sort_keys=True),
                         "name": f"{unit['name']} on {said}"})
    return jobs


# ---------------------------------------------------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------------------------------------------------

def merged(sets, passed):
    """The sets a playground says it was tested with once `passed` (sets that a run tried well) are in: each passed set
    replaces the set of its line, a line out of the MINORS newest goes, and so does a pre-release whose line has a release. The
    builds come first as they were, then the pre-releases, then the releases oldest first."""
    builds = [found for found in sets if found and not _versions(found)]
    by_line = {}
    for found in [*(found for found in sets if found and _versions(found)), *passed]:
        versions = _versions(found)
        first = next(iter(versions.values()))
        line = _line(first)
        kept = by_line.get(line)
        if kept is None or [_order(v) for v in versions.values()] >= [_order(v) for v in _versions(kept).values()]:
            by_line[line] = found
    releases = sorted((line for line, pre in by_line if not pre))[-MINORS:]
    pres = sorted(line for line, pre in by_line if pre and line not in releases and (not releases or line > releases[-1]))
    return [*builds, *(by_line[(line, True)] for line in pres), *(by_line[(line, False)] for line in releases)]


TESTED = re.compile(r"^  tested:[^\n]*\n(?:(?:    [^\n]*|[ \t]*)\n)*", re.M)
KEY = re.compile(r"^  [A-Za-z_][A-Za-z0-9_-]*:", re.M)


def _ordered(found, order=()):
    """A set with each version variable followed by its registry, as a person writes it, the variables in the `order` the file
    gave them (`APIM_VERSION` before `AM_VERSION`), then the others."""
    keys = []
    for key in sorted(found, key=lambda key: list(order).index(key) if key in order else len(order)):
        if VERSION_KEY.fullmatch(key):
            keys += [key, *([key[:-len("_VERSION")] + "_REGISTRY"] if key[:-len("_VERSION")] + "_REGISTRY" in found else [])]
    return [(key, found[key]) for key in [*keys, *(key for key in found if key not in keys)]]


def _block(sets, order=()):
    lines = ["  tested:"]
    for found in sets:
        for index, (key, value) in enumerate(_ordered(found, order)):
            text = f'"{value}"' if VERSION_KEY.fullmatch(key) else str(value)
            lines.append(f"    {'- ' if index == 0 else '  '}{key}: {text}")
    return "\n".join(lines) + "\n"


def write(text, sets):
    """The compose file of a playground (`text`) with `tested` saying `sets`: the block replaced where it is, or put after
    `requires`, at the end of `x-gravitee`. ValueError for a file whose `x-gravitee` is not where this reads it (at the top level,
    its keys indented by two spaces)."""
    start = re.search(r"^x-gravitee:[^\n]*\n", text, re.M)
    if not start:
        raise ValueError("The file has no x-gravitee at its top level")
    end = re.search(r"^\S", text[start.end():], re.M)
    end = start.end() + end.start() if end else len(text)
    body = text[start.end():end]
    found = TESTED.search(body)
    if found:
        order = list(dict.fromkeys(re.findall(r"^    [ -] ([A-Z][A-Z0-9_]*):", found.group(0), re.M)))
        body = body[:found.start()] + _block(sets, order) + body[found.end():]
    else:
        keys = list(KEY.finditer(body))
        after = next((key for key in keys if key.group(0) == "  requires:"), None)
        place = next((key.start() for key in keys if after and key.start() > after.start()), None)
        place = place if place is not None else len(body.rstrip("\n")) + 1
        body = body[:place] + _block(sets) + body[place:]
    return text[:start.end()] + body + text[end:]


__all__ = ["MINORS", "candidates", "merged", "newest", "plan", "registries", "variables", "write"]
