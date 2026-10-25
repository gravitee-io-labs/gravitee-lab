"""The two parsers that read compose files as text: the include list and the variables with their comments. Then
what `raw` and `ps` make of Docker's answers, the project's name while the core file does not load, where a script
runs, and Docker's memory and the Lab UI's scratch folder. And what every command runs under: the environment it gets
(no remote include, the core alone while the layout is waiting), the time it has and what happens to what it started
when that is up; and what a file written by someone else can make Compose do, tried with Compose itself where it is
installed."""

import json
import os
import random
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.docker import compose

from .. import Case
from ..fixtures import have_compose

# What the Docker CLI prints when the daemon does not answer
DOCKER_CLI_SAYS = ("failed to connect to the docker API at unix:///var/run/docker.sock; check if the path is correct "
                   "and if the daemon is running: dial unix /var/run/docker.sock: connect: no such file or directory")

LAB_UI = Path(compose.__file__).resolve().parents[2]   # the folder that holds the lab_ui package
LAYOUT = "lab_ui.library.layout"
GONE = "addons/gone/compose.yml"   # an include that this machine does not have
FOREVER = 600   # seconds a command that must be ended would run for


COMPOSE_5 = have_compose()


def alive(pid):
    """Whether a process runs. One that was killed and is not collected yet does not."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return not state.startswith("Z")


def settled(done, seconds=15):
    """Whether `done()` comes true within a few seconds: the system takes a moment to end a process, longer when it
    holds gigabytes."""
    end = time.monotonic() + seconds
    while not done() and time.monotonic() < end:
        time.sleep(0.05)
    return done()


def holding(marker):
    """The ids of the processes that run with `marker` in their command line, or None where ps cannot list them."""
    listing = subprocess.run(["ps", "-axo", "pid=,stat=,command="], capture_output=True, text=True)
    if listing.returncode != 0:
        return None
    found = []
    for line in listing.stdout.splitlines():
        pid, _, rest = line.strip().partition(" ")
        state, _, command = rest.strip().partition(" ")
        if marker in command and not state.startswith("Z") and int(pid) != os.getpid():
            found.append(int(pid))
    return found


class ProjectFolder(Case):
    """A temporary project root: files are written relative to it, the way the parsers are given paths."""

    def setUp(self):
        self.folder(compose)


class Includes(ProjectFolder):

    def test_paths_are_relative_to_the_including_file(self):
        path = self.write("setups/local/mine.yml", "x-gravitee:\n  kind: setup\n\ninclude:\n"
                          "  - ../../addons/kafka/compose.yml\n  - ../../addons/jade/compose.yml\n")
        self.assertEqual(compose.includes(path), ["addons/kafka/compose.yml", "addons/jade/compose.yml"])

    def test_what_belongs_to_this_machine_is_one_folder_deeper(self):
        addon = self.write("addons/local/whoami/compose.yml", "include:\n  - ../../kafka/compose.yml\n"
                           "  - ../other/compose.yml\n")
        self.assertEqual(compose.includes(addon), ["addons/kafka/compose.yml", "addons/local/other/compose.yml"])
        playground = self.write("playgrounds/local/sandbox/compose.yml", "include:\n"
                                "  - ../../../setups/local/mine.yml\n  - ../../../addons/local/whoami/compose.yml\n")
        self.assertEqual(compose.includes(playground), ["setups/local/mine.yml", "addons/local/whoami/compose.yml"])

    def test_quotes_comments_and_long_form(self):
        path = self.write(".lab/on.yml", "include:\n"
                          "  # the playground first\n"
                          "  - \"playgrounds/welcome/compose.yml\"   # one at a time\n"
                          "  - 'addons/my addon/compose.yml'\n"
                          "\n"
                          "  - path: addons/kafka/compose.yml\n")
        self.assertEqual(compose.includes(path), ["playgrounds/welcome/compose.yml", "addons/my addon/compose.yml",
                                                  "addons/kafka/compose.yml"])

    def test_items_without_indentation(self):
        path = self.write(".lab/on.yml",
                          "include:\n- addons/kafka/compose.yml\n- addons/mail/compose.yml\n")
        self.assertEqual(compose.includes(path), ["addons/kafka/compose.yml", "addons/mail/compose.yml"])

    def test_the_files_a_key_of_an_item_lists_are_not_items(self):
        path = self.write("playgrounds/mine/compose.yml", "include:\n"
                          "  - path: ../../addons/tool/compose.yml\n"
                          "    env_file:\n"
                          "      - ../../addons/tool/.env\n"
                          "      - path: ../../addons/tool/other.env\n"
                          "        required: false\n"
                          "  - ../../addons/kafka/compose.yml\n"
                          "services:\n  mine:\n    image: x\n")
        self.assertEqual(compose.includes(path), ["addons/tool/compose.yml", "addons/kafka/compose.yml"])
        path = self.write(".lab/on.yml", "include:\n- path: addons/tool/compose.yml\n  env_file:\n  - addons/tool/.env\n"
                          "- addons/kafka/compose.yml\n")
        self.assertEqual(compose.includes(path), ["addons/tool/compose.yml", "addons/kafka/compose.yml"])

    def test_the_files_of_a_path_list_are_items(self):
        path = self.write("playgrounds/mine/compose.yml", "include:\n"
                          "  - path:   # two files, merged\n"
                          "      - ../../addons/a/compose.yml\n"
                          "      - ../../addons/b/compose.yml\n"
                          "    env_file:\n"
                          "      - ../../addons/a/.env\n"
                          "  - ../../addons/c/compose.yml\n")
        self.assertEqual(compose.includes(path), ["addons/a/compose.yml", "addons/b/compose.yml", "addons/c/compose.yml"])

    def test_only_the_include_list(self):
        path = self.write("addons/kafka-desk/compose.yml", "include:\n  - ../kafka/compose.yml\n\n"
                          "services:\n  kafka-desk:\n    volumes:\n      - ./certs:/certs\n"
                          "    networks:\n      - internal\n")
        self.assertEqual(compose.includes(path), ["addons/kafka/compose.yml"])

    def test_a_file_outside_the_project_is_left_out(self):
        path = self.write(".lab/on.yml",
                          "include:\n  - ../elsewhere/compose.yml\n  - addons/kafka/compose.yml\n")
        self.assertEqual(compose.includes(path), ["addons/kafka/compose.yml"])

    def test_no_file_no_include(self):
        self.assertEqual(compose.includes(".lab/on.yml"), [])
        path = self.write("addons/mail/compose.yml", "services:\n  smtp4dev:\n    image: x\n")
        self.assertEqual(compose.includes(path), [])


class References(Case):

    def test_forms(self):
        self.assertEqual(compose.references("${A} ${B:-1} ${C-} $D ${E:?needed} ${F:+set}"),
                         [("A", None, True), ("B", "1", False), ("C", "", False), ("D", None, True),
                          ("E", None, True), ("F", None, False)])

    def test_nested_defaults_follow_their_variable(self):
        self.assertEqual(compose.references("${GW_VERSION:-${VERSION:-4.12.20}-debian}"),
                         [("GW_VERSION", "${VERSION:-4.12.20}-debian", False), ("VERSION", "4.12.20", False)])

    def test_a_doubled_dollar_is_not_a_reference(self):
        self.assertEqual(compose.references("$${gravitee.home}/plugins $$HOME ${A}"), [("A", None, True)])
        self.assertEqual(compose.references("costs 5$"), [])


class Interpolated(Case):
    """A text with its variables replaced the way Compose does, from what a lookup knows."""

    @staticmethod
    def text(text, **values):
        return compose.interpolated(text, values.get)

    def test_a_variable_is_replaced_by_its_value_or_its_default(self):
        self.assertEqual(self.text("${A} ${B:-x} ${C-y}", A="1", B="2", C="3"), "1 2 3")
        self.assertEqual(self.text("${B:-x} ${C-y}"), "x y")

    def test_an_empty_value_is_no_value_for_a_colon_default_only(self):
        self.assertEqual(self.text("[${B:-x}] [${C-y}] [${D}]", B="", C="", D=""), "[x] [] []")

    def test_a_default_may_refer_to_variables(self):
        self.assertEqual(self.text("${A:-${B:-${C:-end}}}"), "end")
        self.assertEqual(self.text("${A:-${B:-${C:-end}}}", B="mid"), "mid")
        self.assertEqual(self.text("${A:-pre-${B:-1}-post}"), "pre-1-post")

    def test_a_variable_set_nowhere_stays_as_written(self):
        self.assertEqual(self.text("${A} and ${B}", B="b"), "${A} and b")

    def test_other_forms_stay_as_written(self):
        self.assertEqual(self.text("${A:?needed} ${B:+set} ${C}x $D ${E", A="1", B="2", C="3", E="5"),
                         "${A:?needed} ${B:+set} 3x $D ${E")

    def test_a_doubled_dollar_is_a_dollar_sign(self):
        self.assertEqual(self.text("$${A} costs 5$$ $$"), "${A} costs 5$ $")
        self.assertEqual(self.text("pass$${A:-x}word"), "pass${A:-x}word")

    def test_a_text_without_variable_is_left_alone(self):
        self.assertEqual(self.text("HelloWorld@123"), "HelloWorld@123")
        self.assertEqual(self.text(""), "")


class Variables(ProjectFolder):

    FILE = """\
x-shared: &shared
  token: bearer ${TOKEN}   # Personal access token, used by every agent

# What runs
services:
  agent:
    image: ${REGISTRY:-graviteeio}/agent:${VERSION:-${CORE_VERSION:-4.12.20}-debian}
    command: run --password ${PASSWORD:-demo}
    environment:
      # Apigee X, and its integration
      type: apigee
      integration: ${INTEGRATION_ID}
      project: ${PROJECT_ID}

      region: ${REGION}
      home: $${gravitee.home}/plugins
      # a variable named in a comment: ${OLD}
      again: ${PASSWORD}
      secret: ${SECRET:-}   # Signs the tokens
"""

    def variables(self):
        return {v["key"]: v for v in compose.variables(self.write("addons/agent/compose.yml", self.FILE))}

    def test_order_of_first_appearance(self):
        self.assertEqual(list(self.variables()), ["TOKEN", "REGISTRY", "VERSION", "CORE_VERSION", "PASSWORD",
                                                  "INTEGRATION_ID", "PROJECT_ID", "REGION", "SECRET"])

    def test_comment_at_the_end_of_the_line(self):
        found = self.variables()
        self.assertEqual(found["TOKEN"]["comment"], "Personal access token, used by every agent")
        self.assertEqual(found["SECRET"]["comment"], "Signs the tokens")

    def test_comment_heading_the_block(self):
        found = self.variables()
        self.assertEqual(found["INTEGRATION_ID"]["comment"], "Apigee X, and its integration")
        self.assertEqual(found["PROJECT_ID"]["comment"], "Apigee X, and its integration")
        self.assertEqual(found["REGION"]["comment"], "")   # a blank line ends the block
        self.assertEqual(found["REGISTRY"]["comment"], "")   # a comment heading a block further out is not its own

    def test_defaults(self):
        found = self.variables()
        self.assertEqual(found["REGISTRY"]["default"], "graviteeio")
        self.assertEqual(found["VERSION"]["default"], "${CORE_VERSION:-4.12.20}-debian")
        self.assertEqual(found["CORE_VERSION"]["default"], "4.12.20")
        self.assertEqual(found["SECRET"]["default"], "")
        self.assertIsNone(found["TOKEN"]["default"])

    def test_required_when_a_reference_has_no_default(self):
        found = self.variables()
        self.assertTrue(found["TOKEN"]["required"])
        self.assertFalse(found["REGISTRY"]["required"])
        self.assertFalse(found["SECRET"]["required"])
        self.assertTrue(found["PASSWORD"]["required"])   # ${PASSWORD:-demo} then ${PASSWORD}
        self.assertEqual(found["PASSWORD"]["default"], "demo")

    def test_doubled_dollar_and_comment_text_are_ignored(self):
        found = self.variables()
        self.assertNotIn("gravitee", found)
        self.assertNotIn("OLD", found)


class Raw(ProjectFolder):

    def test_labels_environment_and_ports_come_in_one_shape(self):
        # Compose leaves the list form of labels and environment, and a port that holds a variable, as written
        written = {"name": "lab", "services": {
            "listed": {"labels": ["io.gravitee.group=Databases", "io.gravitee.name=Mongo=DB"],
                       "environment": ["A=1", "FROM_THE_SHELL"],
                       "ports": ["${UI_PORT:-8085}:8080", "127.0.0.1:${P:-1}:9000/udp", "${ONLY_TARGET:-70}",
                                 "[::1]:${Q:-2}:81", "${BIND:-127.0.0.1}:${R:-3}:82", "0.0.0.0:83:83",
                                 {"target": 80, "published": "8080"}]},
            "mapped": {"labels": {"io.gravitee.group": "Databases"}, "environment": {"A": "1"}},
            "bare": {"image": "busybox"}}}
        with mock.patch.object(compose, "_config", return_value=written):
            services = compose.raw("docker-compose.yml")["services"]
        self.assertEqual(services["listed"], {
            "labels": {"io.gravitee.group": "Databases", "io.gravitee.name": "Mongo=DB"},
            "environment": {"A": "1", "FROM_THE_SHELL": ""},
            "ports": [{"target": 8080, "published": "${UI_PORT:-8085}"},
                      {"target": 9000, "published": "${P:-1}", "host_ip": "127.0.0.1"},
                      {"target": "${ONLY_TARGET:-70}"}, {"target": 81, "published": "${Q:-2}", "host_ip": "::1"},
                      {"target": 82, "published": "${R:-3}", "host_ip": "${BIND:-127.0.0.1}"},
                      {"target": 83, "published": "83", "host_ip": "0.0.0.0"},
                      {"target": 80, "published": "8080"}]})
        self.assertEqual(services["mapped"], {"labels": {"io.gravitee.group": "Databases"}, "environment": {"A": "1"}})
        self.assertEqual(services["bare"], {"image": "busybox"})


class Copies(ProjectFolder):
    """raw reads the project through an overlay (compose._overlay) when a file has to be read as something else: a folder of
    its own whose entries link to the project's, but for the files written in it."""

    def setUp(self):
        super().setUp()
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        self.patch(compose, "scratch", return_value=self.scratch)

    def read(self, path, fails=None, **more):
        """raw(path, ...) with Compose replaced: the arguments it was given, the folder it ran in, and the files written in
        that folder ({path in the project: text}), as they were while it ran. `fails`: what Compose says, from them."""
        seen = {}

        def config(arguments, *options, timeout=120, cwd=None):
            seen.update(arguments=arguments, options=options, timeout=timeout, cwd=cwd)
            seen["written"] = {}
            for folder, _, names in os.walk(cwd) if cwd else ():   # what is no link: the files written for the reading
                for name in names:
                    one = Path(folder) / name
                    if not one.is_symlink():
                        seen["written"][one.relative_to(cwd).as_posix()] = one.read_text(encoding="utf-8")
            if fails:
                raise compose.ComposeError(fails(seen))
            return {"services": {}, "x-gravitee": {"kind": "addon", "where": f"{cwd}/addons"} if cwd else {}}
        with mock.patch.object(compose, "_config", side_effect=config):
            seen["config"] = compose.raw(path, **more)
        return seen


class PullPolicyVariables(Copies):
    """Compose 5.5.1 and later refuse `pull_policy: ${VAR:-never}` when they do not interpolate, which is how raw reads
    a file: it reads, in an overlay, the file with the default written there."""

    TOOL = ("services:\n"
            "  tool:\n"
            "    image: ghcr.io/example/tool:latest\n"
            "    build: ./image\n"
            "    pull_policy: ${TOOL_PULL_POLICY:-never}   # never: build it here once\n"
            "    ports:\n"
            '      - "127.0.0.1:${TOOL_PORT:-9002}:9002"\n')
    COPIED = TOOL.replace("${TOOL_PULL_POLICY:-never}", "never")

    def test_the_default_is_written_in_place_of_the_variable(self):
        for written, wanted in [("${TOOL_PULL_POLICY:-never}", "never"), ('"${TOOL_PULL_POLICY:-build}"', "build"),
                                ("'${A:-${B:-always}}'", "always"), ("${TOOL_PULL_POLICY:-every_2h}", "every_2h"),
                                ("${TOOL_PULL_POLICY}", "missing"), ("${TOOL_PULL_POLICY:-}", "missing"),
                                ("$TOOL_PULL_POLICY", "missing")]:
            with self.subTest(written):
                text = "services:\n  tool:\n    pull_policy: %s   # why\n"
                self.assertEqual(compose._policies_written(text % written), text % wanted)

    def test_the_rest_of_the_file_is_left_as_it_is(self):
        text = ("# pull_policy: ${X:-never}\n"
                "services:\n"
                "  tool:\n"
                "    pull_policy: never\n"
                "    image: ${TOOL_REGISTRY:-ghcr.io}/tool:${TOOL_VERSION:-1}\n"
                "    labels:\n"
                "      note: pull_policy ${X:-never}\n")
        self.assertEqual(compose._policies_written(text), text)

    def test_a_file_without_a_variable_policy_is_read_as_it_is(self):
        self.write("addons/tool/compose.yml", self.COPIED)
        seen = self.read("addons/tool/compose.yml")
        self.assertEqual((seen["arguments"], seen["cwd"]), (["-f", "addons/tool/compose.yml"], None))
        self.assertEqual(seen["options"], ("--no-interpolate", "--no-consistency"))
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_the_file_written_again_is_read_in_its_place_and_its_paths_are_the_projects(self):
        self.write("addons/tool/compose.yml", self.TOOL)
        seen = self.read("addons/tool/compose.yml")
        self.assertEqual(seen["arguments"], ["-f", "addons/tool/compose.yml"], "the same file, in the overlay")
        self.assertEqual(seen["written"], {"addons/tool/compose.yml": self.COPIED})
        self.assertEqual(seen["config"]["x-gravitee"]["where"], f"{self.root}/addons", "what Compose names is in the project")
        self.assertEqual((self.root / "addons/tool/compose.yml").read_text(encoding="utf-8"), self.TOOL)
        self.assertEqual(list(self.scratch.iterdir()), [])   # the overlay is removed once read

    def test_what_a_file_includes_is_written_again_and_the_rest_is_read_as_it_is(self):
        self.write("addons/tool/compose.yml", self.TOOL)
        self.write("addons/other/compose.yml", "services:\n  other:\n    image: busybox\n")
        self.write("setups/mine.yml", "include:\n  - ../addons/tool/compose.yml   # the tool\n"
                   "  - path: ../addons/other/compose.yml\n    project_directory: ../addons/other\n")
        seen = self.read("setups/mine.yml")
        self.assertEqual(seen["written"], {"addons/tool/compose.yml": self.COPIED}, "no include line is written again")

    def test_a_file_reached_twice_is_written_once(self):
        self.write("addons/tool/compose.yml", self.TOOL)
        for name in ("a", "b"):
            self.write(f"addons/{name}/compose.yml", "include:\n  - ../tool/compose.yml\n")
        self.write("setups/both.yml", "include:\n  - ../addons/a/compose.yml\n  - ../addons/b/compose.yml\n")
        self.assertEqual(self.read("setups/both.yml")["written"], {"addons/tool/compose.yml": self.COPIED})

    def test_a_problem_names_the_file_of_the_project(self):
        self.write("addons/tool/compose.yml", self.TOOL)
        self.write("setups/mine.yml", "include:\n  - ../addons/tool/compose.yml\n")
        with self.assertRaises(compose.ComposeError) as caught:
            self.read("setups/mine.yml", fails=lambda seen: f"validating {seen['cwd']}/addons/tool/compose.yml: "
                                                            "services.tool additional properties 'bogus' not allowed")
        self.assertEqual(str(caught.exception), f"validating {self.root / 'addons/tool/compose.yml'}: "
                                                "services.tool additional properties 'bogus' not allowed")
        self.assertEqual(list(self.scratch.iterdir()), [])


class SkippedIncludes(Copies):
    """A file that includes what is not there loads when raw is told to leave that include out: Compose reads an empty stand-in
    in its place, and resolves every include itself, whatever way the file writes it (the unit of a catalog that this machine
    has not added yet still shows what it is)."""

    TOOL = "services:\n  tool:\n    image: busybox\n"
    MINE = "\nservices:\n  mine:\n    image: busybox\n"

    def setUp(self):
        super().setUp()
        self.write("addons/tool/compose.yml", self.TOOL)

    def test_an_include_that_is_not_there_is_an_empty_stand_in_and_nothing_else_is_written(self):
        text = "include:\n  - ../../addons/tool/compose.yml\n" f"  - ../../{GONE}   # in a catalog to add\n" + self.MINE
        self.write("playgrounds/mine/compose.yml", text)
        seen = self.read("playgrounds/mine/compose.yml", skip=[GONE])
        self.assertEqual(seen["written"], {GONE: compose.STAND_IN})
        self.assertEqual(seen["arguments"], ["-f", "playgrounds/mine/compose.yml"])
        self.assertEqual((self.root / "playgrounds/mine/compose.yml").read_text(encoding="utf-8"), text, "never changed where it is")
        self.assertFalse((self.root / GONE).exists())
        self.assertEqual(list(self.scratch.iterdir()), [])   # the overlay is removed once read

    def test_each_way_of_writing_an_item_is_the_same_stand_in(self):
        for item in (f'"../../{GONE}"', f"'../../{GONE}'", f"path: ../../{GONE}   # a comment", f"path: '../../{GONE}'",
                     f"path: ../../{GONE}\n    project_directory: ../../addons/gone\n    env_file: ../../addons/gone/.env"):
            with self.subTest(item):
                self.write("playgrounds/mine/compose.yml", f"include:\n  - {item}\n  - ../../addons/tool/compose.yml\n" + self.MINE)
                self.assertEqual(self.read("playgrounds/mine/compose.yml", skip=[GONE])["written"], {GONE: compose.STAND_IN})

    def test_the_include_of_an_included_file_is_left_out_too(self):
        self.write("addons/mid/compose.yml", "include:\n  - ../gone/compose.yml\n  - ../tool/compose.yml\n"
                   "services:\n  mid:\n    image: busybox\n")
        self.write("playgrounds/mine/compose.yml", "include:\n  - ../../addons/mid/compose.yml\n" + self.MINE)
        self.assertEqual(self.read("playgrounds/mine/compose.yml", skip=[GONE])["written"], {GONE: compose.STAND_IN})

    def test_a_file_that_is_there_and_left_out_is_not_read(self):
        self.write("playgrounds/mine/compose.yml", "include:\n  - ../../addons/tool/compose.yml\n" + self.MINE)
        seen = self.read("playgrounds/mine/compose.yml", skip=["addons/tool/compose.yml"])
        self.assertEqual(seen["written"], {"addons/tool/compose.yml": compose.STAND_IN})

    def test_nothing_to_leave_out_is_the_file_as_it_is(self):
        self.write("playgrounds/mine/compose.yml", "include:\n  - ../../addons/tool/compose.yml\n" + self.MINE)
        for skip in ((), [GONE], ["addons/tool/other.yml"]):
            with self.subTest(skip=skip):
                seen = self.read("playgrounds/mine/compose.yml", skip=skip)
                self.assertEqual((seen["arguments"], seen["cwd"], seen["options"]), (
                    ["-f", "playgrounds/mine/compose.yml"], None, ("--no-interpolate", "--no-consistency")))
                self.assertEqual(list(self.scratch.iterdir()), [])

    def test_a_hostile_include_list_is_read_or_refused_never_a_crash(self):
        # whatever the file holds, raw answers or raises ComposeError: an exception of any other kind ends the read of the
        # whole project (project.gather)
        shapes = [
            "  - ../../addons/tool/compose.yml", f"  - ../../{GONE}", f"  - path: ../../{GONE}", "  - path:",
            "  - path:  # c", "    path:", "      - ../../addons/tool/compose.yml", f"      - ../../{GONE}",
            "    env_file:", "      - ../../addons/tool/compose.yml", "      - ../../addons/tool/.env",
            "    env_file: ../../addons/tool/.env", "    project_directory: ../../addons/tool", "  # a comment", "",
            "- ../../addons/tool/compose.yml", "    - ../../addons/tool/compose.yml",
            "        - ../../addons/tool/compose.yml", "      - - ../../x",
            '  - {path: ".lab/targets/cloud/addons__gone__compose.yml", project_directory: "addons/gone"}',
            '  - "unclosed', "  - 'q'   # c", "  -", "  - path: ''", "   - path: ../../addons/tool/compose.yml",
            "  - ../../addons/tool/\x00compose.yml", f"  - ../../../outside/{GONE}", "include:", "x-note: top",
            "  - ../../addons/tool/compose.yml:", "  - ../../" + "n" * 300 + "/compose.yml"]
        self.write("addons/tool/.env", "A=1\n")
        rng = random.Random(7)
        for _ in range(400):
            body = "\n".join(rng.choice(shapes) for _ in range(rng.randint(1, 9)))
            path = self.write("playgrounds/mine/compose.yml", f"include:\n{body}\n" + self.MINE)
            with self.subTest(body=body):
                self.read(path, skip=[GONE])
                self.assertTrue(all(isinstance(target, str) for target in compose.includes(path)))
        self.assertFalse((self.root / GONE).exists())

    def test_a_problem_names_the_file_of_the_project(self):
        self.write("setups/mine.yml", f"include:\n  - ../addons/tool/compose.yml\n  - ../{GONE}\n")
        with self.assertRaises(compose.ComposeError) as caught:
            self.read("setups/mine.yml", skip=[GONE], fails=lambda seen: f"validating {seen['cwd']}/setups/mine.yml: bogus")
        self.assertEqual(str(caught.exception), f"validating {self.root / 'setups/mine.yml'}: bogus")
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_the_time_compose_has(self):
        self.write("setups/mine.yml", f"include:\n  - ../addons/tool/compose.yml\n  - ../{GONE}\n")
        self.assertEqual(self.read("setups/mine.yml")["timeout"], 120)
        self.assertEqual(self.read("setups/mine.yml", timeout=15)["timeout"], 15)
        self.assertEqual(self.read("setups/mine.yml", skip=[GONE], timeout=15)["timeout"], 15)


@unittest.skipUnless(have_compose(), "needs docker compose")
class Overlaid(ProjectFolder):
    """What Compose reads through the overlay is the project as it is, a stand-in for each include left out: the reading of a
    file whose include is not there is the reading of the same file once it is, whatever its text holds that looks like an
    include list (a string, a comment)."""

    HIDE = ('x-gravitee: {kind: playground, name: Mine, note: "\ninclude:\n- a.yml\n  ", copy_of: tour, z: "\n"}\n'
            "services:\n  mine-web:\n    image: busybox:1.36\n")
    REVEAL = ('x-gravitee: {kind: playground, name: Mine, note: "\ninclude:\n  - ../../addons/x/compose.yml\n   "\n #", '
              'copy_of: tour, z: "\n #"\n}\nservices:\n  mine-web:\n    image: busybox:1.36\n')

    def test_a_text_that_looks_like_an_include_reads_as_it_does_once_its_target_is_there(self):
        for name, text, target in (("hide", self.HIDE, ".lab/catalogs/local/playgrounds/mine/a.yml"),
                                   ("reveal", self.REVEAL, ".lab/catalogs/local/addons/x/compose.yml")):
            with self.subTest(name):
                path = self.write(".lab/catalogs/local/playgrounds/mine/compose.yml", text)
                missing = compose.left_out(text, path)
                self.assertEqual(missing, [target], "the Lab's reader takes it for an include")
                without = compose.raw(path, skip=missing)["x-gravitee"]
                said = compose.x_gravitee(text, self.root / ".lab/catalogs/local/playgrounds/mine", fresh=True)
                self.write(target, compose.STAND_IN)
                self.assertEqual((without, said), (compose.raw(path)["x-gravitee"],) * 2)
                self.assertEqual("copy_of" in without, name == "hide", without)
                (self.root / target).unlink()

    def test_an_include_left_out_reads_as_nothing_and_the_paths_are_the_projects(self):
        self.write(".lab/catalogs/local/addons/tool/compose.yml", "services:\n  tool:\n    image: busybox\n    env_file: [x.env]\n")
        self.write(".lab/catalogs/local/addons/tool/x.env", "A=1\n")
        path = self.write(".lab/catalogs/local/playgrounds/mine/compose.yml",
                          "x-gravitee: {kind: playground, name: Mine}\ninclude:\n  - ../../addons/tool/compose.yml\n"
                          "  - path: ../../addons/gone/compose.yml\nservices:\n  mine:\n    image: busybox\n")
        found = compose.raw(path, skip=[".lab/catalogs/local/addons/gone/compose.yml"])
        self.assertEqual(sorted(found["services"]), ["mine", "tool"])
        self.assertEqual(found["services"]["tool"]["env_file"][0]["path"], str(self.root / ".lab/catalogs/local/addons/tool/x.env"))
        self.assertFalse((self.root / ".lab/catalogs/local/addons/gone").exists())


class ProjectName(ProjectFolder):

    def setUp(self):
        super().setUp()
        self.start(mock.patch.dict(compose._name, clear=True))
        self.write("docker-compose.yml", "name: lab\n")

    def test_read_again_only_when_the_file_changes(self):
        with mock.patch.object(compose, "raw", return_value={"name": "lab"}) as raw:
            self.assertEqual((compose.project_name(), compose.project_name()), ("lab", "lab"))
            self.write("docker-compose.yml", "name: other-lab\n")
            raw.return_value = {"name": "other-lab"}
            self.assertEqual(compose.project_name(), "other-lab")
        self.assertEqual(raw.call_count, 2)

    def test_the_last_name_is_kept_while_the_file_does_not_load(self):
        with mock.patch.object(compose, "raw", return_value={"name": "lab"}) as raw:
            compose.project_name()
            self.write("docker-compose.yml", "name: lab\nservices: [broken\n")
            raw.side_effect = compose.ComposeError("yaml: did not find expected ',' or ']'")
            self.assertEqual((compose.project_name(), compose.project_name()), ("lab", "lab"))
        self.assertEqual(raw.call_count, 2)   # not asked again until the file changes

    def test_a_core_file_that_is_not_there_keeps_the_last_name(self):
        # Being saved, synced or renamed: Compose says it is missing, the containers are still found
        with mock.patch.object(compose, "raw", return_value={"name": "lab"}) as raw:
            compose.project_name()
            (self.root / "docker-compose.yml").unlink()
            raw.side_effect = compose.ComposeError("open docker-compose.yml: no such file or directory")
            self.assertEqual((compose.project_name(), compose.project_name()), ("lab", "lab"))
            self.assertEqual(raw.call_count, 2)
            compose._name.clear()   # never read: Compose's error is raised
            with self.assertRaisesRegex(compose.ComposeError, "no such file"):
                compose.project_name()

    def test_without_a_name_the_error_is_raised_and_docker_is_not_asked(self):
        failure = compose.ComposeError("yaml: line 2: did not find expected key")
        with mock.patch.object(compose, "raw", side_effect=failure) as raw, mock.patch.object(compose, "ps") as ps:
            for _ in range(2):
                with self.assertRaisesRegex(compose.ComposeError, "line 2"):
                    compose.project_name()
            self.assertEqual(compose.containers(), ({}, None))   # the file's problem, not Docker's
        self.assertEqual(raw.call_count, 1)
        ps.assert_not_called()


class Unreachable(Case):

    def test_each_way_docker_says_that_it_does_not_answer(self):
        for said in ("Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
                     "Is the docker daemon running?",
                     "error during connect: Get http://%2F%2F.%2Fpipe%2Fdocker_engine/v1.24/info: open //./pipe/x",
                     "Is the docker daemon running?", DOCKER_CLI_SAYS):
            with self.subTest(said=said):
                self.assertTrue(compose.unreachable(said))
        for said in ("", None, "Error response from daemon: No such container: gio-redis", "yaml: line 3: bad key"):
            with self.subTest(said=said):
                self.assertFalse(compose.unreachable(said))


class Containers(Case):

    def ps(self, *answers, inspected="", call=compose.ps):
        """What `call` makes of Docker's answers to `compose ps`, given in turn."""
        answers = list(answers)

        def run(cmd, timeout, cwd=None):
            self.asked.append(cmd)
            return (0, inspected, "") if cmd[1:3] == ["container", "inspect"] else answers.pop(0)
        self.asked = []
        with mock.patch.object(compose, "project_name", return_value="lab"), \
                mock.patch.object(compose, "run", side_effect=run):
            return call()

    def test_one_container_per_line_with_the_ports_it_publishes(self):
        # A port is listed once per address family; one that is only exposed is published as 0
        publishers = [{"PublishedPort": port, "TargetPort": 80} for port in (18082, 8082, 8082, 0)]
        line = {"Service": "apim-gateway", "State": "running", "Health": "healthy", "Status": "Up 5 minutes (healthy)",
                "Image": "graviteeio/apim-gateway:4", "ExitCode": 0, "Name": "gio-gateway", "Publishers": publishers}
        self.assertEqual(self.ps((0, json.dumps(line) + "\n", "")), {"apim-gateway": {
            "state": "running", "health": "healthy", "status": "Up 5 minutes (healthy)", "exit_code": 0,
            "image": "graviteeio/apim-gateway:4", "name": "gio-gateway", "ports": ["8082", "18082"], "oom": False,
            "restarts": 0, "config_hash": None, "image_id": None}})

    def test_inspect_tells_a_memory_kill_and_the_recent_restarts(self):
        def started(seconds_ago):
            return time.strftime("%Y-%m-%dT%H:%M:%S.123456789Z", time.gmtime(time.time() - seconds_ago))
        lines = [{"Service": name, "State": "running", "Name": f"gio-{name}"} for name in ("kiosk", "kafka", "mail")]
        inspected = "\n".join([f"/gio-kiosk|false|3|{started(20)}|h1|sha256:k", f"/gio-kafka|true|4|{started(3600)}||",
                               "", "error: no such object: gio-mail"])
        found = self.ps((0, "\n".join(map(json.dumps, lines)), ""), inspected=inspected)
        self.assertEqual({name: (c["oom"], c["restarts"]) for name, c in found.items()},
                         {"kiosk": (False, 3),     # restarted, and again 20 s ago
                          "kafka": (True, 0),       # its last restart is an hour old
                          "mail": (False, 0)})      # removed since it was listed
        # The configuration Compose made it from, and the image it runs: what runs behind what is saved is told so
        self.assertEqual({name: (c["config_hash"], c["image_id"]) for name, c in found.items()},
                         {"kiosk": ("h1", "sha256:k"), "kafka": (None, None), "mail": (None, None)})
        self.assertNotIn("variable", found["kiosk"])   # not asked: not read

    def test_a_container_that_never_started_has_no_restarts(self):
        line = {"Service": "kiosk", "State": "created", "Name": "gio-kiosk"}
        found = self.ps((0, json.dumps(line), ""), inspected="/gio-kiosk|false|0|0001-01-01T00:00:00Z||\n")
        self.assertEqual((found["kiosk"]["oom"], found["kiosk"]["restarts"]), (False, 0))

    def test_a_container_removed_while_they_are_listed_is_asked_again(self):
        line = {"Service": "kiosk", "State": "running", "Name": "gio-kiosk"}
        removed = (1, "", "Error response from daemon: No such container: b2940a36c007\n")
        self.assertEqual(list(self.ps(removed, (0, json.dumps(line), ""))), ["kiosk"])

    def test_one_variable_of_its_environment_is_read_as_a_digest(self):
        # Only the entry asked for is printed by Docker; the Lab UI keeps a digest of its value, never the value
        lines = [{"Service": name, "State": "running", "Name": f"gio-{name}"} for name in ("gateway", "mongodb", "ui")]
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        inspected = "\n".join([f"/gio-gateway|false|0|{now}|h|sha256:g|key=c2VjcmV0=",
                               f"/gio-mongodb|false|0|{now}|h|i|", f"/gio-ui|false|0|{now}|h|i|key="])
        found = self.ps((0, "\n".join(map(json.dumps, lines)), ""), inspected=inspected,
                        call=lambda: compose.ps("key"))
        self.assertEqual({name: c["variable"] for name, c in found.items()},
                         {"gateway": compose.digest("c2VjcmV0="), "mongodb": None, "ui": compose.digest("")})
        self.assertIn('{{if eq (index (split . "=") 0) "key"}}{{.}}{{end}}', self.asked[-1][4])
        self.assertNotIn("c2VjcmV0", repr(found))

    def test_docker_not_answering_raises_its_last_line(self):
        with self.assertRaises(compose.ComposeError) as raised:
            self.ps((1, "", "warning\nCannot connect to the Docker daemon\n"))
        self.assertEqual(str(raised.exception), "Cannot connect to the Docker daemon")

    def test_containers_says_why_docker_did_not_answer(self):
        self.assertEqual(self.ps((1, "", "Cannot connect to the Docker daemon\n"), call=compose.containers),
                         ({}, "Cannot connect to the Docker daemon"))


class Saved(Case):
    """What Compose would make each service from, the images Docker holds, and what an image is read from."""

    def setUp(self):
        compose.CACHE.drop("local:image-ids")
        self.addCleanup(compose.CACHE.drop, "local:image-ids")

    def test_the_hash_of_each_service_as_compose_would_create_it(self):
        asked = []

        def run(cmd, timeout, cwd=None, env=None):
            asked.append((cmd, env))
            return 0, "apim-gateway 3848e4\nmongodb 6794b7\n", ""
        with mock.patch.object(compose, "run", side_effect=run):
            self.assertEqual(compose.config_hashes(), {"apim-gateway": "3848e4", "mongodb": "6794b7"})
            # Of other files, with values in place of the .env ones
            compose.config_hashes(["-f", "a.yml"], env={"KEY": "value"})
        self.assertEqual(asked, [([*compose.COMPOSE, "config", "--hash", "*"], None),
                                 ([*compose.COMPOSE, "-f", "a.yml", "config", "--hash", "*"], {"KEY": "value"})])
        with mock.patch.object(compose, "run", return_value=(1, "", "yaml: line 3: did not find expected key")):
            self.assertIsNone(compose.config_hashes())   # the files do not load: nothing is known

    def test_the_core_s_env_read_in_place_from_a_private_copy(self):
        folder, read = self.folder(), {}

        def run(cmd, timeout, cwd=None, env=None):
            copy = cmd[cmd.index("--env-file") + 1]
            with open(copy, encoding="utf-8") as file:
                read.update(text=file.read(), mode=oct(os.stat(copy).st_mode & 0o777))
            return 0, "mongodb h\n", ""
        with mock.patch.object(compose, "scratch", lambda: folder), mock.patch.object(compose, "run", side_effect=run):
            self.assertEqual(compose.config_hashes(dotenv="GRAVITEE_LICENSE=secret\n"), {"mongodb": "h"})
        self.assertEqual(read, {"text": "GRAVITEE_LICENSE=secret\n", "mode": "0o600"})
        self.assertEqual(list(folder.iterdir()), [])   # it holds secrets: gone once read

    def test_values_given_to_a_command_come_before_the_env_files(self):
        # Compose reads the core and what is on, whatever the shell says
        with mock.patch.dict(compose.os.environ, {"COMPOSE_FILE": "other.yml"}):
            answer = compose.run(["sh", "-c", 'printf "%s %s" "$KAFKA_VERSION" "$COMPOSE_FILE"'],
                                 env={"KAFKA_VERSION": "3.8"})
        self.assertEqual(answer, (0, "3.8 docker-compose.yml:.lab/on.yml", ""))
        # Whatever the terminal's language: what it prints is read as UTF-8, and never refused
        self.assertEqual(compose.run(["sh", "-c", r"printf 'caf\303\251 \377'"]), (0, "caf\u00e9 \ufffd", ""))

    def test_an_image_as_docker_lists_the_ones_it_holds(self):
        for image, listed in (("docker.io/bitnamilegacy/kafka:3.9", "bitnamilegacy/kafka:3.9"),
                              ("docker.io/library/mongo:8", "mongo:8"), ("mongo", "mongo:latest"),
                              ("localhost:5000/team/app", "localhost:5000/team/app:latest"),
                              ("graviteeio/apim-gateway:4.12-debian", "graviteeio/apim-gateway:4.12-debian")):
            self.assertEqual(compose.reference(image), listed, image)

    def test_the_images_docker_holds(self):
        listed = "mongo:8.3.11 sha256:d731\n<none>:<none> sha256:0ld\ngraviteeio/am-gateway:4 sha256:e633\n"
        with mock.patch.object(compose, "run", return_value=(0, listed, "")) as run:
            self.assertEqual(compose.image_ids(), {"mongo:8.3.11": "sha256:d731", "<none>:<none>": "sha256:0ld",
                                                   "graviteeio/am-gateway:4": "sha256:e633"})
            compose.image_ids()   # kept for a moment
        self.assertEqual(run.call_count, 1)
        self.assertIn("--no-trunc", run.call_args.args[0])
        compose.CACHE.drop("local:")   # a job ended: a pull may have moved a tag
        with mock.patch.object(compose, "run", return_value=(1, "", "Cannot connect to the Docker daemon")):
            self.assertIsNone(compose.image_ids())

    def test_an_image_built_again_from_unchanged_sources_is_the_same_image(self):
        # Docker's image store gives each build its own id: the manifest the container was made from says what it runs
        made = '{"digest":"sha256:arm","platform":{"architecture":"arm64","os":"linux"}}\n'
        compose._same.clear()
        with mock.patch.object(compose, "run", side_effect=[(0, made, ""), (0, "sha256:arm\n", "")]) as run:
            self.assertTrue(compose.same_image("gio-bakery-api", "sha256:new", "sha256:old"))
            self.assertTrue(compose.same_image("gio-bakery-api", "sha256:new", "sha256:old"))   # kept: ids never change
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0][3:5], ["--platform", "linux/arm64"])
        # Built from changed sources: another manifest
        with mock.patch.object(compose, "run", side_effect=[(0, made, ""), (0, "sha256:other\n", "")]):
            self.assertFalse(compose.same_image("gio-bakery-api", "sha256:newer", "sha256:old"))
        # No manifest to compare (Docker's former image store, or the container is gone), or the image is not
        # there: another id is another image, asked again next time
        for answers in (((0, "null\n", ""),), ((1, "", "No such container"),),
                        ((0, made, ""), (1, "", "No such image"))):
            with mock.patch.object(compose, "run", side_effect=answers):
                self.assertFalse(compose.same_image("gio-bakery-api", "sha256:gone", "sha256:old"))
        self.assertNotIn(("sha256:gone", "sha256:old"), compose._same)

    def test_what_an_image_is_read_from_is_known_by_its_id(self):
        # Its container's image while it runs; else the one its configured tag names here; else its name
        service = {"container": "gio-gateway", "image": "graviteeio/apim-gateway:4"}
        for answers, key in ((((0, "true sha256:run\n", ""),), "sha256:run"),
                             (((0, "false sha256:old\n", ""), (0, "sha256:tag\n", "")), "sha256:tag"),
                             (((1, "", "No such container"), (1, "", "No such image")), "graviteeio/apim-gateway:4")):
            with mock.patch.object(compose, "run", side_effect=list(answers)):
                self.assertEqual(compose.image_key(service), key)

    def test_why_an_image_could_not_be_read_in_plain_words(self):
        image = "graviteeio/apim-gateway:4"
        for said, plain in (("Cannot connect to the Docker daemon at unix:///x.sock. Is the docker daemon running?",
                             "Docker is not reachable"),
                            (DOCKER_CLI_SAYS, "Docker is not reachable"),
                            ("docker: Error response from daemon: No such image: graviteeio/apim-gateway:4",
                             "graviteeio/apim-gateway:4 is not pulled"),
                            ("unzip: cannot find license-model.yml", None), ("", None)):
            self.assertEqual(compose.unread(image, said), plain, said)


class Retargeted(ProjectFolder):

    def test_a_copy_includes_copies_in_place_of_the_files(self):
        text = ("include:\n  - ../kafka/compose.yml   # what it needs\n  - path: ../mail/compose.yml\n"
                "\nservices:\n  console:\n    volumes:\n      - ../kafka/compose.yml:/x\n")
        self.write("addons/console/compose.yml", text)
        found = compose.retargeted("addons/console/compose.yml", text,
                                   {"addons/kafka/compose.yml": "addons/kafka/.compose.yml.effects.yml"})
        self.assertEqual(found, text.replace("  - ../kafka/compose.yml ", "  - ../kafka/.compose.yml.effects.yml ", 1))
        # What is not in the include list stays as written
        self.assertIn("      - ../kafka/compose.yml:/x", found)


class Shell(Case):
    """A script run where a service's files are: in its container, else in its image."""

    SERVICE = {"container": "gio-gateway", "image": "graviteeio/apim-gateway:4"}
    EXEC = ["docker", "exec", "gio-gateway", "sh", "-c", "ls"]
    IMAGE = ["docker", "run", "--rm", "--pull", "never", "--entrypoint", "sh", "graviteeio/apim-gateway:4", "-c", "ls"]

    def shell(self, refused, image=(0, "found\n", "")):
        """(what shell() answers, the commands it ran) when the exec answers `refused`."""
        asked = []

        def run(cmd, timeout):
            asked.append(cmd)
            return refused if cmd[1] == "exec" else image
        with mock.patch.object(compose, "run", side_effect=run):
            return compose.shell(self.SERVICE, ["-c", "ls"]), asked

    def test_in_the_image_when_docker_cannot_run_it_in_the_container(self):
        for why in ("No such container: gio-gateway", "container 3f1c0a7d is not running",
                    "Container 3f1c0a7d is restarting, wait until the container is running",
                    "Container 3f1c0a7d is paused, unpause the container before exec"):
            answer, asked = self.shell((1, "", f"Error response from daemon: {why}\n"))
            self.assertEqual((answer, asked), ((0, "found\n", ""), [self.EXEC, self.IMAGE]), why)

    def test_an_image_that_is_not_here_is_not_pulled(self):
        missing = (125, "", "docker: Error response from daemon: No such image: graviteeio/apim-gateway:4\n\n"
                            "Run 'docker run --help' for more information\n")
        answer, asked = self.shell((1, "", "Error response from daemon: No such container: gio-gateway\n"), missing)
        self.assertEqual((answer, asked[-1]), (missing, self.IMAGE))

    def test_a_script_that_fails_in_its_container_is_not_run_again(self):
        answer, asked = self.shell((1, "", "unzip: cannot find license-model.yml\n"))
        self.assertEqual((answer, asked), ((1, "", "unzip: cannot find license-model.yml\n"), [self.EXEC]))


class Docker(Case):

    def setUp(self):
        compose.CACHE.drop("docker-memory")
        self.addCleanup(compose.CACHE.drop, "docker-memory")

    def test_its_memory_or_none_when_it_does_not_answer(self):
        with mock.patch.object(compose, "run", return_value=(1, "", "Cannot connect to the Docker daemon")):
            self.assertIsNone(compose.docker_memory())
        info = json.dumps({"ServerVersion": "29.7.2", "MemTotal": 8_000_000_000, "NCPU": 8})
        with mock.patch.object(compose, "run", return_value=(0, info, "")):
            self.assertEqual(compose.docker_memory(), 8_000_000_000)

    def test_its_scratch_folder_is_private_and_kept_for_the_run(self):
        folder = compose.scratch()
        self.assertEqual((folder, stat.S_IMODE(folder.stat().st_mode)), (compose.scratch(), 0o700))

    def test_its_scratch_folder_is_made_again_when_the_system_clears_it(self):
        shutil.rmtree(compose.scratch())
        folder = compose.scratch()
        self.assertEqual((folder.is_dir(), stat.S_IMODE(folder.stat().st_mode)), (True, 0o700))


class Environment(Case):
    """What every command gets: the files Compose reads, and no remote include."""

    def layout(self, waiting=None):
        """The layout module replaced until the test ends: by one that says whether it is waiting, else (None) by no
        module at all, as in a checkout that has not got one yet."""
        module = None
        if waiting is not None:
            module = types.ModuleType(LAYOUT)
            module.waiting = lambda: waiting
        absent = object()
        was = sys.modules.get(LAYOUT, absent)
        sys.modules[LAYOUT] = module
        self.addCleanup(lambda: sys.modules.pop(LAYOUT, None) if was is absent else sys.modules.update({LAYOUT: was}))

    def test_the_core_and_what_is_on_whatever_the_shell_says_and_no_remote_include(self):
        self.layout(waiting=False)
        shell = {"COMPOSE_FILE": "other.yml", "COMPOSE_EXPERIMENTAL_GIT_REMOTE": "true",
                 "COMPOSE_EXPERIMENTAL_OCI_REMOTE": "true", "SHELL_KEEPS": "this"}
        with mock.patch.dict(os.environ, shell):
            found = compose.environment()
        self.assertEqual(found["COMPOSE_FILE"], "docker-compose.yml:.lab/on.yml")
        self.assertEqual(found["COMPOSE_EXPERIMENTAL_GIT_REMOTE"], "false")
        self.assertEqual(found["COMPOSE_EXPERIMENTAL_OCI_REMOTE"], "false")
        self.assertEqual(found["SHELL_KEEPS"], "this")

    def test_the_flags_reach_every_command(self):
        self.layout(waiting=False)
        with mock.patch.dict(os.environ, {"COMPOSE_EXPERIMENTAL_GIT_REMOTE": "true"}):
            answer = compose.run(["sh", "-c", 'printf "%s %s" "$COMPOSE_EXPERIMENTAL_GIT_REMOTE" '
                                              '"$COMPOSE_EXPERIMENTAL_OCI_REMOTE"'])
        self.assertEqual(answer, (0, "false false", ""))

    def test_the_core_alone_while_the_layout_is_waiting(self):
        self.layout(waiting=True)
        with mock.patch.dict(os.environ, {"COMPOSE_FILE": "other.yml"}):
            found = compose.environment()
            answer = compose.run(["sh", "-c", 'printf "%s" "$COMPOSE_FILE"'])
        self.assertEqual(found["COMPOSE_FILE"], "docker-compose.yml")
        self.assertEqual(found["COMPOSE_EXPERIMENTAL_GIT_REMOTE"], "false")   # in every phase
        self.assertEqual(answer, (0, "docker-compose.yml", ""))

    def test_the_core_and_what_is_on_once_the_layout_is_not_waiting(self):
        self.layout(waiting=False)
        self.assertEqual(compose.environment()["COMPOSE_FILE"], compose.COMPOSE_FILE)

    def test_a_checkout_without_the_layout_is_not_waiting(self):
        self.layout(None)
        self.assertEqual(compose.environment()["COMPOSE_FILE"], compose.COMPOSE_FILE)

    def test_a_module_that_the_layout_needs_and_is_missing_is_a_fault_to_see(self):
        modules = self.patch(compose, "importlib")
        modules.import_module.side_effect = ModuleNotFoundError("No module named 'tomllib'", name="tomllib")
        with self.assertRaisesRegex(ModuleNotFoundError, "tomllib"):
            compose.environment()

    def test_a_command_gets_the_environment_it_is_given_in_place_of_the_shells(self):
        # scrubbed_environment() and build_environment() are given whole: nothing of the shell stays under them
        self.layout(waiting=False)
        given = {"PATH": os.environ["PATH"], "ONLY_THIS": "yes"}
        show = 'printf "%s|%s|%s|%s" "$ONLY_THIS" "$SHELL_KEEPS" "$SOME_API_TOKEN" "$COMPOSE_EXPERIMENTAL_GIT_REMOTE"'
        with mock.patch.dict(os.environ, {"SHELL_KEEPS": "this", "SOME_API_TOKEN": "secret-value"}):
            self.assertEqual(compose.run(["sh", "-c", show], environ=given), (0, "yes|||", ""))
            self.assertEqual(compose.run(["sh", "-c", show]), (0, "|this|secret-value|false", ""), "as before without it")

    def test_what_env_sets_is_still_set_over_the_environment_it_is_given(self):
        answer = compose.run(["sh", "-c", 'printf "%s %s" "$A" "$B"'], environ={"PATH": os.environ["PATH"], "A": "1"},
                             env={"A": "2", "B": "3"})
        self.assertEqual(answer, (0, "2 3", ""))

    def test_an_environment_given_does_not_ask_the_layout(self):
        # environment() reads the layout of the machine; a scrubbed environment has nothing to do with it
        modules = self.patch(compose, "importlib")
        modules.import_module.side_effect = AssertionError("asked the layout")
        self.assertEqual(compose.run(["sh", "-c", "printf ok"], environ={"PATH": os.environ["PATH"]}), (0, "ok", ""))


class Run(Case):
    """A command that does not finish in time is ended with everything it started, and so is one that the Lab UI leaves
    behind: Compose is a plugin that the Docker CLI starts, and ending the CLI alone leaves it running."""

    def setUp(self):
        self.folder()
        self.pidfile = self.root / "child.pid"
        self.record = f"echo $$ > {shlex.quote(str(self.pidfile))}"

    def test_a_command_is_told_the_folder_it_runs_in_whatever_environment_it_gets(self):
        """$PWD is what Compose spells its paths from: two environments (the Lab's, the scrubbed one) must not spell them two ways."""
        for environ in (None, {"PATH": os.environ["PATH"], "PWD": "/somewhere/else"}):
            with self.subTest(environ=environ):
                code, out, _ = compose.run(["sh", "-c", 'printf %s "$PWD"'], cwd=str(self.root), environ=environ)
                self.assertEqual((code, out), (0, str(self.root)))

    def started(self):
        """Waits for the command to record the id of a process it started."""
        def written():
            return self.pidfile.is_file() and self.pidfile.read_text(encoding="utf-8").endswith("\n")
        self.assertTrue(settled(written, 10), "the command did not start")

    def child(self):
        """The id that the command recorded; that process is ended when the test ends, whatever the test found."""
        self.started()
        pid = int(self.pidfile.read_text(encoding="utf-8"))
        self.addCleanup(lambda: alive(pid) and os.kill(pid, 9))
        return pid

    def test_it_answers_its_code_and_what_it_printed(self):
        self.assertEqual(compose.run(["sh", "-c", "echo out; echo err >&2; exit 3"]), (3, "out\n", "err\n"))

    def test_a_command_that_is_not_there(self):
        code, out, err = compose.run(["no-such-command-for-the-lab"])
        self.assertEqual((code, out), (127, ""))
        self.assertIn("no-such-command-for-the-lab", err)

    def test_it_gets_no_input(self):
        self.assertEqual(compose.run(["sh", "-c", 'read line; echo "[$line]"']), (0, "[]\n", ""))

    def test_it_runs_from_the_project_root_or_the_folder_asked(self):
        folder = self.root / "elsewhere"
        folder.mkdir()
        self.patch(compose, "ROOT", self.root)
        self.assertEqual(compose.run(["pwd"]), (0, f"{self.root}\n", ""))
        self.assertEqual(compose.run(["pwd"], cwd=folder), (0, f"{folder}\n", ""))

    def test_past_its_time_it_is_ended_with_everything_it_started(self):
        started = time.monotonic()
        answer = compose.run(["sh", "-c", f"sleep {FOREVER} & echo $! > {shlex.quote(str(self.pidfile))}; wait"], timeout=1)
        self.assertEqual(answer, (124, "", "sh did not answer within 1 s"))
        self.assertLess(time.monotonic() - started, 10)
        pid = self.child()
        self.assertTrue(settled(lambda: not alive(pid)), "what the command started is still running")
        self.assertEqual(compose._running, set())

    def test_an_interrupt_ends_it_too(self):
        def interrupted(process, *args, **kwargs):
            self.started()
            raise KeyboardInterrupt
        with mock.patch.object(subprocess.Popen, "communicate", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                compose.run(["sh", "-c", f"{self.record}; exec sleep {FOREVER}"], timeout=FOREVER)
        pid = self.child()
        self.assertTrue(settled(lambda: not alive(pid)))
        self.assertEqual(compose._running, set())

    def test_what_still_runs_when_the_lab_ui_exits_is_ended(self):
        answered = []
        thread = threading.Thread(target=lambda: answered.append(
            compose.run(["sh", "-c", f"{self.record}; exec sleep {FOREVER}"], timeout=FOREVER)))
        thread.start()
        self.addCleanup(thread.join, 10)
        pid = self.child()
        self.assertTrue(settled(lambda: compose._running, 10))
        compose.stop_running()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertNotEqual(answered[0][0], 0)
        self.assertTrue(settled(lambda: not alive(pid)))

    def test_the_exit_function_ends_what_runs_and_nothing_is_started_after_it(self):
        self.patch(compose, "_ending", False)
        git = sys.modules.get("lab_ui.catalogs.gitx")   # _end ends its commands too, and leaves it ending for the rest of the run
        if git is not None:
            self.patch(git, "_ending", False)
        answered = []
        thread = threading.Thread(target=lambda: answered.append(
            compose.run(["sh", "-c", f"{self.record}; exec sleep {FOREVER}"], timeout=FOREVER)))
        thread.start()
        self.addCleanup(thread.join, 10)
        pid = self.child()
        self.assertTrue(settled(lambda: compose._running, 10))
        compose._end()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertTrue(settled(lambda: not alive(pid)))
        started = self.root / "started"
        self.assertEqual(compose.run(["sh", "-c", f"touch {shlex.quote(str(started))}"]),
                         (130, "", "sh was not run: the Lab UI is ending"))
        self.assertFalse(started.exists())

    def test_a_command_started_as_the_lab_ui_begins_to_exit_is_ended(self):
        # _end ended what it saw run: this one was started after that, by a thread that had looked before
        self.patch(compose, "_ending", False)
        popen = subprocess.Popen

        def late(*args, **kwargs):
            process = popen(*args, **kwargs)
            compose._ending = True
            return process
        with mock.patch.object(subprocess, "Popen", late):
            started = time.monotonic()
            code, _, _ = compose.run(["sleep", str(FOREVER)], timeout=5)
        self.assertEqual(code, -signal.SIGKILL)   # not 124: it did not wait for its time
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(compose._running, set())


# A Lab UI, as a program: the commands of a project read, six of them asked of a pool of two threads, which is how the
# project is read. It says when two of them run, then waits; Ctrl+C ends it
# What a program started from a terminal has for the signals the cases below send: the defaults
TERMINAL = """
import signal
signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGHUP, signal.SIG_DFL)
signal.signal(signal.SIGTERM, signal.SIG_DFL)
"""
POOL = """
import os, sys, threading, time
sys.path.insert(0, sys.argv[1])
if sys.argv[2] == "before":
    import concurrent.futures
from lab_ui.docker import compose
import concurrent.futures
def work():
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: compose.run(["sleep", os.environ["SLEEP"]], timeout=600), range(6)))
threading.Thread(target=work, daemon=True).start()
while len(compose._running) < 2:
    time.sleep(0.05)
try:   # (the interrupt can come as soon as "ready" is read: no instruction of this program is outside the try)
    print("ready", flush=True)
    while True:   # (not one long sleep: a signal that the system hands to another thread does not wake the main thread)
        time.sleep(0.05)
except KeyboardInterrupt:
    pass
"""
# A Lab UI that asks to end what runs when it is hung up on or terminated, with what it did before for that signal
SIGNALS = """
import os, signal, sys, threading, time
sys.path.insert(0, sys.argv[1])
from lab_ui.docker import compose
if sys.argv[2] == "ignored":
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
if sys.argv[2] == "handled":   # (written below the buffer of print: the signal can come while "ready" is still being flushed)
    signal.signal(signal.SIGHUP, lambda number, frame: os.write(1, b"handled\\n"))
compose.stop_on_signals()
threading.Thread(target=lambda: compose.run(["sleep", os.environ["SLEEP"]], timeout=600), daemon=True).start()
while not compose._running:
    time.sleep(0.05)
print("ready", flush=True)
while True:   # (not one long sleep: a signal that the system hands to another thread does not wake the main thread)
    time.sleep(0.05)
"""


class Exiting(Case):
    """What happens to the commands when the Lab UI itself ends. Each leads a session of its own, so what ends the Lab UI
    (the Ctrl+C of its terminal, the terminal closing, a signal) does not reach them: the Lab UI ends them. Each case is
    a Lab UI of its own, run as a program."""

    def setUp(self):
        self.marker = f"{FOREVER}.{random.randrange(10 ** 6):06d}"   # the time its commands sleep, which no other has
        if holding(self.marker) is None:
            self.skipTest("ps cannot list processes: what is left behind is not found")

    def program(self, script, *more):
        """A Python program started in a process group of its own, as a terminal does, once it says it is ready. It and
        what it started are ended when the test ends. Its signals are the defaults of a program a terminal starts, whatever
        started the tests: a program started in the background ignores Ctrl+C, and with `nohup` the hang-up as well, and
        what it starts would inherit that."""
        process = subprocess.Popen([sys.executable, "-c", TERMINAL + script, str(LAB_UI), *more], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True,
                                   env={**os.environ, "SLEEP": self.marker})   # not in its arguments: only commands hold it

        def end():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            process.stdout.close()
            for pid in holding(self.marker) or []:
                os.kill(pid, signal.SIGKILL)
        self.addCleanup(end)
        self.assertEqual(process.stdout.readline().strip(), "ready")
        return process

    def exits(self, process, seconds=20):
        """The code the program exited with; it fails when it does not exit."""
        try:
            return process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            self.fail(f"the Lab UI did not exit within {seconds} s: it waits for what it started")

    def test_ctrl_c_ends_the_commands_that_a_pool_of_threads_runs(self):
        # Python waits for the threads of a pool before it runs the functions of `atexit`, and those threads wait for the
        # commands. Six asked of two threads: the four queued are not started as the two running are ended
        for imported in ("before", "after"):
            with self.subTest(pool_imported=imported):
                process = self.program(POOL, imported)
                started = time.monotonic()
                os.killpg(process.pid, signal.SIGINT)   # what a terminal sends to the group of its foreground program
                self.assertEqual(self.exits(process), 0)
                self.assertLess(time.monotonic() - started, 15)
                self.assertTrue(settled(lambda: not holding(self.marker)), "a command is still running")

    def test_a_hang_up_and_a_termination_end_the_commands_too(self):
        for signal_name in ("SIGHUP", "SIGTERM"):
            with self.subTest(signal_name):
                process = self.program(SIGNALS, "default")
                os.kill(process.pid, getattr(signal, signal_name))   # to the Lab UI alone, as `kill` does
                self.assertEqual(self.exits(process), -getattr(signal, signal_name))   # it ends as it would have
                self.assertTrue(settled(lambda: not holding(self.marker)), "the command is still running")

    def test_a_signal_the_lab_ui_ignores_or_handles_is_left_to_it(self):
        for how, expected in (("ignored", None), ("handled", "handled")):
            with self.subTest(how):
                process = self.program(SIGNALS, how)
                os.kill(process.pid, signal.SIGHUP)
                if expected:
                    self.assertEqual(process.stdout.readline().strip(), expected)
                time.sleep(0.5)
                self.assertIsNone(process.poll())
                self.assertTrue(holding(self.marker))
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                for pid in holding(self.marker) or []:
                    os.kill(pid, signal.SIGKILL)
                self.assertTrue(settled(lambda: not holding(self.marker)))


class Limits(ProjectFolder):
    """The time a read of Compose has, and what it answers."""

    WHOLE = {"name": "lab", "services": {"a": {"image": "busybox"}},
             "volumes": {"data": {"name": "lab_data"}}, "networks": {"net": {"name": "lab_net"}},
             "secrets": {"tok": {"environment": "TOKEN"}}, "configs": {"cfg": {"content": "hello"}}}

    def test_each_read_has_the_time_it_needs(self):
        with mock.patch.object(compose, "run", return_value=(0, json.dumps(self.WHOLE), "")) as run:
            compose._config(["-f", "a.yml"])
            compose._config(["-f", "a.yml"], timeout=5)
            compose.config_json(["-f", "a.yml"])
            compose.config_json(["-f", "a.yml"], timeout=3)
            compose.resolved(["-f", "a.yml"])
        self.assertEqual([call.kwargs["timeout"] for call in run.call_args_list], [120, 5, 15, 3, 120])

    def test_the_environment_asked_for_reaches_the_command_and_none_asked_changes_nothing(self):
        asked = {"PATH": "/bin", "HOME": "/scrubbed"}
        with mock.patch.object(compose, "run", return_value=(0, json.dumps(self.WHOLE), "")) as run:
            compose.config_json(["-f", "a.yml"])
            compose.config_json(["-f", "a.yml"], environ=asked)
            compose.config_json(["-f", "a.yml"], 3, asked)
            compose._config(["-f", "a.yml"], "--no-interpolate", environ=asked)
            compose.resolved(["-f", "a.yml"])
        first, *others = run.call_args_list
        self.assertNotIn("environ", first.kwargs, "the call of the Lab is the call it was")
        self.assertEqual([call.kwargs.get("environ") for call in others], [asked, asked, asked, None])
        self.assertEqual([call.kwargs["timeout"] for call in run.call_args_list], [15, 15, 3, 120, 120])

    def test_a_read_that_is_not_answered_in_time_raises_compose_error(self):
        self.patch(compose, "COMPOSE", ["sh", "-c", f"sleep {FOREVER}", "sh"])   # a Compose that never answers
        started = time.monotonic()
        with self.assertRaisesRegex(compose.ComposeError, "^sh did not answer within 1 s$"):
            compose.config_json(["-f", "a.yml"], timeout=1)
        self.assertLess(time.monotonic() - started, 10)

    def test_the_whole_configuration_is_answered_not_only_the_services(self):
        with mock.patch.object(compose, "run", return_value=(0, json.dumps(self.WHOLE), "")) as run:
            found = compose.config_json(["-f", "a.yml"])
            self.assertEqual(compose.resolved(["-f", "a.yml"]), self.WHOLE["services"])
        self.assertEqual(found, self.WHOLE)
        self.assertEqual(run.call_args.args[0], [*compose.COMPOSE, "-f", "a.yml", "config", "--format", "json"])

    def test_what_compose_refuses_raises_its_last_line(self):
        with mock.patch.object(compose, "run", return_value=(1, "", "time=x level=warning msg=y\nyaml: line 3: bad key\n")):
            with self.assertRaisesRegex(compose.ComposeError, "^yaml: line 3: bad key$"):
                compose.config_json(["-f", "a.yml"])


FAKE_GIT = """#!/bin/sh
echo "$@" >> "$GIT_LOG"
exit 1
"""
CORE = "name: lab\nservices:\n  core:\n    image: busybox\n"


@unittest.skipUnless(COMPOSE_5, "Docker Compose 5 or later is not installed: what a file can make it do is not tried")
class Compose5(ProjectFolder):
    """What a file written by someone else can make Compose do, tried with Compose itself (it renders files with no
    daemon, and never touches the lab that runs here). The folder is the project; nothing here uses the network."""

    def setUp(self):
        super().setUp()
        self.write("docker-compose.yml", CORE)

    def fake_git(self):
        """A `git` first on the PATH that records what it is asked and fails: whether Compose fetches, and no network.
        Answers the file that records."""
        shim, log = self.root / "bin", self.root / "git.log"
        shim.mkdir()
        (shim / "git").write_text(FAKE_GIT, encoding="utf-8")
        (shim / "git").chmod(0o755)
        log.write_text("", encoding="utf-8")
        self.start(mock.patch.dict(os.environ, {"PATH": f"{shim}{os.pathsep}{os.environ['PATH']}", "GIT_LOG": str(log)}))
        return log

    def test_a_file_that_includes_what_is_not_there_loads_when_that_is_left_out(self):
        for policy in ("", "    pull_policy: ${TOOL_PULL:-never}\n"):   # with and without a copy of the file to read
            with self.subTest(policy=policy):
                self.write("addons/tool/compose.yml", f"services:\n  tool:\n    image: busybox\n{policy}")
                path = self.write("playgrounds/mine/compose.yml", "x-gravitee:\n  name: Mine\ninclude:\n"
                                 "  - ../../addons/tool/compose.yml\n  - path: ../../addons/gone/compose.yml\n"
                                 "    project_directory: ../../addons/gone\nservices:\n  mine:\n    image: busybox\n")
                with self.assertRaisesRegex(compose.ComposeError, "gone/compose.yml: no such file"):
                    compose.raw(path)
                found = compose.raw(path, skip=[GONE])
                self.assertEqual(sorted(found["services"]), ["mine", "tool"])
                self.assertEqual(found["x-gravitee"], {"name": "Mine"})

    def test_an_item_with_files_to_read_loads_whether_it_is_kept_or_left_out(self):
        # The lines of its env_file are files of the project, not includes; a copy keeps them below its item as they are
        self.write("addons/tool/compose.yml", "services:\n  tool:\n    image: busybox\n")
        self.write("addons/tool/.env", "A=1\n")
        path = self.write("playgrounds/mine/compose.yml", "include:\n"
                         "  - path: ../../addons/tool/compose.yml\n"
                         "    env_file:\n      - ../../addons/tool/.env\n"
                         "  - path: ../../addons/gone/compose.yml\n"
                         "    env_file:\n      - ../../addons/tool/.env\n      - ../../addons/tool/compose.yml\n"
                         "services:\n  mine:\n    image: busybox\n")
        self.assertEqual(sorted(compose.raw(path, skip=[GONE])["services"]), ["mine", "tool"])

    def test_an_item_that_names_its_project_directory_loads_as_a_copy(self):
        self.write("addons/tool/compose.yml", "services:\n  tool:\n    image: busybox\n    pull_policy: ${TOOL_PULL:-never}\n"
                  "    volumes: ['./data:/data']\n")
        path = self.write("playgrounds/mine/compose.yml", "include:\n  - path: ../../addons/tool/compose.yml\n"
                         "    project_directory: ../../addons/tool\nservices:\n  mine:\n    image: busybox\n")
        found = compose.raw(path)
        self.assertEqual(sorted(found["services"]), ["mine", "tool"])
        (mount,) = found["services"]["tool"]["volumes"]
        self.assertEqual(mount["source"], str(self.root / "addons/tool/data"))   # relative to the folder it names

    def test_the_files_of_a_path_list_are_read_where_they_are(self):
        # a copy is written for the other items; the files an item lists under `path:` are not rewritten
        self.write("addons/a/compose.yml", "services:\n  a:\n    image: busybox\n")
        self.write("addons/b/compose.yml", "services:\n  b:\n    image: busybox\n    volumes: ['./data:/data']\n")
        path = self.write("playgrounds/mine/compose.yml", "include:\n  - path:\n      - ../../addons/a/compose.yml\n"
                         "      - ../../addons/b/compose.yml\n  - ../../addons/gone/compose.yml\n"
                         "services:\n  mine:\n    image: busybox\n")
        self.assertEqual(compose.includes(path), ["addons/a/compose.yml", "addons/b/compose.yml", GONE])
        with self.assertRaisesRegex(compose.ComposeError, "gone/compose.yml: no such file"):
            compose.raw(path)
        found = compose.raw(path, skip=[GONE])
        self.assertEqual(sorted(found["services"]), ["a", "b", "mine"])
        self.write(path, "include:\n  - path:\n      - ../../addons/a/compose.yml\n      - ../../addons/b/compose.yml\n"
                  "services:\n  mine:\n    image: busybox\n")
        self.assertEqual(compose.raw(path), found)   # as if the item had never been there

    def test_a_file_without_what_to_leave_out_is_read_as_it_always_was(self):
        self.write("addons/tool/compose.yml", "services:\n  tool:\n    image: busybox\n    pull_policy: ${TOOL_PULL:-never}\n"
                  "    labels: [a=b]\n    ports: ['127.0.0.1:${P:-8080}:80']\n")
        path = self.write("playgrounds/mine/compose.yml", "include:\n  - ../../addons/tool/compose.yml\n"
                         "services:\n  mine:\n    image: busybox\n    environment: ['K=${V}']\n")
        found = compose.raw(path)
        tool = found["services"]["tool"]
        self.assertEqual((tool["pull_policy"], tool["labels"], tool["ports"]), (
            "never", {"a": "b"}, [{"target": 80, "published": "${P:-8080}", "host_ip": "127.0.0.1"}]))
        self.assertEqual(found["services"]["mine"]["environment"], {"K": "${V}"})
        self.assertEqual(compose.raw(path, skip=["addons/other/compose.yml"], timeout=60), found)

    def test_the_whole_configuration_has_what_a_verdict_reads(self):
        path = self.write("addons/mine/compose.yml", "services:\n  mine:\n    image: busybox\n    networks: [net]\n"
                         "    volumes: [data:/data]\n    secrets: [tok]\n    configs: [cfg]\n"
                         "volumes:\n  data: {}\nnetworks:\n  net: {}\nsecrets:\n  tok:\n    environment: HOME\n"
                         "configs:\n  cfg:\n    content: hello\n")
        found = compose.config_json(compose.all_files([path]))
        self.assertEqual(sorted(found["services"]), ["core", "mine"])
        for key, name in (("volumes", "data"), ("networks", "net"), ("secrets", "tok"), ("configs", "cfg")):
            self.assertIn(name, found[key], key)

    def test_a_variable_that_names_a_folder_is_a_bind_once_it_is_interpolated(self):
        path = self.write("addons/mine/compose.yml", "services:\n  mine:\n    image: busybox\n"
                         "    volumes:\n      - ${HOME}:/host:ro\n")
        # Not interpolated, Compose cannot tell a folder from the name of a volume: only the interpolated form is typed
        (named,) = compose.raw(path)["services"]["mine"]["volumes"]
        self.assertEqual((named["type"], named["source"], named["target"]), ("volume", "${HOME}", "/host"))
        (mount,) = compose.config_json(compose.all_files([path]))["services"]["mine"]["volumes"]
        self.assertEqual((mount["type"], mount["source"], mount["target"]), ("bind", os.environ["HOME"], "/host"))

    def test_a_remote_include_is_refused_and_nothing_is_fetched(self):
        log = self.fake_git()
        for address in ("https://example.invalid/org/repo.git", "git@example.invalid:org/repo.git",
                        "oci://example.invalid/org/repo:tag"):
            with self.subTest(address):
                path = self.write("addons/remote/compose.yml", f"include:\n  - path: {address}\n")
                for read in (lambda: compose.raw(path), lambda: compose.config_json(["-f", path])):
                    with self.assertRaisesRegex(compose.ComposeError, r"disabled by \"COMPOSE_EXPERIMENTAL_(GIT|OCI)_REMOTE\""):
                        read()
                self.assertEqual(log.read_text(encoding="utf-8"), "")

    def test_it_would_fetch_without_the_flags(self):
        # What the flags hold back: the control that makes the test above mean something
        log = self.fake_git()
        path = self.write("addons/remote/compose.yml", "include:\n  - path: https://example.invalid/org/repo.git\n")
        code, _, _ = compose.run([*compose.COMPOSE, "-f", path, "config"], env={"COMPOSE_EXPERIMENTAL_GIT_REMOTE": "true"})
        self.assertNotEqual(code, 0)
        self.assertIn("ls-remote", log.read_text(encoding="utf-8"))

    def test_a_docker_compose_typed_by_hand_is_held_back_by_the_env_file(self):
        log = self.fake_git()
        self.write("remote.yml", "include:\n  - path: https://example.invalid/org/repo.git\n")
        shell = {name: value for name, value in os.environ.items() if not name.startswith("COMPOSE_")}
        for dotenv, fetched in (("", True), ("COMPOSE_EXPERIMENTAL_GIT_REMOTE=false\n"
                                              "COMPOSE_EXPERIMENTAL_OCI_REMOTE=false\n", False)):
            with self.subTest(dotenv=dotenv):
                self.write(".env", dotenv)
                log.write_text("", encoding="utf-8")
                subprocess.run(["docker", "compose", "-f", "remote.yml", "config"], cwd=self.root, env=shell,
                               capture_output=True, text=True, timeout=60)
                self.assertEqual(bool(log.read_text(encoding="utf-8")), fetched)

    def test_a_remote_build_context_is_not_an_include_and_is_left_alone(self):
        path = self.write("addons/mine/compose.yml", "services:\n  mine:\n    image: busybox\n"
                         "    build:\n      context: https://github.com/example/repo.git#main\n")
        found = compose.config_json(compose.all_files([path]))
        self.assertEqual(found["services"]["mine"]["build"]["context"], "https://github.com/example/repo.git#main")

    def test_a_file_that_makes_compose_wait_without_end_is_ended_with_everything_it_started(self):
        # `env_file: /dev/zero` is the same for the time Compose takes (it reads without end) and fills the machine's memory
        # at 6 GB a second, which a test must not do; a pipe that nobody writes to makes it wait the same way
        name = f"wait-{os.urandom(4).hex()}.yml"
        os.mkfifo(self.root / "never")
        self.write(name, "services:\n  a:\n    image: busybox\n    env_file: never\n")
        if holding(name) is None:
            self.skipTest("ps cannot list processes: what is left behind is not found")
        self.addCleanup(lambda: [os.kill(pid, 9) for pid in holding(name) or []])   # nothing may be left waiting
        started = time.monotonic()
        with self.assertRaisesRegex(compose.ComposeError, "did not answer within 1 s"):
            compose.config_json(["-f", "docker-compose.yml", "-f", name], timeout=1)
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue(settled(lambda: not holding(name)), "Compose is still waiting")


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(have_compose(), "needs docker compose")
class XGravitee(ProjectFolder):
    """The x-gravitee block of a text, as Compose reads it in the item's folder: from its input, nothing written, bounded."""

    def setUp(self):
        super().setUp()
        self.write("addons/base/compose.yml", "services:\n  base:\n    image: busybox\n")
        self.here = self.root / "playgrounds" / "mine"
        self.here.mkdir(parents=True)

    def test_every_way_of_writing_an_include_is_read_where_the_item_is(self):
        block = "x-gravitee: {kind: playground, name: Mine}\n"
        for name, include in {"list": "include:\n  - ../../addons/base/compose.yml\n",
                              "path list": "include:\n  - path:\n      - ../../addons/base/compose.yml\n",
                              "flow": "include: [../../addons/base/compose.yml]\n",
                              "quoted key": '"include":\n  - ../../addons/base/compose.yml\n'}.items():
            with self.subTest(name):
                self.assertEqual(compose.x_gravitee(include + block, self.here), {"kind": "playground", "name": "Mine"})
        self.assertEqual(list(self.here.iterdir()), [], "nothing is written where the item is")

    def test_a_text_that_takes_too_long_or_answers_too_much_cannot_be_read_and_leaves_nothing_running(self):
        os.mkfifo(self.here / "never")   # a file that never ends: Compose waits on it
        self.patch(compose, "ALONE_TIMEOUT", 2)
        started = time.monotonic()
        with self.assertRaisesRegex(compose.ComposeError, "did not answer within 2 s"):
            compose.x_gravitee("include: [never]\nx-gravitee: {kind: addon}\n", self.here)
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue(settled(lambda: not holding(str(self.here))), "the Compose that waited on it is ended, with its plugin")
        self.patch(compose, "ALONE_OUTPUT", 10)
        with self.assertRaisesRegex(compose.ComposeError, "^Compose answered more than an item can hold$"):
            compose.x_gravitee("x-gravitee: {kind: addon, name: Long}\n", self.here)

    def test_no_env_file_of_the_folder_is_read(self):
        text = "x-gravitee: {kind: addon, name: Quiet}\nservices:\n  quiet:\n    image: busybox\n    env_file: [x.env]\n"
        for name, make in (("a pipe", lambda file: os.mkfifo(file)), ("a link to an endless file", lambda file: os.symlink("/dev/zero", file))):
            with self.subTest(name):
                for one in (".env", "x.env"):
                    make(self.here / one)
                started = time.monotonic()
                self.assertEqual(compose.x_gravitee(text, self.here, fresh=True), {"kind": "addon", "name": "Quiet"})
                self.assertLess(time.monotonic() - started, 5, "answered at once: neither file is opened")
                self.assertTrue(settled(lambda: not holding(str(self.here))))
                for one in (".env", "x.env"):
                    (self.here / one).unlink()

    def test_the_same_text_in_the_same_folder_is_read_once_and_a_file_raw_read_is_not_read_again(self):
        text = "x-gravitee: {kind: addon, name: Once}\nservices:\n  once:\n    image: busybox\n"
        real, runs = compose.run, []
        self.patch(compose, "run", lambda cmd, **more: runs.append(cmd) or real(cmd, **more))
        first = compose.x_gravitee(text, self.here)
        first["name"] = "changed by its caller"
        self.assertEqual(compose.x_gravitee(text, self.here), {"kind": "addon", "name": "Once"})
        self.assertEqual(len(runs), 1)
        self.write("addons/read/compose.yml", text.replace("Once", "Read"))
        compose.raw("addons/read/compose.yml")
        self.assertEqual(compose.x_gravitee(text.replace("Once", "Read"), self.root / "addons" / "read"), {"kind": "addon", "name": "Read"})
        self.assertEqual(len(runs), 2, "raw's reading answers")
