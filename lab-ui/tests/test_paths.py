"""paths: what a file is from its folder, the ids on disk, what belongs to this machine, and a file written whole."""

import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

from lab_ui import paths
from lab_ui.catalogs import registry

from . import Case


class Units(Case):
    """No default file: the layout before catalogs, the one every other suite still writes."""

    def setUp(self):
        self.folder(paths)   # never the checkout, which will have a default file
        self.patch(paths, "_default", (None, None))

    def test_the_folder_decides_what_a_file_is(self):
        for path, unit in (("addons/kafka/compose.yml", ("addon", "kafka")),
                           ("addons/local/whoami/compose.yml", ("addon", "local/whoami")),
                           ("setups/events.yml", ("setup", "events")),
                           ("setups/local/mine.yml", ("setup", "local/mine")),
                           ("playgrounds/welcome/compose.yml", ("playground", "welcome")),
                           ("playgrounds/local/sandbox/compose.yml", ("playground", "local/sandbox")),
                           ("addons/local/local/compose.yml", ("addon", "local/local")),
                           ("addons/kafka/.env", None), ("addons/My_Addon/compose.yml", None),
                           ("addons/local/whoami/more/compose.yml", None), ("addons/local/../kafka/compose.yml", None),
                           ("setups/local/mine/more.yml", None), ("playgrounds/_scratch/compose.yml", None),
                           ("docker-compose.yml", None)):
            with self.subTest(path=path):
                self.assertEqual(paths.unit_of(path), unit)
                if unit:
                    self.assertEqual(paths.unit_file(*unit), path)

    def test_local_is_the_folder_of_this_machine_not_an_add_on_or_a_playground(self):
        self.assertIsNone(paths.unit_of("addons/local/compose.yml"))
        self.assertIsNone(paths.unit_of("playgrounds/local/compose.yml"))
        # A file, not a folder: it is a setup, whose name `make check` refuses
        self.assertEqual(paths.unit_of("setups/local.yml"), ("setup", "local"))

    def test_what_belongs_to_this_machine(self):
        self.assertEqual([paths.personal(uid) for uid in ("local/whoami", "whoami", "local", "localhost")],
                         [True, False, False, False])
        self.assertEqual(paths.unit_env("addon", "local/whoami"), "addons/local/whoami/.env")
        self.assertEqual(paths.unit_env("addon", "kafka"), "addons/kafka/.env")
        self.assertEqual(paths.unit_env("playground", "local/sandbox"), "playgrounds/local/sandbox/.env")
        self.assertIsNone(paths.unit_env("setup", "local/mine"))

    def test_a_unit_folder_holds_its_files(self):
        self.assertEqual([paths.unit_folder(kind, uid) for kind, uid in (
            ("addon", "kafka"), ("playground", "local/sandbox"), ("setup", "events"))],
            ["addons/kafka", "playgrounds/local/sandbox", None])
        self.assertEqual(paths.unit_guide("playground", "local/sandbox"), "playgrounds/local/sandbox/guide.md")
        self.assertIsNone(paths.unit_guide("addon", "kafka"))

    def test_the_ids_found_on_disk(self):
        for file in ("addons/kafka/compose.yml", "addons/local/whoami/compose.yml", "addons/local/compose.yml",
                     "addons/local/.hidden/compose.yml", "addons/jade/notes.md", "setups/events.yml",
                     "setups/local/mine.yml", "setups/AGENTS.md", "playgrounds/welcome/compose.yml",
                     "playgrounds/_scratch/compose.yml", "playgrounds/local/sandbox/compose.yml",
                     "playgrounds/local/sandbox/guide.md"):
            self.write(file)
        self.assertEqual({kind: paths.unit_ids(kind) for kind in paths.KINDS},
                         {"addon": ["kafka", "local/whoami"], "setup": ["events", "local/mine"],
                          "playground": ["local/sandbox", "welcome"]})
        self.assertEqual(paths.unit_files(), [
            "addons/kafka/compose.yml", "addons/local/whoami/compose.yml", "setups/events.yml",
            "setups/local/mine.yml", "playgrounds/local/sandbox/compose.yml", "playgrounds/welcome/compose.yml"])


class SeedApis(Case):

    def found(self, root):
        return [file.relative_to(root).as_posix() for file in paths.seed_apis(root)]

    def test_the_apis_of_a_seed_are_found_in_the_platforms_folder_and_in_each_environment(self):
        root = self.folder(files=dict.fromkeys((
            "seed/apim/apis/b.json", "seed/apim/apis/a.json", "seed/apim/apis/notes.txt", "seed/apim/settings.yaml",
            "seed/environments/novo-nordisk/apis/batch-status.json", "seed/environments/beam/apis/sample-requests.json",
            "seed/environments/beam/applications/app.yaml", "seed/environments/beam/portal/apis/deeper.json",
            "seed/environments/beam/apis/nested/deeper.json", "seed/am/apis/z.json"), ""))
        self.assertEqual(self.found(root), [
            "seed/am/apis/z.json", "seed/apim/apis/a.json", "seed/apim/apis/b.json",
            "seed/environments/beam/apis/sample-requests.json", "seed/environments/novo-nordisk/apis/batch-status.json"])

    def test_a_folder_without_a_seed_has_no_api(self):
        root = self.folder(files={"compose.yml": "", "seed/controls.yaml": ""})
        self.assertEqual(self.found(root), [])
        self.assertEqual(self.found(root / "gone"), [])


class Isolation(Case):
    """The day the checkout has its catalogs.default.json, no test of this module may notice."""

    def test_no_test_reads_the_project_it_runs_in(self):
        self.folder(paths)   # stands for the checkout, with the file it will have
        self.write(paths.DEFAULT_FILE, json.dumps({"schema": 1, "catalogs": [{"id": "checkout-catalog"}]}))
        self.patch(paths, "_default", (None, None))
        cases = [case for case in vars(sys.modules[__name__]).values()
                 if isinstance(case, type) and issubclass(case, unittest.TestCase) and case is not Isolation]
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case) for case in cases)
        result = unittest.TestResult()
        suite.run(result)
        self.assertGreater(result.testsRun, 60)
        self.assertEqual(result.testsRun, suite.countTestCases())
        self.assertEqual([str(case) for case, _ in result.failures + result.errors], [])


class OnDisk(Case):
    """A temporary project whose default catalog is `gravitee`, and the means to put units in it."""

    def setUp(self):
        self.folder(paths)
        self.patch(paths, "_default", (None, None))   # what a previous test read must not answer for this one
        self.default("gravitee")

    def default(self, *namespaces):
        """The default file lists these catalogs, the first being the one that owns the bare ids."""
        entries = [{"id": ns, "name": "A catalog", "url": "https://example.invalid/catalog.git"} for ns in namespaces]
        self.write(paths.DEFAULT_FILE, json.dumps({"schema": 1, "catalogs": entries}))

    def put(self, *files):
        for file in files:
            self.write(f".lab/catalogs/{file}")


class Grammar(OnDisk):

    ADVERSARIAL = (
        "@acme-hub/../kafka", "@acme-hub/", "@/x", "@a/x", "@acme-hub/x/y", "@local/x", "@official/x", "@acme_hub/x",
        "@ACME/x", "local/../kafka", "local/", "kafka\0", "ka\0fka", "@acme-hub/x\0", "local/x\0", "kafka\n", "\nkafka",
        "@acme-hub/x\n", "local/x\n", "kafka:x", "@acme-hub/x:y", "local/x:y", "playground:kafka",
        # Further shapes of the same mistakes
        "", " kafka", "kafka ", "Kafka", "LOCAL/x", "local/X", "local//x", "local/x/y", "/kafka", "kafka/", "//", "@",
        "@acme-hub", "@@acme-hub/x", "@acme-hub//x", "@acme-hub/X", "@-ab/x", "@ab-/x", "@1ab/x", f"@{'a' * 25}/x",
        "@acmé/x", "kafkä", "ｋafka", "../kafka", "kafka/..", "@acme-hub/..", "@acme-hub/.", ".kafka",
        "kafka.yml", "kafka\\x", "a b", "a\tb", "~", "${HOME}", "%2e%2e", None, 5, b"kafka", ["kafka"])
    GLOBS = ("*", "@acme-hub/*", "local/*")   # not ids, but what unit_file turns into the listing of a catalog

    def test_an_id_is_bare_personal_or_of_a_catalog(self):
        for uid in ("kafka", "a", "-", "0", "mock-bank", "local", "local/whoami", "local/local", "local/a-b",
                    "@acme-hub/mock-bank", "@acme-hub/local", "@ab/x", f"@{'a' * 24}/x", "@a1/x", "@a-b/x",
                    "@acme-hub/-"):
            with self.subTest(uid=uid):
                self.assertTrue(paths.is_id(uid))

    def test_every_adversarial_id_is_refused(self):
        for default in (("gravitee",), ("demos",)):
            self.default(*default)
            for uid in self.ADVERSARIAL + self.GLOBS:
                with self.subTest(default=default, uid=uid):
                    self.assertFalse(paths.is_id(uid))
                    self.assertFalse(paths.is_id(paths.canonical(uid)))

    def test_the_default_catalog_is_not_spelled_with_its_namespace(self):
        self.assertFalse(paths.is_id("@gravitee/kafka"))
        self.default("demos")
        self.assertFalse(paths.is_id("@demos/kafka"))
        self.assertTrue(paths.is_id("@gravitee-hub/kafka"))

    def test_a_reserved_namespace_is_no_id_whoever_asks(self):
        os.remove(self.root / paths.DEFAULT_FILE)
        for ns in sorted(paths.RESERVED_NS):
            with self.subTest(ns=ns):
                self.assertFalse(paths.is_id(f"@{ns}/x"))
        self.assertTrue(paths.RESERVED_NS >= {"local", "gravitee", "official", "lab", "core", "builtin"})

    def test_a_namespace_of_another_catalog_needs_no_default_file(self):
        os.remove(self.root / paths.DEFAULT_FILE)
        self.assertTrue(paths.is_id("@acme-hub/bank"))
        self.assertFalse(paths.is_id("@acme-hub/../bank"))

    def test_a_bare_id_is_still_an_id_before_catalogs(self):
        # The transition: the layout before catalogs serves a bare id until there is no such layout
        os.remove(self.root / paths.DEFAULT_FILE)
        self.assertTrue(paths.is_id("kafka"))
        self.assertTrue(paths.is_id("local/whoami"))

    def test_the_pattern_of_a_route_has_one_group(self):
        self.assertEqual(re.compile(paths.ID).groups, 1)
        self.assertEqual(paths.ID, "(local/[a-z0-9-]+|@[a-z][a-z0-9-]{0,22}[a-z0-9]/[a-z0-9-]+"
                                   "|(?!local/)(?!@)[a-z0-9-]+)")
        route = re.compile(rf"/api/(addon|setup|playground)s/{paths.ID}/(start|stop)")
        for path, groups in (("/api/addons/kafka/start", ("addon", "kafka", "start")),
                             ("/api/playgrounds/local/demo/stop", ("playground", "local/demo", "stop")),
                             ("/api/playgrounds/@acme-hub/bank/start", ("playground", "@acme-hub/bank", "start")),
                             ("/api/setups/@a-b/c-d/stop", ("setup", "@a-b/c-d", "stop"))):
            with self.subTest(path=path):
                self.assertEqual(route.fullmatch(path).groups(), groups)
        for path in ("/api/addons/@acme-hub/../kafka/start", "/api/addons/@x/y/start",
                     "/api/addons/@acme-hub/b/c/start", "/api/addons/local/start", "/api/addons/local//x/start",
                     "/api/addons/@acme-hub/x\n/start"):
            with self.subTest(path=path):
                self.assertIsNone(route.fullmatch(path))

    def test_the_old_patterns_are_unchanged(self):
        # The layout before catalogs reads files with the id of before: no @ in them
        self.assertEqual({kind: pattern.pattern for kind, (_, pattern) in paths.UNITS.items()}, {
            "addon": r"addons/(local/[a-z0-9-]+|(?!local/)[a-z0-9-]+)/compose\.yml",
            "setup": r"setups/(local/[a-z0-9-]+|(?!local/)[a-z0-9-]+)\.yml",
            "playground": r"playgrounds/(local/[a-z0-9-]+|(?!local/)[a-z0-9-]+)/compose\.yml"})
        self.assertEqual(paths.KINDS, ("addon", "setup", "playground"))


class DefaultNamespace(OnDisk):

    def test_the_first_entry_owns_the_bare_ids(self):
        self.default("gravitee", "second-catalog")
        self.assertEqual(paths.default_ns(), "gravitee")
        self.default("demos", "gravitee")
        self.assertEqual(paths.default_ns(), "demos")

    def test_no_file_no_default(self):
        os.remove(self.root / paths.DEFAULT_FILE)
        self.assertIsNone(paths.default_ns())

    def test_the_real_file_is_read_by_this_one_reader(self):
        self.assertEqual((paths.DEFAULT_FILE, paths.ROOT), ("catalogs.default.json", self.root))
        self.assertEqual(paths.default_ns(), "gravitee")

    def test_whatever_is_not_a_usable_file_is_no_default(self):
        entry = {"id": "gravitee", "name": "n", "url": "https://example.invalid/c.git"}
        for label, text in (
                ("empty", ""), ("not json", "catalogs"), ("a list", "[]"), ("null", "null"), ("a string", '"gravitee"'),
                ("a truncated file", '{"schema": 1, "catalogs": [{"id": "grav'),
                ("no schema", json.dumps({"catalogs": [entry]})),
                ("an unknown schema", json.dumps({"schema": 2, "catalogs": [entry]})),
                ("a schema that is true", json.dumps({"schema": True, "catalogs": [entry]})),
                ("a schema in text", json.dumps({"schema": "1", "catalogs": [entry]})),
                ("a schema that is a float", json.dumps({"schema": 1.5, "catalogs": [entry]})),
                ("no catalogs", json.dumps({"schema": 1})),
                ("catalogs not a list", json.dumps({"schema": 1, "catalogs": entry})),
                ("no entry", json.dumps({"schema": 1, "catalogs": []})),
                ("an entry that is not an object", json.dumps({"schema": 1, "catalogs": ["gravitee"]})),
                ("an entry with no id", json.dumps({"schema": 1, "catalogs": [{"name": "n"}]})),
                ("an id that is not text", json.dumps({"schema": 1, "catalogs": [{"id": 7}]})),
                ("an id with a capital", json.dumps({"schema": 1, "catalogs": [{"id": "Gravitee"}]})),
                ("an id too short", json.dumps({"schema": 1, "catalogs": [{"id": "g"}]})),
                ("an id too long", json.dumps({"schema": 1, "catalogs": [{"id": "a" * 25}]})),
                ("an id that starts with a dash", json.dumps({"schema": 1, "catalogs": [{"id": "-ab"}]})),
                ("an id with a slash", json.dumps({"schema": 1, "catalogs": [{"id": "a/../b"}]})),
                ("an id with a newline", json.dumps({"schema": 1, "catalogs": [{"id": "gravitee\n"}]})),
                ("the namespace of this machine", json.dumps({"schema": 1, "catalogs": [{"id": "local"}]})),
                ("a damaged first entry before a good one",
                 json.dumps({"schema": 1, "catalogs": [{"id": "G!"}, entry]})),
                ("a nest that is too deep", "[" * 100000)):
            with self.subTest(label):
                self.write(paths.DEFAULT_FILE, text)
                self.assertIsNone(paths.default_ns())

    def test_a_file_that_is_not_text_is_no_default(self):
        (self.root / paths.DEFAULT_FILE).write_bytes(b'{"schema": 1, "catalogs": [{"id": "grav\xff\xfeitee"}]}')
        self.assertIsNone(paths.default_ns())

    def test_a_byte_order_mark_is_tolerated(self):
        entries = {"schema": 1, "catalogs": [{"id": "gravitee"}]}
        (self.root / paths.DEFAULT_FILE).write_bytes(b"\xef\xbb\xbf" + json.dumps(entries).encode("utf-8"))
        self.assertEqual(paths.default_ns(), "gravitee")

    def test_a_file_over_64_kilobytes_is_no_default(self):
        entries = json.dumps({"schema": 1, "catalogs": [{"id": "gravitee"}]})
        self.write(paths.DEFAULT_FILE, entries + " " * ((1 << 16) - len(entries)))   # as big as it may be
        self.assertEqual(paths.default_ns(), "gravitee")
        self.write(paths.DEFAULT_FILE, entries + " " * ((1 << 16) - len(entries) + 1))
        self.assertIsNone(paths.default_ns())

    def test_a_number_of_a_million_digits_does_not_hold_up_the_reader(self):
        # Before Python 3.11 reading one takes seconds; the size limit keeps it to a few milliseconds
        self.write(paths.DEFAULT_FILE, '{"schema": 1, "catalogs": [{"id": "gravitee"}], "more": ' + "9" * 900000 + "}")
        started = time.perf_counter()
        self.assertIsNone(paths.default_ns())
        self.assertLess(time.perf_counter() - started, 1)

    def test_what_is_not_a_regular_file_is_never_opened(self):
        os.remove(self.root / paths.DEFAULT_FILE)
        os.mkdir(self.root / paths.DEFAULT_FILE)
        self.assertIsNone(paths.default_ns())
        os.rmdir(self.root / paths.DEFAULT_FILE)
        if not hasattr(os, "mkfifo"):
            self.skipTest("no named pipes on this system")
        os.mkfifo(self.root / paths.DEFAULT_FILE)   # opening it would wait for a writer for ever
        self.assertIsNone(paths.default_ns())

    def test_a_link_to_the_file_is_followed(self):
        target = self.write("elsewhere.json", (self.root / paths.DEFAULT_FILE).read_text(encoding="utf-8"))
        os.remove(self.root / paths.DEFAULT_FILE)
        os.symlink(self.root / target, self.root / paths.DEFAULT_FILE)
        self.assertEqual(paths.default_ns(), "gravitee")

    def test_the_file_is_read_again_only_when_it_changes(self):
        read = self.patch(paths, "_read_default", wraps=paths._read_default)
        self.assertEqual([paths.default_ns() for _ in range(3)], ["gravitee"] * 3)
        self.assertEqual(read.call_count, 1)
        file = self.root / paths.DEFAULT_FILE
        before = file.stat().st_mtime_ns
        self.default("gravitex")   # as long as it was, in the same file: only the modified time tells
        os.utime(file, ns=(before + 5_000_000_000, before + 5_000_000_000))
        self.assertEqual([paths.default_ns() for _ in range(3)], ["gravitex"] * 3)
        self.assertEqual(read.call_count, 2)

    def test_a_file_that_goes_and_comes_back_is_followed(self):
        os.remove(self.root / paths.DEFAULT_FILE)
        self.assertIsNone(paths.default_ns())
        self.default("demos")
        self.assertEqual(paths.default_ns(), "demos")
        os.remove(self.root / paths.DEFAULT_FILE)
        self.assertIsNone(paths.default_ns())

    def test_a_file_replaced_whole_is_followed(self):
        self.assertEqual(paths.default_ns(), "gravitee")
        file = self.root / paths.DEFAULT_FILE
        stamp = file.stat().st_mtime_ns
        paths.write_atomic(file, file.read_text(encoding="utf-8").replace("gravitee", "gravitex"))   # as long, same time
        os.utime(file, ns=(stamp, stamp))
        self.assertEqual(paths.default_ns(), "gravitex")

    def test_another_project_does_not_answer_with_the_cache_of_this_one(self):
        self.assertEqual(paths.default_ns(), "gravitee")
        first, stamp = self.root, (self.root / paths.DEFAULT_FILE).stat().st_mtime_ns
        self.folder(paths)
        self.assertIsNone(paths.default_ns())
        self.default("gravitex")   # as long as the first, written at the same time
        os.utime(self.root / paths.DEFAULT_FILE, ns=(stamp, stamp))
        self.assertEqual(paths.default_ns(), "gravitex")
        self.patch(paths, "ROOT", first)
        self.assertEqual(paths.default_ns(), "gravitee")


class InCatalogs(OnDisk):
    """The layout of 3.1: every unit sits in the folder of a catalog."""

    TABLE = (
        # kind, id, file, folder
        ("addon", "kafka", ".lab/catalogs/gravitee/addons/kafka/compose.yml", ".lab/catalogs/gravitee/addons/kafka"),
        ("addon", "local/whoami", ".lab/catalogs/local/addons/whoami/compose.yml", ".lab/catalogs/local/addons/whoami"),
        ("addon", "@acme-hub/mock-bank", ".lab/catalogs/acme-hub/addons/mock-bank/compose.yml",
         ".lab/catalogs/acme-hub/addons/mock-bank"),
        ("setup", "kubernetes-flow", ".lab/catalogs/gravitee/setups/kubernetes-flow.yml", None),
        ("setup", "local/ai-agents", ".lab/catalogs/local/setups/ai-agents.yml", None),
        ("setup", "@acme-hub/bank-stack", ".lab/catalogs/acme-hub/setups/bank-stack.yml", None),
        ("playground", "acme-bakery", ".lab/catalogs/gravitee/playgrounds/acme-bakery/compose.yml",
         ".lab/catalogs/gravitee/playgrounds/acme-bakery"),
        ("playground", "local/demo", ".lab/catalogs/local/playgrounds/demo/compose.yml",
         ".lab/catalogs/local/playgrounds/demo"),
        ("playground", "@acme-hub/bank", ".lab/catalogs/acme-hub/playgrounds/bank/compose.yml",
         ".lab/catalogs/acme-hub/playgrounds/bank"))

    def test_the_file_and_folder_of_an_id_in_each_catalog(self):
        for kind, uid, file, folder in self.TABLE:
            with self.subTest(kind=kind, uid=uid):
                self.assertEqual(paths.unit_file(kind, uid), file)
                self.assertEqual(paths.unit_folder(kind, uid), folder)

    def test_a_file_gives_back_its_id(self):
        for kind, uid, file, _ in self.TABLE:
            with self.subTest(kind=kind, uid=uid):
                self.assertEqual(paths.unit_of(file), (kind, uid))
                self.assertEqual(paths.unit_of(paths.unit_file(kind, uid)), (kind, uid))

    def test_the_default_namespace_is_read_when_asked(self):
        self.default("demos")
        self.assertEqual(paths.unit_file("addon", "kafka"), ".lab/catalogs/demos/addons/kafka/compose.yml")
        self.assertEqual(paths.unit_of(".lab/catalogs/demos/addons/kafka/compose.yml"), ("addon", "kafka"))
        self.assertIsNone(paths.unit_of(".lab/catalogs/gravitee/addons/kafka/compose.yml"))   # reserved: no id

    def test_the_folder_decides_what_a_file_is(self):
        # The table of today's ids and files, in the folders of a catalog
        for path, unit in (
                (".lab/catalogs/gravitee/addons/kafka/compose.yml", ("addon", "kafka")),
                (".lab/catalogs/local/addons/whoami/compose.yml", ("addon", "local/whoami")),
                (".lab/catalogs/gravitee/setups/events.yml", ("setup", "events")),
                (".lab/catalogs/local/setups/mine.yml", ("setup", "local/mine")),
                (".lab/catalogs/gravitee/playgrounds/welcome/compose.yml", ("playground", "welcome")),
                (".lab/catalogs/local/playgrounds/sandbox/compose.yml", ("playground", "local/sandbox")),
                (".lab/catalogs/local/addons/local/compose.yml", ("addon", "local/local")),
                (".lab/catalogs/gravitee/addons/kafka/.env", None),
                (".lab/catalogs/gravitee/addons/My_Addon/compose.yml", None),
                (".lab/catalogs/local/addons/whoami/more/compose.yml", None),
                (".lab/catalogs/local/addons/../kafka/compose.yml", None),
                (".lab/catalogs/local/setups/mine/more.yml", None),
                (".lab/catalogs/gravitee/playgrounds/_scratch/compose.yml", None),
                ("docker-compose.yml", None)):
            with self.subTest(path=path):
                self.assertEqual(paths.unit_of(path), unit)
                if unit:
                    self.assertEqual(paths.unit_file(*unit), path)

    def test_the_name_of_this_machines_folder_is_a_name_in_a_catalog(self):
        # In the folders of before, local was the folder of this machine and could not name a unit; here the folder of
        # this machine is a catalog, and a unit called local is one like any
        self.assertEqual(paths.unit_of(".lab/catalogs/gravitee/addons/local/compose.yml"), ("addon", "local"))
        self.assertEqual(paths.unit_of(".lab/catalogs/gravitee/setups/local.yml"), ("setup", "local"))
        self.assertEqual(paths.unit_of(".lab/catalogs/acme-hub/playgrounds/local/compose.yml"),
                         ("playground", "@acme-hub/local"))
        self.assertFalse(paths.personal("local"))
        self.assertEqual(paths.unit_file("addon", "local"), ".lab/catalogs/gravitee/addons/local/compose.yml")

    def test_what_has_the_shape_of_a_unit_in_another_place_is_none(self):
        for path in (
                "addons/@x/y/compose.yml", "addons/@xx/y/compose.yml", "addons/x/compose.yml",
                "addons/local/x/compose.yml",
                "setups/x.yml", "playgrounds/x/compose.yml", "addons/kafka/compose.yml",
                ".lab/catalogs/ns/addons/x/certs/compose.yml", ".lab/catalogs/acme-hub/addons/x/certs/compose.yml",
                ".lab/catalogs/.stage-acme-hub/addons/x/compose.yml", ".lab/catalogs/.shadow-acme-hub.json",
                ".lab/catalogs/.removed-acme-hub-20261001/addons/x/compose.yml",
                ".lab/catalogs/acme-hub/addons/x/compose.yml.bak", ".lab/catalogs/acme-hub/addons/x/compose.yaml",
                ".lab/catalogs/acme-hub/addons/x/Compose.yml", ".lab/catalogs/acme-hub/addons/x.yml",
                ".lab/catalogs/acme-hub/addons/compose.yml", ".lab/catalogs/acme-hub/addons//compose.yml",
                ".lab/catalogs/acme-hub/setups/x/y.yml", ".lab/catalogs/acme-hub/setups/x.yaml",
                ".lab/catalogs/acme-hub/setups/.x.yml", ".lab/catalogs/acme-hub/setups/x.yml.tmp",
                ".lab/catalogs/acme-hub/playgrounds/x/y/compose.yml", ".lab/catalogs/acme-hub/other/x/compose.yml",
                ".lab/catalogs/acme-hub/addons/X/compose.yml", ".lab/catalogs/acme-hub/addons/x_y/compose.yml",
                ".lab/catalogs/acme-hub/addons/../compose.yml", ".lab/catalogs/acme-hub/addons/./x/compose.yml",
                ".lab/catalogs/acme-hub/../gravitee/addons/x/compose.yml",
                ".lab/catalogs/Acme-Hub/addons/x/compose.yml",
                ".lab/catalogs/a/addons/x/compose.yml", f".lab/catalogs/{'a' * 25}/addons/x/compose.yml",
                ".lab/catalogs/official/addons/x/compose.yml", ".lab/catalogs/all/setups/x.yml",
                ".lab/catalogs/@acme-hub/addons/x/compose.yml", ".lab/catalogs//acme-hub/addons/x/compose.yml",
                ".lab//catalogs/acme-hub/addons/x/compose.yml", "./.lab/catalogs/acme-hub/addons/x/compose.yml",
                "/.lab/catalogs/acme-hub/addons/x/compose.yml",
                f"{paths.ROOT}/.lab/catalogs/acme-hub/addons/x/compose.yml",
                "lab/catalogs/acme-hub/addons/x/compose.yml", "xlab/catalogs/acme-hub/addons/x/compose.yml",
                ".lab/catalogs/acme-hub/addons/x/compose.yml\n", ".lab/catalogs/acme-hub/addons/x\n/compose.yml",
                ".lab/catalogs/acme-hub/addons/x/compose.yml\0", ".lab/catalogs/acme-hub\\addons\\x\\compose.yml",
                ".lab/catalogs/acme-hub", ".lab/catalogs", ""):
            with self.subTest(path=path):
                self.assertIsNone(paths.unit_of(path))

    def test_a_namespace_that_is_not_the_default_is_the_catalogs_own(self):
        self.assertEqual(paths.unit_of(".lab/catalogs/a-b/addons/x/compose.yml"), ("addon", "@a-b/x"))
        self.assertEqual(paths.unit_of(f".lab/catalogs/{'a' * 24}/setups/x.yml"), ("setup", f"@{'a' * 24}/x"))
        self.assertEqual(paths.unit_of(".lab/catalogs/acme-hub/playgrounds/-/compose.yml"),
                         ("playground", "@acme-hub/-"))

    def test_an_id_that_is_not_one_has_no_file(self):
        for uid in Grammar.ADVERSARIAL + ("@gravitee/kafka", "@gravitee/x", "@local/x", "@official/x"):
            for kind in paths.KINDS:
                with self.subTest(kind=kind, uid=uid):
                    with self.assertRaises(paths.NotFound):
                        paths.unit_file(kind, uid)

    def test_a_wildcard_is_the_listing_of_a_catalog_not_a_name(self):
        # What unit_ids lists with: every unit of the default catalog, every unit of this machine
        self.assertEqual(paths.unit_file("addon", "*"), ".lab/catalogs/gravitee/addons/*/compose.yml")
        self.assertEqual(paths.unit_file("setup", "local/*"), ".lab/catalogs/local/setups/*.yml")
        self.assertEqual(paths.unit_file("playground", "@acme-hub/*"),
                         ".lab/catalogs/acme-hub/playgrounds/*/compose.yml")
        for uid in ("a*", "*a", "**", "@gravitee/*", "@local/*", "x/*", "@acme-hub*", "local*", "*/x"):
            with self.subTest(uid=uid):
                with self.assertRaises(paths.NotFound):
                    paths.unit_file("addon", uid)

    def test_the_names_that_are_no_dot_file_are_a_listing_too(self):
        # What `make check` lists to find the names the Lab UI does not see
        self.assertEqual(paths.unit_file("addon", "[!.]*"), ".lab/catalogs/gravitee/addons/[!.]*/compose.yml")
        self.assertEqual(paths.unit_file("setup", "local/[!.]*"), ".lab/catalogs/local/setups/[!.]*.yml")
        self.assertEqual(paths.unit_file("playground", "@acme-hub/[!.]*"),
                         ".lab/catalogs/acme-hub/playgrounds/[!.]*/compose.yml")
        for uid in ("a[!.]*", "[!.]*a", "[!.]*[!.]*", "[!.]", "[!.]**", "*[!.]*", "@gravitee/[!.]*", "@local/[!.]*",
                    "x/[!.]*", "[!a]*", "[.]*", "[!.]*\n", "local/[!.]*\0", "\n[!.]*"):
            with self.subTest(uid=uid):
                with self.assertRaises(paths.NotFound):
                    paths.unit_file("addon", uid)

    def test_a_bare_name_has_no_place_without_a_default_catalog(self):
        # The guard inside unit_file's reach: it checks the default itself, but a place is never made of None
        for uid in ("kafka", "*", "[!.]*", "local/x", "@acme-hub/x"):
            with self.subTest(uid=uid):
                with self.assertRaises(paths.NotFound):
                    paths._place(uid, None)

    def test_a_kind_that_does_not_exist_is_a_key_error(self):
        with self.assertRaises(KeyError):
            paths.unit_file("playlist", "kafka")
        with self.assertRaises(KeyError):
            paths.ids("playlist")

    def test_the_patterns_and_the_files_of_the_scheme(self):
        self.assertEqual(paths.CATALOG_FILES, {
            "addon": ".lab/catalogs/{ns}/addons/{name}/compose.yml",
            "setup": ".lab/catalogs/{ns}/setups/{name}.yml",
            "playground": ".lab/catalogs/{ns}/playgrounds/{name}/compose.yml"})

    def test_what_belongs_to_this_machine(self):
        ids = ("local/whoami", "whoami", "local", "localhost", "@acme-hub/x")
        self.assertEqual([paths.personal(uid) for uid in ids], [True, False, False, False, False])
        self.assertEqual(paths.unit_env("addon", "local/whoami"), ".lab/catalogs/local/addons/whoami/.env")
        self.assertEqual(paths.unit_env("addon", "kafka"), ".lab/catalogs/gravitee/addons/kafka/.env")
        self.assertEqual(paths.unit_env("playground", "@acme-hub/bank"), ".lab/catalogs/acme-hub/playgrounds/bank/.env")
        self.assertEqual(paths.unit_env("playground", "local/sandbox"), ".lab/catalogs/local/playgrounds/sandbox/.env")
        self.assertIsNone(paths.unit_env("setup", "local/mine"))
        self.assertEqual(paths.unit_guide("playground", "local/sandbox"),
                         ".lab/catalogs/local/playgrounds/sandbox/guide.md")
        self.assertEqual(paths.unit_guide("playground", "acme-bakery"),
                         ".lab/catalogs/gravitee/playgrounds/acme-bakery/guide.md")
        self.assertIsNone(paths.unit_guide("addon", "kafka"))
        self.assertIsNone(paths.unit_folder("setup", "events"))


class Names(OnDisk):

    def test_where_an_item_sits(self):
        for uid, ns, name, default, _ in (
                ("kafka", "gravitee", "kafka", True, True), ("local/whoami", "local", "whoami", False, False),
                ("@acme-hub/bank", "acme-hub", "bank", False, True), ("a-b", "gravitee", "a-b", True, True),
                ("local/local", "local", "local", False, False), ("local", "gravitee", "local", True, True)):
            with self.subTest(uid=uid):
                self.assertEqual(paths.split_ref(uid), (ns, name))
                self.assertEqual(paths.origin(uid), ns)
                self.assertEqual(paths.is_default(uid), default)

    def test_the_long_form_of_the_default_catalog_reads_as_the_bare_id(self):
        self.assertEqual(paths.canonical("@gravitee/kafka"), "kafka")
        self.assertEqual(paths.split_ref("@gravitee/kafka"), ("gravitee", "kafka"))
        self.assertEqual(paths.origin("@gravitee/kafka"), "gravitee")
        for uid in ("kafka", "local/kafka", "@acme-hub/kafka", "@gravitee/", "@gravitee/a/b", "@gravitee/../x",
                    "@gravitee-x/y", "@graviteex/y", "@gravite/y", "gravitee/kafka", "@gravitee/Kafka",
                    "@gravitee/kafka\n", "", "*"):
            with self.subTest(uid=uid):
                self.assertEqual(paths.canonical(uid), uid)
        self.assertEqual(paths.canonical(None), None)
        self.default("demos")
        self.assertEqual(paths.canonical("@demos/kafka"), "kafka")
        self.assertEqual(paths.canonical("@gravitee/kafka"), "@gravitee/kafka")

    def test_what_is_not_an_id_has_no_origin(self):
        for uid in Grammar.ADVERSARIAL + ("@local/x", "@official/x"):
            with self.subTest(uid=uid):
                with self.assertRaises(paths.NotFound):
                    paths.split_ref(uid)
                with self.assertRaises(paths.NotFound):
                    paths.origin(uid)

    def test_a_bare_id_has_no_namespace_without_a_default_catalog(self):
        os.remove(self.root / paths.DEFAULT_FILE)
        self.assertEqual(paths.split_ref("kafka"), (None, "kafka"))
        self.assertEqual(paths.split_ref("local/x"), ("local", "x"))
        self.assertEqual(paths.canonical("@gravitee/kafka"), "@gravitee/kafka")

    def test_the_default_file_is_looked_at_once_per_question(self):
        # Two looks could see two files: the answer of one call is about one project
        seen = self.patch(paths, "default_ns", wraps=paths.default_ns)
        for label, call in (
                ("a bare id's file", lambda: paths.unit_file("addon", "kafka")),
                ("a personal id's file", lambda: paths.unit_file("addon", "local/x")),
                ("a catalog id's file", lambda: paths.unit_file("addon", "@acme-hub/x")),
                ("a listing", lambda: paths.unit_file("addon", "*")),
                ("a listing of the names that are no dot file", lambda: paths.unit_file("setup", "@acme-hub/[!.]*")),
                ("a unit's folder", lambda: paths.unit_folder("playground", "kafka")),
                ("a unit's env", lambda: paths.unit_env("addon", "@acme-hub/x")),
                ("the unit of a path", lambda: paths.unit_of(".lab/catalogs/gravitee/addons/x/compose.yml")),
                ("a place", lambda: paths.split_ref("kafka")), ("a long form", lambda: paths.split_ref("@gravitee/x")),
                ("an origin", lambda: paths.origin("@acme-hub/x")), ("a canonical form", lambda: paths.canonical("x")),
                ("whether a long form is an id", lambda: paths.is_id("@acme-hub/x"))):
            with self.subTest(label):
                seen.reset_mock()
                call()
                self.assertEqual(seen.call_count, 1)

    def test_a_default_file_that_goes_while_a_path_is_made_never_makes_a_path_of_none(self):
        answers = iter(["gravitee", None, None, None])
        self.patch(paths, "default_ns", lambda: next(answers))
        self.assertEqual(paths.unit_file("addon", "kafka"), ".lab/catalogs/gravitee/addons/kafka/compose.yml")

    def test_only_what_is_shaped_like_a_bare_id_is_default(self):
        for uid in ("local/x", "@acme-hub/x", "", "a b", "x/y", "x\n", None, 5):
            with self.subTest(uid=uid):
                self.assertFalse(paths.is_default(uid))
        self.assertTrue(paths.is_default("x"))


class Listing(OnDisk):
    """`names(ns, kind)`: what one folder of `.lab/catalogs` holds of a kind, by listing it. It is how a folder catalog, this
    machine's own and a staged tree (`.stage-<ns>`, which no id names) are read; the registry and the judgement of a stage use
    it by this name."""

    def test_the_names_of_a_kind_are_those_of_the_folders_with_a_compose_file_and_of_the_setup_files_sorted(self):
        self.put("acme/addons/b/compose.yml", "acme/addons/a/compose.yml", "acme/addons/no-file/README.md",
                 "acme/playgrounds/p/compose.yml", "acme/setups/s.yml", "acme/setups/t.yml", "acme/setups/notes.md")
        self.assertEqual(paths.names("acme", "addon"), ["a", "b"])
        self.assertEqual(paths.names("acme", "playground"), ["p"])
        self.assertEqual(paths.names("acme", "setup"), ["s", "t"])
        self.assertEqual([paths.names("acme", kind) for kind in paths.KINDS], [["a", "b"], ["s", "t"], ["p"]])

    def test_a_stage_and_the_machines_own_folder_are_listed_like_any_other(self):
        self.put(".stage-acme/addons/x/compose.yml", "local/addons/mine/compose.yml", "local/setups/mix.yml")
        self.assertEqual(paths.names(".stage-acme", "addon"), ["x"])
        self.assertEqual((paths.names("local", "addon"), paths.names("local", "setup")), (["mine"], ["mix"]))

    def test_what_is_not_the_name_of_a_unit_is_not_listed(self):
        self.put("acme/addons/UPPER/compose.yml", "acme/addons/under_score/compose.yml", "acme/addons/.hidden/compose.yml",
                 "acme/addons/with space/compose.yml", "acme/addons/ok/compose.yml", "acme/setups/.dot.yml", "acme/setups/Up.yml",
                 "acme/setups/ok.yml", "acme/setups/two.dots.yml")
        self.assertEqual(paths.names("acme", "addon"), ["ok"])
        self.assertEqual(paths.names("acme", "setup"), ["ok"])

    def test_a_file_or_a_folder_where_the_other_is_wanted_is_not_a_unit(self):
        self.put("acme/addons/file-only", "acme/setups/folder.yml/inside.txt", "acme/playgrounds/p/compose.yml/deeper.txt")
        (self.root / ".lab/catalogs/acme/addons/linked").symlink_to(self.root / ".lab/catalogs/acme/addons/file-only")
        self.assertEqual((paths.names("acme", "addon"), paths.names("acme", "setup"), paths.names("acme", "playground")),
                         ([], [], []))

    def test_what_is_not_there_has_no_names(self):
        for ns in ("nowhere", "", ".stage-nowhere"):
            with self.subTest(ns=ns):
                self.assertEqual([paths.names(ns, kind) for kind in paths.KINDS], [[], [], []])
        self.put("acme/addons")   # a file where the folder of the kind should be
        self.assertEqual(paths.names("acme", "addon"), [])

    def test_the_private_name_is_gone_for_good(self):
        self.assertFalse(hasattr(paths, "_names"), "one name, the public one")


class Found(OnDisk):
    """The units on disk: the default catalog's, this machine's, then the other catalogs'."""

    def setUp(self):
        super().setUp()
        self.put(
            "gravitee/addons/kafka/compose.yml", "gravitee/addons/jade/compose.yml", "gravitee/setups/events.yml",
            "gravitee/playgrounds/welcome/compose.yml", "gravitee/playgrounds/acme-bakery/compose.yml",
            "local/addons/whoami/compose.yml", "local/setups/mine.yml", "local/playgrounds/sandbox/compose.yml",
            "acme-hub/addons/mock-bank/compose.yml", "acme-hub/setups/bank-stack.yml",
            "acme-hub/playgrounds/bank/compose.yml", "a-b/addons/first/compose.yml", "zeta/addons/last/compose.yml")

    def test_the_ids_of_each_kind_by_catalog(self):
        # The set of units, group by group: who wins a name is the precedence of the registry, in the order added
        self.assertEqual({kind: paths.ids(kind) for kind in paths.KINDS}, {
            "addon": ["jade", "kafka", "local/whoami", "@a-b/first", "@acme-hub/mock-bank", "@zeta/last"],
            "setup": ["events", "local/mine", "@acme-hub/bank-stack"],
            "playground": ["acme-bakery", "welcome", "local/sandbox", "@acme-hub/bank"]})

    def test_the_files_follow_the_ids(self):
        self.assertEqual(paths.files(), [
            ".lab/catalogs/gravitee/addons/jade/compose.yml", ".lab/catalogs/gravitee/addons/kafka/compose.yml",
            ".lab/catalogs/local/addons/whoami/compose.yml", ".lab/catalogs/a-b/addons/first/compose.yml",
            ".lab/catalogs/acme-hub/addons/mock-bank/compose.yml", ".lab/catalogs/zeta/addons/last/compose.yml",
            ".lab/catalogs/gravitee/setups/events.yml", ".lab/catalogs/local/setups/mine.yml",
            ".lab/catalogs/acme-hub/setups/bank-stack.yml",
            ".lab/catalogs/gravitee/playgrounds/acme-bakery/compose.yml",
            ".lab/catalogs/gravitee/playgrounds/welcome/compose.yml",
            ".lab/catalogs/local/playgrounds/sandbox/compose.yml",
            ".lab/catalogs/acme-hub/playgrounds/bank/compose.yml"])
        for kind in paths.KINDS:
            for uid in paths.ids(kind):
                with self.subTest(kind=kind, uid=uid):
                    self.assertEqual(paths.unit_of(paths.unit_file(kind, uid)), (kind, uid))
                    self.assertTrue((self.root / paths.unit_file(kind, uid)).is_file())

    def test_the_default_catalog_is_the_one_the_file_names(self):
        self.default("acme-hub")
        self.assertEqual(paths.ids("addon"), ["mock-bank", "local/whoami", "@a-b/first", "@zeta/last"])
        self.assertEqual(paths.ids("setup"), ["bank-stack", "local/mine"])

    def test_nothing_that_is_not_a_unit_is_listed(self):
        self.put(
            # A catalog's staging, its leftovers and its names that cannot be a namespace
            ".stage-acme-hub/addons/staged/compose.yml", ".removed-acme-hub-20261001/addons/gone/compose.yml",
            "official/addons/reserved/compose.yml", "Acme/addons/capital/compose.yml", "a/addons/short/compose.yml",
            f"{'a' * 25}/addons/long/compose.yml", "acme_hub/addons/underscore/compose.yml",
            # A unit that is not complete or not named
            "acme-hub/addons/no-file/notes.md", "acme-hub/addons/Bad_Name/compose.yml",
            "acme-hub/addons/.hidden/compose.yml",
            "acme-hub/addons/deep/more/compose.yml", "acme-hub/addons/yaml/compose.yaml",
            "acme-hub/setups/pair.yaml", "acme-hub/setups/Bad.yml", "acme-hub/setups/nested/inside.yml",
            "acme-hub/setups/.hidden.yml", "acme-hub/playgrounds/_scratch/compose.yml",
            "acme-hub/playgrounds/lonely/guide.md", "acme-hub/other/thing/compose.yml", "acme-hub/addons/loose.yml",
            "acme-hub/catalog.yml", "acme-hub.partial", "catalogs.json", "gravitee/addons/compose.yml",
            "gravitee/addons/shadow/compose.yml/inside", "local/addons/compose.yml", "local/addons/.hidden/compose.yml")
        os.makedirs(self.root / ".lab/catalogs/acme-hub/setups/dir.yml")
        self.write("addons/legacy/compose.yml")   # the folders of before are not read
        self.write("setups/legacy.yml")
        self.write("playgrounds/legacy/compose.yml")
        self.assertEqual({kind: paths.ids(kind) for kind in paths.KINDS}, {
            "addon": ["jade", "kafka", "local/whoami", "@a-b/first", "@acme-hub/mock-bank", "@zeta/last"],
            "setup": ["events", "local/mine", "@acme-hub/bank-stack"],
            "playground": ["acme-bakery", "welcome", "local/sandbox", "@acme-hub/bank"]})

    def test_a_catalog_folder_may_be_a_link(self):
        # A folder catalog is a link to the author's folder; its units are found through it
        author = tempfile.TemporaryDirectory()
        self.addCleanup(author.cleanup)
        for file in ("addons/linked/compose.yml", "setups/linked-set.yml", "playgrounds/linked-play/compose.yml"):
            (Path(author.name) / file).parent.mkdir(parents=True, exist_ok=True)
            (Path(author.name) / file).write_text("", encoding="utf-8")
        os.symlink(author.name, self.root / ".lab/catalogs/author-cat")
        os.symlink(self.root / "missing", self.root / ".lab/catalogs/broken-link")   # no folder behind it
        self.assertEqual({kind: paths.ids(kind) for kind in paths.KINDS}, {
            "addon": ["jade", "kafka", "local/whoami", "@a-b/first", "@acme-hub/mock-bank", "@author-cat/linked",
                      "@zeta/last"],
            "setup": ["events", "local/mine", "@acme-hub/bank-stack", "@author-cat/linked-set"],
            "playground": ["acme-bakery", "welcome", "local/sandbox", "@acme-hub/bank", "@author-cat/linked-play"]})
        self.assertNotIn(".lab/catalogs/broken-link", " ".join(paths.files()))
        self.assertEqual(paths.unit_of(".lab/catalogs/author-cat/addons/linked/compose.yml"),
                         ("addon", "@author-cat/linked"))

    def test_a_folder_that_cannot_be_read_lists_nothing(self):
        real = os.listdir

        def listdir(path):
            if "acme-hub" in str(path) or str(path).endswith("/catalogs"):
                raise PermissionError(13, "Permission denied", str(path))
            return real(path)
        self.patch(paths.os, "listdir", listdir)
        self.assertEqual(paths.ids("addon"), ["jade", "kafka", "local/whoami"])
        self.assertEqual(paths.ids("setup"), ["events", "local/mine"])

    def test_no_catalog_folder_lists_nothing(self):
        self.folder(paths)
        self.default("gravitee")
        self.assertEqual([paths.ids(kind) for kind in paths.KINDS], [[], [], []])
        self.assertEqual(paths.files(), [])

    def test_the_names_of_before_list_the_default_catalog_and_this_machine_through_the_new_files(self):
        # unit_ids and unit_files stay as they were until their callers have moved: with a default file their code
        # reads the same two places through unit_file, and not the other catalogs
        self.assertEqual({kind: paths.unit_ids(kind) for kind in paths.KINDS}, {
            "addon": ["jade", "kafka", "local/whoami"], "setup": ["events", "local/mine"],
            "playground": ["acme-bakery", "local/sandbox", "welcome"]})
        self.assertEqual(paths.unit_files(), [
            ".lab/catalogs/gravitee/addons/jade/compose.yml", ".lab/catalogs/gravitee/addons/kafka/compose.yml",
            ".lab/catalogs/local/addons/whoami/compose.yml", ".lab/catalogs/gravitee/setups/events.yml",
            ".lab/catalogs/local/setups/mine.yml", ".lab/catalogs/gravitee/playgrounds/acme-bakery/compose.yml",
            ".lab/catalogs/local/playgrounds/sandbox/compose.yml",
            ".lab/catalogs/gravitee/playgrounds/welcome/compose.yml"])

    def test_the_ids_found_on_disk(self):
        # The table of today's ids and files, in the folders of a catalog
        self.folder(paths)
        self.default("gravitee")
        self.put(*[f"{folder}/{file}" for folder, file in (
            ("gravitee", "addons/kafka/compose.yml"), ("local", "addons/whoami/compose.yml"),
            ("local", "addons/compose.yml"), ("local", "addons/.hidden/compose.yml"),
            ("gravitee", "addons/jade/notes.md"),
            ("gravitee", "setups/events.yml"), ("local", "setups/mine.yml"), ("gravitee", "setups/AGENTS.md"),
            ("gravitee", "playgrounds/welcome/compose.yml"), ("gravitee", "playgrounds/_scratch/compose.yml"),
            ("local", "playgrounds/sandbox/compose.yml"), ("local", "playgrounds/sandbox/guide.md"))])
        self.assertEqual({kind: paths.ids(kind) for kind in paths.KINDS},
                         {"addon": ["kafka", "local/whoami"], "setup": ["events", "local/mine"],
                          "playground": ["welcome", "local/sandbox"]})
        self.assertEqual(paths.files(), [
            ".lab/catalogs/gravitee/addons/kafka/compose.yml", ".lab/catalogs/local/addons/whoami/compose.yml",
            ".lab/catalogs/gravitee/setups/events.yml", ".lab/catalogs/local/setups/mine.yml",
            ".lab/catalogs/gravitee/playgrounds/welcome/compose.yml",
            ".lab/catalogs/local/playgrounds/sandbox/compose.yml"])


class Vouched(OnDisk):
    """A git install lists what its manifest vouches for, as the registry says it; every other folder as it is."""

    def setUp(self):
        super().setUp()
        self.put(
            "acme-hub/.installed.json", "acme-hub/addons/listed/compose.yml", "acme-hub/addons/rogue/compose.yml",
            "acme-hub/setups/unlisted.yml", "acme-hub/playgrounds/bank/compose.yml",
            "gravitee/.installed.json", "gravitee/addons/kafka/compose.yml", "gravitee/addons/stray/compose.yml",
            "local/.installed.json", "local/addons/mine/compose.yml", "local/setups/mine.yml",
            "plain/addons/loose/compose.yml")
        self.vouches = {"acme-hub": {"addon": ["listed"], "playground": ["bank", "ghost"]},
                        "gravitee": {"addon": ["kafka"]}}
        self.asked = self.patch(registry, "units_of", side_effect=lambda ns: self.vouches.get(ns, {}))

    def everything(self):
        return {kind: paths.ids(kind) for kind in paths.KINDS}

    def test_a_unit_outside_the_manifest_is_no_unit(self):
        self.assertEqual(self.everything(), {
            "addon": ["kafka", "local/mine", "@acme-hub/listed", "@plain/loose"],
            "setup": ["local/mine"], "playground": ["@acme-hub/bank"]})
        self.assertEqual(paths.files(), [
            ".lab/catalogs/gravitee/addons/kafka/compose.yml", ".lab/catalogs/local/addons/mine/compose.yml",
            ".lab/catalogs/acme-hub/addons/listed/compose.yml", ".lab/catalogs/plain/addons/loose/compose.yml",
            ".lab/catalogs/local/setups/mine.yml", ".lab/catalogs/acme-hub/playgrounds/bank/compose.yml"])

    def test_a_name_the_manifest_vouches_for_and_the_folder_lacks_is_no_unit(self):
        self.assertNotIn("@acme-hub/ghost", paths.ids("playground"))

    def test_the_half_of_an_install_the_registry_does_not_know_lists_nothing(self):
        # A manifest the registry gives no units for (not valid for the pin, not in the lock): its files are not content
        self.vouches.clear()
        self.assertEqual(self.everything(), {
            "addon": ["local/mine", "@plain/loose"], "setup": ["local/mine"], "playground": []})

    def test_this_machine_is_listed_as_it_is_whatever_a_file_says(self):
        self.assertIn("local/mine", paths.ids("addon"))
        self.assertNotIn("local", [call.args[0] for call in self.asked.call_args_list])

    def test_a_link_to_a_folder_is_listed_as_it_is(self):
        author = tempfile.TemporaryDirectory()
        self.addCleanup(author.cleanup)
        for file in (".installed.json", "addons/linked/compose.yml"):
            (Path(author.name) / file).parent.mkdir(parents=True, exist_ok=True)
            (Path(author.name) / file).write_text("", encoding="utf-8")
        os.symlink(author.name, self.root / ".lab/catalogs/author-cat")
        self.assertIn("@author-cat/linked", paths.ids("addon"))
        self.assertNotIn("author-cat", [call.args[0] for call in self.asked.call_args_list])

    def test_a_folder_with_no_manifest_is_listed_as_it_is(self):
        self.assertIn("@plain/loose", paths.ids("addon"))
        self.assertNotIn("plain", [call.args[0] for call in self.asked.call_args_list])

    def test_a_manifest_that_is_not_a_file_is_not_a_manifest(self):
        os.makedirs(self.root / ".lab/catalogs/plain/.installed.json")
        self.assertIn("@plain/loose", paths.ids("addon"))

    def test_the_registry_is_asked_once_per_install_and_only_for_installs_with_units(self):
        paths.files()
        self.assertEqual(sorted(call.args[0] for call in self.asked.call_args_list), ["acme-hub", "gravitee"])
        self.put("empty-cat/.installed.json")
        self.asked.reset_mock()
        paths.ids("addon")
        self.assertEqual(sorted(call.args[0] for call in self.asked.call_args_list), ["acme-hub", "gravitee"])

    def test_a_registry_that_cannot_say_vouches_for_nothing(self):
        for label, answer in (("raises", RuntimeError("no lock")), ("not found", FileNotFoundError("lock")),
                              ("none", None), ("text", "addon"), ("a list", ["kafka"]),
                              ("kinds not lists", {"addon": 5}), ("kinds none", {"addon": None})):
            with self.subTest(label):
                self.asked.side_effect = None
                if isinstance(answer, Exception):
                    self.asked.side_effect = answer
                else:
                    self.asked.return_value = answer
                self.assertEqual(self.everything(), {
                    "addon": ["local/mine", "@plain/loose"], "setup": ["local/mine"], "playground": []})
                self.assertEqual(paths.files(), [".lab/catalogs/local/addons/mine/compose.yml",
                                                 ".lab/catalogs/plain/addons/loose/compose.yml",
                                                 ".lab/catalogs/local/setups/mine.yml"])

    def test_each_kind_has_its_own_vouched_names(self):
        self.vouches["acme-hub"] = {"addon": ["listed"], "setup": ["unlisted"]}
        self.assertEqual(paths.ids("setup"), ["local/mine", "@acme-hub/unlisted"])
        self.assertEqual(paths.ids("playground"), [])

    def test_before_catalogs_nobody_is_asked(self):
        os.remove(self.root / paths.DEFAULT_FILE)
        paths.ids("addon")
        paths.files()
        self.asked.assert_not_called()


class RegistryNotWritten(OnDisk):
    """The registry as shipped before it is written: it vouches for no install."""

    def test_a_git_install_lists_nothing_and_the_rest_is_listed(self):
        self.put("acme-hub/.installed.json", "acme-hub/addons/x/compose.yml", "gravitee/addons/kafka/compose.yml",
                 "local/addons/mine/compose.yml", "plain/addons/loose/compose.yml")
        self.assertEqual(paths.ids("addon"), ["kafka", "local/mine", "@plain/loose"])


class BeforeCatalogs(Case):
    """No default file: ids and files are the layout of before, whatever the new functions are asked."""

    def setUp(self):
        self.folder(paths, files=dict.fromkeys((
            "addons/kafka/compose.yml", "addons/local/whoami/compose.yml", "setups/events.yml", "setups/local/mine.yml",
            "playgrounds/welcome/compose.yml", "playgrounds/local/sandbox/compose.yml",
            ".lab/catalogs/gravitee/addons/not-here/compose.yml", ".lab/catalogs/local/addons/not-here/compose.yml",
            ".lab/catalogs/acme-hub/addons/not-here/compose.yml"), ""))
        self.patch(paths, "_default", (None, None))

    def test_the_ids_and_files_are_those_of_before(self):
        self.assertIsNone(paths.default_ns())
        self.assertEqual({kind: paths.ids(kind) for kind in paths.KINDS}, {
            "addon": ["kafka", "local/whoami"], "setup": ["events", "local/mine"],
            "playground": ["local/sandbox", "welcome"]})
        self.assertEqual(paths.files(), paths.unit_files())
        self.assertEqual(paths.files(), [
            "addons/kafka/compose.yml", "addons/local/whoami/compose.yml", "setups/events.yml", "setups/local/mine.yml",
            "playgrounds/local/sandbox/compose.yml", "playgrounds/welcome/compose.yml"])

    def test_a_file_gives_its_id_and_an_id_its_file(self):
        for kind, uid, file in (("addon", "kafka", "addons/kafka/compose.yml"),
                                ("addon", "local/whoami", "addons/local/whoami/compose.yml"),
                                ("setup", "events", "setups/events.yml"),
                                ("setup", "local/mine", "setups/local/mine.yml"),
                                ("playground", "welcome", "playgrounds/welcome/compose.yml"),
                                ("playground", "local/sandbox", "playgrounds/local/sandbox/compose.yml")):
            with self.subTest(file=file):
                self.assertEqual(paths.unit_file(kind, uid), file)
                self.assertEqual(paths.unit_of(file), (kind, uid))
                self.assertEqual(paths.unit_folder(kind, uid), file.rsplit("/", 1)[0] if kind != "setup" else None)

    def test_the_folders_of_the_catalogs_are_not_read(self):
        for path in (".lab/catalogs/gravitee/addons/not-here/compose.yml",
                     ".lab/catalogs/local/addons/not-here/compose.yml",
                     ".lab/catalogs/acme-hub/addons/not-here/compose.yml"):
            with self.subTest(path=path):
                self.assertIsNone(paths.unit_of(path))

    def test_there_is_no_id_of_a_catalog_before_catalogs(self):
        for kind in paths.KINDS:
            with self.assertRaises(paths.NotFound):
                paths.unit_file(kind, "@acme-hub/bank")
        for path in ("addons/@acme-hub/bank/compose.yml", "addons/@xx/y/compose.yml", "setups/@acme-hub/x.yml"):
            with self.subTest(path=path):
                self.assertIsNone(paths.unit_of(path))

    def test_an_id_is_a_file_name_as_it_always_was(self):
        self.assertEqual(paths.unit_file("addon", "anything goes"), "addons/anything goes/compose.yml")
        self.assertEqual(paths.unit_file("addon", "*"), "addons/*/compose.yml")
        self.assertEqual(paths.unit_file("setup", "local/*"), "setups/local/*.yml")

    def test_the_new_questions_about_a_name_answer_for_the_bare_one(self):
        self.assertEqual((paths.origin("local/x"), paths.is_default("x")), ("local", True))
        self.assertEqual(paths.split_ref("kafka"), (None, "kafka"))


class Contained(Case):

    def setUp(self):
        self.folder(paths)
        self.state = self.root / "unit" / ".state"
        self.outside = self.root / "outside"
        for folder in (self.state / "inside", self.outside):
            folder.mkdir(parents=True)
        os.symlink(self.outside, self.state / "planted")
        os.symlink(self.state, self.root / "alias")

    def test_what_is_inside(self):
        for path in (self.state, self.state / "created.json", self.state / "inside" / "x" / "y", self.state / "inside",
                     self.state / "inside" / ".." / "inside" / "z", str(self.state / "created.json"),
                     self.root / "alias" / "created.json", "unit/.state/created.json", "unit/.state"):
            with self.subTest(path=path):
                self.assertTrue(paths.contained(path, self.state))
                self.assertTrue(paths.contained(path, "unit/.state"))

    def test_what_is_outside(self):
        for path in (self.root, self.root / "unit", self.outside / "x", self.state / ".." / "secrets",
                     self.state / "inside" / ".." / ".." / "secrets", self.root / "unit" / ".state-other" / "x",
                     self.root / "unit" / ".stat", str(self.state) + "x", "unit/.state/../x", "../x", "/", "/etc/hosts",
                     "elsewhere/.state/x"):
            with self.subTest(path=path):
                self.assertFalse(paths.contained(path, self.state))

    def test_a_planted_link_does_not_lead_out(self):
        self.assertFalse(paths.contained(self.state / "planted", self.state))
        self.assertFalse(paths.contained(self.state / "planted" / "x", self.state))
        self.assertFalse(paths.contained("unit/.state/planted/created.json", "unit/.state"))
        os.symlink(self.state / "planted", self.state / "chain")   # a link to a link
        self.assertFalse(paths.contained(self.state / "chain" / "x", self.state))
        # A link that stays inside is no way out
        os.symlink(self.state / "inside", self.state / "within")
        self.assertTrue(paths.contained(self.state / "within" / "x", self.state))

    def test_a_folder_that_is_a_link_is_the_folder_it_leads_to(self):
        alias = self.root / "alias"
        self.assertTrue(paths.contained(self.state / "created.json", alias))
        self.assertTrue(paths.contained(alias / "created.json", alias))
        self.assertFalse(paths.contained(self.outside / "x", alias))

    def test_a_relative_path_is_the_projects(self):
        self.patch(paths, "ROOT", self.root / "unit")
        self.assertTrue(paths.contained(".state/x", ".state"))
        self.assertFalse(paths.contained("../outside/x", ".state"))

    def test_a_path_that_cannot_be_asked_about_is_refused(self):
        for path in ("unit/.state/x\0", "\0", None, 5, b"unit/.state/x", ["unit"]):
            with self.subTest(path=path):
                self.assertFalse(paths.contained(path, self.state))
        for root in ("unit/.state\0", None, 5, b"unit/.state"):
            with self.subTest(root=root):
                self.assertFalse(paths.contained(self.state / "x", root))

    def test_the_root_of_everything_holds_everything(self):
        self.assertTrue(paths.contained(self.state, "/"))

    def test_a_root_that_is_blank_holds_nothing(self):
        # An unset variable is not the project: a doubt refuses
        for root in ("", " ", "\t", "\n", " \n "):
            for path in (self.state / "x", "unit/.state/x", "x", ""):
                with self.subTest(root=root, path=path):
                    self.assertFalse(paths.contained(path, root))
        self.assertTrue(paths.contained("unit/.state/x", "."))   # the project, named

    def test_the_root_is_a_folder_no_container_writes_in(self):
        # The state folder itself replaced by a link: as a root it is resolved like the path, and holds the way out.
        # The unit's folder, which a container does not write in, is the root to ask with
        unit = self.root / "unit"
        os.rename(self.state, self.root / "moved-state")
        os.symlink(self.outside, self.state)
        secret = self.state / "secret"
        self.assertTrue(paths.contained(secret, self.state))
        self.assertFalse(paths.contained(secret, unit))
        self.assertFalse(paths.contained(secret, "unit"))


class WriteAtomic(Case):

    def setUp(self):
        self.folder()

    def copies(self):
        return sorted(path.name for path in self.root.glob(".*.tmp"))

    def test_a_file_written_whole_with_its_mode(self):
        env = self.root / ".env"
        paths.write_atomic(env, "TOKEN=x\n", mode=0o600)
        self.assertEqual((env.read_text(encoding="utf-8"), env.stat().st_mode & 0o777, self.copies()),
                         ("TOKEN=x\n", 0o600, []))

    def test_a_file_is_utf8_with_unix_line_ends(self):
        notes = self.root / "notes.md"
        paths.write_atomic(notes, "# Démo ✓\nline two\n")
        self.assertEqual(notes.read_bytes(), "# Démo ✓\nline two\n".encode("utf-8"))

    def test_a_system_without_fchmod_sets_the_mode_on_the_copy(self):
        env = self.root / ".env"
        fchmod = paths.os.fchmod
        self.addCleanup(setattr, paths.os, "fchmod", fchmod)
        del paths.os.fchmod
        paths.write_atomic(env, "TOKEN=x\n", mode=0o600)
        self.assertEqual((env.read_text(encoding="utf-8"), env.stat().st_mode & 0o777, self.copies()),
                         ("TOKEN=x\n", 0o600, []))

    def test_a_write_that_fails_leaves_the_file_and_no_copy(self):
        env = self.root / self.write(".env", "TOKEN=old\n")
        self.patch(paths.os, "replace", side_effect=OSError("No space left on device"))
        with self.assertRaisesRegex(OSError, "No space left"):
            paths.write_atomic(env, "TOKEN=new\n", mode=0o600)
        self.assertEqual((env.read_text(encoding="utf-8"), self.copies()), ("TOKEN=old\n", []))


if __name__ == "__main__":
    unittest.main()
