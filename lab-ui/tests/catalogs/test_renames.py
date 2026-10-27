"""catalogs/renames.py: a catalog renames an item, and everyone who has it keeps everything. Where each name goes between two
versions (a chain, a version skipped, a Go back), the settings renamed in a .env, what `follow` moves on disk for one install
(machine files, the on file and the copy for a platform, includes, copies, stars, tested sets, held settings, what a share
renamed), and the whole of it end to end: a scratch Lab that adds a catalog from a local git repository, turns
an item on, stars it, sets its settings, and updates to releases that rename it."""

import json
import os
import unittest
from unittest import mock

from lab_ui.docker import compose, envfiles, jobs
from lab_ui.gravitee import license
from lab_ui.library import check, layers, moves, project, starred
from lab_ui.server import stack
from lab_ui import paths
from lab_ui.catalogs import gitx, install, manifest, registry, renames, service, understood

from .. import Case, fixtures, gitfixture


# ---------------------------------------------------------------------------------------------------------------------
# Where each name goes
# ---------------------------------------------------------------------------------------------------------------------

class Resolving(unittest.TestCase):

    def test_an_update_moves_each_old_name_to_the_end_of_its_chain(self):
        later = {"addon:trace-viewer": "addon:agent-trace-viewer", "addon:viewer": "addon:trace-viewer"}
        self.assertEqual(renames.part({}, later), {"addon:trace-viewer": "addon:agent-trace-viewer",
                                                   "addon:viewer": "addon:agent-trace-viewer"},
                         "a Lab that skipped the release of the first rename goes straight to the last name")

    def test_a_version_that_keeps_its_pairs_moves_nothing_more_from_the_one_before(self):
        pairs = {"addon:gate": "addon:gate-keeper"}
        self.assertEqual(renames.part(pairs, pairs), pairs, "what is no longer there is nothing to move: follow judges the disk")

    def test_going_back_moves_the_new_name_to_the_one_the_older_version_has(self):
        newer = {"addon:gate": "addon:gate-keeper"}
        self.assertEqual(renames.part(newer, {}, back=True), {"addon:gate-keeper": "addon:gate"})
        self.assertEqual(renames.part(newer, {}), {}, "an update to a version that declares nothing renames nothing")

    def test_going_back_one_step_of_a_chain_ends_at_the_name_of_that_step(self):
        last = {"addon:viewer": "addon:trace-viewer", "addon:trace-viewer": "addon:agent-trace-viewer"}
        middle = {"addon:viewer": "addon:trace-viewer"}
        self.assertEqual(renames.part(last, middle, back=True),
                         {"addon:viewer": "addon:trace-viewer", "addon:agent-trace-viewer": "addon:trace-viewer"})

    def test_a_name_going_back_could_take_two_ways_stays(self):
        newer = {"addon:a": "addon:c", "addon:b": "addon:c"}
        self.assertEqual(renames.part(newer, {}, back=True), {}, "two items made one cannot be told apart again")

    def test_a_cycle_the_check_refuses_ends_where_it_would_repeat_and_never_loops(self):
        self.assertEqual(renames.part({}, {"A": "B", "B": "A"}), {})

    def test_the_parts_are_resolved_alike_from_two_blocks(self):
        later = {"items": {"addon:gate": "addon:gate-keeper"}, "settings": {"GATE_LICENSE": "GATE_KEEPER_LICENSE"}}
        self.assertEqual(renames.resolve(None, later), later)
        self.assertEqual(renames.resolve(later, None, back=True),
                         {"items": {"addon:gate-keeper": "addon:gate"},
                          "settings": {"GATE_KEEPER_LICENSE": "GATE_LICENSE"}})
        self.assertEqual(renames.resolve(None, None), {"items": {}, "settings": {}})

    def test_a_block_as_read_is_two_mappings_whatever_it_lacks(self):
        self.assertEqual(renames.declared({"renamed": {"items": {"addon:a": "addon:b"}}}), {"items": {"addon:a": "addon:b"}, "settings": {}})
        self.assertEqual(renames.declared(None), {"items": {}, "settings": {}})


class Settings(unittest.TestCase):

    def test_a_setting_takes_its_new_name_with_its_value_and_its_place(self):
        text = "# Mine\nexport GATE_LICENSE=abc==\nGATE_CLOUD_TOKEN=t\n"
        self.assertEqual(renames.rename_settings(text, {"GATE_LICENSE": "GATE_KEEPER_LICENSE",
                                                        "GATE_CLOUD_TOKEN": "GATE_KEEPER_CLOUD_TOKEN"}),
                         "# Mine\nexport GATE_KEEPER_LICENSE=abc==\nGATE_KEEPER_CLOUD_TOKEN=t\n")

    def test_a_setting_set_under_its_new_name_already_is_never_written_over(self):
        text = "OLD=1\nNEW=2\n"
        self.assertEqual(renames.rename_settings(text, {"OLD": "NEW"}), text)

    def test_only_a_whole_name_at_the_start_of_a_line_is_renamed(self):
        text = "MY_OLD=1\nOLDER=2\n# OLD=3\nOLD = 4\n"
        self.assertEqual(renames.rename_settings(text, {"OLD": "NEW"}), text)

    def test_a_env_that_holds_two_names_of_a_chain_gives_the_last_name_the_value_of_the_newer_one(self):
        found = renames.resolve(None, {"settings": {"OLD": "MIDDLE", "MIDDLE": "NEW"}})
        self.assertEqual(renames.rename_settings("OLD=1\nMIDDLE=2\n", found["settings"]), "OLD=1\nNEW=2\n")

    def test_renaming_twice_changes_nothing_more(self):
        once = renames.rename_settings("OLD=1\n", {"OLD": "NEW"})
        self.assertEqual(renames.rename_settings(once, {"OLD": "NEW"}), once)


# ---------------------------------------------------------------------------------------------------------------------
# What follow moves, for one install
# ---------------------------------------------------------------------------------------------------------------------

PARTNER = ".lab/catalogs/partner"
OLD_FILE, NEW_FILE = f"{PARTNER}/addons/books/compose.yml", f"{PARTNER}/addons/ledger/compose.yml"
FOUND = {"items": {"addon:books": "addon:ledger"}, "settings": {"BOOKS_CURRENCY": "PARTNER_LEDGER_CURRENCY"}}


class Following(Case):
    """The partner catalog installed from git with its add-on `ledger`, which was `books` before: what the person had under the
    old name is on disk where the old name put it."""

    def setUp(self):
        gitfixture.require()
        fixtures.project(self, paths, compose, ns="fixture")
        self.patch(paths, "_default", (None, None))
        gitfixture.isolate(self)
        fixtures.install(self, "partner", kind="git")
        self.old = self.root / PARTNER / "addons/books"

    def machine(self, **files):
        for rel, text in files.items():
            self.write(f"{PARTNER}/addons/books/{rel}", text)

    def text(self, rel):
        return (self.root / rel).read_text(encoding="utf-8")

    def test_the_machine_files_of_the_old_folder_move_to_the_new_one_and_the_old_folder_goes(self):
        self.machine(**{".env": "BOOKS_CURRENCY=CHF\nOTHER=1\n", ".state/created.json": "[]\n", "secrets/key": "k\n",
                        "data/db": "rows\n"})
        done = renames.follow("partner", FOUND)
        new = self.root / PARTNER / "addons/ledger"
        self.assertEqual(self.text(f"{PARTNER}/addons/ledger/.env"), "PARTNER_LEDGER_CURRENCY=CHF\nOTHER=1\n",
                         "the value is kept under the name the new item reads")
        self.assertEqual([(new / rel).read_text() for rel in (".state/created.json", "secrets/key", "data/db")],
                         ["[]\n", "k\n", "rows\n"])
        self.assertFalse(self.old.exists(), "nothing of the old folder is left")
        self.assertEqual(done, [{"kind": "addon", "from": "@partner/books", "to": "@partner/ledger",
                                 "moved": [".env", ".state", "data", "secrets"], "kept": []}])
        self.assertTrue(any(entry["op"] == "move" for entry in moves.read_journal()), "every move is in the journal")

    def test_an_entry_that_cannot_be_moved_whole_stops_the_rename_and_is_never_counted_as_moved(self):
        self.machine(**{".env": "BOOKS_CURRENCY=CHF\n", ".state/created.json": "[]\n"})
        self.write(paths.ON_FILE, f"include:\n  - {OLD_FILE}\n")
        real = moves.relocate
        for answer in ("copied", "stayed"):
            with self.subTest(answer=answer):
                self.patch(moves, "relocate", lambda src, dst, why: answer if src.endswith(".state") else real(src, dst, why))
                with self.assertRaises(registry.CatalogError) as raised:
                    renames.follow("partner", FOUND)
                self.assertEqual(raised.exception.code, "rename_stuck")
                self.assertEqual(raised.exception.detail["units"], ["addon:ledger"], "the caller names the item")
                self.assertNotIn(".state", str(raised.exception))
                self.assertTrue((self.old / ".state/created.json").is_file(), "what could not be moved is where it was")
                self.assertEqual(self.text(paths.ON_FILE), f"include:\n  - {OLD_FILE}\n", "nothing after it is changed")

    def test_an_entry_whose_place_is_taken_is_kept_aside_and_never_written_over(self):
        self.machine(**{".env": "BOOKS_CURRENCY=CHF\n", ".state/created.json": "old\n", "apps/web/a": "old a\n",
                        "apps/web/b": "old b\n"})
        self.write(f"{PARTNER}/addons/ledger/.env", "PARTNER_LEDGER_CURRENCY=EUR\n")
        self.write(f"{PARTNER}/addons/ledger/.state/created.json", "new\n")
        self.write(f"{PARTNER}/addons/ledger/apps/web/a", "new a\n")
        done = renames.follow("partner", FOUND)
        kept = ".lab/migration/kept/partner/addons/books"
        self.assertEqual(self.text(f"{PARTNER}/addons/ledger/.env"), "PARTNER_LEDGER_CURRENCY=EUR\n")
        self.assertEqual(self.text(f"{kept}/.env"), "BOOKS_CURRENCY=CHF\n")
        self.assertEqual((self.text(f"{PARTNER}/addons/ledger/.state/created.json"), self.text(f"{kept}/.state/created.json")),
                         ("new\n", "old\n"), "records are never mixed: the old ones are kept aside whole")
        self.assertEqual((self.text(f"{PARTNER}/addons/ledger/apps/web/a"), self.text(f"{PARTNER}/addons/ledger/apps/web/b"),
                          self.text(f"{kept}/apps/web/a")), ("new a\n", "old b\n", "old a\n"), "two folders are merged")
        self.assertEqual(sorted(done[0]["kept"]), [f"{kept}/.env", f"{kept}/.state", f"{kept}/apps/web/a"])
        self.assertFalse(self.old.exists())

    def test_the_on_file_turns_on_the_new_file_and_a_copy_for_a_platform_is_written_for_it(self):
        old_copy = compose.target_copy(OLD_FILE, "cloud")
        self.write(old_copy, f"{compose.HEADER}{OLD_FILE} (partner-ledger=gw)\nservices: {{}}\n")
        self.write(paths.ON_FILE, f"include:\n  - {OLD_FILE}   # mine\n"
                                  f'  - {{path: "{old_copy}", project_directory: "{PARTNER}/addons/books"}}\n'
                                  "  - .lab/catalogs/fixture/addons/echo/compose.yml\n")
        renames.follow("partner", FOUND)
        new_copy = compose.target_copy(NEW_FILE, "cloud")
        self.assertEqual(self.text(paths.ON_FILE), f"include:\n  - {NEW_FILE}   # mine\n"
                                                   f'  - {{path: "{new_copy}", project_directory: "{PARTNER}/addons/ledger"}}\n'
                                                   "  - .lab/catalogs/fixture/addons/echo/compose.yml\n")
        self.assertEqual(compose.target_names(new_copy), {"partner-ledger": "gw"}, "the copy is made as the old one was")
        self.assertFalse((self.root / old_copy).exists(), "the old copy goes once nothing names it")

    def test_the_items_of_this_machine_and_the_imported_playgrounds_include_the_new_place_and_copies_say_its_new_id(self):
        self.write(".lab/catalogs/local/addons/mine/compose.yml",
                   "x-gravitee: {kind: addon, name: Mine}\ninclude:\n  - ../../../partner/addons/books/compose.yml\n")
        self.write(".lab/catalogs/local/addons/books/compose.yml",
                   'x-gravitee: {copy_of: "@partner/books", kind: addon, name: Books}\nservices: {}\n')
        imports = self.root / paths.IMPORTS_DIR / "shop"
        (imports / "playgrounds/shop").mkdir(parents=True)
        (imports / "playgrounds/shop/compose.yml").write_text(
            "include:\n  - ../../../partner/addons/books/compose.yml\n", encoding="utf-8")
        os.symlink(imports, self.root / ".lab/catalogs/shop")
        listed = registry.entries
        self.patch(registry, "entries", lambda: [*listed(), {"id": "shop", "kind": "folder", "path": str(imports)}])
        self.patch(registry, "imported", lambda ns: ns == "shop")
        renames.follow("partner", FOUND)
        self.assertEqual(self.text(".lab/catalogs/local/addons/mine/compose.yml"),
                         "x-gravitee: {kind: addon, name: Mine}\ninclude:\n  - ../../../partner/addons/ledger/compose.yml\n")
        self.assertEqual(self.text(".lab/catalogs/local/addons/books/compose.yml"),
                         'x-gravitee: {copy_of: "@partner/ledger", kind: addon, name: Books}\nservices: {}\n')
        self.assertEqual((imports / "playgrounds/shop/compose.yml").read_text(encoding="utf-8"),
                         "include:\n  - ../../../partner/addons/ledger/compose.yml\n")

    def test_its_star_its_tested_sets_what_a_share_renamed_and_its_held_settings_follow_it(self):
        self.write(starred.FILE, json.dumps({"addon": ["@partner/books", "echo"]}))
        self.write(renames.SHARED, json.dumps({"addon:@partner/books": {"MINE_X": "PARTNER_X"}}))
        self.write(".lab/held/addons/books/notes.txt", "kept\n")
        self.write(".lab/held/addons/books/.env", "BOOKS_CURRENCY=CHF\n")
        renames.follow("partner", FOUND, moved_from=["addon:ledger"])
        self.assertEqual(starred.read()["addon"], {"@partner/ledger", "echo"})
        self.assertEqual(json.loads(self.text(renames.SHARED)), {"addon:@partner/ledger": {"MINE_X": "PARTNER_X"}})
        self.assertEqual(self.text(".lab/held/addons/ledger/notes.txt"), "kept\n",
                         "what waits for the item waits under its new name, for the adoption that follows")
        self.assertEqual(self.text(".lab/held/addons/ledger/.env"), "PARTNER_LEDGER_CURRENCY=CHF\n",
                         "the settings that wait take their new names before the item adopts them")
        self.assertFalse((self.root / ".lab/held/addons/books").exists())
        self.patch(registry, "units_of", lambda ns: {"addon": ["ledger"]})
        self.assertEqual([one["unit"] for one in install.adopt("partner", ["addon:ledger"])], ["addon:ledger"],
                         "the catalog that follows is the one that adopts")
        self.assertEqual(self.text(f"{PARTNER}/addons/ledger/.env"), "PARTNER_LEDGER_CURRENCY=CHF\n")

    def test_held_settings_whose_place_is_taken_are_kept_aside_and_nothing_stays_under_the_old_name(self):
        self.write(".lab/held/addons/books/.env", "BOOKS_CURRENCY=CHF\n")
        self.write(".lab/held/addons/ledger/.env", "PARTNER_LEDGER_CURRENCY=EUR\n")
        renames.follow("partner", FOUND, moved_from=["addon:ledger"])
        self.assertEqual(self.text(".lab/held/addons/ledger/.env"), "PARTNER_LEDGER_CURRENCY=EUR\n")
        self.assertEqual(self.text(".lab/migration/kept/partner/held/addons/books/.env"), "BOOKS_CURRENCY=CHF\n")
        self.assertFalse((self.root / ".lab/held/addons/books").exists())

    def test_the_on_file_turns_on_what_a_list_on_one_line_names(self):
        self.write(paths.ON_FILE, f"include: [{OLD_FILE}]\n")
        self.assertEqual(install.on_units("partner"), {"addon:books"})

    def test_held_settings_that_cannot_be_moved_stop_the_rename_as_its_own_files_do(self):
        self.write(".lab/held/addons/books/.env", "BOOKS_CURRENCY=CHF\n")
        real = moves.relocate
        self.patch(moves, "relocate", lambda src, dst, why: "copied" if why == "rename-held" else real(src, dst, why))
        with self.assertRaises(registry.CatalogError) as raised:
            renames.follow("partner", FOUND, moved_from=["addon:ledger"])
        self.assertEqual((raised.exception.code, raised.exception.detail["units"]), ("rename_stuck", ["addon:ledger"]))
        self.assertTrue((self.root / ".lab/held/addons/books/.env").is_file(), "the value is where it was")

    def test_the_release_before_a_rename_is_the_greatest_version_tag_that_leads_to_it(self):
        repo = self.root / PARTNER   # a working copy: its git is the history
        a, b = "services:\n  partner-a:\n    image: busybox\n", "services:\n  partner-b:\n    image: busybox\n"

        def run(*args):
            return gitfixture.git("-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.invalid", *args)

        def history(tags):
            for old in run("tag", "--list").split():
                run("tag", "-d", old)
            for name, at, annotated in tags:
                run("tag", *(["-a", "-m", name] if annotated else []), name, at)
            return check._history("partner")("addons/a/compose.yml", "addons/b/compose.yml")
        run("init", "-q", "-b", "main")
        self.write(f"{PARTNER}/addons/a/compose.yml", a)
        run("add", "-A"), run("commit", "-qm", "a")
        first = run("rev-parse", "HEAD").strip()
        self.write(f"{PARTNER}/addons/b/compose.yml", b)
        run("add", "-A"), run("commit", "-qm", "b, added before the rename")
        second = run("rev-parse", "HEAD").strip()
        (repo / "addons/a/compose.yml").unlink()
        run("add", "-A"), run("commit", "-qm", "a renamed to b")
        third = run("rev-parse", "HEAD").strip()
        self.assertEqual(history([("v1.0.0", first, True), ("v1.1.0-rc1", second, False)]), (a, None, b),
                         "an annotated release is read through to its commit, and a pre-release is no release")
        self.assertEqual(history([("v1.0.0", first, False), ("v1.1.0", second, True)]), (a, b, b),
                         "the greatest version that leads there: b was an item already")
        self.assertEqual(history([("v2.0.0", third, True)]), (a, None, b), "releases, none before: nothing was there yet")
        self.assertEqual(history([]), (a, b, b), "no release at all: the commit before is what a branch had")

    def test_an_installed_catalog_reads_its_history_from_the_store_and_never_from_the_git_of_the_lab_it_lies_in(self):
        def run(*args):
            return gitfixture.git("-C", str(self.root), "-c", "user.name=T", "-c", "user.email=t@example.invalid", *args)
        run("init", "-q", "-b", "main")   # the Lab is a clone itself
        self.write(f"{PARTNER}/addons/a/compose.yml", "services: {}\n")
        run("add", "-f", f"{PARTNER}/addons/a/compose.yml"), run("commit", "-qm", "a")
        run("rm", "-q", f"{PARTNER}/addons/a/compose.yml"), run("commit", "-qm", "a goes")
        self.patch(registry, "store_dir", lambda ns: self.root / "no-store")
        self.assertIsNone(check._history("partner")("addons/a/compose.yml"), "the Lab's own history is not the catalog's")

    def test_a_rename_asks_docker_only_for_an_add_on_or_a_playground_whose_folder_is_there(self):
        def asked(*args):
            raise AssertionError("Docker was asked")
        self.patch(install, "CONTAINERS", asked)
        for items in ({"setup:tour": "setup:city-tour"}, {"addon:gone": "addon:ledger"}):
            with self.subTest(items=items):
                self.patch(install, "_moving", lambda *args, items=items: ({"items": items, "settings": {}}, "c" * 40))
                self.assertEqual(install.movable("partner", "a" * 40, "b" * 40, "update")["items"], items)

    def test_a_held_folder_is_left_for_a_catalog_that_does_not_adopt_the_item(self):
        self.write(".lab/held/addons/books/notes.txt", "kept\n")
        renames.follow("partner", FOUND)
        renames.follow("partner", FOUND, moved_from=["addon:books"])
        self.assertTrue((self.root / ".lab/held/addons/books/notes.txt").is_file(),
                        "a moved_from that names only the old name adopts nothing under the new one: install.adopt agrees")
        self.assertFalse(renames.adopts("partner", "addon:ledger", ["addon:books"]))

    def test_the_settings_are_renamed_in_every_item_of_the_catalog(self):
        self.write(f"{PARTNER}/addons/ledger/.env", "BOOKS_CURRENCY=USD\n")
        renames.follow("partner", {"items": {}, "settings": FOUND["settings"]})
        self.assertEqual(self.text(f"{PARTNER}/addons/ledger/.env"), "PARTNER_LEDGER_CURRENCY=USD\n",
                         "a setting renamed without its item being renamed is renamed too")

    def test_the_copy_for_a_platform_of_an_item_of_this_machine_that_includes_what_moved_is_written_again(self):
        mine = ".lab/catalogs/local/playgrounds/mine/compose.yml"
        self.write(mine, "include:\n  - ../../../partner/addons/books/compose.yml\nservices:\n  mine:\n    image: alpine\n")
        copy = compose.write_target(mine, "cloud", {})
        self.write(paths.ON_FILE, f'include:\n  - {{path: "{copy}", project_directory: ".lab/catalogs/local/playgrounds/mine"}}\n')
        self.write(OLD_FILE, "services: {}\n")
        known = {"addons": {"@partner/books": {"name": "Books"}}, "playgrounds": {"local/mine": {"name": "Mine"}}}
        self.assertEqual(layers._copy_problems(known), [])
        (self.root / OLD_FILE).unlink()
        self.assertEqual(layers._copy_problems(known), [{"file": mine, "message": "what Mine runs on its platform includes Books, "
                                                                                   "which is not there any more: turn Mine off and "
                                                                                   "on again"}],
                         "a copy that would keep Compose from reading the project is said by the names of the items")
        renames.follow("partner", FOUND)
        self.assertIn("../../../partner/addons/ledger/compose.yml", self.text(copy))
        self.assertEqual(layers._copy_problems(known), [])

    def test_a_path_to_what_moved_on_a_line_the_lab_does_not_write_again_is_said_until_it_is_changed(self):
        mine = ".lab/catalogs/local/playgrounds/mine/compose.yml"
        self.write(mine, "services:\n  mine:\n    image: alpine\n    volumes: [../../../partner/addons/books/data:/data:ro]\n"
                         "    # volumes: [../../../partner/addons/gone/data:/data:ro]\n")
        self.assertEqual(layers._dangling_problems(), [{"file": mine, "message": "names a folder that Partner fixture no longer has "
                                                                                  "(an item it renamed or removed): change it"}],
                         "a list on one line is the person's: the Lab says it, by the item, and a comment names nothing")
        renames.follow("partner", FOUND)
        self.assertEqual(len(layers._dangling_problems()), 1)
        self.write(mine, self.text(mine).replace("addons/books/", "addons/ledger/"))
        self.assertEqual(layers._dangling_problems(), [], "a file that changed is read again")

    def test_a_file_follows_when_no_path_to_the_old_place_is_left_once_repoint_wrote_it(self):
        moved = {OLD_FILE: NEW_FILE}
        mine = ".lab/catalogs/local/addons/mine/compose.yml"
        for text, follows in (("include:\n  - ../../../partner/addons/books/compose.yml\n", True),
                              ("include: [../../../partner/addons/books/compose.yml]\n", False),
                              ("include:\n  - path: ../../../partner/addons/books/compose.yml\n"
                               "    project_directory: ../../../partner/addons/books\n", False),
                              ("include:\n  - ../../../partner/addons/books/compose.yml   # ../../../partner/addons/books\n", True)):
            with self.subTest(text=text):
                self.write(mine, text)
                self.assertEqual(renames.follows(mine, moved), follows)
        self.write(".lab/catalogs/fixture/addons/other/compose.yml", "include:\n  - ../../../partner/addons/books/compose.yml\n")
        self.assertFalse(renames.follows(".lab/catalogs/fixture/addons/other/compose.yml", moved),
                         "a file of another catalog is its owners' to change")

    def test_following_twice_changes_nothing_more(self):
        self.machine(**{".env": "BOOKS_CURRENCY=CHF\n"})
        self.write(paths.ON_FILE, f"include:\n  - {OLD_FILE}\n")
        renames.follow("partner", FOUND)
        before = {path: path.read_bytes() for path in sorted(self.root.rglob("*")) if path.is_file()}
        self.assertEqual(renames.follow("partner", FOUND)[0]["moved"], [])
        self.assertEqual({path: path.read_bytes() for path in sorted(self.root.rglob("*")) if path.is_file()
                          if path.name != "journal.jsonl"},
                         {path: data for path, data in before.items() if path.name != "journal.jsonl"})

    def test_a_pair_whose_new_item_is_not_installed_or_whose_old_one_still_is_moves_nothing(self):
        self.machine(**{".env": "BOOKS_CURRENCY=CHF\n"})
        self.assertEqual(renames.follow("partner", {**FOUND, "items": {"addon:books": "addon:elsewhere"}}), [])
        self.assertEqual(renames.follow("partner", {**FOUND, "items": {"addon:ledger": "addon:books"}}), [],
                         "an old name that is an item of the install is that item's")
        self.assertTrue((self.old / ".env").is_file())

    def test_a_file_the_catalog_still_puts_in_the_old_folder_is_not_moved(self):
        self.machine(**{".env": "X=1\n"})
        self.patch(renames, "_content", lambda ns: {"addons/books", "addons/books/README.md"})
        self.write(f"{PARTNER}/addons/books/README.md", "The catalog's\n")
        renames.follow("partner", FOUND)
        self.assertTrue((self.old / "README.md").is_file())
        self.assertFalse((self.old / ".env").exists())

    def test_each_change_is_announced_to_the_hook_of_the_operation_first(self):
        self.machine(**{".env": "BOOKS_CURRENCY=CHF\n"})
        self.write(paths.ON_FILE, f"include:\n  - {OLD_FILE}\n")
        said = []
        renames.follow("partner", FOUND, step=said.append)
        self.assertEqual(said, ["rename:entry", "rename:on", "rename:settings"])


# ---------------------------------------------------------------------------------------------------------------------
# End to end: a scratch Lab, a catalog in a local repository, releases that rename an item
# ---------------------------------------------------------------------------------------------------------------------

# The level `renamed` came at: what the line for older Gravitee Labs says of a catalog that uses it
RENAMED = understood.parse((fixtures.CORE.parent / "contract.lock").read_text(encoding="utf-8")).rows[
    ("catalog.key", "x-gravitee-catalog.renamed")]


def catalog_yml(version, lab=1, renamed=None):
    lines = ["x-gravitee-catalog:", "  schema: 1", "  id: acme", "  name: Acme", "  description: Units that get renamed",
             f"  version: {version}", f"  lab: {lab}", "  license: AGPL-3.0-only"]
    if renamed:
        lines.append("  renamed:")
        for part, pairs in renamed.items():
            lines.append(f"    {part}:")
            lines += [f"      {old}: {new}" for old, new in pairs.items()]
    return "\n".join(lines) + "\n"


def addon(name, service, setting):
    """An add-on whose service reads `setting` and mounts a file of its own folder, so that Compose makes it from another
    configuration once its folder is another one."""
    return (f"x-gravitee: {{kind: addon, name: {name}, category: Other, description: Browser of what the agents send}}\n"
            "services:\n"
            f"  {service}:\n"
            "    image: busybox:${ACME_BUSYBOX_VERSION:-1.36}   # Image version\n"
            f"    container_name: gio-{service}\n"
            "    restart: unless-stopped\n"
            '    command: ["httpd", "-f", "-p", "80"]\n'
            f"    environment: {{TOKEN: \"${{{setting}:-}}\"}}   # Token of the viewer\n"
            "    volumes: [./config.txt:/etc/viewer.txt:ro]\n"
            "    labels:\n"
            f"      io.gravitee.name: {name}\n"
            "      io.gravitee.description: Browser of what the agents send\n"
            "    networks: [internal]\n")


V1 = {"catalog.yml": catalog_yml("1.0.0"),
      "addons/viewer/compose.yml": addon("Viewer", "acme-viewer", "ACME_VIEWER_TOKEN"),
      "addons/viewer/config.txt": "port=80\n",
      "addons/board/compose.yml": addon("Board", "acme-board", "ACME_BOARD_TOKEN"),
      "addons/board/config.txt": "port=80\n"}
FIRST = {"items": {"addon:viewer": "addon:trace-viewer", "addon:board": "addon:cluster-board"},
         "settings": {"ACME_VIEWER_TOKEN": "ACME_TRACE_VIEWER_TOKEN"}}
V2 = {"catalog.yml": catalog_yml("1.1.0", lab=RENAMED, renamed=FIRST),
      "addons/viewer/compose.yml": None, "addons/viewer/config.txt": None,
      "addons/board/compose.yml": None, "addons/board/config.txt": None,
      "addons/trace-viewer/compose.yml": addon("Trace viewer", "acme-viewer", "ACME_TRACE_VIEWER_TOKEN"),
      "addons/trace-viewer/config.txt": "port=80\n",
      "addons/cluster-board/compose.yml": addon("Cluster board", "acme-cluster-board", "ACME_BOARD_TOKEN"),
      "addons/cluster-board/config.txt": "port=80\n"}
SECOND = {"items": {**FIRST["items"], "addon:trace-viewer": "addon:agent-trace-viewer"},
          "settings": {**FIRST["settings"], "ACME_TRACE_VIEWER_TOKEN": "ACME_AGENT_TRACE_VIEWER_TOKEN"}}
V3 = {"catalog.yml": catalog_yml("1.2.0", lab=RENAMED, renamed=SECOND),
      "addons/trace-viewer/compose.yml": None, "addons/trace-viewer/config.txt": None,
      "addons/agent-trace-viewer/compose.yml": addon("Agent trace viewer", "acme-viewer", "ACME_AGENT_TRACE_VIEWER_TOKEN"),
      "addons/agent-trace-viewer/config.txt": "port=80\n"}


class EndToEnd(Case):
    """A Lab in a temporary folder with the real core, Compose 5 reading the files (a visible skip without it, as without git),
    and the catalog `acme` served by a bare repository on this machine that releases 1.0.0, then 1.1.0 and 1.2.0."""

    def setUp(self):
        gitfixture.require()
        fixtures.need_compose()
        self.start(mock.patch.dict(os.environ, {"DOCKER_CONFIG": os.path.join(os.path.expanduser("~"), ".docker")}))
        fixtures.project(self, paths, project, compose, envfiles, ns="fixture")
        self.patch(paths, "_default", (None, None))
        self.home = gitfixture.isolate(self)
        self.patch(gitx, "_ending", False)
        self.patch(license, "status_of", lambda variable: None)
        self.patch(jobs, "JOBS", jobs.Jobs())
        self.patch(service, "TICKETS", {})
        self.patch(service, "_checking", {})
        self.patch(service, "_started", {})
        self.patch(service, "_known", {"at": None, "version": None})
        self.patch(service, "_running", lambda found, ns: set())
        self.patch(compose, "image_ids", lambda: {})   # Docker is not asked: no daemon here
        self.containers = {}   # what Docker says runs, by service: a test sets it
        self.patch(compose, "ps", lambda variable=None: json.loads(json.dumps(self.containers)))
        project.forget()
        self.addCleanup(project.forget)
        self.repo = gitfixture.make_repo(self.home / "acme.git", V1)
        self.release("1.0.0")

    def release(self, version, files=None):
        if files:
            gitfixture.commit(self.repo, files, f"Release {version}")
        gitfixture.git("tag", f"v{version}", gitfixture.tip(self.repo), repo=self.repo)

    def add(self):
        service.add(service.check_address(gitfixture.url(self.repo))["ticket"], host=True)
        project.forget()

    def update(self, undo=False):
        service.check_now("acme")
        act = service.undo if undo else service.update
        plan = act("acme", check=True)
        act("acme", ticket=plan["ticket"])
        project.forget()
        return plan

    def addon_dir(self, name):
        return self.root / ".lab/catalogs/acme/addons" / name

    def person(self):
        """Viewer on, starred, with its token set and what its seed recorded; the board on too."""
        self.write(paths.ON_FILE, "include:\n  - .lab/catalogs/acme/addons/viewer/compose.yml\n"
                                  "  - .lab/catalogs/acme/addons/board/compose.yml\n")
        starred.star(project.load(), "addon", "@acme/viewer", True)
        self.write(".lab/catalogs/acme/addons/viewer/.env", "ACME_VIEWER_TOKEN=secret-value\n")
        self.write(".lab/catalogs/acme/addons/viewer/.state/created.json", '[{"name": "agent"}]\n')
        project.forget()

    def assert_kept_under(self, name, setting):
        base = self.root / ".lab/catalogs/acme/addons" / name
        self.assertEqual((base / ".env").read_text(encoding="utf-8"), f"{setting}=secret-value\n")
        self.assertEqual((base / ".state/created.json").read_text(encoding="utf-8"), '[{"name": "agent"}]\n')
        found = project.load()
        state = layers.current(found)
        self.assertIn(f"@acme/{name}", state["explicit"], "the item is still on, under its new id")
        self.assertEqual(state["problems"], [], "the on file names no file that is gone")
        self.assertIn(f"@acme/{name}", starred.read()["addon"])
        self.assertEqual(found["addons"][f"@acme/{name}"]["blocked"], None)

    def test_an_update_that_renames_an_item_waits_while_it_has_a_container_then_keeps_everything_under_the_new_id(self):
        self.add()
        self.person()
        self.containers = {"acme-viewer": {"state": "exited", "name": "gio-acme-viewer", "id": "a" * 64}}
        self.release("1.1.0", V2)
        service.check_now("acme")
        plan = service.update("acme", check=True)
        self.assertEqual(plan["held"], "Turn off Viewer first", "a container stopped is a container: nothing moves under it")
        with self.assertRaises(registry.CatalogError) as raised:
            service.update("acme", ticket=plan["ticket"])
        self.assertEqual(raised.exception.code, "held")
        self.assertTrue((self.addon_dir("viewer") / ".env").is_file())
        self.containers = {}   # turned off: its containers are gone
        plan = service.update("acme", check=True)
        self.assertEqual(plan["held"], None, "an item that is on and has no container does not hold back the update")
        self.assertEqual((plan["adds"], plan["removes"], plan["breaks"]), ([], [], []))
        self.assertEqual(plan["renames"], [
            {"kind": "addon", "id": "@acme/cluster-board", "name": "Cluster board", "was": "Board", "on": True},
            {"kind": "addon", "id": "@acme/trace-viewer", "name": "Trace viewer", "was": "Viewer", "on": True}])
        self.containers = {"acme-board": {"state": "running", "name": "gio-acme-board", "id": "b" * 64}}
        with self.assertRaises(registry.CatalogError) as raised:
            service.update("acme", ticket=plan["ticket"])
        self.assertEqual(raised.exception.code, "held", "a container made since the plan holds the update too")
        self.containers = {}
        service.update("acme", ticket=plan["ticket"])
        project.forget()
        self.assert_kept_under("trace-viewer", "ACME_TRACE_VIEWER_TOKEN")
        self.assertFalse(self.addon_dir("viewer").exists(), "nothing is left under the old name")
        said = [line for _, line in check.catalog_problems("acme") if line.startswith(("renamed", "lab is"))]
        self.assertEqual(said, [], "the release passes the check: its renames are true and lab 3 is its level")

    def test_a_rename_chain_followed_release_by_release_and_one_skipped_end_at_the_same_place(self):
        self.add()
        self.person()
        self.release("1.1.0", V2)
        self.update()
        self.release("1.2.0", V3)
        self.update()
        self.assert_kept_under("agent-trace-viewer", "ACME_AGENT_TRACE_VIEWER_TOKEN")
        chained = self.root

        self.setUp_again()
        self.add_at("1.0.0")
        self.person()
        plan = self.update()
        self.assertEqual(plan["version"], "1.2.0", "the greatest release, two renames at once")
        self.assertEqual([row["id"] for row in plan["renames"]], ["@acme/cluster-board", "@acme/agent-trace-viewer"])
        self.assert_kept_under("agent-trace-viewer", "ACME_AGENT_TRACE_VIEWER_TOKEN")
        self.assertNotEqual(chained, self.root)

    def setUp_again(self):
        """Another Lab, in another folder, that has 1.0.0 of the same repository."""
        fixtures.project(self, paths, project, compose, envfiles, ns="fixture")
        project.forget()

    def add_at(self, version):
        """The catalog added while the repository's greatest release is `version`: the later tags are moved aside meanwhile."""
        later = [tag for tag in gitfixture.git("tag", "--list", "v*", repo=self.repo).split() if tag != f"v{version}"]
        commits = {tag: gitfixture.git("rev-list", "-n", "1", tag, repo=self.repo).strip() for tag in later}
        for tag in later:
            gitfixture.git("tag", "-d", tag, repo=self.repo)
        self.add()
        for tag, commit in commits.items():
            gitfixture.git("tag", tag, commit, repo=self.repo)

    def test_going_back_puts_everything_back_under_the_old_name(self):
        self.add()
        self.person()
        self.release("1.1.0", V2)
        self.update()
        plan = self.update(undo=True)
        self.assertEqual([(row["id"], row["was"], row["on"]) for row in plan["renames"]],
                         [("@acme/board", "Cluster board", True), ("@acme/viewer", "Trace viewer", True)],
                         "in the order of the names they leave")
        self.assert_kept_under("viewer", "ACME_VIEWER_TOKEN")


    def test_a_lab_released_before_renamed_keeps_the_newest_version_it_reads_and_says_it_needs_a_newer_gravitee_lab(self):
        self.add()
        self.person()
        # 1.1.0 as a catalog writes it now, with no line for older Labs: what the Lab reads is what it understands
        self.release("1.1.0", {**V2, "catalog.yml": "".join(line for line in V2["catalog.yml"].splitlines(True)
                                                             if line.strip().split(":")[0] not in ("schema", "lab"))})
        self.patch(manifest, "KEYS", tuple(key for key in manifest.KEYS if key != "renamed"))   # a Lab of before
        row = service.check_now("acme")["catalog"]
        self.assertEqual((row["state"]["key"], row["state"]["label"]), ("lab_new", "Needs a newer Gravitee Lab"))
        self.assertEqual(service.update("acme", check=True), {"upToDate": True}, "the version it has is the newest it reads")
        self.assertTrue((self.addon_dir("viewer") / ".env").is_file(), "nothing of the person's moved")

    def test_a_personal_copy_that_runs_under_the_names_of_the_original_is_untouched_by_a_rename_of_the_original(self):
        """The person's copy of the board keeps its service name, acme-board, and runs; a release renames the original's
        service to acme-cluster-board. The copy's container is the copy's: it holds nothing back, stops as it did, and turning
        the catalog's item on removes nothing of it."""
        self.add()
        base, copy = self.addon_dir("board"), self.root / ".lab/catalogs/local/addons/board"
        copy.mkdir(parents=True)
        (copy / "config.txt").write_text("port=80\n", encoding="utf-8")
        (copy / "compose.yml").write_text((base / "compose.yml").read_text(encoding="utf-8").replace(
            "x-gravitee: {kind: addon,", 'x-gravitee: {copy_of: "@acme/board", kind: addon,'), encoding="utf-8")
        self.write(paths.ON_FILE, "include:\n  - .lab/catalogs/local/addons/board/compose.yml\n")
        self.containers = {"acme-board": {"state": "running", "name": "gio-acme-board", "id": "c" * 64}}
        project.forget()
        self.release("1.1.0", V2)
        plan = self.update()
        self.assertEqual(plan["held"], None, "the container is the copy's, which is on")
        self.assertEqual([(row["id"], row["on"]) for row in plan["renames"]],
                         [("@acme/cluster-board", False), ("@acme/trace-viewer", False)], "the catalog's own board is off")
        found = project.load()
        self.assertIn('copy_of: "@acme/cluster-board"', (copy / "compose.yml").read_text(encoding="utf-8"),
                      "the copy says whose copy it is by the new id")
        self.patch(stack, "JOBS", mock.Mock())
        stack.unit_action(found, "addon", "local/board", "stop")
        self.assertEqual(self.started(stack.JOBS)[1], [[*compose.by_name(), "stop", "acme-board"]])
        start = {"services": {"remove": [], "start": ["acme-cluster-board"]}}
        self.assertEqual(layers.commands(start), [[*compose.COMPOSE, "up", "-d", "--no-deps", "acme-cluster-board"]],
                         "turning the catalog's item on removes nothing of the copy")
        self.assertEqual(layers.current(found)["problems"], [])

    def test_a_renamed_item_that_is_on_whose_new_version_cannot_run_holds_the_update(self):
        self.add()
        self.person()
        blocked = {**V2, "addons/trace-viewer/compose.yml": V2["addons/trace-viewer/compose.yml"].replace(
            "    networks: [internal]\n", '    networks: [internal]\n    ports: ["8099:80"]   # HTTP\n')}
        self.release("1.1.0", blocked)
        service.check_now("acme")
        plan = service.update("acme", check=True)
        self.assertEqual(plan["held"], "Turn off Viewer first", "as for any item that is on and would come back blocked")
        with self.assertRaises(registry.CatalogError) as raised:
            service.update("acme", ticket=plan["ticket"])
        self.assertEqual(raised.exception.code, "held")
        self.assertTrue((self.addon_dir("viewer") / ".env").is_file())

    def test_a_rename_waits_while_docker_cannot_say_what_has_containers(self):
        self.add()
        self.person()
        self.release("1.1.0", V2)
        service.check_now("acme")
        for error in (compose.ComposeError("Cannot connect to the Docker daemon"), ValueError("Expecting value")):
            with self.subTest(error=error):
                def down(variable=None):
                    raise error
                self.patch(compose, "ps", down)
                plan = service.update("acme", check=True)
                self.assertEqual(plan["held"], "Start Docker first")
                with self.assertRaises(registry.CatalogError) as raised:
                    service.update("acme", ticket=plan["ticket"])
                self.assertEqual(raised.exception.code, "docker_unknown")
                self.assertTrue((self.addon_dir("viewer") / ".env").is_file())

    def test_a_repair_after_a_rename_that_is_done_does_not_ask_docker(self):
        self.add()
        self.person()
        self.release("1.1.0", V2)
        self.write(paths.ON_FILE, "")
        project.forget()
        self.update()

        def down(variable=None):
            raise compose.ComposeError("Cannot connect to the Docker daemon")
        self.patch(compose, "ps", down)
        service.repair("acme")   # nothing is left under the old names: there is nothing to move under a container
        self.assertIsNone(registry.lock_of("acme")["error"])

    def test_an_update_that_stopped_and_meets_a_container_is_damaged_with_the_hold_said_and_repair_finishes_it(self):
        self.add()
        self.person()
        self.release("1.1.0", V2)
        self.write(paths.ON_FILE, "")
        project.forget()
        service.check_now("acme")
        plan = service.update("acme", check=True)
        follow = install._follow

        def killed(*args, **kwargs):   # the update stops there, and its item is started meanwhile
            self.containers = {"acme-viewer": {"state": "running", "name": "gio-acme-viewer", "id": "a" * 64}}
            raise OSError("killed")
        self.patch(install, "_follow", killed)
        with self.assertRaises(OSError):
            service.update("acme", ticket=plan["ticket"])   # which finishes it at once, as a start would
        self.patch(install, "_follow", follow)
        project.forget()
        install.recover()
        held = registry.lock_of("acme")
        self.assertEqual((bool(held["pending"]), held["error"]), (True, "damaged"), "kept for Repair, which finishes it")
        state = service._row_of("acme")["state"]
        self.assertEqual((state["key"], state["reason"], state["next"]), ("damaged", "Stop Viewer first.", "Repair it from the menu."),
                         "the row says what holds it, by the item's name")
        self.assertTrue((self.addon_dir("viewer") / ".env").is_file())
        for asked in (lambda: service.update("acme", check=True), lambda: service.repair("acme")):
            with self.assertRaises(registry.CatalogError) as raised:
                asked()
            self.assertEqual(str(raised.exception), "Stop Viewer first.", "a plan and Repair say the hold alike")
        self.assertEqual(service.remove("acme", check=True)["held"], "Stop Viewer first", "and so does Remove")
        with self.assertRaises(registry.CatalogError) as raised:
            service.remove("acme")
        self.assertEqual(str(raised.exception), "Stop Viewer first.")
        self.containers = {}
        state = service._row_of("acme")["state"]
        self.assertEqual((state["key"], state["reason"]), ("damaged", "An update stopped half way."), "nothing holds it now")
        service.repair("acme")
        held = registry.lock_of("acme")
        self.assertEqual((held["pending"], held["error"], held["version"]), (None, None, "1.1.0"), "finished forward")
        self.assertEqual((self.addon_dir("trace-viewer") / ".env").read_text(encoding="utf-8"),
                         "ACME_TRACE_VIEWER_TOKEN=secret-value\n")

    def test_the_services_an_item_runs_itself_leave_out_the_core_s_and_those_of_what_it_includes(self):
        rest = ("services:\n  acme-ghost:\n    image: busybox\n  apim-gateway:\n    environment: {A: b}\n  acme-viewer:\n"
                "    environment: {B: c}\n")
        for include in ("include:\n  - ../viewer/compose.yml\n", "include: [../viewer/compose.yml]\n"):
            with self.subTest(include=include):
                files = {"addons/ghost/compose.yml": f"x-gravitee: {{kind: addon, name: Ghost}}\n{include}{rest}",
                         "addons/viewer/compose.yml": "services:\n  acme-viewer:\n    image: busybox\n"}
                self.patch(install.bare, "read",
                           lambda store, commit, path, limit=None, files=files: files[path].encode() if path in files else None)
                found = install._unit_at("acme", "addon:ghost", "a" * 40)
                self.assertEqual(found["services"], {"acme-ghost"}, "a running platform, or an add-on it includes, holds nothing")
                if include.startswith("include:\n"):
                    self.assertEqual(found["name"], "Ghost")

    def test_an_item_that_is_off_and_has_stopped_containers_says_to_remove_them(self):
        self.add()
        self.write(".lab/catalogs/acme/addons/viewer/.env", "ACME_VIEWER_TOKEN=secret-value\n")
        self.containers = {"acme-viewer": {"state": "exited", "name": "gio-acme-viewer", "id": "a" * 64}}
        self.release("1.1.0", V2)
        service.check_now("acme")
        self.assertEqual(service.update("acme", check=True)["held"], "Remove the containers of Viewer first")

    def test_a_plan_whose_renames_cannot_be_read_says_so_and_not_as_a_hold(self):
        self.add()
        self.release("1.1.0", V2)
        service.check_now("acme")

        def unread(*args):
            raise registry.CatalogError("renames_unread", renames.UNREAD, 503)
        self.patch(install, "movable", unread)
        with self.assertRaises(registry.CatalogError) as raised:
            service.update("acme", check=True)
        self.assertEqual((raised.exception.code, str(raised.exception)), ("renames_unread", renames.UNREAD))

    def test_a_renamed_item_that_is_on_whose_new_version_lacks_a_catalog_holds_the_update(self):
        self.add()
        self.person()
        self.release("1.1.0", V2)
        service.check_now("acme")
        review = service._review

        def lacking(*args, **kwargs):
            judged, verdicts, mine = review(*args, **kwargs)
            judged["addon"]["@acme/trace-viewer"]["needs_catalogs"] = [{"id": "hub", "name": "Hub", "reason": "failed"}]
            return judged, verdicts, mine
        self.patch(service, "_review", lacking)
        self.assertEqual(service.update("acme", check=True)["held"], "Turn off Viewer first",
                         "an item that needs a catalog this machine lacks cannot run either")

    def test_a_repair_after_a_rename_that_stopped_puts_back_what_it_had_moved(self):
        self.add()
        self.person()
        self.release("1.1.0", V2)
        real = moves.relocate
        self.patch(moves, "relocate", lambda src, dst, why: "stayed" if src.endswith("viewer/.state") else real(src, dst, why))
        service.check_now("acme")
        plan = service.update("acme", check=True)
        with self.assertRaises(registry.CatalogError) as raised:
            service.update("acme", ticket=plan["ticket"])
        self.assertEqual((raised.exception.code, str(raised.exception)), ("rename_stuck", renames.STUCK.format(name="Trace viewer")))
        self.assertTrue((self.addon_dir("trace-viewer") / ".env").is_file(), "the .env had moved before")
        install.recover()
        self.assertEqual(registry.lock_of("acme")["error"], "damaged", "what cannot be finished is for Repair")
        self.patch(moves, "relocate", real)
        self.containers = {"acme-viewer": {"state": "running", "name": "gio-acme-viewer", "id": "a" * 64}}   # started since
        with self.assertRaises(registry.CatalogError) as raised:
            service.repair("acme")
        self.assertEqual((raised.exception.code, str(raised.exception)), ("held", "Stop Trace viewer first."),
                         "nothing is moved back under a container, which runs: a damaged catalog shows no unit, its containers do")
        self.assertTrue((self.addon_dir("trace-viewer") / ".env").is_file())
        self.containers = {}
        service.repair("acme")
        project.forget()
        self.assert_kept_under("viewer", "ACME_VIEWER_TOKEN")
        self.assertFalse(self.addon_dir("trace-viewer").exists(), "nothing of the person's is left in the new folder")

    def test_an_update_whose_renames_cannot_be_read_now_changes_nothing_and_a_start_finishes_it_later(self):
        self.add()
        self.person()
        self.release("1.1.0", V2)
        service.check_now("acme")
        plan = service.update("acme", check=True)
        real, pin = manifest.read_catalog, registry.lock_of("acme")["pin"]

        def busy(path, *args, **kwargs):
            if str(path).startswith(str(compose.scratch())):
                raise registry.CatalogError("catalog_invalid", "catalog.yml: it could not be read.", 400)
            return real(path, *args, **kwargs)
        self.patch(manifest, "read_catalog", busy)
        renames._read.clear()
        with self.assertRaises(registry.CatalogError) as raised:
            service.update("acme", ticket=plan["ticket"])
        self.assertEqual(raised.exception.code, "renames_unread")
        held = registry.lock_of("acme")
        self.assertEqual((held["pin"], held["pending"], held["error"]), (pin, None, None), "nothing was changed")
        self.assertTrue((self.addon_dir("viewer") / ".env").is_file())
        # A stop between the files and the renames (the update under way) is finished by a start that can read them
        target = registry.lock_of("acme")["tip"]
        registry.set_lock("acme", pending={"op": "update", "from": pin, "to": target})
        install.recover()
        self.assertEqual((registry.lock_of("acme")["pending"] or {}).get("to"), target, "kept for a start that reads")
        self.assertIsNone(registry.lock_of("acme")["error"], "a moment's trouble marks nothing damaged")
        self.patch(manifest, "read_catalog", real)
        install.recover()
        self.assertEqual(registry.lock_of("acme")["pin"], target)
        self.assert_kept_under("trace-viewer", "ACME_TRACE_VIEWER_TOKEN")

    def test_check_reads_the_items_renamed_in_the_history_and_says_a_setting_left_behind(self):
        self.add()
        left = {**V2, "addons/trace-viewer/compose.yml": V2["addons/trace-viewer/compose.yml"].replace(
            "ACME_BUSYBOX_VERSION", "ACME_TRACE_VIEWER_VERSION")}
        self.release("1.1.0", left)
        self.update()
        history = check._history("acme")
        self.assertEqual(history("addons/viewer/compose.yml", "addons/trace-viewer/compose.yml"),
                         (V1["addons/viewer/compose.yml"], None, left["addons/trace-viewer/compose.yml"]),
                         "read from the Lab's store, across the commit that renamed it")
        self.assertIsNone(history("addons/never/compose.yml"))
        said = [line for _, line in check.catalog_problems("acme") if line.startswith("renamed")]
        self.assertEqual(said, ["renamed.settings: addon:viewer read ACME_BUSYBOX_VERSION, which addon:trace-viewer no longer "
                                "reads: map it to the name addon:trace-viewer reads, or the value people set is left behind"])

    def test_check_judges_a_rename_against_the_release_before_it_not_a_commit_between(self):
        """The new item is added in one commit and the old one goes, renamed, in the next: the release before has only the old
        one, so the rename is one and the check says nothing of an item that was there already."""
        self.add()
        gitfixture.commit(self.repo, {key: V2[key] for key in ("addons/trace-viewer/compose.yml", "addons/trace-viewer/config.txt",
                                                               "addons/cluster-board/compose.yml",
                                                               "addons/cluster-board/config.txt")}, "Add the new items")
        self.release("1.1.0", {key: V2[key] for key in ("catalog.yml", "addons/viewer/compose.yml", "addons/viewer/config.txt",
                                                        "addons/board/compose.yml", "addons/board/config.txt")})
        self.update()
        said = [line for _, line in check.catalog_problems("acme") if line.startswith("renamed")]
        self.assertEqual(said, [])

    def test_an_item_of_another_catalog_that_is_on_and_includes_a_renamed_item_holds_the_update(self):
        """The Lab writes again what includes a renamed item on this machine, never what another catalog holds: while such an
        item is on, the update waits, as it waits for an item removed."""
        self.add()
        files = {"catalog.yml": "x-gravitee-catalog:\n  schema: 1\n  id: hub\n  name: Hub\n  description: Uses Acme\n"
                                "  version: 1.0.0\n  lab: 1\n  license: AGPL-3.0-only\n  requires:\n    catalogs:\n"
                                f"      acme: {{name: Acme, url: \"{gitfixture.url(self.repo)}\", min: 1.0.0}}\n",
                 "playgrounds/inspect/compose.yml": "x-gravitee: {kind: playground, name: Inspect, category: Other, description: "
                                                    "Inspects what the agents send}\ninclude:\n  - ../../../acme/addons/viewer/compose.yml\n"}
        hub = gitfixture.make_repo(self.home / "hub.git", files)
        gitfixture.git("tag", "v1.0.0", gitfixture.tip(hub), repo=hub)
        service.add(service.check_address(gitfixture.url(hub))["ticket"], host=True)
        self.write(paths.ON_FILE, "include:\n  - .lab/catalogs/hub/playgrounds/inspect/compose.yml\n")
        project.forget()
        self.release("1.1.0", V2)
        service.check_now("acme")
        plan = service.update("acme", check=True)
        self.assertEqual(plan["held"], "Turn off Inspect first")
        self.assertEqual([one["id"] for one in plan["breaks"]], ["@hub/inspect"])
        with self.assertRaises(registry.CatalogError) as raised:
            service.update("acme", ticket=plan["ticket"])
        self.assertEqual((raised.exception.code, str(raised.exception)), ("held", "Turn off Inspect first."))
        # Turned off, then on again after the plan: the update asks what is on when it is applied
        self.write(paths.ON_FILE, "")
        project.forget()
        plan = service.update("acme", check=True)
        self.assertEqual(plan["held"], None)
        self.write(paths.ON_FILE, "include: [.lab/catalogs/hub/playgrounds/inspect/compose.yml]\n")
        project.forget()
        with self.assertRaises(registry.CatalogError) as raised:
            service.update("acme", ticket=plan["ticket"])
        self.assertEqual((raised.exception.code, str(raised.exception)), ("held", "Turn off Inspect first."))

    def test_an_item_of_this_machine_that_includes_a_renamed_item_is_judged_with_its_new_include(self):
        """Mine includes the viewer of Acme, which is not the default catalog, and is on: the new version is judged with Mine
        including the trace viewer, as it will once the update has written it again, so nothing holds it back."""
        self.add()
        mine = ".lab/catalogs/local/playgrounds/mine/compose.yml"
        self.write(mine, "x-gravitee: {kind: playground, name: Mine, category: Other, description: Mine}\n"
                         "include:\n  - ../../../acme/addons/viewer/compose.yml\n")
        self.write(paths.ON_FILE, f"include:\n  - {mine}\n")
        project.forget()
        self.release("1.1.0", V2)
        service.check_now("acme")
        plan = service.update("acme", check=True)
        self.assertEqual((plan["held"], plan["blocked"], plan["breaks"]), (None, [], []))
        service.update("acme", ticket=plan["ticket"])
        project.forget()
        self.assertIn("../../../acme/addons/trace-viewer/compose.yml", (self.root / mine).read_text(encoding="utf-8"))
        found = project.load()
        self.assertEqual((found["playgrounds"]["local/mine"]["blocked"], layers.current(found)["problems"]), (None, []))

    def test_an_item_of_this_machine_whose_include_the_lab_cannot_write_again_holds_the_update_while_it_is_on(self):
        """The same Mine, its include written in long form with a `project_directory:` that names the viewer's folder: the Lab
        writes the path again, not the folder, so Mine would not follow. It breaks, and holds the update while it is on."""
        self.add()
        mine = ".lab/catalogs/local/playgrounds/mine/compose.yml"
        self.write(mine, "x-gravitee: {kind: playground, name: Mine, category: Other, description: Mine}\n"
                         "include:\n  - path: ../../../acme/addons/viewer/compose.yml\n"
                         "    project_directory: ../../../acme/addons/viewer\n")
        self.write(paths.ON_FILE, f"include:\n  - {mine}\n")
        project.forget()
        self.release("1.1.0", V2)
        service.check_now("acme")
        plan = service.update("acme", check=True)
        self.assertEqual((plan["held"], [one["id"] for one in plan["breaks"]]), ("Turn off Mine first", ["local/mine"]))

    def test_an_item_of_this_machine_that_is_on_and_includes_a_renamed_item_on_one_line_holds_the_update(self):
        self.add()
        mine = ".lab/catalogs/local/playgrounds/mine/compose.yml"
        self.write(mine, "x-gravitee: {kind: playground, name: Mine, category: Other, description: Mine}\n"
                         "include: [../../../acme/addons/viewer/compose.yml]\n")
        self.write(paths.ON_FILE, f"include:\n  - {mine}\n")
        project.forget()
        self.release("1.1.0", V2)
        service.check_now("acme")
        plan = service.update("acme", check=True)
        self.assertEqual((plan["held"], [one["id"] for one in plan["breaks"]]), ("Turn off Mine first", ["local/mine"]),
                         "the Lab writes a list on one line again never: Mine would lose the viewer")

    def test_a_file_of_this_machine_that_names_a_service_no_file_defines_any_more_is_a_problem_compose_says(self):
        """The board's service is acme-cluster-board now: a playground of the person's that waits for acme-board does not load,
        and the Lab says why, in Compose's own words."""
        self.add()
        mine = ".lab/catalogs/local/playgrounds/mine/compose.yml"
        self.write(mine, "x-gravitee: {kind: playground, name: Mine, category: Other, description: Mine}\n"
                         "include:\n  - ../../../acme/addons/board/compose.yml\nservices:\n  mine-app:\n    image: alpine:3.20\n"
                         "    depends_on: [acme-board]\n")
        project.forget()
        self.assertEqual([one for one in project.load()["problems"] if "acme-board" in one["message"]], [])
        self.release("1.1.0", V2)
        self.update()
        said = [one for one in project.load()["problems"] if "acme-board" in one["message"]]
        self.assertEqual(len(said), 1, project.load()["problems"])
        self.assertIn("undefined service", said[0]["message"])
