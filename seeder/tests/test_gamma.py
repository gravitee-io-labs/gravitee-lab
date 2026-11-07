"""The Gamma client: where each client points, how it signs in, what it asks again while the management API reloads,
and how it reads a paged list."""


import requests

from lab_seeder import common
from lab_seeder import gamma
from lab_seeder.testkit import Seed

SCOPE = "/organizations/DEFAULT/environments/DEFAULT"


class Paths(Seed):

    def test_each_client_serves_its_own_base_path_of_the_management_api(self):
        g = gamma.Gamma()
        clients = {"v1": g.v1, "v2": g.v2, "org_v1": g.org_v1, "automation": g.automation, "aim": g.aim, "authz": g.authz,
                   "platform": g.platform, "observability": g.observability}
        self.assertEqual({name: api.base_url for name, api in clients.items()}, {
            "v1": f"http://apim/management{SCOPE}",
            "v2": f"http://apim/management/v2{SCOPE}",
            "org_v1": "http://apim/management/organizations/DEFAULT",
            "automation": f"http://apim/automation{SCOPE}",
            "aim": f"http://apim/gamma{SCOPE}/modules/aim",
            "authz": f"http://apim/gamma{SCOPE}/modules/authz",
            "platform": f"http://apim/gamma{SCOPE}/modules/platform",
            "observability": f"http://apim/gamma{SCOPE}/observability",
        })

    def test_the_organization_and_the_environment_are_settings(self):
        self.env(APIM_ORG="acme", APIM_ENV="prod")
        g = gamma.Gamma()
        self.assertEqual((g.org, g.env), ("acme", "prod"))
        self.assertEqual(g.aim.base_url, "http://apim/gamma/organizations/acme/environments/prod/modules/aim")
        self.assertEqual(g.v2.base_url, "http://apim/management/v2/organizations/acme/environments/prod")

    def test_gamma_and_the_automation_api_can_be_elsewhere_than_the_management_api(self):
        self.env(AIM_GAMMA_URL="https://gamma.example/", AIM_AUTOMATION_URL="https://automation.example/apim")
        self.answer({("GET", f"/gamma{SCOPE}/modules/aim/providers"): 200,
                     ("GET", f"/gamma{SCOPE}/observability/dashboards"): 200,
                     ("GET", f"/gamma{SCOPE}/modules/platform/am/am-config"): 200,
                     ("GET", f"/apim/automation{SCOPE}/aim/mcp-proxies"): 200,
                     ("GET", f"/management/v2{SCOPE}/apis"): 200})
        g = gamma.Gamma()
        g.aim.get("/providers")
        g.observability.get("/dashboards")
        g.platform.get("/am/am-config")
        g.automation.get("/aim/mcp-proxies")
        g.v2.get("/apis")
        self.assertEqual([call.url for call in self.platform.calls], [
            f"https://gamma.example/gamma{SCOPE}/modules/aim/providers",
            f"https://gamma.example/gamma{SCOPE}/observability/dashboards",
            f"https://gamma.example/gamma{SCOPE}/modules/platform/am/am-config",
            f"https://automation.example/apim/automation{SCOPE}/aim/mcp-proxies",
            f"http://apim/management/v2{SCOPE}/apis"])


class SigningIn(Seed):

    def clients(self):
        g = gamma.Gamma()
        return [g.v1, g.v2, g.org_v1, g.automation, g.aim, g.authz, g.platform, g.observability]

    def calls(self):
        self.answer({("GET", "/x"): 200})
        for api in self.clients():
            api.session.request("GET", "http://apim/x")   # the path is what the fake routes on, not the client's base
        return self.platform.calls

    def test_a_user_and_a_password_are_sent_to_every_client_as_basic_authentication(self):
        calls = self.calls()
        self.assertEqual({call.auth for call in calls}, {("admin", "admin")})
        self.assertEqual([call.headers.get("Authorization") for call in calls], [None] * len(calls))

    def test_a_token_is_sent_to_every_client_as_a_bearer_and_wins(self):
        self.env(APIM_TOKEN="secret")
        calls = self.calls()
        self.assertEqual({call.headers["Authorization"] for call in calls}, {"Bearer secret"})
        self.assertEqual({call.auth for call in calls}, {None})

    def test_a_token_needs_no_user(self):
        self.env(APIM_TOKEN="secret", APIM_USERNAME=None, APIM_PASSWORD=None)
        self.assertEqual(len(self.calls()), 8)

    def test_an_empty_token_is_no_token(self):
        self.env(APIM_TOKEN="")
        self.assertEqual({call.auth for call in self.calls()}, {("admin", "admin")})

    def test_no_credentials_or_no_management_api_is_an_error(self):
        for name in ("APIM_USERNAME", "APIM_MANAGEMENT_URL"):
            self.env(**{name: None})
            with self.subTest(name=name), self.assertRaisesRegex(common.SeedError, f"{name} is not set"):
                gamma.Gamma()
            self.env(**{name: "x"})


class Patience(Seed):
    """The Gamma clients ask again as every client of the seed does (test_common.Requests); what is Gamma's own is below."""

    def setUp(self):
        super().setUp()
        self.api = gamma.Gamma().v2

    def get(self, *answers, **kwargs):
        self.answer({("GET", f"/management/v2{SCOPE}/x"): list(answers)})
        return self.api.get("/x", **kwargs)

    def test_a_reload_is_waited_for_and_said_under_the_section_of_gamma(self):
        self.assertEqual(self.get(503, 503, (200, {"ok": True})).json(), {"ok": True})
        self.assertEqual(self.clock.sleeps, [common.RETRY_PAUSE, common.RETRY_PAUSE])
        self.assertIn(f"[gamma] ! GET /x: HTTP 503, trying again in {common.RETRY_PAUSE} s (1/{common.RETRIES})", self.output())

    def test_a_module_that_is_not_set_up_says_so_for_good(self):
        with self.assertRaisesRegex(common.HttpError, "not_configured"):
            self.get((503, {"code": "not_configured"}))
        self.assertEqual((len(self.platform.calls), self.clock.sleeps), (1, []))


def content(rows, total):
    return {"content": rows, "totalElements": total}


def data(rows, total):
    return {"data": rows, "pagination": {"totalCount": total}}


def totaled(rows, total):
    return {"data": rows, "total": total}


def items(rows, total):
    return {"items": rows, "total": total}


def no_total(rows, total):
    return {"data": rows}


class Lists(Seed):
    """Where a list starts and how many rows a page holds depend on the endpoint."""

    def serve(self, path, rows, shape, first):
        """A list that answers like Gamma: `shape` says how it wraps the rows of the page asked for."""
        def page(call):
            number, size = int(call.params["page"]) - first, int(call.params["perPage"])
            return 200, shape(rows[number * size:(number + 1) * size], len(rows))
        self.answer({("GET", f"/gamma{SCOPE}/modules/{path}"): page})
        return gamma.Gamma()

    def asked(self):
        return [(int(call.params["page"]), int(call.params["perPage"])) for call in self.platform.calls]

    def test_a_list_is_read_from_where_it_starts_by_pages_of_its_size_until_it_ends(self):
        for shape, paging, first, count, asked in (
                (content, gamma.WORKSPACES, 0, 250, [(0, 100), (1, 100), (2, 100)]),
                (content, gamma.WORKSPACES, 0, 0, [(0, 100)]),
                (data, gamma.LISTS, 1, 150, [(1, 100), (2, 100)]),
                (data, gamma.CATALOG, 1, 450, [(1, 200), (2, 200), (3, 200)]),
                (totaled, gamma.LISTS, 1, 120, [(1, 100), (2, 100)]),
                (totaled, gamma.LISTS, 1, 100, [(1, 100)]),   # the total says there is no more
                (items, gamma.LISTS, 1, 3, [(1, 100)]),
                (no_total, gamma.LISTS, 1, 130, [(1, 100), (2, 100)]),
                (no_total, gamma.LISTS, 1, 100, [(1, 100), (2, 100)])):   # only an empty page says so
            with self.subTest(shape=shape.__name__, count=count):
                self.platform.calls.clear()
                g = self.serve("aim/x", list(range(count)), shape, first)
                self.assertEqual(g.aim.rows("/x", paging), list(range(count)))
                self.assertEqual(self.asked(), asked)

    def test_a_list_refused_from_page_0_is_read_from_page_1(self):
        # A list the seed reads from page 0 on a build that counts from 1, whatever its refusal says
        rows = list(range(250))

        def page(call):
            number, size = int(call.params["page"]) - 1, int(call.params["perPage"])
            if number < 0:
                return 400, {"message": "whatever the build says"}
            return 200, content(rows[number * size:(number + 1) * size], len(rows))
        self.answer({("GET", f"/gamma{SCOPE}/modules/aim/ai-workspaces"): page})
        self.assertEqual(gamma.Gamma().aim.rows("/ai-workspaces", gamma.WORKSPACES), rows)
        self.assertEqual([number for number, _ in self.asked()], [0, 1, 2, 3])
        self.assertEqual(gamma.WORKSPACES.first, 0, "what the seed expects is not changed")

    def test_a_list_refused_from_page_0_and_page_1_is_the_refusal_of_page_1(self):
        for status in (400, 403):
            with self.subTest(status=status):
                self.platform.calls.clear()
                self.answer({("GET", f"/gamma{SCOPE}/modules/aim/ai-workspaces"): lambda call: (
                    (400, {"message": "page floor"}) if call.params["page"] == 0 else (status, {"message": "the real reason"}))})
                with self.assertRaisesRegex(gamma.HttpError, "the real reason"):
                    gamma.Gamma().aim.rows("/ai-workspaces", gamma.WORKSPACES)
                self.assertEqual([number for number, _ in self.asked()], [0, 1])

    def test_a_list_asked_from_page_1_is_never_asked_for_page_0(self):
        self.answer({("GET", f"/gamma{SCOPE}/modules/aim/ai-workspaces/w1/users"): lambda call: (400, {"message": "the real reason"})})
        with self.assertRaisesRegex(gamma.HttpError, "the real reason"):
            gamma.Gamma().aim.rows("/ai-workspaces/w1/users", gamma.LISTS)
        self.assertEqual([number for number, _ in self.asked()], [1])

    def test_any_other_refusal_of_a_list_is_the_refusal(self):
        self.answer({("GET", f"/gamma{SCOPE}/modules/aim/ai-workspaces"): lambda call: (403, {"message": "Forbidden"})})
        with self.assertRaises(gamma.HttpError):
            gamma.Gamma().aim.rows("/ai-workspaces", gamma.WORKSPACES)
        self.assertEqual(len(self.asked()), 1)

    def test_a_list_asked_from_page_1_that_counts_from_0_is_read_from_page_0(self):
        for count in (5, 150):
            with self.subTest(count=count):
                g = self.serve("aim/ai-workspaces/w1/users", list(range(count)), data, first=0)
                self.assertEqual(g.aim.rows("/ai-workspaces/w1/users", gamma.LISTS), list(range(count)))

    def test_a_list_that_lost_rows_while_it_was_read_from_page_0_is_read_once(self):
        rows = list(range(3))
        self.answer({("GET", f"/gamma{SCOPE}/modules/aim/ai-workspaces"): lambda call: (
            200, content(rows if call.params["page"] == 0 else [], 5))})
        self.assertEqual(gamma.Gamma().aim.rows("/ai-workspaces", gamma.WORKSPACES), rows)
        self.assertEqual([number for number, _ in self.asked()], [0, 1])

    def test_a_refusal_after_the_first_page_is_the_refusal(self):
        rows = list(range(250))

        def page(call):
            number, size = int(call.params["page"]), int(call.params["perPage"])
            if number > 0:
                return 400, {"message": "too far"}
            return 200, content(rows[:size], len(rows))
        self.answer({("GET", f"/gamma{SCOPE}/modules/aim/ai-workspaces"): page})
        with self.assertRaisesRegex(gamma.HttpError, "too far"):
            gamma.Gamma().aim.rows("/ai-workspaces", gamma.WORKSPACES)

    def test_an_answer_that_is_not_a_page_is_an_error_and_not_an_empty_list(self):
        g = gamma.Gamma()
        self.answer({("GET", f"/gamma{SCOPE}/modules/aim/bare"): (200, [1, 2]),
                     ("GET", f"/gamma{SCOPE}/modules/aim/other"): (200, {"id": "x"})})
        for path in ("/bare", "/other"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(common.SeedError, f"GET {path}: this is not a paged list"):
                    g.aim.rows(path, gamma.LISTS)

    def test_the_filters_of_a_list_are_sent_with_every_page(self):
        g = self.serve("aim/catalog/sources", list(range(250)), data, first=1)
        g.aim.rows("/catalog/sources", gamma.CATALOG, sourceKind="manual")
        self.assertEqual([call.params["sourceKind"] for call in self.platform.calls], ["manual", "manual"])


class Connecting(Seed):

    def test_it_waits_until_the_probe_succeeds(self):
        g = gamma.Gamma()
        answers = iter([common.SeedError("starting"), requests.ConnectionError("refused"), None])

        def probe():
            answer = next(answers)
            if answer:
                raise answer
        g.connect("aim-catalog", probe, "AI Management")
        self.assertEqual(self.clock.sleeps, [0.5, 1])
        self.assertIn("[aim-catalog] Waiting for AI Management", self.output())
        self.assertIn("[aim-catalog] ✓ AI Management ready", self.output())

    def test_a_module_the_platform_has_no_license_for_ends_the_wait_at_once(self):
        g = gamma.Gamma()
        self.answer({("GET", f"/gamma{SCOPE}/modules/aim/providers"): (503, {"code": "not_configured"})})
        with self.assertRaisesRegex(common.Gone, "AI Management is not available on this platform \\(GET /providers: "
                                                 "HTTP .*Gamma modules of API Management 4.13 or later"):
            g.connect("aim-catalog", lambda: g.aim.get("/providers"), "AI Management")
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(len(self.platform.sent("GET", f"/gamma{SCOPE}/modules/aim/providers")), 1)

    def test_a_module_the_platform_does_not_serve_ends_the_wait_once_its_404_lasts(self):
        g = gamma.Gamma()
        self.answer({("GET", f"/gamma{SCOPE}/modules/aim/providers"): 404})
        with self.assertRaisesRegex(common.Gone, "AI Management is not available on this platform \\(GET /providers: HTTP 404"):
            g.connect("aim-catalog", lambda: g.aim.get("/providers"), "AI Management")
        self.assertGreaterEqual(self.clock.elapsed, gamma.UNSERVED)
        self.assertLess(self.clock.elapsed, gamma.UNSERVED + 5, "and not the whole wait for a platform that starts")

    def test_a_404_while_the_platform_starts_is_waited_out(self):
        g = gamma.Gamma()
        self.answer({("GET", f"/gamma{SCOPE}/modules/aim/providers"): [404, 404, (200, {"data": []})]})
        g.connect("aim-catalog", lambda: g.aim.get("/providers"), "AI Management")
        self.assertIn("✓ AI Management ready", self.output())

    def test_credentials_or_ids_the_platform_refuses_end_the_wait_at_once(self):
        for status in common.REFUSED:
            with self.subTest(status=status):
                self.platform.calls.clear()
                g = gamma.Gamma()
                self.answer({("GET", f"/gamma{SCOPE}/modules/aim/providers"): (status, {"message": "no"})})
                with self.assertRaisesRegex(common.SeedError, f"^AI Management: GET /providers: HTTP {status} "):
                    g.connect("aim-catalog", lambda: g.aim.get("/providers"), "AI Management")
                self.assertEqual(len(self.platform.calls), 1)


class Modules(Seed):
    """What the platform serves, asked before the seed changes anything."""

    LIST = "/gamma/organizations/DEFAULT/modules"

    def serve(self, answer):
        self.answer({("GET", self.LIST): answer})

    def test_an_answer_that_is_no_list_is_an_error_and_not_a_platform_without_modules(self):
        for body in ("<html>", {"data": []}):
            with self.subTest(body=body):
                self.serve((200, body))
                with self.assertRaisesRegex(common.SeedError, "GET /modules: the answer is not a list of modules"):
                    gamma.Gamma().modules()

    def test_the_modules_it_will_fill_must_all_be_there(self):
        self.serve((200, [{"id": "aim"}, {"id": "authz"}, {"id": "platform"}, {"id": "edge"}]))
        gamma.require_modules({"aim", "authz"})
        self.assertEqual(self.platform.changes(), [])

    def test_a_missing_module_stops_the_seed_naming_it_and_what_to_choose(self):
        self.serve((200, [{"id": "edge"}, {"id": "aim"}]))
        with self.assertRaisesRegex(common.SeedError, "does not serve the Gamma module authz: this playground needs the Gamma "
                                                      "modules of API Management 4.13 or later and a license that includes them"):
            gamma.require_modules({"aim", "authz"})
        self.serve(404)
        with self.assertRaisesRegex(common.SeedError, "Gamma modules aim, authz:"):
            gamma.require_modules({"aim", "authz"})

    def test_a_404_or_a_page_that_is_no_list_while_the_platform_starts_is_not_the_platform_without_gamma(self):
        for first in (404, (200, "<html>starting</html>"), (500, {"message": "starting"})):
            with self.subTest(first=str(first)):
                self.platform.calls.clear()
                self.serve([first, first, (200, [{"id": "aim"}])])
                gamma.require_modules({"aim"})
                self.assertEqual(len(self.platform.calls), 3)

    def test_a_404_that_lasts_is_the_platform_without_gamma_and_said_soon(self):
        self.serve(404)
        with self.assertRaisesRegex(common.SeedError, "does not serve the Gamma module aim"):
            gamma.require_modules({"aim"})
        self.assertLess(self.clock.elapsed, gamma.UNSERVED + 5)

    def test_credentials_the_platform_refuses_stop_the_seed_at_once(self):
        self.serve((401, {"message": "Full authentication is required"}))
        with self.assertRaisesRegex(common.SeedError, "^the list of Gamma modules: GET /modules: HTTP 401"):
            gamma.require_modules({"aim"})
        self.assertEqual(len(self.platform.calls), 1)

    def test_a_playground_that_fills_no_module_asks_nothing(self):
        gamma.require_modules(set())
        self.assertEqual(self.platform.calls, [])

    def test_the_list_is_read_where_gamma_is_served_with_the_same_credentials(self):
        self.env(AIM_GAMMA_URL="https://gamma.example.com", APIM_TOKEN="token-1")
        self.answer({("GET", "https://gamma.example.com/gamma/organizations/DEFAULT/modules"): (200, [{"id": "aim"}])})
        gamma.require_modules({"aim"})
        self.assertEqual(self.platform.calls[0].headers["Authorization"], "Bearer token-1")
