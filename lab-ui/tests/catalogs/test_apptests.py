"""`lab_ui/catalogs/ci/apptests.py` (what runs the tests of an app) and `catalog.py ci test` (TS01, TS02).

The flag set of 5.6 is pinned word for word. Docker is `tests/fakedocker.py`, so the tests prove the commands a run emits and
what each outcome becomes, never that an image passes."""

import io
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.catalogs.ci import apptests
from lab_ui.catalogs import ci
from lab_ui.catalogs.ci import apps, build
from lab_ui.catalogs.ci import main as ci_main
from tests import Case
from tests.fakedocker import FakeDocker, option
from tests.catalogs.ci.test_build import Disk

REPO = Path(__file__).resolve().parents[3]
NODE = "node:24-alpine@sha256:" + "ab" * 32
HARDENING = ("--pull never --network none --cap-drop ALL --security-opt no-new-privileges --pids-limit 512 --memory 2g --cpus 2 "
             "--read-only --tmpfs /tmp:rw,exec,nosuid,nodev,size=256m").split()
UNITTEST_FAILED = """\
FAIL: test_a (tests.test_x.X.test_a)
----------------------------------------------------------------------
Traceback (most recent call last):
AssertionError: 1 != 2

----------------------------------------------------------------------
Ran 7 tests in 0.020s

FAILED (failures=2, errors=1, skipped=1)
"""
NODE_FAILED = ".XX.\n\nFailed tests:\n\n✖ fails one (2.6ms)\n  AssertionError: 1 == 2\n      at X\n✖ fails two (0.1ms)\n  AssertionError: 1 == 3\n"


NAME = re.compile(r"lab-test-[0-9a-f]{8}")


def unnamed(command):
    """A `docker run` of a test without its `--name lab-test-<8 hexadecimal digits>`, the one flag the runs add to the set of 5.6;
    the name is checked to be one."""
    at = command.index("--name")
    assert NAME.fullmatch(command[at + 1]), command[at + 1]
    return command[:at] + command[at + 2:]


class Commands(unittest.TestCase):

    def test_a_run_has_a_name_of_its_own_right_after_rm_and_nothing_else_is_added(self):
        for command, plain in ((apptests.python_command("img", None, "lab-test-0123abcd"), apptests.python_command("img")),
                               (apptests.node_command("/a", NODE, "lab-test-0123abcd"), apptests.node_command("/a", NODE))):
            self.assertEqual(command[:4], ["docker", "run", "--rm", "--name"])
            self.assertEqual(command[4], "lab-test-0123abcd")
            self.assertEqual(command[:3] + command[5:], plain)
        names = {apptests.container_name() for _ in range(50)}
        self.assertEqual(len(names), 50)
        for each in names:
            self.assertRegex(each, NAME.pattern)

    def test_the_python_command_is_the_flag_set_of_the_standard(self):
        self.assertEqual(apptests.python_command("lab-test-web"), [
            "docker", "run", "--rm", *HARDENING, "-e", "PYTHONDONTWRITEBYTECODE=1", "--entrypoint", "python", "lab-test-web",
            "-m", "unittest", "discover", "-s", "tests", "-t", "."])

    def test_the_guide_is_mounted_read_only_and_named_for_the_tests(self):
        command = apptests.python_command("img", "/work/catalog/playgrounds/bank/guide.md")
        self.assertEqual(command[command.index("PYTHONDONTWRITEBYTECODE=1") + 1:command.index("--entrypoint")],
                         ["-v", "/work/catalog/playgrounds/bank/guide.md:/guide.md:ro", "-e", "GUIDE=/guide.md"])

    def test_the_javascript_command_runs_in_the_node_image_with_the_app_read_only_and_the_same_hardening(self):
        self.assertEqual(apptests.node_command("/work/app", NODE), [
            "docker", "run", "--rm", *HARDENING, "-v", "/work/app:/app:ro", "-w", "/app", NODE, "node", "--test",
            "--test-timeout=60000", "--test-reporter=dot", "tests/js/*.test.mjs"])

    def test_the_hardening_is_everything_the_standard_lists_and_pulls_nothing(self):
        for command in (apptests.python_command("img"), apptests.node_command("/a", NODE)):
            self.assertEqual(option(command, "--pull"), "never")
            self.assertEqual(option(command, "--network"), "none")
            self.assertEqual(option(command, "--tmpfs"), "/tmp:rw,exec,nosuid,nodev,size=256m")   # exec: an app runs a script from /tmp
            self.assertEqual({word for word in command if word in ("--read-only", "--rm")}, {"--read-only", "--rm"})
            self.assertNotIn("--privileged", command)
            self.assertNotIn("--volume", command)

    def test_the_node_image_is_the_pin_of_the_makefile(self):
        found = re.search(r"^NODE_IMAGE \?= (\S+)$", (REPO / "Makefile").read_text(encoding="utf-8"), re.M)
        self.assertEqual(apptests.node_image({}), found.group(1))
        self.assertRegex(apptests.node_image({}), r"^node:\d+-alpine@sha256:[0-9a-f]{64}$")

    def test_the_environment_may_choose_another_as_make_does(self):
        self.assertEqual(apptests.node_image({"NODE_IMAGE": "node:99"}), "node:99")

    def test_no_pin_is_a_refusal_not_a_guess(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        file = Path(folder, "Makefile")
        with self.assertRaises(apptests.Missing):
            apptests.node_image({}, file)
        file.write_text("PYTHON ?= python3\n")
        with self.assertRaises(apptests.Missing):
            apptests.node_image({}, file)
        file.write_text("NODE_IMAGE ?= node:1@sha256:abc\n")
        self.assertEqual(apptests.node_image({}, file), "node:1@sha256:abc")


class Files(unittest.TestCase):

    def app(self, files):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        for path, text in files.items():
            Path(folder, path).parent.mkdir(parents=True, exist_ok=True)
            Path(folder, path).write_text(text)
        return folder

    def test_the_tests_an_app_has(self):
        for files, expected in (({"Dockerfile": ""}, (False, False)), ({"tests/__init__.py": "", "tests/test_a.py": ""}, (True, False)),
                                ({"tests/js/a.test.mjs": ""}, (False, True)),
                                ({"tests/test_a.py": "", "tests/js/a.test.mjs": ""}, (True, True)),
                                ({"tests/base.py": "", "tests/fakes.py": ""}, (False, False)),
                                ({"tests/js/helper.mjs": ""}, (False, False)), ({"tests/sub/test_deep.py": ""}, (True, False)),
                                ({"tests/other/a.test.mjs": ""}, (False, False)), ({"test_a.py": ""}, (False, False))):
            with self.subTest(files=sorted(files)):
                self.assertEqual(tuple(apptests.has_tests(self.app(files))), expected)

    def test_a_link_is_not_followed_to_find_tests(self):
        outside = self.app({"test_evil.py": ""})
        folder = self.app({"Dockerfile": ""})
        os.symlink(outside, os.path.join(folder, "tests"))
        self.assertEqual(tuple(apptests.has_tests(folder)), (False, False))
        folder = self.app({"tests/real.py": ""})
        os.symlink(os.path.join(outside, "test_evil.py"), os.path.join(folder, "tests", "test_link.py"))
        self.assertEqual(tuple(apptests.has_tests(folder)), (False, False))

    def test_the_guide_is_the_playgrounds_two_folders_above_the_app(self):
        folder = self.app({"guide.md": "# Guide", "apps/web/Dockerfile": ""})
        self.assertEqual(apptests.guide_of(os.path.join(folder, "apps", "web")), os.path.realpath(os.path.join(folder, "guide.md")))
        self.assertEqual(apptests.guide_of("/nonexistent/apps/web"), None)
        self.assertEqual(apptests.guide_of(os.path.join(folder, "apps", "web"), folder), os.path.realpath(os.path.join(folder, "guide.md")))

    def test_a_guide_that_is_a_link_is_none(self):
        folder = self.app({"apps/web/Dockerfile": ""})
        os.symlink("/etc/hosts", os.path.join(folder, "guide.md"))
        self.assertIsNone(apptests.guide_of(os.path.join(folder, "apps", "web")))


class Counting(unittest.TestCase):

    def test_unittest_failures_and_errors_are_added(self):
        self.assertEqual(apptests.failures(UNITTEST_FAILED), 3)
        self.assertEqual(apptests.failures("FAILED (failures=4)\n"), 4)
        self.assertEqual(apptests.failures("FAILED (errors=2)\n"), 2)

    def test_nodes_failed_tests_are_counted_under_their_heading(self):
        self.assertEqual(apptests.failures(NODE_FAILED, js=True), 2)

    def test_a_failure_with_no_count_is_at_least_one(self):
        for text in ("", "Traceback: boom\n", "FAILED\n", "\n✖ stray\n"):
            self.assertEqual(apptests.failures(text), 1)
            self.assertEqual(apptests.failures(text, True), 1)

    def test_a_run_with_no_test_in_it(self):
        for text in ("Ran 0 tests in 0.000s\n\nNO TESTS RAN\n", "ImportError: Start directory is not importable: 'tests'\n",
                     "python: can't open file '/x': [Errno 2]"):
            self.assertTrue(apptests.ran_none(5, text), text)
        self.assertFalse(apptests.ran_none(0, "Ran 7 tests in 0.1s\n\nOK\n"))

    def test_a_line_a_test_printed_does_not_make_a_failing_run_one_with_no_test(self):
        # A test that runs a unittest of its own, or prints such a line: the run is the last `Ran N tests` and the last line
        nested = ("Ran 0 tests in 0.000s\n\nNO TESTS RAN\n.F\n" + UNITTEST_FAILED)
        self.assertFalse(apptests.ran_none(1, nested))
        self.assertEqual(apptests.run_python("img", lambda argv, timeout, cwd: (1, "", nested))[0], "failed")
        for text in ("ImportError: Start directory is not importable: 'tests'\nRan 3 tests in 0.1s\n\nFAILED (failures=1)\n",
                     "python: can't open file '/x': [Errno 2]\nRan 3 tests in 0.1s\n\nFAILED (failures=1)\n"):
            with self.subTest(text=text[:30]):
                self.assertFalse(apptests.ran_none(1, text))
        self.assertTrue(apptests.ran_none(5, "Ran 3 tests in 0.1s\n\nOK\nRan 0 tests in 0.000s\n\nNO TESTS RAN\n"))

    def test_the_line_of_a_megabyte_that_looks_like_the_other_cause_is_read_in_a_second(self):
        import time
        for text in ("python " * 150000 + "can't open file", "ImportError: Start directory is not importable" * 20000,
                     "python" * 170000 + ": can't open file"):
            start = time.process_time()
            apptests.ran_none(1, text)
            self.assertLess(time.process_time() - start, 1.0)

    def test_a_megabyte_of_output_is_read_in_a_second(self):
        import time
        for text in ("FAILED (" + "failures=1, " * 90000 + ")", "x" * 1_000_000, "Ran " * 250000, ("✖ a\n" * 100000)):
            start = time.process_time()
            apptests.failures(text)
            apptests.failures(text, True)
            apptests.ran_none(1, text)
            self.assertLess(time.process_time() - start, 1.5)


class Runs(unittest.TestCase):
    """One app, with a runner that answers from a script."""

    def runner(self, answers):
        self.calls = []

        def run(argv, timeout, cwd):
            self.calls.append(argv)
            for words, answer in answers:
                if all(word in argv for word in words):
                    return answer
            return 0, "", ""
        return run

    def app(self, files):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        for path, text in files.items():
            Path(folder, path).parent.mkdir(parents=True, exist_ok=True)
            Path(folder, path).write_text(text)
        return folder

    def test_python_tests_that_pass(self):
        folder = self.app({"tests/test_a.py": ""})
        outcome = apptests.run_app(folder, "img", self.runner([(["--entrypoint"], (0, "", "Ran 3 tests in 1s\n\nOK\n"))]))
        self.assertEqual((outcome.status, outcome.python, outcome.js, outcome.failed), ("passed", "passed", "none", 0))
        self.assertEqual(len(self.calls), 1)

    def test_python_tests_that_fail_say_how_many(self):
        folder = self.app({"tests/test_a.py": ""})
        outcome = apptests.run_app(folder, "img", self.runner([(["--entrypoint"], (1, "", UNITTEST_FAILED))]))
        self.assertEqual((outcome.status, outcome.failed), ("failed", 3))
        self.assertIn("FAILED (failures=2, errors=1, skipped=1)", outcome.output)

    def test_both_kinds_are_run_and_their_failures_are_added(self):
        folder = self.app({"tests/test_a.py": "", "tests/js/a.test.mjs": ""})
        outcome = apptests.run_app(folder, "img", self.runner([(["--entrypoint"], (1, "", UNITTEST_FAILED)), ([NODE], (1, NODE_FAILED, ""))]),
                                   NODE)
        self.assertEqual((outcome.status, outcome.failed, outcome.python, outcome.js), ("failed", 5, "failed", "failed"))
        self.assertEqual(len(self.calls), 2)

    def test_a_javascript_failure_alone_fails_the_app(self):
        folder = self.app({"tests/js/a.test.mjs": ""})
        outcome = apptests.run_app(folder, "img", self.runner([([NODE], (1, NODE_FAILED, ""))]), NODE)
        self.assertEqual((outcome.status, outcome.failed, outcome.python), ("failed", 2, "none"))

    def test_no_node_image_fails_the_javascript_tests_and_says_so(self):
        folder = self.app({"tests/js/a.test.mjs": ""})
        outcome = apptests.run_app(folder, "img", self.runner([]), None)
        self.assertEqual((outcome.status, outcome.js, outcome.failed), ("failed", "failed", 1))
        self.assertIn("The Node image of the JavaScript tests could not be had", outcome.output)

    def test_an_app_with_no_tests_is_none_and_runs_nothing(self):
        outcome = apptests.run_app(self.app({"Dockerfile": ""}), "img", self.runner([]))
        self.assertEqual((outcome.status, self.calls), ("none", []))

    def test_tests_the_image_does_not_hold_are_none(self):
        # tests/ is in the folder and the Dockerfile never copied it: the image has no tests to start from
        folder = self.app({"tests/test_a.py": ""})
        outcome = apptests.run_app(folder, "img", self.runner([(["--entrypoint"], (1, "", "ImportError: Start directory is not importable: 'tests'\n"))]))
        self.assertEqual(outcome.status, "none")

    def test_a_run_of_zero_tests_that_exits_0_is_none(self):
        folder = self.app({"tests/test_a.py": ""})
        outcome = apptests.run_app(folder, "img", self.runner([(["--entrypoint"], (0, "", "Ran 0 tests in 0.000s\n\nNO TESTS RAN\n"))]))
        self.assertEqual(outcome.status, "none")

    def test_the_node_image_is_pulled_quietly_only_when_it_is_missing(self):
        run = self.runner([(["inspect"], (1, "", "No such image"))])
        self.assertIsNone(apptests.ensure_node(NODE, run))
        self.assertEqual([call[:3] for call in self.calls], [["docker", "image", "inspect"], ["docker", "pull", "-q"]])
        run = self.runner([])
        self.assertIsNone(apptests.ensure_node(NODE, run))
        self.assertEqual(len(self.calls), 1)
        run = self.runner([(["inspect"], (1, "", "")), (["pull"], (1, "", "unauthorized: x\n"))])
        slept = []
        self.assertEqual(apptests.ensure_node(NODE, run, slept.append), "unauthorized: x")
        self.assertEqual(slept, [])   # (a refusal that is the author's or the machine's is not tried again)

    RATE = "Error response from daemon: toomanyrequests: You have reached your unauthenticated pull rate limit.\n"

    def test_a_pull_docker_hub_refused_is_tried_again_after_20_and_60_seconds(self):
        slept = []
        run = self.runner([(["inspect"], (1, "", "")), (["pull"], (1, "", self.RATE))])
        said = apptests.ensure_node(NODE, run, slept.append)
        self.assertIn("pull rate limit", said)
        self.assertEqual(slept, [20, 60])
        self.assertEqual(len([call for call in self.calls if call[1] == "pull"]), 3)

    def test_a_pull_that_works_the_second_time_is_a_pull(self):
        slept, answers = [], iter([(1, "", self.RATE), (0, "sha256:x\n", "")])

        def run(argv, timeout, cwd):
            if argv[1:3] == ["image", "inspect"]:
                return 1, "", "No such image"
            return next(answers)
        self.assertIsNone(apptests.ensure_node(NODE, run, slept.append))
        self.assertEqual(slept, [20])

    def test_what_is_infrastructure_here_is_what_it_is_in_a_build(self):
        from lab_ui.catalogs.ci import build
        for text in ("toomanyrequests: rate limit", "TLS handshake timeout", "dial tcp 1.2.3.4:443: i/o timeout", "no such host",
                     "received unexpected HTTP status: 503 Service Unavailable", "unexpected status: 502 Bad Gateway",
                     "manifest unknown", "unauthorized: x", "pull access denied", "exit code: 1", "denied: requested access is denied"):
            self.assertEqual(bool(apptests.INFRA.search(text)), bool(build.INFRA.search(text)), text)

    def test_a_run_that_passed_its_time_limit_is_removed_by_its_name(self):
        folder = self.app({"tests/test_a.py": "", "tests/js/a.test.mjs": ""})
        run = self.runner([(["--entrypoint"], (124, "", "docker did not answer within 900 s")), ([NODE], (124, "", "docker did not answer within 600 s"))])
        outcome = apptests.run_app(folder, "img", run, NODE)
        self.assertEqual((outcome.status, outcome.python, outcome.js), ("failed", "failed", "failed"))
        runs = [call for call in self.calls if call[1] == "run"]
        removed = [call for call in self.calls if call[1:3] == ["rm", "-f"]]
        self.assertEqual(len(runs), 2)
        self.assertEqual([call[3] for call in removed], [option(call, "--name") for call in runs])
        self.assertEqual(len({call[3] for call in removed}), 2)

    def test_a_run_that_ended_by_itself_is_not_removed_by_name(self):
        folder = self.app({"tests/test_a.py": ""})
        apptests.run_app(folder, "img", self.runner([(["--entrypoint"], (1, "", UNITTEST_FAILED))]))
        self.assertEqual([call for call in self.calls if "rm" in call], [])


class Make(Case):
    """`apptests.main`, against the fake docker."""

    def setUp(self):
        self.fake = FakeDocker(self)
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name).resolve()
        self.out, self.err = io.StringIO(), io.StringIO()
        self.patch(apptests, "node_image", lambda *a, **k: NODE)

    def app(self, folder, name, tests=True, js=False, guide=True):
        base = self.root / folder
        (base / "apps" / name).mkdir(parents=True, exist_ok=True)
        (base / "apps" / name / "Dockerfile").write_text("FROM scratch\n")
        if tests:
            (base / "apps" / name / "tests").mkdir(exist_ok=True)
            (base / "apps" / name / "tests" / "test_a.py").write_text("")
        if js:
            (base / "apps" / name / "tests" / "js").mkdir(parents=True, exist_ok=True)
            (base / "apps" / name / "tests" / "js" / "a.test.mjs").write_text("")
        if guide:
            (base / "guide.md").write_text("# Guide")
        return base / "apps" / name

    def main(self, *argv):
        return apptests.main(list(argv), out=self.out, err=self.err)

    def test_every_app_is_built_tested_and_removed(self):
        web = self.app("playgrounds/bank", "bakery-web", js=True)
        self.app("playgrounds/bank", "bakery-api")
        code = self.main(str(self.root / "playgrounds"))
        self.assertEqual((code, self.err.getvalue()), (0, ""))
        calls = [call.argv for call in self.fake.calls()]
        self.assertEqual([call[:2] for call in calls if call[0] == "build"], [["build", "-q"], ["build", "-q"]])
        self.assertEqual([call[2:] for call in calls if call[0] == "build"][0][0:2], ["-t", "lab-test-bakery-api"])
        runs = [call for call in calls if call[0] == "run"]
        self.assertEqual(len(runs), 3)   # two Python runs and one JavaScript run
        guide = os.path.realpath(self.root / "playgrounds/bank/guide.md")
        python = [call for call in runs if "--entrypoint" in call]
        self.assertEqual([option(call, "-v") for call in python], [f"{guide}:/guide.md:ro"] * 2)
        node = [call for call in runs if NODE in call]
        self.assertEqual(node[0][node[0].index("-v") + 1], f"{web}:/app:ro")
        self.assertEqual([call for call in calls if call[0] == "rmi"], [["rmi", "lab-test-bakery-api"], ["rmi", "lab-test-bakery-web"]])

    def test_the_node_image_is_pulled_quietly_when_it_is_missing_and_not_when_it_is_there(self):
        self.app("playgrounds/bank", "bakery-web")
        self.fake.when("image", "inspect", code=1)
        self.main(str(self.root / "playgrounds"))
        self.assertEqual(self.fake.commands("pull"), [["pull", "-q", NODE]])
        self.fake.reset()
        self.main(str(self.root / "playgrounds"))
        self.assertEqual(self.fake.commands("pull"), [])

    def test_a_node_image_docker_hub_refuses_is_pulled_again_and_then_the_javascript_tests_fail_with_the_reason(self):
        self.app("playgrounds/bank", "bakery-web", tests=False, js=True)
        self.fake.when("image", "inspect", NODE, code=1)
        self.fake.when("pull", "-q", code=1, stderr="toomanyrequests: You have reached your pull rate limit\n")
        slept = []
        code = apptests.main([str(self.root / "playgrounds")], out=self.out, err=self.err, sleep=slept.append)
        self.assertEqual((code, slept, len(self.fake.commands("pull"))), (1, [20, 60], 3))
        self.assertIn("The Node image of the JavaScript tests could not be had", self.err.getvalue())

    def test_a_run_of_a_person_is_named_too(self):
        self.app("playgrounds/bank", "bakery-web")
        self.main(str(self.root / "playgrounds"))
        runs = self.fake.commands("run")
        self.assertEqual(len(runs), 1)
        unnamed(runs[0])

    def test_an_app_that_fails_ends_the_run_with_1_and_the_others_are_tested(self):
        self.app("playgrounds/bank", "bakery-web")
        self.app("playgrounds/bank", "bakery-api")
        self.fake.when("run", "lab-test-bakery-api", code=1, stderr=UNITTEST_FAILED)
        code = self.main(str(self.root / "playgrounds"))
        self.assertEqual(code, 1)
        self.assertEqual(len(self.fake.commands("run")), 2)
        self.assertIn("FAILED (failures=2, errors=1, skipped=1)", self.err.getvalue())

    def test_an_image_that_does_not_build_ends_the_run_with_1(self):
        self.app("playgrounds/bank", "bakery-web")
        self.fake.when("build", code=1, stderr="failed to solve")
        self.assertEqual(self.main(str(self.root / "playgrounds")), 1)
        self.assertEqual(self.fake.commands("run"), [])
        self.assertIn("failed to solve", self.err.getvalue())

    def test_an_app_with_no_tests_is_said_and_not_a_failure(self):
        self.app("playgrounds/bank", "bakery-web", tests=False)
        self.assertEqual(self.main(str(self.root / "playgrounds")), 0)
        self.assertIn("bakery-web has no tests", self.out.getvalue())
        self.assertEqual(self.fake.commands("run"), [])

    def test_docker_that_does_not_answer_is_exit_2_before_anything_is_built(self):
        self.app("playgrounds/bank", "bakery-web")
        self.fake.when("info", code=1, stderr="Cannot connect to the Docker daemon")
        self.assertEqual(self.main(str(self.root / "playgrounds")), 2)
        self.assertIn("Docker is not running", self.err.getvalue())
        self.assertEqual(self.fake.commands("build"), [])

    def test_the_images_are_kept_when_asked(self):
        self.app("playgrounds/bank", "bakery-web")
        self.main("--keep", str(self.root / "playgrounds"))
        self.assertEqual(self.fake.commands("rmi"), [])

    def test_an_app_may_be_named_alone_and_a_folder_with_no_app_is_nothing_to_test(self):
        web = self.app("playgrounds/bank", "bakery-web")
        self.app("playgrounds/bank", "bakery-api")
        self.main(str(web))
        self.assertEqual(len(self.fake.commands("build")), 1)
        self.out = io.StringIO()
        self.assertEqual(self.main(str(self.root / "empty")), 0)
        self.assertIn("No app to test", self.out.getvalue())

    def test_a_makefile_with_no_pin_is_exit_2(self):
        self.app("playgrounds/bank", "bakery-web")
        self.patch(apptests, "node_image", mock.Mock(side_effect=apptests.Missing("the Makefile of the Lab pins no NODE_IMAGE")))
        self.assertEqual(self.main(str(self.root / "playgrounds")), 2)
        self.assertIn("pins no NODE_IMAGE", self.err.getvalue())


class Stage(Disk):
    """`catalog.py ci test` with the units given by the loader seam."""

    def setUp(self):
        super().setUp()
        self.fake = FakeDocker(self)
        self.out, self.err = io.StringIO(), io.StringIO()
        self.patch(apptests, "node_image", lambda *a, **k: NODE)
        self.guide = Path(self.root, "playgrounds", "bank", "guide.md")
        self.guide.parent.mkdir(parents=True, exist_ok=True)
        self.guide.write_text("# Guide")

    def with_tests(self, name, js=False, python=True):
        folder = Path(self.app(name=name))
        (folder / "tests").mkdir()
        if python:
            (folder / "tests" / "test_a.py").write_text("")
        if js:
            (folder / "tests" / "js").mkdir()
            (folder / "tests" / "js" / "a.test.mjs").write_text("")
        return str(folder)

    def stage(self, services, *argv, loading=None, **seams):
        units = loading or ci.Loading([self.unit(services)], [], [])
        with mock.patch.object(ci, "LOADER", mock.Mock(return_value=units)):
            return apps.main([self.root, "--units", '["playground:bank"]', *argv], self.out, self.err, lab=self.lab, **seams)

    def runs(self):
        return self.fake.commands("run")

    def test_the_tests_of_an_app_run_in_its_image_with_the_guide_of_its_playground(self):
        context = self.with_tests("web")
        code = self.stage({"bakery-web": self.service(context=context)}, "--format", "github")
        self.assertEqual((code, self.err.getvalue()), (0, ""))
        runs = self.runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(unnamed(runs[0]), ["run", "--rm", *HARDENING, "-e", "PYTHONDONTWRITEBYTECODE=1", "-v",
                                   f"{os.path.realpath(self.guide)}:/guide.md:ro", "-e", "GUIDE=/guide.md", "--entrypoint", "python",
                                   "example.test/lab/bakery-web:1.0", "-m", "unittest", "discover", "-s", "tests", "-t", "."])
        self.assertIn("✓ web  passed", self.out.getvalue())

    def test_an_app_that_has_javascript_tests_runs_them_in_the_node_image(self):
        context = self.with_tests("web", js=True)
        self.stage({"bakery-web": self.service(context=context)})
        node = [call for call in self.runs() if NODE in call]
        self.assertEqual(len(node), 1)
        self.assertEqual(node[0][node[0].index("-v") + 1], f"{context}:/app:ro")
        self.assertEqual(self.fake.commands("image", "inspect")[0][-1], "example.test/lab/bakery-web:1.0")   # the image is looked for first

    def test_a_failed_test_is_ts01_with_how_many(self):
        context = self.with_tests("web")
        self.fake.when("run", "--entrypoint", code=1, stderr=UNITTEST_FAILED)
        code = self.stage({"bakery-web": self.service(context=context)}, "--format", "github")
        self.assertEqual(code, 1)
        self.assertIn("::error file=playgrounds/bank/compose.yml,title=TS01::web: 3 tests failed. Fix: run make ci APP=web to see "
                      "them.", self.out.getvalue())

    def test_an_app_with_no_tests_is_ts02_a_warning(self):
        context = self.app(name="web")
        code = self.stage({"bakery-web": self.service(context=context)}, "--format", "github")
        self.assertEqual(code, 0)
        self.assertIn("::warning file=playgrounds/bank/compose.yml,title=TS02::web has no tests. Fix: COPY tests ./tests in the "
                      "Dockerfile and add some.", self.out.getvalue())
        self.assertEqual(self.runs(), [])

    def test_tests_the_image_does_not_hold_are_ts02_too(self):
        context = self.with_tests("web")
        self.fake.when("run", "--entrypoint", code=1, stderr="ImportError: Start directory is not importable: 'tests'\n")
        self.stage({"bakery-web": self.service(context=context)}, "--format", "github")
        self.assertIn("title=TS02::web has no tests.", self.out.getvalue())

    def test_what_the_tests_printed_is_data_between_the_stop_commands(self):
        context = self.with_tests("web")
        self.fake.when("run", "--entrypoint", code=1, stderr="::error::boom ::add-mask::hunter2\n" + UNITTEST_FAILED)
        self.stage({"bakery-web": self.service(context=context)}, "--format", "github")
        lines = self.out.getvalue().splitlines()
        start = next(number for number, line in enumerate(lines) if line.startswith("::stop-commands::"))
        end = lines.index("::" + lines[start].split("::")[2] + "::")
        self.assertTrue(any("hunter2" in line for line in lines[start:end]))
        self.assertFalse(any("hunter2" in line for line in lines[:start] + lines[end + 1:]))

    def test_the_summary_has_a_table_and_the_tail_of_a_failure(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        summary = os.path.join(folder, "summary.md")
        a, b = self.with_tests("web"), self.app(name="api")
        self.fake.when("run", "--entrypoint", "example.test/lab/bakery-web:1.0", code=1, stderr=UNITTEST_FAILED)
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary}):
            self.stage({"bakery-web": self.service(context=a), "bakery-api": self.service("bakery-api", b)}, "--ci")
        with open(summary, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("### Tests of the apps", text)
        self.assertRegex(text, r"\| `web` \| playground:bank \| failed \|  \| 3 failed \| \d+ s \|")
        self.assertRegex(text, r"\| `api` \| playground:bank \|  \|  \| no tests \|  \|")
        self.assertIn("FAILED (failures=2, errors=1, skipped=1)", text)

    RATE = "Error response from daemon: toomanyrequests: You have reached your unauthenticated pull rate limit.\n"

    def test_a_node_image_docker_hub_refuses_is_exit_2_after_new_tries_and_is_never_a_failed_test(self):
        context = self.with_tests("web", js=True)
        self.fake.when("image", "inspect", NODE, code=1, stderr="Error: No such image")
        self.fake.when("pull", "-q", code=1, stderr=self.RATE)
        slept = []
        code = self.stage({"bakery-web": self.service(context=context)}, "--format", "github", sleep=slept.append)
        self.assertEqual(code, 2)
        self.assertEqual(slept, [20, 60])
        self.assertEqual(len(self.fake.commands("pull")), 3)
        self.assertEqual(self.runs(), [])   # (nothing of the author's was run, so nothing of it failed)
        self.assertIn("catalog.py ci: the Node image of the JavaScript tests could not be had: ", self.err.getvalue())
        self.assertIn("pull rate limit", self.err.getvalue())
        self.assertIn("This is not your change: run the job again in a few minutes", self.err.getvalue())
        self.assertNotIn("TS01", self.out.getvalue())
        self.assertNotIn("All good", self.out.getvalue())

    def test_a_node_image_that_cannot_be_had_for_another_reason_is_exit_2_without_the_advice(self):
        context = self.with_tests("web", js=True)
        self.fake.when("image", "inspect", NODE, code=1)
        self.fake.when("pull", "-q", code=1, stderr="Error response from daemon: manifest unknown\n")
        slept = []
        self.assertEqual(self.stage({"bakery-web": self.service(context=context)}, sleep=slept.append), 2)
        self.assertEqual((slept, len(self.fake.commands("pull"))), ([], 1))
        self.assertIn("manifest unknown", self.err.getvalue())
        self.assertNotIn("not your change", self.err.getvalue())

    def test_a_node_image_that_is_had_on_the_second_try_runs_the_tests(self):
        context = self.with_tests("web", js=True)
        self.fake.when("image", "inspect", NODE, code=1)
        self.fake.when("pull", "-q", code=1, stderr=self.RATE, times=1)
        slept = []
        code = self.stage({"bakery-web": self.service(context=context)}, sleep=slept.append)
        self.assertEqual((code, slept, self.err.getvalue()), (0, [20], ""))
        self.assertEqual(len([call for call in self.runs() if NODE in call]), 1)

    def test_the_node_image_is_not_needed_by_an_app_with_python_tests_only(self):
        context = self.with_tests("web")
        self.fake.when("pull", "-q", code=1, stderr=self.RATE)
        self.assertEqual(self.stage({"bakery-web": self.service(context=context)}), 0)
        self.assertEqual(self.fake.commands("pull"), [])

    def test_a_makefile_with_no_node_pin_is_exit_2_here_too(self):
        context = self.with_tests("web", js=True)
        self.patch(apptests, "node_image", mock.Mock(side_effect=apptests.Missing("the Makefile of the Lab pins no NODE_IMAGE")))
        self.assertEqual(self.stage({"bakery-web": self.service(context=context)}), 2)
        self.assertIn("pins no NODE_IMAGE", self.err.getvalue())

    def test_every_run_has_a_name_and_a_run_that_passes_its_limit_is_removed_by_it(self):
        context = self.with_tests("web", js=True)
        self.patch(apptests, "PYTHON_TIMEOUT", 1)
        self.patch(apptests, "NODE_TIMEOUT", 1)
        self.fake.when("run", sleep=30)   # (the process group is ended at the limit, the container would not be)
        code = self.stage({"bakery-web": self.service(context=context)}, "--format", "github")
        self.assertEqual(code, 1)
        self.assertIn("title=TS01::web: ", self.out.getvalue())
        runs = self.runs()
        self.assertEqual(len(runs), 2)
        removed = self.fake.commands("rm", "-f")
        self.assertEqual([call[2:] for call in removed], [[option(call, "--name")] for call in runs])
        self.assertEqual(len({call[2] for call in removed}), 2)
        for call in runs:
            unnamed(call)   # (the name is one, and the rest is the flag set)

    def test_a_run_that_ends_by_itself_is_left_alone(self):
        context = self.with_tests("web")
        self.stage({"bakery-web": self.service(context=context)})
        self.assertEqual(self.fake.commands("rm"), [])

    def test_lines_of_megabytes_in_a_failed_test_leave_a_summary_and_an_output_of_a_few_kilobytes(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        summary = os.path.join(folder, "summary.md")
        context = self.with_tests("web")
        self.fake.when("run", "--entrypoint", code=1, stderr=("w" * 2_000_000 + "\n") * 3 + UNITTEST_FAILED)
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary}):
            code = self.stage({"bakery-web": self.service(context=context)}, "--ci")
        self.assertEqual(code, 1)
        self.assertLess(os.path.getsize(summary), 20_000)
        self.assertLess(len(self.out.getvalue()), 20_000)

    def test_the_images_are_the_ones_that_were_built_and_none_is_pulled(self):
        context = self.with_tests("web")
        self.stage({"bakery-web": self.service(context=context)})
        for command in self.runs():
            self.assertEqual(option(command, "--pull"), "never")
        self.assertEqual(self.fake.commands("pull"), [])

    def test_an_image_that_is_not_here_is_exit_2_and_not_a_pull(self):
        context = self.with_tests("web")
        self.fake.when("image", "inspect", code=1, stderr="No such image")
        code = self.stage({"bakery-web": self.service(context=context)})
        self.assertEqual(code, 2)
        self.assertIn("is not here: build it first (catalog.py ci build)", self.err.getvalue())
        self.assertEqual(self.runs() + self.fake.commands("pull"), [])

    def test_the_services_the_labs_seeder_builds_are_not_tested(self):
        seed = {"image": "example.test/lab/seeder:latest", "build": {"context": os.path.join(self.lab, "seeder")}}
        self.stage({"bakery-seed": seed})
        self.assertEqual(self.fake.calls(), [])

    def test_an_app_that_builds_with_no_image_is_tested_in_the_image_the_build_made_of_it(self):
        context = self.with_tests("web")
        service = self.service(context=context)
        service.pop("image")
        code = self.stage({"bakery-web": service})
        self.assertEqual((code, self.err.getvalue()), (0, ""))
        runs = self.runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0][runs[0].index("python") + 1], "lab-ci/playground-bank-bakery-web:ci")

    def test_one_app_alone(self):
        a, b = self.with_tests("web"), self.with_tests("api")
        self.stage({"bakery-web": self.service(context=a), "bakery-api": self.service("bakery-api", b)}, "--app", "api")
        self.assertEqual([command[command.index("python") + 1] for command in self.runs()], ["example.test/lab/bakery-api:1.0"])

    def test_nothing_of_the_runner_reaches_a_test(self):
        context = self.with_tests("web", js=True)
        secrets = {"GITHUB_TOKEN": "ghp_" + "T" * 36, "ACTIONS_RUNTIME_TOKEN": "runtime-secret-value", "SSH_AUTH_SOCK": "/tmp/agent"}
        with mock.patch.dict(os.environ, secrets):
            self.stage({"bakery-web": self.service(context=context)})
        words = " ".join(" ".join(call.argv) for call in self.fake.calls())
        for value in secrets.values():
            self.assertNotIn(value, words)
        for call in self.fake.calls():
            self.assertEqual([name for name in call.env if name.startswith(("ACTIONS_", "GITHUB_", "GH_")) or name == "SSH_AUTH_SOCK"], [])

    def test_a_unit_the_lab_refused_is_never_tested(self):
        loading = ci.Loading([], [ci.Refused("playground", "bank", "playgrounds/bank/compose.yml", ("Runs with full privileges",), "T03")], [])
        code = self.stage({}, "--format", "github", loading=loading)
        self.assertEqual(code, 1)
        self.assertEqual(self.fake.calls(), [])
        self.assertIn("title=T03::playground:bank was not tested: Runs with full privileges.", self.out.getvalue())

    def test_the_dispatcher_reaches_it(self):
        context = self.with_tests("web")
        with mock.patch.object(ci, "LOADER", mock.Mock(return_value=ci.Loading([self.unit({"bakery-web": self.service(context=context)})], [], []))):
            code = ci_main(["test", self.root, "--units", '["playground:bank"]'], self.out, self.err, lab=self.lab)
        self.assertEqual(code, 0, self.err.getvalue())

    def test_the_messages_are_the_standards(self):
        self.assertEqual(ci.problem("TS01", "p", 0, app="bakery-web", n=2).message,
                         "bakery-web: 2 tests failed. Fix: run make ci APP=bakery-web to see them.")
        self.assertEqual(ci.problem("TS02", "p", 0, app="bakery-web").message,
                         "bakery-web has no tests. Fix: COPY tests ./tests in the Dockerfile and add some.")
        self.assertEqual(build.app_of("/a/b/apps/bakery-web"), "bakery-web")


if __name__ == "__main__":
    unittest.main()
