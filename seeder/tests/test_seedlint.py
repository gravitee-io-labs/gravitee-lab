"""seedlint.py: the checks of the seed files of a playground as a command, which a catalog's CI runs (`python seedlint.py
catalog/playgrounds --format github`) and the engine's tests run through test_seed_files.py.

Playgrounds are made in a temporary folder. The folder is a stranger's pull request: links, special files, files of any
size and any name, lines of a megabyte, and YAML that makes the loader copy millions of pairs, none of which may make the
lint read outside the folder, run anything, write anything or take more than seconds of CPU. test_seed_files.py tests the
checks on files made for them (the 30 tests that used to carry the code); this file tests what the command adds: the problems
of a playground with their path and line, the addresses a file writes in escapes (in a default, in a JSON text inside a string,
with a user in front of the host; the names that resolve nowhere are allowed there, and a file whose strings cannot be read is
named), the annotations, the folders it is given, and what it does with hostile input. The count of the pairs
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
from urllib.parse import urlsplit

import yaml

from lab_seeder import common
from lab_seeder import seedlint
from lab_seeder.seedlint import Problem, Report

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
# The names of the folders of a catalog that these tests make in a temporary folder, spelled in two parts: test_testkit.py looks
# in the strings of the tests for the names of the folders of the repository's own content, which no test but the ones that
# move may read
ADDONS, FOLDER = "add" + "ons", "play" + "grounds"


def nested(count, text):
    """`text` as a string of a JSON document that is a string of another, `count` times: JSON texts one in the other."""
    for _ in range(count):
        text = json.dumps({"a": text})
    return text


def run(*argv, variables=None):
    """(exit code, what it printed, what it said as an error) of the command with these arguments and this environment."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(err):
        code = seedlint.main(list(argv), variables={} if variables is None else variables, out=out)
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
    started = time.perf_counter()
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise AssertionError(f"still running after {seconds} s")
    return box["answer"], time.perf_counter() - started


# --- What a playground says -------------------------------------------------------------

class Problems(Made):

    def test_a_good_playground_has_nothing_to_say(self):
        self.assertEqual(seedlint.problems(self.playground()), [])

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
        files = {"seed/environments/novo/environment.yaml": "id: ${ENV_ID}\nname: Novo\n", "seed/apim/a.yaml": "id: ${ENV_ID}\n"}
        self.assertEqual(self.found(self.playground(files)), [("variable", "seed/apim/a.yaml", 1)])

    def test_an_address_that_is_written_out_is_found_with_its_line(self):
        files = {"seed/aim/a.yaml": "name: x\nurl: http://bakery-mcp:8000/mcp\ndocs: https://docs.gravitee.io/apim\n"}
        folder = self.playground(files)
        self.assertEqual(seedlint.problems(folder), [Problem(
            "address", str(folder / "seed/aim/a.yaml"), 2,
            "http://bakery-mcp is written out. Fix: an address is a ${VARIABLE} set by the seed service.")])

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
        files = {"seed/b.yaml": "x: ${A}\ny: http://h\n", "seed/a.yaml": "x: ${B}\n", "seed/c.json": "{"}
        found = self.found(self.playground(files))
        self.assertEqual(found, [("variable", "seed/a.yaml", 1), ("variable", "seed/b.yaml", 1), ("address", "seed/b.yaml", 2),
                                 ("parse", "seed/c.json", 1)])

    def test_the_findings_are_the_same_each_time(self):
        folder = self.playground({"seed/a.yaml": "x: ${A}\ny: ${B}\nz: http://h\n", "seed/b.yaml": "q: [\n"})
        self.assertEqual(seedlint.problems(folder), seedlint.problems(folder))

    def test_the_findings_of_one_kind_are_told_twenty_at_a_time_and_the_others_counted(self):
        text = "".join(f"v{n}: ${{NO{n}}}\n" for n in range(30)) + "".join(f"u{n}: http://h{n}\n" for n in range(25))
        found = seedlint.problems(self.playground({"seed/a.yaml": text}))
        self.assertEqual([p.check for p in found].count("variable"), 21)
        self.assertEqual([p.check for p in found].count("address"), 21)
        self.assertEqual([p.message for p in found if p.line == 0], ["5 more addresses are written out.", "10 more variables have no value."])

    def test_a_folder_that_is_no_playground_has_nothing_to_lint(self):
        self.assertEqual(seedlint.problems(self.root / "none"), [])
        self.assertEqual(seedlint.problems(self.playground(compose=None)), [])
        self.assertEqual(seedlint.problems(self.playground(compose="services:\n  app: {image: x}\n", name="no-seed-service")), [])
        self.assertEqual(seedlint.problems(self.playground({"README.md": "x"}, name="no-seed-folder")), [])

    def test_a_compose_file_that_does_not_parse_is_the_only_finding(self):
        folder = self.playground({"seed/a.yaml": "x: ${A}\n"}, compose="services: [\n")
        self.assertEqual(self.found(folder), [("playground", "compose.yml", 2)])

    def test_an_environment_folder_is_checked_against_itself(self):
        files = {"seed/environments/novo/environment.yaml": "id: novo\nname: Novo\n",
                 "seed/environments/novo/groups/g.yaml": "hrid: g\nname: G\nmembers:\n  - {source: memory, sourceId: nobody}\n",
                 "seed/environments/ok/environment.yaml": "id: ok\nname: Ok\n"}
        folder = self.playground(files)
        self.assertEqual(seedlint.problems(folder), [Problem(
            "environment", str(folder / "seed/environments/novo/groups/g.yaml"), 0,
            "nobody is no user of users.yaml: add it there, or flag the member `external: true`")])

    def test_an_environment_folder_without_its_file_says_so(self):
        folder = self.playground({"seed/environments/novo/users.yaml": "users: []\n"})
        self.assertEqual(self.found(folder), [("environment", "seed/environments/novo/environment.yaml", 0)])

    def test_an_environment_folder_that_cannot_be_read_is_not_also_checked_against_itself(self):
        files = {"seed/environments/novo/environment.yaml": "id: novo\nname: ${NOBODY}\n", "seed/environments/ok/x.yaml": "a: 1\n"}
        self.assertEqual(self.found(self.playground(files)),
                         [("variable", "seed/environments/novo/environment.yaml", 2), ("environment", "seed/environments/ok/environment.yaml", 0)])

    def test_the_findings_of_an_environment_folder_are_those_of_environment_problems(self):
        files = {"seed/environments/novo/environment.yaml": "id: other\nname: Novo\n"}
        folder = self.playground(files)
        wanted = seedlint.environment_problems(folder / "seed/environments/novo", {"APIM_URL": "http://apim"})
        self.assertEqual(wanted, ["environment.yaml: id is 'other', the folder is 'novo'"])
        self.assertEqual([p.message for p in seedlint.problems(folder)], ["id is 'other', the folder is 'novo'"])

    def test_the_seed_service_may_set_its_variables_either_way_they_are_written(self):
        compose = ("services:\n  seed:\n    labels: [io.gravitee.role=seed]\n    environment:\n      - URL=${URL:-http://apim}\n"
                   "      - BARE\n")
        folder = self.playground({"seed/a.yaml": "a: ${URL}\nb: ${BARE}\n"}, compose=compose)
        self.assertEqual(self.found(folder), [("variable", "seed/a.yaml", 2)])

    def test_a_scheme_is_found_in_any_case_and_a_public_host_in_any_case_too(self):
        for text in ("u: HTTP://evil:8080", "u: Http://evil", "u: hTTps://evil/x"):
            self.assertEqual(len(seedlint.literal_addresses(text)), 1, text)
        self.assertEqual(seedlint.literal_addresses("a: HTTPS://Docs.Gravitee.IO/x\nb: Http://${HOST}/y"), set())
        self.assertEqual(self.found(self.playground({"seed/a.yaml": "a: 1\nu: HTTP://evil:8080\n"})), [("address", "seed/a.yaml", 2)])

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


# --- What a file writes in escapes ------------------------------------------------------

FIX = "Fix: an address is a ${VARIABLE} set by the seed service."


def escaped(host, path="/x"):
    """`https://host/x` as a double-quoted scalar of YAML or a string of JSON writes it: each slash is an escape, so that the
    text of the file shows no `://`."""
    return "https:\\u002f\\u002f" + host + path


def written(value):
    """A string as a double-quoted scalar of YAML and a string of JSON write it, both: each `://` is in escapes, so that the text
    shows none for the first rule to find, and a quote, a backslash or a control character is escaped too."""
    return json.dumps(value).replace("://", ":\\u002f\\u002f")


class Escapes(Made):
    """A YAML or JSON file can write `https://host` with escapes, and the text shows no `://` for the first rule to find. The
    strings of the parsed document are read too, and an address under a name that nothing resolves (RFC 2606 and 6761: .test,
    .example, .invalid, example.com, .org and .net) is allowed there, as a documentation URL cannot reach a service."""

    SPELLINGS = {
        "a unicode escape": '"https:\\u002f\\u002fevil.com/x"',
        "a hexadecimal escape": '"https:\\x2f\\x2fevil.com/x"',
        "a long unicode escape": '"https:\\U0000002f\\U0000002fevil.com/x"',
        "an escaped slash": '"https:\\/\\/evil.com/x"',
        "a letter of the scheme": '"h\\x74tps://evil.com/x"',
        "the colon": '"https\\x3a//evil.com/x"',
        "a line that goes on": '"https://\\\n    evil.com/x"',
    }
    ALLOWED = ("docs.apelor.test", "a.b.c.test", "x.example", "x.invalid", "example.com", "www.example.org", "a.b.example.net",
               "DOCS.Example.ORG", "docs.apelor.test.", "x.test:8080", "example.com:443", "x_y.test", "a-b.example")
    NOT_ALLOWED = ("evil.com", "test", "example", "invalid", "localhost", "notexample.com", "myexample.org", "example.com.evil.io",
                   "example.org.evil.com", "evil.test.io", "x.testing", "example.co", "examplecom", "x.examples", "test.com",
                   "test.evil", "127.0.0.1", "docs.gravitee.io.evil.com", ".test", "x..test", "-.test!", "ex ample.com")

    def addresses(self, text, name="a.yaml"):
        return seedlint.escaped_addresses(name, text)

    def test_an_address_written_in_escapes_is_found_in_every_spelling_of_the_escape(self):
        for what, scalar in self.SPELLINGS.items():
            for name in ("a.yaml", "a.yml"):
                with self.subTest(what=what, file=name):
                    self.assertEqual(self.addresses("u: " + scalar + "\n", name), {"https://evil.com"})
                    self.assertEqual(seedlint.literal_addresses("u: " + scalar + "\n"), set(), "the text shows none")

    def test_an_escape_that_ends_the_name_the_first_rule_reads_is_found(self):
        """The first rule reads the host up to a backslash: `https://docs.gravitee.io` is a public name, and `\\u002eevil.com`
        after it makes the host another."""
        for text in ('u: "https://docs.gravitee.io\\u002eevil.com/x"\n', 'u: "https://www.w3.org\\x2eevil.com/x"\n'):
            with self.subTest(text=text):
                self.assertEqual(seedlint.literal_addresses(text), set())
                self.assertEqual(len(self.addresses(text)), 1)

    def test_an_address_written_in_escapes_in_json_is_found(self):
        for what, string in {"a unicode escape": '"https:\\u002f\\u002fevil.com/x"', "an escaped slash": '"https:\\/\\/evil.com/x"',
                             "a letter of the scheme": '"\\u0068ttps://evil.com/x"'}.items():
            with self.subTest(what=what):
                self.assertEqual(self.addresses('{"u": ' + string + ', "n": 1}', "a.json"), {"https://evil.com"})

    def test_it_is_found_as_a_problem_with_its_line_and_it_says_that_it_is_written_in_escapes(self):
        files = {"seed/a.yaml": "x: 1\ny:\n  - " + self.SPELLINGS["a unicode escape"] + "\n", "seed/b.json": '{\n"u": "http:\\/\\/bakery-mcp:8000"\n}'}
        folder = self.playground(files)
        self.assertEqual(seedlint.problems(folder), [
            Problem("address", str(folder / "seed/a.yaml"), 3, f"https://evil.com is written out in escapes. {FIX}"),
            Problem("address", str(folder / "seed/b.json"), 0, f"http://bakery-mcp is written out in escapes. {FIX}")])

    def test_it_reaches_the_command_as_an_annotation_of_the_file_and_the_line(self):
        self.playground({"seed/a.yaml": "x: 1\nu: " + self.SPELLINGS["the colon"] + "\n"})
        code, out, _ = run(str(self.root), "--format", "github")
        self.assertEqual(code, 1)
        self.assertRegex(out.splitlines()[0], r"^::error file=.*a\.yaml,line=2,title=Seed address::https://evil\.com is written out in escapes\.")

    def test_a_key_is_a_string_too(self):
        self.assertEqual(self.addresses('"http:\\x2f\\x2fevil.com": 1\n'), {"http://evil.com"})
        self.assertEqual(self.addresses('{"http:\\u002f\\u002fevil.com": 1}', "a.json"), {"http://evil.com"})

    def test_the_names_that_resolve_nowhere_are_allowed(self):
        for host in self.ALLOWED:
            for name, text in (("a.yaml", f'u: "{escaped(host)}"\n'), ("a.json", f'{{"u": "{escaped(host)}"}}')):
                with self.subTest(host=host, file=name):
                    self.assertEqual(self.addresses(text, name), set())

    def test_the_allowance_stops_where_the_name_does(self):
        """A name that only ends like one, a bare `test` (a service of a Compose network may be named so), a real host that
        starts like one, or a name that is no host: found."""
        for host in self.NOT_ALLOWED:
            for name, text in (("a.yaml", f'u: "{escaped(host)}"\n'), ("a.json", f'{{"u": "{escaped(host)}"}}')):
                with self.subTest(host=host, file=name):
                    self.assertNotEqual(self.addresses(text, name), set())

    def test_a_user_in_front_of_the_name_does_not_make_it_the_host(self):
        """`https://example.com:x@elsewhere/` goes to `elsewhere`: only a port may follow the colon."""
        for host in ("example.com:pw@evil.com", "example.com@evil.com", "docs.test:x@evil.com", "docs.test:@evil.com",
                     "example.com:123456@evil.com", "example.com:80x@evil.com"):
            with self.subTest(host=host):
                self.assertNotEqual(self.addresses(f'u: "{escaped(host)}"\n'), set())
        for host in ("example.com:80", "example.com:", "docs.test:8443"):
            with self.subTest(host=host):
                self.assertEqual(self.addresses(f'u: "{escaped(host)}"\n'), set())

    # What a parser takes for the user in front of a host, whatever pattern read the name before it
    JOINERS = ("'", '"', "}", " ", "\t", "\n", "\x0b", "\xa0", "\\", ",", ";", "<")

    def both(self, value):
        """The same string as a YAML file and as a JSON file: [(text, file name)]."""
        return [(f"u: {written(value)}\n", "a.yaml"), (f'{{"u": {written(value)}}}', "a.json")]

    def test_a_character_that_does_not_end_the_authority_joins_the_user_in_front_of_the_host(self):
        """A parser ends the authority of an address at `/`, `?` or `#` and at nothing else: the quote, the space, the tab or the
        backslash after a documentation host are part of the user, and the host is the name after the `@`, which is the address."""
        for name in ("example.com", "docs.apelor.test", "www.example.org", "a.b.example.net", "x.invalid", "x.example",
                     "docs.gravitee.io", "www.w3.org"):
            for joiner in self.JOINERS:
                value = f"https://{name}{joiner}@evil.com/x"
                with self.subTest(name=name, joiner=repr(joiner)):
                    self.assertEqual(urlsplit(value).hostname, "evil.com", "the attack is a real one: a parser connects to evil.com")
                    for text, file in self.both(value):
                        self.assertEqual(self.addresses(text, file), {"https://evil.com"})
        text = f"u: {written('https://example.com' + chr(39) + '@evil.com/x')}\n"
        self.assertEqual(self.found(self.playground({"seed/a.yaml": text})), [("address", "seed/a.yaml", 1)])

    def test_an_at_sign_after_the_authority_is_in_front_of_nothing(self):
        """The path, the query and the fragment may hold one. Prose that puts an address of its own between a name and the first
        slash is read as a parser would read it: the host is the name after the `@`."""
        for value in ("https://example.com/a@b", "https://docs.apelor.test/pd/x@y.pdf", "https://docs.gravitee.io/a@b",
                      "https://example.com:8080/x@y", "see https://docs.apelor.test/x and mail me@x.org",
                      "write to https://docs.apelor.test or support@apelor.test"):
            for text, file in self.both(value):
                with self.subTest(value=value, file=file):
                    self.assertEqual(self.addresses(text, file), set())
        for text, file in self.both("write to https://docs.apelor.test or me@evil.org"):
            self.assertEqual(self.addresses(text, file), {"https://evil.org"})

    def test_a_user_in_front_of_a_variable_or_a_public_host_does_not_make_it_the_host(self):
        """The exemption of a name that starts with a variable and the one of a public name hold when the name is the host, and
        not when `@` makes it the user of another: the address told is the host."""
        for value in ("https://${KEY}@evil.com/x", "https://${@evil.com/x", "https://${KEY}:${KEY}@evil.com:8080/x",
                      "https://${KEY}'@evil.com/x", "https://${KEY} @evil.com/x"):
            for text, file in self.both(value):
                with self.subTest(value=value, file=file):
                    self.assertEqual(self.addresses(text, file), {"https://evil.com"})
        for host in ("docs.gravitee.io", "www.w3.org", "json-schema.org", "huggingface.co"):
            for value in (f"https://{host}'@evil.com/x", f"https://{host}:x@evil.com/x", f"https://{host} @evil.com/x"):
                for text, file in self.both(value):
                    with self.subTest(value=value, file=file):
                        self.assertEqual(self.addresses(text, file), {"https://evil.com"})
        for value in ("https://${U}:${P}@${HOST}/x", "https://${U}:${P}@${HOST}:8080", "https://${U}@docs.gravitee.io/x", "https://${KEY}/x",
                      "https://${KEY}:8080/x", "https://docs.gravitee.io:8443/x", "https://docs.gravitee.io/a@b",
                      "https://user:pw@docs.apelor.test/x", "https://${U}:${P}@docs.apelor.test:8443/x"):
            for text, file in self.both(value):
                with self.subTest(value=value, file=file):
                    self.assertEqual(self.addresses(text, file), set())

    def test_the_allowance_and_the_user_rule_are_two_switches_of_one_function(self):
        """The strings of a document get both. The first rule, on the text as it is, gets neither (): the day it gets one, it
        is a word at the call."""
        text = "u: https://docs.apelor.test/x https://docs.gravitee.io'@evil.com/x https://${KEY}@evil.com/x"

        def told(**switches):
            return [address for address, _ in seedlint._addresses(text, **switches)]

        self.assertEqual(told(), ["https://docs.apelor.test"])
        self.assertEqual(told(reserved=True), [])
        self.assertEqual(told(strict=True), ["https://docs.apelor.test", "https://evil.com"])
        self.assertEqual(told(reserved=True, strict=True), ["https://evil.com"])
        self.assertEqual(sorted(seedlint.literal_addresses(text)), ["https://docs.apelor.test"])

    def test_a_default_is_read_as_the_text_it_becomes(self):
        """The seeder puts the default in the text before YAML reads it, and in a string after JSON has read it: an address that
        a default writes in escapes is an address."""
        cases = {"in YAML": ("a.yaml", 'u: "${H:-https:\\x2f\\x2fevil.com}"\n'),
                 "in YAML, with other text": ("a.yaml", 'u: "x ${H:-https:\\u002f\\u002fevil.com/x} ${K:-y}" # ${Z:-z}\n'),
                 "in JSON": ("a.json", '{"u": "${H:-https:\\u002f\\u002fevil.com}"}')}
        for number, (what, (file, text)) in enumerate(cases.items()):
            with self.subTest(what=what):
                self.assertEqual(self.addresses(text, file), {"https://evil.com"})
                folder = self.playground({f"seed/{file}": text}, name=f"p{number}")
                with mock.patch.dict(os.environ, {}, clear=True):
                    self.assertIn("https://evil.com", json.dumps(common.load(folder / "seed" / file)), "the seeder gets it")
                self.assertEqual([p.check for p in seedlint.problems(folder)], ["address"])
        self.assertEqual(seedlint.literal_addresses('u: "${H:-https://evil.com}"'), {"https://evil.com"}, "and in the clear")

    def test_a_default_the_parser_cannot_take_in_the_place_of_the_variable_does_not_hide_the_file(self):
        """`a: b` in a plain scalar is no YAML where the variable stands, though it is the text the seeder gets: the variable is
        read as a character that stands for it, and the strings of the file are read all the same."""
        text = 'u: ${H:-a: b}\nv: "https:\\x2f\\x2fevil.com"\n'
        with self.assertRaises(yaml.YAMLError):
            yaml.safe_load(text.replace("${H:-a: b}", "a: b"))
        self.assertEqual(self.addresses(text), {"https://evil.com"})
        self.assertEqual(seedlint._decoded(Path("a.yaml"), text)[1], True)

    def test_a_default_in_json_that_is_no_document_yet_keeps_its_backslashes_as_the_seeder_gives_it(self):
        """A bare variable makes the seeder replace the text of the file, and a default goes into it escaped for JSON: its
        backslashes arrive as backslashes."""
        text = '{"n": ${N:-1}, "u": "${H:-https:\\u002f\\u002fevil.com}"}'
        folder = self.playground({"seed/a.json": text})
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(common.load(folder / "seed" / "a.json"), {"n": 1, "u": "https:\\u002f\\u002fevil.com"})
        self.assertEqual(self.addresses(text, "a.json"), set())
        self.assertEqual(seedlint.problems(folder), [])

    def test_a_default_that_is_a_variable_or_starts_the_host_is_no_address(self):
        for text in ('u: "https:\\x2f\\x2f${H:-${KEY}}/x"\n', 'u: "https:\\x2f\\x2f${H:-evil.com}/x"\n', 'u: "https:\\x2f\\x2f${H}/x"\n'):
            with self.subTest(text=text):
                self.assertEqual(self.addresses(text), set())

    def test_strings_the_lint_cannot_read_to_the_end_are_named_and_not_passed(self):
        """A variable where YAML wants a name makes the text of the lint's copy no document, while the seeder's text parses: the
        file is named, as the parse rule has nothing to say about it."""
        service = SERVICE + "      ANCH: a1\n      TAG: str\n      A: '2'\n"
        cases = {"an anchor": 'x: &${ANCH} 1\nu: "https:\\x2f\\x2fevil.com"\ny: *${ANCH}\n',
                 "an alias": 'x: &a1 1\ny: *${ANCH}\nu: "https:\\x2f\\x2fevil.com"\n',
                 "a tag": 'x: !!${TAG} 1\nu: "https:\\x2f\\x2fevil.com"\n',
                 "an escape that a variable completes": 'u: "https:\\x${A}f\\x${A}fevil.com"\n'}
        for number, (what, text) in enumerate(cases.items()):
            with self.subTest(what=what):
                folder = self.playground({"seed/a.yaml": text}, compose=service, name=f"p{number}")
                with mock.patch.dict(os.environ, {"ANCH": "a1", "TAG": "str", "A": "2"}, clear=True):
                    self.assertEqual(common.load(folder / "seed" / "a.yaml")["u"], "https://evil.com", "the seeder reads the file")
                self.assertEqual(seedlint.problems(folder), [Problem("read", str(folder / "seed/a.yaml"), 0, seedlint.UNREAD)])
                self.assertEqual(seedlint._decoded(Path("a.yaml"), text)[1], False)
        control = self.playground({"seed/a.yaml": 'x: &a1 1\nu: "https:\\x2f\\x2fevil.com"\ny: *a1\n'}, compose=service, name="control")
        self.assertEqual(self.found(control), [("address", "seed/a.yaml", 2)])
        self.assertIn("Fix:", seedlint.UNREAD)

    def test_a_file_that_is_named_for_another_reason_is_not_named_for_this_one_too(self):
        service = SERVICE + "      ANCH: a1\n"
        for what, text, checks in (("an unset variable", 'x: &${NOBODY} 1\nu: "https:\\x2f\\x2fevil.com"\n', ["variable"]),
                                   ("a file that does not parse", 'x: &${ANCH} [\nu: "https:\\x2f\\x2fevil.com"\n', ["parse"])):
            with self.subTest(what=what):
                folder = self.playground({"seed/a.yaml": text}, compose=service, name=what.split()[-1])
                self.assertEqual([p.check for p in seedlint.problems(folder)], checks)

    def test_a_json_text_inside_a_string_is_read_too(self):
        """The `sharedConfiguration` of an endpoint is a string that holds JSON, which the platform parses again: the escapes of
        that text hide an address as well."""
        inner = '{"target": "https:\\u002f\\u002fevil.com"}'
        cases = {"in JSON": ("a.json", json.dumps({"sharedConfiguration": inner})),
                 "in a single-quoted scalar": ("a.yaml", "sharedConfiguration: '" + inner + "'\n"),
                 "in a block scalar": ("a.yaml", "sharedConfiguration: |\n  " + inner + "\n"),
                 "in a list": ("a.json", json.dumps({"c": '[{"u": "https:\\u002f\\u002fevil.com"}]'})),
                 "as a key": ("a.json", json.dumps({"c": '{"https:\\u002f\\u002fevil.com": 1}'})),
                 "in a text in a text": ("a.json", json.dumps({"c": json.dumps({"d": inner})})),
                 "in a text in a text in a text": ("a.json", json.dumps({"c": json.dumps({"d": json.dumps({"e": inner})})}))}
        for number, (what, (file, text)) in enumerate(cases.items()):
            with self.subTest(what=what):
                self.assertEqual(self.addresses(text, file), {"https://evil.com"})
                self.assertEqual([(p.check, p.message[:17]) for p in seedlint.problems(self.playground({f"seed/{file}": text}, name=f"p{number}"))],
                                 [("address", "https://evil.com ")])
        text = "c: '" + inner.replace("evil.com", "docs.apelor.test") + "'\n"
        self.assertEqual(self.addresses(text), set(), "and the names that resolve nowhere are allowed there too")
        self.assertEqual(self.addresses("c: '{not json, \"https:\\u002f\\u002fevil.com\"'\n"), set(), "what is no JSON is a text like another")

    def test_json_texts_nested_deeper_than_the_lint_reads_are_named(self):
        """`nested(count, "x")` is a JSON file with `count - 1` JSON texts, one in the string of the other."""
        self.assertEqual(seedlint.NESTED, 3)
        self.assertEqual(seedlint.problems(self.playground({"seed/a.json": nested(4, "x")})), [], "three texts are read")
        folder = self.playground({"seed/a.json": nested(5, "x")}, name="deep")
        self.assertEqual(seedlint.problems(folder), [Problem("read", str(folder / "seed/a.json"), 0, seedlint.UNREAD)])

    def test_a_number_is_not_read_for_its_digits(self):
        """Only the strings of a document are read: a number of many digits costs the square of them on a Python without a limit."""
        text = "[" + "9" * 100_000 + "]"
        self.assertEqual(seedlint._parsed(text), [0])
        self.assertEqual(seedlint._parsed('{"a": 1.5, "b": ["x", 2]}'), {"a": 0, "b": ["x", 0]})
        self.assertEqual(self.addresses(f"c: '{text}'\n"), set())
        self.assertEqual(self.addresses(json.dumps({"c": "[1, 2.5, \"https:\\u002f\\u002fevil.com\"]"}), "a.json"), {"https://evil.com"})

    def test_the_first_rule_is_what_it_was_and_has_no_allowance(self):
        """The text as it is: a documentation URL written in the clear is still an address written out."""
        text = "u: https://docs.apelor.test/coa/x.pdf\n"
        self.assertEqual(seedlint.literal_addresses(text), {"https://docs.apelor.test"})
        self.assertEqual(self.addresses(text), set(), "and it is not told twice")
        self.assertEqual([p.check for p in seedlint.problems(self.playground({"seed/a.yaml": text}))], ["address"])
        self.assertEqual(seedlint.literal_addresses("u: http://x.example http://www.example.org\n"), {"http://x.example", "http://www.example.org"})

    def test_the_documentation_urls_of_the_apelor_files_stay_legal(self):
        """Five files of one playground write the URL of a document of a company that does not exist, with an escape for each
        slash so that the first rule leaves it alone. They have to stay legal when the strings are read."""
        document = ("openapi: 3.0.3\ninfo: {title: Batch release}\npaths:\n  /batches/{id}:\n    get:\n      responses:\n        '200':\n"
                    "          content:\n            application/json:\n              example:\n"
                    "                certificates:\n"
                    '                  - "https:\\u002f\\u002fdocs.apelor.test/coa/COA-26-2401-01.pdf"\n'
                    '                documentUrl: "https:\\u002f\\u002fdocs.apelor.test/coa/COA-26-2401-01.pdf"\n')
        files = {"seed/environments/novo/environment.yaml": "id: novo\nname: Novo\n",
                 "seed/environments/novo/portal/api-docs/batch-release.yaml": document}
        self.assertEqual(seedlint.problems(self.playground(files)), [])
        self.assertEqual(run(str(self.root))[0], 0)

    def test_a_variable_is_no_address_in_escapes_either(self):
        for host in ("${KEY}", "${APIM_URL}.example", "${NOBODY:-fallback}"):
            with self.subTest(host=host):
                self.assertEqual(self.addresses(f'u: "{escaped(host)}"\n'), set())
        self.assertEqual(self.found(self.playground({"seed/a.yaml": f'u: "{escaped("${NOBODY}")}"\n'})), [("variable", "seed/a.yaml", 1)])

    def test_a_variable_where_the_format_has_no_room_for_one_does_not_hide_the_file(self):
        """`[${KEY}]` is no YAML until the variable is replaced, a bare `${PORT}` is no JSON: the file is still read."""
        yaml_text = f'x: [${{KEY}}, "{escaped("evil.com")}"]\n'
        json_text = f'{{"port": ${{PORT}}, "u": "{escaped("evil.com")}", "v": "{escaped("${KEY}")}"}}'
        with self.assertRaises(Exception):
            yaml.safe_load(yaml_text)
        with self.assertRaises(ValueError):
            json.loads(json_text)
        compose = SERVICE + "      PORT: 8080\n"
        folder = self.playground({"seed/a.yaml": yaml_text, "seed/b.json": json_text}, compose=compose)
        self.assertEqual([(p.check, Path(p.path).name, p.line) for p in seedlint.problems(folder)],
                         [("address", "a.yaml", 1), ("address", "b.json", 1)])
        self.assertEqual(self.addresses(yaml_text), {"https://evil.com"})
        self.assertEqual(self.addresses(json_text, "a.json"), {"https://evil.com"})

    def test_a_character_of_the_file_is_not_taken_for_a_variable(self):
        """A variable is parsed as a character that the text does not use, and put back after: a text that uses the first one
        gets the next, and a host that starts with that character is a host."""
        private = chr(0xE000)
        text = f'x: [${{KEY}}, "{private}", "https:\\x2f\\x2f{private}evil.com"]\n'
        self.assertEqual(self.addresses(text), {f"https://{private}evil.com"})

    def test_an_address_in_the_clear_and_in_escapes_is_told_once(self):
        folder = self.playground({"seed/a.yaml": 'a: http://evil.com\nb: "http:\\x2f\\x2fevil.com"\nc: "http:\\x2f\\x2fother"\n'})
        found = seedlint.problems(folder)
        self.assertEqual([(p.line, p.message.split(" is written out")[0]) for p in found], [(1, "http://evil.com"), (3, "http://other")])
        self.assertNotIn("escapes", found[0].message)

    def test_each_address_is_told_at_its_first_use(self):
        text = 'a: "http:\\x2f\\x2fevil.com"\nb:\n  - "http:\\x2f\\x2fevil.com"\n  - "http:\\x2f\\x2fother"\n'
        self.assertEqual([p.line for p in seedlint.problems(self.playground({"seed/a.yaml": text}))], [1, 4])

    def test_twenty_are_told_and_the_others_counted_whichever_way_they_are_written(self):
        text = "".join(f"u{n}: http://h{n}\n" for n in range(10)) + "".join(f'e{n}: "http:\\x2f\\x2fe{n}"\n' for n in range(25))
        found = seedlint.problems(self.playground({"seed/a.yaml": text}))
        self.assertEqual(len(found), seedlint.MANY + 1)
        self.assertEqual([p.message for p in found if p.line == 0], ["15 more addresses are written out."])
        self.assertEqual(sum("in escapes" in p.message for p in found), 10)

    def test_only_a_file_that_has_a_backslash_is_read_again(self):
        """Nothing is written in escapes without one: the others cost nothing more than before."""
        files = {**GOOD, "seed/b.json": '{"u": "http://${KEY}", "n": 1}', "seed/c.yaml": "a: 'http://${KEY}'\nb: [1, 2]\n"}
        folder = self.playground(files)
        with mock.patch.object(seedlint, "_scalars", side_effect=AssertionError("read again")), \
                mock.patch.object(seedlint, "_masked", side_effect=AssertionError("read again")):
            self.assertEqual(seedlint.problems(folder), [])
            self.assertEqual(seedlint._decoded(Path("a.yaml"), "a: 1\n"), ([], True))
            self.assertEqual(seedlint._decoded(Path("a.json"), '{"a": 1}'), ([], True))

    def test_a_file_read_as_text_is_not_decoded(self):
        """A page, a policy or a note is handed to the seed as text, and a backslash in it is a backslash."""
        text = escaped("evil.com")
        folder = self.playground({"seed/aim/page.md": text, "seed/x.txt": text, "seed/p.cedar": text, "seed/q.gapl": text})
        with mock.patch.object(seedlint, "_scalars", side_effect=AssertionError("decoded")):
            self.assertEqual(seedlint.problems(folder), [])
        for name in ("a.md", "a.txt", "a.cedar", "a.gapl", "a"):
            self.assertEqual(self.addresses(text, name), set(), name)

    def test_a_plain_or_single_quoted_scalar_has_no_escapes_and_is_no_address(self):
        """`\\u002f` is four characters there, as the seed reads it: it makes no `://`."""
        self.assertEqual(self.addresses("a: https:\\u002f\\u002fevil.com\nb: 'https:\\u002f\\u002fevil.com'\n"), set())

    def test_what_does_not_parse_is_the_parse_rule_s_to_say_and_what_came_before_it_is_read(self):
        text = 'a: "http:\\x2f\\x2fevil.com"\n---\nb: [\n'
        self.assertEqual(self.addresses(text), {"http://evil.com"})
        self.assertEqual(self.addresses('x: [\n"http:\\x2f\\x2fevil.com"\n'), set())
        folder = self.playground({"seed/a.yaml": text})
        self.assertEqual(sorted(p.check for p in seedlint.problems(folder)), ["address", "parse"])

    def test_a_variable_is_replaced_by_the_stand_in_and_the_escaped_dollar_is_left(self):
        self.assertEqual(seedlint._masked("a ${A} b $${C} ${D:-x} ${E", "#"), "a # b $${C} # ${E")
        self.assertEqual(seedlint._masked("a ${A} b $${C} ${D:-x} ${E", "#", defaults=True), "a # b $${C} #x ${E", "a default stays")
        self.assertEqual(seedlint._masked("no variable", "#"), "no variable")

    def test_a_file_that_uses_every_character_of_the_private_use_area_is_still_read(self):
        """The stand-in is a character the text does not use; when there is none, the text is read as it is."""
        used = "".join(chr(code) for code in range(0xE000, 0xF900))
        text = f'x: "{used}"\ny: "https:\\x2f\\x2fevil.com"\nz: "https:\\x2f\\x2f${{KEY}}"\n'
        self.assertEqual(self.addresses(text), {"https://evil.com"})

    def test_a_document_nested_too_deep_for_the_parser_ends_the_list_and_not_the_run(self):
        text = "[" * 3000 + '"\\x41"' + "]" * 3000 + "\n"
        self.assertEqual(self.addresses(text), set())
        self.assertEqual(self.addresses(text.strip(), "a.json"), set())
        found = seedlint.problems(self.playground({"seed/a.yaml": text, "seed/b.json": text}))
        self.assertEqual(sorted(Path(p.path).name for p in found), ["a.yaml", "b.json"])

    def test_a_document_that_repeats_itself_through_aliases_is_read_once(self):
        levels = "".join(f"a{n}: &a{n} [{', '.join(f'*a{n - 1}' for _ in range(9))}]\n" for n in range(1, 13))
        text = 'a0: &a0 ["http:\\x2f\\x2fevil.com"]\n' + levels
        found, seconds = within(30, lambda: self.addresses(text))
        self.assertEqual(found, {"http://evil.com"})
        self.assertLess(seconds, WHOLE_SECONDS)

    def test_a_long_scalar_with_many_addresses_in_it_is_told_twenty_at_a_time(self):
        text = 'k: "' + "".join(f"http:\\x2f\\x2fh{n} " for n in range(5000)) + '"\n'
        found = seedlint.problems(self.playground({"seed/a.yaml": text}))
        self.assertEqual(len(found), seedlint.MANY + 1)
        self.assertEqual([p.message for p in found if p.line == 0], [f"{5000 - seedlint.MANY} more addresses are written out."])

    def test_the_path_says_which_files_are_read_so(self):
        text = 'u: "' + escaped("evil.com") + '"\n'
        self.assertEqual(self.addresses(text, "x/seed/a.yaml"), {"https://evil.com"})
        self.assertEqual(self.addresses(text, Path("x/seed/a.yml")), {"https://evil.com"})
        self.assertEqual(self.addresses(text, "a.YAML"), set(), "the seed reads the suffixes in lower case")


# --- The command ------------------------------------------------------------------------

class Command(Made):

    def bad(self, root=None):
        return self.playground({"seed/a.yaml": "x: ${NOBODY}\ny: http://bakery-mcp\n"}, root=root)

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
        self.assertTrue(lines[1].startswith(f"{self.root}/acme/seed/a.yaml:2: http://bakery-mcp is written out."), lines[1])
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
        self.assertTrue(lines[1].startswith(f"::error file={escaped},line=2,title=Seed address::http://bakery-mcp is written out."))
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

    def test_an_annotation_keeps_the_path_it_was_given_when_no_work_tree_holds_it(self):
        self.bad()
        out = run(str(self.root), "--format", "github")[1]
        self.assertTrue(out.startswith("::error file=" + seedlint.escape_property(f"{self.root}/acme/seed/a.yaml") + ",line=1,"), out[:100])

    def test_json_is_a_list_of_problems_with_sorted_keys_and_nothing_else(self):
        self.bad()
        code, out, _ = run(str(self.root), "--format", "json")
        self.assertEqual(code, 1)
        found = json.loads(out)
        self.assertEqual([sorted(p) for p in found], [["check", "line", "message", "path"]] * 2)
        self.assertEqual(out, json.dumps(found, indent=2, sort_keys=True) + "\n")
        self.playground(root=self.root / "good")
        self.assertEqual(run(str(self.root / "good"), "--format", "json"), (0, "[]\n", ""))

    def test_a_folder_that_does_not_exist_is_nothing_to_lint(self):
        for fmt in ("text", "github", "json"):
            with self.subTest(format=fmt):
                code, out, err = run(str(self.root / FOLDER), "--format", fmt)
                self.assertEqual((code, err), (0, ""))
                self.assertNotIn("::error", out)
        self.assertEqual(run(str(self.root / FOLDER))[1], f"No folder {self.root}/playgrounds: nothing to lint.\n")

    def test_a_catalog_of_add_ons_and_tests_has_nothing_to_lint(self):
        catalog = self.root / "catalog"
        (catalog / ADDONS / "hello").mkdir(parents=True)
        (catalog / ADDONS / "hello" / "compose.yml").write_text("services:\n  hello: {image: x}\n", encoding="utf-8")
        (catalog / "tests").mkdir()
        (catalog / "tests" / "test_hello.py").write_text("import unittest\n", encoding="utf-8")
        self.assertEqual(run(str(catalog / FOLDER), "--format", "github"),
                         (0, f"No folder {catalog}/playgrounds: nothing to lint.\n", ""))

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
                self.assertIn("/acme/seed/a.yaml,line=2,title=Seed address::", out)

    def test_a_folder_that_is_a_playground_and_holds_playgrounds_lints_both(self):
        outer = self.playground({"seed/a.yaml": "x: ${OUTER}\n"}, name="outer")
        self.bad(root=outer)
        report = seedlint.lint(outer)
        self.assertEqual((report.playgrounds, report.files), (2, 2))
        self.assertEqual([(p.check, Path(p.path).relative_to(outer).as_posix(), p.line) for p in report.problems],
                         [("variable", "acme/seed/a.yaml", 1), ("address", "acme/seed/a.yaml", 2), ("variable", "seed/a.yaml", 1)])

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
        # the variable is the maintainer's own: the folder it names is the one it resolves to
        self.assertEqual(run(variables={"LAB_PLAYGROUNDS": str(folder)})[0], 1)
        self.assertIn("NOBODY has no value", run(variables={"LAB_PLAYGROUNDS": str(folder)})[1])

    def test_options_and_folders_may_come_in_any_order(self):
        self.bad(root=self.root / "one")
        self.playground(root=self.root / "two")
        one, two = str(self.root / "one"), str(self.root / "two")
        first = run(one, "--format", "github", two)
        self.assertEqual(first[0], 1)
        self.assertEqual(first, run("--format", "github", one, two))
        self.assertEqual(first, run(one, two, "--format", "github"))
        self.assertEqual(len([line for line in first[1].splitlines() if line.startswith("::error")]), 2)

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

    def test_a_playground_may_be_given_itself(self):
        folder = self.bad()
        self.assertEqual(run(str(folder))[0], 1)
        self.assertEqual(run(str(self.playground(name="good")), "--format", "json"), (0, "[]\n", ""))

    def test_a_path_that_is_not_a_folder_is_a_wrong_command_line(self):
        (self.root / "file").write_text("x", encoding="utf-8")
        self.assertEqual(run(str(self.root / "file")), (2, "", f"{self.root}/file is not a folder\n"))

    def test_a_wrong_option_is_a_wrong_command_line(self):
        for argv in (["--format", "xml"], ["--nope"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
                seedlint.main(argv, variables={})
            self.assertEqual(stop.exception.code, 2)

    def test_several_folders_are_all_linted_and_the_worst_decides(self):
        self.bad(root=self.root / "one")
        self.playground(root=self.root / "two")
        code, out, _ = run(str(self.root / "one"), str(self.root / "two"), str(self.root / "three"))
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines()[-3:], [f"2 problems in 1 seed file of 1 playground ({self.root}/one)",
                                                  f"All good: 2 seed files of 1 playground ({self.root}/two)",
                                                  f"No folder {self.root}/three: nothing to lint."])

    def test_paths_under_the_folder_it_runs_in_are_printed_relative_to_it(self):
        self.bad()
        before = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, before)
        # os.getcwd() of a temporary folder can be a link (/var on a Mac), the path the command compares with is its own
        here = Path.cwd()
        code, out, _ = run(str(here / "acme"), "--format", "github")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("::error file=acme/seed/a.yaml,line=1,"), out[:80])

    def test_the_variable_names_the_folder_of_a_catalog_that_is_linted_instead_of_the_sample(self):
        self.bad()
        self.assertEqual(run(variables={})[0], 0, "no catalog: the sample of the engine")
        code, out, _ = run(variables={"LAB_PLAYGROUNDS": str(self.root)})
        self.assertEqual(code, 1)
        self.assertIn("acme/seed/a.yaml:1: NOBODY has no value", out)
        self.assertEqual(run(variables={"LAB_PLAYGROUNDS": ""})[0], 0, "empty is unset")

    def test_a_folder_on_the_command_line_wins_over_the_variable(self):
        self.bad()
        self.playground(root=self.root / "good")
        self.assertEqual(run(str(self.root / "good"), variables={"LAB_PLAYGROUNDS": str(self.root)})[0], 0)

    def test_without_a_folder_or_a_variable_it_lints_the_sample_of_the_engine(self):
        code, out, _ = run()
        self.assertEqual((code, out.splitlines()[0].split(" (")[0]), (0, "All good: 57 seed files of 2 playgrounds"))
        self.assertEqual(seedlint.playgrounds_root({}), seedlint.SAMPLES)
        self.assertEqual(seedlint.playgrounds_root({"OTHER": "x"}), seedlint.SAMPLES)

    def test_the_folder_a_variable_names_is_resolved_as_the_tests_resolve_it(self):
        for named, resolved in (("/catalog/" + FOLDER, Path("/catalog") / FOLDER), (FOLDER, Path(FOLDER).resolve()),
                                ("~/" + FOLDER, Path("~").expanduser() / FOLDER)):
            self.assertEqual(seedlint.playgrounds_root({"LAB_PLAYGROUNDS": named}), resolved.resolve(), named)

    def test_only_the_first_problems_are_printed_and_the_others_counted(self):
        text = "".join(f"v{n}: http://h{n}\n" for n in range(seedlint.MANY))
        for n in range(30):
            self.playground({f"seed/f{n}.yaml": text}, name=f"p{n}")
        code, out, _ = run(str(self.root))
        lines = out.splitlines()
        self.assertEqual(code, 1)
        self.assertEqual(len(lines), seedlint.SHOWN + 2)
        self.assertEqual(lines[-2], f"{600 - seedlint.SHOWN} more problems not shown.")

    def test_it_runs_as_a_script_with_the_folder_and_the_variable(self):
        self.bad()
        environment = {**os.environ, "LAB_PLAYGROUNDS": str(self.root)}
        by_argument = subprocess.run([sys.executable, str(SEEDER / "seedlint.py"), str(self.root), "--format", "github"],
                                     capture_output=True, text=True, cwd=str(self.root), check=False)
        by_variable = subprocess.run([sys.executable, str(SEEDER / "seedlint.py")], capture_output=True, text=True,
                                     cwd=str(self.root), env=environment, check=False)
        self.assertEqual(by_argument.returncode, 1, by_argument.stderr)
        self.assertEqual(by_variable.returncode, 1, by_variable.stderr)
        self.assertEqual(len([line for line in by_argument.stdout.splitlines() if line.startswith("::error file=")]), 2)
        self.assertIn("a.yaml:1: NOBODY has no value", by_variable.stdout)
        self.assertEqual(subprocess.run([sys.executable, str(SEEDER / "seedlint.py"), str(self.root / "none")], capture_output=True,
                                        text=True, check=False).returncode, 0)


# --- The annotations --------------------------------------------------------------------

class Annotations(unittest.TestCase):
    """Escaped as report.py of the Lab UI escapes its own: a name in a stranger's tree cannot start a command."""

    def test_percent_carriage_return_and_line_feed_are_escaped_in_a_message(self):
        self.assertEqual(seedlint.escape_data("evil\n::set-env name=A::b 100%"), "evil%0A::set-env name=A::b 100%25")
        self.assertEqual(seedlint.escape_data("a\r\nb"), "a%0D%0Ab")

    def test_a_property_also_escapes_the_colon_and_the_comma(self):
        self.assertEqual(seedlint.escape_property("x/y/a:b,c.yml"), "x/y/a%3Ab%2Cc.yml")

    def test_the_escape_goes_first_so_that_it_is_not_escaped_twice_or_undone(self):
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

    def test_every_kind_of_problem_has_a_title(self):
        self.assertEqual(sorted(seedlint.TITLES), ["address", "environment", "parse", "playground", "read", "variable"])

    def test_a_name_that_is_not_text_cannot_stop_the_command(self):
        """A name with bytes that are no UTF-8 reaches Python as lone surrogates, which stdout cannot write."""
        report = Report([Problem("variable", "seed/a\udcffb.yaml", 1, "m\ud800")], 1, 1)
        for fmt in ("text", "github", "json"):
            with self.subTest(format=fmt), mock.patch.object(seedlint, "lint", return_value=report):
                out = io.StringIO()
                with tempfile.TemporaryDirectory() as folder:
                    self.assertEqual(seedlint.main([folder, "--format", fmt], variables={}, out=out), 1)
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
        self.assertEqual(seedlint.seed_paths(folder), [folder / "seed" / "a.yaml"])

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

    def test_a_playground_whose_compose_file_cannot_be_read_stops_the_test_that_asks_for_it_and_counts_no_file(self):
        outside = self.root / "outside"
        self.playground(root=outside, name="p")
        folder = self.root / "catalog" / "one"
        (folder / "seed").mkdir(parents=True)
        self.link(folder / "compose.yml", outside / "p" / "compose.yml")
        with self.assertRaisesRegex(common.SeedError, "compose.yml: is a link"):
            list(seedlint.seeded_playgrounds(self.root / "catalog"))
        self.assertEqual(seedlint.lint(self.root / "catalog").files, 0)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "this machine has no named pipes")
    def test_a_special_file_with_the_name_of_a_seed_file_is_named_and_does_not_block_the_lint(self):
        folder = self.playground({"seed/a.yaml": "a: 1\n"})
        os.mkfifo(folder / "seed" / "pipe.yaml")
        found, seconds = within(10, lambda: seedlint.problems(folder))
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
        files = {**GOOD, "seed/bad.yaml": "a: [\n", "seed/environments/novo/environment.yaml": "id: novo\nname: ${NOBODY}\n"}
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
        env = self.root / "acme" / "seed" / "environments" / "novo"
        files = {"seed/environments/novo/environment.yaml": "id: novo\nname: Novo\n",
                 "seed/environments/novo/portal/pages/up.yaml": "hrid: up\nfile: ../../../../../../outside.svg\n",
                 "seed/environments/novo/portal/pages/abs.yaml": f"hrid: abs\nfile: {outside}\n",
                 "seed/environments/novo/portal/pages/in.yaml": "hrid: in\nfile: ../content/in.md\n",
                 "seed/environments/novo/portal/content/in.md": "# in\n",
                 "seed/environments/novo/portal/theme.yaml": "hrid: theme\nassets: {logo: ../../../../../../outside.svg, home: ../assets/home.svg}\n",
                 "seed/environments/novo/assets/home.svg": "<svg/>"}
        folder = self.playground(files)
        wanted = ["portal/pages/abs.yaml: file " + str(outside) + " is outside the folder",
                  "portal/pages/up.yaml: file ../../../../../../outside.svg is outside the folder",
                  "portal/theme.yaml: assets.logo ../../../../../../outside.svg is outside the folder"]
        self.assertEqual(seedlint.environment_problems(env, {}), wanted)
        self.assertEqual([(p.check, p.message) for p in seedlint.problems(folder)],
                         [("environment", text.partition(": ")[2]) for text in wanted])

    def test_a_link_for_an_asset_or_a_file_of_an_environment_is_not_followed(self):
        outside = self.root / "outside.svg"
        outside.write_text("x" * (seedlint.SMALL + 1), encoding="utf-8")
        files = {"seed/environments/novo/environment.yaml": "id: novo\nname: Novo\n",
                 "seed/environments/novo/portal/theme.yaml": "hrid: theme\nassets: {logo: ../assets/logo.svg}\n"}
        folder = self.playground(files)
        env = folder / "seed" / "environments" / "novo"
        (env / "assets").mkdir()
        self.link(env / "assets" / "logo.svg", outside)
        self.assertEqual(seedlint.environment_problems(env, {}), ["portal/theme.yaml: assets.logo ../assets/logo.svg does not exist"])
        os.unlink(env / "portal" / "theme.yaml")
        self.link(env / "portal" / "theme.yaml", outside)
        self.assertEqual(seedlint.environment_problems(env, {}), ["portal/theme.yaml: is a link. Fix: a seed holds files, not links: copy the file"])

    def test_a_document_that_repeats_itself_through_aliases_is_not_written_out_in_a_message(self):
        levels = "".join(f"a{n}: &a{n} [{', '.join(f'*a{n - 1}' for _ in range(9))}]\n" for n in range(1, 13))
        text = "a0: &a0 [x]\n" + levels + "users:\n  - {big: *a12}\n  - {sourceId: *a12, source: memory}\n"
        files = {"seed/environments/novo/environment.yaml": "id: novo\nname: Novo\n", "seed/environments/novo/users.yaml": text}
        found, seconds = within(30, lambda: seedlint.problems(self.playground(files)))
        self.assertEqual([p.check for p in found], ["environment", "environment"])
        self.assertTrue(found[0].message.startswith("cannot be checked: TypeError: "), found[0].message)
        self.assertIn("unhashable type: 'list'", found[0].message, "Python words it as it likes")
        self.assertEqual(found[1].message, "a user needs a source and a sourceId: ...", "and the finding made before it is kept")
        self.assertLess(seconds, WHOLE_SECONDS)

    def test_a_document_of_the_wrong_shape_is_a_finding_and_not_a_crash(self):
        for text in ("- a\n- b\n", "just text\n", "users: {a: 1}\n", "users: [1, 2]\n"):
            with self.subTest(text=text):
                files = {"seed/environments/novo/environment.yaml": "id: novo\nname: Novo\n", "seed/environments/novo/users.yaml": text}
                found = seedlint.problems(self.playground(files, name=f"p{len(text)}{text[:2].strip('-: ')}"))
                self.assertTrue(all(p.check == "environment" for p in found), found)

    def test_a_seed_service_that_is_not_what_compose_says_is_no_seed_service(self):
        composes = ("", "- a\n", "services: [a]\n", "services: {seed: just text}\n", "services: {seed: {labels: [1, {a: b}]}}\n",
                    "services: {seed: {labels: x}}\n")
        for number, compose in enumerate(composes):
            with self.subTest(compose=compose):
                self.assertEqual(seedlint.problems(self.playground(compose=compose, name=f"p{number}")), [])

    def test_a_value_of_the_seed_service_that_is_a_document_is_not_expanded(self):
        levels = "".join(f"a{n}: &a{n} [{', '.join(f'*a{n - 1}' for _ in range(9))}]\n" for n in range(1, 13))
        compose = ("x-bomb:\n  a0: &a0 [x]\n" + "".join("  " + line for line in levels.splitlines(True)) +
                   "services:\n  seed:\n    labels: {io.gravitee.role: seed}\n    environment: {BOMB: *a12, N: 5}\n")
        environment, seconds = within(30, lambda: dict(seedlint.seeded_playgrounds(self.playground(compose=compose).parent)))
        self.assertEqual(environment[self.root / "acme"]["N"], "5")
        self.assertLess(len(environment[self.root / "acme"]["BOMB"]), 10)

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
            self.assertEqual(seedlint.problems(folder), [Problem("read", str(folder / "seed/aim/skills/x/SKILL.md"), 0, seedlint.MERGES)])

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
    # What the scan of the strings has to read: a file that has a backslash is composed again
    "escapes": 'k: "' + "\\x41" * 250_000 + '"',
    "backslashes": 'k: "' + "\\\\" * 500_000 + '"',
    "backslashes in a plain scalar": "k: " + "\\" * ONE_MEGABYTE,
    "unicode escapes": 'k: "' + "\\u002f" * 160_000 + '"',
    "escaped addresses": 'k: "' + "".join(f"http:\\x2f\\x2fh{n};" for n in range(50_000)) + '"',
    "one escaped host": 'k: "http:\\x2f\\x2f' + "a" * ONE_MEGABYTE + '"',
    "variables and a backslash": "\\" + "${KEY}" * 160_000,
    # A string that is JSON text is parsed again: its escapes, its depth and its size
    "a json text with escapes": "k: '{\"a\": \"" + "".join(f"http:\\u002f\\u002fh{n};" for n in range(30_000)) + "\"}'",
    "json texts inside one another": "k: '" + nested(5, "".join(f"http:\\u002f\\u002fh{n};" for n in range(8_000))) + "'",
    "a json text that is one string of a megabyte": "k: '{\"a\": \"" + "\\u002f" * 150_000 + "\"}'",
    # What the lint reads again when the first reading fails: a variable where YAML wants a name
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
    "merge keys that a variable brings": ("a.yaml", "a: ${BOMB}\n", {"BOMB": flowing(22)}),
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
        rules[name] = {"unset_variables": seconds(lambda: seedlint.unset_variables(text, {"KEY": "k"})),
                       "literal_addresses": seconds(lambda: seedlint.literal_addresses(text)),
                       "compose_value": seconds(lambda: seedlint.compose_value(text)),
                       "_slow": seconds(lambda: seedlint._slow("a.yaml", text)),
                       "_slow of JSON": seconds(lambda: seedlint._slow("a.json", as_json)),
                       "escaped_addresses": seconds(lambda: seedlint.escaped_addresses("a.yaml", text)),
                       "escaped_addresses of JSON": seconds(lambda: seedlint.escaped_addresses("a.json", as_json))}
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
            playground = make_playground(Path(folder), {"seed/environments/novo/environment.yaml": NOT_READ[name][1]}, f"e{number}")
            started = time.process_time()
            found[name + " in an environment"] = seedlint.environment_problems(playground / "seed/environments/novo", {})
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


class Backstop(unittest.TestCase):
    """The process of the hostile cases is stopped by the CPU it uses, which a loaded machine does not change."""

    def resource(self):
        try:
            import resource
        except ImportError:
            self.skipTest("this system has no resource module")
        return resource

    def test_the_child_may_use_so_much_cpu_and_the_memory_it_can_be_limited_in(self):
        resource = self.resource()
        limits = []
        with mock.patch.object(resource, "setrlimit", lambda kind, pair: limits.append((kind, pair))):
            limited()
        wanted = [(resource.RLIMIT_CPU, (CPU_LIMIT, CPU_LIMIT + 10)), (resource.RLIMIT_AS, (8 << 30, 8 << 30))]
        self.assertEqual(limits, wanted)

    def test_a_limit_the_system_refuses_does_not_keep_the_other_from_being_set(self):
        resource = self.resource()
        limits = []

        def refusing(kind, pair):
            if kind == resource.RLIMIT_AS:
                raise ValueError("current limit exceeds maximum limit")
            limits.append(kind)

        with mock.patch.object(resource, "setrlimit", refusing):
            limited()
        self.assertEqual(limits, [resource.RLIMIT_CPU])

    def test_the_limit_really_stops_a_child_that_burns_cpu(self):
        if not hasattr(signal, "SIGXCPU"):
            self.skipTest("this system has no SIGXCPU")
        code = ("import sys; sys.path.insert(0, '.'); from tests import test_seedlint as t; t.CPU_LIMIT = 1; t.limited()\n"
                "while True: pass")
        done = subprocess.run([sys.executable, "-c", code], cwd=str(SEEDER), capture_output=True, text=True, timeout=300, check=False)
        self.assertEqual(done.returncode, -signal.SIGXCPU, done.stderr[-500:])

    def test_a_child_that_the_cpu_limit_stopped_says_so_and_does_not_blame_the_clock(self):
        if not hasattr(signal, "SIGXCPU"):
            self.skipTest("this system has no SIGXCPU")
        stopped = subprocess.CompletedProcess([], -signal.SIGXCPU, "", "")
        with mock.patch.dict(PROBED, clear=True), mock.patch.object(subprocess, "run", return_value=stopped):
            with self.assertRaisesRegex(AssertionError, f"more than {CPU_LIMIT} s of CPU"):
                probed()

    def test_the_clock_is_a_backstop_a_loaded_machine_does_not_reach(self):
        with mock.patch.dict(PROBED, clear=True), mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired([], 1)) as run:
            with self.assertRaisesRegex(AssertionError, "did not end"):
                probed()
        self.assertGreaterEqual(run.call_args.kwargs["timeout"], 900, "several times what the cases need on a loaded machine")
        self.assertGreater(CLOCK_LIMIT, 3 * CPU_LIMIT)

    def test_a_child_that_failed_for_another_reason_says_what_it_printed(self):
        failed = subprocess.CompletedProcess([], 1, "", "Traceback ... ImportError")
        with mock.patch.dict(PROBED, clear=True), mock.patch.object(subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(AssertionError, "ImportError"):
                probed()


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
                self.assertEqual(found[case], [["read", seedlint.MERGES]])

    def test_the_compose_file_and_the_files_of_an_environment_are_not_loaded_either(self):
        found = probed()["found"]
        for case in COMPOSES:
            with self.subTest(case=case):
                self.assertEqual(found[case], [["playground", seedlint.MERGES]])
        for case in IN_AN_ENVIRONMENT:
            with self.subTest(case=case):
                self.assertEqual(found[case + " in an environment"][0], "environment.yaml: " + seedlint.MERGES)

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
                self.assertEqual(seedlint.unset_variables(text, environment, ("ENV_ID",)),
                                 before(text, environment, ("ENV_ID",)), repr(text))

    def test_a_value_is_what_compose_gives_it(self):
        reference = re.compile(r"\$(?:\$|\{[A-Za-z_]\w*(?::?-([^}]*))?\})")

        def before(value):
            return reference.sub(lambda m: "$" if m.group(0) == "$$" else (m.group(1) or ""), value)

        for text in self.texts(20_000) + ["${A-b}", "${A:-b}$$", "$${A:-b}", "$$", "$", "${A:-b$$}", "}$$"]:
            self.assertEqual(seedlint.compose_value(text), before(text), repr(text))

    def test_the_engine_names_come_from_the_seeder(self):
        self.assertIs(seedlint.provided_to(Path("/p/seed/environments/x/a.yaml"), Path("/p")), common.PROVIDED)

    def test_the_cost_of_a_text_is_what_its_unclosed_defaults_make_the_seeder_read(self):
        self.assertEqual(seedlint._cost("${A:-x} ${B:-"), len(" ${B:-"), "one default, read to the end of the text after the last brace")
        self.assertEqual(seedlint._cost("${A:-x}"), 0)
        self.assertEqual(seedlint._cost("x" * 100), 0)
        self.assertEqual(seedlint._cost("${A:-" * 3), 3 * 15)


# --- The module in the engine -----------------------------------------------------------

class InTheEngine(unittest.TestCase):

    def tree(self):
        return ast.parse((SEEDER / "lab_seeder" / "seedlint.py").read_text(encoding="utf-8"))

    def test_it_reads_no_setting_of_the_seed(self):
        """The contract of the engine (lab-ui/tests/test_contract.py, scan_seed) lists the settings that the seeder's modules take
        from the environment and the folders they read from the seed. This module is a tool, and takes its environment as a
        parameter: none of the reads the contract looks for is in it."""
        def named(node):
            return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None

        reads = []
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.Call):
                if named(node.func) in ("setting", "getenv", "files", "seed_file", "has_seed"):
                    reads.append(named(node.func))
                if isinstance(node.func, ast.Attribute) and node.func.attr == "get" and named(node.func.value) == "environ":
                    reads.append("environ.get")
            elif isinstance(node, ast.Subscript) and named(node.value) == "environ":
                reads.append("environ[]")
            if named(node) in ("SEED_DIR", "FOLDERS"):
                reads.append(named(node))
        self.assertEqual(reads, [])

    def test_it_imports_the_standard_library_and_what_the_seeder_installs_and_nothing_else(self):
        names = {alias.name.split(".")[0] for node in ast.walk(self.tree()) if isinstance(node, ast.Import) for alias in node.names}
        names |= {node.module.split(".")[0] for node in ast.walk(self.tree()) if isinstance(node, ast.ImportFrom) and node.module}
        names |= {alias.name for node in ast.walk(self.tree()) if isinstance(node, ast.ImportFrom) and node.level and not node.module
                  for alias in node.names}   # `from . import common`: a sibling of the package
        self.assertEqual(names, {"argparse", "collections", "contextlib", "json", "os", "pathlib", "re", "stat", "sys", "yaml", "common"})
        requirements = (SEEDER / "requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(requirements, r"(?m)^pyyaml==")

    def test_the_image_leaves_it_out_as_it_leaves_out_the_testkit(self):
        """The seeder's Dockerfile copies the package that seeder/.dockerignore does not leave out. This module is a tool of tests and of
        a catalog's CI, run from the checkout of the Lab, as the testkit is: both are listed, with the files that stand for them at
        the root."""
        listed = [line.strip() for line in (SEEDER / ".dockerignore").read_text(encoding="utf-8").splitlines()]
        dockerfile = (SEEDER / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY lab_seeder lab_seeder", dockerfile)
        self.assertIn("COPY seed.py ./", dockerfile)
        for tool in ("testkit.py", "seedlint.py"):
            self.assertIn(f"lab_seeder/{tool}", listed)

    def test_no_module_of_the_image_imports_what_the_image_leaves_out(self):
        """The loader the seed runs is common.py, and the guard of YAML lives there: a module that is in the image cannot import a
        tool that is not."""
        listed = {line.strip() for line in (SEEDER / ".dockerignore").read_text(encoding="utf-8").splitlines()}
        tools = {name.rsplit("/", 1)[-1][:-3] for name in listed if name.startswith("lab_seeder/") and name.endswith(".py")}
        self.assertEqual(tools, {"testkit", "seedlint"})
        for path in sorted((SEEDER / "lab_seeder").glob("*.py")):
            if f"lab_seeder/{path.name}" in listed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
            names |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
            with self.subTest(module=path.name):
                self.assertEqual(names & tools, set())

    def test_the_count_of_merged_pairs_is_the_loaders_and_the_lint_holds_no_second_one(self):
        self.assertIs(seedlint.COPIED, common.COPIED)
        self.assertIs(seedlint.MERGES, common.MERGES)
        defined = {node.name for node in ast.walk(self.tree()) if isinstance(node, ast.FunctionDef)}
        self.assertFalse(defined & {"_copied", "_merging", "merged_pairs", "parse_yaml", "parse_yaml_all"}, "the count and the guard are common.py's")

    def test_the_playgrounds_the_tests_read_have_nothing_to_lint_through_the_command(self):
        """What a catalog's CI runs on its playgrounds is what the engine's tests run on theirs: LAB_PLAYGROUNDS, else the
        sample."""
        root = seedlint.playgrounds_root(os.environ)
        report = seedlint.lint(root)
        self.assertEqual(report.problems, [])
        self.assertGreater(report.playgrounds, 0, f"no playground of {root} has a seed")
        self.assertGreater(report.files, 0)

    def test_the_sample_is_read_whole(self):
        report = seedlint.lint(seedlint.SAMPLES)
        self.assertEqual((report.problems, report.playgrounds, report.files), ([], 2, 57))


if __name__ == "__main__":
    unittest.main()
