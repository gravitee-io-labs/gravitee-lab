"""Catalogs/manifest.py: the `catalog.yml` of a catalog (every field of), the level a Lab reads, the compatible
pin over tables and against a bare repository, and the generated index. The pure functions are tested over
tables; `read_catalog` against a Compose that is replaced and, where Compose 5 is installed, against the real one (a
visible skip otherwise); `levels` against bare repositories made on this machine (a visible skip without git)."""

import json
import os
import re
import stat
import time
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.library import check, project
from lab_ui.docker import compose, envfiles
from lab_ui.gravitee import license
from lab_ui import paths
from lab_ui.catalogs import gitx, manifest, registry, standard
from lab_ui.catalogs import store as bare

from .. import Case, fixtures, gitfixture

GOOD = {"schema": 1, "id": "acme-hub", "name": "Acme Hub", "description": "Banking demos for partner workshops",
        "version": "1.4.0", "lab": 1}
FULL = {**GOOD, "license": "Apache-2.0", "images": "ghcr.io/acme/lab-hub", "contribute": "pull-request",
        "shared_settings": ["GEMINI_API_KEY", "OLLAMA_URL"], "moved_from": ["addon:echo", "playground:tour"],
        "canary": ["playground:tour"], "x-note": "an unknown key",
        "requires": {"catalogs": {"gravitee": {"name": "Gravitee public", "url": "https://github.com/acme/demos.git",
                                               "min": "1.0.0"},
                                  "partner-bank": {"name": "Partner Bank", "url": "git@github.com:partner/lab-bank.git",
                                                   "min": "2.0.0"}}}}


def problems(**changes):
    """validate_catalog of the good block with these keys changed; a key set to ... is removed."""
    meta = {**GOOD}
    for key, value in changes.items():
        if value is ...:
            meta.pop(key, None)
        else:
            meta[key] = value
    return manifest.validate_catalog(meta)


class Versions(Case):

    def test_a_version_is_three_numbers_with_dots(self):
        self.assertEqual(manifest.parse_version("1.4.0"), (1, 4, 0))
        self.assertEqual(manifest.parse_version("10.20.300"), (10, 20, 300))
        self.assertEqual(manifest.parse_version("0.0.0"), (0, 0, 0))

    def test_anything_else_is_refused(self):
        for text in ("1.4", "1.4.0.1", "v1.4.0", "1.4.0-beta", "1.4.x", "", " 1.4.0", "1.4.0 ", "1..0", "-1.0.0",
                     "1.0.0\n", "１.０.０", "١.٠.٠", "1" * 12 + ".0.0", None, 1, 1.4, ("1", "4", "0")):
            with self.assertRaises(ValueError, msg=repr(text)):
                manifest.parse_version(text)

    def test_a_version_is_at_least_another_by_its_numbers_not_its_text(self):
        for have, need, answer in (("1.4.0", "1.4.0", True), ("1.4.1", "1.4.0", True), ("1.10.0", "1.9.0", True),
                                   ("1.9.0", "1.10.0", False), ("2.0.0", "1.99.99", True), ("1.0.0", "2.0.0", False),
                                   ("0.9.9", "1.0.0", False), ("10.0.0", "9.0.0", True)):
            self.assertIs(manifest.at_least(have, need), answer, (have, need))

    def test_a_doubt_is_never_enough(self):
        for have, need in (("1.0", "1.0.0"), ("1.0.0", "1.0"), (None, "1.0.0"), ("1.0.0", None), ("", ""), ("x", "y")):
            self.assertIs(manifest.at_least(have, need), False, (have, need))


class Shared(Case):
    """What the registry shares with the package is public: `shown` and `scan`, not the names they had when they were private."""

    def test_no_module_of_the_package_reaches_into_a_private_name_of_the_registry(self):
        for name in ("manifest", "install"):
            source = Path(manifest.__file__).with_name(f"{name}.py").read_text(encoding="utf-8")
            with self.subTest(module=name):
                self.assertEqual(re.findall(r"\bregistry\._\w+", source), [])
        self.assertIs(registry._scan, registry.scan)
        self.assertIs(registry._shown, registry.shown)


class Levels(Case):
    """compat over the table of 4.4."""

    def test_a_level_between_the_floor_and_the_newest_is_read(self):
        for level in (2, 3, 4):
            self.assertEqual(manifest.compat(level, 4, 2), "ok", level)

    def test_a_newer_level_needs_a_newer_lab_and_an_older_one_was_written_for_an_older_lab(self):
        self.assertEqual(manifest.compat(5, 4, 2), "new")
        self.assertEqual(manifest.compat(99, 4, 2), "new")
        self.assertEqual(manifest.compat(1, 4, 2), "old")
        self.assertEqual(manifest.compat(0, 4, 2), "old")
        self.assertEqual(manifest.compat(-3, 4, 2), "old")

    def test_a_core_without_levels_reads_level_one(self):
        self.assertEqual([manifest.compat(level, None, None) for level in (0, 1, 2)], ["old", "ok", "new"])
        self.assertEqual(manifest.compat(1, 1, None), "ok")
        self.assertEqual(manifest.compat(2, None, 1), "new")

    def test_a_level_that_is_not_a_whole_number_is_not_one_this_lab_reads(self):
        for level in (None, "1", 1.0, True, False, [1], {}):
            self.assertEqual(manifest.compat(level, 4, 1), "old", repr(level))

    def test_the_floor_is_never_above_what_is_read(self):
        self.assertEqual([manifest.compat(level, 1, 1) for level in (0, 1, 2)], ["old", "ok", "new"])


class Picking(Case):
    """Pick returns the newest readable commit on every case of the table."""

    def test_a_readable_tip_is_the_pin(self):
        self.assertEqual(manifest.pick([("t", 2), ("a", 1)], 3, 1), ("t", "ok"))
        self.assertEqual(manifest.pick([("t", 1)], 1, 1), ("t", "ok"))

    def test_a_tip_that_needs_a_newer_lab_gives_the_newest_older_version_that_is_read(self):
        self.assertEqual(manifest.pick([("t", 3), ("a", 2), ("b", 1)], 2, 1), ("a", "new"))
        self.assertEqual(manifest.pick([("t", 9), ("a", 8), ("b", 2), ("c", 1)], 2, 1), ("b", "new"))

    def test_when_no_version_is_read_there_is_no_commit_and_the_tip_says_why(self):
        self.assertEqual(manifest.pick([("t", 5), ("a", 4)], 3, 1), (None, "new"))
        self.assertEqual(manifest.pick([("t", 1), ("a", 0)], 3, 2), (None, "old"))

    def test_a_tip_that_is_too_old_has_no_readable_version_and_is_old(self):
        self.assertEqual(manifest.pick([("t", 1)], 4, 3), (None, "old"))

    def test_a_revert_to_an_older_level_is_one_more_version_not_a_loop(self):
        levels = [("t", 3), ("r", 1), ("o", 2)]   # the newest changed to 3, before it was 1 (a revert of 2), before 2
        self.assertEqual(manifest.pick(levels, 2, 1), ("r", "new"))
        self.assertEqual(manifest.pick(levels, 1, 1), ("r", "new"))
        self.assertEqual(manifest.pick([("t", 1), ("r", 2), ("o", 1)], 1, 1), ("t", "ok"))

    def test_the_newest_is_taken_even_when_older_ones_are_read_too(self):
        self.assertEqual(manifest.pick([("t", 2), ("a", 2), ("b", 1)], 2, 1), ("t", "ok"))

    def test_a_version_below_the_floor_is_skipped_for_the_one_above_it(self):
        self.assertEqual(manifest.pick([("t", 4), ("a", 1), ("b", 2)], 3, 2), ("b", "new"))

    def test_nothing_to_choose_from_is_no_commit(self):
        self.assertEqual(manifest.pick([], 3, 1), (None, "old"))

    def test_a_version_the_reader_was_not_sure_of_is_one_the_strict_reading_decides(self):
        """A `lab` of None is "cannot say" (`levels`), which is neither a level this Lab reads nor one it does not: the version
        is a candidate, and the tip is "ok" until the strict reading of the file, at Add and at Update, says otherwise."""
        self.assertEqual(manifest.pick([("t", None), ("a", 1)], 3, 2), ("t", "ok"))
        self.assertEqual(manifest.pick([("t", None)], 4, 3), ("t", "ok"))
        self.assertEqual(manifest.pick([("t", 5), ("a", None), ("b", 3)], 3, 2), ("a", "new"), "the newest that may be read")
        self.assertEqual(manifest.pick([("t", 5), ("a", 1), ("b", None)], 3, 2), ("b", "new"))
        self.assertEqual(manifest.pick([("t", 5), ("a", 4)], 3, 2), (None, "new"), "without one, as before")
        self.assertEqual(manifest.pick([("t", 1), ("a", 0)], 3, 2), (None, "old"), "a level it does not state is still old")

    def test_a_readable_version_six_back_is_found_and_one_past_the_budget_is_said_truly(self):
        def readable_at(index):
            return lambda commit: commit == str(index)
        six = [(str(number), None) for number in range(10)]
        self.assertEqual(manifest.searched(six, 1, 1, readable_at(6)), ("6", "new", 0))
        many = [(str(number), None) for number in range(30)]
        self.assertEqual(manifest.searched(many, 1, 1, readable_at(25)), (None, "new", manifest.READS))
        error = manifest.no_commit(many, 1, 1, searched=manifest.READS)
        self.assertEqual((error.code, error.message), ("no_readable_version", f"This Lab found no version of this catalog it reads "
                                                       f"among the newest {manifest.READS}. Fix: update Gravitee Lab (make update), "
                                                       "then add it again."))
        self.assertEqual(manifest.searched(many, 1, 1, lambda commit: False)[2], manifest.READS)
        self.assertEqual(manifest.searched(six, 1, 1, lambda commit: False)[2], 0, "every version was read: none needs saying so")

    def test_the_budget_of_time_stops_the_reading_too(self):
        clock = iter(range(0, 1000, 4))
        with mock.patch.object(manifest.time, "monotonic", lambda: next(clock)):
            found = manifest.searched([(str(number), None) for number in range(10)], 1, 1, lambda commit: False, seconds=10)
        self.assertEqual(found[:2], (None, "new"))
        self.assertLess(found[2], 5)

    def test_no_commit_never_says_a_level_it_does_not_have(self):
        """A tip that states no `lab` and gave no commit is one whose files this Lab does not read: it needs a newer Lab, and
        the line names no level, not "None" either."""
        found = manifest.no_commit([("t", None)], 3, 2)
        self.assertEqual(found.code, "no_readable_version")
        self.assertNotIn("None", found.message)
        self.assertNotIn("level", found.message)

    def test_a_version_that_states_no_lab_is_read_by_its_files(self):
        """The form a catalog has now: no `lab`, so `readable` reads the files of the version, newest first."""
        understood = {"a"}
        self.assertEqual(manifest.pick([("t", None), ("a", None)], 1, 1, lambda commit: commit in understood), ("a", "new"))
        self.assertEqual(manifest.pick([("t", None), ("a", None)], 1, 1, lambda commit: True), ("t", "ok"))
        self.assertEqual(manifest.pick([("t", None), ("a", None)], 1, 1, lambda commit: False), (None, "new"))

    def test_a_legacy_lab_is_a_fast_hint_and_a_version_it_lets_through_is_still_read(self):
        asked = []

        def readable(commit):
            asked.append(commit)
            return commit != "t"
        self.assertEqual(manifest.pick([("x", 9), ("t", 1), ("a", 1)], 1, 1, readable), ("a", "new"))
        self.assertEqual(asked, ["t", "a"], "a lab this Lab does not read is passed over unread; one it reads is read")

    def test_at_most_a_few_versions_are_read(self):
        asked = []

        def readable(commit):
            asked.append(commit)
            return False
        levels = [(str(number), None) for number in range(20)]
        self.assertEqual(manifest.pick(levels, 1, 1, readable), (None, "new"))
        self.assertEqual(len(asked), manifest.READS)
        self.assertEqual(manifest.pick(levels, 1, 1, lambda commit: commit == "3", reads=3), (None, "new"),
                         "past the reads none is taken")

    def test_the_case_table_of_9_2(self):
        # contract 3 and contract_min 2 are this Lab; a level is what a catalog.yml says
        this = (3, 2)
        table = [
            ("A. the tip is readable", [("tip", 3), ("old", 2)], ("tip", "ok"), None),
            ("B. the tip needs a newer Lab and an older version is readable", [("tip", 4), ("old", 3), ("older", 1)],
             ("old", "new"), None),
            ("C. no version is readable", [("tip", 5), ("old", 4)], (None, "new"), "no_readable_version"),
            ("D. the Lab was updated and the pin is now too old, the tip is readable", [("tip", 3), ("pin", 1)],
             ("tip", "ok"), None),
            ("E. the pin is too old and no readable tip exists", [("tip", 1), ("pin", 1)], (None, "old"), "lab_old"),
            ("F. the tip is too old at Add", [("tip", 0)], (None, "old"), "lab_old"),
        ]
        for name, levels, answer, refusal in table:
            with self.subTest(name):
                self.assertEqual(manifest.pick(levels, *this), answer)
                if refusal:
                    self.assertEqual(manifest.no_commit(levels, *this).code, refusal)

    def test_pick_is_pure(self):
        levels = [("t", 3), ("a", 2)]
        before = list(levels)
        with mock.patch("subprocess.Popen", side_effect=AssertionError("pick ran a command")), \
                mock.patch("builtins.open", side_effect=AssertionError("pick opened a file")):
            self.assertEqual(manifest.pick(levels, 2, 1), ("a", "new"))
        self.assertEqual(levels, before)


class Refusals(Case):
    """The refusals of the catalogue of errors that the levels give, word for word."""

    def test_a_catalog_that_needs_a_newer_lab(self):
        error = manifest.compat_error(5, 3, 2)
        self.assertEqual((error.code, error.status, error.message),
                         ("lab_new", 400, "This catalog needs a newer Gravitee Lab. Run make update, then add it again."))

    def test_a_catalog_written_for_an_older_lab(self):
        error = manifest.compat_error(1, 3, 2)
        self.assertEqual((error.code, error.status, error.message),
                         ("lab_old", 400, "Written for an older Gravitee Lab. Ask its owners to update it."))

    def test_a_catalog_that_states_no_level_is_no_refusal_of_its_level(self):
        self.assertIsNone(manifest.compat_error(None, 3, 2))

    def test_what_a_published_catalog_uses_that_this_lab_does_not_read_needs_a_newer_lab(self):
        error = manifest.not_understood([("unit.key", "x-gravitee.badge"), ("catalog.key", "mirrors")])
        self.assertEqual((error.code, error.status, error.message),
                         ("lab_new", 400, "This catalog needs a newer Gravitee Lab. Run make update, then add it again."))
        self.assertEqual(error.detail["unknown"], ["x-gravitee.badge", "catalog.yml key mirrors"])

    def test_the_catalog_the_engine_lists_has_no_owner_to_ask(self):
        error = manifest.compat_error(1, 3, 2, listed=True)
        self.assertEqual((error.code, error.message), ("lab_old", "The catalog is not ready for this version of the Lab. "
                                                                  "Try again later."))

    def test_a_level_this_lab_reads_is_no_refusal(self):
        self.assertIsNone(manifest.compat_error(2, 3, 2))
        self.assertIsNone(manifest.compat_error(3, 3, 2, listed=True))

    def test_no_commit_is_the_one_rule_of_9_2(self):
        newer = manifest.no_commit([("t", 5), ("a", 4)], 3, 1)
        self.assertEqual((newer.code, newer.message), ("no_readable_version", "Every version of this catalog needs a newer "
                                                                                "Gravitee Lab. Run make update, then add it again."))
        older = manifest.no_commit([("t", 1)], 4, 3)
        self.assertEqual((older.code, older.message), ("lab_old", "Written for an older Gravitee Lab. Ask its owners to update "
                                                                  "it."))
        self.assertEqual(manifest.no_commit([("t", 1)], 4, 3, listed=True).message,
                         "The catalog is not ready for this version of the Lab. Try again later.")
        self.assertEqual(manifest.no_commit([], 4, 3).code, "lab_old")
        self.assertEqual(manifest.no_commit([], 4, 3).message, "Written for an older Gravitee Lab. Ask its owners to update it.")


class Fields(Case):
    """validate_catalog: the table of 4.1, field by field."""

    def test_a_good_block_has_no_problem(self):
        self.assertEqual(manifest.validate_catalog(GOOD), [])
        self.assertEqual(manifest.validate_catalog(FULL), [])

    def test_a_block_that_is_not_a_mapping_is_one_problem(self):
        for meta in (None, [], "text", 1):
            self.assertEqual(manifest.validate_catalog(meta), ["x-gravitee-catalog must be a mapping"])

    def test_every_required_field_is_required(self):
        for key in ("id", "name", "description", "version"):
            self.assertEqual(problems(**{key: ...}), [f"{key} is required"], key)

    def test_a_catalog_yml_without_the_legacy_fields_is_valid_for_this_lab(self):
        """schema and lab are for older Gravitee Labs: an author never has to write them."""
        self.assertEqual(problems(schema=..., lab=...), [])
        self.assertEqual(problems(schema=...), [])
        self.assertEqual(problems(lab=...), [])

    def test_the_schema_is_a_whole_number_this_lab_supports(self):
        for bad in (0, -1, "1", 1.0, True, None, []):
            self.assertEqual(problems(schema=bad), ["schema must be a whole number, such as 1"], repr(bad))
        self.assertEqual(problems(schema=2), ["schema says the catalog needs a newer Gravitee Lab"])
        self.assertEqual(problems(schema=1), [])

    def test_an_id_is_two_to_twenty_four_lowercase_letters_digits_and_dashes(self):
        for good in ("ab", "a1", "acme-hub", "a" + "b" * 22 + "c", "a-b"):
            self.assertEqual(problems(id=good), [], good)
        for bad in ("a", "A", "Acme", "1ab", "-ab", "ab-", "a_b", "a" * 25, "", " ab", "ab ", "a/b", "../x", "a.b", 12,
                    None, "éa", "ab\n", "@ab"):
            self.assertEqual(problems(id=bad), ["id must be 2 to 24 lowercase letters, digits and dashes"], repr(bad))

    def test_a_reserved_namespace_is_the_registrys_to_refuse_not_the_formats(self):
        for ns in sorted(paths.RESERVED_NS):
            if len(ns) >= 2:
                self.assertEqual(problems(id=ns), [], ns)

    def test_a_name_and_a_description_are_text(self):
        for key in ("name", "description"):
            for bad in ("", "   ", "\n\t", 12, None, [], {}, True):
                self.assertEqual(len(problems(**{key: bad})), 1, (key, bad))
                self.assertTrue(problems(**{key: bad})[0].startswith(f"{key} must be text"), (key, bad))

    def test_a_name_that_is_a_seal_the_lab_never_gives_is_refused(self):
        for name in ("official", "Official", "OFFICIAL", "verified", "Verified", "personal", "this machine", "This  Machine",
                     "ＯＦＦＩＣＩＡＬ", "Offi​cial", "official\x00"):
            self.assertEqual(problems(name=name), [f"name cannot be {registry._shown(name, 40)}"], repr(name))

    def test_a_name_that_only_contains_the_word_is_fine(self):
        for name in ("Official demos", "Not official", "Personal stack", "Machine"):
            self.assertEqual(problems(name=name), [], name)

    def test_the_name_of_a_listed_catalog_is_refused_to_another(self):
        self.assertEqual(manifest.validate_catalog(GOOD, borrowed=["Gravitee public"]), [])
        self.assertEqual(manifest.validate_catalog({**GOOD, "name": "Gravitee public"}, borrowed=["Gravitee public"]),
                         ["name cannot be Gravitee public"])
        self.assertEqual(manifest.validate_catalog({**GOOD, "name": "GRAVITEE  PUBLIC"}, borrowed=["Gravitee public"]),
                         ["name cannot be GRAVITEE PUBLIC"])
        self.assertEqual(manifest.validate_catalog({**GOOD, "name": "Gravitee public"}, borrowed=[]), [])
        self.assertEqual(manifest.validate_catalog({**GOOD, "name": "Gravitee public"}, borrowed=[None, 3]), [])

    def test_a_version_is_three_numbers(self):
        for good in ("0.0.1", "1.4.0", "10.20.30"):
            self.assertEqual(problems(version=good), [], good)
        for bad in ("1.4", "1.4.0.0", "v1.0.0", "1.0.0-rc1", 1.4, 1, None, "", "1.0.x"):
            self.assertEqual(problems(version=bad), ["version must be three numbers with dots, such as 1.0.0"], repr(bad))

    def test_lab_is_a_whole_number_of_zero_or_more(self):
        for good in (0, 1, 2, 99):
            self.assertEqual(problems(lab=good), [], good)
        for bad in (-1, "1", 1.5, True, None, [1]):
            self.assertEqual(problems(lab=bad), ["lab must be a whole number, such as 1: catalog.py upgrade --write writes it"],
                             repr(bad))

    def test_a_license_is_an_spdx_token_or_expression(self):
        for good in ("Apache-2.0", "AGPL-3.0-only", "MIT", "UNLICENSED", "GPL-2.0+", "MIT OR Apache-2.0",
                     "Apache-2.0 WITH LLVM-exception"):
            self.assertEqual(problems(license=good), [], good)
        for bad in ("", " MIT", "MIT ", "a b", "<script>", "Apache 2.0", 2, ["MIT"], "MIT;rm", "M" * 65, "-MIT", "MIT\n"):
            self.assertEqual(problems(license=bad), ["license must be an SPDX identifier, such as Apache-2.0"], repr(bad))
        self.assertEqual(problems(license=None), [])

    def test_images_is_a_registry_prefix_without_a_tag(self):
        for good in ("ghcr.io/acme/lab-hub", "registry.example.com:5000/team/hub", "docker.io/library/x", "localhost/x",
                     "ghcr.io/acme"):
            self.assertEqual(problems(images=good), [], good)
        for bad in ("ghcr.io/acme/lab-hub:1.0", "ghcr.io", "ghcr.io/", "/acme/hub", "ghcr.io/Acme/Hub", "https://ghcr.io/x",
                    "ghcr.io/acme hub", "ghcr.io//x", "ghcr.io/acme/hub@sha256:abc", "", 7, ["ghcr.io/x"]):
            self.assertEqual(problems(images=bad),
                             ["images must be a registry prefix without a tag, such as ghcr.io/acme/lab-hub"], repr(bad))

    def test_contribute_is_pull_request_or_none(self):
        for good in ("pull-request", "none"):
            self.assertEqual(problems(contribute=good), [], good)
        for bad in ("push", "", "None", True, 1, []):
            self.assertEqual(problems(contribute=bad), ["contribute must be pull-request or none"], repr(bad))

    def test_shared_settings_are_names_in_capitals(self):
        self.assertEqual(problems(shared_settings=[]), [])
        self.assertEqual(problems(shared_settings=["GEMINI_API_KEY", "A", "X1_2"]), [])
        for bad in ("GEMINI_API_KEY", ["gemini"], ["1ABC"], ["A B"], ["A-B"], [1], [None], ["A" * 65], {"A": 1},
                    ["OK"] * 101):
            expect = ["shared_settings must be a list of setting names in capitals, such as GEMINI_API_KEY"]
            if bad == ["OK"] * 101:
                self.assertEqual(problems(shared_settings=bad), expect, "more than 100 is not a list a person wrote")
            else:
                self.assertEqual(problems(shared_settings=bad), expect, repr(bad))

    def test_host_access_is_true_or_false_and_absent_is_false(self):
        self.assertEqual(problems(host_access=True), [])
        self.assertEqual(problems(host_access=False), [])
        for bad in ("true", 1, 0, [], {}, "yes"):
            self.assertEqual(problems(host_access=bad), ["host_access must be true or false"], repr(bad))
        self.assertIs(manifest.normalize({**GOOD})["host_access"], False)
        self.assertIs(manifest.normalize({**GOOD, "host_access": True})["host_access"], True)

    def test_moved_from_and_canary_are_lists_of_kind_and_name(self):
        for key in ("moved_from", "canary"):
            wanted = f"{key} must be a list of kind:name, such as addon:echo, at most 50"
            self.assertEqual(problems(**{key: ["addon:echo", "setup:pair", "playground:tour-2"]}), [])
            self.assertEqual(problems(**{key: []}), [])
            for bad in ("addon:echo", ["addon"], ["addon:"], ["addons:echo"], ["addon:Echo"], ["addon:a b"], ["addon:a/b"],
                        ["addon:@ns/x"], ["addon:echo:x"], [1], [None], ["Addon:echo"], ["addon:echo\n"]):
                self.assertEqual(problems(**{key: bad}), [wanted], (key, bad))
            self.assertEqual(problems(**{key: [f"addon:x{i}" for i in range(50)]}), [])
            self.assertEqual(problems(**{key: [f"addon:x{i}" for i in range(51)]}), [wanted])

    def test_a_required_catalog_has_a_namespace_an_address_and_a_floor(self):
        def requires(**more):
            entry = {"name": "Partner Bank", "url": "https://github.com/partner/lab-bank.git", "min": "2.0.0", **more}
            return problems(requires={"catalogs": {"partner-bank": entry}})
        self.assertEqual(requires(), [])
        self.assertEqual(problems(requires={"catalogs": {"p-b": {"url": "https://github.com/partner/lab-bank.git",
                                                                  "min": "2.0.0"}}}), [], "the name is optional")
        where = "requires.catalogs.partner-bank"
        for bad in ("http://example.com/x.git", "ext::sh -c id", "https://user:token@github.com/x/y.git", "", None, 3,
                    "not an address", "git://github.com/x/y.git"):
            self.assertEqual(requires(url=bad),
                             [f"{where}.url must be a Git address without a user name, such as https://github.com/owner/repo.git"],
                             repr(bad))
        for bad in ("2.0", "v2.0.0", 2, None, "", "latest"):
            self.assertEqual(requires(min=bad), [f"{where}.min must be three numbers with dots, such as 1.0.0"], repr(bad))
        self.assertEqual(requires(name=""), [f"{where}.name must be text"])
        self.assertEqual(requires(name=3), [f"{where}.name must be text"])

    def test_the_namespace_of_a_required_catalog_follows_the_grammar(self):
        entry = {"url": "https://github.com/partner/lab-bank.git", "min": "2.0.0"}
        for bad in ("A", "a", "-ab", "a_b", "ab/c", "../x", ""):
            self.assertEqual(problems(requires={"catalogs": {bad: entry}}),
                             [f"requires.catalogs.{bad}: the namespace must be 2 to 24 lowercase letters, digits and dashes"], bad)

    def test_requires_is_a_mapping_of_mappings(self):
        self.assertEqual(problems(requires="partner"), ["requires must be a mapping"])
        self.assertEqual(problems(requires={"catalogs": []}), ["requires.catalogs must be a mapping of namespaces"])
        self.assertEqual(problems(requires={"catalogs": {"pb": "https://x"}}), ["requires.catalogs.pb must hold url and min"])
        self.assertEqual(problems(requires=None), [])
        self.assertEqual(problems(requires={}), [])
        self.assertEqual(problems(requires={"catalogs": {}}), [])
        self.assertEqual(problems(requires={"other": 1}), [], "a key the Lab does not know is not a problem")

    def test_a_block_with_several_faults_names_them_in_the_order_of_the_table(self):
        found = manifest.validate_catalog({"schema": 0, "id": "A", "name": "", "version": "x", "lab": "1", "license": "a b",
                                           "contribute": "x"})
        self.assertEqual([line.split(" ")[0] for line in found],
                         ["schema", "id", "name", "description", "version", "lab", "license", "contribute"])

    def test_a_key_the_lab_does_not_know_is_ignored_and_reported_by_check(self):
        meta = {**GOOD, "colour": "red", "x-note": 1, "Lab": 3}
        self.assertEqual(manifest.validate_catalog(meta), [])
        self.assertEqual(manifest.unknown_keys(meta), ["Lab", "colour", "x-note"])
        self.assertEqual(manifest.unknown_keys(GOOD), [])
        self.assertEqual(manifest.unknown_keys(None), [])

    def test_the_known_keys_are_the_table_of_4_1(self):
        self.assertEqual(set(manifest.KEYS), {"schema", "id", "name", "description", "version", "lab", "license", "images",
                                              "contribute", "shared_settings", "moved_from", "canary", "requires", "host_access"})

    def test_validation_reads_nothing_and_runs_nothing(self):
        with mock.patch("subprocess.Popen", side_effect=AssertionError("ran a command")), \
                mock.patch("builtins.open", side_effect=AssertionError("opened a file")):
            self.assertEqual(manifest.validate_catalog({**GOOD, "requires": {"catalogs": {}}}), [])
            self.assertTrue(manifest.validate_catalog({**GOOD, "id": "A"}))

    def test_hostile_values_are_problems_not_crashes(self):
        deep = cur = {}
        for _ in range(500):
            cur["x"] = {}
            cur = cur["x"]
        for value in ("\x00" * 5, "a" * 100000, deep, [deep], float("nan"), float("inf"), 10 ** 40, -10 ** 40, b"x", {1, 2}):
            for key in ("schema", "id", "name", "description", "version", "lab", "license", "images", "contribute",
                        "shared_settings", "moved_from", "canary", "requires", "host_access"):
                try:
                    manifest.validate_catalog({**GOOD, key: value})
                except Exception as error:   # noqa: BLE001 - the point of the test is that nothing escapes
                    self.fail(f"{key}={type(value).__name__} raised {error!r}")


class Normalizing(Case):

    def test_the_block_is_kept_as_the_lab_shows_it(self):
        found = manifest.normalize(FULL)
        self.assertEqual(found["id"], "acme-hub")
        self.assertEqual((found["name"], found["description"]), ("Acme Hub", "Banking demos for partner workshops"))
        self.assertEqual(found["shared_settings"], ["GEMINI_API_KEY", "OLLAMA_URL"])
        self.assertEqual(found["requires"]["catalogs"]["gravitee"],
                         {"name": "Gravitee public", "url": "https://github.com/acme/demos.git", "min": "1.0.0"})
        self.assertEqual(found["requires"]["catalogs"]["partner-bank"]["url"], "git@github.com:partner/lab-bank.git")
        self.assertNotIn("x-note", found)

    def test_every_optional_key_is_there(self):
        found = manifest.normalize(GOOD)
        self.assertEqual((found["license"], found["images"], found["contribute"]), (None, None, "pull-request"))
        self.assertEqual((found["shared_settings"], found["moved_from"], found["canary"]), ([], [], []))
        self.assertEqual(found["requires"], {"catalogs": {}})
        self.assertEqual(set(found), set(manifest.KEYS))

    def test_a_name_is_cleaned_and_cut_to_forty_with_an_ellipsis(self):
        found = manifest.normalize({**GOOD, "name": "  A​ very\tlong\nname " + "x" * 60})
        self.assertEqual(found["name"], ("A very long name " + "x" * 60)[:39].rstrip() + "…")
        self.assertEqual(len(found["name"]), 40)
        self.assertEqual(manifest.normalize({**GOOD, "name": "ＡＢ"})["name"], "AB", "NFKC")
        self.assertEqual(manifest.normalize({**GOOD, "name": "x" * 40})["name"], "x" * 40)

    def test_a_description_is_one_line_cut_to_ninety(self):
        found = manifest.normalize({**GOOD, "description": "line one\nline two\n\n" + "y" * 200})
        self.assertNotIn("\n", found["description"])
        self.assertEqual(len(found["description"]), 90)
        self.assertTrue(found["description"].endswith("…"))
        self.assertTrue(found["description"].startswith("line one line two yyy"))

    def test_lists_lose_their_repeats_and_keep_their_order(self):
        found = manifest.normalize({**GOOD, "shared_settings": ["B", "A", "B"], "moved_from": ["addon:b", "addon:a", "addon:b"],
                                    "canary": ["playground:x", "playground:x"]})
        self.assertEqual((found["shared_settings"], found["moved_from"], found["canary"]),
                         (["B", "A"], ["addon:b", "addon:a"], ["playground:x"]))

    def test_a_required_catalog_without_a_name_is_named_by_its_namespace(self):
        found = manifest.normalize({**GOOD, "requires": {"catalogs": {"partner": {
            "url": "owner/repo", "min": "1.0.0"}}}})
        self.assertEqual(found["requires"]["catalogs"]["partner"],
                         {"name": "partner", "url": "https://github.com/owner/repo.git", "min": "1.0.0"})


def compose_returning(block, **more):
    """A compose.raw that answers what Compose answers for a file with this block."""
    return mock.Mock(return_value={"name": "stage", **({"x-gravitee-catalog": block} if block is not ... else {}), **more})


class Reading(Case):
    """read_catalog with the Compose that reads the file replaced, and the file on disk as a person would have it."""

    def setUp(self):
        self.folder(paths, compose)
        self.file = self.root / "catalog.yml"
        self.file.write_text("x-gravitee-catalog: {}\n", encoding="utf-8")

    def read(self, block, **how):
        self.patch(compose, "raw", compose_returning(block))
        return manifest.read_catalog("catalog.yml", **how)

    def test_a_good_file_is_read_cleaned_with_the_keys_it_does_not_know(self):
        found = self.read(FULL)
        self.assertEqual((found["id"], found["name"], found["version"], found["lab"]), ("acme-hub", "Acme Hub", "1.4.0", 1))
        self.assertEqual(found["unknown"], ["x-note"])
        self.assertEqual(found["contribute"], "pull-request")

    def test_the_file_is_read_by_compose_with_a_time_and_by_its_path(self):
        raw = compose_returning(GOOD)
        self.patch(compose, "raw", raw)
        manifest.read_catalog("catalog.yml")
        raw.assert_called_once()
        self.assertEqual(raw.call_args.args, ("catalog.yml",))
        self.assertLessEqual(raw.call_args.kwargs["timeout"], 30)

    def test_a_file_that_is_not_there_is_not_a_catalog(self):
        self.file.unlink()
        error = self.assertRaisesCatalog(lambda: manifest.read_catalog("catalog.yml"))
        self.assertEqual((error.code, error.message), ("not_catalog", "this repository has no catalog.yml, so it is not a catalog."))

    def test_the_line_names_the_repository_or_the_folder(self):
        self.file.unlink()
        repo = gitx.parse_address("acme/lab-hub")
        error = self.assertRaisesCatalog(lambda: manifest.read_catalog("catalog.yml", repo))
        self.assertEqual((error.code, error.message), ("not_catalog", "acme/lab-hub has no catalog.yml, so it is not a catalog."))
        folder = gitx.Address("folder", "", str(self.root), "~/code/hub", "", "")
        error = self.assertRaisesCatalog(lambda: manifest.read_catalog("catalog.yml", folder))
        self.assertEqual((error.code, error.message), ("folder_not_catalog", "~/code/hub has no catalog.yml, so it is not a catalog."))

    def test_a_link_in_the_place_of_the_file_is_not_followed(self):
        outside = self.root / "outside.yml"
        outside.write_text("x-gravitee-catalog: {}\n", encoding="utf-8")
        self.file.unlink()
        self.file.symlink_to(outside)
        raw = compose_returning(GOOD)
        self.patch(compose, "raw", raw)
        error = self.assertRaisesCatalog(lambda: manifest.read_catalog("catalog.yml"))
        self.assertEqual(error.code, "not_catalog")
        raw.assert_not_called()

    def test_a_folder_in_the_place_of_the_file_is_not_a_catalog(self):
        self.file.unlink()
        self.file.mkdir()
        self.assertEqual(self.assertRaisesCatalog(lambda: manifest.read_catalog("catalog.yml")).code, "not_catalog")

    def test_a_file_over_a_megabyte_is_refused_before_compose_reads_it(self):
        self.file.write_text("# " + "x" * (manifest.FILE_LIMIT + 10) + "\n", encoding="utf-8")
        raw = compose_returning(GOOD)
        self.patch(compose, "raw", raw)
        error = self.assertRaisesCatalog(lambda: manifest.read_catalog("catalog.yml"))
        self.assertEqual((error.code, error.message), ("catalog_invalid", "catalog.yml: it is larger than 1 MB."))
        raw.assert_not_called()

    def test_a_file_compose_cannot_read_is_invalid_and_its_output_is_for_the_dialog(self):
        self.patch(compose, "raw", mock.Mock(side_effect=compose.ComposeError("yaml: line 2: did not find expected key")))
        error = self.assertRaisesCatalog(lambda: manifest.read_catalog("catalog.yml"))
        self.assertEqual((error.code, error.status, error.message), ("catalog_invalid", 400, "catalog.yml: it could not be read."))
        self.assertEqual(error.detail["output"], "yaml: line 2: did not find expected key")

    def test_a_file_without_the_block_is_invalid(self):
        self.patch(compose, "raw", compose_returning(...))
        error = self.assertRaisesCatalog(lambda: manifest.read_catalog("catalog.yml"))
        self.assertEqual((error.code, error.message), ("catalog_invalid", "catalog.yml: it has no x-gravitee-catalog block."))
        self.patch(compose, "raw", mock.Mock(return_value=None))
        self.assertEqual(self.assertRaisesCatalog(lambda: manifest.read_catalog("catalog.yml")).code, "catalog_invalid")

    def test_a_block_that_is_not_a_mapping_is_invalid(self):
        for block in ("text", [1], 3, True):
            self.patch(compose, "raw", compose_returning(block))
            error = self.assertRaisesCatalog(lambda: manifest.read_catalog("catalog.yml"))
            self.assertEqual((error.code, error.message), ("catalog_invalid",
                                                           "catalog.yml: x-gravitee-catalog must be a mapping."), block)

    def test_a_newer_format_is_asked_for_a_newer_lab_before_anything_else_is_judged(self):
        error = self.assertRaisesCatalog(lambda: self.read({"schema": 2, "id": "A", "lab": "x"}))
        self.assertEqual((error.code, error.status, error.message),
                         ("schema_new", 400, "This catalog needs a newer Gravitee Lab. Run make update, then add it again."))

    def test_the_first_problem_is_the_line_and_all_of_them_are_in_the_detail(self):
        error = self.assertRaisesCatalog(lambda: self.read({**GOOD, "id": "A", "version": "x"}))
        self.assertEqual((error.code, error.message), ("catalog_invalid",
                                                       "catalog.yml: id must be 2 to 24 lowercase letters, digits and dashes."))
        self.assertEqual(error.detail["problems"], ["id must be 2 to 24 lowercase letters, digits and dashes",
                                                    "version must be three numbers with dots, such as 1.0.0"])

    def test_a_name_borrowed_from_a_listed_catalog_is_refused(self):
        error = self.assertRaisesCatalog(lambda: self.read({**GOOD, "name": "Gravitee public"}, borrowed=["Gravitee public"]))
        self.assertEqual(error.message, "catalog.yml: name cannot be Gravitee public.")

    def test_a_catalog_may_be_older_than_this_lab_reads_and_still_be_read(self):
        self.assertEqual(self.read({**GOOD, "lab": 0})["lab"], 0)
        self.assertEqual(self.read({**GOOD, "lab": 99})["lab"], 99)

    def test_a_catalog_without_the_legacy_fields_is_read_with_none_for_them(self):
        meta = {key: value for key, value in GOOD.items() if key not in ("schema", "lab")}
        found = self.read(meta)
        self.assertEqual((found["schema"], found["lab"], found["unknown"]), (None, None, []))

    def test_a_reserved_namespace_is_read_the_registry_decides(self):
        self.assertEqual(self.read({**GOOD, "id": "official"})["id"], "official")

    def test_an_absolute_path_is_read_where_it_is(self):
        raw = compose_returning(GOOD)
        self.patch(compose, "raw", raw)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "catalog.yml").write_text("x-gravitee-catalog: {}\n", encoding="utf-8")
        manifest.read_catalog(str(elsewhere / "catalog.yml"))
        self.assertEqual(raw.call_args.args, (str(elsewhere / "catalog.yml"),))

    # -- the file is screened before Compose is asked anything -------------------------------------------------------

    MORE = "catalog.yml: it may hold only the x-gravitee-catalog block."

    def refused(self, text, message=None):
        """`text` is the file; Compose is replaced by something that fails the test if it is asked."""
        self.file.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
        asked = mock.Mock(side_effect=AssertionError("Compose was asked about a file that holds more than its block"))
        self.patch(compose, "raw", asked)
        self.patch(compose, "run", asked)
        error = self.assertRaisesCatalog(lambda: manifest.read_catalog("catalog.yml"))
        self.assertEqual((error.code, error.status, error.message), ("catalog_invalid", 400, message or self.MORE), text)
        asked.assert_not_called()

    def test_a_file_that_holds_more_than_its_block_never_reaches_compose(self):
        for text in (
                "x-gravitee-catalog: {}\ninclude:\n  - /etc/hosts\n",   # an include after the block
                "include:\n  - /etc/hosts\nx-gravitee-catalog: {}\n",   # before it
                "include: [/etc/hosts]\nx-gravitee-catalog: {}\n",   # in the flow
                "include:\n  - ../../..\nx-gravitee-catalog: {}\n",
                "  include:\n    - /etc/hosts\n  x-gravitee-catalog: {}\n",   # the whole document, indented
                "\tinclude: [a]\nx-gravitee-catalog: {}\n",
                "# a comment\n  include: [a]\n",   # an indented first line after a comment
                "---\n  include: [a]\n",
                '{"x-gravitee-catalog": {}, "include": ["/etc/hosts"]}\n',   # a flow document
                "[]\n",
                "services:\n  a: {image: b}\nx-gravitee-catalog: {}\n",
                "name: stage\nx-gravitee-catalog: {}\n",
                "x-gravitee-catalog: {}\n<<: *a\n",   # a merge key
                "? include\n: [a]\nx-gravitee-catalog: {}\n",   # an explicit key
                '"include": [a]\nx-gravitee-catalog: {}\n',   # a quoted key
                "&a include: [a]\nx-gravitee-catalog: {}\n",
                "!!map {include: [a]}\n",
                "%YAML 1.2\n---\nx-gravitee-catalog: {}\n",   # a directive
                "---\nx-gravitee-catalog: {}\n---\nx-more: 1\n",   # a second document
                "x-gravitee-catalog: {}\n...\n",
                "--- {include: [a]}\n",   # the content of the document on its start line
                "---x\nx-gravitee-catalog: {}\n",
                "x-gravitee-catalog: {}\nx_gravitee: 1\n",
                "X-gravitee-catalog: {}\n",
        ):
            with self.subTest(text=text):
                self.refused(text)

    def test_a_pipe_an_include_names_is_never_opened(self):
        os.mkfifo(self.root / "pipe")
        self.refused("include:\n  - pipe\nx-gravitee-catalog: {}\n")

    def test_a_file_with_a_control_character_or_a_line_break_a_reader_would_not_split_is_refused_in_its_own_words(self):
        odd = "catalog.yml: it holds a control character or an unusual line break."
        for text in ("x-gravitee-catalog: {}\x00\n", "x-gravitee-catalog: {}\n\x0binclude: [a]\n", "x-a: 1\x85include: [a]\n",
                     "x-a: 1\u2028include: [a]\n", "x-a: 1\u2029include: [a]\n", "x-a: 1\x1b[0m\n", "x-a: 1\x7f\n",
                     "x-a: \ufeffb\n"):
            with self.subTest(text=text):
                self.refused(text, odd)

    def test_a_file_that_is_not_text_is_a_file_that_cannot_be_read(self):
        self.refused(b"x-gravitee-catalog: {}\n\xff\xfe", "catalog.yml: it could not be read.")

    def test_the_forms_a_catalog_file_is_written_in_are_read(self):
        raw = compose_returning(GOOD)
        self.patch(compose, "raw", raw)
        for text in ("x-gravitee-catalog:\n  id: acme-hub\n",
                     "# a comment first\n\n   # an indented one\nx-gravitee-catalog:\n  id: acme-hub\n\n\n",
                     "---\nx-gravitee-catalog:\n  id: acme-hub\n",
                     "--- # the document\nx-gravitee-catalog:\n  id: acme-hub\n",
                     "x-gravitee-catalog:\r\n  id: acme-hub\r\n",   # Windows line ends
                     "x-gravitee-catalog:\r  id: acme-hub\r",
                     "\ufeffx-gravitee-catalog:\n  id: acme-hub\n",   # a byte order mark
                     '"x-gravitee-catalog":\n  id: acme-hub\n', "'x-gravitee-catalog':\n  id: acme-hub\n",
                     "x-gravitee-catalog: {id: acme-hub,\n  name: A}\n",   # a flow that goes on the next line
                     "x-gravitee-catalog:\n  id: acme-hub\nx-notes: |\n  include: not a key\n  services: nor this\n",
                     "x-gravitee-catalog:\n  id: acme-hub  # services: a comment\n", "", "\n# only a comment\n"):
            with self.subTest(text=text):
                self.file.write_bytes(text.encode("utf-8"))
                raw.reset_mock()
                manifest.read_catalog("catalog.yml")
                raw.assert_called_once()

    def test_a_file_the_size_of_the_limit_is_read_and_one_byte_more_is_not(self):
        raw = compose_returning(GOOD)
        self.patch(compose, "raw", raw)
        head = "x-gravitee-catalog: {}\n# "
        self.file.write_text(head + "x" * (manifest.FILE_LIMIT - len(head)), encoding="utf-8")
        manifest.read_catalog("catalog.yml")
        raw.assert_called_once()
        self.file.write_text(head + "x" * (manifest.FILE_LIMIT - len(head) + 1), encoding="utf-8")
        self.assertEqual(self.assertRaisesCatalog(lambda: manifest.read_catalog("catalog.yml")).message,
                         "catalog.yml: it is larger than 1 MB.")

    def test_a_file_that_becomes_a_link_after_it_was_looked_at_is_not_followed(self):
        outside = self.root / "outside.yml"
        outside.write_text("x-gravitee-catalog: {}\n", encoding="utf-8")
        looked = self.file.lstat()
        self.file.unlink()
        self.file.symlink_to(outside)
        raw = compose_returning(GOOD)
        self.patch(compose, "raw", raw)
        self.patch(type(self.file), "lstat", lambda path, **how: looked)   # what a swap between the look and the read does
        error = self.assertRaisesCatalog(lambda: manifest.read_catalog("catalog.yml"))
        self.assertEqual((error.code, error.message), ("catalog_invalid", "catalog.yml: it could not be read."))
        raw.assert_not_called()

    def test_a_pipe_in_the_place_of_the_file_is_not_opened(self):
        self.file.unlink()
        os.mkfifo(self.file)
        raw = compose_returning(GOOD)
        self.patch(compose, "raw", raw)
        self.assertEqual(self.assertRaisesCatalog(lambda: manifest.read_catalog("catalog.yml")).code, "not_catalog")
        raw.assert_not_called()

    def assertRaisesCatalog(self, call):
        with self.assertRaises(registry.CatalogError) as raised:
            call()
        return raised.exception


class WithCompose(Case):
    """The fixture catalogs read by the real Compose."""

    def setUp(self):
        fixtures.need_compose()
        self.folder(paths, compose)
        self.docker = os.path.join(os.path.expanduser("~"), ".docker")
        self.start(mock.patch.dict(os.environ, {"DOCKER_CONFIG": os.environ.get("DOCKER_CONFIG", self.docker)}))

    def put(self, text, name="catalog.yml"):
        (self.root / name).write_text(text, encoding="utf-8")
        return name

    def test_every_fixture_catalog_is_a_good_catalog_the_way_compose_reads_it(self):
        for ns in ("fixture", "partner", "lab-new", "lab-old", "hostile"):
            with self.subTest(ns):
                found = manifest.read_catalog(f"{os.path.relpath(fixtures.CATALOGS / ns, self.root)}/catalog.yml")
                self.assertEqual(found["id"], ns)
                self.assertEqual(found["unknown"], [])
        found = manifest.read_catalog(os.path.join(fixtures.CATALOGS, "fixture", "catalog.yml"))
        self.assertEqual((found["name"], found["version"], found["lab"], found["license"]),
                         ("Fixture catalog", "1.0.0", 1, "AGPL-3.0-only"))
        self.assertEqual(found["requires"]["catalogs"]["partner"],
                         {"name": "Partner fixture", "url": "https://example.invalid/partner.git", "min": "1.0.0"})
        self.assertEqual(manifest.read_catalog(os.path.join(fixtures.CATALOGS, "hostile", "catalog.yml"))["shared_settings"],
                         ["FIXTURE_SHARED_TOKEN"])
        self.assertEqual(manifest.read_catalog(os.path.join(fixtures.CATALOGS, "lab-old", "catalog.yml"))["lab"], 0)

    def test_the_example_of_section_4_1_is_valid_with_its_comments(self):
        name = self.put("""x-gravitee-catalog:
  schema: 1                     # format of this file and of the layout
  id: acme-hub                  # the namespace of every item: @acme-hub/<name>
  name: Acme Hub                # shown; at most 40 characters
  description: Banking demos for partner workshops     # one line, at most 90 characters
  version: 1.4.0
  lab: 1
  license: Apache-2.0
  images: ghcr.io/acme/lab-hub
  contribute: pull-request
  shared_settings: [GEMINI_API_KEY]
  canary:
    - playground:tour
  moved_from: [addon:echo]
  requires:
    catalogs:
      gravitee:
        name: Gravitee public
        url: https://github.com/acme/base-demos.git
        min: 1.0.0
""")
        found = manifest.read_catalog(name)
        self.assertEqual((found["id"], found["shared_settings"], found["canary"], found["moved_from"]),
                         ("acme-hub", ["GEMINI_API_KEY"], ["playground:tour"], ["addon:echo"]))
        self.assertEqual(found["requires"]["catalogs"]["gravitee"]["min"], "1.0.0")

    def test_the_strict_reading_gives_the_level_the_tolerant_one_could_not(self):
        """What `_lab_of` leaves to Add: the same file, read with Compose, states its level, and the refusal is the real one."""
        for written, level in (("0x2", 2), ("&level 2", 2), ("\n    2", 2), ("0x9", 9)):
            with self.subTest(written=written):
                name = self.put(f"x-gravitee-catalog:\n  schema: 1\n  id: acme-hub\n  name: A\n  description: B\n  version: 1.0.0\n"
                                f"  lab: {written}\n")
                self.assertIsNone(manifest._lab_of((self.root / name).read_bytes()))
                found = manifest.read_catalog(name)
                self.assertEqual(found["lab"], level)
                refusal = manifest.compat_error(found["lab"], 3, 1)
                self.assertEqual(refusal.code if refusal else None, "lab_new" if level > 3 else None)
        self.assertEqual(manifest.pick([("t", None)], 3, 1), ("t", "ok"))

    def test_a_version_written_as_a_number_is_not_a_version(self):
        name = self.put("x-gravitee-catalog:\n  schema: 1\n  id: acme-hub\n  name: A\n  description: B\n  version: 1.4\n  lab: 1\n")
        with self.assertRaises(registry.CatalogError) as raised:
            manifest.read_catalog(name)
        self.assertEqual(raised.exception.message, "catalog.yml: version must be three numbers with dots, such as 1.0.0.")

    def test_a_file_with_a_syntax_error_is_invalid(self):
        name = self.put("x-gravitee-catalog:\n  id: [unclosed\n")
        with self.assertRaises(registry.CatalogError) as raised:
            manifest.read_catalog(name)
        self.assertEqual((raised.exception.code, raised.exception.message), ("catalog_invalid", "catalog.yml: it could not be read."))
        self.assertTrue(raised.exception.detail["output"])

    def test_a_file_with_no_block_is_invalid(self):
        name = self.put("x-other: {}\n")
        with self.assertRaises(registry.CatalogError) as raised:
            manifest.read_catalog(name)
        self.assertEqual(raised.exception.message, "catalog.yml: it has no x-gravitee-catalog block.")

    def test_a_file_that_is_a_compose_file_is_refused_for_what_it_holds_besides_its_block(self):
        name = self.put("services: {}\n")
        with self.assertRaises(registry.CatalogError) as raised:
            manifest.read_catalog(name)
        self.assertEqual(raised.exception.message, "catalog.yml: it may hold only the x-gravitee-catalog block.")

    def test_an_include_of_a_pipe_or_of_a_file_outside_is_refused_at_once_and_compose_never_opens_it(self):
        os.mkfifo(self.root / "pipe")
        (self.root / "other.yml").write_text("x-gravitee-catalog: {id: other}\n", encoding="utf-8")
        for target in ("pipe", "other.yml", "/etc/hosts", "../../.."):
            with self.subTest(target):
                name = self.put(f"x-gravitee-catalog:\n  schema: 1\n  id: acme-hub\n  name: A\n  description: B\n"
                                f"  version: 1.0.0\n  lab: 1\ninclude:\n  - {target}\n")
                began = time.monotonic()
                with self.assertRaises(registry.CatalogError) as raised:
                    manifest.read_catalog(name)
                self.assertEqual(raised.exception.message, "catalog.yml: it may hold only the x-gravitee-catalog block.")
                self.assertLess(time.monotonic() - began, 5, "nothing waited for a pipe")

    def test_a_variable_is_not_replaced(self):
        name = self.put("x-gravitee-catalog:\n  schema: 1\n  id: acme-hub\n  name: ${HOME}\n  description: B\n  version: 1.0.0\n  lab: 1\n")
        self.assertEqual(manifest.read_catalog(name)["name"], "${HOME}")


class Legacy(Case):
    """The lines of catalog.yml that older Gravitee Labs read: one comment for them, wherever catalog.py writes them."""

    def test_the_template_writes_them_with_the_comment_with_legacy_writes(self):
        """`catalog.py new` renders the template and `with_legacy` adds missing lines: the two say the same, from one constant."""
        template = (standard.TEMPLATE / "seeded" / "catalog.yml").read_text(encoding="utf-8")
        lines = [line for line in template.splitlines() if line.strip().split(":")[0] in manifest.LEGACY]
        self.assertEqual(lines, [f"  schema: {manifest.SCHEMA}   {manifest.LEGACY_NOTE}", f"  lab: 1   {manifest.LEGACY_NOTE}"])
        bare = "".join(line for line in template.splitlines(True) if line.rstrip("\n") not in lines)
        self.assertEqual(manifest.with_legacy(bare, 1), template, "the lines it adds are the template's, in the same places")

    def test_a_line_that_is_there_keeps_its_place_and_its_comment(self):
        text = "x-gravitee-catalog:\n  schema: 1   # mine\n  id: a\n  version: 1.0.0\n  lab: 1\n  license: MIT\n"
        self.assertEqual(manifest.with_legacy(text, 3), text.replace("lab: 1", "lab: 3"))
        self.assertEqual(manifest.with_legacy(text, None), text, "without a level to write, the one it has stays")
        self.assertEqual(manifest.with_legacy("plain text\n", 2), "plain text\n", "a file with no block is the check's to tell")


# ---------------------------------------------------------------------------------------------------------------------
# The versions of a catalog in its history
# ---------------------------------------------------------------------------------------------------------------------

def catalog_yml(lab=1, version="1.0.0", name="History fixture"):
    return (f"x-gravitee-catalog:\n  schema: 1\n  id: history\n  name: {name}\n  description: A history\n"
            f"  version: {version}\n  lab: {lab}\n")


class History(Case):
    """levels over a repository whose catalog.yml changes at some commits and not at others."""

    def setUp(self):
        gitfixture.require()
        self.folder(paths)
        self.home = gitfixture.isolate(self)
        self.patch(gitx, "_ending", False)   # a test of the exit function elsewhere leaves it for good
        self.repo = gitfixture.make_repo(self.home / "history.git", {"catalog.yml": catalog_yml(1, "1.0.0"),
                                                                       "addons/a/compose.yml": "services: {}\n"})
        self.store = self.root / "store.git"
        self.address = gitx.parse_address(gitfixture.url(self.repo))
        self.commits = [gitfixture.tip(self.repo)]

    def push(self, files, message="Change"):
        self.commits.append(gitfixture.commit(self.repo, files, message))
        bare.fetch(self.address, "main", self.store)
        return self.commits[-1]

    def levels(self, **how):
        bare.fetch(self.address, "main", self.store)
        return manifest.levels(self.store, bare.resolve(self.store, "tip"), **how)

    def test_a_catalog_whose_file_never_changed_is_one_version(self):
        self.push({"README.md": "x\n"})
        self.push({"README.md": "y\n"})
        self.assertEqual(self.levels(), [(self.commits[-1], 1)])

    def test_each_form_of_the_file_is_one_version_ending_at_the_commit_before_the_next_change(self):
        self.push({"README.md": "x\n"})                      # 1
        self.push({"catalog.yml": catalog_yml(2, "2.0.0")})           # 2: lab 2
        self.push({"README.md": "y\n"})                      # 3
        self.push({"catalog.yml": catalog_yml(3, "3.0.0")})           # 4: lab 3
        self.push({"README.md": "z\n"})                      # 5: tip
        c = self.commits
        self.assertEqual(self.levels(), [(c[5], 3), (c[3], 2), (c[1], 1)])

    def test_the_tip_that_changed_the_file_is_the_first_version(self):
        self.push({"catalog.yml": catalog_yml(2, "2.0.0")})
        c = self.commits
        self.assertEqual(self.levels(), [(c[1], 2), (c[0], 1)])

    def test_the_levels_feed_the_pick(self):
        self.push({"catalog.yml": catalog_yml(2, "2.0.0")})
        self.push({"README.md": "x\n"})
        c = self.commits
        found = self.levels()
        self.assertEqual(manifest.pick(found, 1, 1), (c[0], "new"))
        self.assertEqual(manifest.pick(found, 2, 1), (c[2], "ok"))
        self.assertEqual(manifest.pick(found, 3, 3), (None, "old"))

    def test_a_revert_is_one_more_version(self):
        self.push({"catalog.yml": catalog_yml(2, "2.0.0")})           # 1
        self.push({"catalog.yml": catalog_yml(1, "1.0.0")})           # 2: back to the first form
        self.push({"README.md": "x\n"})                      # 3
        c = self.commits
        self.assertEqual(self.levels(), [(c[3], 1), (c[1], 2), (c[0], 1)])
        self.assertEqual(manifest.pick(self.levels(), 1, 1), (c[3], "ok"))

    def test_a_file_that_does_not_state_a_level_is_read_by_its_files(self):
        """The form a catalog.yml has now: no `lab`. The version is None, and what decides is what its files use."""
        self.push({"catalog.yml": "x-gravitee-catalog:\n  id: history\n"})
        c = self.commits
        self.assertEqual(self.levels(), [(c[1], None), (c[0], 1)])
        self.assertEqual(manifest.pick(self.levels(), 1, 1), (c[1], "ok"))
        self.assertEqual(manifest.pick(self.levels(), 1, 1, lambda commit: commit != c[1]), (c[0], "new"))

    def test_a_form_without_lab_is_a_version_for_each_change_of_its_units(self):
        """Without `lab` nothing in catalog.yml changes when a unit starts to use what an older Lab does not read: each commit
        that changed the units is where the newest version this Lab reads may end."""
        self.push({"catalog.yml": "x-gravitee-catalog:\n  id: history\n"})          # 1: no lab
        self.push({"addons/a/compose.yml": "services: {a: {image: busybox}}\n"})    # 2: a unit changes
        self.push({"README.md": "# History\n"})                                      # 3: nothing of a unit
        self.push({"playgrounds/p/compose.yml": "services: {}\n"})                   # 4: another unit: the tip
        c = self.commits
        self.assertEqual(self.levels(), [(c[4], None), (c[3], None), (c[1], None), (c[0], 1)])
        newest_read = manifest.pick(self.levels(), 1, 1, lambda commit: commit not in (c[4],))
        self.assertEqual(newest_read, (c[3], "new"), "the newest commit before the unit this Lab does not read")
        self.assertEqual(manifest.pick(self.levels(), 1, 1, lambda commit: commit in (c[0], c[1])), (c[1], "new"))

    def test_a_form_that_states_its_lab_is_a_version_for_each_change_of_its_units_too(self):
        """The `lab` a published catalog keeps for older Labs says nothing of a key its units started to use: the files do."""
        self.push({"addons/a/compose.yml": "services: {a: {image: busybox}}\n"})
        self.push({"addons/a/compose.yml": "services: {b: {image: busybox}}\n"})
        c = self.commits
        self.assertEqual(self.levels(), [(c[2], 1), (c[1], 1), (c[0], 1)])
        self.assertEqual(manifest.pick(self.levels(), 1, 1, lambda commit: commit != c[2]), (c[1], "new"))
        self.assertEqual(manifest.pick(self.levels(), 2, 2, lambda commit: True), (None, "old"), "a hint is read first")

    def test_a_form_the_reader_cannot_be_sure_of_is_a_level_it_does_not_invent(self):
        """An anchor, a number spelled another way, a scalar on two lines: the tolerant reader leaves them out and says so
        (`partial`), and Compose reads each as a level. It is "cannot say" here, and the strict reading decides at Add."""
        spelled = {"hex": "0x2", "anchor": "&level 2", "next line": "\n    2", "plus": "+2", "underscore": "2_0"}
        for how, written in spelled.items():
            with self.subTest(how=how):
                self.push({"catalog.yml": catalog_yml(written, f"2.{len(self.commits)}.0")})
                c, found = self.commits, self.levels()
                self.assertEqual((found[0], found[-1]), ((c[-1], None), (c[0], 1)))
                self.assertEqual(manifest.pick(found, 2, 1), (c[-1], "ok"), "the tip may be read: it is the pin")
                self.assertEqual(manifest.pick(found, 1, 1), (c[-1], "ok"), "and the strict reading refuses it at Add")

    def test_a_catalog_that_does_not_state_its_level_is_not_one_that_cannot_be_read(self):
        """An unusual word, or a file that is no catalog, is a level no Lab reads (0); a file with no `lab` and a form the
        reader gave up on are None: the files say."""
        for how, (text, level) in {"no lab": ("x-gravitee-catalog:\n  schema: 1\n  id: history\n", None),
                                   "a word": ("x-gravitee-catalog:\n  schema: 1\n  id: history\n  lab: soon\n", 0),
                                   "not yaml": ("plain text\n", 0)}.items():
            with self.subTest(how=how):
                self.push({"catalog.yml": text})
                self.assertEqual(self.levels()[0][1], level)
        self.assertEqual(manifest._lab_of(b""), 0)
        self.assertEqual(manifest._lab_of(None), 0)
        self.assertEqual(manifest._lab_of(catalog_yml(3).encode()), 3)
        self.assertEqual(manifest._lab_of(b"x-gravitee-catalog:\n  lab: &l 3\n"), None)

    def test_a_form_of_the_file_that_was_deleted_is_left_out(self):
        self.push({"catalog.yml": None})
        self.push({"catalog.yml": catalog_yml(2, "2.0.0")})
        c = self.commits
        self.assertEqual(self.levels(), [(c[2], 2), (c[0], 1)], "the commits between hold no catalog")

    def test_a_tip_with_no_catalog_file_is_not_a_catalog(self):
        self.push({"catalog.yml": None})
        with self.assertRaises(registry.CatalogError) as raised:
            self.levels()
        self.assertEqual((raised.exception.code, raised.exception.message),
                         ("not_catalog", "this repository has no catalog.yml, so it is not a catalog."))
        with self.assertRaises(registry.CatalogError) as raised:
            self.levels(address=self.address)
        self.assertEqual(raised.exception.code, "not_catalog")
        self.assertIn(self.address.repo or self.address.display, raised.exception.message)

    def test_a_tip_the_store_does_not_hold_is_the_damage_a_fetch_repairs(self):
        self.push({"README.md": "x\n"})
        with self.assertRaises(registry.CatalogError) as raised:
            manifest.levels(self.store, "a" * 40)
        self.assertEqual(raised.exception.code, "fsck")

    def test_a_history_is_cut_at_the_limit(self):
        for number in range(6):
            self.push({"catalog.yml": catalog_yml(1, f"1.{number + 1}.0")})
        self.assertEqual(len(self.levels()), 7)
        self.assertEqual(len(self.levels(limit=3)), 3)
        self.assertEqual(self.levels(limit=1)[0][0], self.commits[-1])

    def test_the_levels_are_read_from_the_store_only(self):
        self.push({"catalog.yml": catalog_yml(2, "2.0.0")})
        with mock.patch("socket.socket", side_effect=AssertionError("network")):
            self.assertEqual(manifest.levels(self.store, bare.resolve(self.store, "tip"))[0][1], 2)

    def test_a_catalog_file_that_is_a_link_is_no_catalog_file(self):
        self.push({"catalog.yml": gitfixture.Symlink("addons/a/compose.yml")})
        with self.assertRaises(registry.CatalogError) as raised:
            self.levels()
        self.assertEqual(raised.exception.code, "not_catalog")


class Offering(History):
    """offer: the releases of a catalog that makes them, the commits of one that makes none."""

    def tag(self, name, commit):
        gitfixture.git("tag", "-f", name, commit, repo=self.repo)

    def offer(self, contract=1, contract_min=1):
        bare.fetch(self.address, "main", self.store)
        return manifest.offer(self.store, bare.resolve(self.store, "tip"), contract, contract_min)

    def test_a_catalog_with_no_release_is_offered_its_newest_commit_as_before(self):
        self.push({"addons/a/README.md": "x\n"})
        found = self.offer()
        self.assertEqual((found.commit, found.tip, found.release), (self.commits[-1], "ok", None))
        self.assertEqual(found.levels, self.levels())

    def test_a_catalog_with_releases_is_offered_the_greatest_and_not_the_commits_after_it(self):
        self.tag("v1.0.0", self.commits[0])
        self.push({"catalog.yml": catalog_yml(1, "1.1.0")}, "Release 1.1.0")
        self.tag("v1.1.0", self.commits[-1])
        self.push({"addons/a/README.md": "x\n"}, "Not released yet")
        found = self.offer()
        self.assertEqual((found.commit, found.tip, found.release), (self.commits[1], "ok", "1.1.0"))
        self.assertEqual(found.levels, [(self.commits[1], 1), (self.commits[0], 1)])
        self.assertEqual(manifest.releases(self.store, self.commits[0]), [(self.commits[0], 1, "1.0.0")])

    def test_a_tag_its_catalog_file_does_not_agree_with_is_no_release(self):
        self.push({"addons/a/README.md": "x\n"})
        self.tag("v2.0.0", self.commits[-1])   # the file still says 1.0.0
        self.assertEqual(self.offer().release, None, "a catalog with no release: its newest commit")
        self.tag("v1.0.0", self.commits[0])
        self.assertEqual(self.offer()[:3], (self.commits[0], "ok", "1.0.0"))

    def test_a_release_for_a_newer_lab_leaves_the_greatest_this_lab_reads_and_says_a_newer_one_exists(self):
        self.tag("v1.0.0", self.commits[0])
        self.push({"catalog.yml": catalog_yml(2, "2.0.0")})
        self.tag("v2.0.0", self.commits[-1])
        self.assertEqual(self.offer()[:3], (self.commits[0], "new", "1.0.0"))
        self.assertEqual(self.offer(2, 1)[:3], (self.commits[1], "ok", "2.0.0"))
        found = self.offer(3, 3)
        self.assertEqual(found[:3], (None, "old", None))
        self.assertEqual(manifest.no_commit(found.levels, 3, 3).code, "lab_old")

    def test_a_released_catalog_whose_tip_has_no_catalog_file_is_not_a_catalog(self):
        self.tag("v1.0.0", self.commits[0])
        self.push({"catalog.yml": None})
        with self.assertRaises(registry.CatalogError) as raised:
            self.offer()
        self.assertEqual(raised.exception.code, "not_catalog")

    def test_release_of_says_whether_a_commit_is_the_release_of_its_version(self):
        self.push({"addons/a/README.md": "x\n"})
        self.tag("v1.0.0", self.commits[0])
        self.offer()
        self.assertEqual(manifest.release_of(self.store, self.commits[0], "1.0.0"), "1.0.0")
        self.assertIsNone(manifest.release_of(self.store, self.commits[1], "1.0.0"), "a commit after it")
        self.assertIsNone(manifest.release_of(self.store, None, "1.0.0"))
        self.assertIsNone(manifest.release_of(self.store, self.commits[0], None))


# ---------------------------------------------------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------------------------------------------------

def unit(kind, uid, **more):
    name = uid.split("/")[-1]
    base = {"id": uid, "kind": kind, "name": name.title(), "file": f".lab/catalogs/acme-hub/{kind}s/{name}/compose.yml",
            "category": "API", "description": f"The {uid}", "requires": {}, "conflicts": {}}
    return {**base, **more}


class Indexing(Case):

    def setUp(self):
        self.folder(paths)
        self.patch(paths, "_default", (None, None))
        (self.root / "catalogs.default.json").write_text(json.dumps({"schema": 1, "catalogs": [
            {"id": "gravitee", "name": "Base", "description": "", "url": "https://example.invalid/base.git"}]}), encoding="utf-8")
        self.loaded = {
            "addons": {"@acme-hub/fake-core": unit("addon", "@acme-hub/fake-core", needs=[], tags=["banking"]),
                       "@acme-hub/tracer": unit("addon", "@acme-hub/tracer", needs=["broker"],
                                                services=["tracer-ui", "tracer-db", "tracer-agent"]),
                       "broker": unit("addon", "broker", needs=[])},
            "setups": {"@acme-hub/stack": unit("setup", "@acme-hub/stack", members=["@acme-hub/tracer"],
                                               file=".lab/catalogs/acme-hub/setups/stack.yml")},
            "playgrounds": {"@acme-hub/tour": unit("playground", "@acme-hub/tour", tags=["banking"], duration=15,
                                                   first_start=4, source=None, requires={"lab": 1},
                                                   addons=["@acme-hub/fake-core", "@partner/ledger", "@acme-hub/tracer"],
                                                   services=["tour-web", "tour-seed"]),
                            "elsewhere": unit("playground", "elsewhere", addons=[])},
            # as project.build gives them: the image Compose resolved, and whether the service is built
            "services": {"tracer-ui": {"image": "registry.example.invalid/acme-hub/tracer-ui:1.2", "built": True},
                         "tracer-db": {"image": "postgres:16", "built": False},
                         "tracer-agent": {"image": "registry.example.invalid/acme-hub/tracer-agent:latest", "built": True},
                         "tour-web": {"image": "registry.example.invalid/acme-hub/tour-web:latest", "built": True},
                         "tour-seed": {"image": "registry.example.invalid/lab/seeder:latest", "built": True}}}
        self.meta = {**GOOD, "license": "Apache-2.0"}
        self.catalog = self.root / paths.CATALOGS_DIR / "acme-hub"

    def index(self, raw=None):
        return manifest.build_index("acme-hub", self.loaded, self.meta, raw)

    def raw(self, *building):
        """The files of the project as gather() read them, for the project above: each unit's file holds its services, and those
        of `building` have a `build:`, the others an image to pull. The fact a unit states, apart from the project's own flag."""
        found = {}
        for group in ("addons", "playgrounds"):
            for one in self.loaded[group].values():
                found[one["file"]] = {"services": {name: {"build": {"context": "./app"}} if name in building
                                                   else {"image": "pulled:1"}
                                                   for name in one.get("services") or [] if isinstance(name, str)}}
        return found

    def test_the_index_has_the_catalog_and_its_units_sorted_by_kind_and_id(self):
        found = self.index()
        self.assertEqual(found["schema"], 1)
        self.assertEqual(found["catalog"], {"id": "acme-hub", "name": "Acme Hub", "description": GOOD["description"],
                                            "version": "1.4.0", "lab": 1, "license": "Apache-2.0"})
        self.assertEqual([(one["kind"], one["id"]) for one in found["units"]],
                         [("addon", "fake-core"), ("addon", "tracer"), ("playground", "tour"), ("setup", "stack")])

    def test_a_unit_is_named_inside_its_catalog_and_its_path_is_the_one_in_the_repository(self):
        found = {(one["kind"], one["id"]): one for one in self.index()["units"]}
        tour = found[("playground", "tour")]
        self.assertEqual(tour["path"], "playgrounds/tour/compose.yml")
        self.assertEqual(found[("setup", "stack")]["path"], "setups/stack.yml")

    def test_a_playground_carries_what_a_registry_would_list(self):
        tour = next(one for one in self.index()["units"] if one["id"] == "tour")
        self.assertEqual((tour["name"], tour["description"], tour["category"], tour["tags"]),
                         ("Tour", "The @acme-hub/tour", "API", ["banking"]))
        self.assertEqual((tour["duration"], tour["first_start"], tour["source"]), (15, 4, None))
        self.assertEqual(tour["requires"], {"lab": 1})
        self.assertEqual(tour["brings"], ["addon:@partner/ledger", "addon:fake-core", "addon:tracer"],
                         "inside the catalog by name, from another by its id, sorted")

    def test_an_add_on_has_no_playground_keys(self):
        tracer = next(one for one in self.index()["units"] if one["id"] == "tracer")
        self.assertNotIn("duration", tracer)
        self.assertEqual(tracer["brings"], ["addon:broker"])
        self.assertEqual(set(tracer), {"kind", "id", "path", "name", "description", "category", "tags", "requires", "brings",
                                       "conflicts", "builds"})

    def test_a_unit_of_another_catalog_is_not_in_it(self):
        ids = [one["id"] for one in self.index()["units"]]
        self.assertNotIn("broker", ids)
        self.assertNotIn("elsewhere", ids)

    def test_the_text_is_the_same_bytes_twice(self):
        first, second = manifest.render_index(self.index()), manifest.render_index(self.index())
        self.assertEqual(first, second)
        self.assertEqual(first.encode(), second.encode())
        self.loaded["addons"] = dict(reversed(list(self.loaded["addons"].items())))
        self.assertEqual(manifest.render_index(self.index()), first, "the order of the project does not matter")

    def test_the_text_is_sorted_json_with_a_final_newline_and_no_time_or_commit(self):
        text = manifest.render_index(self.index())
        self.assertTrue(text.endswith("}\n"))
        self.assertNotIn("\r", text)
        found = json.loads(text)
        self.assertEqual(list(found), sorted(found))
        self.assertEqual(text, json.dumps(found, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        for word in ("commit", "pin", "installed", "checked", "time", "Z\""):
            self.assertNotIn(word, text.replace("\"requires\"", ""), word)

    def test_without_a_project_the_project_is_loaded_with_its_files_and_without_a_meta_the_file_is_read(self):
        from lab_ui.library import project
        raw = self.raw("tracer-ui", "tour-web")
        self.patch(project, "loaded", mock.Mock(return_value=(self.loaded, {"raw": raw})))
        read = mock.Mock(return_value=self.meta)
        self.patch(manifest, "read_catalog", read)
        found = manifest.build_index("acme-hub")
        self.assertEqual(found, self.index(raw))
        read.assert_called_once_with(".lab/catalogs/acme-hub/catalog.yml")
        self.assertEqual([one["service"] for unit in found["units"] for one in unit["builds"]], ["tracer-ui", "tour-web"],
                         "what the files say, and not the project's flag")

    def test_a_project_that_is_given_with_the_files_it_was_read_from_is_not_loaded_again(self):
        from lab_ui.library import project
        loaded = self.patch(project, "loaded", mock.Mock(side_effect=AssertionError("loaded again")))
        self.patch(project, "load", mock.Mock(side_effect=AssertionError("loaded again")))
        manifest.build_index("acme-hub", self.loaded, self.meta, self.raw("tracer-ui"))
        loaded.assert_not_called()

    def test_an_index_of_a_catalog_with_no_unit_is_empty(self):
        self.assertEqual(manifest.build_index("nobody", self.loaded, self.meta)["units"], [])

    # -- builds: the services a unit builds, 4.5 -------------------------------------------------------------------------

    def builds(self, name):
        return next(one for one in self.index()["units"] if one["id"] == name)["builds"]

    def test_a_unit_lists_the_services_it_builds_with_the_image_each_runs_sorted_by_service(self):
        self.assertEqual(self.builds("tracer"), [
            {"service": "tracer-agent", "image": "registry.example.invalid/acme-hub/tracer-agent:latest"},
            {"service": "tracer-ui", "image": "registry.example.invalid/acme-hub/tracer-ui:1.2"}])
        self.assertEqual(self.builds("tour"), [
            {"service": "tour-seed", "image": "registry.example.invalid/lab/seeder:latest"},
            {"service": "tour-web", "image": "registry.example.invalid/acme-hub/tour-web:latest"}])

    def test_a_service_that_is_only_pulled_is_not_a_build(self):
        self.assertNotIn("tracer-db", json.dumps(self.builds("tracer")))

    def test_a_unit_that_builds_nothing_has_an_empty_list_and_so_has_a_setup(self):
        self.assertEqual(self.builds("fake-core"), [])
        self.assertEqual(self.builds("stack"), [])

    def test_every_unit_has_the_key_whatever_it_is(self):
        for one in self.index()["units"]:
            self.assertIsInstance(one["builds"], list, one["id"])

    def test_a_unit_that_is_blocked_lists_none_for_the_project_clears_its_services(self):
        self.loaded["addons"]["@acme-hub/tracer"]["services"] = []
        self.assertEqual(self.builds("tracer"), [])

    def test_a_service_the_project_does_not_know_is_left_out(self):
        self.loaded["addons"]["@acme-hub/tracer"]["services"] = ["tracer-ui", "ghost", None, ["x"], 7]
        self.assertEqual([one["service"] for one in self.builds("tracer")], ["tracer-ui"])
        del self.loaded["services"]
        self.assertEqual(self.builds("tracer"), [], "a project that lists no service")
        self.assertEqual(manifest.build_index("acme-hub", self.loaded, self.meta, self.raw("tracer-ui"))["units"][1]["builds"], [])

    def test_what_a_unit_builds_does_not_depend_on_the_order_the_project_lists_it_in(self):
        first = manifest.render_index(self.index())
        self.loaded["addons"]["@acme-hub/tracer"]["services"].reverse()
        self.loaded["playgrounds"]["@acme-hub/tour"]["services"].reverse()
        self.loaded["services"] = dict(reversed(list(self.loaded["services"].items())))
        self.assertEqual(manifest.render_index(self.index()), first)
        self.assertIn('"builds": [', first)

    def test_an_image_is_shown_as_text_a_person_can_read(self):
        self.loaded["services"]["tracer-ui"]["image"] = "registry.example.invalid/x\n::error::a\x1b[31m\u2028b" + "z" * 1000
        found = self.builds("tracer")[-1]["image"]
        self.assertLessEqual(len(found), 300)
        self.assertTrue(found.isprintable(), found)

    def test_a_megabyte_of_image_or_service_name_is_cut_at_once(self):
        self.loaded["services"]["tracer-ui"]["image"] = "registry.example.invalid/" + "z" * 1000000 + "\n::error::x"
        self.loaded["addons"]["@acme-hub/tracer"]["services"].append("s" * 1000000)
        self.loaded["services"]["s" * 1000000] = {"image": "busybox", "built": True}
        began = time.process_time()
        found = self.builds("tracer")
        self.assertLess(time.process_time() - began, 1.0)
        self.assertLessEqual(max(len(one["image"]) for one in found), 300)
        self.assertLessEqual(max(len(one["service"]) for one in found), 100)

    def test_no_digest_and_no_time_is_in_what_a_unit_builds(self):
        text = json.dumps(self.builds("tracer"))
        self.assertNotIn("sha256", text)
        self.assertEqual(sorted(self.builds("tracer")[0]), ["image", "service"])

    # -- builds as the file of the unit states them: the same whatever else is installed ---------------------------------

    def built(self, name, *building):
        return next(one for one in self.index(self.raw(*building))["units"] if one["id"] == name)["builds"]

    def test_a_service_builds_when_the_file_of_its_unit_says_build(self):
        self.assertEqual(self.built("tracer", "tracer-ui", "tracer-agent"), [
            {"service": "tracer-agent", "image": "registry.example.invalid/acme-hub/tracer-agent:latest"},
            {"service": "tracer-ui", "image": "registry.example.invalid/acme-hub/tracer-ui:1.2"}])
        self.assertEqual([one["service"] for one in self.built("tour", "tour-seed", "tour-web")], ["tour-seed", "tour-web"])

    def test_a_service_that_runs_the_image_another_one_builds_is_not_a_build_though_the_project_calls_it_built(self):
        """project.build marks as built every service whose image another service of the project builds, also in another catalog."""
        self.loaded["services"]["tracer-db"] = {"image": "registry.example.invalid/acme-hub/tracer-ui:1.2", "built": True}
        self.assertEqual([one["service"] for one in self.built("tracer", "tracer-ui", "tracer-agent")], ["tracer-agent", "tracer-ui"])
        self.assertEqual(self.built("tracer"), [], "and a unit whose files build nothing builds nothing")

    def test_a_build_that_names_no_image_is_listed_with_none_and_not_left_out(self):
        self.loaded["services"]["tracer-agent"] = {"image": "", "built": True}
        found = self.built("tracer", "tracer-agent", "tracer-ui")
        self.assertEqual(found, [{"service": "tracer-agent", "image": None},
                                 {"service": "tracer-ui", "image": "registry.example.invalid/acme-hub/tracer-ui:1.2"}])
        self.assertIn('"image": null', manifest.render_index(self.index(self.raw("tracer-agent"))))

    def test_an_image_that_is_nothing_once_cleaned_is_none_too(self):
        self.loaded["services"]["tracer-agent"] = {"image": "\x1b\n\u2028", "built": False}
        self.assertEqual(self.built("tracer", "tracer-agent"), [{"service": "tracer-agent", "image": None}])

    def test_the_index_of_a_catalog_is_the_same_whatever_else_is_installed(self):
        """Another catalog builds the image a service of this one only pulls, and the other way round."""
        raw = self.raw("tracer-ui", "tour-web")
        first = manifest.render_index(self.index(raw))
        self.loaded["addons"]["@partner/ledger"] = unit("addon", "@partner/ledger", needs=[], services=["partner-ledger"],
                                                        file=".lab/catalogs/partner/addons/ledger/compose.yml")
        self.loaded["services"]["partner-ledger"] = {"image": "registry.example.invalid/acme-hub/tour-web:latest", "built": True}
        self.loaded["services"]["tracer-db"] = {"image": "registry.example.invalid/partner/ledger:1", "built": True}
        raw[".lab/catalogs/partner/addons/ledger/compose.yml"] = {"services": {"partner-ledger": {"build": "./app"}}}
        self.assertEqual(manifest.render_index(self.index(raw)), first)
        self.assertEqual(manifest.build_index("partner", self.loaded, self.meta, raw)["units"][0]["builds"],
                         [{"service": "partner-ledger", "image": "registry.example.invalid/acme-hub/tour-web:latest"}])

    def test_what_the_file_says_wins_over_the_flag_of_the_project_both_ways(self):
        self.loaded["services"]["tracer-ui"]["built"] = False
        self.loaded["services"]["tracer-db"]["built"] = True
        self.assertEqual([one["service"] for one in self.built("tracer", "tracer-ui")], ["tracer-ui"])

    def test_only_a_build_that_says_something_is_a_build(self):
        for value, builds in (({"context": "."}, True), ("./app", True), ({"context": ".", "args": {"A": "b"}}, True),
                              (None, False), ("", False), ({}, False), (False, False), (0, False), ([], False)):
            with self.subTest(build=value):
                raw = self.raw()
                raw[self.loaded["addons"]["@acme-hub/tracer"]["file"]]["services"]["tracer-ui"] = {"build": value}
                found = next(one for one in self.index(raw)["units"] if one["id"] == "tracer")["builds"]
                self.assertEqual([one["service"] for one in found], ["tracer-ui"] if builds else [])

    def test_a_file_of_the_project_that_is_not_what_gather_reads_lists_nothing_and_is_no_error(self):
        file = self.loaded["addons"]["@acme-hub/tracer"]["file"]
        for odd in ({}, {file: None}, {file: []}, {file: "x"}, {file: {}}, {file: {"services": None}},
                    {file: {"services": []}}, {file: {"services": "tracer-ui"}}, {file: {"services": {"tracer-ui": None}}},
                    {file: {"services": {"tracer-ui": ["build"]}}}, {file: {"services": {"tracer-ui": "build"}}},
                    {"other": {"services": {"tracer-ui": {"build": "."}}}}, {7: 8}):
            with self.subTest(raw=odd):
                found = next(one for one in self.index(odd)["units"] if one["id"] == "tracer")["builds"]
                self.assertEqual(found, [])

    def test_without_the_files_the_flag_of_the_project_is_all_there_is(self):
        self.assertEqual([one["service"] for one in self.index()["units"][1]["builds"]], ["tracer-agent", "tracer-ui"])
        self.assertEqual(self.index(None), self.index())

    def test_a_megabyte_of_file_is_read_at_once(self):
        raw = self.raw("tracer-ui")
        raw[self.loaded["addons"]["@acme-hub/tracer"]["file"]]["services"].update(
            {f"s{number}": {"build": "." * 100} for number in range(20000)})
        began = time.process_time()
        found = self.index(raw)
        self.assertLess(time.process_time() - began, 1.0)
        self.assertEqual([one["service"] for one in found["units"][1]["builds"]], ["tracer-ui"])


# ---------------------------------------------------------------------------------------------------------------------
# The standard a catalog follows (governance 3.2): carried by the index, read as data
# ---------------------------------------------------------------------------------------------------------------------

class Standard(Case):
    """`standard` of the index is the level in `.github/standard.json` of the catalog's folder: there only when the file says
    one. The folder is a hostile tree (the CI of a pull request links it), so nothing leads out of it and nothing is run."""

    def setUp(self):
        self.folder(paths)
        self.catalog = self.root / paths.CATALOGS_DIR / "acme-hub"
        self.catalog.mkdir(parents=True)
        self.meta = {**GOOD, "license": "Apache-2.0"}

    def index(self):
        return manifest.build_index("acme-hub", {"addons": {}, "setups": {}, "playgrounds": {}}, self.meta)

    def standard(self, content, name="standard.json"):
        """`.github/<name>` of the catalog written with these bytes (text is UTF-8)."""
        file = self.catalog / ".github" / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return file

    GOOD_FILE = json.dumps({"form": "inline", "lab": {"ref": "0" * 40, "repository": "o/lab"}, "platforms": ["linux/amd64"],
                            "standard": 1}, indent=2, sort_keys=True) + "\n"

    def test_the_level_the_file_states_is_in_the_index(self):
        self.standard(self.GOOD_FILE)
        self.assertEqual(self.index()["standard"], 1)
        self.standard(json.dumps({"standard": 3}))
        self.assertEqual(self.index()["standard"], 3)

    def test_without_the_file_there_is_no_key(self):
        self.assertNotIn("standard", self.index())
        (self.catalog / ".github").mkdir()
        self.assertNotIn("standard", self.index())

    def test_the_index_is_the_same_bytes_twice_with_and_without_the_file_and_the_key_is_sorted_in(self):
        without = manifest.render_index(self.index())
        self.assertEqual(manifest.render_index(self.index()), without)
        self.standard(self.GOOD_FILE)
        first, second = manifest.render_index(self.index()), manifest.render_index(self.index())
        self.assertEqual(first, second)
        self.assertEqual(first.encode(), second.encode())
        self.assertEqual(list(json.loads(first)), ["catalog", "schema", "standard", "units"])
        self.assertEqual(first, json.dumps(json.loads(first), indent=2, sort_keys=True, ensure_ascii=False) + "\n")

    def test_the_rest_of_the_index_is_the_same_with_the_file_and_without(self):
        without = self.index()
        self.standard(self.GOOD_FILE)
        with_file = self.index()
        self.assertEqual({key: value for key, value in with_file.items() if key != "standard"}, without)

    def test_a_file_that_is_not_a_level_gives_no_key(self):
        for content in ("", "{", "[]", "null", "1", '"standard"', "{}", '{"standard": 0}', '{"standard": -1}',
                        '{"standard": true}', '{"standard": 1.0}', '{"standard": "1"}', '{"standard": null}',
                        '{"standard": [1]}', '{"standard": {"standard": 1}}', '{"Standard": 1}', '{"standard": 1000001}',
                        '{"standard": ' + "9" * 5000 + "}", '{"standard": NaN}', '{"standard": Infinity}',
                        '{"standard": 1e2}', "standard: 1\n", "\x00\x01\x02", "{'standard': 1}", '{"standard": 1,}'):
            with self.subTest(content=content[:40]):
                self.standard(content)
                self.assertNotIn("standard", self.index())

    def test_a_file_that_is_not_utf_8_is_not_read(self):
        for content in ('{"standard": 1}'.encode("utf-16"), '{"standard": 1}'.encode("utf-32"), b'{"standard": 1}\xff',
                        b'\xff\xfe{"standard": 1}'):
            with self.subTest(content=content[:12]):
                self.standard(content)
                self.assertNotIn("standard", self.index())

    def test_what_is_lenient_is_the_rest_of_the_file_and_a_byte_order_mark(self):
        self.standard(b"\xef\xbb\xbf" + json.dumps({"standard": 2, "unknown": {"deep": [1, 2, 3]}, "form": 7}).encode())
        self.assertEqual(self.index()["standard"], 2)
        self.standard('{"standard": 2, "standard": 4}')
        self.assertEqual(self.index()["standard"], 4, "the last of a repeated key, as JSON readers take it")
        self.standard(json.dumps({"standard": 1000000}))
        self.assertEqual(self.index()["standard"], 1000000)

    def test_a_file_larger_than_the_limit_is_not_read_and_one_at_it_is(self):
        pad = " " * (manifest.STANDARD_LIMIT - len('{"standard": 1}'))
        self.standard('{"standard": 1}' + pad)
        self.assertEqual(self.index()["standard"], 1)
        self.standard('{"standard": 1}' + pad + " ")
        self.assertNotIn("standard", self.index())

    def test_a_deeply_nested_file_and_a_megabyte_of_it_are_refused_at_once(self):
        for content in ('{"a": ' + "[" * 60000 + "]" * 60000 + ', "standard": 1}', "[" * 1000000, '{"standard": 1}' + " " * 1000000):
            self.standard(content)
            began = time.process_time()
            self.assertNotIn("standard", self.index())
            self.assertLess(time.process_time() - began, 1.0)

    def test_a_link_in_place_of_the_file_is_not_followed(self):
        elsewhere = self.root / "elsewhere.json"
        elsewhere.write_text('{"standard": 1}', encoding="utf-8")
        file = self.catalog / ".github" / "standard.json"
        file.parent.mkdir()
        os.symlink(elsewhere, file)
        self.assertNotIn("standard", self.index())

    def test_a_link_in_place_of_the_folder_is_not_followed(self):
        outside = self.root / "outside"
        (outside).mkdir()
        (outside / "standard.json").write_text('{"standard": 1}', encoding="utf-8")
        os.symlink(outside, self.catalog / ".github")
        self.assertNotIn("standard", self.index())

    def test_a_link_to_a_file_that_is_not_there_is_nothing(self):
        (self.catalog / ".github").mkdir()
        os.symlink(self.root / "missing.json", self.catalog / ".github" / "standard.json")
        self.assertNotIn("standard", self.index())

    def test_a_pipe_in_place_of_the_file_is_not_waited_for(self):
        (self.catalog / ".github").mkdir()
        os.mkfifo(self.catalog / ".github" / "standard.json")
        began = time.monotonic()
        self.assertNotIn("standard", self.index())
        self.assertLess(time.monotonic() - began, 5.0)

    def test_a_folder_in_place_of_the_file_is_nothing(self):
        (self.catalog / ".github" / "standard.json").mkdir(parents=True)
        self.assertNotIn("standard", self.index())

    def test_a_file_that_cannot_be_read_is_nothing(self):
        file = self.standard(self.GOOD_FILE)
        file.chmod(0)
        self.addCleanup(file.chmod, stat.S_IRUSR | stat.S_IWUSR)
        if os.access(file, os.R_OK):
            self.skipTest("this user reads a file with no permission")
        self.assertNotIn("standard", self.index())

    def test_the_folder_of_the_catalog_may_be_a_link_for_that_is_how_a_folder_is_linked(self):
        folder = self.root / "work-tree"
        (folder / ".github").mkdir(parents=True)
        (folder / ".github" / "standard.json").write_text(self.GOOD_FILE, encoding="utf-8")
        self.catalog.rmdir()
        os.symlink(folder, self.catalog)
        self.assertEqual(self.index()["standard"], 1)

    def test_a_name_that_is_not_a_namespace_is_not_a_place_to_read(self):
        self.standard(self.GOOD_FILE)
        for name in ("../acme-hub", "acme-hub/..", "", ".", "a/b", "ACME", "acme hub", None, 3):
            with self.subTest(name=name):
                self.assertIsNone(manifest._standard(name))

    def test_the_index_reads_nothing_of_the_file_but_the_level(self):
        self.standard(json.dumps({"standard": 1, "lab": {"ref": "x" * 40}, "platforms": ["linux/amd64"],
                                  "note": "\x1b[31m::error::a"}))
        text = manifest.render_index(self.index())
        self.assertNotIn("::error::", text)
        self.assertNotIn("platforms", text)
        self.assertEqual(json.loads(text)["standard"], 1)


@unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
class RealIndex(Case):
    """The index of a catalog as the project reads it with Compose (`docker compose config`, no daemon): what a unit builds is
    what its files say, and two runs give the same bytes."""

    def setUp(self):
        fixtures.project(self, check, paths, project, compose, envfiles)
        self.patch(license, "status_of", lambda variable: None)
        project.forget()
        self.addCleanup(project.forget)

    def test_the_units_that_build_list_their_services_and_the_others_an_empty_list(self):
        found = {(one["kind"], one["id"]): one["builds"] for one in manifest.build_index("fixture")["units"]}
        self.assertEqual([build["service"] for build in found[("playground", "shop")]], ["fixture-shop-seed", "fixture-shop-web"])
        self.assertEqual(next(build for build in found[("playground", "shop")] if build["service"] == "fixture-shop-web")["image"],
                         "registry.example.invalid/lab/fixture-shop-web:latest")
        self.assertEqual([build["service"] for build in found[("playground", "tour")]], ["fixture-tour-seed"])
        for key in (("addon", "echo"), ("addon", "cluster"), ("setup", "pair")):
            self.assertEqual(found[key], [], key)

    def test_two_runs_give_the_same_bytes_and_no_standard_where_no_file_says_one(self):
        first = manifest.render_index(manifest.build_index("fixture"))
        project.forget()
        self.assertEqual(manifest.render_index(manifest.build_index("fixture")), first)
        self.assertNotIn("standard", json.loads(first))

    SHOP_IMAGE = "registry.example.invalid/lab/fixture-shop-web:latest"

    def partner(self, image=None, build=False):
        """The partner catalog installed, its ledger add-on written to pull `image` or, with `build`, to build ./app and name no
        image. The project is read again with it."""
        text = (fixtures.CATALOGS / "partner" / "addons" / "ledger" / "compose.yml").read_text(encoding="utf-8")
        old = "image: busybox:${PARTNER_LEDGER_VERSION:-1.36}   # Image version"
        self.assertIn(old, text)
        files = {"addons/ledger/compose.yml": text.replace(old, f"image: {image}")}
        if build:
            files = {"addons/ledger/compose.yml": text.replace(old, "build: ./app\n    pull_policy: ${PARTNER_LEDGER_PULL_POLICY:-build}"),
                     "addons/ledger/app/Dockerfile": "FROM scratch\n"}
        fixtures.install(self, "partner", files=files)
        project.forget()
        return project.load()

    def test_a_service_that_only_pulls_the_image_another_catalog_builds_is_no_build_and_changes_no_index(self):
        alone = manifest.render_index(manifest.build_index("fixture"))
        found = self.partner(image=self.SHOP_IMAGE)
        self.assertEqual(found["services"]["fixture-shop-web"]["image"], self.SHOP_IMAGE, "the image the shop builds")
        self.assertIsNone(found["addons"]["@partner/ledger"]["blocked"], "the unit is judged and runs")
        self.assertTrue(found["services"]["partner-ledger"]["built"], "the project says built: it runs a build of here")
        self.assertEqual(manifest.build_index("partner")["units"][0]["builds"], [], "and the unit builds nothing")
        self.assertEqual(manifest.render_index(manifest.build_index("fixture")), alone, "the shop is the same with the partner")

    def test_a_build_that_names_no_image_is_listed_with_none(self):
        found = self.partner(build=True)
        self.assertIsNone(found["addons"]["@partner/ledger"]["blocked"], "the unit is judged and runs")
        self.assertEqual((found["services"]["partner-ledger"]["image"], found["services"]["partner-ledger"]["built"]), ("", True))
        self.assertEqual(manifest.build_index("partner")["units"][0]["builds"], [{"service": "partner-ledger", "image": None}])
        self.assertIn('"image": null', manifest.render_index(manifest.build_index("partner")))


if __name__ == "__main__":
    unittest.main()
