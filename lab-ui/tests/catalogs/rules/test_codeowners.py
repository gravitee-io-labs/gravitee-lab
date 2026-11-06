"""CO, the CODEOWNERS file. CO01 fails on a file that lacks the line `*` or `/.github/`, on a line with no owner, on an
owner that is not `@user` or `@organization/team` in ASCII, and on a later line that takes `/.github/` away from its owners;
the two messages equal. The injection strings of 11.1 are refused by `new --maintainers` before anything is rendered:
an owner string with a newline or a space would write a line of the file, so the first thing `new` does with it is to refuse it.
A line of a megabyte finishes in under a second."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from lab_ui.catalogs import standard
from lab_ui.catalogs.rules import codeowners
from lab_ui.catalogs.tree import ListTree
from tests.data.rules import support

GOOD = "*                       @acme/maintainers\n/.github/               @acme/maintainers\n"
FILE = ".github/CODEOWNERS"
INJECTIONS = ["@x\n/.github/ @evil", "@x @y", "x", "@", "@a/b/c", "@org/", "@\U00000430cme", "@x\r\n/.github/ @evil", "@x\t@y", "@x#y",
              "@-x", "@x-", "@" + "a" * 40, "@org/" + "t" * 101, "@acme/team name", "x@example.test", "@x,", ",", "@x;@y", "@\U0000ff21",
              "@org/../x", "@org/.", "\n@x"]


def found(text, *more):
    """The CO01 findings on the good catalog with this CODEOWNERS (None: no file) as (line, message)."""
    changes = ({FILE: text},) if text is not None else ()
    drop = [] if text is not None else [FILE]
    listing = support.tree(*changes, *more, drop=drop)
    return [(each.line, each.message) for each in codeowners.check(listing, support.ctx_for(listing))]


def no_owner(pattern, owners="@acme/maintainers"):
    return f".github/CODEOWNERS: {pattern} has no owner. Fix: add the line: {pattern} {owners}"


def not_a_name(value):
    return f".github/CODEOWNERS: {value} is not a GitHub name. Fix: write @name or @organization/team."


class Passing(unittest.TestCase):

    def test_the_two_fixed_lines(self):
        self.assertEqual(found(GOOD), [])

    def test_the_file_of_the_template(self):
        files = standard.render_new({"id": "acme-hub", "name": "Acme Hub", "maintainers": "@acme/maintainers,@alice",
                                     "lab_repository": "acme/lab", "lab_ref": "0" * 40})
        tree = ListTree.from_files({path: data for path, data in files.items()})
        self.assertEqual(codeowners.check(tree), [])
        self.assertIn(b"*                       @acme/maintainers @alice\n", files[FILE])

    def test_comments_blank_lines_tabs_a_byte_order_mark_and_section_headers_are_fine(self):
        text = "\U0000feff# who owns what\n\n[Docs]\n*\t@acme/maintainers   \n\n/.github/\t\t@acme/maintainers\r\n/docs/ @alice @bob\n"
        self.assertEqual(found(text), [])

    def test_a_comment_at_the_end_of_a_line_is_no_owner(self):
        self.assertEqual(found("* @acme/maintainers # all\n/.github/ @acme/maintainers #the team\n"), [])
        self.assertEqual(found(GOOD + "/docs/ #nobody\n"), [(3, no_owner("/docs/", "@acme/maintainers"))])

    def test_a_later_line_with_the_same_owners_changes_nothing(self):
        self.assertEqual(found(GOOD + "/.github/workflows/ @acme/maintainers\n*.yml @acme/maintainers\n"), [])

    def test_item_owners_outside_dot_github_are_fine(self):
        self.assertEqual(found(GOOD + "/addons/bank/ @alice\n/playgrounds/ @bob @acme/bank\n/docs/*.md @docs\n/*.md @docs\n"), [])


class Failing(unittest.TestCase):

    def test_a_file_that_is_missing_has_neither_line(self):
        self.assertEqual(found(None), [(0, no_owner("*", "@OWNER/maintainers")), (0, no_owner("/.github/", "@OWNER/maintainers"))])

    def test_a_link_in_its_place_is_a_missing_file(self):
        from lab_ui.catalogs.tree import Entry

        def read(path, limit=1048576):
            return b"" if path == "x" else None
        listing = ListTree([Entry("120000", FILE, 4), Entry("100644", "x", 0)], read)
        self.assertEqual([each.line for each in codeowners.check(listing)], [0, 0])

    def test_a_file_too_large_to_be_one_is_a_missing_file(self):
        big = GOOD + "# " + "x" * (2 * 1024 * 1024) + "\n"
        self.assertEqual(len(found(big)), 2)

    def test_the_star_line_missing(self):
        self.assertEqual(found("/.github/ @acme/maintainers\n"), [(0, no_owner("*", "@OWNER/maintainers"))])

    def test_the_dot_github_line_missing(self):
        self.assertEqual(found("* @acme/maintainers\n"), [(0, no_owner("/.github/", "@acme/maintainers"))])

    def test_a_line_must_be_exactly_the_pattern(self):
        for pattern in ("/.github", ".github/", "/.github/*", "/.github/**"):
            got = found(f"* @acme/maintainers\n{pattern} @acme/maintainers\n")
            self.assertIn((0, no_owner("/.github/", "@acme/maintainers")), got, pattern)
        for pattern in ("**", "/*", "*.*"):
            got = found(f"{pattern} @acme/maintainers\n/.github/ @acme/maintainers\n")
            self.assertIn((0, no_owner("*", "@OWNER/maintainers")), got, pattern)

    def test_a_line_with_no_owner(self):
        self.assertEqual(found("* @acme/maintainers\n/.github/\n"), [(2, no_owner("/.github/", "@acme/maintainers"))])
        self.assertEqual(found("*\n/.github/ @acme/maintainers\n"), [(1, no_owner("*", "@OWNER/maintainers"))])
        self.assertEqual(found(GOOD + "/docs/\n"), [(3, no_owner("/docs/", "@acme/maintainers"))])

    def test_an_owner_that_is_not_a_github_name(self):
        for word in ("alice", "alice@example.test", "@", "@a/b/c", "@org/", "@\U00000430cme", "@x#y", "@x,@y"):
            self.assertEqual(found(f"* @acme/maintainers\n/.github/ @acme/maintainers {word}\n"), [(2, not_a_name(word))])

    def test_every_problem_is_found_in_one_run(self):
        got = found("* nobody\n/docs/\n")
        self.assertEqual(sorted(line for line, _ in got), [0, 1, 2])

    def test_an_invisible_character_is_not_a_blank(self):
        self.assertEqual(found("* @acme/maintainers\n/.github/ @acme/maintainers\U000000a0@evil\n"),
                         [(2, not_a_name("@acme/maintainers @evil"))])   # a plain space on the screen: nothing invisible reaches it

    def test_a_stranger_word_in_a_message_is_made_safe_and_cut(self):
        word = "\x1b[31m" + "w" * 500
        got = found(f"* @acme/maintainers\n/.github/ @acme/maintainers {word}\n")
        self.assertEqual(len(got), 1)
        self.assertNotIn("\x1b", got[0][1])
        self.assertLess(len(got[0][1]), 200)


class Shadowing(unittest.TestCase):
    """The last matching line decides who owns a file: a later line that matches a file of `.github/` with other owners takes
    the second line away, and it is said as that line having no owner."""

    def test_a_later_line_for_the_workflows(self):
        text = GOOD + "/.github/workflows/ @evil\n"
        self.assertEqual(found(text), [(3, no_owner("/.github/", "@acme/maintainers"))])

    def test_a_later_line_by_extension_or_by_name_or_with_a_wildcard(self):
        for pattern in ("*.yml", "*.json", "standard.json", "workflows/", "**/ci.yml", ".github/*", "/.github/**", "*", "**",
                        "/.github/workflows/*.yml", "dependabot.yml", "/.github/actions/**/action.yml", "CODEOWNERS", "?ODEOWNERS",
                        "/", "/*", "*.txt", "docs/", "workflows2/", "**/docs/**/x.md",
                        "/.github/workflows/publish.yml", "/.github/workflows/nightly.yml", "/.github/workflows/e2e.yml",
                        "/.github/rulesets/tags.json", "/.github/rulesets/push.json", "/.github/rulesets/protected-paths.json",
                        "/.github/ISSUE_TEMPLATE/", "/.github/PULL_REQUEST_TEMPLATE.md", "/.github/workflows/p*", "/.github/rulesets/*.json",
                        "/.github/ISSUE_TEMPLATE/*.yml", "*.md", "PULL_REQUEST_TEMPLATE.md", "ISSUE_TEMPLATE/",
                        "/.github/workflows/custom.yml", ".github/anything", "/.github/workflows/nope/", "//.github/x", "/.github/w*/custom-*.yml",
                        "/.github/a/b/c/d/e/f/g/h", "/.github/actions/other/action.yml", "/.github", ".github", ".github/"):
            with self.subTest(pattern=pattern):
                self.assertEqual(len(found(GOOD + f"{pattern} @evil\n")), 1, pattern)

    def test_a_line_with_more_owners_is_a_shadow_too(self):
        self.assertEqual(len(found(GOOD + "/.github/workflows/ @acme/maintainers @evil\n")), 1)

    def test_lines_that_do_not_reach_dot_github_are_not(self):
        for pattern in ("/docs/", "/.githubx/", "/addons/*", "/*.md", "docs/*.md", "/Makefile", "/.github2/", "/catalog.yml",
                        "/addons/**/.github/x.yml", "/a*/.github/*", "/" + "a/" * 400):
            with self.subTest(pattern=pattern):
                self.assertEqual(found(GOOD + f"{pattern} @someone\n"), [], pattern)

    def test_the_star_line_after_a_different_dot_github_line_is_a_shadow(self):
        self.assertEqual(len(found("/.github/ @acme/maintainers\n* @someone\n")), 1)

    def test_a_pattern_too_long_to_read_is_taken_to_match(self):
        self.assertEqual(len(found(GOOD + "/" + "a" * 600 + "/x @evil\n")), 1)

    def test_the_standard_line_written_again_at_the_end_cures_it(self):
        text = GOOD + "/.github/workflows/ @evil\n/.github/ @acme/maintainers\n"
        self.assertEqual(found(text), [])
        self.assertEqual(len(found(text + "/.github/rulesets/ @evil\n")), 1)

    def test_the_same_owners_on_any_of_them_changes_nothing(self):
        text = GOOD + "/.github/workflows/publish.yml @acme/maintainers\n/.github/ISSUE_TEMPLATE/ @ACME/Maintainers\n*.md @acme/maintainers\n"
        self.assertEqual(found(text), [])

    def test_a_line_with_no_owner_takes_the_files_away_as_well(self):
        got = found(GOOD + "/.github/workflows/publish.yml\n")
        self.assertEqual([line for line, _ in got], [3, 3])   # it has no owner itself, and it takes /.github/ away

    def test_many_patterns_from_the_top_are_read_by_their_first_folder(self):
        text = GOOD + "".join(f"/a{n}/*/*.yml @evil\n/{n}*/{n}/x @evil\n" for n in range(2000))
        self.assertEqual(found(text), [])


class Maintainers(unittest.TestCase):

    def test_names_are_taken_in_order_once_each(self):
        self.assertEqual(codeowners.maintainers("@alice, @acme/maintainers,@Alice,@bob-2"), ["@alice", "@acme/maintainers", "@bob-2"])

    def test_the_longest_names_github_allows(self):
        self.assertEqual(codeowners.maintainers("@" + "a" * 39), ["@" + "a" * 39])
        self.assertEqual(codeowners.maintainers("@" + "o" * 39 + "/" + "t" * 100), ["@" + "o" * 39 + "/" + "t" * 100])

    def test_the_injection_strings_are_refused_with_one_line(self):
        for text in INJECTIONS:
            with self.subTest(text=text):
                with self.assertRaises(codeowners.OwnerError) as caught:
                    codeowners.maintainers(text)
                message = str(caught.exception)
                self.assertNotIn("\n", message)
                self.assertTrue(message.startswith("--maintainers: ") and message.endswith(
                    "is not a GitHub name. Fix: write @name or @organization/team."), message)

    def test_nothing_and_too_many_are_refused(self):
        for text in (None, "", "   ", 5):
            with self.assertRaisesRegex(codeowners.OwnerError, "--maintainers is required: name the people or the team that own this "
                                        "catalog, for example --maintainers @acme/maintainers."):
                codeowners.maintainers(text)
        with self.assertRaisesRegex(codeowners.OwnerError, "names 21 owners"):
            codeowners.maintainers(",".join(f"@user{n}" for n in range(21)))

    def test_what_looks_like_a_name_is_checked_as_a_whole(self):
        for word in ("@x\n", "@x ", "@x\U00002028", "\n@x"):
            self.assertFalse(codeowners.owner_ok(word), repr(word))


class New(unittest.TestCase):
    """`catalog.py new --maintainers` refuses the injection strings before anything is rendered."""

    def run_new(self, folder, *more):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = standard.main_new([str(folder), "--id", "acme-hub", "--name", "Acme Hub", "--lab-repo", "acme/gravitee-lab",
                                      "--lab-ref", "0" * 40, *more])
        return code, out.getvalue(), err.getvalue()

    def test_each_injection_string_ends_with_exit_2_and_no_folder(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        for text in INJECTIONS:
            with self.subTest(text=text):
                folder = Path(holder.name) / "hub"
                code, out, err = self.run_new(folder, "--maintainers", text)
                self.assertEqual((code, out), (2, ""))
                self.assertTrue(err.startswith("--maintainers"), err)
                self.assertEqual(len(err.splitlines()), 1, err)
                self.assertFalse(folder.exists())

    def test_the_render_function_refuses_them_too(self):
        for text in INJECTIONS:
            with self.subTest(text=text):
                with self.assertRaises(standard.TemplateError):
                    standard.render_new({"id": "acme-hub", "name": "Acme Hub", "maintainers": [text], "lab_repository": "acme/lab",
                                         "lab_ref": "0" * 40})

    def test_no_maintainers_is_asked_for_with_an_example(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        code, out, err = self.run_new(Path(holder.name) / "hub")
        self.assertEqual((code, out, err), (2, "", "--maintainers is required: name the people or the team that own this catalog, "
                                                   "for example --maintainers @acme/maintainers.\n"))

    def test_a_good_owner_writes_the_two_lines_and_only_them(self):
        files = standard.render_new({"id": "acme-hub", "name": "Acme Hub", "maintainers": "@acme/maintainers, @alice",
                                     "lab_repository": "acme/lab", "lab_ref": "0" * 40})
        lines = [line for line in files[FILE].decode().splitlines() if not line.startswith("#")]
        self.assertEqual(lines, ["*                       @acme/maintainers @alice", "/.github/               @acme/maintainers @alice"])


class Hostile(unittest.TestCase):

    def test_a_line_of_a_megabyte_costs_a_fraction_of_a_second(self):
        for text in ("* @acme/maintainers\n/.github/ @acme/maintainers " + "@" * 1000000 + "\n",
                     GOOD + "/" + "*" * 1000000 + " @evil\n",
                     GOOD + "*" * 500000 + "a" * 500000 + " @evil\n",
                     GOOD + "/".join(["**"] * 200000) + " @evil\n",
                     GOOD + ("\n" * 200000)):
            answer, seconds = support.timed(found, text)
            self.assertLess(seconds, 1.0, text[:60])

    def test_a_file_of_a_hundred_thousand_lines(self):
        text = GOOD + "".join(f"/docs/{n}/ @alice\n" for n in range(100000))
        answer, seconds = support.timed(found, text)
        self.assertEqual(answer, [])
        self.assertLess(seconds, 2.0)


if __name__ == "__main__":
    unittest.main()
