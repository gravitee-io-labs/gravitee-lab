"""The seed against management APIs that answer from a table: what it changes is its own, a removal that fails keeps
its record, an authorization engine follows the seed's OpenFGA model, the modules run in their order and a module
with nothing to seed leaves the platform alone. Also the fake platform the other tests build on."""

import json
import os
import re
import time
import threading
import unittest
from unittest import mock

import requests

from lab_seeder import am
from lab_seeder import apim
from lab_seeder import common
from lab_seeder import openfga
from lab_seeder import seed
from lab_seeder.testkit import Platform, Response, Seed

V2 = "/management/v2/organizations/DEFAULT/environments/DEFAULT"
ENGINES = "/domain/authorization-engines"


class Apis(Seed):

    DEFINITION = {"api": {"name": "Example LLM", "listeners": [{"paths": [{"path": "/llm-proxy/"}]}]}}

    def holding(self, api_id, name):
        """APIM where this API holds the path of the seed's."""
        api = {**self.DEFINITION["api"], "id": api_id, "name": name}
        return {("GET", f"{V2}/apis"): (200, {"data": [api], "pagination": {"pageCount": 1}})}

    def test_an_api_the_seed_did_not_create_is_never_changed(self):
        self.answer(self.holding("mine", "My LLM proxy"))
        with self.assertRaisesRegex(common.SeedError, "/llm-proxy/ is the path of 'My LLM proxy'"):
            apim.ApiManagement().import_api(self.DEFINITION, "llms.json")
        self.assertEqual(self.platform.changes(), [])

    def test_the_seeds_api_is_replaced_and_recorded_under_its_new_id(self):
        self.answer({**self.holding("old", "Example LLM"),
                     ("GET", f"{V2}/apis/old/plans"): (200, {"data": []}),
                     ("PUT", f"{V2}/apis/old/_import/definition"): (500, {"message": "Not supported"}),
                     ("POST", f"{V2}/apis/old/_stop"): 400,
                     ("DELETE", f"{V2}/apis/old"): 204,
                     ("POST", f"{V2}/apis/_import/definition"): (201, {"id": "new"}),
                     ("GET", f"{V2}/apis/new"): (200, {"lifecycleState": "PUBLISHED", "state": "STARTED"}),
                     ("POST", f"{V2}/apis/new/deployments"): 202},
                    [{"product": "apim", "kind": "api", "name": "Example LLM", "id": "old"}])
        apim.ApiManagement().import_api(self.DEFINITION, "llms.json")
        self.assertEqual([(x["name"], x["id"]) for x in common.created()], [("Example LLM", "new")])


class Engines(Seed):

    SPEC = {"type": "openfga", "name": "OpenFGA", "store": "Documents", "connectionUri": "http://openfga:8180"}
    STORES = {"Documents": {"id": "store", "model_id": "model-2"}}
    WANTED = {"connectionUri": "http://openfga:8180", "storeId": "store", "authorizationModelId": "model-2"}

    def engine(self, configuration, recorded=True):
        """AM with one OpenFGA engine set so; then the seed for it."""
        record = {"product": "am", "kind": "authorization-engine", "name": "OpenFGA", "id": "e1"}
        self.answer({("GET", ENGINES): (200, [{"id": "e1", "type": "openfga", "name": "OpenFGA",
                                               "configuration": json.dumps(configuration)}]),
                     ("PUT", f"{ENGINES}/e1"): (200, {})},
                    [record] if recorded else [])
        access = am.AccessManagement()
        access.domain_url, access.domain_id = "/domain", "domain"
        access.authorization_engine(self.SPEC, self.STORES)

    def test_the_seeds_engine_follows_a_new_model(self):
        self.engine({**self.WANTED, "authorizationModelId": "model-1"})
        self.assertEqual(self.platform.calls[-1].json, {"name": "OpenFGA", "configuration": json.dumps(self.WANTED)})
        self.assertEqual(self.platform.changes(), [("PUT", f"{ENGINES}/e1")])

    def test_an_engine_set_as_the_seed_wants_is_kept(self):
        self.engine(self.WANTED)
        self.assertEqual(self.platform.changes(), [])

    def test_an_engine_the_seed_did_not_create_is_never_changed(self):
        with self.assertRaisesRegex(common.SeedError, "the seed did not create it"):
            self.engine({**self.WANTED, "authorizationModelId": "model-1"}, recorded=False)
        self.assertEqual(self.platform.changes(), [])


class Removal(Seed):

    STORE = {"product": "openfga", "kind": "store", "name": "Documents", "id": "s1"}

    def remove(self, status):
        self.answer({("DELETE", "/stores/s1"): status}, [self.STORE])
        return self.main("--remove")

    def test_what_is_gone_already_counts_as_deleted(self):
        self.assertEqual(self.remove(404), 0)
        self.assertEqual(common.created(), [])

    def test_a_refused_delete_keeps_the_record(self):
        self.assertEqual(self.remove(403), 1)
        self.assertEqual([x["id"] for x in common.created()], ["s1"])

    def test_it_says_how_many_items_it_removed(self):
        self.answer({("DELETE", "/stores/s1"): 204, ("DELETE", "/stores/s2"): 204},
                    [self.STORE, {**self.STORE, "name": "Projects", "id": "s2"}])
        self.main("--remove")
        self.assertIn("✓ 2 items removed", self.output())

    def test_it_says_item_for_one(self):
        self.remove(204)
        self.assertIn("✓ 1 item removed", self.output())

    def test_what_a_module_gives_back_is_kept_in_the_record_and_the_rest_is_removed(self):
        mine = {"product": "apim", "kind": "user", "label": "users", "name": "astrid", "id": "u1"}
        self.answer({("DELETE", "/stores/s1"): 204}, [self.STORE, mine])
        with mock.patch.object(apim, "remove", return_value=[mine]):
            self.assertEqual(self.main("--remove"), 0)
        self.assertEqual([x["id"] for x in common.created()], ["u1"])
        self.assertIn("✓ 1 item removed, 1 kept", self.output())


class Modules(Seed):
    """What the order and the contract of the modules give: a playground that seeds nothing of a module is left alone."""

    def run_modules(self):
        """Replaces seed() and remove() of every module with a function that notes its call, as (action, module)."""
        self.order = []
        for module in seed.MODULES:
            for action in ("seed", "remove"):
                def note(*arguments, name=module.__name__.rsplit('.', 1)[-1], action=action):
                    self.order.append((action, name))

                patch = mock.patch.object(module, action, side_effect=note)
                patch.start()
                self.addCleanup(patch.stop)

    def test_the_modules_that_do_not_depend_on_one_another_seed_at_the_same_time_and_the_others_after_theirs(self):
        self.workers(4)
        meeting, events, lock = threading.Barrier(2, timeout=5), [], threading.Lock()   # the two that start first wait for each other
        for module in seed.MODULES:
            def note(*arguments, name=module.__name__.rsplit('.', 1)[-1]):
                with lock:
                    events.append(("start", name))
                if name in ("openfga", "apim"):
                    meeting.wait()
                with lock:
                    events.append(("end", name))
            patch = mock.patch.object(module, "seed", side_effect=note)
            patch.start()
            self.addCleanup(patch.stop)
        self.assertEqual(self.main(), 0, self.output())
        self.assertEqual(sorted(name for kind, name in events if kind == "start"), sorted(module.__name__.rsplit('.', 1)[-1] for module in seed.MODULES))
        for module in seed.MODULES:
            for need in module.DEPENDS:
                self.assertLess(events.index(("end", need)), events.index(("start", module.__name__.rsplit('.', 1)[-1])), f"{module.__name__.rsplit('.', 1)[-1]} after {need}")

    def test_the_seed_says_what_running_steps_together_saved_and_says_nothing_when_it_saved_nothing(self):
        seed.told({"apim": 40.2, "am": 25.0, "k8s": 0.2}, 41.0)
        self.assertIn("[seed] Took 41 s; the steps add up to 65 s (apim 40 s, am 25 s)", self.output())
        before = self.output()
        seed.told({"apim": 40.0}, 40.5)
        self.assertEqual(self.output(), before)

    def test_the_first_failure_is_the_one_the_seed_reports(self):
        self.workers(4)
        for module in seed.MODULES:
            def note(*arguments, name=module.__name__.rsplit('.', 1)[-1]):
                if name == "apim":
                    raise common.SeedError("apim failed")
                for _ in range(100):
                    common.pause(0.01)   # stops when the seed does
            patch = mock.patch.object(module, "seed", side_effect=note)
            patch.start()
            self.addCleanup(patch.stop)
        self.assertEqual(self.main(), 1)
        self.assertTrue(self.output().rstrip().endswith("[seed] ✗ apim failed"), self.output())

    def test_the_modules_seed_in_their_order(self):
        self.run_modules()
        self.assertEqual(self.main(), 0)
        self.assertEqual([name for _, name in self.order],
                         ["openfga", "am", "apim", "apim_portal", "am_link", "aim_catalog", "aim_workspaces", "aim_proxies",
                          "aim_agents", "aim_governance", "authz", "aim_dashboards", "k8s"])

    def test_the_gamma_modules_to_fill_follow_the_folders_of_the_seed(self):
        self.assertEqual(seed.gamma_modules(), set())
        self.seed_files({"aim/providers/p.yaml": "name: p", "aim/dashboards/d.yaml": "id: d"})
        self.assertEqual(seed.gamma_modules(), {"aim"})
        self.seed_files({"authz/entities.yaml": "entities: []", "am/gamma-link.yaml": "domain: d"})
        self.assertEqual(seed.gamma_modules(), {"aim", "authz", "platform"})

    def test_a_platform_that_lacks_a_module_the_seed_fills_stops_it_before_any_module_runs(self):
        self.run_modules()
        self.seed_files({"aim/providers/p.yaml": "name: p"})
        self.answer({("GET", "/gamma/organizations/DEFAULT/modules"): (200, [{"id": "edge"}])})
        self.assertEqual(self.main(), 1)
        self.assertIn("[seed] ✗ This platform does not serve the Gamma module aim: this playground needs the Gamma modules",
                      self.output())
        self.assertEqual(self.order, [])
        self.assertEqual(self.platform.changes(), [])

    def test_a_platform_that_serves_them_is_seeded(self):
        self.run_modules()
        self.seed_files({"aim/providers/p.yaml": "name: p", "authz/entities.yaml": "entities: []"})
        self.answer({("GET", "/gamma/organizations/DEFAULT/modules"): (200, [{"id": "aim"}, {"id": "authz"}])})
        self.assertEqual(self.main(), 0, self.output())
        self.assertEqual(len(self.order), len(seed.MODULES))

    def test_a_removal_asks_nothing_of_the_platform_first(self):
        self.run_modules()
        self.seed_files({"aim/providers/p.yaml": "name: p"})
        self.assertEqual(self.main("--remove"), 0)
        self.assertEqual(self.platform.calls, [])

    def test_a_removal_runs_them_the_other_way_round(self):
        self.run_modules()
        self.assertEqual(self.main("--remove"), 0)
        self.assertEqual(self.order, [("remove", module.__name__.rsplit('.', 1)[-1]) for module in reversed(seed.MODULES)])

    def test_the_stores_of_openfga_go_to_am(self):
        with mock.patch.object(openfga, "seed", return_value={"Documents": {"id": "s"}}), \
                mock.patch.object(am, "seed") as access, mock.patch.object(apim, "seed"):
            self.main()
        access.assert_called_once_with({"Documents": {"id": "s"}})

    def test_a_module_that_fails_stops_the_seed_with_its_reason(self):
        self.run_modules()
        with mock.patch.object(seed.aim_catalog, "seed", side_effect=common.SeedError("no provider")):
            self.assertEqual(self.main(), 1)
        self.assertIn("[seed] ✗ no provider", self.output())
        self.assertNotIn(("seed", "authz"), self.order)

    def test_a_module_that_breaks_fails_the_seed(self):
        self.run_modules()
        with mock.patch.object(seed.authz, "seed", side_effect=KeyError("id")), \
                mock.patch("traceback.print_exc") as shown:
            self.assertEqual(self.main(), 1)
        shown.assert_called_once()
        self.assertNotIn("Playground ready", self.output())

    def test_a_playground_that_seeds_nothing_needs_no_setting_and_makes_no_call(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.main(), 0)
            self.assertEqual(self.main("--remove"), 0)
        self.assertEqual(self.platform.calls, [])
        self.assertEqual(sorted({line.split("]")[0] for line in self.output().splitlines()}), ["[seed"])

    def test_every_module_leaves_the_records_of_the_others_alone(self):
        everyone = [{"product": module.SECTION, "kind": kind, "name": kind, "id": kind, "label": ""}
                    for module in seed.MODULES for kind in getattr(module, "ORDER", ["x"])]
        everyone += [{"product": "am", "kind": "domain", "name": "d", "id": "d", "label": ""},
                     {"product": "apim", "kind": "api", "name": "a", "id": "a", "label": ""},
                     {"product": "unknown", "kind": "thing", "name": "t", "id": "t", "label": ""}]
        for module in seed.MODULES:
            theirs = [x for x in everyone if x["product"] != module.SECTION
                      or (hasattr(module, "ORDER") and x["kind"] not in module.ORDER)]
            with self.subTest(module=module.__name__.rsplit('.', 1)[-1]):
                module.remove(theirs)
                module.remove([])
        self.assertEqual(self.platform.calls, [])

    def test_no_two_modules_record_the_same_kind(self):
        kinds = [(module.SECTION, kind) for module in seed.MODULES for kind in getattr(module, "ORDER", [])]
        self.assertEqual(len(kinds), len(set(kinds)))

    def test_what_a_module_removes_is_its_own_in_the_order_of_its_kinds(self):
        records = [{"product": "p", "kind": kind, "id": i} for i, kind in enumerate(["a", "b", "c", "b", "other"])]
        records.append({"product": "elsewhere", "kind": "b", "id": 9})
        self.assertEqual([x["id"] for x in common.owned(records, "p", ["b", "a"])], [1, 3, 0])

    def test_a_module_asks_the_seed_for_its_folders_before_anything(self):
        self.assertFalse(common.has_seed("aim/providers", "authz"))
        self.seed_files({"authz/policies.yaml": "x: 1"})
        self.assertTrue(common.has_seed("aim/providers", "authz"))
        self.assertFalse(common.has_seed("aim/providers"))


class Fakes(Seed):
    """The platform the tests build on."""

    def get(self, path, **kwargs):
        return common.Api("http://apim").get(path, expect=range(100, 600), **kwargs)

    def test_a_body_is_json_for_a_dict_and_a_list_and_text_for_a_string(self):
        self.answer({("GET", "/a"): (200, {"x": 1}), ("GET", "/b"): (200, [1]), ("GET", "/c"): (200, "hello")})
        self.assertEqual((self.get("/a").json(), self.get("/b").json()), ({"x": 1}, [1]))
        self.assertEqual(self.get("/a").headers["content-type"], "application/json")
        text = self.get("/c")
        self.assertEqual((text.text, text.headers["Content-Type"]), ("hello", "text/plain"))
        with self.assertRaises(ValueError):
            text.json()

    def test_an_answer_is_a_status_or_a_tuple_with_a_body_and_headers(self):
        self.answer({("GET", "/a"): 404, ("GET", "/b"): (201, None, {"Location": "/b/1"})})
        self.assertEqual((self.get("/a").status_code, self.get("/a").ok), (404, False))
        self.assertEqual(self.get("/b").headers["location"], "/b/1")

    def test_a_path_that_is_a_pattern_must_match_the_whole_path(self):
        self.answer({("GET", re.compile(r"/apis/[^/]+")): 200})
        self.assertEqual(self.get("/apis/abc").status_code, 200)
        with self.assertRaisesRegex(AssertionError, "Unexpected GET /apis/abc/plans"):
            self.get("/apis/abc/plans")

    def test_a_route_that_names_query_parameters_wins_over_one_that_does_not(self):
        self.answer({("GET", "/list"): (200, "any"), ("GET", "/list?page=2"): (200, "second")})
        self.assertEqual(self.get("/list", params={"page": 2}).text, "second")
        self.assertEqual(self.get("/list", params={"page": 1}).text, "any")
        self.assertEqual(self.get("/list?page=2&perPage=5").text, "second")

    def test_a_route_that_starts_with_http_is_matched_against_the_whole_url(self):
        self.answer({("GET", "http://bakery-agent:8000/card"): (200, "agent"), ("GET", "/card"): (200, "other")})
        self.assertEqual(requests.get("http://bakery-agent:8000/card").text, "agent")
        self.assertEqual(requests.get("http://elsewhere/card").text, "other")

    def test_a_list_answers_one_item_per_call_and_the_last_one_for_the_rest(self):
        self.answer({("GET", "/a"): [503, 503, (200, "up")]})
        self.assertEqual([self.get("/a").status_code for _ in range(4)], [503, 503, 200, 200])

    def test_a_function_answers_from_the_call(self):
        self.answer({("POST", "/echo"): lambda call: (201, {"echo": call.json, "key": call.headers.get("X-Key")})})
        self.assertEqual(common.Api("http://apim").post("/echo", json={"a": 1}, headers={"X-Key": "k"}).json(),
                         {"echo": {"a": 1}, "key": "k"})

    def test_an_exception_is_raised(self):
        self.answer({("GET", "/down"): [requests.ConnectionError("down"), 200]})
        with self.assertRaises(requests.ConnectionError):
            self.get("/down")
        self.assertEqual(self.get("/down").status_code, 200)

    def test_a_call_no_route_answers_fails_the_test(self):
        with self.assertRaisesRegex(AssertionError, r"Unexpected GET /nowhere \{'q': 1\}"):
            self.get("/nowhere", params={"q": 1})

    def test_a_call_is_kept_with_what_it_sent(self):
        self.answer({("POST", "/upload"): 201})
        api = common.Api("http://apim")
        api.session.auth = ("admin", "admin")
        api.post("/upload", files={"file": ("a.zip", b"PK", "application/zip")}, data={"kind": "skill"},
                 params={"v": 1}, headers={"Content-Type": None, "X-A": "1"})
        call = self.platform.calls[0]
        self.assertEqual((call.method, call.path, call.params, call.data), ("POST", "/upload", {"v": 1}, {"kind": "skill"}))
        self.assertEqual(call.files["file"][1], b"PK")
        self.assertEqual((call.auth, call.headers.get("X-A"), "Content-Type" in call.headers), (("admin", "admin"), "1", False))

    def test_every_client_has_its_own_headers_and_credentials(self):
        self.answer({("GET", "/x"): 200})
        first, second = common.Api("http://one"), common.Api("http://two")
        first.session.headers["Authorization"] = "Bearer t"
        first.get("/x")
        second.get("/x")
        self.assertEqual([call.headers.get("Authorization") for call in self.platform.calls], ["Bearer t", None])

    def test_what_changes_and_what_was_sent_are_listed(self):
        self.answer({("GET", "/a"): 200, ("PUT", "/a"): 200, ("PUT", re.compile(r"/b/\d")): 200})
        api = common.Api("http://apim")
        api.get("/a")
        api.put("/a")
        api.put("/b/1")
        self.assertEqual(self.platform.changes(), [("PUT", "/a"), ("PUT", "/b/1")])
        self.assertEqual(len(self.platform.sent("PUT", re.compile(r"/[ab].*"))), 2)

    def test_the_requests_functions_and_sessions_all_reach_the_platform(self):
        self.answer({("GET", "/a"): 200, ("POST", "/b"): 200})
        with requests.Session() as session:
            session.get("http://x/a")
        requests.post("http://x/b", json={})
        self.assertEqual([call.path for call in self.platform.calls], ["/a", "/b"])

    def test_nothing_sleeps_and_time_moves_when_something_does(self):
        before = time.time()
        time.sleep(100)
        self.assertGreaterEqual(time.time() - before, 100)
        self.assertEqual(self.clock.sleeps, [100])

    def test_a_test_sets_and_unsets_settings_and_writes_seed_files(self):
        self.env(APIM_TOKEN="t", APIM_PASSWORD=None)
        self.assertEqual((os.environ["APIM_TOKEN"], "APIM_PASSWORD" in os.environ), ("t", False))
        self.seed_files({"apim/applications/a.yaml": "name: A\n"})
        self.assertEqual([p.name for p in common.files("apim", "applications")], ["a.yaml"])
        self.assertEqual(common.load(common.files("apim", "applications")[0]), {"name": "A"})

    def test_a_response_says_whether_it_is_an_error(self):
        with self.assertRaises(requests.HTTPError):
            Response(500).raise_for_status()
        Response(204).raise_for_status()
        self.assertEqual(Platform({("GET", "/a"): 200}).routes, {("GET", "/a"): 200})


if __name__ == "__main__":
    unittest.main()
