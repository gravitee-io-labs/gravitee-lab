"""paths: what a file is from its folder, the ids on disk, what belongs to this machine, and a file written whole."""

import fcntl
import json
import os
import re
import stat
import threading
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from lab_ui import paths
from lab_ui.catalogs import registry

from . import Case
from .server.test_api import served_elsewhere


class SeedApis(Case):

    def found(self, root):
        return [file.relative_to(root).as_posix() for file in paths.seed_apis(root)]

    def test_the_apis_of_a_seed_are_found_in_the_platforms_folder_and_in_each_environment(self):
        root = self.folder(files=dict.fromkeys((
            "seed/apim/apis/b.json", "seed/apim/apis/a.json", "seed/apim/apis/notes.txt", "seed/apim/settings.yaml",
            "seed/environments/north-meadow/apis/batch-status.json", "seed/environments/beet/apis/sample-requests.json",
            "seed/environments/beet/applications/app.yaml", "seed/environments/beet/portal/apis/deeper.json",
            "seed/environments/beet/apis/nested/deeper.json", "seed/am/apis/z.json"), ""))
        self.assertEqual(self.found(root), [
            "seed/am/apis/z.json", "seed/apim/apis/a.json", "seed/apim/apis/b.json",
            "seed/environments/beet/apis/sample-requests.json", "seed/environments/north-meadow/apis/batch-status.json"])

    def test_a_folder_without_a_seed_has_no_api(self):
        root = self.folder(files={"compose.yml": "", "seed/controls.yaml": ""})
        self.assertEqual(self.found(root), [])
        self.assertEqual(self.found(root / "gone"), [])


class Records(Case):
    """A file written whole reaches the disk before it takes its name; a record of this machine that does not read is kept aside."""

    def setUp(self):
        self.folder(paths)
        (self.root / ".lab").mkdir()
        self.file = self.root / ".lab" / "kiosk.json"

    def test_a_file_written_whole_is_on_the_disk_then_its_name_too(self):
        synced, real = [], os.fsync

        def fsync(descriptor):
            synced.append("folder" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file")
            real(descriptor)
        with unittest.mock.patch.object(paths.os, "fsync", side_effect=fsync):
            paths.write_atomic(self.file, "{}\n")
        self.assertEqual(synced, ["file", "folder"])   # the copy, then the folder that names it
        self.assertEqual(self.file.read_text(encoding="utf-8"), "{}\n")

    def test_a_record_reads_as_its_json_and_one_that_is_not_there_as_none(self):
        self.assertIsNone(paths.read_record(self.file))
        self.file.write_text('{"kiosk": [1]}', encoding="utf-8")
        self.assertEqual(paths.read_record(self.file), {"kiosk": [1]})

    def test_a_record_cut_short_is_kept_aside_and_reads_as_none(self):
        self.file.write_text('{"kiosk": [1, 2', encoding="utf-8")
        self.assertIsNone(paths.read_record(self.file))
        kept = sorted(self.file.parent.glob("kiosk.json.bad-*"))
        self.assertEqual([one.read_text(encoding="utf-8") for one in kept], ['{"kiosk": [1, 2'])
        self.assertFalse(self.file.exists())
        self.assertIsNone(paths.read_record(self.file))
        self.file.write_text("[" * 100000, encoding="utf-8")   # too deep for json: no RecursionError leaves the reader
        self.assertIsNone(paths.read_record(self.file))
        self.assertEqual(len(list(self.file.parent.glob("kiosk.json.bad-*"))), 2)

    def test_an_empty_record_holds_nothing_to_keep(self):
        self.file.write_text("", encoding="utf-8")
        self.assertIsNone(paths.read_record(self.file))
        self.assertEqual(list(self.file.parent.glob("*.bad-*")), [])


class Serving(Case):
    """One Lab UI serves a folder: the lock it holds for its life, with its port, as this process and another one find it."""

    def setUp(self):
        self.folder(paths)
        self.addCleanup(paths.stop_serving)

    def test_the_lab_ui_that_serves_the_folder_holds_it_with_its_port_and_lets_it_go(self):
        self.assertIsNone(paths.served_elsewhere())
        self.assertIsNone(paths.serve_here(9000))
        self.assertEqual((self.root / paths.SERVING_FILE).read_text(), "9000\n")
        self.assertIsNone(paths.serve_here(9001), "it holds it already")
        self.assertIsNone(paths.served_elsewhere(), "this process is the one")
        paths.stop_serving()
        self.assertIsNone(paths.serve_here(9002), "let go, it is free for the next")

    def test_another_process_that_serves_the_folder_is_found_with_its_port(self):
        served_elsewhere(self, 9000)
        self.assertEqual(paths.served_elsewhere(), 9000)
        self.assertEqual(paths.serve_here(9001), 9000)
        self.assertEqual((self.root / paths.SERVING_FILE).read_text(), "9000\n", "nothing written over")

    def test_a_command_that_only_looks_is_no_lab_ui_that_serves(self):
        self.patch(paths, "SERVING_WAIT", 60)   # it is free once the command goes: no slow timer may outlast the wait
        (self.root / paths.LAB_DIR).mkdir()
        looking = os.open(self.root / paths.SERVING_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        os.write(looking, b"9000\n")   # what the last Lab UI wrote before it stopped
        fcntl.flock(looking, fcntl.LOCK_SH)
        threading.Timer(0.2, os.close, args=(looking,)).start()   # it looks, then goes
        self.assertIsNone(paths.serve_here(9001))
        self.assertEqual((self.root / paths.SERVING_FILE).read_text(), "9001\n")


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
        "kafka.yml", "kafka\\x", "a b", "a\tb", "~", "${HOME}", "%2e%2e", None, 5, b"kafka", ["kafka"],
        "*", "@acme-hub/*", "local/*", "[!.]*", "local/[!.]*")

    def test_an_id_is_bare_personal_or_of_a_catalog(self):
        for uid in ("kafka", "a", "-", "0", "mock-bank", "local", "local/whoami", "local/local", "local/a-b",
                    "@acme-hub/mock-bank", "@acme-hub/local", "@ab/x", f"@{'a' * 24}/x", "@a1/x", "@a-b/x",
                    "@acme-hub/-"):
            with self.subTest(uid=uid):
                self.assertTrue(paths.is_id(uid))

    def test_every_adversarial_id_is_refused(self):
        for default in (("gravitee",), ("demos",)):
            self.default(*default)
            for uid in self.ADVERSARIAL:
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

    def test_the_pattern_of_a_route_has_one_group(self):
        self.assertEqual(re.compile(paths.ID).groups, 1)
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


class DefaultNamespace(OnDisk):

    def test_the_first_entry_owns_the_bare_ids(self):
        self.default("gravitee", "second-catalog")
        self.assertEqual(paths.default_ns(), "gravitee")
        self.default("demos", "gravitee")
        self.assertEqual(paths.default_ns(), "demos")

    def test_no_file_no_default(self):
        os.remove(self.root / paths.DEFAULT_FILE)
        self.assertIsNone(paths.default_ns())

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
    """Every unit sits in the folder of a catalog."""

    TABLE = (
        # kind, id, file, folder
        ("addon", "kafka", ".lab/catalogs/gravitee/addons/kafka/compose.yml", ".lab/catalogs/gravitee/addons/kafka"),
        ("addon", "local/whoami", ".lab/catalogs/local/addons/whoami/compose.yml", ".lab/catalogs/local/addons/whoami"),
        ("addon", "local/local", ".lab/catalogs/local/addons/local/compose.yml", ".lab/catalogs/local/addons/local"),
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
                self.assertEqual(paths.unit_of(file), (kind, uid))

    def test_the_default_namespace_is_read_when_asked(self):
        self.default("demos")
        self.assertEqual(paths.unit_file("addon", "kafka"), ".lab/catalogs/demos/addons/kafka/compose.yml")
        self.assertEqual(paths.unit_of(".lab/catalogs/demos/addons/kafka/compose.yml"), ("addon", "kafka"))
        self.assertIsNone(paths.unit_of(".lab/catalogs/gravitee/addons/kafka/compose.yml"))   # reserved: no id

    def test_the_name_of_this_machines_folder_is_a_name_in_a_catalog(self):
        # The folder of this machine is a catalog, and a unit called local is one like any
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
                ".lab/catalogs/gravitee/addons/kafka/.env", ".lab/catalogs/gravitee/addons/My_Addon/compose.yml",
                ".lab/catalogs/local/addons/whoami/more/compose.yml", ".lab/catalogs/local/addons/../kafka/compose.yml",
                ".lab/catalogs/local/setups/mine/more.yml", ".lab/catalogs/gravitee/playgrounds/_scratch/compose.yml",
                "docker-compose.yml",
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

    def test_a_bare_name_has_no_place_without_a_default_catalog(self):
        os.remove(self.root / paths.DEFAULT_FILE)
        with self.assertRaises(paths.NotFound):   # a place is never made of None
            paths.unit_file("addon", "kafka")

    def test_a_kind_that_does_not_exist_is_a_key_error(self):
        with self.assertRaises(KeyError):
            paths.unit_file("playlist", "kafka")
        with self.assertRaises(KeyError):
            paths.ids("playlist")

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
        self.write("addons/root/compose.yml")   # the project's own folders are not read
        self.write("setups/root.yml")
        self.write("playgrounds/root/compose.yml")
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

    def test_without_a_default_file_this_machine_and_the_other_catalogs_stay(self):
        os.remove(self.root / paths.DEFAULT_FILE)
        self.assertEqual(paths.ids("setup"), ["local/mine", "@acme-hub/bank-stack"])
        self.assertEqual(paths.files()[-2:], [".lab/catalogs/local/playgrounds/sandbox/compose.yml",
                                              ".lab/catalogs/acme-hub/playgrounds/bank/compose.yml"])


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

    def test_the_half_of_an_install_the_registry_does_not_know_lists_nothing(self):
        # A manifest the registry gives no units for (not valid for the pin, not in the lock): its files are not content
        self.vouches.clear()
        self.assertEqual(self.everything(), {
            "addon": ["local/mine", "@plain/loose"], "setup": ["local/mine"], "playground": []})

    def test_a_link_to_a_folder_is_listed_as_it_is(self):
        author = tempfile.TemporaryDirectory()
        self.addCleanup(author.cleanup)
        for file in (".installed.json", "addons/linked/compose.yml"):
            (Path(author.name) / file).parent.mkdir(parents=True, exist_ok=True)
            (Path(author.name) / file).write_text("", encoding="utf-8")
        os.symlink(author.name, self.root / ".lab/catalogs/author-cat")
        self.assertIn("@author-cat/linked", paths.ids("addon"))
        self.assertNotIn("author-cat", [call.args[0] for call in self.asked.call_args_list])

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


class RegistryNotWritten(OnDisk):
    """The registry as shipped before it is written: it vouches for no install."""

    def test_a_git_install_lists_nothing_and_the_rest_is_listed(self):
        self.put("acme-hub/.installed.json", "acme-hub/addons/x/compose.yml", "gravitee/addons/kafka/compose.yml",
                 "local/addons/mine/compose.yml", "plain/addons/loose/compose.yml")
        self.assertEqual(paths.ids("addon"), ["kafka", "local/mine", "@plain/loose"])


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

    def test_a_write_that_fails_leaves_the_file_and_no_copy(self):
        env = self.root / self.write(".env", "TOKEN=old\n")
        self.patch(paths.os, "replace", side_effect=OSError("No space left on device"))
        with self.assertRaisesRegex(OSError, "No space left"):
            paths.write_atomic(env, "TOKEN=new\n", mode=0o600)
        self.assertEqual((env.read_text(encoding="utf-8"), self.copies()), ("TOKEN=old\n", []))


if __name__ == "__main__":
    unittest.main()
