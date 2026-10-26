"""library/updates.py: the Lab's own versions, read from real Git repositories in a temporary folder. A Lab is a clone of a bare
repository whose main branch has tags of versions; what each version changed is the subjects of its commits; the update is a
fast-forward to the newest tag, then a new start of the Lab UI (stood in for here). Needs git (a visible skip without it)."""

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lab_ui import paths
from lab_ui.catalogs.registry import CatalogError
from lab_ui.docker import jobs
from lab_ui.library import updates

from .. import Case

GIT = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.test", "GIT_COMMITTER_NAME": "Test",
       "GIT_COMMITTER_EMAIL": "test@example.test", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def git(folder, *args):
    done = subprocess.run(["git", "-C", str(folder), "-c", "init.defaultBranch=main", "-c", "tag.gpgSign=false",
                           "-c", "commit.gpgSign=false", *args], capture_output=True, text=True, env={**os.environ, **GIT})
    if done.returncode != 0:
        raise AssertionError(done.stderr)
    return done.stdout.strip()


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class Versions(Case):
    """A bare repository with v1.0.0, then two changes and v1.1.0; the Lab is a clone of it."""

    def setUp(self):
        self.top = Path(tempfile.mkdtemp(prefix="lab-updates-"))
        self.addCleanup(shutil.rmtree, self.top, True)
        self.patch(os, "environ", {**os.environ, **GIT})
        source = self.top / "source"
        source.mkdir()
        git(source, "init", "-q")
        self.commit(source, "First commit")
        git(source, "tag", "-a", "v1.0.0", "-m", "1.0.0")
        self.commit(source, "Playgrounds export to a file (#15)")
        self.commit(source, "The guide can be hidden (#16)")
        git(source, "tag", "-a", "v1.1.0", "-m", "1.1.0")
        git(source, "tag", "not-a-version")
        self.source = source
        self.remote = self.top / "remote.git"
        git(self.top, "clone", "-q", "--bare", str(source), str(self.remote))
        self.lab = self.top / "lab"
        git(self.top, "clone", "-q", str(self.remote), str(self.lab))
        self.patch(paths, "ROOT", self.lab)
        updates.forget()
        self.addCleanup(updates.forget)
        self.patch(updates, "_checked", {"at": None, "error": None, "checking": False})

    def commit(self, folder, subject):
        (folder / "file.txt").write_text(f"{subject}\n", encoding="utf-8")
        git(folder, "add", "file.txt")
        git(folder, "commit", "-q", "-m", subject)

    def at(self, ref):
        git(self.lab, "reset", "-q", "--hard", ref)
        updates.forget()

    def test_this_lab_is_the_newest_tag_it_holds_and_a_newer_one_is_said(self):
        self.at("v1.0.0")
        lab = updates.describe()
        self.assertEqual((lab["version"], lab["ahead"], lab["latest"], lab["update"], lab["blocked"]), ("1.0.0", 0, "1.1.0", True, None))
        self.assertEqual(lab["run"], updates.RUN)
        self.at("v1.1.0")
        self.assertEqual((updates.describe()["version"], updates.describe()["update"]), ("1.1.0", False))

    def test_a_clone_of_main_past_the_newest_version_says_how_many_changes(self):
        self.commit(self.lab, "Something on main (#17)")
        updates.forget()
        lab = updates.describe()
        self.assertEqual((lab["version"], lab["ahead"], lab["update"]), ("1.1.0", 1, False))

    def test_without_a_version_it_is_a_development_version_and_a_lab_that_is_no_clone_says_so(self):
        git(self.lab, "tag", "-d", "v1.0.0", "v1.1.0")
        updates.forget()
        lab = updates.describe()
        self.assertEqual((lab["version"], lab["latest"], lab["update"]), (None, None, False))
        self.assertEqual(len(lab["commit"]), 7)
        self.patch(paths, "ROOT", self.top / "nowhere")
        updates.forget()
        self.assertEqual(updates.describe()["version"], None)
        self.assertEqual(updates.blocked(), "This Lab is not a Git clone. Download the new version from GitHub")

    def test_each_version_says_what_it_changed_newest_first_with_the_number_of_its_pull_request(self):
        self.at("v1.0.0")
        found = updates.releases()
        self.assertEqual([(one["version"], one["newer"], one["current"]) for one in found["versions"]],
                         [("1.1.0", True, False), ("1.0.0", False, True)])
        self.assertEqual([(change["text"], change["number"], change["url"]) for change in found["versions"][0]["changes"]],
                         [("The guide can be hidden", 16, None), ("Playgrounds export to a file", 15, None)])
        git(self.lab, "remote", "set-url", "origin", "https://github.com/acme/gravitee-lab.git")
        updates.forget()
        found = updates.releases()
        self.assertEqual(found["repository"], "acme/gravitee-lab")
        self.assertEqual(found["url"], "https://github.com/acme/gravitee-lab/releases")
        self.assertEqual(found["versions"][0]["changes"][0]["url"], "https://github.com/acme/gravitee-lab/pull/16")
        self.assertEqual(found["versions"][0]["url"], "https://github.com/acme/gravitee-lab/releases/tag/v1.1.0")

    def test_what_keeps_the_update_from_being_made_here_is_said(self):
        self.at("v1.0.0")
        (self.lab / "file.txt").write_text("mine\n", encoding="utf-8")
        updates.forget()
        self.assertEqual(updates.describe()["blocked"], "Files of the Lab were changed on this machine: file.txt. Undo or commit them to update")
        git(self.lab, "checkout", "-q", "--", "file.txt")
        git(self.lab, "checkout", "-q", "-b", "mine")
        updates.forget()
        self.assertEqual(updates.describe()["blocked"], "This Lab is on the branch mine. Switch to main to update")
        git(self.lab, "checkout", "-q", "main")
        self.commit(self.lab, "A change of mine")
        updates.forget()
        self.assertEqual(updates.describe()["blocked"], "This Lab has changes of its own that the new version does not have. Update it with Git")

    def test_a_check_fetches_the_new_versions_and_a_repository_out_of_reach_is_said(self):
        self.at("v1.1.0")
        self.commit(self.source, "A third change (#18)")
        git(self.source, "tag", "-a", "v1.2.0", "-m", "1.2.0")
        git(self.source, "push", "-q", str(self.remote), "HEAD:refs/heads/main", "--tags")
        self.assertEqual(updates.describe()["latest"], "1.1.0", "nothing is fetched before a check")
        lab = updates.check()
        self.assertEqual((lab["latest"], lab["update"], lab["error"]), ("1.2.0", True, None))
        self.assertIsNotNone(lab["checked"])
        git(self.lab, "remote", "set-url", "origin", str(self.top / "gone.git"))
        self.assertTrue(updates.check()["error"])

    def test_a_tag_that_is_not_on_main_is_no_version_to_offer(self):
        self.at("v1.1.0")
        git(self.source, "checkout", "-q", "-b", "elsewhere")
        self.commit(self.source, "Not merged")
        git(self.source, "tag", "-a", "v9.0.0", "-m", "9.0.0")
        git(self.source, "push", "-q", str(self.remote), "--tags")
        lab = updates.check()
        self.assertEqual((lab["latest"], lab["update"]), ("1.1.0", False))
        self.assertNotIn("9.0.0", [one["version"] for one in updates.releases()["versions"]])

    def test_the_update_moves_main_to_the_newest_version_then_starts_the_lab_ui_again(self):
        self.at("v1.0.0")
        started = []
        self.patch(updates, "restart", lambda: started.append(True))
        self.patch(updates, "_loads", lambda: None)   # the new code starts: this repository holds no Lab UI to load
        self.patch(jobs, "JOBS", jobs.Jobs())
        job = updates.update("1.1.0")
        for _ in range(100):
            if job.done:
                break
            time.sleep(0.05)
        self.assertTrue(job.ok, job.lines)
        self.assertEqual(job.title, "Update Gravitee Lab to 1.1.0")
        self.assertEqual(git(self.lab, "describe", "--tags"), "v1.1.0")
        self.assertEqual(git(self.lab, "branch", "--show-current"), "main")
        if shutil.which("make"):   # this Lab has no Makefile: what init could not make is said, and the Lab UI starts again anyway
            self.assertTrue(any(line.startswith("make init failed") for line in job.lines), job.lines)
        time.sleep(1.8)   # the new start comes once the job said it is over
        self.assertEqual(started, [True])

    def finished(self, job):
        for _ in range(100):
            if job.done:
                return job
            time.sleep(0.05)
        self.fail("the job did not end")

    def test_a_version_that_cannot_start_here_leaves_the_lab_as_it_was(self):
        self.at("v1.0.0")
        before = git(self.lab, "rev-parse", "HEAD")
        started = []
        self.patch(updates, "restart", lambda: started.append(True))
        self.patch(updates, "_loads", lambda: "SyntaxError: invalid syntax")
        self.patch(jobs, "JOBS", jobs.Jobs())
        job = self.finished(updates.update("1.1.0"))
        self.assertFalse(job.ok)
        self.assertIn("Gravitee Lab 1.1.0 does not start on this machine: SyntaxError: invalid syntax. This Lab stays as it was",
                      job.lines[-1])
        self.assertEqual((git(self.lab, "rev-parse", "HEAD"), git(self.lab, "branch", "--show-current")), (before, "main"))
        time.sleep(1.8)
        self.assertEqual(started, [], "the Lab UI that runs is not replaced")

    def test_the_new_code_is_loaded_by_its_own_server_before_the_lab_ui_starts_on_it(self):
        server = self.lab / "lab-ui" / "server.py"
        server.parent.mkdir(parents=True)
        server.write_text("import sys\nsys.exit(0 if sys.argv[1:] == ['--help'] else 3)\n", encoding="utf-8")
        self.assertIsNone(updates._loads())
        server.write_text("def broken(:\n", encoding="utf-8")
        self.assertIn("SyntaxError", updates._loads())

    def test_a_file_git_does_not_track_where_the_new_version_adds_one_is_said_first(self):
        self.at("v1.0.0")
        self.commit(self.source, "A new file (#19)")
        (self.source / "new.txt").write_text("theirs\n", encoding="utf-8")
        git(self.source, "add", "new.txt")
        git(self.source, "commit", "-q", "-m", "Adds new.txt (#20)")
        git(self.source, "tag", "-a", "v1.2.0", "-m", "1.2.0")
        git(self.source, "push", "-q", str(self.remote), "HEAD:refs/heads/main", "--tags")
        (self.lab / "new.txt").write_text("mine\n", encoding="utf-8")
        lab = updates.check()
        self.assertEqual(lab["latest"], "1.2.0")
        self.assertEqual(lab["blocked"], "Files of this Lab that Git does not track are where the new version adds its own: new.txt. Move them to update")

    def test_a_tag_of_another_form_hides_no_version_and_a_moved_tag_fails_no_check(self):
        self.at("v1.1.0")
        git(self.lab, "tag", "v1.2.0-rc1")
        updates.forget()
        self.assertEqual(updates.describe()["version"], "1.1.0", "v1.2.0-rc1 is no version: the Lab is still 1.1.0")
        self.assertIsNone(updates.check()["error"])
        # The origin moves v1.1.0 (it should not happen): the check takes the tag as the origin says, and goes on working
        self.commit(self.source, "After 1.1.0 (#21)")
        git(self.source, "tag", "-f", "-a", "v1.1.0", "-m", "1.1.0 again")
        git(self.source, "push", "-q", "-f", str(self.remote), "HEAD:refs/heads/main", "refs/tags/v1.1.0")
        self.assertIsNone(updates.check()["error"])
        self.assertEqual(git(self.lab, "rev-parse", "v1.1.0^{commit}"), git(self.source, "rev-parse", "HEAD"))

    def test_an_update_to_no_newer_version_is_refused_in_words(self):
        self.at("v1.1.0")
        for version, said in (("9.9.9", "There is no version 9.9.9"), ("1.0.0", "This Lab is 1.1.0 already"), (None, "There is no version None")):
            with self.subTest(version=version):
                with self.assertRaises(CatalogError) as caught:
                    updates.update(version)
                self.assertEqual((caught.exception.code, caught.exception.status, caught.exception.message), ("update_blocked", 409, said))

    def test_a_started_again_lab_ui_opens_no_browser(self):
        with mock.patch.object(updates.os, "execv") as execv:
            updates.restart()
        self.assertEqual(os.environ.get(updates.RESTARTED), "1")
        execv.assert_called_once()
        os.environ.pop(updates.RESTARTED, None)
