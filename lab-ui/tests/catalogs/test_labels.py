"""The private label of an item is a set of origins, the private catalogs of Git what it holds is content of. Every Copy, Share
and Export writes the set the file says joined by what the Lab knows directly (the catalog the item is in; for an older copy
without a label, the catalog its copy_of names, one step). Nothing takes an origin away but a share into that very catalog."""

import os
import random
import tempfile
import threading
from pathlib import Path

from lab_ui import paths
from lab_ui.catalogs import contribute, exports, gitx, moving
from lab_ui.library import project

from .. import fixtures
from .test_contribute import Seam
from .test_exports import EVIL_CATALOG, zipped

NOTES = ("x-gravitee: {{{said}kind: addon, name: Notes, category: Other, description: Notes of a team}}\n"
         "services:\n  notes:\n    image: busybox:1.36\n    networks: [internal]\n")


def notes(said=""):
    return NOTES.format(said=said)


def mine(slug="notes"):
    return paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / f"addons/{slug}/compose.yml"


def books(label=None, private_id=None):
    """An archive whose add-on Books says the label and the id it is given."""
    said = (f'private_from: ["{label}"], ' if label else "") + (f'private_id: ["{private_id}"], ' if private_id else "")
    return zipped({
        "carried/catalog.yml": EVIL_CATALOG.replace("{id}", "carried").encode(),
        "carried/playgrounds/carried/compose.yml": ("x-gravitee: {kind: playground, name: Carried, category: Other, description: x}\n"
                                                    "include:\n  - ../../addons/books/compose.yml\nservices: {}\n").encode(),
        "carried/addons/books/compose.yml": (f"x-gravitee: {{{said}kind: addon, name: Books, category: Other, description: x}}\n"
                                             "services:\n  books:\n    image: busybox:1.36\n    networks: [internal]\n").encode()})


class Labels(Seam):

    def setUp(self):
        super().setUp()
        fixtures.install(self, "lab-old", kind="git", store=True, audience="private", lab=2)
        self.old = contribute._catalog_name("lab-old")
        project.forget()

    def shared(self, uid, slug, to="partner"):
        done = self.share(uid, to)
        return self.remote(to).git("show", f"{done['branch']}:addons/{slug}/compose.yml")

    def test_an_item_of_a_private_catalog_keeps_the_label_its_file_has_and_gains_its_catalog(self):
        fixtures.install(self, "partner", kind="git", store=True, lab=2,
                         files={"addons/notes/compose.yml": notes(f'private_from: ["{self.old}"], private_id: [lab-old], ')})
        project.forget()
        moving.copy("addon", "@partner/notes")
        project.forget()
        self.assertEqual(contribute.origins(mine().read_text(), "local/notes"), [("lab-old", self.old), ("partner", "Partner fixture")])
        os.rename(mine().parent, mine().parent.parent / "notes2")   # renamed, and shared back to partner: lab-old's stays
        project.forget()
        self.item("addon", "notes2", "Notes")
        shared = self.shared("local/notes2", "notes2")
        self.assertIn(f'private_from: ["{self.old}"]', shared)
        self.assertIn('private_id: ["lab-old"]', shared)

    def test_what_another_catalogs_item_says_never_steers_a_copy(self):
        fixtures.install(self, "lab-new", "folder", audience="private", lab=2, name="Team",
                         files={"addons/notes/compose.yml": notes(f'private_from: ["{self.old}"], private_id: [lab-old], ')})
        project.forget()
        moving.copy("addon", "@lab-new/notes")
        folder_item = self.root / ".lab/catalogs/lab-new/addons/notes/compose.yml"   # its author then says it is partner's copy
        folder_item.write_text(notes(f'private_from: ["{self.old}"], private_id: [lab-old], copy_of: "@partner/ledger", '))
        project.forget()
        self.assertEqual(contribute.origins(mine().read_text(), "local/notes"), [("lab-old", self.old)])
        self.item("addon", "notes", "Notes")
        self.assertIn(f'private_from: ["{self.old}"]', self.shared("local/notes", "notes"), "lab-old's label stays on a share to partner")

    def test_an_imported_label_only_adds_and_goes_only_into_its_own_catalog(self):
        self.patch(gitx, "home", lambda: paths.ROOT.parent)
        exports.to_personal(exports.receive(books("Acme secret", "partner"))["ticket"])
        project.forget()
        self.item("addon", "books", "Books")
        self.assertNotIn("private_", self.shared("local/books", "books"), "it says partner's: a share into partner takes it away")
        exports.to_personal(exports.receive(books("Acme secret", "acme"))["ticket"])
        project.forget()
        self.item("addon", "books", "Books")
        self.assertIn('private_from: ["Acme secret"]', self.shared("local/books", "books"), "another's stays")

    def test_a_copy_edited_and_shared_back_to_its_catalog_says_nothing_and_leaves_its_level(self):
        moving.copy("addon", "@partner/ledger")
        project.forget()
        mine("ledger").write_text(mine("ledger").read_text().replace("Keeps the entries of a partner}", "Keeps them, edited}", 1))
        os.rename(mine("ledger").parent, mine("ledger").parent.parent / "ledger2")
        project.forget()
        self.item("addon", "ledger2", "Ledger2")
        before = self.remote("partner").git("show", "HEAD:catalog.yml")
        done = self.share("local/ledger2", "partner")
        shared = self.remote("partner").git("show", f"{done['branch']}:addons/ledger2/compose.yml")
        self.assertNotIn("private_", shared)
        self.assertNotIn("copy_of", shared)
        self.assertEqual(self.remote("partner").git("show", f"{done['branch']}:catalog.yml"), before)

    def test_a_folder_whose_catalog_yml_is_a_pipe_never_stops_a_share(self):
        fixtures.install(self, "lab-new", "folder", audience="private", lab=1, name="Team")
        root = self.root / ".lab/catalogs/lab-new"
        pipe = Path(tempfile.mkdtemp()) / "pipe"
        os.mkfifo(pipe)
        (root / "catalog.yml").unlink()
        os.symlink(pipe, root / "catalog.yml")
        project.forget()
        self.item("addon", "my-ledger", "Ledger", notes('private_from: ["Partner fixture"], private_id: [partner], '))
        result = {}
        worker = threading.Thread(target=lambda: result.update(done=self.share("local/my-ledger", "lab-new")), daemon=True)
        worker.start()
        worker.join(20)
        stuck = worker.is_alive()
        if stuck:   # let it go
            with open(pipe, "w"):
                pass
            worker.join(10)
        self.assertFalse(stuck, "a pipe is never read")
        self.assertEqual(result["done"].get("warning"), contribute.LAB_NOT_RAISED.format(catalog="Team", level=contribute.LABEL_LEVEL))

    def test_the_rule(self):
        fixtures.install(self, "lab-new", "folder", audience="private", lab=1, name="Team")
        cases = [   # (the item, what its x-gravitee says, the origins)
            ("@partner/ledger", "", [("partner", "Partner fixture")]),   # the catalog it is in
            ("@partner/ledger", f'private_from: ["{self.old}"], private_id: [lab-old], ', [("lab-old", self.old),
                                                                                            ("partner", "Partner fixture")]),
            ("local/a", 'copy_of: "@partner/ledger", ', [("partner", "Partner fixture")]),   # an older copy: one step
            ("local/a", 'copy_of: "@lab-new/notes", ', []),   # a folder catalog's item says nothing for the Lab
            ("local/a", 'copy_of: "echo", ', []),   # nor does a public one's
            ("local/a", 'copy_of: "@gone/x", ', [("gone", contribute.UNKNOWN)]),   # a copy of what this machine does not have
            ("local/a", 'private_from: ["Acme"], copy_of: "@partner/ledger", ', [("", "Acme")]),   # a label says it all
            ("@lab-new/notes", 'private_from: ["Acme", "Beta"], private_id: [acme], ', [("acme", "Acme"), ("", "Beta")]),
            ("local/a", "", []),
        ]
        for uid, said, wanted in cases:
            with self.subTest(said or "nothing", uid=uid):
                self.assertEqual(contribute.origins(notes(said), uid), wanted)
        both = [("partner", "Partner fixture"), ("lab-old", self.old)]
        table = [("partner", [("lab-old", self.old)]), ("lab-old", [("partner", "Partner fixture")]), ("lab-new", both),
                 ("fixture", both), ("nowhere", both)]
        for to, wanted in table:   # what a share into each catalog leaves: only a private catalog of Git takes its own away
            with self.subTest(to=to):
                self.assertEqual(contribute.without(both, to), wanted)

    def test_an_origin_goes_only_by_a_share_into_its_own_catalog(self):
        """Seeded random runs of Copy, Share to a folder, Share to Git, Export and Import over three private catalogs of Git, a
        folder and a public one: an origin is lost only by a share into its own catalog."""
        fixtures.install(self, "lab-new", kind="git", store=True, audience="private", lab=2)
        fixtures.install(self, "hostile", "folder", audience="private", lab=2, name="Team")
        project.forget()
        privates, others = ["partner", "lab-old", "lab-new"], ["hostile", "fixture"]
        rng = random.Random(14)
        for run in range(6):
            where, text = rng.choice(privates + others), notes()
            for _ in range(8):
                before = {ns for ns, _ in contribute.origins(text, f"@{where}/notes")}
                operation, to = rng.choice(["copy", "share", "export", "import"]), rng.choice(privates + others)
                if operation == "copy":   # into Personal, from where it is
                    text, where = contribute.labelled(contribute.unmark(text), contribute.origins(text, f"@{where}/notes")), paths.LOCAL
                elif operation == "share":
                    found = contribute.origins(text, f"{where}/notes" if where == paths.LOCAL else f"@{where}/notes")
                    if to == "fixture":   # a Public catalog takes no private content
                        if found:
                            continue
                    text, where = contribute.labelled(contribute.unmark(text), contribute.without(found, to)), to
                elif operation == "export":
                    text = contribute.labelled(contribute.unmark(text), contribute.origins(text, f"@{where}/notes"))
                after = {ns for ns, _ in contribute.origins(text, f"{where}/notes" if where == paths.LOCAL else f"@{where}/notes")}
                gone = before - after
                with self.subTest(run=run, operation=operation, to=to):
                    self.assertTrue(not gone or (operation == "share" and gone == {to} and to in privates), (before, after))
