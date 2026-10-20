"""The runner of the Lab UI's tests: how the CI deals the modules out to its shares."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import run


class Shards(unittest.TestCase):
    def times(self, found):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "durations.json"
        path.write_text(found if isinstance(found, str) else json.dumps(found), encoding="utf-8")
        patch = mock.patch.object(run, "DURATIONS", path)
        patch.start()
        self.addCleanup(patch.stop)

    def test_every_module_is_in_one_share_and_the_shares_take_about_as_long(self):
        self.times({"a": 9, "b": 5, "c": 4, "d": 3, "e": 2, "f": 1})
        names = ["a", "b", "c", "d", "e", "f"]
        shares = [run.shard(names, i, 2) for i in range(2)]
        self.assertEqual(sorted(sum(shares, [])), names)
        self.assertEqual(shares, [["a", "d"], ["b", "c", "e", "f"]], "9 + 3 and 5 + 4 + 2 + 1: the longest first, to the least loaded")

    def test_a_module_not_timed_counts_as_the_median_and_a_bad_file_as_nothing(self):
        self.times({"a": 1, "b": 2, "c": 30})
        self.assertEqual(run.weights(["a", "new"]), {"a": 1, "new": 2})
        self.times("not json")
        self.assertEqual(run.weights(["a"]), {"a": 1.0})
        self.times({"a": "slow"})
        self.assertEqual(run.durations(), {})

    def test_the_file_names_modules_that_exist(self):
        found = set(run.modules())
        self.assertLessEqual(set(run.durations()), found, "a module that is gone is taken out of durations.json (--time)")
        self.assertIn("tests.test_run", found)


if __name__ == "__main__":
    unittest.main()
