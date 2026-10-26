"""The rules the workflows keep, so that no change to one weakens them unseen: the repository is public and runs the code of pull
requests, some of it with a license. Read as text (indentation), so that they need no YAML library. Then the shell the end-to-end
workflows run, run for real with the inputs a hostile person would type, and the agreement of the engine's reusable workflow with
the workflow a catalog copies to call it."""

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
WORKFLOWS = [file for file in FILES if file.parent.name == "workflows"]
INDENT = re.compile(r"^( *)")
E2E = ROOT / ".github" / "workflows" / "e2e.yml"           # the engine's own: a change of the engine tried on a catalog
RUN = ROOT / ".github" / "workflows" / "e2e-run.yml"       # the run, which the engine and each catalog call
CI = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
SHA = "0123456789abcdef0123456789abcdef01234567"
BASH = shutil.which("bash")


def indent(line):
    return len(INDENT.match(line).group(1))


def run_blocks(text):
    """(line number, line) of every line a `run:` of a step runs: its value on the line itself, or the block under it."""
    lines, found, i = text.splitlines(), [], 0
    while i < len(lines):
        match = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", lines[i])
        if match:
            if match.group(2) and not match.group(2).startswith(("|", ">")):
                found.append((i + 1, match.group(2)))
            else:
                depth = len(match.group(1)) + (2 if lines[i].lstrip().startswith("- ") else 0)
                j = i + 1
                while j < len(lines) and (not lines[j].strip() or indent(lines[j]) > depth):
                    found.append((j + 1, lines[j]))
                    j += 1
                i = j - 1
        i += 1
    return found


def jobs(text):
    """{job name: its text} of a workflow: the keys of `jobs:` at two spaces."""
    after = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([\w-]+):\s*$", after, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def steps(body):
    """The steps of a job, each as its text: a step starts with a dash at six spaces."""
    parts = re.split(r"(?m)^      - ", body)
    return ["      - " + part for part in parts[1:]]


def children(text, *path):
    """{key: its lines} of the keys that stand directly under a path of keys (`on`, `workflow_call`, `inputs`): the structure of a
    workflow where the rules look for a name and not for a word."""
    lines, start = text.splitlines(), 0
    for depth, key in enumerate(path):
        found = re.compile(rf"^ {{{2 * depth}}}{re.escape(key)}:\s*(?:#.*)?$")
        at = next((i for i in range(start, len(lines)) if found.match(lines[i])), None)
        if at is None:
            raise KeyError("/".join(path))
        start = at + 1
    child, found, current = 2 * len(path), {}, None
    for line in lines[start:]:
        if line.strip() and not line.lstrip().startswith("#") and indent(line) < child:
            break
        match = re.match(rf"^ {{{child}}}([\w-]+):", line)
        if match:
            current = match.group(1)
            found[current] = []
        elif current is not None:
            found[current].append(line)
    return found


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


def texts():
    """Every file the rules read, by name: the workflows and actions of the engine, and the workflow a catalog copies."""
    found = {file.name: file.read_text(encoding="utf-8") for file in FILES}
    return found


class WorkflowsTest(unittest.TestCase):
    def test_there_are_workflows_to_read(self):
        self.assertIn("e2e.yml", [file.name for file in WORKFLOWS])
        self.assertIn("e2e-run.yml", [file.name for file in WORKFLOWS])
        self.assertIn("ci.yml", [file.name for file in WORKFLOWS])

    def test_every_workflow_and_action_keeps_the_rules_of_any_workflow(self):
        """WF01 to WF05, WF07, WF11, WF12, WF14, WF17 and WF19 to WF21: no pull_request_target, every action pinned to a commit, the
        token read-only, no `${{ }}` in a script, a timeout and a concurrency group, every checkout forgets its credentials, no cache,
        no hidden construct. The rules for the lanes of a catalog (LANES) are not asked of the engine's own privileged workflows."""
        found = {file.relative_to(ROOT).as_posix(): file.read_text(encoding="utf-8") for file in FILES}
        for path, text in found.items():
            with self.subTest(file=path):
                self.assertEqual([each.message for each in problems(path, text, GENERIC)], [])

    def test_the_workflows_are_yaml(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        found = {file.name: file.read_text(encoding="utf-8") for file in WORKFLOWS}
        for name, text in found.items():
            with self.subTest(file=name):
                self.assertIn("jobs", yaml.safe_load(text))


class RunTest(unittest.TestCase):
    """e2e-run.yml: the end-to-end run, which the engine and each catalog call. The license is the one thing it must not leak."""

    def setUp(self):
        self.text = RUN.read_text(encoding="utf-8")
        self.jobs = jobs(self.text)

    def test_it_is_called_and_starts_by_none_of_the_events_of_this_repository(self):
        self.assertEqual(sorted(children(self.text, "on")), ["workflow_call"])

    def test_it_has_no_concurrency_of_its_own(self):
        # a group the same as its caller's would cancel the run that is waiting for it
        self.assertNotIn("concurrency:", self.text)

    def test_the_jobs_are_the_plan_the_images_the_item_and_the_result(self):
        self.assertEqual(list(self.jobs), ["plan", "manifests", "e2e", "result"])

    def test_the_license_is_given_to_the_one_job_that_waits_for_a_maintainer(self):
        found = {name for name, body in self.jobs.items() if re.search(r"secrets\.(?!(?:LAB|CATALOG|NEEDS_CATALOG)_DEPLOY_KEY)", body)}
        self.assertEqual(found, {"e2e"})
        body = self.jobs["e2e"]
        self.assertIn("environment:", body)
        self.assertIn("e2e-nightly", body)
        self.assertNotIn("environment:", self.jobs["plan"] + self.jobs["manifests"] + self.jobs["result"], "no other job holds a secret")

    def test_the_other_secrets_are_keys_that_only_read_and_only_a_checkout_takes(self):
        taken = 0
        for name, body in self.jobs.items():
            for step in steps(body):
                for line in step.splitlines():
                    if re.search(r"secrets\.(LAB|CATALOG|NEEDS_CATALOG)_DEPLOY_KEY", line):
                        taken += 1
                        with self.subTest(job=name, line=line.strip()):
                            self.assertIn("actions/checkout@", step)
                            self.assertRegex(line, r"^\s+ssh-key: \$\{\{ secrets\.\w+_DEPLOY_KEY \}\}$")
        self.assertEqual(taken, 9, "three keys in each of the three jobs that check the repositories out")

    def test_the_license_reaches_the_step_that_writes_it_and_no_other(self):
        body = self.jobs["e2e"]
        self.assertEqual(len([step for step in steps(body) if "secrets.GRAVITEE_LICENSE" in step]), 1)
        self.assertEqual(len(re.findall(r"secrets\.GRAVITEE_LICENSE", body)), 1)
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
        body = self.jobs["e2e"]
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", body)

    def test_the_change_is_read_from_the_history_of_the_repository_it_is_in_and_nothing_else_is_fetched_whole(self):
        """`git diff base...head` needs the history of the repository the change is in: a depth of 1 there fails every plan that
        reads a change, and a full history of the other one costs a download for nothing."""
        checkouts = {re.search(r"(?m)^          path: (\w+)$", one).group(1): one for one in steps(self.jobs["plan"]) if "actions/checkout@" in one}
        self.assertEqual(sorted(checkouts), ["catalog", "lab", "needs"])
        for path, change in (("lab", "engine"), ("catalog", "catalog")):
            with self.subTest(path=path):
                depth = re.findall(r"(?m)^          fetch-depth: (.+?)(?:\s+#.*)?$", checkouts[path])
                self.assertEqual(depth, ["${{ inputs.change == '%s' && '0' || '1' }}" % change])
        self.assertNotIn("fetch-depth", checkouts["needs"])
        for name in ("manifests", "e2e", "result"):
            self.assertNotIn("fetch-depth", self.jobs[name], f"{name} reads no change")

    def test_the_check_to_read_waits_for_everything_and_runs_even_when_it_failed(self):
        body = self.jobs["result"]
        self.assertIn("name: Result", body)
        self.assertIn("needs: [plan, manifests, e2e]", body)
        self.assertIn("always()", body)
        self.assertIn("A job that had to run was skipped", body)
        self.assertIn("The plan gave no count", body)

    def test_the_matrix_is_what_the_plan_made_and_the_item_is_never_in_a_script(self):
        for name in ("manifests", "e2e"):
            self.assertIn("fromJSON(needs.plan.outputs.matrix)", self.jobs[name])
            for number, line in run_blocks(self.jobs[name]):
                self.assertNotIn("matrix.", line.replace("e2e_matrix.py", ""), f"{name}: a value goes through env, line {number}")

    def test_the_inputs_it_reads_are_the_ones_it_declares_and_it_declares_none_it_does_not_read(self):
        declared = set(children(self.text, "on", "workflow_call", "inputs"))
        used = set(re.findall(r"inputs\.([\w-]+)", self.text.split("\njobs:\n", 1)[1]))
        self.assertEqual(used, declared)

    def test_the_secrets_it_reads_are_the_ones_it_declares_or_the_environment_holds(self):
        declared = set(children(self.text, "on", "workflow_call", "secrets"))
        used = set(re.findall(r"secrets\.(\w+)", self.text.split("\njobs:\n", 1)[1]))
        self.assertEqual(used - declared, {"GRAVITEE_LICENSE", "DOCKERHUB_USERNAME", "DOCKERHUB_TOKEN"}, "the environment's, not the caller's")
        self.assertEqual(declared - used, set())

    def test_every_input_has_a_type_and_what_is_required_has_no_default(self):
        for name, lines in children(self.text, "on", "workflow_call", "inputs").items():
            body = "\n".join(lines)
            with self.subTest(input=name):
                self.assertRegex(body, r"type: (string|boolean)")
                self.assertRegex(body, r"description: ")
                self.assertTrue(("required: true" in body) != ("default:" in body), "required, or with a default")

    def test_the_tokens_that_read_are_not_required(self):
        for name, lines in children(self.text, "on", "workflow_call", "secrets").items():
            with self.subTest(secret=name):
                self.assertIn("required: false", "\n".join(lines))

    def test_a_step_the_jobs_repeat_is_the_same_step_in_each_and_the_shell_tests_run_the_one_of_all(self):
        def plain(step):
            """The step without its comments, its blank lines and the depth of its history."""
            return "\n".join(line for line in step.splitlines() if line.strip() and not line.lstrip().startswith("#")
                              and "fetch-depth:" not in line)
        for marker, where in (("The change is the one that was read", ("plan", "e2e")),
                              ("Link the catalogs into the engine", ("plan", "manifests", "e2e"))):
            with self.subTest(step=marker):
                self.assertEqual(len({plain(step_with(self.jobs[name], marker)) for name in where}), 1)
        checkouts = [[one for one in steps(self.jobs[name]) if "actions/checkout@" in one] for name in ("plan", "manifests", "e2e")]
        self.assertEqual([len(one) for one in checkouts], [3, 3, 3], "the engine, the catalog, and the one it needs")
        for index in range(3):
            with self.subTest(checkout=index):
                self.assertEqual(len({plain(found[index]) for found in checkouts}), 1, "each job checks the same repository out")

    def test_the_license_never_reaches_a_checkout_of_a_change_or_a_script_of_another_job(self):
        for name in ("plan", "manifests", "result"):
            self.assertNotIn("GRAVITEE_LICENSE", self.jobs[name])


class EngineCallerTest(unittest.TestCase):
    """e2e.yml: a change of the engine, tried on the catalog this engine lists first."""

    def setUp(self):
        self.text = E2E.read_text(encoding="utf-8")
        self.jobs = jobs(self.text)

    def test_a_pull_request_the_night_and_a_maintainer_start_it_and_a_push_never_does(self):
        self.assertEqual(sorted(children(self.text, "on")), ["pull_request", "schedule", "workflow_dispatch"])

    def test_no_branch_of_this_repository_is_how_a_foreign_commit_gets_the_license(self):
        # A push runs every workflow of the pushed commit with the secrets: the commit of a fork is a pull request, tried from main
        self.assertNotIn("e2e/", self.text)
        self.assertNotRegex(self.text, r"(?m)^\s+push:")

    def test_a_pull_request_is_data_of_the_workflow_that_is_always_mains(self):
        script_text = script(step_with(self.jobs["prepare"], "id: prepare"))
        self.assertIn("refs/heads/main", script_text)
        self.assertIn("refs/pull/$PR/head", script_text)

    def test_it_calls_the_run_of_this_repository_and_holds_no_license(self):
        body = self.jobs["try"]
        self.assertIn("uses: ./.github/workflows/e2e-run.yml", body)
        self.assertNotIn("GRAVITEE_LICENSE", self.text)
        self.assertNotIn("environment:", self.text)
        self.assertNotIn("steps:", body, "a job that calls a workflow has none of its own")

    def test_it_hands_over_one_secret_by_name_the_one_that_reads_the_catalog(self):
        secrets = children(self.text, "jobs", "try", "secrets")
        self.assertEqual(list(secrets), ["CATALOG_DEPLOY_KEY"])
        self.assertEqual(sorted(set(re.findall(r"secrets\.(\w+)", self.text))), ["CATALOG_DEPLOY_KEY"])

    def test_it_gives_the_run_the_inputs_it_declares_and_all_that_it_requires(self):
        declared = children(RUN.read_text(encoding="utf-8"), "on", "workflow_call", "inputs")
        given = set(children(self.text, "jobs", "try", "with"))
        self.assertLessEqual(given, set(declared))
        required = {name for name, lines in declared.items() if "required: true" in "\n".join(lines)}
        self.assertLessEqual(required, given)

    def test_it_says_the_change_is_the_engine_s_and_only_the_night_may_write(self):
        self.assertRegex(self.text, r"(?m)^      change: engine$")
        writers = [name for name, body in self.jobs.items() if re.search(r"(?m)^\s+[a-z-]+: write$", body)]
        self.assertEqual(writers, ["nightly"])
        self.assertNotRegex(self.text.split("\njobs:\n", 1)[0], r"(?m)^\s+[a-z-]+: write$")

    def test_an_engine_without_a_catalog_to_try_says_so_and_runs_nothing(self):
        self.assertIn("if: needs.prepare.outputs.skip != 'true'", self.jobs["try"])
        self.assertIn("The variable CATALOG_REPOSITORY is not set", self.text)

    def test_the_check_to_require_is_a_job_of_this_file_named_as_it_always_was(self):
        """A job that calls a workflow shows as `Run / Result`, the callee's name behind the caller's: the name that the
        documentation, a ruleset and the audit of the settings ask for is this file's own job, which always reports."""
        self.assertEqual(list(self.jobs), ["prepare", "try", "result", "nightly"])
        body = self.jobs["result"]
        self.assertEqual(re.findall(r"(?m)^    name: (.+)$", body), ["E2E result"])
        self.assertEqual(len(re.findall(r"(?m)^    name: E2E result$", self.text)), 1, "no other job of this file bears the name")
        self.assertIn("needs: [prepare, try]", body)
        self.assertIn("always() &&", body, "it reports when the run failed, was cancelled or was refused")
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", body, "none for a fork, as for the run")
        self.assertIn("runs-on: ubuntu-24.04", body)
        self.assertIn("timeout-minutes: 5", body)
        self.assertNotIn("environment:", body)
        self.assertNotIn("secrets.", body)
        self.assertEqual(sorted(re.findall(r"\$\{\{ (needs\.[\w.]+) \}\}", body)),
                         ["needs.prepare.outputs.skip", "needs.prepare.result", "needs.try.result"])


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

    SOURCES = {"engine": (E2E, "id: prepare")}
    ENV = {"EVENT": "pull_request", "REF": "refs/pull/7/merge", "HEAD": SHA, "BASE": "b" * 40, "PR": "", "SHA": "", "ITEMS": "",
           "CATALOG_REPOSITORY": "owner/catalog", "CATALOG_REF": "c" * 40, "E2E_ENABLED": "true"}

    def prepare(self, source, **env):
        path, marker = self.SOURCES[source]
        text = path.read_text(encoding="utf-8")
        return self.execute(script(step_with(jobs(text)["prepare"], marker)), {**self.ENV, **env})

    def test_a_pull_request_of_the_engine_is_tried_where_it_changes_the_catalog_s_known_good_commit(self):
        done = self.prepare("engine")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.outputs(), {"lab_ref": SHA, "catalog_ref": "c" * 40, "items": "changed", "base": "b" * 40, "expect_sha": "",
                                          "skip": "false"})

    def test_an_engine_whose_repository_has_not_enabled_end_to_end_tries_nothing(self):
        for enabled in ("", "false", "yes"):
            with self.subTest(enabled=enabled):
                done = self.prepare("engine", E2E_ENABLED=enabled)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn("The variable E2E_ENABLED is not true", done.stdout)
                self.assertEqual(self.outputs()["skip"], "true")

    def test_the_night_tries_the_canary_on_the_default_branch_of_the_catalog(self):
        done = self.prepare("engine", EVENT="schedule", REF="refs/heads/main", BASE="")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.outputs(), {"lab_ref": SHA, "catalog_ref": "", "items": "canary", "base": "", "expect_sha": "", "skip": "false"})

    def test_a_maintainer_who_read_a_pull_request_tries_that_commit_from_main(self):
        done = self.prepare("engine", EVENT="workflow_dispatch", REF="refs/heads/main", BASE="", PR="123", SHA="d" * 40, ITEMS="canary")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.outputs()["lab_ref"], "refs/pull/123/head")
        self.assertEqual(self.outputs()["expect_sha"], "d" * 40)
        self.assertEqual(self.outputs()["items"], "canary")

    def test_a_dispatch_with_nothing_typed_tries_the_canary_of_the_commit_it_runs_on(self):
        done = self.prepare("engine", EVENT="workflow_dispatch", REF="refs/heads/main", BASE="")
        self.assertEqual((done.returncode, self.outputs()["items"], self.outputs()["lab_ref"]), (0, "canary", SHA))

    def test_a_pull_request_is_tried_from_main_and_from_nowhere_else(self):
        done = self.prepare("engine", EVENT="workflow_dispatch", REF="refs/heads/e2e/7", PR="7", SHA="d" * 40)
        self.assertEqual(done.returncode, 1)
        self.assertIn("E2E02", done.stdout)
        self.assertEqual(self.outputs(), {})

    def test_what_is_not_a_number_a_commit_or_words_stops_everything(self):
        hostile = [{"PR": "12a", "SHA": "d" * 40}, {"PR": "12345678", "SHA": "d" * 40}, {"PR": "1; echo hacked", "SHA": "d" * 40},
                   {"PR": "7", "SHA": "short"}, {"PR": "7", "SHA": "D" * 40}, {"PR": "7", "SHA": ""}, {"PR": "7", "SHA": "d" * 41},
                   {"ITEMS": "addon:a; echo hacked >&2"}, {"ITEMS": "addon:a $(id)"}, {"ITEMS": "addon:a\n::set-output name=x::y"},
                   {"ITEMS": "`id`"}, {"ITEMS": "x" * 501}, {"CATALOG_REF": "-x"}, {"CATALOG_REF": "a b"}, {"CATALOG_REF": "x\ny"}]
        for env in hostile:
            with self.subTest(env=env):
                self.output.write_text("", encoding="utf-8")
                done = self.prepare("engine", EVENT="workflow_dispatch", REF="refs/heads/main", BASE="", **env)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn("::error title=E2E04::", done.stdout)
                self.assertNotIn("hacked", done.stderr + self.output.read_text(encoding="utf-8"))
                self.assertEqual(self.outputs(), {}, "nothing is passed on")

    def test_words_that_name_items_pass_with_their_catalog_and_their_spaces(self):
        done = self.prepare("engine", EVENT="workflow_dispatch", REF="refs/heads/main", BASE="", ITEMS="playground:a addon:@hub/b  setup:c")
        self.assertEqual((done.returncode, self.outputs()["items"]), (0, "playground:a addon:@hub/b  setup:c"))

    def test_an_engine_with_no_catalog_to_try_says_so_and_goes_on_to_nothing(self):
        done = self.prepare("engine", CATALOG_REPOSITORY="", CATALOG_REF="")
        self.assertEqual(done.returncode, 0)
        self.assertIn("::notice title=E2E not tried::The variable CATALOG_REPOSITORY is not set", done.stdout)
        self.assertEqual(self.outputs()["skip"], "true")

    def test_the_outputs_are_six_lines_whatever_the_event(self):
        self.prepare("engine")
        self.assertEqual(len(self.output.read_text(encoding="utf-8").splitlines()), 6)


class InputsTest(ShellTest):
    """The first step of the run: a name that reaches a path, a ref or a word is what it says and nothing else."""

    GOOD = {"LAB_REPOSITORY": "owner/lab", "LAB_REF": "a" * 40, "CATALOG_REPOSITORY": "owner/catalog", "CATALOG_REF": "refs/pull/12/head",
            "CATALOG_ID": "hub", "CHANGE": "catalog", "ITEMS": "changed", "BASE": "origin/main", "EXPECT_SHA": "", "NEEDS_ID": "",
            "NEEDS_REPOSITORY": "", "NEEDS_REF": ""}

    def check(self, **env):
        text = RUN.read_text(encoding="utf-8")
        return self.execute(script(step_with(jobs(text)["plan"], "The inputs are what they say")), {**self.GOOD, **env})

    def test_what_the_callers_pass_is_accepted(self):
        for env in ({}, {"LAB_REF": "main"}, {"LAB_REF": "refs/pull/7/head", "EXPECT_SHA": "b" * 40}, {"CATALOG_REF": ""}, {"CATALOG_ID": ""},
                    {"ITEMS": "all"}, {"ITEMS": "canary"}, {"ITEMS": ""}, {"ITEMS": "playground:@hub/bank addon:echo"}, {"BASE": ""},
                    {"BASE": "b" * 40}, {"CHANGE": "engine"}, {"NEEDS_ID": "base", "NEEDS_REPOSITORY": "owner/base", "NEEDS_REF": "c" * 40},
                    {"NEEDS_ID": "base", "NEEDS_REPOSITORY": "owner/base", "NEEDS_REF": ""}):
            with self.subTest(env=env):
                done = self.check(**env)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_what_is_not_what_it_says_stops_the_run_and_names_the_input(self):
        hostile = {"LAB_REPOSITORY": ["owner", "a/b/c", "o/l; id", "../x/y", "o/l\n::error::x", ""],
                   "LAB_REF": ["", "-x", "a b", "$(id)", "x\ny", "refs/pull/x/head;", "a" * 101],
                   "CATALOG_REPOSITORY": ["", "owner", "o/c$(id)"], "CATALOG_REF": ["-x", "a;b", "a b"],
                   "CATALOG_ID": ["Hub", "h", "../x", "a" * 30, "hub;id"], "CHANGE": ["", "both", "engine; id"],
                   "ITEMS": ["a;b", "$(id)", "x" * 501, "a\nb", "a|b", "a&b", "`id`", "a>b"], "BASE": ["-x", "a b", "$(id)"],
                   "EXPECT_SHA": ["short", "G" * 40, "a" * 41], "NEEDS_ID": ["Hub"]}
        for name, values in hostile.items():
            for value in values:
                with self.subTest(**{name: value[:30]}):
                    done = self.check(**{name: value})
                    self.assertEqual(done.returncode, 1, done.stdout)
                    self.assertIn("::error title=E2E04::", done.stdout)
                    self.assertTrue(done.stdout.count("\n::error") + done.stdout.startswith("::error") == 1, "one error, and ours")

    def test_a_catalog_that_is_needed_is_checked_only_when_there_is_one(self):
        done = self.check(NEEDS_REPOSITORY="not a repository", NEEDS_REF="a b")
        self.assertEqual(done.returncode, 0, "the catalog that is needed is none: its repository is not read")
        done = self.check(NEEDS_ID="base", NEEDS_REPOSITORY="not a repository", NEEDS_REF="")
        self.assertEqual(done.returncode, 1)
        self.assertIn("needs-catalog-repository", done.stdout)


class PlanShellTest(ShellTest):
    """The step that asks for the matrix: which of its modes, and the words that follow."""

    def matrix(self, **env):
        text = RUN.read_text(encoding="utf-8")
        base = {"CATALOG_ID": "hub", "CHANGE": "catalog", "ITEMS": "changed", "BASE": ""}
        done = self.execute(script(step_with(jobs(text)["plan"], "id: matrix")), {**base, **env})
        self.assertEqual(done.returncode, 0, done.stderr)
        return self.called()

    def test_a_catalog_and_what_a_change_touches_in_it(self):
        self.assertEqual(self.matrix(), ["-I", "lab/.github/scripts/e2e_matrix.py", "--catalog", "hub", "--changed", "origin/main", "HEAD",
                                         "--in", "catalog", "--github-output"])
        self.assertEqual(self.matrix(BASE="b" * 40, CHANGE="engine", ITEMS="")[4:9], ["--changed", "b" * 40, "HEAD", "--in", "engine"])

    def test_the_default_catalog_when_none_is_named(self):
        self.assertEqual(self.matrix(CATALOG_ID="", ITEMS="all")[2:], ["--default-catalog", "--all", "--github-output"])

    def test_every_item_and_the_canary(self):
        self.assertEqual(self.matrix(ITEMS="all")[2:], ["--catalog", "hub", "--all", "--github-output"])
        self.assertEqual(self.matrix(ITEMS="canary")[2:], ["--catalog", "hub", "--canary", "--github-output"])

    def test_the_items_a_person_named_come_after_two_dashes_and_no_word_is_a_pattern_for_files(self):
        (self.folder / "a1").write_text("", encoding="utf-8")
        got = self.matrix(ITEMS="--all playground:a* addon:b  setup:@hub/c")
        self.assertEqual(got[2:], ["--catalog", "hub", "--github-output", "--items", "--", "--all", "playground:a*", "addon:b",
                                   "setup:@hub/c"])


class StepsShellTest(ShellTest):
    """The steps that link the catalogs, check the commit, and try the item."""

    def step(self, job, marker):
        return script(step_with(jobs(RUN.read_text(encoding="utf-8"))[job], marker))

    def test_the_catalog_that_is_needed_is_linked_first_and_the_one_tried_after_it(self):
        text = self.step("e2e", "Link the catalogs into the engine")
        done = self.execute(text, {"NEEDS_ID": "base", "ALLOW_MACHINE": "false"})
        self.assertEqual((done.returncode, self.called()), (0, ["-I", "lab/lab-ui/catalog.py", "link", "catalog"]), done.stderr)
        log = self.folder / "calls"
        (self.bin / "python3").write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {log}\n', encoding="utf-8")
        done = self.execute(text, {"NEEDS_ID": "base", "ALLOW_MACHINE": "true"})
        self.assertEqual(done.returncode, 0)
        self.assertEqual(log.read_text(encoding="utf-8").splitlines(),
                         ["-I lab/lab-ui/catalog.py link needs", "-I lab/lab-ui/catalog.py link catalog --allow-machine"])

    def test_a_catalog_alone_is_linked_without_the_allowance_unless_it_is_asked(self):
        text = self.step("manifests", "Link the catalogs into the engine")
        for allow, wanted in (("false", ["link", "catalog"]), ("", ["link", "catalog"]), ("yes", ["link", "catalog"]),
                              ("true", ["link", "catalog", "--allow-machine"])):
            with self.subTest(allow=allow):
                done = self.execute(text, {"NEEDS_ID": "", "ALLOW_MACHINE": allow})
                self.assertEqual((done.returncode, self.called()[2:]), (0, wanted), done.stderr)

    def test_a_link_that_fails_ends_the_job(self):
        done = self.execute(self.step("e2e", "Link the catalogs into the engine"), {"NEEDS_ID": "", "ALLOW_MACHINE": "false"}, stub_exit=1)
        self.assertEqual(done.returncode, 1)

    def test_the_item_has_to_be_in_the_catalog_before_a_not_tried_can_pass(self):
        text = self.step("e2e", "The item is there")
        done = self.execute(text, {"CATALOG_ID": "hub", "ITEM": "playground:@hub/bank"})
        self.assertEqual((done.returncode, self.called()[2:]), (0, ["--catalog", "hub", "--items", "--", "playground:@hub/bank"]), done.stderr)
        done = self.execute(text, {"CATALOG_ID": "", "ITEM": "addon:x"})
        self.assertEqual(self.called()[2:], ["--default-catalog", "--items", "--", "addon:x"])
        done = self.execute(text, {"CATALOG_ID": "hub", "ITEM": "addon:x"}, stub_exit=1)
        self.assertEqual(done.returncode, 1, "an item that is not there is a failure, not a warning")

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
        for name in ("lab", "catalog"):
            subprocess.run([*git, "init", "-q", name], cwd=self.folder, check=True, env=env)
            subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", name], cwd=self.folder / name, check=True, env=env)
        head = {name: subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.folder / name, capture_output=True, text=True, env=env).stdout.strip()
                for name in ("lab", "catalog")}
        for change, sha, wanted in (("catalog", head["catalog"], 0), ("engine", head["lab"], 0), ("catalog", head["lab"], 1),
                                    ("engine", "f" * 40, 1)):
            with self.subTest(change=change, sha=sha[:7]):
                done = self.execute(text, {"CHANGE": change, "EXPECT_SHA": sha})
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
        env = {"PLAN": "success", "MANIFESTS": "success", "E2E": "success", "COUNT": "2"}
        for change, wanted in (({}, 0), ({"COUNT": "0", "MANIFESTS": "skipped", "E2E": "skipped"}, 0),
                               ({"COUNT": "2", "E2E": "skipped"}, 1), ({"E2E": "failure"}, 1), ({"MANIFESTS": "cancelled"}, 1),
                               ({"PLAN": "failure", "COUNT": "0"}, 1), ({"COUNT": ""}, 1), ({"COUNT": "x"}, 1), ({"COUNT": "0", "E2E": "failure"}, 1)):
            with self.subTest(change=change):
                self.assertEqual(self.execute(text, {**env, **change}).returncode, wanted)


class EngineResultTest(ShellTest):
    """The step of the engine's own `E2E result`: what it makes of the inputs and of the run."""

    def setUp(self):
        super().setUp()
        self.text = script(step_with(jobs(E2E.read_text(encoding="utf-8"))["result"], "needs.prepare.result"))

    def test_it_passes_for_a_run_that_ended_well_and_for_an_engine_with_no_catalog_to_try_and_fails_for_all_else(self):
        env = {"PREPARE": "success", "SKIP": "false", "TRY": "success"}
        for change, wanted in (({}, 0), ({"SKIP": "true", "TRY": "skipped"}, 0), ({"TRY": "failure"}, 1), ({"TRY": "cancelled"}, 1),
                               ({"TRY": "skipped"}, 1), ({"TRY": ""}, 1), ({"SKIP": "", "TRY": "skipped"}, 1),
                               ({"PREPARE": "failure", "SKIP": "", "TRY": "skipped"}, 1), ({"PREPARE": "cancelled", "SKIP": ""}, 1),
                               ({"PREPARE": "failure", "SKIP": "true", "TRY": "skipped"}, 1), ({"PREPARE": "skipped"}, 1)):
            with self.subTest(change=change):
                done = self.execute(self.text, {**env, **change})
                self.assertEqual(done.returncode, wanted, done.stdout + done.stderr)

    def test_it_says_why_it_failed_and_that_nothing_was_tried(self):
        done = self.execute(self.text, {"PREPARE": "success", "SKIP": "false", "TRY": "failure"})
        self.assertIn("::error::The run ended failure", done.stdout)
        done = self.execute(self.text, {"PREPARE": "failure", "SKIP": "", "TRY": "skipped"})
        self.assertIn("::error::The inputs were refused", done.stdout)
        done = self.execute(self.text, {"PREPARE": "success", "SKIP": "true", "TRY": "skipped"})
        self.assertIn("::notice::There is no catalog to try, so nothing was tried", done.stdout)


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


class ReleaseTest(unittest.TestCase):
    """release.yml: every merge to main is a version. The deploy key that may create tags is the one thing it must keep in one step."""

    def setUp(self):
        self.text = RELEASE.read_text(encoding="utf-8")
        self.jobs = jobs(self.text)
        self.body = self.jobs["release"]

    def test_a_push_to_main_starts_it_and_nothing_else(self):
        self.assertEqual(list(children(self.text, "on")), ["push"])
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


class ReaderTest(unittest.TestCase):
    """The reader of the rules above: what it takes for a script, and for a job."""

    def test_a_run_is_its_line_or_its_block(self):
        text = "steps:\n  - run: echo ${{ a }}\n  - name: x\n    run: |\n      echo one\n      echo ${{ b }}\n  - run: ok\n"
        self.assertEqual([line.strip() for _, line in run_blocks(text)], ["echo ${{ a }}", "echo one", "echo ${{ b }}", "ok"])

    def test_the_jobs_of_a_workflow_by_name(self):
        text = "name: x\njobs:\n  one:\n    runs-on: a\n  two-b:\n    runs-on: b\n"
        self.assertEqual(list(jobs(text)), ["one", "two-b"])

    def test_the_steps_of_a_job_and_the_script_of_one(self):
        body = "\n    steps:\n      - uses: a\n      - name: x\n        run: |\n          echo one\n\n          echo two\n      - run: y\n"
        found = steps(body)
        self.assertEqual(len(found), 3)
        self.assertEqual(script(found[1]), "echo one\n\necho two\n")
        self.assertEqual(step_with(body, "name: x"), found[1])

    def test_the_keys_under_a_path(self):
        text = "on:\n  workflow_call:\n    inputs:\n      a:\n        type: string\n      b-c:\n        required: true\n    secrets:\n      T:\n        x: y\njobs:\n  j:\n"
        self.assertEqual(list(children(text, "on", "workflow_call", "inputs")), ["a", "b-c"])
        self.assertEqual(children(text, "on", "workflow_call", "secrets"), {"T": ["        x: y"]})
        self.assertEqual(list(children(text, "on")), ["workflow_call"])
        with self.assertRaises(KeyError):
            children(text, "on", "push")


if __name__ == "__main__":
    unittest.main()
