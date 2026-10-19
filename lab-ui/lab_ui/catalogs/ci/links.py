"""`catalog.py ci links PATH` (LK01): the links of the guides and the READMEs of a catalog answer. It is a step of the nightly
run, on the newest Lab, with nothing to steal (`verify-latest` holds no token), and not a step of a pull request: external links are
not fetched there (egress, flakiness, and the Lab's own rule that a view makes no request a catalog chose).

    LK01  {path}:{line}: the link {url} answers {status}.      a warning

A link is an address `http://` or `https://` that a renderer shows as a link in a Markdown file: an inline link, a reference, an
autolink, a bare address, a `href` or `src` of raw HTML. What is in a code span or a fenced block is code, not a link, and is not
asked (`rules.guides.mask`, the mask the rules GD01 to GD05 read). An address that is a template (`${...}`, `<host>`, `{id}`), one
that names a host reserved for documentation (`.test`, `.example`, `.invalid`, `localhost`, `example.com` and its sisters), and one
with a user name in it, are not asked.

The file is a stranger's text and the nightly run is inside a network, so what is asked is guarded as the registry check is
(`.github/scripts/image_manifests.py`), and the same way: the name of a host is resolved once, every address has to be on the public
network (`is_global`), and the connection is made to the address that was checked, not to the name again, so that a name that answers
a public address to the look and another to the connection (DNS rebinding) is not followed there; each redirect is judged the same
way and at most five are followed; a request carries nothing but a fixed `User-Agent`, never a header from the environment or a
cookie; and the body of an answer is never read: the status is the answer. `HEAD` is asked first, and `GET` (with no body read) when
a server mishandles `HEAD`.

What counts: 2xx and 3xx are fine; so are 401, 403, 429 and 999, which a site answers to a program that is not a browser, and which say
the page is there; 404, 410, the other 4xx, a 5xx that is still there a second time, and a host that does not answer (a name that does
not resolve, a refused connection, a certificate that is not accepted, no answer in 15 seconds) are LK01. One address is asked once for
the whole catalog, at most 2 000 of them, one at a time to a host and 8 hosts at once, and the run ends after ten minutes with what it
has. A run in which not one address answers at all, where a network was needed, did not run (exit 2): the warnings would say nothing
of the guides. LK01 is a warning, so the exit code is 0 for any number of them.

Python 3.9 and later, standard library only."""

import concurrent.futures
import http.client
import ipaddress
import os
import re
import socket
import ssl
import threading
import time
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlsplit

from .. import report, tree as trees
from ..report import WARNING, Problem
from ..rules import guides, newlines, read_limit, say
from . import Unavailable, Usage, conclude, entry, parsed, parser

MESSAGE = "{path}:{line}: the link {url} answers {status}."
TIMEOUT = 15            # seconds a host has to answer
HOPS = 5                # redirects followed
MAX_LINKS = 2000        # addresses asked, whatever the catalog holds
MAX_PER_FILE = 500      # addresses read from one file
MAX_FILES = 3000        # Markdown files read
WORKERS = 8             # hosts asked at once
BUDGET = 600            # seconds of the whole run
SHOWN = 120             # characters of an address a message shows
AGENT = "gravitee-lab-links/1 (+the nightly check of a catalog)"
# What a site answers to a program that is not a browser, and what says the page is there all the same
ROBOTS = (401, 403, 429, 999)
# After a HEAD that a server may mishandle, GET says what the page really answers
RETRY_WITH_GET = (400, 403, 404, 405, 406, 410, 429, 500, 501, 502, 503)
REDIRECTS = (301, 302, 303, 307, 308)
# The reserved names (RFC 2606 and 6761): they resolve nowhere, so a link to one is an example and not a link
RESERVED = (".test", ".example", ".invalid", ".localhost", ".local", ".internal", ".lan")
SISTERS = ("example.com", "example.org", "example.net")
URL = re.compile(r"https?://[^\s<>\"'`\\\[\]]{1,2000}", re.I)
TEMPLATE = re.compile(r"[${}<>*|]|%7[bB]")
TRAILING = ".,;:!?*_~'\"»"


class Answer(NamedTuple):
    """What asking an address came to: `status` (an HTTP status, 0 when nothing answered), `why` (in words, when nothing did), and
    `asked`, False when the address was not asked at all because it is not on the public network."""
    status: int
    why: str = ""
    asked: bool = True


class Link(NamedTuple):
    path: str
    line: int
    url: str


def is_public(text: str) -> bool:
    """Whether an address (as a resolver writes it) is on the public network. An IPv6 address that carries an IPv4 one (mapped,
    or in the NAT64 prefix) is judged by the IPv4 address it carries."""
    try:
        address = ipaddress.ip_address(text.partition("%")[0])
    except ValueError:
        return False
    if address.version == 6:
        mapped = address.ipv4_mapped
        if mapped is None and address in ipaddress.ip_network("64:ff9b::/96"):
            mapped = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
        if mapped is not None:
            address = mapped
    return address.is_global and not address.is_multicast


def resolve(host: str, port: int) -> List[str]:
    """The addresses a name resolves to, as text."""
    return list(dict.fromkeys(str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))


def default_wrap(sock, host: str):
    """TLS over a connected socket, for `host`, with the certificate checked."""
    return ssl.create_default_context().wrap_socket(sock, server_hostname=host)


class Fetcher:
    """Asks an address. `resolve(host, port) -> [address]`, `connect((address, port), timeout) -> socket` and `wrap(socket, host)`
    are what the network is reached through; a test hands in its own. The name of the host is resolved here, once for each request,
    judged, and the connection is made to the address that was judged."""

    def __init__(self, resolver: Callable = resolve, connect: Callable = socket.create_connection, wrap: Callable = default_wrap,
                 timeout: float = TIMEOUT, sleep: Callable = time.sleep):
        self.resolver, self.connect, self.wrap, self.timeout, self.sleep = resolver, connect, wrap, timeout, sleep

    def __call__(self, url: str) -> Answer:
        answer = self.follow(url)
        if answer.asked and (answer.status >= 500 or answer.why == "no answer: timed out"):
            self.sleep(2)   # a server that is busy or restarting, or slow: once more
            answer = self.follow(url)
        return answer

    def follow(self, url: str) -> Answer:
        for _ in range(HOPS + 1):
            status, location, refused = self.ask(url, "HEAD")
            if status in RETRY_WITH_GET and not refused:
                status, location, refused = self.ask(url, "GET")
            if refused:
                return refused
            if status in REDIRECTS and location:
                url = urljoin(url, location)
                if urlsplit(url).scheme not in ("http", "https"):
                    return Answer(status)
                continue
            return Answer(status)
        return Answer(0, "too many redirects")

    def ask(self, url: str, method: str) -> Tuple[int, Optional[str], Optional[Answer]]:
        """(status, Location, a refusal to ask) for one request. No answer is status 0 with the refusal that says why."""
        try:
            parts = urlsplit(url)
            host, port = parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
            if parts.scheme not in ("http", "https") or not host:
                return 0, None, Answer(0, "not an address of the web", False)
            name = host.encode("idna").decode("ascii")
        except (ValueError, UnicodeError):
            return 0, None, Answer(0, "not an address of the web", False)
        try:
            found = self.resolver(name, port)
        except (OSError, UnicodeError, ValueError):
            return 0, None, Answer(0, "no answer: the name does not resolve")
        if not found or not all(is_public(each) for each in found):
            return 0, None, Answer(0, "the address is not on the public network", False)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        sock = None
        try:
            sock = self.connect((found[0], port), self.timeout)
            if parts.scheme == "https":
                sock = self.wrap(sock, name)
            sock.settimeout(self.timeout)
            connection = (http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection)(name, port, timeout=self.timeout)
            connection.sock = sock
            connection.request(method, path, headers={"User-Agent": AGENT, "Accept": "*/*", "Connection": "close"})
            response = connection.getresponse()
            return response.status, response.getheader("Location"), None
        except ssl.SSLError:
            return 0, None, Answer(0, "no answer: the certificate is not accepted")
        except socket.timeout:
            return 0, None, Answer(0, "no answer: timed out")
        except (http.client.InvalidURL, ValueError, UnicodeError):
            return 0, None, Answer(0, "not an address of the web", False)
        except ConnectionRefusedError:
            return 0, None, Answer(0, "no answer: the connection was refused")
        except (OSError, http.client.HTTPException) as error:
            return 0, None, Answer(0, "no answer: " + report.printable(type(error).__name__))
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass


# ---------------------------------------------------------------------------------------------------------------------
# What the guides link to
# ---------------------------------------------------------------------------------------------------------------------

def clean(found: str) -> str:
    """An address as a renderer would end it: without the punctuation that follows it in a sentence, and without the `)` that
    closes the link around it."""
    while found:
        if found[-1] in TRAILING:
            found = found[:-1]
        elif found[-1] == ")" and found.count(")") > found.count("("):
            found = found[:-1]
        else:
            break
    return found


def reserved(host: str) -> bool:
    host = host.lower().rstrip(".")
    return (host == "localhost" or host.endswith(RESERVED) or "." not in host
            or any(host == name or host.endswith("." + name) for name in SISTERS))


def wanted(url: str) -> bool:
    """Whether an address is one to ask: a web address with a host of its own, not a template, not reserved for documentation, with
    no user name or password in it."""
    if TEMPLATE.search(url):
        return False
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port   # (a port that is not a number raises ValueError here)
    except ValueError:
        return False
    return bool(host) and port != 0 and parts.scheme in ("http", "https") and "@" not in parts.netloc and not reserved(host)


def links_of(path: str, text: str) -> List[Link]:
    """The addresses of one Markdown file, each with its line, in the order they appear (the same address twice on a line once)."""
    masked = guides.mask(text)
    if "http" not in masked.lower():
        return []
    offsets = newlines(masked)
    found: List[Link] = []
    seen = set()
    for match in URL.finditer(masked):
        url = clean(match.group())
        if not wanted(url):
            continue
        line = _line_of(offsets, match.start())
        if (line, url) in seen:
            continue
        seen.add((line, url))
        found.append(Link(path, line, url))
        if len(found) >= MAX_PER_FILE:
            break
    return found


def _line_of(offsets: Sequence[int], position: int) -> int:
    """The line (from 1) of an offset, given the offsets of the line feeds."""
    low, high = 0, len(offsets)
    while low < high:
        middle = (low + high) // 2
        if offsets[middle] < position:
            low = middle + 1
        else:
            high = middle
    return low + 1


def markdown_links(tree) -> List[Link]:
    """Every address of every Markdown file of a tree, files in the order of their paths."""
    found: List[Link] = []
    files = 0
    for each in sorted(tree.entries(), key=lambda one: one.path):
        if each.mode not in trees.REGULAR or not each.path.lower().endswith((".md", ".markdown")):
            continue
        if each.size > read_limit(each.path):
            continue
        files += 1
        if files > MAX_FILES:
            break
        data = tree.read(each.path, read_limit(each.path))
        if data is not None:
            found += links_of(each.path, data.decode("utf-8", "replace"))
    return found


# ---------------------------------------------------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------------------------------------------------

def broken(answer: Answer) -> Optional[str]:
    """What a link answers, in the words of LK01, when that is something to say; None when it is fine or was not asked."""
    if not answer.asked:
        return None
    if answer.status == 0:
        return "nothing (" + answer.why.replace("no answer: ", "") + ")" if answer.why else "nothing"
    if answer.status in ROBOTS or answer.status < 400:
        return None
    return str(answer.status)


def ask_all(urls: Sequence[str], fetch: Callable, clock: Callable = time.monotonic, budget: float = BUDGET) -> Dict[str, Optional[Answer]]:
    """{address: its Answer, or None when the time was up before it was asked}. One host is asked one address at a time, and
    WORKERS hosts at once."""
    by_host: Dict[str, List[str]] = {}
    for url in urls:
        by_host.setdefault((urlsplit(url).hostname or "").lower(), []).append(url)
    started = clock()
    results: Dict[str, Optional[Answer]] = {url: None for url in urls}
    lock = threading.Lock()

    def one_host(group: List[str]) -> None:
        for url in group:
            if clock() - started > budget:
                return
            try:
                answer = fetch(url)
            except Exception as error:   # noqa: BLE001 - one address that breaks the fetcher is that address's answer, not the run's
                answer = Answer(0, "no answer: " + report.printable(type(error).__name__))
            with lock:
                results[url] = answer

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(one_host, by_host.values()))
    return results


def findings(links: Sequence[Link], answers: Dict[str, Optional[Answer]]) -> List[Problem]:
    """LK01 for each place that links an address that does not answer."""
    found: List[Problem] = []
    for link in links:
        answer = answers.get(link.url)
        said = broken(answer) if answer is not None else None
        if said is None:
            continue
        shown = report.printable(link.url if len(link.url) <= SHOWN else link.url[:SHOWN - 1] + "…")
        found.append(Problem("LK01", WARNING, link.path, link.line,
                             say(MESSAGE, path=report.printable(link.path), line=link.line, url=shown, status=said)))
    return found


def counts(links: Sequence[Link], answers: Dict[str, Optional[Answer]]) -> Dict[str, int]:
    """How many distinct addresses: asked, fine, not on the public network, left (time up), and broken."""
    distinct = {link.url for link in links}
    got = {url: answers.get(url) for url in distinct}
    return {"addresses": len(distinct), "private": sum(1 for each in got.values() if each is not None and not each.asked),
            "left": sum(1 for each in got.values() if each is None),
            "broken": sum(1 for each in got.values() if each is not None and broken(each) is not None)}


def no_network(answers: Dict[str, Optional[Answer]]) -> bool:
    """Whether no address that was asked answered at all, with at least three asked: the network is not there, and warnings would say
    nothing of the guides."""
    asked = [each for each in answers.values() if each is not None and each.asked]
    return len(asked) >= 3 and all(each.status == 0 for each in asked)


@entry
def main(argv, out, err, **seams) -> int:
    """`ci links`. Exit 0 whatever LK01 found (they are warnings), 2 when the links could not be asked at all."""
    found = parser("links", "The links of the guides and the READMEs answer.")
    args = parsed(found, argv)
    if not os.path.isdir(args.path):
        raise Usage(f"{report.printable(args.path)} is not a folder")
    tree = seams.get("tree") or trees.FolderTree(os.path.abspath(args.path))
    links = markdown_links(tree)
    urls = list(dict.fromkeys(link.url for link in links))
    cut = len(urls) - MAX_LINKS if len(urls) > MAX_LINKS else 0
    urls = urls[:MAX_LINKS]
    fetch = seams.get("fetch") or Fetcher(sleep=seams.get("sleep") or time.sleep)
    answers = ask_all(urls, fetch, seams.get("clock") or time.monotonic, seams.get("budget") or BUDGET)
    if no_network(answers):
        raise Unavailable("the network does not answer: not one of the links was reached, so nothing was checked")
    problems = findings(links, answers)
    total = counts(links, answers)
    skipped: List[str] = []
    if cut:
        skipped.append(f"{cut} addresses past the first {MAX_LINKS} were not asked")
    if total["left"]:
        skipped.append(f"{total['left']} addresses were not asked: the ten minutes of the run were up")
    if total["private"]:
        skipped.append(f"{total['private']} addresses are not on the public network and were not asked")
    if args.format != "json":
        out.write(f"Links: {total['addresses']} addresses in {len({link.path for link in links})} files, "
                  f"{total['broken']} that do not answer\n")
    return conclude("links", problems, args, out, "catalog.py ci links", skipped, title="The links of the guides answer")


__all__ = ["Answer", "Fetcher", "Link", "MESSAGE", "ask_all", "broken", "clean", "findings", "is_public", "links_of", "main",
           "markdown_links", "no_network", "reserved", "wanted"]
