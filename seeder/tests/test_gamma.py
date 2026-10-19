"""The Gamma client: where each client points, how it signs in, what it asks again while the management API reloads,
and how it reads a paged list."""

import unittest

import requests

from lab_seeder import common
from lab_seeder import gamma
from lab_seeder.testkit import Seed

SCOPE = "/organizations/DEFAULT/environments/DEFAULT"


class Paths(Seed):

    def test_each_client_serves_its_own_base_path_of_the_management_api(self):
        g = gamma.Gamma()
        clients = {"v1": g.v1, "v2": g.v2, "org_v1": g.org_v1, "automation": g.automation, "aim": g.aim, "authz": g.authz,
                   "platform": g.platform, "observability": g.observability, "other": g.module("other")}
        self.assertEqual({name: api.base_url for name, api in clients.items()}, {
            "v1": f"http://apim/management{SCOPE}",
            "v2": f"http://apim/management/v2{SCOPE}",
            "org_v1": "http://apim/management/organizations/DEFAULT",
            "automation": f"http://apim/automation{SCOPE}",
            "aim": f"http://apim/gamma{SCOPE}/modules/aim",
            "authz": f"http://apim/gamma{SCOPE}/modules/authz",
            "platform": f"http://apim/gamma{SCOPE}/modules/platform",
            "observability": f"http://apim/gamma{SCOPE}/observability",
            "other": f"http://apim/gamma{SCOPE}/modules/other",
        })

    def test_the_organization_and_the_environment_are_settings(self):
        self.env(APIM_ORG="acme", APIM_ENV="prod")
        g = gamma.Gamma()
        self.assertEqual((g.org, g.env), ("acme", "prod"))
        self.assertEqual(g.aim.base_url, "http://apim/gamma/organizations/acme/environments/prod/modules/aim")
        self.assertEqual(g.v2.base_url, "http://apim/management/v2/organizations/acme/environments/prod")

    def test_gamma_and_the_automation_api_are_where_the_management_api_is_unless_told_otherwise(self):
        self.env(AIM_GAMMA_URL="", AIM_AUTOMATION_URL="")
        g = gamma.Gamma()
        self.assertTrue(g.aim.base_url.startswith("http://apim/gamma/"))
        self.assertTrue(g.automation.base_url.startswith("http://apim/automation/"))

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
        return [g.v1, g.v2, g.org_v1, g.automation, g.aim, g.authz, g.platform, g.observability, g.module("other")]

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
        self.assertEqual(len(self.calls()), 9)

    def test_an_empty_token_is_no_token(self):
        self.env(APIM_TOKEN="")
        self.assertEqual({call.auth for call in self.calls()}, {("admin", "admin")})

    def test_no_credentials_is_an_error(self):
        self.env(APIM_USERNAME=None)
        with self.assertRaisesRegex(common.SeedError, "APIM_USERNAME is not set"):
            gamma.Gamma()

    def test_no_management_api_is_an_error(self):
        self.env(APIM_MANAGEMENT_URL=None)
        with self.assertRaisesRegex(common.SeedError, "APIM_MANAGEMENT_URL is not set"):
            gamma.Gamma()


class Reloads(Seed):
    """While it reloads, the management API answers errors that say nothing about the request."""

    def setUp(self):
        super().setUp()
        self.api = gamma.Gamma().v2

    def get(self, *answers, **kwargs):
        self.answer({("GET", f"/management/v2{SCOPE}/x"): list(answers)})
        return self.api.get("/x", **kwargs)

    def test_each_of_its_answers_while_it_reloads_is_asked_again(self):
        for answer in ((400, {"message": "Invalid organization or environment"}), 502, 503, 504,
                       (500, "Timeout while receiving message"), requests.ConnectionError("refused")):
            with self.subTest(answer=str(answer)):
                self.platform.calls.clear()
                self.assertEqual(self.get(answer, (200, {"ok": True})).json(), {"ok": True})
                self.assertEqual(len(self.platform.calls), 2)

    def test_it_waits_and_says_so(self):
        self.get(503, 503, 200)
        self.assertEqual(self.clock.sleeps, [gamma.PAUSE, gamma.PAUSE])
        self.assertIn(f"[gamma] ! GET /x: HTTP 503, trying again in {gamma.PAUSE} s (1/{gamma.RETRIES})",
                      self.output())
        self.assertIn(f"(2/{gamma.RETRIES})", self.output())

    def test_it_gives_up_after_its_retries_with_the_last_answer(self):
        with self.assertRaisesRegex(common.SeedError, "GET /x: HTTP 502 bad gateway"):
            self.get((502, "bad gateway"))
        self.assertEqual(len(self.platform.calls), gamma.RETRIES + 1)
        self.assertEqual(len(self.clock.sleeps), gamma.RETRIES)

    def test_a_caller_that_expects_the_last_answer_gets_it(self):
        self.assertEqual(self.get(503, expect=range(200, 600)).status_code, 503)

    def test_a_service_that_cannot_be_reached_is_an_error_naming_the_request(self):
        with self.assertRaisesRegex(common.SeedError, "GET /x: down"):
            self.get(requests.ConnectionError("down"))
        self.assertEqual(len(self.platform.calls), gamma.RETRIES + 1)

    def test_what_is_an_answer_to_the_request_is_not_asked_again(self):
        for answer in (404, (400, {"message": "Name is required"}), (500, "boom"), (503, {"code": "not_configured"})):
            with self.subTest(answer=str(answer)):
                self.platform.calls.clear()
                with self.assertRaises(common.SeedError):
                    self.get(answer)
                self.assertEqual(len(self.platform.calls), 1)
        self.assertEqual(self.clock.sleeps, [])

    def test_every_method_and_every_client_asks_again(self):
        g = gamma.Gamma()
        self.answer({("POST", f"/gamma{SCOPE}/modules/aim/providers"): [503, 201],
                     ("PUT", f"/automation{SCOPE}/aim/mcp-proxies"): [502, 200]})
        g.aim.post("/providers", json={})
        g.automation.put("/aim/mcp-proxies", json={})
        self.assertEqual(len(self.platform.calls), 4)


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

    def test_workspaces_start_at_page_0_and_hold_100(self):
        g = self.serve("aim/ai-workspaces", list(range(250)), content, first=0)
        self.assertEqual(g.aim.rows("/ai-workspaces", gamma.WORKSPACES), list(range(250)))
        self.assertEqual(self.asked(), [(0, 100), (1, 100), (2, 100)])

    def test_a_list_that_starts_where_the_platform_says_is_read_from_there(self):
        # The workspaces of a build whose lists start at page 1: its 400 says where, and the list is read again from there
        rows = list(range(250))

        def page(call):
            number, size = int(call.params["page"]), int(call.params["perPage"])
            if number < 1:
                return 400, {"message": "Pagination page param must be >= 1", "path": "list.arg0"}
            return 200, content(rows[(number - 1) * size:number * size], len(rows))
        self.answer({("GET", f"/gamma{SCOPE}/modules/aim/ai-workspaces"): page})
        g = gamma.Gamma()
        self.assertEqual(g.aim.rows("/ai-workspaces", gamma.WORKSPACES), rows)
        self.assertEqual(self.asked(), [(0, 100), (1, 100), (2, 100), (3, 100)])
        self.assertEqual(gamma.WORKSPACES.first, 0, "what the seed expects is not changed")

    def test_any_other_refusal_of_a_list_is_the_refusal(self):
        self.answer({("GET", f"/gamma{SCOPE}/modules/aim/ai-workspaces"):
                     lambda call: (400, {"message": "Pagination page param must be >= 0"})})
        with self.assertRaises(gamma.HttpError):
            gamma.Gamma().aim.rows("/ai-workspaces", gamma.WORKSPACES)   # it is asked for where it is already
        self.answer({("GET", f"/gamma{SCOPE}/modules/aim/ai-workspaces"): lambda call: (400, {"message": "Invalid query"})})
        with self.assertRaises(gamma.HttpError):
            gamma.Gamma().aim.rows("/ai-workspaces", gamma.WORKSPACES)

    def test_members_start_at_page_1_and_hold_100(self):
        g = self.serve("aim/ai-workspaces/w1/users", list(range(150)), data, first=1)
        self.assertEqual(g.aim.rows("/ai-workspaces/w1/users", gamma.LISTS), list(range(150)))
        self.assertEqual(self.asked(), [(1, 100), (2, 100)])

    def test_the_catalog_starts_at_page_1_and_holds_200(self):
        g = self.serve("aim/catalog/models", list(range(450)), data, first=1)
        self.assertEqual(g.aim.rows("/catalog/models", gamma.CATALOG), list(range(450)))
        self.assertEqual(self.asked(), [(1, 200), (2, 200), (3, 200)])

    def test_authorization_lists_say_total(self):
        g = self.serve("authz/entities", list(range(120)), totaled, first=1)
        self.assertEqual(g.authz.rows("/entities", gamma.LISTS), list(range(120)))
        self.assertEqual(self.asked(), [(1, 100), (2, 100)])

    def test_a_list_that_says_items_is_read_too(self):
        g = self.serve("aim/identity/agents", [1, 2, 3], items, first=1)
        self.assertEqual(g.aim.rows("/identity/agents", gamma.LISTS), [1, 2, 3])

    def test_a_list_that_says_no_total_ends_with_the_first_short_page(self):
        g = self.serve("aim/x", list(range(130)), no_total, first=1)
        self.assertEqual(g.aim.rows("/x", gamma.LISTS), list(range(130)))
        self.assertEqual(self.asked(), [(1, 100), (2, 100)])

    def test_a_list_that_says_no_total_and_fills_its_last_page_ends_with_an_empty_one(self):
        g = self.serve("aim/x", list(range(100)), no_total, first=1)
        self.assertEqual(len(g.aim.rows("/x", gamma.LISTS)), 100)
        self.assertEqual(self.asked(), [(1, 100), (2, 100)])

    def test_a_list_that_fills_its_last_page_says_no_more_when_its_total_is_reached(self):
        g = self.serve("aim/x", list(range(100)), totaled, first=1)
        self.assertEqual(len(g.aim.rows("/x", gamma.LISTS)), 100)
        self.assertEqual(self.asked(), [(1, 100)])

    def test_an_answer_that_is_not_a_page_is_an_error_and_not_an_empty_list(self):
        g = gamma.Gamma()
        self.answer({("GET", f"/gamma{SCOPE}/modules/aim/bare"): (200, [1, 2]),
                     ("GET", f"/gamma{SCOPE}/modules/aim/other"): (200, {"id": "x"})})
        for path in ("/bare", "/other"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(common.SeedError, f"GET {path}: this is not a paged list"):
                    g.aim.rows(path, gamma.LISTS)

    def test_an_empty_list_is_one_request(self):
        g = self.serve("aim/x", [], content, first=0)
        self.assertEqual(g.aim.rows("/x", gamma.WORKSPACES), [])
        self.assertEqual(len(self.platform.calls), 1)

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

    def test_it_gives_up_when_the_platform_keeps_failing(self):
        g = gamma.Gamma()
        self.answer({("GET", f"/gamma{SCOPE}/modules/aim/providers"): (500, "boom")})
        with self.assertRaisesRegex(common.SeedError, "not ready after 300 s: GET /providers: HTTP 500 boom"):
            g.connect("aim-catalog", lambda: g.aim.get("/providers"))

    def test_a_module_the_platform_does_not_serve_or_has_no_license_for_ends_the_wait_at_once(self):
        for answer in (404, (503, {"code": "not_configured"})):
            with self.subTest(answer=str(answer)):
                self.platform.calls.clear()
                g = gamma.Gamma()
                self.answer({("GET", f"/gamma{SCOPE}/modules/aim/providers"): answer})
                with self.assertRaisesRegex(common.Gone, "AI Management is not available on this platform \\(GET /providers: "
                                                         "HTTP .*Gamma modules of API Management 4.13 or later"):
                    g.connect("aim-catalog", lambda: g.aim.get("/providers"), "AI Management")
                self.assertEqual(self.clock.sleeps, [])
                self.assertEqual(len(self.platform.sent("GET", f"/gamma{SCOPE}/modules/aim/providers")), 1)


class Modules(Seed):
    """What the platform serves, asked before the seed changes anything."""

    LIST = "/gamma/organizations/DEFAULT/modules"

    def serve(self, answer):
        self.answer({("GET", self.LIST): answer})

    def test_the_ids_of_the_modules_the_platform_serves(self):
        self.serve((200, [{"id": "aim", "name": "Agent Management"}, {"id": "authz"}, {"id": "platform"}]))
        self.assertEqual(gamma.Gamma().modules(), {"aim", "authz", "platform"})

    def test_a_platform_that_has_no_such_list_serves_none(self):
        self.serve(404)
        self.assertEqual(gamma.Gamma().modules(), set())

    def test_a_list_that_is_not_one_serves_none(self):
        self.serve((200, "<html>"))
        self.assertEqual(gamma.Gamma().modules(), set())

    def test_any_other_answer_is_an_error_with_its_status(self):
        self.serve((401, {"message": "Full authentication is required"}))
        with self.assertRaisesRegex(common.HttpError, "GET /modules: HTTP 401 .*Full authentication"):
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

    def test_a_list_that_is_not_answering_yet_is_asked_again(self):
        answers = iter([(500, {"message": "starting"}), (200, [{"id": "aim"}])])
        self.answer({("GET", self.LIST): lambda call: next(answers)})
        gamma.require_modules({"aim"})
        self.assertEqual(len(self.platform.calls), 2)
        self.assertEqual(self.clock.sleeps, [0.5])

    def test_a_playground_that_fills_no_module_asks_nothing(self):
        gamma.require_modules(set())
        self.assertEqual(self.platform.calls, [])

    def test_the_list_is_read_where_gamma_is_served_with_the_same_credentials(self):
        self.env(AIM_GAMMA_URL="https://gamma.example.com", APIM_TOKEN="token-1")
        self.answer({("GET", "https://gamma.example.com/gamma/organizations/DEFAULT/modules"): (200, [{"id": "aim"}])})
        gamma.require_modules({"aim"})
        self.assertEqual(self.platform.calls[0].headers["Authorization"], "Bearer token-1")


if __name__ == "__main__":
    unittest.main()
