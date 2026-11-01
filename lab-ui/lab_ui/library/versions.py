"""An image tag read as a version, its system left out (bare, from products.VARIANTS): whether it is a version or a later one,
as the gate of a playground's requirements asks it (at_least), and the order of the builds that the Dashboard and the reasons
of a service compare (parse, order, written, older, latest, channel, judge). Pure."""

import re

from ..products import VARIANTS

# A tag that names a build: three numbers, then a pre-release that ends with one (alpha.4); not 4.12, latest or alpha
BUILD = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]*\d))?")


def bare(tag):
    """(the tag without its system, the system or ""): "4.13.0-alpha.8-debian" gives ("4.13.0-alpha.8", "debian"). Every
    version of an image is compared so."""
    tag = str(tag or "")
    for variant in VARIANTS:
        if tag.endswith(f"-{variant}"):
            return tag[:-len(variant) - 1], variant
    return tag, ""


def split(text):
    """A version as it orders, its system left out (bare): (its numbers, its pre-release identifiers or None).
    "4.13.0-alpha.3-debian" is ((4, 13, 0), ["alpha", "3"]), "4.13.1-chainguard" ((4, 13, 1), None); None for a tag
    without numbers."""
    found = re.match(r"(\d+(?:\.\d+)*)(?:-([0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*))?", bare(text)[0])
    if not found:
        return None
    return tuple(map(int, found.group(1).split("."))), found.group(2) and found.group(2).split(".")


def at_least(tag, wanted):
    """Whether an image tag is that version or a later one, as semantic versioning orders them: a pre-release comes before
    its release, so 4.13.0-alpha.5 is not a 4.13 (nor a 4.13.0), and a version asked in release form is met by a release.
    A wanted pre-release orders the previews of its version too: 4.13.0-alpha.2 is before 4.13.0-alpha.3, itself before
    4.13.0-beta.1 and 4.13.0; a later version, a preview included, is after them all (4.14.0-alpha.1 is a 4.13). A tag
    without numbers (latest) says nothing of its version: it is taken as fine. A version asked that has no numbers
    (amp-demo-latest, a build and not a release) is not ordered: it is that build or it is not."""
    have, want = split(tag), split(wanted)
    if not want:
        return bare(tag)[0] == bare(wanted)[0]
    if not have:
        return True
    size = max(len(have[0]), len(want[0]))
    numbers = [version[0] + (0,) * (size - len(version[0])) for version in (have, want)]
    if numbers[0] != numbers[1]:
        return numbers[0] > numbers[1]
    if not want[1]:
        return not have[1]   # a preview comes before its release
    if not have[1]:
        return True   # the release comes after its previews
    # Identifier by identifier, as semantic versioning orders them: numbers by value, before words
    def key(part):
        return (0, int(part), "") if part.isdigit() else (1, 0, part)
    return [key(part) for part in have[1]] >= [key(part) for part in want[1]]


def parse(tag):
    """(numbers, pre-release or None) of a tag that names a build, its system ignored ("4.13.0-alpha.8-debian" gives
    ((4, 13, 0), "alpha.8")); None for a tag that moves (latest, 4.12, 4.13.0-alpha)."""
    found = BUILD.fullmatch(bare(tag)[0])
    return (tuple(int(number) for number in found.groups()[:3]), found.group(4)) if found else None


def order(version):
    """What orders builds (parse), as semver does: a release above its pre-releases, the identifiers of a pre-release one
    by one, numbers before words."""
    numbers, pre = version
    if pre is None:
        return numbers, 1, ()
    return numbers, 0, tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in pre.split("."))


def written(version):
    """A build (parse) written as a tag without its system."""
    numbers, pre = version
    return ".".join(map(str, numbers)) + (f"-{pre}" if pre else "")


def older(saved, running):
    """Whether the tag saved names an older version than the one that runs, their systems ignored: making it again goes
    back. False when either names no version (a tag that moves, a local build)."""
    one, other = parse(saved or ""), parse(running or "")
    return bool(one and other) and order(one) < order(other)


def latest(tags):
    """(the latest stable, the latest pre-release) among tag names, each as a version without its system; None for
    none. A pre-release is listed only above the stable one: once 4.13.0 is out, 4.13.0-alpha.8 is not news."""
    found = [version for version in map(parse, tags) if version]
    stable = max((version for version in found if version[1] is None), key=order, default=None)
    pre = max((version for version in found if version[1] is not None), key=order, default=None)
    if pre and stable and order(pre) < order(stable):
        pre = None
    return written(stable) if stable else None, written(pre) if pre else None


def channel(deployed):
    """The channel a version is of: 'pre-release' for a build with a pre-release part (4.13.0-alpha.8), else 'stable',
    also for a tag that names no version."""
    found = parse(deployed or "")
    return "pre-release" if found and found[1] is not None else "stable"


def judge(deployed, stable, prerelease):
    """(status, latest): the newest version of the channel of the deployed one, and whether it is that one. 'latest'
    when nothing above the deployed version is known (the latest then is the highest version known, the deployed one
    when the registry lists none that high), 'newer' when the channel has a version above it, 'unknown' (and none)
    when a version is missing or moves. A stable version is compared with the latest stable, a pre-release with the
    latest pre-release, else with the stable one: a release is above its pre-releases."""
    here, other = parse(deployed or ""), parse(stable or "")
    ahead = parse(prerelease or "") if here and here[1] is not None and prerelease else other
    if not here or not ahead:
        return "unknown", None
    if order(here) < order(ahead):
        return "newer", written(ahead)
    return "latest", written(max(here, ahead, key=order))
