"""seedlint.py: the checks of the seed files of a playground as a command, which a catalog's CI runs (`python seedlint.py
catalog/playgrounds --format github`) and the engine's tests run through test_seed_files.py.

Playgrounds are made in a temporary folder. The folder is a stranger's pull request: links, special files, files of any
size and any name, lines of a megabyte, and YAML that makes the loader copy millions of pairs, none of which may make the
lint read outside the folder, run anything, write anything or take more than seconds of CPU. test_seed_files.py tests the
checks on files made for them; this file tests what the command adds: the problems
of a playground with their path and line, the annotations, the folders it is given, and what it does with hostile input. The count of the pairs
that YAML merge keys make the loader copy, and the refusal of a file that asks for too many, are common.py's and are tested in
test_common.py; the lint names such a file and does not load it. What would take minutes or the memory of the machine when it
goes wrong is run in a process of its own (`probed`), with a limit on both."""

import ast
import contextlib
import io
import json
import os
import random
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import yaml

from lab_seeder import aim_proxies, common
from lab_seeder import seedlint
from lab_seeder.seedlint import Problem, Report
from lab_seeder.testkit import PLAYGROUNDS

from . import chained, doubling, fanned, flowing

SEEDER = Path(seedlint.__file__).resolve().parents[1]
SERVICE = ("services:\n  seed:\n    labels: {io.gravitee.role: seed}\n    environment:\n      APIM_URL: http://apim\n"
           "      KEY: abc\n      EMPTY: ''\n")
GOOD = {"seed/apim/settings.yaml": "url: ${APIM_URL}\nkey: ${KEY:-none}\n", "seed/aim/notes.md": "# Notes\n"}
ONE_MEGABYTE = 1_000_000
# Seconds of CPU, not of the clock: a machine that runs a hundred tests at once makes a fast rule slow, never a quadratic one
# fast. A rule ends a line of a megabyte in a fraction of a second (the slowest measured is about 0.4); these limits sit a
# few times above that, and far below the minutes to hours of a pattern that went quadratic again
RULE_SECONDS, WHOLE_SECONDS = 2.0, 20.0
FOLDER = "playgrounds"
# Lists of lists through aliases, nine of the one before at each level: a few lines that are 9 ** 12 values written out
ALIASES = "a0: &a0 [x]\n" + "".join(f"a{n}: &a{n} [{', '.join(f'*a{n - 1}' for _ in range(9))}]\n" for n in range(1, 13))


def nested(count, text):
    """`text` as a string of a JSON document that is a string of another, `count` times: JSON texts one in the other."""
    for _ in range(count):
        text = json.dumps({"a": text})
    return text


def run(*argv):
    """(exit code, what it printed, what it said as an error) of the command with these arguments and this environment."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(err):
        code = seedlint.main(list(argv), out=out)
    return code, out.getvalue(), err.getvalue()


def make_playground(root, files=GOOD, name="acme", compose=SERVICE):
    """A playground in `root` with these files ({"seed/x.yaml": text or bytes}) and this compose file (None: none)."""
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    if compose is not None:
        (folder / "compose.yml").write_text(compose, encoding="utf-8")
    for relative, content in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return folder


class Made(unittest.TestCase):
    """Playgrounds in a temporary folder."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def playground(self, files=GOOD, name="acme", compose=SERVICE, root=None):
        return make_playground(root or self.root, files, name, compose)

    def link(self, path, target):
        """A symbolic link, or a skipped test on a machine that cannot make one."""
        try:
            os.symlink(target, path)
        except (OSError, NotImplementedError, AttributeError) as e:
            self.skipTest(f"this machine cannot make a link: {e}")

    def found(self, folder):
        return [(p.check, Path(p.path).relative_to(folder).as_posix(), p.line) for p in seedlint.problems(folder)]


def within(seconds, function):
    """The answer of a function that must end in `seconds`; a test that would hang fails instead."""
    box = {}
    thread = threading.Thread(target=lambda: box.update(answer=function()), daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise AssertionError(f"still running after {seconds} s")
    return box["answer"]


# --- What a playground says -------------------------------------------------------------

class Problems(Made):

    def test_a_variable_the_seed_service_does_not_set_is_found_at_its_first_use(self):
        folder = self.playground({"seed/apim/a.yaml": "name: x\nurl: ${OLLAMA_URL}\nagain: ${OLLAMA_URL}\nkey: ${KEY}\n"})
        self.assertEqual(seedlint.problems(folder), [Problem(
            "variable", str(folder / "seed/apim/a.yaml"), 2,
            "OLLAMA_URL has no value: the seed service sets none. Fix: set it, with a value, in the seed service's "
            "environment, or give it a default (${OLLAMA_URL:-default}).")])

    def test_a_default_in_the_file_an_escaped_dollar_and_a_value_the_service_sets_need_nothing(self):
        files = {"seed/a.yaml": "a: ${NONE:-x}\nb: $${page}\nc: ${KEY}\nd: ${APIM_URL}\n"}
        self.assertEqual(seedlint.problems(self.playground(files)), [])

    def test_a_setting_the_service_leaves_empty_is_no_value(self):
        self.assertEqual(self.found(self.playground({"seed/a.yaml": "a: ${EMPTY}\n"})), [("variable", "seed/a.yaml", 1)])

    def test_the_names_the_engine_gives_are_set_for_the_files_of_an_environment_only(self):
        files = {"seed/environments/newt/environment.yaml": "id: ${ENV_ID}\nname: Newt\n", "seed/environments/newt/settings.yaml": "name: ${ENV_NAME}\n",
                 "seed/apim/a.yaml": "id: ${ENV_ID}\n"}
        self.assertEqual(self.found(self.playground(files)), [("variable", "seed/apim/a.yaml", 1)])

    def test_a_file_that_does_not_parse_is_found_with_its_line(self):
        folder = self.playground({"seed/aim/a.yaml": "a: 1\nb: [1, 2\n", "seed/apim/b.json": '{"a": '})
        found = seedlint.problems(folder)
        self.assertEqual([(p.check, Path(p.path).name) for p in found], [("parse", "a.yaml"), ("parse", "b.json")])
        self.assertTrue(found[0].message.startswith("does not parse once its variables are replaced: "), found[0].message)
        self.assertGreater(found[0].line, 1)

    def test_a_file_whose_variable_is_unset_is_not_also_said_not_to_parse(self):
        self.assertEqual(self.found(self.playground({"seed/a.yaml": "a: ${NOBODY}\n"})), [("variable", "seed/a.yaml", 1)])

    def test_a_text_that_is_not_yaml_is_not_parsed(self):
        self.assertEqual(seedlint.problems(self.playground({"seed/aim/page.md": "# a: [1, 2\n", "seed/x.txt": "{"})), [])

    def test_every_kind_of_finding_is_told_for_each_file_in_path_and_line_order(self):
        files = {"seed/b.yaml": "x: ${A}\ny: ${C}\n", "seed/a.yaml": "x: ${B}\n", "seed/c.json": "{"}
        found = self.found(self.playground(files))
        self.assertEqual(found, [("variable", "seed/a.yaml", 1), ("variable", "seed/b.yaml", 1), ("variable", "seed/b.yaml", 2),
                                 ("parse", "seed/c.json", 1)])

    def test_the_findings_of_one_kind_are_told_twenty_at_a_time_and_the_others_counted(self):
        text = "".join(f"v{n}: ${{NO{n}}}\n" for n in range(30))
        found = seedlint.problems(self.playground({"seed/a.yaml": text}))
        self.assertEqual([p.check for p in found].count("variable"), 21)
        self.assertEqual([p.message for p in found if p.line == 0], ["10 more variables have no value."])

    def test_a_folder_that_is_no_playground_has_nothing_to_lint(self):
        self.assertEqual(seedlint.problems(self.root / "none"), [])
        self.assertEqual(seedlint.problems(self.playground(compose=None)), [])
        self.assertEqual(seedlint.problems(self.playground(compose="services:\n  app: {image: x}\n", name="no-seed-service")), [])
        self.assertEqual(seedlint.problems(self.playground({"README.md": "x"}, name="no-seed-folder")), [])

    def test_a_compose_file_that_does_not_parse_is_the_only_finding(self):
        folder = self.playground({"seed/a.yaml": "x: ${A}\n"}, compose="services: [\n")
        self.assertEqual(self.found(folder), [("playground", "compose.yml", 2)])

    def test_an_environment_folder_is_checked_against_itself(self):
        files = {"seed/environments/newt/environment.yaml": "id: newt\nname: Newt\n",
                 "seed/environments/newt/groups/g.yaml": "hrid: g\nname: G\nmembers:\n  - {source: memory, sourceId: nobody}\n",
                 "seed/environments/ok/environment.yaml": "id: ok\nname: Ok\n"}
        folder = self.playground(files)
        self.assertEqual(seedlint.problems(folder), [Problem(
            "environment", str(folder / "seed/environments/newt/groups/g.yaml"), 0,
            "nobody is no user of users.yaml: add it there, or flag the member `external: true`")])

    def test_an_environment_folder_without_its_file_says_so(self):
        folder = self.playground({"seed/environments/newt/users.yaml": "users: []\n"})
        self.assertEqual(self.found(folder), [("environment", "seed/environments/newt/environment.yaml", 0)])

    def test_an_environment_folder_that_cannot_be_read_is_not_also_checked_against_itself(self):
        files = {"seed/environments/newt/environment.yaml": "id: newt\nname: ${NOBODY}\n", "seed/environments/ok/x.yaml": "a: 1\n"}
        self.assertEqual(self.found(self.playground(files)),
                         [("variable", "seed/environments/newt/environment.yaml", 2), ("environment", "seed/environments/ok/environment.yaml", 0)])

    def test_merge_keys_in_reasonable_use_are_nothing_to_say(self):
        compose = ("x-env: &env {APIM_URL: http://apim, KEY: abc}\nservices:\n  seed:\n    labels: {io.gravitee.role: seed}\n"
                   "    environment: {<<: *env}\n")
        folder = self.playground({"seed/a.yaml": fanned(20, 20) + "url: ${APIM_URL}\n", "seed/b.yaml": chained(40)}, compose=compose)
        self.assertEqual(seedlint.problems(folder), [])

    def test_a_variable_no_environment_can_hold_is_named_and_the_environment_is_left_as_it_was(self):
        """A name with `=` or no characters, a value with a NUL byte, text the system cannot encode: the compose file is blamed,
        and no seed file is read with half an environment."""
        cases = {"a name with =": '      "B=C": z\n', "a name with nothing": '      "": z\n', "a value with a NUL byte": '      A: "x\\0y"\n',
                 "a name with a NUL byte": '      "A\\0B": z\n', "a lone surrogate": '      A: "\\ud800"\n'}
        for what, line in cases.items():
            with self.subTest(what=what):
                folder = self.playground({"seed/a.yaml": "a: ${KEY}\n"}, compose=SERVICE + line, name="p" + str(len(what)))
                before = dict(os.environ)
                found = seedlint.problems(folder)
                self.assertEqual([(p.check, Path(p.path).name, p.line) for p in found], [("playground", "compose.yml", 0)])
                self.assertIn("which no environment can hold", found[0].message)
                self.assertEqual(dict(os.environ), before)

    @unittest.skipUnless(os.name == "posix", "the names an environment refuses are those of POSIX")
    def test_an_environment_the_system_stops_half_way_is_given_back_whole(self):
        before = dict(os.environ)
        with self.assertRaises(ValueError), seedlint._with_environment({"SEEDLINT_A": "x", "SEEDLINT_B=C": "y"}):
            self.fail("the block must not run")
        self.assertEqual(dict(os.environ), before)
        with seedlint._with_environment({"SEEDLINT_A": "x"}):
            self.assertEqual(os.environ.get("SEEDLINT_A"), "x")
            self.assertNotIn("SEEDLINT_TEST_KEPT", os.environ)
        self.assertEqual(dict(os.environ), before)


STUDIO = ("name: Desk\ncontextPath: /mcp/desk\nsupportedProtocolVersions: ['2025-11-25']\nfga: false\n"
          "plan: {name: Copilots, security: {type: API_KEY}}\ntools: [{api: Clients API, tool: getClient}]\n")
AGENTS_STUDIO = ("name: Desk\ncontextPath: /mcp/desk\nprotocolVersion: '2025-11-25'\ntools: [{api: Clients API, tool: getClient}]\n"
                 "access: {registration: {name: Registration, type: OAUTH2_AM}, agents: {name: Agents, type: JWT, jwksUrl: x, "
                 "clientIdClaim: client_id}}\n")


class Studios(Made):
    """The subscriptions of a studio: an application, a plan of the studio that takes them, and a key that is text."""

    def studio(self, subscriptions, studio=STUDIO):
        folder = self.playground({**GOOD, "seed/aim/studios/desk.yaml": studio + subscriptions})
        return [(p.check, Path(p.path).name, p.message) for p in seedlint.problems(folder)]

    def test_subscriptions_that_the_platform_takes_have_nothing_to_say(self):
        for text in ("", "subscriptions: [{application: Copilot, apiKey: '${KEY}'}]\n",
                     "subscriptions: [{application: Copilot, plan: Copilots}]\n"):
            with self.subTest(text=text):
                self.assertEqual(self.studio(text), [])
        self.assertEqual(self.studio("subscriptions: [{application: Copilot}]\n", AGENTS_STUDIO), [])

    def test_what_the_platform_or_the_seed_would_refuse_is_named(self):
        for text, studio, message in (
                ("", STUDIO.replace("supportedProtocolVersions: ['2025-11-25']\n", ""), "one of the two"),
                ("protocolVersion: '2025-11-25'\n", STUDIO, "one of the two"),
                ("", STUDIO.replace("['2025-11-25']", "[2025-11-25]"), "a protocol version is text: quote it"),
                ("", AGENTS_STUDIO.replace("'2025-11-25'", "2025-11-25"), "a protocol version is text: quote it"),
                ("subscriptions: [{application: Copilot, apiKey: ${NUMBER}}]\n", STUDIO,
                 "the apiKey of Copilot is read as int, not as text: quote the variable in the file, '${NAME}'"),
                ("subscriptions: [{application: Copilot, plan: Others}]\n", STUDIO, "'Others' is no plan of the studio"),
                ("subscriptions: [{application: Copilot, apiKey: abc}]\n", STUDIO.replace("API_KEY", "JWT"),
                 "an apiKey needs a plan with API keys"),
                ("subscriptions: [{application: Copilot}]\n", STUDIO.replace("API_KEY", "KEY_LESS"),
                 "a keyless plan takes no subscription"),
                ("subscriptions: [{application: Copilot}]\n", STUDIO.replace("API_KEY", "KEYLESS"),
                 "a keyless plan takes no subscription"),
                ("subscriptions: [{application: Copilot, apiKey: abc}]\n", AGENTS_STUDIO, "an apiKey needs a plan with API keys"),
                ("subscriptions: [{application: Copilot, plan: Registration}]\n", AGENTS_STUDIO, "'Registration' is no plan"),
                ("subscriptions: [{apiKey: abc}]\n", STUDIO, "each names its application"),
                ("subscriptions: {application: Copilot}\n", STUDIO, "subscriptions is a list of")):
            with self.subTest(text=text, message=message):
                compose = SERVICE + "      NUMBER: '1234'\n"
                folder = self.playground({**GOOD, "seed/aim/studios/desk.yaml": studio + text}, compose=compose)
                found = [(p.check, Path(p.path).name, p.message) for p in seedlint.problems(folder)]
                self.assertEqual(len(found), 1, found)
                self.assertEqual(found[0][:2], ("studio", "desk.yaml"))
                self.assertIn(message, found[0][2])

    def test_a_file_of_another_folder_is_not_read_as_a_studio(self):
        folder = self.playground({**GOOD, "seed/aim/mcp-proxies/desk.yaml": STUDIO + "subscriptions: [{plan: 3}]\n"})
        self.assertEqual(seedlint.problems(folder), [])


class Documents(Made):

    def test_a_number_is_not_read_for_its_digits(self):
        """Only the strings of a document are read: a number of many digits costs the square of them on a Python without a limit."""
        text = "[" + "9" * 100_000 + "]"
        self.assertEqual(seedlint._parsed(text), [0])
        self.assertEqual(seedlint._parsed('{"a": 1.5, "b": ["x", 2]}'), {"a": 0, "b": ["x", 0]})

    def test_a_document_nested_too_deep_for_the_parser_is_a_finding_and_not_the_end_of_the_run(self):
        text = "[" * 3000 + '"\\x41"' + "]" * 3000 + "\n"
        found = seedlint.problems(self.playground({"seed/a.yaml": text, "seed/b.json": text}))
        self.assertEqual(sorted(Path(p.path).name for p in found), ["a.yaml", "b.json"])

    def test_a_document_that_repeats_itself_through_aliases_is_read_once(self):
        self.assertEqual(within(30, lambda: seedlint.problems(self.playground({"seed/a.yaml": ALIASES}))), [])


# --- The command ------------------------------------------------------------------------

class Command(Made):

    def bad(self, root=None):
        return self.playground({"seed/a.yaml": "x: ${NOBODY}\ny: ${NOONE}\n"}, root=root)

    def test_a_good_folder_prints_what_it_read_and_ends_well(self):
        self.playground()
        self.assertEqual(run(str(self.root)), (0, f"All good: 2 seed files of 1 playground ({self.root})\n", ""))

    def test_the_problems_of_a_folder_are_lines_of_a_file_a_line_and_a_message(self):
        self.bad()
        code, out, _ = run(str(self.root))
        lines = out.splitlines()
        self.assertEqual(code, 1)
        self.assertEqual(lines[0], f"{self.root}/acme/seed/a.yaml:1: NOBODY has no value: the seed service sets none. Fix: set it, "
                                   "with a value, in the seed service's environment, or give it a default (${NOBODY:-default}).")
        self.assertTrue(lines[1].startswith(f"{self.root}/acme/seed/a.yaml:2: NOONE has no value"), lines[1])
        self.assertEqual(lines[2], f"2 problems in 1 seed file of 1 playground ({self.root})")

    def test_github_gets_one_annotation_for_each_problem(self):
        self.bad()
        code, out, _ = run(str(self.root), "--format", "github")
        lines = out.splitlines()
        escaped = str(self.root / "acme/seed/a.yaml").replace(":", "%3A").replace(",", "%2C")
        self.assertEqual(code, 1)
        self.assertEqual(lines[0], f"::error file={escaped},line=1,title=Seed variable::NOBODY has no value: the seed service sets "
                                   "none. Fix: set it, with a value, in the seed service's environment, or give it a default "
                                   "(${NOBODY:-default}).")
        self.assertTrue(lines[1].startswith(f"::error file={escaped},line=2,title=Seed variable::NOONE has no value"))
        self.assertEqual(len(lines), 3)
        self.assertFalse(lines[2].startswith("::"), "the summary is a line of the log, not a command")

    def test_an_annotation_names_the_file_as_the_repository_does(self):
        """A workflow checks a catalog out into a folder of its own: the file is `playgrounds/...`, not `catalog/playgrounds/...`."""
        for git in ("dir", "file"):
            with self.subTest(git=git):
                repository = self.root / git
                repository.mkdir()
                if git == "dir":
                    (repository / ".git").mkdir()
                else:
                    (repository / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
                self.bad(root=repository / FOLDER)
                out = run(str(repository / FOLDER), "--format", "github")[1]
                self.assertTrue(out.startswith(f"::error file={FOLDER}/acme/seed/a.yaml,line=1,"), out[:100])
                self.assertIn(f"{repository}/{FOLDER}/acme/seed/a.yaml:1: NOBODY", run(str(repository / FOLDER))[1], "text keeps the path it was given")

    def test_a_folder_that_does_not_exist_is_nothing_to_lint(self):
        for fmt in ("text", "github"):
            with self.subTest(format=fmt):
                code, out, err = run(str(self.root / FOLDER), "--format", fmt)
                self.assertEqual((code, err), (0, ""))
                self.assertNotIn("::error", out)
        self.assertEqual(run(str(self.root / FOLDER))[1], f"No folder {self.root}/playgrounds: nothing to lint.\n")

    def test_a_folder_of_playgrounds_that_have_no_seed_has_nothing_to_lint(self):
        self.playground(compose="services:\n  app: {image: x}\n")
        self.assertEqual(run(str(self.root)), (0, f"Nothing to lint: no playground of {self.root} has a seed.\n", ""))

    def test_a_compose_file_beside_the_playgrounds_does_not_hide_them(self):
        """`playgrounds/compose.yml` is no playground, and no reason to leave the ones under it unread."""
        self.bad()
        for decoy in ("services: {}\n", "services: [\n", SERVICE):
            with self.subTest(decoy=decoy):
                (self.root / "compose.yml").write_text(decoy, encoding="utf-8")
                code, out, _ = run(str(self.root), "--format", "github")
                self.assertEqual(code, 1)
                self.assertIn("/acme/seed/a.yaml,line=1,title=Seed variable::NOBODY has no value", out)
                self.assertIn("/acme/seed/a.yaml,line=2,title=Seed variable::NOONE has no value", out)

    def test_a_folder_that_is_a_playground_and_holds_playgrounds_lints_both(self):
        outer = self.playground({"seed/a.yaml": "x: ${OUTER}\n"}, name="outer")
        self.bad(root=outer)
        report = seedlint.lint(outer)
        self.assertEqual((report.playgrounds, report.files), (2, 2))
        self.assertEqual([(p.check, Path(p.path).relative_to(outer).as_posix(), p.line) for p in report.problems],
                         [("variable", "acme/seed/a.yaml", 1), ("variable", "acme/seed/a.yaml", 2), ("variable", "seed/a.yaml", 1)])

    def test_the_links_beside_a_compose_file_that_is_no_playground_are_named_and_those_of_a_playground_are_not(self):
        outside = self.root / "outside"
        self.bad(root=outside)
        folder = self.root / "catalog"
        folder.mkdir()
        (folder / "compose.yml").write_text("services: {}\n", encoding="utf-8")
        self.link(folder / "three", outside / "acme")
        self.assertEqual([(p.check, Path(p.path).name) for p in seedlint.lint(folder).problems], [("playground", "three")])
        playground = self.playground(name="mine")
        self.link(playground / "data", outside)
        self.assertEqual(seedlint.lint(playground).problems, [], "a folder of a playground that is a link is the playground's business")

    def test_a_link_given_as_the_folder_is_not_followed(self):
        outside = self.root / "outside"
        self.bad(root=outside)
        folder = self.root / "catalog-playgrounds"
        self.link(folder, outside)
        for argument in (str(folder), str(folder) + "/"):
            with self.subTest(argument=argument):
                code, out, _ = run(argument, "--format", "github")
                lines = out.splitlines()
                self.assertEqual(code, 1)
                self.assertTrue(lines[0].startswith("::error file=") and lines[0].endswith("::is a link. Fix: a folder is not a link: copy it"), lines[0])
                self.assertNotIn("Seed variable", out)
                self.assertEqual(lines[1], f"1 problem in 0 seed files of 0 playgrounds ({folder})")
        self.assertEqual(seedlint.lint(folder), Report([Problem("playground", str(folder), 0, "is a link. Fix: a folder is not a link: copy it")], 0, 0))
        self.assertEqual([p.check for p in seedlint.problems(folder)], ["playground"], "a playground that is a link is not read")
        self.link(self.root / "one", outside / "acme")
        self.assertEqual([p.check for p in seedlint.problems(self.root / "one")], ["playground"])

    def test_a_line_of_text_never_starts_a_command_of_the_runner(self):
        """A runner takes a line that starts with `::` or `##[` for a command of its own, and a folder is named by a stranger."""
        names = ("::error::INJECTED", "##[error]INJECTED", " ::set-env name=A::b")
        tree = self.root / "tree"
        for name in names:
            self.playground({"seed/a.yaml": "x: ${NOBODY}\n"}, name=name, root=tree)
        before = os.getcwd()
        os.chdir(tree)
        self.addCleanup(os.chdir, before)
        code, out, _ = run(".")
        self.assertEqual(code, 1)
        for line in out.splitlines():
            self.assertFalse(line.lstrip().startswith(("::", "##[")), line)
        for name in names:
            self.assertIn(f"./{name}/seed/a.yaml:1: NOBODY has no value", out)
        self.assertEqual(seedlint.render([Problem("variable", "tree/::x.yaml", 1, "m")], "text"), "tree/::x.yaml:1: m", "only the start of a line")

    def test_a_path_that_is_not_a_folder_is_a_wrong_command_line(self):
        (self.root / "file").write_text("x", encoding="utf-8")
        self.assertEqual(run(str(self.root / "file")), (2, "", f"{self.root}/file is not a folder\n"))

    def test_several_folders_are_all_linted_and_the_worst_decides(self):
        self.bad(root=self.root / "one")
        self.playground(root=self.root / "two")
        code, out, _ = run(str(self.root / "one"), str(self.root / "two"), str(self.root / "three"))
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines()[-3:], [f"2 problems in 1 seed file of 1 playground ({self.root}/one)",
                                                  f"All good: 2 seed files of 1 playground ({self.root}/two)",
                                                  f"No folder {self.root}/three: nothing to lint."])

    def test_only_the_first_problems_are_printed_and_the_others_counted(self):
        text = "".join(f"v{n}: ${{NO{n}}}\n" for n in range(seedlint.MANY))
        for n in range(30):
            self.playground({f"seed/f{n}.yaml": text}, name=f"p{n}")
        code, out, _ = run(str(self.root))
        lines = out.splitlines()
        self.assertEqual(code, 1)
        self.assertEqual(len(lines), seedlint.SHOWN + 2)
        self.assertEqual(lines[-2], f"{600 - seedlint.SHOWN} more problems not shown.")

    def test_it_runs_as_a_script_with_a_folder(self):
        self.bad()
        by_argument = subprocess.run([sys.executable, str(SEEDER / "seedlint.py"), str(self.root), "--format", "github"],
                                     capture_output=True, text=True, cwd=str(self.root), check=False)
        self.assertEqual(by_argument.returncode, 1, by_argument.stderr)
        self.assertEqual(len([line for line in by_argument.stdout.splitlines() if line.startswith("::error file=")]), 2)
        self.assertEqual(subprocess.run([sys.executable, str(SEEDER / "seedlint.py"), str(self.root / "none")], capture_output=True,
                                        text=True, check=False).returncode, 0)


# --- The annotations --------------------------------------------------------------------

class Annotations(unittest.TestCase):
    """Escaped as report.py of the Lab UI escapes its own: a name in a stranger's tree cannot start a command."""

    def test_what_would_end_or_change_a_command_is_escaped_once(self):
        self.assertEqual(seedlint.escape_data("evil\n::set-env name=A::b 100%"), "evil%0A::set-env name=A::b 100%25")
        self.assertEqual(seedlint.escape_data("a\r\nb"), "a%0D%0Ab")
        self.assertEqual(seedlint.escape_property("x/y/a:b,c.yml"), "x/y/a%3Ab%2Cc.yml")
        self.assertEqual(seedlint.escape_data("%0A"), "%250A")
        self.assertEqual(seedlint.escape_property("a%0A::error::x"), "a%250A%3A%3Aerror%3A%3Ax")

    def test_a_file_named_to_start_a_command_gives_one_annotation(self):
        for name in ("a%0A::error::x.yaml", "a\n::error::x.yaml", "a\r::set-env name=A::b.yaml", "a,line=9,title=x.yaml"):
            with self.subTest(name=name):
                line = seedlint.annotation(Problem("variable", "catalog/p/seed/" + name, 3, "NOBODY has no value\n::error::x"))
                properties, _, data = line[2:].partition("::")
                self.assertEqual(line.splitlines(), [line], "one line: nothing after a line feed can start a command")
                self.assertEqual(properties.count(","), 2, "the file, the line and the title are the three properties it wrote")
                self.assertNotIn(":", properties.partition(" ")[2], "no colon in a property, but the one that ends them all")
                self.assertTrue(properties.startswith("error file=catalog/p/seed/a"))
                self.assertTrue(properties.endswith(",line=3,title=Seed variable"))
                self.assertTrue(data.startswith("NOBODY has no value%0A::error::x"))

    def test_a_line_is_written_only_when_it_is_known(self):
        self.assertNotIn("line=", seedlint.annotation(Problem("environment", "a.yaml", 0, "m")))
        self.assertIn(",line=7,", seedlint.annotation(Problem("environment", "a.yaml", 7, "m")))

    def test_a_name_that_is_not_text_cannot_stop_the_command(self):
        """A name with bytes that are no UTF-8 reaches Python as lone surrogates, which stdout cannot write."""
        report = Report([Problem("variable", "seed/a\udcffb.yaml", 1, "m\ud800")], 1, 1)
        for fmt in ("text", "github"):
            with self.subTest(format=fmt), mock.patch.object(seedlint, "lint", return_value=report):
                out = io.StringIO()
                with tempfile.TemporaryDirectory() as folder:
                    self.assertEqual(seedlint.main([folder, "--format", fmt], out=out), 1)
                self.assertNotRegex(out.getvalue(), "[\ud800-\udfff]")

    def test_a_name_with_control_characters_is_one_line_of_text(self):
        line = seedlint.render([Problem("variable", "seed/a\nb\x1b[31m.yaml", 1, "m\nn")], "text")
        self.assertEqual(line, "seed/a?b?[31m.yaml:1: m?n")


# --- A stranger's tree ------------------------------------------------------------------

class Hostile(Made):

    def test_a_link_to_a_file_outside_is_not_read(self):
        outside = self.root / "outside.yaml"
        outside.write_text("secret: ${NOBODY}\nurl: http://localhost\nbroken: [\n", encoding="utf-8")
        folder = self.playground({"seed/a.yaml": "a: 1\n"})
        self.link(folder / "seed" / "b.yaml", outside)
        found = seedlint.problems(folder)
        self.assertEqual([(p.check, Path(p.path).name) for p in found], [("read", "b.yaml")])
        self.assertIn("is a link", found[0].message)

    def test_a_link_to_a_folder_outside_is_not_followed(self):
        outside = self.root / "outside"
        (outside / "deeper").mkdir(parents=True)
        (outside / "deeper" / "x.yaml").write_text("secret: ${NOBODY}\n", encoding="utf-8")
        folder = self.playground({"seed/a.yaml": "a: 1\n"})
        self.link(folder / "seed" / "linked", outside)
        found = seedlint.problems(folder)
        self.assertEqual([(p.check, Path(p.path).name) for p in found], [("read", "linked")])

    def test_a_link_for_the_compose_file_the_seed_folder_or_a_playground_is_not_followed(self):
        outside = self.root / "outside"
        self.playground({"seed/a.yaml": "a: ${NOBODY}\n"}, root=outside, name="p")   # what following a link would find
        folder = self.root / "catalog" / "one"
        folder.mkdir(parents=True)
        self.link(folder / "compose.yml", outside / "p" / "compose.yml")
        (folder / "seed").mkdir()
        self.assertEqual(self.found(folder), [("playground", "compose.yml", 0)])
        other = self.root / "catalog" / "two"
        other.mkdir()
        (other / "compose.yml").write_text(SERVICE, encoding="utf-8")
        self.link(other / "seed", outside / "p" / "seed")
        self.assertEqual(self.found(other), [("playground", "seed", 0)])
        self.link(self.root / "catalog" / "three", outside / "p")
        code, out, _ = run(str(self.root / "catalog"))
        self.assertEqual(code, 1)
        self.assertEqual(sorted(line.split(":", 1)[0].rsplit("/", 1)[-1] for line in out.splitlines()[:-1]), ["compose.yml", "seed", "three"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "this machine has no named pipes")
    def test_a_special_file_with_the_name_of_a_seed_file_is_named_and_does_not_block_the_lint(self):
        folder = self.playground({"seed/a.yaml": "a: 1\n"})
        os.mkfifo(folder / "seed" / "pipe.yaml")
        found = within(10, lambda: seedlint.problems(folder))
        self.assertEqual([(p.check, Path(p.path).name, p.message) for p in found], [("read", "pipe.yaml", "is not a regular file")])

    def test_a_file_over_the_limit_is_named_and_not_read(self):
        folder = self.playground({"seed/big.yaml": "a: " + "x" * seedlint.LIMIT, "seed/ok.yaml": "a: 1\n"})
        found = seedlint.problems(folder)
        self.assertEqual([(p.check, Path(p.path).name) for p in found], [("read", "big.yaml")])
        self.assertIn("more than the 1024 KB that is read", found[0].message)

    def test_a_file_that_is_not_utf8_is_named(self):
        found = seedlint.problems(self.playground({"seed/a.yaml": b"a: \xff\xfe\n"}))
        self.assertEqual([(p.check, p.message) for p in found], [("read", "is not UTF-8 text. Fix: save it as UTF-8")])

    def test_nothing_a_seed_file_says_is_run(self):
        marker = self.root / "ran"
        code = f"open({str(marker)!r}, 'w').write('x')"
        files = {"seed/evil.py": code, "seed/a.yaml": f"a: !!python/object/apply:os.system ['touch {marker}']\n",
                 "seed/b.yaml": f"a: !!python/object/new:subprocess.Popen [[touch, {marker}]]\n"}
        found = seedlint.problems(self.playground(files))
        self.assertEqual([(p.check, Path(p.path).name) for p in found], [("parse", "a.yaml"), ("parse", "b.yaml")])
        self.assertFalse(marker.exists())

    def test_nothing_is_written_and_the_environment_is_left_as_it_was(self):
        files = {**GOOD, "seed/bad.yaml": "a: [\n", "seed/environments/newt/environment.yaml": "id: newt\nname: ${NOBODY}\n"}
        folder = self.playground(files)

        def snapshot():
            return sorted((str(p.relative_to(self.root)), p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
                          for p in self.root.rglob("*"))

        with mock.patch.dict(os.environ, {"SEEDLINT_TEST_KEPT": "yes"}):
            before, environment = snapshot(), dict(os.environ)
            seedlint.problems(folder)
            run(str(self.root), "--format", "github")
            self.assertEqual(snapshot(), before)
            self.assertEqual(dict(os.environ), environment)
            self.assertNotIn("APIM_URL", os.environ, "the variables of the seed service are the lint's own, for the time it loads a file")

    def test_a_page_or_an_asset_of_an_environment_names_a_file_of_its_folder_and_no_other(self):
        outside = self.root / "outside.svg"
        outside.write_text("<svg/>", encoding="utf-8")
        env = self.root / "acme" / "seed" / "environments" / "newt"
        files = {"seed/environments/newt/environment.yaml": "id: newt\nname: Newt\n",
                 "seed/environments/newt/portal/pages/up.yaml": "hrid: up\nfile: ../../../../../../outside.svg\n",
                 "seed/environments/newt/portal/pages/abs.yaml": f"hrid: abs\nfile: {outside}\n",
                 "seed/environments/newt/portal/pages/in.yaml": "hrid: in\nfile: ../content/in.md\n",
                 "seed/environments/newt/portal/content/in.md": "# in\n",
                 "seed/environments/newt/portal/theme.yaml": "hrid: theme\nassets: {logo: ../../../../../../outside.svg, home: ../assets/home.svg}\n",
                 "seed/environments/newt/assets/home.svg": "<svg/>"}
        folder = self.playground(files)
        wanted = ["portal/pages/abs.yaml: file " + str(outside) + " is outside the folder",
                  "portal/pages/up.yaml: file ../../../../../../outside.svg is outside the folder",
                  "portal/theme.yaml: assets.logo ../../../../../../outside.svg is outside the folder"]
        self.assertEqual([f"{path}: {text}" for path, text in seedlint._environment_findings(env, {})], wanted)
        self.assertEqual([(p.check, p.message) for p in seedlint.problems(folder)],
                         [("environment", text.partition(": ")[2]) for text in wanted])

    def test_a_link_for_an_asset_or_a_file_of_an_environment_is_not_followed(self):
        outside = self.root / "outside.svg"
        outside.write_text("x" * (seedlint.SMALL + 1), encoding="utf-8")
        files = {"seed/environments/newt/environment.yaml": "id: newt\nname: Newt\n",
                 "seed/environments/newt/portal/theme.yaml": "hrid: theme\nassets: {logo: ../assets/logo.svg}\n"}
        folder = self.playground(files)
        env = folder / "seed" / "environments" / "newt"
        (env / "assets").mkdir()
        self.link(env / "assets" / "logo.svg", outside)
        self.assertEqual(seedlint._environment_findings(env, {}), [("portal/theme.yaml", "assets.logo ../assets/logo.svg does not exist")])
        os.unlink(env / "portal" / "theme.yaml")
        self.link(env / "portal" / "theme.yaml", outside)
        self.assertEqual(seedlint._environment_findings(env, {}), [("portal/theme.yaml", "is a link. Fix: a seed holds files, not links: copy the file")])

    def test_a_document_that_repeats_itself_through_aliases_is_not_written_out_in_a_message(self):
        text = ALIASES + "users:\n  - {big: *a12}\n  - {sourceId: *a12, source: memory}\n"
        files = {"seed/environments/newt/environment.yaml": "id: newt\nname: Newt\n", "seed/environments/newt/users.yaml": text}
        found = within(30, lambda: seedlint.problems(self.playground(files)))
        self.assertEqual([p.check for p in found], ["environment", "environment"])
        self.assertTrue(found[0].message.startswith("cannot be checked: TypeError: "), found[0].message)
        self.assertIn("unhashable type: 'list'", found[0].message, "Python words it as it likes")
        self.assertEqual(found[1].message, "a user needs a source and a sourceId: ...", "and the finding made before it is kept")

    def test_a_document_of_the_wrong_shape_is_a_finding_and_not_a_crash(self):
        for text in ("- a\n- b\n", "just text\n", "users: {a: 1}\n", "users: [1, 2]\n"):
            with self.subTest(text=text):
                files = {"seed/environments/newt/environment.yaml": "id: newt\nname: Newt\n", "seed/environments/newt/users.yaml": text}
                found = seedlint.problems(self.playground(files, name=f"p{len(text)}{text[:2].strip('-: ')}"))
                self.assertTrue(all(p.check == "environment" for p in found), found)

    def test_a_seed_service_that_is_not_what_compose_says_is_no_seed_service(self):
        composes = ("", "- a\n", "services: [a]\n", "services: {seed: just text}\n", "services: {seed: {labels: [1, {a: b}]}}\n",
                    "services: {seed: {labels: x}}\n")
        for number, compose in enumerate(composes):
            with self.subTest(compose=compose):
                self.assertEqual(seedlint.problems(self.playground(compose=compose, name=f"p{number}")), [])

    def test_a_value_of_the_seed_service_that_is_a_document_is_not_expanded(self):
        compose = ("x-bomb:\n" + "".join("  " + line for line in ALIASES.splitlines(True)) +
                   "services:\n  seed:\n    labels: {io.gravitee.role: seed}\n    environment: {BOMB: *a12, N: 5}\n")
        folder = self.playground(compose=compose)
        environment = within(30, lambda: seedlint.seed_environment(seedlint.seed_service(folder / "compose.yml")))
        self.assertEqual(environment["N"], "5")
        self.assertLess(len(environment["BOMB"]), 10)

    def test_the_front_matter_of_a_skill_that_asks_for_millions_of_pairs_is_named_and_not_loaded(self):
        """The seeder of the skills loads the front matter of a SKILL.md (aim_catalog.skill_files): it is YAML of a stranger like
        the rest, and a bomb in it hangs the seed container. Another Markdown file has no front matter to load."""
        bomb = "---\n" + doubling(22) + "---\nA skill.\n"
        good = "---\nname: y\ndescription: d\nbase: &b {k: 1}\nuse: {<<: *b}\n---\nbody\n"
        folder = self.playground({"seed/aim/skills/x/SKILL.md": bomb, "seed/aim/skills/y/SKILL.md": good, "seed/aim/notes.md": bomb,
                                  "seed/aim/skills/z/SKILL.md": "no front matter, " + "<<: " * 10 + "\n"})
        construct = yaml.SafeLoader.construct_document

        def building(loader, node):
            if any(getattr(key, "value", None) == "a22" for key, _ in getattr(node, "value", [])):
                raise AssertionError("the loader ran on a bomb")
            return construct(loader, node)

        with mock.patch.object(yaml.SafeLoader, "construct_document", building):
            self.assertEqual(seedlint.problems(folder), [Problem("read", str(folder / "seed/aim/skills/x/SKILL.md"), 0, common.MERGES)])

    def test_the_front_matter_is_found_as_the_seeder_of_the_skills_finds_it(self):
        source = (SEEDER / "lab_seeder" / "aim_catalog.py").read_text(encoding="utf-8")
        self.assertIn("re.match(r\"" + seedlint.FRONT_MATTER.pattern + "\"", source)
        self.assertEqual(seedlint._front_matter("---\nname: x\n---\nbody\n---\n"), "name: x")
        self.assertEqual(seedlint._front_matter("body\n---\nname: x\n---\n"), "")
        self.assertEqual(seedlint._front_matter(""), "")

    def test_a_name_with_a_line_feed_is_one_annotation(self):
        folder = self.playground({"seed/a\n::error::x.yaml": "a: ${NOBODY}\n"})
        code, out, _ = run(str(folder), "--format", "github")
        self.assertEqual(code, 1)
        lines = out.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("::error file="))
        self.assertIn("a%0A%3A%3Aerror%3A%3Ax.yaml", lines[0])
        self.assertEqual(len(run(str(folder))[1].splitlines()), 2)


# A line of a megabyte that is made to make a pattern run for minutes, or the parser fill the memory
SHAPES = {
    "defaults opened and never closed": "${A:-" * 200_000,
    "the same, escaped": "$${A:-" * 170_000,
    "dollars": "$" * ONE_MEGABYTE,
    "braces": "}" * ONE_MEGABYTE,
    "openings": "${" * 500_000,
    "variables": "${KEY}" * 160_000,
    "variables nobody sets": "${A}" * 250_000,
    "addresses": "http://" * 140_000,
    "one host": "http://" + "a" * ONE_MEGABYTE,
    "text": "a" * ONE_MEGABYTE,
    "brackets": "[" * ONE_MEGABYTE,
    "one value": "k: " + "a" * ONE_MEGABYTE,
    "a comment": "# " + "a" * ONE_MEGABYTE,
    "spaces": "k: a" + " " * ONE_MEGABYTE + "b",
    "merge keys": "{a: &a {x: 1}, " + "<<: *a, " * 60_000 + "}",   # the slowest to compose: half a megabyte of pairs
    "exclamation marks": "!" * ONE_MEGABYTE,
    # Escapes and backslashes, in the strings the loader reads
    "escapes": 'k: "' + "\\x41" * 250_000 + '"',
    "backslashes": 'k: "' + "\\\\" * 500_000 + '"',
    "backslashes in a plain scalar": "k: " + "\\" * ONE_MEGABYTE,
    "unicode escapes": 'k: "' + "\\u002f" * 160_000 + '"',
    "escaped addresses": 'k: "' + "".join(f"http:\\x2f\\x2fh{n};" for n in range(50_000)) + '"',
    "one escaped host": 'k: "http:\\x2f\\x2f' + "a" * ONE_MEGABYTE + '"',
    "variables and a backslash": "\\" + "${KEY}" * 160_000,
    # A string that is JSON text: its escapes, its depth and its size
    "a json text with escapes": "k: '{\"a\": \"" + "".join(f"http:\\u002f\\u002fh{n};" for n in range(30_000)) + "\"}'",
    "json texts inside one another": "k: '" + nested(5, "".join(f"http:\\u002f\\u002fh{n};" for n in range(8_000))) + "'",
    "a json text that is one string of a megabyte": "k: '{\"a\": \"" + "\\u002f" * 150_000 + "\"}'",
    # A variable where YAML wants a name
    "variables in anchors": "x: &${A} 1\n" * 60_000 + "\\",
}
# Files of many lines: the parser of YAML is Python, and takes seconds for them, so they are measured as a whole
LINES = {
    "many scalars with an escape": "".join('- "a\\n"\n' for _ in range(100_000)),
    "many escaped addresses": "".join(f'- "http:\\x2f\\x2fh{n}"\n' for n in range(40_000)),
    "many defaults with escapes": "".join(f'k{n}: "${{A:-http:\\x2f\\x2fh{n}}}"\n' for n in range(25_000)),
    "many json texts": "".join(f"- '[{{\"a\": \"http:\\u002f\\u002fh{n}\"}}]'\n" for n in range(20_000)),
}
# Files that must be named and not read, whatever the suffix: (file, text, what the seed service sets besides). The merge keys
# are sized to be refused by a long way and to cost seconds and a gigabyte when nothing refuses them, so that a regression is a
# failed test and not a machine that runs out of memory (the doubling of 60 mappings is counted, never loaded: test_common.py)
NOT_READ = {
    "defaults opened and never closed in YAML": ("a.yaml", "${A:-" * 200_000, {}),
    "the same in Markdown, escaped": ("a.md", "$${A:-" * 170_000, {}),
    "the same in the strings of JSON": ("a.json", json.dumps({"a": "${A:-" * 190_000}), {}),
    "variables that would fill the memory": ("a.yaml", "a: ${BIG}\n" * 100_000, {"BIG": "x" * 200_000}),
    "merge keys chained through anchors": ("a.yaml", chained(3000), {}),
    "merge keys that double": ("a.yaml", doubling(22), {}),
    "merge keys that double, by their tag": ("a.yaml", doubling(22, merge="!!merge m"), {}),
    "one mapping that a thousand others merge": ("a.yaml", fanned(2000, 1000), {}),
    "merge keys that a variable brings": ("a.yaml", "a: [${BOMB}]\n", {"BOMB": flowing(22)}),   # no YAML until it is replaced
    "merge keys in the front matter of a skill": ("aim/skills/x/SKILL.md", "---\n" + doubling(22) + "---\nA skill.\n", {}),
}
MERGING = [name for name in NOT_READ if name.startswith(("merge keys", "one mapping"))]
IN_AN_ENVIRONMENT = ("merge keys chained through anchors", "merge keys that double")
# The same in the compose file of the playground, which the lint loads before it knows the seed service
COMPOSES = {"merge keys that double in the compose file": "x-bomb:\n" + doubling(22, "  "),
            "merge keys chained in the compose file": "x-bomb:\n" + chained(3000, "  ")}
SEED_SERVICE = "services:\n  seed:\n    labels: {io.gravitee.role: seed}\n"


def limited():
    """Limits this process, which runs `probe`, on the CPU it may use (CPU_LIMIT seconds: the clock of a machine that runs a
    hundred tests at once says nothing, the CPU a quadratic pattern needs does) and on its memory, so that a regression that
    fills the memory ends here and not in the machine. A limit this system does not take is left out."""
    try:
        import resource
    except ImportError:
        return
    for name, soft, hard in (("RLIMIT_CPU", CPU_LIMIT, CPU_LIMIT + 10), ("RLIMIT_AS", 8 << 30, 8 << 30)):   # SIGXCPU first, SIGKILL later
        try:
            resource.setrlimit(getattr(resource, name), (soft, hard))
        except (AttributeError, ValueError, OSError):
            pass


def probe():
    """Runs every hostile case and says how long each took (seconds of CPU) and what it found: {"rules": {shape: {rule:
    seconds}}, "whole": {shape and suffix: seconds}, "found": {case: [[check, message]]}}. The tests run it in a process of
    its own (`probed`)."""
    limited()
    rules, whole, found = {}, {}, {}

    def seconds(function):
        started = time.process_time()
        function()
        return time.process_time() - started

    for name, text in SHAPES.items():
        as_json = json.dumps({"k": text})
        rules[name] = {"_first_uses": seconds(lambda: seedlint._first_uses(seedlint._variables(text), {"KEY": "k"})),
                       "compose_value": seconds(lambda: seedlint.compose_value(text)),
                       "_slow": seconds(lambda: seedlint._slow("a.yaml", text)),
                       "_slow of JSON": seconds(lambda: seedlint._slow("a.json", as_json))}
    with tempfile.TemporaryDirectory() as folder:
        for number, (name, text) in enumerate(SHAPES.items()):
            for suffix in (".yaml", ".json", ".md"):
                playground = make_playground(Path(folder), {f"seed/x{suffix}": text}, f"p{number}{suffix}")
                whole[f"{name} in {suffix}"] = seconds(lambda: seedlint.problems(playground))
        for number, (name, text) in enumerate(LINES.items()):
            playground = make_playground(Path(folder), {"seed/x.yaml": text}, f"l{number}")
            whole[f"{name} in .yaml"] = seconds(lambda: seedlint.problems(playground))
        for number, (name, (file, text, given)) in enumerate(NOT_READ.items()):
            variables = "".join(f"      {key}: {json.dumps(value)}\n" for key, value in given.items())
            playground = make_playground(Path(folder), {f"seed/{file}": text}, f"n{number}", SERVICE + variables)
            started = time.process_time()
            found[name] = [[p.check, p.message] for p in seedlint.problems(playground)]
            whole[f"{name} (not read)"] = time.process_time() - started
        for number, (name, text) in enumerate(COMPOSES.items()):
            playground = make_playground(Path(folder), {"seed/a.yaml": "a: 1\n"}, f"c{number}", text + SEED_SERVICE)
            started = time.process_time()
            found[name] = [[p.check, p.message] for p in seedlint.problems(playground)]
            whole[f"{name} (not read)"] = time.process_time() - started
        for number, name in enumerate(IN_AN_ENVIRONMENT):   # the files of an environment: the same loader, asked directly
            playground = make_playground(Path(folder), {"seed/environments/newt/environment.yaml": NOT_READ[name][1]}, f"e{number}")
            started = time.process_time()
            found[name + " in an environment"] = seedlint._environment_findings(playground / "seed/environments/newt", {})
            whole[f"{name} in an environment (not read)"] = time.process_time() - started
    return {"rules": rules, "whole": whole, "found": found}


PROBED = {}
# The process that runs `probe` may use this much CPU, and has this long on the clock before it is given up as stuck: the
# backstop of the limits of each case, which are the real test. The CPU of a pattern that went quadratic is minutes to hours
CPU_LIMIT, CLOCK_LIMIT = 300, 1800


def probed():
    """The result of `probe`, made once, in a process that is stopped when it uses too much CPU. A pattern that is quadratic
    again would keep this one busy for hours, and no thread can interrupt a regular expression. The limit is on the CPU, not on
    the clock: a loaded machine makes the clock of a fast rule long, and would blame the pattern."""
    if not PROBED:
        code = "import json, sys; sys.path.insert(0, '.'); from tests.test_seedlint import probe; print(json.dumps(probe()))"
        try:
            done = subprocess.run([sys.executable, "-c", code], cwd=str(SEEDER), capture_output=True, text=True, timeout=CLOCK_LIMIT,
                                  check=False)
            if getattr(signal, "SIGXCPU", None) is not None and done.returncode == -signal.SIGXCPU:
                PROBED["error"] = (f"the lint used more than {CPU_LIMIT} s of CPU on lines of a megabyte: a pattern is quadratic again")
            else:
                PROBED.update(json.loads(done.stdout) if done.returncode == 0 else {"error": done.stderr[-2000:]})
        except subprocess.TimeoutExpired:
            PROBED["error"] = f"the lint did not end in {CLOCK_LIMIT} s of the clock on lines of a megabyte"
    if "error" in PROBED:
        raise AssertionError(PROBED["error"])
    return PROBED


class OneMegabyteLine(unittest.TestCase):
    """A rule finishes a line of a megabyte in a fraction of a second; the whole check, which also parses the file, in seconds
    of CPU. The limits are far above what is measured and far below the minutes of a pattern that is quadratic again."""

    def test_no_rule_takes_seconds_on_a_line_of_a_megabyte(self):
        for shape, rules in probed()["rules"].items():
            for rule, seconds in rules.items():
                with self.subTest(shape=shape, rule=rule):
                    self.assertLess(seconds, RULE_SECONDS)

    def test_the_whole_check_ends_on_a_file_of_such_a_line_and_on_the_files_it_will_not_read(self):
        for what, seconds in probed()["whole"].items():
            with self.subTest(case=what):
                self.assertLess(seconds, WHOLE_SECONDS)

    def test_yaml_that_makes_the_loader_copy_millions_of_pairs_is_named_and_not_loaded(self):
        """Chained, doubling, one mapping merged by a thousand, spelled `<<` or by its tag, and brought by a variable of the
        seed service. The loader would take minutes for the first and fill the memory for the second."""
        found = probed()["found"]
        for case in MERGING:
            with self.subTest(case=case):
                self.assertEqual(found[case], [["read", common.MERGES]])

    def test_the_compose_file_and_the_files_of_an_environment_are_not_loaded_either(self):
        found = probed()["found"]
        for case in COMPOSES:
            with self.subTest(case=case):
                self.assertEqual(found[case], [["playground", common.MERGES]])
        for case in IN_AN_ENVIRONMENT:
            with self.subTest(case=case):
                self.assertEqual(found[case + " in an environment"][0], ["environment.yaml", common.MERGES])

    def test_defaults_that_nothing_closes_are_named_and_not_read(self):
        found = probed()["found"]
        for case in ("defaults opened and never closed in YAML", "the same in Markdown, escaped", "the same in the strings of JSON"):
            with self.subTest(case=case):
                self.assertEqual(found[case], [["read", seedlint.UNCLOSED]])

    def test_a_file_that_would_weigh_too_much_once_its_variables_are_replaced_is_not_loaded(self):
        [[check, message]] = probed()["found"]["variables that would fill the memory"]
        self.assertEqual((check, message[:30]), ("parse", "would weigh more than 8 MB onc"))

    def test_the_strings_of_a_json_file_are_counted_one_by_one(self):
        """The closing brace of the object is no closing of the default inside the string, for the loader that reads the string."""
        text = json.dumps({"a": "${A:-" * 190_000, "b": 1})
        self.assertGreater(text.rfind("}"), len(text) - 3)
        self.assertEqual(seedlint._cost(text), 0, "the text as a whole closes everything")
        self.assertGreater(seedlint._slow("x.json", text), seedlint.SLOW)

    def test_a_few_defaults_that_nothing_closes_are_a_file_like_another(self):
        self.assertEqual(seedlint._slow("a.yaml", "a: ${A:-x\n" * 3 + "b: 1\n"), 0)
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(seedlint.problems(make_playground(Path(folder), {"seed/a.yaml": 'a: "${KEY:-x"\n'})), [])


# --- One implementation of each pattern -------------------------------------------------

class Patterns(unittest.TestCase):
    """The lint matches only up to the last brace of a text, and does not run the patterns of the seeder on all of it: it
    must find what they find."""

    ALPHABET = "${}A:-x$_ \né٣?\n"

    def texts(self, count, longest=14):
        rnd = random.Random(20261017)
        return ["".join(rnd.choice(self.ALPHABET) for _ in range(rnd.randint(0, longest))) for _ in range(count)]

    def test_the_variables_are_those_the_pattern_of_the_seeder_finds(self):
        for text in self.texts(20_000) + ["${A:-}", "$${A}", "x${A:-b}y${C}", "${A:-${B}}", "${A:-x\n}", "$$${A}", "${1A}"]:
            wanted = [(m.start(), m.groups()) for m in common.VARIABLE.finditer(text)]
            self.assertEqual([(m.start(), m.groups()) for m in seedlint._variables(text)], wanted, repr(text))

    def test_the_unset_variables_are_what_the_tests_of_the_seed_files_always_found(self):
        def before(text, environment, provided=()):
            used = {m.group(2) for m in common.VARIABLE.finditer(text) if not m.group(1) and m.group(3) is None}
            return {name for name in used if name not in provided and not environment.get(name)}

        for text in self.texts(5_000):
            for environment in ({}, {"A": "x", "x": ""}):
                self.assertEqual({name for name, _ in seedlint._first_uses(seedlint._variables(text), environment, ("ENV_ID",))},
                                 before(text, environment, ("ENV_ID",)), repr(text))

    def test_a_value_is_what_compose_gives_it(self):
        reference = re.compile(r"\$(?:\$|\{[A-Za-z_]\w*(?::?-([^}]*))?\})")

        def before(value):
            return reference.sub(lambda m: "$" if m.group(0) == "$$" else (m.group(1) or ""), value)

        for text in self.texts(20_000) + ["${A-b}", "${A:-b}$$", "$${A:-b}", "$$", "$", "${A:-b$$}", "}$$"]:
            self.assertEqual(seedlint.compose_value(text), before(text), repr(text))

    def test_the_cost_of_a_text_is_what_its_unclosed_defaults_make_the_seeder_read(self):
        self.assertEqual(seedlint._cost("${A:-x} ${B:-"), len(" ${B:-"), "one default, read to the end of the text after the last brace")
        self.assertEqual(seedlint._cost("${A:-x}"), 0)
        self.assertEqual(seedlint._cost("x" * 100), 0)
        self.assertEqual(seedlint._cost("${A:-" * 3), 3 * 15)


# --- The module in the engine -----------------------------------------------------------

class InTheEngine(unittest.TestCase):

    def test_no_module_of_the_image_imports_what_the_image_leaves_out(self):
        """The loader the seed runs is common.py, and the guard of YAML lives there: a module that is in the image cannot import a
        tool that is not."""
        listed = {line.strip() for line in (SEEDER / ".dockerignore").read_text(encoding="utf-8").splitlines()}
        tools = {name.rsplit("/", 1)[-1][:-3] for name in listed if name.startswith("lab_seeder/") and name.endswith(".py")}
        self.assertEqual(tools, {"testkit", "seedlint"})
        for path in sorted((SEEDER / "lab_seeder").glob("*.py")):
            if f"lab_seeder/{path.name}" in listed:
                continue
            imports = [node for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))) if isinstance(node, (ast.Import, ast.ImportFrom))]
            names = {alias.name.split(".")[0] for node in imports for alias in node.names} | {(getattr(node, "module", None) or ".").split(".")[0] for node in imports}
            with self.subTest(module=path.name):
                self.assertEqual(names & tools, set())

    def test_the_sample_is_read_whole(self):
        report = seedlint.lint(PLAYGROUNDS)
        self.assertEqual((report.problems, report.playgrounds, report.files), ([], 2, 48))


class StudioShapes(Made):
    """The subscriptions of a studio's file, in a file of any shape: a finding or none, never a crash."""

    def studio(self, text):
        return seedlint.problems(self.playground({**GOOD, "seed/aim/studios/desk.yaml": text}))

    def test_a_plan_or_an_access_of_another_shape_is_read_without_a_crash(self):
        for text in ("name: Desk\ncontextPath: /mcp/desk\nplan: Copilots\nsubscriptions: [{application: Copilot}]\n",
                     "name: Desk\ncontextPath: /mcp/desk\naccess: yes\nsubscriptions: [{application: Copilot}]\n",
                     "name: Desk\ncontextPath: /mcp/desk\nplan: {name: P, security: API_KEY}\nsubscriptions: [{application: A}]\n"):
            with self.subTest(text=text):
                self.assertIsInstance(self.studio(text), list)

    def test_unknown_keys_of_any_type_are_named(self):
        said = [p.message for p in self.studio(STUDIO + "subscriptions: [{application: A, 1: x, b: y}]\n")]
        self.assertTrue(any("is not a key of a subscription" in message for message in said), said)

    def test_an_empty_list_of_subscriptions_is_what_the_seed_takes(self):
        self.assertEqual(aim_proxies.read_subscriptions({"subscriptions": None}, "Desk"), [])
        self.assertEqual(self.studio(STUDIO + "subscriptions:\n"), [])
