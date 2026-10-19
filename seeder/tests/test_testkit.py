"""The part of testkit that says which files a test reads: the folder of playgrounds (a catalog's, or the sample of the
engine), the personal folder of this machine, the sample itself, and the guard that keeps the engine's tests off the
content of the repository; and the checks of the seed files and the reading of the seed
service, which are seedlint's and have no copy here."""

import ast
import inspect
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
from lab_seeder import testkit
from lab_seeder.testkit import SAMPLES, Seed, personal_root, playgrounds_root, seeded_playgrounds

from . import chained, doubling

# A string that is a path into the content of the repository, as a folder or as a part of a path: a test reads a file with
# ROOT / "playgrounds" as well as with ROOT.glob("addons/*/compose.yml")
CONTENT = re.compile(r"(?:^|[/\\])(?:addons|setups|playgrounds)(?:[/\\]|$)")
# The files that read the add-ons and playgrounds of the repository itself. They move to the catalog with them
MOVING = {"test_readme_commands.py", "test_tracing_redaction.py"}


def content_paths(source):
    """The strings of a Python source that name a folder of content of the repository, in any spelling of a path. A string
    with a space is a sentence (a docstring, a message), not a path: it is left out."""
    return sorted({node.value for node in ast.walk(ast.parse(source))
                   if isinstance(node, ast.Constant) and isinstance(node.value, str)
                   and not re.search(r"\s", node.value) and CONTENT.search(node.value)})


def readers_of_content(folder, exclude=()):
    """{file: the paths it names} of the test files of `folder`, but those of `exclude`, that name a folder of content."""
    found = {}
    for file in sorted(Path(folder).glob("test_*.py")):
        names = [] if file.name in exclude else content_paths(file.read_text(encoding="utf-8"))
        if names:
            found[file.name] = names
    return found


class Roots(unittest.TestCase):

    def test_without_a_catalog_the_tests_read_the_sample_of_the_engine(self):
        for environ in ({}, {"LAB_PLAYGROUNDS": ""}, {"OTHER": "x"}):
            with self.subTest(environ=environ):
                self.assertEqual(playgrounds_root(environ), SAMPLES)

    def test_the_folder_a_catalog_names_is_the_one_read(self):
        self.assertEqual(playgrounds_root({"LAB_PLAYGROUNDS": "/catalog/playgrounds"}), Path("/catalog/playgrounds").resolve())
        self.assertEqual(playgrounds_root({"LAB_PLAYGROUNDS": "playgrounds"}), Path("playgrounds").resolve())
        self.assertEqual(playgrounds_root({"LAB_PLAYGROUNDS": "~/playgrounds"}), Path("~/playgrounds").expanduser().resolve())

    def test_the_personal_playgrounds_are_those_of_the_catalog_that_is_named_or_else_of_this_clone(self):
        self.assertEqual(personal_root({"LAB_PLAYGROUNDS": "/catalog/playgrounds"}), Path("/catalog/playgrounds").resolve() / "local")
        self.assertEqual(personal_root({}), Path(testkit.__file__).resolve().parents[2] / "playgrounds" / "local")
        self.assertEqual(personal_root({"LAB_PLAYGROUNDS": ""}), personal_root({}))

    def test_the_roots_of_the_tests_are_those_of_the_environment_they_run_in(self):
        self.assertEqual(testkit.PLAYGROUNDS, playgrounds_root())
        self.assertEqual(testkit.PERSONAL, personal_root())


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


# What testkit names and seedlint holds: the checks of the seed files and the reading of a playground's seed service
SHARED = ("TEXT", "LITERAL_URL", "PUBLIC", "SAMPLES", "seed_paths", "unset_variables", "provided_to", "literal_addresses",
          "escaped_addresses", "as_mapping", "compose_value", "seed_service", "seed_environment")
CHECKS = "service: {labels: {io.gravitee.role: seed}, environment: {A: x}}"


class OneSetOfChecks(unittest.TestCase):
    """The tests of the engine and the tests of a catalog import the checks from the kit, and the catalog's CI runs them on the
    command line: they are one set, so that they never disagree about a file."""

    def test_every_name_the_kit_shares_is_the_object_of_seedlint(self):
        for name in SHARED:
            with self.subTest(name=name):
                self.assertIs(getattr(testkit, name), getattr(seedlint, name))

    def test_the_kit_holds_no_copy_of_what_it_names(self):
        """It defines none of them: a name is an alias of a seedlint object, or a function that calls one."""
        tree = ast.parse(Path(testkit.__file__).read_text(encoding="utf-8"))
        own = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.assertEqual(own & set(SHARED), set())
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names = [name.id for target in node.targets for name in ast.walk(target) if isinstance(name, ast.Name)]
                shared = [name for name in names if name in SHARED]
                if shared:
                    values = node.value.elts if isinstance(node.value, ast.Tuple) else [node.value]
                    for value in values:
                        with self.subTest(names=shared):
                            self.assertTrue(isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
                                            and value.value.id == "seedlint", ast.unparse(node))
        self.assertTrue(all(name not in vars(testkit) for name in ("re", "yaml")), "it parses and matches nothing itself")

    def test_the_roots_are_resolved_as_the_command_resolves_them(self):
        for environ in ({}, {"LAB_PLAYGROUNDS": ""}, {"LAB_PLAYGROUNDS": "/catalog/playgrounds"}, {"LAB_PLAYGROUNDS": "playgrounds"},
                        {"LAB_PLAYGROUNDS": "~/playgrounds"}, {"OTHER": "x"}):
            with self.subTest(environ=environ):
                self.assertEqual(playgrounds_root(environ), seedlint.playgrounds_root(environ))
        with mock.patch.dict(os.environ, {"LAB_PLAYGROUNDS": "/catalog/playgrounds"}):
            self.assertEqual(playgrounds_root(), Path("/catalog/playgrounds").resolve())

    def test_the_playgrounds_without_a_root_are_those_the_tests_read(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "acme" / "seed").mkdir(parents=True)
            (root / "acme" / "compose.yml").write_text("services:\n  " + CHECKS + "\n", encoding="utf-8")
            with mock.patch.object(testkit, "PLAYGROUNDS", root):
                self.assertEqual(list(seeded_playgrounds()), [(root / "acme", {"A": "x"})])
            self.assertEqual(list(seeded_playgrounds(root)), [(root / "acme", {"A": "x"})])
            self.assertEqual(list(seeded_playgrounds(root / "none")), [])

    def test_the_sample_is_read_whole_with_nothing_to_say(self):
        report = seedlint.lint(SAMPLES)
        self.assertEqual((report.problems, report.playgrounds), ([], 2))

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
                    testkit.seed_service(bomb)
                bomb.write_text("x-bomb:\n" + chained(3000, "  "), encoding="utf-8")
                with self.assertRaisesRegex(common.SeedError, "merges mappings with << keys"):
                    testkit.seed_service(bomb)
            self.assertLess(time.process_time() - started, 10.0)
            binary = root / "binary.yml"
            binary.write_bytes(b"services: \xff\xfe\n")
            with self.assertRaisesRegex(common.SeedError, "not UTF-8"):
                testkit.seed_service(binary)

    def test_a_compose_file_that_is_a_link_is_not_followed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "real.yml").write_text("services:\n  " + CHECKS + "\n", encoding="utf-8")
            try:
                os.symlink(root / "real.yml", root / "linked.yml")
            except (OSError, NotImplementedError, AttributeError) as e:
                self.skipTest(f"this machine cannot make a link: {e}")
            self.assertIsNotNone(testkit.seed_service(root / "real.yml"))
            with self.assertRaisesRegex(common.SeedError, "is a link"):
                testkit.seed_service(root / "linked.yml")

    def test_a_service_and_its_settings_are_read_whichever_way_they_are_written(self):
        with tempfile.TemporaryDirectory() as folder:
            compose = Path(folder) / "compose.yml"
            compose.write_text("services:\n  init:\n    labels: [io.gravitee.role=seed]\n    environment:\n      - URL=${URL:-http://apim}\n"
                               "      - BARE\n", encoding="utf-8")
            self.assertEqual(testkit.seed_environment(testkit.seed_service(compose)), {"URL": "http://apim", "BARE": ""})


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

    def test_a_test_file_that_reads_the_content_is_found_but_for_the_ones_that_move(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, source in {"test_exact.py": 'ROOT / "setups"\n', "test_glob.py": 'ROOT.glob("addons/*/compose.yml")\n',
                                 "test_part.py": '(ROOT / "playgrounds/acme-bakery/README.md").exists()\n',
                                 "test_clean.py": '"""Reads the playgrounds of a catalog."""\nROOT = testkit.PLAYGROUNDS\n',
                                 "test_moves.py": 'ROOT / "addons"\n', "helper.py": 'ROOT / "addons"\n'}.items():
                (Path(folder) / name).write_text(source, encoding="utf-8")
            self.assertEqual(readers_of_content(folder, exclude={"test_moves.py"}),
                             {"test_exact.py": ["setups"], "test_glob.py": ["addons/*/compose.yml"],
                              "test_part.py": ["playgrounds/acme-bakery/README.md"]})

    def test_no_test_but_the_ones_that_move_names_a_content_folder_of_the_repository(self):
        """A test that wants the files of a playground asks testkit for its root, so that a catalog can give its own, and the
        engine runs where the three folders are not."""
        here = Path(__file__).resolve()
        self.assertGreater(len(list(here.parent.glob("test_*.py"))), 20)
        self.assertEqual(readers_of_content(here.parent, exclude=MOVING | {here.name}), {},
                         "read the files of a playground under testkit.PLAYGROUNDS")

    def test_the_files_that_move_still_name_what_they_read(self):
        """Else the exception above is a name that nothing uses: remove it with the file."""
        here = Path(__file__).resolve().parent
        for name in sorted(MOVING):
            if (here / name).exists():
                with self.subTest(file=name):
                    self.assertTrue(content_paths((here / name).read_text(encoding="utf-8")))


class Names(unittest.TestCase):

    def test_no_function_of_the_kit_is_named_like_a_method_of_the_harness(self):
        """A test file that uses both would not tell a seed that is written from one that is read."""
        functions = {name for name, value in vars(testkit).items() if inspect.isfunction(value) and value.__module__ == "testkit"}
        methods = {name for name in vars(Seed) if not name.startswith("_")}
        self.assertEqual(functions & methods, set())


if __name__ == "__main__":
    unittest.main()
