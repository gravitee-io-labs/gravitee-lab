"""image_manifests.py: how an image is read, what a registry is asked and what its answer says. No network: the registries are given."""

import contextlib
import io
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import image_manifests  # noqa: E402


class Response:
    def __init__(self, status=200, headers=None, body=b""):
        self.status, self.headers, self.body = status, headers or {}, body

    def __enter__(self):
        return self

    def __exit__(self, *error):
        return False

    def read(self):
        return self.body


def registry(routes):
    """An opener whose answers are those of `routes`, by (method, url, whether it brings a token): a status, or a Response. Every
    request is kept, in `.asked`."""
    def opener(request, timeout=0):
        opener.asked.append((request.get_method(), request.full_url, request.get_header("Authorization")))
        key = (request.get_method(), request.full_url, bool(request.get_header("Authorization")))
        found = routes.get(key, 404)
        if isinstance(found, Response) and found.status < 400:
            return found
        status, headers = (found.status, found.headers) if isinstance(found, Response) else (found, {})
        raise urllib.error.HTTPError(request.full_url, status, "", headers, io.BytesIO(b""))
    opener.asked = []
    return opener


# The addresses of the names the tests use: public ones, and the names a hostile image would use to reach the machine
NAMES = {"registry-1.docker.io": "44.0.0.1", "auth.docker.io": "44.0.0.2", "docker.elastic.co": "44.0.0.3", "ghcr.io": "44.0.0.4",
         "graviteeio.azurecr.io": "44.0.0.5", "auth.example.com": "44.0.0.6", "cdn.example.com": "44.0.0.7",
         "registry.example.com": "44.0.0.8", "localhost.": "127.0.0.1", "127.0.0.1.nip.io": "127.0.0.1",
         "internal.example.com": "10.0.0.9", "mixed.example.com": ["44.0.0.9", "10.0.0.9"], "v6.example.com": "fe80::1%en0"}


def resolver(names=NAMES):
    """The resolver of a machine with no network: the names it is given, and the numeric forms the C library reads as addresses
    (`127.1`, `0x7f.1`, `169.254.43518`), which is what the connection would have reached."""
    def resolve(host, port, type=0):
        if host in names:
            found = names[host] if isinstance(names[host], list) else [names[host]]
        else:
            try:
                found = [str(ipaddress.ip_address(host))]
            except ValueError:
                try:
                    found = [socket.inet_ntoa(socket.inet_aton(host))]
                except OSError:
                    raise socket.gaierror(socket.EAI_NONAME, "Name or service not known") from None
        return [(socket.AF_INET6 if ":" in one else socket.AF_INET, socket.SOCK_STREAM, 6, "", (one, port)) for one in found]
    return resolve


class Offline(unittest.TestCase):
    """A case that asks no resolver of the machine: nothing in these tests needs a network, and a name resolves as it is told."""

    def setUp(self):
        patcher = mock.patch.object(image_manifests, "RESOLVE", resolver())
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(os.environ)   # a test that runs on GitHub is not one that prints for it
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("GITHUB_ACTIONS", None)


class ReferenceTest(unittest.TestCase):
    def test_an_image_as_docker_reads_it(self):
        hub = "registry-1.docker.io"
        for image, wanted in (("graviteeio/apim-gateway:4.13.0", (hub, "graviteeio/apim-gateway", "4.13.0")),   # no host: Docker Hub
                              ("redis:8.10.2-alpine", (hub, "library/redis", "8.10.2-alpine")),   # an official image is in library
                              ("mongo", (hub, "library/mongo", "latest")),   # no tag: latest
                              ("docker.io/library/mongo:8", (hub, "library/mongo", "8")),
                              ("docker.elastic.co/elasticsearch/elasticsearch:9.5.3", ("docker.elastic.co", "elasticsearch/elasticsearch", "9.5.3")),
                              ("graviteeio.azurecr.io/apim-management-api:amp-demo-latest", ("graviteeio.azurecr.io", "apim-management-api", "amp-demo-latest")),
                              ("localhost:5000/x/y:1", ("localhost:5000", "x/y", "1")),   # a host has a dot or a port
                              ("node:24-alpine@sha256:abc", (hub, "library/node", "sha256:abc"))):   # a digest in place of a tag
            with self.subTest(image=image):
                self.assertEqual(image_manifests.reference(image), wanted)


class ExistsTest(Offline):
    URL = "https://registry-1.docker.io/v2/graviteeio/apim-gateway/manifests/4.13.0"
    CHALLENGE = 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io",scope="repository:graviteeio/apim-gateway:pull"'
    TOKEN = "https://auth.docker.io/token?service=registry.docker.io&scope=repository:graviteeio/apim-gateway:pull"

    def test_a_manifest_the_registry_gives_is_there(self):
        self.assertTrue(image_manifests.exists("graviteeio/apim-gateway:4.13.0", registry({("HEAD", self.URL, False): Response()})))

    def test_a_registry_that_wants_a_token_is_asked_with_the_anonymous_one(self):
        opener = registry({("HEAD", self.URL, False): Response(401, {"WWW-Authenticate": self.CHALLENGE}),
                           ("GET", self.TOKEN, False): Response(body=json.dumps({"token": "t0k"}).encode()),
                           ("HEAD", self.URL, True): Response()})
        self.assertTrue(image_manifests.exists("graviteeio/apim-gateway:4.13.0", opener))
        self.assertEqual([call[0] for call in opener.asked], ["HEAD", "GET", "HEAD"], "a HEAD: no pull is counted")
        self.assertEqual(opener.asked[-1][2], "Bearer t0k")

    def test_a_tag_the_registry_does_not_have_is_missing(self):
        opener = registry({("HEAD", self.URL, False): Response(401, {"WWW-Authenticate": self.CHALLENGE}),
                           ("GET", self.TOKEN, False): Response(body=b'{"access_token": "t0k"}'),
                           ("HEAD", self.URL, True): 404})
        self.assertIs(image_manifests.exists("graviteeio/apim-gateway:4.13.0", opener), False)

    def test_what_cannot_be_told_is_neither_there_nor_missing(self):
        # A redirect that was not followed says nothing of the image, and a login is not a missing image
        for answer in (503, 429, 307, Response(401, {"WWW-Authenticate": "Basic realm=x"})):
            with self.subTest(answer=answer):
                self.assertIsNone(image_manifests.exists("graviteeio/apim-gateway:4.13.0", registry({("HEAD", self.URL, False): answer})))
        self.assertIsNone(image_manifests.exists("graviteeio/apim-gateway:4.13.0", mock.Mock(side_effect=OSError("no route"))))

    def test_a_registry_on_the_network_of_the_machine_is_not_asked(self):
        """An image names its registry in a file anyone wrote, the runner that asks is inside that network, and it connects by
        what the resolver says, however the name reads."""
        for image in ("localhost:5000/x/y:1", "127.0.0.1/x:1", "10.0.0.5:5000/x:1", "192.168.1.9/x:1", "169.254.169.254/x:1",
                      "registry.internal/x:1", "registry.local/x:1", "intranet:5000/x:1", "[::1]:5000/x:1", "app.localhost/x:1",
                      *(f"{host}/x/y:1" for host in self.HIDDEN)):
            with self.subTest(image=image):
                opener = registry({})
                self.assertIsNone(image_manifests.exists(image, opener))
                self.assertEqual(opener.asked, [], "nothing was sent")

    def test_which_hosts_are_the_network_of_the_machine(self):
        """By the name too, where the resolver answers a public address: behind a proxy it is the proxy that resolves the name."""
        names = dict.fromkeys(("localhost", "nas", "x.internal", "x.local", "x.lan", "x.localhost", "x.home.arpa"), "44.0.0.1")
        with mock.patch.object(image_manifests, "RESOLVE", resolver(names)):
            for host in (*names, "localhost:5000", "127.0.0.1:5000", "10.1.2.3", "172.16.0.1", "[::1]:5000", "[fd00::1]", "::1", "169.254.169.254"):
                with self.subTest(host=host):
                    self.assertFalse(image_manifests.public(host))

    # The ways to write an address of this machine or its network that `ipaddress` does not read as one, and that a connection does
    HIDDEN = ("127.1", "0x7f.1", "10.1", "192.168.1", "169.254.43518", "0177.0.0.1", "localhost.", "127.0.0.1.nip.io",
              "internal.example.com", "mixed.example.com", "v6.example.com", "[::1]:5000", "[fe80::1]", "unknown.example.com")
    LOGIN = ("user@127.0.0.1", "user:pw@127.1:5000", "user@localhost.")   # where a URL, not an image, may carry one

    def test_a_host_is_public_by_what_it_resolves_to_and_not_by_how_it_is_written(self):
        for host in ("registry-1.docker.io", "ghcr.io:443", "docker.elastic.co", "graviteeio.azurecr.io:5000", "8.8.8.8", "44.0.0.1",
                     "user@registry.example.com", "[2606:4700::1111]:443"):
            with self.subTest(host=host):
                self.assertTrue(image_manifests.public(host))
        for host in (*self.HIDDEN, *self.LOGIN):
            with self.subTest(host=host):
                self.assertFalse(image_manifests.public(host))
        for host in ("", "a b", "[::1", "host:notaport", "host:99999", "@", ":5000", "a" * 300):
            with self.subTest(odd=host):
                self.assertFalse(image_manifests.public(host), "what is no host is not asked")

    def test_a_resolver_that_fails_or_answers_oddly_is_not_a_public_registry(self):
        answers = [mock.Mock(side_effect=failure) for failure in (OSError("down"), UnicodeError("label too long"), ValueError("x"))]
        answers += [mock.Mock(return_value=found) for found in ([], [(2, 1, 6, "", ())], [(2, 1, 6, "", ("not an address", 443))])]
        for resolve in answers:
            with self.subTest(resolve=resolve), mock.patch.object(image_manifests, "RESOLVE", resolve):
                self.assertFalse(image_manifests.public("registry.example.com"))

    def test_a_token_is_not_asked_for_at_a_realm_that_leads_to_this_network(self):
        for realm in (*(f"https://{host}/token" for host in (*self.HIDDEN, *self.LOGIN)), "http://auth.example.com/token",
                      "https://127.0.0.1/token", "https://localhost/token", "https://10.0.0.1:8443/t", "https://user:pw@internal/token",
                      "ftp://auth.example.com/t", "//auth.example.com/t", "auth.example.com/t", ""):
            with self.subTest(realm=realm):
                opener = registry({})
                self.assertIsNone(image_manifests.token(f'Bearer realm="{realm}",service="s",scope="r"', opener))
                self.assertEqual(opener.asked, [])

    def test_an_answer_that_holds_no_token_is_none_and_not_a_crash(self):
        for body in (b"<html>", b"[1]", b'"token"', b"null", b"3", b"{}"):
            with self.subTest(body=body):
                self.assertIsNone(image_manifests.token(self.CHALLENGE, registry({("GET", self.TOKEN, False): Response(body=body)})))
        self.assertIsNone(image_manifests.token("Basic realm=x", registry({})))
        self.assertIsNone(image_manifests.token(None, registry({})))
        self.assertIsNone(image_manifests.token(self.CHALLENGE, registry({})), "a refusal of the realm is no token")


class Context(ssl.SSLContext):
    """A real SSL context (an HTTPS connection reads its settings, which differ from one Python to the next) that makes no
    handshake: the server of these tests speaks plain HTTP. It keeps the name each connection was made for, which is what a real
    context would check the certificate against."""

    def __new__(cls):
        return super().__new__(cls, ssl.PROTOCOL_TLS_CLIENT)

    def __init__(self):
        super().__init__()
        self.names = []

    def wrap_socket(self, sock, server_hostname=None, **kwargs):
        self.names.append(server_hostname)
        return sock


class Server:
    """A registry on this machine that answers a request with the status and the redirect its routes give, and keeps the head of
    every request it reads. The connection does not go where the address says: `connect` takes it here, which is how a test
    sees what address the connection was asked for."""

    def __init__(self, routes):
        self.routes, self.heads = routes, []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while True:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            with client:
                head = b""
                while b"\r\n\r\n" not in head:
                    more = client.recv(4096)
                    if not more or (not head and more[:1] == b"\x16"):   # nothing more, or a TLS hello, which this server cannot answer
                        break
                    head += more
                text = head.decode("latin-1")
                if not text:
                    self.heads.append("")
                    continue
                self.heads.append(text)
                path = text.split(" ")[1] if text.count(" ") > 1 else "/"
                code, location = self.routes.get(path, (404, None))
                reply = f"HTTP/1.1 {code} Answer\r\nContent-Length: 0\r\nConnection: close\r\n"
                reply += f"Location: {location}\r\n" if location else ""
                client.sendall(reply.encode("latin-1") + b"\r\n")

    def close(self):
        self.sock.close()


class Served(Offline):
    """Requests over the script's own handlers, each connection taken to the registry of the test."""

    def setUp(self):
        super().setUp()
        self.asked, self.context = [], Context()
        self.real = socket.create_connection

    def serve(self, routes, down=()):
        """The registry of the test, and `connect` taking every connection to it. `down` are the addresses that do not answer."""
        server = Server(routes)
        self.addCleanup(server.close)

        def connect(address, timeout=None, source_address=None):
            self.asked.append(address)
            if address[0] in down:
                raise ConnectionRefusedError(61, "Connection refused")
            return self.real(("127.0.0.1", server.port), timeout)
        patcher = mock.patch.object(image_manifests, "CONNECT", connect)
        patcher.start()
        self.addCleanup(patcher.stop)
        # What the system would do if the script went round the pin: it must not, and a test that lets it fails
        patcher = mock.patch.object(socket, "create_connection", side_effect=AssertionError("a connection that is not pinned"))
        patcher.start()
        self.addCleanup(patcher.stop)
        return server

    def opener(self):
        director = urllib.request.build_opener(image_manifests.Redirect, image_manifests.Pinning(context=self.context))
        return director.open

    def patch_resolver(self, resolve):
        patcher = mock.patch.object(image_manifests, "RESOLVE", resolve)
        patcher.start()
        self.addCleanup(patcher.stop)


class RedirectTest(Served):
    """A registry may send the request elsewhere, but not to the network of the machine, and never with the token it was given."""

    START = "https://registry.example.com/a"

    def test_a_redirect_to_a_public_address_over_https_is_followed_and_pinned_like_the_first_request(self):
        server = self.serve({"/a": (302, "https://cdn.example.com/b"), "/b": (200, None)})
        status, _ = image_manifests.answer(self.START, {}, self.opener())
        self.assertEqual((status, self.asked, self.context.names), (200, [("44.0.0.8", 443), ("44.0.0.7", 443)],
                                                                  ["registry.example.com", "cdn.example.com"]))
        self.assertIn("\r\nHost: cdn.example.com\r\n", server.heads[1])

    def test_a_redirect_to_an_address_of_the_machine_or_to_plain_http_is_not_followed(self):
        server = self.serve({})
        for target in ("http://127.0.0.1/internal", "https://127.0.0.1/internal", "https://127.1/internal", "https://0x7f.1/internal",
                       "https://169.254.169.254/latest", "https://[::1]/x", "https://localhost./x", "https://127.0.0.1.nip.io/x",
                       "https://internal.example.com/x", "https://mixed.example.com/x", "http://cdn.example.com/b",
                       "https://user@127.0.0.1/x", "https://unknown.example.com/x", "//127.0.0.1/x"):
            with self.subTest(target=target):
                self.asked.clear()
                server.routes["/a"] = (302, target)
                status, _ = image_manifests.answer(self.START, {}, self.opener())
                self.assertEqual((status, self.asked), (302, [("44.0.0.8", 443)]), "the redirect is the answer, and nothing else was asked")

    def test_a_path_on_the_same_host_is_the_same_registry(self):
        server = self.serve({"/a": (302, "/relative")})
        image_manifests.answer(self.START, {}, self.opener())
        self.assertIn(" /relative HTTP/1.1\r\n", server.heads[-1])

    def test_a_redirect_of_a_registry_is_no_manifest_and_no_missing_image(self):
        server = self.serve({"/v2/x/y/manifests/1": (307, "http://127.0.0.1/internal")})
        self.assertIsNone(image_manifests.exists("x/y:1", self.opener()))
        self.assertEqual(len(server.heads), 1)

    def test_a_token_that_is_given_stays_with_the_host_it_was_made_for(self):
        server = self.serve({"/a": (302, "https://cdn.example.com/b"), "/b": (302, "https://cdn.example.com/c"), "/c": (200, None),
                             "/d": (302, "https://registry.example.com/e"), "/e": (200, None)})
        sent = {"Authorization": "Bearer t0k"}
        self.assertEqual(image_manifests.answer(self.START, sent, self.opener())[0], 200)
        self.assertEqual(["Authorization: Bearer t0k" in head for head in server.heads], [True, False, False], "not sent to another host")
        server.heads.clear()
        image_manifests.answer("https://registry.example.com/d", sent, self.opener())
        self.assertEqual(["Authorization: Bearer t0k" in head for head in server.heads], [True, True], "the same host keeps it")

    def test_a_token_realm_that_redirects_to_the_machine_gives_no_token(self):
        server = self.serve({"/token?service=s&scope=r": (302, "http://127.0.0.1/internal")})
        self.assertIsNone(image_manifests.token('Bearer realm="https://auth.example.com/token",service="s",scope="r"', self.opener()))
        self.assertEqual(len(server.heads), 1)

    def test_the_opener_the_script_uses_is_this_one(self):
        """The default handlers, one of which resolves the name again, are replaced."""
        kinds = [type(handler) for handler in image_manifests.OPENER.handlers]
        self.assertTrue({image_manifests.Redirect, image_manifests.Pinning} <= set(kinds))
        self.assertFalse({urllib.request.HTTPRedirectHandler, urllib.request.HTTPSHandler} & set(kinds))


class PinnedTest(Served):
    """The connection is made to the address that was checked, and the name is not asked twice and believed once: a name that
    answers a public address to a look and another to the connection (DNS rebinding) leads nowhere."""

    def rebinding(self, *answers):
        """A resolver that gives each question the next of these answers, and the last one from then on. What it was asked, in the
        list it returns."""
        calls = []

        def resolve(host, port, type=0):
            calls.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers[min(len(calls), len(answers)) - 1], port))]
        self.patch_resolver(resolve)
        return calls

    def test_the_connection_goes_to_the_address_that_was_checked_and_the_certificate_is_the_names(self):
        server = self.serve({"/v2/x/y/manifests/1": (200, None)})
        self.assertTrue(image_manifests.exists("registry.example.com/x/y:1", self.opener()))
        self.assertEqual(self.asked, [("44.0.0.8", 443)], "the address of the answer, not the name")
        self.assertEqual(self.context.names, ["registry.example.com"], "TLS is made for the name")
        self.assertRegex(server.heads[0], r"^HEAD /v2/x/y/manifests/1 HTTP/1\.1\r\n")
        self.assertIn("\r\nHost: registry.example.com\r\n", server.heads[0])

    def test_a_name_that_answers_a_public_address_and_then_the_machine_s_own_is_not_followed(self):
        """The look (`public`) is answered with a public address and the connection's own question with the machine's: the old
        connection resolved the name a second time and went there. This one resolves for itself, finds it out and refuses."""
        server = self.serve({"/v2/x/y/manifests/1": (200, None)})
        calls = self.rebinding("44.0.0.8", "127.0.0.1")
        self.assertIsNone(image_manifests.exists("registry.example.com/x/y:1", self.opener()), "the registry did not say")
        self.assertEqual(calls, ["registry.example.com", "registry.example.com"], "the look, and the connection's own")
        self.assertEqual((self.asked, server.heads, self.context.names), ([], [], []), "nothing was connected to")

    def test_the_address_the_connection_resolved_is_the_one_it_goes_to_even_when_the_name_changes_its_mind_after(self):
        server = self.serve({"/v2/x/y/manifests/1": (200, None)})
        calls = self.rebinding("44.0.0.8", "44.0.0.8", "127.0.0.1")   # the look, the connection, and a third that nothing asks for
        self.assertTrue(image_manifests.exists("registry.example.com/x/y:1", self.opener()))
        self.assertEqual((len(calls), self.asked, len(server.heads)), (2, [("44.0.0.8", 443)], 1),
                         "the name is asked for the look and for the connection, never again")

    def test_every_address_of_the_answer_has_to_be_public_and_the_next_one_is_tried_when_one_does_not_answer(self):
        self.serve({"/v2/x/y/manifests/1": (200, None)}, down=("44.0.0.1",))
        self.patch_resolver(resolver({"two.example.com": ["44.0.0.1", "44.0.0.2"], "mixed.example.com": ["44.0.0.9", "10.0.0.9"]}))
        connection = self.connection("two.example.com", 443)
        connection.connect()
        self.assertEqual(self.asked, [("44.0.0.1", 443), ("44.0.0.2", 443)], "in the order of the answer")
        self.asked.clear()
        with self.assertRaisesRegex(OSError, "does not resolve to addresses of the public network"):
            self.connection("mixed.example.com", 443).connect()
        with self.assertRaisesRegex(OSError, "public network"):
            self.connection("nowhere.example.com", 443).connect()
        self.assertEqual(self.asked, [], "one address of the machine's network in the answer is enough to refuse it")

    def test_when_no_address_answers_the_last_error_is_the_error(self):
        self.serve({}, down=("44.0.0.1", "44.0.0.2"))
        self.patch_resolver(resolver({"two.example.com": ["44.0.0.1", "44.0.0.2"]}))
        with self.assertRaises(ConnectionRefusedError):
            self.connection("two.example.com", 443).connect()
        self.assertEqual(self.asked, [("44.0.0.1", 443), ("44.0.0.2", 443)])

    def test_the_opener_of_the_script_connects_to_the_address_it_checked_and_to_no_other(self):
        """`exists` with no opener given is the script as it runs: the handshake of a real context fails against this plain server,
        and that is the answer of a registry that does not say. What counts is where the connection was made, and that it was
        made by the pin: the system's own `create_connection` is a failure of the test."""
        self.serve({})
        self.assertIsNone(image_manifests.exists("registry.example.com/x/y:1"))
        self.assertEqual(self.asked, [("44.0.0.8", 443)])
        self.asked.clear()
        self.rebinding("44.0.0.8", "127.0.0.1")
        self.assertIsNone(image_manifests.exists("registry.example.com/x/y:1"))
        self.assertEqual(self.asked, [], "the name turned to the machine's own address after the look")

    def test_a_token_realm_is_pinned_as_well(self):
        self.serve({})
        self.rebinding("44.0.0.6", "127.0.0.1")
        self.assertIsNone(image_manifests.token('Bearer realm="https://auth.example.com/token",service="s",scope="r"', self.opener()))
        self.assertEqual(self.asked, [], "the realm turned to the machine's own address between the look and the connection")

    def test_behind_a_proxy_the_proxy_resolves_the_name_and_nothing_is_pinned(self):
        self.serve({"registry.example.com:443": (200, None)})   # the proxy agrees to the tunnel
        calls = []
        self.patch_resolver(lambda *args, **kwargs: calls.append(args) or [])
        connection = self.connection("proxy.corp.example", 3128)
        connection.set_tunnel("registry.example.com", 443)
        connection.connect()
        self.assertEqual((self.asked, calls), ([("proxy.corp.example", 3128)], []), "the name of the proxy, as given, and no look")

    def test_the_connection_is_not_pinned_by_the_way_the_name_is_written(self):
        self.serve({"/v2/x/y/manifests/1": (200, None)})
        self.patch_resolver(resolver({"[::1]": "::1"}))
        for host in ("127.0.0.1", "10.0.0.9", "169.254.169.254", "0x7f.1", "localhost.", "127.0.0.1.nip.io", "internal.example.com"):
            with self.subTest(host=host):
                with self.assertRaises(OSError):
                    self.connection(host, 443).connect()
        self.assertEqual(self.asked, [])

    def connection(self, host, port):
        """A pinned connection that is closed when the test ends."""
        connection = image_manifests.Pinned(host, port, context=self.context)
        self.addCleanup(connection.close)
        return connection


class CheckTest(unittest.TestCase):
    def test_every_image_is_asked_and_the_answers_keep_their_images(self):
        answers = {"a:1": True, "b:2": False, "c:3": None}
        self.assertEqual(image_manifests.check(list(answers), answers.get), answers)
        self.assertEqual(image_manifests.check([], answers.get), {})


class ImagesTest(Offline):
    """images(): what the core and an item pull, read the way a file of a stranger is read. The project and Compose are given."""

    def setUp(self):
        super().setUp()
        e2e = image_manifests.e2e
        from lab_ui.docker import compose
        from lab_ui.library import project
        self.built = {"services": {"gateway": {"raw_image": "graviteeio/gateway:${APIM_VERSION:-4.12}"}},
                      "addons": {}, "setups": {},
                      "playgrounds": {"shop": {"id": "shop", "file": ".lab/catalogs/base/playgrounds/shop/compose.yml", "blocked": None,
                                               "name": "Shop", "tested": [{"APIM_VERSION": "4.13.0"}]},
                                      "@hub/bank": {"id": "@hub/bank", "file": ".lab/catalogs/hub/playgrounds/bank/compose.yml",
                                                    "blocked": None, "name": "Bank"},
                                      "stuck": {"id": "stuck", "file": ".lab/catalogs/base/playgrounds/stuck/compose.yml", "name": "Stuck",
                                                "blocked": {"reasons": ["Mounts the Docker\nsocket"]}},
                                      "odd": {"id": "odd", "file": "x", "blocked": None, "name": "Odd", "tested": [{"PATH": "/tmp/mine"}]},
                                      "local/mine": {"id": "local/mine", "file": "x", "blocked": None, "name": "Mine"}}}
        self.services = {"gateway": {"image": "graviteeio/gateway:4.13.0"}, "shop-web": {"image": "shop/web:1", "build": {"context": "."}},
                         "db": {"image": "mongo:8"}, "seed": {"image": "registry.example/seeder", "build": {"context": "../seeder"}},
                         "none": {}}
        self.calls = []
        for target, name, new in ((project, "load", lambda: self.built), (compose, "scrubbed_environment", lambda: {"PATH": "/usr/bin"}),
                                  (compose, "all_files", lambda files: ["-f", "core.yml", "-f", *files]),
                                  (compose, "config_json", self.config)):
            patcher = mock.patch.object(target, name, new)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(e2e.paths, "default_ns", lambda: "base")
        patcher.start()
        self.addCleanup(patcher.stop)

    def config(self, files, timeout=15, environ=None):
        self.calls.append((files, timeout, environ))
        return {"services": self.services}

    def test_the_images_it_pulls_and_not_the_ones_the_lab_builds(self):
        self.assertEqual(image_manifests.images("playground:shop", None), ["graviteeio/gateway:4.13.0", "mongo:8"])
        files, timeout, environ = self.calls[0]
        self.assertEqual((files, timeout), (["-f", "core.yml", "-f", ".lab/catalogs/base/playgrounds/shop/compose.yml"], 120))
        self.assertEqual(environ, {"PATH": "/usr/bin"}, "Compose reads the file with the scrubbed environment: no token of the runner")

    def test_a_set_of_versions_is_given_to_compose_over_that_environment_and_to_no_one_else(self):
        saved = dict(os.environ)
        image_manifests.images("playground:shop", 0)
        self.assertEqual(self.calls[0][2], {"PATH": "/usr/bin", "APIM_VERSION": "4.13.0"})
        self.assertEqual(dict(os.environ), saved, "the process is as it was")

    def test_what_is_not_read_gives_compose_nothing(self):
        for item, tested, said in (("playground:stuck", None, r"playground:stuck is blocked \(Mounts the Docker socket\): its files are not read"),
                                   ("playground:gone", None, "No playground named gone"), ("playground:local/mine", None, "belongs to this machine"),
                                   ("addon:@hub/ghost", None, "No add-on named @hub/ghost"), ("mailer", None, "Name the item as kind:id"),
                                   ("addon:Mailer", None, "Name the item as kind:id"), ("playground:shop", 1, "choose from 0 to 0"),
                                   ("playground:odd", 0, "sets 'PATH' in its versions")):
            with self.subTest(item=item), self.assertRaisesRegex(ValueError, said):
                image_manifests.images(item, tested)
        self.assertEqual(self.calls, [])

    def test_main_reads_an_item_of_any_catalog_and_says_what_it_cannot_read_on_one_line(self):
        out = io.StringIO()
        with mock.patch.object(image_manifests, "exists", return_value=True), contextlib.redirect_stdout(out):
            self.assertEqual(image_manifests.main(["playground:@hub/bank"]), 0)
        self.assertIn("✓ mongo:8", out.getvalue())
        self.assertEqual(self.calls[0][0][-1], ".lab/catalogs/hub/playgrounds/bank/compose.yml")
        with self.assertRaises(SystemExit) as caught:
            image_manifests.main(["playground:stuck"])
        self.assertEqual(str(caught.exception), "playground:stuck is blocked (Mounts the Docker socket): its files are not read")


class GithubTest(Offline):
    """On GitHub a line that begins `::` is a command, and the first words of a message are a unit's own name: what the script
    says of a unit, or of its images, is data there."""

    def setUp(self):
        super().setUp()
        os.environ["GITHUB_ACTIONS"] = "true"

    def test_the_name_of_a_unit_in_a_refusal_cannot_write_a_workflow_command(self):
        hostile = "::error title=pwn::pwned ::add-mask::OK"
        out = io.StringIO()
        with mock.patch.object(image_manifests, "images", side_effect=ValueError(f"{hostile}\nwas tested with 2 sets of versions")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as caught:
            image_manifests.main(["playground:shop", "7"])
        self.assertEqual(caught.exception.code, 1)
        lines = out.getvalue().splitlines()
        at = next(i for i, line in enumerate(lines) if "pwned" in line)
        self.assertEqual(lines[at], f"{hostile} was tested with 2 sets of versions", "on one line")
        self.assertRegex(lines[at - 1], r"^::stop-commands::[0-9a-f]{32}$")
        self.assertEqual(lines[at + 1], "::" + lines[at - 1].split("::")[-1] + "::", "closed by the token only the script knew")
        self.assertEqual(err.getvalue(), "", "one stream, so that the block is not split")

    def test_the_images_it_prints_are_data_too(self):
        out = io.StringIO()
        with mock.patch.object(image_manifests, "images", return_value=["a:1", "::error::b:2"]), \
                mock.patch.object(image_manifests, "exists", side_effect=lambda image: image == "a:1"), contextlib.redirect_stdout(out):
            self.assertEqual(image_manifests.main(["playground:shop"]), 1)
        lines = out.getvalue().splitlines()
        opened = [i for i, line in enumerate(lines) if line.startswith("::stop-commands::")]
        closed = [i for i, line in enumerate(lines) if re.fullmatch(r"::[0-9a-f]{32}::", line)]
        self.assertEqual((len(opened), len(closed)), (1, 1))
        for needle in ("✓ a:1", "✗ ::error::b:2", "1 image not found"):
            at = next(i for i, line in enumerate(lines) if needle in line)
            self.assertTrue(opened[0] < at < closed[0], f"{needle!r} is outside the block")


class MainTest(Offline):
    def test_what_it_says_of_the_images_and_how_it_ends(self):
        for there, code, said in (({"a:1": True, "b:2": True}, 0, "✓ a:1"), ({"a:1": True, "b:2": True}, 0, "2 images"),
                                  ({"a:1": True, "b:2": False}, 1, "✗ b:2"), ({"a:1": True, "b:2": False}, 1, "1 image not found"),
                                  ({"a:1": None}, 0, "? a:1: the registry did not say")):
            with self.subTest(there=there, said=said), mock.patch.object(image_manifests, "images", return_value=list(there)), \
                    mock.patch.object(image_manifests, "exists", side_effect=there.get), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(image_manifests.main(["addon:mailer", "0"]), code)
                self.assertIn(said, out.getvalue())
                self.assertNotIn("::", out.getvalue(), "off GitHub nothing is wrapped")

    def test_a_command_line_of_another_form_prints_how_to_ask(self):
        for argv in ([], ["addon:mailer", "x"], ["addon:mailer", "0", "1"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as caught:
                image_manifests.main(argv)
            self.assertIn("image_manifests.py ITEM [TESTED]", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
