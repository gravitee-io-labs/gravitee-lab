"""compat.py, the measure of an image against the tables of versions.py: no other test covers it. What it decides without a
platform: which answer says a route is served, what each probe is held to, and that each behavior of the tables has its
probe."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compat  # noqa: E402
from lab_seeder import versions  # noqa: E402

AMP_DEMO = "4.13.0-amp-demo-SNAPSHOT"


class Served(unittest.TestCase):
    def test_the_frameworks_own_404_and_any_405_say_the_route_is_not_served(self):
        self.assertFalse(compat.served((404, {"message": "HTTP 404 Not Found", "http_status": 404})))
        self.assertFalse(compat.served((405, {"message": "HTTP 405 Method Not Allowed"})))
        self.assertFalse(compat.served((0, "connection refused")))

    def test_the_404_of_an_object_that_is_not_there_and_a_refused_body_say_it_is(self):
        self.assertTrue(compat.served((404, {"message": "AI Workspace not found.", "http_status": 404})))
        self.assertTrue(compat.served((400, {"message": "must not be null"})))
        self.assertTrue(compat.served((200, [])))


class Expected(unittest.TestCase):
    def test_a_release_is_held_to_the_variant_of_its_rows(self):
        self.assertEqual(compat.expected(versions.APIM, "4.13.5", "governance"), {"absent"})
        self.assertEqual(compat.expected(versions.APIM, "4.13.5", "mcp-protocol-versions"), {"list"})

    def test_a_build_is_held_to_where_it_differs(self):
        self.assertEqual(compat.expected(versions.APIM, AMP_DEMO, "governance"), {"served"})
        self.assertEqual(compat.expected(versions.APIM, AMP_DEMO, "mcp-protocol-versions"), {"single"})
        self.assertEqual(compat.expected(versions.APIM, "4.14.0-alpha.1", "studio-human-approval"), {"switch"})
        self.assertEqual(compat.expected(versions.APIM, "4.14.0-alpha.1", "governance"), {"absent"})

    def test_a_version_the_tables_do_not_seed_is_the_finding(self):
        [said] = compat.expected(versions.APIM, "4.15.0-SNAPSHOT", "governance")
        self.assertIn("neither a release nor a build", said)

    def test_a_shape_is_held_to_those_the_seeder_reads(self):
        self.assertEqual(compat.expected(versions.APIM, "4.13.5", "authz-read-back"), {"as written", "without active"})


class Platform:
    """A management API that serves the routes `served` says, and gives a studio the flows `flows` says once human approval is
    on. It notes what it is sent."""

    def __init__(self, served=lambda path: True, flows=()):
        self.served, self.flows, self.studio, self.sent = served, flows, None, []

    def call(self, method, path, body=None):
        self.sent.append((method, path, body))
        if not self.served(path):
            return 404, {"message": "HTTP 404 Not Found"}
        if method == "POST" and path.endswith("/mcp-studios"):
            return 200, {"id": "studio-1"}
        if method == "GET" and path.endswith("/apis/studio-1"):
            return 200, {"flows": [{"name": name} for name in self.flows]}
        return 200, []


class Probes(unittest.TestCase):
    def test_governance_is_absent_where_only_some_of_its_routes_are_served(self):
        rules = {path for _, path, name in compat.routes_of(compat.BEHAVIOR_ROUTES["governance"]) if "approval-rules" in name}
        for served, wanted in ((lambda path: True, "served"), (lambda path: False, "absent"), (lambda path: path in rules, "absent")):
            with self.subTest(wanted=wanted):
                api = Platform(served)
                self.assertEqual(compat.governance(api).measured, wanted)
        self.assertIn("without GET aim /approval-settings", compat.governance(api).notes)

    def test_human_approval_on_a_studio_is_measured_by_where_its_step_goes(self):
        for flows, wanted in ((["Human approval"], "served"), ([], "switch")):
            with self.subTest(wanted=wanted):
                api = Platform(flows=flows)
                api.studio = None
                self.assertEqual(compat.studio_human_approval(api).measured, wanted)
                switched = [body for method, path, body in api.sent if path.endswith("/studio-1/human-approval")]
                self.assertEqual(switched, [{"enabled": True}, {"enabled": False}])
        api = Platform(lambda path: "human-approval" not in path)
        self.assertEqual(compat.studio_human_approval(api).measured, "absent")


class Coverage(unittest.TestCase):
    def test_each_behavior_of_the_tables_has_a_probe_of_its_product(self):
        probes = {product: {name for name, _ in found} for product, found in compat.PROBES.items()}
        missing = [name for name, behavior in versions.RELEASES.items() if name not in probes[behavior.product]]
        self.assertEqual(missing, [])

    def test_a_probe_is_either_a_behavior_of_the_tables_or_a_shape_the_seeder_reads(self):
        names = [name for found in compat.PROBES.values() for name, _ in found]
        self.assertEqual([n for n in names if (n in versions.RELEASES) == (n in compat.READS)], [])
        self.assertEqual(len(names), len(set(names)))


class Routes(unittest.TestCase):
    def test_each_route_names_a_method_a_base_and_a_path(self):
        for text in (compat.ROUTES, *compat.BEHAVIOR_ROUTES.values()):
            for method, path, name in compat.routes_of(text):
                self.assertIn(method, ("GET", "POST", "PUT", "DELETE"), name)
                self.assertNotIn("/X", path.replace(compat.UNKNOWN, ""), name)

    def test_a_route_is_in_one_list_and_a_behavior_list_is_a_behavior_of_the_tables(self):
        names = [name for text in (compat.ROUTES, *compat.BEHAVIOR_ROUTES.values()) for _, _, name in compat.routes_of(text)]
        self.assertEqual(len(names), len(set(names)))
        self.assertLessEqual(set(compat.BEHAVIOR_ROUTES), set(versions.RELEASES))


class Seeded(unittest.TestCase):
    def test_a_version_the_seeder_refuses_is_said_whatever_the_product(self):
        self.assertEqual(compat.seeded(versions.AM, "4.13.6"), "seeded")
        self.assertIn("the seeder fills 4.12.0 and later", compat.seeded(versions.AM, "4.11.9"))
        self.assertIn("neither a release nor a build", compat.seeded(versions.AM, "4.15.0-SNAPSHOT"))


class Stack(unittest.TestCase):
    def test_an_image_has_the_same_short_name_on_every_run(self):
        name = compat.Stack("graviteeio/apim-management-api:4.13.5").name
        self.assertEqual(name, compat.Stack("graviteeio/apim-management-api:4.13.5").name)
        self.assertLessEqual(len(name + "-mongo"), 63)   # a container name is a host name on its network

    def test_the_product_follows_the_image(self):
        self.assertEqual(compat.Stack("graviteeio.azurecr.io/am-management-api:master-latest").product, versions.AM)
        self.assertEqual(compat.Stack("graviteeio/apim-management-api:4.13.5").product, versions.APIM)


if __name__ == "__main__":
    unittest.main()
