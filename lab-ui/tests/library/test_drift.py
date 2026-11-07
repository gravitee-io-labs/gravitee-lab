"""What differs between what Compose would give a service and what its container has: the settings, named; never valued,
asked once for a pair of hashes."""

import json

from lab_ui.docker import compose
from lab_ui.library import drift, reasons

from .. import Case

CONTAINER = {"name": "gio-engine", "config_hash": "old"}
SAVED = {"hashes": {"engine": "new"}}
RAW = {"docker-compose.yml": {"services": {"engine": {"environment": {
    "gravitee_license_key": "${GRAVITEE_LICENSE:-}", "plain": "literal", "both": "${ONE}${TWO}"}}}}}


class Changes(Case):
    def setUp(self):
        drift._found.clear()
        compose.CACHE.drop()
        self.addCleanup(compose.CACHE.drop)
        self.patch(drift.project, "config_key", return_value="k")
        self.resolved = self.patch(drift.compose, "resolved", return_value={"engine": {"environment": {
            "gravitee_license_key": "new-secret", "plain": "same", "both": "x", "unset": None}}})
        self.has = ["gravitee_license_key=old-secret", "plain=same", "PATH=/bin", "both=x"]
        self.run_ = self.patch(drift.compose, "run", side_effect=lambda *a, **k: (0, json.dumps(self.has), ""))

    def ask(self, raw=RAW):
        return drift.changes(raw, {"engine": CONTAINER}, {"engine": "config"}, SAVED)

    def test_a_setting_that_differs_is_named_by_the_setting_that_holds_it(self):
        self.assertEqual(self.ask(), {"engine": ["GRAVITEE_LICENSE"]})

    def test_a_dollar_that_is_no_variable_is_the_same_on_both_sides(self):
        # `compose config` writes it twice, the container has it once: it is no difference, whatever else changed
        self.resolved.return_value["engine"]["environment"]["gravitee_plugins_path_0"] = "$${gravitee.home}/plugins"
        self.has.append("gravitee_plugins_path_0=${gravitee.home}/plugins")
        self.assertEqual(self.ask(), {"engine": ["GRAVITEE_LICENSE"]})
        # and a value that does differ still does
        self.has[-1] = "gravitee_plugins_path_0=${gravitee.home}/other"
        drift._found.clear()
        compose.CACHE.drop()
        self.assertEqual(self.ask()["engine"], ["GRAVITEE_LICENSE", "gravitee_plugins_path_0"])

    def test_a_variable_set_in_the_file_is_named_as_it_is(self):
        self.has[1] = "plain=other"
        self.assertEqual(self.ask()["engine"], ["GRAVITEE_LICENSE", "plain"])

    def test_a_variable_that_two_settings_make_is_named_as_it_is(self):
        self.has[3] = "both=y"
        self.assertEqual(self.ask()["engine"], ["GRAVITEE_LICENSE", "ONE", "TWO"])

    def test_a_variable_that_a_changed_file_adds_is_no_setting_the_person_changed(self):
        # A Lab update adds REDISCLI_AUTH: ${REDIS_PASSWORD:-redispassword}; the container made before it has none of it
        self.has[0] = "gravitee_license_key=new-secret"
        self.resolved.return_value["engine"]["environment"]["REDISCLI_AUTH"] = "redispassword"
        raw = {"docker-compose.yml": {"services": {"engine": {"environment": {"REDISCLI_AUTH": "${REDIS_PASSWORD:-redispassword}"}}}}}
        why = reasons.why({"tag": "", "built": False}, {}, "config", self.ask(raw).get("engine", ()))
        self.assertEqual(reasons.line({"engine": why}), "Its configuration changed since it started.")

    def test_only_what_runs_behind_by_its_configuration_is_read(self):
        found = drift.changes(RAW, {"engine": CONTAINER}, {"engine": "image"}, SAVED)
        self.assertEqual(found, {})
        self.run_.assert_not_called()
        self.resolved.assert_not_called()

    def test_a_pair_of_hashes_is_read_once(self):
        self.ask()
        self.ask()
        self.assertEqual((self.resolved.call_count, self.run_.call_count), (1, 1))

    def test_what_cannot_be_read_is_asked_again_and_leaves_the_service_out(self):
        self.resolved.side_effect = compose.ComposeError("does not load")
        self.assertEqual(self.ask(), {})
        self.resolved.side_effect = None
        self.run_.side_effect = lambda *a, **k: (1, "", "No such container")
        self.assertEqual(self.ask(), {})
        self.run_.side_effect = lambda *a, **k: (0, json.dumps(self.has), "")
        self.assertEqual(self.ask(), {"engine": ["GRAVITEE_LICENSE"]})

    def test_no_value_is_ever_kept_or_said(self):
        self.ask()
        self.assertNotIn("secret", repr(drift._found) + repr(compose.CACHE._items))
