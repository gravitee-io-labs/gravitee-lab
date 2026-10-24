"""catalogs/exports.py: a playground in a file. What an export carries and needs, the archive it makes (a folder catalog), the
judgement of it as another Lab will make it, and its import: the archive read with the limits of a catalog, written under
.lab/imports, checked as the Add of a folder catalog, added, and gone with the catalog. `Archives` needs nothing but Python;
`RoundTrip` reads the fixture catalogs with Compose 5 (a visible skip without it)."""

import base64
import io
import json
import shutil
import stat
import zipfile

from lab_ui import paths
from lab_ui.catalogs import exports, gitx, moving, registry, service
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
        self.assertTrue(caught.exception.message.endswith(". Fix: change it, then export it again."), caught.exception.message)

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

    def test_a_copy_of_an_item_of_a_private_catalog_is_private_content_too_and_the_file_does_not_say_whose_copy_it_is(self):
        moving.copy("addon", "@partner/ledger")
        scratch = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons" / "scratch" / "compose.yml"
        scratch.write_text(scratch.read_text(encoding="utf-8").replace(self.LEDGER, "../ledger/compose.yml"), encoding="utf-8")
        project.forget()
        plan = exports.plan("local/sandbox")
        self.assertIn("local/ledger", [one["id"] for one in plan["carries"]])
        self.assertEqual(plan["private"], [{"id": "partner", "name": "Partner fixture", "items": ["Ledger"]}])
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(exports.archive("local/sandbox", private=["partner"])["data"]))) as archive:
            self.assertNotIn("copy_of", archive.read("sandbox/addons/ledger/compose.yml").decode())

    def test_an_image_the_other_lab_pulls_from_a_registry_that_asks_for_a_login_is_said(self):
        plan = exports.plan("local/sandbox")
        self.assertEqual(plan["registries"], [{"registry": "registry.acme.example", "images": ["registry.acme.example/ledger:1"]}])
        self.assertEqual(self.asked[0], ["busybox:1.36", "busybox:1.36"], "the images of the carried services the other Lab pulls")

    def test_the_file_is_made_once_the_person_saw_what_leaves_from_a_private_catalog(self):
        with self.assertRaises(CatalogError) as caught:
            exports.archive("local/sandbox")
        self.assertEqual((caught.exception.code, caught.exception.message), ("export_private", "It now carries private content of "
                                                                             "Partner fixture. Fix: export it again to see what leaves with it."))
        self.assertTrue(exports.archive("local/sandbox", private=["partner"])["data"])

    def test_the_archive_says_where_each_item_of_a_catalog_comes_from(self):
        made = exports.archive("local/sandbox", private=["partner"])
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(made["data"]))) as archive:
            said = json.loads(archive.read("sandbox/export.json"))
            scratch = archive.read("sandbox/addons/scratch/compose.yml").decode()
        self.assertEqual([(one["kind"], one["item"], one["catalog"]) for one in said["items"]], [("addon", "ledger", "partner")])
        self.assertIn("  - ../ledger/compose.yml\n", scratch, "the copy is what Scratch includes in the file")

    def test_a_lab_that_has_the_catalog_uses_its_own_item_instead_of_a_second_copy(self):
        preview = exports.receive(exports.archive("local/sandbox", private=["partner"])["data"])
        self.assertEqual(preview["reused"], [{"name": "Ledger", "catalog": "Partner fixture"}])
        self.assertEqual(preview["counts"], {"playground": 1, "setup": 0, "addon": 1})
        folder = paths.ROOT / exports.IMPORTS_DIR / "sandbox"
        self.assertFalse((folder / "addons" / "ledger").exists())
        self.assertIn(self.LEDGER, (folder / "addons" / "scratch" / "compose.yml").read_text(encoding="utf-8"))
        self.assertIn("      partner:\n", (folder / "catalog.yml").read_text(encoding="utf-8"))
        self.assertEqual(preview.get("blocked") or [], [])

    def test_a_lab_without_the_catalog_keeps_the_copy(self):
        data = exports.archive("local/sandbox", private=["partner"])["data"]
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

    def test_a_removed_import_keeps_the_persons_settings_for_the_next_import_of_the_same_playground(self):
        data = evil_zip(playground=evil_playground(privileged=False))
        service.add(exports.receive(data)["ticket"])
        folder = paths.ROOT / exports.IMPORTS_DIR / "evil"
        (folder / "playgrounds/evil/.env").write_text("EVIL_LEVEL=debug\n")
        (folder / "playgrounds/evil/secrets").mkdir()
        (folder / "playgrounds/evil/secrets/key").write_text("not a real one\n")
        project.forget()
        self.assertEqual(service.remove("evil"), {"warning": None})
        self.assertEqual(sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()),
                         [".kept", "playgrounds/evil/.env", "playgrounds/evil/secrets/key"], "the settings stay, the files go")
        exports.receive(data)   # a preview left as it is keeps them too
        service.add(exports.receive(data)["ticket"])
        self.assertEqual((folder / "playgrounds/evil/.env").read_text(), "EVIL_LEVEL=debug\n", "the next import has them")
        self.assertTrue((folder / "playgrounds/evil/compose.yml").is_file())
        project.forget()
        service.remove("evil", keep_settings=False)
        self.assertFalse(folder.exists())

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
        self.assertEqual(message, "What an earlier import of Evil left cannot be deleted. Fix: remove the folder .lab/imports/evil "
                                  "with administrator rights, then import it again.")

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
