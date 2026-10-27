"""The registry: the catalogs this machine has, read and written as says. Config and lock round trips, the
tolerance of every read, the manifest that vouches for the units of a git install, namespaces, addresses, the
suggestions file and what the resolver reads. Everything is on disk in a temporary project folder: no network, no git
except where a fixture catalog is installed from a local bare repository (a visible skip without git), no Docker."""

import fcntl
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from lab_ui import paths
from lab_ui.catalogs import gitx, registry, resolve

from .. import Case, fixtures

A, B, C = "a" * 40, "b" * 40, "c" * 40
LAB_UI = Path(__file__).resolve().parents[2]
LISTED = {"id": "gravitee", "name": "Base demos", "description": "Playgrounds, setups and add-ons",
          "url": "https://example.invalid/gravitee.git", "branch": "main"}
ROW_KEYS = {"name", "description", "version", "lab", "audience", "kind", "requires_catalogs", "state", "units",
            "ignored", "error", "pending", "listed", "host"}


def git_entry(ns, **more):
    return {"id": ns, "kind": "git", "url": f"https://example.invalid/{ns}.git", "branch": "main", "auto": True,
            "added": "2026-10-16T09:00:00Z", **more}


def folder_entry(ns, path, **more):
    return {"id": ns, "kind": "folder", "path": str(path), "added": "2026-10-16T09:00:00Z", **more}


def lock_entry(**fields):
    return {"pin": A, "previous": None, "tip": A, "roots": [B], "audience": "public", "name": "Hub", "version": "1.0.0",
            "release": "1.0.0", "lab": 1, "installed": "2026-10-16T09:00:30Z", "checked": "2026-10-16T09:01:00Z", "error": None,
            "pending": None, **fields}


class Disk(Case):
    """A project folder (the ROOT of paths, which the registry reads at each call) and the files of in it."""

    def setUp(self):
        self.folder(paths)
        self.patch(paths, "_default", (None, None))

    def json(self, relative, value, mode=0o600):
        file = self.root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        file.chmod(mode)
        return file

    def config(self, *entries, **top):
        return self.json(".lab/catalogs.json", {"schema": 1, "catalogs": list(entries), **top})

    def lockfile(self, **entries):
        return self.json(".lab/catalogs.lock", {"schema": 1, "catalogs": entries})

    def default(self, *ids):
        entries = [{**LISTED, "id": ns, "url": f"https://example.invalid/{ns}.git"} for ns in ids]
        return self.json("catalogs.default.json", {"schema": 1, "catalogs": entries}, 0o644)

    def raw(self, relative, data):
        file = self.root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        return file

    def unit_path(self, key):
        kind, name = key.split(":")
        return {"addon": f"addons/{name}/compose.yml", "setup": f"setups/{name}.yml",
                "playground": f"playgrounds/{name}/compose.yml"}[kind]

    def install(self, ns, commit=A, units=("addon:one",), catalog_yml=None, **manifest):
        """A git install: the manifest that vouches for `units` (and the files of them), and a catalog.yml."""
        base = f".lab/catalogs/{ns}"
        document = {"schema": 1, "catalog": ns, "commit": commit, "files": {}, "ignored": 0,
                    "units": {key: "sha256:" + "0" * 64 for key in units}, **manifest}
        self.json(f"{base}/.installed.json", document, 0o644)
        for key in units:
            self.write(f"{base}/{self.unit_path(key)}", "services: {}\n")
        self.write(f"{base}/catalog.yml", catalog_yml if catalog_yml is not None else
                   f"x-gravitee-catalog:\n  schema: 1\n  id: {ns}\n  name: {ns.title()}\n  version: 1.0.0\n  lab: 1\n")
        return self.root / base

    def old(self, file, age=3600):
        """A file as old as one that nothing has written for a while (a cache may trust it)."""
        past = time.time() - age
        os.utime(file, (past, past))
        return file

    def tree(self):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))


class Errors(Case):

    def test_a_refusal_has_a_code_a_line_a_status_and_its_facts(self):
        error = registry.CatalogError("busy", "Another catalog change is running.", 409, id="x")
        self.assertEqual((error.code, error.message, error.status, error.detail),
                         ("busy", "Another catalog change is running.", 409, {"id": "x"}))
        self.assertEqual(str(error), "Another catalog change is running.")
        self.assertIsInstance(error, Exception)

    def test_the_status_is_a_bad_request_unless_said_and_the_facts_are_empty(self):
        error = registry.CatalogError("not_found", "Nothing.")
        self.assertEqual((error.status, error.detail), (400, {}))

    def test_git_raises_this_class_and_not_a_copy_of_it(self):
        from lab_ui.catalogs import store
        self.assertIs(gitx.CatalogError, registry.CatalogError)
        self.assertTrue(issubclass(store.MissingObject, registry.CatalogError))
        with self.assertRaises(registry.CatalogError) as raised:
            gitx.parse_address("git://example.invalid/a/b")
        self.assertEqual((raised.exception.code, raised.exception.status), ("bad_address", 400))


class ConfigRead(Disk):

    def test_a_missing_file_is_a_first_run_and_the_only_none(self):
        self.assertIsNone(registry.read_config())
        self.assertIsNone(registry.config_problem())
        self.assertEqual((registry.entries(), registry.entry("acme-hub")), ([], None))

    def test_a_file_that_is_there_reads_whatever_it_holds(self):
        self.config(git_entry("acme-hub"), folder_entry("my-catalog", "/home/me/code/mine"))
        config = registry.read_config()
        self.assertEqual(config["schema"], 1)
        self.assertEqual([one["id"] for one in config["catalogs"]], ["acme-hub", "my-catalog"])
        self.assertIsNone(registry.config_problem())
        self.assertEqual(registry.entry("my-catalog")["path"], "/home/me/code/mine")

    def test_what_is_read_is_the_caller_s_to_change(self):
        self.config(git_entry("acme-hub"))
        registry.read_config()["catalogs"][0]["url"] = "changed"
        registry.entries().clear()
        self.assertEqual(registry.entry("acme-hub")["url"], "https://example.invalid/acme-hub.git")

    def test_a_file_that_cannot_be_read_is_an_empty_list_and_never_a_first_run(self):
        # "No file" is a first run: damage must read as something else, or the Lab would add the default again
        cases = {"empty": b"", "not json": b"not json", "a list": b"[]", "a string": b'"x"', "a number": b"7",
                 "null": b"null", "newer schema": b'{"schema": 2, "catalogs": []}',
                 "schema text": b'{"schema": "1", "catalogs": []}', "schema zero": b'{"schema": 0, "catalogs": []}',
                 "schema true": b'{"schema": true, "catalogs": []}', "no list": b'{"schema": 1}',
                 "a dictionary": b'{"schema": 1, "catalogs": {}}', "cut": b'{"schema": 1, "catalogs": [',
                 "not text": b"\xff\xfe\x00{", "nesting": b"[" * 200000, "nul": b'{"schema": 1\x00}'}
        for label, data in cases.items():
            with self.subTest(label):
                self.raw(".lab/catalogs.json", data)
                self.assertEqual(registry.read_config(), {"schema": 1, "catalogs": []})
                self.assertIn("could not be read", registry.config_problem())
                self.assertEqual((registry.entries(), registry.precedence()), ([], ["local"]))

    def test_json_that_is_not_json_is_not_read(self):
        # Python reads NaN and numbers of any length, a file of the Lab holds neither, and a NaN could not be written back
        for text in ('{"schema": 1, "catalogs": [], "x": NaN}', '{"schema": 1, "catalogs": [], "x": Infinity}',
                     '{"schema": 1, "catalogs": [], "x": -Infinity}', '{"schema": 1, "catalogs": [], "x": ' + "9" * 5000 + "}"):
            with self.subTest(text[:50]):
                self.raw(".lab/catalogs.json", text.encode())
                self.assertEqual(registry.entries(), [])
                self.assertIn("not valid JSON", registry.config_problem())
        self.raw(".lab/catalogs.json", b'{"schema": 1, "catalogs": [], "x": 1.5e3, "y": -7}')
        self.assertIsNone(registry.config_problem())

    def test_the_problem_says_why_and_is_gone_when_the_file_is_fixed(self):
        file = self.raw(".lab/catalogs.json", b"{")
        self.assertIn("not valid JSON", registry.config_problem())
        self.raw(".lab/catalogs.json", b'{"schema": 2, "catalogs": []}')
        self.assertIn("newer than this Lab reads", registry.config_problem())
        file.write_text('{"schema": 1, "catalogs": []}', encoding="utf-8")
        self.assertIsNone(registry.config_problem())

    def test_a_file_that_is_not_a_file_or_is_too_big_is_not_read(self):
        os.makedirs(self.root / ".lab/catalogs.json")
        self.assertIn("not a regular file", registry.config_problem())
        os.rmdir(self.root / ".lab/catalogs.json")
        self.raw(".lab/catalogs.json", b" " * (registry.FILE_LIMIT + 1))
        self.assertIn("larger than", registry.config_problem())
        self.assertEqual(registry.read_config(), {"schema": 1, "catalogs": []})

    @unittest.skipUnless(hasattr(os, "mkfifo"), "no named pipes on this system")
    def test_a_pipe_in_its_place_does_not_block_the_read(self):
        os.makedirs(self.root / ".lab")
        os.mkfifo(self.root / ".lab/catalogs.json")
        started = time.monotonic()
        self.assertEqual(registry.read_config(), {"schema": 1, "catalogs": []})
        self.assertLess(time.monotonic() - started, 5)
        self.assertIn("not a regular file", registry.config_problem())

    def test_a_link_to_a_file_is_followed_because_it_is_the_person_s(self):
        elsewhere = self.root / "dotfiles" / "catalogs.json"
        elsewhere.parent.mkdir()
        elsewhere.write_text(json.dumps({"schema": 1, "catalogs": [git_entry("acme-hub")]}), encoding="utf-8")
        os.makedirs(self.root / ".lab")
        os.symlink(elsewhere, self.root / ".lab/catalogs.json")
        self.assertEqual([one["id"] for one in registry.entries()], ["acme-hub"])

    def test_a_byte_order_mark_is_not_a_damage(self):
        self.raw(".lab/catalogs.json", b"\xef\xbb\xbf" + json.dumps({"schema": 1, "catalogs": []}).encode())
        self.assertEqual(registry.read_config(), {"schema": 1, "catalogs": []})
        self.assertIsNone(registry.config_problem())

    def test_a_file_without_a_schema_is_read_as_the_first(self):
        self.raw(".lab/catalogs.json", json.dumps({"catalogs": [git_entry("acme-hub")]}).encode())
        self.assertEqual([one["id"] for one in registry.entries()], ["acme-hub"])

    def test_an_entry_that_cannot_be_read_is_left_out_and_the_rest_is_kept(self):
        good = git_entry("acme-hub")
        bad = [5, None, "x", [], {}, {"id": "BAD", "url": "https://example.invalid/x.git"},
               {"id": "x", "url": "https://example.invalid/x.git"}, {"id": "no-address", "kind": "git"},
               {"id": "bad-url", "kind": "git", "url": "has a space\n"}, {"id": "relative", "kind": "folder", "path": "code/x"},
               {"id": "local", "kind": "folder", "path": "/home/me/x"}, git_entry("official"), git_entry("lab"),
               {"id": "acme_hub", "kind": "git", "url": "https://example.invalid/x.git"}]
        self.config(good, *bad, git_entry("acme-hub", url="https://example.invalid/twice.git"))
        self.assertEqual(registry.entries(), [good])
        self.assertIn(f"{len(bad) + 1} of the catalogs", registry.config_problem())

    def test_a_very_long_list_is_cut(self):
        self.config(*[git_entry(f"cat-{n:03d}") for n in range(registry.ENTRIES_LIMIT + 50)])
        self.assertEqual(len(registry.entries()), registry.ENTRIES_LIMIT)
        self.assertIn("50 of the catalogs", registry.config_problem())

    def test_the_fields_of_an_entry_are_the_types_they_must_be(self):
        self.config(git_entry("acme-hub", auto="yes", branch=5, added=7, host="true"),
                    git_entry("other-hub", branch="", host=True, shadows={"url": "x"}),
                    folder_entry("my-catalog", "/home/me/x", host=True, shadows={"url": "https://example.invalid/m.git",
                                                                                 "branch": "main", "extra": 1}))
        first, second, third = registry.entries()
        self.assertEqual(first, {"id": "acme-hub", "kind": "git", "url": "https://example.invalid/acme-hub.git"})
        self.assertEqual(second, {"id": "other-hub", "kind": "git", "url": "https://example.invalid/other-hub.git",
                                  "auto": True, "host": True, "added": "2026-10-16T09:00:00Z"})
        self.assertEqual(third["shadows"], {"url": "https://example.invalid/m.git", "branch": "main"})
        self.assertIs(third["host"], True)

    def test_a_kind_that_is_missing_is_read_from_the_address(self):
        self.config({"id": "acme-hub", "url": "https://example.invalid/a.git"}, {"id": "my-catalog", "path": "/home/me/x"},
                    {"id": "both", "kind": "ftp", "url": "https://example.invalid/b.git"})
        self.assertEqual([(one["id"], one["kind"]) for one in registry.entries()],
                         [("acme-hub", "git"), ("my-catalog", "folder"), ("both", "git")])

    def test_the_keys_of_a_newer_lab_survive_a_round_trip(self):
        self.config(git_entry("acme-hub", color="red", nested={"a": [1, 2]}), ui={"theme": "dark"})
        config = registry.read_config()
        self.assertEqual(config["catalogs"][0]["nested"], {"a": [1, 2]})
        self.assertEqual(config["ui"], {"theme": "dark"})
        registry.write_config(config)
        self.assertEqual(registry.read_config(), config)

    def test_an_address_never_keeps_a_user_name_or_a_password(self):
        self.config(git_entry("one-hub", url="https://user:token@example.invalid/a.git"),
                    git_entry("two-hub", url="https://token@example.invalid/b.git"),
                    git_entry("three-hub", url="ssh://git:secret@example.invalid/c.git"),
                    git_entry("four-hub", url="ssh://git@example.invalid/d.git"),
                    git_entry("five-hub", url="git@example.invalid:team/e.git"),
                    git_entry("six-hub", shadows=None, url="HTTPS://user@example.invalid/f.git"),
                    git_entry("seven-hub", url="https://one9@two9:three9@example.invalid/g.git"),
                    git_entry("eight-hub", url="ssh://git:se9@cret9@example.invalid/h.git"),
                    git_entry("nine-hub", url="https://example.invalid/team@home/i.git"))
        self.assertEqual([one["url"] for one in registry.entries()],
                         ["https://example.invalid/a.git", "https://example.invalid/b.git",
                          "ssh://git@example.invalid/c.git", "ssh://git@example.invalid/d.git",
                          "git@example.invalid:team/e.git", "HTTPS://example.invalid/f.git",
                          "https://example.invalid/g.git", "ssh://git@example.invalid/h.git",
                          "https://example.invalid/team@home/i.git"])
        for word in ("token", "secret", "one9", "two9", "three9", "se9", "cret9"):
            self.assertNotIn(word, json.dumps(registry.read_config()))

    def test_the_array_is_the_order_of_precedence(self):
        self.config(git_entry("zeta"), git_entry("alpha"), folder_entry("middle", "/home/me/m"))
        self.assertEqual([one["id"] for one in registry.entries()], ["zeta", "alpha", "middle"])
        self.assertEqual(registry.precedence(), ["local", "zeta", "alpha", "middle"])

    def test_the_default_catalog_claims_first_wherever_it_stands_in_the_file(self):
        self.default("fixture")
        self.config(git_entry("zeta"), git_entry("fixture"), git_entry("alpha"))
        self.assertEqual(registry.precedence(), ["fixture", "local", "zeta", "alpha"])
        self.config(git_entry("zeta"), git_entry("alpha"))
        self.assertEqual(registry.precedence(), ["local", "zeta", "alpha"])

    def test_a_reserved_word_is_a_name_only_for_a_listed_catalog(self):
        self.config(git_entry("gravitee"), git_entry("official"), git_entry("acme-hub"))
        self.assertEqual([one["id"] for one in registry.entries()], ["acme-hub"])
        self.default("gravitee")
        self.assertEqual([one["id"] for one in registry.entries()], ["gravitee", "acme-hub"])
        self.assertIn("official is a reserved name", registry.config_problem())

    def test_a_fork_may_list_several_and_the_first_owns_the_bare_ids(self):
        self.default("fixture", "second")
        self.config(git_entry("second"), git_entry("acme-hub"), git_entry("fixture"))
        self.assertEqual(registry.precedence(), ["fixture", "local", "second", "acme-hub"])
        rows = registry.inputs()
        self.assertEqual([(ns, rows[ns]["listed"], rows[ns]["host"]) for ns in ("fixture", "second", "acme-hub")],
                         [("fixture", True, "listed"), ("second", True, "listed"), ("acme-hub", False, "no")])
        self.lockfile(**{ns: lock_entry() for ns in ("fixture", "second", "acme-hub")})
        for ns in ("fixture", "second", "acme-hub"):
            self.install(ns, A, ("addon:kafka",))
        self.assertEqual(registry.units("addon"), ["kafka", "@second/kafka", "@acme-hub/kafka"])

    def test_the_allowance_of_a_listed_catalog_is_implied_and_not_read(self):
        self.default("fixture")
        self.config(git_entry("fixture", host=True), git_entry("acme-hub", host=True))
        listed, other = registry.entries()
        self.assertNotIn("host", listed)
        self.assertIs(other["host"], True)

    def test_a_default_file_that_cannot_be_read_lists_nothing_and_reading_still_works(self):
        self.raw("catalogs.default.json", b"{")
        self.config(git_entry("gravitee"), git_entry("acme-hub"))
        self.assertEqual([one["id"] for one in registry.entries()], ["acme-hub"])
        with mock.patch.object(registry, "_defaults", side_effect=RuntimeError("broken")):
            self.assertEqual([one["id"] for one in registry.entries()], ["acme-hub"])


class ConfigWrite(Disk):

    def test_a_config_is_written_whole_with_mode_0600_and_reads_back_as_given(self):
        config = {"schema": 1, "catalogs": [git_entry("acme-hub"), folder_entry("my-catalog", "/home/me/code/mine"),
                                            git_entry("shadowed", branch="dev", host=True)]}
        with mock.patch.object(paths, "write_atomic", wraps=paths.write_atomic) as atomic:
            registry.write_config(config)
        self.assertEqual(atomic.call_args.args[0], self.root / ".lab/catalogs.json")
        self.assertEqual(atomic.call_args.kwargs["mode"], 0o600)
        self.assertEqual(os.stat(self.root / ".lab/catalogs.json").st_mode & 0o777, 0o600)
        self.assertEqual(registry.read_config(), config)
        text = (self.root / ".lab/catalogs.json").read_text(encoding="utf-8")
        self.assertTrue(text.endswith("}\n"))
        self.assertEqual(json.loads(text), config)
        self.assertIsNone(registry.config_problem())

    def test_the_folder_of_the_lab_is_made_when_it_is_not_there(self):
        self.assertFalse((self.root / ".lab").exists())
        registry.write_config({"schema": 1, "catalogs": []})
        self.assertEqual(registry.read_config(), {"schema": 1, "catalogs": []})

    def test_the_schema_is_always_the_one_this_lab_writes(self):
        registry.write_config({"schema": 7, "catalogs": []})
        self.assertEqual(json.loads((self.root / ".lab/catalogs.json").read_text(encoding="utf-8"))["schema"], 1)

    def test_a_listed_catalog_is_not_written_with_an_allowance(self):
        self.default("fixture")
        registry.write_config({"schema": 1, "catalogs": [git_entry("fixture", host=True), git_entry("acme-hub", host=True)]})
        written = json.loads((self.root / ".lab/catalogs.json").read_text(encoding="utf-8"))["catalogs"]
        self.assertEqual([one.get("host") for one in written], [None, True])

    def test_what_would_not_read_back_as_given_is_refused_and_nothing_is_written(self):
        table = {"not a dictionary": [], "no list": {"schema": 1}, "a dictionary": {"catalogs": {}},
                 "an entry that is not one": {"catalogs": ["x"]},
                 "no id": {"catalogs": [{"kind": "git", "url": "https://example.invalid/x.git"}]},
                 "a bad id": {"catalogs": [git_entry("Bad")]}, "a reserved id": {"catalogs": [git_entry("official")]},
                 "the personal area": {"catalogs": [folder_entry("local", "/home/me/x")]},
                 "a repeated id": {"catalogs": [git_entry("acme-hub"), git_entry("acme-hub")]},
                 "a relative folder": {"catalogs": [folder_entry("my-catalog", "code/x")]},
                 "no address": {"catalogs": [{"id": "acme-hub", "kind": "git"}]},
                 "too many": {"catalogs": [git_entry(f"cat-{n:03d}") for n in range(registry.ENTRIES_LIMIT + 1)]},
                 "a number that is not one": {"catalogs": [git_entry("acme-hub", more=float("nan"))]}}
        for label, config in table.items():
            with self.subTest(label), self.assertRaises(ValueError):
                registry.write_config(config)
            self.assertEqual(self.tree(), [])

    def test_an_address_with_a_user_name_is_refused_with_the_line_of_section_11(self):
        for url in ("https://user:token@example.invalid/a.git", "https://token@example.invalid/a.git"):
            with self.subTest(url), self.assertRaises(registry.CatalogError) as raised:
                registry.write_config({"schema": 1, "catalogs": [git_entry("acme-hub", url=url)]})
            self.assertEqual((raised.exception.code, raised.exception.status), ("bad_address_secret", 400))
            self.assertEqual(raised.exception.message,
                             "Remove the user name and token from the address. Git uses the sign-in of this machine.")
        self.assertEqual(self.tree(), [])
        registry.write_config({"schema": 1, "catalogs": [git_entry("acme-hub", url="git@example.invalid:team/a.git")]})

    def test_a_file_that_cannot_be_read_is_set_aside_and_never_overwritten(self):
        self.raw(".lab/catalogs.json", b"{ the person's own edit")
        registry.write_config({"schema": 1, "catalogs": [git_entry("acme-hub")]})
        aside = sorted(p.name for p in (self.root / ".lab").iterdir() if ".bad-" in p.name)
        self.assertEqual(len(aside), 1)
        self.assertRegex(aside[0], r"catalogs\.json\.bad-\d{8}T\d{6}Z")
        self.assertEqual((self.root / ".lab" / aside[0]).read_bytes(), b"{ the person's own edit")
        self.assertEqual([one["id"] for one in registry.entries()], ["acme-hub"])
        self.assertIsNone(registry.config_problem())

    def test_each_file_set_aside_keeps_a_name_of_its_own(self):
        with mock.patch("time.strftime", return_value="20261017T000000Z"):
            for text in (b"one", b"two", b"three"):
                self.raw(".lab/catalogs.json", text)
                registry.write_config({"schema": 1, "catalogs": []})
        kept = {p.name: p.read_bytes() for p in (self.root / ".lab").iterdir() if ".bad-" in p.name}
        self.assertEqual(kept, {"catalogs.json.bad-20261017T000000Z": b"one", "catalogs.json.bad-20261017T000000Z-1": b"two",
                                "catalogs.json.bad-20261017T000000Z-2": b"three"})

    def test_a_file_from_a_newer_lab_and_one_with_a_bad_entry_are_set_aside_too(self):
        self.raw(".lab/catalogs.json", b'{"schema": 2, "catalogs": [{"id": "future"}]}')
        registry.write_config({"schema": 1, "catalogs": []})
        self.config(git_entry("acme-hub"), {"id": "BAD"})
        registry.write_config({"schema": 1, "catalogs": [git_entry("acme-hub")]})
        self.assertEqual(len([p for p in (self.root / ".lab").iterdir() if ".bad-" in p.name]), 2)

    def test_a_file_that_reads_is_replaced_and_not_set_aside(self):
        self.config(git_entry("acme-hub"))
        registry.write_config({"schema": 1, "catalogs": [git_entry("other-hub")]})
        self.assertEqual([p.name for p in (self.root / ".lab").iterdir() if ".bad-" in p.name], [])
        self.assertEqual([one["id"] for one in registry.entries()], ["other-hub"])

    def test_a_file_that_cannot_be_kept_is_not_overwritten(self):
        original = self.raw(".lab/catalogs.json", b"not json")
        with mock.patch("os.link", side_effect=OSError("no")), mock.patch("shutil.copyfile", side_effect=OSError("no")), \
                self.assertRaises(OSError):
            registry.write_config({"schema": 1, "catalogs": []})
        self.assertEqual(original.read_bytes(), b"not json")
        self.assertEqual([p.name for p in (self.root / ".lab").iterdir() if ".bad-" in p.name], [])

    def test_where_links_are_not_allowed_the_file_is_copied(self):
        self.raw(".lab/catalogs.json", b"not json")
        with mock.patch("os.link", side_effect=OSError("no")):
            registry.write_config({"schema": 1, "catalogs": []})
        aside = [p for p in (self.root / ".lab").iterdir() if ".bad-" in p.name]
        self.assertEqual([p.read_bytes() for p in aside], [b"not json"])

    def test_a_write_that_fails_after_the_file_was_kept_leaves_it_where_it_was_and_not_a_first_run(self):
        original = self.raw(".lab/catalogs.json", b"not json")
        with mock.patch.object(paths, "write_atomic", side_effect=OSError("disk full")), self.assertRaises(OSError):
            registry.write_config({"schema": 1, "catalogs": [git_entry("acme-hub")]})
        self.assertEqual(original.read_bytes(), b"not json")
        self.assertIsNotNone(registry.read_config())   # damaged, never missing: a first run would add the default again
        self.assertIn("not valid JSON", registry.config_problem())

    def test_something_that_is_not_a_file_is_moved_aside(self):
        os.makedirs(self.root / ".lab/catalogs.json/inside")
        registry.write_config({"schema": 1, "catalogs": []})
        self.assertTrue((self.root / ".lab/catalogs.json").is_file())
        aside = [p for p in (self.root / ".lab").iterdir() if ".bad-" in p.name]
        self.assertEqual(len(aside), 1)
        self.assertTrue((aside[0] / "inside").is_dir())

    def test_a_write_that_fails_leaves_the_file_as_it_was(self):
        self.config(git_entry("acme-hub"))
        before = (self.root / ".lab/catalogs.json").read_bytes()
        with mock.patch.object(paths, "write_atomic", side_effect=OSError("disk full")), self.assertRaises(OSError):
            registry.write_config({"schema": 1, "catalogs": []})
        self.assertEqual((self.root / ".lab/catalogs.json").read_bytes(), before)

    def test_a_listed_reserved_name_is_written_only_when_the_file_lists_it(self):
        with self.assertRaises(ValueError):
            registry.write_config({"schema": 1, "catalogs": [git_entry("gravitee")]})
        self.default("gravitee")
        registry.write_config({"schema": 1, "catalogs": [git_entry("gravitee")]})
        self.assertEqual([one["id"] for one in registry.entries()], ["gravitee"])


class LockRead(Disk):

    def test_a_missing_lock_is_an_empty_one_and_no_problem(self):
        self.assertEqual(registry.read_lock(), {"schema": 1, "catalogs": {}})
        self.assertIsNone(registry.config_problem())

    def test_every_entry_has_every_field_of_section_2_2(self):
        self.lockfile(**{"acme-hub": {"pin": A}})
        found = registry.lock_of("acme-hub")
        self.assertEqual(list(found), ["pin", "previous", "tip", "roots", "audience", "name", "version", "release", "lab",
                                       "installed", "checked", "error", "pending"])
        self.assertEqual((found["pin"], found["previous"], found["roots"], found["audience"]), (A, None, [], "private"))

    def test_a_catalog_with_no_entry_has_the_blank_one(self):
        blank = registry.lock_of("acme-hub")
        self.assertEqual((blank["pin"], blank["error"], blank["pending"], blank["audience"]), (None, None, None, "private"))
        self.assertEqual(registry.lock_of("acme-hub"), registry.lock_of("never-heard"))

    def test_a_lock_that_cannot_be_read_is_empty_and_the_problem_says_why(self):
        cases = {"empty": b"", "not json": b"{", "a list": b"[]", "newer": b'{"schema": 3, "catalogs": {}}',
                 "catalogs a list": b'{"schema": 1, "catalogs": []}', "not text": b"\xff\xfe"}
        for label, data in cases.items():
            with self.subTest(label):
                self.raw(".lab/catalogs.lock", data)
                self.assertEqual(registry.read_lock(), {"schema": 1, "catalogs": {}})
                self.assertIn("The file that records installed catalogs could not be read", registry.config_problem())

    def test_both_files_bad_say_both(self):
        self.raw(".lab/catalogs.json", b"{")
        self.raw(".lab/catalogs.lock", b"{")
        problem = registry.config_problem()
        self.assertIn("lists your catalogs", problem)
        self.assertIn("records installed catalogs", problem)

    def test_a_field_of_the_wrong_type_reads_as_its_blank(self):
        self.lockfile(**{"acme-hub": {
            "pin": "garbage", "previous": 5, "tip": A.upper(), "roots": [B, "x"], "audience": "everyone", "name": 7,
            "version": "1.0", "release": "v1.0.0", "lab": "1", "installed": "yesterday", "checked": 12, "error": "BAD CODE",
            "pending": {"op": "explode", "to": A}, "via": "ftp", "shadowed": "yes"}})
        found = registry.lock_of("acme-hub")
        self.assertEqual(found, {"pin": None, "previous": None, "tip": None, "roots": [], "audience": "private",
                                 "name": None, "version": None, "release": None, "lab": None, "installed": None,
                                 "checked": None, "error": None, "pending": None})

    def test_a_good_entry_reads_as_it_is(self):
        entry = lock_entry(via="ssh", shadowed=True, pending={"op": "update", "from": A, "to": B})
        self.lockfile(**{"acme-hub": entry})
        self.assertEqual(registry.lock_of("acme-hub"), entry)

    def test_a_date_is_iso_with_a_zone(self):
        for stamp in ("2026-10-16T09:00:30Z", "2026-10-16T09:00:30.123Z", "2026-10-16T09:00:30+00:00", "2026-10-16T11:00:30+02:00"):
            with self.subTest(stamp):
                self.lockfile(**{"acme-hub": lock_entry(installed=stamp, checked=stamp)})
                self.assertEqual(registry.lock_of("acme-hub")["installed"], stamp)
        for stamp in ("2026-10-16", "2026-10-16 09:00:30", "2026-10-16T09:00:30", "now", "2026-10-16T09:00:30Z\n"):
            with self.subTest(stamp):
                self.lockfile(**{"acme-hub": lock_entry(installed=stamp)})
                self.assertIsNone(registry.lock_of("acme-hub")["installed"])

    def test_a_name_in_the_lock_is_cleaned_like_any_text_of_an_author(self):
        self.lockfile(**{"acme-hub": lock_entry(name="Acme\u202e\x00 \n Hub " + "x" * 80)})
        shown = registry.lock_of("acme-hub")["name"]
        self.assertEqual(len(shown), 40)
        self.assertTrue(shown.startswith("Acme Hub xxx"))
        self.assertNotIn("\u202e", shown)

    def test_a_name_is_normalized_the_way_a_person_reads_it(self):
        self.lockfile(**{"acme-hub": lock_entry(name="\uff28\uff55\uff42 \u2460 \ufb01x")})   # full-width Hub, a circled 1, a ligature
        self.assertEqual(registry.lock_of("acme-hub")["name"], "Hub 1 fix")

    def test_at_most_twenty_roots_are_read_and_written(self):
        roots = ["%040x" % n for n in range(21)]
        self.lockfile(**{"acme-hub": lock_entry(roots=roots[:20]), "other-hub": lock_entry(roots=roots)})
        self.assertEqual(registry.lock_of("acme-hub")["roots"], roots[:20])
        self.assertEqual(registry.lock_of("other-hub")["roots"], [])
        with self.assertRaises(ValueError):
            registry.set_lock("other-hub", roots=roots)
        self.assertEqual(registry.set_lock("other-hub", roots=roots[:20])["roots"], roots[:20])

    def test_an_operation_under_way_names_commits(self):
        table = {"from a word": {"op": "add", "from": "x", "to": B}, "from a short id": {"op": "update", "from": A[:39], "to": B},
                 "from a number": {"op": "undo", "from": 5, "to": B}, "to nothing": {"op": "add", "from": None, "to": None},
                 "to a short id": {"op": "add", "from": None, "to": "abc"}, "to an upper case id": {"op": "add", "from": None, "to": B.upper()},
                 "no to": {"op": "add", "from": None}, "an operation of another name": {"op": "remove", "from": None, "to": B},
                 "a list": [], "a word": "add"}
        for label, pending in table.items():
            with self.subTest(label):
                self.lockfile(**{"acme-hub": lock_entry(pending=pending)})
                self.assertIsNone(registry.lock_of("acme-hub")["pending"])
                with self.assertRaises(ValueError):
                    registry.set_lock("acme-hub", pending=pending)
        for pending in ({"op": "add", "from": None, "to": B}, {"op": "update", "from": A, "to": B}, {"op": "undo", "to": B}):
            self.lockfile(**{"acme-hub": lock_entry(pending=pending)})
            self.assertEqual(registry.lock_of("acme-hub")["pending"], {"op": pending["op"], "from": pending.get("from"), "to": B})

    def test_a_record_that_is_not_one_is_left_out_and_said(self):
        self.lockfile(**{"acme-hub": lock_entry(), "Bad": lock_entry(), "other-hub": "x", "third-hub": None})
        self.assertEqual(list(registry.read_lock()["catalogs"]), ["acme-hub"])
        self.assertIn("records installed catalogs", registry.config_problem())

    def test_the_keys_of_a_newer_lab_are_kept(self):
        self.lockfile(**{"acme-hub": lock_entry(update=True, extra={"a": 1})})
        self.assertEqual(registry.lock_of("acme-hub")["extra"], {"a": 1})


class LockWrite(Disk):

    def test_a_lock_is_written_whole_with_mode_0600_in_the_order_of_section_2_2(self):
        registry.write_lock({"schema": 1, "catalogs": {"acme-hub": {"audience": "public", "pin": A, "name": "Hub"}}})
        file = self.root / ".lab/catalogs.lock"
        self.assertEqual(os.stat(file).st_mode & 0o777, 0o600)
        written = json.loads(file.read_text(encoding="utf-8"))
        self.assertEqual(list(written["catalogs"]["acme-hub"]), ["pin", "previous", "tip", "roots", "audience", "name",
                                                                  "version", "release", "lab", "installed", "checked", "error",
                                                                  "pending"])
        self.assertEqual(registry.read_lock(), written)

    def test_a_field_of_the_wrong_type_is_a_programmer_s_error_and_nothing_is_written(self):
        table = {"pin": "abc", "previous": 1, "tip": "G" * 40, "roots": "x", "audience": "everyone", "version": "1", "release": "v1",
                 "lab": True, "installed": "now", "error": "Bad Code", "pending": {"op": "add"}, "via": "ftp",
                 "shadowed": "yes", "name": 3}
        for field, value in table.items():
            with self.subTest(field), self.assertRaises(ValueError):
                registry.write_lock({"schema": 1, "catalogs": {"acme-hub": {field: value}}})
            self.assertFalse((self.root / ".lab/catalogs.lock").exists())
        for bad in ([], {"catalogs": []}, {"catalogs": {"Bad": {}}}, {"catalogs": {"acme-hub": "x"}}):
            with self.subTest(bad), self.assertRaises(ValueError):
                registry.write_lock(bad)

    def test_set_lock_makes_the_entry_and_merges_into_it(self):
        made = registry.set_lock("acme-hub", pin=A, tip=A, audience="public", error=None)
        self.assertEqual((made["pin"], made["audience"]), (A, "public"))
        registry.set_lock("acme-hub", tip=B, error="unreachable", checked="2026-10-16T09:01:00Z")
        registry.set_lock("other-hub", pin=C)
        self.assertEqual(registry.lock_of("acme-hub")["pin"], A)
        self.assertEqual(registry.lock_of("acme-hub")["tip"], B)
        self.assertEqual(registry.lock_of("acme-hub")["error"], "unreachable")
        self.assertEqual(registry.lock_of("other-hub")["pin"], C)
        self.assertEqual(sorted(registry.read_lock()["catalogs"]), ["acme-hub", "other-hub"])

    def test_set_lock_answers_a_copy_and_removes_what_is_set_to_none(self):
        made = registry.set_lock("acme-hub", via="ssh", shadowed=True, pending={"op": "add", "from": None, "to": A})
        self.assertEqual((made["via"], made["shadowed"], made["pending"]["to"]), ("ssh", True, A))
        made["pin"] = "changed"
        again = registry.set_lock("acme-hub", via=None, shadowed=None, pending=None)
        self.assertNotIn("via", again)
        self.assertNotIn("shadowed", again)
        self.assertEqual((again["pin"], again["pending"]), (None, None))
        self.assertNotIn("via", json.loads((self.root / ".lab/catalogs.lock").read_text(encoding="utf-8"))["catalogs"]["acme-hub"])

    def test_set_lock_refuses_a_name_that_is_not_a_namespace_and_a_value_that_is_not_one(self):
        for ns in ("../x", "A", "", None, 5, "x"):
            with self.subTest(ns), self.assertRaises(ValueError):
                registry.set_lock(ns, pin=A)
        with self.assertRaises(ValueError):
            registry.set_lock("acme-hub", pin="abc")
        self.assertFalse((self.root / ".lab/catalogs.lock").exists())

    def test_drop_lock_removes_an_entry_and_is_idempotent(self):
        registry.set_lock("acme-hub", pin=A)
        registry.set_lock("other-hub", pin=B)
        registry.drop_lock("acme-hub")
        registry.drop_lock("acme-hub")
        registry.drop_lock("never-heard")
        self.assertEqual(list(registry.read_lock()["catalogs"]), ["other-hub"])

    def test_dropping_where_there_is_no_lock_writes_nothing(self):
        registry.drop_lock("acme-hub")
        self.assertEqual(self.tree(), [".lab", ".lab/catalogs.flock"] if (self.root / ".lab").exists() else [])

    def test_a_lock_that_cannot_be_read_is_set_aside_before_a_change(self):
        self.raw(".lab/catalogs.lock", b"{ half")
        registry.set_lock("acme-hub", pin=A)
        aside = [p for p in (self.root / ".lab").iterdir() if p.name.startswith("catalogs.lock.bad-")]
        self.assertEqual([p.read_bytes() for p in aside], [b"{ half"])
        self.assertEqual(registry.lock_of("acme-hub")["pin"], A)
        self.assertIsNone(registry.config_problem())

    def test_a_write_takes_the_two_locks_in_their_order(self):
        order, real = [], registry._acquire

        def spy(path, wait):
            order.append(registry.LOCK._is_owned())   # the advisory lock is asked for with LOCK held by this thread
            return real(path, wait)

        with mock.patch.object(registry, "_acquire", spy):
            registry.set_lock("acme-hub", pin=A)
            registry.write_config({"schema": 1, "catalogs": []})
            registry.write_lock({"schema": 1, "catalogs": {}})
            registry.drop_lock("acme-hub")
        self.assertEqual(order, [True, True, True, True])
        self.assertFalse(registry.LOCK._is_owned())

    def test_the_leftovers_of_a_write_that_was_killed_are_swept_when_they_are_old(self):
        lab = self.root / ".lab"
        days = 3 * 86400
        old = [self.old(self.raw(f".lab/{name}", b"{"), days) for name in (".catalogs.json.ab12cd34.tmp",
                                                                          ".catalogs.lock.zz_99aa1.tmp")]
        young = [self.raw(".lab/.catalogs.json.young0_1.tmp", b"{"),
                 self.old(self.raw(".lab/.catalogs.lock.hour0001.tmp", b"{"), 3600)]   # a write may be under way in a Lab
        kept = [self.old(self.raw(f".lab/{name}", b"{"), days) for name in (
            ".catalogs.json.bak", ".other.ab12cd34.tmp", ".catalogs.json.AB12.tmp", "catalogs.json.ab12cd34.tmp",
            ".catalogs.json..tmp", ".catalogs.json.ab12cd34.tmp.old")]
        folder = lab / ".catalogs.lock.dir00001.tmp"
        folder.mkdir()
        victim = self.old(self.raw("victim", b"keep"), days)
        link = lab / ".catalogs.json.link0001.tmp"
        os.symlink(victim, link)
        if os.utime in os.supports_follow_symlinks:
            os.utime(link, (1, 1), follow_symlinks=False)   # an old link is not a file of a killed write either
        registry.set_lock("acme-hub", pin=A)
        self.assertEqual([one.exists() for one in old], [False, False])
        self.assertTrue(all(one.exists() for one in young + kept))
        self.assertTrue(folder.is_dir())
        self.assertTrue(os.path.islink(link))
        self.assertEqual(victim.read_bytes(), b"keep")

    def test_every_writer_sweeps_and_a_reader_never_does(self):
        old = self.old(self.raw(".lab/.catalogs.lock.ab12cd34.tmp", b"{"), 3 * 86400)
        registry.read_lock(), registry.read_config(), registry.inputs(), registry.config_problem()
        self.assertTrue(old.exists())
        registry.write_config({"schema": 1, "catalogs": []})
        self.assertFalse(old.exists())
        old = self.old(self.raw(".lab/.catalogs.json.ab12cd34.tmp", b"{"), 3 * 86400)
        registry.drop_lock("acme-hub")   # nothing to drop: nothing written, nothing swept
        self.assertTrue(old.exists())
        registry.write_lock({"schema": 1, "catalogs": {}})
        self.assertFalse(old.exists())

    def test_a_write_in_a_folder_that_cannot_be_listed_still_succeeds(self):
        with mock.patch("os.scandir", side_effect=PermissionError("no")):
            registry.set_lock("acme-hub", pin=A)
        self.assertEqual(registry.lock_of("acme-hub")["pin"], A)

    def test_threads_never_lose_one_another_s_change(self):
        def work(n):
            for step in range(10):
                registry.set_lock(f"cat-{n:02d}", tip=("%040x" % (n * 100 + step)))
        threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        found = {ns: entry["tip"] for ns, entry in registry.read_lock()["catalogs"].items()}
        self.assertEqual(found, {f"cat-{n:02d}": "%040x" % (n * 100 + 9) for n in range(8)})

    def test_processes_never_lose_one_another_s_change(self):
        script = (
            "import sys\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {str(LAB_UI)!r})\n"
            "from lab_ui import paths\n"
            "paths.ROOT = Path(sys.argv[1])\n"
            "from lab_ui.catalogs import registry\n"
            "n = int(sys.argv[2])\n"
            "for step in range(12):\n"
            "    registry.set_lock('cat-%02d' % n, tip='%040x' % (n * 100 + step))\n")
        running = [subprocess.Popen([sys.executable, "-I", "-c", script, str(self.root), str(n)]) for n in range(4)]
        for process in running:
            self.assertEqual(process.wait(timeout=120), 0)
        found = {ns: entry["tip"] for ns, entry in registry.read_lock()["catalogs"].items()}
        self.assertEqual(found, {f"cat-{n:02d}": "%040x" % (n * 100 + 11) for n in range(4)})
        self.assertIsNone(registry.config_problem())


class Flock(Disk):

    def lock_file(self):
        return self.root / ".lab/catalogs.flock"

    def held_elsewhere(self):
        """Whether another descriptor on the lock file is refused the lock, as another process would be."""
        descriptor = os.open(self.lock_file(), os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True
        finally:
            os.close(descriptor)

    def test_the_lock_is_held_inside_and_released_after(self):
        with registry.flock():
            self.assertTrue(self.held_elsewhere())
            self.assertEqual(os.stat(self.lock_file()).st_mode & 0o777, 0o600)
        self.assertFalse(self.held_elsewhere())

    def test_an_error_inside_releases_it(self):
        with self.assertRaises(RuntimeError), registry.flock():
            raise RuntimeError("boom")
        self.assertFalse(self.held_elsewhere())

    def test_a_thread_that_holds_it_takes_it_again_at_no_cost(self):
        with registry.flock(wait=0):
            with registry.flock(wait=0):
                with registry.flock(wait=0):
                    self.assertTrue(self.held_elsewhere())
            self.assertTrue(self.held_elsewhere())
        self.assertFalse(self.held_elsewhere())

    def test_busy_is_one_public_refusal_that_the_locks_and_the_services_raise_alike(self):
        error = registry.busy()
        self.assertIsInstance(error, registry.CatalogError)
        self.assertEqual((error.code, error.status, error.message, error.detail),
                         ("busy", 409, "Another catalog change is running. Try again in a moment.", {}))
        self.assertIsNot(registry.busy(), error, "a new error each time: a traceback is not shared")
        self.assertFalse(hasattr(registry, "_busy"))

    def test_another_holder_makes_the_change_busy(self):
        gate, release = threading.Event(), threading.Event()

        def hold():
            with registry.flock():
                gate.set()
                release.wait(30)
        thread = threading.Thread(target=hold)
        thread.start()
        self.assertTrue(gate.wait(10))
        try:
            started = time.monotonic()
            with self.assertRaises(registry.CatalogError) as raised, registry.flock(wait=0.2):
                self.fail("the lock was taken while another thread held it")
            self.assertGreaterEqual(time.monotonic() - started, 0.15)
            self.assertEqual((raised.exception.code, raised.exception.status), ("busy", 409))
            self.assertEqual(raised.exception.message, "Another catalog change is running. Try again in a moment.")
            with self.assertRaises(registry.CatalogError), registry.flock(wait=0):
                pass
        finally:
            release.set()
            thread.join(10)
        with registry.flock(wait=0):
            pass

    def test_a_writer_waits_for_the_holder_and_then_goes_on(self):
        gate = threading.Event()

        def hold():
            with registry.flock():
                gate.set()
                time.sleep(0.4)
        thread = threading.Thread(target=hold)
        thread.start()
        self.assertTrue(gate.wait(10))
        registry.set_lock("acme-hub", pin=A)   # waits for the lock, does not fail
        thread.join(10)
        self.assertEqual(registry.lock_of("acme-hub")["pin"], A)

    def test_a_writer_that_waits_too_long_is_busy_and_writes_nothing(self):
        gate, release = threading.Event(), threading.Event()

        def hold():
            with registry.flock():
                gate.set()
                release.wait(30)
        thread = threading.Thread(target=hold)
        thread.start()
        self.assertTrue(gate.wait(10))
        try:
            with mock.patch.object(registry, "FLOCK_WAIT", 0.2), self.assertRaises(registry.CatalogError) as raised:
                registry.set_lock("acme-hub", pin=A)
            self.assertEqual(raised.exception.code, "busy")
            self.assertFalse((self.root / ".lab/catalogs.lock").exists())
        finally:
            release.set()
            thread.join(10)

    def test_without_fcntl_it_is_a_no_op(self):
        with mock.patch.object(registry, "fcntl", None):
            with registry.flock(wait=0):
                with registry.flock(wait=0):
                    pass
            registry.set_lock("acme-hub", pin=A)
        self.assertFalse(self.lock_file().exists())
        self.assertEqual(registry.lock_of("acme-hub")["pin"], A)

    def test_a_lock_file_that_cannot_be_made_leaves_the_lock_of_the_process(self):
        os.makedirs(self.root / ".lab")
        self.lock_file().mkdir()   # a folder in its place: it cannot be opened for writing
        with registry.flock(wait=0):
            registry.set_lock("acme-hub", pin=A)
        self.assertEqual(registry.lock_of("acme-hub")["pin"], A)

    def test_a_link_in_the_place_of_the_lock_file_is_never_written_through(self):
        os.makedirs(self.root / ".lab")
        victim = self.root / "victim"
        os.symlink(victim, self.lock_file())
        with registry.flock(wait=0):
            pass
        self.assertFalse(victim.exists())
        victim.write_text("keep", encoding="utf-8")
        with registry.flock(wait=0):
            pass
        self.assertEqual(victim.read_text(encoding="utf-8"), "keep")

    def test_a_reader_never_waits_for_it(self):
        self.config(git_entry("acme-hub"))

        def read():
            registry.entries(), registry.inputs(), registry.sources(), registry.suggestions(), registry.manifest("acme-hub")
            registry.units_of("acme-hub"), registry.units(), registry.config_problem(), registry.read_lock()
        with registry.flock():
            thread = threading.Thread(target=read)
            thread.start()
            thread.join(10)
            self.assertFalse(thread.is_alive())

    def other_thread(self, work):
        """What `work()` answered in another thread of this process: (result, None) or (None, the exception)."""
        box = []

        def run():
            try:
                box.append((work(), None))
            except BaseException as error:  # noqa: BLE001 - whatever it was, the test looks at it
                box.append((None, error))
        thread = threading.Thread(target=run)
        thread.start()
        thread.join(30)
        self.assertFalse(thread.is_alive())
        return box[0]

    def lock_is_free(self):
        """Whether another thread of this process can take LOCK now (and gives it back)."""
        def take():
            if registry.LOCK.acquire(timeout=0.05):
                registry.LOCK.release()
                return True
            return False
        return self.other_thread(take)

    def test_it_holds_the_lock_of_the_process_first_and_gives_it_back(self):
        self.assertFalse(registry.LOCK._is_owned())
        with registry.flock():
            self.assertTrue(registry.LOCK._is_owned())
            self.assertTrue(self.held_elsewhere())
            self.assertEqual(self.lock_is_free(), (False, None))
            with registry.flock(wait=0):
                self.assertTrue(registry.LOCK._is_owned())
            self.assertTrue(registry.LOCK._is_owned())
        self.assertFalse(registry.LOCK._is_owned())
        self.assertEqual(self.lock_is_free(), (True, None))

    def test_a_thread_that_holds_it_can_write_while_another_writer_waits_for_it(self):
        # What recover() does (it takes the flock and then works with the registry) while a request writes: the two never
        # wait for each other until the end of the wait, where one would fail busy and the other would stall
        waiting, results = threading.Event(), {}
        started = time.monotonic()

        def request():
            waiting.set()
            try:
                registry.set_lock("beta-two", error="timeout")
                results["request"] = ("done", time.monotonic() - started)
            except registry.CatalogError as error:
                results["request"] = (error.code, time.monotonic() - started)
        with mock.patch.object(registry, "FLOCK_WAIT", 8.0):
            thread = threading.Thread(target=request)
            with registry.flock():
                thread.start()
                self.assertTrue(waiting.wait(10))
                time.sleep(0.3)   # the request is now waiting for the lock
                registry.set_lock("alpha-one", error="timeout")
                results["recover"] = time.monotonic() - started
            thread.join(30)
        self.assertLess(results["recover"], 4.0)
        self.assertEqual(results["request"][0], "done")
        self.assertLess(results["request"][1], 4.0)
        self.assertEqual(sorted(registry.read_lock()["catalogs"]), ["alpha-one", "beta-two"])

    def test_the_wait_is_one_wait_for_both_locks(self):
        holder, release = threading.Event(), threading.Event()

        def hold():
            with registry.LOCK:   # a thread that has the lock of the process and not the advisory one
                holder.set()
                release.wait(30)
        thread = threading.Thread(target=hold)
        thread.start()
        self.assertTrue(holder.wait(10))
        try:
            started = time.monotonic()
            with self.assertRaises(registry.CatalogError) as raised, registry.flock(wait=0.3):
                self.fail("taken while another thread held LOCK")
            self.assertEqual(raised.exception.code, "busy")
            self.assertGreaterEqual(time.monotonic() - started, 0.25)
            self.assertLess(time.monotonic() - started, 5)
            self.assertFalse(self.lock_file().exists() and self.held_elsewhere())   # the advisory lock was not taken
            with self.assertRaises(registry.CatalogError), registry.flock(wait=0):
                pass
        finally:
            release.set()
            thread.join(10)

    def test_busy_on_the_advisory_lock_gives_the_lock_of_the_process_back(self):
        os.makedirs(self.root / ".lab")
        descriptor = os.open(self.lock_file(), os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX)   # another process of this machine holds it
        with self.assertRaises(registry.CatalogError) as raised, registry.flock(wait=0.2):
            self.fail("taken while another process held the lock")
        self.assertEqual(raised.exception.code, "busy")
        self.assertFalse(registry.LOCK._is_owned())
        self.assertEqual(self.lock_is_free(), (True, None))
        self.assertEqual(getattr(registry._held, "held", {}), {})

    def test_it_composes_with_the_lock_of_the_process_taken_before_it(self):
        with registry.LOCK, registry.flock(wait=0):
            self.assertTrue(self.held_elsewhere())
            registry.set_lock("acme-hub", pin=A)
        self.assertFalse(self.held_elsewhere())
        self.assertFalse(registry.LOCK._is_owned())


class Places(Disk):

    def test_where_things_are(self):
        self.assertEqual(registry.install_dir("acme-hub"), self.root / ".lab/catalogs/acme-hub")
        self.assertEqual(registry.stage_dir("acme-hub"), self.root / ".lab/catalogs/.stage-acme-hub")
        self.assertEqual(registry.store_dir("acme-hub"), self.root / ".lab/cache/git/acme-hub.git")
        self.assertEqual(registry.new_store_dir("9f3a4c1d2e5b6a70"), self.root / ".lab/cache/git/.new-9f3a4c1d2e5b6a70.git")
        self.assertEqual(registry.stage_dir("new-9f3a4c1d2e5b6a70"), self.root / ".lab/catalogs/.stage-new-9f3a4c1d2e5b6a70")
        self.assertIsInstance(registry.install_dir("acme-hub"), Path)

    def test_they_follow_the_root_at_each_call(self):
        first = registry.install_dir("acme-hub")
        other = Path(tempfile.mkdtemp())
        self.addCleanup(other.rmdir)
        self.patch(paths, "ROOT", other)
        self.assertEqual(registry.install_dir("acme-hub"), other / ".lab/catalogs/acme-hub")
        self.assertNotEqual(first, registry.install_dir("acme-hub"))

    def test_a_name_that_could_lead_out_of_the_catalogs_is_refused(self):
        for bad in ("../x", "a/b", "", ".", "..", "x", "A", "a b", "acme\n", None, 5, "-acme", "acme-", ".stage-x"):
            for function in (registry.install_dir, registry.stage_dir, registry.store_dir):
                with self.subTest(bad=bad, function=function.__name__), self.assertRaises(ValueError):
                    function(bad)
        for bad in ("../x", "", None, "xyz", "9F3A", "a" * 33, "1/2"):
            with self.subTest(ticket=bad), self.assertRaises(ValueError):
                registry.new_store_dir(bad)


class Namespaces(Disk):

    def refused(self, ns, **how):
        with self.assertRaises(registry.CatalogError) as raised:
            registry.check_namespace(ns, **how)
        return raised.exception

    def test_a_namespace_is_accepted(self):
        for ns in ("acme-hub", "ab", "a1", "a-b", "x" * 24, "partner-bank", "a0-0b"):
            with self.subTest(ns):
                self.assertIsNone(registry.check_namespace(ns))

    def test_a_malformed_namespace_is_catalog_invalid_with_the_line_of_section_11(self):
        for ns in ("", "a", "A", "Acme", "acme_hub", "-acme", "acme-", "1acme", "a" * 25, "acme/hub", "acme hub",
                   "acme\n", "@acme", "acme.hub", "acmé", None, 5, ["acme"]):
            with self.subTest(ns=ns):
                error = self.refused(ns)
                self.assertEqual((error.code, error.status), ("catalog_invalid", 400))
                self.assertEqual(error.message,
                                 "catalog.yml: id must be 2 to 24 lowercase letters, digits and dashes.")

    def test_every_reserved_word_is_refused(self):
        for ns in sorted(paths.RESERVED_NS):
            with self.subTest(ns):
                error = self.refused(ns)
                self.assertEqual((error.code, error.status), ("name_reserved", 400))
                self.assertEqual(error.message, f"{ns} is a reserved name. Ask the owner of this catalog to choose another.")

    def test_a_reserved_word_is_accepted_for_a_listed_id_only(self):
        self.assertIsNone(registry.check_namespace("gravitee", listed=["gravitee"]))
        self.assertIsNone(registry.check_namespace("gravitee", listed=iter(["x", "gravitee"])))
        self.assertEqual(self.refused("official", listed=["gravitee"]).code, "name_reserved")
        self.assertEqual(self.refused("gravitee", listed=[]).code, "name_reserved")
        self.assertEqual(self.refused("gravitee", listed=["gravitee-x"]).code, "name_reserved")

    def test_the_personal_area_is_never_a_catalog_whatever_is_listed(self):
        self.assertEqual(self.refused("local", listed=["local"]).code, "name_reserved")

    def test_a_namespace_that_is_taken_says_by_which_address(self):
        self.config(git_entry("acme-hub", url="https://github.com/acme/lab-hub.git"), folder_entry("my-catalog", "/x/y"))
        error = self.refused("acme-hub", taken=["acme-hub"])
        self.assertEqual((error.code, error.status), ("name_taken", 409))
        self.assertEqual(error.message, "Another catalog already uses the name acme-hub (github.com/acme/lab-hub). "
                                        "Remove it first, or ask the owner of this one to rename it.")
        self.assertEqual(self.refused("my-catalog", taken=iter(["my-catalog"])).message,
                         "Another catalog already uses the name my-catalog (/x/y). "
                         "Remove it first, or ask the owner of this one to rename it.")
        self.assertEqual(self.refused("ghost-hub", taken=("ghost-hub",)).message,
                         "Another catalog already uses the name ghost-hub. "
                         "Remove it first, or ask the owner of this one to rename it.")
        self.assertIsNone(registry.check_namespace("other-hub", taken=["acme-hub"]))

    def test_the_checks_come_in_order(self):
        self.assertEqual(self.refused("Gravitee", taken=["Gravitee"]).code, "catalog_invalid")
        self.assertEqual(self.refused("gravitee", taken=["gravitee"]).code, "name_reserved")
        self.assertEqual(self.refused("gravitee", taken=["gravitee"], listed=["gravitee"]).code, "name_taken")


class Manifests(Disk):

    def lock(self, **fields):
        self.lockfile(**{"acme-hub": lock_entry(**fields)})

    def test_it_is_valid_for_the_pin(self):
        self.install("acme-hub", A, ("addon:one", "playground:two"))
        self.lock()
        found = registry.manifest("acme-hub")
        self.assertEqual(found["commit"], A)
        self.assertEqual(sorted(found["units"]), ["addon:one", "playground:two"])

    def test_the_manifest_is_a_copy(self):
        self.install("acme-hub", A)
        self.lock()
        registry.manifest("acme-hub")["units"].clear()
        self.assertEqual(list(registry.manifest("acme-hub")["units"]), ["addon:one"])

    def test_a_commit_that_is_not_the_pin_is_a_damaged_catalog_not_an_error(self):
        self.install("acme-hub", B)
        self.lock()
        self.assertIsNone(registry.manifest("acme-hub"))
        self.lock(pin=None)
        self.assertIsNone(registry.manifest("acme-hub"))
        self.lockfile()
        self.assertIsNone(registry.manifest("acme-hub"))

    def test_while_an_operation_names_the_commit_the_manifest_is_valid_for_it(self):
        # An Add between the rename and the lock write: no pin yet. An Update between the manifest and the pin: the old one
        self.install("acme-hub", B)
        self.lock(pin=None, pending={"op": "add", "from": None, "to": B})
        self.assertEqual(registry.manifest("acme-hub")["commit"], B)
        self.lock(pin=A, pending={"op": "update", "from": A, "to": B})
        self.assertEqual(registry.manifest("acme-hub")["commit"], B)
        self.lock(pin=A, pending={"op": "update", "from": A, "to": C})
        self.assertIsNone(registry.manifest("acme-hub"))

    def test_it_must_name_this_catalog_and_have_the_shape_of_section_2_3(self):
        base = {"schema": 1, "catalog": "acme-hub", "commit": A, "files": {}, "units": {}, "ignored": 0}
        table = {"another catalog": {"catalog": "other-hub"}, "newer schema": {"schema": 2}, "schema text": {"schema": "1"},
                 "short commit": {"commit": "abc"}, "commit not text": {"commit": 5}, "no files": {"files": None},
                 "units a list": {"units": []}, "ignored negative": {"ignored": -1}, "ignored text": {"ignored": "3"}}
        self.lock()
        for label, change in table.items():
            with self.subTest(label):
                self.json(".lab/catalogs/acme-hub/.installed.json", {**base, **change})
                self.assertIsNone(registry.manifest("acme-hub"))
        self.json(".lab/catalogs/acme-hub/.installed.json", base)
        self.assertIsNotNone(registry.manifest("acme-hub"))
        for data in (b"", b"[]", b"{", b"\xff", b"null", b"[" * 100000):
            with self.subTest(data=data[:5]):
                self.raw(".lab/catalogs/acme-hub/.installed.json", data)
                self.assertIsNone(registry.manifest("acme-hub"))

    def test_it_is_not_read_through_a_link_or_from_something_that_is_not_a_file(self):
        self.lock()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.json("elsewhere/.installed.json", {"schema": 1, "catalog": "acme-hub", "commit": A, "files": {}, "units": {},
                                                "ignored": 0})
        os.makedirs(self.root / ".lab/catalogs")
        os.symlink(elsewhere, self.root / ".lab/catalogs/acme-hub")   # a git install is a real directory
        self.assertIsNone(registry.manifest("acme-hub"))
        os.remove(self.root / ".lab/catalogs/acme-hub")
        os.makedirs(self.root / ".lab/catalogs/acme-hub")
        os.symlink(elsewhere / ".installed.json", self.root / ".lab/catalogs/acme-hub/.installed.json")
        self.assertIsNone(registry.manifest("acme-hub"))
        os.remove(self.root / ".lab/catalogs/acme-hub/.installed.json")
        os.makedirs(self.root / ".lab/catalogs/acme-hub/.installed.json")
        self.assertIsNone(registry.manifest("acme-hub"))

    def test_the_manifest_of_whatever_commit_can_be_read_for_the_code_that_finishes_an_operation(self):
        self.install("acme-hub", B)
        self.lock()
        self.assertIsNone(registry.manifest("acme-hub"))
        self.assertEqual(registry.read_manifest("acme-hub")["commit"], B)
        registry.read_manifest("acme-hub")["units"].clear()
        self.assertEqual(list(registry.read_manifest("acme-hub")["units"]), ["addon:one"])
        os.remove(self.root / ".lab/catalogs/acme-hub/.installed.json")
        self.assertIsNone(registry.read_manifest("acme-hub"))
        for ns in ("../x", None, "A"):
            self.assertIsNone(registry.read_manifest(ns))

    def test_a_catalog_that_is_not_installed_or_not_a_name_has_none(self):
        self.lock()
        self.assertIsNone(registry.manifest("acme-hub"))
        for ns in ("../x", "", None, 5, "A", "acme\n"):
            with self.subTest(ns):
                self.assertIsNone(registry.manifest(ns))

    def test_a_manifest_is_read_once_until_the_file_changes(self):
        self.lock()
        self.install("acme-hub", A, ("addon:one",))
        file = self.old(self.root / ".lab/catalogs/acme-hub/.installed.json")
        self.assertEqual(list(registry.manifest("acme-hub")["units"]), ["addon:one"])
        real = registry._read

        def not_the_manifest(path, *more, **how):
            if path.endswith(".installed.json"):
                raise AssertionError("the manifest was read again")
            return real(path, *more, **how)
        with mock.patch.object(registry, "_read", side_effect=not_the_manifest):
            self.assertEqual(list(registry.manifest("acme-hub")["units"]), ["addon:one"])
            self.assertEqual(list(registry.manifest("acme-hub")["units"]), ["addon:one"])
        self.assertTrue(file.exists())

    def test_a_manifest_changed_within_the_tick_of_the_clock_is_read_again(self):
        # The same size, the same inode, the same modification time: only the age of the file tells it from the cached one
        self.lock()
        self.install("acme-hub", A, ("addon:one",))
        file = self.root / ".lab/catalogs/acme-hub/.installed.json"
        stamp = os.stat(file).st_mtime_ns
        self.assertEqual(list(registry.manifest("acme-hub")["units"]), ["addon:one"])
        document = json.loads(file.read_text(encoding="utf-8"))
        document["units"] = {"addon:two": "sha256:" + "0" * 64}
        file.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        os.utime(file, ns=(stamp, stamp))
        self.assertEqual(list(registry.manifest("acme-hub")["units"]), ["addon:two"])

    def test_a_manifest_replaced_by_a_file_of_the_same_size_and_time_is_read_again(self):
        # An atomic replace leaves another inode: size and modification time alone cannot tell the two files apart
        self.lock()
        self.install("acme-hub", A, ("addon:one",))
        file = self.root / ".lab/catalogs/acme-hub/.installed.json"
        self.old(file)
        stamp = os.stat(file).st_mtime_ns
        self.assertEqual(list(registry.manifest("acme-hub")["units"]), ["addon:one"])
        self.assertEqual(list(registry.manifest("acme-hub")["units"]), ["addon:one"])   # from the cache now
        document = json.loads(file.read_text(encoding="utf-8"))
        document["units"] = {"addon:two": "sha256:" + "0" * 64}
        other = file.with_name("replacement")
        other.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        self.assertEqual(os.stat(other).st_size, os.stat(file).st_size)
        os.utime(other, ns=(stamp, stamp))
        before = os.stat(file).st_ino
        os.replace(other, file)
        self.assertNotEqual(os.stat(file).st_ino, before)
        self.assertEqual((os.stat(file).st_size, os.stat(file).st_mtime_ns), (os.stat(file).st_size, stamp))
        self.assertEqual(list(registry.manifest("acme-hub")["units"]), ["addon:two"])

    def test_an_old_file_that_changes_in_size_is_read_again(self):
        self.lock()
        self.install("acme-hub", A, ("addon:one",))
        file = self.old(self.root / ".lab/catalogs/acme-hub/.installed.json")
        registry.manifest("acme-hub")
        self.install("acme-hub", A, ("addon:one", "setup:longer-name"))
        self.old(file)
        self.assertEqual(sorted(registry.manifest("acme-hub")["units"]), ["addon:one", "setup:longer-name"])

    def test_a_manifest_that_is_gone_is_gone(self):
        self.lock()
        self.install("acme-hub", A)
        self.old(self.root / ".lab/catalogs/acme-hub/.installed.json")
        self.assertIsNotNone(registry.manifest("acme-hub"))
        os.remove(self.root / ".lab/catalogs/acme-hub/.installed.json")
        self.assertIsNone(registry.manifest("acme-hub"))


class Units(Disk):

    def test_a_git_install_lists_what_its_manifest_vouches_for_and_nothing_else(self):
        self.config(git_entry("acme-hub"))
        self.lockfile(**{"acme-hub": lock_entry()})
        base = self.install("acme-hub", A, ("addon:listed", "addon:also", "setup:set", "playground:bank"))
        for stray in ("addons/rogue/compose.yml", "setups/unlisted.yml", "playgrounds/ghost/compose.yml"):
            (base / stray).parent.mkdir(parents=True, exist_ok=True)
            (base / stray).write_text("", encoding="utf-8")
        self.assertEqual(registry.units_of("acme-hub"), {"addon": ["also", "listed"], "setup": ["set"],
                                                         "playground": ["bank"]})

    def test_the_three_kinds_are_always_there(self):
        self.assertEqual(registry.units_of("acme-hub"), {"addon": [], "setup": [], "playground": []})
        self.assertEqual(registry.units_of("local"), {"addon": [], "setup": [], "playground": []})

    def test_what_a_manifest_names_that_is_not_a_unit_is_ignored(self):
        self.config(git_entry("acme-hub"))
        self.lockfile(**{"acme-hub": lock_entry()})
        self.install("acme-hub", A, ("addon:one", "playground:ok"))
        document = {"schema": 1, "catalog": "acme-hub", "commit": A, "files": {}, "ignored": 0,
                    "units": {key: "sha256:" + "0" * 64 for key in (
                        "addon:one", "@catalog", "addon:", "addon:UPPER", "bogus:name", "addon:a:b", "addon:../x", "plain",
                        "playground:ok", "", "setup:")}}
        self.json(".lab/catalogs/acme-hub/.installed.json", document, 0o644)
        self.assertEqual(registry.units_of("acme-hub"), {"addon": ["one"], "setup": [], "playground": ["ok"]})

    def test_a_git_catalog_with_no_valid_manifest_vouches_for_nothing(self):
        self.config(git_entry("acme-hub"))
        self.install("acme-hub", A)
        self.assertEqual(registry.units_of("acme-hub")["addon"], [])   # no lock: no pin
        self.lockfile(**{"acme-hub": lock_entry(pin=B, tip=B)})
        self.assertEqual(registry.units_of("acme-hub")["addon"], [])   # a manifest for another commit
        self.lockfile(**{"acme-hub": lock_entry()})
        self.assertEqual(registry.units_of("acme-hub")["addon"], ["one"])
        os.remove(self.root / ".lab/catalogs/acme-hub/.installed.json")
        self.assertEqual(registry.units_of("acme-hub")["addon"], [])

    def test_a_catalog_the_person_does_not_have_vouches_for_nothing(self):
        self.lockfile(**{"acme-hub": lock_entry()})
        self.install("acme-hub", A)
        self.assertEqual(registry.units_of("acme-hub")["addon"], [])
        self.config(git_entry("other-hub"))
        self.assertEqual(registry.units_of("acme-hub")["addon"], [])

    def test_a_folder_catalog_and_the_personal_area_are_listed_by_listing(self):
        self.config(folder_entry("my-catalog", self.root / ".lab/catalogs/my-catalog"))
        for file in ("addons/b/compose.yml", "addons/a/compose.yml", "addons/.hidden/compose.yml", "addons/Bad_Name/compose.yml",
                     "addons/no-file/notes.md", "setups/set-one.yml", "setups/.hidden.yml", "setups/Bad.yml",
                     "playgrounds/play/compose.yml", "playgrounds/play/guide.md", "playgrounds/lonely/guide.md"):
            self.write(f".lab/catalogs/my-catalog/{file}")
            self.write(f".lab/catalogs/local/{file}")
        expected = {"addon": ["a", "b"], "setup": ["set-one"], "playground": ["play"]}
        self.assertEqual(registry.units_of("my-catalog"), expected)
        self.assertEqual(registry.units_of("local"), expected)

    def test_a_folder_catalog_may_be_a_link_and_a_dead_link_has_nothing(self):
        author = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(author, True))
        (author / "addons/linked").mkdir(parents=True)
        (author / "addons/linked/compose.yml").write_text("", encoding="utf-8")
        os.makedirs(self.root / ".lab/catalogs")
        os.symlink(author, self.root / ".lab/catalogs/my-catalog")
        self.config(folder_entry("my-catalog", author))
        self.assertEqual(registry.units_of("my-catalog")["addon"], ["linked"])
        shutil.rmtree(author)
        self.assertEqual(registry.units_of("my-catalog")["addon"], [])

    def test_a_folder_entry_is_listed_whatever_a_manifest_in_it_says(self):
        self.config(folder_entry("my-catalog", self.root / ".lab/catalogs/my-catalog"))
        self.install("my-catalog", A, ("addon:vouched",))
        self.write(".lab/catalogs/my-catalog/addons/other/compose.yml")
        self.assertEqual(registry.units_of("my-catalog")["addon"], ["other", "vouched"])

    def test_a_name_that_is_not_a_namespace_has_nothing(self):
        for ns in ("../x", "A", "", None, 5, "x", "a/b"):
            with self.subTest(ns):
                self.assertEqual(registry.units_of(ns), {"addon": [], "setup": [], "playground": []})

    def test_the_ids_of_every_installed_catalog_by_kind_and_precedence(self):
        self.default("fixture")
        self.config(git_entry("zeta"), git_entry("fixture"), folder_entry("my-catalog", self.root / ".lab/catalogs/my-catalog"))
        self.lockfile(zeta=lock_entry(), fixture=lock_entry())
        self.install("fixture", A, ("addon:kafka", "setup:events", "playground:bank"))
        self.install("zeta", A, ("addon:last", "playground:bank"))
        self.write(".lab/catalogs/my-catalog/addons/mine/compose.yml")
        self.write(".lab/catalogs/local/addons/personal/compose.yml")
        self.assertEqual(registry.units("addon"), ["kafka", "@zeta/last", "@my-catalog/mine"])
        self.assertEqual(registry.units("setup"), ["events"])
        self.assertEqual(registry.units("playground"), ["bank", "@zeta/bank"])
        self.assertEqual(registry.units(), ["kafka", "@zeta/last", "@my-catalog/mine", "events", "bank", "@zeta/bank"])
        self.assertEqual(registry.units(None), registry.units())

    def test_no_catalog_has_no_unit_and_a_kind_that_does_not_exist_is_an_error(self):
        self.assertEqual(registry.units(), [])
        self.assertEqual(registry.units("addon"), [])
        with self.assertRaises(ValueError):
            registry.units("catalog")

    def test_paths_lists_only_what_the_registry_vouches_for(self):
        # The contract with paths.ids: a git install is read through units_of, so a file the manifest does not name is no unit
        self.default("fixture")
        self.config(git_entry("fixture"), git_entry("acme-hub"))
        self.lockfile(fixture=lock_entry(), **{"acme-hub": lock_entry()})
        self.install("fixture", A, ("addon:kafka",))
        self.install("acme-hub", A, ("addon:listed", "playground:bank"))
        self.write(".lab/catalogs/fixture/addons/stray/compose.yml")
        self.write(".lab/catalogs/acme-hub/addons/rogue/compose.yml")
        self.write(".lab/catalogs/local/addons/mine/compose.yml")
        self.assertEqual(paths.ids("addon"), ["kafka", "local/mine", "@acme-hub/listed"])
        self.assertEqual(paths.ids("playground"), ["@acme-hub/bank"])
        self.assertEqual(paths.files(), [".lab/catalogs/fixture/addons/kafka/compose.yml",
                                         ".lab/catalogs/local/addons/mine/compose.yml",
                                         ".lab/catalogs/acme-hub/addons/listed/compose.yml",
                                         ".lab/catalogs/acme-hub/playgrounds/bank/compose.yml"])
        self.lockfile(fixture=lock_entry(pin=B), **{"acme-hub": lock_entry()})   # the manifest is not the pin's: Damaged
        self.assertEqual(paths.ids("addon"), ["local/mine", "@acme-hub/listed"])


    def test_a_git_catalog_is_known_by_its_entry(self):
        self.config(git_entry("acme-hub"), folder_entry("my-catalog", self.root / ".lab/catalogs/my-catalog"))
        self.assertIs(registry.is_git("acme-hub"), True)
        for ns in ("my-catalog", "local", "never-heard", "../x", "A", "", None, 5):
            with self.subTest(ns):
                self.assertIs(registry.is_git(ns), False)
        self.raw(".lab/catalogs.json", b"{")   # a list that cannot be read has no catalog
        self.assertIs(registry.is_git("acme-hub"), False)

    def test_a_git_install_with_no_manifest_vouches_for_nothing_whatever_its_folder_holds(self):
        # Deleted, damaged or never written: Damaged, and a unit file a container or a person left there is no unit
        self.default("fixture")
        self.config(git_entry("acme-hub"))
        self.lockfile(**{"acme-hub": lock_entry()})
        self.write(".lab/catalogs/acme-hub/addons/planted/compose.yml")
        self.write(".lab/catalogs/acme-hub/playgrounds/planted/compose.yml")
        self.assertTrue(registry.is_git("acme-hub"))
        self.assertEqual(registry.units_of("acme-hub"), {"addon": [], "setup": [], "playground": []})
        self.assertEqual((registry.units(), registry.inputs()["acme-hub"]["state"]), ([], "damaged"))
        self.assertEqual([one["state"] for one in registry.sources() if one["id"] == "acme-hub"], ["damaged"])
        # The same through paths, which lists a catalog by its folder unless it asks the registry: it must ask for a git
        # entry whether or not the manifest file is there
        self.assertEqual((paths.ids("addon"), paths.ids("playground"), paths.files()), ([], [], []))
        self.install("acme-hub", A, ("addon:real",))
        self.assertEqual((paths.ids("addon"), paths.ids("playground")), (["@acme-hub/real"], []))


class Addresses(Disk):

    def home(self):
        home = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(home, True))
        patcher = mock.patch.dict(os.environ, {"HOME": str(home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        return home

    def test_every_spelling_of_one_repository_reads_the_same(self):
        for text in ("https://github.com/acme/lab-hub.git", "https://github.com/acme/lab-hub", "https://github.com/acme/lab-hub/",
                     "https://user@github.com/acme/lab-hub.git", "https://user:token@GitHub.com/acme/lab-hub.git/",
                     "git@github.com:acme/lab-hub.git", "git@github.com:acme/lab-hub", "ssh://git@github.com/acme/lab-hub.git",
                     "acme/lab-hub", "  https://github.com/acme/lab-hub.git  ", "HTTPS://GITHUB.COM/acme/lab-hub.GIT"):
            with self.subTest(text):
                self.assertEqual(registry.display_address(text), "github.com/acme/lab-hub")

    def test_the_port_stays_and_the_scheme_and_user_go(self):
        self.assertEqual(registry.display_address("ssh://git@host.example:2222/a/b.git"), "host.example:2222/a/b")
        self.assertEqual(registry.display_address("http://localhost:8080/x/y.git"), "localhost:8080/x/y")
        self.assertEqual(registry.display_address("https://git.example.invalid/group/sub/repo.git"),
                         "git.example.invalid/group/sub/repo")
        self.assertEqual(registry.display_address("file:///tmp/remote/hub.git"), "/tmp/remote/hub")

    def test_a_folder_under_the_home_folder_is_shown_with_a_tilde(self):
        home = self.home()
        self.assertEqual(registry.display_address(f"{home}/code/mine"), "~/code/mine")
        self.assertEqual(registry.display_address(f"{home}/code/mine/"), "~/code/mine")
        self.assertEqual(registry.display_address(str(home)), "~")
        self.assertEqual(registry.display_address("~/code/mine"), "~/code/mine")
        self.assertEqual(registry.display_address("/srv/elsewhere"), "/srv/elsewhere")
        self.assertEqual(registry.display_address(f"{home}-other/x"), f"{home}-other/x")

    def test_what_is_not_an_address_is_cleaned_and_never_raises(self):
        for value in (None, 5, [], {}, b"x", "", "   ", "\n"):
            with self.subTest(value=value):
                self.assertEqual(registry.display_address(value), "")
        self.assertEqual(registry.display_address("not an address"), "not an address")
        shown = registry.display_address("https://github.com/a\x00\u202e/b\x1b[31m.git")
        self.assertFalse(any(ord(c) < 32 or c == "\u202e" for c in shown))
        self.assertLessEqual(len(registry.display_address("x " * 5000)), 200)

    def test_no_credential_is_ever_shown(self):
        for text in ("https://user:secret@github.com/a/b.git", "ssh://git:secret@github.com/a/b.git",
                     "see https://user:secret@github.com/a/b", "weird://user:secret@host/x", "ftp://secret@host/x",
                     "https://secret@[::1]:99/x"):
            with self.subTest(text):
                self.assertNotIn("secret", registry.display_address(text))

    def test_a_repository_that_is_online_has_a_page(self):
        for url in ("https://github.com/acme/lab-hub.git", "https://github.com/acme/lab-hub", "https://github.com/acme/lab-hub/",
                    "https://user:token@github.com/acme/lab-hub.git", "HTTPS://GitHub.com/acme/lab-hub.GIT"):
            with self.subTest(url):
                self.assertEqual(registry.web_url(git_entry("acme-hub", url=url)), "https://github.com/acme/lab-hub")
        self.assertEqual(registry.web_url({"kind": "git", "url": "https://git.example.invalid:8443/group/sub/repo.git"}),
                         "https://git.example.invalid:8443/group/sub/repo")

    def test_everything_else_has_none(self):
        for url in ("git@github.com:acme/lab-hub.git", "ssh://git@github.com/acme/lab-hub.git", "http://localhost/a/b.git",
                    "file:///tmp/x.git", "acme/lab-hub", "", None, 5, "https://github.com", "https://github.com/",
                    "https://github.com/a/../b", "https://github.com/a//b", "https://github.com/a/b?x=1", "https://github.com/a/b#x",
                    "https://github.com/a b/c", "https://github.com/a/b\n", "javascript:alert(1)", "https://", "https:///a/b"):
            with self.subTest(url=url):
                self.assertIsNone(registry.web_url({"id": "acme-hub", "kind": "git", "url": url}))
        self.assertIsNone(registry.web_url(folder_entry("my-catalog", "/x/y")))
        self.assertIsNone(registry.web_url({"kind": "folder", "url": "https://github.com/a/b"}))
        for value in (None, "x", 5, [], {}):
            self.assertIsNone(registry.web_url(value))

    def test_a_listed_catalog_has_the_page_of_the_listed_address_whatever_it_stored(self):
        self.default("fixture")
        entry = git_entry("fixture", url="https://evil.invalid/else.git")
        self.assertEqual(registry.web_url(entry), "https://example.invalid/fixture")
        self.assertEqual(registry.web_url(git_entry("other-hub", url="https://evil.invalid/else.git")),
                         "https://evil.invalid/else")


class Suggestions(Disk):

    def setUp(self):
        super().setUp()
        self.elsewhere = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.elsewhere, True))
        self.file = self.elsewhere / "suggestions.json"
        patcher = mock.patch.dict(os.environ, {"LAB_CATALOGS_FILE": str(self.file), "HOME": str(self.elsewhere)})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("XDG_CONFIG_HOME",):
            os.environ.pop(name, None)

    def suggest(self, *rows, schema=1):
        self.file.write_text(json.dumps({"schema": schema, "catalogs": list(rows)}), encoding="utf-8")

    def test_the_file_is_where_section_4_6_says(self):
        self.assertEqual(registry.suggestions_file(), str(self.file))
        with mock.patch.dict(os.environ, {"LAB_CATALOGS_FILE": "", "XDG_CONFIG_HOME": "/xdg"}):
            self.assertEqual(registry.suggestions_file(), "/xdg/gravitee-lab/catalogs.json")
        with mock.patch.dict(os.environ, {"LAB_CATALOGS_FILE": "", "XDG_CONFIG_HOME": "relative/xdg"}):
            self.assertEqual(registry.suggestions_file(), f"{self.elsewhere}/.config/gravitee-lab/catalogs.json")
        with mock.patch.dict(os.environ, {"LAB_CATALOGS_FILE": ""}):
            os.environ.pop("XDG_CONFIG_HOME", None)
            self.assertEqual(registry.suggestions_file(), f"{self.elsewhere}/.config/gravitee-lab/catalogs.json")
        with mock.patch.dict(os.environ, {"LAB_CATALOGS_FILE": "~/mine.json"}):
            self.assertEqual(registry.suggestions_file(), f"{self.elsewhere}/mine.json")

    def test_a_row_of_the_file_is_a_not_added_row(self):
        self.suggest({"name": "Example internal", "description": "Demos kept private",
                      "url": "git@git.example.invalid:team/lab-internal.git", "branch": "main", "host_access": True})
        self.assertEqual(registry.suggestions(), [{
            "id": None, "kind": "suggested", "name": "Example internal", "description": "Demos kept private",
            "url": "git@git.example.invalid:team/lab-internal.git", "branch": "main", "host_access": True, "listed": False}])

    def test_the_optional_fields_are_optional_and_a_hint_is_never_more_than_a_hint(self):
        self.suggest({"url": "https://git.example.invalid/team/lab.git"},
                     {"url": "https://git.example.invalid/team/two.git", "host_access": "yes", "name": "  Two\n"})
        first, second = registry.suggestions()
        self.assertEqual((first["name"], first["description"], first["branch"], first["host_access"]),
                         ("git.example.invalid/team/lab", "", None, False))
        self.assertEqual((second["name"], second["host_access"]), ("Two", False))

    def test_a_row_that_is_not_an_address_the_lab_could_add_is_left_out(self):
        self.suggest(5, None, "x", [], {}, {"name": "No address"}, {"url": 5}, {"url": ""},
                     {"url": "git://git.example.invalid/a/b"}, {"url": "http://evil.example.invalid/a/b"},
                     {"url": "ext::sh -c touch% /tmp/pwned"}, {"url": "-oProxyCommand=x"}, {"url": "file:///tmp/x"},
                     {"url": "https://user:token@git.example.invalid/a/b.git"}, {"url": "/tmp"},
                     {"url": "https://git.example.invalid/a/b", "branch": "bad..branch"},
                     {"url": "https://git.example.invalid/keep/this.git"})
        self.assertEqual([row["url"] for row in registry.suggestions()], ["https://git.example.invalid/keep/this.git"])

    def test_a_file_of_another_format_gives_nothing(self):
        row = {"url": "https://git.example.invalid/team/one.git"}
        for label, document in {"a newer schema": {"schema": 2, "catalogs": [row]}, "no schema": {"catalogs": [row]},
                                "schema text": {"schema": "1", "catalogs": [row]}, "schema true": {"schema": True, "catalogs": [row]}}.items():
            with self.subTest(label):
                self.file.write_text(json.dumps(document), encoding="utf-8")
                self.assertEqual(registry.suggestions(), [])
        self.suggest(row)
        self.assertEqual(len(registry.suggestions()), 1)

    def test_a_folder_is_not_a_suggestion_whatever_it_is_spelled_like(self):
        # A path that is a folder here is a folder catalog, not a repository to add: only an address is suggested
        folder = self.elsewhere / "a-folder"   # under the home folder (HOME is this temporary folder) and a catalog
        folder.mkdir()
        (folder / "catalog.yml").write_text("x-gravitee-catalog:\n  id: a-folder\n", encoding="utf-8")
        self.assertEqual(gitx.parse_address(str(folder)).kind, "folder")
        self.suggest({"url": str(folder)}, {"url": str(folder) + "/"}, {"url": "https://git.example.invalid/team/one.git"})
        self.assertEqual([row["url"] for row in registry.suggestions()], ["https://git.example.invalid/team/one.git"])

    def test_the_text_of_a_row_is_an_author_s_and_is_cleaned(self):
        self.suggest({"url": "https://git.example.invalid/a/b.git", "name": "X\u202e" + "y" * 100, "description": "d" * 200})
        row = registry.suggestions()[0]
        self.assertEqual((len(row["name"]), len(row["description"])), (40, 90))
        self.assertNotIn("\u202e", row["name"])

    def test_a_file_that_is_missing_or_damaged_or_hostile_gives_nothing(self):
        self.assertEqual(registry.suggestions(), [])
        for label, data in {"not json": b"{", "a list": b"[]", "newer": json.dumps({"schema": 2, "catalogs": []}).encode(),
                            "no list": b'{"schema": 1}', "not text": b"\xff\xfe", "empty": b"", "nesting": b"[" * 100000,
                            "huge": b" " * (registry.SUGGESTIONS_LIMIT + 1)}.items():
            with self.subTest(label):
                self.file.write_bytes(data)
                self.assertEqual(registry.suggestions(), [])
        self.file.unlink()
        self.file.mkdir()
        self.assertEqual(registry.suggestions(), [])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "no named pipes on this system")
    def test_a_pipe_in_its_place_does_not_block(self):
        os.mkfifo(self.file)
        started = time.monotonic()
        self.assertEqual(registry.suggestions(), [])
        self.assertLess(time.monotonic() - started, 5)

    def test_there_are_at_most_fifty(self):
        self.suggest(*[{"url": f"https://git.example.invalid/team/repo-{n}.git"} for n in range(80)])
        self.assertEqual(len(registry.suggestions()), registry.SUGGESTIONS_MAX)

    def test_a_catalog_that_is_added_is_not_suggested_in_any_spelling(self):
        self.suggest({"url": "https://git.example.invalid/team/one.git"}, {"url": "git@git.example.invalid:team/two.git"},
                     {"url": "https://git.example.invalid/team/three.git"})
        self.config(git_entry("one-hub", url="git@git.example.invalid:team/one.git"),
                    git_entry("two-hub", url="https://git.example.invalid/team/two"))
        self.assertEqual([row["url"] for row in registry.suggestions()], ["https://git.example.invalid/team/three.git"])

    def test_one_repository_in_two_rows_is_one(self):
        self.suggest({"url": "https://git.example.invalid/team/one.git", "name": "First"},
                     {"url": "git@git.example.invalid:team/one.git", "name": "Second"})
        self.assertEqual([row["name"] for row in registry.suggestions()], ["First"])

    def test_the_listed_catalogs_the_person_does_not_have_come_first(self):
        self.default("fixture")
        self.suggest({"url": "https://git.example.invalid/team/one.git"})
        rows = registry.suggestions()
        self.assertEqual([(row["id"], row["listed"]) for row in rows], [("fixture", True), (None, False)])
        self.assertEqual(rows[0]["url"], "https://example.invalid/fixture.git")
        self.assertEqual((rows[0]["name"], rows[0]["host_access"]), ("Base demos", False))
        self.config(git_entry("fixture"))
        self.assertEqual([row["id"] for row in registry.suggestions()], [None])
        os.remove(self.root / ".lab/catalogs.json")
        self.assertEqual([row["id"] for row in registry.suggestions()], ["fixture", None])

    def test_a_listed_catalog_the_file_also_suggests_is_one_row(self):
        self.default("fixture")
        self.suggest({"url": "https://example.invalid/fixture.git", "host_access": True})
        self.assertEqual([(row["id"], row["host_access"]) for row in registry.suggestions()], [("fixture", False)])

    def test_the_default_module_failing_still_leaves_the_file(self):
        self.suggest({"url": "https://git.example.invalid/team/one.git"})
        with mock.patch.object(registry, "_defaults", side_effect=RuntimeError("broken")):
            self.assertEqual([row["url"] for row in registry.suggestions()], ["https://git.example.invalid/team/one.git"])

    def test_the_file_is_only_read(self):
        self.suggest({"url": "https://git.example.invalid/team/one.git"})
        self.file.chmod(0o400)
        self.elsewhere.chmod(0o500)
        self.addCleanup(self.elsewhere.chmod, 0o700)
        before = (self.file.read_bytes(), os.stat(self.file).st_mtime_ns, sorted(os.listdir(self.elsewhere)))
        with mock.patch.object(paths, "write_atomic", side_effect=AssertionError("written")), \
                mock.patch("os.rename", side_effect=AssertionError("renamed")), \
                mock.patch("os.replace", side_effect=AssertionError("replaced")), \
                mock.patch("os.remove", side_effect=AssertionError("removed")), \
                mock.patch("os.unlink", side_effect=AssertionError("removed")):
            self.assertEqual(len(registry.suggestions()), 1)
        self.assertEqual((self.file.read_bytes(), os.stat(self.file).st_mtime_ns, sorted(os.listdir(self.elsewhere))), before)
        self.assertNotIn(self.file.name, [p.name for p in self.root.rglob("*")])


class Scanner(Disk):
    """What a catalog.yml says of the catalog, read for display and dependencies (not the strict reading of Add)."""

    def scan(self, text):
        return registry._scan(text.encode("utf-8") if isinstance(text, str) else text)

    def test_host_access_is_read_only_when_it_is_true(self):
        block = "x-gravitee-catalog:\n  schema: 1\n  id: acme-hub\n  name: Acme Hub\n  description: d\n  version: 1.0.0\n  lab: 1\n"
        self.assertTrue(self.scan(block + "  host_access: true\n")["host_access"])
        for text in ("  host_access: false\n", "  host_access: yes\n", ""):
            self.assertNotIn("host_access", self.scan(block + text), text)
        self.assertNotIn("partial", self.scan(block + "  host_access: true\n"))

    def test_the_block_of_section_4_1(self):
        found = self.scan("""x-gravitee-catalog:
  schema: 1                     # format
  id: acme-hub                  # the namespace
  name: Acme Hub
  description: Banking demos for partner workshops
  version: 1.4.0
  lab: 1
  license: Apache-2.0
  images: ghcr.io/acme/lab-hub
  shared_settings: [GEMINI_API_KEY]
  canary:
    - playground:bank
  requires:
    catalogs:
      base-hub:
        name: Base demos
        url: https://github.com/base/lab-base.git
        min: 1.0.0
      partner-bank:
        name: Partner Bank
        url: https://github.com/partner/lab-bank.git
        min: 2.0.0
""")
        self.assertEqual(found, {"id": "acme-hub", "name": "Acme Hub", "description": "Banking demos for partner workshops",
                                 "version": "1.4.0", "lab": 1,
                                 "requires_catalogs": {
                                     "base-hub": {"name": "Base demos", "min": "1.0.0",
                                                  "url": "https://github.com/base/lab-base.git"},
                                     "partner-bank": {"name": "Partner Bank", "min": "2.0.0",
                                                      "url": "https://github.com/partner/lab-bank.git"}}})

    def test_the_flow_form_of_the_fixtures_and_quotes(self):
        found = self.scan("""x-gravitee-catalog:
  schema: 1
  id: "hub-one"
  name: 'It''s a hub'
  description: "Say \\"hi\\" # not a comment"
  version: "3.2.1"
  lab: 4
  requires:
    catalogs:
      partner: {name: Partner fixture, url: "https://example.invalid/partner.git", min: 1.0.0}
      other: {name: 'A, B', url: https://example.invalid/o.git}
""")
        self.assertEqual(found["id"], "hub-one")
        self.assertEqual(found["name"], "It's a hub")
        self.assertEqual(found["description"], 'Say "hi" # not a comment')
        self.assertEqual((found["version"], found["lab"]), ("3.2.1", 4))
        self.assertEqual(found["requires_catalogs"], {
            "partner": {"name": "Partner fixture", "url": "https://example.invalid/partner.git", "min": "1.0.0"},
            "other": {"name": "A, B", "url": "https://example.invalid/o.git", "min": None}})

    def test_the_real_fixtures(self):
        for ns in ("fixture", "partner", "lab-new", "lab-old", "hostile"):
            with self.subTest(ns):
                found = self.scan((fixtures.CATALOGS / ns / "catalog.yml").read_bytes())
                meta = fixtures.catalog_meta(ns)
                self.assertEqual((found["id"], found["name"], found["version"], found["lab"]),
                                 (ns, meta["name"], meta["version"], meta["lab"]))
                self.assertEqual(sorted(found["requires_catalogs"]), fixtures._requires(ns))
                self.assertNotIn("partial", found)   # every shape the fixtures use is one it reads

    def test_what_is_wrong_is_left_out_and_the_rest_is_kept(self):
        found = self.scan("""x-gravitee-catalog:
  id: Bad_Id
  name: "  \\u202eHub\\n  "
  description: 7
  version: 1.0
  lab: one
  requires:
    catalogs:
      Bad_Ns: {name: X, url: "https://example.invalid/x.git", min: 1.0.0}
      good: {name: Good, url: "https://user:secret@example.invalid/g.git", min: 1.x}
      list-form: [a, b]
""")
        self.assertEqual(found, {"name": "Hub", "description": "7",
                                 "requires_catalogs": {"good": {"name": "Good", "url": "https://example.invalid/g.git",
                                                                "min": None}}})

    def test_the_block_must_be_there_and_alone_at_the_top(self):
        for text in ("", "name: x\n", "services: {}\nx-gravitee-catalog-other:\n  name: x\n", "x-gravitee-catalog:\n",
                     "x-gravitee-catalog:\nname: x\n", "  x-gravitee-catalog:\n    name: x\n"):
            with self.subTest(text=text):
                self.assertEqual(self.scan(text), {})
        found = self.scan("x-other:\n  name: not-this\nx-gravitee-catalog:  # the block\n  name: This\nx-after:\n  name: nor-this\n")
        self.assertEqual(found["name"], "This")
        self.assertEqual(self.scan("x-gravitee-catalog:\n  name: First\nx-gravitee-catalog:\n  name: Second\n")["name"], "First")

    def test_text_it_cannot_read_gives_nothing(self):
        self.assertEqual(self.scan(b"\xff\xfe\x00"), {})
        self.assertEqual(self.scan("x-gravitee-catalog:\n\tname: tab\n"), {})
        self.assertEqual(self.scan("x-gravitee-catalog:\n  \tname: tab\n"), {})

    def test_crlf_and_a_byte_order_mark(self):
        found = self.scan(b"\xef\xbb\xbfx-gravitee-catalog:\r\n  name: Windows\r\n  version: 1.0.0\r\n")
        self.assertEqual((found["name"], found["version"]), ("Windows", "1.0.0"))

    def test_a_list_in_block_form_and_a_scalar_on_several_lines_are_skipped(self):
        found = self.scan("x-gravitee-catalog:\n  shared_settings:\n    - A\n    - B\n  description: >\n    folded\n    text\n"
                          "  name: After\n  note: |\n    kept\n  version: 1.0.0\n")
        self.assertEqual((found["name"], found["version"]), ("After", "1.0.0"))
        self.assertNotIn("description", found)

    def test_hostile_text_is_bounded(self):
        deep = "x-gravitee-catalog:\n" + "".join(" " * (2 + n) + "a:\n" for n in range(3000)) + " " * 3002 + "name: deep\n"
        for label, text in {"deep": deep, "long line": "x-gravitee-catalog:\n  name: " + "a" * 2_000_000 + "\n",
                            "many lines": "x-gravitee-catalog:\n" + "  k: v\n" * 200000,
                            "brackets": "x-gravitee-catalog:\n  name: " + "[" * 100000 + "\n",
                            "braces": "x-gravitee-catalog:\n  requires: {" + "{a: " * 50000 + "\n",
                            "anchors": "x-gravitee-catalog:\n  a: &x\n    b: *x\n  name: *x\n",
                            "quotes": "x-gravitee-catalog:\n  name: \"" + "\\" * 100001 + "\n"}.items():
            with self.subTest(label):
                self.assertIsInstance(self.scan(text), dict)

    def test_the_names_the_first_modules_import_are_the_public_ones(self):
        self.assertIs(registry._scan, registry.scan)
        self.assertIs(registry._shown, registry.shown)
        self.assertEqual(registry.shown("  Ｈｕｂ\u202e\n  one  ", 40), "Hub one")

    def test_the_block_on_one_line_quoted_keys_and_flows_inside_flows(self):
        found = self.scan("x-gravitee-catalog: {schema: 1, id: acme-hub, name: Acme Hub, version: 1.4.0, lab: 2}\n")
        self.assertEqual(found, {"id": "acme-hub", "name": "Acme Hub", "version": "1.4.0", "lab": 2, "requires_catalogs": {}})
        found = self.scan("services: {}\n\"x-gravitee-catalog\":\n  schema: 1\n  'id': acme-hub\n  \"lab\": 2\n  \"name\": Q\n")
        self.assertEqual((found["id"], found["lab"], found["name"]), ("acme-hub", 2, "Q"))
        found = self.scan("x-gravitee-catalog:\n  id: acme-hub\n  requires: {catalogs: {gravitee: {name: G, "
                          "url: 'https://example.invalid/y.git', min: 1.0.0}, other: {name: O}}}\n")
        self.assertEqual(found["requires_catalogs"], {
            "gravitee": {"name": "G", "url": "https://example.invalid/y.git", "min": "1.0.0"},
            "other": {"name": "O", "url": None, "min": None}})
        self.assertNotIn("partial", found)
        self.assertEqual(registry._flow("{a: [1, {b: c}], d: {e: {f: g}}}"), {"a": ["1", {"b": "c"}], "d": {"e": {"f": "g"}}})
        deep = "{a: " * 30 + "x" + "}" * 30
        self.assertIsInstance(registry._flow(deep), dict)   # nested flows go so deep and then are text

    def test_it_says_when_it_could_not_read_what_the_lab_reads(self):
        head = "x-gravitee-catalog:\n  id: acme-hub\n"
        cannot = {
            "an anchor on a key it reads": head + "  name: &n Acme\n", "an alias": head + "  name: *n\n",
            "a tag": head + "  lab: !!int 2\n", "a folded description": head + "  description: >\n    folded\n    text\n",
            "a literal name": head + "  name: |\n    literal\n", "a name on two lines": head + "  name: Acme\n    Hub\n",
            "a hexadecimal level": head + "  lab: 0x2\n", "a signed level": head + "  lab: +2\n",
            "a level with a point": head + "  lab: 2.0\n", "a level with a mark": head + "  lab: 1_0\n",
            "a merge key": head + "  <<: *base\n", "a line that is no key": head + "  junk\n  name: A\n",
            "a complex key": head + "  ? complex\n  name: A\n",
            "a list where a mapping goes": head + "  requires:\n    - catalogs\n",
            "a list level with the key": head + "  requires:\n  - catalogs\n",
            "a flow that does not close": head + "  requires: {catalogs: {a: {name: x}}\n",
            "a flow with a part that is no pair": head + "  requires: {catalogs, a: b}\n",
            "a block that is lower than its first line": "x-gravitee-catalog:\n    id: acme-hub\n  name: Low\n",
            "an anchor on the block": "x-gravitee-catalog: &c\n  id: acme-hub\n",
            "an alias for the block": "x-gravitee-catalog: *c\n",
            "a flow on several lines": "x-gravitee-catalog: {\n  id: acme-hub\n}\n",
            "a flow with a part that is no pair, on the key line": "x-gravitee-catalog: {id: acme-hub, junk}\n",
            "a block after the lines it reads": "services: {}\n" * 4100 + head}
        for label, text in cannot.items():
            with self.subTest(label):
                self.assertIs(self.scan(text).get("partial"), True)
        can = {
            "the block of catalog.yml": "x-gravitee-catalog:\n  schema: 1\n  id: acme-hub\n  name: Acme Hub\n  version: 1.4.0\n  lab: 1\n"
                                        "  license: Apache-2.0\n  canary:\n    - playground:bank\n  moved_from:\n  - addon:x\n"
                                        "  shared_settings: [A, B]\n  requires:\n    catalogs:\n      other:\n        name: O\n"
                                        "        url: https://example.invalid/o.git\n        min: 1.0.0\n",
            "a key it does not read, however it is written": head + "  images: &i ghcr.io/acme\n  note: >\n    folded\n"
                                                                    "  extra:\n    - a\n    - b\n  other: {a: b\n",
            "values that are readable and wrong": "x-gravitee-catalog:\n  id: Bad_Id\n  name: ''\n  version: 1.0\n  lab: one\n",
            "a level in decimal, quoted": head + "  lab: \"2\"\n", "comments": head + "  # a comment\n  name: A # another\n",
            "no description": head, "no block": "services: {}\n", "nothing": ""}
        for label, text in can.items():
            with self.subTest(label):
                self.assertNotIn("partial", self.scan(text))

    def test_a_requirement_is_the_address_of_a_git_repository_or_nothing(self):
        table = {"https://example.invalid/p.git": "https://example.invalid/p.git",
                 "https://user:tok@example.invalid/g.git": "https://example.invalid/g.git",
                 "https://us er:tok@example.invalid/g.git": "https://example.invalid/g.git",
                 "git@example.invalid:team/e.git": "git@example.invalid:team/e.git",
                 "acme/hub": "https://github.com/acme/hub.git",
                 "javascript:alert(document.cookie)": None, "file:///etc/passwd": None, "/tmp": None, "/etc/passwd": None,
                 "http://example.invalid/x.git": None, "git://example.invalid/x.git": None, "ext::sh -c touch% /tmp/x": None,
                 "-oProxyCommand=x": None, "https://example.invalid/a b.git": None, "https://example.invalid/": None, "": None}
        for url, expected in table.items():
            with self.subTest(url):
                found = self.scan(f'x-gravitee-catalog:\n  id: acme-hub\n  requires: {{catalogs: {{partner: {{name: P, url: "{url}", '
                                  'min: 1.0.0}}}\n')
                self.assertEqual(found["requires_catalogs"]["partner"]["url"], expected)
                self.assertNotIn("partial", found)
        found = self.scan("x-gravitee-catalog:\n  requires:\n    catalogs:\n      partner:\n        name: P\n        url: 7\n")
        self.assertIsNone(found["requires_catalogs"]["partner"]["url"])
        self.assertIsNone(registry._git_url(None))
        self.assertIsNone(registry._git_url("https://example.invalid/a\ud800.git"))

    def test_an_install_is_read_for_its_summary_only_through_a_regular_file(self):
        self.config(folder_entry("my-catalog", self.root / ".lab/catalogs/my-catalog"))
        self.write(".lab/catalogs/my-catalog/catalog.yml", "x-gravitee-catalog:\n  name: Mine\n")
        self.assertEqual(registry._summary("my-catalog")["name"], "Mine")
        os.remove(self.root / ".lab/catalogs/my-catalog/catalog.yml")
        os.symlink(self.root / "elsewhere", self.root / ".lab/catalogs/my-catalog/catalog.yml")
        (self.root / "elsewhere").write_text("x-gravitee-catalog:\n  name: Linked\n", encoding="utf-8")
        self.assertEqual(registry._summary("my-catalog"), {})
        os.remove(self.root / ".lab/catalogs/my-catalog/catalog.yml")
        self.raw(".lab/catalogs/my-catalog/catalog.yml", b"x" * (registry.SUMMARY_LIMIT + 1))
        self.assertEqual(registry._summary("my-catalog"), {})


class Rows(Disk):
    """sources() and inputs(): what the resolver reads, from the disk."""

    def test_with_nothing_there_there_is_the_personal_area(self):
        self.assertEqual(registry.sources(), [{"id": "local", "kind": "machine", "path": ".lab/catalogs/local",
                                               "state": "machine"}])
        found = registry.inputs()
        self.assertEqual(list(found), ["local"])
        self.assertEqual(found["local"], {
            "name": "Personal", "description": "", "version": None, "lab": None, "audience": "private", "kind": "machine",
            "requires_catalogs": {}, "state": "machine", "units": {"addon": [], "setup": [], "playground": []},
            "ignored": 0, "error": None, "pending": None, "listed": False, "host": "no"})

    def set_up_three(self):
        self.default("fixture")
        self.config(git_entry("acme-hub"), git_entry("fixture"), folder_entry("my-catalog", self.root / ".lab/catalogs/my-catalog"))
        self.lockfile(**{"acme-hub": lock_entry(name="Acme Hub", version="1.4.0", lab=2, audience="private"),
                         "fixture": lock_entry(name="Fixture catalog", audience="public")})
        self.install("fixture", A, ("addon:kafka", "setup:events", "playground:bank"), ignored=3)
        self.install("acme-hub", A, ("addon:mock-bank",))
        self.write(".lab/catalogs/my-catalog/addons/mine/compose.yml")
        self.write(".lab/catalogs/my-catalog/catalog.yml",
                   "x-gravitee-catalog:\n  name: Mine\n  description: My own\n  version: 0.1.0\n  lab: 1\n")
        self.write(".lab/catalogs/local/addons/personal/compose.yml")

    def test_the_order_is_the_default_then_the_personal_area_then_the_others_as_added(self):
        self.set_up_three()
        self.assertEqual([row["id"] for row in registry.sources()], ["fixture", "local", "acme-hub", "my-catalog"])
        self.assertEqual(list(registry.inputs()), ["fixture", "local", "acme-hub", "my-catalog"])
        self.assertEqual(registry.sources()[2], {"id": "acme-hub", "kind": "git", "path": ".lab/catalogs/acme-hub",
                                                 "state": "uptodate"})

    def test_a_row_is_exactly_the_keys_of_section_6_2_with_no_pin(self):
        self.set_up_three()
        for ns, row in registry.inputs().items():
            with self.subTest(ns):
                self.assertEqual(set(row), ROW_KEYS)
                self.assertNotIn("pin", row)
                self.assertNotIn("pin", json.dumps(row))
        json.dumps(registry.inputs())
        json.dumps(registry.sources())

    def test_what_a_row_says(self):
        self.set_up_three()
        rows = registry.inputs()
        self.assertEqual(rows["fixture"], {
            "name": "Fixture catalog", "description": "", "version": "1.0.0", "lab": 1, "audience": "public", "kind": "git",
            "requires_catalogs": {}, "state": "uptodate", "units": {"addon": ["kafka"], "setup": ["events"],
                                                                    "playground": ["bank"]},
            "ignored": 3, "error": None, "pending": None, "listed": True, "host": "listed"})
        self.assertEqual((rows["acme-hub"]["name"], rows["acme-hub"]["version"], rows["acme-hub"]["lab"],
                          rows["acme-hub"]["audience"], rows["acme-hub"]["listed"], rows["acme-hub"]["host"]),
                         ("Acme Hub", "1.4.0", 2, "private", False, "no"))
        self.assertEqual(rows["my-catalog"]["units"]["addon"], ["mine"])
        self.assertEqual(rows["local"]["units"]["addon"], ["personal"])

    def test_a_git_install_is_named_by_the_lock_and_a_folder_by_its_live_file(self):
        self.set_up_three()
        rows = registry.inputs()
        self.assertEqual((rows["fixture"]["name"], rows["acme-hub"]["name"]), ("Fixture catalog", "Acme Hub"))   # the lock
        self.assertEqual((rows["my-catalog"]["name"], rows["my-catalog"]["description"], rows["my-catalog"]["version"],
                          rows["my-catalog"]["lab"]), ("Mine", "My own", "0.1.0", 1))
        self.lockfile(**{"my-catalog": lock_entry(name="Stale", version="9.9.9", lab=9, audience="public")})
        self.assertEqual(registry.inputs()["my-catalog"]["name"], "Mine")   # a folder is read live, the lock is a shadow's cache
        self.assertEqual(registry.inputs()["my-catalog"]["audience"], "public")   # and its audience is the lock's, when it has one

    def test_a_git_install_whose_lock_has_no_name_is_named_by_its_catalog_yml(self):
        self.config(git_entry("acme-hub"))
        self.lockfile(**{"acme-hub": lock_entry(name=None, version=None, lab=None)})
        self.install("acme-hub", A, catalog_yml="x-gravitee-catalog:\n  name: From file\n  description: Say\n  version: 3.0.0\n  lab: 3\n")
        row = registry.inputs()["acme-hub"]
        self.assertEqual((row["name"], row["description"], row["version"], row["lab"]), ("From file", "Say", "3.0.0", 3))

    def test_the_audience_is_the_locks_and_private_for_any_doubt(self):
        self.config(git_entry("one-hub"), git_entry("two-hub"), folder_entry("my-catalog", self.root / "x"))
        self.lockfile(**{"one-hub": lock_entry(audience="public"), "two-hub": lock_entry(audience="whatever")})
        rows = registry.inputs()
        self.assertEqual([rows[ns]["audience"] for ns in ("one-hub", "two-hub", "my-catalog", "local")],
                         ["public", "private", "private", "private"])

    def test_the_catalogs_a_catalog_needs_come_from_its_catalog_yml(self):
        self.config(git_entry("acme-hub"), git_entry("other-hub"))
        self.lockfile(**{"acme-hub": lock_entry(), "other-hub": lock_entry(pin=B, tip=B)})
        needs = "x-gravitee-catalog:\n  name: X\n  requires:\n    catalogs:\n      partner: {name: Partner, url: \"https://example.invalid/p.git\", min: 2.0.0}\n"
        self.install("acme-hub", A, catalog_yml=needs)
        self.install("other-hub", A, catalog_yml=needs)   # a manifest that is not the pin's vouches for nothing
        rows = registry.inputs()
        self.assertEqual(rows["acme-hub"]["requires_catalogs"],
                         {"partner": {"name": "Partner", "url": "https://example.invalid/p.git", "min": "2.0.0"}})
        self.assertEqual(rows["other-hub"]["requires_catalogs"], {})
        self.assertEqual(rows["local"]["requires_catalogs"], {})

    def test_a_row_is_a_copy(self):
        self.set_up_three()
        registry.inputs()["fixture"]["units"]["addon"].append("x")
        self.assertEqual(registry.inputs()["fixture"]["units"]["addon"], ["kafka"])

    def test_the_allowance_is_listed_allowed_or_no(self):
        self.default("fixture")
        self.config(git_entry("fixture"), git_entry("one-hub", host=True), git_entry("two-hub"),
                    folder_entry("my-catalog", self.root / "x", host=True))
        rows = registry.inputs()
        self.assertEqual([(rows[ns]["listed"], rows[ns]["host"]) for ns in ("fixture", "one-hub", "two-hub", "my-catalog", "local")],
                         [(True, "listed"), (False, "allowed"), (False, "no"), (False, "allowed"), (False, "no")])

    def test_a_folder_that_stands_in_for_a_listed_catalog_is_listed(self):
        self.default("fixture")
        self.config(folder_entry("fixture", self.root / ".lab/catalogs/fixture", shadows={"url": "https://example.invalid/fixture.git"}))
        self.write(".lab/catalogs/fixture/addons/kafka/compose.yml")
        row = registry.inputs()["fixture"]
        self.assertEqual((row["kind"], row["state"], row["listed"], row["host"]), ("folder", "linked", True, "listed"))

    def test_a_listed_catalog_not_yet_downloaded_has_the_name_of_the_file_and_no_version(self):
        self.default("fixture")
        self.config(git_entry("fixture"))
        row = registry.inputs()["fixture"]
        self.assertEqual((row["name"], row["description"], row["version"], row["lab"], row["state"]),
                         ("Base demos", "Playgrounds, setups and add-ons", None, None, "downloading"))
        self.assertEqual(row["units"], {"addon": [], "setup": [], "playground": []})

    def test_a_name_that_is_not_known_is_the_namespace(self):
        self.config(git_entry("acme-hub"))
        self.assertEqual(registry.inputs()["acme-hub"]["name"], "acme-hub")

    def test_the_default_module_failing_is_a_catalog_that_is_listed_by_its_namespace_only(self):
        self.default("fixture")
        self.config(git_entry("fixture"), git_entry("gravitee"))
        with mock.patch.object(registry, "_defaults", side_effect=RuntimeError("broken")):
            rows = registry.inputs()
        self.assertEqual((rows["fixture"]["listed"], rows["fixture"]["name"]), (True, "fixture"))
        self.assertNotIn("gravitee", rows)


class States(Disk):
    """The state key of a catalog, as far as the disk says it."""

    def state(self, ns="acme-hub"):
        rows, sources = registry.inputs(), {row["id"]: row["state"] for row in registry.sources()}
        self.assertEqual(rows[ns]["state"], sources[ns])
        return rows[ns]["state"]

    def git(self, lock=None, installed=True, entry=None, **fields):
        self.config(entry or git_entry("acme-hub"))
        if lock is not False:
            self.lockfile(**{"acme-hub": lock_entry(**fields)})
        if installed:
            self.install("acme-hub", A)

    def test_the_personal_area(self):
        self.assertEqual(self.state("local"), "machine")

    def test_a_catalog_never_downloaded_is_downloading_whatever_is_in_the_way(self):
        self.git(lock=False, installed=False)
        self.assertEqual(self.state(), "downloading")
        self.lockfile(**{"acme-hub": {"pin": None}})
        self.assertEqual(self.state(), "downloading")
        self.assertIsNone(registry.inputs()["acme-hub"]["version"])

    def test_an_installed_catalog_is_up_to_date_as_far_as_the_disk_goes(self):
        self.git()
        self.assertEqual(self.state(), "uptodate")
        self.assertEqual(registry.inputs()["acme-hub"]["version"], "1.0.0")

    def test_a_pin_without_a_valid_manifest_is_damaged(self):
        self.git(installed=False)
        self.assertEqual(self.state(), "damaged")
        self.install("acme-hub", B)
        self.assertEqual(self.state(), "damaged")
        self.install("acme-hub", A)
        self.assertEqual(self.state(), "uptodate")
        self.assertEqual(registry.inputs()["acme-hub"]["version"], "1.0.0")   # a damaged install can still be named

    def test_a_copy_that_is_damaged_is_damaged_before_it_is_anything_a_failure_says(self):
        self.git(installed=False, error="unreachable")
        self.assertEqual(self.state(), "damaged")
        self.git(installed=True, error="unreachable")
        self.assertEqual(self.state(), "unreachable")

    def test_an_operation_under_way(self):
        self.git(pin=None, installed=True, pending={"op": "add", "from": None, "to": A})
        self.assertEqual(self.state(), "downloading")
        self.lockfile(**{"acme-hub": lock_entry(pending={"op": "update", "from": B, "to": A}, pin=B)})
        self.assertEqual(self.state(), "updating")
        self.lockfile(**{"acme-hub": lock_entry(pending={"op": "undo", "from": A, "to": B})})
        self.assertEqual(self.state(), "updating")
        row = registry.inputs()["acme-hub"]
        self.assertEqual(row["pending"], {"op": "undo", "from": A, "to": B})

    def test_the_last_failure_names_the_state(self):
        table = {"git_missing": "needs_git", "git_old": "needs_git", "docker_missing": "needs_docker", "docker_unanswered": "needs_docker", "no_access": "noaccess", "not_found": "noaccess",
                 "ssh_denied": "noaccess", "host_key": "noaccess", "unreachable": "unreachable", "timeout": "unreachable",
                 "certificate": "unreachable", "wrong_catalog": "wrong_catalog", "history_changed": "rewritten",
                 "lab_new": "lab_new", "no_readable_version": "lab_new", "lab_old": "lab_old", "damaged": "damaged",
                 "too_large": "failed", "fsck": "failed", "something_else": "failed"}
        for code, state in table.items():
            with self.subTest(code):
                self.git(error=code)
                self.assertEqual(self.state(), state)
                self.assertEqual(registry.inputs()["acme-hub"]["error"], code)
        self.assertEqual(set(registry.FAILURES.values()) - set(table.values()), set())

    def test_a_failure_before_any_copy_is_the_failure_and_a_copy_does_not_hide_it(self):
        self.git(pin=None, installed=False, error="no_access")
        self.assertEqual((self.state(), registry.inputs()["acme-hub"]["version"]), ("noaccess", None))
        self.git(error="unreachable")
        self.assertEqual((self.state(), registry.inputs()["acme-hub"]["version"]), ("unreachable", "1.0.0"))

    def test_the_failures_are_the_states_the_resolver_calls_failed(self):
        self.assertTrue(set(registry.FAILURES.values()) - {"damaged"} <= set(resolve.FAILED))

    def test_a_folder_is_linked_or_its_folder_is_not_found(self):
        author = self.root / "author"
        self.config(folder_entry("my-catalog", author))
        self.assertEqual(self.state("my-catalog"), "missing_folder")
        os.makedirs(self.root / ".lab/catalogs")
        os.symlink(author, self.root / ".lab/catalogs/my-catalog")
        self.assertEqual(self.state("my-catalog"), "missing_folder")   # a link to nothing
        author.mkdir()
        self.assertEqual(self.state("my-catalog"), "linked")
        row = registry.inputs()["my-catalog"]
        self.assertEqual((row["kind"], row["audience"]), ("folder", "private"))

    def test_a_folder_catalog_that_is_a_plain_folder_is_linked_too(self):
        self.config(folder_entry("my-catalog", self.root / ".lab/catalogs/my-catalog"))
        self.write(".lab/catalogs/my-catalog/catalog.yml", "x-gravitee-catalog:\n  name: M\n")
        self.assertEqual(self.state("my-catalog"), "linked")

    def test_a_folder_ignores_the_failures_of_a_shadowed_git_catalog(self):
        self.config(folder_entry("my-catalog", self.root / ".lab/catalogs/my-catalog", shadows={"url": "https://example.invalid/m.git"}))
        self.lockfile(**{"my-catalog": lock_entry(error="unreachable", shadowed=True)})
        self.write(".lab/catalogs/my-catalog/catalog.yml", "x-gravitee-catalog:\n  name: M\n")
        self.assertEqual(self.state("my-catalog"), "linked")

    def needs(self, includer="local", missing=True):
        target = ".lab/catalogs/acme-hub/addons/one/compose.yml"
        return resolve.catalog_needs({"ns": includer, "includes": [target]}, registry.inputs(), [target] if missing else [])

    def test_the_resolver_reads_a_copy_from_the_version_of_a_row_that_has_no_pin(self):
        # catalog_needs takes a copy for what has a pin, else what has a version: a row says it by its version
        self.assertEqual([need["reason"] for need in self.needs()], ["missing"])   # not in the configuration
        self.git(lock=False, installed=False)
        self.assertEqual([need["reason"] for need in self.needs()], ["downloading"])
        self.git(installed=False, pin=None, error="unreachable")
        self.assertEqual([need["reason"] for need in self.needs()], ["failed"])
        self.git()
        self.assertEqual(self.needs(missing=False), [])

    def test_the_resolver_reads_the_floor_a_catalog_declares_of_another(self):
        self.config(git_entry("acme-hub"), git_entry("partner"))
        self.lockfile(**{"acme-hub": lock_entry(), "partner": lock_entry(version="1.0.0")})
        self.install("acme-hub", A, catalog_yml="x-gravitee-catalog:\n  name: A\n  requires:\n    catalogs:\n"
                                                "      partner: {name: P, url: \"https://example.invalid/p.git\", min: 2.0.0}\n")
        self.install("partner", A)
        needs = resolve.catalog_needs({"ns": "acme-hub", "includes": [".lab/catalogs/partner/addons/one/compose.yml"]},
                                      registry.inputs(), [])
        self.assertEqual([(need["id"], need["reason"], need["have"], need["min"]) for need in needs],
                         [("partner", "old", "1.0.0", "2.0.0")])
        self.lockfile(**{"acme-hub": lock_entry(), "partner": lock_entry(version="2.0.0")})
        self.assertEqual(resolve.catalog_needs({"ns": "acme-hub", "includes": [".lab/catalogs/partner/addons/one/compose.yml"]},
                                               registry.inputs(), []), [])


class Fixtures(Case):
    """The registry on the fixture catalogs of tests.catalogs, installed the way the Lab leaves them."""

    def test_a_default_catalog_and_its_neighbours(self):
        fixtures.need_git()
        fixtures.project(self, paths, ns="fixture", kind="git")
        fixtures.install(self, "partner", kind="git")
        fixtures.install(self, "lab-new", kind="folder")
        fixtures.local(self, fixtures.units("local")[0][1], fixtures.units("local")[0][0])
        rows = registry.inputs()
        self.assertEqual(list(rows), ["fixture", "local", "partner", "lab-new"])
        fixture, partner, new = rows["fixture"], rows["partner"], rows["lab-new"]
        self.assertEqual((fixture["name"], fixture["version"], fixture["lab"], fixture["audience"], fixture["state"],
                          fixture["kind"]), ("Fixture catalog", "1.0.0", 1, "public", "uptodate", "git"))
        self.assertEqual(sorted(fixture["requires_catalogs"]), ["partner"])
        self.assertEqual(fixture["requires_catalogs"]["partner"]["min"], "1.0.0")
        self.assertEqual((partner["version"], partner["audience"], partner["state"]), ("2.0.0", "private", "uptodate"))
        self.assertEqual((new["lab"], new["kind"], new["state"]), (99, "folder", "linked"))
        for ns in ("fixture", "partner", "lab-new"):
            with self.subTest(ns):
                disk = fixtures.units(ns)
                found = [(kind, name) for kind in fixtures.KINDS for name in rows[ns]["units"][kind]]
                self.assertEqual(sorted(found), sorted(disk))
        self.assertEqual(fixture["ignored"], registry.manifest("fixture")["ignored"])
        self.assertEqual(paths.ids("addon"), [uid for uid in paths.ids("addon")])
        for kind in fixtures.KINDS:
            for uid in paths.ids(kind):
                self.assertTrue((self.root / paths.unit_file(kind, uid)).is_file())

    def test_the_manifest_of_a_fixture_install_is_valid_for_its_pin(self):
        fixtures.need_git()
        fixtures.project(self, paths, ns="fixture", kind="git")
        done = fixtures.read_lock(self)["catalogs"]["fixture"]
        found = registry.manifest("fixture")
        self.assertEqual(found["commit"], done["pin"])
        self.assertEqual(registry.lock_of("fixture")["pin"], done["pin"])
        self.assertEqual(sorted(registry.units_of("fixture")["addon"]), [n for k, n in fixtures.units("fixture") if k == "addon"])

    def test_a_declared_catalog_and_one_that_failed(self):
        fixtures.project(self, paths, ns="fixture", kind="folder")
        fixtures.declare(self, "partner")
        fixtures.declare(self, "lab-new", error="unreachable")
        rows = registry.inputs()
        self.assertEqual((rows["partner"]["state"], rows["partner"]["version"]), ("downloading", None))
        self.assertEqual((rows["lab-new"]["state"], rows["lab-new"]["error"]), ("unreachable", "unreachable"))

    def test_a_shadow_is_a_folder_with_a_store_and_a_lock_that_says_so(self):
        fixtures.need_git()
        fixtures.project(self, paths, ns="fixture", kind="folder", store=True)
        row = registry.inputs()["fixture"]
        self.assertEqual((row["kind"], row["state"], row["listed"]), ("folder", "linked", True))
        self.assertEqual(registry.entry("fixture")["shadows"]["branch"], "main")
        self.assertIs(registry.lock_of("fixture")["shadowed"], True)


class ReadersWrite(Disk):

    def test_no_reader_makes_a_file_or_a_folder(self):
        self.default("fixture")
        for call in (registry.read_config, registry.config_problem, registry.entries, registry.precedence, registry.read_lock,
                     registry.sources, registry.inputs, registry.suggestions, registry.units, registry.suggestions_file,
                     lambda: registry.entry("fixture"), lambda: registry.lock_of("fixture"),
                     lambda: registry.manifest("fixture"), lambda: registry.units_of("fixture"),
                     lambda: registry.units_of("local"), lambda: registry.install_dir("fixture"),
                     lambda: registry.stage_dir("fixture"), lambda: registry.store_dir("fixture"),
                     lambda: registry.check_namespace("fixture", listed=["fixture"]),
                     lambda: registry.web_url(git_entry("fixture")), lambda: registry.display_address("acme/x")):
            call()
        self.assertEqual(self.tree(), ["catalogs.default.json"])

    def test_the_readers_work_when_the_project_folder_is_gone(self):
        other = Path(tempfile.mkdtemp())
        other.rmdir()
        self.patch(paths, "ROOT", other)
        self.assertEqual((registry.entries(), registry.read_config(), registry.sources()[0]["id"], list(registry.inputs())),
                         ([], None, "local", ["local"]))


class Hostile(Disk):
    """Whatever a file holds, a read answers a value of its type and never raises."""

    def mutations(self, seed, data, count):
        generator = random.Random(seed)
        for _ in range(count):
            mutated = bytearray(data)
            for _ in range(generator.randint(1, 8)):
                kind = generator.randrange(4)
                at = generator.randrange(len(mutated) + 1)
                if kind == 0 and mutated:
                    mutated[min(at, len(mutated) - 1)] = generator.randrange(256)
                elif kind == 1:
                    mutated[at:at] = bytes(generator.randrange(256) for _ in range(generator.randint(1, 6)))
                elif kind == 2:
                    del mutated[at:at + generator.randint(1, 20)]
                else:
                    mutated = mutated[:at]
            yield bytes(mutated)

    def test_a_config_mutated_at_random(self):
        good = json.dumps({"schema": 1, "catalogs": [git_entry("acme-hub"), folder_entry("my-catalog", "/home/me/x")]}).encode()
        for data in self.mutations(1, good, 300):
            self.raw(".lab/catalogs.json", data)
            config = registry.read_config()
            self.assertIsInstance(config, dict)
            self.assertIsInstance(config["catalogs"], list)
            for one in config["catalogs"]:
                self.assertTrue(re.fullmatch(paths.NS, one["id"]))
                self.assertIn(one["kind"], ("git", "folder"))
            registry.config_problem(), registry.entries(), registry.precedence(), registry.sources(), registry.inputs()
            registry.suggestions(), registry.units()

    def test_a_lock_mutated_at_random(self):
        good = json.dumps({"schema": 1, "catalogs": {"acme-hub": lock_entry(via="ssh", pending={"op": "add", "from": None, "to": B})}}).encode()
        self.config(git_entry("acme-hub"))
        for data in self.mutations(2, good, 300):
            self.raw(".lab/catalogs.lock", data)
            lock = registry.read_lock()
            for ns, entry in lock["catalogs"].items():
                self.assertTrue(re.fullmatch(paths.NS, ns))
                self.assertEqual(set(entry) >= {"pin", "roots", "audience", "pending"}, True)
                registry._lock_entry(entry, strict=True)   # what was read is what can be written
            registry.lock_of("acme-hub"), registry.inputs(), registry.sources(), registry.units_of("acme-hub")

    def test_a_manifest_mutated_at_random(self):
        self.config(git_entry("acme-hub"))
        self.lockfile(**{"acme-hub": lock_entry()})
        self.install("acme-hub", A, ("addon:one", "playground:two"))
        good = (self.root / ".lab/catalogs/acme-hub/.installed.json").read_bytes()
        for data in self.mutations(3, good, 300):
            self.raw(".lab/catalogs/acme-hub/.installed.json", data)
            found = registry.manifest("acme-hub")
            self.assertTrue(found is None or found["commit"] == A)
            for names in registry.units_of("acme-hub").values():
                for name in names:
                    self.assertRegex(name, paths.NAME)
            registry.inputs(), registry.sources()

    def test_a_catalog_yml_mutated_at_random(self):
        good = (fixtures.CATALOGS / "fixture" / "catalog.yml").read_bytes() + b"  shared_settings: [A, B]\n  canary:\n    - x\n"
        for data in self.mutations(4, good, 400):
            found = registry._scan(data)
            self.assertIsInstance(found, dict)
            for need in found.get("requires_catalogs", {}).values():
                self.assertEqual(set(need), {"name", "url", "min"})
            self.assertLessEqual(len(found.get("name", "")), 40)

    def test_a_suggestions_file_mutated_at_random(self):
        elsewhere = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(elsewhere, True))
        file = elsewhere / "s.json"
        patcher = mock.patch.dict(os.environ, {"LAB_CATALOGS_FILE": str(file)})
        patcher.start()
        self.addCleanup(patcher.stop)
        good = json.dumps({"schema": 1, "catalogs": [{"name": "A", "url": "https://git.example.invalid/a/b.git",
                                                      "branch": "main", "host_access": True}]}).encode()
        for data in self.mutations(5, good, 200):
            file.write_bytes(data)
            for row in registry.suggestions():
                self.assertEqual(set(row), {"id", "kind", "name", "description", "url", "branch", "host_access", "listed"})
                self.assertNotIn("@", row["url"].split("//", 1)[-1].split("/", 1)[0].replace("git@", ""))

    def readers(self, ns="acme-hub"):
        """Every read of the registry, for a file that holds something hostile: none may raise."""
        for call in (registry.read_config, registry.config_problem, registry.entries, registry.precedence,
                     registry.read_lock, registry.sources, registry.inputs, registry.suggestions, registry.units,
                     lambda: registry.entry(ns), lambda: registry.lock_of(ns), lambda: registry.manifest(ns),
                     lambda: registry.read_manifest(ns), lambda: registry.units_of(ns), lambda: registry.is_git(ns)):
            call()

    def test_a_document_nested_too_deeply_reads_as_damaged_and_never_raises(self):
        # Valid JSON that Python parses and cannot copy or dump: a depth of a few hundred overflows the stack
        entry = git_entry("acme-hub")
        for depth in (70, 300, 900, 5000, 400000):
            deep = "[" * depth + "]" * depth
            documents = {
                "config, a key of the file": ('.lab/catalogs.json', '{"schema": 1, "x": %s, "catalogs": []}' % deep),
                "config, a key of an entry": ('.lab/catalogs.json', '{"schema": 1, "catalogs": [%s]}' % json.dumps(
                    {**entry, "x": "DEEP"}).replace('"DEEP"', deep)),
                "config, an object": ('.lab/catalogs.json', '{"schema": 1, "catalogs": [], "x": %s}' % (
                    '{"a": ' * min(depth, 100000) + "1" + "}" * min(depth, 100000))),
                "lock, a key of the file": ('.lab/catalogs.lock', '{"schema": 1, "x": %s, "catalogs": {}}' % deep),
                "lock, a key of an entry": ('.lab/catalogs.lock', '{"schema": 1, "catalogs": {"acme-hub": {"x": %s}}}' % deep),
                "manifest": ('.lab/catalogs/acme-hub/.installed.json', json.dumps(
                    {"schema": 1, "catalog": "acme-hub", "commit": A, "files": {}, "units": {}, "ignored": 0,
                     "x": "DEEP"}).replace('"DEEP"', deep)),
                "suggestions": ('suggestions.json', '{"schema": 1, "catalogs": [], "x": %s}' % deep)}
            for label, (relative, text) in documents.items():
                with self.subTest(f"{label}, {depth} deep"):
                    self.config(entry)
                    self.lockfile(**{"acme-hub": lock_entry()})
                    self.install("acme-hub", A)
                    self.raw(relative, text.encode())
                    with mock.patch.dict(os.environ, {"LAB_CATALOGS_FILE": str(self.root / "suggestions.json")}):
                        self.readers()
                        if label.startswith("config"):
                            self.assertEqual(registry.read_config(), {"schema": 1, "catalogs": []})
                            self.assertIn("nested too deeply", registry.config_problem())
                        elif label.startswith("lock"):
                            self.assertEqual(registry.read_lock(), {"schema": 1, "catalogs": {}})
                            self.assertIn("nested too deeply", registry.config_problem())
                        elif label == "manifest":
                            self.assertIsNone(registry.manifest("acme-hub"))
                            self.assertEqual(registry.inputs()["acme-hub"]["state"], "damaged")
                        else:
                            self.assertEqual(registry.suggestions(), [])

    def test_the_depth_limit_is_exact(self):
        limit = registry.DEPTH_LIMIT
        self.assertEqual(registry._json(b"[" * limit + b"]" * limit), (self.nest(limit), None))
        self.assertEqual(registry._json(b"[" * (limit + 1) + b"]" * (limit + 1)), (None, "it is nested too deeply"))
        self.assertEqual(registry._json(b'{"a": ' * limit + b"1" + b"}" * limit)[1], None)
        self.assertEqual(registry._json(b'{"a": ' * (limit + 1) + b"1" + b"}" * (limit + 1)), (None, "it is nested too deeply"))
        self.assertEqual(registry._json(b'{"a": [' * 40 + b"1" + b"]}" * 40)[1], "it is nested too deeply")
        for text in (b"[" * 1_000_000, b'{"a":' * 200_000, b"[[" * 500_000 + b"]" * 5):
            self.assertIn(registry._json(text)[1], ("it is nested too deeply", "it is not valid JSON"))
        self.assertEqual(registry._json(b'{"a": [1, {"b": [2, 3]}], "c": {"d": {}}, "e": []}')[1], None)

    @staticmethod
    def nest(depth):
        value = []
        for _ in range(depth - 1):
            value = [value]
        return value

    def test_a_document_that_is_too_deep_is_not_written(self):
        deep = self.nest(3000)
        for label, write in {"config, a key of the file": lambda: registry.write_config({"schema": 1, "catalogs": [], "x": deep}),
                             "config, a key of an entry": lambda: registry.write_config(
                                 {"schema": 1, "catalogs": [git_entry("acme-hub", x=deep)]}),
                             "lock, a key of the file": lambda: registry.write_lock({"schema": 1, "catalogs": {}, "x": deep}),
                             "lock, a key of an entry": lambda: registry.write_lock(
                                 {"schema": 1, "catalogs": {"acme-hub": {"x": deep}}}),
                             "set_lock": lambda: registry.set_lock("acme-hub", x=deep)}.items():
            with self.subTest(label), self.assertRaises(ValueError):
                write()
        cycle = []
        cycle.append(cycle)
        with self.assertRaises(ValueError):
            registry.set_lock("acme-hub", x=cycle)
        self.assertFalse((self.root / ".lab/catalogs.json").exists() or (self.root / ".lab/catalogs.lock").exists())

    def test_a_number_out_of_range_reads_as_damaged_and_a_finite_one_round_trips(self):
        # Python reads 1e999 as infinity, which no writer can store: the file would read fine and the next write would fail
        for number in ("1e999", "-1e999", "1E400", "1e+99999999999", "9" * 400 + ".5"):
            for relative, body in (
                    (".lab/catalogs.json", '{"schema": 1, "catalogs": [], "x": %s}'),
                    (".lab/catalogs.lock", '{"schema": 1, "catalogs": {"acme-hub": {"pin": null, "x": %s}}}'),
                    (".lab/catalogs/acme-hub/.installed.json",
                     '{"schema": 1, "catalog": "acme-hub", "commit": "' + A + '", "files": {}, "units": {}, "ignored": 0, "x": %s}')):
                with self.subTest(f"{number} in {relative}"):
                    self.config(git_entry("acme-hub"))
                    self.lockfile(**{"acme-hub": lock_entry()})
                    self.raw(relative, (body % number).encode())
                    self.readers()
                    if relative.endswith(".installed.json"):
                        self.assertIsNone(registry.manifest("acme-hub"))
                    else:
                        self.assertIn("not valid JSON", registry.config_problem())
        self.raw(".lab/catalogs.json", b'{"schema": 1, "catalogs": [], "big": 1.5e300, "small": 1e-999, "n": -0.0}')
        self.assertIsNone(registry.config_problem())
        config = registry.read_config()
        registry.write_config(config)
        self.assertEqual(registry.read_config(), config)
        self.assertEqual(config["big"], 1.5e300)

    def test_a_lone_surrogate_is_text_no_one_can_store(self):
        # "\ud800" is valid JSON and a Python string, and has no UTF-8: it must not get into a name, an address or a path,
        # and a newer Lab's key that holds one is written back escaped
        self.raw(".lab/catalogs.json", json.dumps({"schema": 1, "catalogs": [
            git_entry("one-hub", url="https://example.invalid/\ud800.git"),
            git_entry("two-hub", branch="ma\ud800in"),
            folder_entry("my-catalog", "/home/me/\ud800", shadows={"url": "https://example.invalid/\udfff.git"}),
            git_entry("three-hub", note="\ud800", **{"\ud801": 1})], "future": ["\udc00"]}).encode("ascii"))
        config = registry.read_config()
        self.assertEqual([one["id"] for one in config["catalogs"]], ["two-hub", "three-hub"])
        self.assertNotIn("branch", config["catalogs"][0])
        self.assertEqual(config["catalogs"][1]["note"], "\ud800")
        self.assertIn("2 of the catalogs", registry.config_problem())
        registry.write_config(config)
        written = (self.root / ".lab/catalogs.json").read_bytes()
        written.decode("utf-8")   # a file that is text
        self.assertEqual(registry.read_config(), config)
        self.assertIsNone(registry.config_problem())
        registry.write_config(registry.read_config())
        self.assertEqual((self.root / ".lab/catalogs.json").read_bytes(), written)   # and the same each time
        self.lockfile(**{"acme-hub": lock_entry(name="Hub\ud800‮!", note="\ud800")})
        self.assertEqual(registry.lock_of("acme-hub")["name"], "Hub!")
        registry.set_lock("acme-hub", error="timeout")
        self.assertEqual(registry.lock_of("acme-hub")["note"], "\ud800")
        json.loads((self.root / ".lab/catalogs.lock").read_bytes().decode("utf-8"))
        for text in ("a\ud800b", "\udfff"):
            self.assertEqual(registry.shown(text, 40), text.replace("\ud800", "").replace("\udfff", ""))
            self.assertNotIn("\ud800", registry.display_address(f"https://example.invalid/{text}"))
        self.assertTrue(registry._plain("abc") and not registry._plain("a\ud800") and not registry._plain(""))

    def test_an_address_of_any_kind_is_displayed_without_raising(self):
        generator = random.Random(6)
        alphabet = "ab/:@.~ \n\x00\u202e%#?[]{}'\"\\-_=+&*"
        for _ in range(500):
            text = "".join(generator.choice(alphabet) for _ in range(generator.randint(0, 40)))
            self.assertIsInstance(registry.display_address(text), str)
            self.assertTrue(registry.web_url({"id": "acme-hub", "kind": "git", "url": text}) is None
                            or registry.web_url({"id": "acme-hub", "kind": "git", "url": text}).startswith("https://"))


class Privacy(Disk):

    def test_no_credential_reaches_an_answer_or_a_line(self):
        secret = "s3cr3t-t0k3n"
        self.raw(".lab/catalogs.json", json.dumps({"schema": 1, "catalogs": [
            git_entry("one-hub", url=f"https://user:{secret}@example.invalid/a.git"),
            git_entry("two-hub", url=f"ssh://git:{secret}@example.invalid/b.git"), {"id": "BAD", "token": secret}]}).encode())
        self.raw(".lab/catalogs.lock", f'{{"schema": 1, "catalogs": {{"one-hub": {{"name": "{secret}"}}}}, "{secret}": 1}}'.encode())
        everything = json.dumps([registry.read_config(), registry.entries(), registry.sources(), registry.inputs(),
                                 registry.suggestions(), registry.config_problem(), registry.read_lock(),
                                 [registry.web_url(one) for one in registry.entries()],
                                 [registry.display_address(one["url"]) for one in registry.entries()]])
        # The secret in a field the Lab shows is the author's name, which is text, never an address
        self.assertNotIn(f"user:{secret}", everything)
        self.assertNotIn(f"git:{secret}", everything)
        self.assertNotIn(secret, registry.config_problem() or "")

    def test_a_problem_never_says_what_the_file_held(self):
        self.raw(".lab/catalogs.json", b'{"token": "s3cr3t", "catalogs": [')
        self.assertNotIn("s3cr3t", registry.config_problem())

    def test_a_space_in_the_user_information_does_not_keep_the_credential(self):
        # The promise: a user name or token in an address is removed on read and refused on write
        for url, clean in (("https://us er:tok@example.invalid/acme/hub", "https://example.invalid/acme/hub"),
                           ("https://user:to k@example.invalid/acme/hub", "https://example.invalid/acme/hub"),
                           ("https://user\u00a0name:tok@example.invalid/acme/hub", "https://example.invalid/acme/hub"),
                           ("HTTP://a b@example.invalid/x.git", "HTTP://example.invalid/x.git"),
                           ("ssh://u:p w@example.invalid/x", "ssh://u@example.invalid/x")):
            with self.subTest(url):
                self.config(git_entry("acme-hub", url=url))
                self.assertEqual(registry.entries()[0]["url"], clean)
                self.assertNotIn("tok", json.dumps([registry.read_config(), registry.inputs(), registry.config_problem()]))
                with self.assertRaises(registry.CatalogError) as raised:
                    registry.write_config({"schema": 1, "catalogs": [git_entry("acme-hub", url=url)]})
                self.assertEqual(raised.exception.code, "bad_address_secret")
                self.assertEqual(json.loads((self.root / ".lab/catalogs.json").read_text(encoding="utf-8"))["catalogs"][0]["url"],
                                 url)   # the file is the person's, and was not rewritten
        with self.assertRaises(ValueError):   # a user name with a space is no address once the password is out
            registry.write_config({"schema": 1, "catalogs": [git_entry("acme-hub", url="ssh://u :p@example.invalid/x")]})
        self.assertEqual(registry._clean_url("ssh://git:se cret@example.invalid/x"), "ssh://git@example.invalid/x")
        self.assertEqual(registry._clean_url("ssh://git@example.invalid/team@home"), "ssh://git@example.invalid/team@home")
        self.assertEqual(registry._clean_url("https://example.invalid/a b@c"), "https://example.invalid/a b@c")

    def test_an_address_has_no_space_of_any_kind(self):
        for url in ("https://example.invalid/a b.git", "https://example.invalid/a\tb.git", "https://example.invalid/a\u00a0b",
                    "https://example.invalid/a\u3000b", " https://example.invalid/a.git", "https://example.invalid/a.git "):
            with self.subTest(url):
                self.config(git_entry("acme-hub", url=url), git_entry("other-hub"))
                self.assertEqual([one["id"] for one in registry.entries()], ["other-hub"])
                self.assertIn("acme-hub has no valid address", registry.config_problem())
                with self.assertRaises(ValueError):
                    registry.write_config({"schema": 1, "catalogs": [git_entry("acme-hub", url=url)]})
        folder = "/home/me/My Drive/catalog"
        self.config(folder_entry("my-catalog", folder, shadows={"url": "https://example.invalid/a b.git"}))
        self.assertEqual(registry.entries(), [{"id": "my-catalog", "kind": "folder", "path": folder,
                                               "added": "2026-10-16T09:00:00Z"}])   # a folder may have spaces, a repository not

    def test_the_address_of_a_shadowed_repository_is_credential_free_too(self):
        self.config(folder_entry("my-catalog", "/home/me/x", shadows={"url": "https://user:tok@example.invalid/m.git"}))
        self.assertEqual(registry.entries()[0]["shadows"], {"url": "https://example.invalid/m.git"})
        with self.assertRaises(registry.CatalogError) as raised:
            registry.write_config({"schema": 1, "catalogs": [folder_entry(
                "my-catalog", "/home/me/x", shadows={"url": "https://user:tok@example.invalid/m.git"})]})
        self.assertEqual(raised.exception.code, "bad_address_secret")


if __name__ == "__main__":
    unittest.main()
