"""starred: what is starred on this machine, and how a star follows its item when it is renamed, becomes a catalog's or is
deleted, in the temporary project folder of test_personal. No Docker."""

import json
import unittest

from lab_ui import paths
from lab_ui.library import personal, starred

from .test_personal import Folder, WHOAMI


class Stars(Folder):

    def setUp(self):
        super().setUp()
        self.own()

    def saved(self):
        return json.loads(self.text(starred.FILE))

    def test_starred_and_unstarred(self):
        self.assertEqual(starred.read(), {"addon": set(), "setup": set(), "playground": set()})
        self.assertEqual(starred.star(self.project, "addon", "kafka", True), {})
        starred.star(self.project, "addon", "local/whoami", True)
        starred.star(self.project, "playground", "welcome", True)
        starred.star(self.project, "addon", "kafka", True)   # twice is once
        self.assertEqual(self.saved(), {"addon": ["kafka", "local/whoami"], "playground": ["welcome"]})
        starred.star(self.project, "playground", "welcome", False)
        starred.star(self.project, "setup", "observability", False)   # not starred: nothing changes
        self.assertEqual(self.saved(), {"addon": ["kafka", "local/whoami"]})

    def test_the_state_says_it_of_every_item(self):
        starred.star(self.project, "setup", "observability", True)
        state = {kind + "s": [{"id": uid} for uid in self.project[kind + "s"]] for kind in paths.KINDS}
        starred.mark(state)
        self.assertEqual({x["id"] for x in state["setups"] if x["starred"]}, {"observability"})
        self.assertFalse(any(x["starred"] for x in state["addons"] + state["playgrounds"]))

    def test_only_what_the_project_has(self):
        with self.assertRaisesRegex(paths.NotFound, "No add-on nope"):
            starred.star(self.project, "addon", "nope", True)
        self.assertFalse((self.root / starred.FILE).exists())

    def test_a_file_that_does_not_read_stars_nothing(self):
        empty = {"addon": set(), "setup": set(), "playground": set()}
        for text, found in (("{", empty), ("[]", empty), ('{"addon": "kafka", "setup": [1, "observability"]}',
                                                          {**empty, "setup": {"observability"}})):
            self.write(starred.FILE, text)
            self.assertEqual(starred.read(), found, text)

    def test_renamed_it_keeps_its_star(self):
        starred.star(self.project, "setup", "local/mine", True)
        personal.save_setup(self.project, "Other name", "", ["kiosk"], sid="local/mine")
        self.assertEqual(self.saved(), {"setup": ["local/other-name"]})

    def test_asked_to_share_it_keeps_its_star_where_it_is(self):
        # A share is a copy: the item stays, and so does its star
        starred.star(self.project, "addon", "local/whoami", True)
        personal.share(self.project, "addon", "local/whoami", check_only=True)
        self.assertEqual(self.saved(), {"addon": ["local/whoami"]})

    def test_become_a_catalogs_it_keeps_its_star_under_the_new_id(self):
        starred.star(self.project, "addon", "local/whoami", True)
        starred.star(self.project, "addon", "kafka", True)
        starred.moved("addon", "local/whoami", "@acme-hub/whoami")
        self.assertEqual(self.saved(), {"addon": ["@acme-hub/whoami", "kafka"]})
        starred.moved("addon", "local/whoami", "@acme-hub/whoami")   # again: nothing left to move
        self.assertEqual(self.saved(), {"addon": ["@acme-hub/whoami", "kafka"]})

    def test_where_the_new_id_is_starred_already_the_two_are_one(self):
        starred.star(self.project, "addon", "local/whoami", True)
        self.write(starred.FILE, json.dumps({"addon": ["local/whoami", "whoami"]}))
        starred.moved("addon", "local/whoami", "whoami")
        self.assertEqual(self.saved(), {"addon": ["whoami"]})

    def test_a_star_of_another_kind_is_not_moved(self):
        starred.star(self.project, "setup", "local/mine", True)
        starred.moved("addon", "local/mine", "mine")
        self.assertEqual(self.saved(), {"setup": ["local/mine"]})

    def test_nothing_starred_nothing_written(self):
        starred.moved("addon", "local/whoami", "whoami")
        starred.moved("addon", "local/whoami")
        self.assertFalse((self.root / starred.FILE).exists())

    def test_deleted_it_loses_its_star(self):
        starred.star(self.project, "playground", "local/sandbox", True)
        starred.star(self.project, "playground", "welcome", True)
        personal.delete(self.project, "playground", "local/sandbox")
        self.assertEqual(self.saved(), {"playground": ["welcome"]})

    def test_the_file_is_replaced_whole_and_a_file_that_does_not_read_is_left(self):
        starred.star(self.project, "addon", "local/whoami", True)
        self.assertEqual([p.name for p in (self.root / ".lab").iterdir() if p.name.endswith(".tmp")], [])
        self.write(starred.FILE, "not json")
        starred.moved("addon", "local/whoami", "whoami")   # what it cannot read it does not replace
        self.assertEqual(self.text(starred.FILE), "not json")
        self.assertTrue((self.root / WHOAMI).is_file())


if __name__ == "__main__":
    unittest.main()
