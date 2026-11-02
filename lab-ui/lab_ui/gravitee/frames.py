"""A playground's UIs as the page shows them side by side with its guide: the tabs its `consoles` declare, and so the
only addresses the Lab UI ever asks for (probe.py looks at those). What is not a web page a frame can show is never a
tab, whatever a file says."""

import re
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


def entries(listed):
    """(service, persona) of each entry of a playground's `consoles`: a service's name, or {console, as} for that
    service's UI signed in as one of its `accounts` (`as` is its username); persona is None for the first. What is
    neither is left out."""
    found = []
    for entry in listed if isinstance(listed, list) else []:
        if isinstance(entry, str):
            found.append((entry, None))
        elif isinstance(entry, dict) and isinstance(entry.get("console"), str):
            found.append((entry["console"], entry["as"] if isinstance(entry.get("as"), str) and entry["as"] else None))
    return found


def names(listed):
    """The services a playground's `consoles` show, once each, in its order."""
    return list(dict.fromkeys(service for service, _ in entries(listed)))


def persona(username):
    """The name of a persona in an address and a cookie: its username in lowercase letters, digits and hyphens
    (anouk@van-dijk.test: anouk-van-dijk-test)."""
    return re.sub(r"[^a-z0-9]+", "-", username.lower()).strip("-")[:63] or "persona"


def who(account):
    """Whom an account is, in a few words: its `for` after the last colon and before the first comma ("Developer Portal:
    Anouk Bakker, Van Dijk Logistics' developer" is Anouk Bakker), else its username."""
    said = str(account.get("for") or "").rsplit(":", 1)[-1].split(",", 1)[0].strip()
    return said or str(account.get("username") or "")


def tabs(links, consoles, accounts=()):
    """The UIs of a playground, in the order the page shows them: each entry its `consoles` lists, by the first console
    of its service (`links` are the consoles of the project, the current ones first, then by name). A service is listed
    once for each persona, and only a web address is a UI: a console that is none (`javascript:`) is left out. Each is
    {id, label, url, service}; the id is its service's. One signed in as an account also has `persona`, the name its frame
    and its sign-in go by (lab_ui/server/personas.py), and `as`, the account's username; its id is the service's, @ and the
    persona's. An entry whose `as` is no account is left out."""
    users = {account.get("username"): account for account in accounts if isinstance(account, dict)}
    found = []
    for name, user in entries(consoles):
        link = next((link for link in links if link["service"] == name), None)
        if not link or not origin(link["url"]) or (user is not None and user not in users):
            continue
        label, host = (link["label"], None) if user is None else (f"{link['label']} as {who(users[user])}", persona(user))
        tab_id = name if host is None else f"{name}@{host}"
        if not any(tab["id"] == tab_id for tab in found):
            found.append({"id": tab_id, "label": label, "url": link["url"], "service": name,
                          **({"persona": host, "as": user} if host else {})})
    return found


def declared(p, pid):
    """The tabs a playground of the project declares: the consoles it lists, in its order, as they are with it on (with
    the add-ons it brings): a console it puts on a service of the core is its tab, whether it is on yet or not."""
    playground = p["playgrounds"][pid]
    accounts = playground.get("accounts")
    return tabs(project.shown(p["links"], pid, playground.get("addons") or (), tabs=True), playground.get("consoles") or [],
                accounts if isinstance(accounts, list) else ())


def allowed(p, pid, url):
    """The address as the playground declares it, or None: the one thing the Lab UI asks for is a tab's address."""
    if pid not in p["playgrounds"] or not isinstance(url, str):
        return None
    return next((tab["url"] for tab in declared(p, pid) if same(tab["url"], url)), None)
