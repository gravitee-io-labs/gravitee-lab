"""The custom dashboard of Acme Bakery, from its real seed file, against a platform that stores it as the console's API does:
created once, left alone by a second run, put back when someone edited it, and removed."""

import re
import unittest

from lab_seeder import aim_dashboards
from lab_seeder import common
from lab_seeder.testkit import PLAYGROUNDS, Seed

OBSERVABILITY = "/gamma/organizations/DEFAULT/environments/DEFAULT/observability"
FILE = PLAYGROUNDS / "acme-bakery" / "seed" / "aim" / "dashboards" / "acme-bakery-ai-spend-and-safety.yaml"
ID = "acme-bakery-ai-spend-and-safety"
TITLE = "Acme Bakery - AI spend and safety"


class Console:
    """The dashboards API: a dashboard is stored as sent, with its version and dates; a filter's operator is written in
    capitals; a write over an existing dashboard needs If-Match (428 without); a duplicate id is a 500."""

    def __init__(self, module_required=False):
        self.dashboards, self.module_required = {}, module_required

    def routes(self):
        return {("GET", OBSERVABILITY + "/dashboards"): self.list,
                ("POST", OBSERVABILITY + "/dashboards"): self.create,
                ("GET", re.compile(OBSERVABILITY + "/dashboards/[^/]+")): self.read,
                ("PUT", re.compile(OBSERVABILITY + "/dashboards/[^/]+")): self.replace,
                ("DELETE", re.compile(OBSERVABILITY + "/dashboards/[^/]+")): self.delete}

    @staticmethod
    def stored(body, version, created=1):
        filters = [{**f, "operator": f["operator"].upper(), "editable": f.get("editable", False)} for f in body.get("filters", [])]
        return {**body, "filters": filters, "version": version, "createdAt": created, "updatedAt": created + version,
                "timeRange": {"from": None, "to": None, **body["timeRange"]}}

    def list(self, call):
        rows = list(self.dashboards.values())
        return 200, {"data": rows, "pagination": {"totalCount": len(rows)}}

    def create(self, call):
        if self.module_required and not call.json.get("module"):
            return 400, {"message": "Dashboard field 'module' is required", "technicalCode": "observability.dashboard.module_required"}
        if call.json["id"] in self.dashboards:
            return 500, {"message": "duplicate key"}
        self.dashboards[call.json["id"]] = self.stored(call.json, 1)
        return 201, self.dashboards[call.json["id"]]

    def read(self, call):
        found = self.dashboards.get(call.path.rsplit("/", 1)[1])
        return (200, found) if found else (404, {"message": "Dashboard not found"})

    def replace(self, call):
        dashboard_id = call.path.rsplit("/", 1)[1]
        if "If-Match" not in call.headers:
            return 428, {"message": "If-Match is required"}
        if dashboard_id not in self.dashboards:
            return 404, {"message": "Dashboard not found"}
        assert "id" not in call.json
        self.dashboards[dashboard_id] = {"id": dashboard_id, **self.stored(call.json, self.dashboards[dashboard_id]["version"] + 1)}
        return 200, self.dashboards[dashboard_id]

    def delete(self, call):
        self.dashboards.pop(call.path.rsplit("/", 1)[1], None)
        return 204


class Dashboard(Seed):

    def setUp(self):
        super().setUp()
        self.console = Console()
        self.platform.add(self.console.routes())
        self.seed_files({"aim/dashboards/acme-bakery-ai-spend-and-safety.yaml": FILE.read_text(encoding="utf-8")})

    def seed(self):
        aim_dashboards.seed()

    def the_dashboard(self):
        return self.console.dashboards[ID]


class FirstRun(Dashboard):

    def test_it_is_created_with_the_id_its_file_chooses_and_recorded(self):
        self.seed()
        [call] = self.platform.sent("POST", OBSERVABILITY + "/dashboards")
        self.assertEqual((call.json["id"], call.json["title"]), (ID, TITLE))
        [made] = common.created()
        self.assertEqual((made["product"], made["kind"], made["label"], made["name"], made["id"]),
                         ("aim-dashboards", "dashboard", "dashboards", TITLE, ID))

    def test_it_is_sent_without_the_notes_of_the_file(self):
        self.seed()
        [call] = self.platform.sent("POST", OBSERVABILITY + "/dashboards")
        self.assertEqual(sorted(call.json), ["description", "filters", "id", "timeRange", "title", "widgets"])


class ModuleRequired(Dashboard):
    """A build whose dashboards belong to a module of Gamma: it says so, and is asked again with the module."""

    def setUp(self):
        super().setUp()
        self.console.module_required = True

    def test_it_is_created_with_the_module_of_the_console_once_the_platform_asks_for_it(self):
        self.seed()
        sent = self.platform.sent("POST", OBSERVABILITY + "/dashboards")
        self.assertEqual([call.json.get("module") for call in sent], [None, "aim"])
        self.assertEqual(self.the_dashboard()["module"], "aim")

    def test_a_second_run_writes_nothing_and_an_edited_dashboard_is_put_back_with_its_module(self):
        self.seed()
        self.platform.calls.clear()
        self.seed()
        self.assertEqual(self.platform.changes(), [])
        self.console.dashboards[ID]["title"] = "Mine"
        self.seed()
        [call] = self.platform.sent("PUT", OBSERVABILITY + f"/dashboards/{ID}")
        self.assertEqual((call.json["title"], call.json["module"]), (TITLE, "aim"))

    def test_another_refusal_is_the_refusal(self):
        self.console.create = lambda call: (400, {"message": "Invalid query"})
        self.platform.add(self.console.routes())
        with self.assertRaises(aim_dashboards.HttpError):
            self.seed()


class SecondRun(Dashboard):

    def setUp(self):
        super().setUp()
        self.seed()
        self.platform.calls.clear()

    def test_nothing_is_written(self):
        self.seed()
        self.assertEqual(self.platform.changes(), [])
        self.assertEqual(len(common.created()), 1)

    def test_a_widget_someone_removed_is_put_back_over_whatever_revision_is_stored(self):
        self.the_dashboard()["widgets"].pop()
        self.the_dashboard()["version"] = 7
        self.seed()
        [call] = self.platform.sent("PUT", OBSERVABILITY + f"/dashboards/{ID}")
        self.assertEqual(call.headers["If-Match"], "*")
        self.assertEqual(len(self.the_dashboard()["widgets"]), len(call.json["widgets"]))
        self.assertEqual(self.the_dashboard()["version"], 8)
        self.assertEqual(self.platform.changes(), [("PUT", OBSERVABILITY + f"/dashboards/{ID}")])

    def test_a_dashboard_of_that_id_the_seed_did_not_create_is_never_adopted(self):
        common.save_created([])
        with self.assertRaisesRegex(common.SeedError, "acme-bakery-ai-spend-and-safety is the id of a dashboard the seed did not create"):
            self.seed()
        self.assertEqual(self.platform.changes(), [])

    def test_another_dashboard_is_never_touched(self):
        self.console.dashboards["my-dashboard"] = {"id": "my-dashboard", "title": "My dashboard"}
        self.seed()
        self.assertEqual(self.platform.changes(), [])
        self.assertEqual(self.console.dashboards["my-dashboard"], {"id": "my-dashboard", "title": "My dashboard"})


class Removal(Dashboard):

    def setUp(self):
        super().setUp()
        self.seed()
        self.platform.calls.clear()

    def test_the_dashboard_goes_and_nothing_else(self):
        self.console.dashboards["my-dashboard"] = {"id": "my-dashboard"}
        aim_dashboards.remove(common.created())
        self.assertEqual(list(self.console.dashboards), ["my-dashboard"])
        self.assertEqual(self.platform.changes(), [("DELETE", OBSERVABILITY + f"/dashboards/{ID}")])
        self.assertIn(f"✓ Deleted dashboard '{TITLE}'", self.output())

    def test_a_dashboard_that_is_gone_is_not_an_error(self):
        self.console.dashboards.clear()
        aim_dashboards.remove(common.created())

    def test_what_another_module_made_is_left_alone(self):
        aim_dashboards.remove([{"product": "aim-workspaces", "kind": "workspace", "name": "w", "id": "w", "label": ""}])
        self.assertEqual(self.platform.calls, [])


class Folder(Seed):

    def test_a_playground_without_dashboards_needs_no_setting_and_makes_no_call(self):
        self.env(APIM_MANAGEMENT_URL=None)
        aim_dashboards.seed()
        aim_dashboards.remove([])
        self.assertEqual(self.platform.calls, [])


if __name__ == "__main__":
    unittest.main()
