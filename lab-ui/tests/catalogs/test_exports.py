"""catalogs/exports.py: a playground in a file. What an export carries and needs, the archive it makes (a folder catalog), the
judgement of it as another Lab will make it, and its import: the archive read with the limits of a catalog, written under
.lab/imports, checked as the Add of a folder catalog, added, and gone with the catalog. `Archives` needs nothing but Python;
`RoundTrip` reads the fixture catalogs with Compose 5 (a visible skip without it)."""

import base64
import io
import json
import stat
import zipfile

from lab_ui import paths
from lab_ui.catalogs import exports, gitx, registry, service
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
        service.remove("sandbox")
        self.assertFalse(folder.exists(), "the files of an imported playground go with its catalog")


class FromCatalogs(Case):
    """Sandbox again, whose Scratch now also includes Ledger of Partner, a private catalog of Git: the export carries Ledger, says
    that it is private content, and says where it comes from; a Lab that has Partner uses its own Ledger at the import, one that
    has not keeps the copy."""

    LEDGER = "../../../partner/addons/ledger/compose.yml"

    def setUp(self):
        fixtures.need_compose()
        fixtures.project(self, paths, project, compose, envfiles, check, files, ns="fixture", kind="git", store=True)
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
        self.assertEqual(plan["private"], [{"id": "partner", "name": "Partner fixture", "items": ["Ledger"]}])
        self.assertEqual(plan["blocked"], [])

    def test_an_image_the_other_lab_pulls_from_a_registry_that_asks_for_a_login_is_said(self):
        plan = exports.plan("local/sandbox")
        self.assertEqual(plan["registries"], [{"registry": "registry.acme.example", "images": ["registry.acme.example/ledger:1"]}])
        self.assertEqual(self.asked[0], ["busybox:1.36", "busybox:1.36"], "the images of the carried services the other Lab pulls")

    def test_the_archive_says_where_each_item_of_a_catalog_comes_from(self):
        made = exports.archive("local/sandbox")
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(made["data"]))) as archive:
            said = json.loads(archive.read("sandbox/export.json"))
            scratch = archive.read("sandbox/addons/scratch/compose.yml").decode()
        self.assertEqual([(one["kind"], one["item"], one["catalog"]) for one in said["items"]], [("addon", "ledger", "partner")])
        self.assertIn("  - ../ledger/compose.yml\n", scratch, "the copy is what Scratch includes in the file")

    def test_a_lab_that_has_the_catalog_uses_its_own_item_instead_of_a_second_copy(self):
        preview = exports.receive(exports.archive("local/sandbox")["data"])
        self.assertEqual(preview["reused"], [{"name": "Ledger", "catalog": "Partner fixture"}])
        self.assertEqual(preview["counts"], {"playground": 1, "setup": 0, "addon": 1})
        folder = paths.ROOT / exports.IMPORTS_DIR / "sandbox"
        self.assertFalse((folder / "addons" / "ledger").exists())
        self.assertIn(self.LEDGER, (folder / "addons" / "scratch" / "compose.yml").read_text(encoding="utf-8"))
        self.assertIn("      partner:\n", (folder / "catalog.yml").read_text(encoding="utf-8"))
        self.assertEqual(preview.get("blocked") or [], [])

    def test_a_lab_without_the_catalog_keeps_the_copy(self):
        data = exports.archive("local/sandbox")["data"]
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
