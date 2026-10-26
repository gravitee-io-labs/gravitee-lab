"""The private label of an item is a set of origins, the private catalogs of Git what it holds is content of. Every Copy, Share
and Export writes the set the file says joined by what the Lab knows directly (the catalog the item is in; for an older copy
without a label, the catalog its copy_of names, one step). Nothing takes an origin away but a share into that very catalog."""

import os
import random
import tempfile
import threading
from pathlib import Path

from lab_ui import paths
from lab_ui.catalogs import contribute, exports, gitx, moving, registry
from lab_ui.catalogs.registry import CatalogError
from lab_ui.docker import compose
from lab_ui.library import project

from .. import fixtures
from .test_contribute import Seam, lines_of, root
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
                         files={"addons/notes/compose.yml": notes(f'private_from: ["{self.old}"], private_id: ["{root("lab-old")}"], ')})
        project.forget()
        moving.copy("addon", "@partner/notes")
        project.forget()
        self.assertEqual(contribute.origins(mine().read_text(), "local/notes"), [(root("lab-old"), self.old), (root("partner"), "Partner fixture")])
        os.rename(mine().parent, mine().parent.parent / "notes2")   # renamed, and shared back to partner: lab-old's stays
        project.forget()
        self.item("addon", "notes2", "Notes")
        shared = self.shared("local/notes2", "notes2")
        self.assertIn(f'private_from: ["{self.old}"]', shared)
        self.assertIn(f'private_id: ["{root("lab-old")}"]', shared)

    def test_what_another_catalogs_item_says_never_steers_a_copy(self):
        fixtures.install(self, "lab-new", "folder", audience="private", lab=2, name="Team",
                         files={"addons/notes/compose.yml": notes(f'private_from: ["{self.old}"], private_id: ["{root("lab-old")}"], ')})
        project.forget()
        moving.copy("addon", "@lab-new/notes")
        folder_item = self.root / ".lab/catalogs/lab-new/addons/notes/compose.yml"   # its author then says it is partner's copy
        folder_item.write_text(notes(f'private_from: ["{self.old}"], private_id: ["{root("lab-old")}"], copy_of: "@partner/ledger", '))
        project.forget()
        self.assertEqual(contribute.origins(mine().read_text(), "local/notes"), [(root("lab-old"), self.old)])
        self.item("addon", "notes", "Notes")
        self.assertIn(f'private_from: ["{self.old}"]', self.shared("local/notes", "notes"), "lab-old's label stays on a share to partner")

    def test_an_imported_label_only_adds_and_goes_only_into_its_own_catalog(self):
        self.patch(gitx, "home", lambda: paths.ROOT.parent)
        exports.to_personal(exports.receive(books("Acme secret", "partner"))["ticket"])
        project.forget()
        self.item("addon", "books", "Books")
        self.assertIn('private_from: ["Acme secret"]', self.shared("local/books", "books"), "a catalog's id is no origin: it stays")
        exports.to_personal(exports.receive(books("Partner content", root("partner")))["ticket"])
        project.forget()
        self.item("addon", "books", "Books")
        self.assertNotIn("private_", self.shared("local/books", "books"), "the first commit of partner's history: back in partner")

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
        folder = self.root / ".lab/catalogs/lab-new"
        pipe = Path(tempfile.mkdtemp()) / "pipe"
        os.mkfifo(pipe)
        (folder / "catalog.yml").unlink()
        os.symlink(pipe, folder / "catalog.yml")
        project.forget()
        self.item("addon", "my-ledger", "Ledger", notes(f'private_from: ["Partner fixture"], private_id: ["{root("partner")}"], '))
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
            ("@partner/ledger", "", [(root("partner"), "Partner fixture")]),   # the catalog it is in
            ("@partner/ledger", f'private_from: ["{self.old}"], private_id: ["{root("lab-old")}"], ', [(root("lab-old"), self.old),
                                                                                            (root("partner"), "Partner fixture")]),
            ("local/a", 'copy_of: "@partner/ledger", ', [(root("partner"), "Partner fixture")]),   # an older copy: one step
            ("local/a", 'copy_of: "@lab-new/notes", ', [("", contribute.UNKNOWN)]),   # a folder catalog's item: the id may name another
            ("local/a", 'copy_of: "echo", ', [("", contribute.UNKNOWN)]),   # a public one's: the same
            ("local/a", 'copy_of: "@gone/x", ', [("", contribute.UNKNOWN)]),   # a copy of what this machine does not have
            ("local/a", 'private_from: ["Acme"], copy_of: "@partner/ledger", ', [("", "Acme")]),   # a label says it all
            ("@lab-new/notes", 'private_from: ["Acme", "Beta"], private_id: [acme], ', [("acme", "Acme"), ("", "Beta")]),
            ("local/a", 'private_from: [], copy_of: "echo", ', []),   # a copy the Lab made says its origins: none
            ("local/a", 'private_from: ["partner", "Wrong name"], private_id: ["", "{P}"], ', [("", "partner"), ("{P}", "Partner fixture")]),
            ("local/a", "", []),
        ]
        for uid, said, wanted in cases:
            said, wanted = said.replace("{P}", root("partner")), [(one.replace("{P}", root("partner")), name) for one, name in wanted]
            with self.subTest(said or "nothing", uid=uid):
                self.assertEqual(contribute.origins(notes(said), uid), wanted)
        both = [(root("partner"), "Partner fixture"), (root("lab-old"), self.old)]
        table = [("partner", [(root("lab-old"), self.old)]), ("lab-old", [(root("partner"), "Partner fixture")]), ("lab-new", both),
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


class Identity(Seam):
    """An origin is the first commit of a private catalog's history: the same on every Lab, never an id of this Lab's, which a
    folder, a Public catalog or another repository may hold."""

    def test_a_folder_that_has_the_id_a_label_says_is_another_catalog(self):
        fixtures.install(self, "lab-new", "folder", audience="private", lab=2, name="Team",
                         files={"addons/notes/compose.yml": notes('private_from: ["Team secret"], private_id: ["lab-new"], ')})
        fixtures.local(self, "sandbox")
        project.forget()
        found = exports.copies_of([("addon", "@lab-new/notes")])
        self.assertEqual(found, {("addon", "@lab-new/notes"): [("lab-new", "Team secret")]}, "its own catalog is a folder: nothing is its")

    def test_an_older_copy_of_an_id_a_public_catalog_holds_now_is_private_content(self):
        self.item("addon", "old-copy", "Old copy", notes('copy_of: "echo", '))
        with self.assertRaises(CatalogError) as raised:
            self.share("local/old-copy", "fixture", check_only=True)
        self.assertEqual(raised.exception.code, "share_private_copy")

    def test_another_repository_under_the_same_id_takes_no_label_away(self):
        label = [(root("partner"), "Partner fixture")]
        self.assertEqual(contribute.without(label, "partner"), [])
        lock = registry.lock_of
        self.patch(registry, "lock_of", lambda ns: {**lock(ns), "roots": ["f" * 40]} if ns == "partner" else lock(ns))
        self.assertEqual(contribute.without(label, "partner"), label, "a repository added later under that id, or another Lab's")
        self.item("addon", "my-ledger", "Ledger", notes(f'private_from: ["Partner fixture"], private_id: ["{"0" * 40}"], '))
        done = self.share("local/my-ledger", "partner")
        self.assertIn('private_from: ["Partner fixture"]', self.remote("partner").git("show", f"{done['branch']}:addons/my-ledger/compose.yml"))

    def test_a_label_that_compose_cannot_read_stops_what_would_write_it(self):
        self.patch(compose, "x_gravitee", side_effect=compose.ComposeError("yaml: line 1: bad"))
        with self.assertRaises(CatalogError) as raised:
            contribute.origins(notes('private_from: ["A"], '), "local/a", name="Notes")
        self.assertEqual(raised.exception.code, "label_unread")

    def test_a_target_that_may_be_public_takes_no_private_content(self):
        github = gitx.parse_address("git@github.com:acme/hub.git")
        answers = {"public": None, "private": CatalogError("no_access", "no"), "no answer": CatalogError("timeout", "slow")}
        for said, outcome in answers.items():
            with self.subTest(said):
                self.patch(gitx, "ls_remote", side_effect=outcome) if outcome else self.patch(gitx, "ls_remote", return_value={})
                self.assertEqual(contribute._may_be_public(github), said != "private")
        self.assertTrue(contribute._may_be_public(gitx.parse_address("git@git.example.test:acme/hub.git")), "a host it cannot ask")
        self.assertFalse(contribute._may_be_public(gitx.parse_address("https://github.com/acme/hub.git")), "the audience it was added with")
        self.patch(contribute, "_may_be_public", return_value=True)
        self.item("addon", "my-ledger", "Ledger", notes(f'private_from: ["Older content"], private_id: ["{"e" * 40}"], '))
        with self.assertRaises(CatalogError) as raised:
            self.share("local/my-ledger", "partner")
        self.assertEqual(lines_of(raised.exception), ("share_maybe_public", 409, "Partner fixture may be public: the Lab cannot share "
                         "private content there. Fix: make sure its repository is private and add it again by its https address, then "
                         "share again."))
        self.item("addon", "plain", "Plain", notes())
        self.assertIn("branch", self.share("local/plain", "partner"), "what holds no private content may go")
