"""install.sh and bin/gravitee-lab, run in a scratch home with a scratch repository to clone: what they make, what they refuse,
and what they print in a terminal and out of one. Nothing is installed: the tools of the machine are there, or the one a test
takes away is left off the PATH."""

import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "install.sh"
LAUNCHER = ROOT / "bin" / "gravitee-lab"
MARKER = "# Added by Gravitee Lab"
PROFILE = ".bash_profile" if sys.platform == "darwin" else ".bashrc"

# A Lab that clones in an instant: the files install.sh knows a Lab by, the real launcher, and a Makefile of a few targets
MAKEFILE = """init:
\t@touch .env
help:
\t@echo the targets
ui:
\t@echo ui ran
stop:
\t@echo stop ran $(SVC)
"""

# A git in front of the real one: its clone shows a progress line and waits FAKE_WAIT seconds first
GIT = """#!/bin/sh
if [ "$1" = clone ]; then
    printf 'Receiving objects:  50%% (1/2), 1.00 MiB | 2.00 MiB/s\\r' >&2
    sleep "${FAKE_WAIT:-0}"
fi
exec "$REAL_GIT" "$@"
"""

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


@unittest.skipUnless(os.name == "posix" and shutil.which("git") and shutil.which("make"), "needs sh, git and make")
class Install(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.home = self.folder / "home"
        self.home.mkdir()
        self.lab = self.home / "gravitee-lab"
        self.repository = self.folder / "repository"
        (self.repository / "lab-ui").mkdir(parents=True)
        (self.repository / "bin").mkdir()
        (self.repository / "lab-ui" / "server.py").write_text("")
        (self.repository / "Makefile").write_text(MAKEFILE)
        shutil.copy(LAUNCHER, self.repository / "bin" / "gravitee-lab")
        git = ["git", "-C", str(self.repository), "-c", "user.name=a", "-c", "user.email=a@b"]
        subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", str(self.repository)], check=True)
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-qm", "lab"], check=True)
        local = str(Path.home() / ".local" / "bin")
        self.env = {
            "HOME": str(self.home), "SHELL": "/bin/bash", "LANG": "en_US.UTF-8", "TERM": "xterm-256color",
            "PATH": os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep) if p != local),
            "GRAVITEE_LAB_REPOSITORY": str(self.repository), "GRAVITEE_LAB_NO_START": "1",
        }

    def install(self, **env):
        # A session of its own has no terminal: nothing can ask, as on a CI runner
        run = subprocess.run(["sh", str(INSTALL)], env={**self.env, **env}, stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=120, start_new_session=True)
        return run.returncode, run.stdout + run.stderr

    def terminal(self, interrupt_on=None, **env):
        """install.sh in a pseudo-terminal of 100 columns: its exit code and all it printed. Ctrl+C when `interrupt_on` shows."""
        import fcntl
        import pty
        import struct
        import termios
        pid, fd = pty.fork()
        if pid == 0:
            os.execve("/bin/sh", ["sh", str(INSTALL)], {**self.env, **env})
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        printed, deadline = b"", time.monotonic() + 120
        while time.monotonic() < deadline:
            if not select.select([fd], [], [], 1)[0]:
                continue
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break
            printed += data
            if interrupt_on and interrupt_on.encode() in printed:
                interrupt_on = None
                os.write(fd, b"\x03")
        else:
            os.kill(pid, signal.SIGKILL)
        os.close(fd)
        _, status = os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status), printed.decode("utf-8", "replace")

    def slow_git(self):
        """A git whose clone shows a progress line, then waits FAKE_WAIT seconds: what the PATH of a test puts first."""
        folder = self.folder / "slow"
        folder.mkdir()
        (folder / "git").write_text(GIT)
        (folder / "git").chmod(0o755)
        return {"PATH": f"{folder}{os.pathsep}{self.env['PATH']}", "REAL_GIT": shutil.which("git")}

    def test_a_bare_home_gets_the_clone_its_settings_the_command_and_the_line_of_its_profile(self):
        code, said = self.install()
        self.assertEqual(code, 0, said)
        self.assertTrue((self.lab / ".env").is_file(), "make init ran in the clone")
        link = self.home / ".local" / "bin" / "gravitee-lab"
        self.assertEqual(os.readlink(link), str(self.lab / "bin" / "gravitee-lab"))
        self.assertEqual((self.home / PROFILE).read_text().count(MARKER), 1)
        for step in ("Tools", "Gravitee Lab", "Settings", "Command", "Docker", "Ready in"):
            self.assertIn(step, said)
        self.assertNotIn("\033", said, "no color and no live line out of a terminal")

    def test_a_second_run_keeps_what_is_there_and_adds_nothing(self):
        self.install()
        (self.lab / "mine.txt").write_text("kept")
        code, said = self.install()
        self.assertEqual(code, 0, said)
        self.assertIn("already there", said)
        self.assertEqual((self.lab / "mine.txt").read_text(), "kept")
        self.assertEqual((self.home / PROFILE).read_text().count(MARKER), 1)

    def test_the_line_goes_to_the_profile_of_the_shell_and_none_when_the_path_has_the_folder(self):
        for shell, profile in (("/bin/zsh", ".zshrc"), ("/usr/bin/fish", ".config/fish/conf.d/gravitee-lab.fish"),
                               ("/bin/sh", ".profile")):
            with self.subTest(shell=shell):
                code, said = self.install(SHELL=shell)
                self.assertEqual(code, 0, said)
                self.assertIn(MARKER, (self.home / profile).read_text())
                self.assertIn(f"PATH set in ~/{profile}", said)
        shutil.rmtree(self.home)
        self.home.mkdir()
        local = self.home / ".local" / "bin"
        code, said = self.install(PATH=f"{local}{os.pathsep}{self.env['PATH']}")
        self.assertEqual(code, 0, said)
        self.assertEqual(list(self.home.glob(".*rc")) + list(self.home.glob(".*profile")), [])
        self.assertTrue((local / "gravitee-lab").is_symlink())

    def test_what_it_refuses_stops_it_with_what_to_do_and_changes_nothing(self):
        def other_folder():
            self.lab.mkdir()
            (self.lab / "notes.txt").write_text("mine")

        def older_lab():
            (self.lab / "lab-ui").mkdir(parents=True)
            (self.lab / "lab-ui" / "server.py").write_text("")

        def file_in_the_way():
            (self.home / ".local" / "bin").mkdir(parents=True)
            (self.home / ".local" / "bin" / "gravitee-lab").write_text("mine")

        for case, arrange, env, expected in (
                ("another folder", other_folder, {}, "holds something else"),
                ("an older lab", older_lab, {}, "make update"),
                ("a file in the way", file_in_the_way, {}, "is a file of something else"),
                ("sudo", lambda: None, {"SUDO_USER": "someone"}, "Run this without sudo")):
            with self.subTest(case):
                shutil.rmtree(self.home)
                self.home.mkdir()
                arrange()
                code, said = self.install(**env)
                self.assertEqual(code, 1, said)
                self.assertIn(expected, said)
                self.assertIn("Help: https://github.com/gravitee-io-labs/gravitee-lab/blob/main/docs/installation.md", said)
                self.assertFalse((self.home / PROFILE).exists(), "it stops before the profile")
        self.assertEqual((self.home / ".local" / "bin").exists(), False)

    def test_a_tool_that_is_missing_stops_it_before_the_clone_with_what_to_do(self):
        tools = self.folder / "tools"
        tools.mkdir()
        for folder in ("/usr/bin", "/bin"):
            for program in Path(folder).iterdir():
                if program.name != "git" and not (tools / program.name).exists():
                    (tools / program.name).symlink_to(program)
        code, said = self.install(PATH=str(tools))
        self.assertEqual(code, 1, said)
        self.assertRegex(said, r"git (is still missing|is missing|with)")
        self.assertFalse(self.lab.exists())

    def test_in_a_terminal_a_live_line_runs_and_each_step_ends_on_its_mark(self):
        code, said = self.terminal(**self.slow_git(), FAKE_WAIT="1")
        self.assertEqual(code, 0, said)
        self.assertTrue(any(frame in said for frame in FRAMES), "the spinner turned")
        self.assertIn("━", said, "the percentage of the clone shows as a bar")
        self.assertIn(" 50%  Receiving objects, 1.00 MiB, 2.00 MiB/s", said)
        for step in ("Tools", "Gravitee Lab", "Settings", "Command"):
            self.assertRegex(said, rf"✔\x1b\[0m \x1b\[1m{step}")
        self.assertTrue(said.startswith("\033[?25l"), "the cursor hides while the line runs")
        self.assertTrue(said.rstrip().endswith("\033[?25h"), "and it is back at the end")

    def test_a_terminal_that_is_not_utf8_gets_ascii_and_no_color_gets_none(self):
        code, said = self.terminal(LANG="C")
        self.assertEqual(code, 0, said)
        self.assertIn("+\x1b[0m \x1b[1mTools", said)
        self.assertNotIn("✔", said)
        code, said = self.terminal(NO_COLOR="1")
        self.assertEqual(code, 0, said)
        self.assertIn("✔ Tools", said)
        self.assertNotRegex(said, r"\x1b\[(1|2|3\d)m")

    def test_ctrl_c_stops_it_with_the_cursor_back_and_says_how_to_go_on(self):
        code, said = self.terminal(interrupt_on="Receiving objects", **self.slow_git(), FAKE_WAIT="30")
        self.assertEqual(code, 130, said)
        self.assertIn("Stopped. Run the command again to go on", said)
        self.assertIn("\033[?25h", said.split("Stopped")[0][-40:], "the cursor is back before the message")
        self.assertFalse((self.home / PROFILE).exists())


@unittest.skipUnless(os.name == "posix" and shutil.which("git") and shutil.which("make"), "needs sh, git and make")
class Launcher(unittest.TestCase):
    """bin/gravitee-lab, through the link install.sh makes: a target of the Makefile of its clone, help, and uninstall."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.home = self.folder / "home"
        self.lab = self.folder / "lab"
        (self.lab / "bin").mkdir(parents=True)
        (self.lab / "Makefile").write_text(MAKEFILE)
        shutil.copy(LAUNCHER, self.lab / "bin" / "gravitee-lab")
        self.link = self.home / ".local" / "bin" / "gravitee-lab"
        self.link.parent.mkdir(parents=True)
        self.link.symlink_to(self.lab / "bin" / "gravitee-lab")

    def run_it(self, *argv):
        run = subprocess.run([str(self.link), *argv], env={"HOME": str(self.home), "PATH": os.environ["PATH"]},
                             capture_output=True, text=True, timeout=60)
        return run.returncode, run.stdout, run.stderr

    def test_a_word_runs_that_target_alone_it_opens_the_lab_ui_and_help_lists_them(self):
        self.assertEqual(self.run_it(), (0, "ui ran\n", ""))
        self.assertEqual(self.run_it("stop", "SVC=apim-gateway"), (0, "stop ran apim-gateway\n", ""))
        code, out, _ = self.run_it("help")
        self.assertEqual((code, out.splitlines()[-1]), (0, "the targets"))
        self.assertIn("Usage: gravitee-lab [command]", out)

    def test_a_word_that_is_no_target_is_refused_before_make_runs(self):
        for word in ("nope", "-n", "ui;ls", "../x"):
            with self.subTest(word=word):
                code, out, err = self.run_it(word)
                self.assertEqual((code, out), (2, ""))
                self.assertIn(f"there is no command {word}", err)

    def test_uninstall_takes_off_the_link_and_the_lines_it_added_and_keeps_the_rest(self):
        (self.home / ".zshrc").write_text(f"alias ll='ls -l'\nexport PATH=\"$HOME/.local/bin:$PATH\"  {MARKER}\n")
        fish = self.home / ".config" / "fish" / "conf.d" / "gravitee-lab.fish"
        fish.parent.mkdir(parents=True)
        fish.write_text(f'fish_add_path -g "$HOME/.local/bin"  {MARKER}\n')
        code, out, _ = self.run_it("uninstall")
        self.assertEqual(code, 0, out)
        self.assertFalse(self.link.exists() or self.link.is_symlink())
        self.assertEqual((self.home / ".zshrc").read_text(), "alias ll='ls -l'\n")
        self.assertFalse(fish.exists())
        self.assertTrue((self.lab / "Makefile").exists(), "the Lab stays")
        self.assertIn(f"The Lab stays in {self.lab.resolve()}", out)


if __name__ == "__main__":
    unittest.main()
