"""terminal: a shell on a pseudo-terminal that is kept and read from an offset, what it remembers, what it refuses to do. No Docker: the
shell is `cat`, which gives back what it is typed."""

import fcntl
import struct
import subprocess
import termios
import time
import unittest

from lab_ui.docker import terminal

from .. import Case


POPEN = subprocess.Popen   # the real one: what replaces it must not call itself


def cat(command, **how):
    """What stands in for `docker exec -it`: a process on the same terminal that echoes what it is typed."""
    return POPEN(["cat"], **how)


class Sessions(Case):
    def setUp(self):
        self.patch(terminal.subprocess, "Popen", cat)
        self.addCleanup(terminal.SESSIONS.clear)
        self.sessions = []

    def session(self, container="gio-kubectl"):
        found = terminal.Session(container)
        self.sessions.append(found)
        self.addCleanup(found.close)
        return found

    def printed(self, session, text, offset=0):
        """Waits until the shell has printed `text` from `offset` on; what it printed."""
        deadline, seen = time.time() + 5, b""
        while time.time() < deadline:
            end, seen, _ = session.read(offset, 0.2)
            if text.encode() in seen:
                return seen
        self.fail(f"{text!r} not printed, only {seen!r}")

    def test_what_is_typed_is_what_the_shell_prints_and_what_it_prints_is_kept(self):
        session = self.session()
        self.assertTrue(session.write(b"hello\n"))
        self.assertIn(b"hello", self.printed(session, "hello"))
        end, kept, alive = session.read(0, 0)
        self.assertEqual((kept.startswith(b"hello"), alive), (True, True))

    def test_a_reader_gets_what_came_after_its_offset_and_nothing_twice(self):
        session = self.session()
        session.write(b"one\n")
        self.printed(session, "one\r\none")   # what was typed, echoed, and what the shell printed of it
        end, _, _ = session.read(0, 0)
        session.write(b"two\n")
        later = self.printed(session, "two\r\ntwo", end)
        self.assertNotIn(b"one", later)

    def test_a_reader_with_nothing_new_waits_for_its_time_and_gets_nothing(self):
        session = self.session()
        started = time.time()
        end, data, alive = session.read(session.end, 0.3)
        self.assertEqual((data, alive), (b"", True))
        self.assertGreaterEqual(time.time() - started, 0.25)

    def test_only_the_last_of_the_output_is_kept_and_an_offset_before_it_starts_there(self):
        self.patch(terminal, "BUFFER", 16)
        session = self.session()
        session.write(b"0123456789abcdef-the-end\n")
        self.printed(session, "the-end")
        self.assertLessEqual(len(session.buffer), 16)
        self.assertGreater(session.dropped, 0)
        end, data, _ = session.read(0, 0)
        self.assertEqual(len(data), len(session.buffer))
        self.assertEqual(end, session.end)

    def test_the_size_of_the_page_is_the_size_of_the_terminal(self):
        session = self.session()
        session.resize(31, 117)
        rows, cols, *_ = struct.unpack("HHHH", fcntl.ioctl(session.master, termios.TIOCGWINSZ, b"\0" * 8))
        self.assertEqual((rows, cols), (31, 117))

    def test_a_shell_that_ended_is_not_alive_and_cannot_be_typed_to(self):
        session = self.session()
        session.write(b"\x04")   # end of input: cat ends
        deadline = time.time() + 5
        while session.alive and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(session.alive)
        self.assertFalse(session.write(b"late\n"))
        self.assertEqual(session.read(session.end, 5)[2], False)


class Attached(Case):
    def setUp(self):
        self.patch(terminal.subprocess, "Popen", cat)
        self.addCleanup(terminal.SESSIONS.clear)
        self.runs = self.patch(terminal, "running", return_value=True)

    def test_a_playground_has_one_shell_while_it_lives_in_the_same_container(self):
        first = terminal.attach("welcome", "gio-kubectl")
        self.addCleanup(first.close)
        self.assertIs(terminal.attach("welcome", "gio-kubectl"), first)

    def test_another_container_or_a_dead_shell_is_a_new_shell_and_the_old_one_is_closed(self):
        first = terminal.attach("welcome", "gio-kubectl")
        second = terminal.attach("welcome", "gio-other")
        self.addCleanup(second.close)
        self.assertIsNot(second, first)
        first.process.wait(timeout=5)
        self.assertIsNotNone(first.process.returncode)
        second.close()
        second.process.wait(timeout=5)
        deadline = time.time() + 5
        while second.alive and time.time() < deadline:
            time.sleep(0.05)
        third = terminal.attach("welcome", "gio-other")
        self.addCleanup(third.close)
        self.assertIsNot(third, second)

    def test_a_container_that_does_not_run_has_no_shell(self):
        self.runs.return_value = False
        with self.assertRaises(RuntimeError) as error:
            terminal.attach("welcome", "gio-kubectl")
        self.assertEqual(str(error.exception), "gio-kubectl is not running")
        self.assertEqual(terminal.SESSIONS, {})

    def test_dropping_ends_the_shell_and_the_next_one_is_new(self):
        first = terminal.attach("welcome", "gio-kubectl")
        terminal.drop("welcome")
        first.process.wait(timeout=5)
        self.assertEqual(terminal.SESSIONS, {})
        terminal.drop("welcome")   # nothing to end: no error
        second = terminal.attach("welcome", "gio-kubectl")
        self.addCleanup(second.close)
        self.assertIsNot(second, first)


class Running(Case):
    def test_a_container_runs_when_docker_says_true(self):
        for output, expected in (("true\n", True), ("false\n", False), ("", False)):
            with self.subTest(output=output):
                self.patch(terminal.compose, "run", return_value=(0, output, ""))
                self.assertEqual(terminal.running("gio-x"), expected)
        self.patch(terminal.compose, "run", return_value=(1, "", "No such container"))
        self.assertFalse(terminal.running("gio-x"))


class Sent(Case):
    def test_only_text_is_typed(self):
        self.assertEqual(terminal.clean("k get pods\n"), b"k get pods\n")
        self.assertEqual(terminal.clean("é"), "é".encode())
        for value in ("", None, 12, ["k"], b"k", {"a": 1}):
            self.assertIsNone(terminal.clean(value), repr(value))

    def test_a_size_is_two_whole_numbers_from_1_to_1000(self):
        self.assertEqual(terminal.size([24, 80]), (24, 80))
        for value in (None, [24], [24, 80, 1], [0, 80], [24, 1001], [24.5, 80], ["24", "80"], [True, 80], "24x80", {"rows": 24}):
            self.assertIsNone(terminal.size(value), repr(value))

    def test_the_output_travels_as_base64(self):
        self.assertEqual(terminal.encode(b"\x1b[32mok\x1b[0m\r\n"), "G1szMm1vaxtbMG0NCg==")


if __name__ == "__main__":
    unittest.main()
