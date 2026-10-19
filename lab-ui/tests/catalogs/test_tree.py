"""what the rules read. `FolderTree` on a real work tree and on a plain folder, `GitTree` on a commit of a bare repository,
`ListTree`, and the commits of a change (`History`). The tree is hostile: nothing of it may run, no link may be followed out
of it, and listing and reading write nothing. A test that needs git and does not find it prints a skip line."""

import os
import unittest
from unittest import mock

from lab_ui.catalogs import store, tree
from tests import gitfixture
from tests.data.rules.support import WithGit, sha_of

class FolderTrees(WithGit):

    def test_the_entries_of_a_work_tree_are_what_the_history_would_hold(self):
        repo = self.work(files={"README.md": "hi\n", "run.sh": "echo\n", "docs/a.md": "a\n", ".gitignore": "ignored.txt\n"})
        os.chmod(repo / "run.sh", 0o755)
        os.symlink("README.md", repo / "link")
        self.git(repo, "add", "-A")
        self.git(repo, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},vendor/lib")
        (repo / "vendor/lib").mkdir(parents=True)
        self.git(repo, "commit", "-q", "-m", "More")
        self.put(repo, "new.txt", "not added yet\n")
        self.put(repo, "ignored.txt", "machine file\n")
        self.git(repo, "rm", "--cached", "-q", "docs/a.md")
        self.put(repo, "docs/a.md", "again, but untracked\n")
        found = {each.path: each for each in tree.FolderTree(repo).entries()}
        self.assertEqual("100644", found["README.md"].mode)
        self.assertEqual("100755", found["run.sh"].mode)
        self.assertEqual("120000", found["link"].mode)
        self.assertEqual(("160000", 0), (found["vendor/lib"].mode, found["vendor/lib"].size))
        self.assertEqual(3, found["README.md"].size)
        self.assertIn("new.txt", found, "a file that is not added yet is checked before git add")
        self.assertIn("docs/a.md", found)
        self.assertNotIn("ignored.txt", found, "what .gitignore keeps out is not part of the catalog")
        self.assertNotIn(".git", found)
        self.assertEqual(sorted(found), list(found), "the entries are sorted")

    def test_an_untracked_file_is_left_out_when_asked(self):
        repo = self.work(files={"README.md": "hi\n"})
        self.put(repo, "new.txt", "x\n")
        self.assertEqual(["README.md"], [each.path for each in tree.FolderTree(repo, untracked=False).entries()])

    def test_a_file_the_index_has_and_the_disk_lost_is_not_an_entry(self):
        repo = self.work(files={"a.txt": "a\n", "b.txt": "b\n"})
        (repo / "b.txt").unlink()
        self.assertEqual(["a.txt"], [each.path for each in tree.FolderTree(repo).entries()])

    def test_a_folder_that_is_not_a_work_tree_is_walked_and_a_link_is_a_link(self):
        folder = self.holder / "plain"
        folder.mkdir()
        self.put(folder, "a/b/c.txt", "c\n")
        os.symlink("a", folder / "linked")
        os.symlink("/etc", folder / "out")
        (folder / "inner").mkdir()
        (folder / "inner/.git").mkdir()
        self.put(folder, "inner/file", "x\n")
        (folder / ".git").mkdir()
        self.put(folder, ".git/config", "[core]\n")
        found = {each.path: each.mode for each in tree.FolderTree(folder).entries()}
        self.assertEqual({"a/b/c.txt": "100644", "linked": "120000", "out": "120000", "inner": "160000"}, found)

    def test_a_pipe_is_no_file_and_reading_it_does_not_wait(self):
        folder = self.holder / "plain"
        folder.mkdir()
        os.mkfifo(folder / "pipe")
        listing = tree.FolderTree(folder)
        self.assertEqual("010000", listing.entries()[0].mode)
        self.assertIsNone(listing.read("pipe"))
        self.assertIsNone(tree.read_inside(str(folder), "pipe"))

    def test_a_file_is_read_up_to_the_limit_and_only_when_it_is_in_the_listing(self):
        repo = self.work(files={"a.txt": "0123456789", "docs/b.txt": "b"})
        listing = tree.FolderTree(repo)
        self.assertEqual(b"0123456789", listing.read("a.txt"))
        self.assertEqual(b"0123", listing.read("a.txt", 4))
        self.assertEqual(b"", listing.read("a.txt", 0))
        self.assertEqual("b", listing.text("docs/b.txt"))
        for path in ("missing", "docs", "../a.txt", "docs/../a.txt", "/a.txt", "a.txt/", "", "a.txt\0"):
            self.assertIsNone(listing.read(path), path)
        self.assertTrue(listing.exists("docs/b.txt"))
        self.assertFalse(listing.exists("docs"))

    def test_a_link_is_never_followed_out_of_the_tree(self):
        outside = self.holder / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("the reader's secret\n")
        repo = self.work(files={"a/x.txt": "inside\n", "b.txt": "inside\n"})
        listing = tree.FolderTree(repo)
        self.assertEqual(b"inside\n", listing.read("a/x.txt"))
        # after the listing: a folder of the path becomes a link, and a file becomes a link
        (repo / "a/x.txt").unlink()
        (repo / "a").rmdir()
        os.symlink(outside, repo / "a")
        (outside / "x.txt").write_text("the reader's secret\n")
        (repo / "b.txt").unlink()
        os.symlink(outside / "secret.txt", repo / "b.txt")
        self.assertIsNone(listing.read("a/x.txt"), "a folder that became a link")
        self.assertIsNone(listing.read("b.txt"), "a file that became a link")
        self.assertIsNone(tree.read_inside(str(repo), "b.txt"))

    def test_the_root_may_be_a_link_to_the_folder(self):
        repo = self.work(files={"a.txt": "a\n"})
        os.symlink(repo, self.holder / "alias")
        self.assertEqual(b"a\n", tree.FolderTree(self.holder / "alias").read("a.txt"))

    def test_listing_and_reading_write_nothing(self):
        repo = self.work(files={"a.txt": "a\n", "docs/b.md": "b\n"})
        self.put(repo, "new.txt", "x\n")
        before = sha_of(repo)
        listing = tree.FolderTree(repo)
        listing.entries()
        listing.read("a.txt")
        history = tree.History(repo, None)
        history.commits()
        list(history.added())
        tree.default_base(repo)
        tree.head_of(repo)
        self.assertEqual(before, sha_of(repo))

    def test_nothing_of_the_repository_runs_to_list_it(self):
        """A filter, a hook and a file system monitor of the repository are programs; none runs for a listing or a read."""
        repo = self.work(files={"a.txt": "a\n", ".gitattributes": "* filter=evil\n"})
        marker = self.holder / "ran"
        script = self.holder / "evil.sh"
        script.write_text(f"#!/bin/sh\necho ran >> '{marker}'\ncat\n")
        script.chmod(0o755)
        for key in ("filter.evil.clean", "filter.evil.smudge", "core.fsmonitor", "core.sshCommand", "diff.external"):
            self.git(repo, "config", key, str(script))
        hooks = repo / ".git/hooks"
        hooks.mkdir(exist_ok=True)
        for name in ("post-checkout", "post-merge", "pre-commit", "post-index-change", "reference-transaction"):
            (hooks / name).write_text(f"#!/bin/sh\necho {name} >> '{marker}'\n")
            (hooks / name).chmod(0o755)
        self.put(repo, "new.txt", "untracked\n")
        listing = tree.FolderTree(repo)
        listing.entries()
        listing.read("a.txt")
        listing.read("new.txt")
        history = tree.History(repo, None)
        history.commits()
        list(history.added())
        tree.default_base(repo)
        self.assertFalse(marker.exists(), "something of the repository ran")
        # the control: the same configuration does run when git is asked to check the files out
        self.git(repo, "checkout", "-q", "--", "a.txt", extra={"GIT_OPTIONAL_LOCKS": "1"})
        self.git(repo, "add", "new.txt")
        self.assertTrue(marker.exists(), "the filter is configured, so a checkout would have run it")

    def test_a_catalog_too_large_to_list_is_refused_with_a_message(self):
        folder = self.holder / "plain"
        folder.mkdir()
        for index in range(5):
            self.put(folder, f"f{index}.txt", "x")
        self.patch(tree, "MAX_ENTRIES", 3)
        with self.assertRaises(tree.TreeError):
            tree.FolderTree(folder).entries()


class GitTrees(WithGit):

    def commit_of(self, files, **how):
        repo = gitfixture.make_repo(self.holder / "bare.git", files, **how)
        return repo, gitfixture.tip(repo)

    def test_the_entries_and_the_files_of_a_commit_without_a_checkout(self):
        files = {"catalog.yml": "x\n", "docs/a.md": "alpha\n", "bin/run": gitfixture.Executable("#!/bin/sh\n"),
                 "link": gitfixture.Symlink("catalog.yml"), "vendor/lib": gitfixture.Gitlink(), "weird\\name.txt": "w\n"}
        repo, commit = self.commit_of(files)
        listing = tree.GitTree(repo, commit)
        found = {each.path: each for each in listing.entries()}
        self.assertEqual({"catalog.yml", "docs/a.md", "bin/run", "link", "vendor/lib", "weird\\name.txt"}, set(found))
        self.assertEqual("100755", found["bin/run"].mode)
        self.assertEqual("120000", found["link"].mode)
        self.assertEqual("160000", found["vendor/lib"].mode)
        self.assertEqual(6, found["docs/a.md"].size)
        self.assertEqual(b"alpha\n", listing.read("docs/a.md"))
        self.assertEqual(b"alp", listing.read("docs/a.md", 3))
        self.assertIsNone(listing.read("link"), "a link is not read")
        self.assertIsNone(listing.read("vendor/lib"))
        self.assertIsNone(listing.read("missing"))
        self.assertEqual(b"w\n", listing.read("weird\\name.txt"))
        self.assertFalse((repo / "docs").exists(), "no checkout")

    def test_a_commit_is_a_whole_id(self):
        repo, commit = self.commit_of({"a.txt": "a\n"})
        for bad in ("main", "HEAD", commit[:10], commit + "\n", "--help", commit.upper(), "", None):
            with self.assertRaises(ValueError):
                tree.GitTree(repo, bad)

    def test_a_damaged_or_missing_repository_is_a_message(self):
        _, commit = self.commit_of({"a.txt": "a\n"})
        with self.assertRaises(tree.TreeError):
            tree.GitTree(self.holder / "nowhere.git", commit).entries()

    def test_neither_a_filter_nor_a_hook_nor_an_attribute_runs(self):
        files = {".gitattributes": "* filter=evil\n*.txt diff=evil text eol=crlf\n", "a.txt": "a\n", "b/c.txt": "c\n"}
        repo, commit = self.commit_of(files)
        marker = self.holder / "ran"
        script = self.holder / "evil.sh"
        script.write_text(f"#!/bin/sh\necho ran >> '{marker}'\ncat\n")
        script.chmod(0o755)
        for key in ("filter.evil.clean", "filter.evil.smudge", "core.fsmonitor", "diff.evil.textconv", "diff.evil.command",
                    "core.hooksPath"):
            gitfixture.git("config", key, str(script) if key != "core.hooksPath" else str(repo / "hooks"), repo=repo)
        (repo / "hooks").mkdir(exist_ok=True)
        for name in ("post-checkout", "reference-transaction", "post-update", "pre-auto-gc", "post-index-change"):
            (repo / "hooks" / name).write_text(f"#!/bin/sh\necho {name} >> '{marker}'\n")
            (repo / "hooks" / name).chmod(0o755)
        listing = tree.GitTree(repo, commit)
        for entry in listing.entries():
            if entry.mode == "100644":
                listing.read(entry.path)
        self.assertFalse(marker.exists(), "something of the repository ran")
        # the control: a checkout of the same commit with the same configuration does run the filter
        work = self.holder / "checkout"
        work.mkdir()
        self.git(work, "--git-dir", str(repo), "--work-tree", str(work), "checkout", "-q", "-f", commit)
        self.assertTrue(marker.exists(), "the filter is configured, so a checkout would have run it")

    def test_the_files_are_read_a_batch_at_a_time(self):
        files = {f"docs/f{index:03}.txt": f"file {index}\n" for index in range(250)}
        repo, commit = self.commit_of(files)
        listing = tree.GitTree(repo, commit)
        with mock.patch.object(store, "blobs", wraps=store.blobs) as spy:
            for entry in listing.entries():
                self.assertEqual(f"file {int(entry.path[-7:-4])}\n".encode(), listing.read(entry.path))
        self.assertLessEqual(spy.call_count, 8, "250 files were read with a git for each, not a batch")

    def test_a_blob_over_the_limit_is_not_read(self):
        repo, commit = self.commit_of({"big.txt": "x" * 200, "small.txt": "x"})
        self.patch(tree, "BLOB_LIMIT", 100)
        listing = tree.GitTree(repo, commit)
        self.assertIsNone(listing.read("big.txt"))
        self.assertEqual(b"x", listing.read("small.txt"))

    def test_the_object_that_is_gone_is_none_and_not_an_error(self):
        repo, commit = self.commit_of({"a.txt": "a\n"})
        listing = tree.GitTree(repo, commit)
        listing.entries()
        listing._oids["a.txt"] = "f" * 40
        self.assertIsNone(listing.read("a.txt"))


class ListTrees(unittest.TestCase):

    def test_entries_of_the_stores_listing_or_of_tuples_or_of_this_module(self):
        held = {"a.txt": b"alpha", "b/c.txt": b"gamma"}
        entries = [store.Entry("100644", "0" * 40, 5, "a.txt"), tree.Entry("100755", "b/c.txt", 5), ("120000", "l", 1)]
        listing = tree.ListTree(entries, lambda path, limit: held.get(path))
        self.assertEqual([tree.Entry("100644", "a.txt", 5), tree.Entry("100755", "b/c.txt", 5), tree.Entry("120000", "l", 1)],
                         listing.entries())
        self.assertEqual(b"alpha", listing.read("a.txt"))
        self.assertEqual(b"al", listing.read("a.txt", 2))
        self.assertIsNone(listing.read("l"), "a link is not read")
        self.assertIsNone(listing.read("missing"))

    def test_a_reader_of_the_path_alone_works_too(self):
        listing = tree.ListTree([("100644", "a.txt", 1)], {"a.txt": "alpha"}.get)
        self.assertEqual(b"alpha", listing.read("a.txt"), "a text is read as its bytes")
        listing = tree.ListTree([("100644", "a.txt", 1)], lambda path: b"x")
        self.assertEqual(b"x", listing.read("a.txt"))

    def test_a_reader_that_answers_anything_but_content_read_nothing(self):
        listing = tree.ListTree([("100644", "a.txt", 1)], lambda path, limit: 12345)
        self.assertIsNone(listing.read("a.txt"))

    def test_files_held_in_memory(self):
        listing = tree.ListTree.from_files({"a.txt": "alpha", "run": ("100755", b"#!/bin/sh\n"), "l": ("120000", "a.txt")})
        self.assertEqual(["100644", "100755", "120000"], [each.mode for each in listing.entries()])
        self.assertEqual(b"alpha", listing.read("a.txt"))
        self.assertEqual("alpha", listing.text("a.txt"))
        self.assertIsNone(listing.read("l"))
        self.assertEqual(10, listing.entry("run").size)


class Histories(WithGit):

    def history_repo(self):
        repo = self.work(files={"a.txt": "one\ntwo\nthree\n", "docs/b.md": "b\n"})
        self.base = self.git(repo, "rev-parse", "HEAD").strip()
        self.put(repo, "a.txt", "one\nTWO\nthree\nfour\nfive\n")
        self.second = self.commit(repo, "Change a and add lines")
        self.put(repo, "new file.txt", "x\ny\n")
        self.put(repo, "docs/b.md", "")
        self.third = self.commit(repo, "Add a file with a space\n\nCo-authored-by: Bob <bob@example.test>", extra={
            "GIT_AUTHOR_NAME": "Bob Writer", "GIT_AUTHOR_EMAIL": "bob@example.test"})
        return repo

    def test_the_commits_since_the_base_with_who_made_them(self):
        repo = self.history_repo()
        found = tree.History(repo, self.base).commits()
        self.assertEqual([self.third, self.second], [each.sha for each in found])
        self.assertEqual(("Bob Writer", "bob@example.test", "Ada Dev", "ada@example.test"),
                         (found[0].author, found[0].author_email, found[0].committer, found[0].committer_email))
        self.assertIn("Co-authored-by: Bob", found[0].message)
        self.assertEqual(3, len(tree.History(repo, None).commits()), "without a base, the whole history")

    def test_the_lines_each_commit_added_with_their_numbers(self):
        repo = self.history_repo()
        history = tree.History(repo, self.base)
        added = [(each.sha[:7], each.path, each.line, each.text) for each in history.added()]
        self.assertEqual([
            (self.third[:7], "new file.txt", 1, "x"), (self.third[:7], "new file.txt", 2, "y"),
            (self.second[:7], "a.txt", 2, "TWO"), (self.second[:7], "a.txt", 4, "four"), (self.second[:7], "a.txt", 5, "five"),
        ], added)
        self.assertEqual("", history.note)

    def test_a_line_that_looks_like_a_header_is_still_a_line_of_its_file(self):
        repo = self.work(files={"seed.txt": "start\n"})
        base = self.git(repo, "rev-parse", "HEAD").strip()
        self.put(repo, "evil.txt", "+++ b/innocent.txt\n--- a/x\n@@ -1 +1 @@\ndiff --git a/y b/y\n\x01abc\n")
        sha = self.commit(repo, "Lines that look like a diff")
        added = [(each.sha, each.path, each.line, each.text) for each in tree.History(repo, base).added()]
        self.assertEqual([(sha, "evil.txt", index, text) for index, text in enumerate(
            ["+++ b/innocent.txt", "--- a/x", "@@ -1 +1 @@", "diff --git a/y b/y", "\x01abc"], 1)], added)

    def test_a_file_marked_as_binary_still_shows_its_lines(self):
        """`-diff` in .gitattributes makes git print "Binary files differ": the lines would never be scanned."""
        repo = self.work(files={".gitattributes": "hidden.txt -diff\n", "a.txt": "a\n"})
        base = self.git(repo, "rev-parse", "HEAD").strip()
        self.put(repo, "hidden.txt", "a line to scan\n")
        self.commit(repo, "Hide it")
        self.assertEqual(["a line to scan"], [each.text for each in tree.History(repo, base).added()])

    def test_a_setting_of_the_persons_git_that_fuses_hunks_loses_no_line(self):
        """`diff.interHunkContext` makes git print the lines between two near changes even with -U0; the line added after them
        is still a line the commit added."""
        repo = self.work(files={"f.txt": "1\n2\n3\n4\n5\n6\n7\n8\n"})
        base = self.git(repo, "rev-parse", "HEAD").strip()
        for key, value in (("diff.interHunkContext", "5"), ("diff.context", "9"), ("diff.suppressBlankEmpty", "true")):
            self.git(repo, "config", key, value)
        self.put(repo, "f.txt", "1\n2\nx3\n4\n\n6\nKEY\n8\n")
        sha = self.commit(repo, "Two changes near each other")
        added = [(each.sha, each.path, each.line, each.text) for each in tree.History(repo, base).added()]
        self.assertEqual([(sha, "f.txt", 3, "x3"), (sha, "f.txt", 5, ""), (sha, "f.txt", 7, "KEY")], added)

    def test_a_hunk_with_context_lines_is_read_whole(self):
        """If a git prints context all the same, its lines are counted and the lines after it are still the commit's."""
        history = tree.History(git_dir=self.holder, base=None)
        history._diff = ("\x01" + "a" * 40 + "\ndiff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n@@ -3,5 +3,5 @@\n-3\n+x3\n 4\n\n 6\n-7\n+KEY\n"
                         "\\ No newline at end of file\n+++ b/g.txt\n")
        self.assertEqual([(3, "x3"), (7, "KEY")], [(each.line, each.text) for each in history.added()])

    def test_a_deleted_file_adds_nothing_and_a_name_with_a_quote_or_accents_is_found(self):
        repo = self.work(files={"gone.txt": "bye\n"})
        base = self.git(repo, "rev-parse", "HEAD").strip()
        (repo / "gone.txt").unlink()
        self.put(repo, 'café "q".txt', "hello\n")
        self.commit(repo, "Swap")
        self.assertEqual([('café "q".txt', 1, "hello")], [(each.path, each.line, each.text) for each in tree.History(repo, base).added()])

    def test_a_merge_adds_no_line_twice(self):
        repo = self.work(files={"a.txt": "a\n"})
        base = self.git(repo, "rev-parse", "HEAD").strip()
        self.git(repo, "checkout", "-q", "-b", "side")
        self.put(repo, "side.txt", "from the side\n")
        self.commit(repo, "On the side")
        self.git(repo, "checkout", "-q", "main")
        self.put(repo, "main.txt", "from main\n")
        self.commit(repo, "On main")
        self.git(repo, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
        history = tree.History(repo, base)
        self.assertEqual(3, len(history.commits()), "the merge is a commit of the range, with the two it brings")
        self.assertEqual(["from main", "from the side"], sorted(each.text for each in history.added()))

    def test_a_base_git_does_not_know_is_said_and_not_an_empty_history(self):
        repo = self.history_repo()
        history = tree.History(repo, "0" * 40)
        self.assertEqual([], history.commits())
        self.assertIn("could not be read", history.note)

    def test_a_history_over_the_limit_is_said(self):
        repo = self.history_repo()
        history = tree.History(repo, None, limit=100)
        self.assertEqual([], list(history.added()))
        self.assertIn("too large", history.note)

    def test_a_revision_is_a_name_and_never_an_option_or_a_range(self):
        repo = self.history_repo()
        for bad in ("--output=/tmp/x", "-n1", "a..b", "HEAD@{1}", "main/", "a b", "x\n"):
            with self.assertRaises(ValueError, msg=bad):
                tree.History(repo, bad)
        tree.History(repo, self.base, head="main")

    def test_the_base_of_a_branch_is_where_it_left_origin_main(self):
        repo = self.history_repo()
        self.assertIsNone(tree.default_base(repo), "no origin/main")
        self.git(repo, "update-ref", "refs/remotes/origin/main", self.base)
        self.assertEqual(self.base, tree.default_base(repo))
        self.assertEqual(self.third, tree.head_of(repo))
        self.assertTrue(tree.is_repository(repo))
        self.assertFalse(tree.is_repository(self.holder))

    def test_a_base_is_a_name_that_git_resolves_to_a_commit_and_nothing_else(self):
        repo = self.history_repo()
        self.git(repo, "branch", "feature-base", self.base)
        self.git(repo, "tag", "v1", self.base)
        self.git(repo, "update-ref", "refs/remotes/origin/main", self.base)
        for name in ("origin/main", "feature-base", "v1", "HEAD~2", "HEAD^^", self.base, self.base[:10], "main~2"):
            with self.subTest(name=name):
                self.assertEqual(self.base, tree.resolve(repo, name))
        self.assertEqual(self.third, tree.resolve(repo, "HEAD"))
        self.assertEqual(self.third, tree.resolve(repo, "main"))
        for bad in ("", "nonexistent", "a b", "--output=x", "-n1", "a..b", "HEAD@{1}", "main:a.txt", ":/Add", "HEAD~99", "x" * 300, "a\nb",
                    "v1^{tree}", None, 5):
            with self.subTest(bad=bad):
                self.assertIsNone(tree.resolve(repo, bad))
        self.assertFalse((repo / "x").exists())
        self.assertIsNone(tree.resolve(self.holder, "main"), "not a repository")

    def test_the_git_directory_of_a_clone_and_of_a_linked_work_tree(self):
        repo = self.history_repo()
        self.assertEqual(os.path.realpath(repo / ".git"), os.path.realpath(tree.git_dir_of(repo)))
        linked = self.holder / "linked"
        self.git(repo, "worktree", "add", "-q", "-b", "feature", str(linked))
        self.assertTrue((linked / ".git").is_file(), "a linked work tree has a file where a clone has a folder")
        where = tree.git_dir_of(linked)
        self.assertEqual(os.path.realpath(repo / ".git" / "worktrees" / "linked"), os.path.realpath(where))
        self.assertEqual(self.third, tree.head_of(linked))
        listing = tree.GitTree(where, self.base)
        self.assertEqual(["a.txt", "docs/b.md"], [each.path for each in listing.entries()])
        self.assertEqual(b"one\ntwo\nthree\n", listing.read("a.txt"), "the objects are the repository's, found from the linked git directory")
        self.assertIsNone(tree.git_dir_of(self.holder))

    def test_an_empty_repository_has_no_head(self):
        repo = self.work(commit=False)
        self.assertIsNone(tree.head_of(repo))
        self.assertIsNone(tree.default_base(repo))

    def test_a_bare_repository_is_read_by_its_git_directory(self):
        repo = gitfixture.make_repo(self.holder / "bare.git", {"a.txt": "line\n"})
        history = tree.History(git_dir=repo, base=None)
        self.assertEqual(["line"], [each.text for each in history.added()])
        self.assertEqual(1, len(history.commits()))
        with self.assertRaises(ValueError):
            tree.History()

    def test_diff_paths_and_their_quotes(self):
        self.assertEqual("a/b c.txt", tree.diff_path("b/a/b c.txt\t\n"))
        self.assertIsNone(tree.diff_path("/dev/null\n"))
        self.assertEqual('a "q"\nb', tree.diff_path('"b/a \\"q\\"\\nb"'))
        self.assertEqual("café", tree.unquote('"caf\\303\\251"'))
        self.assertEqual("plain", tree.unquote("plain"))
        self.assertEqual("tab\there", tree.unquote('"tab\\there"'))


if __name__ == "__main__":
    unittest.main()
