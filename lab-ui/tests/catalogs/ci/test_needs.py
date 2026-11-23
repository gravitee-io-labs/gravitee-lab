"""`catalog.py ci needs`: fetch public and private catalog dependencies at pinned commits and link them."""

import io
import json
import os
import shlex
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.catalogs.ci import main, needs

COMMIT = "a" * 40
KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----"


class Needs(unittest.TestCase):

    def catalog(self, data):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        folder = Path(holder.name)
        (folder / ".github").mkdir()
        if data is not None:
            (folder / ".github" / "standard.json").write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return str(folder), Path(holder.name) / "needs"

    def stage(self, folder, into, environ=None, runner=None, linker=None):
        out, err = io.StringIO(), io.StringIO()
        code = main(["needs", folder], stdout=out, stderr=err, environ={} if environ is None else environ, into=into,
                    runner=runner or (lambda command, env: (0, "")), linker=linker or (lambda path: None))
        return code, out.getvalue(), err.getvalue()

    GOOD = {"standard": 1, "needs": {"gravitee": {"repository": "acme/hub", "ref": COMMIT}}}
    PUBLIC = {"standard": 1, "needs": {"hub": {"repository": "acme/hub", "ref": COMMIT, "public": True}}}

    def test_a_catalog_that_needs_nothing_has_nothing_to_fetch(self):
        for data in (None, {"standard": 1}, {"needs": None}, {"needs": {}}):
            with self.subTest(data=data):
                folder, into = self.catalog(data)
                unexpected = mock.Mock(side_effect=AssertionError("A catalog with no needs has no work to do"))
                self.assertEqual(self.stage(folder, into, {needs.KEY: KEY}, unexpected, unexpected), (0, "Needs: none\n\nAll good\n", ""))
                self.assertFalse(into.exists())

    def test_a_need_that_is_not_a_catalog_at_a_full_commit_is_refused_before_anything_runs(self):
        bad = [{"needs": []}, {"needs": {"Gravitee": {"repository": "a/b", "ref": COMMIT}}},
               {"needs": {"g": {"repository": "a/b", "ref": "main"}}}, {"needs": {"g": {"repository": "ab", "ref": COMMIT}}},
               {"needs": {"g": {"repository": "a/b;rm", "ref": COMMIT}}}, {"needs": {"g": "x"}}, "not json", "[1]"]
        for data in bad:
            with self.subTest(data=data):
                folder, into = self.catalog(data)
                ran = []
                code, _, err = self.stage(folder, into, {needs.KEY: KEY}, lambda command, env: ran.append(command) or (0, ""))
                self.assertEqual((code, ran), (2 if data != "[1]" else 0, []), err)

    def test_public_is_a_boolean(self):
        for public in ("true", "false", 0, 1, None, [], {}):
            with self.subTest(public=public):
                folder, into = self.catalog({"needs": {"hub": {"repository": "acme/hub", "ref": COMMIT, "public": public}}})
                unexpected = mock.Mock(side_effect=AssertionError("A malformed need cannot be fetched"))
                code, _, err = self.stage(folder, into, {needs.KEY: KEY}, unexpected)
                self.assertEqual(code, 2)
                self.assertIn("needs.hub.public", err)

    def test_a_missing_key_stops_the_stage_before_any_fetch_and_lists_only_private_dependencies(self):
        folder, into = self.catalog({"needs": {"a-public": {"repository": "acme/a", "ref": COMMIT, "public": True},
                                              "b-private": {"repository": "acme/b", "ref": COMMIT},
                                              "c-private": {"repository": "acme/c", "ref": COMMIT, "public": False}}})
        for environment in ({}, {needs.KEY: " \n"}):
            with self.subTest(environment=environment):
                unexpected = mock.Mock(side_effect=AssertionError("The key check must precede all fetches"))
                code, out, _ = self.stage(folder, into, environment, unexpected)
                self.assertEqual(code, 1)
                self.assertIn("NEEDS_DEPLOY_KEY is not set", out)
                self.assertIn("b-private, c-private", out)
                self.assertNotIn("a-public", out)
                self.assertFalse(into.exists())

    def test_public_dependencies_use_isolated_https_and_link_with_the_runner_home(self):
        folder, into = self.catalog(self.PUBLIC)
        home = Path(folder) / "home"
        home.mkdir()
        template = home / "templates"
        template.mkdir()
        (home / ".netrc").write_text("machine github.com login example password example\n", encoding="utf-8")
        (home / ".gitconfig").write_text('[url "file:///example/"]\n insteadOf = https://github.com/\n'
                                         '[core]\n hooksPath = /example/hooks\n askPass = /example/askpass\n'
                                         f'[init]\n templateDir = {template}\n'
                                         '[filter "example"]\n smudge = /example/filter\n'
                                         '[credential]\n helper = example\n', encoding="utf-8")
        ambient = {"HOME": str(home), "XDG_CONFIG_HOME": str(home), "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
                   "GIT_CONFIG_SYSTEM": str(home / ".gitconfig"), "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
                   "GIT_CONFIG_VALUE_0": "Authorization: example", "GIT_TEMPLATE_DIR": str(template), "GIT_ALLOW_PROTOCOL": "file:ext",
                   "GIT_EXEC_PATH": str(home), "GIT_ASKPASS": "/example/askpass", "SSH_ASKPASS": "/example/askpass",
                   "GIT_SSH_COMMAND": "/example/ssh", "GIT_TERMINAL_PROMPT": "1", "GH_TOKEN": "example"}
        for environment in ({}, {needs.KEY: KEY}):
            with self.subTest(secret=bool(environment)):
                calls, homes, linked = [], [], []

                def run(command, *, env, **kwargs):
                    self.assertNotIn(needs.KEY, env)
                    if command[0] == "git":
                        calls.append(command)
                        empty = Path(env["HOME"])
                        homes.append(empty)
                        self.assertNotEqual(empty, home)
                        self.assertEqual(list(empty.iterdir()), [])
                        self.assertFalse((empty.parent / "key").exists())
                        expected = {"HOME": str(empty), "XDG_CONFIG_HOME": str(empty), "GIT_CONFIG_GLOBAL": os.devnull,
                                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_ALLOW_PROTOCOL": "https", "GIT_ASKPASS": "",
                                    "GIT_CEILING_DIRECTORIES": str(into.resolve()), "GIT_TERMINAL_PROMPT": "0"}
                        self.assertEqual({name: env[name] for name in expected}, expected)
                        self.assertTrue((ambient.keys() - expected.keys()).isdisjoint(env))
                        for option in ("core.hooksPath=/dev/null", "credential.helper=", "http.extraHeader=", "http.followRedirects=false"):
                            self.assertIn(option, command)
                    else:
                        self.assertEqual(env["HOME"], str(home))
                        self.assertNotIn("GIT_SSH_COMMAND", env)
                        self.assertEqual(command[-2], "link")
                        linked.append(Path(command[-1]).name)
                    return subprocess.CompletedProcess(command, 0, "")

                with mock.patch.dict(os.environ, {**ambient, **environment}), mock.patch.object(needs.subprocess, "run", side_effect=run):
                    code, out, err = self.stage(folder, into, environment, needs.run, needs.link)
                self.assertEqual((code, err), (0, ""), out)
                self.assertEqual(len(calls), 3)
                self.assertIn("--template=", calls[0])
                self.assertEqual(calls[1][-2:], ["https://github.com/acme/hub.git", COMMIT])
                self.assertEqual(calls[2][-2:], ["--detach", "FETCH_HEAD"])
                self.assertEqual(linked, ["hub"])
                self.assertTrue(all(not empty.exists() for empty in homes))

    def test_a_mixed_set_uses_the_key_for_private_fetches_only(self):
        folder, into = self.catalog({"needs": {"public": {"repository": "acme/public", "ref": COMMIT, "public": True},
                                              "private": {"repository": "acme/private", "ref": COMMIT, "public": False}}})
        calls, linked = [], []
        code, out, err = self.stage(folder, into, {needs.KEY: KEY}, lambda cmd, env: calls.append((cmd, env)) or (0, ""), linked.append)
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual([name.name for name in linked], ["private", "public"])
        self.assertEqual([cmd[-2:] for cmd, _ in calls if "fetch" in cmd],
                         [["git@github.com:acme/private.git", COMMIT], ["https://github.com/acme/public.git", COMMIT]])
        self.assertEqual([cmd[-2] for cmd, env in calls if "GIT_SSH_COMMAND" in env], ["git@github.com:acme/private.git"])

    def test_each_need_is_fetched_by_its_commit_with_the_key_in_a_private_file_that_is_gone_after(self):
        folder, into = self.catalog({"standard": 1, "needs": {"b": {"repository": "acme/b", "ref": "b" * 40},
                                                              "a": {"repository": "acme/a", "ref": COMMIT}}})
        calls, linked, seen = [], [], {}

        def runner(command, env):
            calls.append(command)
            if "fetch" not in command:
                self.assertNotIn("GIT_SSH_COMMAND", env)
                return 0, ""
            ssh = env["GIT_SSH_COMMAND"]
            key = shlex.split(ssh)[2]
            seen.setdefault("mode", stat.S_IMODE(os.stat(key).st_mode))
            seen["text"] = Path(key).read_text(encoding="utf-8")
            seen["key"] = key
            return 0, ""
        code, out, err = self.stage(folder, into, {needs.KEY: KEY}, runner, linked.append)
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual([call[-1] for call in calls if "fetch" in call], [COMMIT, "b" * 40])   # a before b
        self.assertTrue(any("git@github.com:acme/a.git" in part for call in calls for part in call))
        self.assertEqual((seen["mode"], seen["text"].strip()), (0o600, KEY))
        self.assertFalse(os.path.exists(seen["key"]), "the key does not outlive the stage")
        self.assertEqual([path.name for path in linked], ["a", "b"])
        self.assertEqual(out, "Needs: a at aaaaaaa\nNeeds: b at bbbbbbb\n\nAll good\n")

    def test_a_fetch_that_fails_says_why_and_a_link_that_is_refused_does_too(self):
        folder, into = self.catalog(self.GOOD)
        seen = []

        def fail_fetch(command, env):
            if "fetch" not in command:
                self.assertNotIn("GIT_SSH_COMMAND", env)
                return 0, ""
            key = Path(shlex.split(env["GIT_SSH_COMMAND"])[2])
            seen.append(key)
            self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
            return 128, "fatal: could not read from remote\n"

        unexpected = mock.Mock(side_effect=AssertionError("A failed fetch cannot be linked"))
        code, out, _ = self.stage(folder, into, {needs.KEY: KEY}, fail_fetch, unexpected)
        self.assertEqual(code, 1)
        self.assertIn("gravitee (acme/hub) could not be fetched or linked: fatal: could not read from remote", out)
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].exists())
        code, out, _ = self.stage(folder, into, {needs.KEY: KEY}, linker=lambda path: "it has no catalog.yml")
        self.assertEqual(code, 1)
        self.assertIn("it has no catalog.yml", out)

    def test_public_failure_diagnostics_keep_the_reason_without_credentials_or_terminal_controls(self):
        folder, into = self.catalog(self.PUBLIC)

        def runner(command, env):
            if "fetch" in command:
                return 128, "fatal: could not read https://user:example@github.com/acme/hub.git\x1b[0m\n"
            return 0, ""

        code, out, _ = self.stage(folder, into, runner=runner)
        self.assertEqual(code, 1)
        self.assertIn("could not read https://github.com/acme/hub.git", out)
        for text in ("user:example", "\x1b"):
            self.assertNotIn(text, out)
        self.assertIn("public: true", out)
        self.assertIn("NEEDS_DEPLOY_KEY", out)


if __name__ == "__main__":
    unittest.main()
