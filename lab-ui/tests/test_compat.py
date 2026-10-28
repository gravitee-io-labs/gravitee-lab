"""compat: the versions of Gravitee a playground is tried on each night, and the record of what passed in its `x-gravitee.tested`.
No network: the tags of each image are given."""

import json
import unittest

from lab_ui import compat

APIM = ["4.13.1", "4.13.1-debian", "4.13.0", "4.13.0-alpha.7", "4.13", "latest", "4.12.25", "4.12.24", "4.11.31", "4.10.34",
        "4.9.38", "4.8.34", "4.12-chainguard"]
AM = ["4.13.1", "4.13.0-alpha.10", "4.12.10", "4.12.9", "4.11.20", "4.10.26", "4.9.35", "4.8.37", "4.8.37-hotfix.1-debian"]
TAGS = {"APIM_VERSION": APIM, "AM_VERSION": AM}


def unit(tested=(), requires=None, name="Shop"):
    return {"name": name, "tested": list(tested), "requires": {"images": requires or {}}}


def both(apim, am):
    return {"APIM_VERSION": apim, "APIM_REGISTRY": "graviteeio", "AM_VERSION": am, "AM_REGISTRY": "graviteeio"}


class Newest(unittest.TestCase):
    def test_the_newest_release_of_each_line_and_a_pre_release_only_above_every_release(self):
        releases, pre = compat.newest(APIM)
        self.assertEqual(releases[(4, 13)], ((4, 13, 1), None))
        self.assertEqual(releases[(4, 12)], ((4, 12, 25), None))
        self.assertEqual(sorted(releases), [(4, 8), (4, 9), (4, 10), (4, 11), (4, 12), (4, 13)])
        self.assertIsNone(pre, "4.13.0-alpha.7 is before 4.13.1: no news")
        self.assertEqual(compat.newest([*APIM, "4.14.0-alpha.2", "4.14.0-alpha.10"])[1], ((4, 14, 0), "alpha.10"))


class Candidates(unittest.TestCase):
    def tags(self, key):
        return TAGS[key]

    def test_the_five_newest_lines_each_on_its_newest_release_with_the_registries_of_its_sets(self):
        found = compat.candidates(unit([both("4.13.1", "4.13.1")]), self.tags)
        self.assertEqual([one["APIM_VERSION"] for one in found], ["4.9.38", "4.10.34", "4.11.31", "4.12.25"],
                         "4.13.1 is tested already, and 4.8 is the sixth line")
        self.assertEqual(found[-1], both("4.12.25", "4.12.10"))

    def test_a_newer_patch_of_a_tested_line_is_tried(self):
        found = compat.candidates(unit([both("4.13.0", "4.13.0")], {"APIM_VERSION": "4.13"}), self.tags)
        self.assertEqual(found, [both("4.13.1", "4.13.1")], "below what it requires is never tried")

    def test_a_pre_release_above_every_release_is_tried_when_each_image_has_one_of_that_line(self):
        tags = {"APIM_VERSION": [*APIM, "4.14.0-alpha.1"], "AM_VERSION": [*AM, "4.14.0-alpha.3"]}
        found = compat.candidates(unit([both("4.13.1", "4.13.1")], {"APIM_VERSION": "4.13"}), tags.get)
        self.assertEqual(found, [both("4.14.0-alpha.1", "4.14.0-alpha.3")])
        tags["AM_VERSION"] = AM
        self.assertEqual(compat.candidates(unit([both("4.13.1", "4.13.1")], {"APIM_VERSION": "4.13"}), tags.get), [])

    def test_a_playground_of_a_build_of_its_own_is_never_tried(self):
        demo = {"APIM_VERSION": "amp-demo-latest", "APIM_REGISTRY": "graviteeio.azurecr.io"}
        self.assertEqual(compat.candidates(unit([demo], {"APIM_VERSION": "amp-demo-latest"}), self.tags), [])
        self.assertEqual(compat.candidates(unit([], {"APIM_VERSION": "amp-demo-latest"}), self.tags), [])
        private = {"APIM_VERSION": "4.13.1", "APIM_REGISTRY": "graviteeio.azurecr.io"}
        self.assertEqual(compat.candidates(unit([private]), self.tags), [], "a registry of its own says a build of its own")

    def test_a_playground_that_says_nothing_of_its_versions_is_followed_from_what_it_requires(self):
        found = compat.candidates(unit([], {"APIM_VERSION": "4.12"}), self.tags)
        self.assertEqual(found, [{"APIM_VERSION": "4.12.25"}, {"APIM_VERSION": "4.13.1"}])
        self.assertEqual(compat.candidates(unit(), self.tags), [], "nothing said: not followed")


class Plan(unittest.TestCase):
    def test_a_job_for_each_shared_playground_of_the_catalog_and_each_set(self):
        built = {"playgrounds": {
            "shop": {**unit([both("4.13.1", "4.13.1")], {"APIM_VERSION": "4.12"}), "catalog": "acme"},
            "local/mine": {**unit([both("4.13.1", "4.13.1")], {"APIM_VERSION": "4.12"}), "catalog": None},
            "@other/bank": {**unit([both("4.13.1", "4.13.1")], {"APIM_VERSION": "4.12"}), "catalog": "other"},
        }}
        asked = []

        def tags(project, key, registry):
            asked.append(registry)
            return TAGS[key]
        jobs = compat.plan(built, "acme", tags)
        self.assertEqual([(job["item"], json.loads(job["versions"])) for job in jobs], [("playground:shop", both("4.12.25", "4.12.10"))])
        self.assertEqual(jobs[0]["name"], "Shop on APIM_VERSION 4.12.25, AM_VERSION 4.12.10")
        self.assertEqual(set(asked), {"graviteeio"})


class Merged(unittest.TestCase):
    def test_a_passed_set_replaces_its_line_and_the_newest_release_is_last(self):
        sets = [both("4.12.24", "4.12.9"), both("4.13.1", "4.13.1")]
        self.assertEqual(compat.merged(sets, [both("4.12.25", "4.12.10")]), [both("4.12.25", "4.12.10"), both("4.13.1", "4.13.1")])

    def test_a_line_out_of_the_five_newest_goes_and_a_pre_release_goes_once_its_line_has_a_release(self):
        sets = [both(f"4.{minor}.0", f"4.{minor}.0") for minor in (8, 9, 10, 11, 12)] + [both("4.13.0-alpha.7", "4.13.0-alpha.10")]
        found = compat.merged(sets, [both("4.13.1", "4.13.1")])
        self.assertEqual([one["APIM_VERSION"] for one in found], ["4.9.0", "4.10.0", "4.11.0", "4.12.0", "4.13.1"])

    def test_a_pre_release_above_the_releases_comes_before_them_and_a_build_first_untouched(self):
        demo = {"APIM_VERSION": "amp-demo-latest", "APIM_REGISTRY": "graviteeio.azurecr.io"}
        found = compat.merged([demo, both("4.13.1", "4.13.1")], [both("4.14.0-alpha.1", "4.14.0-alpha.3")])
        self.assertEqual(found, [demo, both("4.14.0-alpha.1", "4.14.0-alpha.3"), both("4.13.1", "4.13.1")],
                         "the last set is what a Start offers to switch to: a release")


TEXT = """# Shop
x-shop-secrets:
  key: &key ${SHOP_KEY:-k}

x-gravitee:
  kind: playground
  name: Shop
  requires:
    images:
      APIM_VERSION: "4.12"   # its policies
  tested:                    # the versions it was built and checked on
    - APIM_VERSION: "4.12.20"
      APIM_REGISTRY: graviteeio

  consoles: [shop]

services:
  shop:
    image: shop
"""


class Write(unittest.TestCase):
    def test_the_block_is_replaced_where_it_is_and_nothing_else_changes(self):
        found = compat.write(TEXT, [{"APIM_VERSION": "4.12.25", "APIM_REGISTRY": "graviteeio"},
                                    {"APIM_VERSION": "4.13.1", "APIM_REGISTRY": "graviteeio"}])
        self.assertEqual(found, TEXT.replace('''  tested:                    # the versions it was built and checked on
    - APIM_VERSION: "4.12.20"
      APIM_REGISTRY: graviteeio

''', '''  tested:
    - APIM_VERSION: "4.12.25"
      APIM_REGISTRY: graviteeio
    - APIM_VERSION: "4.13.1"
      APIM_REGISTRY: graviteeio
'''))

    def test_a_file_without_the_block_gets_it_after_requires(self):
        bare = TEXT.replace('''  tested:                    # the versions it was built and checked on
    - APIM_VERSION: "4.12.20"
      APIM_REGISTRY: graviteeio

''', "")
        found = compat.write(bare, [{"APIM_VERSION": "4.13.1"}])
        self.assertIn('''      APIM_VERSION: "4.12"   # its policies
  tested:
    - APIM_VERSION: "4.13.1"
  consoles: [shop]''', found)

    def test_a_file_without_x_gravitee_at_the_top_is_refused(self):
        with self.assertRaises(ValueError):
            compat.write("services: {}\n", [])


if __name__ == "__main__":
    unittest.main()
