"""The files a test reads: the sample of the engine, and the guard that keeps the engine's tests off the content of the
repository; and the reading of the seed service, which is seedlint's."""

import ast
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import yaml

from lab_seeder import common
from lab_seeder import seedlint
from lab_seeder.seedlint import seeded_playgrounds
from lab_seeder.testkit import SAMPLES

from . import chained, doubling

# A string that is a path into the content of the repository, as a folder or as a part of a path: a test reads a file with
# ROOT / "playgrounds" as well as with ROOT.glob("addons/*/compose.yml")
CONTENT = re.compile(r"(?:^|[/\\])(?:addons|setups|playgrounds)(?:[/\\]|$)")


def content_paths(source):
    """The strings of a Python source that name a folder of content of the repository, in any spelling of a path. A string
    with a space is a sentence (a docstring, a message), not a path: it is left out."""
    return sorted({node.value for node in ast.walk(ast.parse(source))
                   if isinstance(node, ast.Constant) and isinstance(node.value, str)
                   and not re.search(r"\s", node.value) and CONTENT.search(node.value)})


def readers_of_content(folder):
    """{file: the paths it names} of the test files of `folder`, this one apart, that name a folder of content."""
    found = {}
    for file in sorted(Path(folder).glob("test_*.py")):
        names = [] if file.name == Path(__file__).name else content_paths(file.read_text(encoding="utf-8"))
        if names:
            found[file.name] = names
    return found


class Sample(unittest.TestCase):

    def test_the_sample_is_two_playgrounds_that_each_have_a_seed_folder_and_a_seed_service(self):
        found = {folder.name: environment for folder, environment in seeded_playgrounds(SAMPLES)}
        self.assertEqual(sorted(found), ["acme-bakery", "acme-hostels"])
        for name, environment in found.items():
            with self.subTest(playground=name):
                self.assertTrue(environment, "its seed service sets variables")

    def test_the_sample_holds_playgrounds_only(self):
        self.assertEqual(sorted(path.name for path in SAMPLES.iterdir() if path.is_dir()), ["acme-bakery", "acme-hostels"])
        self.assertEqual([path.name for path in SAMPLES.iterdir() if not path.is_dir()], [])


CHECKS = "service: {labels: {io.gravitee.role: seed}, environment: {A: x}}"


class OneSetOfChecks(unittest.TestCase):

    def test_a_compose_file_is_read_with_the_guards_of_the_lint(self):
        """The compose file of a playground is a stranger's: a bomb of YAML, a link, a file that is not text are refused, and the
        loader of YAML does not run on the first."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bomb = root / "bomb.yml"
            bomb.write_text("x-bomb:\n" + doubling(22, "  ") + "services:\n  " + CHECKS + "\n", encoding="utf-8")
            started = time.process_time()
            with mock.patch.object(yaml.SafeLoader, "construct_document", side_effect=AssertionError("the loader ran on a bomb")):
                with self.assertRaisesRegex(common.SeedError, "merges mappings with << keys"):
                    seedlint.seed_service(bomb)
                bomb.write_text("x-bomb:\n" + chained(3000, "  "), encoding="utf-8")
                with self.assertRaisesRegex(common.SeedError, "merges mappings with << keys"):
                    seedlint.seed_service(bomb)
            self.assertLess(time.process_time() - started, 10.0)
            binary = root / "binary.yml"
            binary.write_bytes(b"services: \xff\xfe\n")
            with self.assertRaisesRegex(common.SeedError, "not UTF-8"):
                seedlint.seed_service(binary)

    def test_a_compose_file_that_is_a_link_is_not_followed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "real.yml").write_text("services:\n  " + CHECKS + "\n", encoding="utf-8")
            try:
                os.symlink(root / "real.yml", root / "linked.yml")
            except (OSError, NotImplementedError, AttributeError) as e:
                self.skipTest(f"this machine cannot make a link: {e}")
            self.assertIsNotNone(seedlint.seed_service(root / "real.yml"))
            with self.assertRaisesRegex(common.SeedError, "is a link"):
                seedlint.seed_service(root / "linked.yml")

    def test_a_service_and_its_settings_are_read_whichever_way_they_are_written(self):
        with tempfile.TemporaryDirectory() as folder:
            compose = Path(folder) / "compose.yml"
            compose.write_text("services:\n  init:\n    labels: [io.gravitee.role=seed]\n    environment:\n      - URL=${URL:-http://apim}\n"
                               "      - BARE\n", encoding="utf-8")
            self.assertEqual(seedlint.seed_environment(seedlint.seed_service(compose)), {"URL": "http://apim", "BARE": ""})


class NoContent(unittest.TestCase):

    def test_a_path_into_a_folder_of_content_is_found_in_any_spelling(self):
        for source in ('ROOT / "addons"', 'ROOT.glob("addons/*/compose.yml")', "(ROOT / 'playgrounds/acme-bakery/README.md').exists()",
                       'Path("..") / "setups"', 'f"{ROOT}/playgrounds/{name}"', 'open("../../setups/hybrid.yml")',
                       'ROOT / "play" "grounds"', 'Path(r"..\\addons\\kafka")', 'ROOT.glob("setups/*.yml")'):
            with self.subTest(source=source):
                self.assertTrue(content_paths(source))

    def test_a_sentence_or_a_name_that_only_looks_alike_is_not_a_path_into_a_folder_of_content(self):
        for source in ('"""The files of a playground, read under testkit.PLAYGROUNDS or in playgrounds/acme-bakery."""',
                       '"playground"', '"my-playgrounds"', '"playgrounds.py"', '"addons-and-more/x"', '"fixtures/samples/acme-bakery"',
                       '"read the playgrounds/ folder"'):
            with self.subTest(source=source):
                self.assertEqual(content_paths(source), [])

    def test_a_test_file_that_reads_the_content_is_found(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, source in {"test_exact.py": 'ROOT / "setups"\n', "test_glob.py": 'ROOT.glob("addons/*/compose.yml")\n',
                                 "test_part.py": '(ROOT / "playgrounds/acme-bakery/README.md").exists()\n',
                                 "test_clean.py": '"""Reads the playgrounds of a catalog."""\nROOT = testkit.PLAYGROUNDS\n',
                                 "helper.py": 'ROOT / "addons"\n'}.items():
                (Path(folder) / name).write_text(source, encoding="utf-8")
            self.assertEqual(readers_of_content(folder),
                             {"test_exact.py": ["setups"], "test_glob.py": ["addons/*/compose.yml"],
                              "test_part.py": ["playgrounds/acme-bakery/README.md"]})

    def test_no_test_names_a_content_folder_of_the_repository(self):
        """A test that wants the files of a playground reads them under testkit.PLAYGROUNDS: the engine runs where the three
        folders are not."""
        here = Path(__file__).resolve()
        self.assertGreater(len(list(here.parent.glob("test_*.py"))), 20)
        self.assertEqual(readers_of_content(here.parent), {}, "read the files of a playground under testkit.PLAYGROUNDS")


if __name__ == "__main__":
    unittest.main()
