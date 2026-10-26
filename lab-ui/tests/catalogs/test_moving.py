"""catalogs/moving.py: Move to Personal. Sandbox imported from its file as a catalog of its own (with Scratch, which it brings),
then moved: alone, its include of Scratch then leads to the import; together, both move and the import goes; what an item that does
not move uses stays, and an item that one which does not move includes cannot move at all."""

import json
import os
import shutil
import time

from lab_ui import paths
from lab_ui.catalogs import contribute, exports, gitx, moving, registry, service
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

    def failing_copy(self, after=1, error=OSError(28, "No space left on device")):
        """The writing of the new items fails at its second item, as a full disk makes it, or a stop of the Lab (`error`)."""
        copytree, calls = shutil.copytree, []

        def failing(*args, **how):
            calls.append(args)
            if len(calls) > after:
                raise error
            return copytree(*args, **how)
        self.patch(moving.shutil, "copytree", failing)

    def files_of(self, folder):
        return {path.relative_to(folder).as_posix(): path.read_bytes() for path in folder.rglob("*") if path.is_file()}

    def test_a_move_that_fails_while_it_writes_changes_nothing(self):
        fixtures.local(self, "sandbox")
        mine = self.local("playgrounds/sandbox")
        (mine / ".env").write_text("SANDBOX_LEVEL=mine\n")
        before = self.files_of(mine)
        project.forget()
        self.failing_copy()
        with self.assertRaises(OSError):
            moving.move("playground", "@sandbox/sandbox", True)
        self.assertEqual(self.files_of(mine), before, "the person's item is as it was")
        self.assertFalse(self.local("addons/scratch").exists(), "nothing half written is in Personal")
        imported = paths.ROOT / exports.IMPORTS_DIR / "sandbox"
        self.assertTrue((imported / "playgrounds/sandbox/compose.yml").is_file() and (imported / "addons/scratch/compose.yml").is_file())
        self.assertIsNotNone(registry.entry("sandbox"))
        self.assertFalse(list((paths.ROOT / paths.LAB_DIR).glob(".swap-*")), "what it wrote is gone")

    def test_what_a_stopped_lab_was_writing_goes_and_what_a_live_one_writes_stays(self):
        fixtures.local(self, "sandbox")
        mine = self.local("playgrounds/sandbox")
        before = self.files_of(mine)
        for name, pid in (("dead", 2 ** 22 + 7), ("live", os.getpid())):
            swap = paths.ROOT / paths.LAB_DIR / f".swap-{name}"
            (swap / "new/playgrounds/sandbox").mkdir(parents=True)
            (swap / "new/playgrounds/sandbox/compose.yml").write_text("services: {}\n")
            (swap / "journal.json").write_text(json.dumps({"phase": "staging", "pid": pid, "started": time.time()}))
        self.assertIsNone(moving.recover(), "what was being written was never seen: it goes, and nothing is said")
        self.assertFalse((paths.ROOT / paths.LAB_DIR / ".swap-dead").exists())
        self.assertTrue((paths.ROOT / paths.LAB_DIR / ".swap-live").exists(), "another Lab UI, or this one, writes it now")
        self.assertEqual(self.files_of(mine), before)

    def test_the_moved_items_settings_win_and_those_of_personal_it_replaced_are_kept(self):
        fixtures.local(self, "sandbox")
        mine = self.local("playgrounds/sandbox")
        (mine / ".env").write_text("OLD_PERSONAL=1\n")
        (mine / "secrets").mkdir()
        (mine / "secrets/b").write_text("my own b\n")
        (mine / "secrets/both").write_text("mine\n")
        (mine / ".state").mkdir()
        (mine / ".state/created.json").write_text('{"mine": 1}\n')
        imported = paths.ROOT / exports.IMPORTS_DIR / "sandbox" / "playgrounds/sandbox"
        (imported / ".env").write_text("TYPED_WHILE_IMPORTED=1\n")
        (imported / "secrets").mkdir()
        (imported / "secrets/a").write_text("import a\n")
        (imported / "secrets/both").write_text("import\n")
        (imported / ".state").mkdir()
        (imported / ".state/created.json").write_text('{"import": 1}\n')
        project.forget()
        done = moving.move("playground", "@sandbox/sandbox", False)
        kept = next((paths.ROOT / exports.KEPT_DIR).glob("local-*"))
        self.assertEqual(done["warning"], "Settings of Sandbox that the new version replaced are kept: see Kept settings on the Catalogs page.")
        ours = {rel: data.decode() for rel, data in self.files_of(mine).items() if rel.split("/")[0] in (".env", "secrets", ".state")}
        self.assertEqual(ours, {".env": "TYPED_WHILE_IMPORTED=1\n", "secrets/a": "import a\n", "secrets/b": "my own b\n",
                                "secrets/both": "import\n", ".state/created.json": '{"import": 1}\n'}, "it runs with what it ran with")
        self.assertEqual({rel: data.decode() for rel, data in self.files_of(kept).items()},
                         {"playgrounds/sandbox/.env": "OLD_PERSONAL=1\n", "playgrounds/sandbox/secrets/both": "mine\n",
                          "playgrounds/sandbox/.state/created.json": '{"mine": 1}\n'}, "and nothing of the person's is lost")
        self.assertFalse(list((paths.ROOT / paths.LAB_DIR).glob(".swap-*")))

    def test_a_setting_that_can_be_neither_brought_nor_kept_is_said_and_never_comes_back_as_an_item(self):
        fixtures.local(self, "sandbox")
        mine = self.local("playgrounds/sandbox")
        (mine / "compose.yml").write_text((mine / "compose.yml").read_text() + "# OLD VERSION\n")
        (mine / ".state").mkdir()
        (mine / ".state/root.json").write_text("{}\n")
        (mine / ".state/root.json").chmod(0o000)   # what Docker made as root: neither moved nor read
        self.addCleanup(lambda: [one.chmod(0o600) for one in paths.ROOT.rglob("root.json")])
        imported = paths.ROOT / exports.IMPORTS_DIR / "sandbox" / "playgrounds/sandbox"
        (imported / ".state").mkdir()
        (imported / ".state/created.json").write_text("{}\n")
        project.forget()
        rename = os.rename
        self.patch(moving._kept().os, "rename", lambda one, two: (_ for _ in ()).throw(OSError(18, "Cross-device link"))
                   if ".state" in str(one) else rename(one, two))
        done = moving.move("playground", "@sandbox/sandbox", False)
        left = next((paths.ROOT / paths.LAB_DIR).glob(".leftover-*"))
        self.assertEqual(done["warning"], f"Settings of Sandbox could not be kept and are still in .lab/{left.name}: copy them out, "
                                          "then remove that folder with administrator rights.")
        self.assertTrue((left / "old/playgrounds/sandbox/.state/root.json").exists(), "the setting is not deleted")
        self.assertFalse((left / "old/playgrounds/sandbox/compose.yml").exists(), "the rest of the old item is")
        self.assertIsNone(moving.recover(), "a leftover is never read again")
        shutil.rmtree(mine)
        self.assertIsNone(moving.recover())
        self.assertFalse(mine.exists(), "the old item does not come back")

    def test_a_swap_is_all_its_items_or_none_and_says_what_was_put_back(self):
        fixtures.local(self, "sandbox")
        mine = self.local("playgrounds/sandbox")
        (mine / ".env").write_text("OLD_PERSONAL=1\n")
        before = self.files_of(mine)
        project.forget()
        rename = os.rename
        self.patch(moving.os, "rename", lambda one, two: (_ for _ in ()).throw(OSError(28, "No space left on device"))
                   if str(one).endswith("new/addons/scratch") else rename(one, two))
        with self.assertRaises(CatalogError) as caught:
            moving.move("playground", "@sandbox/sandbox", True)
        self.assertEqual((caught.exception.code, caught.exception.message), ("swap_failed", "Sandbox, Scratch could not be put in "
                         "Personal (no space left on device): Personal is as it was. Fix: see that the disk has room and that .lab "
                         "can be written, then try again."))
        self.assertEqual(self.files_of(mine), before, "the first item is back as it was")
        self.assertFalse(self.local("addons/scratch").exists())
        self.assertTrue((paths.ROOT / exports.IMPORTS_DIR / "sandbox/playgrounds/sandbox/compose.yml").is_file())
        self.assertFalse(list((paths.ROOT / paths.LAB_DIR).glob(".swap-*")))

    def test_a_check_that_fails_in_any_way_undoes_the_swap(self):
        fixtures.local(self, "sandbox")
        mine = self.local("playgrounds/sandbox")
        before = self.files_of(mine)
        project.forget()
        self.patch(moving.project, "read_own", lambda file: (_ for _ in ()).throw(RuntimeError("the reader broke")))
        with self.assertRaisesRegex(RuntimeError, "^the reader broke$"):
            moving.move("playground", "@sandbox/sandbox", True)
        self.assertEqual(self.files_of(mine), before, "Personal is as it was")
        self.assertFalse(self.local("addons/scratch").exists())
        self.assertTrue((paths.ROOT / exports.IMPORTS_DIR / "sandbox/playgrounds/sandbox/compose.yml").is_file())
        self.assertFalse(list((paths.ROOT / paths.LAB_DIR).glob(".swap-*")), "no swap is left half in")

    def test_an_item_the_check_cannot_read_once_placed_undoes_the_swap_and_says_why(self):
        fixtures.local(self, "sandbox")
        mine = self.local("playgrounds/sandbox")
        before = self.files_of(mine)
        project.forget()
        real = project.read_own
        self.patch(moving.project, "read_own", lambda file: (None, "shared/x.yml is not there") if "scratch" in file else real(file))
        with self.assertRaises(CatalogError) as caught:
            moving.move("playground", "@sandbox/sandbox", True)
        self.assertEqual((caught.exception.code, caught.exception.message), ("placed_unread", "Scratch could not be read once in "
                         "Personal (shared/x.yml is not there), so Personal is as it was. Fix: keep it in "
                         "Imported, where the Lab says why."))
        self.assertEqual(self.files_of(mine), before)
        self.assertFalse(self.local("addons/scratch").exists())
        self.assertFalse(list((paths.ROOT / paths.LAB_DIR).glob(".swap-*")))

    def test_the_check_of_a_move_reads_the_items_it_placed_and_not_the_project(self):
        read, loads = [], []
        real_read, real_load = project.read_own, project.load
        self.patch(moving.project, "read_own", lambda file: read.append(file) or real_read(file))
        self.patch(moving.project, "load", lambda *a, **k: loads.append(read[:]) or real_load(*a, **k))
        moving.move("playground", "@sandbox/sandbox", True)
        self.assertEqual(sorted(read), [".lab/catalogs/local/addons/scratch/compose.yml", ".lab/catalogs/local/playgrounds/sandbox/compose.yml"])
        self.assertFalse([one for one in loads if one], "the project is not read again once the items are placed")

    def test_a_file_where_the_person_has_a_folder_keeps_it_and_wedges_nothing(self):
        from .test_exports import evil_playground, evil_zip
        fixtures.local(self, "sandbox")
        mine = self.local("playgrounds/sandbox")
        (mine / "secrets/tls").mkdir(parents=True)
        (mine / "secrets/tls/key.pem").write_text("my key\n")
        imported = paths.ROOT / exports.IMPORTS_DIR / "sandbox" / "playgrounds/sandbox"
        (imported / "secrets").mkdir()
        (imported / "secrets/tls").write_text("a file here\n")
        project.forget()
        done = moving.move("playground", "@sandbox/sandbox", False)
        kept = next((paths.ROOT / exports.KEPT_DIR).glob("local-*"))
        self.assertIn("Settings of Sandbox that the new version replaced are kept: see Kept settings on the Catalogs page.", done["warning"])
        self.assertEqual((kept / "playgrounds/sandbox/secrets/tls/key.pem").read_text(), "my key\n")
        self.assertFalse(list((paths.ROOT / paths.LAB_DIR).glob(".swap-*")))
        self.assertIsNone(moving.recover())
        exports.receive(evil_zip(playground=evil_playground(privileged=False)))   # the next change goes through

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

    def test_what_the_import_cannot_delete_of_an_item_that_moved_alone_names_that_items_folder(self):
        made = paths.ROOT / exports.IMPORTS_DIR / "sandbox" / "playgrounds" / "sandbox" / "data"
        made.mkdir()
        (made / "db").write_text("what a container wrote\n")
        made.chmod(0o500)
        self.addCleanup(made.chmod, 0o700)
        project.forget()
        done = moving.move("playground", "@sandbox/sandbox", False)   # Scratch stays: the import is still a catalog
        self.assertIsNotNone(registry.entry("sandbox"))
        self.assertEqual(done["warning"], "Some files were created by Docker and could not be deleted. Remove the folder "
                                          ".lab/imports/sandbox/playgrounds/sandbox with administrator rights.")

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

    def test_a_copy_reads_back_on_the_oldest_compose_as_on_the_newest(self):
        fixtures.oldest_compose(self)   # an empty label is null there: the copy says it has none all the same
        self.assertEqual(moving.copy("addon", "echo")["id"], "local/echo")
        text = self.local("addons/echo/compose.yml").read_text()
        self.assertIn("private_from: []", text)
        self.assertEqual(contribute.origins(text.replace('copy_of: "echo"', 'copy_of: "@lab-new/notes"'), "local/echo"), [],
                         "a copy the Lab made says its origins, even none: its copy_of is not followed")

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

    def journal(self, name, phase, items):
        swap = paths.ROOT / paths.LAB_DIR / f".swap-{name}"
        swap.mkdir(parents=True, exist_ok=True)
        (swap / "journal.json").write_text(json.dumps({"phase": phase, "items": items}))
        return swap

    def item(self, root, rel, name, **files):
        (root / rel).mkdir(parents=True, exist_ok=True)
        (root / rel / "compose.yml").write_text(f"x-gravitee: {{kind: playground, name: {name}}}\nservices: {{}}\n")
        for path, text in files.items():
            (root / rel / path.replace("__", "/")).parent.mkdir(parents=True, exist_ok=True)
            (root / rel / path.replace("__", "/")).write_text(text)

    def test_a_change_stopped_before_all_its_items_were_in_is_undone_from_its_journal(self):
        from .test_exports import evil_playground, evil_zip
        swap = self.journal("0a1b2c3d", "in", ["playgrounds/sandbox", "playgrounds/other"])
        self.item(swap / "old", "playgrounds/sandbox", "Sandbox", **{".env": "MINE=1\n"})
        self.item(swap / "old", "playgrounds/other", "Other")
        self.item(self.local(""), "playgrounds/sandbox", "New sandbox")   # the first new one was in
        self.item(swap / "new", "playgrounds/other", "New other")   # the second was not
        preview = exports.receive(evil_zip(playground=evil_playground(privileged=False)))
        self.assertEqual(preview["warning"], "A change of Personal that stopped midway was undone for Sandbox, Other.")
        self.assertEqual(self.local("playgrounds/sandbox/.env").read_text(), "MINE=1\n", "each item as it was")
        self.assertIn("name: Other", self.local("playgrounds/other/compose.yml").read_text())
        self.assertFalse(swap.exists())

    def test_a_change_stopped_with_all_its_items_in_is_finished_from_its_journal(self):
        swap = self.journal("0a1b2c3d", "done", ["addons/scratch"])
        self.item(swap / "old", "addons/scratch", "Scratch", **{"secrets__b": "my own b\n"})
        self.item(self.local(""), "addons/scratch", "Scratch")
        self.assertEqual(moving.recover(), "A change of Personal that stopped midway was finished for Scratch.")
        self.assertEqual(self.local("addons/scratch/secrets/b").read_text(), "my own b\n")
        self.assertFalse(swap.exists())

    def test_a_swap_that_cannot_be_put_right_keeps_no_other_nor_the_start_from_being(self):
        bad = self.journal("0a1b2c3d", "aside", ["playgrounds/sandbox"])
        self.item(bad / "old", "playgrounds/sandbox", "Sandbox")
        good = self.journal("1b2c3d4e", "aside", ["addons/scratch"])
        self.item(good / "old", "addons/scratch", "Scratch")
        rename = os.rename
        self.patch(moving.os, "rename", lambda one, two: (_ for _ in ()).throw(OSError(13, "Permission denied"))
                   if "0a1b2c3d" in str(one) else rename(one, two))
        said = moving.recover()
        self.assertIn("A change of Personal that stopped midway was undone for Scratch.", said)
        self.assertIn("could not be put right (", said)
        self.assertIn("it is kept in .lab/.swap-0a1b2c3d", said)
        self.assertTrue(self.local("addons/scratch/compose.yml").is_file())
        self.assertTrue(bad.exists(), "what holds the person's files is not deleted")

    def test_what_the_start_of_a_change_put_right_is_said_when_the_change_is_refused(self):
        swap = self.journal("0a1b2c3d", "aside", ["playgrounds/sandbox"])
        self.item(swap / "old", "playgrounds/sandbox", "Sandbox")
        with self.assertRaises(CatalogError) as caught:
            moving.move("addon", "echo", False)   # no import: refused, after the start of the change put Sandbox back
        self.assertEqual(caught.exception.message, "Echo is not an imported item: only those move to Personal. A change of Personal "
                                                   "that stopped midway was undone for Sandbox.")
        self.assertTrue(self.local("playgrounds/sandbox/compose.yml").is_file())

    def test_the_start_of_the_lab_ui_puts_right_under_the_lock_every_lab_ui_of_this_folder_takes(self):
        held = []
        self.patch(moving, "recover", lambda: held.append(str(paths.ROOT / registry.FLOCK_FILE) in registry._held.__dict__.get("held", {})))
        moving.recover_at_start()
        self.assertEqual(held, [True])

    def test_a_swap_with_no_journal_that_holds_the_persons_files_is_kept_and_said(self):
        swap = paths.ROOT / paths.LAB_DIR / ".swap-0a1b2c3d"
        (swap / "old/playgrounds/sandbox").mkdir(parents=True)
        (swap / "old/playgrounds/sandbox/.env").write_text("MINE=1\n")
        self.assertEqual(moving.recover(), "A change of Personal left files of yours in .lab/.swap-0a1b2c3d, and no journal that says "
                                           "what they are: they are kept there. Fix: copy what you need into your items, then delete "
                                           "that folder.")
        self.assertEqual((swap / "old/playgrounds/sandbox/.env").read_text(), "MINE=1\n")

    def test_a_swap_being_written_for_too_long_is_a_leftover_whoever_has_its_writers_number(self):
        swap = self.journal("0a1b2c3d", "staging", [])
        (swap / "journal.json").write_text(json.dumps({"phase": "staging", "pid": os.getpid(), "started": 1}))
        self.assertIsNone(moving.recover())
        self.assertFalse(swap.exists())
