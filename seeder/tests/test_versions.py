"""The versions of Gravitee a seed reads at its start: a release is seeded by its version, below 4.12 not at all, and a build
that is no release, or a platform that says none, by what it answers. The table of the docs says what the code says."""

import re
from pathlib import Path

from lab_seeder import seed, versions
from lab_seeder.common import SeedError
from lab_seeder.testkit import Seed

INSTANCES = ("GET", "/management/organizations/DEFAULT/environments/DEFAULT/instances")
REPORTERS = ("GET", "/management/platform/plugins/reporters")
MODULES = ("GET", "/gamma/organizations/DEFAULT/modules")


def gateways(*said):
    """The instances API Management lists: (version, state) each, as it writes them."""
    return (200, {"content": [{"version": f"{version} (build: 1) revision#abc", "state": state} for version, state in said]})


class Reading(Seed):

    def test_a_release_is_three_numbers_and_a_build_that_is_no_release_is_none(self):
        for text, wanted in (("4.13.3", (4, 13, 3)), ("4.13.2 (build: 2233858) revision#a4d3", (4, 13, 2)),
                             ("4.13.0-amp-demo-SNAPSHOT", None), ("master-latest", None), ("4.13", None), (None, None)):
            with self.subTest(text=text):
                self.assertEqual(versions.release(text), wanted)

    def test_the_started_gateways_say_the_version_of_api_management_and_the_stopped_ones_are_left_out(self):
        self.seed_files({"apim/settings.yaml": "{}"})
        self.answer({INSTANCES: gateways(("4.12.3", "STARTED"), ("4.13.2", "UNKNOWN"))})
        versions.read()
        self.assertEqual((versions.since("gamma"), self.platform.sent(*REPORTERS)), (False, []))
        self.assertIn("[versions] API Management 4.12.3: a release, seeded by its version", self.output())

    def test_gateways_that_disagree_or_say_nothing_are_no_version_and_the_seed_adapts(self):
        for answer in (gateways(("4.12.3", "STARTED"), ("4.13.3", "STARTED")), (200, {"content": []}), (404, {})):
            with self.subTest(answer=answer):
                self.seed_files({"aim/providers/p.yaml": "name: p"})
                self.answer({INSTANCES: answer})
                versions.read()
                self.assertIsNone(versions.since("gamma"))

    def test_a_build_that_is_no_release_is_seeded_by_what_it_answers(self):
        self.seed_files({"apim/settings.yaml": "{}"})
        self.answer({INSTANCES: gateways(("4.13.0-amp-demo-SNAPSHOT", "STARTED"))})
        versions.read()
        self.assertIsNone(versions.since("gamma"))
        self.assertIn("API Management 4.13.0-amp-demo-SNAPSHOT: not a release, the seed adapts to what it answers", self.output())

    def test_access_management_says_its_version_by_its_reporters(self):
        self.seed_files({"am/domain.yaml": "name: d"})
        self.answer({("POST", "/management/auth/token"): (200, {"access_token": "t"}),
                     REPORTERS: (200, [{"id": "mongodb", "version": "4.13.3"}, {"id": "file", "version": "4.13.3"}])})
        versions.read()
        self.assertEqual(self.platform.sent(*INSTANCES), [])
        self.assertIn("[versions] Access Management 4.13.3: a release, seeded by its version", self.output())

    def test_a_release_below_the_minimum_stops_the_seed(self):
        with self.assertRaises(SeedError) as stopped:
            versions.note(versions.AM, ["4.11.9"])
        self.assertEqual(str(stopped.exception), "Access Management 4.11.9: the seeder fills 4.12 and later releases")
        versions.note(versions.AM, ["4.12.0"])


class Seeding(Seed):

    def test_a_release_without_the_gamma_modules_stops_before_anything_is_asked_of_gamma(self):
        self.seed_files({"aim/providers/p.yaml": "name: p"})
        self.answer({INSTANCES: gateways(("4.12.3", "STARTED"))})
        self.assertEqual(seed.main(), 1)
        self.assertIn("[seed] ✗ This platform does not serve the Gamma modules: this playground needs", self.output())
        self.assertEqual((self.platform.sent(*MODULES), self.platform.changes()), ([], []))

    def test_a_release_below_the_minimum_changes_nothing(self):
        self.seed_files({"apim/settings.yaml": "{}"})
        self.answer({INSTANCES: gateways(("4.11.4", "STARTED"))})
        self.assertEqual(seed.main(), 1)
        self.assertIn("[seed] ✗ API Management 4.11.4: the seeder fills 4.12 and later releases", self.output())
        self.assertEqual(self.platform.changes(), [])


class Documented(Seed):

    def test_the_docs_say_the_minimum_and_each_change(self):
        text = (Path(__file__).resolve().parents[2] / "docs" / "playgrounds.md").read_text(encoding="utf-8")
        section = text.split("### Versions of Gravitee", 1)[1].split("\n### ", 1)[0]
        self.assertIn(f"**{'.'.join(map(str, versions.MINIMUM))} and later**", section)
        rows = re.findall(r"^\| (.+?) \| (.+?) \| (\d+\.\d+\.\d+) \|$", section, re.M)
        self.assertEqual(rows, [(what, product, ".".join(map(str, first)))
                                for product, first, what in versions.CHANGES.values()])
