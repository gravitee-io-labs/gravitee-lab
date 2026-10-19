"""`catalog.py release VERSION [--tag] [--pr]`. Real git on repositories made in a temporary folder, with an origin
beside each, the person's name set in a temporary home; no network. The first step refuses a tree that is not clean, a branch
that is behind origin/main, a version that is not greater than the latest tag; it changes the `version:` line and nothing else
and makes one commit on a new branch. The second step needs origin/main to say the version and makes the annotated tag on it.
Neither pushes: the origin's refs are compared before and after."""

import contextlib
import io
import os
import unittest
from pathlib import Path

from lab_ui.catalogs import gitx, release
from tests import Case
from tests.data.standard import support



def catalog(version="0.1.0", extra="") -> bytes:
    return ("# What the Lab reads\nx-gravitee-catalog:\n  schema: 1   # the format\n  id: acme-hub\n  name: \"Acme Hub\"\n"
            f"  version: {version}   # raised by make release\n  lab: 1\n{extra}\n# the end\n").encode("utf-8")


class Preparing(Case):

    def setUp(self):
        support.identity(self)
        self.repo = support.Repo(self, {"catalog.yml": catalog(), "README.md": b"# Hub\n"})

    def release(self, version="1.0.0", **more):
        return release.release(self.repo.root, version, **more)

    def test_it_makes_a_branch_with_the_bump_in_one_commit_and_pushes_nothing(self):
        before = self.repo.remote_refs()
        lines = self.release("1.5.0")
        self.assertEqual(self.repo.git("symbolic-ref", "--short", "HEAD").strip(), "release/1.5.0")
        self.assertEqual(self.repo.git("log", "-1", "--format=%s").strip(), "Release 1.5.0")
        self.assertEqual(self.repo.git("rev-list", "--count", "main..HEAD").strip(), "1")
        self.assertEqual(self.repo.remote_refs(), before)
        self.assertIn("Prepared release/1.5.0", lines[0])
        self.assertIn("  git push -u origin release/1.5.0", lines)
        self.assertIn('  gh pr create --base main --head release/1.5.0 --title "Release 1.5.0" --body ""', lines)

    def test_only_the_version_line_changes_and_its_comment_stays(self):
        self.release("1.5.0")
        now = (self.repo.root / "catalog.yml").read_bytes()
        self.assertEqual(now, catalog("1.5.0"))
        self.assertEqual(self.repo.git("diff", "--numstat", "main", "HEAD").split(), ["1", "1", "catalog.yml"])

    def test_the_commit_has_no_trailer_and_no_tool(self):
        self.release("1.5.0")
        self.assertEqual(self.repo.git("log", "-1", "--format=%B").strip(), "Release 1.5.0")
        self.assertEqual(self.repo.git("log", "-1", "--format=%an <%ae>").strip(), "Ada Dev <ada@example.test>")

    def test_line_ends_and_a_quoted_version_are_kept(self):
        text = catalog().decode().replace("version: 0.1.0", 'version: "0.1.0"').replace("\n", "\r\n").encode()
        self.repo.commit({"catalog.yml": text})
        self.repo.git("push", "--quiet", "origin", "main")
        self.release("2.0.0")
        self.assertEqual((self.repo.root / "catalog.yml").read_bytes(), text.replace(b"0.1.0", b"2.0.0"))

    def test_a_dirty_tree_is_refused_and_nothing_is_made(self):
        for kind in ("tracked", "untracked"):
            changed = self.repo.root / ("README.md" if kind == "tracked" else "new.txt")
            changed.write_text("changed\n")
            with self.assertRaisesRegex(release.ReleaseError, "tree is not clean"):
                self.release()
            self.assertEqual(self.repo.branches(), ["main"])
            if kind == "tracked":
                self.repo.git("checkout", "--", "README.md")
            else:
                changed.unlink()

    def test_a_branch_off_main_that_is_behind_is_refused(self):
        self.repo.git("switch", "--quiet", "-c", "work")
        other = support.Repo.git_in(self.repo.top, "clone", "--quiet", str(self.repo.origin), str(self.repo.top / "other"))
        self.assertEqual(other, "")
        support.Repo.git_in(self.repo.top / "other", "commit", "--quiet", "--allow-empty", "-m", "Move main")
        support.Repo.git_in(self.repo.top / "other", "push", "--quiet", "origin", "main")
        with self.assertRaisesRegex(release.ReleaseError, "behind origin/main"):
            self.release()
        self.assertEqual(self.repo.branches(), ["main", "work"])

    def test_a_branch_off_main_that_is_up_to_date_is_accepted(self):
        self.repo.git("switch", "--quiet", "-c", "work")
        self.repo.commit({"README.md": b"# Hub, changed\n"})
        self.release("1.0.0")
        self.assertEqual(self.repo.git("rev-list", "--count", "origin/main..HEAD").strip(), "2")

    def test_main_that_origin_does_not_have_is_refused(self):
        self.repo.commit({"README.md": b"# unpushed\n"})
        with self.assertRaisesRegex(release.ReleaseError, "main is not at origin/main"):
            self.release()

    def test_a_version_that_is_not_greater_than_the_latest_tag_is_refused(self):
        self.repo.git("tag", "-a", "v1.4.0", "-m", "Release 1.4.0")
        self.repo.git("tag", "-a", "v1.10.0", "-m", "Release 1.10.0")
        for version in ("1.4.0", "1.9.0", "1.10.0", "0.9.9"):
            with self.assertRaisesRegex(release.ReleaseError, r"is not greater than the latest tag v1\.10\.0"):
                self.release(version)
        self.release("1.11.0")   # ten is greater than nine: the numbers are compared, not the text

    def test_the_tags_of_origin_are_read_too(self):
        other = self.repo.top / "other"
        support.Repo.git_in(self.repo.top, "clone", "--quiet", str(self.repo.origin), str(other))
        support.Repo.git_in(other, "tag", "-a", "v3.0.0", "-m", "Release 3.0.0")
        support.Repo.git_in(other, "push", "--quiet", "origin", "v3.0.0")
        with self.assertRaisesRegex(release.ReleaseError, "not greater than the latest tag v3.0.0"):
            self.release("2.0.0")

    def test_a_version_that_is_not_three_numbers_is_refused(self):
        for bad in ("", "1", "1.2", "1.2.3.4", "v1.2.3", "1.2.x", "01.2.3", "1.2.3-rc1", "1.2.3\n", "-1.0.0", "1e3.0.0", "1." + "9" * 12 + ".0"):
            with self.assertRaisesRegex(release.ReleaseError, "three numbers"):
                self.release(bad)
        self.assertEqual(self.repo.branches(), ["main"])

    def test_a_version_catalog_yml_already_says_is_refused_with_the_way_to_tag_it(self):
        with self.assertRaisesRegex(release.ReleaseError, r"already says 0\.1\.0.*TAG=1"):
            self.release("0.1.0")

    def test_a_branch_that_exists_is_refused(self):
        self.repo.git("branch", "release/1.0.0")
        with self.assertRaisesRegex(release.ReleaseError, "release/1.0.0 exists already"):
            self.release("1.0.0")

    def test_a_catalog_without_one_version_line_is_refused_and_put_back(self):
        for text in (b"x-gravitee-catalog:\n  id: a\n", catalog().replace(b"  lab: 1\n", b"  lab: 1\n  version: 2.0.0\n")):
            self.repo.commit({"catalog.yml": text})
            self.repo.git("push", "--quiet", "origin", "main")
            with self.assertRaisesRegex(release.ReleaseError, "no single version line"):
                self.release("9.0.0")
            self.assertEqual(self.repo.branches(), ["main"])

    def test_git_with_no_name_stops_it_and_leaves_the_branch_as_it_was(self):
        home = Path(os.environ["HOME"])
        (home / ".gitconfig").write_text("[user]\n\tuseConfigOnly = true\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
        with self.assertRaisesRegex(release.ReleaseError, "no name and email"):
            self.release("1.0.0")
        self.assertEqual(self.repo.branches(), ["main"])
        self.assertEqual((self.repo.root / "catalog.yml").read_bytes(), catalog())
        self.assertEqual(self.repo.git("status", "--porcelain").strip(), "")

    def test_it_needs_an_origin_and_a_repository_and_a_catalog(self):
        loose = Path(self.repo.top) / "loose"
        loose.mkdir()
        with self.assertRaisesRegex(release.ReleaseError, "not a git repository"):
            release.release(loose, "1.0.0")
        (loose / ".git").mkdir()
        with self.assertRaisesRegex(release.ReleaseError, "no catalog.yml"):
            release.release(loose, "1.0.0")
        self.repo.git("remote", "remove", "origin")
        with self.assertRaisesRegex(release.ReleaseError, "no origin"):
            self.release()

    def test_pr_runs_gh_with_a_list_of_arguments_and_prints_its_answer(self):
        calls = []

        def gh(argv, cwd):
            calls.append((argv, Path(cwd)))
            return 0, "https://example.test/acme/hub/pull/7\n", ""
        lines = self.release("1.2.0", pr=True, gh=gh)
        self.assertEqual(calls, [(["gh", "pr", "create", "--base", "main", "--head", "release/1.2.0", "--title", "Release 1.2.0",
                                   "--body", ""], self.repo.root)])
        self.assertEqual(lines[-1], "https://example.test/acme/hub/pull/7")

    def test_pr_without_gh_says_so_and_still_prepared_the_branch(self):
        lines = self.release("1.2.0", pr=True, gh=lambda argv, cwd: (127, "", ""))
        self.assertIn("gh is not installed: run the lines above yourself.", lines)
        self.assertEqual(self.repo.git("symbolic-ref", "--short", "HEAD").strip(), "release/1.2.0")

    def test_a_pr_that_gh_refuses_says_what_gh_said(self):
        lines = self.release("1.2.0", pr=True, gh=lambda argv, cwd: (1, "", "must first push the branch"))
        self.assertTrue(any("gh pr create did not work: must first push the branch" in line for line in lines))


class Tagging(Case):

    def setUp(self):
        support.identity(self)
        self.repo = support.Repo(self, {"catalog.yml": catalog("1.5.0")})

    def test_it_tags_origin_main_with_an_annotated_tag_and_pushes_nothing(self):
        before = self.repo.remote_refs()
        lines = release.release(self.repo.root, "1.5.0", tag=True)
        tip = self.repo.git("rev-parse", "origin/main").strip()
        self.assertEqual(self.repo.git("rev-parse", "v1.5.0^{commit}").strip(), tip)
        self.assertEqual(self.repo.git("cat-file", "-t", "v1.5.0").strip(), "tag")
        self.assertEqual(self.repo.git("tag", "-l", "--format=%(contents:subject)", "v1.5.0").strip(), "Release 1.5.0")
        self.assertEqual(self.repo.remote_refs(), before)
        self.assertIn("  git push origin v1.5.0   (an administrator pushes it; publish.yml takes it from there)", lines)

    def test_origin_main_has_to_say_the_version(self):
        with self.assertRaisesRegex(release.ReleaseError, r"origin/main says 1\.5\.0, not 1\.6\.0"):
            release.release(self.repo.root, "1.6.0", tag=True)
        self.assertEqual(self.repo.git("tag", "-l").strip(), "")

    def test_what_main_holds_here_and_not_on_origin_is_not_tagged(self):
        self.repo.commit({"catalog.yml": catalog("1.6.0")})   # committed, not pushed
        with self.assertRaisesRegex(release.ReleaseError, r"origin/main says 1\.5\.0, not 1\.6\.0"):
            release.release(self.repo.root, "1.6.0", tag=True)

    def test_a_tag_that_exists_or_a_version_that_is_not_greater_is_refused(self):
        release.release(self.repo.root, "1.5.0", tag=True)
        with self.assertRaisesRegex(release.ReleaseError, "not greater than the latest tag v1.5.0"):
            release.release(self.repo.root, "1.5.0", tag=True)

    def test_a_dirty_tree_does_not_matter_for_a_tag(self):
        (self.repo.root / "scratch.txt").write_text("x\n")
        release.release(self.repo.root, "1.5.0", tag=True)


class Command(Case):

    def setUp(self):
        support.identity(self)
        self.repo = support.Repo(self, {"catalog.yml": catalog()})

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = release.main([*argv, "--dir", str(self.repo.root)])
        return code, out.getvalue(), err.getvalue()

    def test_exit_0_and_the_lines_on_standard_output(self):
        code, out, err = self.run_main("1.0.0")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith("Prepared release/1.0.0: catalog.yml says 1.0.0 (one commit).\n"))

    def test_json_is_one_document_with_the_lines(self):
        import json
        code, out, err = self.run_main("1.0.0", "--format", "json")
        self.assertEqual((code, err), (0, ""))
        document = json.loads(out)
        self.assertEqual((document["version"], document["step"]), ("1.0.0", "branch"))
        self.assertEqual(document["lines"][0], "Prepared release/1.0.0: catalog.yml says 1.0.0 (one commit).")
        self.repo.git("switch", "--quiet", "main")
        self.repo.commit({"catalog.yml": catalog("1.0.0")})
        self.repo.git("push", "--quiet", "origin", "main")
        code, out, err = self.run_main("1.0.0", "--tag", "--format", "json")
        self.assertEqual((code, err), (0, ""))
        document = json.loads(out)
        self.assertEqual((document["step"], document["lines"][0][:21]), ("tag", "Tagged v1.0.0 on orig"))

    def test_github_has_nothing_to_annotate_and_prints_the_lines(self):
        code, out, err = self.run_main("1.0.0", "--format", "github")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith("Prepared release/1.0.0: catalog.yml says 1.0.0 (one commit).\n"))

    def test_json_keeps_standard_output_empty_when_it_refuses(self):
        code, out, err = self.run_main("1.2", "--format", "json")
        self.assertEqual((code, out, err), (2, "", "VERSION is three numbers, for example 1.5.0.\n"))

    def test_a_format_that_does_not_exist_is_exit_2(self):
        code, out, err = self.run_main("1.0.0", "--format", "yaml")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(len(err.splitlines()), 1, err)
        self.assertEqual(self.repo.branches(), ["main"])

    def test_a_refusal_is_one_line_on_standard_error_and_exit_2(self):
        code, out, err = self.run_main("1.2")
        self.assertEqual((code, out, err), (2, "", "VERSION is three numbers, for example 1.5.0.\n"))

    def test_a_usage_mistake_is_exit_2_too(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(release.main([]), 2)
            self.assertEqual(release.main(["1.0.0", "--tag", "--pr"]), 2)
        self.assertEqual(out.getvalue(), "")

    def test_nothing_in_the_module_pushes(self):
        commands = []
        real = gitx.run

        def watch(args, *more, **how):
            commands.append(list(args))
            return real(args, *more, **how)
        self.patch(gitx, "run", watch)
        self.run_main("1.0.0")
        self.patch(gitx, "run", real)
        self.assertFalse([each for each in commands if "push" in each], commands)
        source = (Path(release.__file__)).read_text(encoding="utf-8")
        self.assertNotRegex(source, r'"push"')


class Version(unittest.TestCase):
    """The line edit, with no git."""

    def test_the_numbers_are_compared_as_numbers(self):
        self.assertGreater(release.numbers("1.10.0"), release.numbers("1.9.9"))
        self.assertEqual(release.numbers("1.2"), (-1, -1, -1))

    def test_a_version_in_another_block_or_nested_is_not_the_one(self):
        text = "x-gravitee-catalog:\n  id: a\n  requires:\n    catalogs:\n      b:\n        version: 3.0.0\n  version: 1.0.0\nother:\n  version: 9.9.9\n"
        new, old = release.edit_version(text, "1.1.0")
        self.assertEqual(old, "1.0.0")
        self.assertEqual(new, text.replace("  version: 1.0.0", "  version: 1.1.0"))

    def test_a_hostile_file_costs_little(self):
        import time
        text = "x-gravitee-catalog:\n  version: 1.0.0\n" + ("  # " + "x" * 1000000 + "\n")
        start = time.process_time()
        release.edit_version(text, "1.0.1")
        self.assertLess(time.process_time() - start, 1.0)


if __name__ == "__main__":
    unittest.main()
