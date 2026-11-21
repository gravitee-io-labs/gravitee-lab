"""Edge's singleton configuration: create and save through its existing API, preserve omitted settings, refuse immutable
URL changes, repeat without writes and retain the environment's shared configuration on playground removal. No other
module tests Edge's configuration API or its ordered interception routes."""

import copy
import json

from lab_seeder import common, edge
from lab_seeder.testkit import Seed

PATH = "/gamma/organizations/DEFAULT/environments/DEFAULT/modules/edge/configuration"
URLS = {"gatewayUrl": "https://gateway.example", "reactorUrl": "https://reactor.example"}
PROXY = {"dnsDomains": [{"name": "api.example"}], "routes": [
    {"pathPrefix": "/v1/messages", "apiPath": "/interception/claude", "provider": "anthropic"},
    {"pathPrefix": "/", "apiPath": "/interception/passthrough"}]}


class Configuration(Seed):
    def setUp(self):
        super().setUp()
        self.live = None
        self.answer({("GET", PATH): self.read, ("PUT", PATH): self.save})

    def read(self, call):
        return (404, {}) if self.live is None else (200, copy.deepcopy(self.live))

    def save(self, call):
        self.live = copy.deepcopy(call.json)
        return 200, self.live

    def spec(self, value):
        self.seed_files({"edge/configuration.yaml": json.dumps(value)})

    def test_no_file_needs_no_settings_or_connection(self):
        self.env(APIM_MANAGEMENT_URL=None, APIM_USERNAME=None, APIM_PASSWORD=None)
        edge.seed()
        self.assertEqual(self.platform.calls, [])

    def test_create_defaults_deploy_and_second_run(self):
        self.spec({**URLS, "proxy": PROXY})
        edge.seed()
        self.assertEqual(self.live, {**URLS, "proxy": PROXY, "shadowAi": edge.DEFAULTS["shadowAi"]})
        self.assertEqual(len(self.platform.sent("PUT", PATH)), 1)
        self.assertEqual(common.created(), [])
        self.platform.calls.clear()
        edge.seed()
        self.assertEqual(self.platform.changes(), [])
        self.assertEqual(edge.DEFAULTS["proxy"], {"dnsDomains": [], "routes": []})

    def test_update_preserves_omitted_settings_and_route_order_matters(self):
        self.live = {**URLS, "proxy": copy.deepcopy(PROXY), "shadowAi": {"domains": [{"name": "api.example"}],
                                                                              "reportInterval": "PT30S"}, "id": "config-1"}
        self.spec({"proxy": {"routes": list(reversed(PROXY["routes"]))}})
        edge.seed()
        self.assertEqual(self.live["proxy"]["routes"], list(reversed(PROXY["routes"])))
        self.assertEqual(self.live["proxy"]["dnsDomains"], PROXY["dnsDomains"])
        self.assertEqual(self.live["shadowAi"]["reportInterval"], "PT30S")
        self.assertEqual(self.live["id"], "config-1")
        self.spec({"proxy": {"routes": [], "dnsDomains": []}, "shadowAi": {"domains": [], "reportInterval": "PT10S"}})
        edge.seed()
        self.assertEqual(self.live["proxy"], {"routes": [], "dnsDomains": []})
        self.assertEqual(self.live["shadowAi"], {"domains": [], "reportInterval": "PT10S"})
        self.platform.calls.clear()
        edge.seed()
        self.assertEqual(self.platform.changes(), [])

    def test_equivalent_reporting_intervals_need_no_write_after_platform_normalization(self):
        self.live = {**URLS, **copy.deepcopy(edge.DEFAULTS)}
        self.spec({"shadowAi": {"reportInterval": "PT120S"}})
        edge.seed()
        self.assertEqual(self.platform.changes(), [])

    def test_defaults_added_to_route_objects_do_not_cause_repeat_saves(self):
        self.live = {**URLS, "proxy": copy.deepcopy(PROXY), "shadowAi": copy.deepcopy(edge.DEFAULTS["shadowAi"])}
        self.live["proxy"]["routes"][1]["provider"] = None
        self.spec({"proxy": PROXY})
        edge.seed()
        self.assertEqual(self.platform.changes(), [])
        self.assertIn("provider", self.live["proxy"]["routes"][1])

    def test_creation_needs_both_urls_but_updates_can_omit_them(self):
        for value in ({}, {"gatewayUrl": URLS["gatewayUrl"]}):
            with self.subTest(value=value):
                self.spec(value)
                with self.assertRaisesRegex(common.SeedError, "gatewayUrl and reactorUrl"):
                    edge.seed()
                self.assertEqual(self.platform.changes(), [])
        self.live = copy.deepcopy(URLS)
        self.spec({})
        edge.seed()
        self.assertEqual(self.platform.changes(), [])

    def test_an_immutable_url_change_stops_without_recreating_anything(self):
        self.live = {**URLS, **copy.deepcopy(edge.DEFAULTS)}
        for key in URLS:
            with self.subTest(key=key):
                self.spec({key: "https://another.example", "proxy": PROXY})
                with self.assertRaisesRegex(common.SeedError, key + " cannot change"):
                    edge.seed()
                self.assertEqual(self.platform.changes(), [])

    def test_validation_names_bad_fields_before_any_connection(self):
        for value, field in (({"unknown": True}, "configuration"), ({"proxy": []}, "proxy"),
                             ({"gatewayUrl": "ftp://gateway.example"}, "gatewayUrl"),
                             ({"reactorUrl": "https://user:demo@reactor.example"}, "reactorUrl"),
                             ({"proxy": {"dnsDomains": ["api.example"]}}, "dnsDomains"),
                             ({"proxy": {"routes": [{"pathPrefix": "/"}]}}, "apiPath"),
                             ({"proxy": {"routes": [{"pathPrefix": "bad", "apiPath": "/"}]}}, "pathPrefix"),
                             ({"shadowAi": {"domains": [{}]}}, "domains"),
                             ({"shadowAi": {"reportInterval": "PT9S"}}, "reportInterval"),
                             ({"shadowAi": {"reportInterval": "PT"}}, "reportInterval"),
                             ({"shadowAi": {"reportInterval": 120}}, "reportInterval"),
                             ({"shadowAi": {"reportInterval": "PT" + "9" * 5000 + "S"}}, "reportInterval"),
                             ({"shadowAi": {"reportInterval": "PT9223372036854775808S"}}, "reportInterval")):
            with self.subTest(value=value):
                self.spec(value)
                with self.assertRaisesRegex(common.SeedError, field):
                    edge.seed()
                self.assertEqual(self.platform.calls, [])

    def test_an_unexpected_success_body_is_not_treated_as_a_missing_configuration(self):
        self.spec(URLS)
        for value in (None, 42, ["not an object"]):
            with self.subTest(value=value):
                self.platform.add({("GET", PATH): (200, value)})
                with self.assertRaisesRegex(common.SeedError, "platform did not return an object"):
                    edge.seed()
                self.assertEqual(self.platform.changes(), [])

    def test_use_the_configured_environment_gamma_endpoint_and_seed_login(self):
        path = PATH.replace("DEFAULT/environments/DEFAULT", "acme/environments/preview")
        self.env(APIM_ORG="acme", APIM_ENV="preview", AIM_GAMMA_URL="https://gamma.example")
        self.answer({("GET", path): self.read, ("PUT", path): self.save})
        self.spec(URLS)
        edge.seed()
        [write] = self.platform.sent("PUT", path)
        self.assertEqual(write.url, "https://gamma.example" + path)
        self.assertEqual(write.auth, ("admin", "admin"))

    def test_removal_keeps_the_shared_configuration_without_a_request(self):
        edge.remove([])
        self.assertEqual(self.platform.calls, [])
