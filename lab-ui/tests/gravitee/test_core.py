"""core: where API Management runs, kept in .lab/core.json: This machine by default, or a linked environment of a registered
Gravitee Cloud account, refused otherwise and This machine again once it is gone; what Start all and Stop all do with API
Management in the Cloud (its gateway, the rest of the platform with what it depends on, what is on; never a service of API
Management or one that exists only for it) and what the state says of it. Against the fake Cloud, in a temporary project
folder. No network, no Docker. A token is never in the file or an answer."""

import copy
import json
import unittest
from unittest import mock

from lab_ui.gravitee import core, places, platforms
from lab_ui.library import layers
from lab_ui import paths

from .. import fake_cloud
from ..library import test_layers
from .test_platforms import DEV, TEST
from .test_places import DEVELOPMENT, Places

PLACE = f"my-account/{DEV}"
RUNNING = {"state": "running"}


def project():
    """The small project of test_layers with the Hybrid gateway of Cloud and Hotels, whose app deploys there, shaped as the
    platform is: API Management (whose plugins folder is apim) needs MongoDB and Elasticsearch, Access Management needs
    MongoDB, the Alert Engine needs nothing. Kiosk needs MongoDB; Kafka UI needs API Management's Management API."""
    found = test_layers.lab()
    test_layers.licensed(found, True)
    found["groups"][0]["product"] = "apim"
    test_layers.cloud(found)
    databases = next(group for group in found["groups"] if group["id"] == "databases")
    databases["services"].append("elasticsearch")
    found["services"]["elasticsearch"] = {**found["services"]["mongodb"], "label": "elasticsearch",
                                          "owner": {"kind": "platform", "id": "databases", "name": "Databases"}}
    owner = {"kind": "platform", "id": "access-management", "name": "Access Management"}
    found["services"]["am-gateway"] = {**found["services"]["mongodb"], "label": "am-gateway", "owner": owner,
                                       "depends_on": ["mongodb"]}
    found["groups"].insert(1, {**databases, "id": "access-management", "name": "Access Management",
                               "services": ["am-gateway"]})
    found["services"]["apim-management-api"]["depends_on"] = ["mongodb", "elasticsearch"]
    found["services"]["apim-gateway"]["depends_on"] = ["apim-management-api", "mongodb"]
    found["services"]["kiosk"]["depends_on"] = ["mongodb"]
    found["services"]["kafka-ui"]["depends_on"] = ["kafka", "apim-management-api"]
    return found


PROJECT = project()
STATE = test_layers.on(PROJECT, explicit=["kafka", "kiosk"], playground="hotels")


class Core(Places):
    """My Account registered with the fake Cloud, read once it is asked."""

    def stored(self):
        return json.loads((self.root / core.FILE).read_text(encoding="utf-8"))

    def link(self, environment=DEV):
        self.keep(fake_cloud.cloud_token(environment), environment)

    def gateway(self, environment=DEV):
        """A hybrid gateway of the Lab in an environment; the platform read again, as the page has it after the action."""
        made = self.create(environment, "lab", platform="my-account")["gateway"]
        places.invalidate("my-account")
        return made


class Choosing(Core):
    def test_this_machine_is_the_core_until_another_is_chosen(self):
        self.assertEqual(core.current(PROJECT), {"place": "machine", "chosen": False, "environment": ""})
        self.assertFalse((self.root / core.FILE).exists())
        self.assertEqual(core.describe(PROJECT, STATE, {}, None, None),
                         {"place": "machine", "chosen": False, "group": {"id": "api-management", "name": "API Management"}})

    def test_this_machine_chosen_is_kept_as_chosen(self):
        self.assertEqual(core.choose(PROJECT, "machine"), {"place": "machine", "name": None})
        self.assertEqual(self.stored(), {"place": "machine", "chosen": True})
        self.assertTrue(core.current(PROJECT)["chosen"])

    def test_an_environment_the_lab_is_not_linked_to_is_refused_before_anything_is_kept(self):
        with self.assertRaisesRegex(ValueError, rf"^Set up {DEVELOPMENT.replace('(', '[(]').replace(')', '[)]')} first$"):
            core.choose(PROJECT, PLACE)
        self.assertFalse((self.root / core.FILE).exists())

    def test_a_cloud_token_links_an_environment_and_it_is_kept_with_its_name_and_no_secret(self):
        self.link()
        self.assertEqual(core.choose(PROJECT, PLACE), {"place": PLACE, "name": DEVELOPMENT})
        self.assertEqual(self.stored(), {"place": PLACE, "chosen": True, "environment": "Development"})
        self.assertHidden(self.stored())
        self.assertEqual(core.current(PROJECT)["place"], PLACE)
        self.assertEqual(core.name(PROJECT), DEVELOPMENT)

    def test_a_hybrid_gateway_of_the_lab_links_an_environment_too(self):
        self.gateway(TEST)
        self.assertEqual(core.choose(PROJECT, f"my-account/{TEST}")["name"], "Test (My Account)")

    def test_what_is_not_an_environment_of_a_registered_account(self):
        for place, error in [("", ValueError), (42, ValueError), ("my-account", ValueError), ("someone/dev", ValueError),
                             ("my-account/nowhere", paths.NotFound)]:
            with self.subTest(place=place), self.assertRaises(error):
                core.choose(PROJECT, place)
        self.assertFalse((self.root / core.FILE).exists())

    def test_an_account_that_cannot_be_read_says_why(self):
        self.link()
        self.patch(platforms, "fetch", return_value={"state": "invalid", "message": "Refused", "environments": [],
                                                       "gateways": []})
        with self.assertRaisesRegex(ValueError, "^Invalid token$"):
            core.choose(PROJECT, PLACE)

    def test_a_core_whose_account_is_gone_is_this_machine_again(self):
        self.link()
        core.choose(PROJECT, PLACE)
        platforms.remove("my-account")
        self.assertEqual(core.current(PROJECT), {"place": "machine", "chosen": True, "environment": ""})

    def test_a_file_that_says_nothing_is_this_machine(self):
        for text in ["", "[]", '{"place": 3}', "not json"]:
            with self.subTest(text=text):
                self.write(core.FILE, text)
                self.assertEqual(core.current(PROJECT)["place"], "machine")


class StartAll(Core):
    def setUp(self):
        super().setUp()
        self.link()
        core.choose(PROJECT, PLACE)

    def planned(self, containers=None, state=STATE):
        with mock.patch.object(core.layers, "plan", wraps=layers.plan) as plan:
            found = core.plan(PROJECT, state, containers or {}, None)
        return found, plan

    def test_without_a_gateway_it_says_to_set_one_up(self):
        with self.assertRaisesRegex(ValueError, r"^Set up a gateway in Development \(My Account\) first$"):
            core.plan(PROJECT, STATE, {}, None)
        described = core.describe(PROJECT, STATE, {}, None, None)
        self.assertEqual(described["start"], {"needed": True, "enabled": False,
                                              "reason": "Set up a gateway in Development (My Account) first"})

    def test_the_group_that_moves_is_the_one_of_the_product_of_the_gateways_scope(self):
        self.assertEqual(core.group(PROJECT)["name"], "API Management")
        self.assertEqual(core.describe(PROJECT, STATE, {}, None, None)["group"], {"id": "api-management", "name": "API Management"})
        core.choose(PROJECT, "machine")
        self.assertEqual(core.describe(PROJECT, STATE, {}, None, None),
                         {"place": "machine", "chosen": True, "group": {"id": "api-management", "name": "API Management"}})

    def test_it_starts_the_gateway_the_rest_of_the_platform_and_what_is_on_but_api_management(self):
        made = self.gateway()
        found, plan = self.planned()
        where = plan.call_args.kwargs["place"]
        self.assertEqual((plan.call_args.args[4:6], where["id"], where["gateway"]), (("hybrid", "start"), PLACE, "hybrid"))
        self.assertEqual(found["gateway_id"], made["id"])
        self.assertEqual(found["others"], ["alert-engine", "am-gateway", "hotels-api", "kafka", "kiosk", "mongodb"],
                         "the products that stay, MongoDB that one of them and Kiosk use, the playground's app and what is on;"
                         " never its seed")
        self.assertEqual([row["name"] for row in found["dialog"]["starts"]],
                         ["Hybrid gateway", "Access Management", "mongodb", "Alert Engine", "Hotels apps", "Kiosk", "Kafka broker"])
        self.assertEqual(found["dialog"]["serves"], [{"kind": "environment", "id": PLACE, "name": DEVELOPMENT}])
        self.assertEqual([row["name"] for row in found["dialog"]["not_started"]], ["API Management", "elasticsearch", "Kafka UI"],
                         "API Management, Elasticsearch that only it uses, and Kafka UI that needs it")
        self.assertEqual(found["dialog"]["hints"], {"not_started": "API Management runs in Gravitee Cloud"})
        named = {row["id"] for part in layers.ROWS for row in found["dialog"][part]}
        self.assertFalse(named & {"apim-gateway", "apim-management-api", "elasticsearch", "api-management", "platform"},
                         "nothing of API Management starts")
        self.assertTrue(found["needed"])

    def test_where_the_gateway_comes_from_is_said_with_its_version_beside_what_is_not_started(self):
        # The add-on of the gateway comes from a catalog that is not the first: the row From, and under it the version, which the
        # hints of this dialog (why API Management does not start) must not take the place of
        self.gateway()
        found = copy.deepcopy(PROJECT)
        found["addons"]["hybrid"]["origin"] = {"id": "acme-hub", "name": "Acme Hub", "version": "1.4.0"}
        dialog = core.plan(found, STATE, {}, None)["dialog"]
        self.assertEqual(dialog["from"], [{"kind": "catalog", "id": "acme-hub", "name": "Acme Hub"}])
        self.assertEqual(dialog["hints"], {"from": "Version 1.4.0", "not_started": "API Management runs in Gravitee Cloud"},
                         "the version of the catalog is kept beside the hint that says what is not started")

    def test_the_hints_are_what_the_gateways_plan_carries_and_the_dialog_adds_to_them_never_the_other_way(self):
        self.gateway()
        found = copy.deepcopy(PROJECT)
        found["addons"]["hybrid"]["origin"] = {"id": "acme-hub", "name": "Acme Hub", "version": "1.4.0"}
        # A hint of the gateway's plan that this dialog also says is the dialog's; the others are the plan's
        said = layers.dialog
        with mock.patch.object(layers, "dialog", side_effect=lambda plan: {
                **said(plan), "hints": {"not_started": "old words", "from": "Version 1.4.0"}}):
            dialog = core.plan(found, STATE, {}, None)["dialog"]
        self.assertEqual(dialog["hints"], {"from": "Version 1.4.0", "not_started": "API Management runs in Gravitee Cloud"})
        # Nothing to say of the catalog, nothing is added: the first catalog's items say no origin
        self.assertEqual(core.plan(PROJECT, STATE, {}, None)["dialog"]["hints"],
                         {"not_started": "API Management runs in Gravitee Cloud"})
        self.assertNotIn("from", core.plan(PROJECT, STATE, {}, None)["dialog"])

    def test_a_database_that_only_api_management_uses_is_not_started_with_nothing_else_on(self):
        self.gateway()
        found, _ = self.planned(state=test_layers.on(PROJECT))
        self.assertEqual(found["others"], ["alert-engine", "am-gateway", "mongodb"])
        self.assertEqual([row["name"] for row in found["dialog"]["not_started"]], ["API Management", "elasticsearch"])

    def test_without_a_usable_license_what_needs_one_is_neither_started_nor_named(self):
        self.gateway()
        unlicensed = {**PROJECT, "license_usable": False}
        found = core.plan(unlicensed, test_layers.on(unlicensed), {}, None)
        self.assertNotIn("alert-engine", found["others"])
        self.assertNotIn("Alert Engine", [row["name"] for part in ("starts", "not_started") for row in found["dialog"][part]])

    def test_what_runs_is_not_started_again(self):
        self.gateway()
        running = ("apim-hybrid-gateway", "hybrid-redis", "hotels-api", "kafka", "kiosk", "mongodb", "am-gateway", "alert-engine")
        found, _ = self.planned({name: RUNNING for name in running})
        self.assertEqual((found["others"], found["needed"]), ([], False))

    def test_stop_all_stops_what_start_all_starts_and_leaves_api_management(self):
        running = {name: RUNNING for name in ("apim-gateway", "elasticsearch", "mongodb", "am-gateway", "apim-hybrid-gateway",
                                              "kafka", "kafka-ui")}
        self.assertEqual(core.stopping(PROJECT, STATE, running),
                         ["am-gateway", "apim-hybrid-gateway", "kafka", "kafka-ui", "mongodb"])
        self.assertTrue(core.describe(PROJECT, STATE, running, None, None)["stop"]["needed"])
        self.assertEqual(core.stopping(PROJECT, STATE, {"apim-gateway": RUNNING, "elasticsearch": RUNNING}), [])

    def test_the_state_names_the_environment_and_waits_with_everything(self):
        self.gateway()
        described = core.describe(PROJECT, STATE, {}, None, "Wait for Start the stack")
        self.assertEqual({key: described[key] for key in ("place", "chosen", "name", "platform", "environment")},
                         {"place": PLACE, "chosen": True, "name": DEVELOPMENT, "platform": {"id": "my-account", "name": "My Account"},
                          "environment": {"id": DEV, "name": "Development"}})
        self.assertEqual(described["start"], {"needed": True, "enabled": False, "reason": "Wait for Start the stack"})
        self.assertHidden(described)


if __name__ == "__main__":
    unittest.main()
