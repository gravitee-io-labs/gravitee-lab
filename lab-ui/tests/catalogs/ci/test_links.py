"""`catalog.py ci links` (LK01): the links of the guides and the READMEs answer.

The text of a guide is a stranger's, and the run is inside a network. These tests prove what is asked (the links of Markdown, not the
code in it, not a template, not an address reserved for documentation), what is never asked (an address that is not on the public
network, by its name or by a redirect, and a name that answers one address to the look and another to the connection), what counts
as an answer, and that LK01 is a warning. The network is a small server of this machine, reached through the seams of the fetcher
(a name that resolves to a public address that is made to connect there), so no test needs a network."""

import io
import json
import os
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from lab_ui.catalogs import ci, explain, tree as trees
from lab_ui.catalogs.ci import links
from lab_ui.catalogs.ci import main as ci_main
from tests import Case
from tests.data.rules import support

PUBLIC = "93.184.216.34"


def lines(text):
    return text.split("\n")


class Server:
    """A web server on this machine with the answers it is told to give: {path: (status, headers, delay)} for HEAD and GET, or a
    list of such answers for a path asked several times (the last one repeats). It records every request."""

    def __init__(self, case):
        self.routes, self.requests, self.used = {}, [], {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def handle_any(self):
                outer.requests.append((self.command, self.path, dict(self.headers)))
                key = (self.command, self.path)
                answer = outer.routes.get(key) or outer.routes.get(self.path) or (404, {}, 0)
                if isinstance(answer, list):
                    number = outer.used.get(key, 0)
                    outer.used[key] = number + 1
                    answer = answer[min(number, len(answer) - 1)]
                status, headers, delay = (tuple(answer) + (None, None))[:3] if len(answer) < 3 else answer
                if delay:
                    time.sleep(delay)
                self.send_response(status)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_HEAD = do_GET = handle_any

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        case.addCleanup(thread.join, 5)
        case.addCleanup(self.server.server_close)
        case.addCleanup(self.server.shutdown)

    def methods(self, path):
        return [method for method, asked, _ in self.requests if asked == path]


class Network:
    """The seams of a fetcher that reach the server above whatever the name is: what is asked of the resolver, and what is connected."""

    def __init__(self, server, addresses=(PUBLIC,), per_name=None):
        self.server, self.addresses, self.per_name = server, list(addresses), per_name or {}
        self.resolved, self.connected = [], []

    def resolve(self, host, port):
        self.resolved.append((host, port))
        answer = self.per_name.get(host, self.addresses)
        if isinstance(answer, Exception):
            raise answer
        return list(answer)

    def connect(self, address, timeout):
        self.connected.append(address)
        return socket.create_connection(("127.0.0.1", self.server.port), timeout)

    def fetcher(self, timeout=5, sleep=lambda seconds: None):
        return links.Fetcher(self.resolve, self.connect, lambda sock, host: sock, timeout, sleep)


class Extraction(unittest.TestCase):

    def found(self, text, path="README.md"):
        return [(each.line, each.url) for each in links.links_of(path, text)]

    def test_every_way_a_renderer_makes_a_link_is_one(self):
        text = ("# Guide\n"
                "An [inline](https://docs.acme.org/a) link and a [reference][r] and an autolink <https://docs.acme.org/b>.\n"
                "\n[r]: https://docs.acme.org/c \"A title\"\n"
                "A bare address https://docs.acme.org/d, then more.\n"
                '<a href="https://docs.acme.org/e">raw</a> and <img src=\'https://img.acme.org/f.png\'>\n')
        self.assertEqual(self.found(text), [(2, "https://docs.acme.org/a"), (2, "https://docs.acme.org/b"),
                                            (4, "https://docs.acme.org/c"), (5, "https://docs.acme.org/d"),
                                            (6, "https://docs.acme.org/e"), (6, "https://img.acme.org/f.png")])

    def test_code_is_not_a_link(self):
        text = ("```sh\ncurl https://docs.acme.org/in-a-fence\n```\n"
                "Use `https://docs.acme.org/in-a-span` or ``a `https://docs.acme.org/two` b``.\n"
                "~~~\nhttps://docs.acme.org/tilde\n~~~\n"
                "Outside https://docs.acme.org/real.\n")
        self.assertEqual(self.found(text), [(8, "https://docs.acme.org/real")])

    def test_the_punctuation_around_an_address_is_not_part_of_it(self):
        text = ("(see https://docs.acme.org/a). Also https://docs.acme.org/b, and https://docs.acme.org/c! "
                "[x](https://docs.acme.org/wiki/Thing_(disambiguation)) and (https://docs.acme.org/d)\n"
                "[https://docs.acme.org/same](https://docs.acme.org/same)\n")
        self.assertEqual([url for _, url in self.found(text)], [
            "https://docs.acme.org/a", "https://docs.acme.org/b", "https://docs.acme.org/c",
            "https://docs.acme.org/wiki/Thing_(disambiguation)", "https://docs.acme.org/d", "https://docs.acme.org/same"])

    def test_a_template_an_example_and_an_address_with_a_user_are_not_asked(self):
        for url in ("https://${HOST}/x", "https://{host}/x", "https://<host>/x", "https://*.acme.org/x", "https://acme.org/%7Bid%7D",
                    "https://localhost:8080/x", "http://gateway/x", "https://example.com/x", "https://www.example.org/x",
                    "https://api.acme.test/x", "https://x.example/x", "https://x.invalid/x", "https://nas.local/x",
                    "https://user:pass@docs.acme.org/x", "https://token@docs.acme.org/x", "https://docs.acme.org:0/x",
                    "https://docs.acme.org:99999/x", "ftp://docs.acme.org/x", "mailto:a@docs.acme.org"):
            with self.subTest(url=url):
                self.assertEqual(self.found(f"A link <{url}> and [one]({url}) and {url}\n"), [], url)

    def test_the_same_address_twice_on_a_line_is_one_and_on_two_lines_is_two(self):
        text = "https://docs.acme.org/a and https://docs.acme.org/a\nhttps://docs.acme.org/a\n"
        self.assertEqual(self.found(text), [(1, "https://docs.acme.org/a"), (2, "https://docs.acme.org/a")])

    def test_one_file_gives_a_bounded_number(self):
        text = "\n".join(f"https://docs.acme.org/{number}" for number in range(links.MAX_PER_FILE + 100))
        self.assertEqual(len(self.found(text)), links.MAX_PER_FILE)

    def test_a_line_of_a_megabyte_is_read_in_under_a_second(self):
        for text in ("https://" + "a" * 1_000_000, "[" * 500_000 + "https://docs.acme.org/x", "](" * 300_000,
                     "`" * 500_000 + "https://docs.acme.org/x", ("https://docs.acme.org/" + "(" * 100) * 5000):
            with self.subTest(start=text[:12]):
                _, spent = support.timed(links.links_of, "README.md", text)
                self.assertLess(spent, 1.0)

    def test_the_markdown_files_of_a_tree_are_read_in_the_order_of_their_paths(self):
        files = {"README.md": "https://docs.acme.org/readme\n", "docs/guide.md": "x\n\nhttps://docs.acme.org/guide\n",
                 "addons/hello/README.md": "https://docs.acme.org/hello\n", "notes.txt": "https://docs.acme.org/txt\n",
                 "addons/hello/compose.yml": "# https://docs.acme.org/yml\n"}
        tree = trees.ListTree.from_files(files)
        self.assertEqual([(each.path, each.line, each.url) for each in links.markdown_links(tree)],
                         [("README.md", 1, "https://docs.acme.org/readme"), ("addons/hello/README.md", 1, "https://docs.acme.org/hello"),
                          ("docs/guide.md", 3, "https://docs.acme.org/guide")])

    def test_what_is_not_a_regular_file_is_never_read(self):
        tree = trees.ListTree([("120000", "LINK.md", 10), ("100644", "README.md", 30)],
                              lambda path, limit=0: b"https://docs.acme.org/readme" if path == "README.md" else b"https://x.acme.org/")
        self.assertEqual([each.path for each in links.markdown_links(tree)], ["README.md"])

    def test_the_names_of_hosts(self):
        for host in ("localhost", "LOCALHOST.", "gateway", "a.test", "a.b.local", "example.com", "x.example.net", "nas.internal"):
            self.assertTrue(links.reserved(host), host)
        for host in ("docs.acme.org", "example.com.acme.org", "93.184.216.34", "github.com"):
            self.assertFalse(links.reserved(host), host)


class Addresses(unittest.TestCase):

    def test_only_an_address_of_the_public_network_is_one_to_connect_to(self):
        for text in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "::1", "fe80::1%en0",
                     "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "64:ff9b::7f00:1", "224.0.0.1", "not an address", ""):
            self.assertFalse(links.is_public(text), text)
        for text in ("93.184.216.34", "8.8.8.8", "2606:2800:220:1:248:1893:25c8:1946", "::ffff:93.184.216.34"):
            self.assertTrue(links.is_public(text), text)

    def test_what_a_link_answers_in_the_words_of_lk01(self):
        for answer, expected in ((links.Answer(200), None), (links.Answer(301), None), (links.Answer(401), None),
                                 (links.Answer(403), None), (links.Answer(429), None), (links.Answer(999), None),
                                 (links.Answer(404), "404"), (links.Answer(410), "410"), (links.Answer(400), "400"),
                                 (links.Answer(500), "500"), (links.Answer(503), "503"),
                                 (links.Answer(0, "no answer: timed out"), "nothing (timed out)"),
                                 (links.Answer(0, "no answer: the name does not resolve"), "nothing (the name does not resolve)"),
                                 (links.Answer(0, "the address is not on the public network", False), None)):
            with self.subTest(answer=answer):
                self.assertEqual(links.broken(answer), expected)

    def test_the_message_is_the_words_of_the_standard(self):
        found = links.findings([links.Link("docs/guide.md", 12, "https://docs.acme.org/gone")],
                               {"https://docs.acme.org/gone": links.Answer(404)})
        self.assertEqual([(each.code, each.severity, each.path, each.line, each.message) for each in found], [
            ("LK01", "warning", "docs/guide.md", 12, "docs/guide.md:12: the link https://docs.acme.org/gone answers 404.")])
        row = next(row for row in explain.STAGES if row[0] == "LK01")
        self.assertEqual((row[1], row[3]), (found[0].severity, links.MESSAGE))

    def test_a_long_address_is_shown_cut(self):
        url = "https://docs.acme.org/" + "a" * 400
        found = links.findings([links.Link("README.md", 1, url)], {url: links.Answer(404)})
        self.assertLess(len(found[0].message), 200)
        self.assertIn("…", found[0].message)


class Asking(Case):
    """The fetcher over a server of this machine."""

    def setUp(self):
        self.server = Server(self)
        self.net = Network(self.server)
        self.fetch = self.net.fetcher()

    def test_a_page_that_is_there_answers_200_to_a_head_and_nothing_else_is_asked(self):
        self.server.routes["/ok"] = (200, {}, 0)
        self.assertEqual(self.fetch("http://docs.acme.org/ok"), links.Answer(200))
        self.assertEqual(self.server.methods("/ok"), ["HEAD"])

    def test_the_request_carries_a_fixed_agent_the_name_asked_and_nothing_of_the_environment(self):
        self.server.routes["/ok?a=1"] = (200, {}, 0)
        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://proxy.invalid", "GH_TOKEN": "x", "HTTP_COOKIE": "y", "AUTHORIZATION": "z"}):
            self.fetch("http://docs.acme.org:8080/ok?a=1#fragment")
        method, path, headers = self.server.requests[0]
        self.assertEqual((method, path), ("HEAD", "/ok?a=1"))
        self.assertEqual(headers["User-Agent"], links.AGENT)
        self.assertEqual(headers["Host"], "docs.acme.org:8080")
        self.assertFalse({"Cookie", "Authorization", "Proxy-Authorization", "Referer"} & set(headers))
        self.assertEqual(self.net.resolved, [("docs.acme.org", 8080)])

    def test_a_missing_page_is_404_after_a_get_confirms_it(self):
        self.server.routes["/gone"] = (404, {}, 0)
        self.assertEqual(self.fetch("http://docs.acme.org/gone"), links.Answer(404))
        self.assertEqual(self.server.methods("/gone"), ["HEAD", "GET"])

    def test_a_server_that_mishandles_head_is_asked_with_get(self):
        self.server.routes[("HEAD", "/odd")] = (405, {"Allow": "GET"}, 0)
        self.server.routes[("GET", "/odd")] = (200, {}, 0)
        self.assertEqual(self.fetch("http://docs.acme.org/odd"), links.Answer(200))
        self.assertEqual(self.server.methods("/odd"), ["HEAD", "GET"])

    def test_a_redirect_is_followed_to_where_it_leads(self):
        self.server.routes["/a"] = (301, {"Location": "/b"}, 0)
        self.server.routes["/b"] = (302, {"Location": "http://docs.acme.org/c"}, 0)
        self.server.routes["/c"] = (200, {}, 0)
        self.assertEqual(self.fetch("http://docs.acme.org/a"), links.Answer(200))
        self.assertEqual([asked for _, asked, _ in self.server.requests], ["/a", "/b", "/c"])

    def test_a_redirect_to_a_page_that_is_gone_is_gone(self):
        self.server.routes["/a"] = (301, {"Location": "/b"}, 0)
        self.assertEqual(self.fetch("http://docs.acme.org/a").status, 404)

    def test_a_loop_of_redirects_ends(self):
        self.server.routes["/a"] = (302, {"Location": "/b"}, 0)
        self.server.routes["/b"] = (302, {"Location": "/a"}, 0)
        self.assertEqual(self.fetch("http://docs.acme.org/a"), links.Answer(0, "too many redirects"))
        self.assertEqual(len(self.server.requests), links.HOPS + 1)

    def test_a_redirect_to_the_private_network_is_not_followed(self):
        self.net.per_name["inside.acme.org"] = ["10.0.0.7"]
        self.server.routes["/a"] = (302, {"Location": "http://inside.acme.org/secret"}, 0)
        answer = self.fetch("http://docs.acme.org/a")
        self.assertEqual(answer, links.Answer(0, "the address is not on the public network", False))
        self.assertEqual(self.server.methods("/secret"), [])
        self.assertEqual(self.net.connected, [(PUBLIC, 80)])   # (one connection: the first page; none to 10.0.0.7)

    def test_a_redirect_to_another_scheme_is_the_answer(self):
        self.server.routes["/a"] = (302, {"Location": "ftp://docs.acme.org/file"}, 0)
        self.assertEqual(self.fetch("http://docs.acme.org/a"), links.Answer(302))

    def test_a_name_that_resolves_to_the_private_network_is_never_connected_to(self):
        for addresses in (["127.0.0.1"], ["10.0.0.1"], ["169.254.169.254"], [PUBLIC, "192.168.0.9"], ["::1"], ["::ffff:127.0.0.1"], []):
            with self.subTest(addresses=addresses):
                net = Network(self.server, addresses)
                answer = net.fetcher()("http://docs.acme.org/ok")
                self.assertEqual(answer, links.Answer(0, "the address is not on the public network", False))
                self.assertEqual(net.connected, [])
        self.assertEqual(self.server.requests, [])

    def test_the_connection_is_made_to_the_address_that_was_judged_not_to_the_name(self):
        self.server.routes["/ok"] = (200, {}, 0)
        asked = []

        def rebinding(host, port):   # the second look would have answered 127.0.0.1: there is no second look
            asked.append(host)
            return [PUBLIC] if len(asked) == 1 else ["127.0.0.1"]
        net = Network(self.server)
        net.resolve = rebinding
        net.fetcher()("http://docs.acme.org/ok")
        self.assertEqual(asked, ["docs.acme.org"])
        self.assertEqual(net.connected, [(PUBLIC, 80)])

    def test_a_name_that_does_not_resolve_is_no_answer(self):
        net = Network(self.server, per_name={"docs.acme.org": socket.gaierror(8, "nodename nor servname provided")})
        slept = []
        self.assertEqual(net.fetcher(sleep=slept.append)("http://docs.acme.org/ok"), links.Answer(0, "no answer: the name does not resolve"))
        self.assertEqual(slept, [], "a name that does not resolve is not asked again")

    def test_a_host_that_does_not_answer_in_time_is_no_answer(self):
        self.server.routes["/slow"] = (200, {}, 4)
        started = time.monotonic()
        answer = self.net.fetcher(timeout=0.3)("http://docs.acme.org/slow")
        self.assertEqual(answer, links.Answer(0, "no answer: timed out"))
        self.assertLess(time.monotonic() - started, 3.5, "it did not wait for the server to finish")

    def test_a_refused_connection_is_no_answer(self):
        def refuse(address, timeout):
            raise ConnectionRefusedError(61, "Connection refused")
        net = Network(self.server)
        net.connect = refuse
        self.assertEqual(net.fetcher()("http://docs.acme.org/ok"), links.Answer(0, "no answer: the connection was refused"))

    def test_a_server_that_says_5xx_is_asked_once_more(self):
        slept = []
        self.server.routes[("HEAD", "/busy")] = [(503, {}, 0), (200, {}, 0)]
        self.server.routes[("GET", "/busy")] = [(503, {}, 0), (200, {}, 0)]
        self.assertEqual(self.net.fetcher(sleep=slept.append)("http://docs.acme.org/busy"), links.Answer(200))
        self.assertEqual(slept, [2])
        self.server.routes["/down"] = (500, {}, 0)
        self.assertEqual(self.net.fetcher(sleep=slept.append)("http://docs.acme.org/down").status, 500)
        self.assertEqual(slept, [2, 2])

    def test_a_certificate_that_is_not_accepted_is_no_answer(self):
        import ssl

        def refuse(sock, host):
            raise ssl.SSLCertVerificationError("certificate verify failed")
        net = Network(self.server)
        self.assertEqual(links.Fetcher(net.resolve, net.connect, refuse, 5, lambda seconds: None)("https://docs.acme.org/ok"),
                         links.Answer(0, "no answer: the certificate is not accepted"))

    def test_https_is_asked_over_tls_for_the_name(self):
        wrapped = []

        def wrap(sock, host):
            wrapped.append(host)
            return sock
        self.server.routes["/ok"] = (200, {}, 0)
        net = Network(self.server)
        answer = links.Fetcher(net.resolve, net.connect, wrap, 5, lambda seconds: None)("https://docs.acme.org/ok")
        self.assertEqual((answer, wrapped, net.resolved), (links.Answer(200), ["docs.acme.org"], [("docs.acme.org", 443)]))

    def test_a_name_that_is_not_text_is_not_asked(self):
        for url in ("http://", "http://[::1", "http://exa mple.org/", "http://a\u0000b.org/", "http://" + "a" * 70 + ".org/"):
            with self.subTest(url=url):
                self.assertFalse(self.fetch(url).status)
        self.assertEqual(self.server.requests, [])


class Stage(Case):

    def setUp(self):
        self.out, self.err = io.StringIO(), io.StringIO()
        self.asked = []

    def run_stage(self, files, answers, *extra, **seams):
        tree = trees.ListTree.from_files(files)

        def fetch(url):
            self.asked.append(url)
            return answers.get(url, links.Answer(200))
        return links.main(["."] + list(extra), self.out, self.err, tree=tree, fetch=fetch, **seams)

    def test_a_broken_link_is_a_warning_with_its_place_and_the_run_still_exits_0(self):
        files = {"README.md": "# Hub\n\nSee [the docs](https://docs.acme.org/gone) and [home](https://docs.acme.org/).\n",
                 "docs/guide.md": "Look at https://docs.acme.org/gone and https://docs.acme.org/slow.\n"}
        answers = {"https://docs.acme.org/gone": links.Answer(404), "https://docs.acme.org/slow": links.Answer(0, "no answer: timed out")}
        code = self.run_stage(files, answers, "--format", "json")
        self.assertEqual(code, 0)
        found = json.loads(self.out.getvalue())
        self.assertEqual([(each["code"], each["severity"], each["path"], each["line"], each["message"]) for each in found], [
            ("LK01", "warning", "README.md", 3, "README.md:3: the link https://docs.acme.org/gone answers 404."),
            ("LK01", "warning", "docs/guide.md", 1, "docs/guide.md:1: the link https://docs.acme.org/gone answers 404."),
            ("LK01", "warning", "docs/guide.md", 1, "docs/guide.md:1: the link https://docs.acme.org/slow answers nothing (timed out).")])

    def test_each_address_is_asked_once_whatever_the_number_of_places(self):
        files = {"README.md": "https://docs.acme.org/a\nhttps://docs.acme.org/a\n", "docs/x.md": "https://docs.acme.org/a\n"}
        self.run_stage(files, {})
        self.assertEqual(self.asked, ["https://docs.acme.org/a"])

    def test_the_text_output_counts_what_was_asked(self):
        self.run_stage({"README.md": "https://docs.acme.org/a https://docs.acme.org/b\n"}, {"https://docs.acme.org/b": links.Answer(410)})
        text = self.out.getvalue()
        self.assertIn("Links: 2 addresses in 1 files, 1 that do not answer", text)
        self.assertIn("LK01 README.md:1: the link https://docs.acme.org/b answers 410.", text)

    def test_the_annotations_are_warnings_on_the_line_of_the_link(self):
        self.run_stage({"docs/a.md": "x\n\nhttps://docs.acme.org/gone\n"}, {"https://docs.acme.org/gone": links.Answer(404)},
                       "--format", "github")
        self.assertIn("::warning file=docs/a.md,line=3,title=LK01::docs/a.md:3: the link https://docs.acme.org/gone answers 404.",
                      self.out.getvalue())

    def test_a_catalog_whose_links_answer_says_all_good(self):
        self.run_stage({"README.md": "https://docs.acme.org/a\n"}, {})
        self.assertTrue(self.out.getvalue().endswith("All good\n"))

    def test_an_address_that_is_not_on_the_public_network_is_said_and_is_no_warning(self):
        answers = {"https://docs.acme.org/a": links.Answer(0, "the address is not on the public network", False)}
        self.assertEqual(self.run_stage({"README.md": "https://docs.acme.org/a\n"}, answers, "--format", "json"), 0)
        self.assertEqual(json.loads(self.out.getvalue()), [])

    def test_a_run_in_which_nothing_answers_did_not_run(self):
        files = {"README.md": "\n".join(f"https://docs.acme.org/{number}" for number in range(5)) + "\n"}
        answers = {f"https://docs.acme.org/{number}": links.Answer(0, "no answer: the name does not resolve") for number in range(5)}
        self.assertEqual(self.run_stage(files, answers), 2)
        self.assertIn("the network does not answer", self.err.getvalue())
        self.assertEqual(self.out.getvalue(), "")

    def test_two_that_do_not_answer_among_others_that_do_are_warnings(self):
        files = {"README.md": "\n".join(f"https://docs.acme.org/{number}" for number in range(5)) + "\n"}
        answers = {f"https://docs.acme.org/{number}": links.Answer(0, "no answer: timed out") for number in range(2)}
        self.assertEqual(self.run_stage(files, answers, "--format", "json"), 0)
        self.assertEqual(len(json.loads(self.out.getvalue())), 2)

    def test_the_run_ends_when_its_time_is_up_and_says_what_it_did_not_ask(self):
        files = {"README.md": "\n".join(f"https://docs.acme.org/{number}" for number in range(6)) + "\n"}
        ticks = iter(range(0, 10000, 400))
        self.assertEqual(self.run_stage(files, {}, clock=lambda: next(ticks), budget=1000), 0)
        text = self.out.getvalue()
        self.assertRegex(text, r"\d+ addresses were not asked: the ten minutes of the run were up")
        self.assertNotIn("All good", text)

    def test_at_most_a_bounded_number_of_addresses_is_asked(self):
        files = {f"docs/{name}.md": "\n".join(f"https://docs.acme.org/{name}/{number}" for number in range(400)) + "\n"
                 for name in "abcdefg"}
        self.run_stage(files, {})
        self.assertEqual(len(self.asked), links.MAX_LINKS)
        self.assertIn("addresses past the first 2000 were not asked", self.out.getvalue())

    def test_a_fetcher_that_breaks_is_that_addresses_answer_and_not_the_runs(self):
        def explode(url):
            if url.endswith("/bad"):
                raise RuntimeError("the fetcher broke")
            return links.Answer(200)
        tree = trees.ListTree.from_files({"README.md": "https://docs.acme.org/bad https://docs.acme.org/good\n"})
        code = links.main([".", "--format", "json"], self.out, self.err, tree=tree, fetch=explode)
        self.assertEqual(code, 0)
        self.assertEqual([each["message"] for each in json.loads(self.out.getvalue())],
                         ["README.md:1: the link https://docs.acme.org/bad answers nothing (RuntimeError)."])

    def test_what_a_hostile_file_holds_cannot_reach_the_output_as_a_command_or_a_line(self):
        evil = "https://docs.acme.org/%0A::error::x?a=%25"
        files = {"README.md": f"See {evil}\n", "docs/a%0A::error::x.md": f"{evil}2\n", "docs/b\nc.md": f"{evil}3\n"}
        answers = {evil: links.Answer(404), evil + "2": links.Answer(404), evil + "3": links.Answer(404)}
        self.run_stage(files, answers, "--format", "github")
        for line in self.out.getvalue().splitlines():
            self.assertTrue(line.startswith(("::warning ", "::notice ", "Links:", "Not checked", "1 ", "2 ", "3 ")) or not line.startswith("::"),
                            line)
        self.assertEqual(self.out.getvalue().count("\n::error"), 0)

    def test_the_summary_of_a_ci_run_lists_the_warnings(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            summary = os.path.join(folder, "summary.md")
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary}):
                self.run_stage({"README.md": "https://docs.acme.org/gone\n"}, {"https://docs.acme.org/gone": links.Answer(404)}, "--ci")
            with open(summary, encoding="utf-8") as handle:
                text = handle.read()
        self.assertIn("### The links of the guides answer", text)
        self.assertIn("| README.md:1 | LK01 |", text)

    def test_a_folder_that_is_not_one_is_a_usage_error(self):
        self.assertEqual(links.main(["/no/such/folder"], self.out, self.err), 2)
        self.assertIn("is not a folder", self.err.getvalue())

    def test_the_command_line_reaches_the_stage(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "README.md"), "w", encoding="utf-8") as handle:
                handle.write("# Nothing to ask\n\nSee [the guide](docs/guide.md) and `https://docs.acme.org/code`.\n")
            out = io.StringIO()
            self.assertEqual(ci_main(["links", folder], out, io.StringIO()), 0)
        self.assertIn("Links: 0 addresses", out.getvalue())
        self.assertEqual(ci.STAGES["links"], ("links", "main"))


if __name__ == "__main__":
    unittest.main()
