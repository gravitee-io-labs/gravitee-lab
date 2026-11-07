"""cloud: the claims of a token, the client of the accounts and of Cloud Gate against the fake Cloud on a free port, and
against servers that answer wrongly. No network. No answer or message holds a token, and a test never prints one: it
asks whether it is there, not what it is."""

import base64
import json
import socket
import ssl
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lab_ui.gravitee import cloud

from .. import Case, fake_cloud

SECRET = "never-in-a-message-1234"
SECRETS = (SECRET, *fake_cloud.SECRETS)   # every token the tests use, apart from those a gateway is created with
ACCOUNT = fake_cloud.ACCOUNT["id"]
ORGANIZATION, DEVELOPMENT, TEST = "fake-organization", "fake-env-dev", "fake-env-test"


def serving(server):
    """A server running in a thread until the test ends: answers it."""
    threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
    return server


class Scripted(ThreadingHTTPServer):
    """A server that answers what `answer(method, path, headers, body)` says, (status, body, headers), and keeps
    what it was asked: (method, path, headers, body)."""

    daemon_threads = True

    def __init__(self, answer):
        super().__init__(("127.0.0.1", 0), self.handler())
        self.answer, self.asked = answer, []

    @staticmethod
    def handler():
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def serve(self):
                length = self.headers.get("Content-Length", "")
                body = self.rfile.read(int(length)) if length.isdigit() else b""
                self.server.asked.append((self.command, self.path, dict(self.headers), body))
                status, text, headers = self.server.answer(self.command, self.path, self.headers, body)
                data = text.encode() if isinstance(text, str) else text
                self.send_response(status)
                for key, value in {"Content-Length": len(data), **(headers or {})}.items():
                    self.send_header(key, str(value))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass   # the client read as much as it wanted and hung up: an answer larger than it accepts

            do_GET = do_POST = do_DELETE = serve
        return Handler


class Client(Case):
    """A client of a server, and what it needs to be asked."""

    def served(self, server):
        serving(server)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def scripted(self, answer):
        server = Scripted(answer)
        return self.served(server), server

    @staticmethod
    def account(address, token=SECRET, account="acc"):
        """A call that asks the Cloud at this address for an account."""
        return lambda: cloud.Cloud(address, token).account(account)

    @staticmethod
    def reads(address, token=fake_cloud.CLOUD_TOKEN):
        """A call that asks Cloud Gate at this address for the first API of an environment."""
        return lambda: cloud.Gate(address, token).read(ORGANIZATION, DEVELOPMENT)

    def assertHidden(self, *things, more=()):
        """No token (those of the tests, and `more`) is in these answers or messages. It does not say which one is."""
        text = json.dumps(things, default=str)
        self.assertFalse(any(secret in text for secret in (*SECRETS, *more)), "a token is in it")

    def raises(self, kind, words, call):
        """The error a call fails with: its kind, its words, and no token in it."""
        with self.assertRaises(cloud.CloudError) as caught:
            call()
        message = str(caught.exception)
        self.assertEqual(caught.exception.kind, kind, message)
        self.assertIn(words, message)
        self.assertHidden(message)
        return caught.exception


class Address(Case):
    def test_the_address_is_normalised(self):
        for given, base in [("https://eu.cloud.gravitee.io", "https://eu.cloud.gravitee.io"),
                            (" https://eu.cloud.gravitee.io/ ", "https://eu.cloud.gravitee.io"),
                            ("https://eu.cloud.gravitee.io/management", "https://eu.cloud.gravitee.io"),
                            ("https://eu.cloud.gravitee.io/management/", "https://eu.cloud.gravitee.io"),
                            ("http://localhost:9990///", "http://localhost:9990"),
                            ("http://[::1]:9990", "http://[::1]:9990"),
                            ("https://proxy.example/cloud/management", "https://proxy.example/cloud")]:
            with self.subTest(given=given):
                self.assertEqual(cloud.base(given), base)

    def test_what_is_no_address_of_a_cloud_is_refused(self):
        for given in ("", "   ", None, 42, "eu.cloud.gravitee.io", "ftp://eu.cloud.gravitee.io", "file:///etc/passwd",
                      "https://", "https://user:pass@eu.cloud.gravitee.io", "https://eu.cloud.gravitee.io?x=1",
                      "https://eu.cloud.gravitee.io#top", "http://localhost:port", "http://localhost:99999"):
            with self.subTest(given=given), self.assertRaisesRegex(ValueError, "must be http"):
                cloud.base(given)

    def test_the_token_goes_in_clear_only_to_this_machine(self):
        for given in ("http://eu.cloud.gravitee.io", "http://192.168.1.20:9990"):
            with self.subTest(given=given), self.assertRaisesRegex(ValueError, "must be https"):
                cloud.base(given)

    def test_the_address_in_the_error_is_not_repeated(self):
        with self.assertRaises(ValueError) as caught:
            cloud.base("https://user:hunter2@eu.cloud.gravitee.io")
        self.assertNotIn("hunter2", str(caught.exception))


class Token(Case):
    def test_a_token_that_cannot_be_sent_is_refused_without_being_repeated(self):
        for token in ("", "   ", None, 7, f"two words {SECRET}", f"line\n{SECRET}", f"café{SECRET}"):
            with self.subTest(token=bool(token)), self.assertRaises(ValueError) as caught:
                cloud.Cloud("https://eu.cloud.gravitee.io", token)
            self.assertFalse(SECRET in str(caught.exception), "the token is in the message")

    def test_it_is_not_in_the_representation(self):
        client = cloud.Cloud("https://eu.cloud.gravitee.io/management/", f"  {SECRET}\n")
        self.assertFalse(SECRET in repr(client), "the token is in it")


class Claims(Case):
    """What a token says about itself, read and not checked."""

    def test_a_cloud_token_names_its_organization_environment_and_expiry(self):
        found = cloud.claims(fake_cloud.CLOUD_TOKEN)
        self.assertEqual({key: found[key] for key in ("kind", "organization", "environment", "gateway")},
                         {"kind": "cloud", "organization": ORGANIZATION, "environment": DEVELOPMENT, "gateway": None})
        self.assertTrue(isinstance(found["expiry"], int) and found["expiry"] > time.time(), "not a future expiry")
        self.assertEqual(set(found), {"kind", "organization", "environment", "expiry", "gateway", "cpg"})
        self.assertEqual(cloud.claims(fake_cloud.CLOUD_TOKEN_TEST)["environment"], TEST)
        self.assertEqual(cloud.claims(fake_cloud.SECOND_CLOUD_TOKEN)["organization"], "fake-second-organization")

    def test_an_expired_token_says_so_by_its_expiry_only(self):
        found = cloud.claims(fake_cloud.CLOUD_TOKEN_EXPIRED)
        self.assertEqual(found["kind"], "cloud")
        self.assertLess(found["expiry"], time.time())

    def test_the_token_of_a_gateway_names_the_gateway(self):
        found = cloud.claims(fake_cloud.GATEWAY_TOKEN)
        self.assertEqual((found["kind"], found["gateway"], found["environment"], found["organization"]),
                         ("gateway", "fake-gateway", DEVELOPMENT, ORGANIZATION))

    def test_the_region_names_where_cloud_gate_is_and_nothing_else_than_a_label_does(self):
        self.assertEqual(cloud.claims(fake_cloud.CLOUD_TOKEN)["cpg"], "eu")
        self.assertEqual(cloud.claims(fake_cloud.GATEWAY_TOKEN)["cpg"], "eu")
        self.assertIsNone(cloud.claims(fake_cloud.ACCOUNT_JWT)["cpg"])   # it says none
        # It becomes a part of the address a Cloud Token is sent to: no claim can name another host
        for region in ("us", "eu-west-1", "a", "e2", "x" * 32):
            with self.subTest(region=region):
                self.assertEqual(cloud.claims(fake_cloud.jwt({"scopes": ["automation"], "cpg": region}))["cpg"], region)
        for region in ("", "EU", "-eu", "eu-", "e u", "eu\n", "eu.evil.example", "evil.example/", "eu/x", "eu#", "eu@x",
                       "eu:443", "é", "x" * 33, 5, None, ["eu"], {"eu": 1}):
            with self.subTest(region=region):
                self.assertIsNone(cloud.claims(fake_cloud.jwt({"scopes": ["automation"], "cpg": region}))["cpg"])

    def test_a_jwt_that_is_neither_a_cloud_token_nor_a_gateway_s_is_unknown(self):
        found = cloud.claims(fake_cloud.ACCOUNT_JWT)
        self.assertEqual((found["kind"], found["organization"], found["environment"], found["gateway"]),
                         ("unknown", None, None, None))
        self.assertIsInstance(found["expiry"], int)

    def test_what_is_no_jwt_is_unknown(self):
        unknown = {"kind": "unknown", "organization": None, "environment": None, "expiry": None, "gateway": None,
                   "cpg": None}
        for token in ("", None, 42, [], fake_cloud.TOKEN, "a.b", "a.b.c.d", "..", "a..c", "e30..", "é.é.é",
                      f"x.{SECRET}.y", "x.e30=.y", "x.W10.y", "x.IjEi.y", "x.NDI.y", "x.bnVsbA.y", "x.\x00.y",
                      "x." + "W" * 40 + ".y", "x.gA.y"):   # a payload that is not an object, or not JSON, or not UTF-8
            with self.subTest(token=token):
                self.assertEqual(cloud.claims(token), unknown)

    def test_a_deep_payload_is_unknown_not_an_error(self):
        deep = base64.urlsafe_b64encode(b"[" * 200000).decode()
        self.assertEqual(cloud.claims(f"x.{deep}.y")["kind"], "unknown")

    def test_a_claim_of_the_wrong_type_is_not_one(self):
        def of(**claims):
            return cloud.claims(fake_cloud.jwt({"scopes": ["automation"], **claims}))
        self.assertEqual(of(org=5, envs="dev", exp="soon")["organization"], None)
        for envs in ("dev", [], [5], ["a", "b"], [""], [["a"]], {"a": 1}):
            with self.subTest(envs=envs):
                self.assertIsNone(of(envs=envs)["environment"])
        for exp in ("soon", True, 1.5, None, [1], float("inf")):
            with self.subTest(exp=exp):
                self.assertIsNone(of(exp=exp)["expiry"])
        for meta in ("g", [], {"GATEWAYID": 5}, {"GATEWAYID": ""}, {}):
            with self.subTest(meta=meta):
                self.assertEqual(of(meta=meta)["kind"], "cloud")
        self.assertEqual(of(scopes="automation")["kind"], "unknown")   # a scope list that is none
        self.assertEqual(of(scopes=[["automation"], {"sync": 1}, None])["kind"], "unknown")
        self.assertEqual(of(scopes=["rest"])["kind"], "cloud")
        self.assertEqual(of(scopes=["sync", "automation"])["kind"], "gateway")
        self.assertEqual(of(meta={"GATEWAYID": "g"})["kind"], "gateway")   # the gateway it names makes it one

    def test_the_header_and_the_signature_are_not_looked_at(self):
        payload = fake_cloud.cloud_token(DEVELOPMENT).split(".")[1]
        for token in (f"x.{payload}.y", f"x.{payload}.", f"x.{payload}.{SECRET}"):
            with self.subTest(token=bool(token)):
                self.assertEqual(cloud.claims(token)["environment"], DEVELOPMENT)

    def test_nothing_else_of_the_token_is_answered(self):
        token = fake_cloud.jwt({"org": "o", "envs": ["e"], "scopes": ["automation"], "secret": SECRET,
                                "meta": {"GATEWAYID": "g", "other": SECRET}, "sub": SECRET})
        self.assertFalse(SECRET in json.dumps(cloud.claims(token)), "a claim that was not asked is in it")
        self.assertFalse(token in json.dumps(cloud.claims(token)), "the token is in it")


class Fake(Client):
    """The client against the fake Cloud."""

    def setUp(self):
        self.fake = fake_cloud.serve(connect_after=0)
        self.url = self.served(self.fake)
        self.client = cloud.Cloud(self.url, fake_cloud.TOKEN)

    def test_the_account_its_environments_and_gateways(self):
        found = self.client.account(ACCOUNT)
        self.assertEqual((found["id"], found["hrid"], found["name"]), (ACCOUNT, "my-account", "My Account"))
        self.assertEqual([e["name"] for e in self.client.environments(ACCOUNT)],
                         ["Development", "Test", "Production"])
        self.assertEqual(self.client.gateways(ACCOUNT), [])

    def test_a_hybrid_gateway_is_created_listed_and_deleted(self):
        gateway, token = self.client.create_gateway(ACCOUNT, "fake-env-dev", "lab", ["localhost:8090"])
        self.assertEqual((gateway["name"], gateway["type"], gateway["scope"], gateway["environmentId"]),
                         ("lab", "HYBRID", "APIM", "fake-env-dev"))
        self.assertTrue(isinstance(token, str) and token, "no token")
        self.assertEqual(self.fake.state.gateways[gateway["id"]]["domains"][0]["domain"], "localhost:8090")
        listed = self.client.gateways(ACCOUNT)
        self.assertEqual([(g["id"], g["connectionStatus"]) for g in listed], [(gateway["id"], "CONNECTED")])
        self.assertIsNone(self.client.delete_gateway(ACCOUNT, gateway["id"]))   # an empty answer
        self.assertEqual(self.client.gateways(ACCOUNT), [])

    def test_the_token_goes_in_the_authorization_header(self):
        address, server = self.scripted(lambda *asked: (200, '{"id": "acc"}', None))
        self.assertEqual(self.account(address)(), {"id": "acc"})
        method, path, headers, _ = server.asked[0]
        self.assertEqual((method, path, headers["Authorization"]), ("GET", "/management/accounts/acc",
                                                                    f"Bearer {SECRET}"))
        self.assertEqual(headers["Accept"], "application/json")

    def test_the_status_of_a_refusal_is_known(self):
        for code, kind, words in [(401, "invalid", "refuses this token"), (403, "invalid", "not allowed"), (404, "invalid", "Not found"),
                                  (400, "invalid", "refuses it"), (500, "unreachable", "answered an error (HTTP 500)"),
                                  (302, "unreachable", "another address")]:
            address, _ = self.scripted(lambda *asked, code=code: (code, "{}", None))
            with self.subTest(code=code):
                self.assertEqual(self.raises(kind, words, self.account(address)).status, code)

    def test_what_the_cloud_refuses_it_says(self):
        self.raises("invalid", "refuses it: Bad request: name must have 2 to 128 characters",
                    lambda: self.client.create_gateway(ACCOUNT, "fake-env-dev", "x", []))
        self.raises("invalid", "refuses it: Bad request: environmentId",
                    lambda: self.client.create_gateway(ACCOUNT, "fake-env-nope", "lab", []))

    def test_an_id_is_one_segment_of_the_path(self):
        self.raises("invalid", "Not found", lambda: self.client.delete_gateway(ACCOUNT, "../accounts"))
        # One segment that is no account: refused as one, not walked into the account's own path
        self.raises("invalid", "refuses this token", lambda: self.client.environments(f"{ACCOUNT}/../x"))
        self.raises("invalid", "refuses this token", lambda: self.client.account(f"{ACCOUNT}/gateways"))
        for bad in ("", ".", "..", None, 5):   # these would walk out of the path, or are no id: nothing is sent
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "Not an id"):
                self.client.delete_gateway(ACCOUNT, bad)
            with self.subTest(account=bad), self.assertRaisesRegex(ValueError, "Not an id"):
                self.client.gateways(bad)
            with self.subTest(asked=bad), self.assertRaisesRegex(ValueError, "Not an id"):
                self.client.account(bad)
        self.assertNotIn("", [path for _, path in self.fake.state.asked])


class GateFake(Client):
    """Cloud Gate of the fake Cloud, and the client that reads it with a Cloud Token."""

    def setUp(self):
        self.fake = fake_cloud.serve(connect_after=0)
        self.url = self.served(self.fake)
        self.read = f"/apim/rest/v2/organizations/{ORGANIZATION}/environments/{DEVELOPMENT}/apis"

    def test_the_read_is_the_first_api_of_the_environment_with_the_token_as_bearer(self):
        address, server = self.scripted(lambda *asked: (200, '{"data": [], "pagination": {}}', None))
        self.reads(address)()
        method, path, headers, _ = server.asked[0]
        self.assertEqual((method, path), ("GET", self.read + "?perPage=1"))
        self.assertEqual(headers["Authorization"], f"Bearer {fake_cloud.CLOUD_TOKEN}")
        self.assertEqual(headers["Accept"], "application/json")

    def test_a_revoked_token_is_found_out_by_the_read(self):
        token = fake_cloud.cloud_token(DEVELOPMENT)
        self.assertIsNone(self.reads(self.url, token)())
        self.fake.state.revoked.add(token)
        error = self.raises("invalid", "Gravitee Cloud refuses this token", self.reads(self.url, token))
        self.assertEqual(error.status, 401)
        self.assertIsNone(self.reads(self.url)())   # not the other tokens of the environment

    def test_a_forbidden_read_is_a_token_that_cannot_read_api_management(self):
        address, _ = self.scripted(lambda *asked: (403, '{"message": "Forbidden"}', None))
        error = self.raises("invalid", "This Cloud Token cannot read API Management", self.reads(address))
        self.assertEqual(error.status, 403)

    def test_an_id_is_one_segment_of_the_path(self):
        address, server = self.scripted(lambda *asked: (200, "{}", None))
        gate = cloud.Gate(address, fake_cloud.CLOUD_TOKEN)
        gate.read("a/b", "c d")
        self.assertEqual(server.asked[0][1], "/apim/rest/v2/organizations/a%2Fb/environments/c%20d/apis?perPage=1")
        for bad in ("", ".", "..", None, 5):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "Not an id"):
                gate.read(bad, "env")
            with self.subTest(env=bad), self.assertRaisesRegex(ValueError, "Not an id"):
                gate.read("org", bad)
        self.assertEqual(len(server.asked), 1)   # nothing else was sent


class Wrong(Client):
    """Servers that are no Cloud, or a Cloud that fails."""

    def test_a_cloud_that_is_not_there(self):
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0))
            port = free.getsockname()[1]
        self.raises("unreachable", "refuses the connection", self.account(f"http://127.0.0.1:{port}"))

    def test_a_host_name_that_is_none(self):
        self.raises("unreachable", "Cannot reach a..b", self.account("https://a..b"))

    def test_a_cloud_that_is_too_slow(self):
        def slow(*asked):
            time.sleep(1)
            return 200, "[]", None
        self.patch(cloud, "TIMEOUT", 0.2)
        address, _ = self.scripted(slow)
        self.raises("unreachable", "does not answer in time", self.account(address))

    def test_a_web_page_is_not_a_cloud(self):
        address, _ = self.scripted(lambda *asked: (200, "<html><body>Welcome</body></html>", None))
        self.raises("unreachable", "does not answer like Gravitee Cloud", self.account(address))

    def test_json_of_another_shape_is_not_an_account(self):
        for text in ('{"accounts": []}', "{}", '{"id": 5}', "[]", "[1, 2]", '[{"id": "acc"}]', '"text"', "null",
                     b"\xff\xfe"):
            address, _ = self.scripted(lambda *asked, text=text: (200, text, None))
            with self.subTest(text=text):
                self.raises("unreachable", "does not answer like Gravitee Cloud", self.account(address))

    def test_an_account_may_say_more_or_less_than_expected(self):
        for text, expected in [('{"id": "acc"}', {"id": "acc"}),
                               ('{"id": "acc", "hrid": null, "name": "N", "more": [1]}',
                                {"id": "acc", "hrid": None, "name": "N", "more": [1]})]:
            address, _ = self.scripted(lambda *asked, text=text: (200, text, None))
            with self.subTest(text=text):
                self.assertEqual(self.account(address)(), expected)

    def test_environments_may_lack_what_the_cloud_does_not_give(self):
        address, _ = self.scripted(lambda *asked: (200, '[{"id": "e1", "name": "Dev"}, {"id": "e2"}]', None))
        self.assertEqual(cloud.Cloud(address, SECRET).environments("acc"), [{"id": "e1", "name": "Dev"}, {"id": "e2"}])
        for text in ('{"id": "e1"}', "[1]", "null"):
            address, _ = self.scripted(lambda *asked, text=text: (200, text, None))
            with self.subTest(text=text):
                self.raises("unreachable", "does not answer like Gravitee Cloud",
                            lambda: cloud.Cloud(address, SECRET).environments("acc"))

    def test_an_answer_that_is_too_large(self):
        self.patch(cloud, "MAX_ANSWER", 10)
        address, _ = self.scripted(lambda *asked: (200, '{"id": "acc", "description": "' + "x" * 20 + '"}', None))
        self.raises("unreachable", "does not answer like Gravitee Cloud", self.account(address))

    def test_a_redirect_is_not_followed_so_the_token_stays_here(self):
        elsewhere, other = self.scripted(lambda *asked: (200, "{}", None))
        address, _ = self.scripted(lambda *asked: (302, "", {"Location": f"{elsewhere}/management/accounts/acc"}))
        self.raises("unreachable", "to another address", self.account(address))
        self.assertEqual(other.asked, [])

    def test_what_the_cloud_says_never_carries_the_token(self):
        address, _ = self.scripted(lambda *asked: (400, f'{{"message": "bad {SECRET} here\\nsecond line"}}', None))
        with self.assertRaises(cloud.CloudError) as caught:
            cloud.Cloud(address, SECRET).create_gateway("a", "e", "lab", [])
        self.assertEqual(str(caught.exception), "Gravitee Cloud refuses it: bad … here second line")
        for body in ("not json", "[1]", '{"message": 5}'):
            address, _ = self.scripted(lambda *asked, body=body: (400, body, None))
            with self.subTest(body=body):
                self.raises("invalid", "Gravitee Cloud refuses it", self.account(address))

    def test_what_it_says_is_short(self):
        address, _ = self.scripted(lambda *asked: (400, '{"message": "' + "long " * 100 + '"}', None))
        with self.assertRaises(cloud.CloudError) as caught:
            self.account(address)()
        self.assertLess(len(str(caught.exception)), 260)

    def test_a_gateway_answered_without_a_token_is_removed_again(self):
        def answer(method, path, headers, body):
            if method == "POST":
                return 200, '{"gateway": {"id": "g1", "name": "lab"}}', None
            return 200, "", None
        address, server = self.scripted(answer)
        self.raises("unreachable", "gave no token", lambda: cloud.Cloud(address, SECRET).create_gateway(
            "acc", "env", "lab", []))
        self.assertEqual([(method, path) for method, path, _, _ in server.asked],
                         [("POST", "/management/accounts/acc/gateways"),
                          ("DELETE", "/management/accounts/acc/gateways/g1")])

    def test_an_answer_that_is_no_gateway(self):
        for text in ('{"token": "t"}', '{"gateway": "g1", "token": "t"}', '{"gateway": {}, "token": "t"}', "[]"):
            address, server = self.scripted(lambda *asked, text=text: (200, text, None))
            with self.subTest(text=text):
                self.raises("unreachable", "does not answer like Gravitee Cloud",
                            lambda: cloud.Cloud(address, SECRET).create_gateway("acc", "env", "lab", []))
                self.assertEqual(len(server.asked), 1)

    def test_the_gate_says_no_in_its_own_words(self):
        for code, kind, words in [(401, "invalid", "refuses this token"),
                                  (403, "invalid", "cannot read API Management"), (404, "invalid", "Not found"),
                                  (500, "unreachable", "answered an error (HTTP 500)")]:
            address, _ = self.scripted(lambda *asked, code=code: (code, "{}", None))
            with self.subTest(code=code):
                self.raises(kind, words, self.reads(address))
        for text in ("<html><body>Welcome</body></html>", "[]", "null", "", "[1]", '"text"'):
            address, _ = self.scripted(lambda *asked, text=text: (200, text, None))
            with self.subTest(text=text):
                self.raises("unreachable", "does not answer like Gravitee Cloud", self.reads(address))


class Reasons(Case):
    def test_why_nothing_answered_in_a_few_words(self):
        for reason, said in [(socket.timeout("timed out"), "h:1 does not answer in time"),
                             (socket.gaierror(8, "unknown"), "Cannot find h:1"),
                             (ConnectionRefusedError(61, "refused"), "h:1 refuses the connection"),
                             (ssl.SSLCertVerificationError("self signed"), "The certificate of h:1 is not trusted"),
                             (OSError("whatever"), "Cannot reach h:1")]:
            with self.subTest(reason=type(reason).__name__):
                self.assertEqual(cloud._unreachable(urllib.error.URLError(reason), "h:1"), said)
        self.assertEqual(cloud._unreachable(TimeoutError("read timed out"), "h:1"), "h:1 does not answer in time")


class Names(Case):
    def test_a_gateway_name_as_the_cloud_checks_it(self):
        for name in ("lab", "My lab 2", "a-b_c.d", "ab", "x" * 128, "tab\tname"):
            with self.subTest(name=name):
                self.assertTrue(cloud.NAME.fullmatch(name))
        for name in ("", "a", "x" * 129, "lab!", "café", "a/b", "nbsp name", "line break"):
            with self.subTest(name=name):
                self.assertFalse(cloud.NAME.fullmatch(name))


if __name__ == "__main__":
    unittest.main()
