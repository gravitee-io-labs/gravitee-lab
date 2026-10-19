"""lab-ui/catalog.py, the authors' and CI's command line: link and unlink (a folder, and a working copy that takes the
place of an installed catalog), check and its three formats, index, the first start from a terminal, suggest, reset, new,
and what the commands that wait for the service do while it is not there. Everything is on disk in a temporary project
folder; git (a visible skip without) for the working copy, Compose 5 (a visible skip without) for what reads a catalog.yml; no
network, no Docker daemon."""

import contextlib
import importlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.catalogs import cli as catalog
from lab_ui.library import check, project
from lab_ui.docker import compose, envfiles
from lab_ui.gravitee import license
from lab_ui import paths
from lab_ui.catalogs import defaults, gitx, manifest, registry
from lab_ui.catalogs import store as bare

from .. import Case, fixtures, gitfixture

REPO = fixtures.CORE.parent
COMPOSE = unittest.skipUnless(fixtures.have_compose(), "the Docker CLI with Compose 5 or later is not installed")
PARTNER = "partner"


def snapshot(root):
    """Everything under a folder, as it is: path, mode, and for a file its size and modification time, for a link where it
    leads. A folder has no size or time to compare (a file made in it changes them), and the lock file of the catalogs is the
    one thing a command that changes catalogs makes before it knows whether it will."""
    found = []
    for top, folders, names in os.walk(root):
        for name in sorted(names) + sorted(folders):
            where = os.path.join(top, name)
            if name == "catalogs.flock":
                continue
            info = os.lstat(where)
            kind = "link" if os.path.islink(where) else "folder" if os.path.isdir(where) else "file"
            found.append((os.path.relpath(where, root), oct(info.st_mode), kind,
                          os.readlink(where) if kind == "link" else None if kind == "folder" else (info.st_size, info.st_mtime_ns)))
    return sorted(found)


def contents(root):
    """The same folder by what is in it: path, mode and the bytes of each file (a file written again with the same bytes is
    the same file here)."""
    found = {}
    for top, folders, names in os.walk(root):
        for name in sorted(names) + sorted(folders):
            where = os.path.join(top, name)
            info = os.lstat(where)
            body = os.readlink(where) if os.path.islink(where) else None if os.path.isdir(where) else Path(where).read_bytes()
            found[os.path.relpath(where, root)] = (oct(info.st_mode), body)
    return found


def isolated(case):
    """gitfixture.isolate(case), a home folder with no git configuration, with Docker's configuration where it was: Compose is
    a plugin of the Docker CLI, found there. Answers the home folder."""
    docker = os.environ.get("DOCKER_CONFIG") or os.path.join(os.path.expanduser("~"), ".docker")
    home = gitfixture.isolate(case)
    os.environ["DOCKER_CONFIG"] = docker   # put back with the environment, when the test ends
    return home


class Tool(Case):
    """An engine with the default file naming `fixture` and nothing installed, the folders of an author under a home folder,
    and a way to run a command and read what it said."""

    def setUp(self):
        self.folder(check, paths, project, compose, envfiles)
        fixtures.core(self)
        fixtures.default(self, "fixture")
        shutil.copy(REPO / check.LOCK_FILE, self.root / check.LOCK_FILE)
        self.patch(license, "status_of", lambda variable: None)
        project.forget()
        home = Path(tempfile.mkdtemp(prefix="gravitee-lab-home-")).resolve()
        self.addCleanup(shutil.rmtree, home, True)
        self.home = home
        self.patch(gitx, "home", lambda: home)
        self.work = home / "work"
        self.work.mkdir()

    def run_tool(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = catalog.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def author(self, ns=PARTNER, name=None, files=None):
        """A working copy of a fixture catalog, in the author's folder: its path."""
        target = self.work / (name or ns)
        shutil.copytree(fixtures.CATALOGS / ns, target)
        for rel, text in (files or {}).items():
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            (target / rel).write_text(text, encoding="utf-8")
        return target

    def config(self):
        return registry.read_config()

    def link_of(self, ns):
        return Path(self.root) / paths.CATALOGS_DIR / ns


@COMPOSE
class Linking(Tool):

    def test_a_folder_is_linked_the_link_made_and_its_id_printed(self):
        folder = self.author()
        code, out, err = self.run_tool("link", str(folder))
        self.assertEqual((code, out, err), (0, "partner\n", ""))
        link = self.link_of(PARTNER)
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(folder))
        [entry] = self.config()["catalogs"]
        self.assertEqual((entry["id"], entry["kind"], entry["path"]), (PARTNER, "folder", str(folder)))
        self.assertNotIn("host", entry)
        self.assertEqual(registry.units_of(PARTNER)["addon"], ["ledger"])

    def test_it_never_reconciles_the_default_file(self):
        # In the CI of a catalog there is no first run: the listed catalog is not downloaded because another is linked
        self.assertIsNone(self.config())
        self.run_tool("link", str(self.author()))
        self.assertEqual([one["id"] for one in self.config()["catalogs"]], [PARTNER])
        self.assertEqual([row["id"] for row in defaults.not_added([one["id"] for one in registry.entries()])], ["fixture"])

    def test_linking_again_changes_nothing(self):
        folder = self.author()
        self.run_tool("link", str(folder))
        before = (snapshot(self.root), (self.root / ".lab/catalogs.json").read_bytes())
        for _ in range(2):
            self.assertEqual(self.run_tool("link", str(folder)), (0, "partner\n", ""))
        self.assertEqual((snapshot(self.root), (self.root / ".lab/catalogs.json").read_bytes()), before)

    def test_the_link_is_made_in_one_step_and_a_failure_leaves_nothing(self):
        folder = self.author()
        with mock.patch.object(os, "symlink", side_effect=OSError("not allowed")):
            code, out, err = self.run_tool("link", str(folder))
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, "The Lab cannot link folders on this system. Use WSL 2.\n")
        self.assertIsNone(self.config())
        self.assertEqual(sorted(p.name for p in (self.root / ".lab/catalogs").iterdir()), [])
        # The link is made under another name and renamed over the place: no one sees it half made
        with mock.patch.object(os, "replace", wraps=os.replace) as replaced:
            self.run_tool("link", str(folder))
        [(source, target)] = [call.args for call in replaced.call_args_list if str(call.args[1]).endswith("catalogs/partner")]
        self.assertTrue(Path(source).name.startswith(".link-partner-"))
        self.assertEqual(sorted(p.name for p in (self.root / ".lab/catalogs").iterdir()), ["partner"])

    def test_a_working_copy_of_a_listed_catalog_with_nothing_installed_is_a_plain_link_and_counts_as_listed(self):
        folder = self.author("fixture")
        code, out, err = self.run_tool("link", str(folder), "--allow-machine")
        self.assertEqual((code, out, err), (0, "fixture\n", ""))
        [entry] = self.config()["catalogs"]
        self.assertEqual((entry["kind"], entry["path"]), ("folder", str(folder)))
        self.assertNotIn("host", entry, "a listed catalog needs no allowance, and none is written")
        self.assertTrue(defaults.listed(entry))
        self.assertEqual(registry.inputs()["fixture"]["host"], "listed")

    def test_a_catalog_declared_and_never_downloaded_is_a_plain_link_the_author_s_loop_starts_here(self):
        # The first download failed, the repository is private and cannot be reached from here, or nothing started it: there is
        # no install for the working copy to stand in for
        folder = self.author()
        fixtures.declare(self, PARTNER, error="no_access", host=True)
        self.assertEqual(registry.inputs()[PARTNER]["state"], "noaccess")
        code, out, err = self.run_tool("link", str(folder))
        self.assertEqual((code, out, err), (0, "partner\n", ""))
        [entry] = self.config()["catalogs"]
        self.assertEqual((entry["id"], entry["kind"], entry["path"]), (PARTNER, "folder", str(folder)))
        self.assertNotIn("url", entry)
        self.assertIs(entry.get("host"), True, "the allowance it had is not lost")
        self.assertTrue(self.link_of(PARTNER).is_symlink())
        self.assertFalse(registry.lock_of(PARTNER).get("error"), "the failure of a download that is not the catalog's any more")
        self.assertEqual(registry.inputs()[PARTNER]["state"], "linked")
        self.assertEqual(self.run_tool("link", str(folder)), (0, "partner\n", ""), "and again")
        self.assertEqual(self.run_tool("unlink", PARTNER), (0, "Unlinked partner.\n", ""))
        self.assertFalse(os.path.lexists(self.link_of(PARTNER)))

    def test_a_catalog_declared_and_waiting_for_its_download_is_a_plain_link_too(self):
        folder = self.author()
        fixtures.declare(self, PARTNER)
        self.assertEqual(registry.inputs()[PARTNER]["state"], "downloading")
        self.assertEqual(self.run_tool("link", str(folder))[:2], (0, "partner\n"))
        self.assertEqual(registry.inputs()[PARTNER]["state"], "linked")

    def test_the_allowance_is_set_for_a_catalog_that_is_not_listed_and_is_kept_when_linking_again(self):
        folder = self.author()
        self.run_tool("link", str(folder), "--allow-machine")
        self.assertIs(self.config()["catalogs"][0]["host"], True)
        self.assertEqual(registry.inputs()[PARTNER]["host"], "allowed")
        self.run_tool("link", str(folder))
        self.assertIs(self.config()["catalogs"][0]["host"], True, "not taken back by linking again")

    def test_a_folder_that_is_not_a_catalog_is_refused_for_the_reason(self):
        empty = self.work / "empty"
        empty.mkdir()
        elsewhere = Path(tempfile.mkdtemp(prefix="gravitee-lab-outside-")).resolve()
        self.addCleanup(shutil.rmtree, elsewhere, True)
        (elsewhere / "catalog.yml").write_text("x-gravitee-catalog: {}\n", encoding="utf-8")
        for argument, code, line in (
                (str(self.work / "nowhere"), 2, f"No folder at {self.work / 'nowhere'}."),
                (str(empty), 2, f"{empty} has no catalog.yml, so it is not a catalog."),
                (str(elsewhere), 2, "Docker cannot share this folder. Move it under your home folder.")):
            with self.subTest(argument=argument):
                self.assertEqual(self.run_tool("link", argument), (code, "", line + "\n"))
        self.assertIsNone(self.config())

    def test_what_the_reading_of_catalog_yml_refuses_is_said_line_by_line(self):
        folder = self.author(files={"catalog.yml": "x-gravitee-catalog:\n  schema: 1\n  name: ''\n"})
        code, out, err = self.run_tool("link", str(folder))
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err.splitlines()[:2], ["catalog.yml: id is required.",
                                                "catalog.yml: name must be text: the name that is shown."])
        self.assertIsNone(self.config())
        self.assertFalse(os.path.lexists(self.link_of(PARTNER)))

    def test_what_a_catalog_yml_wrote_never_moves_the_cursor_of_the_terminal(self):
        # A key of its own table is echoed in the line that refuses it, and anyone can publish a catalog
        text = (fixtures.CATALOGS / PARTNER / "catalog.yml").read_text(encoding="utf-8")
        text += '  requires:\n    catalogs:\n      "x\\e[2J\\e]0;pwn\\a": {url: "https://example.invalid/x.git", min: 1.0.0}\n'
        code, out, err = self.run_tool("link", str(self.author(files={"catalog.yml": text})))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("requires.catalogs.x", err)
        self.assertFalse(any(char in err for char in "\x1b\x07\r"), repr(err))

    def test_an_include_in_catalog_yml_is_refused(self):
        text = (fixtures.CATALOGS / PARTNER / "catalog.yml").read_text(encoding="utf-8") + "include:\n  - ../other.yml\n"
        folder = self.author(files={"catalog.yml": text})
        code, out, err = self.run_tool("link", str(folder))
        self.assertEqual((code, out, err), (1, "", "catalog.yml: it may hold only the x-gravitee-catalog block.\n"))

    def test_a_namespace_that_is_reserved_is_refused_unless_the_engine_lists_it(self):
        text = (fixtures.CATALOGS / PARTNER / "catalog.yml").read_text(encoding="utf-8").replace("id: partner", "id: lab")
        folder = self.author(files={"catalog.yml": text})
        self.assertEqual(self.run_tool("link", str(folder)),
                         (1, "", "lab is a reserved name. Ask the owner of this catalog to choose another.\n"))
        self.assertFalse(os.path.lexists(self.link_of("lab")))
        # Listed, a name of the reserved words is the engine's own: the working copy of the default catalog may say it
        self.write("catalogs.default.json", json.dumps({"schema": 1, "catalogs": [{
            "id": "lab", "name": "Lab", "url": "https://example.invalid/lab.git", "branch": "main"}]}))
        self.assertEqual(self.run_tool("link", str(folder))[:2], (0, "lab\n"))

    def test_another_folder_takes_the_place_once_nothing_of_the_catalog_is_on(self):
        first, second = self.author(), self.author(name="second")
        self.run_tool("link", str(first))
        self.assertEqual(self.run_tool("link", str(second))[:2], (0, "partner\n"))
        self.assertEqual(os.readlink(self.link_of(PARTNER)), str(second))
        self.assertEqual(self.config()["catalogs"][0]["path"], str(second))
        self.assertTrue((first / "catalog.yml").is_file(), "the folder that was linked is never touched")
        self.write(".lab/on.yml", "include:\n  - .lab/catalogs/partner/addons/ledger/compose.yml\n")
        before = snapshot(self.root)
        self.assertEqual(self.run_tool("link", str(first)), (1, "", "Turn off ledger first.\n"))
        self.assertEqual(snapshot(self.root), before)

    def test_a_folder_that_stands_in_the_place_of_a_link_is_not_replaced(self):
        folder = self.author()
        (self.root / ".lab/catalogs/partner").mkdir(parents=True)
        code, out, err = self.run_tool("link", str(folder))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("Another catalog already uses the name partner.", err)
        self.assertFalse(self.link_of(PARTNER).is_symlink())

    def test_a_file_that_lists_the_catalogs_and_cannot_be_read_is_not_written_over(self):
        self.write(".lab/catalogs.json", "{ not json")
        before = snapshot(self.root)
        code, out, err = self.run_tool("link", str(self.author()))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("The file that lists your catalogs could not be read", err)
        self.assertEqual(snapshot(self.root), before)


@COMPOSE
class Unlinking(Tool):

    def test_the_link_goes_with_its_entry_and_the_folder_is_never_touched(self):
        folder = self.author()
        self.run_tool("link", str(folder))
        before = snapshot(folder)
        self.assertEqual(self.run_tool("unlink", PARTNER), (0, "Unlinked partner.\n", ""))
        self.assertFalse(os.path.lexists(self.link_of(PARTNER)))
        self.assertEqual(self.config()["catalogs"], [])
        self.assertEqual(snapshot(folder), before)

    def test_a_catalog_that_is_not_a_link_is_none_to_unlink(self):
        self.assertEqual(self.run_tool("unlink", PARTNER), (2, "", "catalog.py: partner is not linked to a folder\n"))

    def test_a_catalog_that_is_not_added_is_said_so_and_is_no_fault_of_the_tool(self):
        for command in ("update", "undo", "remove"):
            with self.subTest(command=command):
                code, out, err = self.run_tool(command, "nope")
                self.assertEqual((code, out), (2, ""))
                self.assertEqual(err, "catalog.py: No catalog nope\n")

    def test_it_is_held_while_an_item_of_the_catalog_is_on(self):
        self.run_tool("link", str(self.author()))
        self.write(".lab/on.yml", "include:\n  - .lab/catalogs/partner/addons/ledger/compose.yml\n")
        self.assertEqual(self.run_tool("unlink", PARTNER), (1, "", "Turn off ledger first.\n"))
        self.assertTrue(self.link_of(PARTNER).is_symlink())


@COMPOSE
class Shadowing(Tool):
    """The working copy of an installed catalog takes its place, and unlink gives it back."""

    def setUp(self):
        gitfixture.require()
        super().setUp()
        self.home = isolated(self)
        self.patch(gitx, "home", lambda: Path(os.path.realpath(self.home)))
        self.work = self.home / "work"
        self.work.mkdir()
        self.installed = fixtures.install(self, PARTNER, kind="git", store=True)
        project.forget()

    def test_the_working_copy_stands_in_the_place_of_the_install(self):
        folder = self.author()
        code, out, err = self.run_tool("link", str(folder))
        self.assertEqual((code, out, err), (0, "Linked partner. Edit it in place; stop with catalog.py unlink partner\n", ""))
        self.assertTrue(self.link_of(PARTNER).is_symlink())
        [entry] = self.config()["catalogs"]
        self.assertEqual((entry["kind"], entry["path"]), ("folder", str(folder)))
        self.assertEqual(entry["shadows"]["url"], self.installed.remote.url)
        self.assertIs(registry.lock_of(PARTNER).get("shadowed"), True)
        self.assertEqual(registry.inputs()[PARTNER]["state"], "linked")

    def test_the_machine_files_of_the_install_move_into_the_working_copy(self):
        base = self.link_of(PARTNER)
        (base / "addons/ledger/.env").write_text("SECRET=1\n", encoding="utf-8")
        folder = self.author()
        self.run_tool("link", str(folder))
        self.assertEqual((folder / "addons/ledger/.env").read_text(encoding="utf-8"), "SECRET=1\n")

    def test_it_is_held_while_an_item_of_the_installed_catalog_is_on(self):
        self.write(".lab/on.yml", "include:\n  - .lab/catalogs/partner/addons/ledger/compose.yml\n")
        before = snapshot(self.root)
        self.assertEqual(self.run_tool("link", str(self.author())), (1, "", "Turn off ledger first.\n"))
        self.assertEqual(snapshot(self.root), before)

    def test_a_working_copy_that_declares_another_id_is_another_catalog_and_the_install_stays(self):
        text = (fixtures.CATALOGS / PARTNER / "catalog.yml").read_text(encoding="utf-8").replace("id: partner", "id: other")
        folder = self.author(files={"catalog.yml": text})
        self.assertEqual(self.run_tool("link", str(folder))[:2], (0, "other\n"))
        self.assertFalse(registry.lock_of(PARTNER).get("shadowed"))
        self.assertFalse(self.link_of(PARTNER).is_symlink())
        self.assertEqual([one["id"] for one in self.config()["catalogs"]], [PARTNER, "other"])

    def test_unlink_gives_the_install_back_as_it_was(self):
        before = contents(self.link_of(PARTNER))
        folder = self.author()
        self.run_tool("link", str(folder))
        self.assertEqual(self.run_tool("unlink", PARTNER), (0, "Unlinked partner.\n", ""))
        self.assertFalse(self.link_of(PARTNER).is_symlink())
        self.assertEqual(contents(self.link_of(PARTNER)), before)
        [entry] = self.config()["catalogs"]
        self.assertEqual((entry["kind"], entry["url"]), ("git", self.installed.remote.url))
        self.assertNotIn("shadowed", registry.lock_of(PARTNER))

    def test_unlink_fetches_again_when_the_store_was_cleared_and_retries(self):
        self.run_tool("link", str(self.author()))
        shutil.rmtree(registry.store_dir(PARTNER))
        with mock.patch.object(catalog, "_fetch_again", wraps=catalog._fetch_again) as fetched:
            self.assertEqual(self.run_tool("unlink", PARTNER), (0, "Unlinked partner.\n", ""))
        fetched.assert_called_once()
        self.assertTrue(registry.store_dir(PARTNER).is_dir())
        self.assertFalse(self.link_of(PARTNER).is_symlink())
        self.assertEqual(registry.units_of(PARTNER)["addon"], ["ledger"])

    def test_unlink_waits_for_the_network_with_no_lock_held(self):
        # The fetch can last up to ten minutes: a Lab that is running would answer busy to every change meanwhile
        self.run_tool("link", str(self.author()))
        shutil.rmtree(registry.store_dir(PARTNER))
        seen, fetch = [], bare.fetch

        def watching(*args, **kwargs):
            held = dict(registry._held.__dict__.get("held", {}))
            outcome = []

            def other():   # a change made by another thread of the Lab, which tries once
                try:
                    with registry.flock(wait=0):
                        outcome.append("free")
                except registry.CatalogError as error:
                    outcome.append(error.code)
            thread = threading.Thread(target=other)
            thread.start()
            thread.join()
            seen.append((held, outcome))
            return fetch(*args, **kwargs)
        with mock.patch.object(bare, "fetch", watching):
            self.assertEqual(self.run_tool("unlink", PARTNER), (0, "Unlinked partner.\n", ""))
        self.assertEqual(seen, [({}, ["free"])])

    def test_unlink_says_so_without_sending_to_the_owners_when_the_version_is_gone_from_the_remote(self):
        self.run_tool("link", str(self.author()))
        shutil.rmtree(registry.store_dir(PARTNER))
        gitfixture.force_push_over(self.installed.remote.bare, {"catalog.yml": "x-gravitee-catalog: {id: partner}\n"})
        code, out, err = self.run_tool("unlink", PARTNER)
        self.assertEqual((code, out), (1, ""))
        pin = self.installed.commit[:7]
        self.assertEqual(err, f"{registry.display_address(self.installed.remote.url)} no longer has the version of partner that "
                              f"was installed ({pin}), so the link cannot be undone. Restore that commit there, then run "
                              "unlink again.\n")
        self.assertNotIn("owners", err)
        self.assertTrue(self.link_of(PARTNER).is_symlink(), "the link stays, for another try")


class Writing(Tool):
    """The commands that write a file of their own: the suggestions, and starting over."""

    def setUp(self):
        super().setUp()
        self.suggestions = self.home / "config" / "catalogs.json"
        self.patch(os, "environ", {**os.environ, "LAB_CATALOGS_FILE": str(self.suggestions)})

    def test_a_suggestion_is_created_with_one_entry_and_nothing_else_is_written(self):
        before = snapshot(self.root)
        code, out, err = self.run_tool("suggest", "git@git.example.invalid:team/lab-internal.git", "--name", "Example internal",
                                       "--description", "Demos kept private", "--allow-machine")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(self.suggestions.read_text(encoding="utf-8")), {"schema": 1, "catalogs": [{
            "name": "Example internal", "description": "Demos kept private",
            "url": "git@git.example.invalid:team/lab-internal.git", "host_access": True}]})
        self.assertEqual(snapshot(self.root), before, "the project is not touched")
        self.assertEqual(oct(self.suggestions.stat().st_mode & 0o777), "0o600")
        self.assertEqual(sorted(p.name for p in self.suggestions.parent.iterdir()), ["catalogs.json"])
        [row] = [row for row in registry.suggestions() if row["name"] == "Example internal"]
        self.assertTrue(row["host_access"])
        self.assertEqual(registry.read_config(), None, "a suggestion is no consent and no catalog")

    def test_a_suggestion_is_added_to_the_others_and_the_same_repository_again_replaces_its_entry(self):
        self.run_tool("suggest", "git@git.example.invalid:team/one.git", "--name", "One")
        self.run_tool("suggest", "https://git.example.invalid/team/two", "--name", "Two", "--description", "Second")
        self.run_tool("suggest", "git@git.example.invalid:team/one.git", "--name", "One again", "--allow-machine")
        found = json.loads(self.suggestions.read_text(encoding="utf-8"))["catalogs"]
        self.assertEqual([one["name"] for one in found], ["Two", "One again"])
        self.assertEqual(found[0], {"name": "Two", "description": "Second", "url": "https://git.example.invalid/team/two"})
        self.assertNotIn("description", found[1])

    def test_an_address_that_is_no_repository_and_a_file_that_is_not_a_list_are_refused_and_nothing_is_written(self):
        for argument in ("not an address", "ext::sh -c x", "https://user:token@git.example.invalid/a/b"):
            with self.subTest(argument=argument):
                code, out, err = self.run_tool("suggest", argument, "--name", "X")
                self.assertEqual(code, 2, err)
                self.assertFalse(self.suggestions.exists())
        self.assertEqual(self.run_tool("suggest", str(self.work), "--name", "X")[0], 2, "a folder is no suggestion")
        self.suggestions.parent.mkdir(parents=True)
        self.suggestions.write_text("{ not json", encoding="utf-8")
        code, out, err = self.run_tool("suggest", "git@git.example.invalid:team/one.git", "--name", "One")
        self.assertEqual(code, 2)
        self.assertIn("cannot be read as a list of suggestions", err)
        self.assertEqual(self.suggestions.read_text(encoding="utf-8"), "{ not json", "the person's file is not overwritten")

    def test_a_name_and_a_description_are_cleaned_and_cut_as_the_lab_shows_them(self):
        self.run_tool("suggest", "git@git.example.invalid:team/one.git", "--name", "  A\tname\n" + "x" * 80, "--description", "y" * 200)
        [one] = json.loads(self.suggestions.read_text(encoding="utf-8"))["catalogs"]
        self.assertEqual((len(one["name"]), len(one["description"])), (40, 90))
        self.assertTrue(one["name"].startswith("A name "))

    def test_a_name_that_is_nothing_once_cleaned_is_a_usage_error_and_nothing_is_written(self):
        for name in ("", "   ", "\t\n", "\x1b\x07"):
            with self.subTest(name=name):
                code, out, err = self.run_tool("suggest", "git@git.example.invalid:team/one.git", "--name", name)
                self.assertEqual((code, out), (2, ""))
                self.assertIn("--name must say what the catalog is called", err)
                self.assertFalse(self.suggestions.exists())

    def test_there_are_at_most_as_many_suggestions_as_the_lab_reads(self):
        self.suggestions.parent.mkdir(parents=True)
        entries = [{"name": f"N{i}", "url": f"https://git.example.invalid/team/r{i}"} for i in range(catalog.SUGGEST_LIMIT)]
        self.suggestions.write_text(json.dumps({"schema": 1, "catalogs": entries}), encoding="utf-8")
        code, out, err = self.run_tool("suggest", "https://git.example.invalid/team/new", "--name", "New")
        self.assertEqual(code, 2)
        self.assertIn("at most", err)


@COMPOSE
class Starting(Tool):
    """Reset: starts over, held while an item is on."""

    def setUp(self):
        super().setUp()
        self.run_tool("link", str(self.author()))
        self.run_tool("link", str(self.author("fixture")))
        self.write(".lab/catalogs/partner-note.txt", "kept")   # something that is not a catalog

    def test_the_next_start_is_a_first_run_when_the_file_that_lists_the_catalogs_is_gone(self):
        code, out, err = self.run_tool("reset")
        self.assertEqual((code, out, err), (0, "The next start is a first run.\n", ""))
        self.assertIsNone(self.config())
        self.assertEqual(registry.entries(), [])
        self.assertFalse(os.path.lexists(self.link_of(PARTNER)))
        self.assertFalse(os.path.lexists(self.link_of("fixture")))
        self.assertTrue((self.work / "partner/catalog.yml").is_file(), "the folders of the authors are never touched")
        self.assertEqual(self.run_tool("reset")[:2], (0, "The next start is a first run.\n"), "again, nothing to do")

    def test_with_empty_the_lab_adds_nothing_at_all(self):
        self.assertEqual(self.run_tool("reset", "--empty"), (0, "The catalogs are empty.\n", ""))
        self.assertEqual(self.config(), {"schema": 1, "catalogs": []})
        self.assertEqual(json.loads((self.root / ".lab/catalogs.json").read_text(encoding="utf-8")),
                         {"schema": 1, "catalogs": []})
        self.assertEqual(self.run_tool("reset", "--empty")[:2], (0, "The catalogs are empty.\n"))

    def test_it_is_refused_while_an_item_of_a_catalog_is_on_and_nothing_is_changed(self):
        self.write(".lab/on.yml", "include:\n  - .lab/catalogs/partner/addons/ledger/compose.yml\n")
        before = snapshot(self.root)
        for argv in (("reset",), ("reset", "--empty")):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_tool(*argv), (1, "", "Turn off ledger first.\n"))
                self.assertEqual(snapshot(self.root), before)

    def test_the_item_that_is_on_may_be_of_the_second_catalog_and_nothing_of_the_first_is_removed(self):
        self.write(".lab/on.yml", "include:\n  - .lab/catalogs/fixture/addons/echo/compose.yml\n")
        self.assertEqual([one["id"] for one in registry.entries()], [PARTNER, "fixture"])
        before = snapshot(self.root)
        for argv in (("reset",), ("reset", "--empty")):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_tool(*argv), (1, "", "Turn off echo first.\n"))
                self.assertEqual(snapshot(self.root), before, "refused before the first catalog is touched")
        self.assertTrue(self.link_of(PARTNER).is_symlink())

    def test_a_working_copy_that_stands_in_the_place_of_an_installed_catalog_is_unlinked_first(self):
        gitfixture.require()
        self.run_tool("reset")
        home = isolated(self)
        installed = fixtures.install(self, PARTNER, kind="git", store=True)
        self.patch(gitx, "home", lambda: Path(os.path.realpath(home)))
        folder = home / "work-partner"
        shutil.copytree(fixtures.CATALOGS / PARTNER, folder)
        self.assertEqual(self.run_tool("link", str(folder))[0], 0)
        before = snapshot(self.root)
        code, out, err = self.run_tool("reset")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, "partner is linked to a folder on this machine. Unlink it first, with catalog.py unlink partner.\n")
        self.assertEqual(snapshot(self.root), before)
        self.assertTrue(installed.commit)

    def test_a_file_that_lists_the_catalogs_and_cannot_be_read_is_none_of_its_business_to_remove(self):
        self.write(".lab/catalogs.json", "{ not json")
        before = snapshot(self.root)
        code, out, err = self.run_tool("reset")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("could not be read", err)
        self.assertEqual(snapshot(self.root), before)

    def test_the_personal_area_stays(self):
        self.write(".lab/catalogs/local/addons/mine/compose.yml", "services: {}\n")
        self.run_tool("reset")
        self.assertTrue((self.root / ".lab/catalogs/local/addons/mine/compose.yml").is_file())


class Reset(Tool):
    """Without a catalog on the machine: the first run and the opt-out before the first start."""

    def test_before_the_first_start_the_opt_out_writes_the_empty_list(self):
        self.assertIsNone(self.config())
        self.assertEqual(self.run_tool("reset", "--empty"), (0, "The catalogs are empty.\n", ""))
        self.assertEqual(self.config(), {"schema": 1, "catalogs": []})

    def test_a_git_catalog_goes_with_its_settings_kept(self):
        gitfixture.require()
        isolated(self)
        installed = fixtures.install(self, PARTNER, kind="git", store=True)
        (self.link_of(PARTNER) / "addons/ledger/.env").write_text("SECRET=1\n", encoding="utf-8")
        self.assertEqual(self.run_tool("reset")[0], 0)
        self.assertIsNone(self.config())
        self.assertEqual(registry.read_lock()["catalogs"], {})
        self.assertEqual((self.link_of(PARTNER) / "addons/ledger/.env").read_text(encoding="utf-8"), "SECRET=1\n")
        self.assertTrue((self.link_of(PARTNER) / ".kept").is_file(), "the marker that keeps the sweeper away")
        self.assertFalse((self.link_of(PARTNER) / "addons/ledger/compose.yml").exists(), "the content goes")
        self.assertTrue(installed.commit)


@COMPOSE
class Reading(Tool):
    """check and index, on a machine with the fixture catalogs: the exit code and what is said, in three formats."""

    HEADER = ("Reading the files only: there is no origin/main to read the commits from\n"
              "No .github/standard.json: judged on the families that need no GitHub (ST, SE, CR, DF, GD, IM)\n")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        fixtures.need_compose()
        holder = cls.holder = Case()
        cls.addClassCleanup(holder.doCleanups)
        fixtures.project(holder, paths, check, project, compose, envfiles)
        shutil.copy(REPO / check.LOCK_FILE, holder.root / check.LOCK_FILE)
        fixtures.install(holder, PARTNER, files={"README.md": "# Partner\n"})
        holder.patch(license, "status_of", lambda variable: None)
        project.forget()
        cls.shared = holder.root

    def setUp(self):
        self.root = self.shared   # the machine of the class: nothing here writes to it

    def test_a_catalog_that_is_fine_prints_all_good_and_exits_0(self):
        code, out, err = self.run_tool("check", PARTNER)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, self.HEADER + "✓ catalog.yml\n✓ addons/ledger/compose.yml\n\nAll good\n")

    def test_a_catalog_with_problems_says_them_file_by_file_and_exits_1(self):
        code, out, err = self.run_tool("check", "fixture")
        self.assertEqual((code, err), (1, ""))
        self.assertTrue(out.startswith(self.HEADER))
        self.assertIn("✗ addons/port-core/compose.yml\n    publishes port 8082, like docker-compose.yml\n", out)
        self.assertIn("✗ addons/future/compose.yml\n    T26 Needs Lab level 99, this Lab reads 1 to 1. Fix: Set `lab:` in `catalog.yml` to a "
                      "level this Lab reads (1 to 1), or move `lab.ref` in `.github/standard.json`.\n", out)
        self.assertIn("✓ addons/echo/compose.yml\n", out)
        self.assertIn("    T24 Depends on private content. Fix: A Public catalog cannot include a Private one", out)
        self.assertTrue(out.rstrip().splitlines()[-1].endswith(" for more."), out[-120:])
        self.assertIn(" Run make explain CODE=", out.rstrip().splitlines()[-1])

    def test_the_annotations_name_the_file_and_the_code_of_the_problem(self):
        code, out, err = self.run_tool("check", "fixture", "--format", "github")
        self.assertEqual((code, err), (1, ""))
        self.assertIn("::error file=addons/port-core/compose.yml::publishes port 8082, like docker-compose.yml\n", out)
        self.assertIn("::error file=addons/future/compose.yml,title=T26::Needs Lab level 99, this Lab reads 1 to 1. Fix: ", out)
        self.assertTrue(all(line.startswith(("::error file=", "::warning file=")) for line in out.splitlines()), out)
        self.assertEqual(self.run_tool("check", PARTNER, "--format", "github"), (0, "", ""))

    def test_the_json_is_a_list_of_the_problems(self):
        code, out, err = self.run_tool("check", "fixture", "--format", "json")
        self.assertEqual(code, 1)
        found = json.loads(out)
        self.assertIsInstance(found, list)
        self.assertIn({"code": "", "line": 0, "message": "publishes port 8082, like docker-compose.yml", "severity": "error",
                       "path": "addons/port-core/compose.yml"}, found)
        self.assertTrue(all(sorted(one) == ["code", "line", "message", "path", "severity"] for one in found))
        self.assertEqual(json.loads(self.run_tool("check", PARTNER, "--format", "json")[1]), [])

    def test_a_catalog_that_is_not_here_or_misses_one_it_includes_from_is_an_environment_error(self):
        code, out, err = self.run_tool("check", "ghost")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, "catalog.py: ghost is neither a folder nor a catalog installed or linked here\n")
        for fmt in ("text", "github", "json"):
            self.assertEqual(self.run_tool("check", "ghost", "--format", fmt)[0], 2)

    def test_checking_writes_nothing(self):
        before = snapshot(self.root)
        for argv in (("check", "fixture"), ("check", PARTNER, "--format", "json"), ("check", "fixture", "--format", "github"),
                     ("index", PARTNER)):
            self.run_tool(*argv)
        self.assertEqual(snapshot(self.root), before)

    def test_the_index_is_the_catalog_json_and_two_runs_are_the_same_bytes(self):
        first = self.run_tool("index", PARTNER)
        second = self.run_tool("index", PARTNER)
        self.assertEqual((first[0], first[2]), (0, ""))
        self.assertEqual(first, second)
        found = json.loads(first[1])
        self.assertEqual((found["schema"], found["catalog"]["id"]), (1, PARTNER))
        self.assertEqual([(one["kind"], one["id"]) for one in found["units"]], [("addon", "ledger")])
        self.assertEqual(first[1], manifest.render_index(manifest.build_index(PARTNER)))
        self.assertTrue(first[1].endswith("}\n"))

    def test_the_index_goes_to_the_file_asked_for_and_to_no_other(self):
        out = self.work_file()
        before = snapshot(self.root)
        self.assertEqual(self.run_tool("index", PARTNER, "--out", str(out)), (0, "", ""))
        self.assertEqual(out.read_text(encoding="utf-8"), self.run_tool("index", PARTNER)[1])
        self.assertEqual(snapshot(self.root), before)

    def work_file(self):
        folder = Path(tempfile.mkdtemp(prefix="gravitee-lab-index-")).resolve()
        self.addCleanup(shutil.rmtree, folder, True)
        return folder / "catalog.json"

    def test_a_catalog_that_is_not_here_has_no_index(self):
        self.assertEqual(self.run_tool("index", "ghost")[0], 2)


def compose_runs(case, *argv):
    """What Compose was given to run while `argv` ran, with the shell holding a secret of the runner: [(the words after `docker`,
    the environment it was started in)], and what the command answered."""
    found, popen = [], subprocess.Popen

    class Spy(popen):
        def __init__(self, *args, **kwargs):
            words = args[0] if args else kwargs.get("args")
            if isinstance(words, (list, tuple)) and words and str(words[0]).endswith("docker"):
                found.append((list(words[1:]), dict(kwargs.get("env") or {})))
            super().__init__(*args, **kwargs)
    with mock.patch.object(subprocess, "Popen", Spy), mock.patch.dict(os.environ, {"MYSECRET_X": "s3cret", "GITHUB_TOKEN": "g"}):
        answer = case.run_tool(*argv)
    return found, answer


@COMPOSE
class Indexing(Tool):
    """`index` of a folder, read where it is (what the managed workflows and `make index` run: `index catalog --out catalog.json`),
    and of an added catalog by its id."""

    def setUp(self):
        super().setUp()
        self.addCleanup(project.forget)

    def have(self, *ns):
        """Fixture catalogs `ns` added to this machine."""
        for one in ns:
            fixtures.install(self, one)
        project.forget()

    def copy_of(self, ns=PARTNER, files=None, name=None):
        """A working copy of fixture catalog `ns` as an author keeps it (a README beside the rest), the seeder of its seed service the
        core's: its path."""
        folder = self.author(ns, name=name, files={"README.md": "# A catalog\n", **(files or {})})
        for file in folder.rglob("*"):
            if file.is_file() and not file.is_symlink() and file.suffix in fixtures.TEXT:
                text = file.read_text(encoding="utf-8")
                if fixtures.SEEDER_PLACEHOLDER in text:
                    file.write_text(text.replace(fixtures.SEEDER_PLACEHOLDER, fixtures.seeder_image(self)), encoding="utf-8")
        return folder

    def test_the_index_of_a_folder_is_the_index_of_the_same_catalog_added(self):
        self.have(PARTNER)
        folder = self.copy_of()
        code, out, err = self.run_tool("index", str(folder))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, self.run_tool("index", PARTNER)[1])
        found = json.loads(out)
        self.assertEqual((found["catalog"]["id"], [(one["kind"], one["id"]) for one in found["units"]]), (PARTNER, [("addon", "ledger")]))

    def test_a_catalog_that_includes_from_another_is_read_with_the_one_the_lab_holds(self):
        self.have(PARTNER, "fixture")
        installed = self.run_tool("index", "fixture")
        self.assertEqual(installed[0], 0, installed)
        folder = self.copy_of("fixture")
        self.assertEqual(self.run_tool("index", str(folder)), installed)
        self.assertIn({"kind": "setup", "id": "pair"}, [{"kind": one["kind"], "id": one["id"]} for one in json.loads(installed[1])["units"]])

    def test_what_the_managed_workflows_run_is_a_folder_named_from_where_they_run(self):
        self.have(PARTNER)
        # ci.yml: `index catalog --out catalog.json` from the repository's root; `make index`: `index . --out catalog.json`
        folder = self.copy_of()
        wanted = self.run_tool("index", PARTNER)[1]
        before = os.getcwd()
        self.addCleanup(os.chdir, before)
        os.chdir(self.work)
        os.rename(folder, self.work / "catalog")
        self.assertEqual(self.run_tool("index", "catalog", "--out", "catalog.json"), (0, "", ""))
        self.assertEqual((self.work / "catalog.json").read_text(encoding="utf-8"), wanted)
        os.chdir(self.work / "catalog")
        self.assertEqual(self.run_tool("index", ".", "--out", "catalog.json"), (0, "", ""))
        self.assertEqual((self.work / "catalog" / "catalog.json").read_text(encoding="utf-8"), wanted)
        self.assertEqual(self.run_tool("index"), (0, wanted, ""), "and with no argument it is this folder")

    def test_it_links_nothing_and_writes_nothing_but_the_file_asked_for(self):
        self.have(PARTNER)
        folder = self.copy_of()
        holder = Path(tempfile.mkdtemp(prefix="gravitee-lab-private-tmp-")).resolve()
        self.addCleanup(shutil.rmtree, holder, True)
        out = holder / "catalog.json"
        before = (snapshot(self.root), contents(self.root), contents(folder))
        listed = (self.root / ".lab/catalogs.json").read_bytes()
        with mock.patch.object(tempfile, "tempdir", str(holder)):
            self.assertEqual(self.run_tool("index", str(folder)), (0, self.run_tool("index", PARTNER)[1], ""))
            self.assertEqual(self.run_tool("index", str(folder), "--out", str(out)), (0, "", ""))
            self.assertEqual([one for one in os.listdir(holder) if one != "catalog.json"], [], "the temporary project is gone")
        self.assertEqual((snapshot(self.root), contents(self.root), contents(folder)), before)
        self.assertEqual((self.root / ".lab/catalogs.json").read_bytes(), listed)
        self.assertEqual(out.read_text(encoding="utf-8"), self.run_tool("index", PARTNER)[1])
        self.assertEqual((paths.ROOT, check.ROOT, project.ROOT, compose.ROOT), (self.root,) * 4)

    def test_the_level_of_the_standard_the_catalog_follows_is_in_it(self):
        self.have(PARTNER)
        folder = self.copy_of(files={".github/standard.json": json.dumps({"standard": 1, "lab": {"repository": "o/lab", "ref": "a" * 40}})})
        self.assertEqual(json.loads(self.run_tool("index", str(folder))[1])["standard"], 1)
        self.assertNotIn("standard", json.loads(self.run_tool("index", PARTNER)[1]))

    def test_a_folder_that_is_no_catalog_has_no_index_and_says_why(self):
        empty = self.work / "elsewhere"
        empty.mkdir()
        (empty / "notes.txt").write_text("x")
        self.assertEqual(self.run_tool("index", str(empty)), (2, "", f"catalog.py: {empty} is not a catalog: it has no catalog.yml\n"))
        self.assertEqual(self.run_tool("index", "./nowhere")[0], 2)
        self.assertEqual(self.run_tool("index", "ghost")[0], 2)

    def test_a_catalog_yml_that_cannot_be_read_is_a_problem_of_the_catalog_and_not_of_the_tool(self):
        folder = self.copy_of(files={"catalog.yml": "x-gravitee-catalog:\n  schema: 1\n  name: No id\n"})
        code, out, err = self.run_tool("index", str(folder))
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("catalog.yml: "), err)
        gone = self.copy_of(name="gone")
        (gone / "catalog.yml").unlink()
        code, out, err = self.run_tool("index", str(gone))
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, "catalog.py: no index was made: there is no catalog.yml\n")

    def test_a_catalog_it_includes_from_that_is_not_here_is_an_environment_error(self):
        folder = self.copy_of("fixture")
        code, out, err = self.run_tool("index", str(folder))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("fixture includes from partner, which is neither installed nor linked here", err)

    def test_what_a_stranger_links_into_a_folder_is_not_read_into_the_index(self):
        self.have(PARTNER)
        secret = self.home / "secret.yml"
        secret.write_text("x-gravitee: {kind: addon, name: CANARY-NAME, description: CANARY-DESCRIPTION, category: Other}\n"
                          "services:\n  partner-ledger:\n    image: busybox\n    labels: {io.gravitee.description: CANARY-LABEL}\n",
                          encoding="utf-8")
        outside = self.home / "outside.json"
        outside.write_text(json.dumps({"standard": 1, "note": "CANARY-STANDARD"}), encoding="utf-8")
        folder = self.copy_of()
        unit = folder / "addons/ledger/compose.yml"
        unit.unlink()
        unit.symlink_to(secret)
        (folder / ".github").mkdir()
        (folder / ".github/standard.json").symlink_to(outside)
        code, out, err = self.run_tool("index", str(folder))
        self.assertNotIn("CANARY", out + err)
        self.assertNotIn("standard", json.loads(out) if out else {}, "a link is not read")
        (folder / "catalog.yml").unlink()
        (folder / "catalog.yml").symlink_to(self.link_of(PARTNER) / "catalog.yml")
        code, out, err = self.run_tool("index", str(folder))
        self.assertNotIn("CANARY", out + err)

    def test_a_name_with_an_escape_sequence_in_it_is_written_escaped_and_never_to_the_terminal(self):
        self.have(PARTNER)
        unit = (fixtures.CATALOGS / PARTNER / "addons/ledger/compose.yml").read_text(encoding="utf-8")
        folder = self.copy_of(files={"addons/ledger/compose.yml": unit.replace("name: Ledger,", 'name: "Led\\x1b[2Jger",', 1)})
        code, out, err = self.run_tool("index", str(folder))
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("\x1b", out)
        self.assertIn("Led\\u001b[2Jger", out, "JSON says every character it holds, escaped")

    def test_compose_reads_the_files_in_the_scrubbed_environment_for_a_folder_and_for_an_added_catalog(self):
        self.have(PARTNER)
        folder = self.copy_of()
        for argv in (("index", str(folder)), ("index", PARTNER), ("check", str(folder))):
            with self.subTest(argv=argv):
                project.forget()
                runs, (code, out, err) = compose_runs(self, *argv)
                self.assertEqual(code, 0, (out, err))
                self.assertTrue(runs, "it started Compose")
                for words, environ in runs:
                    self.assertTrue(environ, words)
                    self.assertNotIn("MYSECRET_X", environ, words)
                    self.assertNotIn("GITHUB_TOKEN", environ, words)


@COMPOSE
class Damaged(Tool):
    """A git install the Lab cannot read has no unit in the project, so a check of it reads nothing: it says so."""

    def setUp(self):
        gitfixture.require()
        super().setUp()
        isolated(self)
        project.forget()

    def test_a_damaged_install_is_not_all_good_and_has_no_index(self):
        fixtures.install(self, PARTNER, kind="git")
        project.forget()
        self.assertEqual(self.run_tool("check", PARTNER), (0, Reading.HEADER + "✓ catalog.yml\n✓ addons/ledger/compose.yml\n\nAll good\n",
                                                           ""), "an install holds what the Lab installs: the files of a repository are not asked of it")
        (self.link_of(PARTNER) / ".installed.json").unlink()
        project.forget()
        self.assertEqual(registry.inputs()[PARTNER]["state"], "damaged")
        wanted = ("catalog.py: partner is damaged: repair it from the menu of its row in the Lab UI, or remove it and add it "
                  "again\n")
        for argv in (("check", PARTNER), ("check", PARTNER, "--format", "json"), ("check", PARTNER, "--format", "github"),
                     ("index", PARTNER), ("images", PARTNER, "--dry-run")):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_tool(*argv), (2, "", wanted))

    def test_a_catalog_that_includes_from_a_damaged_install_says_which(self):
        fixtures.install(self, PARTNER, kind="git")
        fixtures.install(self, "fixture")
        (self.link_of(PARTNER) / ".installed.json").unlink()
        project.forget()
        code, out, err = self.run_tool("check", "fixture")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, "catalog.py: fixture includes from partner, which is damaged: repair it from the menu of its row "
                              "in the Lab UI, or remove it and add it again\n")


class Linked(Tool):
    """With no argument, `check` is about the current folder."""

    @COMPOSE
    def test_the_current_folder_is_the_one_checked(self):
        folder = self.author(files={"README.md": "# Partner\n"})
        before = os.getcwd()
        os.chdir(folder)
        self.addCleanup(os.chdir, before)
        code, out, err = self.run_tool("check")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, Reading.HEADER + "✓ catalog.yml\n✓ addons/ledger/compose.yml\n\nAll good\n")
        self.assertIsNone(self.config(), "and it was not linked")


class Explain(Tool):
    """explain CODE: the long form of a rule code."""

    def test_the_rule_why_it_exists_the_fix_in_full_and_what_the_lab_does_with_the_file(self):
        code, out, err = self.run_tool("explain", "ST05")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith("ST05 (error)\n"), out)
        for word in ("Rule     ", "Message  ", "Why      ", "Fix      git rm --cached {path}", "At Add   "):
            self.assertIn(word, out)
        self.assertEqual(self.run_tool("explain", "st05"), (code, out, err), "the case of the code does not matter")
        self.assertEqual(self.run_tool("explain", " ST05 "), (code, out, err))

    def test_the_policy_and_the_stages_are_codes_too_and_every_code_the_lab_knows_is_explained(self):
        from lab_ui.catalogs import explain
        for one in ("T11", "N01", "SD02", "BD01", "PB07", "E2E03"):
            with self.subTest(code=one):
                out = self.run_tool("explain", one)
                self.assertEqual(out[0], 0, out)
                self.assertTrue(out[1].startswith(f"{one} ("))
        self.assertGreater(len(explain.codes()), 100)
        for one in explain.codes():
            self.assertEqual(self.run_tool("explain", one)[0], 0, one)

    def test_a_text_that_is_no_code_is_a_usage_error_that_says_what_the_families_are(self):
        for text in ("nothing", "ST99", "st", "5", "ST05; rm -rf /", "\x1b[2Jxx"):
            with self.subTest(text=text):
                code, out, err = self.run_tool("explain", text)
                self.assertEqual((code, out), (2, ""))
                self.assertIn("is not a rule code. The families are ", err)
                self.assertNotIn("\x1b", err)

    def test_the_json_is_the_entry(self):
        code, out, err = self.run_tool("explain", "T03", "--format", "json")
        found = json.loads(out)
        self.assertEqual((code, found["code"], found["severity"]), (0, "T03", "error"))
        self.assertEqual(sorted(found), ["code", "lab", "message", "more", "rule", "severity", "why"])


class Delegated(Tool):
    """new, upgrade, release, repo-settings and ci are run by the module of the standard that owns each, with everything after their
    name as it is."""

    COMMANDS = (("new", "standard", "main_new"), ("upgrade", "standard", "main_upgrade"), ("release", "release", "main"),
                ("repo-settings", "settings", "main"), ("ci", "ci", "main"))

    def run_with(self, command, module_name, function, *argv, answer=0, say=""):
        seen = []

        def main(rest, **more):
            seen.append(list(rest))
            if say:
                print(say)
            return answer
        with mock.patch.dict(sys.modules, {f"lab_ui.catalogs.{module_name}": module(**{function: main})}):
            return self.run_tool(command, *argv), seen

    def test_everything_after_the_name_is_handed_over_as_it_is_and_the_exit_code_is_the_modules(self):
        for command, module_name, function in self.COMMANDS:
            for rest, answer in (([], 0), (["x", "--y", "z"], 1), (["--help"], 2), (["-h"], 0), (["--format", "json", "dir"], 1),
                                 (["--fleet-not", "--base=1"], 0), (["a b", "--maintainers", "@a,@b"], 1)):
                if command in catalog.HELP and ("--help" in rest or "-h" in rest):
                    continue   # the dispatcher answers a help word for a command whose module has none (Help, below)
                with self.subTest(command=command, rest=rest):
                    (code, out, err), seen = self.run_with(command, module_name, function, *rest, answer=answer)
                    self.assertEqual((code, out, err), (answer, "", ""))
                    self.assertEqual(seen, [rest])

    def test_what_the_module_prints_is_what_the_caller_reads(self):
        for command, module_name, function in self.COMMANDS:
            with self.subTest(command=command):
                (code, out, err), _ = self.run_with(command, module_name, function, say="from the module")
                self.assertEqual((code, out), (0, "from the module\n"))

    def test_a_module_that_a_command_needs_and_that_is_missing_is_a_fault_to_show(self):
        def broken(name, package=None):
            raise ModuleNotFoundError("No module named 'yaml'", name="yaml")
        with mock.patch.object(catalog.importlib, "import_module", broken):
            code, out, err = self.run_tool("ci", "plan", "catalog")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("a fault of the tool", err)

    def test_the_loop_over_the_catalogs_of_an_owner_is_the_fleet_and_needs_no_standard(self):
        for rest in (["--fleet", "acme", "--lab-ref", "a" * 40, "--format", "json"], ["--fleet=acme", "--lab-ref", "a" * 40], ["--fleet", "acme"]):
            with self.subTest(rest=rest):
                (code, out, err), seen = self.run_with("upgrade", "fleet", "main", *rest, answer=1)
                self.assertEqual((code, seen), (1, [rest]))
        seen = []
        with mock.patch.dict(sys.modules, {"lab_ui.catalogs.standard": None, "lab_ui.catalogs.fleet": module(main=lambda rest, **m: seen.append(rest) or 0)}):
            self.assertEqual(self.run_tool("upgrade", "--fleet", "acme", "--lab-ref", "b" * 40), (0, "", ""))
        self.assertEqual(seen, [["--fleet", "acme", "--lab-ref", "b" * 40]])

    def test_an_upgrade_that_is_not_the_loop_is_the_standard_s(self):
        (code, out, err), seen = self.run_with("upgrade", "standard", "main_upgrade", ".", "--write", "--lab-ref", "latest", answer=1)
        self.assertEqual((code, seen), (1, [[".", "--write", "--lab-ref", "latest"]]))

    def test_a_refusal_the_module_lets_out_is_its_line_and_a_bug_is_a_fault(self):
        def refuses(rest, **more):
            raise registry.CatalogError("not_catalog", "acme/hub has no catalog.yml, so it is not a catalog.", 400)

        def breaks(rest, **more):
            raise KeyError("version")
        with mock.patch.dict(sys.modules, {"lab_ui.catalogs.release": module(main=refuses)}):
            self.assertEqual(self.run_tool("release", "1.0.0"), (1, "", "acme/hub has no catalog.yml, so it is not a catalog.\n"))
        with mock.patch.dict(sys.modules, {"lab_ui.catalogs.release": module(main=breaks)}):
            code, out, err = self.run_tool("release", "1.0.0")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("a fault of the tool, not a problem of the catalog", err)

    def test_the_help_lists_every_command_of_the_standard(self):
        code, out, err = self.run_tool("--help")
        self.assertEqual(code, 0)
        for command in ("new", "check", "explain", "upgrade", "repo-settings", "release", "ci"):
            self.assertIn(command, out)

    def test_repo_settings_is_the_real_module_with_its_own_help_and_its_own_usage_errors(self):
        code, out, err = self.run_tool("repo-settings", "--help")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("catalog.py repo-settings [OWNER/REPO or FOLDER]", out)
        self.assertEqual(self.run_tool("repo-settings", "--yes"), (2, "", "--yes and --warnings go with --apply.\n"))


class Help(Tool):
    """`catalog.py COMMAND --help`: the modules that run new, upgrade and release have no help (their parsers stop at the first word
    they do not know), so the dispatcher says what the command does and how it is called; repo-settings and ci have their own."""

    def test_the_commands_whose_module_has_no_help_say_what_they_do_and_how_to_call_them(self):
        modules = {"lab_ui.catalogs.standard": None, "lab_ui.catalogs.release": None}   # not even needed for it
        for command in ("new", "upgrade", "release"):
            for words in (("--help",), ("-h",), ("somewhere", "--help"), ("--id", "x", "-h")):
                with self.subTest(command=command, words=words), mock.patch.dict(sys.modules, modules):
                    code, out, err = self.run_tool(command, *words)
                    self.assertEqual((code, err), (0, ""))
                    self.assertTrue(out.startswith(catalog.HELP[command][0] + "\n\nusage: catalog.py " + command), out)
        self.assertIn("--maintainers", self.run_tool("new", "--help")[1])
        self.assertIn("--lab-ref COMMIT|latest", self.run_tool("upgrade", "--help")[1])
        self.assertIn("catalog.py upgrade --fleet OWNER", self.run_tool("upgrade", "-h")[1])
        self.assertIn("[--tag] [--pr]", self.run_tool("release", "--help")[1])

    def test_a_command_that_is_not_asking_for_help_still_goes_to_its_module(self):
        seen = []
        stub = module(main_new=lambda rest, **more: seen.append(rest) or 0)
        with mock.patch.dict(sys.modules, {"lab_ui.catalogs.standard": stub}):
            self.assertEqual(self.run_tool("new", "dir", "--id", "x"), (0, "", ""))
        self.assertEqual(seen, [["dir", "--id", "x"]])

    def test_the_loop_over_an_owner_and_the_two_modules_with_a_help_of_their_own_keep_it(self):
        for command, module_name, function, rest in (("upgrade", "fleet", "main", ["--fleet", "acme", "--help"]),
                                                      ("repo-settings", "settings", "main", ["--help"]), ("ci", "ci", "main", ["--help"])):
            seen = []
            with self.subTest(command=command), mock.patch.dict(sys.modules, {
                    f"lab_ui.catalogs.{module_name}": module(**{function: lambda argv, **more: seen.append(argv) or 0})}):
                self.assertEqual(self.run_tool(command, *rest), (0, "", ""))
            self.assertEqual(seen, [rest])
        self.assertNotIn("repo-settings", catalog.HELP)
        self.assertNotIn("ci", catalog.HELP)

    def test_every_command_the_help_is_for_is_delegated(self):
        self.assertEqual(sorted(set(catalog.HELP) - set(catalog.DELEGATED)), [])
        for text, usage in catalog.HELP.values():
            self.assertTrue(text and usage.startswith("catalog.py "))


MANAGED = REPO / "lab-ui" / "templates" / "catalog" / "managed"
CALL = re.compile(r"python3 -I (?:\$\(CATALOG_PY\)|\S*catalog\.py) (?P<rest>.*)")
STOPS = re.compile(r"\s*(?:;|&&|\|\||\||>|<|\))")


def managed_calls():
    """[(file, line number, the words after `catalog.py`)] of every call of the command line in the files a catalog is given to run
    (the workflows, the actions, `.catalog/standard.mk`): the shell's variables and Make's functions are given a word, and the text
    that ends the command (`;;`, `&&`, a redirection) is cut."""
    found = []
    for file in sorted(MANAGED.rglob("*")):
        if not file.is_file() or file.suffix not in (".yml", ".mk"):
            continue
        for number, line in enumerate(file.read_text(encoding="utf-8").replace("\\\n", " ").splitlines(), 1):
            match = CALL.search(line)
            if not match:
                continue
            rest = match.group("rest")
            while True:   # the longer branch of `$(if $(A),--a,)`, the innermost first
                shorter = re.sub(r"\$\(if \$\(\w+\),([^()]*),\)", r"\1", rest)
                if shorter == rest:
                    break
                rest = shorter
            rest = re.sub(r"\$\(\w+\)|\$\{[^}]*\}|\$\w+", "X", rest)
            stop = STOPS.search(rest)
            found.append((file.relative_to(MANAGED).as_posix(), number, shlex.split(rest[:stop.start()] if stop else rest)))
    return found


class Managed(Tool):
    """The files the template gives a catalog call this command line: whatever they call is a command it has, with options it reads,
    and a stage the dispatcher knows. A change of either side that the other does not follow is a red here and not a red CI on the
    first push of every catalog."""

    def test_there_are_calls_to_look_at(self):
        calls = managed_calls()
        self.assertGreater(len(calls), 20, calls)
        self.assertEqual({"check", "index", "ci", "link", "upgrade", "repo-settings", "release", "explain"},
                         {words[0] for _, _, words in calls if words})

    def test_every_call_is_to_a_command_the_tool_has_with_options_it_reads(self):
        own = {name for name in catalog.parser()._subparsers._group_actions[0].choices} - set(catalog.DELEGATED)
        for file, number, words in managed_calls():
            where = f"{file}:{number}: catalog.py {' '.join(words)}"
            with self.subTest(where=where):
                self.assertTrue(words, where)
                if words[0] in own:
                    with contextlib.redirect_stderr(io.StringIO()) as complaint:
                        try:
                            catalog.parser().parse_args(words)
                        except SystemExit:
                            self.fail(f"{where}: {complaint.getvalue().strip()}")
                else:
                    self.assertIn(words[0], catalog.DELEGATED, where)

    def test_every_option_of_a_command_that_has_a_usage_here_is_in_it(self):
        for file, number, words in managed_calls():
            if words[0] in catalog.HELP:
                for option in (word for word in words[1:] if word.startswith("--")):
                    with self.subTest(file=file, line=number, option=option):
                        self.assertIn(option, catalog.HELP[words[0]][1])

    def test_every_stage_is_one_the_dispatcher_of_ci_has(self):
        try:
            stages = importlib.import_module("lab_ui.catalogs.ci").STAGES
        except ImportError:
            self.skipTest("lab_ui.catalogs.ci is not in this checkout yet")
        for file, number, words in managed_calls():
            if words[0] == "ci":
                with self.subTest(file=file, line=number):
                    self.assertIn(words[1], stages, f"{file}:{number}")

    def test_every_call_to_repo_settings_is_one_its_parser_reads(self):
        try:
            settings = importlib.import_module("lab_ui.catalogs.settings")
        except ImportError:
            self.skipTest("lab_ui.catalogs.settings is not in this checkout yet")
        for file, number, words in managed_calls():
            if words[0] == "repo-settings":
                with self.subTest(file=file, line=number):
                    settings.parser().parse_args(words[1:])

    def test_every_module_a_call_is_for_has_the_function_the_dispatcher_runs(self):
        for command, (name, function) in catalog.DELEGATED.items():
            if any(words[0] == command for _, _, words in managed_calls()):
                try:
                    module_ = importlib.import_module(f"lab_ui.catalogs.{name}")
                except ModuleNotFoundError:
                    continue   # written by another package, not in this checkout yet: the dispatcher says so
                with self.subTest(command=command):
                    self.assertTrue(callable(getattr(module_, function, None)), f"lab_ui.catalogs.{name}.{function}")


def module(**members):
    return types.SimpleNamespace(**members)


class Service(Tool):
    """The commands that do what the page does call the service, which another package writes: what they print, what they ask
    and when they stop. While the service is not in the checkout they say so (exit 2)."""

    def with_service(self, **members):
        members.setdefault("check_now", lambda ns: {})
        return mock.patch.dict(sys.modules, {"lab_ui.catalogs.service": module(**members)})

    PREVIEW = {"ticket": "abc", "expires": 600, "address": "github.com/acme/hub", "audience": "private",
               "catalog": {"id": "hub", "name": "Acme Hub", "version": "1.4.0"}, "counts": {"playground": 2, "addon": 1},
               "rows": {"builds": {"local": 2, "remote": ["github.com/acme/app"]}, "ports": [8140, 8141],
                        "core": ["API Management gateway"], "reads": ["license"],
                        "uses_machine": [{"name": "Kubernetes", "what": ["runs privileged"]}],
                        "needs": [{"name": "Partner Bank", "min": "2.0.0"}], "found": [{"name": "Ledger"}]},
               "blocked": [{"name": "X", "reasons": ["Mounts the Docker socket", "Runs with full privileges"]}]}

    def test_an_add_prints_the_preview_and_adds_only_with_yes(self):
        calls = []
        with self.with_service(check_address=lambda address, branch=None: calls.append(("check", address, branch)) or self.PREVIEW,
                               add=lambda ticket: calls.append(("add", ticket)) or {"catalog": {"name": "Acme Hub"}}):
            code, out, err = self.run_tool("add", "github.com/acme/hub", "--branch", "next")
            self.assertEqual((code, err, calls), (0, "", [("check", "github.com/acme/hub", "next")]))
            self.assertEqual(out.splitlines(), [
                "Acme Hub 1.4.0 (github.com/acme/hub, private)", "  2 playgrounds, 1 add-ons",
                "  Builds: 2 here, github.com/acme/app", "  Ports: 8140, 8141", "  Core: API Management gateway",
                "  Reads: license", "  Uses this machine: Kubernetes (runs privileged)", "  Needs: Partner Bank 2.0.0",
                "  Settings found: Ledger", "  Cannot run: X (Mounts the Docker socket; Runs with full privileges)",
                "Run it again with --yes to add it."])
            calls.clear()
            code, out, err = self.run_tool("add", "github.com/acme/hub", "--yes")
            self.assertEqual((code, calls), (0, [("check", "github.com/acme/hub", None), ("add", "abc")]))
            self.assertTrue(out.endswith("Added Acme Hub.\n"))

    def test_a_refusal_of_the_service_is_its_line_and_exit_1(self):
        def refuse(address, branch=None):
            raise registry.CatalogError("not_found", "acme/hub was not found. Check the address, or sign in with Git if it is private.", 400)
        with self.with_service(check_address=refuse):
            self.assertEqual(self.run_tool("add", "acme/hub"), (
                1, "", "acme/hub was not found. Check the address, or sign in with Git if it is private.\n"))

    def test_an_update_prints_the_plan_and_applies_it_only_with_yes(self):
        plan = {"ticket": "t1", "from": "9f1c2ab", "to": "c4d5e6f", "version": "1.5.0", "rewritten": False,
                "updates": [{"name": "Acme Bakery"}], "adds": [{"name": "Ledger"}], "removes": [{"name": "Old"}],
                "breaks": [{"name": "Demo", "needs": "Old"}], "changes": [{"name": "Acme Bakery", "fact": "port", "value": "8141"}],
                "blocked": [{"name": "X", "reasons": ["Publishes a port to the network"]}], "held": None}
        calls = []

        def update(ns, check=False, ticket=None, confirm_history=False):
            calls.append((ns, check, ticket, confirm_history))
            return plan if check else {"catalog": {}}
        with self.with_service(update=update):
            code, out, err = self.run_tool("update", "hub")
            self.assertEqual((code, calls), (0, [("hub", True, None, False)]))
            self.assertEqual(out.splitlines(), [
                "From 9f1c2ab to c4d5e6f, version 1.5.0", "  Updates: Acme Bakery", "  Adds: Ledger", "  Removes: Old",
                "  Breaks: Demo (needs Old)", "  Changes: Acme Bakery (port 8141)", "  Cannot run: X (Publishes a port to the network)",
                "Run it again with --yes to apply it."])
            calls.clear()
            self.run_tool("update", "hub", "--yes")
            self.assertEqual(calls, [("hub", True, None, False), ("hub", False, "t1", False)])
            calls.clear()
            plan["rewritten"] = True
            code, out, err = self.run_tool("update", "hub", "--yes")
            self.assertEqual((code, calls), (1, [("hub", True, None, False)]), "the word that applies any plan does not confirm a history")
            self.assertIn("This catalog was rewritten or moved.", out)
            self.assertEqual(err, "hub was rewritten or moved: run it again with --yes --confirm-history if you expected that.\n")
            calls.clear()
            self.assertEqual(self.run_tool("update", "hub", "--confirm-history")[0], 0)
            self.assertEqual(calls, [("hub", True, None, False)], "confirming is no applying: the plan is printed")
            calls.clear()
            code, out, err = self.run_tool("update", "hub", "--yes", "--confirm-history")
            self.assertEqual((code, err), (0, ""))
            self.assertEqual(calls[-1], ("hub", False, "t1", True))
            calls.clear()
            plan["rewritten"] = False
            self.run_tool("update", "hub", "--yes", "--confirm-history")
            self.assertEqual(calls[-1], ("hub", False, "t1", False), "a history that did not change is nothing to confirm")

    def test_an_update_asks_the_address_for_news_before_it_plans(self):
        calls = []

        def update(ns, check=False, ticket=None, confirm_history=False):
            calls.append(("update", ns))
            return {"upToDate": True}
        with self.with_service(update=update, check_now=lambda ns: calls.append(("check", ns)) or {}):
            self.assertEqual(self.run_tool("update", "hub")[0], 0)
            self.assertEqual(calls, [("check", "hub"), ("update", "hub")])
            calls.clear()
            self.run_tool("undo", "hub")
            self.assertNotIn(("check", "hub"), calls, "going back needs no news")

    def test_an_update_with_nothing_to_do_or_held_changes_nothing(self):
        calls = []

        def update(ns, check=False, ticket=None, confirm_history=False):
            calls.append((check, ticket))
            return self.answer
        with self.with_service(update=update):
            self.answer = {"upToDate": True}
            self.assertEqual(self.run_tool("update", "hub", "--yes"), (0, "Already up to date.\n", ""))
            self.answer = {"ticket": "t", "from": "a", "to": "b", "held": "Turn off Acme Bakery first."}
            code, out, err = self.run_tool("update", "hub", "--yes")
            self.assertEqual(code, 1)
            self.assertIn("  Held: Turn off Acme Bakery first.", out)
            self.assertEqual(calls, [(True, None), (True, None)])

    def test_going_back_is_the_same_with_its_own_verb(self):
        calls = []

        def undo(ns, check=False, ticket=None):
            calls.append((check, ticket))
            return {"ticket": "u1", "from": "b", "to": "a"} if check else {"catalog": {}}
        with self.with_service(undo=undo):
            self.assertEqual(self.run_tool("undo", "hub", "--yes")[:2], (0, "From b to a\nWent back hub.\n"))
            self.assertEqual(calls, [(True, None), (False, "u1")])

    def test_a_removal_goes_unless_it_is_held_and_keeps_the_settings_unless_asked(self):
        calls = []

        def remove(ns, check=False, keep_settings=True):
            calls.append((ns, check, keep_settings))
            return self.answer if check else {"warning": "Some files were created by Docker."}
        with self.with_service(remove=remove):
            self.answer = {"held": "Turn off Acme Bakery first.", "items": 3}
            self.assertEqual(self.run_tool("remove", "hub"), (1, "", "Turn off Acme Bakery first.\n"))
            self.assertEqual(calls, [("hub", True, True)])
            calls.clear()
            self.answer = {"held": None, "items": 3, "breaks": [{"name": "Demo"}]}
            code, out, err = self.run_tool("remove", "hub")
            self.assertEqual((code, out, err), (0, "Breaks: Demo\nRemoved hub.\n", "Some files were created by Docker.\n"))
            self.assertEqual(calls, [("hub", True, True), ("hub", False, True)])
            calls.clear()
            self.run_tool("remove", "hub", "--delete-settings")
            self.assertEqual(calls[-1], ("hub", False, False))

    def test_what_a_catalog_wrote_never_moves_the_cursor_of_the_terminal(self):
        # Names, reasons and problems come from catalogs that anyone can publish: a cursor-up and an erase-line would hide the
        # "Uses this machine" rows above them, a title or a clipboard sequence would write where it is not asked
        evil = "A\x1b[2K\x1b]0;pwn\x07\rB\u009b"
        preview = {**self.PREVIEW, "address": evil, "catalog": {"id": "hub", "name": evil, "version": evil},
                   "rows": {"uses_machine": [{"name": evil, "what": [evil]}], "needs": [{"name": evil, "min": evil}],
                            "found": [{"name": evil}], "ports": [evil]},
                   "blocked": [{"name": evil, "reasons": [evil]}]}
        plan = {"ticket": "t", "from": evil, "to": evil, "version": evil, "rewritten": True, "updates": [{"name": evil}],
                "breaks": [{"name": evil, "needs": evil}], "changes": [{"name": evil, "fact": evil, "value": evil}],
                "blocked": [{"name": evil, "reasons": [evil]}], "held": None}
        rows = [{"id": "x", "name": evil, "state": {"label": evil}, "version": evil, "address": evil}]
        answers = [{"held": evil}, {"held": None, "breaks": [{"name": evil}], "warning": evil}]

        def remove(ns, check=False, keep_settings=True):
            return answers[0] if check and answers[0].get("held") else {"held": None, **answers[1]} if check else answers[1]
        with self.with_service(check_address=lambda address, branch=None: preview, add=lambda ticket: {"catalog": {"name": evil}},
                               update=lambda ns, check=False, ticket=None, confirm_history=False: plan if check else {},
                               undo=lambda ns, check=False, ticket=None: plan if check else {}, remove=remove,
                               describe=lambda built, inputs: rows):
            for argv in (("add", "x", "--yes"), ("update", "hub", "--yes", "--confirm-history"), ("update", "hub", "--yes"),
                         ("undo", "hub", "--yes"), ("remove", "hub"), ("list",)):
                with self.subTest(argv=argv):
                    code, out, err = self.run_tool(*argv)
                    for stream in (out, err):
                        self.assertFalse(any(char in stream for char in "\x1b\x07\r\u009b"), repr(stream))
                    self.assertIn("A [2K ]0;pwn  B", out + err)
            answers[:] = [{"held": evil}, {}]
            code, out, err = self.run_tool("remove", "hub")
            self.assertEqual((code, out), (1, ""))
            self.assertEqual(err, "A [2K ]0;pwn  B \n")

    def test_the_catalogs_are_listed_one_to_a_line(self):
        rows = [{"id": "gravitee", "name": "Gravitee public", "state": {"key": "uptodate", "label": "Up to date"},
                 "version": "1.0.0", "address": "github.com/acme/hub"},
                {"id": None, "name": "Example", "state": {"key": "suggested", "label": "Not added"}, "version": None, "address": ""},
                {"id": "local", "name": "Personal", "state": {"key": "machine"}, "version": None, "address": None}]
        seen = []
        with self.with_service(describe=lambda built, inputs: seen.append(sorted(inputs)[:1]) or rows):
            code, out, err = self.run_tool("list")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines(), ["gravitee  Up to date  1.0.0  github.com/acme/hub", "-  Not added  -",
                                            "local  machine  -"])


class Provision(Tool):
    """The first start from a terminal: the intent, the downloads, the migration; exit 1 with the line on a failure."""

    def fake(self, provision=None, **more):
        calls = []
        firstrun = module(reconcile=lambda: calls.append("reconcile"), PROGRESS={},
                          provision=provision or (lambda ns: calls.append(f"provision {ns}") or {"id": ns, "name": ns.title()}),
                          **more)
        layout = module(waiting=lambda: False, migrate=lambda: {"moved": [], "problems": []})
        patcher = mock.patch.dict(sys.modules, {"lab_ui.catalogs.firstrun": firstrun, "lab_ui.library.layout": layout})
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls, firstrun, layout

    def declare(self, *names):
        for name in names:
            fixtures.declare(self, name, url=f"https://example.invalid/{name}.git")

    def test_the_intent_is_written_first_and_each_entry_without_a_copy_is_downloaded_in_order(self):
        self.declare("partner", "lab-new")
        fixtures.install(self, "lab-old", pin="a" * 40)   # a copy: left alone
        calls, _, _ = self.fake()
        code, out, err = self.run_tool("provision")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(calls, ["reconcile", "provision partner", "provision lab-new"])
        self.assertEqual(out.splitlines(), ["Downloading example.invalid/partner", "Installed Partner",
                                            "Downloading example.invalid/lab-new", "Installed Lab-New"])

    def test_a_failure_is_the_line_of_the_error_and_exit_1_and_the_others_are_still_tried(self):
        self.declare("partner", "lab-new")

        def provision(ns):
            if ns == "partner":
                raise registry.CatalogError("unreachable", "Cannot reach example.invalid. Check your connection, then try again.", 502)
            return {"id": ns, "name": "Newer"}
        self.fake(provision)
        code, out, err = self.run_tool("provision")
        self.assertEqual(code, 1)
        self.assertEqual(err, "partner: Cannot reach example.invalid. Check your connection, then try again.\n")
        self.assertIn("Installed Newer", out)

    def test_a_row_that_says_it_failed_is_a_failure_too(self):
        self.declare("partner")
        self.fake(lambda ns: {"id": ns, "error": "no_access", "state": {"reason": "No access to acme/hub. Run gh auth login in a terminal, then try again."}})
        code, out, err = self.run_tool("provision")
        self.assertEqual((code, err), (1, "partner: No access to acme/hub. Run gh auth login in a terminal, then try again.\n"))

    def test_nothing_to_download_is_a_success_and_the_intent_is_still_written(self):
        calls, _, _ = self.fake()
        self.assertEqual(self.run_tool("provision"), (0, "", ""))
        self.assertEqual(calls, ["reconcile"])

    def test_the_old_layout_is_moved_when_it_waits_and_its_problems_fail_the_command(self):
        calls, _, layout = self.fake()
        layout.waiting = lambda: True
        layout.migrate = lambda: {"moved": ["a", "b"], "problems": []}
        self.assertEqual(self.run_tool("provision"), (0, "Moved 2 items of the old layout\n", ""))
        layout.migrate = lambda: {"moved": ["addon:a"], "problems": ["addons/x: could not be moved"]}
        code, out, err = self.run_tool("provision")
        self.assertEqual((code, out, err), (1, "Moved 1 item of the old layout\n", "addons/x: could not be moved\n"))

    def test_the_progress_of_a_download_is_said_as_its_phase_changes(self):
        self.declare("partner")

        def provision(ns):
            for phase in ("fetching", "installing"):
                firstrun.PROGRESS[ns] = {"phase": phase, "fraction": 0.5}
                time.sleep(0.3)   # long enough for the watcher, which looks every hundredth of a second
            return {"id": ns, "name": "Partner"}
        _, firstrun, _ = self.fake(provision)
        self.patch(catalog, "POLL", 0.01)
        code, out, err = self.run_tool("provision")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines(), ["Downloading example.invalid/partner", "  fetching", "  installing", "Installed Partner"])

def scratch_lab(case):
    """A Lab checkout of its own for a test: a clone of a repository whose `main` holds one commit. Answers (the folder, the commit
    `origin/main` has, a second commit that is on no branch of the origin). git (a visible skip without)."""
    gitfixture.require()
    bare = gitfixture.make_repo(case.home / "lab-origin.git", {"README.md": "lab\n"})
    folder = case.work / "lab"
    gitfixture.git("clone", "--quiet", "--", gitfixture.url(bare), str(folder))
    main = gitfixture.git("-C", str(folder), "rev-parse", "HEAD").strip()
    gitfixture.git("-C", str(folder), "checkout", "--quiet", "-b", "side")
    (folder / "side").write_text("x\n")
    gitfixture.git("-C", str(folder), "add", "-A")
    gitfixture.git("-C", str(folder), "commit", "--quiet", "-m", "Not on the origin")
    return folder, main, gitfixture.git("-C", str(folder), "rev-parse", "HEAD").strip()


class NeedsCompose(Tool):
    """What reads a catalog needs Compose 5 or later (a running daemon is not needed), and says so when it is not there. `check` does
    not stop: it says what it did not check (tests/library/test_check_command.py)."""

    WANTED = "catalog.py: the Docker CLI with Compose 5 or later is needed to read a catalog (a running daemon is not)\n"

    def test_without_compose_or_with_an_old_one_the_commands_that_read_a_catalog_stop(self):
        folder = self.author()
        for answer in ((127, "", "docker: not found"), (0, "2.40.0\n", ""), (1, "", "no such command"), (0, "", "")):
            with self.subTest(answer=answer), mock.patch.object(compose, "run", return_value=answer), \
                    mock.patch.object(compose, "plugin", return_value="/x/docker-compose"):
                for argv in (("link", str(folder)), ("index", PARTNER), ("images", PARTNER)):
                    self.assertEqual(self.run_tool(*argv), (2, "", self.WANTED), argv)
        with mock.patch.object(compose, "plugin", return_value=None):
            self.assertEqual(self.run_tool("link", str(folder)), (2, "", self.WANTED), "no plugin of Docker to run")
        self.assertIsNone(self.config(), "nothing was linked")

    def test_a_compose_of_version_5_or_later_is_enough(self):
        for said in ("5.0.1\n", "v5.6.0\n", "6.1.2"):
            with self.subTest(said=said), mock.patch.object(compose, "run", return_value=(0, said, "")), \
                    mock.patch.object(compose, "plugin", return_value="/x/docker-compose"):
                catalog._compose()


@COMPOSE
class Spelling(Tool):
    """The engine is spelled one way: Compose takes its working directory from $PWD when that names the folder it is in, so a
    caller that reached the engine through a link would have it spell every path another way than the Lab does."""

    def seen(self, pwd, *argv):
        """The $PWD that every command started while `argv` ran was given (None: it was given none), and what it answered."""
        found, popen = [], subprocess.Popen

        class Spy(popen):
            def __init__(self, *args, **kwargs):
                found.append((kwargs.get("env") or {}).get("PWD"))
                super().__init__(*args, **kwargs)
        with mock.patch.object(subprocess, "Popen", Spy), mock.patch.dict(os.environ, {"PWD": str(pwd)}):
            answer = self.run_tool(*argv)
            put_back = os.environ["PWD"]
        return found, answer, put_back

    def test_the_commands_it_starts_are_told_the_real_folder_of_the_engine(self):
        fixtures.install(self, PARTNER, files={"README.md": "# Partner\n"})
        alias = self.home / "engine-link"
        alias.symlink_to(self.root)
        project.forget()
        found, (code, out, err), put_back = self.seen(alias, "check", PARTNER)
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.endswith("✓ catalog.yml\n✓ addons/ledger/compose.yml\n\nAll good\n"), out)
        self.assertTrue(found, "it started Compose")
        self.assertNotIn(str(alias), found, "never the link, which is the caller's")
        self.assertEqual({one for one in found if one is not None} - {str(self.root)}, set(), "a command is told the real folder or none")
        self.assertEqual(put_back, str(alias), "the caller's own is put back when the command ends")

    def test_the_verdict_is_the_same_from_wherever_the_command_is_run(self):
        fixtures.install(self, PARTNER)
        fixtures.install(self, "fixture")
        alias = self.home / "engine-link"
        alias.symlink_to(self.root)
        answers = {}
        for name, pwd in (("real", self.root), ("link", alias), ("elsewhere", "/")):
            project.forget()
            answers[name] = self.seen(pwd, "check", "fixture")[1]
        self.assertEqual(answers["real"], answers["link"])
        self.assertEqual(answers["real"], answers["elsewhere"])

    def test_without_a_pwd_there_is_none_after_either(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("PWD", None)
            self.run_tool("list")
            self.assertNotIn("PWD", os.environ)


class Other(Tool):
    """images, matrix and the way the tool is run."""

    def test_the_end_to_end_matrix_of_a_catalog_that_is_not_there_is_a_usage_error(self):
        code, out, err = self.run_tool("matrix", "hub", "--changed", "main", "HEAD")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("neither installed nor linked", err)

    def test_the_end_to_end_matrix_is_the_plan_of_the_package_as_json(self):
        from lab_ui import e2e
        asked = []
        self.patch(e2e, "plan", lambda ns, built, **how: asked.append((ns, how)) or {"matrix": {"include": []}, "count": 0})
        self.assertEqual(self.run_tool("matrix", "hub", "--changed", "main", "HEAD"),
                         (0, json.dumps({"matrix": {"include": []}, "count": 0}, indent=2) + "\n", ""))
        self.assertEqual(asked, [("hub", {"changed": ("main", "HEAD")})])

        def refused(ns, built, **how):
            raise e2e.PlanError("nothing to try")
        self.patch(e2e, "plan", refused)
        self.assertEqual(self.run_tool("matrix", "hub", "--changed", "main", "HEAD"), (2, "", "catalog.py: nothing to try\n"))

    def test_the_images_a_catalog_builds_are_printed_with_a_dry_run_and_the_seeder_is_the_engines(self):
        fixtures.install(self, PARTNER)
        builder = module(items=lambda roots: [Path("a")], builds=lambda item: {
            "web": {"context": "/p/web"}, "seeder": {"context": str(self.root / "seeder")}},
            SEEDER=self.root / "seeder", settings=lambda dry: (os.environ["REGISTRY"], "local", None),
            command=lambda registry_name, name, build, commit, source: ["docker", "buildx", f"{registry_name}/{name}"])
        meta = {"images": "ghcr.io/o/hub"}
        with mock.patch.object(catalog, "_lab_images", return_value=builder), \
                mock.patch.object(catalog, "_compose"), mock.patch.object(manifest, "read_catalog", return_value=meta), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REGISTRY", None)
            code, out, err = self.run_tool("images", PARTNER, "--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines(), ["== ghcr.io/o/hub/web", "docker buildx ghcr.io/o/hub/web"])

    def test_a_build_that_fails_is_a_line_and_exit_1_not_a_traceback(self):
        fixtures.install(self, PARTNER)
        builder = module(items=lambda roots: [Path("a")], builds=lambda item: {"web": {"context": "/p/web"}, "api": {"context": "/p/api"}},
                         SEEDER=self.root / "seeder", settings=lambda dry: ("ghcr.io/o/hub", "local", None),
                         command=lambda registry_name, name, build, commit, source: [
                             sys.executable, "-c", f"raise SystemExit({3 if name == 'api' else 0})"])
        with mock.patch.object(catalog, "_lab_images", return_value=builder), mock.patch.object(catalog, "_compose"), \
                mock.patch.object(manifest, "read_catalog", return_value={}):
            code, out, err = self.run_tool("images", PARTNER)
        self.assertEqual((code, out.splitlines(), err), (1, ["== ghcr.io/o/hub/api"], "ghcr.io/o/hub/api: the build failed (exit 3)\n"))

    def test_a_build_runs_in_the_build_environment_the_persons_home_and_nothing_of_the_runner(self):
        fixtures.install(self, PARTNER)
        probe = ("import os, sys; sys.exit(0 if os.environ.get('HOME') == sys.argv[1] and 'ACTIONS_RUNTIME_TOKEN' not in os.environ "
                 "and 'GITHUB_TOKEN' not in os.environ and 'SSH_AUTH_SOCK' not in os.environ else 7)")
        builder = module(items=lambda roots: [Path("a")], builds=lambda item: {"web": {"context": "/p/web"}},
                         SEEDER=self.root / "seeder", settings=lambda dry: ("ghcr.io/o/hub", "local", None),
                         command=lambda registry_name, name, build, commit, source: [sys.executable, "-c", probe, os.environ["HOME"]])
        shell = {"ACTIONS_RUNTIME_TOKEN": "t", "GITHUB_TOKEN": "g", "SSH_AUTH_SOCK": "/agent"}
        with mock.patch.object(catalog, "_lab_images", return_value=builder), mock.patch.object(catalog, "_compose"), \
                mock.patch.object(manifest, "read_catalog", return_value={}), mock.patch.dict(os.environ, shell):
            self.assertEqual(self.run_tool("images", PARTNER), (0, "== ghcr.io/o/hub/web\n", ""))

    def test_the_files_of_a_catalog_are_read_in_the_scrubbed_environment_and_the_process_has_its_own_again_after(self):
        fixtures.install(self, PARTNER)
        seen = {}

        def items(roots):
            seen["items"] = dict(os.environ)
            return [Path("a")]

        def builds(item):
            seen["builds"] = dict(os.environ)
            return {"web": {"context": "/p/web"}}

        def read_catalog(file, *more, **how):
            seen["catalog.yml"] = compose.environment()   # what Compose is given while the file is read
            return {}
        builder = module(items=items, builds=builds, SEEDER=self.root / "seeder", settings=lambda dry: ("ghcr.io/o/hub", "local", None),
                         command=lambda registry_name, name, build, commit, source: ["echo", name])
        shell = {"MYSECRET_X": "s3cret", "ACTIONS_RUNTIME_TOKEN": "t", "GITHUB_TOKEN": "g", "SSH_AUTH_SOCK": "/agent"}
        with mock.patch.object(catalog, "_lab_images", return_value=builder), mock.patch.object(catalog, "_compose"), \
                mock.patch.object(manifest, "read_catalog", read_catalog), mock.patch.dict(os.environ, shell):
            before = dict(os.environ)
            self.assertEqual(self.run_tool("images", PARTNER, "--dry-run"), (0, "== ghcr.io/o/hub/web\necho web\n", ""))
            self.assertEqual(dict(os.environ), before, "and the process has the environment it had")
        self.assertEqual(sorted(seen), ["builds", "catalog.yml", "items"])
        for where, environ in seen.items():
            with self.subTest(where=where):
                self.assertTrue(environ)
                self.assertEqual({name for name in environ if name in shell}, set(), "nothing of the runner's shell")

    def test_a_process_environment_is_put_back_even_when_what_ran_in_the_other_failed(self):
        with mock.patch.dict(os.environ, {"KEEP": "1"}):
            before = dict(os.environ)
            with self.assertRaises(RuntimeError), catalog._process_environment({"ONLY": "this"}):
                self.assertEqual(dict(os.environ), {"ONLY": "this"})
                raise RuntimeError("boom")
            self.assertEqual(dict(os.environ), before)

    def test_the_tool_runs_as_a_script_with_the_isolated_mode_and_says_its_commands(self):
        done = subprocess.run([sys.executable, "-I", str(REPO / "lab-ui" / "catalog.py"), "--help"], capture_output=True,
                              text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 0, done.stderr)
        for command in ("link", "unlink", "add", "provision", "list", "update", "undo", "remove", "suggest", "reset", "check",
                        "index", "images", "matrix", "new"):
            self.assertIn(command, done.stdout)

    def test_what_the_machine_refuses_is_a_line_and_exit_2_not_a_traceback(self):
        with mock.patch.object(registry, "entries", side_effect=OSError(13, "Permission denied")):
            self.assertEqual(self.run_tool("reset"), (2, "", "catalog.py: [Errno 13] Permission denied\n"))
        with mock.patch.object(registry, "entries", side_effect=ValueError("not a catalog")):
            self.assertEqual(self.run_tool("reset"), (2, "", "catalog.py: not a catalog\n"))

    def test_a_fault_of_the_tool_is_exit_2_with_its_trace_never_the_exit_1_of_a_verdict(self):
        # CI reads 1 as "this catalog has problems": a crash must not look like that, and must not write what a catalog put in it
        with mock.patch.object(registry, "entries", side_effect=AttributeError("x\x1b[2J")):
            code, out, err = self.run_tool("reset")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("a fault of the tool, not a problem of the catalog", err)
        self.assertIn("Traceback (most recent call last)", err)
        self.assertIn("AttributeError: x [2J", err)
        self.assertNotIn("\x1b", err)

    def test_a_usage_error_is_exit_2(self):
        for argv in ((), ("nothing",), ("link",), ("check", "x", "--format", "xml"), ("suggest", "a/b"), ("matrix", "x")):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(catalog.main(list(argv)), 2)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(catalog.main(["--help"]), 0)


if __name__ == "__main__":
    unittest.main()
