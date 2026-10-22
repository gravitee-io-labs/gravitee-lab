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

    def test_only_an_imported_item_moves(self):
        with self.assertRaises(CatalogError) as caught:
            moving.plan("addon", "echo")
        self.assertEqual(caught.exception.code, "not_imported")
