"""catalogs/moving.py: Move to Personal. Sandbox imported from its file as a catalog of its own (with Scratch, which it brings),
then moved: alone, its include of Scratch then leads to the import; together, both move and the import goes; what an item that does
not move uses stays, and an item that one which does not move includes cannot move at all."""

import os
import shutil

from lab_ui import paths
from lab_ui.catalogs import exports, gitx, moving, registry, service
from lab_ui.catalogs.registry import CatalogError
from lab_ui.docker import compose, envfiles, files
from lab_ui.library import check, project

from .. import Case, fixtures


def held(case):
    """The lock of the catalogs held by another thread, as a change under way does, until the test ends."""
    import threading
    taken, done = threading.Event(), threading.Event()

    def hold():
        with registry.flock():
            taken.set()
            done.wait(30)
    worker = threading.Thread(target=hold, daemon=True)
    worker.start()
    taken.wait(10)
    case.addCleanup(worker.join)
    case.addCleanup(done.set)


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

    def test_a_move_that_fails_midway_leaves_the_import_as_it_was(self):
        copy2, calls = shutil.copy2, []

        def failing(*args, **how):
            calls.append(args)
            if len(calls) > 1:
                raise OSError(28, "No space left on device")
            return copy2(*args, **how)
        self.patch(moving.shutil, "copy2", failing)
        with self.assertRaises(OSError):
            moving.move("playground", "@sandbox/sandbox", True)
        imported = paths.ROOT / exports.IMPORTS_DIR / "sandbox"
        self.assertTrue((imported / "playgrounds/sandbox/compose.yml").is_file() and (imported / "addons/scratch/compose.yml").is_file())
        self.assertFalse(self.local("playgrounds/sandbox").exists() or self.local("addons/scratch").exists(), "nothing half moved stays")
        self.assertIsNotNone(registry.entry("sandbox"))

    def test_an_item_of_personal_it_replaces_comes_back_whole_when_the_move_fails(self):
        fixtures.local(self, "sandbox")
        self.local("playgrounds/sandbox/.env").write_text("SANDBOX_LEVEL=mine\n")
        before = {path.relative_to(self.local("playgrounds/sandbox")).as_posix(): path.read_bytes()
                  for path in self.local("playgrounds/sandbox").rglob("*") if path.is_file()}
        project.forget()
        copy2, calls = shutil.copy2, []

        def failing(*args, **how):
            calls.append(args)
            if len(calls) > 3:
                raise OSError(28, "No space left on device")
            return copy2(*args, **how)
        self.patch(moving.shutil, "copy2", failing)
        with self.assertRaises(OSError):
            moving.move("playground", "@sandbox/sandbox", True)
        after = {path.relative_to(self.local("playgrounds/sandbox")).as_posix(): path.read_bytes()
                 for path in self.local("playgrounds/sandbox").rglob("*") if path.is_file()}
        self.assertEqual(after, before, "the person's item is as it was")
        self.assertFalse(self.local("addons/scratch").exists())
        self.assertFalse(list((paths.ROOT / paths.LAB_DIR).glob(".move-*")), "nothing is left aside")

    def test_what_the_import_cannot_delete_after_a_move_is_said_and_the_move_completes(self):
        made = paths.ROOT / exports.IMPORTS_DIR / "sandbox" / "addons" / "scratch" / "data"
        made.mkdir()
        (made / "db").write_text("what a container wrote\n")
        made.chmod(0o500)   # what Docker made as root: this user cannot delete what is in it
        self.addCleanup(made.chmod, 0o700)
        project.forget()
        done = moving.move("playground", "@sandbox/sandbox", True)
        self.assertEqual(done["warning"], "Some files were created by Docker and could not be deleted. Remove the folder "
                                          ".lab/imports/sandbox with administrator rights.")
        self.assertEqual(sorted(one["id"] for one in done["moved"]), ["local/sandbox", "local/scratch"])
        self.assertIsNone(registry.entry("sandbox"), "the import whose items all moved is removed")
        found = project.load()
        self.assertIn("local/scratch", found["addons"])
        self.assertNotIn("@sandbox/scratch", found["addons"])

    def test_the_persons_files_of_the_item_it_replaces_are_merged_file_by_file_and_theirs_kept_on_a_clash(self):
        fixtures.local(self, "sandbox")
        mine = self.local("playgrounds/sandbox")
        (mine / "secrets").mkdir()
        (mine / "secrets/b").write_text("my own b\n")
        (mine / "secrets/both").write_text("mine\n")
        (mine / ".env").write_text("MINE=1\n")
        imported = paths.ROOT / exports.IMPORTS_DIR / "sandbox" / "playgrounds/sandbox"
        (imported / "secrets").mkdir()
        (imported / "secrets/a").write_text("import a\n")
        (imported / "secrets/both").write_text("import\n")
        (imported / ".state").mkdir()
        (imported / ".state/created.json").write_text("{}\n")
        project.forget()
        moving.move("playground", "@sandbox/sandbox", False)
        self.assertEqual({path.relative_to(mine).as_posix(): path.read_text() for path in mine.rglob("*")
                          if path.is_file() and path.relative_to(mine).parts[0] in (".env", "secrets", ".state")},
                         {".env": "MINE=1\n", "secrets/a": "import a\n", "secrets/b": "my own b\n", "secrets/both": "mine\n",
                          ".state/created.json": "{}\n"})
        self.assertFalse(list((paths.ROOT / paths.LAB_DIR).glob(".move-*")))

    def test_what_was_set_aside_and_cannot_be_deleted_after_a_move_is_said(self):
        fixtures.local(self, "sandbox")
        made = self.local("playgrounds/sandbox/data")
        made.mkdir()
        (made / "db").write_text("what a container wrote\n")
        made.chmod(0o500)
        project.forget()
        try:
            done = moving.move("playground", "@sandbox/sandbox", False)
        finally:
            for one in (paths.ROOT / paths.LAB_DIR).glob(".move-*/playgrounds/sandbox/data"):
                one.chmod(0o700)
        aside = next((paths.ROOT / paths.LAB_DIR).glob(".move-*"))
        self.assertEqual(done["warning"], f"Some files were created by Docker and could not be deleted. Remove the folder "
                                          f".lab/{aside.name} with administrator rights.")

    def test_a_move_waits_for_another_change_of_catalogs(self):
        held(self)
        with self.assertRaises(CatalogError) as caught:
            moving.move("playground", "@sandbox/sandbox", True)
        self.assertEqual(caught.exception.code, "busy")
        self.assertFalse(self.local("playgrounds/sandbox").exists(), "nothing moved")

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

    def test_a_link_in_the_original_is_not_followed(self):
        outside = paths.ROOT.parent / "outside.txt"
        outside.write_text("what the link points to\n")
        self.addCleanup(outside.unlink)
        echo = paths.ROOT / paths.unit_folder("addon", "echo")
        mode = echo.stat().st_mode
        echo.chmod(mode | 0o200)
        (echo / "link").symlink_to(outside)
        echo.chmod(mode)
        moving.copy("addon", "echo")
        self.assertFalse(os.path.lexists(self.local("addons/echo/link")), "a link is no file of the item")
        self.assertTrue(self.local("addons/echo/compose.yml").is_file())

    def test_a_copy_that_fails_midway_leaves_no_half_copy(self):
        write, calls = moving.Path.write_text, []

        def failing(path, *args, **how):
            calls.append(path)
            if len(calls) > 1:
                raise OSError(28, "No space left on device")
            return write(path, *args, **how)
        self.patch(moving.Path, "write_text", failing)
        with self.assertRaises(OSError):
            moving.copy("addon", "echo", users=True)
        self.assertEqual([path.name for path in self.local("addons").iterdir()], ["scratch"])
        self.assertFalse(self.local("setups").exists() and any(self.local("setups").iterdir()))
        self.assertIn("../../../fixture/addons/echo/compose.yml", self.local("addons/scratch/compose.yml").read_text())

    def test_a_copy_waits_for_another_change_of_catalogs(self):
        held(self)
        with self.assertRaises(CatalogError) as caught:
            moving.copy("addon", "echo")
        self.assertEqual(caught.exception.code, "busy")

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


class Stopped(Case):
    """What a change left set aside when the Lab stopped in the middle of it comes back at the next change of catalogs, and is said."""

    def setUp(self):
        fixtures.need_compose()
        fixtures.project(self, paths, project, compose, envfiles, check, files, ns="fixture", kind="git", store=True)
        self.patch(gitx, "home", lambda: paths.ROOT.parent)
        project.forget()
        self.addCleanup(project.forget)

    def local(self, rel):
        return paths.ROOT / paths.CATALOGS_DIR / paths.LOCAL / rel

    def test_an_item_set_aside_comes_back_whole_or_gives_back_the_persons_files(self):
        from .test_exports import evil_playground, evil_zip
        aside = paths.ROOT / paths.LAB_DIR / ".move-0a1b2c3d"
        (aside / "playgrounds/sandbox").mkdir(parents=True)
        (aside / "playgrounds/sandbox/compose.yml").write_text("services: {}\n")
        (aside / "playgrounds/sandbox/.env").write_text("MINE=1\n")
        (aside / "addons/scratch/secrets").mkdir(parents=True)
        (aside / "addons/scratch/secrets/b").write_text("my own b\n")
        self.local("addons/scratch").mkdir(parents=True)
        self.local("addons/scratch/compose.yml").write_text("services: {}\n")   # what the move had written there
        preview = exports.receive(evil_zip(playground=evil_playground(privileged=False)))
        self.assertEqual(preview["warning"], "A change that stopped midway had set scratch, sandbox aside: they are in Personal again.")
        self.assertEqual(self.local("playgrounds/sandbox/.env").read_text(), "MINE=1\n")
        self.assertEqual(self.local("addons/scratch/secrets/b").read_text(), "my own b\n")
        self.assertFalse(aside.exists())
