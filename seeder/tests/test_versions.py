"""The versions of Gravitee a seed reads at its start and what it does on each: a release by the table of releases, a build off
the release channel by its exact version, anything else not at all. The tables of the docs say what the code says."""

import re
from pathlib import Path

from lab_seeder import seed, versions
from lab_seeder.common import SeedError
from lab_seeder.testkit import Seed

INSTANCES = ("GET", "/management/organizations/DEFAULT/environments/DEFAULT/instances")
REPORTERS = ("GET", "/management/platform/plugins/reporters")
MODULES = ("GET", "/gamma/organizations/DEFAULT/modules")
AMP_DEMO = "4.13.0-amp-demo-SNAPSHOT"


def gateways(*said):
    """The instances API Management lists: (version, state) each, as it writes them."""
    return (200, {"content": [{"version": f"{version} (build: 1) revision#abc", "state": state} for version, state in said]})


class Reading(Seed):

    def setUp(self):
        super().setUp()
        versions._found.clear()

    def test_a_release_is_three_numbers_and_anything_else_is_none(self):
        for text, wanted in (("4.13.3", (4, 13, 3)), ("4.13.2 (build: 2233858) revision#a4d3", (4, 13, 2)),
                             (AMP_DEMO, None), ("master-latest", None), ("4.13", None), (None, None)):
            with self.subTest(text=text):
                self.assertEqual(versions.release(text), wanted)

    def test_the_started_gateways_state_the_version_of_api_management_and_the_stopped_ones_are_left_out(self):
        self.seed_files({"apim/settings.yaml": "{}"})
        self.platform.add({INSTANCES: gateways(("4.12.3", "STARTED"), ("4.13.2", "UNKNOWN"))})
        versions.read()
        self.assertEqual((versions.variant("gamma-modules"), self.platform.sent(*REPORTERS)), ("early", []))
        self.assertIn("[versions] API Management 4.12.3: a release, seeded by its version", self.output())

    def test_a_seed_that_only_links_gamma_to_access_management_reads_api_management_too(self):
        """am_link fills the platform module of Gamma, which API Management serves: its version decides what Gamma is."""
        self.seed_files({"am/gamma-link.yaml": "domain: d"})
        self.platform.add({INSTANCES: gateways(("4.13.5", "STARTED")), ("POST", "/management/auth/token"): (200, {"access_token": "t"}),
                           REPORTERS: (200, [{"id": "mongodb", "version": "4.13.6"}])})
        versions.read()
        self.assertEqual(versions.variant("gamma-modules"), "served")

    def test_gateways_of_several_releases_make_the_oldest_as_the_lab_counts_an_environment(self):
        self.seed_files({"apim/settings.yaml": "{}"})
        self.platform.add({INSTANCES: gateways(("4.13.3", "STARTED"), ("4.12.21", "STARTED"), ("4.13.2", "STARTED"))})
        versions.read()
        self.assertEqual(versions.variant("gamma-modules"), "early")
        self.assertIn("[versions] API Management states 4.12.21, 4.13.2, 4.13.3: seeded as the oldest", self.output())

    def test_a_platform_that_states_no_version_or_a_build_beside_another_stops_the_seed(self):
        for answer, words in ((gateways((AMP_DEMO, "STARTED"), ("4.13.3", "STARTED")), f"states several versions ({AMP_DEMO}, 4.13.3)"),
                              ((200, {"content": []}), "states no version"), ((404, {}), "states no version")):
            with self.subTest(answer=answer):
                self.seed_files({"aim/providers/p.yaml": "name: p"})
                self.platform.add({INSTANCES: answer})
                with self.assertRaisesRegex(SeedError, re.escape(f"API Management {words}")):
                    versions.read()

    def test_a_gateway_that_has_just_started_is_waited_for_until_api_management_lists_it(self):
        self.seed_files({"apim/settings.yaml": "{}"})
        answers = [(200, {"content": []}), gateways(("4.13.4", "STARTED"))]
        self.platform.add({INSTANCES: lambda call: answers.pop(0) if len(answers) > 1 else answers[0]})
        versions.read()
        self.assertEqual((len(self.platform.sent(*INSTANCES)), versions.variant("gamma-modules")), (2, "served"))

    def test_a_build_of_builds_is_seeded_like_its_release_except_where_it_differs(self):
        self.seed_files({"apim/settings.yaml": "{}"})
        self.platform.add({INSTANCES: gateways((AMP_DEMO, "STARTED"))})
        versions.read()
        self.assertEqual({name: versions.variant(name) for name in versions.RELEASES},
                         {"gamma-modules": "served", "mcp-protocol-versions": "single", "agent-application": "served",
                          "dashboard-module": "none", "authz-schema-documents": "one", "governance": "served",
                          "studio-human-approval": "served"})
        self.assertIn(f"[versions] API Management {AMP_DEMO}: The amp-demo build of API Management, seeded like 4.13.0", self.output())

    def test_a_version_that_is_neither_a_release_nor_a_known_build_stops_the_seed(self):
        with self.assertRaisesRegex(SeedError, "API Management 4.15.0-SNAPSHOT: neither a release nor a build the seeder knows "
                                               rf"\(known builds: {AMP_DEMO}\)"):
            versions.note(versions.APIM, ["4.15.0-SNAPSHOT"])

    def test_a_build_of_one_product_is_no_build_of_the_other(self):
        with self.assertRaisesRegex(SeedError, f"Access Management {AMP_DEMO}: neither a release nor a build"):
            versions.note(versions.AM, [AMP_DEMO])
        with self.assertRaisesRegex(SeedError, "API Management 4.14.0-SNAPSHOT: neither a release nor a build"):
            versions.note(versions.APIM, ["4.14.0-SNAPSHOT"])

    def test_access_management_states_its_version_by_its_reporters(self):
        self.seed_files({"am/domain.yaml": "name: d"})
        self.platform.add({("POST", "/management/auth/token"): (200, {"access_token": "t"}),
                           REPORTERS: (200, [{"id": "mongodb", "version": "4.13.3"}, {"id": "file", "version": "4.13.3"}])})
        versions.read()
        self.assertEqual(self.platform.sent(*INSTANCES), [])
        self.assertIn("[versions] Access Management 4.13.3: a release, seeded by its version", self.output())

    def test_a_release_below_the_minimum_stops_the_seed(self):
        with self.assertRaises(SeedError) as stopped:
            versions.note(versions.AM, ["4.11.9"])
        self.assertEqual(str(stopped.exception), "Access Management 4.11.9: the seeder fills 4.12.0 and later releases")
        versions.note(versions.AM, ["4.12.0"])


class Variants(Seed):
    """What `variant` answers: the last row at or below the release, a build's own variant where it differs."""

    def test_a_release_takes_the_variant_of_the_last_row_at_or_below_it(self):
        for text, wanted in (("4.12.0", "early"), ("4.12.28", "early"), ("4.13.0", "served"), ("4.13.5", "served"),
                             ("4.14.28", "served")):
            with self.subTest(text=text):
                self.version(text, versions.APIM)
                self.assertEqual(versions.variant("gamma-modules"), wanted)

    def test_a_release_older_than_the_first_row_of_a_behavior_stops_the_seed(self):
        self.version("4.12.9", versions.APIM)
        with self.assertRaises(SeedError) as stopped:
            versions.variant("mcp-protocol-versions")
        for words in ("4.12.9", "4.13.0 is the first"):
            self.assertIn(words, str(stopped.exception))

    def test_a_product_whose_version_was_not_read_stops_the_seed(self):
        versions._found.clear()
        with self.assertRaisesRegex(SeedError, "The version of API Management was not read"):
            versions.variant("gamma-modules")


class Tables(Seed):
    """The tables hold together: what a row or a build names exists, and each table is in order."""

    def test_each_behavior_starts_at_or_after_the_minimum_in_the_order_of_the_releases_and_says_each_variant(self):
        for name, behavior in versions.RELEASES.items():
            with self.subTest(name=name):
                firsts = [versions.numbers(first) for first in behavior.since]
                self.assertEqual(firsts, sorted(set(firsts)))
                self.assertGreaterEqual(firsts[0], versions.numbers(versions.MINIMUM))
                used = set(behavior.since.values()) | {build.differs[name] for build in versions.BUILDS.values() if name in build.differs}
                self.assertEqual(used, set(behavior.variants), "each variant is taken by a release or a build, and said")
                self.assertIn(behavior.product, (versions.APIM, versions.AM))

    def test_a_build_is_like_a_release_of_its_product_and_differs_by_a_variant_its_behavior_has(self):
        for (product, text), build in versions.BUILDS.items():
            with self.subTest(build=text):
                self.assertIsNone(versions.release(text))
                self.assertIsNotNone(versions.release(build.like))
                for name, chosen in build.differs.items():
                    self.assertEqual(versions.RELEASES[name].product, product)
                    self.assertIn(chosen, versions.RELEASES[name].variants)


class Where(Seed):

    def test_where_a_variant_holds_is_said_from_the_tables(self):
        self.assertEqual(versions.where("gamma-modules", "served"), "API Management 4.13.0 and later")
        self.assertEqual(versions.where("gamma-modules", "early"), "API Management 4.12.0 up to 4.13.0")
        self.assertEqual(versions.where("agent-application", "served"), f"the build {AMP_DEMO}")
        self.assertEqual(versions.where("mcp-protocol-versions", "single"), f"the build {AMP_DEMO}")


class Seeding(Seed):

    def test_a_release_with_gamma_modules_of_version_1_stops_before_anything_is_asked_of_gamma(self):
        self.seed_files({"aim/providers/p.yaml": "name: p"})
        self.version("4.12.3", versions.APIM)
        self.assertEqual(seed.main(), 1)
        self.assertIn("[seed] ✗ This platform serves versions 1 of the Gamma modules, which the seeder does not fill: this "
                      "playground needs", self.output())
        self.assertEqual((self.platform.sent(*MODULES), self.platform.changes()), ([], []))

    def test_a_release_below_the_minimum_changes_nothing(self):
        self.seed_files({"apim/settings.yaml": "{}"})
        self.platform.add({INSTANCES: gateways(("4.11.4", "STARTED"))})
        self.assertEqual(seed.main(), 1)
        self.assertIn("[seed] ✗ API Management 4.11.4: the seeder fills 4.12.0 and later releases", self.output())
        self.assertEqual(self.platform.changes(), [])


class StatedLater(Seed):
    def test_no_row_starts_between_a_release_and_the_later_version_it_states(self):
        """The image 4.13.0 states 4.13.4: a row from 4.13.2 would give it the variant of 4.13.4, which it does not have."""
        between = [(name, first) for name, behavior in versions.RELEASES.items() for first in behavior.since
                   for (product, stated), actual in versions.STATED_LATER.items()
                   if behavior.product == product and versions.numbers(actual) < versions.numbers(first) <= versions.numbers(stated)]
        self.assertEqual(between, [])


class Documented(Seed):

    def test_the_docs_hold_the_tables_the_code_writes_and_no_other_row(self):
        text = (Path(__file__).resolve().parents[2] / "docs" / "playgrounds.md").read_text(encoding="utf-8")
        section = text.split("### Versions of Gravitee", 1)[1].split("\n### ", 1)[0]
        written = "\n".join(line for line in section.splitlines() if line.startswith("|"))
        self.assertEqual(written, "\n".join(line for line in versions.markdown().splitlines() if line))
        self.assertIn(f"**{versions.MINIMUM} and later**", section)
