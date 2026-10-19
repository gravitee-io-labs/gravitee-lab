"""What a playground started on well on this machine: kept after a start, and taken for tested the next time it is started on it."""

import json
import unittest
from unittest import mock

from lab_ui.library import layers, tested
from lab_ui import paths

from .. import Case


def project(tag):
    gateway = {"raw_image": "graviteeio/apim-gateway:${APIM_VERSION:-4.12.20}", "tag": tag, "container": "gio-apim-gateway",
               "owner": {"name": "API Management"}}
    welcome = {"id": "welcome", "requires": {"images": {"APIM_VERSION": "4.13.0-alpha.3"}},
               "tested": [{"APIM_VERSION": "4.13.0-alpha.3"}]}
    return {"services": {"apim-gateway": gateway}, "playgrounds": {"welcome": welcome}}, welcome


class Kept(Case):
    def setUp(self):
        self.folder(paths)
        self.file = self.root / tested.FILE
        self.ids = {"gio-apim-gateway": "sha256:one"}
        self.patch(tested.compose, "image_key", lambda service: self.ids[service["container"]])
        self.patch(tested.compose, "CACHE", mock.Mock(get=lambda key, ttl, ask: ask()))

    def started(self, tag):
        found, welcome = project(tag)
        tested.record(found, "welcome")
        return found, welcome

    def test_a_start_that_ended_well_keeps_the_versions_it_was_on_and_the_image(self):
        self.started("4.13.0-alpha.7")
        [one] = tested.sets("welcome")
        self.assertEqual((one["versions"], one["images"]), ({"APIM_VERSION": "4.13.0-alpha.7"}, {"APIM_VERSION": "sha256:one"}))
        self.assertRegex(one["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(sorted(json.loads(self.file.read_text())), ["welcome"])

    def test_the_same_again_is_kept_once_and_the_latest_are_kept(self):
        self.started("4.13.0-alpha.7")
        self.started("4.13.0-alpha.7")
        self.assertEqual(len(tested.sets("welcome")), 1)
        for number in range(30):
            self.started(f"4.13.0-alpha.{number}")
        self.assertEqual(len(tested.sets("welcome")), tested.KEPT)
        self.assertEqual(tested.sets("welcome")[-1]["versions"], {"APIM_VERSION": "4.13.0-alpha.29"})

    def test_a_playground_that_names_no_image_has_run_and_has_no_version_to_name(self):
        found, welcome = project("4.13.0")
        welcome.update(requires={}, tested=[])
        self.assertFalse(tested.ran("welcome"))
        tested.record(found, "welcome")
        self.assertTrue(tested.ran("welcome"))
        self.assertEqual(tested.sets("welcome"), [], "nothing to judge a start on, nothing to declare")
        tested.record(found, "welcome")
        self.assertEqual(len(json.loads(self.file.read_text())["welcome"]), 1, "kept once")

    def test_a_playground_that_never_started_well_has_not_run(self):
        self.assertFalse(tested.ran("welcome"))
        self.started("4.13.0")
        self.assertTrue(tested.ran("welcome"))
        self.assertFalse(tested.ran("another"))

    def test_a_file_that_is_not_there_or_not_readable_says_nothing(self):
        self.assertEqual(tested.sets("welcome"), [])
        self.file.parent.mkdir(parents=True)
        for text in ("not json", "[]", '{"welcome": "x"}', '{"welcome": [1, {"versions": 2}]}'):
            self.file.write_text(text)
            self.assertEqual(tested.sets("welcome"), [], text)

    def test_what_worked_here_is_not_a_warning_the_next_time(self):
        # Not declared as tested, and it started well: starting it again on the same is not a warning
        found, welcome = self.started("4.13.0-alpha.7")
        self.assertIsNone(tested.untested(found, welcome))
        found, welcome = project("4.13.0-alpha.8")
        self.assertEqual([one["have"] for one in tested.untested(found, welcome)["images"]], ["4.13.0-alpha.8"])

    def test_a_tag_that_is_rebuilt_under_its_name_is_not_the_one_that_worked(self):
        found, welcome = self.started("amp-demo-latest-debian")
        self.assertIsNone(tested.untested(found, welcome))
        self.ids["gio-apim-gateway"] = "sha256:two"   # the tag moved
        self.assertEqual([one["have"] for one in tested.untested(found, welcome)["images"]], ["amp-demo-latest-debian"])
        self.ids["gio-apim-gateway"] = "sha256:one"
        self.assertIsNone(tested.untested(found, welcome))

    def test_a_release_is_the_same_whatever_the_image_is(self):
        found, welcome = self.started("4.13.7")
        self.ids["gio-apim-gateway"] = "sha256:two"
        self.assertIsNone(tested.untested(found, welcome))

    def test_the_registry_is_kept_and_must_be_the_same(self):
        found, welcome = project("4.13.0")
        found["services"]["apim-gateway"].update(raw_image="${APIM_REGISTRY:-graviteeio}/apim-gateway:${APIM_VERSION:-4.12.20}",
                                                 registry="graviteeio")
        welcome["tested"] = [{"APIM_VERSION": "4.13.0-alpha.3", "APIM_REGISTRY": "graviteeio"}]
        tested.record(found, "welcome")
        [one] = tested.sets("welcome")
        self.assertEqual((one["versions"], one["images"]), ({"APIM_VERSION": "4.13.0", "APIM_REGISTRY": "graviteeio"},
                                                           {"APIM_VERSION": "sha256:one"}))
        self.assertIsNone(tested.untested(found, welcome))
        found["services"]["apim-gateway"]["registry"] = "graviteeio.azurecr.io"
        self.assertIsNotNone(tested.untested(found, welcome))

    def test_what_worked_here_does_not_judge_a_playground_that_declares_nothing(self):
        found, welcome = self.started("4.13.0-alpha.7")
        welcome["tested"] = []
        self.assertIsNone(tested.untested(found, welcome), "it is not judged at all")
        self.assertIsNone(layers.untested(found, welcome, tested.sets("welcome"), tested.image_id))

    def test_the_file_is_private_and_replaced_whole(self):
        self.started("4.13.0-alpha.7")
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.name for p in self.file.parent.iterdir()], ["tested.json"], "no copy is left beside it")


class Moved(Case):
    """What started well follows a playground that becomes a catalog's, and goes with one that is deleted."""

    SETS = [{"versions": {"APIM_VERSION": "4.13.0"}, "images": {"APIM_VERSION": "sha256:one"}, "at": "2026-10-01T10:00:00Z"}]

    def setUp(self):
        self.folder(paths)
        self.write(tested.FILE, json.dumps({"local/bank": self.SETS, "other": self.SETS}))

    def saved(self):
        return json.loads((self.root / tested.FILE).read_text())

    def test_the_sets_follow_the_playground_to_its_new_id(self):
        tested.moved("playground", "local/bank", "@acme-hub/bank")
        self.assertEqual(self.saved(), {"@acme-hub/bank": self.SETS, "other": self.SETS})
        self.assertEqual(tested.sets("@acme-hub/bank"), self.SETS)
        self.assertEqual(tested.sets("local/bank"), [])

    def test_where_the_new_id_has_sets_the_two_are_joined_and_the_same_ones_kept_once(self):
        newer = [{"versions": {"APIM_VERSION": "4.13.1"}, "images": {"APIM_VERSION": "sha256:two"}, "at": "2026-10-02T10:00:00Z"}]
        self.write(tested.FILE, json.dumps({"local/bank": self.SETS, "bank": [*self.SETS, *newer]}))
        tested.moved("playground", "local/bank", "bank")
        self.assertEqual(self.saved(), {"bank": [*self.SETS, *newer]})

    def test_only_a_playground_has_tested_sets(self):
        tested.moved("addon", "local/bank", "bank")
        tested.moved("addon", "local/bank")
        self.assertEqual(self.saved(), {"local/bank": self.SETS, "other": self.SETS})

    def test_deleted_it_loses_them_and_the_others_stay(self):
        tested.moved("playground", "local/bank")
        self.assertEqual(self.saved(), {"other": self.SETS})
        tested.moved("playground", "local/bank")   # again: nothing to lose
        self.assertEqual(self.saved(), {"other": self.SETS})

    def test_a_file_that_is_not_there_or_does_not_read_is_not_written(self):
        (self.root / tested.FILE).unlink()
        tested.moved("playground", "local/bank", "bank")
        tested.moved("playground", "local/bank")
        self.assertFalse((self.root / tested.FILE).exists())
        self.write(tested.FILE, "not json")
        tested.moved("playground", "local/bank", "bank")
        tested.moved("playground", "local/bank")
        self.assertEqual((self.root / tested.FILE).read_text(), "not json")


if __name__ == "__main__":
    unittest.main()
