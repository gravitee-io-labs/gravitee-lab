"""library/versions.py: an image tag read as a version, its system left out: the gate of a playground's requirements
(at_least) and the order of builds that the Dashboard and the reasons of a service compare."""

import unittest

from lab_ui.library import versions


class Builds(unittest.TestCase):
    """A tag that names a build, ordered as semver orders it."""

    def test_a_tag_that_names_a_build(self):
        for tag, found in (("4.12.21", ((4, 12, 21), None)), ("3.0.2", ((3, 0, 2), None)),
                           ("4.13.0-alpha.8", ((4, 13, 0), "alpha.8")), ("4.13.0-rc1", ((4, 13, 0), "rc1")),
                           ("v1.21.0", ((1, 21, 0), None)), ("4.8.37-hotfix.1", ((4, 8, 37), "hotfix.1"))):
            self.assertEqual(versions.parse(tag), found, tag)

    def test_the_system_an_image_is_built_on_is_not_part_of_it(self):
        for tag, found in (("4.12.21-debian", ((4, 12, 21), None)), ("4.12.8-noble", ((4, 12, 8), None)),
                           ("4.12.20-chainguard", ((4, 12, 20), None)),
                           ("4.12.20-chainguard-fips", ((4, 12, 20), None)),
                           ("4.13.0-alpha.8-debian", ((4, 13, 0), "alpha.8")),
                           ("4.8.37-hotfix.1-noble", ((4, 8, 37), "hotfix.1"))):
            self.assertEqual(versions.parse(tag), found, tag)

    def test_a_tag_is_told_from_its_system(self):
        for tag, found in (("4.12.21-debian", ("4.12.21", "debian")), ("4.12.20-chainguard-fips", ("4.12.20", "chainguard-fips")),
                           ("4.13.0-alpha.8", ("4.13.0-alpha.8", "")), ("latest", ("latest", "")), ("debian", ("debian", ""))):
            self.assertEqual(versions.bare(tag), found, tag)

    def test_a_tag_that_moves_names_no_version(self):
        for tag in ("latest", "latest-debian", "4", "4.12", "4.12-debian", "4.13.0-alpha", "4.13.0-alpha-debian",
                    "nightly", "4.9.0-kafka-desk", "sha256-abc", "", "master-latest"):
            self.assertIsNone(versions.parse(tag), tag)

    def test_versions_are_ordered_as_semver_orders_them(self):
        ordered = ["3.0.2", "4.12.20", "4.12.21", "4.13.0-alpha.2", "4.13.0-alpha.10", "4.13.0-beta.1", "4.13.0-rc.1",
                   "4.13.0", "4.13.1-alpha.1", "4.13.1"]
        self.assertEqual(sorted(reversed(ordered), key=lambda tag: versions.order(versions.parse(tag))), ordered)

    def test_the_latest_of_each_channel_among_the_tags_of_a_registry(self):
        tags = ["4.13.0-alpha-debian", "4.13.0-alpha.4-debian", "4.13.0-alpha", "4.13.0-alpha.4", "4.13.0-alpha.3",
                "4.10-debian", "4.10.32", "latest", "4", "4.12", "4.12.21-debian", "4.12.21", "4.12.20", "4.11.29",
                "4.13.0-alpha.10-chainguard", "4.9.36"]
        self.assertEqual(versions.latest(tags), ("4.12.21", "4.13.0-alpha.10"))
        self.assertEqual(versions.latest(["3.0", "3", "latest", "3.0.2", "3.0.1", "nightly", "2.3.3"]), ("3.0.2", None))

    def test_a_pre_release_below_the_stable_one_is_no_news(self):
        self.assertEqual(versions.latest(["4.13.0", "4.13.0-rc.2", "4.13.0-alpha.8", "4.12.21"]), ("4.13.0", None))
        self.assertEqual(versions.latest(["4.13.0-alpha.8"]), (None, "4.13.0-alpha.8"))
        self.assertEqual(versions.latest(["latest", "4.12", "nightly"]), (None, None))
        self.assertEqual(versions.latest([]), (None, None))

    def test_the_channel_a_version_is_of(self):
        for tag, found in (("4.12.21", "stable"), ("4.12.21-debian", "stable"), ("4.13.0-alpha.8", "pre-release"),
                           ("4.13.0-alpha.8-debian", "pre-release"), ("4.8.37-hotfix.1", "pre-release"),
                           ("latest", "stable"), ("4.12", "stable"), ("", "stable"), (None, "stable")):
            self.assertEqual(versions.channel(tag), found, tag)

    def test_a_version_against_the_latest_of_its_own_channel(self):
        stable, pre = "4.12.21", "4.13.0-alpha.4"
        for deployed, found in (
                ("4.12.20", ("newer", "4.12.21")), ("4.12.21", ("latest", "4.12.21")),
                ("4.12.21-debian", ("latest", "4.12.21")), ("4.12.22", ("latest", "4.12.22")),
                ("4.11.29", ("newer", "4.12.21")),
                # A stable version is never compared with a pre-release
                ("4.13.0-alpha.3", ("newer", "4.13.0-alpha.4")), ("4.13.0-alpha.3-debian", ("newer", "4.13.0-alpha.4")),
                ("4.13.0-alpha.4", ("latest", "4.13.0-alpha.4")), ("4.13.0-alpha.9", ("latest", "4.13.0-alpha.9")),
                ("latest", ("unknown", None)), ("4.12", ("unknown", None)), ("", ("unknown", None)),
                (None, ("unknown", None))):
            self.assertEqual(versions.judge(deployed, stable, pre), found, deployed)

    def test_a_pre_release_with_none_above_the_stable_one_follows_the_stable_one(self):
        self.assertEqual(versions.judge("4.13.0-alpha.2", "4.13.0", None), ("newer", "4.13.0"))
        # The registry lists nothing above the stable one: what runs is the highest version known
        self.assertEqual(versions.judge("4.13.0-alpha.2", "4.12.21", None), ("latest", "4.13.0-alpha.2"))

    def test_what_a_registry_did_not_say_is_unknown(self):
        self.assertEqual(versions.judge("4.12.20", None, "4.13.0-alpha.1"), ("unknown", None))
        self.assertEqual(versions.judge("4.12.20", None, None), ("unknown", None))
        self.assertEqual(versions.judge("4.13.0-alpha.1", None, None), ("unknown", None))
        self.assertEqual(versions.judge("4.13.0-alpha.1", None, "4.13.0-alpha.2"), ("newer", "4.13.0-alpha.2"))


if __name__ == "__main__":
    unittest.main()
