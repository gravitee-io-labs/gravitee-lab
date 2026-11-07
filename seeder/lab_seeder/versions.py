"""The versions of Gravitee the seed fills, read once from the platform at the start, and what changed from one release to the
next.

A release (4.13.3) is seeded by its version: below MINIMUM the seed stops before it changes anything, and a module asks
`since(name)` whether a change of CHANGES is in it. A build that is no release (4.13.0-amp-demo-SNAPSHOT, a master build),
and a platform that does not say its version, are seeded as a build: `since` answers None, and the module adapts to what
the platform answers ("What differs from one build to another" in docs/playgrounds.md). docs/playgrounds.md says CHANGES and
MINIMUM for people, and a test keeps the two equal.

Where the version comes from, with the credentials of the seed: API Management says it for each gateway that reports to it
(the instances of the environment that are started), Access Management for the plugins it ships with (its reporters). The
technical APIs that say it directly listen on their container's own address, and the modules and plugins of API Management
have numbers of their own (4.13.8 in 4.13.3): with no gateway started, API Management says no version."""

import re

from . import am, apim
from .common import HttpError, SeedError, has_seed, items, log

SECTION = "versions"
APIM, AM = "API Management", "Access Management"
MINIMUM = (4, 12)   # the first version Gravitee Lab was made for: each later release is kept
RELEASE = re.compile(r"(\d+)\.(\d+)\.(\d+)")   # 4.13.3, not 4.13.0-amp-demo-SNAPSHOT
# What changed from one release to the next: {name: (product, the first release that has it, what it is)}
CHANGES = {
    "gamma": (APIM, (4, 13, 0), "The Gamma modules: AI Management, Authorization Management"),
}

_found = {}   # product -> (what the platform said, the release it is or None)


def release(text):
    """(major, minor, patch) of a version that is a release, None for a build that is none, or no version."""
    found = RELEASE.fullmatch(str(text or "").split(" ", 1)[0])
    return tuple(map(int, found.groups())) if found else None


def note(product, said):
    """Keeps the version the platform said of `product`, from its answers: one version when they agree, else none. Stops the
    seed below MINIMUM, and says the regime the seed takes."""
    texts = {str(text).split(" ", 1)[0] for text in said if text}
    text = texts.pop() if len(texts) == 1 else None
    numbers = release(text)
    _found[product] = (text, numbers)
    shown = ".".join(map(str, MINIMUM))
    if numbers and numbers[:2] < MINIMUM:
        raise SeedError(f"{product} {text}: the seeder fills {shown} and later releases")
    if numbers:
        log(SECTION, f"{product} {text}: a release, seeded by its version")
    else:
        log(SECTION, f"{product} {text or 'says no version'}: not a release, the seed adapts to what it answers")


def released(product):
    """Whether the product is a release, read at the start of the seed: what every release takes is sent to it."""
    return _found.get(product, (None, None))[1] is not None


def since(name):
    """Whether the release of the product holds the change `name` of CHANGES; None when the product is no release or was not
    read: the module then adapts to what the platform answers."""
    product, first, _ = CHANGES[name]
    numbers = _found.get(product, (None, None))[1]
    return None if numbers is None else numbers >= first


def read():
    """The versions of the products the seed fills, once at its start. A refusal of the question is no version."""
    if has_seed("apim", "environments", "aim", "authz"):   # what API Management fills, or Gamma, which it serves
        platform = apim.ApiManagement()
        platform.connect()
        note(APIM, _said(platform.api, f"{platform.v1}/instances", lambda row: row.get("state") == "STARTED"))
    if has_seed("am"):
        manager = am.AccessManagement()
        manager.connect()
        note(AM, _said(manager.api, "/management/platform/plugins/reporters", lambda row: True))


def _said(api, path, keep):
    try:
        return [row.get("version") for row in items(api.get(path, expect=(200,)).json()) if isinstance(row, dict) and keep(row)]
    except (HttpError, ValueError):
        return []
