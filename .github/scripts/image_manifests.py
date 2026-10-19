"""Whether every image an item pulls exists: a typo in a version, or a tag a registry removed, ends a start after the whole setup
and is found here in seconds, without pulling anything.

    python3 -I .github/scripts/image_manifests.py ITEM [TESTED]    # ITEM is kind:id, TESTED the number of a set of x-gravitee.tested

ITEM is an item of a catalog linked into the engine, with the id of the Lab (`name`, or `@catalog/name` for a catalog other than
the first). It reads the images the core and the item run on the versions of that set (the core's defaults without one), leaves
out those the lab builds (they are not in a registry yet), and asks each registry for the manifest of the rest with a HEAD
request, which a registry does not count as a pull (a runner shares its address with many others, and Docker Hub limits what an
address pulls). A registry that says nothing the script can use (it is not there, it is slow) is said and is no failure: only a
tag the registry answers it does not have is. Exit code 1 when one is missing.

The module is also imported, by path, by the catalog stage `catalog.py ci manifests` (lab_ui/catalogs/ci/manifests.py), which asks
the same registries the same way for the images of a catalog's units: the command line above is the same. What it uses is
`reference`, `exists`, `check` and `pulled`.

The item is somebody else's text and this runs for a pull request of anyone: Compose reads it with the scrubbed environment of
`compose.scrubbed_environment()`, so that no token of the runner reaches an image name, an item the trust policy blocks is not
read, and a registry on this machine's own network is not asked: not by how its name is written but by what it resolves to
(`public`), not by a redirect either (`Redirect`), and not by a name that answers a public address to the look and another to the
connection: the connection is made to the address that was checked, which it resolved itself (`Pinned`). What it prints is data on
GitHub: an image or a name is a unit's text. `images`, `check` and `exists` are for other scripts to import."""

import concurrent.futures
import http.client
import ipaddress
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "lab-ui") not in sys.path:   # python3 -I puts no folder of the repository on the path
    sys.path.insert(0, str(ROOT / "lab-ui"))

from lab_ui import e2e   # noqa: E402

TIMEOUT = 20   # seconds a registry has to answer
RESOLVE = socket.getaddrinfo   # how a name becomes addresses: given to the tests, which need no network
CONNECT = socket.create_connection   # how a socket to an address is opened: given to the tests too
# What a registry may answer with, by the kinds of manifest: an image, a list of images for several platforms, either as OCI
ACCEPT = ", ".join(("application/vnd.oci.image.index.v1+json", "application/vnd.oci.image.manifest.v1+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.docker.distribution.manifest.v2+json"))
BEARER = re.compile(r'(\w+)="([^"]*)"')
DOCKER_HUB = ("docker.io", "index.docker.io", "registry-1.docker.io")
USAGE = "image_manifests.py ITEM [TESTED]    # ITEM is kind:id, TESTED the number of a set of x-gravitee.tested"


def reference(image):
    """(registry host, repository, tag or digest) of an image as Docker reads it: Docker Hub when the first part is no host, and
    library/ before the name of an official image."""
    name, digest = (image.split("@", 1) + [None])[:2]
    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        host, name = first, rest
    else:
        host = "docker.io"
    tail = name.rsplit("/", 1)[-1]
    tag = "latest"
    if ":" in tail:
        name, tag = name.rsplit(":", 1)
    if host in DOCKER_HUB:
        host = "registry-1.docker.io"
        name = name if "/" in name else f"library/{name}"
    return host, name, digest or tag


class Redirect(urllib.request.HTTPRedirectHandler):
    """A registry may send a request somewhere else (a blob lives on a CDN), but not to an address of this machine's network or to
    a plain http one: the registry is the author's choice and the runner that follows is inside that network. A redirect that is
    refused is the answer, a 3xx the caller takes for 'the registry did not say'. The token goes only to the host it was made for."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        host = target.netloc.rpartition("@")[2]
        if target.scheme != "https" or not target.hostname or local(host) or not public(host):
            return None
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and target.netloc != urlsplit(req.full_url).netloc:
            new.remove_header("Authorization")
        return new


class Pinned(http.client.HTTPSConnection):
    """A connection that resolves the name of the registry itself, once, and connects to an address of that answer: a name that
    answers a public address to `public` and another, of this machine's network, to the connection would otherwise be followed
    there (DNS rebinding). Every address of the answer has to be public, as for `public`; the certificate is still the name's
    (the connection keeps `host`, and TLS is made for it). Behind a proxy the proxy resolves the name: nothing is pinned."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = self._pinned   # what http.client calls to open the socket

    def _pinned(self, address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
        host, port = address
        if self._tunnel_host:
            return CONNECT(address, timeout, source_address)
        found = _addresses(host, port)
        if found is None:
            raise OSError(f"{host} does not resolve to addresses of the public network")
        error = None
        for one in found:
            try:
                return CONNECT((one, port), timeout, source_address)
            except OSError as failed:
                error = failed
        raise error


class Pinning(urllib.request.HTTPSHandler):
    """HTTPS over `Pinned` connections."""

    def https_open(self, req):
        return self.do_open(Pinned, req, context=self._context)


OPENER = urllib.request.build_opener(Redirect, Pinning)


def guarded(request, timeout=TIMEOUT):
    """`urlopen` with the redirects of `Redirect`."""
    return OPENER.open(request, timeout=timeout)


def _release(error):
    """Closes the socket of an answer that is an error, which nobody else will: left to the garbage collector it is a leak that
    shows. An error made with no body (the tests make them) has nothing to close."""
    try:
        error.close()
    except (AttributeError, KeyError, OSError, ValueError):
        pass


def answer(url, headers, opener=guarded, method="HEAD"):
    """(status, headers) of a request; 0 and no headers when nothing answers."""
    request = urllib.request.Request(url, headers=headers, method=method)
    try:
        with opener(request, timeout=TIMEOUT) as response:
            return response.status, response.headers
    except urllib.error.HTTPError as error:
        try:
            return error.code, error.headers
        finally:
            _release(error)
    except (OSError, ValueError):
        return 0, {}


def token(challenge, opener=guarded):
    """The anonymous token a registry's `Bearer realm=...,service=...,scope=...` challenge asks for; None when it gives none,
    and when the realm is not an https address of the public network."""
    found = dict(BEARER.findall(challenge or ""))
    realm = urlsplit(found.get("realm", ""))
    host = realm.netloc.rpartition("@")[2]
    if realm.scheme != "https" or not realm.hostname or local(host) or not public(host):   # where a registry sends us is its word
        return None
    query = "&".join(f"{key}={value}" for key, value in found.items() if key in ("service", "scope"))
    request = urllib.request.Request(f"{found['realm']}?{query}")
    try:
        with opener(request, timeout=TIMEOUT) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as error:
        _release(error)
        return None
    except (OSError, ValueError):
        return None
    return body.get("token") or body.get("access_token") if isinstance(body, dict) else None


def local(host):
    """Whether a registry host is this machine or its own network: a name with no dot, `localhost`, `.local`, `.internal`, or an
    address that is not public. An image names it in a file anyone wrote, and the runner that asks is inside that network."""
    if host.startswith("["):   # [address]:port
        name = host[1:host.find("]")] if "]" in host else host
    else:
        name = host.split(":")[0] if host.count(":") == 1 else host
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return "." not in name or name.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa"))
    return not address.is_global


def _addresses(name, port, resolve=None):
    """The addresses `name` resolves to, as the resolver gives them, when every one is on the public network; None when it does
    not resolve, answers nothing or answers an address that is not public. The one answer is what a connection is made to."""
    try:
        found = (resolve or RESOLVE)(name, port, type=socket.SOCK_STREAM)
        ours = [str(info[4][0]).partition("%")[0] for info in found]
        return ours if ours and all(ipaddress.ip_address(one).is_global for one in ours) else None
    except (OSError, UnicodeError, ValueError, IndexError):
        return None


def public(host, resolve=None):
    """Whether every address a registry host names is on the public network, by what the resolver says: `127.1`, `0x7f.1`, `10.1`,
    `localhost.`, `user@127.0.0.1` and a name that resolves to loopback (`127.0.0.1.nip.io`) are not written as an address, and the
    connection reaches this machine or its network all the same. False for a host that does not resolve and for what is no host:
    there is nothing to ask. `local` is the cheap first look; this is the one that counts, and the connection itself checks again,
    on the answer it connects to (`Pinned`): the name is not asked twice and believed once."""
    try:
        parts = urlsplit(f"//{host}")
        name, port = parts.hostname, parts.port or 443
    except ValueError:
        return False
    return bool(name) and _addresses(name, port, resolve) is not None


def exists(image, opener=guarded):
    """Whether the registry has the image: True, False, or None when it cannot be told (no answer, a refusal that is no 404, a
    registry of this machine's own network, which is not asked)."""
    host, name, tag = reference(image)
    if local(host) or not public(host):
        return None
    url = f"https://{host}/v2/{name}/manifests/{tag}"
    headers = {"Accept": ACCEPT}
    status, received = answer(url, headers, opener)
    if status == 401:
        bearer = token(received.get("WWW-Authenticate"), opener) if received else None
        if bearer:
            status, _ = answer(url, {**headers, "Authorization": f"Bearer {bearer}"}, opener)
    if status == 200:
        return True
    return False if status == 404 else None   # a redirect that was not followed (Redirect) is not an answer either


def images(item, tested):
    """The images the core and the item pull, on the versions of set `tested` (the core's defaults without one): not those the
    lab builds. ValueError, with the words for a person, when the item is none of the project's, belongs to a machine, is
    blocked, or has no such set. The item is read with the scrubbed environment of Compose."""
    from lab_ui.docker import compose
    from lab_ui.library import project   # here and not above: a script that only asks registries needs neither
    built = project.load()
    kind, uid = e2e.target(item, built)
    unit = built[f"{kind}s"][uid]
    if unit.get("blocked"):
        reasons = (unit["blocked"] or {}).get("reasons") or []
        raise ValueError(f"{kind}:{uid} is blocked ({' '.join(str(reasons[0]).split()) if reasons else 'no reason given'}): "
                         "its files are not read")
    chosen = e2e.versions(unit, tested, e2e.image_variables(built))
    services = compose.config_json(compose.all_files([unit["file"]]), timeout=120,
                                   environ={**compose.scrubbed_environment(), **chosen}).get("services", {})
    return pulled(services)


def pulled(services):
    """The images that Docker would pull for these services (the `services` of `docker compose config`), sorted: those that name
    an image and do not build one. What the lab builds is not in a registry yet."""
    return sorted({service["image"] for service in services.values()
                   if isinstance(service, dict) and isinstance(service.get("image"), str) and service["image"]
                   and not service.get("build")})


def check(found, ask=None):
    """{image: True (there), False (missing) or None (the registry did not say)} for these images, asked in parallel."""
    ask = ask or exists
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(found, pool.map(ask, found)))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    github = os.environ.get("GITHUB_ACTIONS") == "true"
    if not argv or len(argv) > 2 or (len(argv) == 2 and not argv[1].isdigit()):
        sys.exit(USAGE)
    try:
        e2e.parse(argv[0])
    except ValueError:
        sys.exit(USAGE)
    try:
        found = images(argv[0], int(argv[1]) if len(argv) == 2 else None)
    except ValueError as error:
        said = " ".join(str(error).split())
        if not github:
            sys.exit(said)
        with e2e.data(print, github):   # the message may begin with the words of a unit, and on GitHub a line that begins `::` is a command
            print(said)
        sys.exit(1)
    results = check(found, exists)
    missing = [image for image, there in results.items() if there is False]
    with e2e.data(print, github):
        for image, there in results.items():
            print(f"{'✓' if there else '✗' if there is False else '?'} {image}"
                  + ("" if there is not None else ": the registry did not say"))
        if missing:
            print(f"\n{len(missing)} image{'s' if len(missing) != 1 else ''} not found: check its version and its registry")
        else:
            print(f"\n{len(found)} images")
    return 1 if missing else 0


__all__ = ["Pinned", "Pinning", "answer", "check", "exists", "guarded", "images", "local", "main", "public", "pulled", "reference",
           "token"]


if __name__ == "__main__":
    sys.exit(main())
