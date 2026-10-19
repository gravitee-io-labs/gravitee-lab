"""one output for CI, `make check` and Share (report.py), the orchestrator of the rules (audit.py) and the long form of a
code (explain.py). What a stranger wrote (a file name, a message, the output of a build) must not become a workflow command, a
line of the report, a table cell or a link; the audit never writes and makes no network call; a skipped check is never "All
good"."""

import json
import re
import socket
import unittest
from unittest import mock

from lab_ui.catalogs import audit, explain, report, tree
from lab_ui.catalogs.report import Problem
from lab_ui.catalogs.tree import ListTree
from lab_ui.catalogs.rules import commits, dockerfiles, guides, images, secrets, structure
from tests import gitfixture
from tests.data.rules import support
from tests.data.rules.support import WithGit, sha_of


def commands(text):
    """The workflow commands a runner would run for some output: the lines that start with `::` outside a `stop-commands`
    region."""
    found, muted = [], None
    for line in text.split("\n"):
        if muted is not None:
            if line == f"::{muted}::":
                muted = None
            continue
        opened = re.fullmatch(r"::stop-commands::(\S+)", line)
        if opened:
            muted = opened.group(1)
        elif line.startswith("::"):
            found.append(line)
    return found


ERROR = Problem("ST05", "error", "addons/x/.env", 0, "addons/x/.env is an environment file. Fix: git rm --cached it.")
WARNING = Problem("DF22", "warning", "apps/Dockerfile", 1, "apps/Dockerfile:1: the base image python:3 is not pinned by digest.")
INFO = Problem("SD08", "info", ".github/dependabot.yml", 0, ".github/dependabot.yml is owned by this catalog.")


class Escaping(unittest.TestCase):

    def test_data_escapes_percent_and_line_breaks(self):
        self.assertEqual("100%25 a%0D%0Ab%0Ac", report.escape_data("100% a\r\nb\nc"))
        self.assertEqual("a:b,c", report.escape_data("a:b,c"))

    def test_properties_escape_the_colon_and_the_comma_too(self):
        self.assertEqual("a%3Ab%2Cc%250A", report.escape_property("a:b,c%0A"))

    def test_a_file_named_like_a_command_is_one_annotation(self):
        hostile = Problem("ST05", "error", "a%0A::error::x", 3, "a%0A::error::x is an environment file.\n::error::forged")
        text = report.render([hostile], "github")
        self.assertEqual(1, len(commands(text)))
        self.assertEqual(1, len(text.splitlines()))
        self.assertTrue(text.startswith("::error file=a%250A%3A%3Aerror%3A%3Ax,line=3,title=ST05::a%250A::error::x is an "), text)
        self.assertNotIn("\n::error::forged", text)

    def test_every_field_of_every_kind_of_hostile_text_stays_on_one_line(self):
        nasty = ["a\nb", "a\rb", "\x1b[31mred", "x\x00y", "a%0Ab", "::add-mask::x", "\U00002028x", "\ud800z", "t\tb", "é"]
        for text in nasty:
            for field in ("path", "message", "code"):
                values = {"code": "ST01", "severity": "error", "path": "p", "line": 1, "message": "m"}
                values[field] = text
                problem = Problem(**values)
                github, plain = report.render([problem], "github"), report.render([problem], "text")
                self.assertEqual(1, len(commands(github)), (field, repr(text)))
                self.assertEqual(1, len(github.split("\n")) - 1, (field, repr(text)))
                self.assertEqual(5, len(plain.split("\n")), (field, repr(text), plain))   # file, problem, blank, verdict, end
                for out in (github, plain):
                    self.assertNotIn("\x1b", out)
                    out.encode("utf-8")   # a lone surrogate would not print

    def test_the_text_of_a_report_cannot_forge_a_line(self):
        hostile = Problem("ST05", "error", "a\n✓ forged.md", 0, "m\n    ST99 forged problem")
        text = report.render([hostile], "text")
        self.assertNotIn("\n✓ forged.md", text)
        self.assertNotIn("\n    ST99", text)
        self.assertEqual(1, text.count("✗"), text)


class Renderings(unittest.TestCase):

    def test_text_is_a_line_for_each_file_with_its_problems_under_it(self):
        out = report.render([WARNING, ERROR], "text", checked=["catalog.yml", "addons/x/compose.yml"], header="Reading the files")
        self.assertEqual("""Reading the files
✓ catalog.yml
✓ addons/x/compose.yml
✗ addons/x/.env
    ST05 addons/x/.env is an environment file. Fix: git rm --cached it.
! apps/Dockerfile
    DF22 apps/Dockerfile:1: the base image python:3 is not pinned by digest.

1 problem, 1 warning. Run make explain CODE=ST05 for more.
""", out)

    def test_the_verdict_counts_and_names_the_first_code(self):
        self.assertEqual("All good", report.verdict([]))
        self.assertEqual("All good", report.verdict([INFO]))
        self.assertEqual("1 problem. Run make explain CODE=ST05 for more.", report.verdict([ERROR]))
        self.assertEqual("2 problems, 1 warning. Run make explain CODE=ST05 for more.", report.verdict([ERROR, ERROR._replace(path="b"), WARNING]))
        self.assertEqual("1 warning. Run make explain CODE=DF22 for more.", report.verdict([WARNING]))

    def test_a_run_that_skipped_something_is_never_all_good(self):
        self.assertEqual("1 check skipped", report.verdict([], ["Docker Compose 5 not found"]))
        self.assertEqual("2 checks skipped", report.verdict([], ["a", "b"]))
        out = report.render([], "text", skipped=["Docker Compose 5 not found"])
        self.assertIn("Not checked: Docker Compose 5 not found", out)
        self.assertNotIn("All good", out)
        self.assertTrue(out.endswith("1 check skipped\n"))
        self.assertTrue(report.verdict([ERROR], ["x"]).startswith("1 problem, 1 check skipped."))

    def test_information_is_shown_only_when_asked(self):
        self.assertNotIn("SD08", report.render([INFO, ERROR], "text"))
        self.assertIn("SD08", report.render([INFO, ERROR], "text", verbose=True))
        self.assertNotIn("SD08", report.render([INFO], "github"))
        self.assertIn("::notice file=.github/dependabot.yml,title=SD08::", report.render([INFO], "github", verbose=True))

    def test_github_annotations(self):
        out = report.render([WARNING, ERROR, ERROR._replace(path="b.env", line=7)], "github")
        self.assertEqual([
            "::error file=addons/x/.env,title=ST05::addons/x/.env is an environment file. Fix: git rm --cached it.",
            "::error file=b.env,line=7,title=ST05::addons/x/.env is an environment file. Fix: git rm --cached it.",
            "::warning file=apps/Dockerfile,line=1,title=DF22::apps/Dockerfile:1: the base image python:3 is not pinned by digest.",
        ], out.splitlines())

    def test_a_problem_of_the_catalog_has_no_file_property(self):
        out = report.render([Problem("ST10", "error", "", 0, "The catalog holds 501 units.")], "github")
        self.assertEqual("::error title=ST10::The catalog holds 501 units.\n", out)

    def test_ten_annotations_of_each_level_and_a_notice_for_the_rest(self):
        many = [Problem("DF03", "error", "a.Dockerfile", number, f"m{number}") for number in range(1, 14)]
        many += [Problem("DF22", "warning", "a.Dockerfile", number, f"w{number}") for number in range(1, 12)]
        lines = report.render(many, "github").splitlines()
        self.assertEqual(10, sum(line.startswith("::error ") for line in lines))
        self.assertEqual(10, sum(line.startswith("::warning ") for line in lines))
        self.assertIn("::notice::3 more errors are in the summary of the run", lines)
        self.assertIn("::notice::1 more warnings are in the summary of the run", lines)
        self.assertTrue(lines[0].startswith("::error "))

    def test_json_has_sorted_keys_and_the_same_bytes_every_time(self):
        out = report.render([WARNING, ERROR], "json")
        self.assertEqual(out, report.render([ERROR, WARNING], "json"))
        rows = json.loads(out)
        self.assertEqual(["addons/x/.env", "apps/Dockerfile"], [row["path"] for row in rows])
        self.assertEqual(["code", "line", "message", "path", "severity"], list(rows[0]))
        self.assertTrue(out.isascii())
        self.assertEqual("[]\n", report.render([], "json"))

    def test_the_same_finding_is_said_once(self):
        self.assertEqual(1, report.render([ERROR, ERROR], "text").count("    ST05 "))

    def test_an_unknown_format_is_refused(self):
        with self.assertRaises(ValueError):
            report.render([ERROR], "xml")

    def test_a_finding_that_cannot_be_printed_is_printable(self):
        text = report.render([Problem("ST03", "error", "bad\udcffname", 0, "bad\udcffname has a name the Lab cannot install.")], "text")
        text.encode("utf-8")
        self.assertIn("bad�name", text)


class Summaries(unittest.TestCase):

    def test_the_first_line_counts_and_gives_the_command(self):
        text = report.summary([ERROR, WARNING], "make check")
        self.assertEqual("1 problem and 1 warning to look at. To see them on your machine, run `make check`.",
                         text.splitlines()[0])
        self.assertIn("| ✗ | addons/x/.env | ST05 |", text)
        self.assertIn("| ! | apps/Dockerfile:1 | DF22 |", text)

    def test_all_good_and_not_all_good(self):
        self.assertEqual("All good.\n", report.summary([], "make check"))
        text = report.summary([], "make check", skipped=["Docker Compose 5 not found"])
        self.assertIn("1 check did not run", text.splitlines()[0])
        self.assertIn("- Docker Compose 5 not found", text)

    @staticmethod
    def outside_code(row):
        """A table row without its code spans: what the Markdown reads as Markdown."""
        return re.sub(r"(?<!\\)`[^`]*`", "CODE", row)

    def test_what_a_stranger_wrote_is_no_link_no_tag_no_code_and_no_cell(self):
        hostile = Problem("ST03", "error", "a|b`c[d](http://evil)<img src=x>.md", 2,
                          "a|b`c has [click](https://evil.example) and <script>alert(1)</script>\nnew row | x")
        text = report.summary([hostile], "make check")
        row = [line for line in text.splitlines() if line.startswith("| ✗")][0]
        self.assertEqual(1, len([line for line in text.splitlines() if line.startswith("| ✗")]))
        bare = self.outside_code(row)
        self.assertNotRegex(bare, r"(?<!\\)\[")
        self.assertNotRegex(bare, r"(?<!\\)<")
        self.assertNotRegex(bare, r"(?<!\\)`")
        self.assertEqual(6, len(re.split(r"(?<!\\)\|", row)), row)   # five bars: the cells did not move

    def test_an_address_a_contributor_chose_is_no_link_in_the_summary(self):
        """GitHub turns `http://...`, `www.` and a mail address into a link by itself. A file name or the source of an image is
        the contributor's, so the summary of the maintainers' run must not link it: such a word is shown as code, where nothing
        is a link."""
        for text in ("the image http://evil.example/login?next=https://github.com/login is not relative or https.",
                     "the base image HTTPS://EVIL.example/image has no version.", "see www.evil.example now", "mail me@evil.example soon",
                     "a (http://evil.example) b", "ftp://evil.example/a", "x http://a|b.example y"):
            with self.subTest(text=text):
                row = [line for line in report.summary([Problem("GD04", "error", "README.md", 3, text)], "make check").splitlines()
                       if line.startswith("| ✗")][0]
                bare = self.outside_code(row)
                self.assertNotRegex(bare, r"(?i)://|www\.|@", row)
                self.assertEqual(6, len(re.split(r"(?<!\\)\|", row)), row)
                self.assertIn("`", row)
        path = [line for line in report.summary([Problem("ST08", "warning", "www.evil.example.md", 0, "m")], "make check").splitlines()
                if line.startswith("| !")][0]
        self.assertIn("| `www.evil.example.md` |", path)
        plain = [line for line in report.summary([ERROR], "make check").splitlines() if line.startswith("| ✗")][0]
        self.assertEqual(-1, plain.find("`"), "a message with no address is plain text")

    def test_the_table_is_cut_and_the_rest_is_in_the_log(self):
        many = [Problem("GD01", "error", f"docs/{number}.md", number, "m") for number in range(1, 251)]
        text = report.summary(many, "make check")
        self.assertEqual(report.SUMMARY_ROWS, sum(line.startswith("| ✗") for line in text.splitlines()))
        self.assertIn(f"{250 - report.SUMMARY_ROWS} more are in the log of the step.", text)
        self.assertLess(len(text), 100_000)

    def test_the_command_cannot_close_its_code_span(self):
        self.assertIn("run `make check`", report.summary([ERROR], "make `check"))


class Untrusted(unittest.TestCase):

    def test_what_a_build_prints_cannot_start_a_workflow_command(self):
        hostile = ["::add-mask::hunter2\n", "::error::pwned\n", "::set-output name=x::y\n", "plain line"]
        out = "".join(report.untrusted(hostile))
        self.assertEqual([], commands(out))
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("::stop-commands::"))
        token = lines[0][len("::stop-commands::"):]
        self.assertRegex(token, r"[0-9a-f]{32}")
        self.assertEqual(f"::{token}::", lines[-1])
        self.assertEqual([line.rstrip("\n") for line in hostile], lines[1:-1])
        self.assertTrue(out.endswith("\n"))

    def test_before_the_region_and_after_it_commands_still_work(self):
        out = report.render([ERROR], "github") + "".join(report.untrusted(["::error::pwned"])) + report.render([WARNING], "github")
        self.assertEqual(2, len(commands(out)))

    def test_the_token_is_new_each_time(self):
        first, second = "".join(report.untrusted([])), "".join(report.untrusted([]))
        self.assertNotEqual(first, second)

    def test_the_region_is_closed_when_the_source_fails(self):
        def source():
            yield "one\n"
            raise RuntimeError("the build died")

        taken = []
        with self.assertRaises(RuntimeError):
            for line in report.untrusted(source()):
                taken.append(line)
        self.assertEqual(3, len(taken))
        token = taken[0][len("::stop-commands::"):].strip()
        self.assertEqual(f"::{token}::\n", taken[2])

    def test_a_text_that_is_whole(self):
        out = report.untrusted_text("::error::x\nsecond")
        self.assertEqual([], commands(out))
        self.assertTrue(out.endswith("::\n"))


# ---------------------------------------------------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------------------------------------------------

class Audit(WithGit):

    def catalog(self, extra=None, name="catalog", base=None):
        """A work tree of the good catalog, committed, with a remote branch origin/main at its first commit."""
        repo = self.work(name, support.files(extra or {}, base=base))
        self.git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        return repo

    def run_in(self, repo, **how):
        return audit.run(repo, audit.Options(seeder_images=("ghcr.io/lab/seeder",), **how))

    def test_the_good_catalog_is_clean_and_the_header_says_what_was_read(self):
        repo = self.catalog()
        result = self.run_in(repo)
        self.assertEqual([], [(each.code, each.path) for each in result.problems if each.code[:2] in ("ST", "SE", "CR", "DF", "GD", "IM")])
        self.assertEqual("Reading the files and the 0 commits since origin/main", result.header)
        self.assertEqual(0, result.commits)
        self.assertRegex(result.base, r"[0-9a-f]{40}")

    def test_without_origin_main_only_the_files_are_read_and_the_header_says_so(self):
        repo = self.work("alone", support.files())
        result = self.run_in(repo)
        self.assertEqual("Reading the files only: there is no origin/main to read the commits from", result.header)
        self.assertEqual("", result.base)

    def test_a_secret_a_later_commit_removed_is_still_found_before_the_push(self):
        repo = self.catalog()
        self.put(repo, "docs/note.md", f"token {support.github_token()}\n")
        self.commit(repo, "Add a note")
        self.put(repo, "docs/note.md", "token removed\n")
        self.commit(repo, "Remove it")
        result = self.run_in(repo)
        found = [each for each in result.problems if each.code == "SE02"]
        self.assertEqual(1, len(found))
        self.assertIn("looks like a GitHub token in commit ", found[0].message)
        self.assertIn(", removed later in this pull request. Fix:", found[0].message)
        self.assertNotIn(support.github_token(), found[0].message)
        self.assertEqual("Reading the files and the 2 commits since origin/main", result.header)
        self.assertEqual(1, audit.status(result, audit.Options()))

    def test_a_secret_behind_a_nul_byte_is_found_in_the_tree_and_in_the_commit(self):
        """GitHub shows no diff for a file with a NUL in it; the history reads it all the same (`--text`), and so does the tree."""
        repo = self.catalog()
        self.put(repo, "docs/notes.txt", b"\0\nkey = " + support.aws_key().encode() + b"\n")
        self.commit(repo, "Add notes")
        found = [each for each in self.run_in(repo, families=self.OWN).problems if each.code == "SE02"]
        self.assertEqual([("docs/notes.txt", 2)], [(each.path, each.line) for each in found])
        self.assertNotIn("removed later", found[0].message)
        self.put(repo, "docs/notes.txt", b"\0\nclean\n")
        self.commit(repo, "Clean it")
        found = [each for each in self.run_in(repo, families=self.OWN).problems if each.code == "SE02"]
        self.assertEqual(1, len(found))
        self.assertIn("removed later in this pull request", found[0].message)

    def test_a_key_that_a_later_commit_only_lengthened_is_still_found(self):
        """The old line is not in the file any more, whole: a character added to a public key does not make it private."""
        repo = self.catalog()
        self.put(repo, "docs/k.md", f"key = {support.aws_key()}\n")
        self.commit(repo, "Add a key")
        self.assertEqual(1, len([each for each in self.run_in(repo, families=self.OWN).problems if each.code == "SE02"]))
        self.put(repo, "docs/k.md", f"key = {support.aws_key()}9\n")
        self.commit(repo, "Hide it")
        found = [each for each in self.run_in(repo, families=self.OWN).problems if each.code == "SE02"]
        self.assertEqual(1, len(found), "the tree is clean now and the pull request is not")
        self.assertIn("in commit ", found[0].message)
        self.assertIn(", removed later in this pull request", found[0].message)

    def test_a_file_a_unit_includes_and_the_check_cannot_read_is_a_check_that_did_not_run(self):
        """The answer is never "clean" by silence: the services of that file were not judged, and the run says so."""
        unit = "include:\n  - gone.yml\nservices:\n  web:\n    image: nginx:1\n"
        repo = self.work("includes", support.files({"playgrounds/shop/compose.yml": unit, "playgrounds/shop/README.md": "# Shop\n"}))
        result = self.run_in(repo, families=("IM",))
        self.assertEqual([], result.problems)
        self.assertEqual(["the services of playgrounds/shop/gone.yml (included by playgrounds/shop/compose.yml): the file could not be read as text"],
                         result.skipped)
        self.assertEqual(1, audit.status(result, audit.Options()))
        self.assertEqual(2, audit.status(result, audit.Options(ci=True)))
        self.assertIn("Not checked: the services of playgrounds/shop/gone.yml", report.render(result.problems, "text", skipped=result.skipped))

    def test_a_commit_in_a_tools_name_is_an_error_and_its_credit_a_warning(self):
        repo = self.catalog()
        self.put(repo, "docs/a.md", "a\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "-m", f"Add a\n\nGenerated with {support.tool()} Code",
                 extra={"GIT_AUTHOR_NAME": support.tool() + " Code", "GIT_AUTHOR_EMAIL": support.tool_address()})
        result = self.run_in(repo)
        self.assertEqual(["CR01", "CR02"], sorted(each.code for each in result.problems if each.code.startswith("CR")))

    def test_the_whole_history_is_read_on_request(self):
        repo = self.work("old", support.files({"docs/old.md": f"key {support.aws_key()}\n"}))
        self.put(repo, "docs/old.md", "clean\n")
        self.commit(repo, "Clean")
        self.git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.assertEqual(0, len([each for each in self.run_in(repo).problems if each.code == "SE02"]))
        result = self.run_in(repo, history=True)
        self.assertEqual(1, len([each for each in result.problems if each.code == "SE02"]))
        self.assertEqual("Reading the files and the 2 commits of the whole history", result.header)

    def test_what_the_base_already_had_is_a_warning_and_what_is_new_is_an_error(self):
        repo = self.work("base", support.files({"docs/old.pem": "x\n"}))
        self.git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.put(repo, "docs/new.pem", "y\n")
        self.commit(repo, "Another key")
        result = self.run_in(repo)
        by_path = {each.path: each for each in result.problems if each.code == "ST06"}
        self.assertEqual("warning", by_path["docs/old.pem"].severity)
        self.assertTrue(by_path["docs/old.pem"].message.startswith(audit.PREFIX), by_path["docs/old.pem"].message)
        self.assertEqual("error", by_path["docs/new.pem"].severity)

    def test_a_secret_the_base_had_is_never_downgraded(self):
        repo = self.work("base", support.files({"docs/old.md": f"key {support.aws_key()}\n"}))
        self.git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.put(repo, "docs/b.md", "b\n")
        self.commit(repo, "Next")
        found = [each for each in self.run_in(repo).problems if each.code == "SE02"]
        self.assertEqual(["error"], [each.severity for each in found])

    def test_a_finding_is_matched_once_so_a_second_one_is_a_new_error(self):
        one = "FROM python\nCMD true\n"
        repo = self.work("base", support.files({"apps/x/Dockerfile": one}))
        self.git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.put(repo, "apps/x/Dockerfile", "FROM python\nFROM python\nCMD true\n")
        self.commit(repo, "A second stage")
        found = [each for each in self.run_in(repo, error=("DF21",)).problems if each.code == "DF21"]
        self.assertEqual(["error", "warning"], sorted(each.severity for each in found))

    def test_with_nothing_changed_there_is_nothing_to_subtract_from(self):
        repo = self.work("base", support.files({"docs/old.pem": "x\n"}))
        self.git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        found = [each for each in self.run_in(repo).problems if each.code == "ST06"]
        self.assertEqual(["error"], [each.severity for each in found], "on main itself, debt is an error")

    def test_a_warning_a_catalog_or_a_run_makes_an_error_is_one(self):
        repo = self.work("warn", support.files({"apps/x/Dockerfile": "FROM python:3\nUSER app\n"}))
        self.assertEqual(["warning"], [each.severity for each in self.run_in(repo).problems if each.code == "DF22"])
        self.assertEqual(["error"], [each.severity for each in self.run_in(repo, error=("DF22",)).problems if each.code == "DF22"])
        marked = support.files({"apps/x/Dockerfile": "FROM python:3\nUSER app\n",
                                ".github/standard.json": support.GOOD[".github/standard.json"].replace(
                                    '"standard": 1', '"standard": 1,\n  "strict": ["DF22"]')})
        repo = self.work("strict", marked)
        self.assertEqual(["error"], [each.severity for each in self.run_in(repo).problems if each.code == "DF22"])

    def test_soft_prints_every_error_as_a_warning_and_exits_zero(self):
        repo = self.work("soft", support.files({"docs/k.pem": "x\n"}))
        options = audit.Options(soft=True, seeder_images=())
        result = audit.run(repo, options)
        self.assertEqual({"warning"}, {each.severity for each in result.problems if each.code == "ST06"})
        self.assertEqual(0, audit.status(result, options))

    def test_the_exit_code(self):
        clean, bad = audit.Result([], []), audit.Result([ERROR], [])
        warn, skipped = audit.Result([WARNING], []), audit.Result([], ["Docker Compose 5 not found"])
        plain = audit.Options()
        self.assertEqual([0, 1, 0, 1], [audit.status(clean, plain), audit.status(bad, plain), audit.status(warn, plain),
                                         audit.status(skipped, plain)])
        self.assertEqual(1, audit.status(warn, audit.Options(strict=True)))
        self.assertEqual(2, audit.status(skipped, audit.Options(ci=True)))
        self.assertEqual(1, audit.status(bad, audit.Options(ci=True)))
        self.assertEqual([0, 0], [audit.status(skipped, audit.Options(soft=True)), audit.status(bad, audit.Options(soft=True))])

    def test_a_folder_catalog_is_judged_on_the_families_that_need_no_github(self):
        repo = self.work("folder", support.FOLDER)
        result = audit.run(repo, audit.Options(seeder_images=()))
        self.assertEqual([], result.problems)
        self.assertEqual([], result.skipped, "no SD, WF or CO is asked of a folder catalog")

    def test_a_tree_that_is_not_a_repository_is_read_where_it_is(self):
        folder = self.holder / "plain"
        for path, content in support.files().items():
            self.put(folder, path, content)
        result = audit.run(folder, audit.Options(seeder_images=()))
        self.assertEqual([], [each for each in result.problems if each.code[:2] in ("ST", "SE", "DF", "GD", "IM")])
        with_base = audit.run(folder, audit.Options(seeder_images=(), base="a" * 40))
        self.assertTrue(any("no git history" in why for why in with_base.skipped), with_base.skipped)
        self.assertEqual(1, audit.status(with_base, audit.Options()))

    OWN = ("ST", "SE", "CR", "DF", "GD", "IM")   # the families of this package: the others may not be in the Lab yet, and say so

    def old_and_new_key(self, name="names", **how):
        """A repository whose base commit has a bad file and whose branch adds another: ST06 twice, one of them old."""
        repo = self.work(name, support.files({"docs/old.pem": "x\n"}))
        self.first = self.git(repo, "rev-parse", "HEAD").strip()
        self.git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.put(repo, "docs/new.pem", "y\n")
        self.commit(repo, "Another key")
        return repo

    def severities(self, result):
        return {each.path: each.severity for each in result.problems if each.code == "ST06"}

    def test_a_base_is_any_name_git_resolves_to_a_commit(self):
        repo = self.old_and_new_key()
        self.git(repo, "branch", "feature-base", self.first)
        self.git(repo, "tag", "v1", self.first)
        for name in ("origin/main", "feature-base", "v1", "HEAD~1", "HEAD^", self.first, self.first[:12], "main~1"):
            with self.subTest(name=name):
                result = self.run_in(repo, base=name, families=self.OWN)
                self.assertEqual({"docs/old.pem": "warning", "docs/new.pem": "error"}, self.severities(result))
                self.assertEqual(self.first, result.base)
                self.assertEqual(f"Reading the files and the 1 commit since {self.first[:7]}", result.header)
                self.assertEqual([], result.skipped)
        warned = [each for each in self.run_in(repo, base="HEAD~1", families=self.OWN).problems if each.path == "docs/old.pem"][0]
        self.assertTrue(warned.message.startswith(audit.PREFIX), warned.message)

    def test_a_base_that_is_no_commit_is_said_and_nothing_raises(self):
        repo = self.old_and_new_key()
        for name in ("nonexistent", "a b", "--output=elsewhere", "HEAD@{1}", "a..b", "HEAD~99", "main:docs/old.pem", "x" * 400, "v1\nv2"):
            with self.subTest(name=name):
                result = self.run_in(repo, base=name, families=self.OWN)
                self.assertEqual({"docs/old.pem": "error", "docs/new.pem": "error"}, self.severities(result), "no base, no subtraction")
                self.assertEqual(1, len(result.skipped), result.skipped)
                self.assertIn("is not a commit of this repository", result.skipped[0])
                self.assertTrue(result.header.startswith("Reading the files only: "), result.header)
                self.assertIn("is not a commit of this repository", result.header)
                self.assertEqual(1, audit.status(result, audit.Options()))
        self.assertFalse((repo / "elsewhere").exists())
        self.assertEqual("", self.run_in(repo, base="nonexistent", families=self.OWN).base)

    def test_a_base_that_is_a_branch_name_works_when_the_tree_has_an_old_error_too(self):
        """The base was resolved only when the tree held no hygiene error: with one, `main` raised a ValueError."""
        repo = self.old_and_new_key()
        result = self.run_in(repo, base="origin/main", families=self.OWN)
        self.assertEqual("warning", self.severities(result)["docs/old.pem"])

    def test_a_linked_work_tree_is_read_like_a_clone(self):
        """`git worktree add` makes a checkout whose `.git` is a file; the base is read all the same."""
        repo = self.work("clone", support.files({"docs/old.pem": "x\n"}))
        self.git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        linked = self.holder / "linked"
        self.git(repo, "worktree", "add", "-q", "-b", "feature", str(linked))
        self.assertTrue((linked / ".git").is_file())
        self.put(linked, "docs/new.pem", "y\n")
        self.commit(linked, "Another key")
        for how in ({}, {"base": "origin/main"}):
            result = self.run_in(linked, families=self.OWN, **how)
            self.assertEqual({"docs/old.pem": "warning", "docs/new.pem": "error"}, self.severities(result), how)
            self.assertEqual([], result.skipped)

    def test_the_errors_stay_errors_and_it_is_said_when_the_files_of_the_base_cannot_be_read(self):
        listing = ListTree.from_files(support.files({"docs/old.pem": "x\n"}))
        how = dict(seeder_images=(), families=("ST",), base="a" * 40)
        result = audit.run(None, audit.Options(tree=listing, **how))
        self.assertEqual(["error"], [each.severity for each in result.problems if each.code == "ST06"])
        self.assertEqual(["which errors were already there at the base: the files of the base could not be read, so they stay errors"],
                         result.skipped)
        self.assertEqual(1, audit.status(result, audit.Options(ci=True)))
        result = audit.run(None, audit.Options(tree=listing, base_tree=listing, **how))
        self.assertEqual(["warning"], [each.severity for each in result.problems if each.code == "ST06"])
        self.assertEqual([], result.skipped)

        class Broken(tree.Tree):
            def entries(self):
                raise tree.TreeError("no")

        result = audit.run(None, audit.Options(tree=listing, base_tree=Broken(), **how))
        self.assertEqual(1, len(result.skipped), result.skipped)
        clean = audit.run(None, audit.Options(tree=ListTree.from_files(support.files()), **how))
        self.assertEqual([], clean.skipped, "nothing to subtract from, nothing to say")

    def test_the_images_of_the_base_are_judged_with_the_same_asks_as_the_head(self):
        """IM04 is a rule of hygiene: one that was there before the change is a warning, in CI as everywhere else."""
        marked = support.files({"catalog.yml": support.CATALOG + "  images: ghcr.io/acme/other\n"})
        repo = self.work("images", marked)
        self.git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.put(repo, "docs/more.md", "more\n")
        self.commit(repo, "More")
        result = self.run_in(repo, ci=True, repository="Acme/Lab-Hub", families=self.OWN)
        found = [each for each in result.problems if each.code == "IM04"]
        self.assertEqual(["warning"], [each.severity for each in found])
        self.assertTrue(found[0].message.startswith(audit.PREFIX), found[0].message)
        result = self.run_in(repo, online=True, repository="Acme/Lab-Hub", families=self.OWN)
        self.assertEqual(["warning"], [each.severity for each in result.problems if each.code == "IM04"])

    def test_the_first_line_names_the_base_of_a_pull_request_and_not_origin_main(self):
        bare = gitfixture.make_repo(self.holder / "pr.git", support.files())
        base = gitfixture.tip(bare)
        gitfixture.commit(bare, {"docs/a.md": "a\n"}, "Add a")
        head = gitfixture.tip(bare)
        listing = tree.GitTree(bare, head)
        result = audit.run(None, audit.Options(tree=listing, commits=tree.History(git_dir=bare, base=base), seeder_images=(), families=("ST",)))
        self.assertEqual(f"Reading the files and the 1 commit since {base[:7]}", result.header)
        result = audit.run(None, audit.Options(tree=listing, commits=tree.History(git_dir=bare, base=None), seeder_images=(), families=("ST",)))
        self.assertEqual("Reading the files and the 2 commits of the whole history", result.header)

    def test_a_catalog_with_no_standard_file_is_told_so_in_a_second_line(self):
        repo = self.work("folder", support.FOLDER)
        result = audit.run(repo, audit.Options(seeder_images=()))
        lines = result.header.split("\n")
        self.assertEqual(2, len(lines), result.header)
        self.assertEqual("No .github/standard.json: judged on the families that need no GitHub (ST, SE, CR, DF, GD, IM)", lines[1])
        text = report.render(result.problems, "text", skipped=result.skipped, header=result.header)
        self.assertEqual(lines, text.splitlines()[:2], "both lines are printed")
        self.assertEqual([], result.skipped)
        full = audit.run(self.work("full", support.files()), audit.Options(seeder_images=()))
        self.assertNotIn("\n", full.header, "a catalog with its standard is judged on all of them and says nothing more")
        chosen = audit.run(repo, audit.Options(seeder_images=(), families=("ST", "SE")))
        self.assertNotIn("\n", chosen.header, "the caller chose the families: nothing was left out")

    def test_a_base_git_does_not_have_is_said_and_never_taken_for_a_clean_history(self):
        repo = self.catalog()
        result = self.run_in(repo, base="f" * 40)
        self.assertTrue(any("could not be read" in why for why in result.skipped), result.skipped)

    def test_the_audit_writes_nothing_and_makes_no_network_call(self):
        repo = self.catalog()
        self.put(repo, "docs/new.md", "new\n")
        self.commit(repo, "More")
        self.put(repo, "untracked.txt", "u\n")
        before = sha_of(repo)

        def refused(*args, **how):
            raise AssertionError("a network call")

        with mock.patch.object(socket.socket, "connect", refused), mock.patch.object(socket, "create_connection", refused), \
                mock.patch.object(socket, "getaddrinfo", refused):
            for how in ({}, {"history": True}, {"online": True, "repository": "OWNER/lab"}, {"ci": True}):
                self.run_in(repo, **how)
        self.assertEqual(before, sha_of(repo))

    def test_online_asks_for_the_checks_that_compare_with_github_and_ci_too(self):
        marked = support.files({"catalog.yml": support.CATALOG + "  images: ghcr.io/acme/other\n"})
        repo = self.work("images", marked)
        quiet = self.run_in(repo)
        self.assertNotIn("IM04", {each.code for each in quiet.problems})
        online = self.run_in(repo, online=True, repository="Acme/Lab-Hub")
        self.assertEqual(["IM04"], [each.code for each in online.problems if each.code == "IM04"])
        self.assertIn("ghcr.io/acme/lab-hub", [each.message for each in online.problems if each.code == "IM04"][0])
        unknown = self.run_in(repo, ci=True)
        self.assertTrue(any("IM04" in why for why in unknown.skipped), unknown.skipped)

    def test_the_repository_is_read_from_the_origin_remote(self):
        repo = self.work("remote", support.files())
        self.git(repo, "remote", "add", "origin", "https://github.com/Acme/lab-hub.git")
        self.assertEqual("Acme/lab-hub", audit.origin_repository(repo))
        self.git(repo, "remote", "set-url", "origin", "git@github.com:acme/hub.git")
        self.assertEqual("acme/hub", audit.origin_repository(repo))
        self.git(repo, "remote", "set-url", "origin", "https://gitlab.example/acme/hub.git")
        self.assertEqual("", audit.origin_repository(repo))

    def test_a_commit_of_a_bare_repository_is_judged_without_a_checkout(self):
        """`check --pr`: the tree and the commits come from git objects."""
        bare = gitfixture.make_repo(self.holder / "pr.git", support.files({"docs/bad name.md": "x\n"}))
        listing = tree.GitTree(bare, gitfixture.tip(bare))
        result = audit.run(None, audit.Options(tree=listing, commits=tree.History(git_dir=bare, base=None),
                                               seeder_images=(), families=("ST",)))
        self.assertEqual(["ST03"], [each.code for each in result.problems])
        self.assertEqual(1, result.commits)

    def test_the_families_are_asked_for_by_name(self):
        repo = self.work("only", support.files({"docs/k.pem": "x\n", "apps/Dockerfile": "FROM python\n"}))
        result = self.run_in(repo, families=("DF",))
        self.assertEqual({"DF21", "DF23"}, {each.code for each in result.problems})

    def test_the_seeder_is_an_anchor_for_the_images_of_a_playground(self):
        playground = ("services:\n  shop-seed:\n    image: %s:latest\n    build: ../../../../../seeder\n"
                      "    pull_policy: ${SHOP_PULL_POLICY:-build}\n")
        repo = self.work("seeded", support.files({"playgrounds/shop/compose.yml": playground % "ghcr.io/lab/seeder",
                                                  "playgrounds/shop/README.md": "# Shop\n"}))
        self.assertEqual([], [each for each in self.run_in(repo).problems if each.code.startswith("IM")])
        other = self.work("other", support.files({"playgrounds/shop/compose.yml": playground % "ghcr.io/lab/other",
                                                  "playgrounds/shop/README.md": "# Shop\n",
                                                  "catalog.yml": support.CATALOG + "  images: ghcr.io/acme/hub\n"}))
        self.assertEqual(["IM02"], [each.code for each in self.run_in(other).problems if each.code.startswith("IM")])

    def test_the_lab_names_its_seeder_in_its_core_file(self):
        found = audit.lab_seeder_images()
        self.assertEqual(1, len(found))
        self.assertRegex(found[0], r"[a-z0-9.-]+(/[a-z0-9._-]+)+")
        self.assertEqual((), audit.lab_seeder_images(self.holder))

    def test_the_check_of_a_family_is_the_check_of_its_module(self):
        self.assertIs(structure.check, audit._load("ST"))
        with mock.patch("importlib.import_module", side_effect=ModuleNotFoundError("y", name="a_package_it_needs")):
            with self.assertRaises(ModuleNotFoundError):
                audit._load("SD")

    def test_an_unreadable_tree_is_said_as_a_skip(self):
        class Broken(tree.Tree):
            def entries(self):
                raise tree.TreeError("This catalog lists more files than the check reads.")

        result = audit.run(None, audit.Options(tree=Broken()))
        self.assertEqual([], result.problems)
        self.assertEqual(["This catalog lists more files than the check reads."], result.skipped)


# ---------------------------------------------------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------------------------------------------------

class Explain(unittest.TestCase):

    def test_the_long_form_of_a_code(self):
        text = explain.explain("st05")
        self.assertTrue(text.startswith("ST05 (error)\n"))
        for part in ("Rule     .env, or .env.* other than .env.example", "Message  {path} is an environment file. Fix:",
                     "Why      ", "Fix      git rm --cached {path}", "At Add   The Lab does not install it"):
            self.assertIn(part, text)

    def test_a_code_with_two_messages_shows_both(self):
        text = explain.explain("IM02")
        self.assertIn("is built by {a} and {b}", text)
        self.assertIn("is outside {prefix}", text)

    def test_a_word_that_is_not_a_code_is_said(self):
        text = explain.explain("nope")
        self.assertTrue(text.startswith("nope is not a rule code."))
        self.assertIn("ST", text)
        self.assertEqual(text, explain.explain("nope"))

    def test_every_code_of_the_rule_modules_has_a_long_form(self):
        table = explain.table()
        for module in (structure, secrets, commits, guides, dockerfiles, images):
            for code in module.RULES:
                self.assertIn(code, table)
                self.assertIn(code, explain.DETAILS, f"{code} has no why and no word on the Lab at Add")
                self.assertTrue(explain.DETAILS[code].why and explain.DETAILS[code].lab, code)
                self.assertIn("Why      ", explain.explain(code))

    def test_the_codes_of_every_family_are_known(self):
        known = explain.codes()
        for code in ("ST01", "ST22", "SE07", "CR02", "GD05", "DF24", "IM04", "SD11", "CO01", "BD06", "MF02", "TS02", "SM03",
                     "PB08", "E2E05", "LK01", "SC01"):
            self.assertIn(code, known)
        self.assertEqual(known, sorted(known, key=lambda code: (explain._family_index(code), code)))
        for code in known:
            self.assertTrue(explain.explain(code).startswith(code + " ("), code)

    def test_no_text_has_a_middle_dot_and_every_message_names_its_fix_or_is_the_fix(self):
        missing = set()
        for code, entry in explain.table().items():
            for text in (entry.message, entry.rule, entry.why, entry.lab, *entry.more):
                self.assertNotIn("·", text, code)
            if "Fix" not in entry.message:
                missing.add(code)
        self.assertLessEqual({"ST14", "ST20", "GD04", "DF22", "DF24"}, missing)


class Imports(unittest.TestCase):
    """The modules of this package use the standard library and the Lab's own modules, nothing else."""

    FILES = ["report.py", "tree.py", "audit.py", "explain.py", "rules/__init__.py", "rules/structure.py", "rules/secrets.py",
             "rules/commits.py", "rules/guides.py", "rules/dockerfiles.py", "rules/images.py"]
    STDLIB = {"base64", "bisect", "collections", "hashlib", "html", "importlib", "inspect", "json", "os", "posixpath", "re", "secrets",
              "stat", "subprocess", "sys", "time", "typing", "unicodedata", "urllib", "pathlib"}

    def test_no_module_imports_a_package_that_is_not_the_standard_library(self):
        import ast
        import sys
        from pathlib import Path
        allowed = self.STDLIB | set(getattr(sys, "stdlib_module_names", ()))
        base = Path(audit.__file__).resolve().parent
        for name in self.FILES:
            tree_ = ast.parse((base / name).read_text(encoding="utf-8"), feature_version=(3, 9))
            for node in ast.walk(tree_):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [each.name.split(".")[0] for each in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    modules = [node.module.split(".")[0]]
                for module in modules:
                    self.assertTrue(module in allowed or module == "lab_ui", f"{name} imports {module}")

    def test_the_sources_are_python_3_9_and_hold_no_invisible_character(self):
        import ast
        import unicodedata
        from pathlib import Path
        base = Path(audit.__file__).resolve().parent
        for name in self.FILES:
            text = (base / name).read_text(encoding="utf-8")
            ast.parse(text, feature_version=(3, 9))
            hidden = sorted({hex(ord(char)) for char in text if unicodedata.category(char) in ("Cf", "Zl", "Zp") or
                             (unicodedata.category(char) == "Cc" and char not in "\n\t")})
            self.assertEqual([], hidden, name)


if __name__ == "__main__":
    unittest.main()
