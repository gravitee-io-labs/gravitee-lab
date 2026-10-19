"""CR, the commits of a change. CR01 (an error) for an author, a committer or a Co-authored-by trailer that names a
tool, and CR02 (a warning) for a credit line in a message; for each one failing fixture in which only that code fires and one that
passes. A tool is its own address or credit form and never a first name alone. The tests build the names of tools from pieces, so
the repository holds no credit. The messages name a commit by its role and its first seven characters, and print neither an
address nor a message."""

import unittest

from lab_ui.catalogs import tree
from lab_ui.catalogs.rules import commits, context
from tests.data.rules import support
from tests.data.rules.support import WithGit, codes

SHA = "1a2b3c4d" + "5" * 32
TOOL = support.tool()
ADDRESS = support.tool_address()


def commit(author=("Ada Dev", "ada@example.test"), committer=("Ada Dev", "ada@example.test"), message="Add a guide\n", sha=SHA):
    return tree.Commit(sha, author[0], author[1], committer[0], committer[1], message)


class Fake:
    """Anything with the commits of a change."""

    def __init__(self, *found):
        self.found = list(found)

    def commits(self):
        return list(self.found)

    def added(self):
        return iter(())


def check(*found, **how):
    listing = support.tree()
    return commits.check(listing, context(listing, history=Fake(*found), **how))


FIX_1 = ("Fix: rewrite the commit under your own name (git commit --amend --reset-author for the last one), or squash your branch "
         "into one commit of yours.")


class Identities(unittest.TestCase):

    def test_cr01_the_author_of_a_commit_is_a_tool(self):
        found = check(commit(author=(TOOL + " Code", ADDRESS)))
        self.assertEqual([("CR01", "error", "", 0, f"commit 1a2b3c4: its author names a tool. {FIX_1}")],
                         [(each.code, each.severity, each.path, each.line, each.message) for each in found])

    def test_cr01_the_committer_of_a_commit_is_a_tool(self):
        found = check(commit(committer=(TOOL, ADDRESS)))
        self.assertEqual(["commit 1a2b3c4: its committer names a tool. " + FIX_1], [each.message for each in found])

    def test_cr01_a_co_author_trailer_names_a_tool(self):
        message = f"Add a guide\n\nCo-authored-by: {TOOL} <{ADDRESS}>\n"
        found = check(commit(message=message))
        self.assertEqual(["commit 1a2b3c4: its Co-authored-by trailer names a tool. " + FIX_1], [each.message for each in found])
        self.assertEqual({"CR01"}, codes(found), "a trailer is CR01's and not also a credit line")

    def test_cr01_each_role_is_said_once_for_a_commit(self):
        message = f"x\n\nCo-authored-by: {TOOL} <{ADDRESS}>\nCo-authored-by: Other {TOOL} <{ADDRESS}>\n"
        found = check(commit(author=(TOOL + " Code", ADDRESS), committer=(TOOL, ADDRESS), message=message))
        self.assertEqual(["author", "committer", "Co-authored-by trailer"],
                         [each.message.split("its ")[1].split(" names")[0] for each in found])

    def test_cr01_the_commits_of_a_range_each_have_their_own_findings(self):
        found = check(commit(), commit(author=(TOOL, ADDRESS), sha="abcdef0" + "1" * 33), commit(sha="2222222" + "3" * 33))
        self.assertEqual(["commit abcdef0: its author names a tool. " + FIX_1], [each.message for each in found])

    def test_cr01_passes_for_people(self):
        for name, address in (("Ada Dev", "ada@example.test"), (TOOL + " Martin", TOOL.lower() + ".martin@acme.example"),
                              (TOOL, "someone@example.test"), ("Devin Smith", "devin@acme.example"), ("Cursor", "cursor@acme.example"),
                              ("GitHub", "noreply@github.com"), ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com"),
                              ("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com"), ("Copilot", "c@example.test")):
            with self.subTest(identity=name):
                self.assertEqual([], check(commit(author=(name, address), committer=(name, address))))

    def test_what_makes_a_tool(self):
        for name, address in ((TOOL, ADDRESS), (TOOL + " Code", "x@example.test"), (TOOL + " Opus 4.5", "x@example.test"),
                              (TOOL + " Sonnet 5", "x@example.test"), ("x", "NOREPLY@" + "anthropic" + ".com"), ("Copilot", "175728472+Copilot@users.noreply.github.com"),
                              ("copilot-swe-agent[bot]", "x@example.test"), ("devin-ai-integration[bot]", "x@example.test"),
                              ("GitHub Copilot", "x@example.test"), ("Cursor Agent", "cursoragent@cursor.com"), ("Jane Doe (aider)", "j@example.test"),
                              ("Jane (aider (model-x))", "j@example.test"), ("ChatGPT", "x@example.test"), ("OpenAI Codex", "x@example.test")):
            with self.subTest(name=name, address=address):
                self.assertTrue(commits.is_tool(name, address))
        for name, address in (("Claude", "claude@acme.example"), ("", ""), ("Gemini", "g@example.test"), ("Aider", "a@example.test")):
            with self.subTest(name=name):
                self.assertFalse(commits.is_tool(name, address))

    def test_the_messages_never_hold_an_address_or_a_message(self):
        message = f"Secret subject\n\nbody text\nCo-authored-by: {TOOL} <{ADDRESS}>\n"
        found = check(commit(author=(TOOL, ADDRESS), message=message))
        for each in found:
            self.assertNotIn(ADDRESS, each.message)
            self.assertNotIn("Secret subject", each.message)
            self.assertNotIn(TOOL, each.message)


class Credits(unittest.TestCase):

    def test_cr02_a_credit_line_in_a_message(self):
        found = check(commit(message=f"Add a guide\n\nGenerated with {TOOL} Code\n"))
        self.assertEqual([("CR02", "warning", "commit 1a2b3c4: its message carries a tool credit. The squash drops it from main. "
                                              "Fix: edit the message if you want it gone.")],
                         [(each.code, each.severity, each.message) for each in found])

    def test_cr02_the_forms_of_a_credit(self):
        for line in (f"Generated with {TOOL} Code", f"generated with [{TOOL} Code](https://{TOOL.lower()}.com/{TOOL.lower()}-code)",
                     f"Written by {TOOL}", "  Created using GitHub Copilot", "Authored by Cursor", f"Assisted via {TOOL}",
                     f"see https://{TOOL.lower()}.com/{TOOL.lower()}-code for how", "Built with Codex", "Made by ChatGPT",
                     "Produced with Aider"):
            with self.subTest(line=line):
                self.assertEqual(["CR02"], [each.code for each in check(commit(message=f"Subject\n\nBody\n{line}\nmore\n"))])

    def test_cr02_what_is_no_credit(self):
        for line in ("Generated with care", "Written by hand", "Made with love by the team", "Cursor movement is fixed",
                     "Copilot features are off", "Created by Ada", "the generated files are not committed"):
            with self.subTest(line=line):
                self.assertEqual([], check(commit(message=f"Subject\n\n{line}\n")))

    def test_credited_gives_the_line(self):
        self.assertEqual(f"Generated with {TOOL} Code", commits.credited(f"x\n  Generated with {TOOL} Code  \ny"))
        self.assertIsNone(commits.credited("x\ny"))
        self.assertIsNone(commits.credited(f"Co-authored-by: {TOOL} <{ADDRESS}>"))

    def test_the_trailers_of_a_message(self):
        message = f"s\n\nco-authored-by: Ada <ada@example.test>\nCo-Authored-By : {TOOL} <{ADDRESS}>\nSigned-off-by: x <x@example.test>\n"
        self.assertEqual([("Ada", "ada@example.test"), (TOOL, ADDRESS)], commits.trailers(message))

    def test_without_the_commits_there_is_nothing_to_say(self):
        listing = support.tree()
        self.assertEqual([], commits.check(listing, context(listing, history=None)))
        self.assertEqual([], commits.check(listing))


class Hostile(unittest.TestCase):

    def test_a_message_of_a_megabyte_is_read_in_a_fraction_of_a_second(self):
        for message in ("x" * 1024 * 1024, " " * 1024 * 1024, "Generated with " * 70000, "co-authored-by: " * 65000,
                        ("Co-authored-by: a <" * 50000), "\n" * 1024 * 1024, "Generated" + " " * 1024 * 1024 + "with Codex",
                        "<" * 1024 * 1024, "é" * 1024 * 1024):
            seconds = min(support.timed(check, commit(message=message))[1] for _ in range(2))
            self.assertLess(seconds, 1.0, f"{message[:20]!r}: {seconds:.2f} s")

    def test_a_name_of_a_megabyte_is_none(self):
        self.assertFalse(commits.is_tool("a" * 1024 * 1024, "b" * 1024 * 1024))
        self.assertFalse(commits.is_tool("claude code" + "x" * 1024 * 1024, ""))


class RealHistory(WithGit):

    def test_a_branch_with_a_tool_in_its_commits_is_found_in_the_repository(self):
        repo = self.work(files={"a.txt": "a\n"})
        base = self.git(repo, "rev-parse", "HEAD").strip()
        self.put(repo, "b.txt", "b\n")
        self.commit(repo, f"Add b\n\nGenerated with {TOOL} Code\n\nCo-authored-by: {TOOL} <{ADDRESS}>\n", extra={
            "GIT_AUTHOR_NAME": "Ada Dev", "GIT_COMMITTER_NAME": TOOL + " Code", "GIT_COMMITTER_EMAIL": ADDRESS})
        self.put(repo, "c.txt", "c\n")
        clean = self.commit(repo, "Add c")
        listing = support.tree()
        found = commits.check(listing, context(listing, history=tree.History(repo, base)))
        self.assertEqual(["CR01", "CR01", "CR02"], sorted(each.code for each in found))
        self.assertNotIn(clean[:7], " ".join(each.message for each in found))
        self.assertEqual({"committer", "Co-authored-by trailer"},
                         {each.message.split("its ")[1].split(" names")[0] for each in found if each.code == "CR01"})
        self.assertEqual([], commits.check(listing, context(listing, history=tree.History(repo, self.git(repo, "rev-parse", "HEAD~1").strip()))))


if __name__ == "__main__":
    unittest.main()
