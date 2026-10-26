"""catalogs/exports.py: a playground in a file. What an export carries and needs, the archive it makes (a folder catalog), the
judgement of it as another Lab will make it, and its import: the archive read with the limits of a catalog, written under
.lab/imports, checked as the Add of a folder catalog, added, and gone with the catalog. `Archives` needs nothing but Python;
`RoundTrip` reads the fixture catalogs with Compose 5 (a visible skip without it)."""

import base64
import io
import json
import os
import re
import shutil
import stat
import zipfile
from pathlib import Path

from lab_ui import paths
from lab_ui.catalogs import contribute, exports, gitx, manifest, moving, registry, service, understood
from lab_ui.catalogs.registry import CatalogError
from lab_ui.docker import compose, envfiles, files
from lab_ui.library import check, project

from .. import Case, fixtures

CATALOG = 'x-gravitee-catalog:\n  schema: 1\n  id: "demo"\n  name: "Demo"\n  version: 1.0.0\n  lab: 1\n'


def zipped(entries):
    """A zip archive of {name: bytes, or (bytes, mode)}, the mode a file's (0o644 by default) or a link's."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in entries.items():
            data, mode = data if isinstance(data, tuple) else (data, stat.S_IFREG | 0o644)
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            archive.writestr(info, data)
    return out.getvalue()


def shown(plan):
    """What the page sends of what it showed of a plan's private content: each item under each origin."""
    return [f"{one['key']}|{uid}" for one in plan["private"] for uid in one["item_ids"]]


def root(ns):
    """The origin id of the private catalog of Git `ns`: the first commit of its history."""
    return contribute._history(ns)


def _layout(old, new):
    base = BASE_YML
    assert old in base, old
    return base.replace(old, new, 1)


BASE_YML = ("x-gravitee-catalog:\n  schema: 1\n  id: partner\n  name: Partner fixture\n  description: d\n  version: 2.0.0\n"
            "  lab: 1\n  license: AGPL-3.0-only\n")
# {name: (catalog.yml whose lab is 1 as Compose reads it, or a file it refuses, whether a share writes it)}
RAISED_LAYOUTS = {
    "plain": (BASE_YML, True),
    "comment after": (_layout("  lab: 1\n", "  lab: 1   # the level\n"), True),
    "comment line under": (_layout("  lab: 1\n", "  lab: 1\n  # the level it needs\n"), True),
    "comment line over": (_layout("  lab: 1\n", "  # the level it needs\n  lab: 1\n"), True),
    "blank lines around": (_layout("  lab: 1\n", "\n  lab: 1\n\n"), True),
    "comment far under": (_layout("  lab: 1\n", "  lab: 1\n                      # more\n"), True),
    "quoted key": (_layout("  lab: 1\n", "  'lab': 1\n"), True),
    "double quoted value": (_layout("  lab: 1\n", '  lab: "1"\n'), False),
    "single quoted value": (_layout("  lab: 1\n", "  lab: '1'\n"), False),
    "anchor": (_layout("  lab: 1\n", "  lab: &level 1\n"), True),
    "tag": (_layout("  lab: 1\n", "  lab: !!int 1\n"), True),
    "leading zero": (_layout("  lab: 1\n", "  lab: 01\n"), True),
    "plus sign": (_layout("  lab: 1\n", "  lab: +1\n"), True),
    "hex": (_layout("  lab: 1\n", "  lab: 0x1\n"), True),
    "value on the next line": (_layout("  lab: 1\n", "  lab:\n    1\n"), False),
    "four spaces": (BASE_YML.replace("\n  ", "\n    "), True),
    "crlf": (BASE_YML.replace("\n", "\r\n"), True),
    "bom": ("\ufeff" + BASE_YML, True),
    "bom and crlf": ("\ufeff" + BASE_YML.replace("\n", "\r\n"), True),
    "tab after the colon": (_layout("  lab: 1\n", "  lab:\t1\n"), True),
    "flow block": ("x-gravitee-catalog: {schema: 1, id: partner, name: Partner fixture, description: d, version: 2.0.0, lab: 1, "
                   "license: AGPL-3.0-only}\n", True),
    "flow block on lines": ("x-gravitee-catalog: {schema: 1, id: partner, name: Partner fixture,\n  description: d, version: 2.0.0, "
                            "lab: 1,\n  license: AGPL-3.0-only}\n", True),
    "lab twice": (_layout("  lab: 1\n", "  lab: 1\n  lab: 1\n"), False),
    "explicit key": (_layout("  lab: 1\n", "  ? lab\n  : 1\n"), False),
    "quoted block key": (BASE_YML.replace("x-gravitee-catalog:", '"x-gravitee-catalog":', 1), True),
    "document start": ("---\n" + BASE_YML, True),
    "comment before the block": ("# my catalog\n" + BASE_YML, True),
    "comment on the key line": (BASE_YML.replace("x-gravitee-catalog:\n", "x-gravitee-catalog:   # hi\n", 1), True),
    "merge key": ("x-base: &b\n  lab: 1\nx-gravitee-catalog:\n  <<: *b\n  schema: 1\n  id: partner\n  name: Partner fixture\n"
                  "  description: d\n  version: 2.0.0\n  license: AGPL-3.0-only\n", True),
    "a lab nested under another key": (_layout("  version: 2.0.0\n", "  version: 2.0.0\n  requires:\n    lab: 1\n"), True),
    "no lab": (_layout("  lab: 1\n", ""), True),
    "a lab nested first, then its own": (_layout("  schema: 1\n", "  schema: 1\n  x:\n    lab: 1\n"), True),
}


class Archives(Case):
    """What an import refuses before anything is written: the file is not what an export makes, or holds what a catalog cannot."""

    def refused(self, data, code="import_invalid"):
        with self.assertRaises(CatalogError) as caught:
            exports.receive(data)
        self.assertEqual(caught.exception.code, code)
        return caught.exception.message

    def test_a_file_that_is_no_archive_of_a_playground_is_refused_in_one_sentence(self):
        for data in (b"", b"not a zip", "not base64!", base64.b64encode(b"not a zip").decode(), 12):
            with self.subTest(data=str(data)[:20]):
                self.assertEqual(self.refused(data), exports.NOT_A_PLAYGROUND)

    def test_an_archive_must_be_one_folder_named_as_a_catalog_with_its_catalog_yml(self):
        for entries in ({"demo/README.md": b"x"},                                   # no catalog.yml
                        {"catalog.yml": CATALOG.encode()},                           # no folder
                        {"demo/catalog.yml": CATALOG.encode(), "other/x": b"x"},     # two folders
                        {"Demo/catalog.yml": CATALOG.encode()}):                      # not a catalog id
            with self.subTest(entries=list(entries)):
                self.refused(zipped(entries))

    def test_no_entry_leaves_its_folder_and_no_link_is_written(self):
        link = (b"/etc/passwd", stat.S_IFLNK | 0o777)
        for bad in ({"demo/../escape": b"x"}, {"/demo/x": b"x"}, {"demo\\x": b"x"}, {"demo/./x": b"x"}, {"demo/link": link}):
            with self.subTest(bad=list(bad)):
                self.refused(zipped({"demo/catalog.yml": CATALOG.encode(), **bad}))

    def test_what_a_catalog_never_holds_is_refused(self):
        message = self.refused(zipped({"demo/catalog.yml": CATALOG.encode(), "demo/addons/x/.env": b"SECRET=1"}))
        self.assertTrue(message.startswith(exports.NOT_A_PLAYGROUND))
        self.assertIn("addons/x/.env", message)

    def test_more_than_a_catalog_may_hold_is_refused_whatever_the_archive_says_of_its_sizes(self):
        self.patch(exports, "MAX_FILES", 2)
        self.refused(zipped({"demo/catalog.yml": CATALOG.encode(), "demo/a": b"a", "demo/b": b"b"}), "import_too_large")
        self.patch(exports, "MAX_FILES", 10000)
        self.patch(exports, "MAX_BYTES", 10)
        self.refused(zipped({"demo/catalog.yml": CATALOG.encode()}), "import_too_large")
        self.patch(exports, "MAX_ARCHIVE", 5)
        self.refused(zipped({"demo/catalog.yml": b"x"}), "import_too_large")


class Unseen(Case):
    """exports.unseen_private: the file holds to the items of private content the page showed, not to their catalogs."""

    def test_an_item_the_page_did_not_show_refuses_the_file_though_its_catalog_was_shown(self):
        said = {"private": [{"key": "r1", "name": "Partner fixture", "items": ["Ledger", "Books"], "item_ids": ["@partner/ledger", "local/books"]}]}
        with self.assertRaises(CatalogError) as caught:
            exports.unseen_private(said, ["r1|@partner/ledger"])
        self.assertEqual(caught.exception.code, "export_private")
        exports.unseen_private(said, ["r1|@partner/ledger", "r1|local/books"])

    def test_an_origin_an_item_gained_since_refuses_the_file_though_the_item_was_shown(self):
        said = {"private": [{"key": "r1", "name": "Partner fixture", "items": ["Ledger"], "item_ids": ["local/ledger"]},
                            {"key": "r2", "name": "Older Lab fixture", "items": ["Ledger"], "item_ids": ["local/ledger"]}]}
        with self.assertRaises(CatalogError) as caught:
            exports.unseen_private(said, ["r1|local/ledger"])
        self.assertEqual(caught.exception.message, "It now carries private content of Older Lab fixture. Fix: export it again to see "
                                                   "what leaves with it.")


class Named(Case):
    """exports._named reads the name of an item of a file someone else made: at once, whatever the file holds."""

    def test_the_name_is_read_in_either_form_and_a_block_with_none_takes_no_time(self):
        import time
        started = time.monotonic()
        text = "x-gravitee:\n" + "    kind: playground\n" * 40 + "services: {}\n"
        self.assertEqual(exports._named(text, "evil"), "evil")
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(exports._named("x-gravitee: {kind: addon, name: Echo, category: Other}\n", "echo"), "Echo")
        self.assertEqual(exports._named("x-gravitee:  # the item\n  kind: addon\n\n  name: 'Echo two'\nservices: {}\n", "echo"), "Echo two")
        self.assertEqual(exports._named("x-gravitee:\n  kind: addon\nservices:\n  a:\n    name: no\n", "echo"), "echo")

    def test_a_flow_block_that_never_closes_takes_no_time_either(self):
        import time
        for text in ("x-gravitee: {\n" * 20000, "x-gravitee: {kind: addon\n" + "  a: b\n" * 20000, "x-gravitee: {" + " " * 400000):
            started = time.monotonic()
            self.assertEqual(exports._named(text, "evil"), "evil")
            self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(exports._named("x-gravitee: {kind: addon,\n  name: Echo}\n", "echo"), "Echo", "a flow block may span lines")

    def test_only_the_top_level_name_of_the_block_is_its_name(self):
        nested = "x-gravitee:\n  kind: playground\n  accounts:\n    admin:\n      name: Admin user\n  name: Bank\nservices: {}\n"
        self.assertEqual(exports._named(nested, "bank"), "Bank")
        self.assertEqual(exports._named(nested.replace("  name: Bank\n", ""), "bank"), "bank", "an account's name is not the item's")
        self.assertEqual(exports._named('x-gravitee: {kind: addon, description: "Pick a name: any", name: Echo}\n', "echo"), "Echo")
        self.assertEqual(exports._named('x-gravitee: {kind: addon, description: "Pick a name: any"}\n', "echo"), "echo",
                         "a name inside a value is not the item's")


class RoundTrip(Case):
    """A project with the fixture catalog as its default one and two items of this machine: Sandbox, a playground that includes
    Scratch, an add-on that includes Echo of the default catalog. Exported, then imported back as another Lab would."""

    def setUp(self):
        fixtures.need_compose()
        fixtures.project(self, paths, project, compose, envfiles, check, files, ns="fixture", kind="git", store=True)
        fixtures.local(self, "sandbox")
        fixtures.local(self, "scratch")
        self.patch(gitx, "home", lambda: paths.ROOT.parent)   # the project's folder is where Docker shares, as a home folder is
        # The fixture's remote is a file: address, which a catalog.yml cannot name; a Lab's default catalog has a Git one
        self.patch(exports, "_address", lambda ns: "https://github.com/acme/fixture-catalog.git")
        self.patch(exports.images, "private_images", lambda pulled: {})   # no registry is asked in a test
        project.forget()
        self.addCleanup(project.forget)

    def test_the_export_carries_what_another_lab_may_not_have_and_needs_the_default_catalog(self):
        plan = exports.plan("local/sandbox")
        self.assertEqual([(one["kind"], one["id"]) for one in plan["carries"]],
                         [("playground", "local/sandbox"), ("addon", "local/scratch")])
        self.assertEqual([one["id"] for one in plan["needs"]], ["fixture"])
        self.assertEqual((plan["file"], plan["blocked"], plan["problems"]), ("sandbox.zip", [], []))

    def test_the_archive_is_a_folder_catalog_whose_includes_follow_the_items(self):
        made = exports.archive("local/sandbox")
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(made["data"]))) as archive:
            names = archive.namelist()
            manifest = archive.read("sandbox/catalog.yml").decode()
            playground = archive.read("sandbox/playgrounds/sandbox/compose.yml").decode()
            addon = archive.read("sandbox/addons/scratch/compose.yml").decode()
        self.assertTrue(all(name.startswith("sandbox/") for name in names))
        self.assertFalse([name for name in names if "/addons/hello/" in name], "the template's example is not carried")
        self.assertFalse([name for name in names if name.endswith((".env", "/.state"))])
        self.assertIn('id: "sandbox"', manifest)
        self.assertIn("  requires:\n    catalogs:\n      fixture:", manifest)
        self.assertIn("../../addons/scratch/compose.yml", playground, "an include of a carried item is written for its new place")
        self.assertIn("../../../fixture/addons/echo/compose.yml", addon, "an include of the default catalog stays as it is")

    def test_what_another_lab_would_block_is_no_file(self):
        sandbox = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "playgrounds" / "sandbox" / "compose.yml"
        sandbox.write_text(sandbox.read_text(encoding="utf-8").replace("    networks: [internal]\n", "    networks: [internal]\n"
                                                                       "    privileged: true\n", 1), encoding="utf-8")
        project.forget()
        self.assertTrue(exports.plan("local/sandbox")["blocked"])
        with self.assertRaises(CatalogError) as caught:
            exports.archive("local/sandbox")
        self.assertEqual(caught.exception.code, "export_blocked")
        self.assertTrue(caught.exception.message.startswith("Sandbox would be blocked on another Lab: "), caught.exception.message)
        self.assertTrue(caught.exception.message.endswith(". Fix: export it again to see what blocks it."), caught.exception.message)

    def test_an_export_asks_compose_nothing_more_of_the_files_the_lab_read(self):
        project.load()
        runs = []
        real = compose.run
        self.patch(compose, "run", lambda cmd, **more: runs.append(cmd) or real(cmd, **more))
        exports.plan("local/sandbox")
        exports.archive("local/sandbox")
        self.assertEqual([cmd for cmd in runs if "-f" in cmd and cmd[cmd.index("-f") + 1] == "-"], [],
                         "what each item says is what the Lab read of it: no item is read again for its label or its copy_of")

    def test_an_item_whose_include_is_gone_is_read_as_the_lab_reads_it_and_the_plan_says_it_is_blocked(self):
        scratch = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons" / "scratch" / "compose.yml"
        scratch.write_text(scratch.read_text(encoding="utf-8").replace("include:\n", "include:\n  - ../gone/compose.yml\n", 1),
                           encoding="utf-8")
        project.forget()
        self.assertIn("local/scratch", project.load()["addons"], "this Lab reads it, without what is gone")
        plan = exports.plan("local/sandbox")
        reasons = {one["name"]: one["reasons"] for one in plan["blocked"]}
        self.assertIn("Cannot be read: it includes a file that is not there", reasons["Scratch"], "as the other Lab would read it")
        with self.assertRaises(CatalogError) as caught:
            exports.archive("local/sandbox")
        self.assertEqual(caught.exception.code, "export_blocked")

    def test_a_file_that_cannot_be_read_now_is_said_as_such_though_it_was_read_before(self):
        sandbox = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "playgrounds" / "sandbox"
        (paths.ROOT / "shared").mkdir()
        (paths.ROOT / "shared" / "extra.yml").write_text("services:\n  extra:\n    image: busybox:1.36\n", encoding="utf-8")
        text = (sandbox / "compose.yml").read_text(encoding="utf-8")
        text = text.replace("include:\n", "include:\n  - ../../../../../shared/extra.yml\n", 1).replace(
            "x-gravitee: {kind: playground,", "x-gravitee: {copy_of: tour, kind: playground,", 1)
        (sandbox / "compose.yml").write_text(text, encoding="utf-8")
        project.forget()
        project.load()   # read whole: what it says is remembered
        (paths.ROOT / "shared" / "extra.yml").unlink()   # gone since, outside the catalogs: Compose cannot read the file now
        with self.assertRaises(CatalogError) as caught:
            contribute.rewritten(text, contribute.unmark(text), {"copy_of": None}, "Sandbox", "whose copy it is", sandbox)
        self.assertEqual(caught.exception.code, "label_unread", "not the Lab's writing")
        self.assertEqual(caught.exception.message, "Sandbox: its compose file cannot be read (shared/extra.yml is not there). Fix: "
                                                   "correct it so that docker compose config reads it, then try again.")

    def test_a_refusal_names_only_files_the_lab_knows_the_item_reads_and_never_what_a_file_holds(self):
        scratch = ".lab/catalogs/local/addons/scratch/compose.yml"
        said = (f"failed to read {paths.ROOT}/.lab/catalogs/local/addons/scratch/x.env: line 3: unexpected character "
                f"\"SECRET=hunter2\" in variable name, while reading {paths.ROOT}/{scratch}")
        error = contribute._unread("Sandbox", compose.ComposeError(said), [scratch])
        self.assertEqual(error.message, f"Scratch: its compose file cannot be read (Compose cannot read {scratch}). Fix: correct it "
                                        "so that docker compose config reads it, then try again.")
        self.assertEqual(contribute._unread("Sandbox", compose.ComposeError(said)).message, "Sandbox: its compose file cannot be "
                         "read (Compose cannot read it). Fix: correct it so that docker compose config reads it, then try again.")
        known = ["shared/x.yml", scratch]
        cases = {"open /Users/someone/elsewhere/compose.yml: no such file or directory": "Compose cannot read it",
                 f"open {paths.ROOT}/shared/x.yml: permission denied": "shared/x.yml cannot be opened",
                 f"open {paths.ROOT}/shared/x.yml: no such file or directory": "shared/x.yml is not there",
                 "docker did not answer within 15 s": "Compose took too long to read it",
                 "services.bad/name additional properties 'a/b' not allowed": "Compose cannot read it",
                 f"open {paths.ROOT}/.lab/kept-settings/local-1a2b3c4d/x: no such file or directory": "Compose cannot read it",
                 "yaml: line 2: did not find key": "Compose cannot read it"}
        for said, shown in cases.items():
            with self.subTest(said):
                self.assertEqual(compose.cause(said, known), shown, "a name its author chose, or a folder of the Lab's, is never said")

    def test_an_include_that_is_not_text_or_cannot_be_opened_is_a_clean_refusal(self):
        scratch = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons" / "scratch" / "compose.yml"
        inner = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons" / "inner" / "compose.yml"
        inner.parent.mkdir(parents=True)
        self.addCleanup(lambda: inner.exists() and os.chmod(inner, 0o644))
        scratch.write_text(scratch.read_text(encoding="utf-8").replace("include:\n", "include:\n  - ../inner/compose.yml\n", 1),
                           encoding="utf-8")
        for name, make in (("latin-1", lambda: inner.write_bytes(b"x-gravitee: {kind: addon, name: Caf\xe9}\nservices: {}\n")),
                           ("mode 000", lambda: (inner.write_text("services: {}\n"), os.chmod(inner, 0)))):
            with self.subTest(name):
                os.chmod(inner, 0o644) if inner.exists() else None
                make()
                project.forget()
                for call in (lambda: exports.plan("local/sandbox"), lambda: exports.archive("local/sandbox")):
                    try:
                        call()
                    except paths.NotFound:   # the Lab does not read Sandbox now: there is no such playground to export
                        pass
                    except CatalogError as error:
                        self.assertNotIn(str(paths.ROOT), error.message)
                        self.assertNotIn("codec", error.message)

    def test_whose_copy_an_item_is_never_leaves_however_its_yaml_says_it(self):
        scratch = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons" / "scratch" / "compose.yml"
        text = scratch.read_text(encoding="utf-8")
        head = "x-gravitee: {kind: addon, name: Scratch, category: Other, description: Notepad of this machine that brings Echo}\n"
        self.patch(contribute, "origins", lambda text, uid, folder=None: [])   # a copy of public content: no label is due
        self.patch(exports, "copies_of", lambda nodes: {})
        shapes = {"escaped key": head.replace("}\n", ', "copy\\x5fof": "@fixture/echo"}\n'),
                  "quote after a colon": head.replace("}\n", ", note: a:'b, copy_of: \"@fixture/echo\", z: x'}\n"),
                  "second document": None}
        for name, written in shapes.items():
            with self.subTest(name):
                scratch.write_text(text.replace(head, written) if written else text + "---\nx-gravitee: {copy_of: \"@fixture/echo\"}\n",
                                   encoding="utf-8")
                project.forget()
                try:
                    made = exports.archive("local/sandbox")
                except CatalogError as error:   # what the Lab cannot take out stops the file
                    self.assertEqual(name, "second document")
                    self.assertEqual((error.code, error.message), ("label_failed", "Scratch: the Lab cannot write whose copy it is in this "
                                     "file. Fix: write its x-gravitee as one block of plain key: value lines, in a file of one YAML "
                                     "document (in a catalog, ask its author to), then try again."))
                    continue
                with zipfile.ZipFile(io.BytesIO(base64.b64decode(made["data"]))) as archive:
                    said = compose.x_gravitee(archive.read("sandbox/addons/scratch/compose.yml").decode(), scratch.parent)
                self.assertNotIn("copy_of", said)
                self.assertEqual(said["name"], "Scratch")

    def test_a_file_without_a_private_label_keeps_the_level_of_the_template(self):
        made = exports.archive("local/sandbox")
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(made["data"]))) as archive:
            self.assertRegex(archive.read("sandbox/catalog.yml").decode(), r"(?m)^  lab: 1\b", "older Labs read it")

    def test_an_item_of_the_default_catalog_has_nothing_to_export(self):
        with self.assertRaises(CatalogError) as caught:
            exports.plan("shop")
        self.assertEqual((caught.exception.code, caught.exception.status), ("export_default", 409))

    def test_the_import_is_the_add_of_a_folder_catalog_kept_by_the_lab_and_gone_with_it(self):
        data = exports.archive("local/sandbox")["data"]
        preview = exports.receive(data)
        self.assertTrue(preview["imported"])
        self.assertEqual((preview["catalog"]["id"], preview["catalog"]["kind"]), ("sandbox", "folder"))
        self.assertEqual(preview["counts"], {"playground": 1, "setup": 0, "addon": 1})
        folder = paths.ROOT / exports.IMPORTS_DIR / "sandbox"
        self.assertTrue((folder / "catalog.yml").is_file())
        again = exports.receive(data)   # the first was never confirmed: its folder is replaced
        service.add(again["ticket"])
        self.assertEqual(registry.entry("sandbox")["kind"], "folder")
        with self.assertRaises(CatalogError) as caught:
            exports.receive(data)
        self.assertEqual(caught.exception.code, "already_imported")
        self.assertIn("is already in this Lab", caught.exception.message)
        self.assertTrue(registry.imported("sandbox"), "a catalog the Lab keeps in .lab/imports is an imported playground")
        self.assertFalse(registry.imported("fixture"))
        project.forget()
        sandbox = project.load()["playgrounds"]["@sandbox/sandbox"]
        self.assertEqual(sandbox["origin"], {"id": "imported", "name": "Imported", "version": None}, "its items say Imported")
        service.remove("sandbox")
        self.assertFalse(folder.exists(), "the files of an imported playground go with its catalog")


class ToPersonal(Case):
    """The import kept in Personal: the items written in .lab/catalogs/local with their includes, the same item used as it is, a
    different one replaced with the person's settings kept, nothing blocked let in, and the archive's folder gone after."""

    setUp = RoundTrip.setUp

    def local(self, rel):
        return paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / rel

    def test_the_check_once_in_personal_reads_the_items_placed_and_not_the_project(self):
        data = exports.archive("local/sandbox")["data"]
        scratch = self.local("addons/scratch/compose.yml")
        scratch.write_text(scratch.read_text(encoding="utf-8") + "# changed here\n", encoding="utf-8")
        preview = exports.receive(data)
        read, loads = [], []
        real_read, real_load = project.read_own, project.load
        self.patch(project, "read_own", lambda file: read.append(file) or real_read(file))
        self.patch(project, "load", lambda *a, **k: loads.append(read[:]) or real_load(*a, **k))
        exports.to_personal(preview["ticket"])
        self.assertEqual(read, [".lab/catalogs/local/addons/scratch/compose.yml"], "the one item it wrote")
        self.assertFalse([one for one in loads if one], "the project is not read again once it is placed")

    def test_the_same_items_are_used_and_a_different_one_is_replaced_keeping_its_settings(self):
        data = exports.archive("local/sandbox")["data"]
        preview = exports.receive(data)
        self.assertEqual(preview["personal"], {"replaces": [], "same": ["Sandbox", "Scratch"]})
        scratch = self.local("addons/scratch/compose.yml")
        original = scratch.read_text(encoding="utf-8")
        scratch.write_text(original + "# changed here\n", encoding="utf-8")
        self.local("addons/scratch/.env").write_text("SCRATCH_NOTES_VERSION=1.35\n", encoding="utf-8")
        preview = exports.receive(data)
        self.assertEqual(preview["personal"], {"replaces": ["Scratch"], "same": ["Sandbox"]})
        done = exports.to_personal(preview["ticket"])
        self.assertEqual(done["playground"], "local/sandbox")
        self.assertEqual(sorted(one["id"] for one in done["items"]), ["local/sandbox", "local/scratch"])
        self.assertEqual(scratch.read_text(encoding="utf-8"), original, "the file's version replaces the one of this machine")
        self.assertEqual(self.local("addons/scratch/.env").read_text(encoding="utf-8"), "SCRATCH_NOTES_VERSION=1.35\n",
                         "the person's settings stay")
        self.assertFalse((paths.ROOT / exports.IMPORTS_DIR / "sandbox").exists(), "nothing is left of the import")
        self.assertIsNone(registry.entry("sandbox"), "no catalog is added")

    def test_a_new_item_is_written_in_personal_with_its_includes(self):
        data = exports.archive("local/sandbox")["data"]
        for rel in ("playgrounds/sandbox", "addons/scratch"):
            shutil.rmtree(self.local(rel))
        preview = exports.receive(data)
        self.assertEqual(preview["personal"], {"replaces": [], "same": []})
        exports.to_personal(preview["ticket"])
        self.assertIn("../../addons/scratch/compose.yml", self.local("playgrounds/sandbox/compose.yml").read_text(encoding="utf-8"))
        self.assertIn("../../../fixture/addons/echo/compose.yml", self.local("addons/scratch/compose.yml").read_text(encoding="utf-8"))
        self.assertTrue(self.local("playgrounds/sandbox/guide.md").is_file(), "its other files come with it")

    def test_what_the_preview_found_blocked_cannot_go_to_personal(self):
        preview = exports.receive(exports.archive("local/sandbox")["data"])
        service.note(preview["ticket"], blocked=["Scratch"])
        with self.assertRaises(CatalogError) as caught:
            exports.to_personal(preview["ticket"])
        self.assertEqual(caught.exception.code, "blocked_personal")
        self.assertEqual(caught.exception.message, "Scratch cannot run: import to Imported, where it stays blocked.")

    def test_a_ticket_that_is_no_import_is_refused(self):
        with self.assertRaises(CatalogError) as caught:
            exports.to_personal("nothing")
        self.assertEqual(caught.exception.code, "ticket_expired")


class FromCatalogs(Case):
    """Sandbox again, whose Scratch now also includes Ledger of Partner, a private catalog of Git: the export carries Ledger, says
    that it is private content, and says where it comes from; a Lab that has Partner uses its own Ledger at the import, one that
    has not keeps the copy."""

    LEDGER = "../../../partner/addons/ledger/compose.yml"

    def setUp(self):
        fixtures.need_compose()
        fixtures.project(self, paths, project, compose, envfiles, check, files, ns="fixture", kind="git", store=True)
        shutil.copy(fixtures.CORE.parent / "contract.lock", self.root / "contract.lock")   # as every Lab has it: it says the label's level
        fixtures.install(self, "partner", kind="git", audience="private")
        fixtures.local(self, "sandbox")
        fixtures.local(self, "scratch")
        scratch = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons" / "scratch" / "compose.yml"
        text = scratch.read_text(encoding="utf-8")
        scratch.write_text(text.replace("include:\n", f"include:\n  - {self.LEDGER}\n", 1), encoding="utf-8")
        self.patch(gitx, "home", lambda: paths.ROOT.parent)
        self.patch(exports, "_address", lambda ns: f"https://github.com/acme/{ns}-catalog.git")
        self.asked = []
        self.patch(exports.images, "private_images",
                   lambda pulled: self.asked.append(sorted(pulled)) or {"registry.acme.example": ["registry.acme.example/ledger:1"]})
        project.forget()
        self.addCleanup(project.forget)

    def test_the_export_carries_what_comes_from_a_private_catalog_and_says_so(self):
        plan = exports.plan("local/sandbox")
        self.assertEqual(sorted(one["id"] for one in plan["carries"]), ["@partner/ledger", "local/sandbox", "local/scratch"])
        self.assertEqual(plan["private"], [{"key": root("partner"), "name": "Partner fixture", "items": ["Ledger"], "item_ids": ["@partner/ledger"]}])
        self.assertEqual(plan["blocked"], [])

    def test_a_copy_of_an_item_of_a_private_catalog_is_private_content_too_and_the_file_says_whose_content_it_is(self):
        moving.copy("addon", "@partner/ledger")
        scratch = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons" / "scratch" / "compose.yml"
        scratch.write_text(scratch.read_text(encoding="utf-8").replace(self.LEDGER, "../ledger/compose.yml"), encoding="utf-8")
        project.forget()
        plan = exports.plan("local/sandbox")
        self.assertIn("local/ledger", [one["id"] for one in plan["carries"]])
        self.assertEqual(plan["private"], [{"key": root("partner"), "name": "Partner fixture", "items": ["Ledger"], "item_ids": ["local/ledger"]}])
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(exports.archive("local/sandbox", private=shown(exports.plan("local/sandbox")))["data"]))) as archive:
            text = archive.read("sandbox/addons/ledger/compose.yml").decode()
        self.assertIn('private_from: ["Partner fixture"]', text, "the label goes with the content: the Lab that imports it keeps it")
        self.assertNotIn("copy_of", text, "whose copy it is says nothing on another Lab")

    def test_a_copy_of_a_private_item_shared_to_a_folder_of_the_person_is_still_private_content(self):
        fixtures.install(self, "lab-new", "folder", audience="private", lab=1)   # a folder of the person's
        moving.copy("addon", "@partner/ledger")
        ledger = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons" / "ledger" / "compose.yml"
        ledger.write_text(ledger.read_text(encoding="utf-8").replace("partner-ledger", "own-ledger").replace("8192", "8291"),
                          encoding="utf-8")   # the person's own version, which the original no longer clashes with
        project.forget()
        self.assertEqual(contribute.share(project.load(), "addon", "local/ledger", "lab-new"), {"copied": "Newer Lab fixture"})
        shutil.rmtree(ledger.parent)
        scratch = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons" / "scratch" / "compose.yml"
        scratch.write_text(scratch.read_text(encoding="utf-8").replace(self.LEDGER, "../../../lab-new/addons/ledger/compose.yml"),
                           encoding="utf-8")
        project.forget()
        plan = exports.plan("local/sandbox")
        self.assertIn("@lab-new/ledger", [one["id"] for one in plan["carries"]])
        self.assertEqual(plan["private"], [{"key": root("partner"), "name": "Partner fixture", "items": ["Ledger"], "item_ids": ["@lab-new/ledger"]}])

    def shared_and_copied_back(self):
        """Partner's Ledger copied to Personal, changed, shared to Lab new (a folder of the person's), then that one copied back to
        Personal, which Scratch includes: a copy of a share of a copy."""
        fixtures.install(self, "lab-new", "folder", audience="private", lab=1)
        moving.copy("addon", "@partner/ledger")
        ledger = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons" / "ledger" / "compose.yml"
        ledger.write_text(ledger.read_text(encoding="utf-8").replace("partner-ledger", "own-ledger").replace("8192", "8291"),
                          encoding="utf-8")
        project.forget()
        contribute.share(project.load(), "addon", "local/ledger", "lab-new")
        shutil.rmtree(ledger.parent)
        project.forget()
        moving.copy("addon", "@lab-new/ledger")
        self.assertIn('copy_of: "@lab-new/ledger"', ledger.read_text(encoding="utf-8"), "the copy is the twin of what it was copied from")
        scratch = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons" / "scratch" / "compose.yml"
        scratch.write_text(scratch.read_text(encoding="utf-8").replace(self.LEDGER, "../ledger/compose.yml"), encoding="utf-8")
        project.forget()

    def test_private_content_keeps_its_label_through_a_copy_of_a_share_of_a_copy(self):
        self.shared_and_copied_back()
        partner = [{"key": root("partner"), "name": "Partner fixture", "items": ["Ledger"], "item_ids": ["local/ledger"]}]
        self.assertEqual(exports.plan("local/sandbox")["private"], partner)
        with self.assertRaises(CatalogError) as caught:
            exports.archive("local/sandbox", private=[])
        self.assertEqual(caught.exception.code, "export_private")

    def test_a_copy_without_a_label_is_content_of_the_catalog_its_copy_of_names_one_step_only(self):
        fixtures.install(self, "lab-new", kind="git", audience="private", lab=1)   # a private catalog of the person's team
        ledger = paths.ROOT / paths.CATALOGS_DIR / "lab-new" / "addons" / "ledger" / "compose.yml"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text('x-gravitee: {copy_of: "@partner/ledger", kind: addon, name: Ledger}\nservices: {}\n', encoding="utf-8")
        origins = lambda said: contribute.origins("x-gravitee: {" + said + ", kind: addon, name: L}\n", "local/l")   # noqa: E731
        self.assertEqual(origins('copy_of: "@lab-new/ledger"'), [(root("lab-new"), "Newer Lab fixture")], "never what that item says")
        self.assertEqual(origins('copy_of: "echo"'), [], "an older copy of the default catalog's item: Public")
        self.assertEqual(origins('copy_of: "@gone/ledger"'), [("", contribute.UNKNOWN)], "what this machine does not have")
        self.assertEqual(origins('copy_of: "../../x"'), [("", contribute.UNKNOWN)])

    def test_an_import_keeps_the_label_of_private_content(self):
        preview = exports.receive(zipped({
            "carried/catalog.yml": EVIL_CATALOG.replace("{id}", "carried").encode(),
            "carried/playgrounds/carried/compose.yml": ('x-gravitee: {private_from: "Partner fixture", kind: playground, name: Carried, '
                                                        'category: Other, description: x}\nservices: {}\n').encode()}))
        exports.to_personal(preview["ticket"])
        home = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL
        self.assertIn('private_from: "Partner fixture"', (home / "playgrounds/carried/compose.yml").read_text(encoding="utf-8"),
                      "carried as it is")

    def carried(self, label):
        said = f'private_from: "{label}", ' if label else ""
        return zipped({
            "carried/catalog.yml": EVIL_CATALOG.replace("{id}", "carried").encode(),
            "carried/playgrounds/carried/compose.yml": ("x-gravitee: {kind: playground, name: Carried, category: Other, description: x}\n"
                                                        "include:\n  - ../../addons/books/compose.yml\nservices: {}\n").encode(),
            "carried/addons/books/compose.yml": (f"x-gravitee: {{{said}kind: addon, name: Books, category: Other, description: x}}\n"
                                                 "services:\n  books:\n    image: busybox:1.36\n    networks: [internal]\n").encode()})

    def test_a_label_is_never_taken_for_the_id_of_the_catalog_it_is_in(self):
        service.add(exports.receive(self.carried("carried"))["ticket"])   # a private catalog named as this import's id
        project.forget()
        self.assertEqual(exports.copies_of([("addon", "@carried/books")]), {("addon", "@carried/books"): [("", "carried")]})

    def test_a_copy_says_its_private_origin_as_it_was_when_copied_whatever_comes_later_at_that_id(self):
        service.add(exports.receive(self.carried("Partner fixture"))["ticket"])
        project.forget()
        moving.copy("addon", "@carried/books")
        mine = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons/books/compose.yml"
        self.assertIn('private_from: ["Partner fixture"]', mine.read_text())
        self.assertIn('copy_of: "@carried/books"', mine.read_text(), "and whose twin it is, on this Lab")
        project.forget()
        service.remove("carried")
        project.forget()
        service.add(exports.receive(self.carried(None))["ticket"])   # another file, of the same id, that says nothing
        self.assertEqual(contribute.origins(mine.read_text(), "local/books"), [("", "Partner fixture")])

    def ledger(self, head):
        """Partner's Ledger with its x-gravitee block written as `head` says (the rest of its file as the fixture has it)."""
        text = open("tests/data/catalogs/partner/addons/ledger/compose.yml").read()
        text = head + text[text.index("\nservices:") + 1:]
        fixtures.install(self, "partner", kind="git", audience="private", files={"addons/ledger/compose.yml": text})
        project.forget()

    def test_the_private_label_is_written_and_read_back_or_the_export_is_refused_naming_the_item(self):
        heads = {"apostrophe": "x-gravitee: {kind: addon, name: Ledger, category: Other, description: Keeps a partner's entries}\n",
                 "next line": "x-gravitee:\n  {kind: addon, name: Ledger, category: Other, description: Keeps entries}\n",
                 "tag": "x-gravitee: !!map {kind: addon, name: Ledger, category: Other, description: Keeps entries}\n"}
        for name, head in heads.items():
            with self.subTest(name):
                self.ledger(head)
                try:
                    made = exports.archive("local/sandbox", private=shown(exports.plan("local/sandbox")))
                except CatalogError as error:
                    self.assertEqual((error.code, error.message), ("label_failed", "Ledger: the Lab cannot write its private label and "
                                     "whose copy it is in this file. Fix: write its x-gravitee as one block of plain key: value lines, "
                                     "in a file of one YAML document (in a catalog, ask its author to), then try again."))
                    continue
                with zipfile.ZipFile(io.BytesIO(base64.b64decode(made["data"]))) as archive:
                    text = archive.read("sandbox/addons/ledger/compose.yml").decode()
                said = compose.x_gravitee(text, self.root)
                self.assertEqual((said["private_from"], said["description"]), (["Partner fixture"], said["description"]))

    def test_an_apostrophe_in_a_flow_block_is_no_quote_and_the_label_is_written(self):
        self.ledger("x-gravitee: {kind: addon, name: Ledger, category: Other, description: Keeps a partner's entries}\n")
        made = exports.archive("local/sandbox", private=shown(exports.plan("local/sandbox")))
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(made["data"]))) as archive:
            said = compose.x_gravitee(archive.read("sandbox/addons/ledger/compose.yml").decode(), self.root)
        self.assertEqual((said["private_from"], said["description"]), (["Partner fixture"], "Keeps a partner's entries"))

    def test_a_file_with_a_private_label_says_to_older_labs_the_level_the_label_appeared_at(self):
        """The lines of older Gravitee Labs in the archive's catalog.yml are true for its files: a private label is a key older
        Labs ignore, so they are told to refuse the file; an archive without one stays one they read."""
        level = understood.lock().rows[("unit.key", "x-gravitee.private_from")]
        made = exports.archive("local/sandbox", private=shown(exports.plan("local/sandbox")))
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(made["data"]))) as archive:
            text = archive.read("sandbox/catalog.yml").decode()
        self.assertIn(f"  lab: {level}", text)
        self.assertGreater(level, 1, "the label came after the first level")
        self.assertEqual(contribute.files_lab({"addons/a/compose.yml": ("100644", b"x-gravitee: {kind: addon, name: A}\n")}), 1)

    def test_an_export_of_a_label_by_a_lab_that_cannot_read_its_contract_lock_is_refused(self):
        """Without its contract.lock the Lab does not know the level of the label: it says so rather than a lower one."""
        (self.root / "contract.lock").unlink()
        with self.assertRaises(CatalogError) as raised:
            exports.archive("local/sandbox", private=shown(exports.plan("local/sandbox")))
        self.assertEqual((raised.exception.code, raised.exception.message),
                         ("lock_unread", "Gravitee Lab cannot read its contract.lock. Fix: make update."))

    LAYOUTS = {
        "two spaces": "x-gravitee-catalog:\n  schema: 1\n  id: partner\n  name: Partner fixture\n  description: d\n  version: 2.0.0\n"
                      "  lab: 1\n  license: AGPL-3.0-only\n",
        "four spaces": "x-gravitee-catalog:\n    schema: 1\n    id: partner\n    name: Partner fixture\n    description: d\n"
                       "    version: 2.0.0\n    lab: 1\n    license: AGPL-3.0-only\n",
        "comment": "# the catalog\nx-gravitee-catalog:\n  schema: 1\n  id: partner\n  name: Partner fixture\n  description: d\n"
                   "  version: 2.0.0\n  lab: 1   # the level\n  license: AGPL-3.0-only\n",
        "crlf": ("x-gravitee-catalog:\n  schema: 1\n  id: partner\n  name: Partner fixture\n  description: d\n  version: 2.0.0\n"
                 "  lab: 1\n  license: AGPL-3.0-only\n").replace("\n", "\r\n"),
        "tab after the colon": "x-gravitee-catalog:\n  schema: 1\n  id: partner\n  name: Partner fixture\n  description: d\n"
                               "  version: 2.0.0\n  lab:\t1\n  license: AGPL-3.0-only\n",
        "bom": "\ufeffx-gravitee-catalog:\n  schema: 1\n  id: partner\n  name: Partner fixture\n  description: d\n  version: 2.0.0\n"
               "  lab: 1\n  license: AGPL-3.0-only\n",
    }

    def test_comments_and_blank_lines_around_lab_stay_where_they_are(self):
        doc = (Path(__file__).resolve().parents[3] / "docs" / "writing-a-catalog.md").read_text(encoding="utf-8")
        example = re.search(r"```yaml\n(x-gravitee-catalog:\n.*?)```", doc, re.S).group(1)   # the guide's, as an author copies it
        two = self.LAYOUTS["two spaces"]
        cases = {"a comment line under it": two.replace("  lab: 1\n", "  lab: 1\n  # the level it needs\n"),
                 "a comment line over it": two.replace("  lab: 1\n", "  # the level it needs\n  lab: 1\n"),
                 "blank lines around it": two.replace("  lab: 1\n", "\n  lab: 1\n\n"),
                 "a comment far under it": two.replace("  lab: 1\n", "  lab: 1\n                      # more\n")}
        for name, text in cases.items():
            with self.subTest(name):
                raised = contribute._raised(text, 2)
                self.assertEqual(raised, text.replace("lab: 1", "lab: 2", 1))
                self.assertEqual(contribute._strict(raised)["lab"], 2)
        raised = contribute._raised(example, 2)   # the guide's has no lines for older Labs: they are written, the rest stays
        self.assertEqual(contribute._strict(raised)["lab"], 2)
        self.assertEqual(manifest.with_legacy(example, 2), raised)

    def test_every_layout_is_written_and_read_back_as_check_reads_it_or_refused(self):
        """Above the first level a share writes what older Labs need to refuse the catalog, verified by reading it back as
        `catalog.py check` and the Add read it (Compose), or it refuses: never a lower `lab` left in place. Over the layouts an
        author may write, YAML forms of the number included."""
        for name, (text, written) in RAISED_LAYOUTS.items():
            with self.subTest(name):
                before = contribute._strict(text)
                if not written:
                    with self.assertRaises(contribute.NotRaised):
                        contribute._raised(text, 2)
                    continue
                raised = contribute._raised(text, 2)
                self.assertIsNotNone(raised, "never nothing to change while lab says less")
                after = contribute._strict(raised)
                self.assertEqual(after["lab"], 2)
                self.assertTrue(contribute._same_but_lab(before, after), "nothing else of what the file says changes")
                self.assertIsNone(contribute._raised(raised, 2), "once")
        self.assertIsNone(contribute._raised(RAISED_LAYOUTS["plain"][0].replace("lab: 1", "lab: 3"), 2), "never lowered")

    def test_a_copy_of_an_item_whose_include_is_not_text_or_cannot_be_opened_is_a_clean_refusal(self):
        text = open("tests/data/catalogs/partner/addons/ledger/compose.yml").read()
        fixtures.install(self, "partner", kind="git", audience="private",
                         files={"addons/ledger/compose.yml": text.replace("services:", "include:\n  - ../inner/compose.yml\nservices:", 1),
                                "addons/inner/compose.yml": "services: {}\n"})
        inner = paths.ROOT / paths.CATALOGS_DIR / "partner" / "addons" / "inner" / "compose.yml"
        self.addCleanup(lambda: os.chmod(inner, 0o644))
        for name, make in (("latin-1", lambda: inner.write_bytes(b"x-gravitee: {kind: addon, name: Caf\xe9}\nservices: {}\n")),
                           ("mode 000", lambda: (inner.write_text("services: {}\n"), os.chmod(inner, 0)))):
            with self.subTest(name):
                os.chmod(inner, 0o644)
                make()
                project.forget()
                try:
                    moving.copy("addon", "@partner/ledger")
                except CatalogError as error:
                    self.assertNotIn(str(paths.ROOT), error.message)
                    self.assertNotIn("codec", error.message)
                    self.assertNotIn("\ufffd", error.message)
                shutil.rmtree(paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons" / "ledger", ignore_errors=True)

    def test_a_copy_says_the_id_of_the_private_catalog_with_its_name(self):
        moving.copy("addon", "@partner/ledger")
        copied = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons" / "ledger" / "compose.yml"
        said = compose.x_gravitee(copied.read_text(), copied.parent, fresh=True)
        self.assertEqual((said["private_from"], said["private_id"], said["copy_of"]), (["Partner fixture"], [root("partner")], "@partner/ledger"))
        made = exports.archive("local/sandbox", private=shown(exports.plan("local/sandbox")))
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(made["data"]))) as archive:
            shipped = compose.x_gravitee(archive.read("sandbox/addons/ledger/compose.yml").decode(), self.root)
        self.assertEqual((shipped["private_from"], shipped["private_id"]), (["Partner fixture"], [root("partner")]), "an export says it too")

    def test_a_label_says_any_name_of_a_catalog_as_it_is(self):
        names = ['Partner "core"', "C:\\labs\\partner", "Partner's", "Partner: core", "Partner # core", "Zürich 東京", "  padded  "]
        for name in names:
            with self.subTest(name=name):
                self.patch(contribute, "_catalog_name", lambda ns, name=name: name)
                made = exports.archive("local/sandbox", private=shown(exports.plan("local/sandbox")))
                with zipfile.ZipFile(io.BytesIO(base64.b64decode(made["data"]))) as archive:
                    said = compose.x_gravitee(archive.read("sandbox/addons/ledger/compose.yml").decode(), self.root)
                self.assertEqual(said["private_from"], [name])

    def test_a_copy_whose_block_the_lab_cannot_write_is_refused_naming_the_item(self):
        self.ledger("x-gravitee: null\n")
        with self.assertRaises(CatalogError) as caught:
            moving.copy("addon", "@partner/ledger")
        self.assertEqual(caught.exception.code, "label_failed")
        self.assertTrue(caught.exception.message.startswith("ledger: the Lab cannot write that it is your copy and its private label"),
                        caught.exception.message)
        self.assertFalse((paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons/ledger").exists())

    def test_an_image_the_other_lab_pulls_from_a_registry_that_asks_for_a_login_is_said(self):
        plan = exports.plan("local/sandbox")
        self.assertEqual(plan["registries"], [{"registry": "registry.acme.example", "images": ["registry.acme.example/ledger:1"]}])
        self.assertEqual(self.asked[0], ["busybox:1.36", "busybox:1.36"], "the images of the carried services the other Lab pulls")

    def test_the_file_is_made_once_the_person_saw_what_leaves_from_a_private_catalog(self):
        with self.assertRaises(CatalogError) as caught:
            exports.archive("local/sandbox")
        self.assertEqual((caught.exception.code, caught.exception.message), ("export_private", "It now carries private content of "
                                                                             "Partner fixture. Fix: export it again to see what leaves with it."))
        self.assertTrue(exports.archive("local/sandbox", private=shown(exports.plan("local/sandbox")))["data"])

    def test_the_archive_says_where_each_item_of_a_catalog_comes_from(self):
        made = exports.archive("local/sandbox", private=shown(exports.plan("local/sandbox")))
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(made["data"]))) as archive:
            said = json.loads(archive.read("sandbox/export.json"))
            scratch = archive.read("sandbox/addons/scratch/compose.yml").decode()
        self.assertEqual([(one["kind"], one["item"], one["catalog"]) for one in said["items"]], [("addon", "ledger", "partner")])
        self.assertIn("  - ../ledger/compose.yml\n", scratch, "the copy is what Scratch includes in the file")

    def test_a_lab_that_has_the_catalog_uses_its_own_item_instead_of_a_second_copy(self):
        preview = exports.receive(exports.archive("local/sandbox", private=shown(exports.plan("local/sandbox")))["data"])
        self.assertEqual(preview["reused"], [{"name": "Ledger", "catalog": "Partner fixture"}])
        self.assertEqual(preview["counts"], {"playground": 1, "setup": 0, "addon": 1})
        folder = paths.ROOT / exports.IMPORTS_DIR / "sandbox"
        self.assertFalse((folder / "addons" / "ledger").exists())
        self.assertIn(self.LEDGER, (folder / "addons" / "scratch" / "compose.yml").read_text(encoding="utf-8"))
        self.assertIn("      partner:\n", (folder / "catalog.yml").read_text(encoding="utf-8"))
        self.assertEqual(preview.get("blocked") or [], [])

    def test_a_lab_without_the_catalog_keeps_the_copy(self):
        data = exports.archive("local/sandbox", private=shown(exports.plan("local/sandbox")))["data"]
        entry = registry.entry
        self.patch(exports.registry, "entry", lambda ns: None if ns == "partner" else entry(ns))
        preview = exports.receive(data)
        self.assertEqual(preview["reused"], [])
        self.assertEqual(preview["counts"], {"playground": 1, "setup": 0, "addon": 2})

    def test_a_playground_this_lab_has_already_is_not_imported_twice(self):
        folder = paths.ROOT / exports.IMPORTS_DIR / "tour-copy"
        (folder / "playgrounds" / "tour").mkdir(parents=True)
        (folder / "catalog.yml").write_text(CATALOG, encoding="utf-8")
        (folder / "playgrounds" / "tour" / "compose.yml").write_text("services: {}\n", encoding="utf-8")
        (folder / exports.ORIGINS).write_text(json.dumps({"items": [
            {"kind": "playground", "item": "tour", "name": "Tour", "catalog": "fixture"}]}), encoding="utf-8")
        with self.assertRaises(CatalogError) as caught:
            exports._reuse(folder, "tour-copy")
        self.assertEqual(caught.exception.code, "already_here")
        self.assertEqual(caught.exception.message, "Tour is already in this Lab, from Fixture catalog.")


class Registries(Case):
    """exports._registries: the platform's images are asked about as the other Lab will pull them, from the latest tested set of the
    item (its registry and its version), else its required version from the platform's default registry; not as this Lab runs them."""

    def test_the_tested_set_says_the_registry_and_the_version_the_other_lab_pulls(self):
        component = {"version_key": "APIM_VERSION", "registry_key": "APIM_REGISTRY", "registry_default": "graviteeio",
                     "repository": "apim-management-api"}
        asked = []
        self.patch(exports.images, "components", lambda found: [component])
        self.patch(exports.images, "private_images", lambda pulled: asked.append(sorted(pulled)) or {})
        found = {"services": {"apim": {"image": "graviteeio/apim-management-api:4.12.0", "built": False,
                                       "owner": {"kind": "platform", "id": "api-management"}}}}
        tested = {"playground": {"local/p": {"tested": [{"APIM_VERSION": "4.12.0"},
                                                        {"APIM_VERSION": "amp-demo-latest", "APIM_REGISTRY": "acme.azurecr.io"}]}}}
        exports._registries(found, [("playground", "local/p")], tested)
        required = {"playground": {"local/p": {"requires": {"images": {"APIM_VERSION": "4.13.0"}}}}}
        exports._registries(found, [("playground", "local/p")], required)
        self.assertEqual(asked, [["acme.azurecr.io/apim-management-api:amp-demo-latest"], ["graviteeio/apim-management-api:4.13.0"]])


# A file someone else made: its catalog, and a playground that runs a privileged container, with or without a reason, and may
# include an item of a catalog it needs
EVIL_CATALOG = ('x-gravitee-catalog:\n  schema: 1\n  id: "{id}"\n  name: "Evil"\n  description: "A file someone else made"\n'
                '  license: MIT\n  version: 1.0.0\n  lab: 1\n')
NEEDS_PARTNER = '  requires:\n    catalogs:\n      partner: {name: Partner fixture, url: "https://github.com/acme/partner.git", min: 1.0.0}\n'


def evil_playground(include="", host_access=False, privileged=True, name="Evil"):
    reason = ", host_access: {evil-root: Runs a service manager}" if host_access else ""
    return (f"x-gravitee: {{kind: playground, name: {name}, category: Other, description: A playground of a file{reason}}}\n"
            + (f"include:\n  - {include}\n" if include else "")
            + "services:\n  evil-root:\n    image: busybox:1.36\n"
            + ('    privileged: true\n    volumes: ["/:/host", "/var/run/docker.sock:/var/run/docker.sock"]\n'
               if privileged and not host_access else "    privileged: true\n" if privileged else "")
            + "    networks: [internal]\n")


def evil_zip(top="evil", catalog_id=None, playground=None, needs=False, extra=None):
    manifest = EVIL_CATALOG.replace("{id}", catalog_id or top) + (NEEDS_PARTNER if needs else "")
    return zipped({f"{top}/catalog.yml": manifest.encode(),
                   f"{top}/playgrounds/evil/compose.yml": (playground or evil_playground()).encode(), **(extra or {})})


class Untrusted(Case):
    """Files that someone else made, imported on a Lab with the fixture catalog: Personal, which nothing judges, takes only what the
    preview judged, as it judged it, and what uses this machine once the person allowed it."""

    LEDGER = "../../../partner/addons/ledger/compose.yml"

    def setUp(self):
        fixtures.need_compose()
        fixtures.project(self, paths, project, compose, envfiles, check, files, ns="fixture", kind="git", store=True)
        self.patch(gitx, "home", lambda: paths.ROOT.parent)
        project.forget()
        self.addCleanup(project.forget)

    def personal(self, rel="playgrounds/evil/compose.yml"):
        return paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / rel

    def refused(self, call, code):
        with self.assertRaises(CatalogError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        return caught.exception.message

    def test_an_item_that_needs_a_catalog_not_added_is_judged_by_nothing_and_cannot_go_to_personal(self):
        preview = exports.receive(evil_zip(playground=evil_playground(include=self.LEDGER), needs=True))
        self.assertEqual(preview["blocked"], [{"kind": "playground", "id": "@evil/evil", "name": "Evil",
                                               "reasons": ["Needs Partner fixture, which is not added"]}])
        message = self.refused(lambda: exports.to_personal(preview["ticket"]), "blocked_personal")
        self.assertEqual(message, "Evil cannot run: import to Imported, where it stays blocked.")
        self.assertFalse(self.personal().exists())

    def test_an_archive_whose_folder_is_not_its_catalogs_id_is_refused(self):
        message = self.refused(lambda: exports.receive(evil_zip(top="evil", catalog_id="other")), "import_invalid")
        self.assertEqual(message, f"{exports.NOT_A_PLAYGROUND} Its folder, evil, is not the id of its catalog, other.")
        self.assertFalse((paths.ROOT / exports.IMPORTS_DIR / "evil").exists())

    def test_a_preview_takes_to_personal_only_the_files_it_judged(self):
        calm = evil_playground(privileged=False)
        first = exports.receive(evil_zip(playground=calm))
        self.assertEqual(first["blocked"], [])
        second = exports.receive(evil_zip())   # the same playground again, another file: the first preview is let go
        self.assertTrue(second["blocked"])
        self.refused(lambda: exports.to_personal(first["ticket"]), "ticket_expired")
        third = exports.receive(evil_zip(playground=calm))
        (paths.ROOT / exports.IMPORTS_DIR / "evil" / "playgrounds" / "evil" / "compose.yml").write_text(evil_playground())
        message = self.refused(lambda: exports.to_personal(third["ticket"]), "import_changed")
        self.assertEqual(message, "The files of this import changed after its preview. Fix: choose its file again.")
        self.assertFalse(self.personal().exists())

    def test_the_import_of_another_file_leaves_a_preview_that_waits(self):
        calm = evil_playground(privileged=False)
        first = exports.receive(evil_zip(playground=calm))
        exports.receive(evil_zip(top="other", playground=calm.replace("name: Evil", "name: Other")))
        self.assertEqual(exports.to_personal(first["ticket"])["playground"], "local/evil")
        self.assertTrue(self.personal().is_file())

    def test_what_uses_this_machine_goes_to_personal_once_allowed(self):
        preview = exports.receive(evil_zip(playground=evil_playground(host_access=True)))
        self.assertEqual((preview["blocked"], [one["what"] for one in preview["rows"]["uses_machine"]]), ([], [["runs privileged"]]))
        message = self.refused(lambda: exports.to_personal(preview["ticket"]), "allow_machine")
        self.assertEqual(message, "Evil uses this machine: allow it to import it to Personal.")
        self.assertFalse(self.personal().exists())
        self.assertEqual(exports.to_personal(preview["ticket"], allow=True)["playground"], "local/evil")

    OTHER = ("x-gravitee: {kind: playground, name: Totally Different, category: Other, description: From someone else}\n"
             "services:\n  evil-root:\n    image: busybox:1.36\n    volumes: [\"./secrets:/stolen:ro\"]\n    networks: [internal]\n")

    def kept_from_a_removal(self, data):
        """The import of `data` kept as a catalog, with settings and a secret of the person's, then removed keeping them."""
        service.add(exports.receive(data)["ticket"])
        folder = paths.ROOT / exports.IMPORTS_DIR / "evil"
        (folder / "playgrounds/evil/.env").write_text("EVIL_LEVEL=debug\n")
        (folder / "playgrounds/evil/secrets").mkdir()
        (folder / "playgrounds/evil/secrets/key").write_text("the person's key\n")
        project.forget()
        self.assertEqual(service.remove("evil"), {"warning": None})
        self.assertFalse(folder.exists(), "the import goes")
        kept = [path for path in (paths.ROOT / exports.KEPT_DIR).glob("evil-*")]
        self.assertEqual(len(kept), 1)
        self.assertEqual((kept[0] / "playgrounds/evil/secrets/key").read_text(), "the person's key\n", "the settings stay apart")
        return folder, kept[0]

    def test_settings_kept_from_a_removed_import_go_back_to_the_same_file_only(self):
        data = evil_zip(playground=evil_playground(privileged=False))
        folder, kept = self.kept_from_a_removal(data)
        where = f"{exports.KEPT_DIR}/{kept.name}"
        # Anyone can name a file evil: another one does not take them, and the preview says they are there and not used
        other = exports.receive(evil_zip(playground=self.OTHER))
        self.assertEqual(other["kept"], {"used": False, "where": where})
        self.assertFalse((folder / "playgrounds/evil/secrets").exists())
        exports.to_personal(other["ticket"])
        self.assertFalse(self.personal("playgrounds/evil/secrets").exists())
        self.assertTrue((kept / "playgrounds/evil/secrets/key").is_file(), "they stay where they are")
        shutil.rmtree(self.personal("playgrounds/evil"))
        project.forget()
        # The same file again takes them
        again = exports.receive(data)
        self.assertEqual(again["kept"], {"used": True, "where": where})
        service.add(again["ticket"])
        self.assertEqual((folder / "playgrounds/evil/.env").read_text(), "EVIL_LEVEL=debug\n")
        self.assertEqual((folder / "playgrounds/evil/secrets/key").read_text(), "the person's key\n")
        self.assertFalse(kept.exists())
        project.forget()
        service.remove("evil", keep_settings=False)
        self.assertFalse(folder.exists() or list((paths.ROOT / exports.KEPT_DIR).glob("evil-*")))

    def test_settings_kept_from_another_file_of_the_same_name_do_not_replace_those_kept_before(self):
        first = evil_zip(playground=evil_playground(privileged=False))
        _, kept = self.kept_from_a_removal(first)
        service.add(exports.receive(evil_zip(playground=self.OTHER))["ticket"])
        (paths.ROOT / exports.IMPORTS_DIR / "evil" / "playgrounds/evil/.env").write_text("OTHER=1\n")
        project.forget()
        service.remove("evil")
        self.assertEqual(len(list((paths.ROOT / exports.KEPT_DIR).glob("evil-*"))), 2, "each removal keeps its own")
        again = exports.receive(first)
        self.assertEqual(again["kept"], {"used": True, "where": f"{exports.KEPT_DIR}/{kept.name}"})
        service.add(again["ticket"])
        self.assertEqual((paths.ROOT / exports.IMPORTS_DIR / "evil" / "playgrounds/evil/.env").read_text(), "EVIL_LEVEL=debug\n")

    def test_kept_settings_that_personal_does_not_take_stay_and_are_said(self):
        data = evil_zip(playground=evil_playground(privileged=False))
        _, kept = self.kept_from_a_removal(data)
        self.personal("playgrounds/evil").mkdir(parents=True)
        self.personal("playgrounds/evil/.env").write_text("EVIL_LEVEL=info\n")
        done = exports.to_personal(exports.receive(data)["ticket"])
        self.assertEqual(done["kept"], {"items": ["Evil"], "where": f"{exports.KEPT_DIR}/{kept.name}"})
        self.assertEqual(self.personal("playgrounds/evil/.env").read_text(), "EVIL_LEVEL=info\n", "Personal's own stay")
        self.assertEqual(self.personal("playgrounds/evil/secrets/key").read_text(), "the person's key\n", "where it had none")
        self.assertTrue((kept / "playgrounds/evil/.env").is_file(), "what did not go anywhere is not deleted")

    def test_a_marker_in_the_file_keeps_nothing(self):
        preview = exports.receive(evil_zip(playground=evil_playground(privileged=False), extra={"evil/.kept": b""}))
        service.forget(preview["ticket"])
        exports._sweep()
        self.assertFalse((paths.ROOT / exports.IMPORTS_DIR / "evil").exists())

    def test_what_a_removal_keeping_the_settings_could_not_delete_is_said_and_kept_apart(self):
        data = evil_zip(playground=evil_playground(privileged=False))
        service.add(exports.receive(data)["ticket"])
        folder = paths.ROOT / exports.IMPORTS_DIR / "evil"
        (folder / "playgrounds/evil/.env").write_text("EVIL_LEVEL=debug\n")
        made = folder / "playgrounds/evil/data"
        made.mkdir()
        (made / "db").write_text("what a container wrote\n")
        made.chmod(0o500)   # what Docker made as root: this user cannot delete what is in it
        self.addCleanup(made.chmod, 0o700)
        project.forget()
        self.assertEqual(service.remove("evil")["warning"], "Some files were created by Docker and could not be deleted. Remove the "
                                                            "folder .lab/imports/evil with administrator rights.")
        kept = list((paths.ROOT / exports.KEPT_DIR).glob("evil-*"))
        self.assertEqual([path.relative_to(kept[0]).as_posix() for path in kept[0].rglob("*") if path.is_file()],
                         ["playgrounds/evil/.env"], "the settings, and nothing that was left")
        self.refused(lambda: exports.receive(data), "import_leftover")

    def test_what_a_removal_could_not_delete_is_said_and_an_import_says_what_to_do(self):
        data = evil_zip(playground=evil_playground(privileged=False))
        service.add(exports.receive(data)["ticket"])
        state = paths.ROOT / exports.IMPORTS_DIR / "evil" / "playgrounds/evil/.state"
        state.mkdir()
        (state / "created.json").write_text("{}\n")
        state.chmod(0o500)   # what Docker made as root: this user cannot delete what is in it
        self.addCleanup(state.chmod, 0o700)
        project.forget()
        warning = service.remove("evil", keep_settings=False)["warning"]
        self.assertEqual(warning, "Some files were created by Docker and could not be deleted. Remove the folder .lab/imports/evil with "
                                  "administrator rights.")
        message = self.refused(lambda: exports.receive(data), "import_leftover")
        self.assertEqual(message, "What an earlier import of Evil left cannot be deleted, and holds its settings "
                                  "playgrounds/evil/.state. Fix: copy them out of .lab/imports/evil, then remove that folder with "
                                  "administrator rights, and import it again.")

    def test_an_import_waits_for_another_change_of_catalogs(self):
        from .test_moving import held
        preview = exports.receive(evil_zip(playground=evil_playground(privileged=False)))
        held(self)
        self.assertEqual(self.refused(lambda: exports.receive(evil_zip(top="other")), "busy"), "Another catalog change is running. Try again in a moment.")
        self.refused(lambda: exports.to_personal(preview["ticket"]), "busy")

    def test_an_import_to_personal_that_fails_midway_leaves_nothing_half_written_and_can_be_tried_again(self):
        data = evil_zip(playground=evil_playground(privileged=False), extra={
            "evil/addons/helper/compose.yml": b"x-gravitee: {kind: addon, name: Helper, category: Other, description: Helps}\nservices: {}\n"})
        preview = exports.receive(data)
        write, calls = exports.Path.write_bytes, []

        def failing(path, *args, **how):
            calls.append(path)
            if len(calls) > 1:
                raise OSError(28, "No space left on device")
            return write(path, *args, **how)
        self.patch(exports.Path, "write_bytes", failing)
        with self.assertRaises(OSError):
            exports.to_personal(preview["ticket"])
        self.assertFalse(self.personal().exists() or self.personal("addons/helper").exists())
        self.patch(exports.Path, "write_bytes", write)
        self.assertEqual(exports.to_personal(preview["ticket"])["playground"], "local/evil")

    def test_a_file_named_as_a_catalog_of_this_lab_that_is_no_import_is_not_told_to_remove_it(self):
        fixtures.install(self, "partner", kind="git", audience="public")
        project.forget()
        message = self.refused(lambda: exports.receive(evil_zip(top="partner")), "import_taken")
        self.assertEqual(message, "Evil has the id of a catalog of this Lab, Partner fixture. Fix: ask for it exported again from a "
                                  "playground of another name.")
        service.add(exports.receive(evil_zip(playground=evil_playground(privileged=False)))["ticket"])
        message = self.refused(lambda: exports.receive(evil_zip()), "already_imported")
        self.assertIn("is already in this Lab. To import it again, remove it on the Catalogs page first.", message)

    def test_an_item_compose_cannot_read_was_judged_by_nothing_and_cannot_go_to_personal(self):
        unread = evil_playground() + "services: {}\n"   # a second services key: Compose refuses the file
        preview = exports.receive(evil_zip(playground=unread))
        self.assertIn({"kind": "playground", "id": "@evil/evil", "name": "Evil", "reasons": ["Its compose file cannot be read"]},
                      preview["blocked"])
        message = self.refused(lambda: exports.to_personal(preview["ticket"], allow=True), "unread_personal")
        self.assertEqual(message, "Evil cannot be read: Compose does not load its compose file. Fix: import it to Imported, where "
                                  "the Lab says why.")
        self.assertFalse(self.personal().exists())

    def test_an_item_of_personal_it_replaces_comes_back_whole_when_the_import_fails(self):
        mine = self.personal("playgrounds/evil")
        mine.mkdir(parents=True)
        (mine / "compose.yml").write_text("x-gravitee: {kind: playground, name: Evil, category: Other, description: mine}\nservices: {}\n")
        (mine / "guide.md").write_text("my guide\n")
        (mine / ".env").write_text("MINE=1\n")
        project.forget()
        preview = exports.receive(evil_zip(playground=evil_playground(privileged=False),
                                           extra={"evil/playgrounds/evil/guide.md": b"their guide\n"}))
        self.assertEqual(preview["personal"]["replaces"], ["Evil"])
        before = {path.name: path.read_text() for path in mine.rglob("*") if path.is_file()}
        write, calls = exports.Path.write_bytes, []

        def failing(path, *args, **how):
            calls.append(path)
            if len(calls) > 1:
                raise OSError(28, "No space left on device")
            return write(path, *args, **how)
        self.patch(exports.Path, "write_bytes", failing)
        with self.assertRaises(OSError):
            exports.to_personal(preview["ticket"])
        self.assertEqual({path.name: path.read_text() for path in mine.rglob("*") if path.is_file()}, before)
        self.patch(exports.Path, "write_bytes", write)
        exports.to_personal(preview["ticket"])
        self.assertEqual(((mine / "guide.md").read_text(), (mine / ".env").read_text()), ("their guide\n", "MINE=1\n"))

    def test_the_record_of_imports_and_the_settings_kept_change_under_the_lock_of_the_catalogs(self):
        held = []

        def locked(call):
            def run(*args, **how):
                held.append((call.__name__, str(paths.ROOT / registry.FLOCK_FILE) in registry._held.__dict__.get("held", {})))
                return call(*args, **how)
            return run
        self.patch(exports, "added", locked(exports.added))
        self.patch(exports, "forget_folder", locked(exports.forget_folder))
        service.add(exports.receive(evil_zip(playground=evil_playground(privileged=False)))["ticket"])
        (paths.ROOT / exports.IMPORTS_DIR / "evil" / "playgrounds/evil/.env").write_text("EVIL_LEVEL=debug\n")
        project.forget()
        service.remove("evil")
        self.assertEqual(held, [("added", True), ("forget_folder", True)])

    def test_a_folder_of_imports_that_no_catalog_uses_gives_its_settings_to_kept_settings_before_it_goes(self):
        data = evil_zip(playground=evil_playground(privileged=False))
        service.add(exports.receive(data)["ticket"])
        folder = paths.ROOT / exports.IMPORTS_DIR / "evil"
        (folder / "playgrounds/evil/.env").write_text("EVIL_LEVEL=debug\n")
        project.forget()
        self.patch(exports, "forget_folder", lambda entry, keep_settings=True: None)   # the Lab stopped right after the catalog left
        service.remove("evil")
        exports.receive(evil_zip(top="other", playground=evil_playground(privileged=False, name="Other")))   # its sweep
        self.assertFalse(folder.exists())
        kept = list((paths.ROOT / exports.KEPT_DIR).glob("evil-*"))
        self.assertEqual((kept[0] / "playgrounds/evil/.env").read_text(), "EVIL_LEVEL=debug\n", "the person's settings are kept")
        again = exports.receive(data)
        self.assertEqual(again["kept"]["used"], True, "and the same file takes them")

    def test_kept_settings_without_a_record_are_shown_and_used_by_nothing(self):
        stray = paths.ROOT / exports.KEPT_DIR / "evil-0a1b2c3d" / "playgrounds" / "evil"
        stray.mkdir(parents=True)
        (stray / ".env").write_text("EVIL_LEVEL=debug\n")
        preview = exports.receive(evil_zip(playground=evil_playground(privileged=False)))
        self.assertEqual(preview["kept"], {"used": False, "where": f"{exports.KEPT_DIR}/evil-0a1b2c3d"})

    def test_a_setting_that_could_not_be_kept_is_said_with_where_it_is_and_is_not_deleted(self):
        data = evil_zip(playground=evil_playground(privileged=False))
        service.add(exports.receive(data)["ticket"])
        folder = paths.ROOT / exports.IMPORTS_DIR / "evil"
        state = folder / "playgrounds/evil/.state"
        state.mkdir()
        (state / "created.json").write_text("{}\n")
        (state / "root.json").write_text("{}\n")
        (state / "root.json").chmod(0o000)   # what Docker made as root: neither moved nor read
        self.addCleanup((state / "root.json").chmod, 0o600)
        self.patch(exports.os, "rename", lambda *args: (_ for _ in ()).throw(OSError(18, "Cross-device link")))
        project.forget()
        warning = service.remove("evil")["warning"]
        self.assertEqual(warning, "The settings of playgrounds/evil/.state could not be kept and are still in .lab/imports/evil: "
                                  "copy them out, then remove that folder with administrator rights.")
        self.assertTrue((state / "created.json").is_file(), "what was not kept is not deleted")
        self.assertFalse(list((paths.ROOT / exports.KEPT_DIR).glob("evil-*/playgrounds/evil/.state")), "no half copy is kept")

    def test_an_import_is_read_and_judged_without_the_lock_of_the_catalogs(self):
        check, held = service.check_address, []

        def judged(*args, **how):
            held.append(str(paths.ROOT / registry.FLOCK_FILE) in registry._held.__dict__.get("held", {}))
            return check(*args, **how)
        self.patch(service, "check_address", judged)
        exports.receive(evil_zip(playground=evil_playground(privileged=False)))
        self.assertEqual(held, [False], "another change does not wait for an import being read")

    def test_an_import_added_before_its_file_was_recorded_gets_its_settings_back_when_imported_again(self):
        data = evil_zip(playground=evil_playground(privileged=False))
        service.add(exports.receive(data)["ticket"])
        (paths.ROOT / exports.kept.RECORD).unlink()   # an import added before the Lab recorded what its file held
        (paths.ROOT / exports.IMPORTS_DIR / "evil" / "playgrounds/evil/.env").write_text("EVIL_LEVEL=debug\n")
        project.forget()
        service.remove("evil")
        again = exports.receive(data)
        self.assertEqual(again["kept"]["used"], True)

    def test_a_leftover_that_holds_the_persons_settings_says_so_before_it_says_to_remove_it(self):
        data = evil_zip(playground=evil_playground(privileged=False))
        left = paths.ROOT / exports.IMPORTS_DIR / "evil" / "playgrounds" / "evil"
        (left / ".state").mkdir(parents=True)
        (left / ".state/root.json").write_text("{}\n")
        (left / ".state/root.json").chmod(0o000)   # neither moved nor read
        (left / ".state").chmod(0o500)   # nor deleted
        self.addCleanup(lambda: ((left / ".state").chmod(0o700), (left / ".state/root.json").chmod(0o600)))
        rename = os.rename
        self.patch(exports.kept.os, "rename", lambda one, two: (_ for _ in ()).throw(OSError(18, "Cross-device link"))
                   if ".state" in str(one) else rename(one, two))
        message = self.refused(lambda: exports.receive(data), "import_leftover")
        self.assertEqual(message, "What an earlier import of Evil left cannot be deleted, and holds its settings "
                                  "playgrounds/evil/.state. Fix: copy them out of .lab/imports/evil, then remove that folder with "
                                  "administrator rights, and import it again.")

    def test_a_copy_of_hidden_behind_decoys_is_refused_and_the_decoys_are_left_as_they_are(self):
        forged = ('services:\n  evil-root:\n    image: busybox:1.36\n    networks: [internal]\n'
                  '    environment:\n      A: "copy_of: x, a"\n      B: "copy_of: x, b"\n'
                  'x-gravitee:\n  kind: playground\n  name: Evil\n  category: Other\n  description: d\n  copy_of: "tour"\n')
        preview = exports.receive(evil_zip(playground=forged))
        self.assertIn(exports.TWIN_CLAIMED, [reason for one in preview["blocked"] for reason in one["reasons"]])
        self.refused(lambda: exports.to_personal(preview["ticket"], allow=True), "twin_claimed")
        self.assertIn('A: "copy_of: x, a"', (paths.ROOT / exports.IMPORTS_DIR / "evil/playgrounds/evil/compose.yml").read_text(),
                      "the file is as it came: the Lab refuses, it does not rewrite")

    def test_what_goes_to_personal_is_the_bytes_whose_digest_is_checked(self):
        calm = evil_playground(privileged=False)
        preview = exports.receive(evil_zip(playground=calm))
        file = paths.ROOT / exports.IMPORTS_DIR / "evil" / "playgrounds" / "evil" / "compose.yml"
        file.write_text(evil_playground())   # changed after the preview...
        stage = moving.stage

        def staged(write):
            swap = stage(write)
            file.write_text(calm)   # ...and put back before the check
            return swap
        self.patch(moving, "stage", staged)
        self.refused(lambda: exports.to_personal(preview["ticket"]), "import_changed")
        self.assertFalse(self.personal().exists())


class SettleFails(Case):
    """Import to Personal of an item that replaces one of Personal, when bringing the person's files to the new one fails: the
    swap that holds them is never deleted, and the next change finishes it."""

    setUp = RoundTrip.setUp

    def test_the_persons_files_are_never_deleted_when_the_end_of_a_swap_fails(self):
        data = exports.archive("local/sandbox")["data"]
        scratch = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons/scratch"
        (scratch / "compose.yml").write_text((scratch / "compose.yml").read_text() + "# changed here\n")
        (scratch / ".env").write_text("MY_SETTING=1\n")
        (scratch / "secrets").mkdir()
        (scratch / "secrets/key").write_text("my key\n")
        preview = exports.receive(data)
        bring = moving._bring
        self.patch(moving, "_bring", lambda old, new, rel: (_ for _ in ()).throw(OSError(5, "Input/output error")))
        with self.assertRaises(OSError):
            exports.to_personal(preview["ticket"])
        self.assertTrue([path for path in (paths.ROOT / paths.LAB_DIR).glob(".swap-*/old/addons/scratch/secrets/key")],
                        "the person's files wait in the swap")
        self.patch(moving, "_bring", bring)
        self.assertEqual(moving.recover(), "A change of Personal that stopped midway was finished for Scratch.")
        self.assertEqual(((scratch / ".env").read_text(), (scratch / "secrets/key").read_text()), ("MY_SETTING=1\n", "my key\n"))



class Claims(Case):
    """A file never makes one of its items the twin of an item of this Lab, whatever way its YAML says copy_of: the import asks
    Compose, the reader the Lab takes twins from, and a claim keeps the item from Personal."""

    setUp = Untrusted.setUp
    BODY = "services:\n  evil-root:\n    image: busybox:1.36\n    networks: [internal]\n"
    HEAD = "kind: playground, name: Evil, category: Other, description: d"
    FORGED = {
        "escaped key": 'x-gravitee:\n  kind: playground\n  name: Evil\n  category: Other\n  description: d\n  "copy\\u005fof": tour\n' + BODY,
        "explicit key": "x-gravitee:\n  kind: playground\n  name: Evil\n  category: Other\n  description: d\n  ? copy_of\n  : tour\n" + BODY,
        "apostrophe": "x-gravitee: {kind: playground, name: Evil's, category: Other, description: d, copy_of: tour}\n" + BODY,
        "json": 'x-gravitee: {"kind": "playground", "name": "Evil", "category": "Other", "description": "d", "copy_of":"tour"}\n' + BODY,
        "bom": "﻿x-gravitee: {" + HEAD + ", copy_of: tour}\n" + BODY,
        "indented document": "".join("  " + line + "\n" for line in ("x-gravitee: {" + HEAD + ", copy_of: tour}\n" + BODY).splitlines()),
        "tag": "x-gravitee: !!map {" + HEAD + ", copy_of: tour}\n" + BODY,
        "flow on the next line": "x-gravitee:\n  {" + HEAD + ", copy_of: tour}\n" + BODY,
        "merge": "x-base: &b {copy_of: tour}\nx-gravitee: {<<: *b, " + HEAD + "}\n" + BODY,
        "alias": "x-base: &b {copy_of: tour, " + HEAD + "}\nx-gravitee: *b\n" + BODY,
        "decoy in a scalar": "x-other: |\n  x-gravitee:\n    name: decoy\nx-gravitee: {" + HEAD + ", copy_of: tour}\n" + BODY,
        "crlf": ("x-gravitee:\n  kind: playground\n  name: Evil\n  category: Other\n  description: d\n  copy_of: tour\n" + BODY).replace("\n", "\r\n"),
        "second document": "x-gravitee: {" + HEAD + "}\n" + BODY + "---\nx-gravitee: {" + HEAD + ", copy_of: tour}\n" + BODY,
    }

    # What the Lab's line reader of include lists could be led to take for one, or not, in the person's own files: a file someone
    # sends that tries it is held by the screen before Compose or that reader see it
    LOOKS_INCLUDED = {
        "dash col0": ('x-gravitee: {' + HEAD + ', note: "\ninclude:\n- a.yml\n  ", copy_of: tour, z: "\n"}\n' + BODY, {}),
        "dash col2": ('x-gravitee: {' + HEAD + ', note: "\ninclude:\n  - a.yml\n   ", copy_of: tour, z: "\n"}\n' + BODY, {}),
        "other folder": ('x-gravitee: {' + HEAD + ', note: "\ninclude:\n  - ../later/compose.yml\n   ", copy_of: tour, z: "\n"}\n'
                         + BODY, {}),
        "reveal": ('x-gravitee: {' + HEAD + ', note: "\ninclude:\n  - ../../addons/x/compose.yml\n   "\n #", copy_of: tour, '
                   'z: "\n #"\n}\n' + BODY,
                   {"evil/addons/x/compose.yml": b"x-gravitee: {kind: addon, name: Xtra, category: Other, description: d}\n"
                                                 b"services:\n  evil-x:\n    image: busybox:1.36\n"}),
    }

    def test_the_preview_blocks_each_forged_item_by_its_claim_or_by_the_screen(self):
        for name, text in self.FORGED.items():
            with self.subTest(name):
                try:
                    preview = exports.receive(evil_zip(playground=text))
                except CatalogError:
                    continue   # not even read: nothing of it goes anywhere
                reasons = [reason for one in preview["blocked"] if one["id"] == "@evil/evil" for reason in one["reasons"]]
                held = {"Includes content from outside the catalog", "Reads files outside its folder"}   # the screen's lines (T19)
                self.assertTrue(exports.TWIN_CLAIMED in reasons or held & set(reasons), reasons)
                service.forget(preview["ticket"])

    def test_a_text_that_looks_like_an_include_list_is_held_by_the_screen_and_never_reaches_personal(self):
        for name, (text, extra) in self.LOOKS_INCLUDED.items():
            with self.subTest(name):
                preview = exports.receive(evil_zip(playground=text, extra=extra))
                reasons = [reason for one in preview["blocked"] if one["id"] == "@evil/evil" for reason in one["reasons"]]
                self.assertIn("Includes content from outside the catalog", reasons)
                with self.assertRaises(CatalogError):
                    exports.to_personal(preview["ticket"], allow=True)
                service.add(preview["ticket"])
                project.forget()
                with self.assertRaises(CatalogError):
                    moving.move("playground", "@evil/evil", True, allow=True)
                found = project.load()
                self.assertTrue(found["playgrounds"]["@evil/evil"]["blocked"])
                self.assertNotIn("local/evil", found["playgrounds"])
                self.assertIsNone(found["playgrounds"]["tour"].get("twin"))
                service.remove("evil")
                project.forget()

    def test_the_label_reader_and_the_lab_read_a_personal_file_alike_whether_what_it_seems_to_include_is_there(self):
        file = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "playgrounds" / "evil" / "compose.yml"
        file.parent.mkdir(parents=True)
        for name, (text, _) in self.LOOKS_INCLUDED.items():
            for there in (True, False):
                with self.subTest(name, there=there):
                    file.write_text(text, encoding="utf-8")
                    named = file.parent / "a.yml"
                    named.unlink() if named.exists() else None
                    if there:
                        named.write_text("services: {}\n", encoding="utf-8")
                    read, why = project.read_own(paths.unit_file("playground", "local/evil"))
                    said = compose.x_gravitee(text, file.parent, fresh=True) if read is not None else None
                    self.assertEqual(said, (read or {}).get("x-gravitee"), why)

    def test_no_way_of_writing_copy_of_makes_a_twin(self):
        for name, text in self.FORGED.items():
            with self.subTest(name):
                try:
                    preview = exports.receive(evil_zip(playground=text))
                except CatalogError:
                    continue   # not even read: nothing of it goes anywhere
                with self.assertRaises(CatalogError):
                    exports.to_personal(preview["ticket"], allow=True)
                project.forget()
                found = project.load()
                self.assertNotIn("local/evil", found["playgrounds"], "it never reaches Personal")
                self.assertIsNone(found["playgrounds"]["tour"].get("twin"))

    def test_a_claim_is_said_in_the_preview_and_a_move_of_it_is_refused(self):
        preview = exports.receive(evil_zip(playground=self.FORGED["apostrophe"]))
        self.assertIn({"kind": "playground", "id": "@evil/evil", "name": "Evil's", "reasons": [exports.TWIN_CLAIMED]}, preview["blocked"])
        with self.assertRaises(CatalogError) as caught:
            exports.to_personal(preview["ticket"], allow=True)
        self.assertEqual((caught.exception.code, caught.exception.message), ("twin_claimed", "Evil's says it is a copy of an item of "
                         "this Lab, which only this Lab writes: it cannot run. Fix: ask for its file again, without copy_of."))
        service.add(preview["ticket"])
        project.forget()
        self.assertEqual(project.load()["playgrounds"]["@evil/evil"]["blocked"], {"reasons": [exports.TWIN_CLAIMED]},
                         "blocked in Imported too, as any refusal of the policy")
        with self.assertRaises(CatalogError):
            moving.move("playground", "@evil/evil", False, allow=True)

    def test_what_slips_by_the_preview_is_caught_once_in_personal_and_undone(self):
        preview = exports.receive(evil_zip(playground=self.FORGED["apostrophe"]))
        service.note(preview["ticket"], blocked=[], claimed=[])   # as if the preview had not seen it
        with self.assertRaises(CatalogError) as caught:
            exports.to_personal(preview["ticket"], allow=True)
        self.assertEqual(caught.exception.code, "twin_claimed")
        self.assertFalse((paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "playgrounds/evil").exists())
        self.assertFalse(list((paths.ROOT / paths.LAB_DIR).glob(".swap-*")))
