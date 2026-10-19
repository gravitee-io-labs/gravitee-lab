"""What the Makefile says when something is missing or wrong, and what its helpers do, run in a scratch folder with
`docker` and `python` programs that are fakes: no real Docker or Python environment is touched."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# `docker info` fails the way FAKE_DAEMON says; `docker image inspect` succeeds when FAKE_IMAGE is present; Compose is
# version 5.6.0. Every call is logged, to FAKE_DOCKER_LOG.
DOCKER = """#!/bin/sh
echo "$*" >> "$FAKE_DOCKER_LOG"
case "$1 $2" in
"image inspect") [ "$FAKE_IMAGE" = present ]; exit;;
"compose version") echo 5.6.0; exit;;
esac
if [ "$1" = info ]; then
    case "$FAKE_DAEMON" in
    denied) echo "permission denied while trying to connect to the docker API at unix:///var/run/docker.sock" >&2
        exit 1;;
    down) echo "failed to connect to the docker API at unix:///x.sock: no such file or directory" >&2; exit 1;;
    esac
fi
exit 0
"""

# What `python -m venv .venv` makes with the interpreter below: a python that says its version and whether it is 3.10
VENV_PYTHON = """#!/bin/sh
case "$1" in
-V) echo "Python {version}";;
-c) exit {too_old};;
esac
"""

# An interpreter that makes a venv (a copy of venv-python next to it) and does nothing else
INTERPRETER = """#!/bin/sh
[ "$1" = -m ] && [ "$2" = venv ] || exit 1
mkdir -p "$3/bin" && cp "$(dirname "$0")/venv-python" "$3/bin/python"
"""


@unittest.skipUnless(shutil.which("make"), "make is not installed")
class MakefileTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        shutil.copy(ROOT / "Makefile", self.folder)
        (self.folder / "docker-compose.yml").write_text("name: gravitee\n")
        self.bin = self.folder / "bin"
        self.bin.mkdir()
        self.log = self.folder / "docker-calls.txt"
        self.program("docker", DOCKER)

    def program(self, name, text):
        path = self.bin / name
        path.write_text(text)
        path.chmod(0o755)
        return path

    def make(self, *arguments, **environment):
        """(exit code, what it printed) of make with these arguments, in the scratch folder. The environment is the
        caller's without what would change the Makefile's behavior, or make's own words when this runs from make."""
        env = {name: value for name, value in os.environ.items()
               if name not in ("PYTHON", "PYTHON3", "COMPOSE_FILE", "MAKEFLAGS", "MFLAGS", "MAKELEVEL", "NO_COLOR")}
        env.update(PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", FAKE_DOCKER_LOG=str(self.log), **environment)
        done = subprocess.run(["make", *arguments], cwd=self.folder, env=env, capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def docker_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    # make help

    def test_help_lists_init_in_the_section_that_runs_the_lab(self):
        _, out = self.make("help")
        lines = out.splitlines()
        row = next(line for line in lines if line.split()[:1] == ["init"])
        self.assertIn("Create what a fresh clone lacks: .env, demo certificates, plugin folders, .lab/on.yml", row)
        self.assertLess(lines.index("Run the lab"), lines.index(row))
        self.assertLess(lines.index(row), lines.index("Work on the lab"))

    def test_help_lists_every_target_that_says_what_it_does_also_one_with_a_digit_in_its_name(self):
        _, out = self.make("help")
        row = next(line for line in out.splitlines() if line.split()[:1] == ["e2e"])
        self.assertIn("(ID=KIND:NAME, TESTED=0)", row)
        lines = out.splitlines()
        self.assertLess(lines.index("Work on the lab"), lines.index(row))

    # make e2e: it names the item it starts, before it asks anything of Docker's server

    def test_e2e_without_an_item_says_what_to_give_and_stops_before_starting_anything(self):
        self.program("python3", "#!/bin/sh\nexit 0\n")
        code, out = self.make("e2e", "-o", "docker-up", "-o", "init", PYTHON="python3")
        self.assertEqual(2, code)
        self.assertIn("Name the item: make e2e ID=KIND:NAME", out)

    # make init: what a fresh clone lacks, with or without the add-ons whose folders it also prepares

    def clone(self, *files):
        """What `make init` reads in a fresh clone: the template of .env, the demo certificates (already made), a
        python and an openssl that are fakes; and these files, each with the text that makes it do something."""
        (self.folder / ".env.example").write_text("GRAVITEE_LICENSE=\n")
        (self.folder / "certs" / "mtls").mkdir(parents=True)
        (self.folder / "certs" / "mtls" / "server.p12").write_text("")
        self.program("python3", "#!/bin/sh\nexit 0\n")
        self.program("openssl", "#!/bin/sh\nexit 0\n")
        for name in files:
            path = self.folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("touch kafka-certificates\n" if name.endswith(".sh") else "")

    def test_init_in_a_clone_with_no_add_on_makes_what_the_lab_needs_and_names_none(self):
        self.clone()
        code, out = self.make("init", PYTHON="python3")
        self.assertEqual((0, "Created .env from .env.example\n"), (code, out))
        env = (self.folder / ".env").read_text()
        self.assertIn("COMPOSE_FILE=docker-compose.yml:.lab/on.yml", env)
        self.assertEqual(0o600, (self.folder / ".env").stat().st_mode & 0o777)
        self.assertTrue((self.folder / ".lab" / "on.yml").is_file())
        self.assertEqual(["am", "apim"], sorted(path.name for path in (self.folder / "plugins").iterdir()))
        self.assertFalse((self.folder / "addons").exists(), "init made a folder of an add-on that is not there")
        self.assertEqual((0, ""), self.make("init", PYTHON="python3"))

    def test_init_makes_nothing_for_the_add_ons_that_are_there(self):
        # An add-on prepares what it needs when it starts (the Kafka certificates are a service of its own): init does not
        self.clone("addons/kafka/certs.sh")
        (self.folder / "addons" / "federation-alpha").mkdir()
        code, out = self.make("init", PYTHON="python3")
        self.assertEqual(0, code, out)
        self.assertFalse((self.folder / "kafka-certificates").exists())
        self.assertEqual([], sorted((self.folder / "addons" / "federation-alpha").iterdir()))
        self.assertEqual([], sorted(path.name for path in (self.folder / "addons" / "kafka").iterdir() if path.name != "certs.sh"))

    # an update from the layout before catalogs: the guard of E3.10

    GUARDED = ("start", "restart", "ps", "logs", "config", "pull", "mongo", "all")
    NOT_GUARDED = ("stop", "down", "init", "catalogs")
    GUARD = "This Lab was updated: run make update to move your settings."

    def legacy(self, line="  - addons/kafka/compose.yml"):
        (self.folder / ".lab").mkdir(exist_ok=True)
        (self.folder / ".lab" / "on.yml").write_text(f"include:\n{line}\n")

    def test_the_targets_that_read_the_on_file_stop_when_it_names_the_old_folders(self):
        for line in ("  - addons/kafka/compose.yml", '  - "setups/events.yml"', "- playgrounds/shop/compose.yml"):
            self.legacy(line)
            for target in self.GUARDED:
                with self.subTest(target=target, line=line):
                    code, out = self.make(target)
                    self.assertNotEqual(0, code, out)
                    self.assertIn(self.GUARD, out)
                    self.assertEqual([], [call for call in self.docker_calls() if "compose" in call and target in call.split()])

    def test_the_targets_that_use_the_project_name_or_move_the_settings_do_not_stop_there(self):
        self.legacy()
        (self.folder / ".env.example").write_text("GRAVITEE_LICENSE=\n")
        for target in self.NOT_GUARDED:
            with self.subTest(target=target):
                _, out = self.make(target)
                self.assertNotIn(self.GUARD, out)

    def test_an_on_file_that_names_catalog_items_or_nothing_is_no_legacy(self):
        for text in ("include:\n  - .lab/catalogs/hub/addons/kafka/compose.yml\n", "", "include:\n  - docker-compose.yml\n"):
            (self.folder / ".lab").mkdir(exist_ok=True)
            (self.folder / ".lab" / "on.yml").write_text(text)
            with self.subTest(text=text):
                self.assertEqual((0, ""), self.make("legacy-ok"))

    def test_update_pulls_fast_forward_only_and_then_downloads_the_catalogs(self):
        code, out = self.make("-n", "update")
        self.assertEqual(0, code, out)
        self.assertLess(out.index("git pull --ff-only"), out.index("catalogs"))

    def test_reset_empties_the_records_of_the_playgrounds_of_every_catalog(self):
        self.assertIn(".lab/catalogs/*/playgrounds/*/.state/created.json", (self.folder / "Makefile").read_text())

    # docker is running, or is not

    def test_docker_that_runs_passes(self):
        code, out = self.make("docker-running")
        self.assertEqual((0, ""), (code, out))

    def test_docker_that_does_not_run_says_so(self):
        code, out = self.make("docker-running", FAKE_DAEMON="down")
        self.assertNotEqual(0, code)
        self.assertIn("Docker is not running: start Docker Desktop (or the docker service), then try again", out)
        self.assertNotIn("docker group", out)

    def test_a_user_outside_the_docker_group_is_told_so(self):
        code, out = self.make("docker-running", FAKE_DAEMON="denied")
        self.assertNotEqual(0, code)
        self.assertIn("permission denied while trying to connect to the docker API", out)
        self.assertIn("Docker is running but your user cannot reach it: add yourself to the docker group "
                      "(sudo usermod -aG docker $USER), then log in again "
                      "(see Troubleshooting in docs/troubleshooting.md)", out)
        self.assertNotIn("Docker is not running", out)

    # make setup

    def interpreter(self, version, too_old):
        self.program("venv-python", VENV_PYTHON.format(version=version, too_old=1 if too_old else 0))
        return str(self.program("python-fake", INTERPRETER))

    def test_setup_removes_the_venv_it_made_with_a_python_too_old(self):
        code, out = self.make("setup", PYTHON3=self.interpreter("3.9.6", too_old=True))
        self.assertNotEqual(0, code)
        self.assertFalse((self.folder / ".venv").exists())
        self.assertIn("The seeder needs Python 3.10 or later, found: Python 3.9.6", out)
        self.assertIn("Run make setup again with a newer python3 (for example PYTHON3=python3.12 make setup)", out)

    def test_setup_leaves_a_venv_that_was_there(self):
        self.interpreter("3.9.6", too_old=True)
        (self.folder / ".venv" / "bin").mkdir(parents=True)
        shutil.copy(self.bin / "venv-python", self.folder / ".venv" / "bin" / "python")
        code, out = self.make("setup", PYTHON3=str(self.bin / "python-fake"))
        self.assertNotEqual(0, code)
        self.assertTrue((self.folder / ".venv" / "bin" / "python").exists())
        self.assertIn(".venv has Python 3.9.6", out)
        self.assertIn("Remove .venv, then run make setup again with a newer python3 "
                      "(for example PYTHON3=python3.12 make setup)", out)

    def test_setup_with_a_python_that_cannot_make_a_venv_leaves_nothing(self):
        broken = self.program("python-broken", "#!/bin/sh\nmkdir -p \"$3\"; exit 1\n")
        code, out = self.make("setup", PYTHON3=str(broken))
        self.assertNotEqual(0, code)
        self.assertFalse((self.folder / ".venv").exists())
        self.assertIn("Cannot make .venv", out)

    def test_setup_with_a_python_new_enough_installs(self):
        code, out = self.make("setup", PYTHON3=self.interpreter("3.12.1", too_old=False))
        self.assertEqual(0, code, out)
        self.assertTrue((self.folder / ".venv").is_dir())
        self.assertIn("Ready:", out)

