"""`catalog.py ci needs`: the catalogs a catalog requires are fetched with the key of the secret NEEDS_DEPLOY_KEY and linked."""

import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from lab_ui.catalogs.ci import MESSAGES, main, needs

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

    def test_a_catalog_that_needs_nothing_has_nothing_to_fetch(self):
        for data in (None, {"standard": 1}, {"needs": None}):
            with self.subTest(data=data):
                folder, into = self.catalog(data)
                self.assertEqual(self.stage(folder, into), (0, "Needs: none\n\nAll good\n", ""))

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

    def test_without_the_key_nothing_is_fetched_and_the_line_says_what_to_do(self):
        folder, into = self.catalog(self.GOOD)
        ran = []
        code, out, _ = self.stage(folder, into, {}, lambda command, env: ran.append(command) or (0, ""))
        self.assertEqual((code, ran), (1, []))
        self.assertIn("NEEDS_DEPLOY_KEY is not set", out)
        self.assertIn("gravitee", out)

    def test_each_need_is_fetched_by_its_commit_with_the_key_in_a_private_file_that_is_gone_after(self):
        folder, into = self.catalog({"standard": 1, "needs": {"b": {"repository": "acme/b", "ref": "b" * 40},
                                                              "a": {"repository": "acme/a", "ref": COMMIT}}})
        calls, linked, seen = [], [], {}

        def runner(command, env):
            calls.append(command)
            ssh = env["GIT_SSH_COMMAND"]
            key = ssh.split()[2]
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
        code, out, _ = self.stage(folder, into, {needs.KEY: KEY}, lambda command, env: (128, "fatal: could not read from remote\n"))
        self.assertEqual(code, 1)
        self.assertIn("gravitee (acme/hub) could not be fetched or linked: fatal: could not read from remote", out)
        code, out, _ = self.stage(folder, into, {needs.KEY: KEY}, linker=lambda path: "it has no catalog.yml")
        self.assertEqual(code, 1)
        self.assertIn("it has no catalog.yml", out)

    def test_the_messages_of_the_stage_are_in_the_table(self):
        self.assertEqual({"ND01", "ND02"} - set(MESSAGES), set())


if __name__ == "__main__":
    unittest.main()
