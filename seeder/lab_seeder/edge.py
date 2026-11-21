"""Edge Management's shared environment configuration, from seed/edge/configuration.yaml.

The console's existing GET and PUT /configuration read and save the same object. PUT creates the configuration on its
first save and deploys changes. Gateway and reactor URLs are immutable once it exists. Proxy APIs remain apim's.
Like APIM environment settings, this shared configuration is updated, never recorded as a playground-owned object or
deleted on removal. Device enrollment, daemon installation and observed activity belong to the platform.
"""

import copy
import re
from urllib.parse import urlsplit

from .apim import merge
from .common import SeedError, contains, load, log, seed_file
from .gamma import Gamma

SECTION = "edge"
FOLDERS = ("edge/configuration.yaml",)
GAMMA = ("edge",)
ORDER = []
CONFIGURATION = "/configuration"
URLS = ("gatewayUrl", "reactorUrl")
DEFAULTS = {"proxy": {"dnsDomains": [], "routes": []}, "shadowAi": {"domains": [], "reportInterval": "PT2M"}}
INTERVAL = re.compile(r"PT(?:([0-9]{1,19})H)?(?:([0-9]{1,19})M)?(?:([0-9]{1,19})S)?")


def configuration(spec, where="seed/edge/configuration.yaml"):
    """Validate the fields the Edge console writes before connecting to the platform."""
    def refused(message):
        raise SeedError(f"{where}: {message}")

    def mapping(value, keys, field):
        if not isinstance(value, dict) or set(value) - set(keys):
            refused(f"{field} takes only {', '.join(keys)}")

    def text(value):
        return isinstance(value, str) and bool(value.strip())

    def domains(value, field):
        if not isinstance(value, list):
            refused(f"{field} is a list of {{name: domain}}")
        for domain in value:
            mapping(domain, ("name",), field)
            if not text(domain.get("name")):
                refused(f"{field}: each domain needs a nonempty name")

    mapping(spec, (*URLS, "proxy", "shadowAi"), "configuration")
    spec = dict(spec)
    for key in URLS:
        if key not in spec:
            continue
        if not text(spec[key]):
            refused(f"{key} is an HTTP or HTTPS URL")
        try:
            url = urlsplit(spec[key])
            url.port   # reject a malformed port as well as a malformed hostname
            valid = url.scheme in ("http", "https") and url.hostname and not url.username and not url.password
        except ValueError:
            valid = False
        if not valid:
            refused(f"{key} is an HTTP or HTTPS URL without credentials")
    if "proxy" in spec:
        proxy = spec["proxy"]
        mapping(proxy, ("dnsDomains", "routes"), "proxy")
        if "dnsDomains" in proxy:
            domains(proxy["dnsDomains"], "proxy.dnsDomains")
        if "routes" in proxy:
            if not isinstance(proxy["routes"], list):
                refused("proxy.routes is an ordered list of {pathPrefix, apiPath, provider}")
            for route in proxy["routes"]:
                mapping(route, ("pathPrefix", "apiPath", "provider"), "proxy.routes")
                for key in ("pathPrefix", "apiPath"):
                    if not text(route.get(key)) or not route[key].startswith("/"):
                        refused(f"proxy.routes: {key} is a path starting with /")
                if "provider" in route and not text(route["provider"]):
                    refused("proxy.routes: provider is nonempty text when present")
    if "shadowAi" in spec:
        shadow = spec["shadowAi"]
        mapping(shadow, ("domains", "reportInterval"), "shadowAi")
        if "domains" in shadow:
            domains(shadow["domains"], "shadowAi.domains")
        if "reportInterval" in shadow:
            value = shadow["reportInterval"]
            interval = INTERVAL.fullmatch(value) if isinstance(value, str) else None
            seconds = sum(int(part or 0) * scale for part, scale in zip(interval.groups(), (3600, 60, 1))) if interval else 0
            if not 10 <= seconds <= 2 ** 63 - 1:
                refused("shadowAi.reportInterval is an ISO 8601 duration of at least 10 seconds within the platform's "
                        "duration range, such as PT2M")
            # Duration serializes PT120S as PT2M: compare and write the canonical form on repeat runs.
            hours, seconds = divmod(seconds, 3600)
            minutes, seconds = divmod(seconds, 60)
            spec["shadowAi"] = {**shadow, "reportInterval": "PT" + "".join(f"{value}{unit}" for value, unit in
                                                                         ((hours, "H"), (minutes, "M"), (seconds, "S")) if value)}
    return spec


def seed():
    path = seed_file("edge", "configuration.yaml")
    if not path:
        return
    spec = configuration(load(path))
    g = Gamma()
    g.connect(SECTION, lambda: g.edge.get(CONFIGURATION, expect=(200, 404)), "Edge Management")
    answer = g.edge.get(CONFIGURATION, expect=(200, 404))
    try:
        live = answer.json() if answer.status_code == 200 else None
    except ValueError:
        live = None
    if answer.status_code == 200 and not isinstance(live, dict):
        raise SeedError("Edge configuration: the platform did not return an object")
    if live is None:
        if not all(spec.get(key) for key in URLS):
            raise SeedError("seed/edge/configuration.yaml: gatewayUrl and reactorUrl are required to create an Edge configuration")
    else:
        for key in URLS:
            if key in spec and spec[key] != live.get(key):
                raise SeedError(f"seed/edge/configuration.yaml: {key} cannot change after the Edge configuration is created")
    wanted = merge(copy.deepcopy(live if live is not None else DEFAULTS), spec)
    if contains(live, wanted):
        log(SECTION, "✓ Edge configuration up to date")
        return
    g.edge.put(CONFIGURATION, json=wanted, what="Edge configuration")
    log(SECTION, "✓ Edge configuration saved and deployed")


def remove(records):
    """The environment's shared configuration remains, as its APIM settings do."""
