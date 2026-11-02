"""The terminal of a playground: a shell in one of its containers (`x-gravitee.terminal` names the service), which the page
shows beside its guide. The shell is `docker exec -it` on a pseudo-terminal of the Lab UI's process, kept while it runs, one
for each playground: reload the page and it is the same shell, with what it printed last. The page reads its output as an event
stream and sends what is typed, and what a guide asks to run, by POST: the same two calls as everything else the page does, so
the checks of api.py (a local name, the page's own header) are all the guard it needs. It is the one place where the Lab UI
runs something a person typed: it runs in the playground's container, never on this machine."""

import base64
import fcntl
import os
import pty
import secrets
import signal
import struct
import subprocess
import termios
import threading

from . import compose

BUFFER = 256 * 1024   # what a session remembers of its output, for a page that opens late or again
HEARTBEAT = 15        # seconds of silence after which an open stream says it is still there
SHELL = ["sh"]        # the container's own shell: its environment (ENV for sh) sets the aliases of the playground
TERM = "xterm-256color"

SESSIONS = {}         # playground id -> Session
LOCK = threading.Lock()


class Session:
    """One shell in one container: its output kept from `dropped` on, the bytes before it forgotten. Its offsets count from
    `start`, a number of its own: the offset a page holds of a shell before this one (the Lab UI started again since) is none
    of this one's, and such a page gets what is kept."""

    def __init__(self, container):
        self.container = container
        self.buffer, self.dropped, self.alive = bytearray(), 0, True
        self.start = (1 << 51) + secrets.randbelow(1 << 51)   # below 2**53: the page holds it as a number
        self.changed = threading.Condition()
        self.held = threading.Lock()   # the terminal's number, used by one thread at a time: _read closes it once the shell ended
        master, slave = pty.openpty()
        self.master = master
        try:
            self.process = subprocess.Popen(["docker", "exec", "-it", "-e", f"TERM={TERM}", container, *SHELL],
                                            stdin=slave, stdout=slave, stderr=slave, close_fds=True,
                                            start_new_session=True, env=compose.environment())
        except OSError:
            os.close(master)
            raise
        finally:
            os.close(slave)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        """Keeps what the shell prints until it ends: the read fails once the other side of the terminal is closed."""
        while True:
            try:
                data = os.read(self.master, 65536)
            except OSError:
                data = b""
            if not data:
                break
            with self.changed:
                self.buffer += data
                if len(self.buffer) > BUFFER:
                    cut = len(self.buffer) - BUFFER
                    del self.buffer[:cut]
                    self.dropped += cut
                self.changed.notify_all()
        self.process.wait()
        with self.changed:
            self.alive = False
            self.changed.notify_all()
        with self.held:   # the number of a closed terminal may be another file's at once: nothing writes to it any more
            try:
                os.close(self.master)
            except OSError:
                pass
            self.master = -1

    @property
    def end(self):
        return self.start + self.dropped + len(self.buffer)

    def read(self, offset, timeout):
        """(the offset after what it returns, the output from `offset` on, whether the shell lives): waits up to `timeout`
        seconds for something new. An offset before what is kept starts at what is kept, and so does one that is not this
        shell's."""
        with self.changed:
            if not self.start <= offset <= self.end:
                offset = self.start
            if self.end <= offset and self.alive:
                self.changed.wait(timeout)
            begin = max(offset, self.start + self.dropped)
            return self.end, bytes(self.buffer[begin - self.start - self.dropped:]), self.alive

    def write(self, data):
        """Types these bytes. False when the shell is gone."""
        view = memoryview(data)
        with self.held:
            try:
                while view and self.master >= 0:
                    view = view[os.write(self.master, view):]
            except OSError:
                return False
            return not view

    def resize(self, rows, cols):
        """The size of the page's terminal becomes the shell's. The new session of `docker exec` has no controlling terminal, so the
        kernel never tells it the size changed: it is told here, and then passes it on to the exec in the container."""
        with self.held:
            try:
                if self.master >= 0:
                    fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
                if self.process.poll() is None:
                    self.process.send_signal(signal.SIGWINCH)
            except OSError:
                pass

    def close(self):
        """Hangs the session up, and ends it: a Lab UI started with nohup ignores SIGHUP, and its children inherit that. A shell
        that ended is not signaled: its number may be another process's by now. It takes no lock: a write that waits for the
        shell to read ends once the shell does."""
        for number in (signal.SIGHUP, signal.SIGTERM):
            if self.process.poll() is not None:
                return
            try:
                os.killpg(self.process.pid, number)
            except OSError:
                pass


def running(container):
    """Whether a container runs: the terminal is opened only in one."""
    rc, out, _ = compose.run(["docker", "container", "inspect", "--format", "{{.State.Running}}", container], timeout=20)
    return rc == 0 and out.strip() == "true"


def attach(pid, container):
    """The playground's session: the one that lives in this container, else a new one."""
    with LOCK:
        found = SESSIONS.get(pid)
        if found and found.alive and found.container == container:
            return found
        if found:
            found.close()
        if not running(container):
            raise RuntimeError(f"{container} is not running")
        SESSIONS[pid] = Session(container)
        return SESSIONS[pid]


def drop(pid):
    """Ends the playground's shell, if it has one: the next opening is a new one."""
    with LOCK:
        found = SESSIONS.pop(pid, None)
    if found:
        found.close()


def clean(data):
    """The text the page sends, as bytes for the shell, or None for anything else than text."""
    return data.encode("utf-8") if isinstance(data, str) and data else None


def encode(chunk):
    return base64.b64encode(chunk).decode("ascii")


def size(value):
    """(rows, cols) from what the page sent, each from 1 to 1000, else None."""
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(n, int) and not isinstance(n, bool) and 0 < n <= 1000
                                                                     for n in value):
        return value[0], value[1]
    return None
