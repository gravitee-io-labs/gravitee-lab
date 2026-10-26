"""The private label of what a share writes: what the Lab knows of where an item's content comes from wins, and what its file says
can only add a notice. A label goes only when an item goes back to that very private catalog of Git."""

from lab_ui import paths
from lab_ui.catalogs import contribute, exports, gitx, moving
from lab_ui.library import project

from .. import fixtures
from .test_contribute import Seam
from .test_exports import EVIL_CATALOG, zipped


def books(label=None, private_id=None):
    """An archive whose add-on Books says the label and the id it is given."""
    said = (f'private_from: "{label}", ' if label else "") + (f"private_id: {private_id}, " if private_id else "")
    return zipped({
        "carried/catalog.yml": EVIL_CATALOG.replace("{id}", "carried").encode(),
        "carried/playgrounds/carried/compose.yml": ("x-gravitee: {kind: playground, name: Carried, category: Other, description: x}\n"
                                                    "include:\n  - ../../addons/books/compose.yml\nservices: {}\n").encode(),
        "carried/addons/books/compose.yml": (f"x-gravitee: {{{said}kind: addon, name: Books, category: Other, description: x}}\n"
                                             "services:\n  books:\n    image: busybox:1.36\n    networks: [internal]\n").encode()})


class Shared(Seam):

    def shared(self, uid, slug, to="partner"):
        done = self.share(uid, to)
        return self.remote(to).git("show", f"{done['branch']}:addons/{slug}/compose.yml")

    def imported(self, label, private_id):
        self.patch(gitx, "home", lambda: paths.ROOT.parent)
        exports.to_personal(exports.receive(books(label, private_id))["ticket"])
        project.forget()
        self.item("addon", "books", "Books")
        return self.shared("local/books", "books")

    def test_an_imported_label_stays_whatever_id_it_says(self):
        self.assertIn('private_from: "Acme secret"', self.imported("Acme secret", None))
        self.assertIn('private_from: "Acme secret"', self.imported("Acme secret", "partner"),
                      "an id a file says, of a catalog of another name, takes nothing away")

    def test_the_label_the_lab_knows_wins_over_what_the_file_of_the_item_says(self):
        text = ('x-gravitee: {private_from: "Elsewhere", private_id: partner, kind: addon, name: Notes, category: Other, '
                'description: Notes of a team}\nservices:\n  notes:\n    image: busybox:1.36\n    networks: [internal]\n')
        fixtures.install(self, "lab-new", kind="git", store=True, audience="private", lab=2, files={"addons/notes/compose.yml": text})
        project.forget()
        moving.copy("addon", "@lab-new/notes")
        project.forget()
        mine = paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "addons/notes/compose.yml"
        name = contribute._catalog_name("lab-new")
        self.assertIn(f'private_from: "{name}"', mine.read_text().splitlines()[0], "the catalog it came from, as the Lab knows it")
        self.assertIn('private_id: "lab-new"', mine.read_text().splitlines()[0])
        self.item("addon", "notes", "Notes")
        self.assertIn(f'private_from: "{name}"', self.shared("local/notes", "notes"), "to another catalog it is still lab-new's")

    def test_a_label_naming_this_very_catalog_by_its_id_and_its_name_goes_when_it_comes_home(self):
        text = ('x-gravitee: {private_from: "Partner fixture", private_id: partner, kind: addon, name: Ledger, category: Other, '
                'description: d}\nservices: {}\n')
        self.item("addon", "my-ledger", "Ledger", text)
        self.assertNotIn("private_", self.shared("local/my-ledger", "my-ledger"))

    def test_the_rule_of_the_label(self):
        fixtures.install(self, "lab-new", "folder", audience="private", lab=1, name="Team")
        cases = [   # (uid, x-gravitee entries, (id, name) of _private_of)
            ("@partner/ledger", 'private_from: "Elsewhere", ', ("partner", "Partner fixture")),   # in a private catalog of Git
            ("local/a", 'copy_of: "@partner/ledger", ', ("partner", "Partner fixture")),   # its copy chain reaches one
            ("local/a", 'private_from: "Elsewhere", private_id: partner, copy_of: "@partner/ledger", ', ("partner", "Partner fixture")),
            ("local/a", 'private_from: "Partner fixture", private_id: partner, ', ("partner", "Partner fixture")),   # id and name
            ("local/a", 'private_from: "Acme secret", private_id: partner, ', (None, "Acme secret")),   # the name is another's
            ("local/a", 'private_from: "Partner fixture", private_id: nowhere, ', (None, "Partner fixture")),   # no such catalog
            ("local/a", 'private_from: "Team", private_id: lab-new, ', (None, "Team")),   # a folder catalog is no catalog of Git
            ("local/a", 'private_from: "Fixture catalog", private_id: fixture, ', (None, "Fixture catalog")),   # Public: no
            ("local/a", "", (None, None)),
        ]
        for uid, said, wanted in cases:
            with self.subTest(said or "nothing", uid=uid):
                text = "x-gravitee: {" + said + "kind: addon, name: A}\nservices: {}\n"
                self.assertEqual(contribute._private_of(text, uid), wanted)
        self.assertIsNone(contribute._label_in(("partner", "Partner fixture"), "partner"))
        self.assertEqual(contribute._label_in((None, "Partner fixture"), "partner"), "Partner fixture")
        self.assertEqual(contribute._label_in(("partner", "Partner fixture"), "lab-new"), "Partner fixture")
