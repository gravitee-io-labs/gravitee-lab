"""The relay of a persona's tab (lab_ui/server/personas.py): the Lab UI's handler on a free port, in front of a fake Management
API on another, which says what it was sent. Each persona's cookies come back under its own name and path, and only its own go
to the API."""

import http.client
import json
import socketserver
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from lab_ui.server import api, personas


class FakeApi(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.dumps({"method": self.command, "path": self.path, "cookie": self.headers.get("Cookie"),
                           "host": self.headers.get("Host"), "body": self.rfile.read(length).decode()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", "Auth-Graviteeio-APIM=token-1; Path=/; Max-Age=60; HttpOnly")
        self.send_header("Set-Cookie", "XSRF-TOKEN=x; Path=/management; Domain=localhost")
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin") or "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self):
        # A redirect of the API names it by the address it is told to have, or from its root
        self.send_response(302)
        self.send_header("Location", "http://localhost:8083/management/next" if self.path == "/away" else "/management/next")
        self.send_header("Content-Security-Policy", "default-src *")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        (self.redirect if self.path in ("/away", "/root") else self.reply)()

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = reply


class NotHttp(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.recv(65536)
        self.request.sendall(b"this is no answer of HTTP\r\n\r\n")


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Cookies(unittest.TestCase):
    def test_only_the_personas_own_cookies_go_to_the_api_under_their_own_names(self):
        header = ("Auth-Graviteeio-APIM=admin; LabPersona_anouk_Auth-Graviteeio-APIM=hers; LabPersona_bob_Auth-Graviteeio-APIM=his; "
                  "LabPersona_anouk_XSRF-TOKEN=x; LabPersona_anouk_=empty")
        self.assertEqual(personas.request_cookies("anouk", header), "Auth-Graviteeio-APIM=hers; XSRF-TOKEN=x")
        self.assertEqual(personas.request_cookies("anouk", "Auth-Graviteeio-APIM=admin"), "")
        self.assertEqual(personas.request_cookies("anouk", None), "")

    def test_a_cookie_the_api_sets_is_kept_under_the_personas_name_and_path(self):
        self.assertEqual(personas.response_cookie("anouk", "Auth-Graviteeio-APIM=t; Path=/; Max-Age=60; HttpOnly"),
                         "LabPersona_anouk_Auth-Graviteeio-APIM=t; Path=/persona/anouk/; Max-Age=60; HttpOnly")
        self.assertEqual(personas.response_cookie("anouk", "XSRF-TOKEN=x; Domain=localhost"),
                         "LabPersona_anouk_XSRF-TOKEN=x; Path=/persona/anouk/")

    def test_only_a_page_of_this_machine_may_ask(self):
        self.assertTrue(personas.allowed({"Host": "localhost:9000", "Origin": "http://localhost:8085"}))
        self.assertTrue(personas.allowed({"Host": "127.0.0.1:9000"}))
        self.assertFalse(personas.allowed({"Host": "localhost:9000", "Origin": "https://evil.example"}))
        self.assertFalse(personas.allowed({"Host": "evil.example:9000", "Origin": "http://localhost:8085"}))


class Relay(unittest.TestCase):
    def setUp(self):
        self.api = serve(FakeApi)
        self.lab = serve(api.Handler)
        for server in (self.api, self.lab):
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        patch = mock.patch.object(personas, "API", ("127.0.0.1", self.api.server_address[1]))
        patch.start()
        self.addCleanup(patch.stop)

    def ask(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.lab.server_address[1], timeout=5)
        connection.request(method, path, body=body, headers={"Host": "localhost:9000", **(headers or {})})
        response = connection.getresponse()
        answer = (response.status, response.getheaders(), response.read())
        connection.close()
        return answer

    def test_what_a_personas_tab_asks_goes_to_the_api_with_its_cookies_and_comes_back_with_them(self):
        cookie = "Auth-Graviteeio-APIM=admin; LabPersona_anouk_Auth-Graviteeio-APIM=hers"
        status, headers, body = self.ask("POST", "/persona/anouk/portal/environments/DEFAULT/auth/login?x=1", b"{}",
                                         {"Cookie": cookie, "Origin": "http://localhost:8085", "Content-Type": "application/json"})
        self.assertEqual(status, 200)
        sent = json.loads(body)
        self.assertEqual((sent["method"], sent["path"], sent["cookie"], sent["body"]),
                         ("POST", "/portal/environments/DEFAULT/auth/login?x=1", "Auth-Graviteeio-APIM=hers", "{}"))
        cookies = [value for key, value in headers if key.lower() == "set-cookie"]
        self.assertEqual(cookies, ["LabPersona_anouk_Auth-Graviteeio-APIM=token-1; Path=/persona/anouk/; Max-Age=60; HttpOnly",
                                   "LabPersona_anouk_XSRF-TOKEN=x; Path=/persona/anouk/"])
        self.assertIn(("Access-Control-Allow-Origin", "http://localhost:8085"), headers)

    def test_every_method_a_ui_uses_is_relayed_and_none_other_is_answered_outside_the_relay(self):
        for method in ("GET", "PUT", "DELETE", "PATCH", "OPTIONS"):
            with self.subTest(method=method):
                status, _, body = self.ask(method, "/persona/anouk/management/organizations/DEFAULT/user")
                self.assertEqual((status, json.loads(body)["method"]), (200, method))
        self.assertEqual(self.ask("PUT", "/api/state")[0], 405)

    def test_a_page_of_another_site_or_a_persona_that_is_no_name_is_refused(self):
        self.assertEqual(self.ask("GET", "/persona/anouk/x", headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.ask("GET", "/persona/Bad_Name/x")[0], 404)
        self.assertEqual(self.ask("GET", "/persona/anouk")[0], 404)

    def test_a_redirect_of_the_api_stays_the_personas(self):
        # Followed straight to localhost:8083, it would carry the sign-in everybody shares, not the persona's
        for path in ("/away", "/root"):
            with self.subTest(path=path):
                status, headers, _ = self.ask("GET", f"/persona/anouk{path}")
                self.assertEqual((status, dict(headers)["Location"]), (302, "/persona/anouk/management/next"))
        self.assertEqual(personas.location("anouk", "https://elsewhere.example/x"), "https://elsewhere.example/x")
        self.assertEqual(personas.location("anouk", "//elsewhere.example/x"), "//elsewhere.example/x")

    def test_an_answer_is_never_a_page_of_this_origin(self):
        # Every answer is read by the UI's scripts: one opened as a page of localhost:9000 runs nothing
        for path in ("/persona/anouk/management/x", "/persona/anouk/away"):
            with self.subTest(path=path):
                headers = dict(self.ask("GET", path)[1])
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(headers["Content-Security-Policy"], "default-src 'none'; frame-ancestors 'none'; sandbox")

    def test_a_body_whose_length_is_no_number_of_bytes_or_too_large_is_refused(self):
        for size in ("-1", "abc", "1.5"):
            with self.subTest(size=size):
                self.assertEqual(self.ask("POST", "/persona/anouk/x", headers={"Content-Length": size})[0], 400)
        with mock.patch.object(personas, "BODY_LIMIT", 10):
            self.assertEqual(self.ask("POST", "/persona/anouk/x", headers={"Content-Length": "11"})[0], 413)
            self.assertEqual(self.ask("POST", "/persona/anouk/x", b"x" * 10)[0], 200)

    def test_an_api_that_answers_no_http_is_a_502(self):
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), NotHttp)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with mock.patch.object(personas, "API", ("127.0.0.1", server.server_address[1])):
            self.assertEqual(self.ask("GET", "/persona/anouk/x")[0], 502)
