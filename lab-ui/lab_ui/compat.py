"""The versions of Gravitee a playground is tried on, so that what it says it was tested with (`x-gravitee.tested`) follows
Gravitee's releases, and the record of what passed. The catalogs' workflow *Gravitee versions* runs it every night, through
`catalog.py ci versions-plan` and `versions-record`: the plan here, a run of each set (`server.py --e2e ITEM --versions JSON`),
then the record.

A playground says the versions it was tested with as sets of version variables (`APIM_VERSION`, `AM_VERSION`) and their
registries; a minor version of Gravitee (4.13) is a line. Only the images of Gravitee are followed (`follow`, the variables whose
image comes from Gravitee's registry): another version in a set is carried over as it is. What it is tried on is, for each of the
MINORS newest lines that every image it follows publishes, the newest release of that line, and the newest pre-release of a line
that has no release yet, above every release. A line below what its `requires.images` asks is left out, and so is a set that is not
newer than the one it has for that line.

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
    """{(major, minor): the newest release of that line} among tag names, and the newest pre-release above every release of a
    line that has none (None for none). A system (4.13.1-debian) is no other version."""
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
    fresh = pre and (top is None or _order(pre) > _order(top)) and pre[0][:2] not in releases
    return releases, (pre if fresh else None)


# ---------------------------------------------------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------------------------------------------------

MAX_JOBS = 64   # the jobs of one night: GitHub runs 256 at most in a matrix, and the next night tries the rest


def _numeric(unit):
    """The sets of a playground that are releases or pre-releases of Gravitee's registry, as they are written."""
    return [found for found in unit.get("tested") or [] if found and _versions(found)]


def variables(unit, follow=None):
    """The version variables a playground is followed on, in the order its sets give them: those of its sets of releases, else,
    when it has no set at all, those its `requires.images` asks a release of; only those `follow` names, when given. Empty for
    a playground whose sets are all builds of its own: it is not followed."""
    sets = [found for found in unit.get("tested") or [] if found]
    found = []
    for tested in _numeric(unit):
        found += [key for key in tested if VERSION_KEY.fullmatch(key) and key not in found]
    if not sets:
        asked = (unit.get("requires") or {}).get("images") or {}
        found = [key for key, value in asked.items() if VERSION_KEY.fullmatch(key) and _numbered(value)]
    return [key for key in found if follow is None or key in follow]


def registries(unit, keys):
    """The registry each version variable is written with in the playground's sets of releases, {REGISTRY variable: value}."""
    found = {}
    for tested in _numeric(unit):
        for key, value in tested.items():
            if key.endswith("_REGISTRY") and key[:-len("_REGISTRY")] + "_VERSION" in keys:
                found.setdefault(key, str(value))
    return found


def _line_of(versions, keys):
    """The line of a set: the line of the first variable it is followed on."""
    return _line(versions[keys[0]])


def _newer(versions, than, keys):
    """Whether a set is newer than another of its line, variable by variable."""
    return [_order(versions[key]) for key in keys] > [_order(than[key]) for key in keys]


def candidates(unit, tags, follow=None):
    """The sets of versions to try a playground on: [{variable: version}], oldest first. `tags(variable)` gives the tag names of
    the image a version variable says; `follow`, the variables of Gravitee's images (None: every one)."""
    keys = variables(unit, follow)
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
    have = {}
    for found in _numeric(unit):
        versions = _versions(found)
        if all(key in versions for key in keys):
            line = _line_of(versions, keys)
            if line not in have or _newer(versions, have[line], keys):
                have[line] = versions
    latest = _numeric(unit)[-1] if _numeric(unit) else {}
    carried = {key: str(value) for key, value in latest.items()
               if key not in keys and key[:-len("_REGISTRY")] + "_VERSION" not in keys}
    extra = registries(unit, keys)
    sets = []
    for versions in chosen:
        if any(key in asked and not layers.at_least(_text(version), str(asked[key])) for key, version in versions.items()):
            continue
        line = _line_of(versions, keys)
        if line in have and not _newer(versions, have[line], keys):
            continue
        sets.append({**{key: _text(version) for key, version in versions.items()}, **extra, **carried})
    return sets


def plan(built, ns=None, tags=None, warn=None):
    """The jobs of a night: [{item, versions (a JSON object as text), name}] for each shared playground of the catalog `ns`
    (every catalog when None) and each set it is to be tried on, MAX_JOBS at most. `tags(built, variable, registry)` lists the
    tags of an image. A playground whose tags cannot be read is said to `warn(sentence)` and left for the next night."""
    import json
    from .docker import images
    tags = tags or (lambda project, key, registry: [tag["name"] for tag in images.tags(project, key, registry)])
    follow = {component["id"] for component in images.components(built)
              if component.get("registry_key") and "graviteeio" in str(component.get("registry_default") or "")} \
        if "services" in built else None
    jobs = []
    for uid, unit in sorted(built["playgrounds"].items()):
        if paths.personal(uid) or (ns is not None and unit.get("catalog") != ns):
            continue
        hub = registries(unit, variables(unit, follow))

        def listed(key, hub=hub):
            return tags(built, key, hub.get(key[:-len("_VERSION")] + "_REGISTRY", "graviteeio"))
        try:
            found = candidates(unit, listed, follow)
        except (OSError, ValueError, LookupError, RuntimeError) as error:
            if warn:
                warn(f"{unit['name']}: its versions could not be read ({' '.join(str(error).split())[:120]}): next night")
            continue
        for versions in found:
            said = ", ".join(f"{key} {value}" for key, value in versions.items() if VERSION_KEY.fullmatch(key) and key in
                             variables(unit, follow))
            jobs.append({"item": f"playground:{uid}", "versions": json.dumps(versions, sort_keys=True),
                         "name": f"{unit['name']} on {said}"})
    if len(jobs) > MAX_JOBS and warn:
        warn(f"{len(jobs)} sets to try: {MAX_JOBS} tonight, the rest the next nights")
    return jobs[:MAX_JOBS]


# ---------------------------------------------------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------------------------------------------------

def merged(sets, passed, keys=None):
    """The sets a playground says it was tested with once `passed` (sets that a run tried well) are in: a passed set replaces
    the set of its line when it is newer, a line out of the MINORS newest goes, and so does a pre-release whose line has a
    release. `keys`, the variables a set is judged on, in order (`variables`; those of the first set by default). The builds come
    first as they were, then the pre-releases, then the releases oldest first."""
    builds = [found for found in sets if found and not _versions(found)]
    numbered = [found for found in [*sets, *passed] if found and _versions(found)]
    if not numbered:
        return builds
    keys = keys or [key for key in numbered[0] if VERSION_KEY.fullmatch(key)]
    by_line = {}
    for found in numbered:
        versions = _versions(found)
        if not all(key in versions for key in keys):
            continue
        line = _line_of(versions, keys)
        kept = by_line.get(line)
        if kept is None or _newer(versions, _versions(kept), keys):
            by_line[line] = found
    releases = sorted(line for line, pre in by_line if not pre)
    releases = [line for line in releases if line in releases[-MINORS:]]
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


def _scalar(key, value):
    """A value as the file says it: a version in quotes, so that YAML never reads it as a number; a name (graviteeio) as it is,
    and anything else in quotes too."""
    text = str(value)
    if not VERSION_KEY.fullmatch(key) and re.fullmatch(r"[A-Za-z][A-Za-z0-9._/-]*", text) \
            and text.lower() not in ("true", "false", "yes", "no", "on", "off", "y", "n", "null"):
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _block(sets, order=()):
    lines = ["  tested:"]
    for found in sets:
        for index, (key, value) in enumerate(_ordered(found, order)):
            lines.append(f"    {'- ' if index == 0 else '  '}{key}: {_scalar(key, value)}")
    return "\n".join(lines) + "\n"


def write(text, sets):
    """The compose file of a playground (`text`) with `tested` saying `sets`: the block replaced where it is, or put after
    `requires`, at the end of `x-gravitee`. ValueError for a file this does not write the way a person would read it: line ends
    that are not LF, an `x-gravitee` that is not at the top level with its keys indented by two spaces, or a `tested` written in
    another form (a flow list, a list not indented under its key). What it wrote is checked by the record anyway
    (`catalog.py ci versions-record` reads the file again)."""
    if "\r" in text:
        raise ValueError("its line ends are not LF")
    start = re.search(r"^x-gravitee:[ \t]*(?:#[^\n]*)?\n", text, re.M)
    if not start:
        raise ValueError("it has no x-gravitee block at its top level")
    end = re.search(r"^[^\s#]", text[start.end():], re.M)
    end = start.end() + end.start() if end else len(text)
    body = text[start.end():end]
    if not re.match(r"(?:[ \t]*(?:#[^\n]*)?\n)*  [A-Za-z_]", body):
        raise ValueError("the keys of its x-gravitee are not indented by two spaces")
    found = TESTED.search(body)
    if found:
        block = found.group(0)
        if re.match(r"[ \t]*-", body[found.end():]) or not re.fullmatch(r"  tested:[ \t]*(?:#[^\n]*)?\n(?:    [- ] [A-Z][A-Z0-9_]*: [^\n]*\n|[ \t]*\n|    #[^\n]*\n)*", block):
            raise ValueError("its tested is not a list of sets indented under its key")
        order = list(dict.fromkeys(re.findall(r"^    [ -] ([A-Z][A-Z0-9_]*):", block, re.M)))
        trailing = block[len(block.rstrip("\n")) + 1:] if block.endswith("\n\n") else ""
        body = body[:found.start()] + _block(sets, order) + trailing + body[found.end():]
    else:
        if re.search(r"^  tested:", body, re.M):
            raise ValueError("its tested is not a list of sets indented under its key")
        if not body.endswith("\n"):
            body += "\n"
        keys = list(KEY.finditer(body))
        after = next((key for key in keys if key.group(0) == "  requires:"), None)
        place = next((key.start() for key in keys if after and key.start() > after.start()), None)
        place = place if place is not None else len(body.rstrip("\n")) + 1
        body = body[:place] + _block(sets) + body[place:]
    return text[:start.end()] + body + text[end:]


__all__ = ["MAX_JOBS", "MINORS", "candidates", "merged", "newest", "plan", "registries", "variables", "write"]
