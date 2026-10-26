"""next_version.py: the version a merge to main publishes, from the one before it and the labels of its pull requests."""

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import next_version  # noqa: E402


class NextVersion(unittest.TestCase):
    def test_the_first_version_is_one_whatever_the_labels(self):
        self.assertEqual(next_version.next_version("", set()), "v1.0.0")
        self.assertEqual(next_version.next_version("", {"major"}), "v1.0.0")

    def test_a_merge_with_no_label_is_a_patch(self):
        self.assertEqual(next_version.next_version("v1.0.0", set()), "v1.0.1")
        self.assertEqual(next_version.next_version("v1.2.9", {"bug", "dependencies"}), "v1.2.10")

    def test_minor_raises_the_minor_number_and_starts_the_patch_again(self):
        self.assertEqual(next_version.next_version("v1.0.4", {"minor"}), "v1.1.0")

    def test_what_catalogs_can_use_is_a_new_feature_and_raises_the_minor_number_at_least(self):
        self.assertEqual(next_version.next_version("v1.4.2", {"catalog-format"}), "v1.5.0")
        self.assertEqual(next_version.next_version("v1.4.2", {"catalog-format", "major"}), "v2.0.0")

    def test_major_raises_the_major_number_and_starts_the_others_again(self):
        self.assertEqual(next_version.next_version("v1.3.2", {"major"}), "v2.0.0")

    def test_the_highest_label_of_the_merges_wins(self):
        self.assertEqual(next_version.next_version("v1.3.2", {"patch", "minor"}), "v1.4.0")
        self.assertEqual(next_version.next_version("v1.3.2", {"minor", "major", "patch"}), "v2.0.0")

    def test_a_tag_that_is_not_a_version_stops(self):
        for tag in ("1.0.0", "v1.0", "v1.0.0-rc.1", "latest"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                next_version.next_version(tag, set())

    def test_the_command_prints_the_tag_and_says_what_is_wrong(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(next_version.main(["v1.0.0", "minor", "docker"]), 0)
        self.assertEqual(out.getvalue(), "v1.1.0\n")
        with contextlib.redirect_stderr(err):
            self.assertEqual(next_version.main(["main"]), 1)
            self.assertEqual(next_version.main([]), 2)
        self.assertIn("main is not a version", err.getvalue())


if __name__ == "__main__":
    unittest.main()
