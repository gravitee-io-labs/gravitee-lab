"""`catalog.py ci gitleaks` (SE03): the pinned gitleaks container over BASE..HEAD, with the Lab's own configuration.

Docker is `tests/fakedocker.py`, whose gitleaks follows the rules of the real one that matter here (the configuration it reads,
the ignore file, the `gitleaks:allow` comment, the exit code), so a test fails when the stage forgets one of the flags: a
repository with an allow-all `.gitleaks.toml`, a `.gitleaksignore` and a comment still gets SE03 for the token planted in it,
because the command line carries `--config`, `--ignore-gitleaks-allow` and `--gitleaks-ignore-path` with the Lab's paths and
never the catalog's. The planted token is made of pieces when a test runs and is written in no file of the repository."""

import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.docker import compose
from lab_ui.catalogs import ci
from lab_ui.catalogs.ci import gitleaks
from lab_ui.catalogs.ci import main as ci_main
from tests.data.rules import support
from tests.fakedocker import FakeDocker, option, options

DIGEST = "sha256:" + "ab" * 32
IMAGE = f"ghcr.io/example/gitleaks:v8.0.0@{DIGEST}"
ALLOW_ALL = "[allowlist]\nregexes = ['''.*''']\n"
FILES = {"README.md": "# Hub\n", "src/app.py": "print('hello')\n"}


def token():
    return support.github_token()


def message(path, line, rule="github-pat"):
    return (f"{path}:{line} matches the rule {rule} (the secret is redacted). Fix: revoke it first, then remove it from the "
            "commits of this branch: git reset --soft $(git merge-base origin/main HEAD), commit again, and force-push your branch.")


class Dockerfile(unittest.TestCase):

    def write(self, text):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        file = Path(folder, "Dockerfile")
        file.write_text(text, encoding="utf-8")
        return file

    def test_the_shipped_file_names_gitleaks_pinned_by_digest(self):
        line = next(each for each in gitleaks.DOCKERFILE.read_text(encoding="utf-8").splitlines() if each.startswith("FROM "))
        reference = line.split()[1]
        self.assertRegex(reference, r"^ghcr\.io/gitleaks/gitleaks:v8\.\d+\.\d+@sha256:[0-9a-f]{64}$")
        self.assertTrue(line.endswith(" AS gitleaks"))

    def test_the_digest_that_is_not_resolved_yet_is_not_run(self):
        with self.assertRaises(ci.Unavailable) as raised:
            gitleaks.image(self.write(f"FROM ghcr.io/example/gitleaks:v8.0.0@sha256:{'0' * 64} AS gitleaks\n"))
        self.assertIn("is not pinned yet", str(raised.exception))
        self.assertIn("docker buildx imagetools inspect", str(raised.exception))

    def test_a_reference_must_be_pinned_by_digest(self):
        for reference in ("ghcr.io/example/gitleaks:v8.0.0", "ghcr.io/example/gitleaks", "ghcr.io/example/gitleaks@sha256:abc",
                          f"ghcr.io/example/gitleaks:v8@{DIGEST}x", "--privileged", f"x y@{DIGEST}", f"X/Y@{DIGEST}"):
            with self.subTest(reference=reference), self.assertRaises(ci.Unavailable):
                gitleaks.image(self.write(f"FROM {reference} AS gitleaks\n"))

    def test_a_pinned_reference_is_read_from_the_line_named_for_the_tool(self):
        text = f"# a comment\nFROM ghcr.io/example/other:1@{DIGEST} AS trivy\nFROM {IMAGE} AS gitleaks\n"
        self.assertEqual(gitleaks.image(self.write(text)), IMAGE)
        self.assertEqual(gitleaks.image(self.write(text), "trivy"), f"ghcr.io/example/other:1@{DIGEST}")

    def test_a_file_with_no_such_line_or_no_file_is_not_a_scan(self):
        for text in ("", "FROM scratch\n", f"FROM {IMAGE}\n", f"FROM {IMAGE} AS other\n"):
            with self.subTest(text=text[:20]), self.assertRaises(ci.Unavailable):
                gitleaks.image(self.write(text))
        with self.assertRaises(ci.Unavailable):
            gitleaks.image(Path("/nonexistent/Dockerfile"))

    def test_the_labs_configuration_extends_the_default_rules_and_adds_no_exception_of_its_own(self):
        # (what the default configuration of the pinned image skips for itself is its own, and is said in the file)
        text = gitleaks.CONFIG.read_text(encoding="utf-8")
        self.assertIn("useDefault = true", text)
        for word in ("[allowlist", "allowlist", "[[rules", "regexes", "paths =", "commits ="):
            self.assertNotIn(word, "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#")))
        self.assertIn("allowlist of its own", text)
        self.assertNotIn("skips nothing", text)


class Command(unittest.TestCase):

    MOUNTS = gitleaks.Mounts("/work/catalog", "/work/lab/lab-ui/lab_ui/catalogs/ci/tools/gitleaks.toml", "/tmp/x/ignore", "/tmp/x/out")

    def command(self, log_opts=(), mode="git", user="1001:1001"):
        return gitleaks.command(IMAGE, self.MOUNTS, list(log_opts), mode, user)

    def test_the_command_line_of_the_standard(self):
        base, head = "a" * 40, "b" * 40
        self.assertEqual(self.command(["--log-opts", f"{gitleaks.LOG_TEXT} {base}..{head}"]), [
            "docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m", "--cap-drop",
            "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "256", "--memory", "2g", "--user", "1001:1001",
            "-e", "HOME=/tmp", "-e", "GIT_CONFIG_COUNT=1", "-e", "GIT_CONFIG_KEY_0=safe.directory",
            "-e", "GIT_CONFIG_VALUE_0=/repo", "-v", "/work/catalog:/repo:ro",
            "-v", "/work/lab/lab-ui/lab_ui/catalogs/ci/tools/gitleaks.toml:/config/gitleaks.toml:ro", "-v", "/tmp/x/ignore:/ignore:ro",
            "-v", "/tmp/x/out:/out", IMAGE, "git", "--config", "/config/gitleaks.toml", "--ignore-gitleaks-allow",
            "--gitleaks-ignore-path", "/ignore", "--redact", "--exit-code", "0", "--no-banner", "--report-format", "json",
            "--report-path", "/out/report.json", "--log-opts", f"--text --no-textconv --no-ext-diff {base}..{head}", "/repo"])

    def test_the_container_has_no_network_and_only_the_catalog_is_its_files(self):
        command = self.command()
        self.assertEqual(option(command, "--network"), "none")
        self.assertIn("--read-only", command)
        self.assertEqual(option(command, "--cap-drop"), "ALL")
        mounts = options(command, "-v")
        self.assertEqual([each.split(":")[1] for each in mounts], ["/repo", "/config/gitleaks.toml", "/ignore", "/out"])
        self.assertEqual([each.endswith(":ro") for each in mounts], [True, True, True, False])   # only our own folder is written

    def test_the_directory_scan_of_a_folder_with_no_history_has_no_range(self):
        command = self.command(["--log-opts", "a..b"], mode="dir")
        self.assertEqual(command[command.index(IMAGE) + 1], "dir")
        self.assertNotIn("--log-opts", command)

    def test_a_path_a_bind_mount_cannot_carry_is_refused(self):
        for where in ("/work/a:b", "/work/a,b", "/work/a\nb", "/work/a\rb"):
            with self.subTest(where=where), self.assertRaises(ci.Unavailable):
                gitleaks.command(IMAGE, self.MOUNTS._replace(repo=where), [])

    def test_the_report_names_the_rule_the_file_and_the_line_and_nothing_else(self):
        report = [{"RuleID": "github-pat", "File": "src/app.py", "StartLine": 3, "Secret": "REDACTED", "Match": token(), "Commit": "abc"},
                  {"RuleID": "bad rule\n::error::x", "File": "a\n::error::b", "StartLine": -4},
                  {"RuleID": 7, "File": None, "StartLine": "3"}, "text", None, 4]
        found = gitleaks.findings(report)
        self.assertEqual([(each.code, each.severity, each.path, each.line) for each in found],
                         [("SE03", "error", "src/app.py", 3), ("SE03", "error", "a\n::error::b", 0), ("SE03", "error", "", 0)])
        self.assertEqual(found[0].message, message("src/app.py", 3))
        self.assertNotIn("ghp_", " ".join(each.message for each in found))
        self.assertIn("rule unknown ", found[1].message)

    def test_a_flood_of_findings_is_cut(self):
        found = gitleaks.findings([{"RuleID": "github-pat", "File": f"f{n}", "StartLine": 1} for n in range(5000)])
        self.assertEqual(len(found), gitleaks.FINDINGS)

    def test_what_is_not_a_report_is_not_findings(self):
        for document in (None, {}, "x", 3, [[]], [None]):
            self.assertEqual(gitleaks.findings(document), [])


class History(support.WithGit):
    """What git prints of the range the scan gives gitleaks, run for real. gitleaks scans the output of `git log -p -U0 <log
    options>` (it splits the options on spaces), and git prints "Binary files differ" and no added line for a file it takes for
    binary: a pull request can add the `.gitattributes` that says so, or a NUL byte, in the same commit as the token."""

    def printed(self, repo, words):
        return self.git(repo, "log", "-p", "-U0", *words.split(" "))

    def planted(self):
        repo = self.work(files=FILES)
        base = self.git(repo, "rev-parse", "HEAD").strip()
        self.put(repo, ".gitattributes", "* -diff\n")
        self.put(repo, "config.txt", f"token = {token()}\n")
        self.put(repo, "bin.dat", b"\0 " + support.aws_key().encode())
        return repo, base, self.commit(repo, "Binary")

    def test_without_the_options_git_prints_no_line_for_such_a_file(self):
        # The proof that the test can tell: the scan as it was, with the range alone
        repo, base, head = self.planted()
        plain = self.printed(repo, f"{base}..{head}")
        self.assertIn("Binary files", plain)
        self.assertNotIn(token(), plain)
        self.assertNotIn(support.aws_key(), plain)

    def test_with_the_options_of_the_scan_every_added_line_is_printed(self):
        repo, base, head = self.planted()
        flag, words = gitleaks.revisions(str(repo), base, head)
        self.assertEqual(flag, "--log-opts")
        printed = self.printed(repo, words)
        self.assertIn(f"+token = {token()}", printed)
        self.assertIn(support.aws_key(), printed)
        self.assertNotIn("Binary files", printed)

    def test_the_whole_history_is_read_as_text_too(self):
        repo, _, _ = self.planted()
        for base in ("", "0" * 40, "f" * 40):
            with self.subTest(base=base):
                flag, words = gitleaks.revisions(str(repo), base, "")
                printed = self.printed(repo, words)
                self.assertIn(f"+token = {token()}", printed)
                self.assertIn("+# Hub", printed)   # (the first commit is in it)

    def test_a_textconv_or_an_external_diff_cannot_change_what_is_printed(self):
        repo, base, head = self.planted()
        self.put(repo, ".gitattributes", "*.txt diff=hide\n")
        head = self.commit(repo, "Driver")
        self.git(repo, "config", "diff.hide.textconv", "echo hidden")
        self.git(repo, "config", "diff.external", "echo external")
        _, words = gitleaks.revisions(str(repo), base, head)
        self.assertIn(f"+token = {token()}", self.printed(repo, words))


class Scan(support.WithGit):
    """The scan of a real repository with the fake docker."""

    def setUp(self):
        super().setUp()
        self.fake = FakeDocker(self)
        self.dockerfile = Path(self.holder, "tools-Dockerfile")
        self.dockerfile.write_text(f"FROM {IMAGE} AS gitleaks\n", encoding="utf-8")
        self.out, self.err = io.StringIO(), io.StringIO()

    def stage(self, repo, *argv):
        with mock.patch.object(gitleaks, "DOCKERFILE", self.dockerfile):
            return gitleaks.main([str(repo), *argv], self.out, self.err)

    def run_command(self):
        return self.fake.commands("run")[-1]

    def mapped(self, command, target):
        """The host place that a container place is mounted from."""
        for pair in options(command, "-v"):
            host, container = pair.split(":")[:2]
            if container == target:
                return host
        raise AssertionError(target)

    def plant(self, **extra):
        files = {**FILES, "src/config.py": f'TOKEN = "{token()}"\n', **extra}
        repo = self.work(files=files)
        return repo, self.git(repo, "rev-parse", "HEAD").strip()

    def test_a_planted_token_is_se03_and_the_secret_is_not_in_the_output(self):
        repo, head = self.plant()
        code = self.stage(repo, "--base", "", "--format", "github")
        self.assertEqual((code, self.err.getvalue()), (1, ""))
        self.assertIn(f"::error file=src/config.py,line=1,title=SE03::{message('src/config.py', 1)}", self.out.getvalue())
        self.assertNotIn(token(), self.out.getvalue())
        self.assertNotIn("ghp_", self.out.getvalue())

    def test_an_allow_all_configuration_an_ignore_file_and_a_comment_silence_nothing(self):
        # What a pull request could add to silence the scan that judges it, all at once
        fingerprint = "src/config.py:github-pat:1"
        repo, _ = self.plant(**{".gitleaks.toml": ALLOW_ALL, ".gitleaksignore": f"{fingerprint}\n",
                                "src/other.py": f'TOKEN = "{token()}"  # gitleaks:allow\n'})
        code = self.stage(repo, "--base", "")
        self.assertEqual(code, 1, self.out.getvalue() + self.err.getvalue())
        self.assertIn(message("src/config.py", 1), self.out.getvalue())
        self.assertIn(message("src/other.py", 1), self.out.getvalue())   # (the comment was passed over too)

    def test_the_command_line_carries_the_labs_paths_and_never_the_catalogs(self):
        repo, _ = self.plant(**{".gitleaks.toml": ALLOW_ALL, ".gitleaksignore": "x\n"})
        self.stage(repo, "--base", "")
        command = self.run_command()
        self.assertEqual(option(command, "--config"), "/config/gitleaks.toml")
        self.assertEqual(option(command, "--gitleaks-ignore-path"), "/ignore")
        self.assertIn("--ignore-gitleaks-allow", command)
        self.assertIn("--redact", command)
        self.assertEqual(option(command, "--exit-code"), "0")
        real = str(Path(repo).resolve())
        self.assertEqual(self.mapped(command, "/config/gitleaks.toml"), str(gitleaks.CONFIG.resolve()))
        self.assertEqual(self.mapped(command, "/repo"), real)
        for target in ("/ignore", "/out"):   # the two folders are ours: not the catalog's and not inside it
            self.assertFalse(self.mapped(command, target).startswith(real), target)
        for word in command:
            self.assertNotIn(f"{real}/.gitleaks", word)   # nothing of the catalog's own is a file the scan is told to read
            self.assertFalse(word.endswith((".gitleaksignore", ".gitleaks.toml")), word)

    def scanned(self, repo, drop=()):
        """What the fake gitleaks finds in `repo` with the command of the stage less the flags of `drop`: the report, as a list.
        The command is run for real by the fake, so what it does with a flag missing is the answer."""
        import subprocess
        work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, work, True)
        os.mkdir(os.path.join(work, "ignore"))
        os.mkdir(os.path.join(work, "out"))
        mounts = gitleaks.Mounts(str(Path(repo).resolve()), str(gitleaks.CONFIG.resolve()), os.path.join(work, "ignore"),
                                 os.path.join(work, "out"))
        command = gitleaks.command(IMAGE, mounts, [])
        for flag in drop:
            at = command.index(flag)
            del command[at:at + (1 if flag == "--ignore-gitleaks-allow" else 2)]
        done = subprocess.run([self.fake.path, *command[1:]], capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        report = Path(work, "out", "report.json")
        return json.loads(report.read_text(encoding="utf-8")) if report.exists() else []

    def test_without_each_flag_the_catalogs_own_choice_would_win(self):
        # The proof that the fake, and so the tests above, can tell: take a flag away and the token is missed
        repo, _ = self.plant(**{".gitleaks.toml": ALLOW_ALL, ".gitleaksignore": "src/config.py:github-pat:1\n"})
        self.put(repo, "src/other.py", f'TOKEN = "{token()}"  # gitleaks:allow\n')
        self.assertEqual(sorted({each["File"] for each in self.scanned(repo)}), ["src/config.py", "src/other.py"])
        for flag in ("--config", "--gitleaks-ignore-path", "--ignore-gitleaks-allow"):
            with self.subTest(removed=flag):
                self.assertNotEqual(sorted({each["File"] for each in self.scanned(repo, [flag])}), ["src/config.py", "src/other.py"])

    def test_a_range_is_two_commits_of_this_repository(self):
        repo, first = self.plant()
        self.put(repo, "src/more.py", f'TOKEN = "{token()}"\n')
        head = self.commit(repo, "More")
        self.stage(repo, "--base", first, "--head", head)
        self.assertEqual(option(self.run_command(), "--log-opts"), f"--text --no-textconv --no-ext-diff {first}..{head}")

    def test_without_a_base_or_with_zeros_or_with_a_commit_git_does_not_know_the_whole_history_is_read(self):
        repo, _ = self.plant()
        for base in ("", "0" * 40, "f" * 40, "nonexistent-branch"):
            with self.subTest(base=base):
                self.fake.clear()
                self.stage(repo, "--base", base)
                # (naming the log options replaces gitleaks' own default of the whole history, so they say it)
                self.assertEqual(option(self.run_command(), "--log-opts"), "--text --no-textconv --no-ext-diff --full-history --all")

    def test_a_base_that_is_an_option_of_git_is_no_commit(self):
        repo, _ = self.plant()
        self.stage(repo, "--base=--output=/tmp/evil", "--head=--all")
        command = self.run_command()
        self.assertEqual(option(command, "--log-opts"), "--text --no-textconv --no-ext-diff --full-history --all")
        self.assertNotIn("--output=/tmp/evil", " ".join(command))
        self.assertNotIn("--all", command)   # (no word of the command is the head that was given)

    def test_a_head_that_is_no_commit_is_not_scanned_at_all(self):
        repo, first = self.plant()
        code = self.stage(repo, "--base", first, "--head", "no-such-branch")
        self.assertEqual(code, 2)
        self.assertEqual(self.fake.commands("run"), [])

    def test_a_history_with_no_token_is_all_good(self):
        repo = self.work(files=FILES)
        code = self.stage(repo, "--base", "")
        self.assertEqual(code, 0)
        self.assertTrue(self.out.getvalue().endswith("All good\n"))
        self.assertIn("Secrets: no commit of the range holds one", self.out.getvalue())

    def test_a_folder_that_is_not_a_repository_is_scanned_as_files(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        (folder / "a.py").write_text(f'T = "{token()}"\n')
        code = self.stage(folder, "--base", "x")
        command = self.run_command()
        self.assertEqual(code, 1)
        self.assertEqual(command[command.index(IMAGE) + 1], "dir")
        self.assertNotIn("--log-opts", command)

    def test_the_image_is_pulled_when_the_machine_has_not_got_it_and_not_otherwise(self):
        repo = self.work(files=FILES)
        self.fake.when("image", "inspect", code=1, stderr="Error: No such image")
        self.stage(repo, "--base", "")
        self.assertEqual(self.fake.commands("pull"), [["pull", "-q", IMAGE]])
        self.fake.reset()
        self.stage(repo, "--base", "")
        self.assertEqual(self.fake.commands("pull"), [])

    def test_nothing_of_the_runner_reaches_the_container(self):
        repo, _ = self.plant()
        secrets = {"GITHUB_TOKEN": "ghp_" + "Q" * 36, "ACTIONS_RUNTIME_TOKEN": "runtime-secret-value", "SSH_AUTH_SOCK": "/tmp/agent"}
        with mock.patch.dict(os.environ, secrets):
            self.stage(repo, "--base", "")
        words = " ".join(" ".join(call.argv) for call in self.fake.calls())
        for value in secrets.values():
            self.assertNotIn(value, words)
        for call in self.fake.calls():
            self.assertEqual(sorted(name for name in call.env if name.startswith(("ACTIONS_", "GITHUB_", "GH_")) or name.endswith("_TOKEN")
                                    or name == "SSH_AUTH_SOCK"), [])

    def test_the_summary_with_ci(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        summary = os.path.join(folder, "summary.md")
        repo, _ = self.plant()
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary}):
            self.stage(repo, "--base", "", "--ci")
        with open(summary, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("1 problem to look at. To see them on your machine, run `make ci`.", text)
        self.assertIn("SE03", text)
        self.assertNotIn("ghp_", text)

    def test_a_scan_that_could_not_run_is_exit_2_and_never_green(self):
        repo = self.work(files=FILES)
        cases = (("no docker", {"PATH": "/nonexistent-bin"}, None, "Docker is not installed"),
                 ("no daemon", {}, ("version", dict(code=1, stderr="Cannot connect to the Docker daemon at unix:///x.sock")), "Docker is not running"),
                 ("no image", {}, ("pull", dict(code=1, stderr="manifest unknown")), "could not be pulled"),
                 ("container failed", {}, ("run", dict(code=1, stderr="fatal: bad revision")), "gitleaks did not run (exit 1)"))
        for name, env, rule, said in cases:
            with self.subTest(name):
                self.fake.reset()
                self.out, self.err = io.StringIO(), io.StringIO()
                if rule:
                    self.fake.when(rule[0], **rule[1])
                    if rule[0] == "pull":
                        self.fake.when("image", "inspect", code=1)
                with mock.patch.dict(os.environ, env):
                    code = self.stage(repo, "--base", "")
                self.assertEqual(code, 2)
                self.assertIn(said, self.err.getvalue())
                self.assertNotIn("All good", self.out.getvalue())

    def test_a_report_that_is_not_json_is_exit_2(self):
        repo = self.work(files=FILES)
        original = gitleaks.read_report

        def broken(folder):
            Path(folder, "report.json").write_text("not json")
            return original(folder)
        with mock.patch.object(gitleaks, "read_report", broken):
            self.assertEqual(self.stage(repo, "--base", ""), 2)
        self.assertIn("the report of the scan is not JSON", self.err.getvalue())

    def test_a_placeholder_digest_is_exit_2_before_docker_is_asked(self):
        repo = self.work(files=FILES)
        self.dockerfile.write_text(f"FROM ghcr.io/example/gitleaks:v8@sha256:{'0' * 64} AS gitleaks\n")
        self.assertEqual(self.stage(repo, "--base", ""), 2)
        self.assertEqual(self.fake.calls(), [])

    def test_the_dispatcher_reaches_it(self):
        repo = self.work(files=FILES)
        with mock.patch.object(gitleaks, "DOCKERFILE", self.dockerfile):
            code = ci_main(["gitleaks", str(repo), "--base", "0" * 40], self.out, self.err)
        self.assertEqual(code, 0, self.err.getvalue())

    def test_a_usage_that_is_wrong_is_exit_2(self):
        for argv in (["/nonexistent-folder", "--base", ""], ["--format", "xml"]):
            self.out, self.err = io.StringIO(), io.StringIO()
            self.assertEqual(gitleaks.main(argv, self.out, self.err), 2)

    def test_the_scan_never_runs_compose(self):
        repo = self.work(files=FILES)
        with mock.patch.object(compose, "unreachable", mock.Mock(return_value=False)):
            self.stage(repo, "--base", "")
        self.assertEqual(self.fake.commands("compose"), [])


if __name__ == "__main__":
    unittest.main()
