"""What a UI of a playground answers when the Lab UI's page asks, and whether that page may frame it: the
X-Frame-Options and frame-ancestors of what it answers, judged as a browser would against the page. Only the addresses
a playground declares (frames.py) are ever asked for."""

import http.client
import re
import threading
import time
from urllib.parse import urljoin, urlsplit

from ..gravitee import frames

TTL = 4            # seconds an answer is kept: the page asks again while a notice shows
TIMEOUT = 3        # seconds a UI has to answer
REDIRECTS = 2      # a UI that sends the page further than this does not answer
MOVED = (301, 302, 303, 307, 308)
NOT_ANSWERING = (502, 503, 504)   # what a proxy says while what is behind it is not there
DEFAULT_PORT = {**frames.PORTS, "ws": 80, "wss": 443}   # the schemes a source of frame-ancestors can name
NETWORK = tuple(DEFAULT_PORT) + ("ftp",)   # the schemes a `*` source stands for
UPGRADES = {"http": ("https",), "ws": ("wss", "http", "https"), "wss": ("https",)}   # also allowed, as in CSP 3
HOST_SOURCE = re.compile(r"^(?:([a-z][a-z0-9+.-]*)://)?(\*|\*\.[^/:]+|\[[^\]]+\]|[^/:*\[\]]+)(?::(\d+|\*))?(?:/.*)?$")
LOCK = threading.Lock()
KEPT = {}          # (address, page) -> (when, answer)


# ---------------------------------------------------------------------------------------------------------------------
# Whether a page may frame it
# ---------------------------------------------------------------------------------------------------------------------

def _scheme_matches(wanted, scheme):
    return wanted == scheme or scheme in UPGRADES.get(wanted, ())


def _matches(source, framed, ancestor):
    """Whether one source of a frame-ancestors list lets `ancestor` frame a document of the origin `framed`."""
    source = source.lower()
    if source == "'self'":
        return framed == ancestor
    if source == "*":
        return ancestor[0] in NETWORK
    if re.fullmatch(r"[a-z][a-z0-9+.-]*:", source):
        return _scheme_matches(source[:-1], ancestor[0])
    found = HOST_SOURCE.match(source)
    if not found:   # 'none', and what is no source
        return False
    scheme, host, port = found.groups()
    host = host.strip("[]")
    named = host == ancestor[1] or host == "*" or (host.startswith("*.") and ancestor[1].endswith(host[1:]))
    return named and _scheme_matches(scheme or framed[0], ancestor[0]) \
        and (port == "*" or int(port or DEFAULT_PORT[ancestor[0]]) == ancestor[2])


def _policies(values):
    """The frame-ancestors directive of each content security policy of these header values, as the list of its sources;
    a header may hold several policies, separated by commas, and a directive that comes twice counts once."""
    lists = []
    for value in values:
        for policy in value.split(","):
            names = {}
            for directive in policy.split(";"):
                words = directive.split()
                if words:
                    names.setdefault(words[0].lower(), words[1:])
            if "frame-ancestors" in names:
                lists.append(names["frame-ancestors"])
    return lists


def can_frame(url, page, csp=(), options=()):
    """(whether `page`, an origin such as http://localhost:9000, may show `url` in a frame, why not). `csp` and
    `options` are the values of the Content-Security-Policy and X-Frame-Options headers that came with it. As in a
    browser: every policy with a frame-ancestors must name the page ('none', and an empty list, name nobody), and
    when none has one X-Frame-Options decides, a conflict between its values refusing."""
    framed, ancestor = frames.origin(url), frames.origin(page)
    if not framed or not ancestor:
        return False, "Not an address a frame can show"
    policies = _policies(csp)
    for sources in policies:
        if not any(_matches(source, framed, ancestor) for source in sources):
            return False, f"frame-ancestors {' '.join(sources) or 'none'}"
    if policies:
        return True, ""   # frame-ancestors replaces X-Frame-Options
    for value in (word.strip() for header in options for word in header.split(",")):
        kind, _, where = value.lower().partition(" ")
        if kind == "deny" or (kind == "sameorigin" and framed != ancestor) \
                or (kind == "allow-from" and frames.origin(where.strip()) != ancestor):
            return False, f"X-Frame-Options: {value}"
    return True, ""


# ---------------------------------------------------------------------------------------------------------------------
# What it answers
# ---------------------------------------------------------------------------------------------------------------------

def _get(url):
    """(status, the address that answered, its headers) of a GET, at most REDIRECTS redirects followed; a UI that sends
    the page further does not answer. The body is never read. What cannot be asked, or does not answer in time, raises
    OSError."""
    hops = 0
    while True:
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as error:   # a port out of range, a bracket left open
            raise OSError(str(error)) from error
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise OSError(f"Not an address to ask: {url}")
        connect = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        connection = connect(parts.hostname, port, timeout=TIMEOUT)
        try:
            connection.request("GET", (parts.path or "/") + (f"?{parts.query}" if parts.query else ""),
                               headers={"Accept": "text/html", "User-Agent": "GraviteeLabUI"})
            response = connection.getresponse()
            status, headers = response.status, response.msg
        except http.client.HTTPException as error:   # what is not HTTP at all: not a UI that answers
            raise OSError(str(error)) from error
        finally:
            connection.close()
        if status in MOVED and headers.get("Location"):
            if hops == REDIRECTS:
                raise OSError("Too many redirects")
            try:
                url = urljoin(url, headers["Location"])
            except ValueError as error:   # an address that is none: a bracket left open
                raise OSError(str(error)) from error
            hops += 1
            continue
        return status, url, headers


def look(url, page):
    """What a UI is for the page: {answers, frameable, reason}. One that does not answer is not judged."""
    try:
        status, final, headers = _get(url)
    except OSError as error:
        return {"answers": False, "frameable": False, "reason": str(error)}
    if status in NOT_ANSWERING:
        return {"answers": False, "frameable": False, "reason": f"{status}"}
    frameable, reason = can_frame(final, page, headers.get_all("Content-Security-Policy") or [],
                                  headers.get_all("X-Frame-Options") or [])
    return {"answers": True, "frameable": frameable, "reason": reason}


def probe(p, pid, url, page):
    """The look of a tab's address, kept for TTL seconds. Only an address the playground declares is asked: anything
    else is a ValueError, which the page is told as a bad request."""
    declared_url = frames.allowed(p, pid, url)
    if declared_url is None:
        raise ValueError("Not a UI of this playground")
    key, now = (declared_url, page), time.monotonic()
    with LOCK:
        for old in [old for old, (when, _) in KEPT.items() if now - when >= TTL]:
            del KEPT[old]
        if key in KEPT:
            return KEPT[key][1]
    answer = look(declared_url, page)
    with LOCK:
        KEPT[key] = (time.monotonic(), answer)
    return answer
