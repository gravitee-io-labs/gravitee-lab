"""The versions of Gravitee a seed fills, read once at its start, and what the seed does differently on some of them.

A version is one of two kinds, and each has its own path:

- **A release** (4.12.2, 4.13.3, 4.14.28) is seeded by RELEASES. Most of what a seed makes is made the same way on every
  release and has no row. A behavior that changed has one: the first release of each of its variants, in order. A release
  takes the variant of the last row at or below it, so a new release takes the latest variant until a row says otherwise.
  Below MINIMUM the seed stops before it changes anything.
- **A build off the release channel** (4.13.0-amp-demo-SNAPSHOT, a master build) is seeded by BUILDS, by its product and
  its exact version: the release it is seeded like, and each behavior where it differs from that release. A version that is
  neither a release nor a build of BUILDS stops the seed: nothing is guessed.

A module never compares versions: it asks `variant(name)` and acts on the variant it gets. docs/playgrounds.md shows both
tables, which `markdown()` writes and a test keeps equal. Each row was read on the platform itself, and seeder/compat.py
measures every behavior on the image of a management API (`make compat`), which a new release goes through before its rows
are trusted.

Where the version comes from, with the credentials of the seed: API Management states it for each gateway that reports to it
(the instances of its environment that are started, "4.13.3 (build: 2233858) revision#a4d3"), Access Management for the
plugins it ships with (its reporters). Gateways of several releases make the oldest one, as the Lab counts an environment of
Gravitee Cloud. The technical API that states the version of the management API itself (`/_node`) also serves the whole
configuration of the node, license and database addresses included, so the lab keeps it inside each container: the version
is the gateways', which the Lab runs from the same setting as the management API (APIM_VERSION)."""

import re
from dataclasses import dataclass, field

from . import am, apim
from .common import HttpError, SeedError, has_seed, items, log, wait_until

SECTION = "versions"
APIM, AM = "API Management", "Access Management"
MINIMUM = "4.12.0"   # the first release Gravitee Lab was made for: each later release is kept
RELEASE = re.compile(r"(\d+)\.(\d+)\.(\d+)")   # 4.13.3, not 4.13.0-amp-demo-SNAPSHOT
STATED = 60   # seconds a gateway that has just started is given to be listed by API Management


@dataclass(frozen=True)
class Behavior:
    """Something a seed does differently from one release to another."""
    product: str
    what: str        # what differs, as the docs say it
    since: dict      # {first release: variant}, in the order of the releases
    variants: dict   # {variant: what the seed does then}


@dataclass(frozen=True)
class Build:
    """A build off the release channel, known by its product and the exact version it states."""
    what: str                                    # what the build is
    like: str                                    # the release it is seeded like
    differs: dict = field(default_factory=dict)  # {behavior: the variant it takes instead of the release's}
    files: str = ""                              # what a seed file writes for this build, where the seeder decides nothing


# What changed from one release to the next. Add a row when a release changes something a seed makes, with the first
# release that has the new variant, and keep the old variant for the releases before it.
RELEASES = {
    "gamma-modules": Behavior(
        APIM, "The Gamma modules the seeder fills: AI Management, Authorization Management",
        {"4.12.0": "early", "4.13.0": "served"},
        {"early": "The platform ships versions 1 of both, whose routes the seeder does not fill (AI Management has no AI "
                  "providers): a playground that seeds `aim/` or `authz/` stops before anything is asked of Gamma",
         "served": "The seed asks the platform which modules it serves"}),
    "mcp-protocol-versions": Behavior(
        APIM, "The MCP protocol versions of a studio and of an MCP proxy",
        {"4.13.0": "list"},
        {"list": "A studio is made with `supportedProtocolVersions`, a list, from either key of its file, and an MCP proxy takes "
                 "the `supportedProtocolVersions` and `endpointProtocolVersions` of its file",
         "single": "A studio is made with `protocolVersion`, the one value its file gives, and the lists of an MCP proxy file are "
                   "skipped with a warning: this build sets no protocol versions on an MCP proxy"}),
    "agent-application": Behavior(
        APIM, "The application of an agent of the AI catalog (`identity` and `access` of an agent file)",
        {"4.13.0": "absent"},
        {"absent": "The seed stops on an agent file that gives them, before any call, with what to write instead",
         "served": "The seed makes the application and subscribes it"}),
    "dashboard-module": Behavior(
        APIM, "The module of a dashboard",
        {"4.13.0": "required"},
        {"required": "A dashboard is created with the module `aim`",
         "none": "A dashboard is created with no module"}),
    "authz-schema-documents": Behavior(
        APIM, "Named schema documents of Authorization Management (`seed/authz/schemas/`)",
        {"4.13.0": "several"},
        {"several": "Each file of `seed/authz/schemas/` is a document of its own",
         "one": "The files of `seed/authz/schemas/` are skipped with a warning: `seed/authz/schema.yaml` is the one document"}),
    "governance": Behavior(
        APIM, "Approvals and compliance of Agent Management (`seed/aim/governance/`)",
        {"4.13.0": "absent"},
        {"absent": "A playground that seeds them stops before any change, with where they are served",
         "served": "The seed writes the approval settings and rules, and the compliance answers"}),
    "studio-human-approval": Behavior(
        APIM, "Human approval on a studio (`humanApproval` of a studio file)",
        {"4.13.0": "absent"},
        {"absent": "A studio file that gives it stops the seed before any change, with where it is served",
         "served": "The seed switches it and writes the hold window of its flow"}),
}

# Builds off the release channel, by their product and the exact version they state. Each is seeded like a release, except
# where it differs.
BUILDS = {
    (APIM, "4.13.0-amp-demo-SNAPSHOT"): Build(
        "The amp-demo build of API Management", "4.13.0",
        {"mcp-protocol-versions": "single", "agent-application": "served", "dashboard-module": "none",
         "authz-schema-documents": "one", "governance": "served", "studio-human-approval": "served"},
        "The models of an AI workspace are a component with an endpoint of its own (`target`, a price on each model)"),
    (AM, "4.14.0-SNAPSHOT"): Build("A master build of Access Management, before 4.14.0", "4.13.6"),
}

# Releases whose images state the version of a later release, on the management API and on the gateway alike (read in the
# images 4.13.0 and 4.13.1 of Docker Hub): {(product, version stated): the release it is}. A seed takes the version stated,
# so no row may start between the two, which a test holds: both get the same variants.
STATED_LATER = {(APIM, "4.13.4"): "4.13.0", (APIM, "4.13.5"): "4.13.1"}

_found = {}   # product -> what it stated, kept for the run: (version text, the release it is seeded as, its Build or None)


def release(text):
    """(major, minor, patch) of a release, None for any other text."""
    found = RELEASE.fullmatch(str(text or "").split(" ", 1)[0])
    return tuple(map(int, found.groups())) if found else None


def numbers(text):
    """(major, minor, patch) of a release written in a table, which a test holds to be one."""
    return tuple(map(int, text.split(".")))


# Where each product states its version, said when it states none
WHERE = {APIM: "it states it for each gateway that reports to it, so start one",
         AM: "it states it for the plugins it ships with, its reporters"}


def note(product, said):
    """Keeps the version `product` stated, from its answers, and the path it takes: a release (the oldest, when its components
    state several), or a build of BUILDS. Stops the seed when the answers state no version, a build beside another version, a
    release below MINIMUM or a version that is neither a release nor a build BUILDS knows."""
    texts = sorted({str(text).split(" ", 1)[0] for text in said if text})
    if not texts:
        raise SeedError(f"{product} states no version: {WHERE[product]}")
    if len(texts) > 1:
        if not all(release(text) for text in texts):
            raise SeedError(f"{product} states several versions ({', '.join(texts)}): a build is seeded alone")
        log(SECTION, f"{product} states {', '.join(sorted(texts, key=release))}: seeded as the oldest")
    text = min(texts, key=release) if len(texts) > 1 else texts[0]
    build = BUILDS.get((product, text))
    if build:
        _found[product] = (text, numbers(build.like), build)
        log(SECTION, f"{product} {text}: {build.what}, seeded like {build.like} except where it differs")
        return
    seeded = release(text)
    if seeded is None:
        known = ", ".join(version for (owner, version) in BUILDS if owner == product) or "none"
        raise SeedError(f"{product} {text}: neither a release nor a build the seeder knows (known builds: {known})")
    if seeded < numbers(MINIMUM):
        raise SeedError(f"{product} {text}: the seeder fills {MINIMUM} and later releases")
    _found[product] = (text, seeded, None)
    log(SECTION, f"{product} {text}: a release, seeded by its version")


def variant(name):
    """The variant of a behavior of RELEASES the platform takes: its build's when its build differs, else that of the last
    row at or below its release. Stops the seed for a release older than the behavior's first row."""
    behavior = RELEASES[name]
    if behavior.product not in _found:
        raise SeedError(f"The version of {behavior.product} was not read: {behavior.what} depends on it")
    text, seeded, build = _found[behavior.product]
    if build and name in build.differs:
        return build.differs[name]
    rows = [chosen for first, chosen in behavior.since.items() if numbers(first) <= seeded]
    if not rows:
        raise SeedError(f"{behavior.what}: {behavior.product} {text} has none, {next(iter(behavior.since))} is the first")
    return rows[-1]


def where(name, chosen):
    """Where a behavior takes a variant, from the tables, for a module to say it: "API Management 4.13.0 and later" for the
    releases from its first row, each build of BUILDS that differs to it, "no release" when none does."""
    behavior = RELEASES[name]
    found, start = [], None
    for first, taken in behavior.since.items():
        if taken == chosen and start is None:
            start = first
        elif taken != chosen and start is not None:
            found.append(f"{behavior.product} {start} up to {first}")
            start = None
    if start is not None:
        found.append(f"{behavior.product} {start} and later")
    found += [f"the build {text}" for (product, text), build in BUILDS.items() if build.differs.get(name) == chosen]
    return ", ".join(found) or "no release"


def read():
    """The versions of the products the seed fills, once at its start. A gateway that has just started may not be listed yet:
    API Management is asked again for STATED seconds before the seed takes it that none states its version."""
    if has_seed("apim", "environments", "aim", "authz", "edge", "am/gamma-link.yaml"):   # what API Management fills, or Gamma
        platform = apim.ApiManagement()
        platform.connect()
        said = []

        def stated():
            said[:] = _said(platform.api, f"{platform.v1}/instances", lambda row: row.get("state") == "STARTED")
            if not said:
                raise SeedError("no gateway reports to it yet")
        try:
            stated()
        except SeedError:
            try:
                wait_until(SECTION, "a gateway that states the version of API Management", stated, timeout=STATED)
            except SeedError:
                pass   # note says why the seed stops
        note(APIM, said)
    if has_seed("am"):
        manager = am.AccessManagement()
        manager.connect()
        note(AM, _said(manager.api, "/management/platform/plugins/reporters", lambda row: True))


def _said(api, path, keep):
    try:
        return [row.get("version") for row in items(api.get(path, expect=(200,)).json()) if isinstance(row, dict) and keep(row)]
    except (HttpError, ValueError):
        return []


def markdown():
    """The two tables of "Versions of Gravitee" in docs/playgrounds.md, as RELEASES and BUILDS say them."""
    lines = ["| What differs | Product | From | Variant | What the seed does |", "|---|---|---|---|---|"]
    for behavior in RELEASES.values():
        for first, chosen in behavior.since.items():
            lines.append(f"| {behavior.what} | {behavior.product} | {first} | {chosen} | {behavior.variants[chosen]} |")
    lines += ["", "| Build | Product | Seeded like | Where it differs | What its seed files write |", "|---|---|---|---|---|"]
    for (product, text), build in BUILDS.items():
        differs = "; ".join(f"{RELEASES[name].what}: {chosen}, {RELEASES[name].variants[chosen][0].lower()}"
                            f"{RELEASES[name].variants[chosen][1:]}" for name, chosen in build.differs.items()) or "Nowhere"
        lines.append(f"| `{text}`: {build.what} | {product} | {build.like} | {differs} | {build.files or 'As on a release'} |")
    return "\n".join(lines)
