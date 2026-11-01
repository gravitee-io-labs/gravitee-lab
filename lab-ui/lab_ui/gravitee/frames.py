"""A playground's UIs as the page shows them side by side with its guide: the tabs its `consoles` declare, and so the
only addresses the Lab UI ever asks for (probe.py looks at those). What is not a web page a frame can show is never a
tab, whatever a file says."""

from urllib.parse import urlsplit

from ..library import project

PORTS = {"http": 80, "https": 443}   # the port of a scheme, when an address names none


def origin(url):
    """(scheme, host, port) of a web address, the port as the browser compares it (the scheme's own when none is
    given); None for what is not one: other schemes, no host, not a text."""
    try:
        parts = urlsplit(url)
        scheme, host, port = parts.scheme.lower(), (parts.hostname or "").lower(), parts.port
    except (AttributeError, TypeError, ValueError):
        return None
    if scheme not in PORTS or not host:
        return None
    return scheme, host, port or PORTS[scheme]


def same(a, b):
    """Whether two addresses are one, a slash at the end or not."""
    return a.rstrip("/") == b.rstrip("/")


def tabs(links, consoles):
    """The UIs of a playground, in the order the page shows them: each service its `consoles` lists, by its first
    console (`links` are the consoles of the project, the current ones first, then by name). A service is listed once,
    and only a web address is a UI: a console that is none (`javascript:`) is left out. Each is
    {id, label, url, service}; the id is its service's."""
    found = []
    for name in consoles:
        link = next((link for link in links if link["service"] == name), None)
        if link and origin(link["url"]) and not any(tab["service"] == name for tab in found):
            found.append({"id": name, "label": link["label"], "url": link["url"], "service": name})
    return found


def declared(p, pid):
    """The tabs a playground of the project declares: the consoles it lists, in its order, as they are with it on (with
    the add-ons it brings): a console it puts on a service of the core is its tab, whether it is on yet or not."""
    playground = p["playgrounds"][pid]
    return tabs(project.shown(p["links"], pid, playground.get("addons") or (), tabs=True), playground.get("consoles") or [])


def allowed(p, pid, url):
    """The address as the playground declares it, or None: the one thing the Lab UI asks for is a tab's address."""
    if pid not in p["playgrounds"] or not isinstance(url, str):
        return None
    return next((tab["url"] for tab in declared(p, pid) if same(tab["url"], url)), None)
