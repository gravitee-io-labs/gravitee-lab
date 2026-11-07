"""The rules the workflows keep, so that no change to one weakens them unseen: the repository is public and runs the code of pull
requests, some of it with a license. Read as text (indentation), so that they need no YAML library. Then the shell the end-to-end
workflow runs, run for real with the inputs a hostile person would type."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The rules every workflow keeps are the Lab's own (the catalog standard's): one module for this test and for
# `catalog.py check`, which applies them to the workflows of a catalog
sys.path.insert(0, str(ROOT / "lab-ui"))
from lab_ui.catalogs.rules.workflows import GENERIC, problems  # noqa: E402

FILES = sorted([*(ROOT / ".github" / "workflows").glob("*.yml"), *(ROOT / ".github" / "actions").glob("*/action.yml")])
E2E = ROOT / ".github" / "workflows" / "e2e.yml"           # a change of the engine tried on a catalog
CI = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
SHA = "0123456789abcdef0123456789abcdef01234567"
BASH = shutil.which("bash")


def indent(line):
    return len(line) - len(line.lstrip(" "))


def jobs(text):
    """{job name: its text} of a workflow: the keys of `jobs:` at two spaces."""
    after = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([\w-]+):\s*$", after, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def steps(body):
    """The steps of a job, each as its text: a step starts with a dash at six spaces."""
    parts = re.split(r"(?m)^      - ", body)
    return ["      - " + part for part in parts[1:]]


def events(text):
    """What starts a workflow: the keys directly under `on:`, where the rules look for a name and not for a word."""
    return re.findall(r"(?m)^  ([\w-]+):", re.search(r"(?ms)^on:\n(.*?)^\S", text).group(1))


def script(step):
    """The shell a step runs, as the runner receives it: the block under `run: |`, with its indentation taken off."""
    lines = step.splitlines()
    at = next(i for i, line in enumerate(lines) if re.match(r"^\s+(?:- )?run:\s*[|>]\s*$", line))
    depth = indent(lines[at])
    block = []
    for line in lines[at + 1:]:
        if line.strip() and indent(line) <= depth:
            break
        block.append(line)
    margin = min(indent(line) for line in block if line.strip())
    return "\n".join(line[margin:] for line in block) + "\n"


def step_with(body, marker):
    """The one step of a job whose text holds `marker`."""
    found = [one for one in steps(body) if marker in one]
    assert len(found) == 1, f"{len(found)} steps hold {marker!r}"
    return found[0]


class WorkflowsTest(unittest.TestCase):
    def test_every_workflow_and_action_keeps_the_rules_of_any_workflow(self):
        """No pull_request_target, every action pinned to a commit, the token read-only, no `${{ }}` in a script, a timeout and a
        concurrency group, every checkout forgets its credentials, no cache, no hidden construct."""
        found = {file.relative_to(ROOT).as_posix(): file.read_text(encoding="utf-8") for file in FILES}
        for path, text in found.items():
            with self.subTest(file=path):
                self.assertEqual([each.message for each in problems(path, text, GENERIC)], [])


class E2ETest(unittest.TestCase):
    """e2e.yml: a change of the engine, tried on the catalog this engine lists first. The license is the one thing it must not leak."""

    def setUp(self):
        self.text = E2E.read_text(encoding="utf-8")
        self.jobs = jobs(self.text)

    def test_a_pull_request_the_night_and_a_maintainer_start_it_and_a_push_never_does(self):
        self.assertEqual(sorted(events(self.text)), ["pull_request", "schedule", "workflow_dispatch"])

    def test_no_branch_of_this_repository_is_how_a_foreign_commit_gets_the_license(self):
        # A push runs every workflow of the pushed commit with the secrets: the commit of a fork is a pull request, tried from main
        self.assertNotIn("e2e/", self.text)
        self.assertNotRegex(self.text, r"(?m)^\s+push:")

    def test_a_pull_request_is_data_of_the_workflow_that_is_always_mains(self):
        script_text = script(step_with(self.jobs["prepare"], "id: prepare"))
        self.assertIn("refs/heads/main", script_text)
        self.assertIn("refs/pull/$PR/head", script_text)

    def test_the_license_is_given_to_the_one_job_that_waits_for_a_maintainer(self):
        found = {name for name, body in self.jobs.items() if re.search(r"secrets\.(?!CATALOG_DEPLOY_KEY)", body)}
        self.assertEqual(found, {"e2e"})
        body = self.jobs["e2e"]
        self.assertIn("environment:", body)
        self.assertIn("e2e-nightly", body)
        for name, other in self.jobs.items():
            if name != "e2e":
                self.assertNotIn("environment:", other, "no other job holds a secret")
                self.assertNotIn("GRAVITEE_LICENSE", other)

    def test_the_other_secret_is_a_key_that_only_reads_and_only_a_checkout_takes(self):
        taken = 0
        for name, body in self.jobs.items():
            for step in steps(body):
                for line in step.splitlines():
                    if "secrets.CATALOG_DEPLOY_KEY" in line:
                        taken += 1
                        with self.subTest(job=name, line=line.strip()):
                            self.assertIn("actions/checkout@", step)
                            self.assertRegex(line, r"^\s+ssh-key: \$\{\{ secrets\.\w+_DEPLOY_KEY \}\}$")
        self.assertEqual(taken, 3, "the key in each of the three jobs that check the catalog out")

    def test_the_catalog_is_given_no_allowance_to_use_this_machine(self):
        self.assertNotIn("--allow-machine", self.text)

    def test_the_license_reaches_the_step_that_writes_it_and_no_other(self):
        self.assertEqual(len([step for step in steps(self.jobs["e2e"]) if "secrets.GRAVITEE_LICENSE" in step]), 1)
        self.assertEqual(len(re.findall(r"secrets\.GRAVITEE_LICENSE", self.text)), 1)

    def test_the_license_is_written_where_the_engine_is_and_after_the_change_was_checked(self):
        body = self.jobs["e2e"]
        names = [re.search(r"name: (.+)", one).group(1) if "name:" in one else one.splitlines()[0] for one in steps(body)]
        license_step = next(i for i, one in enumerate(steps(body)) if "secrets.GRAVITEE_LICENSE" in one)
        check = next(i for i, one in enumerate(steps(body)) if "The change is the one that was read" in one)
        self.assertLess(check, license_step, "the commit is checked before anything holds the license")
        self.assertIn("working-directory: lab", steps(body)[license_step])
        self.assertTrue(all("GRAVITEE_LICENSE" not in one for i, one in enumerate(steps(body)) if i != license_step
                            and "secrets." not in one and "Try it" not in one and "not set in this environment" not in one), names)

    def test_a_fork_never_runs_what_has_the_license(self):
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.jobs["e2e"])

    def test_the_change_is_read_from_the_history_of_the_engine_and_nothing_else_is_fetched_whole(self):
        """`git diff base...head` needs the history of the engine: a depth of 1 there fails every plan that reads a change, and a
        full history of the catalog costs a download for nothing."""
        checkouts = {re.search(r"(?m)^          path: (\w+)$", one).group(1): one for one in steps(self.jobs["plan"]) if "actions/checkout@" in one}
        self.assertEqual(sorted(checkouts), ["catalog", "lab"])
        self.assertEqual(re.findall(r"(?m)^          fetch-depth: (.+?)(?:\s+#.*)?$", checkouts["lab"]), ["0"])
        self.assertNotIn("fetch-depth", checkouts["catalog"])
        for name in ("manifests", "e2e", "result"):
            self.assertNotIn("fetch-depth", self.jobs[name], f"{name} reads no change")

    def test_the_matrix_is_what_the_plan_made(self):
        for name in ("manifests", "e2e"):
            self.assertIn("fromJSON(needs.plan.outputs.matrix)", self.jobs[name])

    def test_a_step_the_jobs_repeat_is_the_same_step_in_each_and_the_shell_tests_run_the_one_of_all(self):
        def plain(step):
            """The step without its comments, its blank lines and the depth of its history."""
            return "\n".join(line for line in step.splitlines() if line.strip() and not line.lstrip().startswith("#")
                              and "fetch-depth:" not in line)
        for marker, where in (("The change is the one that was read", ("plan", "e2e")),
                              ("Link the catalog into the engine", ("plan", "manifests", "e2e"))):
            with self.subTest(step=marker):
                self.assertEqual(len({plain(step_with(self.jobs[name], marker)) for name in where}), 1)
        checkouts = [[one for one in steps(self.jobs[name]) if "actions/checkout@" in one] for name in ("plan", "manifests", "e2e")]
        self.assertEqual([len(one) for one in checkouts], [2, 2, 2], "the engine and the catalog")
        for index in range(2):
            with self.subTest(checkout=index):
                self.assertEqual(len({plain(found[index]) for found in checkouts}), 1, "each job checks the same repository out")

    def test_only_the_night_may_write(self):
        writers = [name for name, body in self.jobs.items() if re.search(r"(?m)^\s+[a-z-]+: write$", body)]
        self.assertEqual(writers, ["nightly"])
        self.assertNotRegex(self.text.split("\njobs:\n", 1)[0], r"(?m)^\s+[a-z-]+: write$")

    def test_an_engine_without_a_catalog_to_try_says_so_and_runs_nothing(self):
        self.assertIn("if: needs.prepare.outputs.skip != 'true'", self.jobs["plan"])
        self.assertIn("The variable CATALOG_REPOSITORY is not set", self.text)

    def test_the_check_to_require_is_named_as_it_always_was_and_waits_for_everything(self):
        """The name that the documentation, a ruleset and the audit of the settings ask for, which always reports."""
        self.assertEqual(list(self.jobs), ["prepare", "plan", "manifests", "e2e", "result", "nightly"])
        body = self.jobs["result"]
        self.assertEqual(len(re.findall(r"(?m)^    name: E2E result$", self.text)), 1, "no other job of this file bears the name")
        self.assertIn("name: E2E result", body)
        self.assertIn("needs: [prepare, plan, manifests, e2e]", body)
        self.assertIn("always() &&", body, "it reports when a job failed, was cancelled or was refused")
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", body, "none for a fork, as for the e2e job")
        self.assertNotIn("environment:", body)
        self.assertNotIn("secrets.", body)
        self.assertEqual(sorted(re.findall(r"\$\{\{ (needs\.[\w.]+) \}\}", body)),
                         ["needs.e2e.result", "needs.manifests.result", "needs.plan.outputs.count", "needs.plan.result",
                          "needs.prepare.outputs.skip", "needs.prepare.result"])


# The shell of the workflows, run with the inputs a hostile person would type. GitHub runs it with `bash -e`.

@unittest.skipUnless(BASH, "bash is not installed")
class ShellTest(unittest.TestCase):
    def setUp(self):
        self.holder = tempfile.TemporaryDirectory()
        self.addCleanup(self.holder.cleanup)
        self.folder = Path(self.holder.name)
        self.output = self.folder / "output"
        self.output.write_text("", encoding="utf-8")
        self.bin = self.folder / "bin"
        self.bin.mkdir()
        # python3 is a program that says how it was called, and ends as it is told to
        stub = self.bin / "python3"
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$STUB_LOG"\nexit "${STUB_EXIT:-0}"\n', encoding="utf-8")
        stub.chmod(0o755)
        self.log = self.folder / "called"

    def execute(self, text, env, stub_exit=0):
        base = {"PATH": f"{self.bin}:/usr/bin:/bin", "GITHUB_OUTPUT": str(self.output), "STUB_LOG": str(self.log),
                "STUB_EXIT": str(stub_exit), "HOME": str(self.folder)}
        return subprocess.run([BASH, "--noprofile", "--norc", "-e", "-c", text], env={**base, **env}, cwd=self.folder,
                              capture_output=True, text=True, timeout=60)

    def outputs(self):
        return dict(line.split("=", 1) for line in self.output.read_text(encoding="utf-8").splitlines())

    def called(self):
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else None


class PrepareTest(ShellTest):
    """What an event and a maintainer's words become before anything is checked out: the first job of e2e.yml."""

    ENV = {"EVENT": "pull_request", "REF": "refs/pull/7/merge", "HEAD": SHA, "BASE": "b" * 40, "PR": "", "SHA": "", "ITEMS": "",
           "CATALOG_REPOSITORY": "owner/catalog", "CATALOG_REF": "c" * 40}

    def prepare(self, **env):
        text = E2E.read_text(encoding="utf-8")
        return self.execute(script(step_with(jobs(text)["prepare"], "id: prepare")), {**self.ENV, **env})

    def test_a_pull_request_of_the_engine_is_tried_where_it_changes_the_catalog_s_known_good_commit(self):
        done = self.prepare()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.outputs(), {"lab_ref": SHA, "catalog_ref": "c" * 40, "items": "changed", "base": "b" * 40, "expect_sha": "",
                                          "skip": "false"})

    def test_the_night_tries_the_canary_on_the_default_branch_of_the_catalog(self):
        done = self.prepare(EVENT="schedule", REF="refs/heads/main", BASE="")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.outputs(), {"lab_ref": SHA, "catalog_ref": "", "items": "canary", "base": "", "expect_sha": "", "skip": "false"})

    def test_a_maintainer_who_read_a_pull_request_tries_that_commit_from_main(self):
        done = self.prepare(EVENT="workflow_dispatch", REF="refs/heads/main", BASE="", PR="123", SHA="d" * 40, ITEMS="canary")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.outputs()["lab_ref"], "refs/pull/123/head")
        self.assertEqual(self.outputs()["expect_sha"], "d" * 40)
        self.assertEqual(self.outputs()["items"], "canary")

    def test_a_dispatch_with_nothing_typed_tries_the_canary_of_the_commit_it_runs_on(self):
        done = self.prepare(EVENT="workflow_dispatch", REF="refs/heads/main", BASE="")
        self.assertEqual((done.returncode, self.outputs()["items"], self.outputs()["lab_ref"]), (0, "canary", SHA))

    def test_a_pull_request_is_tried_from_main_and_from_nowhere_else(self):
        done = self.prepare(EVENT="workflow_dispatch", REF="refs/heads/e2e/7", PR="7", SHA="d" * 40)
        self.assertEqual(done.returncode, 1)
        self.assertIn("E2E02", done.stdout)
        self.assertEqual(self.outputs(), {})

    def test_what_is_not_a_number_a_commit_or_words_stops_everything(self):
        hostile = [{"PR": "12a", "SHA": "d" * 40}, {"PR": "12345678", "SHA": "d" * 40}, {"PR": "1; echo hacked", "SHA": "d" * 40},
                   {"PR": "7", "SHA": "short"}, {"PR": "7", "SHA": "D" * 40}, {"PR": "7", "SHA": ""}, {"PR": "7", "SHA": "d" * 41},
                   {"ITEMS": "addon:a; echo hacked >&2"}, {"ITEMS": "addon:a $(id)"}, {"ITEMS": "addon:a\n::set-output name=x::y"},
                   {"ITEMS": "`id`"}, {"ITEMS": "x" * 501}, {"ITEMS": "a;b"}, {"ITEMS": "a|b"}, {"ITEMS": "a&b"}, {"ITEMS": "a>b"},
                   {"CATALOG_REF": "-x"}, {"CATALOG_REF": "a b"}, {"CATALOG_REF": "x\ny"}, {"CATALOG_REF": "a;b"}, {"CATALOG_REF": "$(id)"}]
        for env in hostile:
            with self.subTest(env=env):
                self.output.write_text("", encoding="utf-8")
                done = self.prepare(EVENT="workflow_dispatch", REF="refs/heads/main", BASE="", **env)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn("::error title=E2E04::", done.stdout)
                self.assertNotIn("hacked", done.stderr + self.output.read_text(encoding="utf-8"))
                self.assertEqual(self.outputs(), {}, "nothing is passed on")

    def test_words_that_name_items_pass_with_their_catalog_and_their_spaces(self):
        done = self.prepare(EVENT="workflow_dispatch", REF="refs/heads/main", BASE="", ITEMS="playground:a addon:@hub/b  setup:c")
        self.assertEqual((done.returncode, self.outputs()["items"]), (0, "playground:a addon:@hub/b  setup:c"))

    def test_an_engine_with_no_catalog_to_try_says_so_and_goes_on_to_nothing(self):
        done = self.prepare(CATALOG_REPOSITORY="", CATALOG_REF="")
        self.assertEqual(done.returncode, 0)
        self.assertIn("::notice title=E2E not tried::The variable CATALOG_REPOSITORY is not set", done.stdout)
        self.assertEqual(self.outputs()["skip"], "true")

    def test_the_outputs_are_six_lines_whatever_the_event(self):
        self.prepare()
        self.assertEqual(len(self.output.read_text(encoding="utf-8").splitlines()), 6)


class PlanShellTest(ShellTest):
    """The step that asks for the matrix: which of its modes, and the words that follow."""

    def matrix(self, **env):
        text = E2E.read_text(encoding="utf-8")
        base = {"ITEMS": "changed", "BASE": ""}
        done = self.execute(script(step_with(jobs(text)["plan"], "id: matrix")), {**base, **env})
        self.assertEqual(done.returncode, 0, done.stderr)
        return self.called()

    def test_what_a_change_of_the_engine_touches(self):
        self.assertEqual(self.matrix(), ["-I", "lab/.github/scripts/e2e_matrix.py", "--changed", "origin/main", "HEAD", "--github-output"])
        self.assertEqual(self.matrix(BASE="b" * 40)[2:5], ["--changed", "b" * 40, "HEAD"])

    def test_the_canary(self):
        self.assertEqual(self.matrix(ITEMS="canary")[2:], ["--canary", "--github-output"])

    def test_the_items_a_person_named_come_after_two_dashes_and_no_word_is_a_pattern_for_files(self):
        (self.folder / "a1").write_text("", encoding="utf-8")
        got = self.matrix(ITEMS="--all playground:a* addon:b  setup:@hub/c")
        self.assertEqual(got[2:], ["--github-output", "--items", "--", "--all", "playground:a*", "addon:b", "setup:@hub/c"])


class StepsShellTest(ShellTest):
    """The steps that check the commit, and try the item."""

    def step(self, job, marker):
        return script(step_with(jobs(E2E.read_text(encoding="utf-8"))[job], marker))

    def test_not_tried_is_a_warning_and_a_failure_is_one(self):
        text = self.step("e2e", "Try it")
        for code, wanted in ((0, 0), (1, 1), (2, 0), (3, 3), (130, 130)):
            with self.subTest(code=code):
                done = self.execute(text, {"ITEM": "playground:x", "TESTED": ""}, stub_exit=code)
                self.assertEqual(done.returncode, wanted)
        self.execute(text, {"ITEM": "playground:@hub/x", "TESTED": "1"})
        self.assertEqual(self.called(), ["-I", "lab-ui/server.py", "--e2e", "playground:@hub/x", "--tested", "1"])
        self.execute(text, {"ITEM": "playground:x", "TESTED": ""})
        self.assertEqual(self.called(), ["-I", "lab-ui/server.py", "--e2e", "playground:x"], "no set: the defaults of the core")

    def test_the_commit_that_was_read_is_the_one_that_is_tried(self):
        if not shutil.which("git"):
            self.skipTest("git is not installed")
        text = self.step("plan", "The change is the one that was read")
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        subprocess.run([*git, "init", "-q", "lab"], cwd=self.folder, check=True, env=env)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "lab"], cwd=self.folder / "lab", check=True, env=env)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.folder / "lab", capture_output=True, text=True, env=env).stdout.strip()
        for sha, wanted in ((head, 0), ("f" * 40, 1)):
            with self.subTest(sha=sha[:7]):
                done = self.execute(text, {"EXPECT_SHA": sha})
                self.assertEqual(done.returncode, wanted, done.stdout + done.stderr)
                if wanted:
                    self.assertIn("::error title=E2E03::The change is", done.stdout)
                    self.assertIn("Fix: read the new commits", done.stdout)

    def test_the_license_step_refuses_to_go_on_without_a_license_and_names_no_value(self):
        text = self.step("e2e", "secrets.GRAVITEE_LICENSE")
        make = self.folder / "make"
        make.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        make.chmod(0o755)
        (self.folder / "lab").mkdir()
        done = self.execute(f"cd lab\n{text}", {"GRAVITEE_LICENSE": ""})
        self.assertEqual(done.returncode, 1)
        self.assertIn("The secret GRAVITEE_LICENSE is not set in this environment", done.stdout)
        done = self.execute(f"cd lab\n{text}", {"GRAVITEE_LICENSE": "s3cr3t-value", "PATH": f"{self.folder}:/usr/bin:/bin"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((self.folder / "lab" / ".env").read_text(encoding="utf-8"), "\nGRAVITEE_LICENSE=s3cr3t-value\n")
        self.assertNotIn("s3cr3t-value", done.stdout + done.stderr)

    def test_the_result_passes_when_nothing_had_to_run_and_fails_when_a_job_that_had_to_run_did_not(self):
        text = self.step("result", "needs.plan.result")
        env = {"PREPARE": "success", "SKIP": "false", "PLAN": "success", "MANIFESTS": "success", "E2E": "success", "COUNT": "2"}
        none = {"PLAN": "skipped", "MANIFESTS": "skipped", "E2E": "skipped", "COUNT": ""}
        for change, wanted in (({}, 0), ({"COUNT": "0", "MANIFESTS": "skipped", "E2E": "skipped"}, 0),
                               ({"COUNT": "2", "E2E": "skipped"}, 1), ({"E2E": "failure"}, 1), ({"MANIFESTS": "cancelled"}, 1),
                               ({"PLAN": "failure", "COUNT": "0"}, 1), ({"COUNT": ""}, 1), ({"COUNT": "x"}, 1), ({"COUNT": "0", "E2E": "failure"}, 1),
                               ({"SKIP": "true", **none}, 0), ({**none}, 1), ({"SKIP": "", **none}, 1),
                               ({"PREPARE": "failure", "SKIP": "", **none}, 1), ({"PREPARE": "cancelled", "SKIP": ""}, 1),
                               ({"PREPARE": "failure", "SKIP": "true", **none}, 1), ({"PREPARE": "skipped"}, 1)):
            with self.subTest(change=change):
                done = self.execute(text, {**env, **change})
                self.assertEqual(done.returncode, wanted, done.stdout + done.stderr)

    def test_the_result_says_why_it_failed_and_that_nothing_was_tried(self):
        text = self.step("result", "needs.plan.result")
        none = {"PLAN": "skipped", "MANIFESTS": "skipped", "E2E": "skipped", "COUNT": ""}
        for env, said in (({"PREPARE": "success", "SKIP": "false", "PLAN": "success", "MANIFESTS": "success", "E2E": "failure", "COUNT": "1"},
                           "::error::A job ended failure"),
                          ({"PREPARE": "failure", "SKIP": "", **none}, "::error::The inputs were refused"),
                          ({"PREPARE": "success", "SKIP": "true", **none}, "::notice::There is no catalog to try, so nothing was tried")):
            with self.subTest(said=said):
                self.assertIn(said, self.execute(text, env).stdout)


class NamesTest(unittest.TestCase):
    """What GitHub shows is read by people: every job and every step of the engine's workflows and actions, and of the managed
    files a catalog gets, has a name in words (an unnamed step shows as `Run actions/checkout@<commit>`)."""

    def test_every_job_and_every_step_is_named(self):
        managed = ROOT / "lab-ui" / "templates" / "catalog" / "managed" / ".github"
        for path in [*FILES, *sorted(managed.glob("workflows/*.yml")), *sorted(managed.glob("actions/*/action.yml"))]:
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=path.relative_to(ROOT).as_posix()):
                steps = unnamed_steps(text)
                self.assertTrue(steps[0], "steps are read")
                self.assertEqual(steps[1], [], "every step has a name")
                if path.parent.name == "workflows":
                    for job, block in jobs(text).items():
                        self.assertRegex(block, r"(?m)^    name: \S", f"the job {job} has a name")


def unnamed_steps(text):
    """(how many steps, the first line of each one with no name) of a workflow or an action: a step is an item of a `steps:` list."""
    lines, count, unnamed, depth = text.split("\n"), 0, [], None
    for i, line in enumerate(lines):
        found = re.match(r"^( *)steps:\s*$", line)
        if found:
            depth = len(found.group(1))
            continue
        item = re.match(r"^( *)- \S", line)
        if depth is not None and line.strip() and len(line) - len(line.lstrip()) <= depth and not item:
            depth = None
        if depth is None or not item or len(item.group(1)) <= depth:
            continue
        indent, block = len(item.group(1)), [line]
        for rest in lines[i + 1:]:
            if rest.strip() and len(rest) - len(rest.lstrip()) <= indent:
                break
            block.append(rest)
        count += 1
        if not re.match(r"^ *- name: ", line) and not any(re.match(r"^ {%d}name: " % (indent + 2), each) for each in block[1:]):
            unnamed.append(line.strip())
    return count, unnamed


class CiResultTest(ShellTest):
    """`CI result`, the check a ruleset asks for: it waits for every other job of ci.yml, and passes only when all ended in success."""

    def test_it_waits_for_every_other_job(self):
        found = jobs(CI.read_text(encoding="utf-8"))
        needs = re.search(r"(?m)^    needs: \[(.+)\]$", found["result"]).group(1).split(", ")
        self.assertEqual(sorted(needs), sorted(name for name in found if name != "result"))
        self.assertIn("if: ${{ always() }}", found["result"])

    def test_it_fails_on_anything_but_success(self):
        text = script(step_with(jobs(CI.read_text(encoding="utf-8"))["result"], "RESULTS"))
        for results, wanted in (("success success", 0), ("success failure", 1), ("cancelled", 1), ("success skipped", 1)):
            with self.subTest(results=results):
                self.assertEqual(self.execute(text, {"RESULTS": results}).returncode, wanted)


class LabImagesTest(ShellTest):
    """lab-images.yml: the seeder's image, under the name units find it by."""

    def test_the_seeder_is_built_for_both_platforms_with_its_tags_and_labels(self):
        text = (ROOT / ".github" / "workflows" / "lab-images.yml").read_text(encoding="utf-8")
        stub = self.bin / "docker"
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$STUB_LOG"\n', encoding="utf-8")
        stub.chmod(0o755)
        done = self.execute(script(step_with(jobs(text)["images"], "buildx build")),
                            {"GITHUB_REPOSITORY": "Owner/Lab", "GITHUB_SHA": "0123456789", "GITHUB_SERVER_URL": "https://git.example"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.called(), ["buildx", "build", "--platform", "linux/amd64,linux/arm64", "--push",
                                         "-t", "ghcr.io/owner/lab/seeder:latest", "-t", "ghcr.io/owner/lab/seeder:sha-0123456",
                                         "--label", "org.opencontainers.image.licenses=AGPL-3.0-only",
                                         "--label", "org.opencontainers.image.source=https://git.example/Owner/Lab", "seeder"])

    def test_it_is_the_name_the_core_gives(self):
        core = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertRegex(core, r"(?m)^  seeder:\n    image: ghcr\.io/[^/\s]+/[^/\s]+/seeder\s")


class ReleaseNotesTest(unittest.TestCase):
    """.github/release.yml: the changelog GitHub writes for a release, one section for each label. "For catalogs" is the compatibility
    matrix of the Lab: what a catalog may use from that version on."""

    def categories(self):
        text = (ROOT / ".github" / "release.yml").read_text(encoding="utf-8")
        found = re.findall(r"^    - title: (.+)\n      labels: \[(.+)\]$", text, flags=re.M)
        return [(title, [label.strip().strip('"') for label in labels.split(",")]) for title, labels in found]

    def test_what_catalogs_can_use_is_a_section_of_its_own_before_the_new_features(self):
        self.assertEqual(self.categories(), [("Changes that need you to do something", ["major"]),
                                             ("For catalogs", ["catalog-format"]), ("New", ["minor"]),
                                             ("Fixes and improvements", ["*"])],
                         "GitHub puts a pull request in the first section whose label it has: a feature catalogs can use is "
                         "said under For catalogs, a change that needs a person to act stays first")

    def test_every_label_that_raises_a_number_has_its_section_and_is_documented(self):
        labeled = {label for _, labels in self.categories() for label in labels} - {"*"}
        self.assertEqual(labeled, {"major", "minor", "catalog-format"})
        source = (ROOT / ".github" / "scripts" / "next_version.py").read_text(encoding="utf-8")
        maintaining = (ROOT / "docs" / "maintaining.md").read_text(encoding="utf-8")
        for label in labeled:
            self.assertIn(f'"{label}" in labels', source, label)
            self.assertIn(f"| `{label}` |", maintaining, label)


class ReleaseTest(unittest.TestCase):
    """release.yml: every merge to main is a version. The deploy key that may create tags is the one thing it must keep in one step."""

    def setUp(self):
        self.text = RELEASE.read_text(encoding="utf-8")
        self.jobs = jobs(self.text)
        self.body = self.jobs["release"]

    def test_a_push_to_main_starts_it_and_nothing_else(self):
        self.assertEqual(events(self.text), ["push"])
        self.assertIn("    branches: [main]", self.text)
        self.assertIn("cancel-in-progress: false", self.text, "a release that is running is never canceled")

    def test_one_job_waits_in_the_environment_publish_and_writes_the_contents_only(self):
        self.assertEqual(list(self.jobs), ["release"])
        self.assertIn("    environment: publish\n", self.body)
        self.assertIn("!github.event.repository.fork", self.body)
        writes = re.findall(r"^\s+([\w-]+): write$", self.body, flags=re.M)
        self.assertEqual(writes, ["contents"])

    def test_the_deploy_key_reaches_the_step_that_pushes_the_tag_and_no_other(self):
        holders = [step for step in steps(self.body) if "secrets." in step]
        self.assertEqual(len(holders), 1)
        self.assertIn("name: Tag the version", holders[0])
        self.assertIn("if: ${{ steps.version.outputs.tag && !steps.version.outputs.tagged }}", holders[0],
                      "a version tagged already is never pushed again")
        self.assertEqual(re.findall(r"secrets\.(\w+)", self.text), ["RELEASE_DEPLOY_KEY"])
        self.assertNotIn("uses:", holders[0], "no action of anyone else sees the key")
        self.assertIn("StrictHostKeyChecking=yes", holders[0])
        self.assertIn("trap 'rm -rf", holders[0], "the key leaves the runner's disk when the step ends")

    @unittest.skipUnless(BASH and shutil.which("git"), "bash and git are needed")
    def test_the_version_is_worked_out_from_the_last_tag_and_the_labels_of_the_merges(self):
        shell = script(step_with(self.body, "name: Work out the version"))
        top = Path(tempfile.mkdtemp(prefix="release-"))
        self.addCleanup(shutil.rmtree, top, True)
        (top / "bin").mkdir()
        (top / "bin" / "gh").write_text('#!/bin/sh\n[ -z "$LABELS" ] || printf "%s\\n" $LABELS\n', encoding="utf-8")
        (top / "bin" / "gh").chmod(0o755)
        repo = top / "repo"
        shutil.copytree(ROOT / ".github" / "scripts", repo / ".github" / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        env = {**os.environ, "PATH": f"{top / 'bin'}{os.pathsep}{os.environ['PATH']}", "GITHUB_REPOSITORY": "o/r",
               "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

        def git(*args):
            subprocess.run(["git", "-C", str(repo), "-c", "tag.gpgSign=false", "-c", "commit.gpgSign=false", *args],
                           check=True, capture_output=True, env=env)

        def run(labels=""):
            output = top / "output"
            output.write_text("", encoding="utf-8")
            done = subprocess.run([BASH, "-eo", "pipefail", "-c", shell], cwd=repo, capture_output=True, text=True,
                                  env={**env, "LABELS": labels, "GITHUB_OUTPUT": str(output)})
            self.assertEqual(done.returncode, 0, done.stderr)
            return output.read_text(encoding="utf-8").strip()

        git("init", "-q")
        git("commit", "-q", "--allow-empty", "-m", "one")
        self.assertEqual(run("major"), "tag=v1.0.0", "the first version is 1.0.0")
        git("tag", "-a", "v1.0.0", "-m", "1.0.0")
        self.assertEqual(run(), "tag=v1.0.0\ntagged=true", "run again once its tag is pushed: no new tag, the release that may be missing")
        git("commit", "-q", "--allow-empty", "-m", "two")
        self.assertEqual(run(), "tag=v1.0.1")
        self.assertEqual(run("dependencies minor"), "tag=v1.1.0")
        self.assertEqual(run("major"), "tag=v2.0.0")
        git("tag", "v1.1.0-rc1")
        self.assertEqual(run(), "tag=v1.0.1", "a tag of another form is no version, and stops nothing")
        # The run of an older merge, run again once a newer one made its version: that version holds it, nothing is made
        one = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD~1"], capture_output=True, text=True, env=env).stdout.strip()
        git("tag", "-a", "v1.0.1", "-m", "1.0.1")
        git("checkout", "-q", one)
        git("tag", "-d", "v1.0.0")
        self.assertEqual(run("major"), "", "v1.0.1 holds the older merge: no v1.0.0 and no v2.0.0 for it")

    def test_a_release_that_exists_is_not_made_again(self):
        shell = script(step_with(self.body, "name: Publish the release"))
        top = Path(tempfile.mkdtemp(prefix="release-"))
        self.addCleanup(shutil.rmtree, top, True)
        (top / "bin").mkdir()
        calls = top / "calls"
        (top / "bin" / "gh").write_text(f'#!/bin/sh\necho "$*" >> {calls}\n[ "$1 $2" != "release view" ] || exit "$VIEW"\n',
                                        encoding="utf-8")
        (top / "bin" / "gh").chmod(0o755)
        for view, made in (("0", False), ("1", True)):
            with self.subTest(exists=view == "0"):
                calls.write_text("", encoding="utf-8")
                done = subprocess.run([BASH, "-eo", "pipefail", "-c", shell], capture_output=True, text=True,
                                      env={**os.environ, "PATH": f"{top / 'bin'}{os.pathsep}{os.environ['PATH']}", "VIEW": view,
                                           "TAG": "v1.2.3", "GITHUB_REPOSITORY": "o/r"})
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(any(line.startswith("release create v1.2.3") for line in calls.read_text().splitlines()), made)


class ComposeTest(unittest.TestCase):
    """The Lab UI's tests run on the oldest Compose the Lab accepts: what reads otherwise there (Compose 5.0 prints an empty list
    under an x- key as null) fails in CI before a person on that release meets it."""

    def test_the_compose_ci_installs_is_the_oldest_the_lab_accepts(self):
        pinned = re.search(r"default:\s*v(\d+)\.(\d+)\.\d+", (ROOT / ".github/actions/compose/action.yml").read_text())
        minimum = re.search(r"^COMPOSE_MIN = \((\d+), (\d+)\)", (ROOT / "lab-ui/lab_ui/library/machine.py").read_text(), re.M)
        self.assertTrue(pinned and minimum)
        self.assertEqual(pinned.groups(), minimum.groups(), "the release CI installs is the Lab's minimum")

    def test_every_job_that_runs_the_lab_ui_tests_installs_that_release(self):
        jobs = re.split(r"^  (?=[a-z][a-z0-9-]*:$)", CI.read_text().split("\njobs:\n", 1)[1], flags=re.M)
        testing = [job for job in jobs if "lab-ui/tests/run.py" in job]
        self.assertEqual(len(testing), 2, "the whole suite and the lane of Python 3.9")
        for job in testing:
            with self.subTest(job=job.split(":", 1)[0]):
                self.assertIn("uses: ./.github/actions/compose", job)
                self.assertNotRegex(job, r"\n\s+version:", "no other release than the one the action pins")


if __name__ == "__main__":
    unittest.main()
