"""`catalog.py ci run`: the runner behind `make ci`. It plans from `origin/main` the way CI plans a pull request, reads the
units once, and runs the stages in the order CI runs them on the architecture of this machine; a stage that is not in this Lab
is said and skipped, and a run that skipped something never says it is all good.

The stages are stubs that record their arguments, and one class runs the real ones over the fake docker."""

import io
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.catalogs import ci
from lab_ui.catalogs.ci import main as ci_main
from lab_ui.catalogs.ci import plan, run
from tests.data.rules import support
from tests.fakedocker import FakeDocker, option
from tests.catalogs.ci.test_plan import COMPOSE, catalog
from tests.catalogs.ci.test_manifests import Registry

PLATFORM = ci.host_platform()


class Recorder:
    """Stages that record what they were called with and answer the exit code they are told to."""

    def __init__(self, codes=None, says=None):
        self.calls = []
        self.codes = codes or {}
        self.says = says or {}   # {stage: what it writes on its error stream}

    def stage(self, name):
        def function(argv, out, err, **seams):
            self.calls.append((name, list(argv), sorted(seams)))
            out.write(f"{name} ran\n")
            if name in self.says:
                err.write(self.says[name])
            return self.codes.get(name, 0)
        return function

    def stages(self, *names):
        return {name: self.stage(name) for name in names}

    @property
    def names(self):
        return [name for name, _, _ in self.calls]


def loader(config_for=None, own_for=None):
    """A loader seam that answers the units asked for, each with the JSON and the services of its own that it is told, and
    remembers what it was asked."""
    asked = []

    def load(folder, names):
        asked.append((folder, list(names)))
        return ci.Loading([ci.Loaded(kind, name, name, ci.unit_path(kind, name), "",
                                     (config_for or (lambda k, n: {"services": {}}))(kind, name),
                                     (own_for or (lambda k, n: ()))(kind, name), (), None) for kind, name in names], [], [])
    load.asked = asked
    return load


class Fixture(support.WithGit):

    def setUp(self):
        super().setUp()
        self.repo = self.work(files=catalog())
        self.first = self.git(self.repo, "rev-parse", "HEAD").strip()
        self.git(self.repo, "update-ref", "refs/remotes/origin/main", self.first)
        self.out, self.err = io.StringIO(), io.StringIO()
        self.recorder = Recorder()

    def change(self, files):
        for path, content in files.items():
            self.put(self.repo, path, content)
        return self.commit(self.repo, "Change")

    def run_ci(self, *argv, stages=None, loading=None, **seams):
        every = stages if stages is not None else self.recorder.stages(*run.ORDER)
        with mock.patch.object(ci, "LOADER", loading or loader()):
            return run.main([str(self.repo), *argv], self.out, self.err, stages=every, **seams)


class Order(Fixture):

    def test_the_stages_run_in_the_order_ci_runs_them_with_the_arguments_ci_gives(self):
        self.change({"playgrounds/bank/apps/web/app.py": "print(2)\n"})
        code = self.run_ci()
        self.assertEqual(code, 0, self.err.getvalue())
        self.assertEqual(self.recorder.names, ["manifests", "build", "test", "smoke", "scan", "gitleaks"])   # smoke of level 2, scan of 6
        calls = {name: argv for name, argv, _ in self.recorder.calls}
        root = str(self.repo)
        units = json.dumps(["playground:bank", "setup:pair"])
        self.assertEqual(calls["manifests"], [root, "--units", units])
        self.assertEqual(calls["build"], [root, "--units", units, "--platform", PLATFORM])
        self.assertEqual(calls["test"], [root, "--units", units])
        self.assertEqual(calls["scan"], [root, "--units", units])
        self.assertEqual(calls["gitleaks"], [root, "--base", self.first])
        self.assertIn("== plan\nScope: the change and what includes it\nUnits: playground:bank, setup:pair", self.out.getvalue())

    def test_the_summary_lists_each_stage(self):
        self.change({"playgrounds/bank/apps/web/app.py": "print(2)\n"})
        self.run_ci()
        text = self.out.getvalue()
        for pattern in (r"✓ plan\s+passed: 2 units", r"✓ manifests\s+passed", r"✓ build\s+passed", r"✓ test\s+passed",
                        r"✓ smoke\s+passed", r"✓ scan\s+passed", r"✓ gitleaks\s+passed"):
            self.assertRegex(text, re.compile(pattern))

    def test_a_run_that_skipped_nothing_says_all_good_and_one_that_did_not_never_does(self):
        self.change({"playgrounds/bank/apps/web/app.py": "print(2)\n"})
        self.run_ci()
        self.assertTrue(self.out.getvalue().endswith("All good\n"))
        self.out = io.StringIO()
        self.run_ci(stages={**self.recorder.stages(*run.ORDER), "gitleaks": None})
        self.assertNotIn("All good", self.out.getvalue())
        self.assertRegex(self.out.getvalue(), r"- gitleaks\s+skipped: this stage is not in this Lab yet")
        self.assertTrue(self.out.getvalue().endswith("1 stage skipped.\n"))

    def test_nothing_changed_in_a_unit_runs_nothing_but_the_scan_of_the_commits(self):
        self.change({"docs/guide.md": "# Changed\n"})
        self.assertEqual(self.run_ci(), 0)
        self.assertEqual(self.recorder.names, ["gitleaks"])
        self.assertIn("Nothing changed in a unit: there is nothing to check, build or start; the commits are still scanned.", self.out.getvalue())
        self.assertRegex(self.out.getvalue(), r"- manifests\s+not asked: no unit changed")
        self.assertTrue(self.out.getvalue().endswith("All good\n"))   # (nothing was skipped: there was nothing to do)

    def test_a_readme_alone_checks_the_images_and_builds_nothing_and_is_all_good(self):
        self.change({"addons/plain/README.md": "# Plain\n"})
        self.assertEqual(self.run_ci(), 0)
        self.assertEqual(self.recorder.names, ["manifests", "smoke", "gitleaks"])
        for stage in ("build", "test", "scan"):   # (as CI, which does not ask for them: a stage that was not asked is not a skip)
            self.assertRegex(self.out.getvalue(), rf"- {stage}\s+not asked: the change builds nothing \(a README alone, or no app\)")
        self.assertNotIn("skipped", self.out.getvalue())
        self.assertTrue(self.out.getvalue().endswith("All good\n"))

    def test_all_runs_every_unit(self):
        self.run_ci("--all")
        units = json.loads(self.recorder.calls[0][1][2])
        self.assertEqual(units, ["addon:mock", "addon:plain", "playground:bank", "setup:pair"])

    def test_the_base_may_be_another_commit(self):
        self.change({"addons/plain/compose.yml": COMPOSE.format(name="plain") + "# 1\n"})
        second = self.change({"addons/mock/compose.yml": COMPOSE.format(name="mock") + "# 2\n"})
        self.run_ci("--base", self.git(self.repo, "rev-parse", "HEAD~1").strip())
        self.assertEqual(json.loads(self.recorder.calls[0][1][2]), ["addon:mock", "playground:bank", "setup:pair"])
        self.assertEqual(self.recorder.calls[-1][1][2], self.git(self.repo, "rev-parse", "HEAD~1").strip())
        self.assertTrue(second)

    def test_a_base_that_is_no_commit_is_a_usage_error(self):
        for base in ("nonexistent", "f" * 40, "--output=x"):
            self.out, self.err = io.StringIO(), io.StringIO()
            self.assertEqual(self.run_ci(f"--base={base}"), 2)
        self.assertEqual(self.recorder.calls, [])

    def test_with_no_origin_main_every_unit_is_run_and_it_says_why(self):
        self.git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")
        self.run_ci()
        self.assertIn("Scope: everything (there is no origin/main to measure the change from)", self.out.getvalue())

    def test_an_app_alone_runs_its_units_and_names_it_to_build_and_test(self):
        context = str(self.repo / "playgrounds" / "bank" / "apps" / "web")
        config = {"services": {"bakery-web": {"image": "example.test/lab/bakery-web:1", "build": {"context": context}}}}
        bank = ("playground", "bank")
        loading = loader(lambda kind, name: config if (kind, name) == bank else {"services": {}},
                         lambda kind, name: ("bakery-web",) if (kind, name) == bank else ())
        self.run_ci("--all", "--app", "web", loading=loading, lab=str(self.repo))
        calls = {name: argv for name, argv, _ in self.recorder.calls}
        self.assertEqual(calls["build"][-2:], ["--app", "web"])
        self.assertEqual(calls["test"][-2:], ["--app", "web"])
        self.assertEqual(calls["scan"][-2:], ["--app", "web"])
        self.assertEqual(json.loads(calls["build"][2]), ["playground:bank"])
        self.assertEqual(json.loads(calls["manifests"][2]), ["playground:bank"])

    def test_the_stages_are_given_the_one_reading_of_the_units(self):
        loading = loader()
        seen = []

        def stage(argv, out, err, **seams):
            seen.append(ci.load(argv[0], [("addon", "plain")]))
            return 0
        self.change({"addons/plain/compose.yml": COMPOSE.format(name="plain") + "# 1\n"})
        self.run_ci(stages={"manifests": stage, "build": stage, "test": stage, "smoke": stage, "scan": stage, "gitleaks": stage},
                    loading=loading)
        self.assertEqual(len(loading.asked), 1)   # read once for the whole run
        self.assertEqual([each.name for each in seen[0].units], ["plain"])
        self.assertIsNone(ci.LOADER)   # and put back when the run ends


class Levels(Fixture):

    def setUp(self):
        super().setUp()
        self.change({"playgrounds/bank/apps/web/app.py": "print(2)\n"})

    def test_smoke_is_not_asked_below_level_two(self):
        with mock.patch.object(run, "level", lambda: 1):
            self.run_ci()
        self.assertEqual(self.recorder.names, ["manifests", "build", "test", "gitleaks"])
        self.assertRegex(self.out.getvalue(), r"- smoke\s+not asked: not a stage of standard level 1")
        self.assertTrue(self.out.getvalue().endswith("All good\n"))

    def test_smoke_runs_from_level_two_when_it_is_in_this_lab(self):
        with mock.patch.object(run, "level", lambda: 2):
            self.run_ci()
        self.assertEqual(self.recorder.names, ["manifests", "build", "test", "smoke", "gitleaks"])
        self.assertEqual(self.recorder.calls[3][1][1:], ["--units", json.dumps(["playground:bank", "setup:pair"])])

    def test_smoke_that_is_not_shipped_is_said_and_skipped(self):
        with mock.patch.object(run, "level", lambda: 2):
            code = self.run_ci(stages={**self.recorder.stages(*run.ORDER), "smoke": None})
        self.assertEqual(code, 0)
        self.assertRegex(self.out.getvalue(), r"- smoke\s+skipped: this stage is not in this Lab yet")
        self.assertNotIn("All good", self.out.getvalue())

    def test_the_scan_of_the_images_built_is_not_asked_below_level_six_and_runs_from_it(self):
        with mock.patch.object(run, "level", lambda: 5):
            self.run_ci()
        self.assertNotIn("scan", self.recorder.names)
        self.assertRegex(self.out.getvalue(), r"- scan\s+not asked: not a stage of standard level 5")
        self.recorder, self.out = Recorder(), io.StringIO()
        with mock.patch.object(run, "level", lambda: 6):
            self.run_ci()
        self.assertEqual(self.recorder.names, ["manifests", "build", "test", "smoke", "scan", "gitleaks"])

    def test_the_scan_step_is_the_stage_scan_built_of_the_command_line(self):
        self.assertEqual(run.STAGE_OF["scan"], "scan-built")
        self.assertEqual(ci.STAGES["scan-built"], ("scan", "built_main"))
        from lab_ui.catalogs.ci import scan
        self.assertIs(run.stage_function("scan"), scan.built_main)

    def test_the_level_is_the_standards_and_one_while_it_is_not_here(self):
        self.assertIsInstance(run.level(), int)
        self.assertGreaterEqual(run.level(), 1)
        with mock.patch("importlib.import_module", side_effect=ModuleNotFoundError("no", name="lab_ui.catalogs.standard")):
            self.assertEqual(run.level(), 1)

    def test_the_real_stage_table_names_a_module_for_each_stage_and_the_missing_ones_answer_not_available(self):
        out, err = io.StringIO(), io.StringIO()
        for stage in ("smoke", "publish-plan", "publish", "release-notes", "links", "scan-published", "e2e-plan", "e2e-gate"):
            module_name, function = ci.STAGES[stage]
            if run.stage_function(stage) is None:
                self.assertEqual(ci_main([stage], out, err), 2)
                self.assertIn(f"{stage} is not available yet in this Lab.", err.getvalue())


class Ends(Fixture):

    def setUp(self):
        super().setUp()
        self.change({"playgrounds/bank/apps/web/app.py": "print(2)\n"})

    def test_a_build_that_fails_skips_the_tests_and_ends_with_1(self):
        self.recorder.codes["build"] = 1
        self.assertEqual(self.run_ci(), 1)
        self.assertEqual(self.recorder.names, ["manifests", "build", "smoke", "gitleaks"])
        self.assertRegex(self.out.getvalue(), r"- test\s+skipped: the build did not end well, so there is no image to test")
        self.assertRegex(self.out.getvalue(), r"- scan\s+skipped: the build did not end well, so there is no image to scan")
        self.assertIn("✗ build", self.out.getvalue())
        self.assertIn("1 stage to fix.", self.out.getvalue())

    def test_every_stage_runs_even_when_an_earlier_one_found_something(self):
        self.recorder.codes["manifests"] = 1
        self.assertEqual(self.run_ci(), 1)
        self.assertEqual(self.recorder.names, ["manifests", "build", "test", "smoke", "scan", "gitleaks"])

    def test_a_stage_the_machine_cannot_run_is_exit_2(self):
        self.recorder.codes["build"] = 2
        self.assertEqual(self.run_ci(), 2)
        self.assertIn("! build", self.out.getvalue())
        self.assertIn("Not everything could run on this machine.", self.out.getvalue())

    def test_a_scan_that_cannot_run_here_is_skipped_with_its_reason_and_does_not_fail_the_run(self):
        for said in ("Docker is not installed or not on the PATH: this stage runs containers",
                     "Docker is not running: start Docker Desktop (or the docker service), then run it again",
                     "the gitleaks image of the Lab is not pinned yet (its digest is the placeholder of zeros): write the digest"):
            with self.subTest(said=said[:30]):
                self.out, self.err = io.StringIO(), io.StringIO()
                self.recorder = Recorder({"gitleaks": 2}, {"gitleaks": f"catalog.py ci: {said}\n"})
                self.assertEqual(self.run_ci(), 0)
                self.assertRegex(self.out.getvalue(), r"- gitleaks\s+skipped: " + re.escape(said[:40]))
                self.assertNotIn("All good", self.out.getvalue())
                self.assertTrue(self.out.getvalue().endswith("1 stage skipped.\n"))
                self.assertEqual(self.err.getvalue(), "")   # (the reason is in the line of the stage)

    def test_the_scan_of_the_images_that_cannot_run_here_is_skipped_with_its_reason_as_the_scan_of_the_commits_is(self):
        said = "the trivy image of the Lab is not pinned yet (its digest is the placeholder of zeros): write the digest"
        self.recorder = Recorder({"scan": 2}, {"scan": f"catalog.py ci: {said}\n"})
        self.assertEqual(self.run_ci(), 0)
        self.assertRegex(self.out.getvalue(), r"- scan\s+skipped: " + re.escape(said[:40]))
        self.assertTrue(self.out.getvalue().endswith("1 stage skipped.\n"))
        self.out, self.err = io.StringIO(), io.StringIO()
        self.recorder = Recorder({"scan": 1})
        self.assertEqual(self.run_ci(), 1)
        self.assertIn("✗ scan", self.out.getvalue())

    def test_a_scan_that_cannot_run_for_any_other_reason_is_a_stage_that_could_not_run_with_its_own_words(self):
        for said in ("the report of the scan is not JSON", "gitleaks did not run (exit 1): fatal: bad revision",
                     "the gitleaks image of the Lab is not pinned by digest: ghcr.io/x", "the gitleaks image could not be pulled: x", ""):
            with self.subTest(said=said[:30]):
                self.out, self.err = io.StringIO(), io.StringIO()
                self.recorder = Recorder({"gitleaks": 2}, {"gitleaks": f"catalog.py ci: {said}\n" if said else ""})
                self.assertEqual(self.run_ci(), 2)
                self.assertRegex(self.out.getvalue(), r"! gitleaks\s+could not run")
                self.assertNotIn("skipped", self.out.getvalue())
                self.assertIn("Not everything could run on this machine.", self.out.getvalue())
                self.assertEqual(self.err.getvalue(), f"catalog.py ci: {said}\n" if said else "")

    def test_what_the_scan_says_on_a_run_that_worked_is_shown(self):
        self.recorder = Recorder({}, {"gitleaks": "a word of the scan\n"})
        self.assertEqual(self.run_ci(), 0)
        self.assertEqual(self.err.getvalue(), "a word of the scan\n")

    def test_the_words_that_make_a_scan_a_skip_are_the_ones_the_stages_say(self):
        from lab_ui.catalogs.ci import gitleaks
        self.assertEqual(ci.SKIPPABLE, (ci.NO_DOCKER, ci.DOCKER_DOWN, ci.NOT_PINNED_YET))
        fake = Path(self.holder, "Dockerfile")
        fake.write_text(f"FROM ghcr.io/example/gitleaks:v8@sha256:{'0' * 64} AS gitleaks\n")
        with self.assertRaises(ci.Unavailable) as raised:
            gitleaks.image(fake)
        self.assertIsNotNone(run.not_here(str(raised.exception)))
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent-bin"}), self.assertRaises(ci.Unavailable) as raised:
            ci.docker_ready()
        self.assertIsNotNone(run.not_here(str(raised.exception)))
        fake.write_text("FROM ghcr.io/example/gitleaks:v8 AS gitleaks\n")
        with self.assertRaises(ci.Unavailable) as raised:
            gitleaks.image(fake)
        self.assertIsNone(run.not_here(str(raised.exception)))   # (a Dockerfile that is wrong is a fault to show)

    def test_a_unit_the_trust_policy_refuses_ends_the_run_before_anything_is_built(self):
        def refusing(folder, names):
            return ci.Loading([], [ci.Refused(kind, name, ci.unit_path(kind, name), ("Runs with full privileges",), "T03")
                                   for kind, name in names], [])
        self.assertEqual(self.run_ci(loading=refusing), 1)
        self.assertEqual(self.recorder.calls, [])
        self.assertIn("playground:bank was not tried: Runs with full privileges. Fix: make check says how.", self.out.getvalue())
        self.assertIn("✗ units", self.out.getvalue())

    def test_a_pull_request_of_too_many_apps_is_the_same_bd06_here(self):
        files = {f"playgrounds/big/apps/app{n}/Dockerfile": "FROM busybox:1.36\n" for n in range(21)}
        self.change({**files, "playgrounds/big/compose.yml": COMPOSE.format(name="big")})
        self.out = io.StringIO()
        self.recorder.calls.clear()
        self.assertEqual(self.run_ci(), 1)
        self.assertIn("BD06 Split this pull request: it changes 22 apps, the limit is 20.", self.out.getvalue())   # (21 and the app of bank)
        self.assertEqual(self.recorder.calls, [])

    def test_a_folder_that_is_not_a_folder_is_a_usage_error(self):
        self.assertEqual(run.main(["/nonexistent-folder-xyz"], self.out, self.err), 2)

    def test_an_app_no_unit_builds_is_a_usage_error(self):
        self.assertEqual(self.run_ci("--all", "--app", "ghost"), 2)
        self.assertIn("no app named ghost", self.err.getvalue())


class Same(Fixture):
    """`make ci` before the push and the pull request after it plan the same units (SG2)."""

    def test_the_units_are_the_ones_the_plan_of_ci_writes_for_the_same_change(self):
        head = self.change({"addons/mock/compose.yml": COMPOSE.format(name="mock") + "# more\n"})
        target = Path(tempfile.mkdtemp(prefix="gravitee-lab-output-")) / "output"
        self.addCleanup(shutil.rmtree, target.parent, True)
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(target)}):
            code = plan.main([str(self.repo), "--event", "pull_request", "--base", self.first, "--head", head, "--github-output"],
                             io.StringIO(), io.StringIO())
        self.assertEqual(code, 0)
        written = dict(line.split("=", 1) for line in target.read_text(encoding="utf-8").splitlines())
        self.run_ci()
        self.assertEqual(self.recorder.calls[0][1][2], json.dumps(json.loads(written["units"])))
        self.assertEqual(json.loads(written["units"]), ["addon:mock", "playground:bank", "setup:pair"])

    def test_the_architecture_is_this_machines_and_only_it(self):
        self.change({"playgrounds/bank/apps/web/app.py": "print(2)\n"})
        self.run_ci()
        self.assertEqual(self.recorder.calls[1][1][-1], PLATFORM)
        self.assertIn(PLATFORM, ("linux/amd64", "linux/arm64"))


class Real(Fixture):
    """The real stages over the fake docker and a fake registry, with the units given by the loader seam."""

    def test_a_change_to_an_app_is_checked_built_tested_and_scanned(self):
        self.fake = FakeDocker(self)
        dockerfile = Path(self.holder, "Dockerfile.tools")
        dockerfile.write_text("FROM ghcr.io/example/gitleaks:v8.0.0@sha256:" + "ab" * 32 + " AS gitleaks\n")
        self.change({"playgrounds/bank/apps/web/app.py": "print(2)\n", "playgrounds/bank/apps/web/tests/test_a.py": ""})
        context = os.path.realpath(self.repo / "playgrounds" / "bank" / "apps" / "web")
        config = {"services": {"bakery-web": {"image": "example.test/lab/bakery-web:1", "build": {"context": context}},
                               "gateway": {"image": "graviteeio/apim-gateway:4.12.0"}}}

        def load(folder, names):
            return ci.Loading([ci.Loaded(kind, name, name, ci.unit_path(kind, name), str(self.repo / "playgrounds" / "bank"),
                                         config if (kind, name) == ("playground", "bank") else {"services": {}},
                                         ("bakery-web",) if (kind, name) == ("playground", "bank") else (), (), None)
                               for kind, name in names], [], [])
        from lab_ui.catalogs.ci import gitleaks, manifests
        script = manifests.script()
        script.RESOLVE = lambda name, port, type=None: [(2, 1, 6, "", ("93.184.216.34", port))]
        registry = Registry(default=200)
        with mock.patch.object(gitleaks, "DOCKERFILE", dockerfile), mock.patch.object(run, "level", lambda: 1):   # smoke needs Compose
            code = self.real(load, script, registry)
        self.assertEqual(code, 0, self.out.getvalue() + self.err.getvalue())
        text = self.out.getvalue()
        for line in ("== manifests", "== build", "== test", "== gitleaks"):
            self.assertIn(line, text)
        build = self.fake.commands("buildx", "build")
        self.assertEqual((len(build), option(build[0], "--platform"), option(build[0], "--tag")),
                         (1, PLATFORM, "example.test/lab/bakery-web:1"))
        self.assertEqual(len([call for call in self.fake.commands("run") if "--entrypoint" in call]), 1)
        self.assertEqual(len([call for call in self.fake.commands("run") if "git" in call]), 1)
        self.assertIn("https://registry-1.docker.io/v2/graviteeio/apim-gateway/manifests/4.12.0", [url for _, url, _ in registry.asked])

    def test_the_real_scan_with_no_digest_yet_is_a_skip_with_that_reason_and_with_a_wrong_dockerfile_a_fault(self):
        from lab_ui.catalogs.ci import gitleaks, manifests
        self.fake = FakeDocker(self)
        dockerfile = Path(self.holder, "Dockerfile.tools")
        script = manifests.script()
        script.RESOLVE = lambda name, port, type=None: [(2, 1, 6, "", ("93.184.216.34", port))]
        self.change({"addons/plain/README.md": "# Plain\n"})
        for text, code, shown in ((f"FROM ghcr.io/example/gitleaks:v8@sha256:{'0' * 64} AS gitleaks\n", 0,
                                   r"- gitleaks\s+skipped: the gitleaks image of the Lab is not pinned yet"),
                                  ("FROM ghcr.io/example/gitleaks:v8 AS gitleaks\n", 2, r"! gitleaks\s+could not run")):
            with self.subTest(code=code):
                self.out, self.err = io.StringIO(), io.StringIO()
                dockerfile.write_text(text)
                with mock.patch.object(gitleaks, "DOCKERFILE", dockerfile):
                    self.assertEqual(self.real(loader(), script, Registry(default=200)), code)
                self.assertRegex(self.out.getvalue(), shown)
                self.assertEqual("not pinned by digest" in self.err.getvalue(), code == 2)

    def real(self, load, script, registry):
        with mock.patch.object(ci, "LOADER", load):
            return run.main([str(self.repo)], self.out, self.err, lab=str(self.repo), sleep=lambda seconds: None,
                            ask=lambda image: script.exists(image, registry), module=script)


if __name__ == "__main__":
    unittest.main()
