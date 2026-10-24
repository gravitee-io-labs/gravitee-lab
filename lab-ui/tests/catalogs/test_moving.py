"""catalogs/moving.py: Move to Personal. Sandbox imported from its file as a catalog of its own (with Scratch, which it brings),
then moved: alone, its include of Scratch then leads to the import; together, both move and the import goes; what an item that does
not move uses stays, and an item that one which does not move includes cannot move at all."""

import shutil

from lab_ui import paths
from lab_ui.catalogs import exports, gitx, moving, registry, service
from lab_ui.catalogs.registry import CatalogError
from lab_ui.docker import compose, envfiles, files
from lab_ui.library import check, project

from .. import Case, fixtures


class Moving(Case):

    def setUp(self):
        fixtures.need_compose()
        fixtures.project(self, paths, project, compose, envfiles, check, files, ns="fixture", kind="git", store=True)
        fixtures.local(self, "sandbox")
        fixtures.local(self, "scratch")
        self.patch(gitx, "home", lambda: paths.ROOT.parent)
        self.patch(exports, "_address", lambda ns: "https://github.com/acme/fixture-catalog.git")
        self.patch(exports.images, "private_images", lambda pulled: {})
        project.forget()
        self.addCleanup(project.forget)
        data = exports.archive("local/sandbox")["data"]
        for rel in ("playgrounds/sandbox", "addons/scratch"):   # what was exported is no longer of this machine: it comes back imported
            shutil.rmtree(self.local(rel))
        service.add(exports.receive(data)["ticket"])
        (paths.ROOT / exports.IMPORTS_DIR / "sandbox" / "addons" / "scratch" / ".env").write_text("SCRATCH_NOTES_VERSION=1.35\n")
        project.forget()

    def local(self, rel):
        return paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / rel

    def test_the_plan_says_what_each_choice_moves(self):
        plan = moving.plan("playground", "@sandbox/sandbox")
        self.assertEqual(plan["alone"], {"moves": ["Sandbox"], "stays": [], "replaces": [], "held": "", "uses_machine": []})
        self.assertEqual(plan["together"], {"moves": ["Sandbox", "Scratch"], "stays": [], "replaces": [], "held": "", "uses_machine": []})
        self.assertEqual(plan["used_by"], "")

    def test_together_both_move_with_their_settings_and_the_import_goes(self):
        done = moving.move("playground", "@sandbox/sandbox", True)
        self.assertEqual(done["id"], "local/sandbox")
        self.assertIn("../../addons/scratch/compose.yml", self.local("playgrounds/sandbox/compose.yml").read_text())
        self.assertIn("../../../fixture/addons/echo/compose.yml", self.local("addons/scratch/compose.yml").read_text())
        self.assertEqual(self.local("addons/scratch/.env").read_text(), "SCRATCH_NOTES_VERSION=1.35\n", "its settings come along")
        self.assertIsNone(registry.entry("sandbox"), "the import whose items all moved is removed")
        self.assertFalse((paths.ROOT / exports.IMPORTS_DIR / "sandbox").exists())
        found = project.load()
        self.assertIn("local/sandbox", found["playgrounds"])
        self.assertIsNone(found["playgrounds"]["local/sandbox"].get("blocked"))

    def test_alone_the_playground_includes_what_stays_in_the_import(self):
        moving.move("playground", "@sandbox/sandbox", False)
        self.assertIn("../../../sandbox/addons/scratch/compose.yml", self.local("playgrounds/sandbox/compose.yml").read_text())
        self.assertIsNotNone(registry.entry("sandbox"), "the import keeps Scratch")
        found = project.load()
        self.assertIn("@sandbox/scratch", found["playgrounds"]["local/sandbox"]["addons"])

    def test_what_an_item_that_does_not_move_uses_stays_and_says_by_whom(self):
        other = self.local("playgrounds/notes/compose.yml")
        other.parent.mkdir(parents=True)
        other.write_text("x-gravitee: {kind: playground, name: Notes, category: Other, description: Notes of this machine}\n"
                         "include:\n  - ../../../sandbox/addons/scratch/compose.yml\nservices: {}\n")
        project.forget()
        plan = moving.plan("playground", "@sandbox/sandbox")
        self.assertIsNone(plan["together"], "nothing else can move with it: the one choice is alone")
        self.assertEqual((plan["alone"]["moves"], plan["alone"]["stays"]), (["Sandbox"], [{"name": "Scratch", "why": "Used by Notes"}]))
        moving.move("playground", "@sandbox/sandbox", True)
        self.assertTrue((paths.ROOT / exports.IMPORTS_DIR / "sandbox" / "addons" / "scratch").is_dir(), "Scratch stays for Notes")

    def test_an_item_that_one_which_does_not_move_includes_cannot_move(self):
        plan = moving.plan("addon", "@sandbox/scratch")
        self.assertEqual(plan["used_by"], "Sandbox")
        with self.assertRaises(CatalogError) as caught:
            moving.move("addon", "@sandbox/scratch", False)
        self.assertEqual((caught.exception.code, caught.exception.message), ("used", "Used by Sandbox: move that one to Personal instead."))

    def test_what_takes_more_of_this_machine_moves_once_allowed_and_what_is_blocked_for_more_does_not(self):
        verdicts = {"@sandbox/scratch": {"state": "blocked", "codes": ["T03"], "reasons": ["Runs with full privileges"]}}
        load = project.load

        def judged():
            found = load()
            for uid, verdict in verdicts.items():
                found["addons"][uid] = {**found["addons"][uid], "verdict": verdict}
            return found
        self.patch(moving.project, "load", judged)
        plan = moving.plan("playground", "@sandbox/sandbox")
        self.assertEqual((plan["together"]["uses_machine"], plan["together"]["held"]), (["Scratch"], ""))
        with self.assertRaises(CatalogError) as caught:
            moving.move("playground", "@sandbox/sandbox", True)
        self.assertEqual(caught.exception.code, "allow_machine")
        self.assertEqual(caught.exception.message, "Scratch uses this machine: allow it to move it to Personal.")
        verdicts["@sandbox/scratch"] = {"state": "blocked", "codes": ["T20"], "reasons": ["Reads settings that are not its own"]}
        plan = moving.plan("playground", "@sandbox/sandbox")
        self.assertEqual(plan["together"]["held"], "Scratch cannot run: Reads settings that are not its own")
        self.assertEqual(plan["alone"]["held"], "", "alone, Scratch does not move")

    def test_an_item_of_personal_that_is_on_is_not_replaced(self):
        fixtures.local(self, "sandbox")
        before = self.local("playgrounds/sandbox/compose.yml").read_text()
        self.write(paths.ON_FILE, "include:\n  - .lab/catalogs/local/playgrounds/sandbox/compose.yml\n")
        self.patch(moving.personals.compose, "containers", lambda *args, **how: ({}, None))   # Docker is not asked in a test
        project.forget()
        plan = moving.plan("playground", "@sandbox/sandbox")
        self.assertEqual(plan["alone"]["replaces"], ["Sandbox"])
        self.assertEqual(plan["alone"]["held"], "Sandbox in Personal is on and would be replaced. Fix: turn it off first.")
        with self.assertRaises(CatalogError) as caught:
            moving.move("playground", "@sandbox/sandbox", False)
        self.assertEqual(caught.exception.code, "held")
        self.assertEqual(self.local("playgrounds/sandbox/compose.yml").read_text(), before, "what is on is left as it is")

    def test_only_an_imported_item_moves(self):
        with self.assertRaises(CatalogError) as caught:
            moving.plan("addon", "echo")
        self.assertEqual(caught.exception.code, "not_imported")


class Copying(Case):
    """Copy to Personal: Echo of the default catalog, which Echo core and Pair include and Scratch of this machine too. Alone, the
    copy says whose copy it is and is its twin, so that neither blocks the other; with what uses it, Echo core and Pair are copied
    and include the copy, and Scratch does. Partner, whose Ledger Pair includes, is added: an item that waits for a catalog is not
    judged, and is not copied."""

    def setUp(self):
        fixtures.need_compose()
        fixtures.project(self, paths, project, compose, envfiles, check, files, ns="fixture", kind="git", store=True)
        fixtures.install(self, "partner", kind="git", audience="public")
        fixtures.local(self, "scratch")
        project.forget()
        self.addCleanup(project.forget)

    def local(self, rel):
        return paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / rel

    def test_the_plan_says_what_each_choice_copies_and_switches(self):
        plan = moving.copy_plan("addon", "echo")
        self.assertEqual(plan["alone"], {"copies": ["Echo"], "switches": [], "uses_machine": [], "held": ""})
        self.assertEqual(sorted(plan["with_users"]["copies"]), ["Echo", "Echo with the gateway", "Pair"])
        self.assertEqual(plan["with_users"]["switches"], ["Scratch"])

    def test_the_copy_is_the_originals_twin_and_neither_blocks_the_other(self):
        done = moving.copy("addon", "echo")
        self.assertEqual(done["id"], "local/echo")
        text = self.local("addons/echo/compose.yml").read_text()
        self.assertIn('copy_of: "echo"', text)
        found = project.load()
        self.assertEqual(found["addons"]["local/echo"]["twin"]["id"], "echo")
        self.assertTrue(found["addons"]["local/echo"]["twin"]["copy"])
        self.assertIsNone(found["addons"]["echo"]["blocked"], "the original is not blocked by the copy")
        self.assertIsNone(found["addons"]["local/echo"]["blocked"])
        self.assertIn("../../../fixture/addons/echo/compose.yml", self.local("addons/scratch/compose.yml").read_text(),
                      "alone, what used the original still does")

    def test_with_what_uses_it_the_users_are_copied_and_include_the_copy(self):
        moving.copy("addon", "echo", users=True)
        self.assertIn("../echo/compose.yml", self.local("addons/echo-core/compose.yml").read_text())
        self.assertIn("../addons/echo/compose.yml", self.local("setups/pair.yml").read_text())
        self.assertIn("../echo/compose.yml", self.local("addons/scratch/compose.yml").read_text(), "Scratch includes the copy")
        self.assertIn('copy_of: "pair"', self.local("setups/pair.yml").read_text())

    def test_an_item_of_personal_is_not_copied_and_a_second_copy_waits(self):
        with self.assertRaises(CatalogError) as caught:
            moving.copy_plan("addon", "local/scratch")
        self.assertEqual(caught.exception.code, "personal")
        moving.copy("addon", "echo")
        self.assertEqual(moving.copy_plan("addon", "echo")["alone"]["held"], "Echo is in Personal already: delete or rename that one first")

    def test_back_to_the_catalog_version_drops_the_copy_and_what_used_it_uses_the_original_again(self):
        from lab_ui.catalogs import contribute
        moving.copy("addon", "echo", users=True)
        contribute.use_catalog_version(project.load(), "addon", "local/echo")
        self.assertFalse(self.local("addons/echo").exists())
        for rel in ("addons/scratch/compose.yml", "addons/echo-core/compose.yml"):
            self.assertIn("fixture/addons/echo/compose.yml", self.local(rel).read_text(), rel)
        found = project.load()
        self.assertIsNone(found["addons"]["local/scratch"]["blocked"])


class Unjudged(Case):
    """An imported playground that includes an item of a catalog this Lab has not added: Compose cannot read it, so nothing judged
    it, and it can no more move or be copied to Personal than an item that is blocked."""

    def setUp(self):
        from .test_exports import evil_playground, evil_zip
        fixtures.need_compose()
        fixtures.project(self, paths, project, compose, envfiles, check, files, ns="fixture", kind="git", store=True)
        self.patch(gitx, "home", lambda: paths.ROOT.parent)
        project.forget()
        self.addCleanup(project.forget)
        data = evil_zip(playground=evil_playground(include="../../../partner/addons/ledger/compose.yml"), needs=True)
        service.add(exports.receive(data)["ticket"])
        project.forget()

    HELD = "Evil cannot run: it needs Partner fixture, which is not added. Fix: add Partner fixture on the Catalogs page first."

    def test_it_does_not_move(self):
        self.assertEqual(moving.plan("playground", "@evil/evil")["alone"]["held"], self.HELD)
        with self.assertRaises(CatalogError) as caught:
            moving.move("playground", "@evil/evil", False, allow=True)
        self.assertEqual((caught.exception.code, caught.exception.message), ("held", self.HELD))

    def test_it_is_not_copied(self):
        self.assertEqual(moving.copy_plan("playground", "@evil/evil")["alone"]["held"], self.HELD)
        with self.assertRaises(CatalogError) as caught:
            moving.copy("playground", "@evil/evil", allow=True)
        self.assertEqual((caught.exception.code, caught.exception.message), ("held", self.HELD))
        self.assertFalse((paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / "playgrounds" / "evil").exists())
