"""`catalog.py check` as a command (7.3 of the catalog repository standard): a folder is read where it is, so nothing is linked into
the Lab and nothing is written; without `--base` the commits since `origin/main` are read and the output says so; a run that could
not read the units says `Not checked: Docker Compose 5 not found`, ends `1 check skipped`, and is never fine; `--pr` reads a pull
request from git objects and runs nothing of it. The options (`--format`, `--ci`, `--soft`, `--strict`, `--error`, `--online`,
`--history`), the code of each finding and the unit rules of the images are here too.

Everything is on disk in a temporary project folder; git (a visible skip without) for the commits and the pull requests, Compose 5
(a visible skip without) for the units; no network and no Docker daemon."""

import contextlib
import importlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.catalogs import cli as catalog
from lab_ui.library import check, project
from lab_ui.docker import compose
from lab_ui import paths
from lab_ui.catalogs import gitx, registry, standard
from lab_ui.catalogs import store as bare
from lab_ui.catalogs import tree as trees

from .. import fixtures, gitfixture
from ..catalogs.test_cli import COMPOSE, PARTNER, REPO, Tool, contents, isolated, scratch_lab, snapshot

README = {"README.md": "# A catalog\n"}   # what a folder catalog needs besides catalog.yml and its units (ST17)
NOT_CHECKED = "Not checked: Docker Compose 5 not found\n"


def lines(text):
    return text.splitlines()


class Folder(Tool):
    """A project folder with the fixture catalogs, and the author's own folders beside it."""

    def setUp(self):
        super().setUp()
        self.addCleanup(project.forget)

    def catalog(self, ns=PARTNER, files=None, **more):
        """A working copy of fixture catalog `ns` with a README, as an author keeps it: its path. The seeder of its seed service is
        the image of this project's core."""
        folder = self.author(ns, files={**README, **(files or {})}, **more)
        for file in folder.rglob("*"):
            if file.is_file() and file.suffix in fixtures.TEXT:
                text = file.read_text(encoding="utf-8")
                if fixtures.SEEDER_PLACEHOLDER in text:
                    file.write_text(text.replace(fixtures.SEEDER_PLACEHOLDER, fixtures.seeder_image(self)), encoding="utf-8")
        return folder

    def check(self, *argv):
        return self.run_tool("check", *[str(one) for one in argv])


@COMPOSE
class Reading(Folder):
    """A folder is read where it is."""

    def test_a_folder_that_is_fine_is_all_good_and_exits_0(self):
        folder = self.catalog()
        code, out, err = self.check(folder)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines(out), [
            "Reading the files only: there is no origin/main to read the commits from",
            "No .github/standard.json: judged on the families that need no GitHub (ST, SE, CR, DF, GD, IM)",
            "✓ catalog.yml", "✓ addons/ledger/compose.yml", "", "All good"])

    def test_it_links_nothing_and_writes_nothing(self):
        # The Lab holds a catalog that this one includes from, and a list of catalogs: all of it is as it was after
        fixtures.install(self, PARTNER)
        folder = self.catalog("fixture")
        project.forget()
        holder = Path(tempfile.mkdtemp(prefix="gravitee-lab-private-tmp-")).resolve()
        self.addCleanup(shutil.rmtree, holder, True)
        before = (snapshot(self.root), contents(self.root), contents(folder))
        listed = (self.root / ".lab/catalogs.json").read_bytes()
        with mock.patch.object(tempfile, "tempdir", str(holder)), mock.patch.dict(os.environ):
            os.environ.pop("GITHUB_STEP_SUMMARY", None)
            for argv in ((folder,), (folder, "--format", "json"), (folder, "--format", "github"), (folder, "--ci"), (folder, "--soft")):
                self.check(*argv)
            self.assertEqual([one for one in os.listdir(holder) if one.startswith("gravitee-lab-check-")], [],
                             "the temporary project of a check is gone")
        self.assertEqual((snapshot(self.root), contents(self.root), contents(folder)), before)
        self.assertEqual((self.root / ".lab/catalogs.json").read_bytes(), listed)
        self.assertFalse(os.path.lexists(self.link_of("fixture")), "the folder was not linked")
        self.assertEqual([one["id"] for one in registry.entries()], [PARTNER])

    def test_without_a_catalogs_json_none_is_made(self):
        folder = self.catalog()
        self.assertFalse((self.root / ".lab/catalogs.json").exists())
        self.check(folder)
        self.assertFalse((self.root / ".lab/catalogs.json").exists())
        self.assertFalse(any(self.root.glob(".lab/catalogs/*")), "nothing is linked")

    def test_the_project_is_the_labs_again_after_the_check_even_when_it_failed(self):
        folder = self.catalog()
        with mock.patch.object(check, "catalog_problems", side_effect=RuntimeError("boom")):
            code, out, err = self.check(folder)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("a fault of the tool, not a problem of the catalog", err)
        self.assertEqual((paths.ROOT, check.ROOT, project.ROOT, compose.ROOT), (self.root,) * 4)
        self.assertEqual(self.check(folder)[0], 0, "and the next check reads the same project")

    def test_what_it_needs_from_the_lab_is_the_labs_own_catalog_and_nothing_else(self):
        fixtures.install(self, PARTNER)
        fixtures.install(self, "lab-old")
        folder = self.catalog("fixture")
        seen = []
        real = check.temporary_link

        @contextlib.contextmanager
        def spy(*args, **how):
            with real(*args, **how) as linked:
                seen.append(sorted(one.name for one in (linked.root / ".lab/catalogs").iterdir()))
                yield linked
        with mock.patch.object(check, "temporary_link", spy):
            self.check(folder)
        self.assertEqual(seen, [["fixture", PARTNER]], "the catalogs it requires, and it: not the others, not the personal area")

    def test_a_catalog_it_includes_from_that_is_not_here_is_an_environment_error(self):
        folder = self.catalog("fixture")
        code, out, err = self.check(folder)
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, "catalog.py: fixture includes from partner, which is neither installed nor linked here: check them "
                              "together, after catalog.py link PATH or catalog.py add ADDRESS --yes\n")

    def test_the_current_folder_is_the_default(self):
        folder = self.catalog()
        before = os.getcwd()
        os.chdir(folder)
        self.addCleanup(os.chdir, before)
        code, out, err = self.run_tool("check")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.endswith("✓ addons/ledger/compose.yml\n\nAll good\n"), out)

    def test_a_folder_is_the_argument_unless_it_is_the_id_of_a_catalog_that_is_here_written_without_a_path(self):
        mine = self.catalog(name="mine")
        fixtures.install(self, PARTNER)
        fixtures.install(self, "fixture")
        project.forget()
        os.chdir(self.work)
        self.addCleanup(os.chdir, str(REPO))
        # `mine` is a folder here and no catalog of the Lab: the folder. `fixture` is a catalog of the Lab and no folder here: the catalog
        self.assertEqual(self.run_tool("check", "mine")[0], 0)
        self.assertIn("✗ setups/pair.yml", self.run_tool("check", "fixture")[1])
        # A folder that is named like a catalog of the Lab is that catalog, unless it is written as a path
        shutil.copytree(mine, self.work / "fixture")
        self.assertIn("✗ setups/pair.yml", self.run_tool("check", "fixture")[1])
        self.assertEqual(self.run_tool("check", "./fixture")[0], 0)
        self.assertEqual(self.run_tool("check", str(self.work / "fixture"))[0], 0)

    def test_what_is_not_a_folder_or_a_catalog_is_an_environment_error(self):
        for argument, said in (("./nowhere", "catalog.py: ./nowhere is not a folder\n"),
                               ("ghost", "catalog.py: ghost is neither a folder nor a catalog installed or linked here\n")):
            with self.subTest(argument=argument):
                self.assertEqual(self.run_tool("check", argument), (2, "", said))
        self.assertEqual(self.run_tool("check", "x" * 400)[0], 2)

    def test_a_folder_that_has_nothing_of_a_catalog_is_not_one(self):
        empty = self.work / "elsewhere"
        empty.mkdir()
        (empty / "notes.txt").write_text("x")
        self.assertEqual(self.check(empty), (2, "", f"catalog.py: {empty} is not a catalog: it has no catalog.yml\n"))
        for sign in ("catalog.yml", ".github/standard.json", "addons", "setups", "playgrounds"):
            with self.subTest(sign=sign):
                folder = self.work / f"has-{sign.replace('/', '-').strip('.')}"
                (folder / sign).parent.mkdir(parents=True)
                if "." in sign:
                    (folder / sign).write_text("x")
                else:
                    (folder / sign).mkdir()
                self.assertNotEqual(self.check(folder)[0], 2)

    def test_a_catalog_yml_that_gives_no_id_is_told_and_the_units_are_not_read(self):
        folder = self.catalog(files={"catalog.yml": "x-gravitee-catalog:\n  schema: 1\n  name: No id\n  version: 1.0.0\n  lab: 1\n"
                                                    "  description: x\n"})
        code, out, err = self.check(folder)
        self.assertEqual((code, err), (1, ""))
        self.assertIn("✗ catalog.yml\n    id", out)
        self.assertIn("Not checked: the items: catalog.yml is not valid, so the units were not read\n", out)
        self.assertEqual(lines(out)[-1], "1 problem, 1 check skipped", "a finding with no code sends nobody to explain")

    def test_a_folder_without_catalog_yml_is_checked_for_what_it_is_and_the_units_are_said_not_read(self):
        folder = self.catalog()
        (folder / "catalog.yml").unlink()
        code, out, err = self.check(folder)
        self.assertEqual((code, err), (1, ""))
        self.assertIn("Not checked: the items: there is no catalog.yml, so the units were not read\n", out)
        self.assertIn("✗ catalog.yml\n    ST17 catalog.yml is missing.", out)
        self.assertEqual(lines(out)[-1], "1 problem, 1 check skipped. Run make explain CODE=ST17 for more.")

    def test_a_reserved_namespace_is_told_and_the_units_are_not_read(self):
        folder = self.catalog(files={"catalog.yml": "x-gravitee-catalog:\n  schema: 1\n  id: lab\n  name: Lab\n  version: 1.0.0\n"
                                                    "  lab: 1\n  description: x\n"})
        code, out, err = self.check(folder)
        self.assertEqual((code, err), (1, ""))
        self.assertIn("catalog.yml\n    lab is a reserved name", out)
        self.assertIn("Not checked: the items: the id in catalog.yml cannot be used, so the units were not read", out)

    def test_the_path_of_this_machine_is_not_in_a_line(self):
        folder = self.catalog(files={"addons/ledger/compose.yml": "x-gravitee: {kind: addon, name: Ledger, description: d, category: x}\n"
                                                                 "services:\n  partner-ledger:\n    image: x\n    volumes:\n"
                                                                 "      - ./data:/data\n      - ../../../data:/more\n"})
        code, out, err = self.check(folder)
        self.assertEqual(code, 1, out)
        for secret in (str(folder), str(self.root), str(self.work), str(self.home), tempfile.gettempdir()):
            self.assertNotIn(secret, out, secret)
            self.assertNotIn(secret, err)

    def test_a_service_that_builds_an_image_with_no_pull_policy_is_said_once_by_the_rule_of_the_images(self):
        unit = ("x-gravitee: {kind: addon, name: Web, description: d, category: Other}\nservices:\n  partner-web:\n    image: partner-web\n"
                "    build: ./web\n    labels: {io.gravitee.description: web}\n")
        folder = self.catalog(files={"addons/web/compose.yml": unit, "addons/web/README.md": "web\n", "addons/web/web/Dockerfile": "FROM x\n"})
        out = self.check(folder, "--format", "json")[1]
        said = [one["message"] for one in json.loads(out) if one["path"] == "addons/web/compose.yml" and "pull_policy" in one["message"]]
        self.assertEqual(said, ["addons/web/compose.yml: partner-web builds an image but has no pull_policy. Fix: pull_policy: "
                                "${ID_PULL_POLICY:-build}."])

    def test_a_bind_inside_the_folder_of_the_unit_says_nothing_though_the_folder_is_reached_through_a_link(self):
        text = (fixtures.CATALOGS / PARTNER / "addons/ledger/compose.yml").read_text(encoding="utf-8")
        unit = text.replace("    networks: [internal]\n", "    networks: [internal]\n    volumes:\n      - ./data:/data:ro\n")
        folder = self.catalog(files={"addons/ledger/compose.yml": unit})
        (folder / "addons/ledger/data").mkdir()
        code, out, err = self.check(folder)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines(out)[-1], "All good")

    def test_the_seeder_is_the_labs_and_is_not_asked_to_be_published(self):
        """The unit rules of the images (): the services a unit defines, with the context as the author wrote it."""
        image = fixtures.seeder_image(self)
        unit = ("x-gravitee: {kind: playground, name: Shop, description: d, tags: [a], duration: 5 min, first_start: 1 min}\n"
                f"services:\n  shop-seed:\n    image: {image}:latest\n    pull_policy: ${{SHOP_PULL_POLICY:-build}}\n"
                "    build: ../../../../../seeder\n    labels:\n      io.gravitee.role: seed\n"
                "      io.gravitee.description: seed\n")
        folder = self.catalog(files={"playgrounds/shop/compose.yml": unit, "playgrounds/shop/guide.md": "## 1. Start\n",
                                     "playgrounds/shop/README.md": "# Shop\n"})
        code, out, err = self.check(folder, "--format", "json")
        self.assertEqual(err, "")
        self.assertEqual([one for one in json.loads(out) if one["code"].startswith("IM")], [])


class Units(unittest.TestCase):
    """What the unit rules of the images are given."""

    def test_a_context_as_compose_prints_it_is_given_back_as_the_author_wrote_it(self):
        folder = "/p/.lab/catalogs/hub/playgrounds/shop"
        for given, wanted in (("/p/.lab/catalogs/hub/playgrounds/shop/app", "app"), ("/p/.lab/catalogs/hub/playgrounds/shop", "."),
                              ("/p/seeder", "../../../../../seeder"), ("https://github.com/o/r.git#" + "a" * 40, None),
                              ("/p/.lab/catalogs/hub/playgrounds/shop/${CONTEXT:-./web}", "${CONTEXT:-./web}")):
            with self.subTest(given=given):
                service = {"image": "i", "build": {"context": given, "dockerfile": "Dockerfile"}}
                found = check._spelled(service, folder)
                self.assertEqual(found["build"]["context"], wanted or given)
                self.assertEqual(found["build"]["dockerfile"], "Dockerfile")
                self.assertEqual(check._spelled({"build": given}, folder)["build"], wanted or given)
        self.assertEqual(check._spelled({"image": "i"}, folder), {"image": "i"})
        self.assertEqual(check._spelled({"build": {"dockerfile_inline": "FROM x"}}, folder), {"build": {"dockerfile_inline": "FROM x"}})


class Placing(unittest.TestCase):
    """Where the places of this machine go from a line."""

    def test_a_folder_is_taken_out_of_a_line_and_what_is_below_it_is_given_from_there(self):
        hide = ("/tmp/gravitee-lab-check-x/.lab/catalogs/hub", "/tmp/gravitee-lab-check-x", "/Users/me/hub")
        for line, wanted in (("mounts /Users/me/hub/plugins/apim of this machine", "mounts plugins/apim of this machine"),
                             ("build context /tmp/gravitee-lab-check-x/.lab/catalogs/hub/app", "build context app"),
                             ("in /tmp/gravitee-lab-check-x/seeder", "in seeder"),
                             ("the folder /Users/me/hub is", "the folder . is"),
                             ("/Users/me/hub-two/x stays", "/Users/me/hub-two/x stays"),
                             ("nothing of the sort", "nothing of the sort")):
            with self.subTest(line=line):
                self.assertEqual(check._hidden(line, hide), wanted)
        self.assertEqual(check._hidden("x /", ["/"]), "x /")
        self.assertEqual(check._hidden("x", [None, ""]), "x")


class Codes(Folder):
    """A unit that cannot run is told with the code of the rule of the Lab's policy that refuses it, and the fix."""

    def test_the_findings_of_the_items_are_problems_with_the_code_of_the_policy_when_it_has_one(self):
        fixtures.install(self, PARTNER)
        folder = self.catalog("fixture")
        code, out, err = self.check(folder, "--format", "json")
        self.assertEqual((code, err), (1, ""))
        found = {(one["path"], one["code"]): one for one in json.loads(out)}
        future = found[("addons/future/compose.yml", "T26")]
        self.assertEqual(future["severity"], "error")
        self.assertEqual(future["message"], "Needs a newer Gravitee Lab. Fix: Remove what this Gravitee Lab does not read, or check "
                                            "with the Gravitee Lab release that reads it (`catalog.py upgrade --lab-ref latest`). "
                                            "A `lab` is for older Gravitee Labs: `catalog.py upgrade --write` writes the "
                                            "one of `catalog.yml`.")
        self.assertEqual(found[("addons/port-core/compose.yml", "")]["message"], "publishes port 8082, like docker-compose.yml")
        self.assertEqual(set(one["code"] for one in json.loads(out)) - {""}, {"T26", "DF22", "DF23"})

    def test_the_text_aligns_a_finding_that_has_no_code_with_the_others_and_explains_one_that_has(self):
        fixtures.install(self, PARTNER)
        folder = self.catalog("fixture")
        out = self.check(folder)[1]
        self.assertIn("✗ addons/port-core/compose.yml\n    publishes port 8082, like docker-compose.yml\n", out)
        self.assertIn("✗ addons/future/compose.yml\n    T26 Needs a newer Gravitee Lab", out)
        self.assertTrue(out.rstrip().endswith(" for more."), out)
        self.assertIn("Run make explain CODE=T26 for more.", out.rstrip().splitlines()[-1])

    def test_a_reason_that_two_rules_say_alike_has_no_code_rather_than_a_wrong_one(self):
        reason = "Includes content from outside the catalog"
        self.assertEqual(check._blocked(reason, ["T19", "T23"], "hub", "a.yml")[0], "")
        self.assertEqual(check._blocked(reason, ["T23"], "hub", "a.yml")[0], "T23")
        code, text = check._blocked("Reads settings that are not its own: A, B", ["T20"], "acme-hub", "a.yml")
        self.assertEqual(code, "T20")
        self.assertIn("Prefix the setting with `ACME_HUB_`", text)
        code, text = check._blocked("Uses this machine without saying why: web", ["T28"], "hub", "a.yml")
        self.assertEqual(code, "T28")
        self.assertTrue(text.startswith("Uses this machine without saying why: web"))
        self.assertEqual(check._blocked("Something the Lab never said", ["T03"], "hub", "a.yml"), ("", "Something the Lab never said"))
        code, text = check._blocked("Needs a newer Gravitee Lab: uses x-gravitee.badge", ["T26"], "hub", "a.yml")
        self.assertEqual(code, "T26")
        self.assertIn("Fix: Remove what this Gravitee Lab does not read", text)

    def test_a_code_the_standard_knows_is_one_explain_knows(self):
        out = self.run_tool("explain", "T26")
        self.assertEqual(out[0], 0)
        self.assertIn("T26 (error)", out[1])


class Range(Folder):
    """Without --base the commits since origin/main are read, and the first line says so."""

    def setUp(self):
        gitfixture.require()
        super().setUp()
        isolated(self)
        project.forget()

    def clone(self, name="hub"):
        """A work tree of a catalog with an `origin` that has `main`: its path."""
        files = {path: (Path(fixtures.CATALOGS / PARTNER / path).read_text(encoding="utf-8")) for path in
                 ("catalog.yml", "addons/ledger/compose.yml", "addons/ledger/README.md")}
        origin = gitfixture.make_repo(self.home / f"{name}-origin.git", {**files, **README})
        work = self.work / name
        gitfixture.git("clone", "--quiet", "--", gitfixture.url(origin), str(work))
        return work

    def commit(self, work, name, text, message):
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        (work / name).write_text(text, encoding="utf-8")
        gitfixture.git("-C", str(work), "add", "-A")
        gitfixture.git("-C", str(work), "commit", "--quiet", "-m", message)

    @COMPOSE
    def test_the_range_since_origin_main_is_read_and_the_first_line_names_it(self):
        work = self.clone()
        gitfixture.git("-C", str(work), "checkout", "--quiet", "-b", "change")
        self.commit(work, "docs/one.md", "one\n", "Add one")
        self.commit(work, "docs/two.md", "two\n", "Add two")
        code, out, err = self.check(work)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines(out)[0], "Reading the files and the 2 commits since origin/main")
        self.assertEqual(lines(out)[-1], "All good")

    @COMPOSE
    def test_a_secret_in_an_earlier_commit_is_found_before_the_push(self):
        work = self.clone()
        gitfixture.git("-C", str(work), "checkout", "--quiet", "-b", "change")
        token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" + "k1L2m3N4o5P6q7R8s9T0"[:16]
        self.commit(work, "notes.txt", f"token {token}\n", "Add a note")
        self.commit(work, "notes.txt", "gone\n", "Remove it again")
        code, out, err = self.check(work)
        self.assertEqual(code, 1, out)
        self.assertIn("SE02", out)
        self.assertNotIn(token, out + err, "nothing prints a secret")
        self.assertEqual(lines(out)[0], "Reading the files and the 2 commits since origin/main")

    @COMPOSE
    def test_a_branch_with_nothing_new_has_no_commits_to_read(self):
        work = self.clone()
        out = self.check(work)[1]
        self.assertEqual(lines(out)[0], "Reading the files and the 0 commits since origin/main")

    def test_without_an_origin_main_only_the_files_are_read_and_the_first_line_says_so(self):
        work = self.clone()
        gitfixture.git("-C", str(work), "remote", "remove", "origin")
        code, out, err = self.check(work)
        self.assertEqual(lines(out)[0], "Reading the files only: there is no origin/main to read the commits from")

    @COMPOSE
    def test_a_base_is_any_name_git_resolves_and_the_first_line_names_the_commit(self):
        work = self.clone()
        self.commit(work, "docs/one.md", "one\n", "Add one")
        self.commit(work, "docs/two.md", "two\n", "Add two")
        head = gitfixture.git("-C", str(work), "rev-parse", "HEAD~1").strip()
        out = self.check(work, "--base", "HEAD~1")[1]
        self.assertEqual(lines(out)[0], f"Reading the files and the 1 commit since {head[:7]}")
        out = self.check(work, "--base", "no-such-branch")[1]
        self.assertIn("Not checked: the commits since the base: no-such-branch is not a commit of this repository", out)

    @COMPOSE
    def test_the_whole_history_is_read_with_history(self):
        work = self.clone()
        self.commit(work, "docs/one.md", "one\n", "Add one")
        out = self.check(work, "--history")[1]
        self.assertEqual(lines(out)[0], "Reading the files and the 2 commits of the whole history")

    @COMPOSE
    def test_the_base_of_a_first_push_is_the_whole_history_and_not_a_commit_that_is_missing(self):
        # ci.yml gives `--base "$BASE"`, and GitHub's `before` is 40 zeros on the first push of a branch: the first run of every new
        # catalog. No commit came before, so every commit is new (as `ci gitleaks` takes it)
        work = self.clone()
        self.commit(work, "docs/one.md", "one\n", "Add one")
        for zeros in ("0" * 40, "0" * 64):
            with self.subTest(zeros=len(zeros)):
                code, out, err = self.check(work, "--base", zeros)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(lines(out)[0], "Reading the files and the 2 commits of the whole history")
                self.assertNotIn("Not checked", out)
                self.assertEqual(self.check(work, "--base", zeros, "--ci"), (0, "", ""), "and in the run that CI makes of it")
                self.assertEqual(self.check(work, "--base", zeros, "--history")[1:], self.check(work, "--base", zeros)[1:])
        self.assertEqual(self.check(work, "--base", "")[0], 0, "an empty base asks for nothing: the default is taken")
        self.assertEqual(lines(self.check(work, "--base", "")[1])[0], "Reading the files and the 1 commit since origin/main")

    @COMPOSE
    def test_a_secret_in_the_first_push_is_found_in_the_whole_history_a_base_of_zeros_reads(self):
        work = self.clone()
        token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" + "k1L2m3N4o5P6q7R8s9T0"[:16]
        self.commit(work, "notes.txt", f"token {token}\n", "Add a note")
        self.commit(work, "notes.txt", "gone\n", "Remove it again")
        code, out, err = self.check(work, "--base", "0" * 40)
        self.assertEqual(code, 1, out)
        self.assertIn("SE02", out)
        self.assertNotIn(token, out + err)

    @COMPOSE
    def test_a_base_that_only_looks_like_zeros_is_still_a_commit_that_is_missing(self):
        work = self.clone()
        for base in ("0" * 39, "0" * 41, "0" * 39 + "1", "0" * 40 + "0" * 23):
            with self.subTest(base=base):
                code, out, err = self.check(work, "--base", base)
                self.assertEqual(code, 1)
                self.assertIn("Not checked: the commits since the base:", out)
                self.assertEqual(self.check(work, "--base", base, "--ci")[0], 2)


class NoCompose(Folder):
    """A run that skipped something never says it is fine."""

    def run_without(self, *argv):
        with mock.patch.object(compose, "plugin", return_value=None):
            return self.check(*argv)

    def test_the_families_run_the_items_are_said_not_checked_and_it_ends_with_one_check_skipped(self):
        folder = self.catalog()
        code, out, err = self.run_without(folder)
        self.assertEqual((code, err), (1, ""))
        self.assertEqual(lines(out), [
            "Reading the files only: there is no origin/main to read the commits from",
            "No .github/standard.json: judged on the families that need no GitHub (ST, SE, CR, DF, GD, IM)",
            "Not checked: Docker Compose 5 not found", "", "1 check skipped"])
        self.assertNotIn("All good", out)

    def test_the_structure_families_still_say_what_is_wrong(self):
        folder = self.catalog(files={".env": "SECRET=1\n"})
        code, out, err = self.run_without(folder)
        self.assertEqual(code, 1)
        self.assertIn("✗ .env\n    ST05 .env is an environment file.", out)
        self.assertTrue(lines(out)[-1].startswith("1 problem, 1 check skipped. Run make explain CODE=ST05 for more."), out)
        self.assertNotIn("All good", out)

    def test_soft_exits_0_and_ci_exits_2_and_neither_says_all_good(self):
        folder = self.catalog()
        code, out, err = self.run_without(folder, "--soft")
        self.assertEqual((code, err), (0, ""))
        self.assertIn(NOT_CHECKED, out)
        self.assertEqual(lines(out)[-1], "1 check skipped")
        code, out, err = self.run_without(folder, "--ci")
        self.assertEqual((code, err), (2, ""))
        self.assertEqual(out, "::error title=check::Not checked: Docker Compose 5 not found\n")
        self.assertNotIn("All good", out)

    def test_the_other_formats_say_it_too(self):
        folder = self.catalog()
        code, out, err = self.run_without(folder, "--format", "github")
        self.assertEqual((code, out), (1, "::warning title=check::Not checked: Docker Compose 5 not found\n"))
        code, out, err = self.run_without(folder, "--format", "json")
        self.assertEqual((code, json.loads(out), err), (1, [], NOT_CHECKED))

    def test_a_compose_that_is_older_than_5_or_cannot_answer_is_not_found_either(self):
        folder = self.catalog()
        for answer in ((0, "4.30.0\n", ""), (127, "", "docker: not found"), (1, "", "unknown command"), (0, "", ""), (124, "", "slow")):
            with self.subTest(answer=answer), mock.patch.object(compose, "plugin", return_value="/x/docker-compose"), \
                    mock.patch.object(compose, "run", return_value=answer):
                code, out, err = self.check(folder)
                self.assertEqual(code, 1)
                self.assertIn(NOT_CHECKED, out)
                self.assertEqual(lines(out)[-1], "1 check skipped")

    def test_a_compose_that_goes_away_while_the_units_are_read_is_a_skip_and_not_a_finding(self):
        fixtures.install(self, PARTNER)
        project.forget()
        with mock.patch.object(check, "compose_missing", return_value=None), \
                mock.patch.object(compose, "_config", side_effect=compose.ComposeError(compose.NO_COMPOSE)):
            code, out, err = self.run_tool("check", PARTNER)
        self.assertEqual(code, 1, out)
        self.assertIn(NOT_CHECKED, out)
        self.assertNotIn("unknown command", out)
        self.assertNotIn("could not be read", out)

    def test_compose_is_looked_for_in_the_real_environment_and_run_in_the_scrubbed_one(self):
        seen = []
        with mock.patch.object(compose, "plugin", return_value="/real/docker-compose"), \
                mock.patch.object(compose, "run", side_effect=lambda *a, **k: seen.append(k.get("environ")) or (0, "5.6.0\n", "")), \
                mock.patch.object(compose, "scrubbed_environment", return_value={"PATH": "/usr/bin", "HOME": "/empty"}):
            self.assertIsNone(check.compose_missing())
        self.assertEqual(seen, [{"PATH": "/usr/bin", "HOME": "/empty"}])


@COMPOSE
class Options(Folder):
    """The format, the summary and the strictness of a run."""

    def test_the_formats_are_the_same_findings(self):
        folder = self.catalog(files={".env": "A=1\n"})
        text = self.check(folder)[1]
        asjson = json.loads(self.check(folder, "--format", "json")[1])
        github = self.check(folder, "--format", "github")[1]
        self.assertIn("ST05", text)
        self.assertEqual([(one["code"], one["path"]) for one in asjson], [("ST05", ".env")])
        self.assertEqual(github, "::error file=.env,title=ST05::.env is an environment file. Fix: git rm --cached .env, add it to "
                                 ".gitignore, and commit a .env.example with defaults. If it held a real secret, revoke the secret "
                                 "first: removing the file does not remove it from the history.\n")
        self.assertEqual(sorted(asjson[0]), ["code", "line", "message", "path", "severity"])
        self.assertEqual(self.check(folder, "--format", "json")[1], self.check(folder, "--format", "json")[1], "the same bytes")

    def test_a_finding_with_no_code_is_an_annotation_with_no_title_and_one_with_a_code_has_it(self):
        given = ("::error file=a.yml,title=::no code\n::error file=b.yml,line=3,title=ST05::coded\n::warning title=::no file\n"
                 "::notice title=check::kept\n::error file=a%2Ctitle=::x,title=::message that says ,title=:: and  title=:: itself\n"
                 "::error file=c.yml,title=::so does this ,title=:: one\n")
        self.assertEqual(catalog._untitled(given),
                         "::error file=a.yml::no code\n::error file=b.yml,line=3,title=ST05::coded\n::warning::no file\n"
                         "::notice title=check::kept\n::error file=a%2Ctitle=::x,title=::message that says ,title=:: and  title=:: itself\n"
                         "::error file=c.yml::so does this ,title=:: one\n")

    def test_ci_is_github_annotations_and_a_summary_in_the_file_the_runner_names(self):
        folder = self.catalog(files={".env": "A=1\n"})
        summary = self.work / "summary.md"
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
            code, out, err = self.check(folder, "--ci")
            self.assertEqual((code, err), (1, ""))
            self.assertTrue(out.startswith("::error file=.env,title=ST05::"), out)
            first = summary.read_text(encoding="utf-8")
            self.assertTrue(first.startswith("### Catalog check\n\n1 problem to look at. To see them on your machine, run `make check`."), first)
            self.assertIn("| ✗ | .env | ST05 |", first)
            self.check(folder, "--ci")
            self.assertEqual(summary.read_text(encoding="utf-8"), first + first, "appended to, as the runner's file is")
        # No file named, or a path that is not absolute: nothing is written and nothing fails
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": "summary.md"}):
            self.assertEqual(self.check(folder, "--ci")[0], 1)
        self.assertFalse((Path.cwd() / "summary.md").exists())
        env = {key: value for key, value in os.environ.items() if key != "GITHUB_STEP_SUMMARY"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(self.check(folder, "--ci")[0], 1)

    def test_a_summary_that_cannot_be_written_is_said_and_the_verdict_stands(self):
        folder = self.catalog()
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(self.work / "nowhere" / "summary.md")}):
            code, out, err = self.check(folder, "--ci")
        self.assertEqual((code, out), (0, ""))
        self.assertTrue(err.startswith("catalog.py: the summary could not be written: "), err)

    def test_nothing_asks_the_network_unless_online_is_given(self):
        fixtures.install(self, PARTNER)
        folder = self.catalog("fixture")
        with mock.patch.object(gitx, "ls_remote", side_effect=AssertionError("a request to the network")) as asked, \
                mock.patch.object(catalog, "_on_main", side_effect=AssertionError("a look at the Lab's origin")):
            for argv in ((folder,), (folder, "--ci"), (folder, "--history"), (folder, "--format", "json")):
                self.assertIn(self.check(*argv)[0], (0, 1), argv)   # 2 is a fault of the tool: a call that was refused
        asked.assert_not_called()

    def test_ci_with_an_explicit_format_keeps_it(self):
        folder = self.catalog()
        self.assertEqual(self.check(folder, "--ci", "--format", "json")[1], "[]\n")

    def test_soft_makes_every_error_a_warning_and_exits_0(self):
        folder = self.catalog(files={".env": "A=1\n"})
        code, out, err = self.check(folder, "--soft")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("! .env", out)
        self.assertIn("1 warning", out)
        found = json.loads(self.check(folder, "--soft", "--format", "json")[1])
        self.assertEqual([one["severity"] for one in found], ["warning"])

    def test_soft_also_softens_what_the_items_found(self):
        fixtures.install(self, PARTNER)
        folder = self.catalog("fixture")
        code, out, err = self.check(folder, "--soft", "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual({one["severity"] for one in json.loads(out)}, {"warning"})

    def test_strict_makes_a_warning_fail_the_run(self):
        folder = self.catalog(files={"docs/win.txt": b"a\r\nb\r\n".decode()})
        (folder / "docs/win.txt").write_bytes(b"a\r\nb\r\n")
        self.assertEqual(self.check(folder)[0], 0)
        self.assertEqual(self.check(folder, "--strict")[0], 1)

    def test_error_makes_the_rules_named_errors_for_this_run(self):
        folder = self.catalog(files={"docs/win.txt": ""})
        (folder / "docs/win.txt").write_bytes(b"a\r\nb\r\n")
        self.assertEqual(self.check(folder, "--error", "ST12")[0], 1)
        self.assertEqual(self.check(folder, "--error", "ST12,DF22")[0], 1)
        self.assertEqual(self.check(folder, "--error", "df22", "--error", "st12")[0], 1)
        self.assertEqual(self.check(folder, "--error", "DF22")[0], 0)
        for bad in ("ST", "st5", "ST12;rm", "--x", ""):
            with self.subTest(bad=bad):
                self.assertEqual(self.check(folder, "--error", bad)[0], 2)

    def test_verbose_adds_the_information(self):
        folder = self.catalog()
        quiet, loud = (json.loads(self.check(folder, "--format", "json", *more)[1]) for more in ((), ("--verbose",)))
        self.assertGreaterEqual(len(loud), len(quiet))

    def test_a_pull_request_has_no_base_of_its_own_to_give(self):
        folder = self.catalog()
        self.assertEqual(self.check(folder, "--pr", "7", "--base", "HEAD"),
                         (2, "", "catalog.py: --base cannot be combined with --pr: the base of a pull request is main\n"))

    def test_a_file_name_with_workflow_commands_in_it_is_one_annotation(self):
        folder = self.catalog(files={"a%0A::error::x.txt": "x\n", ".env": "A=1\n"})
        out = self.check(folder, "--format", "github")[1]
        self.assertTrue(all(line.startswith("::") for line in lines(out)), out)
        self.assertEqual(len([line for line in lines(out) if "ST03" in line or "ST05" in line]), len(lines(out)))


class Pull(Folder):
    """`--pr` reads a pull request from git objects and runs nothing of it."""

    def setUp(self):
        gitfixture.require()
        super().setUp()
        isolated(self)
        project.forget()
        self.marker = self.work / "marker-file"

    def origin(self, extra=None, name="hub"):
        """A repository of a catalog whose `main` is the partner fixture, with pull request 7 on `refs/pull/7/head` that adds `extra`.
        Answers (the bare repository, a work tree of it with its origin)."""
        files = {path: Path(fixtures.CATALOGS / PARTNER / path).read_text(encoding="utf-8")
                 for path in ("catalog.yml", "addons/ledger/compose.yml", "addons/ledger/README.md")}
        repo = gitfixture.make_repo(self.home / f"{name}.git", {**files, **README})
        main = gitfixture.tip(repo)
        pull = gitfixture.commit(repo, extra or {}, "A change")
        gitfixture.git("update-ref", "refs/pull/7/head", pull, repo=repo)
        gitfixture.git("update-ref", "refs/heads/main", main, repo=repo)
        work = self.work / name
        gitfixture.git("clone", "--quiet", "--", gitfixture.url(repo), str(work))
        return repo, work

    def programs(self):
        """What would write the marker file if anyone ran it: a Makefile, a hook, a development container, a task of the editor."""
        touch = f"touch {self.marker}\n"
        return {"Makefile": f"check:\n\t{touch}all: check\n",
                ".githooks/pre-commit": f"#!/bin/sh\n{touch}",
                ".husky/pre-commit": f"#!/bin/sh\n{touch}",
                ".devcontainer/devcontainer.json": '{"postCreateCommand": "touch ' + str(self.marker) + '"}\n',
                ".vscode/tasks.json": '{"tasks": [{"command": "touch ' + str(self.marker) + '"}]}\n',
                ".gitattributes": "* filter=evil\n",
                "addons/ledger/Makefile": f"all:\n\t{touch}"}

    @COMPOSE
    def test_a_pull_request_with_a_makefile_a_hook_and_a_development_container_runs_none_of_them(self):
        repo, work = self.origin(self.programs())
        code, out, err = self.check(work, "--pr", "7")
        self.assertEqual(err, "")
        self.assertFalse(self.marker.exists(), "something of the pull request ran")
        self.assertEqual(code, 1, out)
        self.assertEqual(lines(out)[0].split(" since ")[0], "Reading the files and the 1 commit")
        self.assertIn("ST22 .devcontainer/devcontainer.json would run on a reader's machine", out)
        self.assertIn("ST22 .githooks/pre-commit would run on a reader's machine", out)
        self.assertIn("ST22 addons/ledger/Makefile would run on a reader's machine", out)

    def hostile_clone(self):
        """A clone with a commit of its own on a branch, whose git configuration and hooks would run a program for almost any git
        command: a hook of each kind, a file system monitor, an external diff, a text conversion, a filter, a pager, a program for
        signatures. They are put in once the commit is made, so that the test's own git does not run them."""
        repo, work = self.origin({"docs/note.md": "note\n"})
        gitfixture.git("-C", str(work), "checkout", "--quiet", "-b", "change")
        (work / "docs").mkdir(exist_ok=True)
        (work / "docs/more.md").write_text("more\n")
        gitfixture.git("-C", str(work), "add", "-A")
        gitfixture.git("-C", str(work), "commit", "--quiet", "-m", "Add more")
        program = self.work / "touch-marker.sh"
        program.write_text(f"#!/bin/sh\ntouch {self.marker}\n", encoding="utf-8")
        program.chmod(0o755)
        hooks = work / ".git" / "hooks"
        for name in ("pre-commit", "post-checkout", "post-merge", "post-commit", "reference-transaction", "post-rewrite", "pre-push",
                     "fsmonitor-watchman", "pre-auto-gc"):
            (hooks / name).write_text(f"#!/bin/sh\ntouch {self.marker}\n", encoding="utf-8")
            (hooks / name).chmod(0o755)
        for key, value in (("core.fsmonitor", str(program)), ("core.hooksPath", str(hooks)), ("core.pager", str(program)),
                           ("diff.external", str(program)), ("diff.evil.textconv", str(program)), ("filter.evil.smudge", str(program)),
                           ("filter.evil.clean", str(program)), ("filter.evil.process", str(program)), ("gpg.program", str(program)),
                           ("log.showSignature", "true"), ("core.sshCommand", str(program)), ("core.editor", str(program)),
                           ("credential.helper", str(program)), ("uploadpack.packObjectsHook", str(program))):
            gitfixture.git("-C", str(work), "config", key, value)
        (work / ".git" / "info").mkdir(exist_ok=True)
        (work / ".git" / "info" / "attributes").write_text("* filter=evil diff=evil\n", encoding="utf-8")
        return repo, work

    @COMPOSE
    def test_a_clone_that_would_run_a_program_for_git_does_not_have_it_run(self):
        repo, work = self.hostile_clone()
        try:   # the configuration is a real danger: the person's own git runs the program (and may fail for it: what matters is that it ran)
            gitfixture.git("-C", str(work), "checkout", "--quiet", "-b", "probe")
        except AssertionError:
            pass
        self.assertTrue(self.marker.exists())
        self.marker.unlink()
        for argv in ((work,), (work, "--history"), (work, "--pr", "7"), (work, "--format", "json"), (work, "--base", "HEAD~1")):
            with self.subTest(argv=argv):
                code, out, err = self.check(*argv)
                self.assertIn(code, (0, 1), (out, err))
                self.assertFalse(self.marker.exists(), f"a program of the clone ran for {argv}")
        self.assertIn("1 commit since", self.check(work)[1].splitlines()[0], "and it did read the commit that was made")

    @COMPOSE
    def test_the_working_tree_of_the_clone_is_untouched(self):
        repo, work = self.origin(self.programs())
        before = contents(work)
        self.check(work, "--pr", "7")
        self.assertEqual(contents(work), before)
        self.assertFalse((work / "Makefile").exists(), "nothing was checked out")

    @COMPOSE
    def test_it_links_nothing_and_writes_nothing_in_the_lab_and_leaves_no_store_behind(self):
        repo, work = self.origin({"docs/note.md": "note\n"})
        holder = Path(tempfile.mkdtemp(prefix="gravitee-lab-private-tmp-")).resolve()
        self.addCleanup(shutil.rmtree, holder, True)
        before = (snapshot(self.root), contents(self.root))
        with mock.patch.object(tempfile, "tempdir", str(holder)):
            self.assertEqual(self.check(work, "--pr", "7")[0], 0)
            self.assertEqual([one for one in os.listdir(holder) if one.startswith(("gravitee-lab-pr-", "gravitee-lab-check-"))], [])
        self.assertEqual((snapshot(self.root), contents(self.root)), before)

    @COMPOSE
    def test_the_findings_are_those_of_the_pull_request_and_of_its_commits(self):
        token = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0" + "P9o8N7m6L5k4J3i2"[:16]
        repo, work = self.origin({".env": "A=1\n", "docs/key.txt": f"token {token}\n"})
        code, out, err = self.check(work, "--pr", "7")
        self.assertEqual((code, err), (1, ""))
        self.assertIn("ST05 .env is an environment file", out)
        self.assertIn("SE02", out)
        self.assertNotIn(token, out)

    @COMPOSE
    def test_a_unit_the_pull_request_adds_is_judged_by_the_policy_through_compose(self):
        unit = ("x-gravitee: {kind: addon, name: Risky, description: d, category: x}\nservices:\n  partner-risky:\n"
                "    image: x\n    privileged: true\n    labels: {io.gravitee.description: r}\n")
        repo, work = self.origin({"addons/risky/compose.yml": unit, "addons/risky/README.md": "risky\n"})
        code, out, err = self.check(work, "--pr", "7")
        self.assertEqual((code, err), (1, ""))
        self.assertIn("    T03 Runs with full privileges. Fix: Remove `privileged`: a demo that needs it is a personal item.\n", out)
        self.assertIn("✗ addons/risky/compose.yml\n", out)

    @COMPOSE
    def test_only_what_the_lab_installs_is_handed_to_compose_and_never_a_link(self):
        seen = {}
        real = check.temporary_link

        @contextlib.contextmanager
        def spy(folder, *args, **how):
            seen["files"] = sorted(str(path.relative_to(folder)) for path in Path(folder).rglob("*") if path.is_file() or path.is_symlink())
            seen["links"] = [str(path) for path in Path(folder).rglob("*") if path.is_symlink()]
            with real(folder, *args, **how) as linked:
                yield linked
        repo, work = self.origin({"addons/ledger/link": gitfixture.Symlink("/etc/passwd"), "addons/ledger/.env": "A=1\n",
                                  "docs/note.md": "note\n", "Makefile": "all:\n"})
        with mock.patch.object(check, "temporary_link", spy):
            self.check(work, "--pr", "7")
        self.assertEqual(seen["links"], [])
        self.assertEqual(seen["files"], ["addons/ledger/README.md", "addons/ledger/compose.yml", "catalog.yml"])

    TOO_BIG = "This catalog is larger than the Lab accepts. Ask its owners to make it smaller.\n"

    @COMPOSE
    def test_the_download_of_a_pull_request_stops_when_git_says_it_passed_what_the_lab_accepts(self):
        # A stranger's fork can hold anything: the size is not looked at once the whole of it is on the disk
        repo, work = self.origin({"docs/note.md": "note\n"})
        calls = []

        def stream(args, cwd=None, env=None, stall=30, cap=600, on_stderr=None, limit=None):
            calls.append(list(args))
            for line in ("Receiving objects:   9% (1/10), 1.00 MiB | 2.00 MiB/s\r", "Receiving objects:  50% (5/10), 6.00 MiB | 2.00 MiB/s\r"):
                on_stderr(line)
            calls.append("the download went on")
            return gitx.Result(0, b"", "")
        holder = Path(tempfile.mkdtemp(prefix="gravitee-lab-private-tmp-")).resolve()
        self.addCleanup(shutil.rmtree, holder, True)
        with mock.patch.object(bare, "STORE_LIMIT", 5 * 1024 * 1024), mock.patch.object(gitx, "stream", stream), \
                mock.patch.object(tempfile, "tempdir", str(holder)):
            self.assertEqual(self.check(work, "--pr", "7"), (1, "", self.TOO_BIG))
            self.assertEqual(os.listdir(holder), [], "and the store it was filling is gone")
        [args] = calls
        self.assertIn("--progress", args, "git reports what it has received only when it is asked to")
        self.assertIn("fetch", args)

    @COMPOSE
    def test_a_pull_request_over_the_limit_is_refused_whatever_git_reports_on_the_way(self):
        repo, work = self.origin({"big.bin": gitfixture.filler(6 * 1024 * 1024)})
        with mock.patch.object(bare, "STORE_LIMIT", 5 * 1024 * 1024):
            self.assertEqual(self.check(work, "--pr", "7"), (1, "", self.TOO_BIG))
        self.assertNotEqual(self.check(work, "--pr", "7")[2], self.TOO_BIG, "and under the real limit it is a pull request like another")

    @COMPOSE
    def test_the_audience_of_the_catalog_is_asked_where_its_origin_is_and_not_in_the_folder_of_the_units(self):
        repo, work = self.origin({"docs/note.md": "note\n"})
        asked = []
        real = catalog._audiences

        def spy(folder, *args):
            asked.append(Path(folder))
            return real(folder, *args)
        with mock.patch.object(catalog, "_audiences", spy):
            self.assertEqual(self.check(work, "--pr", "7", "--online")[0], 0)
        self.assertEqual(asked, [work.resolve()], "the clone has the origin; the units of the pull request are not a repository")

    def test_a_pull_request_that_is_not_there_is_an_environment_error(self):
        repo, work = self.origin()
        code, out, err = self.check(work, "--pr", "99")
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("catalog.py: pull request 99 was not found in "), err)

    def test_a_number_or_the_address_of_a_pull_request_of_the_origin_is_all_it_takes(self):
        repo, work = self.origin()
        for bad in ("0", "07", "-1", "7;rm", "x", "", "1" * 12, "https://github.com/other/hub/pull/7", "https://example.com/a/b/pull/7"):
            with self.subTest(bad=bad):
                self.assertEqual(self.check(work, "--pr", bad)[0], 2)
        self.assertEqual(check_number("7"), 7)
        self.assertEqual(check_number("https://github.com/acme/hub/pull/12", "https://github.com/acme/hub.git"), 12)
        self.assertEqual(check_number("github.com/Acme/Hub/pull/12/", "git@github.com:acme/hub.git"), 12)
        with self.assertRaises(catalog.Usage):
            check_number("https://github.com/acme/other/pull/12", "https://github.com/acme/hub.git")

    def test_a_folder_without_an_origin_has_no_pull_request_to_read(self):
        repo, work = self.origin()
        gitfixture.git("-C", str(work), "remote", "remove", "origin")
        code, out, err = self.check(work, "--pr", "7")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("has no origin remote: --pr fetches the pull request from it", err)

    def test_it_reads_a_folder_and_not_an_added_catalog(self):
        fixtures.install(self, PARTNER)
        project.forget()
        self.assertEqual(self.run_tool("check", PARTNER, "--pr", "7"),
                         (2, "", "catalog.py: --pr reads a pull request of the origin of a folder, not an added catalog\n"))


def check_number(spec, origin="https://github.com/acme/hub.git"):
    return catalog._pull_number(spec, origin)


class Online(Folder):
    """What --online adds."""

    def test_the_audience_of_a_catalog_and_of_what_it_requires_is_measured_with_a_stranger_s_ls_remote(self):
        asked = []

        def ls_remote(address, branch=None, anonymous=False, timeout=20):
            asked.append((address.display, anonymous))
            if "private" in address.url:
                raise registry.CatalogError("no_access", "No access.", 400)
            if "down" in address.url:
                raise registry.CatalogError("unreachable", "Cannot reach.", 502)
            return {"branch": "main", "head": "0" * 40}
        notes = []
        summary = {"requires_catalogs": {"a": {"url": "https://example.test/pub.git"}, "b": {"url": "https://example.test/private.git"},
                                         "c": {"url": "https://example.test/down.git"}, "d": {}}}
        gitfixture.require()
        folder = self.work / "hub"
        folder.mkdir()
        gitfixture.git("init", "--quiet", "--", str(folder))
        gitfixture.git("-C", str(folder), "remote", "add", "origin", "https://example.test/hub.git")
        isolated(self)
        with mock.patch.object(gitx, "ls_remote", ls_remote):
            found = catalog._audiences(str(folder), "hub", summary, notes)
        self.assertEqual(found, {"hub": "public", "a": "public", "b": "private"})
        self.assertTrue(all(anonymous for _, anonymous in asked), "as a stranger would")
        self.assertEqual(notes, ["T24: whether c is public could not be measured, so what it includes was not compared with that",
                                 "T24: whether d is public could not be measured, so what it includes was not compared with that"])

    def test_a_catalog_that_requires_nothing_is_judged_on_no_audience_and_asks_nothing(self):
        notes = []
        with mock.patch.object(gitx, "ls_remote", side_effect=AssertionError("a request to the network")):
            self.assertEqual(catalog._audiences(str(self.work), "hub", {"requires_catalogs": {}}, notes), {})
            self.assertEqual(catalog._audiences(str(self.work), "hub", {}, notes), {})
        self.assertEqual(notes, [])

    @COMPOSE
    def test_a_public_catalog_that_includes_a_private_one_is_blocked_only_when_the_audience_is_measured(self):
        fixtures.install(self, PARTNER)
        folder = self.catalog("fixture")
        gitfixture.require()
        gitfixture.git("init", "--quiet", "--", str(folder))
        gitfixture.git("-C", str(folder), "remote", "add", "origin", "https://example.test/fixture.git")
        isolated(self)
        project.forget()
        offline = [one for one in json.loads(self.check(folder, "--format", "json")[1]) if one["code"] == "T24"]
        self.assertEqual(offline, [], "offline everything is private, and a private catalog may include a private one")

        def ls_remote(address, branch=None, anonymous=False, timeout=20):
            if "partner" in address.url:
                raise registry.CatalogError("no_access", "No access.", 400)
            return {"branch": "main", "head": "0" * 40}
        with mock.patch.object(gitx, "ls_remote", ls_remote):
            found = [one for one in json.loads(self.check(folder, "--online", "--format", "json")[1]) if one["code"] == "T24"]
        self.assertEqual([one["path"] for one in found], ["setups/pair.yml"])

    def lab(self):
        """A Lab checkout of its own: its origin/main holds one commit, the release v1.0.0, and the checkout is at a second that main
        does not hold."""
        folder, main, side = scratch_lab(self)
        gitfixture.git("-C", str(folder), "tag", "v1.0.0", main)
        isolated(self)
        return folder, main, side

    def standard(self, ref, extra=None):
        return trees.ListTree.from_files({
            ".github/standard.json": json.dumps({"form": "inline", "lab": {"ref": ref, "repository": "acme/lab"}, "platforms": ["linux/amd64"],
                                                 "standard": 1}, indent=2) + "\n", **(extra or {})})

    def test_the_lab_pin_has_to_be_a_release_of_the_lab(self):
        folder, main, side = self.lab()
        notes = []
        with mock.patch.object(catalog, "LAB", folder):
            self.assertEqual(catalog._pin_problems(self.standard(main), notes), [])
            found = catalog._pin_problems(self.standard(side), notes)
        self.assertEqual(notes, [])
        self.assertEqual([(one.code, one.path, one.severity, one.line) for one in found], [("SD11", ".github/standard.json", "error", 4)])
        self.assertEqual(found[0].message, f"The Lab commit {side[:7]} is not a release of the Lab. Fix: pin the latest release "
                                           "(catalog.py upgrade --lab-ref latest).")

    def test_a_commit_of_the_labs_main_that_no_release_names_is_refused_too(self):
        folder, main, _ = self.lab()
        gitfixture.git("-C", str(folder), "tag", "-d", "v1.0.0")
        gitfixture.git("-C", str(folder), "tag", "nightly", main)
        with mock.patch.object(catalog, "LAB", folder):
            found = catalog._pin_problems(self.standard(main), [])
        self.assertEqual([(one.code, one.message) for one in found], [("SD11", f"The Lab commit {main[:7]} is not a release of the Lab. "
                                                                               "Fix: pin the latest release (catalog.py upgrade "
                                                                               "--lab-ref latest).")])

    def test_a_commit_the_lab_checkout_does_not_hold_is_said_and_is_no_finding(self):
        folder, main, side = self.lab()
        notes = []
        with mock.patch.object(catalog, "LAB", folder):
            self.assertEqual(catalog._pin_problems(self.standard("f" * 40), notes), [])
        self.assertEqual(notes, [f"SD11: the Lab checkout does not hold {'f' * 7}: update it with git pull, then run again"])

    def test_the_pin_of_a_reusable_caller_is_judged_the_same_way_and_another_repository_is_not(self):
        folder, main, side = self.lab()
        notes = []
        caller = (f"jobs:\n  ci:\n    uses: acme/lab/.github/workflows/catalog-ci.yml@{side}\n"
                  f"  other:\n    uses: someone/else/.github/workflows/x.yml@{'e' * 40}\n  third:\n    uses: actions/checkout@{'d' * 40}\n")
        tree = self.standard(main, {".github/workflows/ci.yml": caller})
        with mock.patch.object(catalog, "LAB", folder):
            found = catalog._pin_problems(tree, notes)
        self.assertEqual([(one.code, one.path, one.line) for one in found], [("SD11", ".github/workflows/ci.yml", 3)])
        self.assertEqual(notes, [])

    def test_no_standard_json_and_a_bad_one_have_no_pin_to_judge(self):
        notes = []
        for files in ({}, {".github/standard.json": "{"}, {".github/standard.json": "[]"}, {".github/standard.json": '{"lab": 1}'},
                      {".github/standard.json": '{"lab": {"ref": "main"}}'}, {".github/standard.json": " " * 5000}):
            with self.subTest(files=files):
                self.assertEqual(catalog._pin_problems(trees.ListTree.from_files(files), notes), [])
        self.assertEqual(notes, [])

    def test_a_lab_checkout_with_no_origin_main_is_said(self):
        gitfixture.require()
        isolated(self)
        folder = self.work / "lab"
        folder.mkdir()
        gitfixture.git("init", "--quiet", "-b", "main", "--", str(folder))
        (folder / "x").write_text("x\n")
        gitfixture.git("-C", str(folder), "add", "-A")
        gitfixture.git("-C", str(folder), "commit", "--quiet", "-m", "One")
        sha = gitfixture.git("-C", str(folder), "rev-parse", "HEAD").strip()
        notes = []
        with mock.patch.object(catalog, "LAB", folder):
            self.assertEqual(catalog._pin_problems(self.standard(sha), notes), [])
        self.assertEqual(notes, ["SD11: the Lab checkout has no origin/main to compare with"])


class Download(unittest.TestCase):
    """What the fetch of a pull request reads of git's progress."""

    def test_the_line_that_passes_the_limit_stops_it_however_the_text_arrives(self):
        with mock.patch.object(bare, "STORE_LIMIT", 5 * 1024 * 1024):
            feed = catalog._watch_download()
            feed("remote: Counting objects: 100% (10/10), done.\nReceiving objects:  10% (1/10), 1.00 MiB | 2.00 MiB/s\r")
            feed("Receiving objects: 100% (4/4), done.\n")
            feed("Receiving objects:  50% (5/10), 6.00 M")
            with self.assertRaises(catalog._TooBig):
                feed("iB | 2.00 MiB/s\r")
            with self.assertRaises(catalog._TooBig):
                catalog._watch_download()("Unpacking objects:  99% (9/10), 1.00 GiB | 9.00 MiB/s\n")

    def test_a_line_that_says_no_size_or_a_small_one_is_never_a_reason_to_stop(self):
        feed = catalog._watch_download()
        for text in ("Receiving objects:  50% (5/10)\r", "Receiving objects:  60% (6/10), 12.00 KiB | 2.00 MiB/s\r", "garbage\r\n\n\r", ""):
            feed(text)

    def test_a_megabyte_with_no_end_of_line_is_read_in_under_a_second_and_kept_small(self):
        feed = catalog._watch_download()
        started = time.process_time()
        for _ in range(16):
            feed("x" * (64 * 1024))
        self.assertLess(time.process_time() - started, 1.0)


class Hostile(Folder):
    """A line of a megabyte, in whatever a stranger wrote or chose, is read in under a second by every reader of this command."""

    MEGABYTE = 1024 * 1024

    def quick(self, function, *args):
        """Runs a reader and says it took under a second of the processor: the time the machine gave to other programs while it
        waited (a loaded runner, a laptop that builds) is not a reader being slow."""
        started = time.process_time()
        try:
            function(*args)
        except catalog.Usage:   # a refusal is an answer
            pass
        took = time.process_time() - started
        self.assertLess(took, 1.0, f"{function.__name__} took {took:.2f} s of processor time")

    def test_the_readers_of_a_line(self):
        line = "a" * self.MEGABYTE
        folders = ["/tmp/x", "/Users/me", str(self.work)]
        self.quick(check._hidden, line, folders)
        self.quick(check._hidden, "/tmp/x/" * (self.MEGABYTE // 7), folders)
        for text in (line, "Reads settings that are not its own: " + line, "Same name as " + line, "Needs " + line + ", which is blocked"):
            self.quick(check._blocked, text, ["T20", "T21", "T25", "T26"], "hub", "a.yml")
        self.quick(catalog._tidy, line + "\n" + "     " + line + "\n" + line, [])
        self.quick(catalog._codes, [line])
        self.quick(catalog._codes, [",".join(["ST05"] * 100000)])
        self.quick(catalog._pull_number, "1" * self.MEGABYTE, "https://github.com/a/b.git")
        self.quick(catalog._pull_number, "https://github.com/a/b/pull/" + line, "https://github.com/a/b.git")
        self.quick(catalog._line_of, line + "\n" + line, "zzz")
        self.quick(lambda: check.as_problems([("a.yml", "Blocked: " + line), ("b.yml", line)], {"addons": {}, "setups": {}, "playgrounds": {}}, "hub",
                                             ("/tmp/x",)))

    def test_the_target_of_a_check(self):
        line = "x" * self.MEGABYTE
        self.assertEqual(self.run_tool("check", line)[0], 2)
        self.assertEqual(self.run_tool("check", "-" * 300)[0], 2)

    def test_the_files_of_a_folder(self):
        gitfixture.require()
        folder = self.work / "big"
        (folder / ".github/workflows").mkdir(parents=True)
        line = "a" * self.MEGABYTE
        (folder / "catalog.yml").write_text("x-gravitee-catalog:\n  schema: 1\n  id: big\n  description: " + line + "\n")
        (folder / ".github/standard.json").write_text(json.dumps({"lab": {"ref": "a" * 40, "repository": "o/r"}}) + "\n")
        (folder / ".github/workflows/ci.yml").write_text("jobs:\n  a:\n    uses: " + line + "\n")
        self.quick(check.folder_summary, folder)
        with mock.patch.object(catalog, "_on_main", return_value="yes"):
            self.quick(catalog._pin_problems, trees.FolderTree(folder), [])


@COMPOSE
class DayOne(Folder):
    """A catalog made by `new` runs the commands of its own static job (ci.yml) on its first push, from the folder it is checked out
    in: `check catalog --ci --base BASE` with the zeros GitHub gives as `before`, and `index catalog --out catalog.json`."""

    def setUp(self):
        gitfixture.require()
        super().setUp()
        isolated(self)
        project.forget()
        try:
            importlib.import_module("lab_ui.catalogs.standard")
        except ImportError:
            self.skipTest("lab_ui.catalogs.standard is not in this checkout yet, so `new` renders nothing")
        self.repo = self.work / "repo"
        folder = self.repo / "catalog"
        code, out, err = self.run_tool("new", str(folder), "--id", "newcat", "--name", "New cat", "--maintainers", "@acme/maintainers",
                                       "--lab-repo", "acme/lab", "--lab-ref", "ab" * 20)
        self.assertEqual((code, err), (0, ""), out)
        gitfixture.git("init", "--quiet", "-b", "main", "--", str(folder))
        gitfixture.git("-C", str(folder), "add", "-A")
        gitfixture.git("-C", str(folder), "commit", "--quiet", "-m", "Create the catalog")
        before = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, before)

    def test_the_check_of_the_first_push_is_all_good(self):
        code, out, err = self.run_tool("check", "catalog", "--ci", "--base", "0" * 40)
        self.assertEqual((code, out, err), (0, "", ""))
        code, out, err = self.run_tool("check", "catalog", "--base", "0" * 40)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(lines(out)[0], "Reading the files and the 1 commit of the whole history")
        self.assertEqual(lines(out)[-1], "All good")

    def test_the_index_step_makes_the_catalog_json_the_release_step_reads(self):
        self.assertEqual(self.run_tool("index", "catalog", "--out", "catalog.json"), (0, "", ""))
        found = json.loads((self.repo / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual((found["schema"], found["catalog"]["id"], found["standard"]), (1, "newcat", standard.LEVEL))
        self.assertEqual([(one["kind"], one["id"]) for one in found["units"]], [("addon", "hello")])
        os.chdir(self.repo / "catalog")   # `make index`
        self.assertEqual(self.run_tool("index", ".", "--out", "catalog.json"), (0, "", ""))
        self.assertEqual((self.repo / "catalog" / "catalog.json").read_text(encoding="utf-8"), json.dumps(found, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

    def test_nothing_is_linked_into_the_lab_and_nothing_is_written_by_the_two_steps(self):
        before = (snapshot(self.root), contents(self.root))
        self.run_tool("check", "catalog", "--ci", "--base", "0" * 40)
        self.run_tool("index", "catalog", "--out", str(self.work / "catalog.json"))
        self.assertEqual((snapshot(self.root), contents(self.root)), before)


@COMPOSE
class Today(Folder):
    """The content the engine holds today, as a catalog: the rules of the standard find in it what they found when they were
    calibrated (: 56 GD01 errors and the DF22 and DF23 warnings of the Dockerfiles), and the layout that is not yet the
    installed one (the extraction of a catalog rewrites it): the errors of the paths that reach the seeder and the plugin folders
    with `../../`, which the extracted layout writes with five."""

    def tracked(self):
        try:
            done = subprocess.run(["git", "ls-files", "-z", "--", "addons", "setups", "playgrounds"], cwd=REPO, capture_output=True,
                                  timeout=60)
            names = [name.decode() for name in done.stdout.split(b"\0") if name] if done.returncode == 0 else []
        except (OSError, subprocess.SubprocessError):
            names = []
        return [name for name in names if (REPO / name).is_file() and not (REPO / name).is_symlink()]

    def test_the_committed_content_has_the_gd01_findings_of_the_calibration_and_the_items_of_the_old_layout(self):
        names = self.tracked()
        if not names:
            self.skipTest("this checkout holds no committed addons, setups or playgrounds")
        folder = self.work / "content"
        for name in names:
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, folder / name)
        default = json.loads((REPO / "catalogs.default.json").read_text(encoding="utf-8"))["catalogs"][0]
        (folder / "catalog.yml").write_text(
            f"x-gravitee-catalog:\n  schema: 1\n  id: {default['id']}\n  name: {default['name']}\n  description: The content\n"
            f"  version: 1.0.0\n  lab: 1\n  images: {fixtures.seeder_image(self).rsplit('/', 1)[0]}\n"
            "  shared_settings: [GEMINI_API_KEY, OLLAMA_URL]\n", encoding="utf-8")
        (folder / "README.md").write_text("# Content\n", encoding="utf-8")
        shutil.copy(REPO / "catalogs.default.json", self.root / "catalogs.default.json")
        code, out, err = self.check(folder, "--format", "json")
        self.assertEqual(err, "")
        found = json.loads(out)
        errors = [one for one in found if one["severity"] == "error"]
        self.assertEqual({one["code"] for one in found if one["code"]} - {"GD01", "DF22", "DF23", "T18", "T28"}, set())
        self.assertEqual(len([one for one in errors if one["code"] == "GD01"]), 56)
        # Everything else is the one thing the extraction changes: the seeder and the plugin folders are reached with ../../, which
        # is the engine's layout, and not the five folders of a catalog that is installed
        rest = [one for one in errors if one["code"] != "GD01"]
        self.assertTrue(rest)
        for one in rest:
            self.assertTrue(one["code"] in ("T18", "T28") or "outside the folder of this unit" in one["message"]
                            or "mounts plugins/apim of this machine" in one["message"], one)
        self.assertEqual([one for one in found if one["code"].startswith("IM")], [], "the seeder is the one named anchor")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
