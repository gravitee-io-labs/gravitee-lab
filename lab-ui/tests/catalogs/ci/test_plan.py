"""`catalog.py ci plan`: which units a change touches, and the four lines it writes for the workflow.

The plan reads names and the text of `include:` lines, and nothing else: these tests prove it runs no Compose and no Docker (a
fake `docker` records its calls, and `compose.run` is made to fail), that what it writes is four lines of flags and
`json.dumps` values whatever the tree holds, and that the scope is the one 5.5 says on a push, a first push, a change to
`.github/`, a README, a `guide.md`, an add-on that a playground includes, and a pull request of too many apps."""

import io
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.docker import compose
from lab_ui.catalogs import audit, ci, tree as trees
from lab_ui.catalogs.ci import plan
from lab_ui.catalogs.ci import main as ci_main
from tests import Case
from tests.data.rules import support
from tests.fakedocker import FakeDocker

ARCH = {"arch": "amd64", "platform": "linux/amd64", "runner": "ubuntu-24.04"}
ARM = {"arch": "arm64", "platform": "linux/arm64", "runner": "ubuntu-24.04-arm"}
COMPOSE = "services:\n  {name}:\n    image: busybox:1.36\n"
BANK = ("include:\n  - ../../addons/mock/compose.yml\nservices:\n  bakery-web:\n    image: example.test/lab/bakery-web:1\n"
        "    build: ./apps/web\n")


def catalog(*more, **kinds):
    """The good catalog of the rule tests with a few units: the add-on `mock`, the playground `bank` (it includes `mock`,
    builds an app, has a seed), the setup `pair` (it includes both) and the add-on `plain` (nothing includes it)."""
    own = {
        "addons/mock/compose.yml": COMPOSE.format(name="mock"),
        "addons/mock/README.md": "# Mock\n",
        "addons/plain/compose.yml": COMPOSE.format(name="plain"),
        "playgrounds/bank/compose.yml": BANK,
        "playgrounds/bank/README.md": "# Bank\n",
        "playgrounds/bank/guide.md": "# Guide\n",
        "playgrounds/bank/apps/web/Dockerfile": "FROM busybox:1.36\n",
        "playgrounds/bank/apps/web/app.py": "print(1)\n",
        "playgrounds/bank/seed/apim/apis/shop.json": "{}\n",
        "setups/pair.yml": "include:\n  - ../addons/mock/compose.yml\n  - ../playgrounds/bank/compose.yml\n",
    }
    drop = [*kinds.pop("drop", ()), "addons/hello/compose.yml", "addons/hello/README.md"]
    return support.files(own, *more, drop=drop, **kinds)


def plan_of(files, changed, event="pull_request", base="a" * 40, platforms=None):
    return plan.compute(trees.ListTree.from_files(files), event, changed, base, platforms=platforms)


class Names(unittest.TestCase):

    def test_a_path_belongs_to_a_unit_by_its_segments(self):
        for path, unit in (("addons/mock/compose.yml", "addon:mock"), ("addons/mock/deep/er/file.txt", "addon:mock"),
                           ("playgrounds/bank/apps/web/Dockerfile", "playground:bank"), ("setups/pair.yml", "setup:pair")):
            with self.subTest(path=path):
                self.assertEqual(plan.unit_of(path).key, unit)

    def test_what_is_no_unit_has_none(self):
        for path in ("addons/README.md", "addons", "setups/pair.yaml", "setups/sub/pair.yml", "docs/addons/mock/x", "catalog.yml",
                     "addons/Mock/compose.yml", "addons/a b/compose.yml", "addons/a\nb/compose.yml", "addons/::error::/x",
                     "addons//compose.yml", "addons/./x", "addons/../x/compose.yml", "setups/a\n.yml", "setups/.yml"):
            with self.subTest(path=path):
                self.assertIsNone(plan.unit_of(path))

    def test_a_dockerfile_and_a_readme(self):
        for path in ("Dockerfile", "a/Dockerfile", "a/Dockerfile.dev", "a/web.Dockerfile"):
            self.assertTrue(plan.is_dockerfile(path), path)
        for path in ("a/dockerfile.md", "a/Dockerfile/x", "a/notDockerfile"):
            self.assertFalse(plan.is_dockerfile(path), path)
        for path in ("README.md", "a/b/readme.md", "a/README"):
            self.assertTrue(plan.is_readme(path), path)
        for path in ("guide.md", "a/README.txt", "a/CHANGELOG.md"):
            self.assertFalse(plan.is_readme(path), path)

    def test_only_a_regular_compose_file_makes_a_unit(self):
        files = catalog({"addons/link/compose.yml": ("120000", "../mock/compose.yml"), "addons/empty/README.md": "x"})
        found = plan.listing(trees.ListTree.from_files(files).entries())
        self.assertEqual({unit.key for unit in found.units}, {"addon:mock", "addon:plain", "playground:bank", "setup:pair"})


class Scope(unittest.TestCase):

    def test_a_change_to_a_unit_touches_it_and_whoever_includes_it(self):
        found = plan_of(catalog(), ["addons/mock/compose.yml"])
        self.assertEqual(found.units, ["addon:mock", "playground:bank", "setup:pair"])
        self.assertEqual(found.scope, "change")

    def test_it_follows_the_inclusion_as_far_up_as_it_goes(self):
        found = plan_of(catalog(), ["playgrounds/bank/apps/web/app.py"])
        self.assertEqual(found.units, ["playground:bank", "setup:pair"])
        self.assertNotIn("addon:mock", found.units)   # what a unit includes is not touched by a change to the includer

    def test_a_unit_nobody_includes_touches_only_itself(self):
        self.assertEqual(plan_of(catalog(), ["addons/plain/compose.yml"]).units, ["addon:plain"])

    def test_a_change_outside_the_units_touches_none(self):
        found = plan_of(catalog(), ["docs/guide.md", "README.md"])
        self.assertEqual((found.units, found.seed, found.compile), ([], False, False))

    def test_a_deleted_unit_still_touches_those_that_include_it(self):
        files = catalog(drop=["addons/mock/compose.yml", "addons/mock/README.md"])
        found = plan_of(files, ["addons/mock/compose.yml", "addons/mock/README.md"])
        self.assertEqual(found.units, ["playground:bank", "setup:pair"])

    def test_a_change_to_the_files_every_unit_is_judged_by_is_everything(self):
        for path in ("catalog.yml", "Makefile", ".github/workflows/ci.yml", ".github/standard.json", ".catalog/standard.mk"):
            with self.subTest(path=path):
                found = plan_of(catalog(), [path])
                self.assertEqual(found.units, ["addon:mock", "addon:plain", "playground:bank", "setup:pair"])
                self.assertEqual(found.scope, "a change to a file every unit is judged by")

    def test_a_file_that_only_looks_like_one_of_them_is_not(self):
        for path in ("docs/catalog.yml", "sub/Makefile", ".github-notes/x", "x.github/a"):
            with self.subTest(path=path):
                self.assertEqual(plan_of(catalog(), [path]).scope, "change")

    def test_push_schedule_and_dispatch_are_everything(self):
        for event in ("push", "schedule", "workflow_dispatch"):
            with self.subTest(event=event):
                found = plan_of(catalog(), [], event=event, base="")
                self.assertEqual(found.scope, event)
                self.assertEqual(len(found.units), 4)

    def test_a_first_push_is_everything(self):
        for base in ("", "0" * 40):
            with self.subTest(base=base):
                self.assertEqual(plan_of(catalog(), None, event="pull_request", base=base).scope, "first push")

    def test_a_change_git_cannot_name_is_everything_and_says_so(self):
        found = plan.compute(trees.ListTree.from_files(catalog()), "pull_request", None, "a" * 40)
        self.assertEqual((found.scope, len(found.units)), ("git could not say what changed", 4))

    def test_a_unit_that_cannot_be_followed_is_touched_by_any_change_to_a_unit(self):
        # An include of another catalog is not one the text screen follows: the unit may include any unit of this one
        files = catalog({"addons/plain/compose.yml": "include:\n  - ../../../other/addons/x/compose.yml\nservices: {}\n"})
        self.assertEqual(plan_of(files, ["addons/mock/README.md"]).units,
                         ["addon:mock", "addon:plain", "playground:bank", "setup:pair"])
        self.assertEqual(plan_of(files, ["docs/guide.md"]).units, [])   # but a change to no unit touches nothing

    def test_a_remote_include_is_never_a_unit_of_the_catalog(self):
        files = catalog({"addons/plain/compose.yml": "include:\n  - https://example.test/addons/mock/compose.yml\n"})
        self.assertEqual(plan_of(files, ["addons/mock/README.md"]).units,
                         ["addon:mock", "addon:plain", "playground:bank", "setup:pair"])   # blind: it could include any

    def test_a_unit_that_is_not_text_is_followed_as_blind(self):
        files = catalog({"addons/plain/compose.yml": b"\xff\xfe\x00include:\n"})
        self.assertIn("addon:plain", plan_of(files, ["addons/mock/README.md"]).units)

    def test_a_cycle_ends(self):
        files = catalog({"addons/mock/compose.yml": "include:\n  - ../../playgrounds/bank/compose.yml\n",
                         "playgrounds/bank/compose.yml": BANK})
        self.assertEqual(plan_of(files, ["addons/mock/README.md"]).units, ["addon:mock", "playground:bank", "setup:pair"])

    def test_a_unit_that_includes_itself_is_not_its_own_dependency(self):
        files = catalog({"addons/plain/compose.yml": "include:\n  - compose.yml\n"})
        self.assertEqual(plan_of(files, ["addons/plain/README.md"]).units, ["addon:plain"])


class Flags(unittest.TestCase):

    def test_a_readme_alone_builds_nothing(self):
        found = plan_of(catalog(), ["playgrounds/bank/README.md"])
        self.assertEqual((found.units, found.compile), (["playground:bank", "setup:pair"], False))

    def test_a_readme_of_an_add_on_a_playground_includes_builds_nothing_either(self):
        self.assertFalse(plan_of(catalog(), ["addons/mock/README.md"]).compile)

    def test_the_guide_counts_as_more_than_a_readme(self):
        # The tests of an app read it
        self.assertTrue(plan_of(catalog(), ["playgrounds/bank/guide.md"]).compile)

    def test_the_code_of_an_app_builds(self):
        found = plan_of(catalog(), ["playgrounds/bank/apps/web/app.py"])
        self.assertEqual((found.compile, found.seed), (True, True))

    def test_nothing_to_build_when_no_touched_unit_has_a_dockerfile(self):
        found = plan_of(catalog(), ["addons/plain/compose.yml"])
        self.assertEqual((found.units, found.compile, found.seed), (["addon:plain"], False, False))

    def test_what_is_changed_outside_the_units_does_not_make_a_readme_more_than_one(self):
        for outside in (["docs/guide.md"], ["README.md", "docs/x.md", "tests/test_x.py"], ["LICENSE"]):
            found = plan_of(catalog(), ["playgrounds/bank/README.md", *outside])
            self.assertEqual((found.units, found.compile), (["playground:bank", "setup:pair"], False), outside)
        self.assertTrue(plan_of(catalog(), ["playgrounds/bank/README.md", "playgrounds/bank/apps/web/app.py", "docs/x.md"]).compile)

    def test_a_readme_and_a_file_is_more_than_a_readme(self):
        self.assertTrue(plan_of(catalog(), ["playgrounds/bank/README.md", "playgrounds/bank/apps/web/app.py"]).compile)

    def test_a_playground_with_a_seed_folder_asks_for_the_seed_job(self):
        self.assertTrue(plan_of(catalog(), ["playgrounds/bank/compose.yml"]).seed)
        self.assertFalse(plan_of(catalog(), ["addons/plain/compose.yml"]).seed)

    def test_a_seed_folder_in_an_add_on_is_no_seed(self):
        files = catalog({"addons/plain/seed/apim/apis/x.json": "{}"})
        self.assertFalse(plan_of(files, ["addons/plain/compose.yml"]).seed)

    def test_the_catalogs_own_tests_ask_for_the_seed_job_when_it_touches_them_or_any_unit(self):
        files = catalog({"tests/test_hub.py": "import unittest\n"})
        self.assertTrue(plan_of(files, ["tests/test_hub.py"]).seed)
        self.assertTrue(plan_of(files, ["addons/plain/compose.yml"]).seed)
        self.assertFalse(plan_of(files, ["docs/guide.md"]).seed)
        self.assertFalse(plan_of(catalog(), ["tests/test_hub.py"]).seed)   # a catalog with no tests/ has none to run

    def test_everything_builds_and_seeds_what_there_is(self):
        found = plan_of(catalog({"tests/test_hub.py": "x"}), ["catalog.yml"])
        self.assertEqual((found.compile, found.seed), (True, True))
        self.assertEqual(plan_of(catalog(drop=["playgrounds/bank/apps/web/Dockerfile"]), ["catalog.yml"]).compile, False)


class Matrix(unittest.TestCase):

    def matrix(self, platforms):
        files = catalog({".github/standard.json": json.dumps({"platforms": platforms, "standard": 1})})
        return plan.matrix_of(trees.ListTree.from_files(files))

    def test_each_platform_comes_from_a_fixed_map(self):
        self.assertEqual(self.matrix(["linux/amd64"]), ({"include": [ARCH]}, []))
        self.assertEqual(self.matrix(["linux/arm64", "linux/amd64"]), ({"include": [ARCH, ARM]}, []))

    def test_anything_else_is_sd04(self):
        for platforms in ([], "linux/amd64", ["linux/s390x"], ["linux/amd64", "linux/amd64"], ["linux/amd64", 3], None,
                          ["linux/amd64\nlinux/arm64"], ["linux/amd64; rm -rf /"], [{"platform": "linux/amd64"}]):
            with self.subTest(platforms=platforms):
                grid, problems = self.matrix(platforms)
                self.assertIsNone(grid)
                self.assertEqual([(each.code, each.severity, each.path) for each in problems],
                                 [("SD04", "error", ".github/standard.json")])
                self.assertEqual(problems[0].message, ".github/standard.json: platforms must be a non-empty list made of "
                                                      "linux/amd64 and linux/arm64.")

    def test_a_missing_or_broken_standard_is_sd04_too(self):
        for files, why in ((catalog(drop=[".github/standard.json"]), "the file is missing"),
                           (catalog({".github/standard.json": "{"}), "the file is not JSON"),
                           (catalog({".github/standard.json": b"\xff"}), "the file is not JSON"),
                           (catalog({".github/standard.json": "[]"}), "platforms must be a non-empty list"),
                           (catalog({".github/standard.json": "x" * 5000}), "the file is over 4 KB")):
            with self.subTest(why=why):
                grid, problems = plan.matrix_of(trees.ListTree.from_files(files))
                self.assertIsNone(grid)
                self.assertIn(why, problems[0].message)

    def test_a_run_on_this_machine_names_its_platform_and_needs_no_file(self):
        self.assertEqual(plan.matrix_of(trees.ListTree.from_files({}), ["linux/arm64"]), ({"include": [ARM]}, []))


class Limit(unittest.TestCase):

    def big(self, apps):
        files = catalog({f"playgrounds/big/apps/app{n}/Dockerfile": "FROM busybox:1.36\n" for n in range(apps)})
        files["playgrounds/big/compose.yml"] = COMPOSE.format(name="big")
        return files

    def test_a_pull_request_of_more_than_twenty_apps_is_bd06(self):
        found = plan_of(self.big(22), ["playgrounds/big/compose.yml"])   # 22 of big and 1 of bank is not touched
        self.assertEqual([(each.code, each.severity, each.path, each.line) for each in found.problems], [("BD06", "error", "", 0)])
        self.assertEqual(found.problems[0].message, "Split this pull request: it changes 22 apps, the limit is 20.")

    def test_twenty_is_allowed(self):
        self.assertEqual(plan_of(self.big(20), ["playgrounds/big/compose.yml"]).problems, [])

    def test_what_a_unit_includes_is_not_counted_with_the_includer(self):
        files = self.big(15)
        files["playgrounds/big2/compose.yml"] = "include:\n  - ../big/compose.yml\nservices: {}\n"
        files.update({f"playgrounds/big2/apps/app{n}/Dockerfile": "FROM busybox:1.36\n" for n in range(10)})
        found = plan_of(files, ["playgrounds/big/compose.yml"])
        self.assertEqual(found.apps, 25)   # big (15) and big2 (10), which includes it and is built again
        self.assertEqual([each.code for each in found.problems], ["BD06"])

    def test_a_push_or_a_change_to_the_standard_is_never_split(self):
        self.assertEqual(plan_of(self.big(30), [], event="push").problems, [])
        self.assertEqual(plan_of(self.big(30), ["catalog.yml"]).problems, [])


class Fast(unittest.TestCase):

    def test_a_megabyte_of_include_finishes_in_under_a_second(self):
        for text in ("include:\n  - " + "a" * 1_048_000 + "\n", "include: [" + ",".join(["../x/compose.yml"] * 60000) + "]\n",
                     "include:\n" + "  - ../mock/compose.yml\n" * 45000, "x: " + "[" * 500000 + "\n"):
            files = catalog({"addons/plain/compose.yml": text})
            start = time.process_time()
            found = plan_of(files, ["addons/mock/README.md"])
            self.assertLess(time.process_time() - start, 1.5)   # (three files read as hostile, the 1 MB cap is the guard)
            self.assertIn("addon:mock", found.units)

    def test_a_file_over_a_megabyte_is_not_read(self):
        files = catalog({"addons/plain/compose.yml": "include:\n  - ../mock/compose.yml\n" + "#" * 1_100_000})
        self.assertIn("addon:plain", plan_of(files, ["addons/mock/README.md"]).units)   # blind, so it is touched all the same


class Lines(unittest.TestCase):

    def test_the_four_outputs_are_one_line_each(self):
        found = plan_of(catalog(), ["addons/mock/compose.yml"])
        lines = found.outputs()
        self.assertEqual(list(lines), ["units", "seed", "compile", "matrix"])
        self.assertEqual(lines["units"], '["addon:mock", "playground:bank", "setup:pair"]')
        self.assertEqual((lines["seed"], lines["compile"]), ("true", "true"))
        self.assertEqual(json.loads(lines["matrix"]), {"include": [ARCH]})
        self.assertTrue(all("\n" not in value for value in lines.values()))

    def test_the_words_a_person_reads(self):
        text = plan.lines_of(plan_of(catalog(), ["addons/plain/compose.yml"]))
        self.assertEqual(text, ["Scope: the change and what includes it", "Units: addon:plain",
                                "Seed: no   Build: no   Architectures: amd64"])
        every = plan.lines_of(plan_of(catalog(), [], event="push"))
        self.assertEqual(every[0], "Scope: everything (push)")


class Run(Case):
    """The command: what it writes to $GITHUB_OUTPUT and to the screen, on a real repository."""

    def command(self, root, *argv, outputs=True):
        out, err = io.StringIO(), io.StringIO()
        target = Path(tempfile.mkdtemp(prefix="gravitee-lab-output-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(target, True))
        file = target / "output"
        env = {"GITHUB_OUTPUT": str(file)} if outputs else {}
        with mock.patch.dict(os.environ, env):
            if not outputs:
                os.environ.pop("GITHUB_OUTPUT", None)
            code = plan.main([str(root), *argv], out, err)
        return code, out.getvalue(), err.getvalue(), (file.read_text(encoding="utf-8") if file.exists() else None)


class Command(support.WithGit, Run):

    def setUp(self):
        support.WithGit.setUp(self)
        self.fake = FakeDocker(self)
        self.patch(compose, "run", mock.Mock(side_effect=AssertionError("the plan ran a command")))
        self.repo = self.work(files=catalog())
        self.first = self.git(self.repo, "rev-parse", "HEAD").strip()

    def change(self, files, message="Change"):
        for path, content in files.items():
            self.put(self.repo, path, content)
        return self.commit(self.repo, message)

    def test_a_pull_request_writes_its_four_outputs_and_nothing_else(self):
        head = self.change({"addons/mock/compose.yml": COMPOSE.format(name="mock") + "# more\n"})
        code, shown, said, written = self.command(self.repo, "--event", "pull_request", "--base", self.first, "--head", head,
                                                  "--github-output")
        self.assertEqual((code, said), (0, ""))
        self.assertEqual(written.splitlines(), [
            'units=["addon:mock", "playground:bank", "setup:pair"]', "seed=true", "compile=true",
            "matrix=" + json.dumps({"include": [ARCH]})])
        self.assertIn("Units: addon:mock, playground:bank, setup:pair", shown)

    def test_a_readme_only_pull_request_builds_nothing(self):
        head = self.change({"addons/plain/README.md": "# Plain\n"})
        code, _, _, written = self.command(self.repo, "--event", "pull_request", "--base", self.first, "--head", head,
                                           "--github-output")
        self.assertEqual(code, 0)
        self.assertEqual(written.splitlines()[:3], ['units=["addon:plain"]', "seed=false", "compile=false"])

    def test_a_change_to_the_workflows_is_everything(self):
        head = self.change({".github/workflows/ci.yml": "name: CI\n"})
        _, shown, _, written = self.command(self.repo, "--event", "pull_request", "--base", self.first, "--head", head,
                                            "--github-output")
        self.assertIn('units=["addon:mock", "addon:plain", "playground:bank", "setup:pair"]', written)
        self.assertIn("Scope: everything (a change to a file every unit is judged by)", shown)

    def test_a_push_is_everything_whatever_the_base(self):
        for base in ("", "0" * 40, self.first, "feedface" * 5):
            with self.subTest(base=base):
                _, shown, _, written = self.command(self.repo, "--event", "push", "--base", base, "--head", self.first,
                                                    "--github-output")
                self.assertIn("compile=true", written)
                self.assertIn("Scope: everything (push)", shown)

    def test_a_first_push_is_everything(self):
        _, shown, _, written = self.command(self.repo, "--event", "pull_request", "--base", "0" * 40, "--head", self.first,
                                            "--github-output")
        self.assertIn("Scope: everything (first push)", shown)
        self.assertIn("seed=true", written)

    def test_a_base_that_is_no_commit_is_everything_and_the_plan_still_answers(self):
        code, shown, _, written = self.command(self.repo, "--event", "pull_request", "--base", "ab" * 20, "--head", self.first,
                                               "--github-output")
        self.assertEqual(code, 0)
        self.assertIn("Scope: everything (git could not say what changed)", shown)
        self.assertEqual(len(written.splitlines()), 4)

    def test_a_base_that_is_an_option_is_no_commit(self):
        code, shown, _, _ = self.command(self.repo, "--event", "pull_request", "--base", "--output=/tmp/x", "--head", self.first)
        self.assertEqual(code, 2)   # argparse: a base that starts with a dash is not an argument of the option

    def test_without_a_head_it_reads_the_files_as_they_are(self):
        self.put(self.repo, "addons/plain/compose.yml", COMPOSE.format(name="plain") + "# changed\n")
        self.put(self.repo, "addons/new/compose.yml", COMPOSE.format(name="new"))
        code, _, _, written = self.command(self.repo, "--event", "pull_request", "--base", self.first, "--github-output")
        self.assertEqual(code, 0)
        self.assertIn('units=["addon:new", "addon:plain"]', written)

    def test_without_a_base_the_merge_base_of_origin_main_is_used(self):
        self.git(self.repo, "update-ref", "refs/remotes/origin/main", self.first)
        self.put(self.repo, "addons/plain/compose.yml", COMPOSE.format(name="plain") + "# changed\n")
        self.commit(self.repo, "Change")
        _, _, _, written = self.command(self.repo, "--github-output")
        self.assertIn('units=["addon:plain"]', written)

    def test_a_pull_request_of_too_many_apps_writes_nothing_and_exits_1(self):
        files = {f"playgrounds/big/apps/app{n}/Dockerfile": "FROM busybox:1.36\n" for n in range(21)}
        head = self.change({**files, "playgrounds/big/compose.yml": COMPOSE.format(name="big")})
        code, shown, _, written = self.command(self.repo, "--event", "pull_request", "--base", self.first, "--head", head,
                                               "--github-output", "--format", "github")
        self.assertEqual(code, 1)
        self.assertIsNone(written)   # no file and no key: the result job turns red on an empty answer
        self.assertIn("::error title=BD06::Split this pull request: it changes 21 apps, the limit is 20.", shown)

    def test_a_standard_that_names_no_known_platform_writes_nothing_and_exits_1(self):
        head = self.change({".github/standard.json": '{"platforms": ["linux/riscv64"], "standard": 1}\n'})
        code, shown, _, written = self.command(self.repo, "--event", "pull_request", "--base", self.first, "--head", head,
                                               "--github-output", "--format", "github")
        self.assertEqual((code, written), (1, None))
        self.assertIn("title=SD04::.github/standard.json: platforms must be a non-empty list made of linux/amd64 and "
                      "linux/arm64.", shown)

    def test_ci_means_github_annotations_and_a_summary(self):
        summary = Path(tempfile.mkdtemp(prefix="gravitee-lab-summary-")) / "summary.md"
        self.addCleanup(lambda: __import__("shutil").rmtree(summary.parent, True))
        head = self.change({"playgrounds/big/compose.yml": COMPOSE.format(name="big"),
                            **{f"playgrounds/big/apps/app{n}/Dockerfile": "FROM busybox:1.36\n" for n in range(21)}})
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
            code, shown, _, _ = self.command(self.repo, "--event", "pull_request", "--base", self.first, "--head", head, "--ci")
        self.assertEqual(code, 1)
        self.assertIn("::error title=BD06::", shown)
        text = summary.read_text(encoding="utf-8")
        self.assertIn("What the change touches", text)
        self.assertIn("1 problem to look at. To see them on your machine, run `make ci`.", text)

    def test_the_json_format_is_the_plan_and_its_problems(self):
        head = self.change({"addons/plain/compose.yml": COMPOSE.format(name="plain") + "# x\n"})
        code, shown, _, _ = self.command(self.repo, "--event", "pull_request", "--base", self.first, "--head", head,
                                         "--format", "json")
        document = json.loads(shown)
        self.assertEqual((code, document["units"], document["problems"], document["scope"]), (0, ["addon:plain"], [], "change"))

    def test_the_outputs_need_a_workflow_step(self):
        code, _, said, _ = self.command(self.repo, "--event", "push", "--github-output", outputs=False)
        self.assertEqual(code, 2)
        self.assertIn("--github-output needs $GITHUB_OUTPUT", said)

    def test_a_usage_that_is_wrong_is_exit_2(self):
        for argv in (["--event", "Push; rm"], ["--event", ""], ["--format", "xml"], ["/nonexistent-folder-xyz"]):
            with self.subTest(argv=argv):
                out, err = io.StringIO(), io.StringIO()
                self.assertEqual(plan.main(argv, out, err), 2)
                self.assertEqual(out.getvalue(), "")

    def test_the_plan_never_runs_docker(self):
        head = self.change({"addons/plain/compose.yml": COMPOSE.format(name="plain") + "# x\n"})
        self.command(self.repo, "--event", "pull_request", "--base", self.first, "--head", head, "--github-output")
        self.command(self.repo, "--event", "push", "--github-output")
        self.assertEqual(self.fake.calls(), [])
        compose.run.assert_not_called()

    def test_the_dispatcher_reaches_it(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": os.devnull}):
            code = ci_main(["plan", str(self.repo), "--event", "push", "--github-output"], out, err)
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("Scope: everything (push)", out.getvalue())


class Hostile(support.WithGit, Run):
    """A tree built to make a stage run a command, add an output or start a workflow command."""

    NAMES = ("addons/a\n::error::boom/compose.yml", "addons/x/read\nme::set-output name=units::[\"evil\"]",
             "playgrounds/::stop-commands::/compose.yml", "addons/%0A::error::x/compose.yml", "a%0A::error::x")

    def setUp(self):
        support.WithGit.setUp(self)
        self.fake = FakeDocker(self)
        self.patch(compose, "run", mock.Mock(side_effect=AssertionError("a command was run")))
        files = catalog({"addons/remote/compose.yml": "include:\n  - https://example.test/evil/compose.yml\nservices: {}\n",
                         "addons/remote/README.md": "# Remote\n"})
        for name in self.NAMES:
            files[name] = "x\n"
        self.repo = self.work(files=files)
        self.first = self.git(self.repo, "rev-parse", "HEAD").strip()

    def test_no_name_adds_a_key_or_a_line_to_the_outputs(self):
        for event, extra in (("push", []), ("pull_request", ["--base", "0" * 40]), ("workflow_dispatch", [])):
            with self.subTest(event=event):
                code, shown, said, written = self.command(self.repo, "--event", event, *extra, "--github-output")
                self.assertEqual(code, 0, said)
                lines = written.splitlines()
                self.assertEqual([line.split("=", 1)[0] for line in lines], ["units", "seed", "compile", "matrix"])
                self.assertTrue(all(re.fullmatch(r"[a-z]+=[^\r\n]*", line) for line in lines), lines)
                self.assertNotIn("::", written)
                self.assertNotIn("evil", written)
                self.assertEqual([line for line in shown.splitlines() if line.startswith("::")], [])

    def test_only_the_unit_with_a_name_the_lab_can_install_is_in_the_plan(self):
        _, _, _, written = self.command(self.repo, "--event", "push", "--github-output")
        units = json.loads(written.splitlines()[0].split("=", 1)[1])
        self.assertEqual(units, ["addon:mock", "addon:plain", "addon:remote", "playground:bank", "setup:pair"])

    def test_a_pull_request_that_adds_a_hostile_name_changes_nothing_but_the_flags(self):
        head = self.change_with({"addons/q\n::error::z/compose.yml": "x\n", "addons/plain/README.md": "# P\n"})
        code, shown, _, written = self.command(self.repo, "--event", "pull_request", "--base", self.first, "--head", head,
                                               "--github-output")
        self.assertEqual(code, 0)
        # (the unit with the remote include cannot be followed, so it is touched by a change to any unit)
        self.assertEqual(written.splitlines()[0], 'units=["addon:plain", "addon:remote"]')
        self.assertNotIn("::error", shown)

    def change_with(self, files):
        for path, content in files.items():
            self.put(self.repo, path, content)
        return self.commit(self.repo, "Change")

    def test_the_plan_and_the_check_see_the_same_tree_and_the_check_fails_it(self):
        found = audit.run(self.repo, audit.Options(ci=True))
        codes = {each.code for each in found.problems}
        self.assertIn("ST03", codes)   # the names the Lab cannot install
        self.assertEqual(audit.status(found, audit.Options(ci=True)), 1)

    def test_no_command_was_run_for_any_of_it(self):
        self.command(self.repo, "--event", "push", "--github-output")
        self.assertEqual(self.fake.calls(), [])
        compose.run.assert_not_called()


class Kit(unittest.TestCase):
    """What every stage shares (lab_ui/catalogs/ci/__init__.py)."""

    def test_the_outputs_of_a_step_are_lines_of_a_key_and_a_value_and_nothing_else(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        target = os.path.join(folder, "output")
        ci.write_outputs({"units": '["a"]', "seed": "true"}, target)
        ci.write_outputs({"compile": "false"}, target)
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), 'units=["a"]\nseed=true\ncompile=false\n')   # (appended: other steps write there too)

    def test_a_key_or_a_value_that_could_start_a_command_or_a_line_is_refused_before_anything_is_written(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        target = os.path.join(folder, "output")
        for items in ({"units": "a\nb=c"}, {"units": "a\rb"}, {"units": "::error::x"}, {"units": "a\x00b"}, {"Units": "x"},
                      {"units=x\nevil": "y"}, {"units": 3}, {"": "x"}, {"a b": "x"}, {"ok": "fine", "bad": "line\nbreak"}):
            with self.subTest(items=items), self.assertRaises(ValueError):
                ci.write_outputs(items, target)
        self.assertFalse(os.path.exists(target))

    def test_the_outputs_need_a_file_to_write_to(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("GITHUB_OUTPUT", None)
            with self.assertRaises(ci.Unavailable):
                ci.write_outputs({"units": "[]"})

    def test_a_json_value_is_one_ascii_line_whatever_it_holds(self):
        for value in ("a\nb", "\u2028x", "\x00", "::error::", "é", ["a\r\n"], {"k": "\u0085"}):
            line = ci.json_line(value)
            self.assertTrue(line.isascii() and "\n" not in line and "\r" not in line, line)

    def test_the_units_asked_for_are_kind_and_name_and_nothing_else(self):
        self.assertEqual(ci.parse_units('["addon:a", "playground:b-1", "setup:c", "addon:a"]'),
                         [("addon", "a"), ("playground", "b-1"), ("setup", "c")])
        self.assertEqual(ci.parse_units("[]"), [])
        for text in ("", "null", "{}", '"addon:a"', '[1]', '["addon"]', '["addon:"]', '["Addon:a"]', '["addon:A"]', '["addon:a b"]',
                     '["addon:a\\n"]', '["addon:a/b"]', '["addon:../x"]', '["unit:a"]', '[["addon:a"]]', "[" * 100000, '["addon:' + "a" * 101 + '"]'):
            with self.subTest(text=text[:30]), self.assertRaises(ci.Usage):
                ci.parse_units(text)

    def test_the_messages_of_the_stages_are_the_standards_word_for_word(self):
        spec = {
            "BD01": "{image}: the build failed at step {n}/{m}, {instruction} (exit {code}). Fix: run make ci to see it on your "
                    "machine; the last 15 lines are in the summary.",
            "BD02": "{service}: build.{key} is not accepted. Fix: use context, dockerfile, args, target and labels only.",
            "BD04": "{image}: the build took over 10 minutes and was stopped.",
            "BD05": "Docker Hub refused the pull (rate limit). This is not your change: run the job again in a few minutes.",
            "BD06": "Split this pull request: it changes {n} apps, the limit is 20.",
            "MF01": "{image}: the registry has no tag {tag}. Fix: use a tag that exists, or an earlier version.",
            "MF02": "{registry} did not answer: {image} was not checked.",
            "TS01": "{app}: {n} tests failed. Fix: run make ci APP={app} to see them.",
            "TS02": "{app} has no tests. Fix: COPY tests ./tests in the Dockerfile and add some.",
            "SD04": ".github/standard.json: {problem}.",
            "ND01": "{ids}: this catalog needs these catalogs and the secret NEEDS_DEPLOY_KEY is not set. Fix: give the repository a "
                    "read-only deploy key of theirs: make settings prints the commands.",
            "ND02": "{name} ({repository}) could not be fetched or linked: {why}. Fix: NEEDS_DEPLOY_KEY must be a deploy key of that "
                    "repository, and ref a commit of it.",
        }
        self.assertEqual({code: message for code, (_, message) in ci.MESSAGES.items()}, spec)
        self.assertEqual({code: severity for code, (severity, _) in ci.MESSAGES.items()},
                         {"BD01": "error", "BD02": "error", "BD04": "error", "BD05": "error", "BD06": "error", "MF01": "error",
                          "MF02": "warning", "TS01": "error", "TS02": "warning", "SD04": "error", "ND01": "error", "ND02": "error"})

    def test_they_are_the_table_the_explain_command_prints(self):
        from lab_ui.catalogs import explain
        table = {code: (severity, message) for code, severity, _, message in (*explain.STAGES, *explain.SD)}
        for code, row in ci.MESSAGES.items():
            self.assertEqual(table[code], row, code)

    def test_a_place_in_a_message_is_not_read_again_in_what_fills_it(self):
        found = ci.problem("MF01", "p", 0, image="ghcr.io/x/{tag}", tag="{image}")
        self.assertEqual(found.message, "ghcr.io/x/{tag}: the registry has no tag {image}. Fix: use a tag that exists, or an earlier version.")

    def test_a_name_in_a_summary_cell_has_no_character_of_markdown_or_of_a_line(self):
        for name in ("web", "a|b", "a`b", "a\nb", "[x](http://evil.test)", "<b>", "a" * 500):
            shown = ci.cell(name)
            self.assertRegex(shown, r"^[A-Za-z0-9._?-]{0,80}$")
        self.assertEqual(ci.cell("bakery-web"), "bakery-web")

    def test_lines_of_a_stranger_in_a_fence_cannot_close_it(self):
        text = ci.fence(["a", "```", "``````` x", "\x1b[31m"])
        self.assertTrue(text.startswith("````````\n") and text.endswith("\n````````\n"), text)
        self.assertNotIn("\x1b", text)

    def test_durations_and_sizes_as_a_person_reads_them(self):
        self.assertEqual([ci.seconds(each) for each in (0, 18.4, 59.6, 60, 185)], ["0 s", "18 s", "1 min 0 s", "1 min 0 s", "3 min 5 s"])
        self.assertEqual([ci.megabytes(each) for each in (900, 41_234_567, 1_500_000_000)], ["0.9 kB", "41.2 MB", "1500.0 MB"])

    def test_the_architecture_is_one_the_matrix_knows(self):
        self.assertIn(ci.host_platform(), ci.PLATFORMS)
        with mock.patch("platform.machine", return_value="arm64"):
            self.assertEqual(ci.host_platform(), "linux/arm64")
        with mock.patch("platform.machine", return_value="x86_64"):
            self.assertEqual(ci.host_platform(), "linux/amd64")
        with self.assertRaises(ci.Usage):
            ci.platform_of("linux/riscv64")

    def test_the_stage_word_is_looked_up_and_a_stage_that_is_not_in_this_lab_is_not_available_yet(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(ci.STAGES, {"ghost": ("a_module_that_is_not_there", "main")}):
            self.assertEqual(ci_main(["ghost"], out, err), 2)
        self.assertEqual((out.getvalue(), err.getvalue()), ("", "catalog.py ci: ghost is not available yet in this Lab.\n"))

    def test_a_module_without_the_function_is_not_available_either(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(ci.STAGES, {"ghost": ("plan", "no_such_function")}):
            self.assertEqual(ci_main(["ghost"], out, err), 2)
        self.assertIn("ghost is not available yet in this Lab.", err.getvalue())

    def test_a_module_that_fails_to_import_is_a_fault_not_a_missing_stage(self):
        with mock.patch.dict(ci.STAGES, {"ghost": ("plan", "main")}), \
                mock.patch("importlib.import_module", side_effect=ModuleNotFoundError("no", name="something_it_needs")):
            with self.assertRaises(ModuleNotFoundError):
                ci_main(["ghost"], io.StringIO(), io.StringIO())

    def test_no_stage_and_a_word_that_is_none_are_usage(self):
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(ci_main([], out, err), 2)
        self.assertIn("usage: catalog.py ci <stage>", err.getvalue())
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(ci_main(["--help"], out, err), 0)
        for stage in ci.STAGES:
            self.assertIn(stage, out.getvalue())
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(ci_main(["nope\n::error::x"], out, err), 2)
        self.assertEqual([line for line in err.getvalue().splitlines() if line.startswith("::")], [])

    def test_the_table_of_stages_names_the_function_each_package_writes(self):
        for stage, (module, function) in ci.STAGES.items():
            self.assertRegex(module, r"^[a-z0-9]+$")
            self.assertRegex(function, r"^[a-z_]+$")
        self.assertEqual({stage for stage, (module, _) in ci.STAGES.items() if module in
                          ("plan", "gitleaks", "manifests", "build", "apps", "run")},
                         {"plan", "gitleaks", "manifests", "build", "test", "run"})

    def test_a_catalog_that_lists_too_many_files_is_exit_2_not_a_traceback(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(trees.FolderTree, "entries", side_effect=trees.TreeError("This catalog lists more files than the check reads.")):
            self.assertEqual(plan.main([folder, "--event", "push"], out, err), 2)
        self.assertEqual((out.getvalue(), err.getvalue()), ("", "catalog.py ci: This catalog lists more files than the check reads.\n"))

    def test_a_command_that_prints_without_end_is_kept_to_its_first_and_last_halves(self):
        program = "import sys\nsys.stdout.write('A' * 5000000 + 'MIDDLE' + 'Z' * 5000000 + 'END')\nsys.stderr.write('e' * 3000000 + 'ERREND')"
        code, out, err = ci.run_limited([sys.executable, "-c", program], timeout=60, environ=dict(os.environ), limit=1000)
        self.assertEqual(code, 0)
        self.assertLess(len(out), 1200)
        self.assertLess(len(err), 1200)
        self.assertTrue(out.startswith("AAAA") and out.endswith("ZZZZEND") and "the middle of the output is gone" in out)
        self.assertNotIn("MIDDLE", out)
        self.assertTrue(err.endswith("eeeERREND") and "the middle of the output is gone" in err)

    def test_a_command_that_prints_little_is_returned_whole_and_both_outputs_are_read_at_once(self):
        program = "import sys\nfor n in range(3000):\n    sys.stdout.write('o' * 1000 + '\\n'); sys.stderr.write('e' * 1000 + '\\n')\nsys.exit(3)"
        code, out, err = ci.run_limited([sys.executable, "-c", program], timeout=60, environ=dict(os.environ))
        self.assertEqual((code, len(out), len(err)), (3, 3000 * 1001, 3000 * 1001))   # (neither pipe filled while the other was waited for)

    def test_a_command_that_does_not_end_is_stopped_with_what_it_started(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        pidfile = os.path.join(folder, "pid")
        program = ("import os, subprocess, sys, time\nchild = subprocess.Popen(['sleep', '120'])\n"
                   f"open({pidfile!r}, 'w').write(str(child.pid))\ntime.sleep(120)")
        started = time.monotonic()
        code, out, err = ci.run_limited([sys.executable, "-c", program], timeout=2, environ=dict(os.environ))
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(code, 124)
        self.assertEqual(err, f"{sys.executable} did not answer within 2 s")
        child = int(Path(pidfile).read_text())
        for _ in range(50):   # (the signal is delivered, and the child is collected by init)
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            self.fail("the program it started is still running")

    def test_a_command_that_cannot_be_started_is_127(self):
        self.assertEqual(ci.run_limited(["/nonexistent/program"], environ={})[0], 127)
        self.assertEqual(ci.run_limited(["bad\x00name"], environ={})[0], 127)

    def test_it_is_given_the_environment_it_is_told_and_no_other_and_no_input(self):
        program = "import os, sys\nprint(sorted(name for name in os.environ if name.startswith('GL_')))\nprint(repr(sys.stdin.read()))"
        with mock.patch.dict(os.environ, {"GL_SECRET": "x"}):
            code, out, err = ci.run_limited([sys.executable, "-c", program], environ={"GL_ONE": "1", "PATH": os.environ["PATH"]})
        self.assertEqual((code, out.splitlines()), (0, ["['GL_ONE']", "''"]))

    def test_an_interrupt_ends_a_stage_with_130_and_a_usage_error_with_2(self):
        @ci.entry
        def interrupted(argv, out, err):
            raise KeyboardInterrupt

        @ci.entry
        def wrong(argv, out, err):
            raise ci.Usage("give a thing\n::error::x")
        self.assertEqual(interrupted([], io.StringIO(), io.StringIO()), 130)
        err = io.StringIO()
        self.assertEqual(wrong([], io.StringIO(), err), 2)
        self.assertEqual(err.getvalue(), "catalog.py ci: give a thing ::error::x\n")

    def test_the_help_of_every_stage_is_printed_once_and_is_exit_0(self):
        import contextlib
        for stage in ("plan", "gitleaks", "manifests", "build", "test", "run"):
            for flag in ("--help", "-h"):
                with self.subTest(stage=stage, flag=flag):
                    out, err, printed = io.StringIO(), io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(printed):
                        code = ci_main([stage, flag], out, err)
                    self.assertEqual((code, err.getvalue()), (0, ""))
                    self.assertIn(f"catalog.py ci {stage}", printed.getvalue())
                    self.assertIn("usage:", printed.getvalue())
        self.assertEqual(ci_main(["--help"], io.StringIO(), io.StringIO()), 0)

    def test_a_usage_that_is_wrong_is_still_exit_2_with_one_line(self):
        err = io.StringIO()
        self.assertEqual(ci_main(["build", "--no-such-option"], io.StringIO(), err), 2)
        self.assertEqual(len(err.getvalue().splitlines()), 1)
        self.assertTrue(err.getvalue().startswith("catalog.py ci: "))


class Bounded(unittest.TestCase):
    """What a stranger's program prints is cut to a size a message, a summary and GitHub accept."""

    def test_the_tail_is_fifteen_lines_of_at_most_five_hundred_characters(self):
        lines = ci.tail("\n".join([f"{number} " + "x" * 5_000_000 for number in range(20)]))
        self.assertEqual(len(lines), 15)
        self.assertTrue(all(len(each) <= ci.LINE_WIDTH + len(" [...]") and each.endswith(" [...]") for each in lines))
        self.assertTrue(lines[0].startswith("5 "))   # (the last fifteen)
        self.assertEqual(ci.tail("a\n\n  \nb\n"), ["a", "b"])
        self.assertEqual(ci.tail("x" * 500), ["x" * 500])   # (a line that fits is as it was)

    def test_the_fence_and_the_text_of_a_failure_are_small_whatever_the_program_printed(self):
        from lab_ui.catalogs import report
        lines = ci.tail("\n".join(["y" * 5_000_000] * 15))
        self.assertLess(len(ci.fence(lines)), 15 * 520)
        self.assertLess(len(report.untrusted_text("\n".join(lines) + "\n")), 15 * 600)

    def test_a_line_of_a_megabyte_is_cut_in_a_second(self):
        start = time.process_time()
        ci.tail("z" * 20_000_000)
        self.assertLess(time.process_time() - start, 1.0)

    def test_a_summary_over_what_github_keeps_is_cut_at_a_line_and_says_so(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        target = os.path.join(folder, "summary.md")
        text = "# Title\n" + "".join(f"line {number} {'é' * 40}\n" for number in range(60_000))
        self.assertGreater(len(text.encode("utf-8")), ci.SUMMARY_LIMIT)
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": target}):
            ci.write_summary(text)
            ci.write_summary("## small\n")
        written = Path(target).read_bytes()
        self.assertLessEqual(len(written), ci.SUMMARY_LIMIT + 200)
        decoded = written.decode("utf-8")   # (cut at a character, not in the middle of one)
        self.assertTrue(decoded.startswith("# Title\nline 0 "))
        self.assertIn("The rest of this summary is left out: it is longer than GitHub shows.\n## small\n", decoded)
        cut = decoded.split("\nThe rest of this summary")[0]
        self.assertTrue(cut.splitlines()[-1].startswith("line ") and cut.splitlines()[-1].endswith("é"))   # (a whole line)

    def test_a_summary_under_the_limit_is_written_as_it_is(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        target = os.path.join(folder, "summary.md")
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": target}):
            ci.write_summary("a")
            ci.write_summary("b\n")
        self.assertEqual(Path(target).read_text(), "a\nb\n")


if __name__ == "__main__":
    unittest.main()
