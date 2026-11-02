"""The relay that gives each persona's tab its own sign-in on the platform. A playground's tab shown as one of its accounts
(`consoles: [{console, as}]`) is a frame named for that persona; the platform's UI in it (lab-ui/frames/hosts.js) sends what it
asks of the Management API here, under /persona/<persona>/, instead of to localhost:8083. Every cookie the API sets is kept
under that path and under a name of the persona's, and only the persona's cookies go back to the API: a browser keeps one set
of cookies for localhost whatever the port, so this is what keeps two people signed in side by side, in one browser, on
localhost:9000."""

import http.client
import re
from urllib.parse import urlsplit

API = ("localhost", 8083)   # the Management API, as this machine reaches it: the address its UIs are told to use
# The addresses of the API that its answers name: a redirect to one goes back through the relay, as the persona's
ORIGINS = ("http://localhost:8083", "http://127.0.0.1:8083")
PREFIX = "/persona/"
PERSONA = re.compile(r"^/persona/([a-z0-9][a-z0-9-]{0,62})(/.*)$")
LOCAL = ("localhost", "127.0.0.1")
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization", "proxy-authenticate",
       "host", "content-length"}
TIMEOUT = 130   # an approval holds a call for up to two minutes
BODY_LIMIT = 64 * 1024 * 1024   # what a call may send: an image or a document a person uploads, never more
# What every answer of the relay says beside the API's: it is read by the UI's scripts, never shown as a page of this origin
GUARDED = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; sandbox"}


def handles(path):
    return path.startswith(PREFIX)


def _cookie_name(persona, name):
    return f"LabPersona_{persona}_{name}"


def request_cookies(persona, header):
    """The persona's own cookies of a Cookie header, under the names the API gave them; every other cookie is left out."""
    mine = _cookie_name(persona, "")
    kept = []
    for part in (header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name.startswith(mine) and len(name) > len(mine):
            kept.append(f"{name[len(mine):]}={value}")
    return "; ".join(kept)


def response_cookie(persona, header):
    """A Set-Cookie of the API as the persona's: its name prefixed, its path the persona's, no domain."""
    first, *attributes = [part.strip() for part in header.split(";")]
    name, _, value = first.partition("=")
    kept = [part for part in attributes if part.split("=")[0].strip().lower() not in ("path", "domain")]
    return "; ".join([f"{_cookie_name(persona, name)}={value}", f"Path={PREFIX}{persona}/", *kept])


def location(persona, value):
    """A Location of the API as the persona's: one on the API, absolute or from its root, goes back through the relay."""
    for origin in ORIGINS:
        if value.startswith(origin + "/") or value == origin:
            value = value[len(origin):] or "/"
            break
    return f"{PREFIX}{persona}{value}" if value.startswith("/") and not value.startswith("//") else value


def allowed(headers):
    """Only a page of this machine asks: the Host is a local name, and the page that asks is one of this machine's (the
    platform's UIs are on its ports). A page of another site would not send the persona's cookies anyway."""
    host = (headers.get("Host") or "").rsplit(":", 1)[0]
    origin = headers.get("Origin")
    return host in LOCAL and (origin is None or urlsplit(origin).hostname in LOCAL)


def relay(handler, method):
    """Answers one request under /persona/<persona>/ with what the Management API answers to it."""
    found = PERSONA.match(urlsplit(handler.path).path)
    if not found or not allowed(handler.headers):
        handler.answer(403 if found else 404, b"")
        return
    persona, query = found.group(1), urlsplit(handler.path).query
    target = found.group(2) + (f"?{query}" if query else "")
    size = handler.headers.get("Content-Length") or "0"
    if not (size.isascii() and size.isdigit()):   # a negative number reads until the connection closes
        handler.answer(400, b"The Content-Length must be a number of bytes", "text/plain; charset=utf-8")
        return
    if int(size) > BODY_LIMIT:
        handler.answer(413, b"", "text/plain; charset=utf-8")
        return
    body = handler.rfile.read(int(size)) if int(size) else None
    headers = {key: value for key, value in handler.headers.items() if key.lower() not in HOP and key.lower() != "cookie"}
    cookies = request_cookies(persona, handler.headers.get("Cookie"))
    if cookies:
        headers["Cookie"] = cookies
    headers["Host"] = f"{API[0]}:{API[1]}"
    connection = http.client.HTTPConnection(*API, timeout=TIMEOUT)
    try:
        connection.request(method, target, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
    except (OSError, http.client.HTTPException) as error:   # not reached, or no answer of HTTP
        handler.answer(502, str(error).encode(), "text/plain; charset=utf-8")
        return
    finally:
        connection.close()
    handler.send_response(response.status)
    for key, value in response.getheaders():
        if key.lower() in HOP or key.lower() in {name.lower() for name in GUARDED}:
            continue
        name = key.lower()
        handler.send_header(key, response_cookie(persona, value) if name == "set-cookie" else location(persona, value)
                            if name == "location" else value)
    for key, value in GUARDED.items():
        handler.send_header(key, value)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    if method != "HEAD":
        handler.wfile.write(data)
