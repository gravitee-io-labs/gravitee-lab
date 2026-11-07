"""next_version.py: the version a merge to main publishes, from the one before it and the labels of its pull requests."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import next_version  # noqa: E402


class NextVersion(unittest.TestCase):
    def test_the_version_of_a_merge(self):
        for last, labels, wanted in (
                ("", set(), "v1.0.0"), ("", {"major"}, "v1.0.0"),   # the first version is one whatever the labels
                ("v1.0.0", set(), "v1.0.1"), ("v1.2.9", {"bug", "dependencies"}, "v1.2.10"),   # no label is a patch
                ("v1.0.4", {"minor"}, "v1.1.0"), ("v1.3.2", {"major"}, "v2.0.0"),   # the numbers after it start again
                # What catalogs can use is a new feature: it raises the minor number at least
                ("v1.4.2", {"catalog-format"}, "v1.5.0"), ("v1.4.2", {"catalog-format", "major"}, "v2.0.0"),
                # The highest label of the merges wins
                ("v1.3.2", {"patch", "minor"}, "v1.4.0"), ("v1.3.2", {"minor", "major", "patch"}, "v2.0.0")):
            with self.subTest(last=last, labels=labels):
                self.assertEqual(next_version.next_version(last, labels), wanted)


if __name__ == "__main__":
    unittest.main()
