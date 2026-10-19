"""The environment of a command that reads a file written by someone else (the catalog standard, 5.5): the variables it
may know of the shell and no others, a `HOME` that is an empty temporary folder, and a `DOCKER_CONFIG` of its own that
holds an empty `config.json` and a link to the Compose plugin of the real environment, so that `docker compose` is still
found. For `config` only (`scrubbed_environment`); a build, an `up` and a smoke get the same variables with the real
`HOME` and `DOCKER_CONFIG` (`build_environment`). A Docker CLI is played here by a script that looks for its plugin
where the real one does, in its order, and the real CLI and the Compose that is installed are tried when there are
some: a visible skip where they are not."""

import functools
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from lab_ui.docker import compose

from .. import Case
from ..fixtures import need_compose

# The variables a command may know of the shell, and the two flags that keep Compose from fetching an include
ALLOWED = {"PATH", "LANG", "LC_ALL", "TMPDIR", "DOCKER_HOST"}
FLAGS = {"COMPOSE_EXPERIMENTAL_GIT_REMOTE": "false", "COMPOSE_EXPERIMENTAL_OCI_REMOTE": "false"}
# What a runner or a developer's shell holds and a command that reads a stranger's files must never see; the values are
# looked for in what the environment hands over
SECRETS = {
    "ACTIONS_RUNTIME_TOKEN": "runtime-token-value", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "id-token-value",
    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://actions.example.test/id", "ACTIONS_CACHE_URL": "https://cache.example.test/",
    "GITHUB_TOKEN": "github-token-value", "GITHUB_ENV": "/home/runner/env-file", "GH_TOKEN": "gh-token-value",
    "NPM_TOKEN": "npm-token-value", "SOME_API_TOKEN": "api-token-value", "SSH_AUTH_SOCK": "/tmp/ssh-agent.sock",
    "AWS_SECRET_ACCESS_KEY": "aws-secret-value", "GEMINI_API_KEY": "gemini-key-value", "USER": "somebody-else",
    "COMPOSE_FILE": "other.yml", "COMPOSE_PROFILES": "everything", "COMPOSE_ENV_FILES": "/home/me/secrets.env",
    "COMPOSE_PROJECT_NAME": "mine-for-test", "DOCKER_CONTEXT": "remote-context", "DOCKER_TLS_VERIFY": "tls-verify-value",
    "BUILDX_BUILDER": "builder-for-test",
}

# What the Docker CLI does with `docker compose ...`, in its order (docker/cli, cli-plugins/manager; measured with Docker 29):
# the folders that the `config.json` of its configuration folder lists (`cliPluginsExtraDirs`), then `cli-plugins` of that
# folder (`$DOCKER_CONFIG`, else `~/.docker`: never both), then the folders of the system. @PYTHON@ @EXTRAS@: the program
# that reads the list; @SYSTEM@: the folders of the system, quoted
FAKE_DOCKER = """#!/bin/sh
if [ "$1" != compose ]; then echo "docker: '$1' is not a docker command" >&2; exit 1; fi
shift
config="${DOCKER_CONFIG:-$HOME/.docker}"
folders=$(@PYTHON@ -I @EXTRAS@ "$config"; echo "$config/cli-plugins"; printf '%s\\n' @SYSTEM@)
found=
while IFS= read -r dir; do
  if [ -z "$found" ] && [ -n "$dir" ] && [ -x "$dir/docker-compose" ]; then found="$dir/docker-compose"; fi
done <<EOF
$folders
EOF
if [ -n "$found" ]; then exec "$found" "$@"; fi
echo "docker: unknown command: docker compose" >&2
exit 1
"""
EXTRAS = """import json
import sys

try:
    with open(sys.argv[1] + "/config.json", encoding="utf-8") as handle:
        listed = json.load(handle).get("cliPluginsExtraDirs") or []
except (OSError, ValueError, AttributeError):
    listed = []
print("\\n".join(folder for folder in listed if isinstance(folder, str)))
"""
# The plugin: it says which one it is, then what it was given
FAKE_PLUGIN = """#!/bin/sh
echo "PLUGIN %s"
env
"""


def parsed(output):
    """What the fake plugin printed: (which plugin, {variable: value})."""
    which, found = None, {}
    for line in output.splitlines():
        if line.startswith("PLUGIN "):
            which = line[len("PLUGIN "):]
        elif "=" in line:
            name, _, value = line.partition("=")
            found[name] = value
    return which, found


class Layout(Case):
    """A machine whose Docker is laid out in a folder of the test: a home, the folders of the system and Docker Desktop's,
    a `docker` that looks for its plugin as the CLI does, and a shell that holds secrets."""

    def setUp(self):
        self.folder()
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.system = [str(self.root / "system" / name) for name in ("local-lib", "local-libexec", "lib", "libexec")]
        self.desktop = [str(self.root / "Applications/Docker.app/Contents/Resources/cli-plugins"),
                        "~/Applications/Docker.app/Contents/Resources/cli-plugins"]
        self.patch(compose, "SYSTEM_PLUGINS", tuple(self.system))
        self.patch(compose, "DESKTOP_PLUGINS", tuple(self.desktop))
        # `docker` plays the CLI of this layout, whose system folders are the ones above (the real CLI has them built in)
        (self.bin / "cli-extras.py").write_text(EXTRAS, encoding="utf-8")
        (self.bin / "docker").write_text(
            FAKE_DOCKER.replace("@PYTHON@", shlex.quote(sys.executable))
            .replace("@EXTRAS@", shlex.quote(str(self.bin / "cli-extras.py")))
            .replace("@SYSTEM@", " ".join(shlex.quote(folder) for folder in self.system)), encoding="utf-8")
        (self.bin / "docker").chmod(0o755)
        self.shell({})

    def shell(self, extra, **more):
        """The real environment of the test until it ends: the secrets, a PATH that finds the fake `docker` and a home
        that is the test's (and not the person's), with `extra` over it. `more`: variables to leave out."""
        found = {"PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}", "HOME": str(self.home),
                 "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
                 "DOCKER_HOST": "unix:///var/run/test.sock", **SECRETS, **extra}
        for name in more:
            found.pop(name, None)
        if getattr(self, "_shell", None):
            self._shell.stop()
        self._shell = mock.patch.dict(os.environ, found, clear=True)
        self._shell.start()
        self.addCleanup(self._shell.stop)

    def plugin(self, folder, which=None):
        """A Compose plugin in this folder (made, with the folders above it): answers its path."""
        folder = Path(os.path.expanduser(str(folder)))
        folder.mkdir(parents=True, exist_ok=True)
        file = folder / "docker-compose"
        file.write_text(FAKE_PLUGIN % (which or str(folder)), encoding="utf-8")
        file.chmod(0o755)
        return str(file)

    def call(self, environment, *arguments):
        """`docker compose ...` of the layout, run with this environment: (which plugin answered, the variables it saw,
        the code, what the CLI said)."""
        done = subprocess.run(["docker", "compose", *arguments], env=environment, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=30, cwd=self.root)
        which, seen = parsed(done.stdout)
        return which, seen, done.returncode, done.stderr.strip()

    def fresh_scratch(self):
        """The folder of this run made for the first time, as when a Lab UI has just started, with a `mkdtemp` that takes
        its time so that the threads which ask together are all in it: answers the list of the folders made."""
        self.patch(compose, "_scratch_folder", functools.lru_cache(maxsize=None)(compose._scratch_folder.__wrapped__))
        self.addCleanup(lambda: shutil.rmtree(compose.scratch(), True))
        made, real = [], tempfile.mkdtemp

        def slow(*args, **kwargs):
            made.append(args)
            time.sleep(0.05)
            return real(*args, **kwargs)
        self.patch(tempfile, "mkdtemp", slow)
        return made

    @staticmethod
    def together(count, work):
        """What `work()` answers in each of `count` threads that start it at the same moment; an error in one is raised."""
        barrier = threading.Barrier(count)

        def start():
            barrier.wait(10)
            return work()
        with ThreadPoolExecutor(count) as pool:
            return [future.result() for future in [pool.submit(start) for _ in range(count)]]


class Plugin(Layout):
    """Where the Compose plugin is looked for, in the order the Docker CLI looks, and that the link finds it for `config`."""

    FOLDERS = (   # (the layout, the folder where the plugin lives alone, whether the Docker CLI looks there)
        ("the configuration folder named by DOCKER_CONFIG", lambda self: str(self.root / "config/cli-plugins"), True),
        ("the cli-plugins folder of the home", lambda self: str(self.home / ".docker/cli-plugins"), True),
        ("a folder that the configuration named by DOCKER_CONFIG lists", lambda self: str(self.root / "extra/cli-plugins"), True),
        ("a folder that the configuration of the home lists", lambda self: str(self.root / "extra/cli-plugins"), True),
        ("/usr/local/lib/docker/cli-plugins", lambda self: self.system[0], True),
        ("/usr/local/libexec/docker/cli-plugins", lambda self: self.system[1], True),
        ("/usr/lib/docker/cli-plugins", lambda self: self.system[2], True),
        ("/usr/libexec/docker/cli-plugins", lambda self: self.system[3], True),
        # (Docker Desktop's CLI reaches its Compose through the links in the person's folder; found by the folder, the CLI
        # of this layout does not look there: the Lab's last resort)
        ("Docker Desktop's Resources/cli-plugins", lambda self: self.desktop[0], False),
        ("Docker Desktop's, in the person's own Applications",
         lambda self: str(self.home / "Applications/Docker.app/Contents/Resources/cli-plugins"), False),
    )

    def lay(self, name):
        """The environment of the layout where the plugin is in the folder `name` alone."""
        extra = {}
        if name.startswith("the configuration folder"):
            extra["DOCKER_CONFIG"] = str(self.root / "config")
        if name.startswith("a folder that the configuration named"):
            extra["DOCKER_CONFIG"] = self.configured(self.root / "config", self.root / "extra/cli-plugins")
        if name.startswith("a folder that the configuration of the home"):
            self.configured(self.home / ".docker", self.root / "extra/cli-plugins")
        self.shell(extra)

    def configured(self, config, *listed):
        """A Docker configuration folder whose `config.json` lists these folders for plugins: answers its path."""
        config.mkdir(parents=True, exist_ok=True)
        (config / "config.json").write_text(json.dumps({"cliPluginsExtraDirs": [str(folder) for folder in listed]}),
                                            encoding="utf-8")
        return str(config)

    def test_each_folder_of_the_cli_finds_the_plugin_and_config_runs_it(self):
        for name, where, cli in self.FOLDERS:
            with self.subTest(name):
                self.lay(name)
                folder = where(self)
                made = self.plugin(folder, which=name)
                self.assertEqual(compose.plugin(), made)
                environment = compose.scrubbed_environment()
                link = Path(environment["DOCKER_CONFIG"]) / "cli-plugins" / "docker-compose"
                self.assertTrue(link.is_symlink(), "the plugin is linked into the folder the command reads")
                self.assertEqual(os.readlink(link), made)
                which, seen, code, said = self.call(environment, "config")
                self.assertEqual((which, code, said), (name, 0, ""), "docker compose finds it")
                self.assertEqual(seen["HOME"], environment["HOME"])
                # The plugin the Lab links is the one `docker compose` runs on this machine, with the real HOME and
                # DOCKER_CONFIG (the build environment): it is not another that only `config` would find
                built = self.call(compose.build_environment(), "build")
                self.assertEqual((built[0], built[2]), (name, 0) if cli else (None, 1), "the CLI of the layout")
                shutil.rmtree(folder)

    def test_without_the_link_two_empty_folders_hide_a_plugin_of_the_person(self):
        # What the link is for (measured with Docker Desktop's Compose 5.6.0: `docker: unknown command: docker compose`)
        self.plugin(self.home / ".docker/cli-plugins", which="home")
        empty = self.root / "empty"
        empty.mkdir()
        _, _, code, said = self.call({**compose.scrubbed_environment(), "HOME": str(empty), "DOCKER_CONFIG": str(empty)},
                                     "config")
        self.assertEqual((code, said), (1, "docker: unknown command: docker compose"))
        which, _, code, _ = self.call(compose.scrubbed_environment(), "config")
        self.assertEqual((which, code), ("home", 0))

    def lays_in_order(self, order):
        """The plugin laid in each of these folders one after the other, the last of the order first in the CLI's: after each,
        it is the Lab's choice (plugin(), and the one `config` runs) and, where the CLI looks, the CLI's (the build
        environment, which has the real HOME and DOCKER_CONFIG) that are the one just laid."""
        for folder, which, cli in order:
            self.plugin(folder, which=which)
            self.assertEqual(Path(compose.plugin()).parent, Path(os.path.expanduser(folder)), which)
            self.assertEqual(self.call(compose.scrubbed_environment(), "config")[0], which)
            if cli:
                self.assertEqual(self.call(compose.build_environment(), "build")[0], which, f"{which}: the CLI")

    def test_the_first_folder_that_has_one_wins_in_the_order_of_the_cli(self):
        # With a DOCKER_CONFIG: the folders its configuration lists, then its cli-plugins, then the system's; the
        # cli-plugins of the home are not looked in (measured with the real CLI: Docker 29)
        config = self.root / "config"
        self.shell({"DOCKER_CONFIG": self.configured(config, self.root / "extra")})
        self.lays_in_order([
            (self.desktop[0], "desktop", False), (self.system[3], "libexec", True), (self.system[2], "lib", True),
            (self.system[1], "local-libexec", True), (self.system[0], "local-lib", True),
            (str(config / "cli-plugins"), "config", True), (str(self.root / "extra"), "extra", True)])

    def test_the_folders_that_the_configuration_lists_come_before_its_cli_plugins_and_the_homes_come_after(self):
        # Without a DOCKER_CONFIG the configuration folder is `~/.docker`: the folders its config.json lists, then its
        # cli-plugins, then the system's
        self.configured(self.home / ".docker", self.root / "extra")
        self.lays_in_order([
            (self.desktop[0], "desktop", False), (self.system[3], "libexec", True), (self.system[0], "local-lib", True),
            (str(self.home / ".docker/cli-plugins"), "home", True), (str(self.root / "extra"), "extra", True)])

    def test_an_older_compose_in_the_home_loses_to_the_newer_one_in_the_folders_the_configuration_lists(self):
        # The case of the Lab's review: the CLI runs the plugin of cliPluginsExtraDirs (a Compose 5 of a package manager)
        # and not the older one in ~/.docker/cli-plugins, and the plugin the Lab links is the CLI's
        self.configured(self.home / ".docker", self.root / "brew")
        self.plugin(self.home / ".docker/cli-plugins", which="older, in the home")
        self.plugin(self.root / "brew", which="newer, in the extra folders")
        self.assertEqual(compose.plugin(), str(self.root / "brew/docker-compose"))
        self.assertEqual(self.call(compose.scrubbed_environment(), "config")[0], "newer, in the extra folders")
        self.assertEqual(self.call(compose.build_environment(), "build")[0], "newer, in the extra folders")

    def test_a_docker_config_that_is_set_hides_the_cli_plugins_and_the_configuration_of_the_home(self):
        # The CLI looks in `$DOCKER_CONFIG/cli-plugins` and not in `~/.docker/cli-plugins` when the shell sets it, and reads
        # the list of folders from the configuration there: a Compose that only the home holds is not found by either
        # `docker compose`, so the Lab does not link it (a `config` that passes where every build fails would be a lie)
        self.plugin(self.home / ".docker/cli-plugins", which="home")
        self.configured(self.home / ".docker", self.root / "extra")
        self.plugin(self.root / "extra", which="extra, listed by the home")
        other = self.root / "other"
        other.mkdir()
        self.shell({"DOCKER_CONFIG": str(other)})
        self.assertIsNone(compose.plugin())
        built = self.call(compose.build_environment(), "build")
        self.assertEqual((built[0], built[2]), (None, 1), "the CLI finds none either")
        environment = compose.scrubbed_environment()
        self.assertEqual(os.listdir(Path(environment["DOCKER_CONFIG"]) / "cli-plugins"), [], "so it links none")
        self.assertEqual(self.call(environment, "config")[2:], (1, "docker: unknown command: docker compose"))
        # what the system holds is found whatever DOCKER_CONFIG says
        self.plugin(self.system[1], which="system")
        self.assertEqual(Path(compose.plugin()).parent, Path(self.system[1]))
        self.assertEqual(self.call(compose.build_environment(), "build")[0], "system")

    def test_an_empty_docker_config_is_none_as_it_is_for_the_cli(self):
        self.plugin(self.home / ".docker/cli-plugins", which="home")
        self.shell({"DOCKER_CONFIG": ""})
        self.assertEqual(Path(compose.plugin()).parent, self.home / ".docker/cli-plugins")
        self.assertEqual(self.call(compose.build_environment(), "build")[0], "home")

    def test_a_docker_config_that_is_not_absolute_is_no_configuration_folder_and_the_home_is_not_its_stand_in(self):
        # For the CLI it is the folder the command runs in, which is the catalog's: never searched, and not replaced by
        # the home (that would link a plugin that the CLI does not run)
        self.plugin(self.home / ".docker/cli-plugins", which="home")
        self.shell({"DOCKER_CONFIG": "relative/config"})
        self.assertIsNone(compose.plugin())
        self.plugin(self.system[2], which="system")
        self.assertEqual(Path(compose.plugin()).parent, Path(self.system[2]))

    def test_a_plugin_that_the_runner_has_older_in_the_system_loses_to_the_one_installed_for_the_user(self):
        # A runner has an older Compose in /usr/libexec, and the action of the Lab installs Compose 5 for the user
        self.plugin(self.system[3], which="older, of the runner")
        self.plugin(self.home / ".docker/cli-plugins", which="Compose 5, of the action")
        self.assertEqual(self.call(compose.scrubbed_environment(), "config")[0], "Compose 5, of the action")

    def test_what_is_not_a_plugin_is_passed_over(self):
        self.plugin(self.system[1], which="the good one")
        user = self.home / ".docker/cli-plugins"
        user.mkdir(parents=True)
        (user / "docker-compose").symlink_to(self.root / "nowhere")   # a link to nothing
        self.assertEqual(compose.plugin(), str(Path(self.system[1]) / "docker-compose"))
        (user / "docker-compose").unlink()
        (user / "docker-compose").write_text("#!/bin/sh\n", encoding="utf-8")   # not executable
        self.assertEqual(compose.plugin(), str(Path(self.system[1]) / "docker-compose"))
        (user / "docker-compose").unlink()
        (user / "docker-compose").mkdir()   # a folder
        self.assertEqual(compose.plugin(), str(Path(self.system[1]) / "docker-compose"))

    def test_a_relative_folder_is_never_searched_for_it_would_be_the_catalogs(self):
        # The command runs in the folder of the catalog that is judged: a relative name would find a plugin that its
        # authors wrote. DOCKER_CONFIG, the Docker configuration's list and HOME are the person's, but a relative one is none
        work = self.root / "catalog"
        planted = self.plugin(work / "cli-plugins", which="planted")
        config = self.root / "config"
        config.mkdir()
        (config / "config.json").write_text(json.dumps({"cliPluginsExtraDirs": ["cli-plugins", "catalog/cli-plugins", 7, None]}),
                                            encoding="utf-8")
        self.shell({"DOCKER_CONFIG": "catalog", "HOME": "catalog"})
        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        self.assertIsNone(compose.plugin(), planted)
        self.shell({"DOCKER_CONFIG": str(config)})
        self.assertIsNone(compose.plugin(), planted)

    def test_a_machine_without_compose_is_said_so_and_the_environment_is_still_scrubbed(self):
        self.assertIsNone(compose.plugin())
        environment = compose.scrubbed_environment()
        folder = Path(environment["DOCKER_CONFIG"])
        self.assertEqual(sorted(path.name for path in (folder / "cli-plugins").iterdir()), [], "no link to nothing")
        which, _, code, said = self.call(environment, "config")
        self.assertEqual((which, code, said), (None, 1, "docker: unknown command: docker compose"))
        self.assertEqual(set(environment), ALLOWED | {"HOME", "DOCKER_CONFIG"} | set(FLAGS))

    def test_a_plugin_that_is_installed_later_or_removed_is_followed(self):
        first = Path(compose.scrubbed_environment()["DOCKER_CONFIG"]) / "cli-plugins" / "docker-compose"
        self.assertFalse(os.path.lexists(first))
        made = self.plugin(self.system[2])
        compose.scrubbed_environment()
        self.assertEqual(os.readlink(first), made)
        moved = self.plugin(self.home / ".docker/cli-plugins")
        compose.scrubbed_environment()
        self.assertEqual(os.readlink(first), moved)
        shutil.rmtree(self.home / ".docker")
        shutil.rmtree(self.system[2])
        compose.scrubbed_environment()
        self.assertFalse(os.path.lexists(first))

    def test_the_configuration_folder_is_empty_and_the_real_one_is_never_copied(self):
        real = self.home / ".docker"
        real.mkdir()
        (real / "config.json").write_text(json.dumps({"auths": {"registry.example.test": {"auth": "SECRETAUTH"}},
                                                      "credsStore": "osxkeychain", "currentContext": "remote"}),
                                          encoding="utf-8")
        (real / "daemon.json").write_text('{"token": "SECRETDAEMON"}', encoding="utf-8")
        self.plugin(real / "cli-plugins")
        folder = Path(compose.scrubbed_environment()["DOCKER_CONFIG"])
        self.assertEqual(json.loads((folder / "config.json").read_text(encoding="utf-8")), {})
        self.assertEqual(sorted(path.name for path in folder.iterdir()), ["cli-plugins", "config.json"])
        everything = "".join(path.read_text(encoding="utf-8", errors="ignore") for path in folder.rglob("*")
                             if path.is_file() and not path.is_symlink())
        self.assertNotIn("SECRET", everything)

    def test_the_home_is_an_empty_temporary_folder_of_this_run_and_private(self):
        environment = compose.scrubbed_environment()
        home = Path(environment["HOME"])
        self.assertNotEqual(home, self.home)
        self.assertTrue(home.is_dir())
        self.assertEqual(list(home.iterdir()), [])
        self.assertEqual(home.parent.parent, compose.scratch(), "inside the folder of this run, which is removed on exit")
        for folder in (home, Path(environment["DOCKER_CONFIG"]), compose.scratch()):
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700, folder)

    def test_the_folders_are_the_same_from_call_to_call_and_made_again_when_the_system_clears_them(self):
        first = compose.scrubbed_environment()
        self.assertEqual(compose.scrubbed_environment(), first, "one pair of folders for the run, not one for each call")
        shutil.rmtree(compose.scratch())
        again = compose.scrubbed_environment()
        self.assertTrue(Path(again["HOME"]).is_dir())
        self.assertEqual(json.loads((Path(again["DOCKER_CONFIG"]) / "config.json").read_text(encoding="utf-8")), {})

    def test_threads_that_ask_for_the_scratch_folder_together_the_first_time_get_one_folder(self):
        # (the check resolves the units of a catalog with eight workers: the first calls are at the same moment)
        made = self.fresh_scratch()
        found = self.together(8, compose.scratch)
        self.assertEqual((len(made), len(set(found))), (1, 1), "not a folder for each thread")
        self.assertEqual(compose.scratch(), found[0])

    def test_threads_that_ask_for_the_environment_together_the_first_time_get_the_same_home_and_configuration(self):
        self.plugin(self.system[0], which="system")
        made = self.fresh_scratch()
        answers = self.together(8, compose.scrubbed_environment)
        self.assertEqual(len(made), 1, "one folder of the run")
        self.assertEqual(len({json.dumps(environment, sort_keys=True) for environment in answers}), 1)
        link = Path(answers[0]["DOCKER_CONFIG"]) / "cli-plugins" / "docker-compose"
        self.assertEqual(os.path.realpath(link), os.path.realpath(Path(self.system[0]) / "docker-compose"))

    def test_commands_at_the_same_time_get_the_same_folders_and_a_link_that_is_never_half_made(self):
        self.plugin(self.system[0], which="system")

        def ask(_):
            environment = compose.scrubbed_environment()
            link = Path(environment["DOCKER_CONFIG"]) / "cli-plugins" / "docker-compose"
            return environment, os.path.realpath(link)
        with ThreadPoolExecutor(8) as pool:
            answers = list(pool.map(ask, range(8 * 40)))   # an error in a thread is raised here
        self.assertEqual(len({json.dumps(environment, sort_keys=True) for environment, _ in answers}), 1)
        self.assertEqual({found for _, found in answers}, {os.path.realpath(Path(self.system[0]) / "docker-compose")})

    def test_the_configuration_file_is_private(self):
        settings = Path(compose.scrubbed_environment()["DOCKER_CONFIG"]) / "config.json"
        self.assertEqual(stat.S_IMODE(settings.stat().st_mode), 0o600)

    def test_a_configuration_that_was_written_in_is_made_empty_again(self):
        self.plugin(self.system[0])
        folder = Path(compose.scrubbed_environment()["DOCKER_CONFIG"])
        (folder / "config.json").write_text('{"auths": {"x": {"auth": "y"}}}', encoding="utf-8")
        (folder / "cli-plugins" / "docker-compose").unlink()
        compose.scrubbed_environment()
        self.assertEqual(json.loads((folder / "config.json").read_text(encoding="utf-8")), {})
        self.assertTrue((folder / "cli-plugins" / "docker-compose").is_symlink())


# What a Docker CLI that has no Compose plugin printed (measured: Docker 29, in a configuration folder without it) to the
# Lab's own command line, `docker compose --progress plain ...`, which is not what it prints to `docker compose version`
NO_PLUGIN_FLAG = ("unknown flag: --progress\n\nUsage:  docker [OPTIONS] COMMAND [ARG...]\n\n"
                  "Run 'docker --help' for more information\n")
NO_PLUGIN_SHORT = ("unknown shorthand flag: 'f' in -f\n\nUsage:  docker [OPTIONS] COMMAND [ARG...]\n\n"
                   "Run 'docker --help' for more information\n")


class Missing(Case):
    """What a machine without Compose prints, said as that, and what is not it."""

    def test_a_docker_without_the_plugin_and_no_docker_at_all_are_a_missing_compose(self):
        for said in ("docker: unknown command: docker compose", "docker: unknown command: docker compose\n\nRun 'docker --help'",
                     "docker: 'compose' is not a docker command.\nSee 'docker --help'",
                     "[Errno 2] No such file or directory: 'docker'",
                     "WARNING: Error loading config file: /x/config.json\ndocker: unknown command: docker compose\n"):
            with self.subTest(said):
                self.assertTrue(compose.missing(said))
                self.assertTrue(compose.missing(str(compose.ComposeError(said))))

    def test_the_usage_text_that_a_docker_without_the_plugin_gives_the_labs_own_command_line_is_a_missing_compose(self):
        # `docker compose --progress plain -f ... config`, which the Lab runs, is not `docker compose version`: a Docker
        # that has no plugin takes --progress, and -f, for its own flags
        for said in (NO_PLUGIN_FLAG, NO_PLUGIN_SHORT, NO_PLUGIN_FLAG.rstrip("\n"), "unknown flag: --progress"):
            with self.subTest(said):
                self.assertTrue(compose.missing(said))

    def test_a_file_that_compose_refuses_a_daemon_that_does_not_answer_and_nothing_are_not(self):
        for said in ("yaml: line 3: bad key", "services.a.image is required", "Cannot connect to the Docker daemon at unix:///x",
                     "docker: Error response from daemon: No such container: a", "No such file or directory: 'a.yml'",
                     "addons/gone/compose.yml: no such file or directory", "", None,
                     "Run 'docker --help' for more information", "Usage:  docker [OPTIONS] COMMAND [ARG...]",
                     "unknown flag: --pull", "unknown shorthand flag: 'x' in -x"):
            with self.subTest(said):
                self.assertFalse(compose.missing(said))

    def test_a_name_that_a_stranger_wrote_in_a_message_of_compose_does_not_make_a_missing_compose(self):
        # What Compose printed for these files (measured: Compose 5.6): the name is inside a sentence, newlines escaped
        for said in ('service "a" depends on undefined service "docker: unknown command: docker compose": invalid compose project',
                     "validating /x/a.yml: services additional properties 'unknown flag: --progress' not allowed",
                     "validating /x/a.yml: services additional properties 'x\\nunknown flag: --progress' not allowed",
                     "validating /x/a.yml: services.a additional properties \"docker: 'compose' is not a docker command\" not allowed",
                     "services.a.volumes: [Errno 2] No such file or directory: 'docker'"):
            with self.subTest(said):
                self.assertFalse(compose.missing(said))
                self.assertFalse(compose.missing(str(compose.ComposeError(said))))

    def test_only_a_line_break_of_the_cli_and_a_few_blanks_come_before_the_words(self):
        sign = "unknown flag: --progress"
        for said, expected in (("x\n" + sign, True), ("x\r\n" + sign, True), ("\n  \t" + sign, True), (" " * 64 + sign, True),
                               ("x\n" + " " * 64 + sign, True), (" " * 65 + sign, False), ("x\n" + " " * 65 + sign, False),
                               ("x " + sign, False), ("x\r" + sign, False), ("x\u2028" + sign, False), ("x\u0085" + sign, False),
                               ("x\x0b" + sign, False), ("x\\n" + sign, False)):
            with self.subTest(said):
                self.assertEqual(compose.missing(said), expected)

    def test_a_text_of_a_megabyte_is_read_in_less_than_a_second(self):
        # What a command prints is not bounded: Compose reads the files of a stranger
        mib = 1024 * 1024
        sign = "unknown flag: --progress"
        for name, said, expected in (
                ("blanks", " " * mib + "x", False), ("blanks, then the words", " " * mib + sign, False),
                ("line breaks", "\n" * mib, False), ("line breaks, then the words", "\n" * mib + sign, True),
                ("one line", "x" * (mib // 2) + sign + "y" * (mib // 2), False),
                ("the words in the middle of a line, again and again", ("x " + sign) * (mib // 26), False),
                ("the words in the middle of many lines", ("x " + sign + "\n") * (mib // 28), False),
                ("every phrase, in the middle of a line", "".join(f"x {one} " for one in compose._SIGNS) * (mib // 150), False),
                ("lines of blanks, then the words", "   \n" * (mib // 4) + sign, True)):
            with self.subTest(name):
                started = time.monotonic()
                self.assertEqual(compose.missing(said), expected)
                self.assertLess(time.monotonic() - started, 1)

    def test_it_is_what_a_run_that_finds_no_docker_says_and_not_what_another_missing_command_says(self):
        code, _, said = compose.run(["docker", "compose", "version"], environ={"PATH": "/nonexistent-folder-for-the-lab"})
        self.assertEqual(code, 127)
        self.assertTrue(compose.missing(said), said)
        code, _, said = compose.run(["no-such-command-for-the-lab", "compose"])
        self.assertEqual(code, 127)
        self.assertFalse(compose.missing(said), said)


class ConfigWithoutCompose(Case):
    """What `config_json` raises for a Docker that has no Compose, when it is given an environment (the CI's, a stranger's
    files): the exception alone says so, as the last line of the usage text does not."""

    def raised(self, ran, **more):
        with mock.patch.object(compose, "run", return_value=ran):
            with self.assertRaises(compose.ComposeError) as caught:
                compose.config_json(["-f", "a.yml"], **more)
        return str(caught.exception)

    def test_it_raises_a_message_that_missing_knows_for_the_usage_text_of_a_docker_without_the_plugin(self):
        for said in (NO_PLUGIN_FLAG, NO_PLUGIN_SHORT, "docker: unknown command: docker compose\n\nRun 'docker --help'"):
            with self.subTest(said):
                message = self.raised((125, "", said), environ={"PATH": "/usr/bin"})
                self.assertEqual(message, compose.NO_COMPOSE)
                self.assertTrue(compose.missing(message))

    def test_without_an_environment_the_message_is_what_it_was(self):
        # The Lab's own commands: the line a person has always read
        self.assertEqual(self.raised((125, "", NO_PLUGIN_FLAG)), "Run 'docker --help' for more information")

    def test_a_file_that_compose_refuses_is_still_its_last_line_and_not_a_missing_compose(self):
        said = "validating /x/a.yml: services.a.image must be a string\n"
        message = self.raised((1, "", said), environ={"PATH": "/usr/bin"})
        self.assertEqual(message, "validating /x/a.yml: services.a.image must be a string")
        self.assertFalse(compose.missing(message))


# A Compose plugin for the real Docker CLI: it answers the question the CLI asks of a plugin, then says which one it is
REAL_PLUGIN = """#!/bin/sh
if [ "$1" = docker-cli-plugin-metadata ]; then
  echo '{"SchemaVersion":"0.1.0","Vendor":"test","Version":"v9.9.9","ShortDescription":"a Compose of the test"}'
  exit 0
fi
echo "PLUGIN %s"
"""


class RealCli(Case):
    """The Docker CLI itself, where it is installed (a visible skip where it is not), with plugins that are scripts: where it
    looks for Compose, in what order, and what it prints when it has none. The layouts above play the CLI; this is what they
    were measured against."""

    def setUp(self):
        need_compose()
        self.folder()
        self.home = self.root / "home"
        self.home.mkdir()
        self.system = [folder for folder in compose.SYSTEM_PLUGINS if os.path.isfile(os.path.join(folder, compose.PLUGIN))]
        self.shell({})

    def shell(self, extra):
        """The environment of a person who has this test's home, and the real PATH, with `extra` over it."""
        found = {"PATH": os.environ["PATH"], "HOME": str(self.home), **extra}
        patched = mock.patch.dict(os.environ, found, clear=True)
        patched.start()
        self.addCleanup(patched.stop)

    def plugin(self, folder, which):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "docker-compose").write_text(REAL_PLUGIN % which, encoding="utf-8")
        (folder / "docker-compose").chmod(0o755)
        return folder / "docker-compose"

    def configured(self, config, *listed):
        config.mkdir(parents=True, exist_ok=True)
        (config / "config.json").write_text(json.dumps({"cliPluginsExtraDirs": [str(folder) for folder in listed]}),
                                            encoding="utf-8")
        return config

    def runs(self, *arguments):
        """(which plugin the real CLI ran, the code, what it said) for `docker compose ...` in the build environment."""
        done = subprocess.run(["docker", "compose", *arguments], env=compose.build_environment(), capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, timeout=60, cwd=self.root)
        found = [line[len("PLUGIN "):] for line in done.stdout.splitlines() if line.startswith("PLUGIN ")]
        return (found[0] if found else None), done.returncode, done.stderr

    def no_system_plugin(self):
        if self.system:
            self.skipTest(f"this machine has a Compose plugin in {self.system[0]}: the CLI can not be left without one")
        self.patch(compose, "SYSTEM_PLUGINS", ())
        self.patch(compose, "DESKTOP_PLUGINS", ())

    def test_the_plugin_the_lab_links_is_the_one_the_cli_runs_when_the_extra_folders_hold_another(self):
        # An older Compose in ~/.docker/cli-plugins and a Compose 5 in a folder of cliPluginsExtraDirs (a package manager's):
        # the CLI runs the second, and the Lab used to link the first
        self.configured(self.home / ".docker", self.root / "brew")
        self.plugin(self.home / ".docker/cli-plugins", "older")
        newer = self.plugin(self.root / "brew", "newer")
        self.assertEqual(self.runs("version")[0], "newer")
        self.assertEqual(compose.plugin(), str(newer))
        self.assertEqual(compose.run(["docker", "compose", "version"], environ=compose.scrubbed_environment())[1].split()[:2],
                         ["PLUGIN", "newer"])

    def test_with_a_docker_config_the_extra_folders_come_before_its_cli_plugins(self):
        config = self.configured(self.root / "config", self.root / "extra")
        self.plugin(config / "cli-plugins", "config")
        extra = self.plugin(self.root / "extra", "extra")
        self.shell({"DOCKER_CONFIG": str(config)})
        self.assertEqual(self.runs("version")[0], "extra")
        self.assertEqual(compose.plugin(), str(extra))
        extra.unlink()
        self.assertEqual(self.runs("version")[0], "config")
        self.assertEqual(compose.plugin(), str(config / "cli-plugins/docker-compose"))

    def test_a_docker_config_that_is_set_hides_the_home_for_the_cli_and_so_for_the_lab(self):
        self.no_system_plugin()
        self.configured(self.home / ".docker", self.root / "extra")
        self.plugin(self.home / ".docker/cli-plugins", "home")
        self.plugin(self.root / "extra", "extra, listed by the home")
        other = self.root / "other"
        other.mkdir()
        self.shell({"DOCKER_CONFIG": str(other)})
        which, code, said = self.runs("version")
        self.assertEqual((which, code, compose.missing(said)), (None, 1, True), said)
        self.assertIsNone(compose.plugin())

    def test_a_cli_without_the_plugin_is_a_missing_compose_for_each_command_the_lab_runs(self):
        self.no_system_plugin()
        empty = self.root / "empty"
        empty.mkdir()
        self.shell({"DOCKER_CONFIG": str(empty)})
        (self.root / "a.yml").write_text("services:\n  a:\n    image: busybox\n", encoding="utf-8")
        environment = compose.build_environment()
        for command in (["docker", "compose", "version"], [*compose.COMPOSE, "version"],
                        [*compose.COMPOSE, "-f", "a.yml", "config", "--format", "json"]):
            with self.subTest(command=command[2:]):
                code, _, said = compose.run(command, environ=environment, cwd=self.root)
                self.assertNotEqual(code, 0)
                self.assertTrue(compose.missing(said), said)
        with self.assertRaises(compose.ComposeError) as caught:
            compose.config_json(["-f", str(self.root / "a.yml")], environ=environment)
        self.assertTrue(compose.missing(str(caught.exception)), str(caught.exception))


class Scrubbed(Layout):
    """What a command that reads a stranger's files is told: the allow-list and nothing else."""

    def test_only_the_allowed_variables_reach_it_and_the_flags_are_off(self):
        environment = compose.scrubbed_environment()
        self.assertEqual(set(environment), ALLOWED | {"HOME", "DOCKER_CONFIG"} | set(FLAGS))
        for name, value in {**FLAGS, "PATH": os.environ["PATH"], "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                            "DOCKER_HOST": "unix:///var/run/test.sock"}.items():
            self.assertEqual(environment[name], value, name)

    def test_no_actions_variable_no_token_and_no_agent_socket(self):
        environment = compose.scrubbed_environment()
        for name in environment:
            self.assertFalse(name.startswith(("ACTIONS_", "GITHUB_", "GH_")) or name.endswith("_TOKEN"), name)
        self.assertNotIn("SSH_AUTH_SOCK", environment)

    def test_no_value_of_the_shell_is_in_it(self):
        text = "\n".join(compose.scrubbed_environment().values())
        for name, value in SECRETS.items():
            self.assertNotIn(value, text, name)

    def test_the_shells_compose_variables_are_not_carried(self):
        # They would change which files Compose reads, and the callers name them with -f
        environment = compose.scrubbed_environment()
        for name in ("COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_ENV_FILES", "COMPOSE_PROJECT_NAME", "DOCKER_CONTEXT",
                     "DOCKER_TLS_VERIFY", "BUILDX_BUILDER"):
            self.assertNotIn(name, environment)

    def test_a_remote_include_stays_off_whatever_the_shell_says(self):
        self.shell({"COMPOSE_EXPERIMENTAL_GIT_REMOTE": "true", "COMPOSE_EXPERIMENTAL_OCI_REMOTE": "true"})
        for build in (compose.scrubbed_environment, compose.build_environment):
            environment = build()
            self.assertEqual({name: environment[name] for name in FLAGS}, FLAGS, build.__name__)

    def test_a_variable_the_shell_lacks_is_not_made_up(self):
        self.shell({}, LANG=None, LC_ALL=None, TMPDIR=None, DOCKER_HOST=None)
        environment = compose.scrubbed_environment()
        self.assertEqual(set(environment), {"PATH", "HOME", "DOCKER_CONFIG"} | set(FLAGS))

    def test_the_real_home_and_docker_config_are_not_in_it(self):
        self.shell({"DOCKER_CONFIG": str(self.root / "real-config")})
        environment = compose.scrubbed_environment()
        self.assertNotEqual(environment["HOME"], str(self.home))
        self.assertNotEqual(environment["DOCKER_CONFIG"], str(self.root / "real-config"))

    def test_it_is_a_new_dictionary_each_time(self):
        first = compose.scrubbed_environment()
        first["HOME"] = "changed"
        self.assertNotEqual(compose.scrubbed_environment()["HOME"], "changed")

    def test_the_environment_of_the_lab_is_what_it_was(self):
        # The Lab runs its own commands with environment(): nothing here changes that
        self.patch(compose, "_waiting", return_value=False)
        self.shell({"COMPOSE_FILE": "other.yml", "SOME_API_TOKEN": "api-token-value"})
        found = compose.environment()
        self.assertEqual(found["SOME_API_TOKEN"], "api-token-value")
        self.assertEqual(found["HOME"], str(self.home))
        self.assertEqual(found["COMPOSE_FILE"], compose.COMPOSE_FILE)


class Build(Layout):
    """What a build, an `up`, a `run` and the smoke are told: the same variables, with the real `HOME` and `DOCKER_CONFIG`."""

    def test_the_real_home_and_docker_config_are_kept(self):
        self.shell({"DOCKER_CONFIG": str(self.root / "real-config")})
        environment = compose.build_environment()
        self.assertEqual(environment["HOME"], str(self.home))
        self.assertEqual(environment["DOCKER_CONFIG"], str(self.root / "real-config"))
        self.assertEqual(set(environment), ALLOWED | {"HOME", "DOCKER_CONFIG"} | set(FLAGS))

    def test_a_docker_config_the_shell_lacks_is_not_made_up(self):
        environment = compose.build_environment()
        self.assertNotIn("DOCKER_CONFIG", environment)
        self.assertEqual(environment["HOME"], str(self.home))

    RELATIVE = ("home", "./home", "../home", ".", "..", "~", "~/.docker", "$HOME/.docker", "${HOME}", "cfg/", "a b", "-x",
                "C:\\Users\\me", "\\\\server\\share", "file:///home/me", "\u2215home", " /home/me", "")

    def test_a_relative_home_or_docker_config_is_dropped_and_the_rest_stays(self):
        """: a command that builds a catalog's files runs in the catalog's folder, where a relative folder leads."""
        for value in self.RELATIVE:
            for name in ("HOME", "DOCKER_CONFIG"):
                with self.subTest(name=name, value=value):
                    self.shell({"HOME": str(self.home), "DOCKER_CONFIG": str(self.root / "real-config"), name: value})
                    environment = compose.build_environment()
                    self.assertNotIn(name, environment)
                    other = "DOCKER_CONFIG" if name == "HOME" else "HOME"
                    self.assertEqual(environment[other], str(self.root / "real-config") if other == "DOCKER_CONFIG" else str(self.home))
                    self.assertEqual(set(environment), ALLOWED | {other} | set(FLAGS))

    def test_both_relative_leave_the_allow_list_and_the_two_flags_alone(self):
        self.shell({"HOME": "home", "DOCKER_CONFIG": "cfg"})
        environment = compose.build_environment()
        self.assertEqual(set(environment), ALLOWED | set(FLAGS))
        self.assertEqual({name: environment[name] for name in FLAGS}, FLAGS)

    def test_an_absolute_one_is_kept_as_it_is_whether_or_not_it_is_there_or_looks_odd(self):
        for value in (str(self.root / "real-config"), str(self.root / "not" / "there"), "/", "/a b/c", f"{self.root}/x/../y",
                      "/home/me/.docker/", "/\u00e9t\u00e9", "//double"):
            for name in ("HOME", "DOCKER_CONFIG"):
                with self.subTest(name=name, value=value):
                    self.shell({name: value})
                    self.assertEqual(compose.build_environment()[name], value)

    def test_a_relative_one_is_not_made_absolute_for_it_is_not_the_persons(self):
        self.shell({"DOCKER_CONFIG": "cfg"})
        self.assertNotIn(str(Path.cwd()), "".join(compose.build_environment().values()))
        self.assertNotIn("cfg", "".join(compose.build_environment().values()))

    def test_a_relative_docker_config_cannot_make_the_cli_run_a_plugin_that_the_catalog_brings(self):
        """The Docker CLI takes a relative DOCKER_CONFIG for a folder of the directory it runs in, which is the catalog's: it would
        run the plugin there, with the credentials the build was given. Dropped, it looks where the person's own are."""
        self.plugin(self.root / "cfg" / "cli-plugins", which="the catalog's")
        self.plugin(self.home / ".docker" / "cli-plugins", which="the person's")
        self.shell({"DOCKER_CONFIG": "cfg"})
        self.assertEqual(self.call({**os.environ, "DOCKER_CONFIG": "cfg"}, "build")[0], "the catalog's", "what it would be")
        which, seen, code, _ = self.call(compose.build_environment(), "build")
        self.assertEqual((which, code), ("the person's", 0))
        self.assertNotIn("DOCKER_CONFIG", seen)

    def test_a_relative_home_cannot_make_the_cli_run_a_plugin_that_the_catalog_brings(self):
        self.plugin(self.root / "rel-home" / ".docker" / "cli-plugins", which="the catalog's")
        self.plugin(self.system[0], which="system")
        self.shell({"HOME": "rel-home"})
        self.assertEqual(self.call({**os.environ}, "build")[0], "the catalog's", "what it would be")
        which, seen, code, _ = self.call(compose.build_environment(), "build")
        self.assertEqual((which, code), ("system", 0))
        self.assertNotIn("HOME", seen)

    def test_the_lab_runs_a_build_with_it_and_the_plugin_sees_only_what_is_absolute(self):
        self.plugin(self.system[0], which="system")
        self.shell({"HOME": "rel-home", "DOCKER_CONFIG": "cfg"})
        code, out, err = compose.run(["docker", "compose", "build"], environ=compose.build_environment(), cwd=self.root)
        which, seen = parsed(out)
        self.assertEqual((code, err, which), (0, "", "system"))
        self.assertFalse({"HOME", "DOCKER_CONFIG"} & set(seen))
        self.assertFalse(set(seen) & set(SECRETS))

    # A PATH as the shell gave it and what a command that runs in the catalog's folder is told of it: only the absolute
    # folders, in their order. An empty folder and a relative one are the folder the command runs in.
    PATHS = ((".:/usr/bin:/bin", "/usr/bin:/bin"), (":/usr/bin:/bin", "/usr/bin:/bin"), ("/usr/bin::/bin", "/usr/bin:/bin"),
             ("/usr/bin:/bin:", "/usr/bin:/bin"), ("bin:/usr/bin", "/usr/bin"), ("./bin:../tools:/usr/bin:/bin/", "/usr/bin:/bin/"),
             ("~/bin:$HOME/bin:/usr/bin", "/usr/bin"), ("/usr/bin:node_modules/.bin", "/usr/bin"),
             ("/a b/c:/usr/bin", "/a b/c:/usr/bin"), ("/usr/local/bin:/usr/bin:/bin", "/usr/local/bin:/usr/bin:/bin"),
             ("/usr/bin:/usr/bin", "/usr/bin:/usr/bin"), ("/usr/bin", "/usr/bin"), ("//bin", "//bin"))
    NOWHERE = ("", ":", "::", ".", "..", "bin", "./bin", "bin:tools", ".:bin:", "~/bin", " ", "\n")

    def test_a_path_keeps_the_absolute_folders_in_their_order_and_loses_the_ones_that_lead_into_the_catalog(self):
        for given, kept in self.PATHS:
            with self.subTest(path=given):
                self.shell({"PATH": given})
                self.assertEqual(compose.build_environment()["PATH"], kept)

    def test_a_path_with_no_absolute_folder_is_dropped_for_an_empty_one_would_still_be_the_catalogs_folder(self):
        for given in self.NOWHERE:
            with self.subTest(path=repr(given)):
                self.shell({"PATH": given})
                environment = compose.build_environment()
                self.assertNotIn("PATH", environment)
                self.assertEqual(set(environment), ALLOWED - {"PATH"} | {"HOME"} | set(FLAGS))

    def test_a_shell_with_no_path_is_given_none(self):
        self.shell({}, PATH=None)
        self.assertNotIn("PATH", compose.build_environment())

    def test_a_path_is_changed_for_nothing_but_its_folders_and_the_rest_of_the_environment_stays(self):
        self.shell({"PATH": f".:{self.bin}:{os.defpath}", "LANG": "fr_FR.UTF-8", "DOCKER_CONFIG": str(self.root / "real-config")})
        environment = compose.build_environment()
        self.assertEqual(environment["PATH"], f"{self.bin}:{os.defpath}")
        self.assertEqual((environment["LANG"], environment["DOCKER_CONFIG"], environment["HOME"]),
                         ("fr_FR.UTF-8", str(self.root / "real-config"), str(self.home)))
        self.assertEqual(set(environment), ALLOWED | {"HOME", "DOCKER_CONFIG"} | set(FLAGS))

    def test_the_value_of_the_shell_is_not_changed_by_asking(self):
        self.shell({"PATH": f".::{self.bin}"})
        compose.build_environment()
        self.assertEqual(os.environ["PATH"], f".::{self.bin}")

    def test_a_docker_in_the_catalogs_folder_is_not_run_in_place_of_the_persons(self):
        """The command runs in the catalog's folder and a `.`, an empty folder or a relative one of the PATH leads there: the
        program a catalog calls `docker` would run, with the credentials of the build, before the person's."""
        catalog = self.root / "catalog"
        catalog.mkdir()
        mark = self.root / "ran-from-the-catalog"
        program = catalog / "docker"
        program.write_text(f"#!/bin/sh\necho ran > {shlex.quote(str(mark))}\n", encoding="utf-8")
        program.chmod(0o755)
        self.plugin(self.system[0], which="system")
        empty = self.root / "empty"
        empty.mkdir()
        sep, system = os.pathsep, f"{os.pathsep}/usr/bin{os.pathsep}/bin"   # `env` and `sh` of the fake plugin
        for given in (f".{sep}{self.bin}{system}", f"{sep}{self.bin}{system}", f"{empty}{sep}{sep}{self.bin}{system}",
                      f"bin{sep}.{sep}{self.bin}{system}", "."):
            with self.subTest(path=given):
                self.shell({"PATH": given})
                mark.unlink(missing_ok=True)
                compose.run(["docker", "compose", "ps"], environ=dict(os.environ), cwd=catalog)
                self.assertTrue(mark.exists(), "what the shell's own PATH does")
                mark.unlink()
                code, out, err = compose.run(["docker", "compose", "ps"], environ=compose.build_environment(), cwd=catalog)
                self.assertFalse(mark.exists(), "the program of the catalog was run")
                if given != ".":   # then the person's docker is found at its absolute folder
                    self.assertEqual((code, err, parsed(out)[0]), (0, "", "system"))

    def test_it_is_the_same_allow_list_no_actions_variable_no_token_no_agent_socket(self):
        environment = compose.build_environment()
        for name in environment:
            self.assertFalse(name.startswith(("ACTIONS_", "GITHUB_", "GH_")) or name.endswith("_TOKEN"), name)
        self.assertNotIn("SSH_AUTH_SOCK", environment)
        text = "\n".join(environment.values())
        for name, value in SECRETS.items():
            self.assertNotIn(value, text, name)
        for name in ("COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_ENV_FILES", "DOCKER_CONTEXT"):
            self.assertNotIn(name, environment)

    def test_it_makes_no_folder_and_links_nothing(self):
        before = sorted(compose.scratch().rglob("*"))
        compose.build_environment()
        self.assertEqual(sorted(compose.scratch().rglob("*")), before)

    def test_the_cli_finds_the_plugin_where_the_person_has_it(self):
        self.plugin(self.home / ".docker/cli-plugins", which="home")
        which, seen, code, _ = self.call(compose.build_environment(), "build")
        self.assertEqual((which, code), ("home", 0))
        self.assertEqual(seen["HOME"], str(self.home))

    def test_the_lab_runs_a_command_with_either_in_place_of_the_shell(self):
        self.plugin(self.system[0], which="system")
        for build, home in ((compose.scrubbed_environment, False), (compose.build_environment, True)):
            code, out, err = compose.run(["docker", "compose", "ps"], environ=build(), cwd=self.root)
            which, seen = parsed(out)
            self.assertEqual((code, err, which), (0, "", "system"), build.__name__)
            self.assertEqual(seen["HOME"] == str(self.home), home, build.__name__)
            self.assertFalse(set(seen) & set(SECRETS), "nothing of the shell reaches the plugin")


@unittest.skipUnless(sys.platform != "win32", "a POSIX machine")
class WithCompose(Case):
    """Compose itself, where it is installed: the scrubbed environment keeps it findable, and a file that names a secret
    of the shell or the home reads an empty value or a temporary path."""

    def setUp(self):
        need_compose()
        self.folder(compose)
        self.write("docker-compose.yml", (
            "name: scrubbed\n"
            "services:\n"
            "  probe:\n"
            "    image: busybox\n"
            "    environment:\n"
            "      TOKEN: ${ACTIONS_RUNTIME_TOKEN}\n"
            "      AGENT: ${SSH_AUTH_SOCK}\n"
            "      GITHUB: ${GH_TOKEN:-none}\n"
            "      WHERE: ${HOME}\n"
            "    volumes:\n"
            "      - ${HOME}/.aws:/aws:ro\n"))
        self.start(mock.patch.dict(os.environ, {"ACTIONS_RUNTIME_TOKEN": "runtime-token-value", "GH_TOKEN": "gh-token-value",
                                                "SSH_AUTH_SOCK": "/tmp/agent-for-test.sock"}))

    def test_compose_is_found_and_says_its_version(self):
        done = compose.run([*compose.COMPOSE, "version", "--short"], environ=compose.scrubbed_environment(), cwd=self.root)
        self.assertEqual(done[0], 0, done)
        self.assertRegex(done[1], r"^\d+\.\d+\.\d+")
        self.assertGreaterEqual(int(done[1].split(".")[0]), 5)

    def test_a_secret_of_the_shell_reads_empty_and_the_home_is_a_temporary_folder(self):
        environment = compose.scrubbed_environment()
        found = compose.config_json(["-f", "docker-compose.yml"], environ=environment)
        probe = found["services"]["probe"]
        self.assertEqual(probe["environment"]["TOKEN"], "")
        self.assertEqual(probe["environment"]["AGENT"], "")
        self.assertEqual(probe["environment"]["GITHUB"], "none")
        self.assertEqual(probe["environment"]["WHERE"], environment["HOME"])
        (mount,) = probe["volumes"]
        self.assertEqual(mount["source"], f"{environment['HOME']}/.aws")

    def test_the_same_file_read_with_the_environment_of_the_lab_shows_the_difference(self):
        # The control: it is the environment that holds the values back
        self.patch(compose, "_waiting", return_value=False)
        found = compose.config_json(["-f", "docker-compose.yml"])
        probe = found["services"]["probe"]
        self.assertEqual((probe["environment"]["TOKEN"], probe["environment"]["GITHUB"]),
                         ("runtime-token-value", "gh-token-value"))

    def test_a_name_in_a_file_that_looks_like_the_message_of_a_docker_without_compose_is_still_a_refusal_of_the_file(self):
        # Measured with Compose 5.6: it puts the name inside a sentence, so the words are not at the start of a line
        for name in ("docker: unknown command: docker compose", "unknown flag: --progress", "x\nunknown flag: --progress"):
            with self.subTest(name):
                self.write("forged.yml", f"services:\n  a:\n    image: busybox\n    depends_on: [{json.dumps(name)}]\n")
                with self.assertRaises(compose.ComposeError) as caught:
                    compose.config_json(["-f", "forged.yml"], environ=compose.scrubbed_environment())
                self.assertIn("undefined service", str(caught.exception))
                self.assertFalse(compose.missing(str(caught.exception)), str(caught.exception))

    def test_a_remote_include_is_still_refused(self):
        self.write("remote.yml", "include:\n  - path: https://example.invalid/org/repo.git\n")
        with self.assertRaisesRegex(compose.ComposeError, r"disabled by \"COMPOSE_EXPERIMENTAL_(GIT|OCI)_REMOTE\""):
            compose.config_json(["-f", "remote.yml"], environ=compose.scrubbed_environment())


if __name__ == "__main__":
    unittest.main()
