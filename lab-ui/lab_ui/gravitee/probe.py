"""What a UI of a playground answers when the Lab UI's page asks, and whether that page may frame it: the
X-Frame-Options and frame-ancestors of what it answers, judged against the page for what the lab's UIs write: the browser
still enforces the header, and this only picks the frame or the notice. Only the addresses a playground declares (frames.py)
are ever asked for."""

import http.client
import socket
import ssl
from urllib.parse import urljoin, urlsplit

from . import frames

TIMEOUT = 3        # seconds a UI has to answer
REDIRECTS = 2      # a UI that sends the page further than this does not answer
MOVED = (301, 302, 303, 307, 308)
NOT_ANSWERING = (502, 503, 504)   # what a proxy says while what is behind it is not there
# What a look says of a UI that does not answer, besides the words of the network (_why)
TOO_FAR, NOWHERE = "Too many redirects", "Sends the page to what is no web address"
UNTRUSTED = "Its certificate is not trusted"


# ---------------------------------------------------------------------------------------------------------------------
# Whether a page may frame it
# ---------------------------------------------------------------------------------------------------------------------

def _matches(source, framed, ancestor):
    """Whether one source of a frame-ancestors list lets `ancestor` frame a document of the origin `framed`: `*`, `'self'`,
    or the address of `ancestor`. A source of any other form takes the notice."""
    source = source.lower()
    return source == "*" or (source == "'self'" and framed == ancestor) or frames.origin(source) == ancestor


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
        kind = value.lower()
        if kind == "deny" or (kind == "sameorigin" and framed != ancestor):
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
            raise OSError(NOWHERE) from error
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise OSError(NOWHERE)
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
                raise OSError(TOO_FAR)
            try:
                url = urljoin(url, headers["Location"])
            except ValueError as error:   # an address that is none: a bracket left open
                raise OSError(NOWHERE) from error
            hops += 1
            continue
        return status, url, headers


def _why(error):
    """Why a UI did not answer, in the words of its tooltip: "" when there is nothing to add to "Does not answer"."""
    if isinstance(error, ConnectionRefusedError):
        return "Not listening yet"
    if isinstance(error, (socket.timeout, TimeoutError)):
        return f"No answer within {TIMEOUT:g} s"
    return str(error) if str(error) in (TOO_FAR, NOWHERE) else ""


def look(url, page):
    """What a UI is for the page: {answers, frameable, reason}, the reason in a person's words. One that does not answer is
    not judged; one whose certificate this machine does not trust answers, and is not shown here: a new tab lets the person
    decide."""
    try:
        status, final, headers = _get(url)
    except ssl.SSLCertVerificationError:
        return {"answers": True, "frameable": False, "reason": UNTRUSTED}
    except OSError as error:
        return {"answers": False, "frameable": False, "reason": _why(error)}
    if status in NOT_ANSWERING:
        return {"answers": False, "frameable": False, "reason": "Not ready yet"}
    frameable, reason = can_frame(final, page, headers.get_all("Content-Security-Policy") or [],
                                  headers.get_all("X-Frame-Options") or [])
    return {"answers": True, "frameable": frameable, "reason": reason}


def probe(p, pid, url, page):
    """The look of a tab's address. Only an address the playground declares is asked: anything else is a ValueError, which
    the page is told as a bad request."""
    declared_url = frames.allowed(p, pid, url)
    if declared_url is None:
        raise ValueError("Not a UI of this playground")
    return look(declared_url, page)
