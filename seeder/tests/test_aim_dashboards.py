"""The custom dashboard of Acme Bakery, from its real seed file, against a platform that stores it as the console's API does:
created once, left alone by a second run, put back when someone edited it, and removed. The file itself is held to what
the console accepts of a dashboard: what it draws and how."""

import json
import re
import unittest

import yaml

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

    def test_it_opens_on_the_last_hour(self):
        self.seed()
        self.assertEqual({k: v for k, v in self.the_dashboard()["timeRange"].items() if v}, {"type": "relative", "period": "1h"})

    def test_it_is_sent_without_the_notes_of_the_file(self):
        self.seed()
        [call] = self.platform.sent("POST", OBSERVABILITY + "/dashboards")
        self.assertEqual(sorted(call.json), ["description", "filters", "id", "timeRange", "title", "widgets"])

    def test_the_viewer_can_pick_an_api_and_nothing_is_locked(self):
        self.seed()
        self.assertEqual(self.the_dashboard()["filters"],
                         [{"name": "API", "label": "API", "operator": "IN", "value": [], "editable": True}])


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

    def test_a_title_someone_changed_is_put_back(self):
        self.the_dashboard()["title"] = "Mine"
        self.seed()
        self.assertEqual(self.the_dashboard()["title"], TITLE)

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


class TheFile(unittest.TestCase):
    """What the console demands of a dashboard it opens: it refuses one it cannot draw, and draws a layout without a
    version as the older 12-column grid."""

    TYPES = {"cartesian", "doughnut", "bar", "breakdown", "metric", "metric-group", "table"}
    OPERATORS = {"eq", "in", "lte", "gte", "contains"}
    PERIODS = {"5m", "15m", "1h", "6h", "12h", "24h", "7d", "30d", "90d", "180d", "365d"}

    def setUp(self):
        self.dashboard = yaml.safe_load(FILE.read_text(encoding="utf-8"))
        self.widgets = self.dashboard["widgets"]

    def test_it_has_what_the_platform_needs_to_store_it(self):
        self.assertEqual((self.dashboard["id"], self.dashboard["title"]), (ID, TITLE))
        self.assertLessEqual(len(self.widgets), 50)
        self.assertLess(len(json.dumps(self.dashboard)), 1_000_000)

    def test_every_widget_has_its_own_id_a_known_type_and_a_title(self):
        ids = [w["id"] for w in self.widgets]
        self.assertEqual(len(ids), len(set(ids)))
        for widget in self.widgets:
            with self.subTest(widget=widget["id"]):
                self.assertIn(widget["type"], self.TYPES)
                self.assertIsInstance(widget["title"], str)

    def test_every_widget_sits_on_the_24_column_grid_of_the_current_layout(self):
        for widget in self.widgets:
            with self.subTest(widget=widget["id"]):
                layout = widget["layout"]
                self.assertEqual(layout["version"], 2)
                self.assertTrue(all(isinstance(layout[k], int) for k in ("x", "y", "cols", "rows")))
                self.assertTrue(layout["x"] >= 0 and layout["y"] >= 0 and layout["cols"] >= 1 and layout["rows"] >= 1)
                self.assertLessEqual(layout["x"] + layout["cols"], 24)

    def test_no_widget_overlaps_another(self):
        cells = {}
        for widget in self.widgets:
            layout = widget["layout"]
            for x in range(layout["x"], layout["x"] + layout["cols"]):
                for y in range(layout["y"], layout["y"] + layout["rows"]):
                    self.assertNotIn((x, y), cells, f"{widget['id']} overlaps {cells.get((x, y))}")
                    cells[(x, y)] = widget["id"]

    def test_every_filter_is_one_the_console_can_send(self):
        filters = [f for w in self.widgets for f in w.get("filters", [])]
        self.assertTrue(filters)
        for f in filters:
            with self.subTest(filter=f["field"]):
                self.assertIn(f["operator"], self.OPERATORS)
                self.assertTrue(all(isinstance(v, str) for v in f["value"]))
                self.assertTrue(f["value"], "a filter without a value is dropped by the console")

    def test_a_widget_has_no_two_filters_on_one_field(self):
        for widget in self.widgets:
            fields = [f["field"] for f in widget.get("filters", [])]
            self.assertEqual(len(fields), len(set(fields)), widget["id"])

    def test_the_time_range_is_one_the_console_offers(self):
        self.assertIn(self.dashboard["timeRange"]["period"], self.PERIODS)

    def test_the_dashboard_level_filters_are_written_as_the_platform_stores_them(self):
        for f in self.dashboard["filters"]:
            self.assertEqual(sorted(f), ["editable", "label", "name", "operator", "value"])
            self.assertEqual(f["operator"], f["operator"].upper())

    def test_what_a_widget_draws_is_named_the_way_the_console_reads_it(self):
        for widget in self.widgets:
            with self.subTest(widget=widget["id"]):
                kind = widget["type"]
                if kind == "metric-group":
                    self.assertTrue(all({"metric", "measure"} <= set(item) for item in widget["items"]))
                    self.assertEqual(len({item["metric"] for item in widget["items"]}), len(widget["items"]),
                                     "items of a group are found by their metric: two with one metric would show one value")
                elif kind == "metric":
                    self.assertTrue({"metric", "measure"} <= set(widget))
                elif kind == "cartesian":
                    keys = [(s["metric"], s["measure"], s["representation"]) for s in widget["series"]]
                    self.assertEqual(len(keys), len(set(keys)), "series are told apart by metric, measure and representation")
                elif kind == "table":
                    self.assertTrue(widget["by"] and widget["columns"])
                    self.assertIn(widget["sort"]["columnId"], [f"{c['metric']}:{c['measure']}" for c in widget["columns"]])
                else:
                    self.assertTrue(widget["by"] and widget["metric"] and widget["measure"])

    def test_a_cost_in_a_table_is_rounded_the_way_the_console_rounds_the_templates(self):
        """A sum of floats is shown as 0.28794000000000003 unless the column says how to format it."""
        for widget in (w for w in self.widgets if w["type"] == "table"):
            for column in widget["columns"]:
                if column["metric"] == "LLM_PROMPT_TOKEN_TOTAL_COST":
                    self.assertEqual(column.get("format"), "number", widget["id"])

    def test_it_asks_the_gateway_for_nothing_it_does_not_report(self):
        """Every metric and field of the file is one the observability API has: the authorization ones only on decisions."""
        asked = {s["metric"] for w in self.widgets for s in w.get("series", [])}
        asked |= {i["metric"] for w in self.widgets for i in w.get("items", [])} | {w["metric"] for w in self.widgets if "metric" in w}
        asked |= {c["metric"] for w in self.widgets for c in w.get("columns", [])}
        self.assertLessEqual({m for m in asked if not m.startswith("AUTHZ_")}, {
            "HTTP_REQUESTS", "HTTP_ERROR_RATE", "HTTP_GATEWAY_LATENCY", "LLM_PROMPT_TOTAL_TOKEN", "LLM_PROMPT_TOKEN_TOTAL_COST"})
        for widget in self.widgets:
            uses_decisions = any(m.startswith("AUTHZ_") for m in
                                 [widget.get("metric", "")] + [s["metric"] for s in widget.get("series", [])])
            fields = {f["field"]: f["value"] for f in widget.get("filters", [])}
            self.assertEqual(fields.get("RECORD_TYPE") == ["AUTHZ_DECISION"], uses_decisions, widget["id"])
            if uses_decisions:
                self.assertEqual(set(fields), {"RECORD_TYPE"}, "decisions take no other condition than their own")


if __name__ == "__main__":
    unittest.main()
