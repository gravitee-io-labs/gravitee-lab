"""The matrix of the end-to-end run: the command the workflow runs, and what it prints for the next job to read. No Git, no
Compose: the project is given."""

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

# base is the catalog the engine lists first, hub another
HUB = {"addons": {"echo": {"catalog": "base"}, "@hub/ledger": {"catalog": "hub"}, "local/mine": {"catalog": "local"}},
       "setups": {"@hub/duo": {"catalog": "hub"}},
       "playgrounds": {"shop": {"catalog": "base", "tested": [{"APIM_VERSION": "4.13.0"}, {"APIM_VERSION": "4.14.0"}]},
                       "local/draft": {"catalog": "local"}}}


class MainTest(unittest.TestCase):
    def setUp(self):
        e2e = e2e_matrix.e2e
        self.canary = ["addon:echo"]
        for target, name, new in ((e2e.paths, "default_ns", lambda: "base"),
                                  (e2e.registry, "entry", lambda ns: {"id": ns} if ns == "base" else None),
                                  (e2e.manifest, "read_catalog", lambda path: {"canary": self.canary})):
            patcher = mock.patch.object(target, name, new)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_main(self, *argv):
        out = io.StringIO()
        with mock.patch.object(e2e_matrix, "load_project", return_value=HUB), contextlib.redirect_stdout(out):
            e2e_matrix.main(list(argv))
        return json.loads(out.getvalue())

    def test_it_prints_the_matrix_as_json(self):
        found = self.run_main("--canary")
        self.assertEqual(found["count"], 1)
        self.assertEqual(found["matrix"]["include"][0]["name"], "addon:echo (core defaults)")

    def test_it_appends_what_the_next_job_reads_to_the_output_file(self):
        with tempfile.NamedTemporaryFile("r", suffix=".out") as file, mock.patch.dict(os.environ, {"GITHUB_OUTPUT": file.name}):
            self.run_main("--github-output", "--items", "--", "playground:shop")
            lines = file.read().splitlines()
        self.assertEqual(lines[1], "count=2")
        self.assertEqual(json.loads(lines[0].removeprefix("matrix="))["include"][1]["tested"], "1")

    def test_a_change_of_what_every_item_shares_tries_the_canary_and_anything_else_tries_nothing(self):
        for files, wanted in ((["seeder/seed.py", "docs/a.md"], 1), (["docs/a.md"], 0)):
            with self.subTest(files=files), mock.patch.object(e2e_matrix.e2e, "changed_files", return_value=files) as asked:
                self.assertEqual(self.run_main("--changed", "a", "b")["count"], wanted)
                asked.assert_called_once_with(e2e_matrix.ROOT, "a", "b")

    def test_what_the_plan_refuses_ends_the_command_and_no_matrix_is_printed(self):
        for argv, said in ((["--items", "--", "addon:@hub/ledger"], "is not a shared item of base"),
                           (["--items", "--", "playground:local/draft"], "is not a shared item of base")):
            out = io.StringIO()
            with self.subTest(argv=argv), mock.patch.object(e2e_matrix, "load_project", return_value=HUB), contextlib.redirect_stdout(out), \
                    self.assertRaises(SystemExit) as caught:
                e2e_matrix.main(argv)
            self.assertIn(said, str(caught.exception))
            self.assertEqual(out.getvalue(), "", "no matrix, so no empty one for the next job to take for a pass")

    def test_a_catalog_with_no_canary_is_an_error_and_never_an_empty_matrix(self):
        self.canary = []
        for argv in (["--canary"], ["--changed", "a", "b"]):
            with self.subTest(argv=argv), mock.patch.object(e2e_matrix, "load_project", return_value=HUB), \
                    mock.patch.object(e2e_matrix.e2e, "changed_files", return_value=["seeder/seed.py"]), self.assertRaises(SystemExit) as caught:
                e2e_matrix.main(argv)
            self.assertIn("The catalog base names no canary, so there is nothing to try", str(caught.exception))

    def test_a_catalog_that_is_not_linked_stops_the_plan_with_how_to_link_it(self):
        with mock.patch.object(e2e_matrix.e2e.registry, "entry", lambda ns: None), self.assertRaises(SystemExit) as caught, \
                mock.patch.object(e2e_matrix, "load_project", return_value=HUB):
            e2e_matrix.main(["--canary"])
        self.assertIn("neither installed nor linked", str(caught.exception))

    def test_a_project_that_cannot_be_read_is_a_failure_and_never_an_empty_matrix(self):
        for failure in (e2e_matrix.e2e.compose.ComposeError("services.x.image must be a string"), OSError("disk\nfull")):
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(e2e_matrix, "load_project", side_effect=failure), self.assertRaises(SystemExit) as caught:
                    e2e_matrix.main(["--canary"])
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

    def test_what_to_try(self):
        args = self.read("--changed", "abc", "HEAD", "--github-output")
        self.assertEqual((args.changed, args.github_output), (["abc", "HEAD"], True))
        self.assertTrue(self.read("--canary").canary)

    def test_words_after_two_dashes_are_items_and_never_an_option(self):
        self.assertEqual(self.read("--items", "--", "addon:a", "--canary", "-x").items, ["addon:a", "--canary", "-x"])
        self.assertEqual(self.read("--items", "addon:a", "--", "addon:b").items, ["addon:a", "addon:b"])

    def test_what_makes_no_sense_is_refused(self):
        for argv, said in (([], "required"), (["--items"], "at least one item"), (["--canary", "--changed", "a", "b"], "not allowed with"),
                           (["--canary", "--", "addon:a"], "words after --"), (["--changed", "a"], "expected 2")):
            with self.subTest(argv=argv):
                self.assertIn(said, self.refused(*argv))


class WordsTest(unittest.TestCase):
    def test_the_engine_names_no_item(self):
        """What a workflow, a script or the module of the Lab UI says of an item is `kind:id` with a placeholder, never the name of
        one: the items are a catalog's."""
        root = e2e_matrix.ROOT
        owned = ["lab-ui/lab_ui/e2e.py", ".github/scripts/e2e_matrix.py", ".github/scripts/image_manifests.py",
                 ".github/workflows/e2e.yml"]
        item = re.compile(r"\b(?:addon|setup|playground):[a-z@]|\b(?:addons|setups|playgrounds)/[a-z]")
        for name in owned:
            for number, line in enumerate((root / name).read_text(encoding="utf-8").splitlines(), 1):
                with self.subTest(file=name, line=number):
                    self.assertIsNone(item.search(line), line)


if __name__ == "__main__":
    unittest.main()
