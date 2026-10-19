"""defaults: the catalogs the engine lists. The tracked catalogs.default.json is checked as a file (it parses, its
address is one git can be given, nothing else in the engine's code holds an address), then the reader on files of its
own in a temporary project folder: tolerant of any damage, never raising, and never trusting what a catalogs.json
says."""

import json
import os
import re
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lab_ui import paths
from lab_ui.catalogs import defaults, gitx, registry

from .. import Case

REPOSITORY = Path(__file__).resolve().parents[3]
TRACKED = REPOSITORY / "catalogs.default.json"
ROOT_COMMIT = "3d12437a0c1b2a3f4e5d6c7b8a9f0e1d2c3b4a59"

# Where an address of a catalog may not be written: the code, the page, the server and the command of the Lab UI, the
# seeder's modules, the core file, the Makefile and the workflows. The documentation may link the default catalog by
# name, and so may this file.
SCANNED = ("lab-ui/lab_ui", "lab-ui/web", ".github", "Makefile", "docker-compose.yml")
GLOBBED = ("lab-ui/*.py", "seeder/*.py")
SKIPPED = ("__pycache__", "node_modules", ".git")


def listing(*entries, **top):
    """The text of a default file that lists these entries."""
    return json.dumps({"schema": 1, "catalogs": list(entries), **top})


def entry(uid="demos", **fields):
    """A well formed entry; a field given as None is left out."""
    base = {"id": uid, "name": "Demos", "description": "Playgrounds of the demos", "branch": "main",
            "url": f"https://example.invalid/team/{uid}.git"}
    base.update(fields)
    return {key: value for key, value in base.items() if value is not None}


def mentions(root, words):
    """[(file relative to the root, word)] of the files of the scanned places that hold a word, in any case."""
    root, found = Path(root), []
    places = [root / place for place in SCANNED] + [file for glob in GLOBBED for file in sorted(root.glob(glob))]
    for place in places:
        files = [place] if place.is_file() else [Path(d) / name for d, _, names in os.walk(place) for name in names
                                                  if not set(Path(d).relative_to(root).parts) & set(SKIPPED)]
        for file in sorted(files):
            if file.stat().st_size > 4 << 20:
                continue
            text = file.read_bytes().decode("utf-8", "ignore").lower()
            found += [(file.relative_to(root).as_posix(), word) for word in words if re.search(word, text)]
    return found


class TrackedFile(Case):
    """The file at the root of the engine. The address of the default catalog is the repository that holds it while that
    repository is private. It changes at the public release, in catalogs.default.json alone: this class checks the form
    of the address and never its value, so that the change needs no edit here."""

    def setUp(self):
        self.text = TRACKED.read_text(encoding="utf-8")
        self.data = json.loads(self.text)

    def test_the_file_is_the_schema_with_one_catalog(self):
        self.assertEqual(sorted(self.data), ["catalogs", "schema"])
        self.assertEqual(self.data["schema"], 1)
        self.assertEqual(len(self.data["catalogs"]), 1)
        self.assertTrue(self.text.endswith("}\n") and "\r" not in self.text)

    def test_the_catalog_is_the_default_one_of_the_design(self):
        (item,) = self.data["catalogs"]
        self.assertEqual((item["id"], item["name"], item["branch"]), ("gravitee", "Gravitee public", "main"))
        # The roots are filled at the public release, when the history is final: until then the file has none
        self.assertLessEqual({"id", "name", "description", "url", "branch"}, set(item))
        self.assertLessEqual(set(item), {"id", "name", "description", "url", "branch", "roots"})
        self.assertTrue(item["description"])

    def test_every_id_is_a_namespace_and_none_is_twice(self):
        ids = [item["id"] for item in self.data["catalogs"]]
        self.assertEqual(len(ids), len(set(ids)))
        for uid in ids:
            self.assertRegex(uid, rf"^(?:{paths.NS})$")
            self.assertNotEqual(uid, paths.LOCAL)

    def test_every_address_is_https_without_a_user_for_git_to_read(self):
        for item in self.data["catalogs"]:
            address = gitx.parse_address(item["url"])
            self.assertEqual(address.kind, "git")
            self.assertEqual(address.url, item["url"], "written the way the grammar writes it")
            self.assertTrue(address.url.startswith("https://"))
            self.assertNotIn("@", address.url)

    def test_the_roots_are_commits_when_there_are_any(self):
        for item in self.data["catalogs"]:
            for root in item.get("roots", []):
                self.assertRegex(root, r"[0-9a-f]{40}")

    def test_the_reader_takes_the_file_as_it_is(self):
        read = defaults.read()
        self.assertEqual([item["id"] for item in read], [item["id"] for item in self.data["catalogs"]])
        for item, found in zip(self.data["catalogs"], read):
            self.assertEqual({key: found[key] for key in item}, item)
        self.assertEqual(paths.default_ns(), read[0]["id"])

    def test_no_code_of_the_engine_holds_an_address_of_a_catalog(self):
        # The address and the repository of every listed catalog, taken from the file, and what starts with them:
        # another repository of the same name family, a private one next to a public one
        words = set()
        for item in self.data["catalogs"]:
            address = gitx.parse_address(item["url"])
            words |= {rf"(?<![\w.-]){re.escape(name.lower())}" for name in (address.display, address.repo)}
        self.assertEqual(mentions(REPOSITORY, sorted(words)), [])

    def test_the_scan_finds_an_address_where_there_is_one(self):
        # Without this the test above would pass on a scan that looks nowhere
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, text in (("lab-ui/lab_ui/server/api.py", "URL = 'https://github.com/team/Catalog.git'\n"),
                               ("lab-ui/web/js/a.js", "// see team/catalog\n"), ("Makefile", "all:\n"),
                               ("seeder/seed.py", "x = 'github.com/team/catalog'\n"),
                               ("lab-ui/server.py", "CATALOG = 'ssh://git@github.com/team/catalog'\n"),
                               ("docker-compose.yml", "image: team/Catalog:latest\n"),
                               (".github/workflows/ci.yml", "run: git clone team/catalog-extra\n"),
                               ("lab-ui/lab_ui/b.py", "other = 'team/engine', 'xteam/catalog', 'a-team/catalog'\n"),
                               ("docs/catalogs.md", "team/catalog\n"), ("seeder/tests/a.py", "team/catalog\n"),
                               ("lab-ui/tests/test_a.py", "team/catalog\n"), ("lab-ui/catalog.py", "# team/catalog\n"),
                               ("lab-ui/web/node_modules/x/a.js", "team/catalog\n")):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(text, encoding="utf-8")
            words = [r"(?<![\w.-])team/catalog"]
            self.assertEqual(sorted(mentions(root, words)), [
                (".github/workflows/ci.yml", words[0]), ("docker-compose.yml", words[0]), ("lab-ui/catalog.py", words[0]),
                ("lab-ui/lab_ui/server/api.py", words[0]), ("lab-ui/server.py", words[0]), ("lab-ui/web/js/a.js", words[0]),
                ("seeder/seed.py", words[0])])
            # Not found: another repository of the owner, another owner, the documentation, a test, a library


class Reading(Case):
    """read(): the file as a temporary project folder holds it."""

    def setUp(self):
        self.folder(paths)

    def put(self, text):
        self.write(paths.DEFAULT_FILE, text)
        return defaults.read()

    def test_a_file_that_is_not_there_lists_nothing(self):
        self.assertEqual(defaults.read(), [])
        self.assertIsNone(paths.default_ns())

    def test_a_good_file_is_read_in_order_with_every_field(self):
        found = self.put(listing(entry("alpha", roots=[ROOT_COMMIT.upper(), ROOT_COMMIT]),
                                 entry("beta", branch=None, description=None, name=None)))
        self.assertEqual(found, [
            {"id": "alpha", "name": "Demos", "description": "Playgrounds of the demos", "branch": "main",
             "url": "https://example.invalid/team/alpha.git", "roots": [ROOT_COMMIT]},
            {"id": "beta", "name": "beta", "description": "", "url": "https://example.invalid/team/beta.git"}])
        self.assertEqual(paths.default_ns(), "alpha")

    def test_damage_to_the_file_reads_as_no_list(self):
        bad = [("empty", ""), ("not json", "{"), ("a list", "[]"), ("a text", '"x"'), ("null", "null"),
               ("a number", "1"), ("no schema", json.dumps({"catalogs": [entry()]})),
               ("another schema", json.dumps({"schema": 2, "catalogs": [entry()]})),
               ("a schema as text", json.dumps({"schema": "1", "catalogs": [entry()]})),
               ("a schema as true", json.dumps({"schema": True, "catalogs": [entry()]})),
               ("a schema as 1.0", '{"schema": 1.0, "catalogs": [{"id": "demos"}]}'),
               ("no catalogs", json.dumps({"schema": 1})),
               ("catalogs as a map", json.dumps({"schema": 1, "catalogs": {}})),
               ("no entry", json.dumps({"schema": 1, "catalogs": []})),
               ("entries as texts", json.dumps({"schema": 1, "catalogs": ["demos"]})),
               ("too deep", "[" * 60000), ("too big", "[" + " " * defaults.LIMIT + "]"),
               ("a constant", '{"schema": NaN}')]
        for label, text in bad:
            with self.subTest(label):
                self.assertEqual(self.put(text), [])

    def test_a_file_that_is_not_utf_8_or_is_too_big_reads_as_no_list(self):
        file = self.root / paths.DEFAULT_FILE
        file.write_bytes(b'{"schema": 1, "catalogs": []}\xff')
        self.assertEqual(defaults.read(), [])
        file.write_text(listing(entry(description="x" * defaults.LIMIT)), encoding="utf-8")
        self.assertEqual(defaults.read(), [])
        file.write_text("﻿" + listing(entry()), encoding="utf-8")   # a byte order mark is tolerated
        self.assertEqual([item["id"] for item in defaults.read()], ["demos"])

    def test_what_is_not_a_regular_file_reads_as_no_list_and_never_blocks(self):
        (self.root / paths.DEFAULT_FILE).mkdir()
        self.assertEqual(defaults.read(), [])
        (self.root / paths.DEFAULT_FILE).rmdir()
        os.mkfifo(self.root / paths.DEFAULT_FILE)   # a read would wait for a writer that never comes
        self.assertEqual(defaults.read(), [])

    def test_an_entry_that_is_damaged_reads_as_nothing_and_the_others_stay(self):
        damaged = [
            ("an id in capitals", entry("Demos")), ("an id too short", entry("a")),
            ("an id with a slash", entry("a/b")),
            ("an id as a number", entry(7)), ("an id as a list", entry(["demos"])), ("no id", entry(id=None)),
            ("local", entry("local")), ("a trailing dash", entry("demos-")), ("an id too long", entry("a" * 26)),
            ("no address", entry(url=None)), ("an address as a number", entry(url=5)),
            ("an address as a list", entry(url=["https://example.invalid/a/b.git"])),
            ("a plain http address", entry(url="http://example.invalid/team/x.git")),
            ("an ssh address", entry(url="ssh://git@example.invalid/team/x.git")),
            ("an scp address", entry(url="git@example.invalid:team/x.git")),
            ("a shorthand", entry(url="team/x")), ("a folder", entry(url="/tmp/catalog")),
            ("git://", entry(url="git://example.invalid/team/x.git")),
            ("file://", entry(url="file:///tmp/catalog")),
            ("a user", entry(url="https://me@example.invalid/team/x.git")),
            ("a token", entry(url="https://me:tok@example.invalid/team/x.git")),
            ("a space", entry(url="https://example.invalid/team/x y.git")),
            ("a newline", entry(url="https://example.invalid/team/x.git\n")),
            ("a leading dash", entry(url="-https://example.invalid/team/x.git")),
            ("an option", entry(url="https://example.invalid/--upload-pack=x")),
            ("a query", entry(url="https://example.invalid/team/x.git?a=b")),
            ("an empty address", entry(url="")),
            ("a branch as a number", entry(branch=3)), ("a branch with ..", entry(branch="a..b")),
            ("a branch with a dash first", entry(branch="-x")), ("a branch as a list", entry(branch=["main"])),
            ("a branch with a space", entry(branch="a b")), ("roots as a text", entry(roots=ROOT_COMMIT)),
            ("a short root", entry(roots=["abc123"])), ("a root that is not hex", entry(roots=["z" * 40])),
            ("a root as a number", entry(roots=[1])), ("a good root and a bad one", entry(roots=[ROOT_COMMIT, "x"])),
            ("an entry as a text", "demos"), ("an entry as null", None), ("an entry as a list", [])]
        for label, bad in damaged:
            with self.subTest(label):
                self.assertEqual([item["id"] for item in self.put(listing(entry("first"), bad, entry("last")))],
                                 ["first", "last"])

    def test_a_damaged_first_entry_lists_nothing_so_that_the_second_never_owns_the_bare_ids(self):
        self.assertEqual(self.put(listing(entry(url="http://example.invalid/a/b"), entry("second"))), [])
        # paths reads the first entry for the namespace by its id alone: the two agree whenever the list is not empty
        self.assertEqual(paths.default_ns(), "demos")
        self.assertEqual(self.put(listing(entry("first"), entry("second"))) and paths.default_ns(), "first")

    def test_an_id_is_listed_once_and_the_first_one_wins(self):
        found = self.put(listing(entry("one", name="First"), entry("two"), entry("one", name="Second")))
        self.assertEqual([(item["id"], item["name"]) for item in found], [("one", "First"), ("two", "Demos")])

    def test_a_branch_that_is_empty_is_the_default_branch(self):
        for value in ("", "   ", None):
            with self.subTest(value):
                (found,) = self.put(listing(entry(branch=value)))
                self.assertNotIn("branch", found)

    def test_roots_are_lower_case_once_each_and_none_when_the_list_is_empty(self):
        (found,) = self.put(listing(entry(roots=[ROOT_COMMIT.upper(), ROOT_COMMIT, "A" * 40])))
        self.assertEqual(found["roots"], [ROOT_COMMIT, "a" * 40])
        (found,) = self.put(listing(entry(roots=[])))
        self.assertNotIn("roots", found)

    def test_an_address_is_written_the_way_git_is_given_it(self):
        (found,) = self.put(listing(entry(url="HTTPS://Example.Invalid:8443/Team/Demos.git")))
        self.assertEqual(found["url"], gitx.parse_address("https://example.invalid:8443/Team/Demos.git").url)
        self.assertTrue(found["url"].startswith("https://example.invalid:8443/"))

    def test_a_name_and_a_description_are_text_for_a_person_and_never_remove_the_entry(self):
        (found,) = self.put(listing(entry(name="  Grаvi\x00tee \n demos‮  ", description="a\tb\x1b[0m" + "c" * 200)))
        self.assertEqual(found["name"], "Grаvitee demos")
        self.assertEqual(found["description"], "a b[0m" + "c" * (defaults.DESCRIPTION_LIMIT - 6))
        for value in (5, None, [], {}, True, "", " \n"):
            with self.subTest(value):
                (found,) = self.put(listing(entry("demos", name=value, description=value)))
                self.assertEqual((found["id"], found["name"], found["description"]), ("demos", "demos", ""))
        (found,) = self.put(listing(entry(name="x" * 500)))
        self.assertEqual(len(found["name"]), defaults.NAME_LIMIT)

    def test_keys_it_does_not_know_are_not_kept(self):
        (found,) = self.put(listing(entry(host=True, listed=True, kind="git", extra={"a": 1})))
        self.assertEqual(sorted(found), ["branch", "description", "id", "name", "url"])

    def test_what_a_caller_changes_is_not_what_the_next_read_finds(self):
        self.put(listing(entry(roots=[ROOT_COMMIT])))
        first = defaults.read()
        first[0]["name"], first[0]["roots"] = "Changed", []
        first.append({})
        again = defaults.read()
        self.assertEqual((len(again), again[0]["name"], again[0]["roots"]), (1, "Demos", [ROOT_COMMIT]))

    def test_the_file_is_read_again_when_it_changes_and_not_otherwise(self):
        self.put(listing(entry("one")))
        with mock.patch("builtins.open", side_effect=AssertionError("read again")):
            self.assertEqual([item["id"] for item in defaults.read()], ["one"])
        self.assertEqual([item["id"] for item in self.put(listing(entry("one"), entry("another")))], ["one", "another"])
        (self.root / paths.DEFAULT_FILE).unlink()
        self.assertEqual(defaults.read(), [])

    def test_reading_needs_neither_the_network_nor_a_command(self):
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network")), \
                mock.patch.object(subprocess, "Popen", side_effect=AssertionError("command")):
            self.assertEqual([item["id"] for item in self.put(listing(entry()))], ["demos"])
            self.assertEqual([row["id"] for row in defaults.not_added()], ["demos"])


class Trust(Case):
    """listed() and effective(): computed from the file, never from what an entry says of itself."""

    def setUp(self):
        self.folder(paths)
        self.write(paths.DEFAULT_FILE, listing(entry("one", url="https://example.invalid/team/one.git", branch="trunk"),
                                               entry("two", url="https://example.invalid/team/two.git", branch=None)))

    def test_listed_is_the_id_and_the_kind_of_the_entry(self):
        for kind in ("git", "folder", "suggested"):
            self.assertTrue(defaults.listed({"id": "one", "kind": kind}), kind)
            self.assertTrue(defaults.listed({"id": "two", "kind": kind}), kind)
            self.assertFalse(defaults.listed({"id": "three", "kind": kind}), kind)
        for other in ("machine", "", "listed", None, 1, ["git"], {}):
            self.assertFalse(defaults.listed({"id": "one", "kind": other}), other)
        self.assertFalse(defaults.listed({"id": "one"}))

    def test_a_flag_in_an_entry_cannot_make_it_listed(self):
        forged = {"id": "three", "kind": "git", "listed": True, "host": True, "builtin": True, "default": True,
                  "url": "https://example.invalid/team/one.git", "branch": "trunk", "added": "2026-01-01T00:00:00Z"}
        self.assertFalse(defaults.listed(forged))
        self.assertEqual(defaults.effective(forged), ("https://example.invalid/team/one.git", "trunk"))
        # a listed entry is as listed as the file says, with or without the flags
        self.assertTrue(defaults.listed({"id": "one", "kind": "git", "listed": False, "host": False}))

    def test_a_listed_git_entry_is_read_from_the_address_of_the_file_whatever_it_stores(self):
        stored = {"id": "one", "kind": "git", "url": "https://example.invalid/other/place.git", "branch": "old"}
        self.assertEqual(defaults.effective(stored), ("https://example.invalid/team/one.git", "trunk"))
        # a file that gives no branch asks for the default one, not for the one that was stored
        self.assertEqual(defaults.effective({**stored, "id": "two"}), ("https://example.invalid/team/two.git", None))
        # the file is the source: a later engine that moves the repository reaches the next read
        self.write(paths.DEFAULT_FILE,
                   listing(entry("one", url="https://example.invalid/moved/one-2.git", branch="v2")))
        self.assertEqual(defaults.effective(stored), ("https://example.invalid/moved/one-2.git", "v2"))

    def test_an_entry_that_is_not_listed_or_not_a_download_keeps_its_own_address(self):
        own = {"url": "https://example.invalid/mine.git", "branch": "dev"}
        mine = ("https://example.invalid/mine.git", "dev")
        self.assertEqual(defaults.effective({"id": "three", "kind": "git", **own}), mine)
        self.assertEqual(defaults.effective({"id": "one", "kind": "folder", **own}), mine)
        self.assertEqual(defaults.effective({"id": "one", "kind": "folder", "path": "/x"}), (None, None))
        self.assertEqual(defaults.effective({"id": "three", "kind": "git"}), (None, None))

    def test_nothing_malformed_raises(self):
        for value in (None, 1, "one", [], ["one"], {}, {"id": None}, {"id": ["one"], "kind": "git"},
                      {"id": {}, "kind": ["git"]}, {"id": "one", "kind": {"a": 1}}, {"id": 1.5, "kind": "git"},
                      {"id": "one", "kind": "git", "url": 3, "branch": []}):
            with self.subTest(value):
                self.assertIsInstance(defaults.listed(value), bool)
                url, branch = defaults.effective(value)
                self.assertTrue(url is None or isinstance(url, str))
                self.assertTrue(branch is None or isinstance(branch, str))
        self.assertEqual(defaults.effective({"id": "three", "kind": "git", "url": 3, "branch": []}), (None, None))

    def test_with_no_usable_file_nothing_is_listed(self):
        (self.root / paths.DEFAULT_FILE).write_text("{", encoding="utf-8")
        self.assertFalse(defaults.listed({"id": "one", "kind": "git"}))
        self.assertEqual(defaults.effective({"id": "one", "kind": "git", "url": "https://example.invalid/a/b.git"}),
                         ("https://example.invalid/a/b.git", None))


class NotAdded(Case):
    """not_added(): the listed catalogs that .lab/catalogs.json does not hold, as rows."""

    def setUp(self):
        self.folder(paths)
        self.write(paths.DEFAULT_FILE, listing(entry("one", branch="trunk"), entry("two", branch=None)))

    def config(self, *ids, **fields):
        """.lab/catalogs.json with an entry of the registry's shape for each id (a damaged one when `fields` break it)."""
        entries = [{"id": uid, "kind": "git", "url": f"https://example.invalid/team/{uid}.git", **fields}
                   for uid in ids]
        self.write(".lab/catalogs.json", json.dumps({"schema": 1, "catalogs": entries}))

    def ids(self, **how):
        return [row["id"] for row in defaults.not_added(**how)]

    def test_with_no_catalogs_json_every_listed_catalog_is_not_added(self):
        self.assertEqual(self.ids(), ["one", "two"])

    def test_a_row_is_a_suggestion_of_a_listed_catalog_with_its_address(self):
        one, two = defaults.not_added()
        self.assertEqual(one, {"id": "one", "kind": "suggested", "listed": True, "name": "Demos",
                               "description": "Playgrounds of the demos", "url": "https://example.invalid/team/one.git",
                               "branch": "trunk"})
        self.assertNotIn("branch", two)

    def test_a_catalog_that_is_added_is_not_a_row_and_one_that_is_not_added_is(self):
        self.config("two")
        self.assertEqual(self.ids(), ["one"])
        self.config("one", "two", "mine")
        self.assertEqual(self.ids(), [])

    def test_a_folder_that_stands_in_for_a_listed_catalog_counts_as_added(self):
        folder = {"id": "one", "kind": "folder", "path": "/x"}
        self.write(".lab/catalogs.json", json.dumps({"schema": 1, "catalogs": [folder]}))
        self.assertEqual(self.ids(), ["two"])

    def test_the_caller_may_say_what_it_has_read(self):
        defaults.read()
        with mock.patch("builtins.open", side_effect=AssertionError("catalogs.json read")):
            self.assertEqual(self.ids(added=["one"]), ["two"])
            self.assertEqual(self.ids(added=iter(["one", "two", None, 5, ["x"]])), [])
            self.assertEqual(self.ids(added=[]), ["one", "two"])

    def test_a_catalogs_json_that_cannot_be_read_adds_nothing(self):
        for text in ("{", "[]", "null", '{"catalogs": 5}', '{"catalogs": [5, null, {"id": 3}, {"id": ["one"]}]}',
                     "x" * 10, ""):
            with self.subTest(text[:20]):
                self.write(".lab/catalogs.json", text)
                self.assertEqual(self.ids(), ["one", "two"])
        (self.root / ".lab/catalogs.json").unlink()
        (self.root / ".lab/catalogs.json").mkdir()
        self.assertEqual(self.ids(), ["one", "two"])

    def test_what_is_added_is_what_the_registry_holds(self):
        # An entry the registry drops is no catalog: the listed one it stood for is offered again, so that Add can
        # put it right (the row is the reset)
        damaged = ({"id": "one", "kind": "bogus"}, {"id": "one"}, {"id": "one", "kind": "git"},
                   {"id": "one", "kind": "folder"}, {"id": "one", "kind": "git", "url": 5},
                   {"id": "one", "kind": "folder", "path": "relative/folder"}, {"id": "ONE", "kind": "git", "url": "x"},
                   {"id": 1, "kind": "git", "url": "x"}, ["one"], "one", None)
        for item in damaged:
            with self.subTest(item=item):
                self.write(".lab/catalogs.json", json.dumps({"schema": 1, "catalogs": [item]}))
                self.assertEqual(registry.entries(), [])
                self.assertEqual(self.ids(), ["one", "two"])
        for text in ('{"schema": 2, "catalogs": [{"id": "one", "kind": "git", "url": "https://example.invalid/o.git"}]}',
                     '{"schema": "1", "catalogs": []}', '{"catalogs": {"one": 1}}'):
            with self.subTest(text=text):
                self.write(".lab/catalogs.json", text)
                self.assertEqual(self.ids(), ["one", "two"])
        # One entry that is damaged does not take the others with it
        self.write(".lab/catalogs.json", json.dumps({"schema": 1, "catalogs": [
            {"id": "one", "kind": "git"}, {"id": "two", "kind": "git", "url": "https://example.invalid/team/two.git"}]}))
        self.assertEqual(self.ids(), ["one"])

    def test_what_the_registry_holds_is_added_whatever_the_file_says_of_its_kind(self):
        # The registry reads the kind of an entry that does not say it from what it has: a url or a path
        self.write(".lab/catalogs.json", json.dumps({"schema": 1, "catalogs": [
            {"id": "one", "url": "https://example.invalid/team/one.git"}, {"id": "two", "path": str(self.root)}]}))
        self.assertEqual([one["id"] for one in registry.entries()], ["one", "two"])
        self.assertEqual(self.ids(), [])

    def test_the_page_offers_the_listed_catalog_whose_entry_is_damaged(self):
        # The rows of the Catalogs page: the same answer from the registry that the page is drawn from
        self.write(".lab/catalogs.json", json.dumps({"schema": 1, "catalogs": [{"id": "one", "kind": "bogus"}]}))
        with mock.patch.dict(os.environ, {"LAB_CATALOGS_FILE": str(self.root / "no-suggestions.json")}):
            self.assertEqual([row["id"] for row in registry.suggestions()], ["one", "two"])
            self.config("one")
            self.assertEqual([row["id"] for row in registry.suggestions()], ["two"])

    def test_a_registry_that_cannot_answer_adds_nothing(self):
        for failure in (OSError("gone"), ValueError("bad"), RecursionError(), KeyError("x"), RuntimeError("no")):
            with self.subTest(failure=failure), mock.patch.object(registry, "entries", side_effect=failure):
                self.assertEqual(self.ids(), ["one", "two"])
        for answer in (None, 5, [None, 5, {}, {"id": 3}, "one"], {"one": 1}):
            with self.subTest(answer=answer), mock.patch.object(registry, "entries", return_value=answer):
                self.assertEqual(self.ids(), ["one", "two"])
        with mock.patch.object(registry, "entries", return_value=[{"id": "one", "kind": "git"}]):
            self.assertEqual(self.ids(), ["two"])

    def test_a_project_with_no_default_file_has_nothing_to_suggest(self):
        (self.root / paths.DEFAULT_FILE).unlink()
        self.assertEqual(defaults.not_added(), [])


if __name__ == "__main__":
    unittest.main()
