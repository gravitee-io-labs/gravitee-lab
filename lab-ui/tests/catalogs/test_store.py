"""Catalogs/store.py: the pure filter of what is installed from a repository ( rules 5 and 6) and the rule of the
catalog standard that refuses each file it drops (`rejections`, of which `selected` is the other reading), the digests of
units, and the bare store (fetch, objects, history) against bare repositories made on this machine. No test needs the
network; a test that needs git and finds none prints a skip line."""

import errno
import hashlib
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from typing import NamedTuple
from unittest import mock

from lab_ui.catalogs import gitx, store
from lab_ui.catalogs.store import Entry

from .. import Case, gitfixture

# The rules of the catalog standard that `rejections` answers
CODES = ("ST01", "ST02", "ST03", "ST04", "ST05", "ST06", "ST07", "ST09", "ST10", "ST13", "ST14", "ST15")

MIB = 1024 * 1024


def entry(path, mode="100644", size=10, oid=None):
    return Entry(mode, oid or hashlib.sha1(path.encode("utf-8", "surrogateescape")).hexdigest(), size, path)


# A small, good catalog, and what is kept of it (README.md, the Makefile and .github are not installed and not counted)
GOOD = [entry(path) for path in (
    "README.md", "Makefile", ".github/workflows/ci.yml", "catalog.yml", "addons/echo/.env.example", "addons/echo/README.md",
    "addons/echo/compose.yml", "addons/echo/data/accounts.json", "playgrounds/shop/apps/web/Dockerfile",
    "playgrounds/shop/compose.yml", "playgrounds/shop/guide.md", "playgrounds/shop/seed/apim/apis/shop.json",
    "setups/pair.yml")]
KEPT = [e for e in GOOD if e.path not in ("README.md", "Makefile", ".github/workflows/ci.yml")]


def unit_of_25(name):
    """A unit of exactly 25 MiB: the compose file of 1 MiB and twelve files of 2 MiB."""
    return [entry(f"addons/{name}/compose.yml", size=MIB)] + [entry(f"addons/{name}/f{i}.bin", size=2 * MIB) for i in range(12)]


def refused(*bad):
    """The filter over the good catalog and the entries `bad`: (kept, ignored)."""
    return store.selected(GOOD + list(bad))


class Filter(Case):
    """`selected`: every hostile entry class is refused and counted, and the rest is kept as given."""

    def test_a_good_catalog_is_kept_whole_in_the_order_given(self):
        kept, ignored = store.selected(GOOD)
        self.assertEqual((kept, ignored), (KEPT, 0))
        shuffled = list(reversed(GOOD))
        self.assertEqual(store.selected(shuffled)[0], list(reversed(KEPT)), "the order is the caller's")

    def test_the_rest_of_the_top_level_is_not_installed_and_not_counted(self):
        outside = [entry(p) for p in ("README.md", "LICENSE", "Makefile", ".gitignore", ".github/workflows/ci.yml",
                                      "docs/guide.md", "tests/test_x.py", "Addons/echo/compose.yml", "addons-extra/x.yml",
                                      "docs/.env", "docs/secrets/token", "catalog.json")]
        outside.append(entry("docs/link", mode="120000"))
        self.assertEqual(store.selected(outside), ([], 0))

    def test_a_top_level_name_that_is_not_a_name_is_counted(self):
        for path in ("weird name/x", "../x", "./x", "/abs/x", "a\x00b/x", "back\\slash/x", "ü/x"):
            self.assertEqual(store.selected([entry(path)]), ([], 1), repr(path))

    def test_the_catalog_file_is_installed_only_as_a_regular_file_of_a_size_a_file_may_have(self):
        self.assertEqual(store.selected([entry("catalog.yml")])[0], [entry("catalog.yml")])
        for bad in (entry("catalog.yml", mode="120000"), entry("catalog.yml", mode="160000"),
                    entry("catalog.yml", size=MIB + 1)):
            self.assertEqual(store.selected([bad]), ([], 1), bad)

    def test_every_hostile_class_is_refused_and_counted_and_the_rest_is_untouched(self):
        classes = {
            "a link": [entry("addons/echo/link", mode="120000"), entry("addons/echo/data/link.yml", mode="120000")],
            "a submodule": [entry("addons/echo/sub", mode="160000", size=0)],
            "another mode": [entry("addons/echo/x.yml", mode="100664"), entry("addons/echo/y.yml", mode="100600"),
                             entry("addons/echo/z.yml", mode="040000"), entry("addons/echo/w.yml", mode="100777")],
            "settings": [entry(p) for p in (
                "addons/echo/.env", "addons/echo/.env.local", "addons/echo/.env.production", "playgrounds/shop/.env",
                "addons/echo/.ENV", "addons/echo/.Env.Local", "addons/echo/.env/x", "addons/echo/.env.d/x",
                "addons/echo/data/.env", "addons/echo/.env.example/x")],
            "secrets and state": [entry(p) for p in (
                "addons/echo/secrets/token", "addons/echo/SECRETS/x", "addons/echo/secrets", "playgrounds/shop/.state/created.json",
                "playgrounds/shop/a/.state/x", "playgrounds/shop/.STATE/x")],
            "git": [entry(p) for p in ("addons/echo/.git/config", "addons/echo/.GIT/x", "addons/echo/.git")],
            "keys and certificates": [entry(f"addons/echo/data/x{suffix}") for suffix in (
                ".key", ".pem", ".p12", ".jks", ".crt", ".csr", ".srl", ".KEY", ".Pem", ".CRT")],
            "instructions of agents": [entry(p) for p in (
                "addons/echo/AGENTS.md", "addons/echo/agents.md", "addons/echo/CLAUDE.md", "addons/echo/GEMINI.md",
                "playgrounds/shop/.claude/settings.json", "playgrounds/shop/.agents/skills/x/SKILL.md",
                "playgrounds/shop/.gemini/settings.json", "playgrounds/shop/.cursorrules",
                "playgrounds/shop/.cursor/rules/x.mdc", "playgrounds/shop/.windsurfrules", "playgrounds/shop/.windsurf/x",
                "playgrounds/shop/.github/copilot-instructions.md", "playgrounds/shop/.GITHUB/Copilot-Instructions.md")],
            "a segment that is not a name": [entry(p) for p in (
                "addons/echo/bad name.yml", "addons/echo/back\\slash.yml", "addons/echo/a*b.yml", "addons/echo/ü.md",
                "addons/echo/../x.yml", "addons/echo/./x.yml", "addons/echo//x.yml", "addons/echo/", "addons/echo/a\nb",
                "addons/echo/a\tb", "addons/echo/a\x00b", "addons/echo/a%20b", "addons/echo/a:b", "addons/echo/a;b",
                "addons/echo/$(id)", "addons/echo/`id`", "addons/echo/a'b", "addons/echo/‮x.yml")],
            "too deep or too long": [entry("playgrounds/shop/a/b/c/d/e/f/g.yml"),
                                     entry("addons/echo/" + "d/" * 6 + "x.yml"),
                                     entry("addons/echo/" + "a" * 185 + ".yml")],
            "a unit that is not named as a unit": [entry(p) for p in (
                "addons/Upper/compose.yml", "addons/under_score/compose.yml", "addons/dot.name/compose.yml",
                "playgrounds/Shop/compose.yml", "setups/Pair.yml", "setups/under_score.yml", "addons/a b/compose.yml")],
            "a file that is not in a unit": [entry(p) for p in (
                "addons/NOTES.md", "playgrounds/notes.txt", "setups/pair.yaml", "setups/sub/x.yml", "setups/readme.md",
                "setups/pair.yml.bak", "addons", "playgrounds", "setups/x")],
            "too large": [entry("addons/echo/big.json", size=MIB + 1), entry("addons/echo/big.yml", size=MIB + 1),
                          entry("addons/echo/big.yaml", size=MIB + 1), entry("addons/echo/big.md", size=MIB + 1),
                          entry("addons/echo/big.bin", size=2 * MIB + 1), entry("addons/echo/big.png", size=2 * MIB + 1)],
        }
        for name, bad in classes.items():
            with self.subTest(name):
                kept, ignored = refused(*bad)
                self.assertEqual((kept, ignored), (KEPT, len(bad)), [e.path for e in kept if e not in KEPT])

    def test_each_hostile_entry_is_refused_alone_not_by_company(self):
        """One bad entry beside a valid unit, so that the table above cannot pass by one entry hiding another."""
        unit = [entry("addons/e/compose.yml")]
        for bad in (entry("addons/e/l", mode="120000"), entry("addons/e/s", mode="160000", size=0), entry("addons/e/.env"),
                    entry("addons/e/secrets/x"), entry("addons/e/.state/x"), entry("addons/e/x.pem"),
                    entry("addons/e/AGENTS.md"), entry("addons/e/../f"), entry("addons/e/a b"), entry("addons/e/\\f"),
                    entry("addons/e/big.bin", size=2 * MIB + 1), entry("addons/e/" + "d/" * 7 + "f")):
            self.assertEqual(store.selected(unit + [bad]), (unit, 1), bad.path)

    def test_what_is_just_allowed_is_kept(self):
        allowed = [entry(p) for p in (
            "playgrounds/shop/.ENV.EXAMPLE", "addons/echo/.envrc", "addons/echo/x.pem.txt",
            "addons/echo/keys.md", "addons/echo/AGENTS.md.txt", "addons/echo/my-claude.md", "addons/echo/.gitignore",
            "addons/echo/.gitkeep", "addons/echo/a+b_c.d-e.yml", "addons/echo/UPPER.TXT", "addons/echo/.hidden",
            "addons/echo/state/x", "addons/echo/not-secrets/x", "addons/echo/secret/x", "addons/echo/cursor/x",
            # Copilot's file is `.github/copilot-instructions.md`: a folder that only ends with it is a folder like any other
            "addons/echo/my.github/copilot-instructions.md", "addons/echo/github/copilot-instructions.md",
            "addons/echo/.github/sub/copilot-instructions.md", "addons/echo/.github/copilot-instructions.md.txt")]
        allowed += [entry("addons/echo/exact.json", size=MIB), entry("addons/echo/exact.md", size=MIB),
                    entry("addons/echo/exact.bin", size=2 * MIB), entry("addons/echo/exec.sh", mode="100755"),
                    entry("addons/echo/" + "d/" * 5 + "x.yml"),   # 8 components
                    entry("addons/echo/" + "a" * 184 + ".yml")]   # 200 characters
        self.assertEqual(len(allowed[-2].path.split("/")), 8)
        self.assertEqual(len(allowed[-1].path), 200)
        kept, ignored = refused(*allowed)
        self.assertEqual((kept, ignored), (KEPT + allowed, 0))

    def test_the_listing_tables_of_a_catalog_are_not_a_refusal_worth_counting(self):
        tables = [entry(p) for p in ("addons/README.md", "setups/README.md", "playgrounds/README.md")]
        self.assertEqual(refused(*tables), (KEPT, 0), "documents of the catalog, never installed")
        notes = [entry("addons/NOTES.md"), entry("setups/readme.md"), entry("playgrounds/README.txt"), entry("addons/sub/README.md")]
        self.assertEqual(refused(*notes), (KEPT, 4), "any other file of a folder of units is counted")
        link = entry("addons/README.md", mode="120000")
        self.assertEqual(refused(link), (KEPT, 1), "a link is a link, whatever it is called")
        self.patch(store, "LISTINGS", ())
        self.assertEqual(refused(*tables), (KEPT, 3), "rule 6 to the letter, when the list is empty")

    def test_a_unit_without_its_compose_file_is_not_a_unit(self):
        orphan = [entry("addons/orphan/README.md"), entry("addons/orphan/data/x.json"),
                  entry("playgrounds/ghost/guide.md"), entry("playgrounds/ghost/seed/a.yaml")]
        self.assertEqual(refused(*orphan), (KEPT, 4))
        deep = [entry("addons/deep/sub/compose.yml"), entry("addons/deep/README.md")]
        self.assertEqual(refused(*deep), (KEPT, 2), "the compose file is at the root of the unit")

    def test_a_unit_whose_compose_file_is_refused_goes_with_it(self):
        bad = [entry("addons/dead/compose.yml", mode="120000"), entry("addons/dead/README.md"), entry("addons/dead/data.json")]
        self.assertEqual(refused(*bad), (KEPT, 3))

    def test_files_that_differ_only_by_case_are_all_refused(self):
        twins = [entry("addons/echo/readme.md"), entry("playgrounds/shop/Guide.md")]   # README.md and guide.md exist
        kept, ignored = refused(*twins)
        self.assertEqual(ignored, 4)
        self.assertNotIn("addons/echo/README.md", [e.path for e in kept])
        self.assertNotIn("playgrounds/shop/guide.md", [e.path for e in kept])
        self.assertIn("addons/echo/compose.yml", [e.path for e in kept])

    def test_a_compose_file_in_two_cases_leaves_no_unit(self):
        kept, ignored = refused(entry("addons/echo/Compose.yml"))
        self.assertEqual(ignored, 5, "both spellings, and the three other files of a unit that has no compose.yml left")
        self.assertFalse([e for e in kept if e.path.startswith("addons/echo/")])
        self.assertIn("playgrounds/shop/compose.yml", [e.path for e in kept])

    def test_a_folder_that_differs_only_by_case_takes_everything_below_it(self):
        """A Mac would merge the two folders, or fail to write a folder where a file is: it is not only whole paths."""
        compose = entry("addons/x/compose.yml")
        for twins, gone in (
                ([entry("addons/x/data"), entry("addons/x/DATA/y")], ["addons/x/data", "addons/x/DATA/y"]),
                ([entry("addons/x/Dir/a"), entry("addons/x/dir/b")], ["addons/x/Dir/a", "addons/x/dir/b"]),
                ([entry("addons/x/Dir/sub/a.md"), entry("addons/x/dir/sub/b.md"), entry("addons/x/Dir/c.md")],
                 ["addons/x/Dir/sub/a.md", "addons/x/dir/sub/b.md", "addons/x/Dir/c.md"]),
                ([entry("addons/x/a/B/c.md"), entry("addons/x/a/b/d.md"), entry("addons/x/other.md")],
                 ["addons/x/a/B/c.md", "addons/x/a/b/d.md"]),
                ([entry("addons/x/a.md"), entry("addons/x/a.md")], ["addons/x/a.md", "addons/x/a.md"])):
            with self.subTest([e.path for e in twins]):
                kept, ignored = store.selected([compose, *twins])
                self.assertEqual(ignored, len(gone))
                self.assertEqual([e.path for e in kept if e.path not in ("addons/x/other.md",)], ["addons/x/compose.yml"])

    def test_names_that_differ_by_case_in_different_places_are_not_twins(self):
        paths = ["addons/x/compose.yml", "addons/x/Data/a.md", "addons/y/compose.yml", "addons/y/data/b.md",
                 "addons/x/data.md", "addons/x/Data.json", "playgrounds/p/compose.yml", "playgrounds/p/Docs/g.md"]
        kept, ignored = store.selected([entry(path) for path in paths])
        self.assertEqual(([e.path for e in kept], ignored), (paths, 0))

    def test_the_sizes_are_the_limits_of_section_2(self):
        self.assertEqual((store.TEXT_FILE, store.OTHER_FILE), (MIB, 2 * MIB))
        self.assertEqual((store.UNIT_FILES, store.UNIT_BYTES), (2000, 25 * MIB))
        self.assertEqual((store.CATALOG_UNITS, store.CATALOG_FILES, store.CATALOG_BYTES), (500, 10000, 100 * MIB))
        self.assertEqual((store.DEPTH, store.PATH), (8, 200))
        self.assertEqual(store.STORE_LIMIT, 300 * MIB)

    def assert_too_large(self, entries):
        with self.assertRaises(gitx.CatalogError) as raised:
            store.selected(entries)
        self.assertEqual((raised.exception.code, raised.exception.status), ("too_large", 400))
        self.assertEqual(raised.exception.message,
                         "This catalog is larger than the Lab accepts. Ask its owners to make it smaller.")

    def test_a_catalog_over_the_limits_is_too_large_not_a_quiet_subset(self):
        def setups(count):
            return [entry("catalog.yml")] + [entry(f"setups/s{i}.yml") for i in range(count)]
        self.assertEqual(len(store.selected(setups(499))[0]), 500)
        self.assertEqual(len(store.selected(setups(500))[0]), 501, "500 units and the catalog file")
        self.assert_too_large(setups(501))

        def unit(files, size=1, name="big"):
            return [entry(f"addons/{name}/compose.yml", size=size)] + [entry(f"addons/{name}/f{i}.txt", size=size) for i in range(files - 1)]
        self.assertEqual(len(store.selected(unit(2000))[0]), 2000)
        self.assert_too_large(unit(2001))

        full = unit_of_25("big")
        self.assertEqual(sum(e.size for e in store.selected(full)[0]), 25 * MIB)
        self.assert_too_large(full + [entry("addons/big/one.txt", size=1)])

    def test_the_limits_of_files_and_bytes_of_a_catalog(self):
        def spread(units, per):
            made = [entry("catalog.yml", size=0)]
            for u in range(units):
                made.append(entry(f"addons/u{u}/compose.yml", size=1))
                made += [entry(f"addons/u{u}/f{i}.bin", size=1) for i in range(per - 1)]
            return made
        self.assert_too_large(spread(100, 100))   # 10000 files and the catalog file: one over
        ok = spread(99, 101)                      # 9999 files and the catalog file: 10000
        self.assertEqual(len(store.selected(ok)[0]), 10000)
        self.assert_too_large(ok + [entry("addons/u0/more.bin")])
        full = [entry("catalog.yml", size=0)] + [e for n in range(4) for e in unit_of_25(f"u{n}")]   # 100 MiB
        self.assertEqual(sum(e.size for e in store.selected(full)[0]), 100 * MIB)
        self.assert_too_large(full + [entry("addons/u0/one.txt", size=1)])

    def test_the_filter_is_pure_and_changes_nothing_it_is_given(self):
        entries = tuple(GOOD) + (entry("addons/echo/.env"),)
        before = list(entries)
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("ran a command")), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("ran a command")), \
                mock.patch("builtins.open", side_effect=AssertionError("read a file")), \
                mock.patch.object(os, "stat", side_effect=AssertionError("looked at a file")), \
                mock.patch.object(os, "listdir", side_effect=AssertionError("listed a folder")), \
                mock.patch.object(os, "walk", side_effect=AssertionError("walked a folder")):
            first = store.selected(entries)
            second = store.selected(iter(entries))
        self.assertEqual(first, second)
        self.assertEqual(list(entries), before)
        self.assertEqual(first, (KEPT, 1))
        self.assertIsInstance(first[0], list)

    def test_any_listing_is_filtered_to_files_that_are_safe_to_write_or_is_too_large(self):
        chance = random.Random(2026)
        parts = ["addons", "setups", "playgrounds", "catalog.yml", "compose.yml", "x", "..", ".", "", ".env", "secrets", ".git",
                 "a b", "a.yml", "\u00fc", "AGENTS.md", "k.pem", "-", "A", "z" * 50, "echo", "pair.yml"]
        modes = ["100644", "100755", "120000", "160000", "040000", ""]
        for _ in range(5000):
            listing = [Entry(chance.choice(modes), "a" * 40, chance.choice([0, 1, 10, MIB, MIB + 1, 3 * MIB]),
                             "/".join(chance.choice(parts) for _ in range(chance.randint(1, 10))))
                       for _ in range(chance.randint(0, 25))]
            try:
                kept, ignored = store.selected(listing)
            except gitx.CatalogError as error:
                self.assertEqual(error.code, "too_large")
                continue
            self.assertLessEqual(len(kept) + ignored, len(listing))
            for e in kept:
                self.assertIn(e.mode, ("100644", "100755"))
                self.assertFalse(e.path.startswith("/"), e.path)
                self.assertTrue(all(part and part not in (".", "..") for part in e.path.split("/")), e.path)
            self.assertEqual(len({e.path.lower() for e in kept}), len(kept), "no two files that a Mac would write as one")
            store.unit_digests(kept)

    def test_an_empty_listing_keeps_nothing(self):
        self.assertEqual(store.selected([]), ([], 0))

    def test_units_are_grouped_by_kind_and_name(self):
        grouped = store.by_unit(KEPT)
        self.assertEqual(sorted(grouped), ["addon:echo", "playground:shop", "setup:pair"])
        self.assertEqual([e.path for e in grouped["setup:pair"]], ["setups/pair.yml"])
        self.assertNotIn("catalog.yml", [e.path for files in grouped.values() for e in files])


def coded(*bad):
    """`rejections` over the good catalog and the entries `bad`."""
    return store.rejections(GOOD + list(bad))


class Row(NamedTuple):
    """What the rules of a catalog's CI read of a file (mode, path, size): no object id."""
    mode: str
    path: str
    size: int


class Rejections(Case):
    """`rejections`: every file the Lab drops, with the rule of the catalog standard that refuses it, one list of
    which `selected` is the other reading."""

    # What each code refuses: entries that are refused alone, with that code, beside a good catalog
    ALONE = {
        "ST01": [entry("addons/echo/link", mode="120000"), entry("addons/echo/data/link.yml", mode="120000"),
                 entry("addons/echo/x.yml", mode="100664"), entry("addons/echo/y.yml", mode="100600"),
                 entry("addons/echo/z.yml", mode="040000"), entry("playgrounds/shop/odd", mode=""),
                 entry("catalog.yml", mode="120000"), entry("addons/README.md", mode="120000"),
                 entry("setups/CLAUDE.md", mode="120000")],
        "ST02": [entry("addons/echo/sub", mode="160000", size=0), entry("catalog.yml", mode="160000", size=0),
                 entry("playgrounds/AGENTS.md", mode="160000", size=0)],
        "ST03": [entry(p) for p in (
            "addons/echo/bad name.yml", "addons/echo/back\\slash.yml", "addons/echo/\u00fc.md", "addons/echo/../x.yml",
            "addons/echo/./x.yml", "addons/echo//x.yml", "addons/echo/", "addons/echo/a\nb", "addons/echo/a\tb",
            "addons/echo/a\x00b", "addons/echo/a%20b", "addons/echo/\u202ex.yml", "addons/a b/compose.yml",
            "weird name/x", "../x", "./x", "/abs/x", "\u00fc/x", "back\\slash/x", "a\x00b/x")],
        "ST04": [entry("playgrounds/shop/a/b/c/d/e/f/g.yml"), entry("addons/echo/" + "d/" * 6 + "x.yml"),
                 entry("addons/echo/" + "a" * 185 + ".yml")],
        "ST05": [entry(p) for p in (
            "addons/echo/.env", "addons/echo/.env.local", "addons/echo/.env.production", "playgrounds/shop/.env",
            "addons/echo/.ENV", "addons/echo/.Env.Local", "addons/echo/.env/x", "addons/echo/.env.d/x",
            "addons/echo/data/.env", "addons/echo/.env.example/x")],
        "ST06": [entry(p) for p in (
            "addons/echo/secrets/token", "addons/echo/SECRETS/x", "addons/echo/secrets", "playgrounds/shop/.state/created.json",
            "playgrounds/shop/a/.state/x", "playgrounds/shop/.STATE/x", "addons/echo/.git/config", "addons/echo/.GIT/x",
            "addons/echo/.git")] + [entry(f"addons/echo/data/x{suffix}") for suffix in (
                ".key", ".pem", ".p12", ".pfx", ".jks", ".keystore", ".crt", ".csr", ".srl", ".KEY", ".Pem", ".CRT",
                ".PFX", ".Keystore")],
        "ST07": [entry(p) for p in (
            "addons/echo/AGENTS.md", "addons/echo/agents.md", "addons/echo/CLAUDE.md", "addons/echo/GEMINI.md",
            "playgrounds/shop/AGENTS.md", "playgrounds/shop/.claude/settings.json", "playgrounds/shop/.agents/skills/x/SKILL.md",
            "playgrounds/shop/.gemini/settings.json", "playgrounds/shop/.cursorrules", "playgrounds/shop/.cursor/rules/x.mdc",
            "playgrounds/shop/.windsurfrules", "playgrounds/shop/.windsurf/x", "playgrounds/shop/.github/copilot-instructions.md",
            "playgrounds/shop/.GITHUB/Copilot-Instructions.md",
            "addons/GEMINI.md", "addons/agents.md", "setups/gemini.md")],   # the guides of a kind folder are three
        "ST09": [entry("addons/echo/big.json", size=MIB + 1), entry("addons/echo/big.yml", size=MIB + 1),
                 entry("addons/echo/big.yaml", size=MIB + 1), entry("addons/echo/big.md", size=MIB + 1),
                 entry("addons/echo/big.bin", size=2 * MIB + 1), entry("addons/echo/big.png", size=2 * MIB + 1),
                 entry("catalog.yml", size=MIB + 1)],
        "ST14": [entry(p) for p in (
            "addons/Upper/compose.yml", "addons/under_score/compose.yml", "addons/dot.name/compose.yml",
            "playgrounds/Shop/compose.yml", "setups/Pair.yml", "setups/under_score.yml", "setups/.yml")],
        "ST15": [entry(p) for p in (
            "addons/NOTES.md", "playgrounds/notes.txt", "setups/pair.yaml", "setups/sub/x.yml", "setups/readme.md",
            "setups/pair.yml.bak", "addons", "playgrounds", "setups/x", "addons/readme.md", "playgrounds/Readme.md",
            "addons/README.txt", "addons/sub/README.md", "addons/orphan/data/x.json", "playgrounds/ghost/guide.md",
            "addons/deep/sub/compose.yml")],
    }

    def test_each_code_refuses_what_it_says_alone_and_nothing_else_fires(self):
        for code, entries in self.ALONE.items():
            for bad in entries:
                with self.subTest(code, path=bad.path):
                    pairs = coded(bad)
                    self.assertEqual(pairs, [(bad, code)])
                    self.assertIs(pairs[0][0], bad, "the entry it was given, not a copy")
                    self.assertEqual(refused(bad), (KEPT, 1), "selected counts it")

    def test_every_code_of_the_list_is_reached_and_no_other_is_given(self):
        reached = set(self.ALONE) | {"ST10", "ST13"}
        self.assertEqual(reached, set(CODES))
        self.assertEqual(CODES, ("ST01", "ST02", "ST03", "ST04", "ST05", "ST06", "ST07", "ST09", "ST10", "ST13", "ST14", "ST15"))

    def test_what_is_just_allowed_is_refused_by_no_code(self):
        allowed = [entry(p) for p in (
            "addons/echo/exec.sh", "addons/echo/a+b_c.d-e.yml", "addons/echo/.hidden", "addons/echo/UPPER.TXT",
            "playgrounds/shop/.ENV.EXAMPLE", "addons/echo/.envrc", "addons/echo/x.pem.txt", "addons/echo/keys.md",
            "addons/echo/dir.pem/y.txt", "addons/echo/state/x", "addons/echo/not-secrets/x", "addons/echo/secret/x",
            "addons/echo/AGENTS.md.txt", "addons/echo/my-claude.md", "addons/echo/cursor/x", "addons/echo/.gitignore",
            "addons/echo/.gitkeep", "addons/a-1/compose.yml", "setups/a-1.yml", "playgrounds/p2/compose.yml",
            "addons/echo/my.github/copilot-instructions.md", "addons/echo/github/copilot-instructions.md",
            "addons/echo/.github/sub/copilot-instructions.md", "addons/echo/.github/copilot-instructions.md.txt")]
        allowed += [entry("addons/echo/exact.json", size=MIB), entry("addons/echo/exact.md", size=MIB),
                    entry("addons/echo/exact.bin", size=2 * MIB), entry("addons/echo/exec2.sh", mode="100755"),
                    entry("addons/echo/" + "d/" * 5 + "x.yml"), entry("addons/echo/" + "a" * 184 + ".yml")]
        self.assertEqual(coded(*allowed), [])
        self.assertEqual(refused(*allowed), (KEPT + allowed, 0))

    def test_paths_that_differ_only_by_case_are_all_st13_in_the_order_given(self):
        pairs = coded(entry("addons/echo/readme.md"), entry("playgrounds/shop/Guide.md"))
        self.assertEqual([(e.path, code) for e, code in pairs], [
            ("addons/echo/README.md", "ST13"), ("playgrounds/shop/guide.md", "ST13"), ("addons/echo/readme.md", "ST13"),
            ("playgrounds/shop/Guide.md", "ST13")])
        for twins in ([entry("addons/x/data"), entry("addons/x/DATA/y")], [entry("addons/x/Dir/a"), entry("addons/x/dir/b")],
                      [entry("addons/x/a/B/c.md"), entry("addons/x/a/b/d.md")], [entry("addons/x/a.md"), entry("addons/x/a.md")]):
            with self.subTest([e.path for e in twins]):
                pairs = store.rejections([entry("addons/x/compose.yml"), *twins, entry("addons/x/other.md")])
                self.assertEqual([(e, code) for e, code in pairs], [(e, "ST13") for e in twins])

    def test_a_unit_without_its_compose_file_is_st15_for_every_file_and_a_twin_compose_file_is_st13(self):
        orphan = [entry("addons/orphan/README.md"), entry("addons/orphan/data/x.json"), entry("playgrounds/ghost/guide.md"),
                  entry("playgrounds/ghost/seed/a.yaml")]
        self.assertEqual(coded(*orphan), [(e, "ST15") for e in orphan])
        dead = [entry("addons/dead/compose.yml", mode="120000"), entry("addons/dead/README.md"), entry("addons/dead/data.json")]
        self.assertEqual(coded(*dead), [(dead[0], "ST01"), (dead[1], "ST15"), (dead[2], "ST15")])
        pairs = coded(entry("addons/echo/Compose.yml"))
        self.assertEqual([(e.path, code) for e, code in pairs], [
            ("addons/echo/.env.example", "ST15"), ("addons/echo/README.md", "ST15"), ("addons/echo/compose.yml", "ST13"),
            ("addons/echo/data/accounts.json", "ST15"), ("addons/echo/Compose.yml", "ST13")])

    def test_the_first_rule_that_applies_gives_the_code_those_about_the_file_before_those_about_the_unit(self):
        for path, code, mode, size in (
                ("addons/echo/.env", "ST01", "120000", 1),                       # the mode, before the name
                ("addons/echo/a/b/c/d/e/f/.env", "ST04", "100644", 1),         # the depth, before the names in it
                ("addons/echo/\u00fc/.env", "ST03", "100644", 1),                # a name from the top: the first is ST03
                ("addons/echo/.env/\u00fc", "ST05", "100644", 1),                # and here the first is ST05
                ("addons/echo/.env.pem", "ST05", "100644", 1),                   # the names in it, before the name of the file
                ("addons/echo/secrets/x.pem", "ST06", "100644", 1),
                ("addons/echo/.claude/AGENTS.md", "ST07", "100644", 1),
                ("addons/echo/x.pem", "ST06", "100644", 3 * MIB),                # the name of the file, before its size
                ("addons/echo/AGENTS.md", "ST07", "100644", 3 * MIB),
                ("addons/Upper/.env", "ST05", "100644", 1),                      # the file, before the unit it is in
                ("addons/Upper/x.pem", "ST06", "100644", 1),
                ("addons/Upper/big.bin", "ST09", "100644", 3 * MIB),
                ("addons/a b/compose.yml", "ST03", "100644", 1),                 # the name, before the name of the unit
                ("addons/NOTES.md", "ST15", "100644", 1)):
            with self.subTest(path):
                self.assertEqual(coded(entry(path, mode=mode, size=size))[0][1], code)

    def test_what_is_outside_the_installable_places_is_not_in_the_list_but_a_tree_can_ask_about_a_file(self):
        outside = [entry(p) for p in (
            "AGENTS.md", "CLAUDE.md", "GEMINI.md", ".claude/settings.json", ".github/copilot-instructions.md", ".env",
            "docs/.env", "docs/secrets/token", "docs/server.pem", "tests/data/big.bin", ".gitleaks.toml", "README.md")]
        outside[-3] = entry("tests/data/big.bin", size=3 * MIB)
        self.assertEqual(coded(*outside), [], "nothing there is installed, so nothing is dropped")
        self.assertEqual([store.refusal(e) for e in outside], [
            "ST07", "ST07", "ST07", "ST07", "ST07", "ST05", "ST05", "ST06", "ST06", "ST09", None, None],
            "the rules about a file, where a tree reads them whatever the place")
        for path, code in (("docs/my.github/copilot-instructions.md", None), ("my.github/copilot-instructions.md", None),
                           ("copilot-instructions.md", None), ("docs/.github/copilot-instructions.md", "ST07"),
                           ("docs/.GitHub/COPILOT-INSTRUCTIONS.MD", "ST07")):
            self.assertEqual(store.refusal(entry(path)), code, path)
        for code, entries in self.ALONE.items():
            for bad in entries:
                if code not in ("ST10", "ST13", "ST14", "ST15") and bad.path != "catalog.yml":
                    with self.subTest(code, path=bad.path):
                        self.assertEqual(store.refusal(bad), code)
        for place in ("ST14", "ST15"):   # what only the place decides is not the rule of a file
            for bad in self.ALONE[place]:
                self.assertIsNone(store.refusal(bad), bad.path)
        self.assertEqual([store.refusal(entry(g)) for g in store.LISTINGS],
                         [None if g.endswith("README.md") else "ST07" for g in store.LISTINGS], "the caller lets the guides be")

    def test_the_guides_of_a_kind_folder_are_neither_installed_nor_counted(self):
        guides = [entry(f"{folder}/{name}") for folder in ("addons", "setups", "playgrounds")
                  for name in ("README.md", "AGENTS.md", "CLAUDE.md")]
        self.assertEqual(store.LISTINGS, tuple(g.path for g in guides))
        self.assertEqual(store.GUIDES, ("README.md", "AGENTS.md", "CLAUDE.md"))
        self.assertEqual(coded(*guides), [])
        self.assertEqual(refused(*guides), (KEPT, 0))
        big = [entry(g.path, mode="100755", size=5 * MIB) for g in guides]
        self.assertEqual((coded(*big), refused(*big)), ([], (KEPT, 0)), "not installed, so what they weigh does not matter")
        self.assertEqual(store.selected(guides), ([], 0), "also with nothing else beside them")

    def test_the_same_names_inside_a_unit_stay_dropped_and_the_readme_there_is_a_file_of_the_unit(self):
        inside = [entry("addons/echo/AGENTS.md"), entry("addons/echo/CLAUDE.md"), entry("playgrounds/shop/AGENTS.md"),
                  entry("playgrounds/shop/CLAUDE.md")]
        self.assertEqual(coded(*inside), [(e, "ST07") for e in inside])
        self.assertEqual(refused(*inside), (KEPT, 4))
        self.assertIn("addons/echo/README.md", [e.path for e in store.selected(GOOD)[0]])

    def test_a_guide_that_is_not_a_regular_file_or_not_one_of_the_three_is_counted(self):
        for bad, code in ((entry("addons/AGENTS.md", mode="120000"), "ST01"), (entry("setups/README.md", mode="160000", size=0), "ST02"),
                          (entry("playgrounds/CLAUDE.md", mode="040000"), "ST01"), (entry("addons/GEMINI.md"), "ST07"),
                          (entry("addons/readme.md"), "ST15"), (entry("setups/Readme.md"), "ST15"),
                          (entry("playgrounds/README.md/x"), "ST14"), (entry("addons/AGENTS.md.txt"), "ST15"),
                          (entry("setups/NOTES.md"), "ST15")):
            with self.subTest(bad.path, mode=bad.mode):
                self.assertEqual(coded(bad), [(bad, code)])

    def test_a_guide_takes_no_part_in_the_case_twins_of_the_files_that_are_counted(self):
        self.assertEqual(coded(entry("addons/README.md"), entry("addons/readme.md")), [(entry("addons/readme.md"), "ST15")])

    def test_without_the_list_of_guides_they_are_counted_by_the_rules_of_a_file(self):
        self.patch(store, "LISTINGS", ())
        self.assertEqual([code for _, code in coded(*(entry(f"addons/{n}") for n in store.GUIDES))],
                         ["ST15", "ST07", "ST07"])

    def test_a_unit_over_its_limits_has_st10_on_each_of_its_files_and_the_others_none(self):
        big = [entry("addons/big/compose.yml")] + [entry(f"addons/big/f{i}.txt") for i in range(2000)]   # 2001 files
        pairs = coded(*big)
        self.assertEqual([(e, code) for e, code in pairs], [(e, "ST10") for e in big])
        self.assertEqual(store.overflows(GOOD + big), [store.Overflow("addon:big", "files", 2001, 2000)])
        self.assertEqual(store.overflows(GOOD + big[:-1]), [], "2000 files fit")
        self.assertEqual(coded(*big[:-1]), [])
        with self.assertRaises(gitx.CatalogError) as raised:
            store.selected(GOOD + big)
        self.assertEqual((raised.exception.code, raised.exception.status), ("too_large", 400))

        heavy = unit_of_25("heavy") + [entry("addons/heavy/one.txt", size=1)]
        self.assertEqual([(e.path, code) for e, code in coded(*heavy)], [(e.path, "ST10") for e in heavy])
        self.assertEqual(store.overflows(GOOD + heavy), [store.Overflow("addon:heavy", "bytes", 25 * MIB + 1, 25 * MIB)])
        self.assertEqual(store.overflows(GOOD + unit_of_25("fits")), [])

    def test_a_catalog_over_its_limits_has_st10_on_every_file_it_would_install(self):
        setups = [entry("catalog.yml")] + [entry(f"setups/s{i}.yml") for i in range(501)]
        pairs = store.rejections(setups + [entry("addons/echo/.env")])
        self.assertEqual([(e.path, code) for e, code in pairs],
                         [(e.path, "ST10") for e in setups] + [("addons/echo/.env", "ST05")])
        self.assertEqual(store.overflows(setups), [store.Overflow("", "units", 501, 500)])
        self.assertEqual(store.overflows(setups[:-1]), [])

        many = [entry("catalog.yml", size=0)]
        for u in range(100):
            many += [entry(f"addons/u{u}/compose.yml", size=1)] + [entry(f"addons/u{u}/f{i}.bin", size=1) for i in range(99)]
        self.assertEqual(store.overflows(many), [store.Overflow("", "files", 10001, 10000)])
        self.assertEqual({code for _, code in store.rejections(many)}, {"ST10"})
        self.assertEqual(store.overflows(many[:-1]), [])

        full = [entry("catalog.yml", size=0)] + [e for n in range(4) for e in unit_of_25(f"u{n}")]
        self.assertEqual(store.overflows(full), [])
        more = full + [entry("addons/u0/one.txt", size=1)]
        self.assertEqual(store.overflows(more), [store.Overflow("addon:u0", "bytes", 25 * MIB + 1, 25 * MIB),
                                                 store.Overflow("", "bytes", 100 * MIB + 1, 100 * MIB)])
        self.assertEqual(len(store.rejections(more)), len(more))

    def test_what_another_rule_refuses_does_not_count_against_a_limit(self):
        files = [entry("addons/big/compose.yml")] + [entry(f"addons/big/f{i}.txt") for i in range(1999)]
        self.assertEqual(coded(*files, *[entry(f"addons/big/.env.{i}") for i in range(50)]),
                         [(entry(f"addons/big/.env.{i}"), "ST05") for i in range(50)], "2000 files and 50 refused ones")

    def test_selected_is_rejections_read_the_other_way_on_any_listing(self):
        chance = random.Random(2027)
        parts = ["addons", "setups", "playgrounds", "catalog.yml", "compose.yml", "x", "..", ".", "", ".env", ".env.example",
                 "secrets", ".git", "a b", "a.yml", "\u00fc", "AGENTS.md", "README.md", "CLAUDE.md", "GEMINI.md", "k.pem",
                 "k.pfx", "-", "A", "z" * 50, "echo", "Echo", "pair.yml", "data", "Data", ".claude", ".cursorrules"]
        modes = ["100644", "100755", "120000", "160000", "040000", ""]
        seen = set()
        for _ in range(4000):
            listing = []
            for _ in range(chance.randint(0, 25)):
                path = "/".join(chance.choice(parts) for _ in range(chance.randint(1, 8)))
                if chance.random() < 0.4:
                    path = chance.choice(["addons", "playgrounds"]) + "/echo/" + path
                if chance.random() < 0.2:
                    path = chance.choice(["addons", "setups", "playgrounds"]) + "/" + chance.choice(parts)
                listing.append(Entry(chance.choice(modes), "a" * 40, chance.choice([0, 1, 10, MIB, MIB + 1, 3 * MIB]), path))
            if chance.random() < 0.5:
                listing.append(entry("addons/echo/compose.yml"))
            pairs = store.rejections(listing)
            seen.update(code for _, code in pairs)
            places = {id(e): n for n, e in enumerate(listing)}
            self.assertEqual([places[id(e)] for e, _ in pairs], sorted(places[id(e)] for e, _ in pairs), "in the order given")
            self.assertEqual(len({id(e) for e, _ in pairs}), len(pairs), "each once")
            self.assertTrue(all(code in CODES for _, code in pairs))
            try:
                kept, ignored = store.selected(listing)
            except gitx.CatalogError as error:
                self.assertEqual(error.code, "too_large")
                self.assertIn("ST10", [code for _, code in pairs])
                continue
            self.assertNotIn("ST10", [code for _, code in pairs])
            self.assertEqual(ignored, len(pairs))
            self.assertFalse({id(e) for e in kept} & {id(e) for e, _ in pairs}, "no file is kept and refused")
            self.assertEqual([places[id(e)] for e in kept], sorted(places[id(e)] for e in kept), "in the order given")
        self.assertTrue({"ST01", "ST02", "ST03", "ST04", "ST05", "ST06", "ST07", "ST09", "ST13", "ST14", "ST15"} <= seen, seen)

    def test_an_entry_needs_only_a_mode_a_path_and_a_size_and_comes_back_as_it_was_given(self):
        rows = [Row("100644", "catalog.yml", 3), Row("100644", "addons/echo/compose.yml", 3), Row("120000", "addons/echo/l", 1),
                Row("100644", "addons/README.md", 9), Row("100644", "addons/echo/.env", 1), Row("100644", "README.md", 1)]
        pairs = store.rejections(rows)
        self.assertEqual(pairs, [(rows[2], "ST01"), (rows[4], "ST05")])
        self.assertIs(pairs[0][0], rows[2])
        self.assertEqual(store.selected(rows), ([rows[0], rows[1]], 2))
        self.assertEqual(store.rejections(iter(rows)), pairs, "also from an iterator")
        self.assertEqual(store.rejections([]), [])

    def test_it_is_pure_and_changes_nothing_it_is_given(self):
        entries = tuple(GOOD) + (entry("addons/echo/.env"), entry("addons/echo/l", mode="120000"))
        before = list(entries)
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("ran a command")), \
                mock.patch.object(subprocess, "run", side_effect=AssertionError("ran a command")), \
                mock.patch("builtins.open", side_effect=AssertionError("read a file")), \
                mock.patch.object(os, "stat", side_effect=AssertionError("looked at a file")), \
                mock.patch.object(os, "lstat", side_effect=AssertionError("looked at a file")), \
                mock.patch.object(os, "listdir", side_effect=AssertionError("listed a folder")), \
                mock.patch.object(os, "scandir", side_effect=AssertionError("listed a folder")), \
                mock.patch.object(os, "walk", side_effect=AssertionError("walked a folder")):
            first = store.rejections(entries)
            second = store.rejections(iter(entries))
            store.overflows(entries)
        self.assertEqual(first, second)
        self.assertEqual(list(entries), before)
        self.assertEqual([code for _, code in first], ["ST05", "ST01"])

    def test_a_name_of_a_million_characters_is_judged_in_a_moment(self):
        """A hostile contributor's tree is the input: no name makes the judgment slow."""
        line = "a" * (1 << 20)
        hostile = [entry("addons/echo/" + line), entry("addons/echo/" + line + ".pem"), entry("addons/" + line + "/compose.yml"),
                   entry(line), entry("addons/" + "a/" * (1 << 19) + "f"), entry("addons/echo/" + "\u202e" * (1 << 19)),
                   entry("addons/echo/" + "." * (1 << 20)), entry("addons/echo/" + ".env." * (1 << 18)),
                   entry("addons/echo/" + "A/" * 4 + line), entry("x/" * (1 << 19)), entry("/" * (1 << 20)),
                   entry("addons/" + "/" * (1 << 20)), entry("setups/" + line + ".yml")]
        started = time.process_time()   # the time it works, not the time it waits for a machine that other programs share
        pairs = store.rejections(GOOD + hostile)
        self.assertLess(time.process_time() - started, 1.0)
        self.assertEqual(len(pairs), len(hostile) - 2, "the top level that is a name is not a refusal: only the rest is")
        started = time.process_time()
        store.selected(GOOD + hostile)
        self.assertLess(time.process_time() - started, 1.0)

    def test_a_listing_of_a_hundred_thousand_files_is_judged_in_seconds(self):
        listing = [entry("catalog.yml")] + [entry(f"playgrounds/p{u}/compose.yml") for u in range(300)]
        listing += [entry(f"playgrounds/p{n % 300}/data/d{n // 300}/f{n}.json") for n in range(100000)]
        started = time.process_time()
        pairs = store.rejections(listing)
        self.assertLess(time.process_time() - started, 5.0)
        self.assertEqual({code for _, code in pairs}, {"ST10"}, "300 units of 333 files each: the catalog has too many files")


class Digests(Case):

    def digest(self, entries):
        return store.unit_digests(store.selected(entries)[0])

    def test_a_digest_is_the_sha256_of_the_sorted_lines_of_the_files_of_the_unit(self):
        found = self.digest(GOOD)
        lines = sorted(f"{e.mode} {e.oid} {e.path}\n" for e in KEPT if e.path.startswith("addons/echo/"))
        self.assertEqual(found["addon:echo"], "sha256:" + hashlib.sha256("".join(lines).encode()).hexdigest())
        self.assertEqual(found["setup:pair"],
                         "sha256:" + hashlib.sha256(f"100644 {entry('setups/pair.yml').oid} setups/pair.yml\n".encode()).hexdigest())
        self.assertEqual(set(found), {"addon:echo", "playground:shop", "setup:pair", "@catalog"})
        self.assertEqual(found["@catalog"], entry("catalog.yml").oid, "the catalog file is its object id")
        for key, value in found.items():
            if key != "@catalog":
                self.assertRegex(value, r"^sha256:[0-9a-f]{64}$")

    def changed(self, replace=None, add=(), remove=()):
        base = self.digest(GOOD)
        entries = [replace.get(e.path, e) if replace else e for e in GOOD if e.path not in remove] + list(add)
        after = self.digest(entries)
        return {key for key in set(base) | set(after) if base.get(key) != after.get(key)}

    def test_a_digest_changes_when_and_only_when_the_files_of_its_unit_change(self):
        pairs = {
            "a file's content": ({"addons/echo/compose.yml": entry("addons/echo/compose.yml", oid="a" * 40)}, (), ()),
            "a file's mode": ({"addons/echo/compose.yml": entry("addons/echo/compose.yml", mode="100755")}, (), ()),
            "a file added": (None, [entry("addons/echo/new.txt")], ()),
            "a file removed": (None, (), ["addons/echo/README.md"]),
        }
        for name, (replace, add, remove) in pairs.items():
            with self.subTest(name):
                self.assertEqual(self.changed(replace, add, remove), {"addon:echo"})

    def test_a_file_that_moves_changes_the_digest_though_its_content_does_not(self):
        moved = entry("addons/echo/README.md")
        renamed = Entry(moved.mode, moved.oid, moved.size, "addons/echo/docs-readme.md")
        self.assertEqual(self.changed(None, [renamed], ["addons/echo/README.md"]), {"addon:echo"})

    def test_what_is_not_in_the_unit_does_not_change_its_digest(self):
        for add in ([entry("README.md", oid="1" * 40)], [entry("addons/echo/.env")], [entry("addons/echo/AGENTS.md")],
                    [entry("addons/echo/link", mode="120000")], [entry("addons/echo/big.json", size=MIB + 1)]):
            self.assertEqual(self.changed(None, add, ()), set(), add)

    def test_a_new_unit_and_a_removed_unit_change_their_own_digests_only(self):
        added = [entry("addons/new/compose.yml")]
        self.assertEqual(self.changed(None, added, ()), {"addon:new"})
        self.assertEqual(self.changed(None, (), ["setups/pair.yml"]), {"setup:pair"})

    def test_the_catalog_file_changes_only_its_own_key(self):
        self.assertEqual(self.changed({"catalog.yml": entry("catalog.yml", oid="b" * 40)}), {"@catalog"})
        self.assertNotIn("@catalog", store.unit_digests([e for e in KEPT if e.path != "catalog.yml"]))

    def test_the_order_of_the_listing_does_not_matter(self):
        self.assertEqual(store.unit_digests(KEPT), store.unit_digests(list(reversed(KEPT))))

    def test_the_same_files_in_two_kinds_are_two_digests(self):
        digests = store.unit_digests([entry("addons/same/compose.yml"), entry("playgrounds/same/compose.yml")])
        self.assertEqual(sorted(digests), ["addon:same", "playground:same"])
        self.assertNotEqual(digests["addon:same"], digests["playground:same"])


class Progress(Case):

    def test_the_lines_of_the_download_are_a_fraction_and_the_bytes_when_git_says_them(self):
        for line, expected in (
                ("Receiving objects:  45% (450/1000), 12.30 MiB | 5.20 MiB/s", (0.45, int(12.30 * MIB))),
                ("Receiving objects: 100% (1000/1000), 3.56 GiB | 20.00 MiB/s, done.", (1.0, int(3.56 * 1024 * MIB))),
                ("Receiving objects:   3% (30/1000)", (0.03, None)),
                ("Unpacking objects: 100% (5/5), 3.00 KiB | 1.00 MiB/s, done.", (1.0, 3072)),
                ("Unpacking objects:  50% (3/6), 512 bytes | 512.00 KiB/s", (0.5, 512)),
                ("Receiving objects:  7% (1/14)\r", (0.07, None))):
            self.assertEqual(store.parse_progress(line), expected, line)

    def test_other_lines_are_not_the_download(self):
        for line in ("Resolving deltas:  50% (5/10)", "remote: Counting objects:  50% (1/2)", "remote: Compressing objects: 100%",
                     "From file:///x", " * [new branch]      main       -> refs/lab/tip", "", "Receiving objects: lots",
                     "fatal: could not read Username"):
            self.assertIsNone(store.parse_progress(line), line)

    def test_a_download_reports_a_fraction_that_only_grows_across_chunks_of_any_size(self):
        seen = []
        reader = store._Download(seen.append)
        text = "".join(f"Receiving objects: {n:3d}% ({n}/100), 1.00 MiB | 1.00 MiB/s\r" for n in (3, 3, 10, 7, 50, 100))
        for start in range(0, len(text), 7):
            reader.feed(text[start:start + 7])
        self.assertEqual(seen, [0.03, 0.1, 0.5, 1.0])
        self.assertEqual(reader.fraction, 1.0)

    def test_lines_end_with_a_return_or_a_new_line_and_a_line_without_an_end_is_waited_for(self):
        seen = []
        reader = store._Download(seen.append)
        reader.feed("Receiving objects:  10% (1/10)")
        self.assertEqual(seen, [])
        reader.feed("\nReceiving objects:  20% (2/10)\r\nremote: x\rReceiving objects:  30%")
        self.assertEqual(seen, [0.1, 0.2])

    def test_noise_does_not_grow_without_end(self):
        reader = store._Download(None)
        reader.feed("x" * 100000)
        self.assertLessEqual(len(reader.pending), 2048)

    def test_a_download_larger_than_a_store_stops_the_fetch(self):
        reader = store._Download(None)
        reader.feed("Receiving objects:  10% (1/10), 299.00 MiB | 5.00 MiB/s\r")
        with self.assertRaises(store._TooBig):
            reader.feed("Receiving objects:  11% (1/10), 301.00 MiB | 5.00 MiB/s\r")
        with self.assertRaises(store._TooBig):
            store._Download(None).feed("Receiving objects:  1% (1/100), 2.00 GiB | 5.00 MiB/s\n")

    def test_a_progress_that_is_not_given_is_fine(self):
        store._Download(None).feed("Receiving objects: 50% (1/2)\r")


def repo_files():
    return {**gitfixture.catalog_files(), **gitfixture.hostile_files()}


class Remote(Case):
    """The setup of a test that uses a remote and a store: a bare remote and the address of it."""

    def setUp(self):
        gitfixture.require()
        gitfixture.isolate(self)
        self.folder()
        self.repo = gitfixture.make_repo(self.root / "remotes" / "hub.git", repo_files())
        self.address = gitx.parse_address(gitfixture.url(self.repo))
        self.store = self.root / ".lab" / "cache" / "git" / "hub.git"

    def fetched(self):
        """The store filled from the remote: its tip."""
        return store.fetch(self.address, "main", self.store)

    def prefetched(self):
        """The same, the first time by a fetch and after that by a copy of what that fetch made: the same objects and
        references, for a tenth of the time. Its tip."""
        cache = gitfixture.home() / "fetched" / "hub.git"
        if not cache.exists():
            tip = self.fetched()
            shutil.copytree(self.store, cache, symlinks=True)
            return tip
        shutil.copytree(cache, self.store, symlinks=True)
        return gitfixture.tip(self.repo)


class Stores(Remote):

    def config(self, key, where=None):
        return gitfixture.git("config", "--get", key, repo=where or self.store).strip()

    def test_a_store_is_bare_without_hooks_templates_or_garbage_collection(self):
        store.init(self.store)
        self.assertEqual(gitfixture.git("rev-parse", "--is-bare-repository", repo=self.store).strip(), "true")
        self.assertEqual(self.config("gc.auto"), "0")
        self.assertEqual(self.config("core.hooksPath"), "/dev/null")
        self.assertEqual(self.config("core.fsmonitor"), "false")
        self.assertEqual(self.config("transfer.fsckObjects"), "true")
        self.assertEqual(sorted(os.listdir(self.store)), ["HEAD", "config", "objects", "refs"], "no hooks, no description")

    def test_init_is_idempotent_and_makes_the_folders_above(self):
        store.init(self.store)
        self.fetched()
        before = store.resolve(self.store, "refs/lab/tip")
        store.init(self.store)
        store.init(str(self.store))
        self.assertEqual(store.resolve(self.store, "refs/lab/tip"), before, "an init keeps what the store holds")
        self.assertEqual(self.config("gc.auto"), "0")

    def test_references_commits_and_objects(self):
        tip = self.fetched()
        self.assertIsNone(store.resolve(self.store, "refs/lab/pin"))
        store.set_ref(self.store, "refs/lab/pin", tip)
        self.assertEqual(store.resolve(self.store, "refs/lab/pin"), tip)
        self.assertEqual(store.resolve(self.store, tip), tip)
        store.set_ref(self.store, "refs/lab/pin", tip)   # twice
        store.set_ref(self.store, "refs/lab/previous", tip)
        store.set_ref(self.store, "refs/lab/previous", None)
        store.set_ref(self.store, "refs/lab/previous", None)   # and deleting what is not there
        self.assertIsNone(store.resolve(self.store, "refs/lab/previous"))
        self.assertTrue(store.has(self.store, tip))
        self.assertFalse(store.has(self.store, "1" * 40))
        self.assertIsNone(store.resolve(self.store, "1" * 40))

    def test_the_three_references_may_be_named_short(self):
        tip = self.fetched()
        store.set_ref(self.store, "pin", tip)
        self.assertEqual(store.resolve(self.store, "refs/lab/pin"), tip)
        self.assertEqual(store.resolve(self.store, "pin"), tip)
        self.assertEqual(store.roots(self.store, "tip"), [tip])
        self.assertTrue(store.is_ancestor(self.store, "pin", "tip"))
        store.set_ref(self.store, "pin", None)
        self.assertIsNone(store.resolve(self.store, "pin"))
        for name in ("main", "refs", "Tip", "tip2", "head"):
            with self.assertRaises(ValueError, msg=name):
                store.set_ref(self.store, name, tip)
            with self.assertRaises(ValueError, msg=name):
                store.resolve(self.store, name)

    def test_a_reference_to_a_commit_the_store_lacks_is_an_error(self):
        self.fetched()
        with self.assertRaises(gitx.CatalogError):
            store.set_ref(self.store, "refs/lab/pin", "1" * 40)
        self.assertIsNone(store.resolve(self.store, "refs/lab/pin"))

    def test_only_the_references_and_ids_the_lab_names_are_accepted(self):
        tip = self.fetched()
        for name in ("refs/heads/main", "refs/lab/", "refs/lab/Tip", "refs/lab/a/b", "--delete", "refs/lab/../heads/x", "HEAD", ""):
            with self.assertRaises(ValueError, msg=name):
                store.set_ref(self.store, name, tip)
        for value in ("--upload-pack=x", "HEAD", "main", "refs/heads/main", tip[:10], tip + "0", tip.upper(), "", None, 7,
                      tip + "^{tree}", f"{tip}..{tip}", "refs/lab/tip^{commit}"):
            for call in (store.resolve, store.roots, store.listing, store.has):
                with self.assertRaises(ValueError, msg=(call.__name__, value)):
                    call(self.store, value)
        with self.assertRaises(ValueError):
            store.set_ref(self.store, "refs/lab/pin", "--no-verify")
        with self.assertRaises(ValueError):
            store.is_ancestor(self.store, tip, "--output=x")

    def test_roots_ancestry_and_the_history_after_a_force_push(self):
        first = self.fetched()
        store.set_ref(self.store, "refs/lab/pin", first)
        root = store.roots(self.store, first)
        self.assertEqual(root, [first], "one commit, its own root")
        second = gitfixture.commit(self.repo, {"addons/echo/README.md": "Echo 2\n"}, "Second")
        third = gitfixture.commit(self.repo, {"addons/echo/README.md": "Echo 3\n"}, "Third")
        self.assertEqual(self.fetched(), third)
        self.assertEqual(store.roots(self.store, "refs/lab/tip"), root, "the same root over a longer history")
        self.assertTrue(store.is_ancestor(self.store, first, third))
        self.assertTrue(store.is_ancestor(self.store, "refs/lab/pin", "refs/lab/tip"))
        self.assertTrue(store.is_ancestor(self.store, third, third), "a commit is its own ancestor")
        self.assertFalse(store.is_ancestor(self.store, third, first))
        rewritten = gitfixture.force_push_over(self.repo, {"catalog.yml": "x\n"})
        self.assertEqual(self.fetched(), rewritten, "a force push is followed")
        self.assertNotEqual(store.roots(self.store, "refs/lab/tip"), root, "another history has another root")
        self.assertFalse(store.is_ancestor(self.store, first, rewritten))
        self.assertFalse(store.is_ancestor(self.store, "refs/lab/pin", "refs/lab/tip"))
        self.assertTrue(store.has(self.store, first), "the pinned commit and its objects are kept")
        self.assertTrue(store.has(self.store, second))

    def test_a_commit_the_store_does_not_hold_is_not_an_ancestor(self):
        tip = self.fetched()
        self.assertFalse(store.is_ancestor(self.store, "1" * 40, tip))
        self.assertFalse(store.is_ancestor(self.store, tip, "1" * 40))

    def test_history_with_several_roots_lists_them_sorted(self):
        self.fetched()
        other = gitfixture.make_repo(self.root / "remotes" / "other.git", {"x": "1\n"})
        gitfixture.git("fetch", "--quiet", gitfixture.url(other), "+refs/heads/main:refs/other", repo=self.store)
        a, b = store.resolve(self.store, "refs/lab/tip"), gitfixture.tip(other)
        merged = gitfixture.git("commit-tree", gitfixture.git("rev-parse", f"{a}^{{tree}}", repo=self.store).strip(),
                                "-p", a, "-p", b, "-m", "Join", repo=self.store, when=gitfixture.BASE_TIME + 999).strip()
        self.assertEqual(store.roots(self.store, merged), sorted([a, b]))

    def test_commands_on_a_store_never_reach_a_repository_above_it(self):
        """A store that is missing, empty or not a repository is an error, never the repository it sits in."""
        outer = self.root / "outer"
        gitfixture.git("init", "--quiet", "--", str(outer))
        before = gitfixture.git("for-each-ref", repo=outer / ".git")
        sha = gitfixture.git("commit-tree", gitfixture.git("write-tree", repo=outer / ".git").strip(), "-m", "x",
                             repo=outer / ".git").strip()
        inside = outer / "cache" / "hub.git"
        inside.mkdir(parents=True)   # a store folder that is empty: not a repository
        for target in (inside, outer / "cache" / "none.git"):
            with self.subTest(str(target.name)):
                for call in (lambda: store.roots(target, sha), lambda: store.listing(target, sha),
                             lambda: store.set_ref(target, "refs/lab/pin", sha), lambda: store.size(target),
                             lambda: store.is_ancestor(target, sha, sha), lambda: list(store.blobs(target, [sha])),
                             lambda: store.log(target, None, sha, []), lambda: store.diff(target, sha, sha, "x")):
                    with self.assertRaises(gitx.CatalogError):
                        call()
                self.assertFalse(store.has(target, sha))
                self.assertIsNone(store.resolve(target, sha))
        self.assertEqual(gitfixture.git("for-each-ref", repo=outer / ".git"), before, "the outer repository was not touched")
        self.assertFalse((outer / ".git" / "refs" / "lab").exists())

    def test_what_a_killed_git_left_goes_when_it_is_old_and_stays_when_it_is_fresh(self):
        self.fetched()
        old = time.time() - 3600
        refs, pack = self.store / "refs" / "lab", self.store / "objects" / "pack"
        stale = [refs / "tip.lock", pack / "x.pack.lock", pack / "tmp_pack_AbC123", pack / "tmp_idx_AbC123"]
        fresh = [refs / "pin.lock", pack / "tmp_pack_new"]
        kept = [refs / "tip.lockfile", pack / "pack-0.pack", pack / "my_tmp_pack_x"]
        for path in stale + fresh + kept:
            path.write_text("")
        for path in stale + kept:
            os.utime(path, (old, old))
        link = refs / "link.lock"
        os.symlink("/nonexistent", link)
        if os.utime in os.supports_follow_symlinks:
            os.utime(link, (old, old), follow_symlinks=False)
        self.assertEqual(store.clean(self.store), 4)
        self.assertEqual([p.exists() for p in stale], [False] * 4)
        self.assertEqual([p.exists() for p in fresh + kept], [True] * 5)
        self.assertTrue(os.path.islink(link), "a link is left alone")
        self.assertEqual(store.clean(self.store), 0)
        self.assertEqual(store.clean(self.store, age=-1), 2, "with no age, every lock and temporary pack that is left")
        self.assertEqual(store.clean(self.root / "none.git"), 0)

    def test_a_fetch_goes_on_after_a_stale_lock_and_is_refused_by_a_fresh_one(self):
        tip = self.fetched()
        lock = self.store / "refs" / "lab" / "tip.lock"
        lock.write_text("")
        old = time.time() - 3600
        os.utime(lock, (old, old))
        second = gitfixture.commit(self.repo, {"README.md": "again\n"})
        self.assertEqual(self.fetched(), second)
        self.assertFalse(lock.exists())
        lock.write_text("")   # a lock git holds right now
        third = gitfixture.commit(self.repo, {"README.md": "and again\n"})
        with self.assertRaises(gitx.CatalogError) as raised:
            self.fetched()
        self.assertEqual(raised.exception.code, "git_failed")
        self.assertEqual(store.resolve(self.store, "refs/lab/tip"), second, "the store is as it was")
        self.assertNotEqual(tip, third)

    def test_size_counts_loose_objects_and_packs(self):
        self.fetched()
        self.assertGreater(store.size(self.store), 0)
        gitfixture.git("gc", "--quiet", repo=self.store)
        self.assertGreater(store.size(self.store), 0)


class Fetching(Remote):

    def test_a_fetch_answers_the_tip_and_keeps_it_as_refs_lab_tip(self):
        seen = []
        tip = store.fetch(self.address, "main", self.store, progress=seen.append)
        self.assertEqual(tip, gitfixture.tip(self.repo))
        self.assertEqual(gitfixture.git("rev-parse", "refs/lab/tip", repo=self.store).strip(), tip)
        self.assertEqual(seen[-1], 1.0)
        self.assertEqual(seen, sorted(seen), "only forward")
        self.assertTrue(all(0 < fraction <= 1.0 for fraction in seen))
        self.assertEqual(seen.count(1.0), 1, "the end is said once")

    def test_without_a_branch_the_head_of_the_remote_is_fetched(self):
        self.assertEqual(store.fetch(self.address, None, self.store), gitfixture.tip(self.repo))

    def test_a_fetch_follows_new_commits_and_force_pushes_and_keeps_what_was_pinned(self):
        first = self.fetched()
        store.set_ref(self.store, "refs/lab/pin", first)
        second = gitfixture.commit(self.repo, {"addons/echo/README.md": "newer\n"})
        self.assertEqual(store.fetch(self.address, "main", self.store), second)
        rewritten = gitfixture.force_push_over(self.repo, {"catalog.yml": "other\n"})
        self.assertEqual(store.fetch(self.address, "main", self.store), rewritten)
        self.assertTrue(store.has(self.store, first))
        self.assertEqual(store.resolve(self.store, "refs/lab/pin"), first)

    def test_tags_are_not_fetched(self):
        gitfixture.git("tag", "v1", gitfixture.tip(self.repo), repo=self.repo)
        self.fetched()
        self.assertEqual(gitfixture.git("tag", "--list", repo=self.store).strip(), "")

    def test_the_command_is_the_one_of_section_9(self):
        log = self.root / "args"
        gitfixture.fake_git(self, f"for a in \"$@\"; do if [ \"$a\" = fetch ]; then printf '%s\\n' \"$@\" > '{log}'; fi; done\n"
                                  "exec \"$REAL\" \"$@\"")
        tip = self.fetched()
        args = log.read_text().splitlines()
        end = len(gitx.HARDEN)
        self.assertEqual(args[:end], gitx.HARDEN)
        self.assertEqual(args[end:end + 2], ["--git-dir", str(self.store)])
        wanted = ["-c", "transfer.fsckObjects=true", "-c", "fetch.fsckObjects=true", "-c", "core.hooksPath=/dev/null",
                  "-c", "core.fsmonitor=false", "fetch", "--progress", "--no-tags", "--prune", "--no-recurse-submodules", "--",
                  self.address.url, "+refs/heads/main:refs/lab/tip"]
        self.assertEqual(args[end + 2:], wanted)
        self.assertEqual(tip, gitfixture.tip(self.repo))

    def test_an_address_built_by_hand_is_not_fetched(self):
        with mock.patch.object(gitx.subprocess, "Popen", side_effect=AssertionError("ran")):
            for url in ("ext::sh -c touch% /tmp/pwned", "-oProxyCommand=evil", "git://github.com/a/b.git"):
                with self.assertRaises(gitx.CatalogError, msg=url) as raised:
                    store.fetch(gitx.Address("git", url, "", "github.com/a/b", "github.com", "a/b"), "main", self.store)
                self.assertEqual(raised.exception.code, "bad_address")
        self.assertFalse(self.store.exists())

    def test_a_folder_is_not_fetched(self):
        with self.assertRaises(gitx.CatalogError) as raised:
            store.fetch(gitx.Address("folder", "", "/h/x", "~/x", "", ""), "main", self.store)
        self.assertEqual(raised.exception.code, "bad_address")
        self.assertFalse(self.store.exists())

    def test_a_branch_that_is_not_a_name_is_refused_before_anything_is_made(self):
        for name in ("--upload-pack=x", "a..b", "a b", "\t", 7):
            with self.assertRaises(gitx.CatalogError, msg=name) as raised:
                store.fetch(self.address, name, self.store)
            self.assertEqual(raised.exception.code, "branch_missing")
        self.assertFalse(self.store.exists())

    def test_no_branch_is_none_an_empty_text_or_spaces_and_the_head_of_the_remote_is_fetched(self):
        """The Branch field of the page is optional: what it sends when left empty is not a branch named ''."""
        for name in ("", "  "):
            gone = self.root / "stores" / f"{len(name)}.git"
            self.assertEqual(store.fetch(self.address, name, gone), gitfixture.tip(self.repo), repr(name))
        self.assertEqual(store.fetch(self.address, " main ", self.root / "stores" / "padded.git"), gitfixture.tip(self.repo))

    def test_a_branch_the_remote_lacks_leaves_no_new_store_and_keeps_an_old_one(self):
        with self.assertRaises(gitx.CatalogError) as raised:
            store.fetch(self.address, "nope", self.store)
        self.assertEqual((raised.exception.code, raised.exception.message),
                         ("branch_missing", "hub has no branch nope. Check the name, or leave it empty for the default."))
        self.assertFalse(self.store.exists(), "a store made by a failed first try is removed")
        tip = self.fetched()
        with self.assertRaises(gitx.CatalogError):
            store.fetch(self.address, "nope", self.store)
        self.assertEqual(store.resolve(self.store, "refs/lab/tip"), tip, "the store of an installed catalog is kept")

    def test_a_remote_that_is_not_there_is_not_found_and_leaves_nothing(self):
        gone = gitx.parse_address("file://" + str(self.root / "remotes" / "none.git"))
        with self.assertRaises(gitx.CatalogError) as raised:
            store.fetch(gone, "main", self.store)
        self.assertEqual(raised.exception.code, "not_found")
        self.assertFalse(self.store.exists())

    def test_git_refuses_an_object_that_is_not_right_and_the_store_is_removed(self):
        broken = gitfixture.make_dotgit_repo(self.root / "remotes" / "dotgit.git")
        with self.assertRaises(gitx.CatalogError) as raised:
            store.fetch(gitx.parse_address(gitfixture.url(broken)), "main", self.store)
        self.assertEqual((raised.exception.code, raised.exception.message),
                         ("fsck", "This repository is damaged. Ask its owners to check it."))
        self.assertIn("hasDotgit", raised.exception.detail["output"])
        self.assertFalse(self.store.exists())

    def test_a_store_over_the_limit_is_removed_and_the_catalog_refused(self):
        big = gitfixture.make_repo(self.root / "remotes" / "big.git", {"catalog.yml": "x\n", "addons/a/data.bin": gitfixture.filler(3 * MIB)})
        self.patch(store, "STORE_LIMIT", MIB)
        for existing in (False, True):
            if existing:
                store.init(self.store)
            with self.assertRaises(gitx.CatalogError) as raised:
                store.fetch(gitx.parse_address(gitfixture.url(big)), "main", self.store)
            self.assertEqual((raised.exception.code, raised.exception.status), ("too_large", 400))
            self.assertEqual(raised.exception.message,
                             "This catalog is larger than the Lab accepts. Ask its owners to make it smaller.")
            self.assertFalse(self.store.exists(), "the store is removed")

    def test_a_download_that_passes_the_limit_while_it_runs_is_stopped_at_once(self):
        pids = self.root / "pid"
        gitfixture.fake_git(self, "for a in \"$@\"; do if [ \"$a\" = fetch ]; then\n"
                                  f"  sleep 60 &\n  echo $! > '{pids}'\n"
                                  "  printf 'Receiving objects:  10%% (1/10), 2.00 GiB | 5.00 MiB/s\\r' >&2\n  wait\n  exit 0\nfi; done\n"
                                  "exec \"$REAL\" \"$@\"")
        with self.assertRaises(gitx.CatalogError) as raised:
            store.fetch(self.address, "main", self.store, stall=30, cap=60)
        self.assertEqual(raised.exception.code, "too_large")
        self.assertFalse(self.store.exists())
        end = time.monotonic() + 5
        pid = int(pids.read_text())
        while time.monotonic() < end:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("the download was left running")

    def test_a_fetch_that_stalls_fails_with_a_timeout_and_leaves_nothing(self):
        gitfixture.fake_git(self, "for a in \"$@\"; do if [ \"$a\" = fetch ]; then echo 'From x' >&2; sleep 60; fi; done\n"
                                  "exec \"$REAL\" \"$@\"")
        address = gitx.parse_address("https://git.example.test/team/hub.git")
        started = time.monotonic()
        with self.assertRaises(gitx.CatalogError) as raised:
            store.fetch(address, "main", self.store, stall=0.4, cap=60)
        self.assertLess(time.monotonic() - started, 6)
        self.assertEqual((raised.exception.code, raised.exception.status, raised.exception.message),
                         ("timeout", 502, "git.example.test did not answer in time. Try again."))
        self.assertFalse(self.store.exists())

    def test_a_fetch_that_never_ends_is_cut_at_the_cap_though_it_sends(self):
        gitfixture.fake_git(self, "for a in \"$@\"; do if [ \"$a\" = fetch ]; then while true; do printf . >&2; sleep 0.1; done; fi; done\n"
                                  "exec \"$REAL\" \"$@\"")
        with self.assertRaises(gitx.CatalogError) as raised:
            store.fetch(self.address, "main", self.store, stall=5, cap=0.6)
        self.assertEqual(raised.exception.code, "timeout")

    def test_the_credentials_in_what_git_said_are_not_kept_in_the_error(self):
        gitfixture.fake_git(self, "for a in \"$@\"; do if [ \"$a\" = fetch ]; then\n"
                                  "  echo \"fatal: unable to access 'https://user:s3cr3t@git.example.test/team/hub.git/': Could not resolve host: git.example.test\" >&2\n"
                                  "  exit 128; fi; done\nexec \"$REAL\" \"$@\"")
        with self.assertRaises(gitx.CatalogError) as raised:
            store.fetch(gitx.parse_address("https://git.example.test/team/hub.git"), "main", self.store)
        self.assertEqual(raised.exception.code, "unreachable")
        self.assertNotIn("s3cr3t", raised.exception.detail["output"])

    def test_fetches_into_one_store_at_the_same_time_take_turns(self):
        """A check in the background and a Check again by hand meet when the remote has news: one of the two used to fail
        on refs/lab/tip though the other had succeeded."""
        self.fetched()
        for number in range(6):
            tip = gitfixture.commit(self.repo, {"addons/echo/README.md": f"round {number}\n"})
            gate = threading.Barrier(2)

            def work():
                gate.wait(10)
                return store.fetch(self.address, "main", self.store)
            with ThreadPoolExecutor(2) as pool:
                answers = [future.result() for future in [pool.submit(work), pool.submit(work)]]
            self.assertEqual(answers, [tip, tip], number)
        self.assertEqual(store.resolve(self.store, "refs/lab/tip"), tip)

    def test_fetches_into_a_store_that_does_not_exist_yet_take_turns_and_both_succeed(self):
        gate = threading.Barrier(3)

        def work():
            gate.wait(10)
            return store.fetch(self.address, "main", self.store)
        with ThreadPoolExecutor(3) as pool:
            futures = [pool.submit(work) for _ in range(3)]
            answers = [future.result() for future in futures]
        self.assertEqual(answers, [gitfixture.tip(self.repo)] * 3)
        self.assertEqual(store.resolve(self.store, "refs/lab/tip"), answers[0])

    def test_fetches_into_other_stores_do_not_wait_for_each_other(self):
        """Each fetch waits, inside git's place in the script, until all three are there: three that took turns would
        never be."""
        gate = self.root / "gate"
        gate.mkdir()
        gitfixture.fake_git(self, "for a in \"$@\"; do if [ \"$a\" = fetch ]; then\n"
                                  f"  touch '{gate}/in-'$$\n  n=0\n"
                                  f"  while [ $(ls '{gate}' | grep -c '^in-') -lt 3 ] && [ $n -lt 200 ]; do sleep 0.05; n=$((n+1)); done\n"
                                  f"  ls '{gate}' | grep -c '^in-' > '{gate}/seen-'$$\nfi; done\n"
                                  "exec \"$REAL\" \"$@\"")
        with ThreadPoolExecutor(3) as pool:
            futures = [pool.submit(store.fetch, self.address, "main", self.root / "stores" / f"{n}.git") for n in range(3)]
            [future.result() for future in futures]
        seen = [(path.read_text().strip()) for path in gate.glob("seen-*")]
        self.assertEqual(seen, ["3", "3", "3"])

    HOLDER = (
        "import shutil, sys\n"
        "from lab_ui.catalogs import store\n"
        "with store._exclusive(sys.argv[1], 60):\n"
        "    print('held', flush=True)\n"
        "    if sys.stdin.readline().strip() == 'remove':\n"
        "        shutil.rmtree(sys.argv[1])\n")

    def holder(self):
        """Another process that holds the fetch of the store until it is told to leave (a line on its input): `remove`
        also removes the store, as a fetch that failed does."""
        process = subprocess.Popen([sys.executable, "-c", self.HOLDER, str(self.store)], cwd=Path(__file__).resolve().parents[2],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: [process.wait(), process.stdout.close(), process.stderr.close()])
        self.addCleanup(process.kill)
        self.assertEqual(process.stdout.readline().strip(), b"held")
        return process

    def release(self, process, line=b"leave\n"):
        process.stdin.write(line)
        process.stdin.close()
        self.assertEqual(process.wait(15), 0)

    def test_a_fetch_waits_for_another_process_that_is_fetching_into_the_same_store(self):
        process = self.holder()
        with self.assertRaises(gitx.CatalogError) as raised:
            store.fetch(self.address, "main", self.store, stall=0.2, cap=0.3)
        self.assertEqual((raised.exception.code, raised.exception.status), ("timeout", 502))
        self.assertFalse((self.store / "HEAD").exists(), "a fetch that did not run made nothing")
        answers = []
        thread = threading.Thread(target=lambda: answers.append(store.fetch(self.address, "main", self.store)))
        thread.start()
        thread.join(0.5)
        self.assertTrue(thread.is_alive(), "it waits for the one that holds the store")
        self.release(process)
        thread.join(30)
        self.assertEqual(answers, [gitfixture.tip(self.repo)])

    def test_a_store_that_the_holder_removed_is_made_again_by_the_one_that_waited_for_it(self):
        """The lock file is in the store: a waiter that got the lock of a file that is gone holds nothing."""
        process = self.holder()
        answers = []
        thread = threading.Thread(target=lambda: answers.append(store.fetch(self.address, "main", self.store)))
        thread.start()
        thread.join(0.3)
        self.assertTrue(thread.is_alive())
        self.release(process, b"remove\n")
        thread.join(30)
        self.assertEqual(answers, [gitfixture.tip(self.repo)])
        self.assertEqual(store.resolve(self.store, "refs/lab/tip"), answers[0])

    def test_the_lock_a_waiter_gets_is_on_the_file_that_is_there_when_the_holder_removed_the_store(self):
        """A lock on a file that was removed excludes no one: the next fetch makes another file, and takes its lock."""
        process = self.holder()
        got = []
        thread = threading.Thread(target=lambda: got.append(store._flock(str(self.store), time.monotonic() + 30, None)))
        thread.start()
        thread.join(0.3)
        self.assertTrue(thread.is_alive(), "it waits for the one that holds the store")
        self.release(process, b"remove\n")
        thread.join(30)
        (descriptor,) = got
        self.addCleanup(os.close, descriptor)
        self.assertEqual(os.fstat(descriptor).st_ino, os.stat(self.store / store.FETCH_LOCK).st_ino)

    def test_the_lock_of_a_fetch_is_released_when_it_ends_however_it_ends(self):
        self.fetched()
        with self.assertRaises(gitx.CatalogError):
            store.fetch(self.address, "nope", self.store)
        with self.assertRaises(RuntimeError):
            store.fetch(self.address, "main", self.store, progress=mock.Mock(side_effect=RuntimeError("x")))
        self.assertEqual(store.fetch(self.address, "main", self.store, stall=5, cap=5), gitfixture.tip(self.repo))
        process = self.holder()   # a lock that is still held would make this wait for the 60 s of the holder
        self.release(process)

    def test_the_lock_file_is_not_a_leftover_to_clean(self):
        self.fetched()
        lock = self.store / store.FETCH_LOCK
        self.assertTrue(lock.is_file())
        self.assertFalse(lock.name.endswith(".lock"))
        os.utime(lock, (1, 1))
        self.assertEqual(store.clean(self.store, age=0), 0)
        self.assertTrue(lock.is_file())

    def test_a_file_system_that_cannot_lock_still_has_the_lock_of_the_process(self):
        def refuse(descriptor, how):
            raise OSError(errno.ENOLCK, "No locks available")
        self.patch(store.fcntl, "flock", refuse)
        self.assertEqual(self.fetched(), gitfixture.tip(self.repo))

    def test_a_progress_callback_that_fails_stops_the_fetch_and_leaves_no_new_store(self):
        gitfixture.fake_git(self, "for a in \"$@\"; do if [ \"$a\" = fetch ]; then printf 'Receiving objects:  10%% (1/10)\\r' >&2; sleep 60; fi; done\n"
                                  "exec \"$REAL\" \"$@\"")

        def broken(fraction):
            raise RuntimeError("the page went away")
        with self.assertRaises(RuntimeError):
            store.fetch(self.address, "main", self.store, stall=30, cap=60, progress=broken)
        self.assertFalse(self.store.exists())


class Reading(Remote):

    def setUp(self):
        super().setUp()
        self.tip = self.prefetched()

    def test_the_listing_is_every_file_of_the_commit_with_its_mode_and_size(self):
        entries = store.listing(self.store, self.tip)
        by_path = {e.path: e for e in entries}
        self.assertEqual(len(entries), len(repo_files()), "every file, links and submodules too")
        self.assertEqual(by_path["addons/echo/link"].mode, "120000")
        self.assertEqual(by_path["addons/echo/link"].size, len("../../../../etc/passwd"))
        self.assertEqual((by_path["addons/echo/sub"].mode, by_path["addons/echo/sub"].size), ("160000", 0))
        self.assertEqual(by_path["addons/echo/compose.yml"].mode, "100644")
        self.assertEqual(by_path["addons/echo/big.json"].size, MIB + 1)
        self.assertIn("addons/echo/back\\slash.yml", by_path, "a name is given as it is, not quoted")
        self.assertIn("addons/echo/bad name.yml", by_path)
        for e in entries:
            self.assertRegex(e.oid, r"^[0-9a-f]{40}$")

    def test_what_was_pinned_is_read_beside_a_history_that_was_replaced(self):
        store.set_ref(self.store, "refs/lab/pin", self.tip)
        rewritten = gitfixture.force_push_over(self.repo, {"catalog.yml": "other\n", "addons/new/compose.yml": "services: {}\n"},
                                               "A new beginning")
        gitfixture.commit(self.repo, {"addons/new/README.md": "new\n"}, "Describe the new add-on")
        tip = self.fetched()
        self.assertNotEqual(store.roots(self.store, tip), store.roots(self.store, "refs/lab/pin"))
        self.assertEqual([e.path for e in store.listing(self.store, tip)], ["addons/new/README.md", "addons/new/compose.yml", "catalog.yml"])
        self.assertEqual(len(store.listing(self.store, "refs/lab/pin")), len(repo_files()))
        oid = next(e.oid for e in store.listing(self.store, "refs/lab/pin") if e.path == "catalog.yml")
        self.assertEqual(list(store.blobs(self.store, [oid])), [(oid, gitfixture.catalog_files()["catalog.yml"].encode())])
        self.assertEqual(store.log(self.store, "refs/lab/pin", tip, ["addons/new"]), ["Describe the new add-on", "A new beginning"])
        self.assertIn("-  name: Acme Hub", store.diff(self.store, "refs/lab/pin", tip, "catalog.yml"))
        self.assertIn("+other", store.diff(self.store, "refs/lab/pin", tip, "catalog.yml"))
        self.assertEqual(store.touching(self.store, tip, "catalog.yml"), [rewritten])
        self.assertEqual(store.read(self.store, "refs/lab/pin", "catalog.yml"), gitfixture.catalog_files()["catalog.yml"].encode())
        self.assertFalse(store.is_ancestor(self.store, "refs/lab/pin", "refs/lab/tip"))

    def test_a_name_with_a_newline_or_quotes_comes_back_as_it_is(self):
        gitfixture.commit(self.repo, {"addons/echo/a\nb": "x", 'addons/echo/q"t': "y", "addons/echo/tab\there": "z",
                                      "addons/echo/ünicode.md": "w"})
        tip = self.fetched()
        paths = {e.path for e in store.listing(self.store, tip)}
        self.assertTrue({"addons/echo/a\nb", 'addons/echo/q"t', "addons/echo/tab\there", "addons/echo/ünicode.md"} <= paths)

    def test_the_filter_over_a_real_hostile_listing_keeps_the_good_and_counts_the_rest(self):
        kept, ignored = store.selected(store.listing(self.store, self.tip))
        self.assertEqual(sorted(e.path for e in kept), sorted((
            "catalog.yml", "addons/echo/.env.example", "addons/echo/README.md", "addons/echo/compose.yml",
            "setups/pair.yml", "playgrounds/shop/compose.yml", "playgrounds/shop/guide.md",
            "playgrounds/shop/seed/apim/apis/shop.json")))
        self.assertEqual(ignored, len(gitfixture.hostile_files()) - 1)
        for e in kept:
            self.assertIn(e.mode, ("100644", "100755"))

    def test_the_digests_of_a_real_listing_follow_the_files_across_commits(self):
        before = store.unit_digests(store.selected(store.listing(self.store, self.tip))[0])
        gitfixture.commit(self.repo, {"addons/echo/README.md": "Echo, changed\n", "README.md": "top\n"})
        after_tip = self.fetched()
        after = store.unit_digests(store.selected(store.listing(self.store, after_tip))[0])
        self.assertEqual({k for k in before if before[k] != after[k]}, {"addon:echo"})
        gitfixture.commit(self.repo, {"addons/echo/README.md": "Echo\n"})   # the content of the first commit again
        again = store.unit_digests(store.selected(store.listing(self.store, self.fetched()))[0])
        self.assertEqual(again, before, "the same files give the same digest at another commit")

    def test_a_listing_too_large_to_read_is_too_large(self):
        self.patch(store, "LISTING_LIMIT", 100)
        with self.assertRaises(gitx.CatalogError) as raised:
            store.listing(self.store, self.tip)
        self.assertEqual(raised.exception.code, "too_large")

    def test_a_record_that_git_did_not_write_is_fsck(self):
        real = gitx.run

        def odd(args, **how):
            if "ls-tree" in args:
                return gitx.Result(0, b"100644 blob nothex  1\tx\0", "")
            return real(args, **how)
        self.patch(gitx, "run", odd)
        with self.assertRaises(gitx.CatalogError) as raised:
            store.listing(self.store, self.tip)
        self.assertEqual(raised.exception.code, "fsck")

    def oids(self, *paths):
        by_path = {e.path: e.oid for e in store.listing(self.store, self.tip)}
        return [by_path[p] for p in paths]

    def test_the_commits_that_changed_a_file_are_listed_newest_first(self):
        first = gitfixture.commit(self.repo, {"catalog.yml": "version 2\n"}, "Version 2")
        gitfixture.commit(self.repo, {"README.md": "other\n"}, "Another file")
        second = gitfixture.commit(self.repo, {"catalog.yml": "version 3\n"}, "Version 3")
        tip = self.fetched()
        self.assertEqual(store.touching(self.store, tip, "catalog.yml"), [second, first, self.tip])
        self.assertEqual(store.touching(self.store, tip, "catalog.yml", limit=2), [second, first])
        self.assertEqual(store.touching(self.store, "refs/lab/tip", "README.md")[1:], [self.tip])
        self.assertEqual(store.touching(self.store, tip, "none.yml"), [])
        for bad in (0, -1, 1001, "5", None):
            with self.assertRaises(ValueError, msg=bad):
                store.touching(self.store, tip, "catalog.yml", limit=bad)
        for path in ("--all", "../x", "a b", ""):
            with self.assertRaises(ValueError, msg=path):
                store.touching(self.store, tip, path)

    def test_a_file_is_read_at_a_commit_only_when_it_is_a_regular_file_of_a_size_that_is_allowed(self):
        files = gitfixture.catalog_files()
        self.assertEqual(store.read(self.store, self.tip, "catalog.yml"), files["catalog.yml"].encode())
        newer = gitfixture.commit(self.repo, {"catalog.yml": "version 2\n"})
        self.fetched()
        self.assertEqual(store.read(self.store, newer, "catalog.yml"), b"version 2\n")
        self.assertEqual(store.read(self.store, self.tip, "catalog.yml"), files["catalog.yml"].encode(), "an older commit")
        for path in ("addons/echo/link", "addons/echo/sub", "addons/echo", "none.yml", "addons/echo/none"):
            self.assertIsNone(store.read(self.store, self.tip, path), path)
        self.assertEqual(len(store.read(self.store, self.tip, "addons/echo/big.bin", limit=3 * MIB)), 2 * MIB + 1)
        self.assertIsNone(store.read(self.store, self.tip, "addons/echo/big.bin"))
        self.assertIsNone(store.read(self.store, self.tip, "addons/echo/compose.yml", limit=10))
        for bad in ("--all", "../x", "a b", "", "addons/echo/back\\slash.yml"):
            with self.assertRaises(ValueError, msg=bad):
                store.read(self.store, self.tip, bad)
        with self.assertRaises(ValueError):
            store.read(self.store, "--all", "catalog.yml")

    def test_blobs_are_read_in_the_order_asked_with_their_ids(self):
        wanted = self.oids("catalog.yml", "addons/echo/compose.yml", "addons/echo/README.md", "addons/echo/big.bin")
        found = list(store.blobs(self.store, wanted))
        self.assertEqual([oid for oid, _ in found], wanted)
        self.assertEqual(found[1][1], gitfixture.catalog_files()["addons/echo/compose.yml"].encode())
        self.assertEqual(found[3][1], gitfixture.filler(2 * MIB + 1), "a binary file is read whole")
        for oid, data in found:
            self.assertEqual(gitfixture.git("hash-object", "--stdin", input=data).strip(), oid)

    def test_blobs_read_in_batches_and_the_same_file_twice(self):
        self.patch(store, "BATCH", 2)
        wanted = self.oids("catalog.yml", "addons/echo/compose.yml", "addons/echo/README.md", "setups/pair.yml",
                           "playgrounds/shop/guide.md") * 2
        found = list(store.blobs(self.store, wanted))
        self.assertEqual([oid for oid, _ in found], wanted)
        self.assertEqual(list(store.blobs(self.store, [])), [])

    def test_an_id_that_is_not_one_is_refused_when_asked_not_when_read(self):
        for bad in ("--batch", "abc", "HEAD", "g" * 40, "A" * 40, "1" * 41):
            with self.assertRaises(ValueError, msg=bad):
                store.blobs(self.store, ["1" * 40, bad])

    def test_an_object_the_store_lacks_is_missing_and_a_repair_can_tell(self):
        with self.assertRaises(store.MissingObject) as raised:
            list(store.blobs(self.store, ["1" * 40]))
        self.assertEqual((raised.exception.code, raised.exception.detail), ("fsck", {"oid": "1" * 40}))
        self.assertIsInstance(raised.exception, gitx.CatalogError)
        with self.assertRaises(store.MissingObject):
            list(store.blobs(self.store, self.oids("catalog.yml") + ["1" * 40]))

    def test_an_object_that_is_not_a_file_is_not_a_blob(self):
        with self.assertRaises(gitx.CatalogError) as raised:
            list(store.blobs(self.store, [self.tip]))
        self.assertEqual(raised.exception.code, "fsck")

    def test_too_much_content_in_a_batch_is_too_large(self):
        self.patch(store, "BLOBS_LIMIT", 1000)
        with self.assertRaises(gitx.CatalogError) as raised:
            list(store.blobs(self.store, self.oids("addons/echo/big.bin")))
        self.assertEqual(raised.exception.code, "too_large")

    def commits(self, *subjects):
        last = self.tip
        for index, subject in enumerate(subjects):
            last = gitfixture.commit(self.repo, {f"addons/echo/n{index}.txt": f"{index}\n"}, subject)
        return self.fetched() if subjects else last

    def test_the_log_is_the_subjects_newest_first_without_merges_and_dependency_bumps(self):
        tip = self.commits("Add the echo add-on", "Bump wiremock from 3.9.0 to 3.9.1", "Merge pull request #4 from x/y",
                           'Revert "Add the echo add-on"', "Fix the guide of the shop", "Merge branch 'x'", "Bumper cars are fun")
        self.assertEqual(store.log(self.store, self.tip, tip, ["addons/echo"]),
                         ["Bumper cars are fun", "Fix the guide of the shop", "Add the echo add-on"])

    def test_the_log_limit_the_range_and_the_folders(self):
        tip = self.commits(*[f"Change {n}" for n in range(8)])
        self.assertEqual(len(store.log(self.store, self.tip, tip, ["addons/echo"])), 5)
        self.assertEqual(store.log(self.store, self.tip, tip, ["addons/echo"], limit=2), ["Change 7", "Change 6"])
        self.assertEqual(store.log(self.store, self.tip, tip, ["playgrounds/shop"]), [], "no commit touched that folder")
        self.assertEqual(store.log(self.store, self.tip, tip, ["setups/pair.yml"]), [])
        self.assertEqual(store.log(self.store, tip, tip, ["addons/echo"]), [], "nothing between a commit and itself")
        everything = store.log(self.store, None, tip, ["addons/echo"], limit=50)
        self.assertEqual(everything[-1], "Initial commit")
        self.assertEqual(len(everything), 9)
        self.assertEqual(store.log(self.store, self.tip, tip, [], limit=1), ["Change 7"], "no folder is every folder")

    def test_a_merge_commit_with_an_ordinary_subject_is_not_listed(self):
        tip = self.commits("Side work")
        merge = gitfixture.git("commit-tree", gitfixture.git("rev-parse", f"{tip}^{{tree}}", repo=self.store).strip(),
                               "-p", tip, "-p", self.tip, "-m", "Combine the two", repo=self.store,
                               when=gitfixture.BASE_TIME + 5000).strip()
        self.assertEqual(store.log(self.store, self.tip, merge, ["addons/echo"]), ["Side work"])

    def test_a_subject_is_cleaned_and_clipped(self):
        long = "Make the thing that the people use do more of what they asked for in the first place " * 2
        tip = self.commits(long, "Two\tspaces\x1b[31m red\x07 and‮ bidi", "   Padded   subject   ")
        found = store.log(self.store, self.tip, tip, ["addons/echo"])
        self.assertEqual(found[0], "Padded subject")
        self.assertEqual(found[1], "Two spaces [31m red and bidi")
        self.assertTrue(70 < len(found[2]) <= 80, len(found[2]))
        self.assertTrue(found[2].startswith("Make the thing") and found[2].endswith("…"))
        for subject in found:
            self.assertFalse(re.search(r"[\x00-\x1f\x7f‮]", subject), repr(subject))

    def test_a_signature_check_that_the_person_asked_git_for_is_not_part_of_the_subjects(self):
        """`log.showSignature` in the configuration of the person prints gpg's lines among the subjects, and runs gpg for
        each commit."""
        home = Path(os.environ["HOME"])
        ran = home / "gpg-ran"
        (home / "gpg").write_text(f"#!/bin/sh\necho x >> '{ran}'\necho 'gpg: Signature made Mon 01 Jan 2026 by someone' >&2\nexit 0\n")
        (home / "gpg").chmod(0o755)
        (home / ".gitconfig").write_text(f"[log]\n\tshowSignature = true\n[gpg]\n\tprogram = {home / 'gpg'}\n")
        tip = self.commits("Add a setup", "Fix the echo add-on")
        signed = self.tip
        for commit in reversed(gitfixture.git("rev-list", f"{self.tip}..{tip}", repo=self.store).split()):
            head, _, message = gitfixture.git("cat-file", "commit", commit, repo=self.store).partition("\n\n")
            lines = [f"parent {signed}" if line.startswith("parent ") else line for line in head.split("\n")]
            raw = "\n".join(lines) + "\ngpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEz\n -----END PGP SIGNATURE-----\n\n" + message
            signed = gitfixture.git("hash-object", "-t", "commit", "-w", "--stdin", repo=self.store, input=raw).strip()
        gitfixture.git("update-ref", "refs/lab/signed", signed, repo=self.store)
        plain = subprocess.run([gitfixture._GIT, "--git-dir", str(self.store), "log", "--format=%s", signed],
                               env=gitx.environment(), capture_output=True, text=True, cwd=str(home))
        self.assertIn("gpg: Signature made", plain.stdout, "the setup of this test makes git print what the lab must not read")
        ran.unlink()
        self.assertEqual(store.log(self.store, self.tip, signed, ["addons/echo"]), ["Fix the echo add-on", "Add a setup"])
        self.assertEqual(store.log(self.store, None, signed, []), ["Fix the echo add-on", "Add a setup", "Initial commit"])
        self.assertFalse(ran.exists(), "gpg was run for a log the person did not ask to be checked")

    def test_the_log_takes_only_revisions_and_paths_it_can_trust(self):
        for bad in ("--all", "-p", "HEAD", "a..b"):
            with self.assertRaises(ValueError, msg=bad):
                store.log(self.store, bad, self.tip, [])
            with self.assertRaises(ValueError, msg=bad):
                store.log(self.store, None, bad, [])
        for folder in ("-p", "--output=x", "/etc", "../x", "a/../b", "a b", "a*", "a?", ":(top)x", "x\ny", ""):
            with self.assertRaises(ValueError, msg=folder):
                store.log(self.store, None, self.tip, [folder])

    def test_the_diff_of_a_file_between_two_commits(self):
        new = gitfixture.commit(self.repo, {"addons/echo/README.md": "Echo\nand more\n", "addons/echo/added.txt": "fresh\n",
                                            "addons/echo/compose.yml": None, "addons/echo/bin.dat": b"\x00\x01\x02" * 100})
        self.fetched()
        text = store.diff(self.store, self.tip, new, "addons/echo/README.md")
        self.assertIn("diff --git a/addons/echo/README.md b/addons/echo/README.md", text)
        self.assertIn("+and more", text)
        self.assertIn(" Echo", text)
        self.assertIn("new file mode", store.diff(self.store, self.tip, new, "addons/echo/added.txt"))
        self.assertIn("deleted file mode", store.diff(self.store, self.tip, new, "addons/echo/compose.yml"))
        self.assertEqual(store.diff(self.store, self.tip, new, "catalog.yml"), "", "nothing changed")
        self.assertEqual(store.diff(self.store, self.tip, new, "addons/echo/bin.dat"), "", "a binary file is not shown")
        self.assertEqual(store.diff(self.store, self.tip, new, "addons/echo/none.txt"), "")

    def test_a_diff_that_is_too_large_to_show_is_not_shown(self):
        new = gitfixture.commit(self.repo, {"addons/echo/README.md": "line\n" * 5000})
        self.fetched()
        self.assertGreater(len(store.diff(self.store, self.tip, new, "addons/echo/README.md")), 1000)
        self.patch(store, "DIFF_LIMIT", 500)
        self.assertEqual(store.diff(self.store, self.tip, new, "addons/echo/README.md"), "")

    def test_the_diff_takes_only_revisions_and_paths_it_can_trust(self):
        for bad in ("--output=x", "-x", "../x", "/etc/passwd", "a b", ""):
            with self.assertRaises(ValueError, msg=bad):
                store.diff(self.store, self.tip, self.tip, bad)
        with self.assertRaises(ValueError):
            store.diff(self.store, "--output=x", self.tip, "catalog.yml")

    def test_the_diff_runs_no_external_program(self):
        """A diff driver in the configuration of the store is a program; the lab asks for none."""
        gitfixture.git("config", "diff.external", "/bin/false", repo=self.store)
        gitfixture.git("config", "diff.lab.textconv", "/bin/false", repo=self.store)
        new = gitfixture.commit(self.repo, {"addons/echo/README.md": "Echo\nmore\n"})
        self.fetched()
        self.assertIn("+more", store.diff(self.store, self.tip, new, "addons/echo/README.md"))


class Fixtures(Case):
    """The repositories of the tests are what the tests say they are."""

    def setUp(self):
        gitfixture.require()
        self.folder()

    def test_the_same_files_give_the_same_commit_everywhere(self):
        one = gitfixture.make_repo(self.root / "a.git", gitfixture.catalog_files())
        two = gitfixture.make_repo(self.root / "b" / "c.git", gitfixture.catalog_files())
        self.assertEqual(gitfixture.tip(one), gitfixture.tip(two))
        self.assertEqual(gitfixture.commit(one, {"x": "1"}), gitfixture.commit(two, {"x": "1"}))
        three = gitfixture.make_repo(self.root / "d.git", {"catalog.yml": "x\n"})
        self.assertNotEqual(gitfixture.tip(three), gitfixture.tip(one))

    def test_a_copy_of_a_repository_is_independent(self):
        one = gitfixture.make_repo(self.root / "a.git", gitfixture.catalog_files())
        two = gitfixture.make_repo(self.root / "b.git", gitfixture.catalog_files())
        gitfixture.commit(one, {"x": "1"})
        self.assertNotEqual(gitfixture.tip(one), gitfixture.tip(two))

    def test_a_repository_is_bare_and_a_remote_and_offers_no_partial_clone(self):
        repo = gitfixture.make_repo(self.root / "a.git", gitfixture.catalog_files(), branch="trunk")
        self.assertEqual(gitfixture.git("rev-parse", "--is-bare-repository", repo=repo).strip(), "true")
        self.assertEqual(gitfixture.current(repo), "trunk")
        self.assertEqual(gitfixture.bare(repo), repo)
        self.assertTrue(gitfixture.url(repo).startswith("file:///"))
        self.assertEqual(gitfixture.git("config", "--get", "uploadpack.allowFilter", repo=repo).strip(), "false")
        self.assertFalse((repo / "hooks").exists())

    def test_a_working_repository_gets_a_bare_clone(self):
        work = self.root / "work"
        gitfixture.git("init", "--quiet", "--", str(work))
        (work / "f.txt").write_text("x")
        gitfixture.git("-C", str(work), "add", "f.txt")
        gitfixture.git("-C", str(work), "commit", "--quiet", "-m", "one")
        clone = gitfixture.bare(work)
        self.assertEqual((clone.name, gitfixture.git("rev-parse", "--is-bare-repository", repo=clone).strip()), ("work.git", "true"))

    def test_a_commit_lays_files_over_the_tip_and_none_removes(self):
        repo = gitfixture.make_repo(self.root / "a.git", {"a.txt": "1", "dir/b.txt": "2"})
        sha = gitfixture.commit(repo, {"a.txt": "11", "dir/b.txt": None, "c.txt": gitfixture.Executable("#!/bin/sh\n")}, "Change")
        files = {line.split("\t")[1]: line.split()[0] for line in gitfixture.git("ls-tree", "-r", sha, repo=repo).splitlines()}
        self.assertEqual(files, {"a.txt": "100644", "c.txt": "100755"})
        self.assertEqual(gitfixture.git("show", f"{sha}:a.txt", repo=repo), "11")

    def test_a_force_push_gives_another_root_and_keeps_the_old_commits(self):
        repo = gitfixture.make_repo(self.root / "a.git", {"a.txt": "1"})
        first = gitfixture.tip(repo)
        second = gitfixture.force_push_over(repo, {"a.txt": "1"})
        self.assertNotEqual(first, second, "even with the same files")
        self.assertEqual(gitfixture.git("rev-list", "--max-parents=0", second, repo=repo).split(), [second])
        self.assertEqual(gitfixture.git("cat-file", "-t", first, repo=repo).strip(), "commit", "the old history is still there")

    def test_links_and_submodules_are_what_they_say(self):
        repo = gitfixture.make_repo(self.root / "a.git", {"l": gitfixture.Symlink("target"), "s": gitfixture.Gitlink("2" * 40)})
        modes = {line.split("\t")[1]: line.split()[0] for line in gitfixture.git("ls-tree", "-r", "HEAD", repo=repo).splitlines()}
        self.assertEqual(modes, {"l": "120000", "s": "160000"})

    def test_filler_is_the_size_asked_the_same_every_time_and_does_not_compress(self):
        data = gitfixture.filler(100000)
        self.assertEqual((len(data), data == gitfixture.filler(100000)), (100000, True))
        self.assertNotEqual(gitfixture.filler(100, "other"), gitfixture.filler(100))
        self.assertGreater(len(zlib.compress(data)), 99000)

    def test_without_git_a_test_says_it_is_skipped_and_the_run_is_told_once(self):
        self.patch(gitfixture, "_GIT", None)
        self.patch(gitfixture, "_SAID", [])
        shown = StringIO()
        with redirect_stderr(shown):
            for _ in range(5):
                with self.assertRaises(unittest.SkipTest):
                    gitfixture.require()
        self.assertEqual(shown.getvalue().count("SKIP: git is not installed"), 1, "the line is not repeated for every test")
        self.assertFalse(gitfixture.available())

    def test_the_fixture_finds_git_as_the_product_does_and_never_runs_the_macos_shim(self):
        """On a Mac without the Command Line Tools, running /usr/bin/git opens an installer dialog: `make test` must not."""
        ran = self.root / "ran"
        shim = self.root / "shim" / "git"
        shim.parent.mkdir()
        shim.write_text(f"#!/bin/sh\ntouch '{ran}'\n")
        shim.chmod(0o755)
        asked = []

        def tools(argv, **how):
            asked.append(argv)
            return subprocess.CompletedProcess(argv, 2, b"", b"")
        with mock.patch.dict(os.environ, {"PATH": str(shim.parent)}):
            self.patch(gitx, "PLATFORM", "darwin")
            self.patch(gitx, "SHIM", str(shim))
            self.patch(gitx, "_TOOLS", {"at": None, "ok": False})
            self.patch(gitx.subprocess, "run", tools)
            self.assertIsNone(gitfixture._find())
        self.assertEqual(asked, [["xcode-select", "-p"]])
        self.assertFalse(ran.exists(), "the shim was run")
        self.assertEqual(gitfixture._GIT, gitx.executable(), "the git of the fixture is the one the product finds")

    def test_the_fixture_git_ignores_the_configuration_of_the_person(self):
        with mock.patch.dict(os.environ, {"GIT_AUTHOR_NAME": "Someone Else", "GIT_DIR": "/nonexistent", "GIT_CONFIG_COUNT": "1",
                                          "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "Evil"}):
            repo = gitfixture.make_repo(self.root / "a.git", {"a": "1"})
            self.assertEqual(gitfixture.git("log", "-1", "--format=%an <%ae>", repo=repo).strip(), "Test Author <author@example.test>")


if __name__ == "__main__":
    unittest.main()
