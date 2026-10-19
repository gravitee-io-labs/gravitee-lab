"""probe: how the headers of a UI are judged against the page that frames it, and what a UI answers (a real server on
a free port, no Docker)."""

import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lab_ui.docker import probe

from .. import Case

PAGE = "http://localhost:9000"
GAMMA = "http://localhost:8086"
LABS = "http://localhost:9000 http://127.0.0.1:9000"


def link(label, url, service, legacy=False):
    return {"label": label, "url": url, "port": None, "service": service, "section": "", "legacy": legacy}


class Judgement(Case):
    """Whether a page may show a UI, from the two headers that say it, as a browser reads them."""

    def can(self, csp=(), options=(), url=GAMMA, page=PAGE):
        return probe.can_frame(url, page, list(csp), list(options))[0]

    def test_without_a_header_anything_may_frame_it(self):
        self.assertTrue(self.can())

    def test_x_frame_options(self):
        cases = [("DENY", GAMMA, PAGE, False), ("deny", GAMMA, PAGE, False), (" Deny ", GAMMA, PAGE, False),
                 ("SAMEORIGIN", GAMMA, PAGE, False),          # another port is another origin
                 ("SAMEORIGIN", PAGE, PAGE, True), ("sameorigin", PAGE + "/x", PAGE, True),
                 ("ALLOW-FROM http://localhost:9000", GAMMA, PAGE, True),
                 ("ALLOW-FROM http://localhost:9000/some/path", GAMMA, PAGE, True),
                 ("ALLOW-FROM http://127.0.0.1:9000", GAMMA, PAGE, False),
                 ("whatever", GAMMA, PAGE, True)]             # what a browser does not know, it ignores
        for value, url, page, expected in cases:
            with self.subTest(value=value, url=url):
                self.assertEqual(self.can(options=[value], url=url, page=page), expected)

    def test_x_frame_options_that_disagree_refuse(self):
        self.assertFalse(self.can(options=["SAMEORIGIN, DENY"]))
        self.assertFalse(self.can(options=["ALLOW-FROM http://localhost:9000", "DENY"]))
        self.assertTrue(self.can(options=["ALLOW-FROM http://localhost:9000", "ALLOW-FROM http://localhost:9000"]))

    def test_frame_ancestors_none_and_empty_name_nobody(self):
        for policy in ("frame-ancestors 'none'", "frame-ancestors", "frame-ancestors ;", "default-src 'self'; frame-ancestors 'NONE'"):
            with self.subTest(policy=policy):
                self.assertFalse(self.can(csp=[policy]))

    def test_self_is_the_origin_of_the_framed_page(self):
        self.assertFalse(self.can(csp=["frame-ancestors 'self'"]))
        self.assertTrue(self.can(csp=["frame-ancestors 'self'"], url=PAGE + "/page"))
        self.assertTrue(self.can(csp=["frame-ancestors 'SELF'"], url=PAGE))
        self.assertFalse(self.can(csp=["frame-ancestors 'self'"], url="https://localhost:9000"))   # the scheme counts

    def test_host_lists(self):
        policy = f"default-src 'none'; frame-ancestors 'self' {LABS}; img-src 'self'"
        self.assertTrue(self.can(csp=[policy]))
        self.assertTrue(self.can(csp=[policy], page="http://127.0.0.1:9000"))
        self.assertFalse(self.can(csp=[policy], page="http://localhost:9001"))
        self.assertFalse(self.can(csp=[policy], page="http://[::1]:9000"))
        self.assertTrue(self.can(csp=["frame-ancestors http://[::1]:9000"], page="http://[::1]:9000"))

    def test_scheme_and_port_have_to_match(self):
        cases = [("http://localhost:9000", PAGE, True), ("https://localhost:9000", PAGE, False),
                 ("localhost:9000", PAGE, True),                 # no scheme: the framed page's own
                 ("localhost:9000", "https://localhost:9000", True),   # http is also allowed over https
                 ("https://localhost:9000", "http://localhost:9000", False),
                 ("http://localhost", PAGE, False),             # no port: the scheme's own, which is 80
                 ("http://localhost", "http://localhost", True), ("http://localhost:80", "http://localhost", True),
                 ("https://localhost", "https://localhost:443", True),
                 ("http://localhost:*", PAGE, True), ("http://localhost:9001", PAGE, False),
                 ("http://localhost:9000/some/path", PAGE, True)]   # a path is ignored
        for source, page, expected in cases:
            with self.subTest(source=source, page=page):
                self.assertEqual(self.can(csp=[f"frame-ancestors {source}"], page=page), expected)

    def test_wildcards(self):
        cases = [("*", PAGE, True), ("*", "https://example.com", True),
                 ("*.example.com", "https://app.example.com", True), ("*.example.com", "https://a.b.example.com", True),
                 ("*.example.com", "https://example.com", False), ("*.example.com", "https://badexample.com", False),
                 ("https://*.example.com", "https://app.example.com", True), ("https://*.example.com", "http://app.example.com", False),
                 ("*:*", PAGE, True), ("'unsafe-inline'", PAGE, False), ("nonsense words", PAGE, False),
                 ("http:", PAGE, True), ("https:", PAGE, False), ("https:", "https://example.com", True), ("http:", "https://example.com", True)]
        for source, page, expected in cases:
            with self.subTest(source=source, page=page):
                self.assertEqual(self.can(csp=[f"frame-ancestors {source}"], page=page), expected)

    def test_it_is_not_case_sensitive(self):
        self.assertTrue(self.can(csp=["Frame-Ancestors HTTP://LOCALHOST:9000"]))

    def test_frame_ancestors_replaces_x_frame_options(self):
        # Both headers, as the images sent them before and after: the policy decides
        self.assertFalse(self.can(csp=["frame-ancestors 'self'"], options=["SAMEORIGIN"]))
        self.assertTrue(self.can(csp=[f"frame-ancestors 'self' {LABS}"], options=["SAMEORIGIN"]))
        self.assertTrue(self.can(csp=[f"frame-ancestors {LABS}"], options=["DENY"]))
        self.assertFalse(self.can(csp=["frame-ancestors 'none'"], options=["ALLOW-FROM http://localhost:9000"]))

    def test_a_policy_without_frame_ancestors_leaves_it_to_x_frame_options(self):
        self.assertFalse(self.can(csp=["default-src 'self'"], options=["DENY"]))
        self.assertTrue(self.can(csp=["default-src 'self'; script-src 'none'"]))

    def test_every_policy_has_to_allow_the_page(self):
        self.assertFalse(self.can(csp=[f"frame-ancestors {LABS}", "frame-ancestors 'none'"]))
        self.assertFalse(self.can(csp=[f"frame-ancestors {LABS}, frame-ancestors 'self'"]))   # two policies in one header
        self.assertTrue(self.can(csp=[f"frame-ancestors {LABS}", f"frame-ancestors {PAGE}"]))
        self.assertFalse(self.can(csp=["frame-ancestors 'self'; frame-ancestors http://localhost:9000"]))   # the first one counts

    def test_what_is_no_address_is_never_framed(self):
        self.assertFalse(self.can(url="not an address"))
        self.assertFalse(self.can(url="javascript:alert(1)"))
        self.assertFalse(self.can(page="null"))

    def test_the_reason_names_what_refused(self):
        self.assertEqual(probe.can_frame(GAMMA, PAGE, ["frame-ancestors 'self'"], []), (False, "frame-ancestors 'self'"))
        self.assertEqual(probe.can_frame(GAMMA, PAGE, [], ["DENY"]), (False, "X-Frame-Options: DENY"))
        self.assertEqual(probe.can_frame(GAMMA, PAGE, ["frame-ancestors"], []), (False, "frame-ancestors none"))
        self.assertEqual(probe.can_frame(GAMMA, PAGE, [], []), (True, ""))


class Ui(BaseHTTPRequestHandler):
    """What a UI answers on each path: its headers, a redirect, an error, a delay."""

    ANSWERS = {
        "/open": (200, {}), "/deny": (200, {"X-Frame-Options": "DENY"}),
        "/policy": (200, {"Content-Security-Policy": f"default-src 'none'; frame-ancestors 'self' {LABS}"}),
        "/self": (200, {"Content-Security-Policy": "frame-ancestors 'self'", "X-Frame-Options": "SAMEORIGIN"}),
        "/proxy": (502, {}), "/down": (503, {}), "/missing": (404, {}),
        "/one": (302, {"Location": "/deny"}), "/two": (301, {"Location": "/one"}), "/three": (307, {"Location": "/two"}),
        "/away": (302, {"Location": "mailto:someone@example.test"}),
    }
    hits = []

    def do_GET(self):
        Ui.hits.append(self.path)
        if self.path == "/slow":
            time.sleep(1)
        status, headers = self.ANSWERS.get(self.path.split("?")[0], (200, {}))
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


class Probe(Case):

    def setUp(self):
        Ui.hits = []
        probe.KEPT.clear()
        self.addCleanup(probe.KEPT.clear)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Ui)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, args=(0.01,), daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.patch(probe, "TIMEOUT", 0.3)

    def look(self, path):
        return probe.look(self.base + path, PAGE)

    def test_a_ui_that_allows_the_page_is_framable(self):
        self.assertEqual(self.look("/open"), {"answers": True, "frameable": True, "reason": ""})
        self.assertEqual(self.look("/policy"), {"answers": True, "frameable": True, "reason": ""})

    def test_a_ui_that_does_not_is_not_framable_and_says_which_header(self):
        self.assertEqual(self.look("/deny"), {"answers": True, "frameable": False, "reason": "X-Frame-Options: DENY"})
        self.assertEqual(self.look("/self"), {"answers": True, "frameable": False, "reason": "frame-ancestors 'self'"})

    def test_the_page_that_asks_is_the_one_judged(self):
        self.assertTrue(probe.look(self.base + "/policy", "http://127.0.0.1:9000")["frameable"])
        self.assertFalse(probe.look(self.base + "/policy", "http://localhost:9001")["frameable"])

    def test_an_error_page_is_a_page_but_a_proxy_that_has_nothing_behind_it_is_not_a_ui_that_answers(self):
        self.assertTrue(self.look("/missing")["answers"])
        for path in ("/proxy", "/down"):
            self.assertEqual(self.look(path)["answers"], False, path)
            self.assertEqual(self.look(path)["frameable"], False, path)

    def test_a_ui_that_is_not_there_does_not_answer(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Ui)
        port = server.server_port
        server.server_close()
        found = probe.look(f"http://127.0.0.1:{port}/", PAGE)
        self.assertEqual((found["answers"], found["frameable"]), (False, False))
        self.assertTrue(found["reason"])

    def test_a_ui_that_is_too_slow_does_not_answer(self):
        self.assertEqual(self.look("/slow")["answers"], False)

    def test_a_couple_of_redirects_are_followed_and_the_last_answer_judges(self):
        self.assertEqual(self.look("/one")["reason"], "X-Frame-Options: DENY")
        self.assertEqual(self.look("/two")["reason"], "X-Frame-Options: DENY")
        self.assertEqual(Ui.hits, ["/one", "/deny", "/two", "/one", "/deny"])

    def test_a_ui_that_sends_the_page_further_than_two_redirects_does_not_answer(self):
        # Judged by the redirect itself it would be framable, and the browser would then refuse what is at the end
        found = self.look("/three")
        self.assertEqual((found["answers"], found["frameable"], found["reason"]), (False, False, "Too many redirects"))
        self.assertEqual(Ui.hits, ["/three", "/two", "/one"])

    def test_a_redirect_that_leaves_for_what_is_no_web_page_does_not_answer(self):
        self.assertEqual(self.look("/away")["answers"], False)

    def test_a_redirect_to_another_scheme_is_never_followed(self):
        # The address answers nothing, but a request to it would show: nothing is sent to what is not http
        port = self.server.server_port
        self.patch(Ui, "ANSWERS", {**Ui.ANSWERS, "/ftp": (302, {"Location": f"ftp://127.0.0.1:{port}/hit"}),
                                   "/gopher": (302, {"Location": f"gopher://127.0.0.1:{port}/hit"})})
        for path in ("/ftp", "/gopher"):
            self.assertEqual(self.look(path)["answers"], False, path)
        self.assertEqual(Ui.hits, ["/ftp", "/gopher"])

    def test_a_redirect_to_an_address_that_is_not_one_does_not_answer(self):
        self.patch(Ui, "ANSWERS", {**Ui.ANSWERS, "/port": (302, {"Location": "http://127.0.0.1:99999/"}),
                                   "/bracket": (302, {"Location": "http://[::1/"})})
        for path in ("/port", "/bracket"):
            found = self.look(path)
            self.assertEqual((found["answers"], found["frameable"]), (False, False), path)
            self.assertTrue(found["reason"])

    def test_what_is_not_a_web_address_is_not_asked(self):
        self.assertEqual(probe.look("file:///etc/passwd", PAGE)["answers"], False)
        self.assertEqual(probe.look("ftp://localhost/", PAGE)["answers"], False)


class Kept(Probe):
    """The look of a tab, which the page asks for again and again while it shows a notice."""

    def project(self, path="/deny"):
        url = self.base + path
        return {"links": [link("Console", url, "ui")], "addons": {}, "services": {"ui": {}},
                "playgrounds": {"p": {"services": ["ui"], "addons": [], "consoles": ["ui"]}}}, url

    def test_it_is_asked_once_in_a_few_seconds(self):
        p, url = self.project()
        first = probe.probe(p, "p", url, PAGE)
        self.assertEqual(probe.probe(p, "p", url, PAGE), first)
        self.assertEqual(Ui.hits, ["/deny"])

    def test_it_is_asked_again_once_kept_long_enough(self):
        p, url = self.project()
        clock = self.patch(probe.time, "monotonic", return_value=100.0)
        probe.probe(p, "p", url, PAGE)
        clock.return_value = 100.0 + probe.TTL - 0.1
        probe.probe(p, "p", url, PAGE)
        self.assertEqual(Ui.hits, ["/deny"])
        clock.return_value = 100.0 + probe.TTL
        probe.probe(p, "p", url, PAGE)
        self.assertEqual(Ui.hits, ["/deny", "/deny"])
        self.assertEqual(len(probe.KEPT), 1)

    def test_each_page_has_its_own_look(self):
        p, url = self.project("/policy")
        self.assertTrue(probe.probe(p, "p", url, "http://localhost:9000")["frameable"])
        self.assertFalse(probe.probe(p, "p", url, "http://localhost:9999")["frameable"])

    def test_an_address_the_playground_does_not_declare_is_never_asked(self):
        p, url = self.project()
        for asked in (self.base + "/open", self.base, "http://127.0.0.1:1/", None):
            with self.assertRaisesRegex(ValueError, "Not a UI of this playground"):
                probe.probe(p, "p", asked, PAGE)
        with self.assertRaises(ValueError):
            probe.probe(p, "gone", url, PAGE)
        self.assertEqual(Ui.hits, [])


if __name__ == "__main__":
    unittest.main()
