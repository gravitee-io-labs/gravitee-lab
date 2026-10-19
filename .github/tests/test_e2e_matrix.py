"""The matrix of the end-to-end run: which items a change touches, the jobs of each, and what is printed for the next job to
read. No Git, no Compose: the changes and the project are given."""

import argparse
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import e2e_matrix  # noqa: E402

PROJECT = {"addons": {"mailer": {}, "beacon": {}, "local/mine": {}}, "setups": {"events": {}},
           "playgrounds": {"shop": {"tested": [{"APIM_VERSION": "4.13.0"}, {"APIM_VERSION": "4.14.0"}]}, "tour": {"tested": []},
                           "local/draft": {}}}


class ItemOfTest(unittest.TestCase):
    def test_a_file_of_an_add_on_or_a_playground_belongs_to_its_folder(self):
        self.assertEqual(e2e_matrix.e2e.item_of("addons/mailer/compose.yml"), "addon:mailer")
        self.assertEqual(e2e_matrix.e2e.item_of("addons/mailer/certs/generate.sh"), "addon:mailer")
        self.assertEqual(e2e_matrix.e2e.item_of("playgrounds/shop/apps/web/server.py"), "playground:shop")

    def test_a_setup_is_one_file(self):
        self.assertEqual(e2e_matrix.e2e.item_of("setups/events.yml"), "setup:events")
        self.assertIsNone(e2e_matrix.e2e.item_of("setups/README.md"))
        self.assertIsNone(e2e_matrix.e2e.item_of("setups/events.yml.bak"))

    def test_what_belongs_to_a_machine_and_what_belongs_to_no_item_are_none(self):
        for path in ("addons/local/mine/compose.yml", "playgrounds/local/draft/guide.md", "setups/local/mine.yml", "README.md",
                     "addons/README.md", "docs/lab-ui.md", "addons/Mailer/compose.yml", "addons/mailer"):
            with self.subTest(path=path):
                self.assertIsNone(e2e_matrix.e2e.item_of(path))


class JobsTest(unittest.TestCase):
    def sets(self, item):
        return len(PROJECT[f"{item.partition(':')[0]}s"][item.partition(':')[2]].get("tested") or [])

    def test_a_job_for_each_set_of_versions_of_an_item(self):
        self.assertEqual(e2e_matrix.jobs(["playground:shop"], self.sets), [
            {"item": "playground:shop", "tested": "0", "name": "playground:shop (versions 0)"},
            {"item": "playground:shop", "tested": "1", "name": "playground:shop (versions 1)"}])

    def test_an_item_that_says_no_versions_runs_once_on_the_defaults_of_the_core(self):
        self.assertEqual(e2e_matrix.jobs(["playground:tour", "addon:mailer"], self.sets), [
            {"item": "playground:tour", "tested": "", "name": "playground:tour (core defaults)"},
            {"item": "addon:mailer", "tested": "", "name": "addon:mailer (core defaults)"}])

    def test_no_item_no_job(self):
        self.assertEqual(e2e_matrix.jobs([], self.sets), [])


# The catalogs: the plan of a catalog linked into the engine, which the workflows ask for

HUB = {"addons": {"echo": {"catalog": "base"}, "@hub/ledger": {"catalog": "hub"}, "local/mine": {"catalog": "local"}},
       "setups": {"@hub/duo": {"catalog": "hub"}},
       "playgrounds": {"shop": {"catalog": "base", "tested": [{"APIM_VERSION": "4.13.0"}, {"APIM_VERSION": "4.14.0"}]},
                       "@hub/bank": {"catalog": "hub", "tested": [{"APIM_VERSION": "4.13.0"}]}, "local/draft": {"catalog": "local"}}}


def patch_catalogs(case):
    """The catalogs of the engine: `base` is the first one listed, `hub` another, each with its canary."""
    e2e = e2e_matrix.e2e
    for target, name, new in ((e2e.paths, "default_ns", lambda: "base"),
                              (e2e.registry, "entry", lambda ns: {"id": ns} if ns in ("base", "hub") else None),
                              (e2e.manifest, "read_catalog", lambda path: {"canary": ["playground:bank"] if "/hub/" in path else ["addon:echo"]})):
        patcher = mock.patch.object(target, name, new)
        patcher.start()
        case.addCleanup(patcher.stop)


class CatalogTest(unittest.TestCase):
    """--catalog ID, --default-catalog: the items of one catalog, with the ids the Lab gives them."""

    def setUp(self):
        patch_catalogs(self)

    def args(self, **more):
        return argparse.Namespace(**{"catalog": None, "default_catalog": False, "where": "catalog", "all": False, "canary": False,
                                     "items": None, "changed": None, **more})

    def items(self, found):
        return [job["item"] for job in found["matrix"]["include"]]

    def test_every_item_of_the_catalog_for_the_nightly_run(self):
        found = e2e_matrix.matrix(self.args(catalog="hub", all=True), HUB)
        self.assertEqual(self.items(found), ["addon:@hub/ledger", "setup:@hub/duo", "playground:@hub/bank"])

    def test_the_default_catalog_is_the_first_one_the_engine_lists_and_its_ids_are_bare(self):
        found = e2e_matrix.matrix(self.args(default_catalog=True, all=True), HUB)
        self.assertEqual(self.items(found), ["addon:echo", "playground:shop", "playground:shop"])
        self.assertEqual(found["count"], 2 + 1)

    def test_the_canary_of_catalog_yml(self):
        self.assertEqual(self.items(e2e_matrix.matrix(self.args(catalog="hub", canary=True), HUB)), ["playground:@hub/bank"])
        self.assertEqual(self.items(e2e_matrix.matrix(self.args(default_catalog=True, canary=True), HUB)), ["addon:echo"])

    def test_named_items_are_those_of_the_catalog(self):
        found = e2e_matrix.matrix(self.args(catalog="hub", items=["playground:@hub/bank"]), HUB)
        self.assertEqual(self.items(found), ["playground:@hub/bank"])
        with self.assertRaises(e2e_matrix.e2e.PlanError) as caught:
            e2e_matrix.matrix(self.args(catalog="hub", items=["addon:echo"]), HUB)
        self.assertIn("is not a shared item of hub", str(caught.exception))
        with mock.patch.object(e2e_matrix, "load_project", return_value=HUB), self.assertRaises(SystemExit) as stopped:
            e2e_matrix.main(["--catalog", "hub", "--items", "--", "addon:echo"])   # the command ends with the same words
        self.assertIn("is not a shared item of hub", str(stopped.exception))

    def test_a_change_in_the_catalog_is_read_in_the_folder_of_the_catalog(self):
        asked = []

        def changes(base, head, folder=None):
            asked.append((base, head, folder))
            return ["playgrounds/bank/guide.md", "README.md"]
        with mock.patch.object(e2e_matrix, "git_changes", changes):
            found = e2e_matrix.matrix(self.args(catalog="hub", changed=["a", "b"]), HUB)
        self.assertEqual(self.items(found), ["playground:@hub/bank"])
        self.assertEqual(asked, [("a", "b", e2e_matrix.ROOT / ".lab/catalogs/hub")])

    def test_a_change_in_the_engine_is_read_in_the_engine_and_runs_the_canary(self):
        asked = []

        def changes(base, head, folder=None):
            asked.append(folder)
            return ["seeder/seed.py"]
        with mock.patch.object(e2e_matrix, "git_changes", changes):
            found = e2e_matrix.matrix(self.args(catalog="hub", changed=["a", "b"], where="engine"), HUB)
        self.assertEqual(self.items(found), ["playground:@hub/bank"])
        self.assertEqual(asked, [e2e_matrix.ROOT])

    def test_a_catalog_that_is_not_linked_stops_the_plan_with_how_to_link_it(self):
        with self.assertRaises(SystemExit) as caught:
            e2e_matrix.main(["--catalog", "other", "--all"])
        self.assertIn("neither installed nor linked", str(caught.exception))

    def test_an_item_that_does_not_load_stops_the_plan_and_no_matrix_is_printed(self):
        broken = {**HUB, "catalog_problems": {"hub": [{"display": "addons/mailer/compose.yml", "message": "Cannot be read: go-yaml load error"}]}}
        out = io.StringIO()
        with mock.patch.object(e2e_matrix, "load_project", return_value=broken), \
                mock.patch.object(e2e_matrix, "git_changes", return_value=["addons/mailer/compose.yml"]), \
                contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            e2e_matrix.main(["--catalog", "hub", "--changed", "a", "b", "--github-output"])
        self.assertEqual(str(caught.exception), "addon:mailer of hub cannot be tried, its files do not load: Cannot be read: go-yaml load "
                                                "error. Fix: catalog.py check hub")
        self.assertEqual(out.getvalue(), "", "no matrix, so no empty one for the next job to take for a pass")

    def test_a_catalog_with_no_canary_is_an_error_for_the_canary_and_the_engine_and_a_warning_for_its_own_change(self):
        patcher = mock.patch.object(e2e_matrix.e2e.manifest, "read_catalog", lambda path: {"canary": []})
        patcher.start()
        self.addCleanup(patcher.stop)
        for argv in (["--default-catalog", "--canary"], ["--default-catalog", "--changed", "a", "b", "--in", "engine"]):
            with self.subTest(argv=argv), mock.patch.object(e2e_matrix, "load_project", return_value=HUB), \
                    mock.patch.object(e2e_matrix, "git_changes", return_value=["seeder/seed.py"]), self.assertRaises(SystemExit) as caught:
                e2e_matrix.main(argv)
            self.assertIn("The catalog base names no canary, so there is nothing to try", str(caught.exception))
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(e2e_matrix, "load_project", return_value=HUB), mock.patch.object(e2e_matrix, "git_changes", return_value=["Makefile"]), \
                mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            e2e_matrix.main(["--default-catalog", "--changed", "a", "b"])
        self.assertEqual(json.loads(out.getvalue()), {"matrix": {"include": []}, "count": 0}, "standard output is the matrix and only that")
        self.assertRegex(err.getvalue(), r"^::warning title=E2E canary::The catalog base names no canary, so a change of what its items "
                                         r"share tries nothing: add `canary: \[kind:name\]`.*\n$")

    def test_what_the_plan_has_to_say_is_a_warning_on_github_and_a_line_elsewhere(self):
        for github, wanted in (("true", "::warning title=E2E canary::50%25 of it%0Aand more\n"), ("", "Warning: 50% of it\nand more\n")):
            err = io.StringIO()
            with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": github}), contextlib.redirect_stderr(err):
                e2e_matrix.announce("50% of it\nand more")
            self.assertEqual(err.getvalue(), wanted)


class MainTest(unittest.TestCase):
    def setUp(self):
        patch_catalogs(self)

    def run_main(self, *argv):
        out = io.StringIO()
        with mock.patch.object(e2e_matrix, "load_project", return_value=HUB), contextlib.redirect_stdout(out):
            e2e_matrix.main(list(argv))
        return json.loads(out.getvalue())

    def test_it_prints_the_matrix_as_json(self):
        found = self.run_main("--catalog", "hub", "--all")
        self.assertEqual(found["count"], 3)
        self.assertEqual(found["matrix"]["include"][0]["name"], "addon:@hub/ledger (core defaults)")

    def test_it_appends_what_the_next_job_reads_to_the_output_file(self):
        with tempfile.NamedTemporaryFile("r", suffix=".out") as file, mock.patch.dict(os.environ, {"GITHUB_OUTPUT": file.name}):
            self.run_main("--default-catalog", "--all", "--github-output")
            lines = file.read().splitlines()
        self.assertEqual(lines[1], "count=3")
        self.assertEqual(json.loads(lines[0].removeprefix("matrix="))["include"][2]["tested"], "1")

    def test_a_project_that_cannot_be_read_is_a_failure_and_never_an_empty_matrix(self):
        for failure in (e2e_matrix.e2e.compose.ComposeError("services.x.image must be a string"), OSError("disk\nfull")):
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(e2e_matrix, "load_project", side_effect=failure), self.assertRaises(SystemExit) as caught:
                    e2e_matrix.main(["--catalog", "hub", "--all"])
                self.assertIn("The project cannot be read", str(caught.exception))
                self.assertNotIn("\n", str(caught.exception))

    def test_it_needs_to_be_told_what_to_read(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            e2e_matrix.main([])


class CommandTest(unittest.TestCase):
    """The command line: what it accepts, and what it refuses before it reads anything."""

    def read(self, *argv):
        with contextlib.redirect_stderr(io.StringIO()):
            return e2e_matrix.read(list(argv))

    def refused(self, *argv):
        with contextlib.redirect_stderr(io.StringIO()) as said, self.assertRaises(SystemExit) as caught:
            e2e_matrix.read(list(argv))
        self.assertEqual(caught.exception.code, 2)
        return said.getvalue()

    def test_a_catalog_and_what_to_try_in_it(self):
        args = self.read("--catalog", "hub", "--changed", "abc", "HEAD", "--in", "engine", "--github-output")
        self.assertEqual((args.catalog, args.changed, args.where, args.github_output), ("hub", ["abc", "HEAD"], "engine", True))
        self.assertTrue(self.read("--default-catalog", "--canary").canary)
        self.assertTrue(self.read("--catalog", "hub", "--all").all)

    def test_words_after_two_dashes_are_items_and_never_an_option(self):
        self.assertEqual(self.read("--catalog", "hub", "--items", "--", "addon:a", "--all", "-x").items, ["addon:a", "--all", "-x"])
        self.assertEqual(self.read("--catalog", "hub", "--items", "addon:a", "--", "addon:b").items, ["addon:a", "addon:b"])

    def test_what_makes_no_sense_is_refused(self):
        for argv, said in ((["--catalog", "hub"], "required"), (["--catalog", "hub", "--items"], "at least one item"),
                           (["--catalog", "hub", "--all", "--canary"], "not allowed with"),
                           (["--catalog", "hub", "--default-catalog", "--all"], "not allowed with"),
                           (["--canary"], "name the catalog"), (["--all"], "name the catalog"),
                           (["--all", "--in", "engine"], "name the catalog"),
                           (["--catalog", "hub", "--all", "--in", "engine"], "--in engine is for --changed of a catalog"),
                           (["--catalog", "hub", "--triggered", "a", "b"], "takes no catalog"),
                           (["--all", "--", "addon:a"], "words after --"), (["--catalog", "hub", "--changed", "a"], "expected 2")):
            with self.subTest(argv=argv):
                self.assertIn(said, self.refused(*argv))


class TriggeredTest(unittest.TestCase):
    """--triggered: whether a change of the engine is of what every item shares, for the jobs of the engine's CI."""

    def run_main(self, files, *argv):
        out = io.StringIO()
        with mock.patch.object(e2e_matrix, "git_changes", return_value=files), contextlib.redirect_stdout(out):
            e2e_matrix.main(["--triggered", "a", "b", *argv])
        return json.loads(out.getvalue())

    def test_a_change_of_what_every_item_shares_is_one(self):
        self.assertEqual(self.run_main(["docs/a.md", "seeder/seed.py", "Makefile"]), {"triggered": True, "paths": ["seeder/seed.py", "Makefile"]})

    def test_documentation_and_the_page_are_not(self):
        self.assertEqual(self.run_main(["docs/a.md", "lab-ui/web/js/core.js"]), {"triggered": False, "paths": []})

    def test_the_answer_is_for_the_next_job_to_read(self):
        with tempfile.NamedTemporaryFile("r", suffix=".out") as file, mock.patch.dict(os.environ, {"GITHUB_OUTPUT": file.name}):
            self.run_main(["Makefile"], "--github-output")
            self.assertEqual(file.read(), "triggered=true\n")
        with tempfile.NamedTemporaryFile("r", suffix=".out") as file, mock.patch.dict(os.environ, {"GITHUB_OUTPUT": file.name}):
            self.run_main(["docs/a.md"], "--github-output")
            self.assertEqual(file.read(), "triggered=false\n")

    def test_the_paths_are_the_ones_the_plan_uses(self):
        self.assertIs(e2e_matrix.SHARED, e2e_matrix.e2e.ENGINE_PATHS)


class WordsTest(unittest.TestCase):
    def test_the_engine_names_no_item(self):
        """What a workflow, a script or the module of the Lab UI says of an item is `kind:id` with a placeholder, never the name of
        one: the items are a catalog's."""
        root = e2e_matrix.ROOT
        owned = ["lab-ui/lab_ui/e2e.py", ".github/scripts/e2e_matrix.py", ".github/scripts/image_manifests.py",
                 ".github/workflows/e2e.yml", ".github/workflows/e2e-run.yml"]
        item = re.compile(r"\b(?:addon|setup|playground):[a-z@]|\b(?:addons|setups|playgrounds)/[a-z]")
        for name in owned:
            for number, line in enumerate((root / name).read_text(encoding="utf-8").splitlines(), 1):
                with self.subTest(file=name, line=number):
                    self.assertIsNone(item.search(line), line)


if __name__ == "__main__":
    unittest.main()
