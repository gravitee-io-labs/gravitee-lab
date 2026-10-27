"""The only module that runs git. It parses what a person types as an address, builds the environment git runs in, runs
git without a shell and kills it (and what it started) at its time, and maps git's failures to the error lines of
the catalogue of errors. Everything git does for the catalogs goes through `run` or `stream`; `store.py` builds on them.

Git is run with an argument list, never a shell, with no input, in a session of its own (so that no terminal, askpass
program or credential manager can ask a question) and with a scrubbed environment: only the variables a connection
needs are passed on, none of git's own, so that a variable of the shell the Lab UI was started from cannot redirect a
command. A command lives in a session of its own, so the Lab UI ends it itself when it exits: `stop_running` is
registered for the exit, and the handler of the signals of the Lab UI has to call it. Failures are CatalogErrors whose
line is the one of the catalogue of errors; the raw output goes to `detail["output"]`, credentials removed."""

import atexit
import codecs
import concurrent.futures.thread   # noqa: F401  (see stop_running: its exit hook has to be registered before ours)
import ipaddress
import os
import re
import secrets
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from .registry import CatalogError

# The protocols git may use. Tests add "file" and set ALLOW_FILE; production never allows a local path as an address
PROTOCOLS = "https:ssh"
ALLOW_FILE = False
# sys.platform and the shim of macOS, read at call time so that a test can stand on a Mac
PLATFORM = sys.platform
SHIM = "/usr/bin/git"
MINIMUM = (2, 25)

# The seconds a killed git has to clean up (its .lock files) after SIGTERM before SIGKILL
KILL_GRACE = 2.0
# The seconds a command that has ended is given to drain its output when something it started still holds the pipes (an
# ssh helper): after that the command's own exit code is its answer and what it left behind is killed. And how often a
# wait looks whether the command ended, which no output may say
EXIT_GRACE = 1.0
POLL = 0.25
# What `run` keeps of the output: all of stdout up to this size, the last part of stderr
OUTPUT_LIMIT = 64 * 1024 * 1024
STDERR_TAIL = 128 * 1024
# Appended to the errors of a command killed for its time; `classify` reads it
TIMEOUT_MARK = "git timed out"
# Options of every command: a repository's hooks and file system monitor are programs, and none of ours may run one; and
# a person's log.showSignature would run their gpg for every commit and print its lines among the subjects
HARDEN = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "log.showSignature=false"]
# The variables of the shell that a connection needs and that carry no git behavior
PASSED = ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "TZ", "SSH_AUTH_SOCK", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR",
          "DBUS_SESSION_BUS_ADDRESS", "SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE",
          "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy")

# code: (status, line). Placeholders: {repo} {host} {signin} {path} {have} {branch}; the catalogue of errors is the reference
MESSAGES = {
    "git_missing": (400, "Git is not installed. Install it, then add the catalog again."),
    "git_old": (400, "Git {have} is too old. Install Git 2.25 or later, then add the catalog again."),
    "docker_missing": (400, "Docker is not installed. Install it, then add the catalog again."),
    "bad_address": (400, "Use owner/repo, a Git address such as https://github.com/owner/repo, or the path of a folder."),
    "bad_address_secret": (400, "Remove the user name and token from the address. Git uses the sign-in of this machine."),
    "no_access": (400, "No access to {repo}. {signin}, then try again."),
    "not_found": (400, "{repo} was not found. Check the address, or sign in with Git if it is private."),
    "ssh_denied": (400, "Permission denied for {host}. Add your SSH key to {host}, then try again."),
    "host_key": (400, "{host} is not a known SSH host. Connect to it once from a terminal, then try again."),
    "unreachable": (502, "Cannot reach {host}. Check your connection, then try again."),
    "timeout": (502, "{host} did not answer in time. Try again."),
    "certificate": (502, "The certificate of {host} was refused. A company proxy may be in the way."),
    "too_large": (400, "This catalog is larger than the Lab accepts. Ask its owners to make it smaller."),
    "fsck": (400, "This repository is damaged. Ask its owners to check it."),
    "not_catalog": (400, "{repo} has no catalog.yml, so it is not a catalog."),
    "branch_missing": (400, "{repo} has no branch {branch}. Check the name, or leave it empty for the default."),
    "folder_missing": (400, "No folder at {path}."),
    "folder_not_catalog": (400, "{path} has no catalog.yml, so it is not a catalog."),
    "folder_not_shared": (400, "Docker cannot share this folder. Move it under your home folder."),
    # Added to the catalogue of errors: git failed in a way none of its lines describes
    "git_failed": (502, "Git could not read {repo}. Check the address and your connection, then try again."),
}


class Address(NamedTuple):
    """What a person typed, once understood. `kind` is "git" or "folder"; `url` is the credential-free address git
    is given (empty for a folder); `path` the absolute folder (empty for git); `display` what the page shows, the
    same for every spelling of one repository (`github.com/acme/lab-hub`, `~/code/hub`); `host` and `repo`
    (`owner/repo`) are what the error lines name."""
    kind: str
    url: str
    path: str
    display: str
    host: str
    repo: str


class Result(NamedTuple):
    """How a command ended: its exit code (124 when it was killed for its time), its output and its errors."""
    code: int
    out: bytes
    err: str


class OutputLimit(Exception):
    """A command printed more than it was allowed to."""


def signin(address: Optional[Address] = None) -> str:
    """The sign-in hint of a host, for `{signin}`: the command that works for GitHub, the general words elsewhere."""
    if address is not None and address.host == "github.com":
        return "Run gh auth login in a terminal"
    return "Sign in with Git on this machine"


def fail(code: str, address: Optional[Address] = None, **fields) -> CatalogError:
    """The CatalogError of a code of the catalogue of errors, its line filled from the address and `fields` (have, branch, path,
    and `output`, which goes to `detail` and not into the line)."""
    status, line = MESSAGES[code]
    output = fields.pop("output", None)
    names = {"repo": (address.repo or address.display) if address else "", "host": address.host if address else "",
             "signin": signin(address), "path": "", "have": "", "branch": ""}
    if address is not None and not names["host"]:
        names["host"] = address.display
    names["repo"] = names["repo"] or "this repository"
    names["host"] = names["host"] or "the host"
    names.update({key: str(value) for key, value in fields.items()})
    detail = {"output": output} if output else {}
    return CatalogError(code, line.format_map(names), status, **detail)


# ---------------------------------------------------------------- the address grammar

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"{_LABEL}(?:\.{_LABEL})*")
_USER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,31}")   # never a dash first: ssh would read the address as an option
_SEGMENT = re.compile(r"[A-Za-z0-9._~+-]+")
_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*)://(.*)", re.S)
_SCP = re.compile(r"([^@:/\s]+)@([^:/@\s]+):(.+)", re.S)
_SHORTHAND = re.compile(r"([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100})")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+/-]{0,199}")
LOOPBACK = ("localhost",)


def home():
    """The home folder, links resolved: the only place a folder catalog may be (Docker shares it)."""
    return Path(os.path.realpath(os.path.expanduser("~")))


def _is_ipv4(host):
    try:
        ipaddress.IPv4Address(host)
        return True
    except ValueError:
        return False


def _host_of(text):
    """(host, port) of an authority without userinfo, or None when it is not a host git may be sent to. A host is
    ASCII and never starts with a dash (ssh would read it as an option); a name made of digits is an IPv4 address."""
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            return None
        host, rest = text[1:end], text[end + 1:]
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return None
        if rest and not rest.startswith(":"):
            return None
        host, port = f"[{host}]", (rest[1:] if rest else None)
    else:
        name, colon, number = text.partition(":")
        host, port = name.lower(), (number if colon else None)
        if not host or len(host) > 253 or not _HOST.fullmatch(host):
            return None
        if re.fullmatch(r"[0-9.]+", host) and not _is_ipv4(host):
            return None
    if port is not None and not (re.fullmatch(r"[0-9]{1,5}", port) and 0 < int(port) < 65536):
        return None
    return host, port


def _loopback(host):
    bare = host.strip("[]")
    if bare in LOOPBACK:
        return True
    try:
        return ipaddress.ip_address(bare).is_loopback
    except ValueError:
        return False


def _repo_path(path):
    """The path of a repository on its host with a leading and a trailing slash removed, or None when it holds an
    empty, dot or odd segment."""
    if "//" in path:
        return None
    body = path.strip("/")
    if not body:
        return None
    for segment in body.split("/"):
        if segment in (".", "..") or not _SEGMENT.fullmatch(segment):
            return None
    return body


def _named(body):
    """`owner/repo` of a path: the path without a final .git."""
    return body[:-4] if body.lower().endswith(".git") and len(body) > 4 else body


def _git_address(url, host, port, body):
    shown = host + (f":{port}" if port else "")
    name = _named(body)
    return Address("git", url, "", f"{shown}/{name}", host.strip("[]"), name)


def _url_address(scheme, rest):
    """The address of `scheme://rest`, or the CatalogError that refuses it."""
    if scheme == "file":
        if not ALLOW_FILE:
            raise fail("bad_address")
        authority, slash, path = rest.partition("/")
        if authority not in ("", "localhost") or not slash:
            raise fail("bad_address")
        body = _repo_path(path)
        if body is None:
            raise fail("bad_address")
        name = _named(body)
        return Address("git", f"file:///{body}", "", f"/{name}", "localhost", name.rsplit("/", 1)[-1])
    if scheme not in ("https", "http", "ssh"):
        raise fail("bad_address")
    authority, slash, path = rest.partition("/")
    if not slash or not authority:
        raise fail("bad_address")
    userinfo, at, hostport = authority.rpartition("@")
    if at:
        if scheme != "ssh" or ":" in userinfo or not userinfo:
            raise fail("bad_address_secret")
        if not _USER.fullmatch(userinfo):
            raise fail("bad_address")
    found = _host_of(hostport)
    body = _repo_path("/" + path)
    if found is None or body is None:
        raise fail("bad_address")
    host, port = found
    if scheme == "http" and not _loopback(host):
        raise fail("bad_address")
    shown = f"{host}:{port}" if port else host
    url = f"{scheme}://{userinfo + '@' if at else ''}{shown}/{body}"
    return _git_address(url, host, port, body)


def _scp_address(match):
    user, host, path = match.groups()
    found = _host_of(host)
    body = _repo_path(path)
    if found is None or found[1] is not None or body is None or not _USER.fullmatch(user):
        raise fail("bad_address")
    host = found[0]
    prefix = "/" if path.startswith("/") else ""
    return _git_address(f"{user}@{host}:{prefix}{body}", host, None, body)


def _folder_address(text):
    path = os.path.abspath(os.path.expanduser(text))
    if not os.path.isdir(path):
        raise fail("folder_missing", path=text)
    root = str(home())
    real = os.path.realpath(path)
    if real != root and not real.startswith(root.rstrip(os.sep) + os.sep):
        raise fail("folder_not_shared")
    try:
        regular = stat.S_ISREG(os.lstat(os.path.join(path, "catalog.yml")).st_mode)
    except OSError:
        regular = False
    if not regular:
        raise fail("folder_not_catalog", path=text)
    shown = path
    for base in (os.path.expanduser("~"), root):
        if path == base or path.startswith(base.rstrip(os.sep) + os.sep):
            shown = "~" + path[len(base.rstrip(os.sep)):]
            break
    return Address("folder", "", path, shown, "", "")


def parse_address(text: str) -> Address:
    """What a person typed, as an Address, or the CatalogError that refuses it.

    Git: `owner/repo` (GitHub), `https://host/path`, `ssh://[user@]host[:port]/path`, `user@host:path`, and
    `http://` only for a loopback host (a git server on this machine). A folder starts with `/`, `~`, `./` or `../`.
    Refused (`bad_address`): any other scheme (`git://`, `ext::`, `fd::`, `file://` unless a test allows it), a
    leading dash, a control character, a space in a Git address, a query or fragment, an odd host or path segment.
    Refused (`bad_address_secret`): a user name or password in an https address, a password in any. A folder must
    exist (`folder_missing`), be under the home folder (`folder_not_shared`) and hold a regular catalog.yml
    (`folder_not_catalog`). Two spellings of one repository have one `display`."""
    if not isinstance(text, str):
        raise fail("bad_address")
    raw = text.strip(" ")
    if not raw or raw.startswith("-") or len(raw) > 4096:
        raise fail("bad_address")
    if any(unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") for c in raw):
        raise fail("bad_address")
    if raw in (".", "..", "~") or raw.startswith(("/", "~", "./", "../")):
        return _folder_address(raw)
    if any(c.isspace() for c in raw) or "\\" in raw or not raw.isascii():
        raise fail("bad_address")
    scheme = _SCHEME.fullmatch(raw)
    if scheme:
        return _url_address(scheme.group(1).lower(), scheme.group(2))
    userinfo = raw.split("@", 1)[0] if "@" in raw else ""
    if userinfo and ":" in userinfo and "/" not in userinfo:
        raise fail("bad_address_secret")   # user:token@host:path
    scp = _SCP.fullmatch(raw)
    if scp:
        return _scp_address(scp)
    short = _SHORTHAND.fullmatch(raw)
    if short and short.group(2) not in (".", "..", ".git"):
        owner, repo = short.groups()
        repo = _named(repo)
        return _git_address(f"https://github.com/{owner}/{repo}.git", "github.com", None, f"{owner}/{repo}")
    raise fail("bad_address")


def ssh_form(address: Address) -> Optional[Address]:
    """The ssh spelling of an https address (`git@HOST:owner/repo.git`), the second try of the first start and the
    background check; None for an address that is not https or that names a port (the ssh port is another)."""
    if address.kind != "git" or not address.url.startswith("https://") or ":" in address.display.split("/", 1)[0]:
        return None
    path = _repo_path(address.display.split("/", 1)[1]) if "/" in address.display else None
    if path is None:
        return None
    return Address("git", f"git@{address.host}:{path}.git", "", address.display, address.host, address.repo)


def checked(address: Address) -> Address:
    """The address when it is one the grammar would make (a git address whose `url` parses to itself), else
    `bad_address`: what reaches a command is never an address that someone built by hand with an `ext::` program or an
    option in it. `ls_remote`, `push_probe` and `store.fetch` ask this before they run anything."""
    if not isinstance(address, Address) or address.kind != "git" or parse_address(address.url).url != address.url:
        raise fail("bad_address")
    return address


def is_ssh(address: Address) -> bool:
    return address.kind == "git" and (address.url.startswith("ssh://") or _SCP.fullmatch(address.url) is not None)


def valid_branch(name: str, address: Optional[Address] = None) -> str:
    """A branch name that is safe as an argument and as part of a reference, or `branch_missing`: letters, digits and
    `._+-/`, no `..`, no empty or dotted component, not ending in `/` `.` or `.lock`."""
    ok = (isinstance(name, str) and _BRANCH.fullmatch(name) and ".." not in name and "//" not in name
          and "/." not in name and not name.endswith(("/", ".", ".lock")))
    if not ok:
        raise fail("branch_missing", address, branch=str(name)[:80])
    return name


def asked_branch(name: Optional[str], address: Optional[Address] = None) -> Optional[str]:
    """The branch a person asked for, checked (`valid_branch`), or None for the default one of the remote: the page's
    field is optional, so None, an empty text and a text of spaces all mean "none". Anything else that is not a
    name is `branch_missing`, a value that is not text included."""
    if name is None:
        return None
    if isinstance(name, str):
        name = name.strip(" ")
        if not name:
            return None
    return valid_branch(name, address)


# ---------------------------------------------------------------- environment and processes

def environment() -> Dict[str, str]:
    """The environment git runs in. Only the variables a connection needs are passed on (PASSED): the path, the home
    folder, the ssh agent, proxies and certificates; no GIT_* variable and no askpass of the shell. Added: the
    protocols git may use (PROTOCOLS, read now), no terminal prompt, an empty GIT_ASKPASS (which git reads as "no
    askpass at all", ahead of core.askPass and SSH_ASKPASS), no prompt of the credential manager, `ssh -o BatchMode=yes`,
    and the C locale, so that what git says is what `classify` knows."""
    env = {name: os.environ[name] for name in PASSED if name in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.update({"GIT_ALLOW_PROTOCOL": PROTOCOLS, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "",
                "SSH_ASKPASS_REQUIRE": "never", "GCM_INTERACTIVE": "never", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes",
                "LC_ALL": "C"})
    return env


def address_env(address: Optional[Address]) -> Dict[str, str]:
    """What a command about `address` adds to `environment()`: http when the address is a loopback http one."""
    if address is not None and address.url.startswith("http://"):
        return {"GIT_ALLOW_PROTOCOL": PROTOCOLS + ":http"}
    return {}


def redact(text: str) -> str:
    """A text with the user names and passwords of its addresses removed: `https://user:token@host` is
    `https://host`."""
    return re.sub(r"(://)[^/\s@]*@", r"\1", text or "")


# The seconds an answer of `xcode-select -p` is kept: it is one more process for every command, and the Command Line
# Tools do not come and go between two clicks
TOOLS_AGE = 30.0
_TOOLS = {"at": None, "ok": False}
_TOOLS_LOCK = threading.Lock()


def _tools() -> bool:
    """Whether the Command Line Tools of macOS are installed (`xcode-select -p`), asked at most every TOOLS_AGE seconds."""
    with _TOOLS_LOCK:
        now = time.monotonic()
        if _TOOLS["at"] is None or now - _TOOLS["at"] >= TOOLS_AGE:
            try:
                answer = subprocess.run(["xcode-select", "-p"], stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
                ok = answer.returncode == 0
            except (OSError, subprocess.SubprocessError):
                ok = False
            _TOOLS.update({"at": now, "ok": ok})
        return _TOOLS["ok"]


def executable() -> Optional[str]:
    """The git to run, or None when there is none. On macOS `/usr/bin/git` is a shim that opens an installer when the
    Command Line Tools are missing, so `xcode-select -p` is asked first (`_tools`), and a failure answers None without
    the shim ever being run."""
    path = shutil.which("git", path=environment()["PATH"])
    if path is None:
        return None
    if PLATFORM == "darwin" and (path == SHIM or os.path.realpath(path) == SHIM) and not _tools():
        return None
    return path


_NEUTRAL = {}
_NEUTRAL_LOCK = threading.Lock()


def _neutral():
    """A folder of this run of the Lab UI that is in no repository and that no one else can write: where git runs when
    a command names no folder, so that the configuration of a repository found above the current folder (an
    fsmonitor, an ssh command) is never read. Made again when the system clears it."""
    with _NEUTRAL_LOCK:
        folder = _NEUTRAL.get("folder")
        if not folder or not os.path.isdir(folder):
            folder = os.path.realpath(tempfile.mkdtemp(prefix="gravitee-lab-git-"))
            atexit.register(shutil.rmtree, folder, True)
            _NEUTRAL["folder"] = folder
        return folder


def _prepare(exe, args, cwd, env):
    """(argv, cwd, env) of a command: the executable, the options of HARDEN, the arguments checked to be text without
    NUL, and the environment with the ceiling that keeps git from looking for a repository above the neutral folder."""
    args = list(args)
    if args and args[0] == "git":
        args = args[1:]
    for arg in args:
        if not isinstance(arg, str) or "\0" in arg:
            raise TypeError(f"git was given an argument that is not text: {arg!r}")
    merged = {**environment(), **(env or {})}
    if cwd is None:
        cwd = _neutral()
        merged["GIT_CEILING_DIRECTORIES"] = os.path.dirname(cwd)
    return [exe, *HARDEN, *args], str(cwd), merged


# The commands being run, to end with their group if the Lab UI exits before they do. The lock is reentrant: a signal
# handler that calls `stop_running` runs on the thread it interrupted, which may hold the lock
_LIVE = set()
_LIVE_LOCK = threading.RLock()
# Whether the Lab UI is exiting (stop_running): no command is started from then on
_ending = False
ENDING = "git was not run: the Lab UI is ending"


def _signal_group(process, number):
    try:
        os.killpg(process.pid, number)
    except (ProcessLookupError, PermissionError):
        pass


def _kill(process):
    """Ends a command and everything it started: SIGTERM to its group so that git removes its lock files, SIGKILL after
    the grace, and a last SIGKILL for what outlived it (an ssh)."""
    _signal_group(process, signal.SIGTERM)
    try:
        process.wait(KILL_GRACE)
    except subprocess.TimeoutExpired:
        _signal_group(process, signal.SIGKILL)
        process.wait()
    _signal_group(process, signal.SIGKILL)


def stop_running() -> None:
    """Ends every command that runs, with what it started, and starts none from then on: what the Lab UI does as it
    exits. A command leads a session of its own, so the Ctrl+C or the hang-up that ends the Lab UI does not reach it,
    and a download must not outlive the page that asked for it. Registered with `atexit` and with the exit hook of the
    threads (which runs before Python waits for the threads of a pool: those run the commands, and the wait would last
    as long as a download). The handler of the signals that end the Lab UI (`compose.stop_on_signals`) has to call it
    too: Python runs no exit function for a signal. Safe to call from a signal handler, twice, and from any thread."""
    global _ending
    with _LIVE_LOCK:
        _ending = True
        running = list(_LIVE)
    for process in running:
        _signal_group(process, signal.SIGTERM)
    end = time.monotonic() + 1.0
    for process in running:
        try:
            process.wait(max(end - time.monotonic(), 0.01))
        except subprocess.TimeoutExpired:
            pass
        _signal_group(process, signal.SIGKILL)


atexit.register(stop_running)
if hasattr(threading, "_register_atexit"):   # CPython 3.9 and later; after the import of the pool, so that ours runs first
    threading._register_atexit(stop_running)


def _close(*pipes):
    for pipe in pipes:
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass


def _execute(argv, cwd, env, data, timeout, stall, cap, limit, on_stderr):
    """Runs `argv` and answers a Result. The command gets `data` on its input (or none), runs in a session of its own
    and is killed with its group when: `timeout` seconds passed, `cap` seconds passed, `stall` seconds passed with no
    byte on either output, its output went over `limit` (OutputLimit), or the caller was interrupted or `on_stderr`
    raised. `on_stderr(text)` is given what arrives on stderr as it arrives. A command that ended while something it
    started still holds its output is not waited for: after EXIT_GRACE seconds with no byte its own exit code is the
    answer, and what it left behind is killed. While the Lab UI exits it is not run (code 130)."""
    started = time.monotonic()
    with _LIVE_LOCK:
        if _ending:
            return Result(130, b"", ENDING)
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE if data else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, bufsize=0)
        _LIVE.add(process)
    out, tail = bytearray(), bytearray()
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    selector = selectors.DefaultSelector()
    reason, finished, stray = None, False, False
    try:
        pending = memoryview(data) if data else None
        if pending is not None:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "in")
        for name, pipe in (("out", process.stdout), ("err", process.stderr)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        last, ended = started, None

        def remaining():
            """The seconds before the first deadline, and what that deadline is."""
            now = time.monotonic()
            due = [(started + timeout - now, f"no answer within {timeout:g} s")] if timeout else []
            if cap:
                due.append((started + cap - now, f"still running after {cap:g} s"))
            if stall:
                due.append((last + stall - now, f"no byte for {stall:g} s"))
            return min(due) if due else (None, None)

        if _ending:   # the Lab UI began to exit while this started: stop_running did not see it
            reason = ENDING
        while reason is None and selector.get_map():
            wait, why = remaining()
            if wait is not None and wait <= 0:
                reason = why
                break
            if ended is None and process.poll() is not None:
                ended = time.monotonic()
            if ended is not None:
                left = max(ended, last) + EXIT_GRACE - time.monotonic()
                if left <= 0:
                    stray = True   # it ended and something it started holds the pipes, silent
                    break
                wait = left if wait is None else min(wait, left)
            elif wait is None or wait > POLL:
                wait = POLL
            for key, _ in selector.select(wait):
                if key.data == "in":
                    try:
                        sent = os.write(process.stdin.fileno(), pending[:65536])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        sent = len(pending)
                    pending = pending[sent:]
                    if not pending:
                        selector.unregister(process.stdin)
                        process.stdin.close()
                    continue
                try:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                last = time.monotonic()
                if key.data == "out":
                    out += chunk
                    if limit and len(out) > limit:
                        raise OutputLimit(f"git printed more than {limit} bytes")
                    continue
                tail += chunk
                del tail[:-STDERR_TAIL]
                if on_stderr:
                    on_stderr(decoder.decode(chunk))
        if reason is None and not stray:
            wait, why = remaining()
            try:
                process.wait(None if wait is None else max(wait, 0.05))
            except subprocess.TimeoutExpired:
                reason = why
        finished = reason is None and not stray
    finally:
        selector.close()
        if not finished:
            _kill(process)
        with _LIVE_LOCK:
            _LIVE.discard(process)
        _close(process.stdin, process.stdout, process.stderr)
    err = bytes(tail).decode("utf-8", "replace")
    if reason == ENDING:
        return Result(130, bytes(out), ENDING)
    if reason is not None:
        return Result(124, bytes(out), f"{err.rstrip()}\n{TIMEOUT_MARK} ({reason})".lstrip())
    return Result(process.returncode, bytes(out), err)


def _go(args, cwd, env, data, timeout, stall, cap, limit, on_stderr):
    exe = executable()
    if exe is None:
        raise fail("git_missing")
    argv, cwd, env = _prepare(exe, args, cwd, env)
    if isinstance(data, str):
        data = data.encode("utf-8")
    return _execute(argv, cwd, env, data, timeout, stall, cap, OUTPUT_LIMIT if limit is None else limit, on_stderr)


def run(args: List[str], cwd=None, timeout: float = 60, input=None, env: Optional[Dict[str, str]] = None,
        limit: Optional[int] = None) -> Result:
    """Runs git with `args` (git's arguments, without the word git) and answers a Result. No shell, no terminal, the
    environment of `environment()` plus `env`. `cwd` is a folder, else a neutral one outside any repository: a command
    on a store names it with `--git-dir`. Killed with its group after `timeout` seconds (exit code 124 and
    TIMEOUT_MARK in the errors). Raises CatalogError git_missing when there is no usable git, OutputLimit when the
    output is over `limit` bytes (default OUTPUT_LIMIT)."""
    return _go(args, cwd, env, input, timeout, None, None, limit, None)


def stream(args: List[str], cwd=None, env: Optional[Dict[str, str]] = None, stall: float = 30, cap: float = 600,
           on_stderr: Optional[Callable[[str], None]] = None, limit: Optional[int] = None) -> Result:
    """Like `run` for a command that talks to a network and reports progress: it is killed after `stall` seconds without
    a byte on either output, or `cap` seconds in all, and `on_stderr(text)` is given the errors as they arrive."""
    return _go(args, cwd, env, None, None, stall, cap, limit, on_stderr)


def require_ok(result: Result, address: Optional[Address] = None) -> Result:
    """The Result when git succeeded; else the CatalogError `classify` makes of what it said."""
    if result.code != 0:
        raise classify(result.err, address)
    return result


def version() -> Optional[Tuple[int, int, int]]:
    """The version of git as a tuple of three numbers, or None when there is no git (or only the macOS shim without
    the Command Line Tools: it is never run)."""
    exe = executable()
    if exe is None:
        return None
    argv, cwd, env = _prepare(exe, ["--version"], None, None)
    try:
        result = _execute(argv, cwd, env, None, 10, None, None, 4096, None)
    except (OSError, OutputLimit):
        return None
    found = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", result.out.decode("utf-8", "replace")) if result.code == 0 else None
    return tuple(int(part or 0) for part in found.groups()) if found else None


def require(minimum: Tuple[int, ...] = MINIMUM) -> Tuple[int, int, int]:
    """The version of git, or git_missing, or git_old when it is below `minimum`."""
    have = version()
    if have is None:
        raise fail("git_missing")
    if have < tuple(minimum):
        raise fail("git_old", have=".".join(str(part) for part in have))
    return have


# ---------------------------------------------------------------- what git says

# (code, phrases of git's or ssh's errors in lower case), in the order they are tried: a phrase of an earlier rule wins
_RULES = (
    ("timeout", (TIMEOUT_MARK, "timed out", "timeout was reached", "operation too slow")),
    ("bad_address", ("transport '", "protocol not allowed", "is not allowed by the policy")),
    ("certificate", ("ssl certificate problem", "certificate verify failed", "server certificate verification failed",
                     "self signed certificate", "self-signed certificate", "unable to get local issuer certificate",
                     "certificate has expired", "no alternative certificate subject name", "error setting certificate")),
    ("host_key", ("host key verification failed", "remote host identification has changed", "no matching host key")),
    ("ssh_denied", ("permission denied (publickey", "permission denied, please try again", "permission denied (password",
                    "too many authentication failures")),
    ("fsck", ("fsck error", "fsck failed", "badtimezone", "did not receive expected object", "index-pack failed",
              "pack has bad object", "bad object", "not a valid object name", "invalid index-pack output",
              "corrupt loose object", "inflate: data stream error")),
    ("not_found", ("repository not found", "returned error: 404", "does not appear to be a git repository",
                   "' not found", "repository does not exist", "project not found")),
    ("no_access", ("could not read username", "could not read password", "authentication failed", "terminal prompts disabled",
                   "invalid username or password", "returned error: 401", "returned error: 403", "http basic: access denied",
                   "permission to ", "access denied", "support for password authentication was removed",
                   "write access to repository not granted")),
    ("unreachable", ("could not resolve host", "could not resolve hostname", "name or service not known",
                     "nodename nor servname provided", "temporary failure in name resolution", "connection refused",
                     "no route to host", "network is unreachable", "failed to connect to", "couldn't connect to server",
                     "could not connect to server", "connection reset", "connection closed by", "remote end hung up",
                     "recv failure", "ssl_error_syscall", "tls connection was non-properly terminated", "early eof",
                     "proxy connect aborted", "from proxy after connect", "could not resolve proxy", "returned error: 500",
                     "returned error: 502", "returned error: 503", "returned error: 504")),
    # The last line git prints for any failure of an ssh connection, the network's included: it says "access" only when
    # nothing above said what went wrong
    ("no_access", ("make sure you have the correct access rights",)),
)
_MISSING_REF = re.compile(r"couldn't find remote ref (?:refs/heads/)?(\S+)", re.I)


def classify(stderr: str, address: Optional[Address]) -> CatalogError:
    """The CatalogError of what git (or ssh) said, by the mapping of the catalogue of errors; the line never holds git's words, which
    go to `detail["output"]` with the credentials removed. What no rule knows is `git_failed`."""
    text = stderr or ""
    low = text.lower()
    output = redact(text).strip()[-2000:]
    missing = _MISSING_REF.search(text)
    if missing:
        return fail("branch_missing", address, branch=missing.group(1), output=output)
    for code, phrases in _RULES:
        if any(phrase in low for phrase in phrases):
            return fail(code, address, output=output)
    return fail("git_failed", address, output=output)


# ---------------------------------------------------------------- remotes

_SHA = re.compile(r"[0-9a-f]{40}")


# The options of a question asked as a stranger: no credential helper and no stored header
STRANGER = ["-c", "credential.helper=", "-c", "http.extraHeader="]


def ls_remote(address: Address, branch: Optional[str] = None, anonymous: bool = False, timeout: float = 20) -> dict:
    """What the remote has: {"branch": the branch asked for or the default one, "head": the sha it points to}. Raises
    the classified error of a failure; `branch_missing` for a branch the remote lacks (an empty `branch` asks for the
    default one: `asked_branch`); `not_catalog` for an empty repository. `anonymous` asks as a stranger would, to
    measure whether the catalog is Public: no credential helper, no stored header, a home folder that is empty, so that
    the `.netrc` of the person, which the HTTP library answers a 401 with, is not read, and neither their configuration nor
    the system's (/etc/gitconfig, Homebrew's), whose headers, cookies or rewritten addresses could answer for them. Only
    how to reach the host is carried (network_options), in the environment of the command, never in its arguments, which
    anyone on the machine may list. An ssh address cannot be asked that way and answers `no_access` without a command."""
    checked(address)
    branch = asked_branch(branch, address)
    if anonymous and is_ssh(address):
        raise fail("no_access", address)
    if not anonymous:
        return _ls_remote(address, branch, timeout, [], {})
    network = network_options()   # read with the person's and the system's configuration, before both are left behind
    with tempfile.TemporaryDirectory(prefix="gravitee-lab-stranger-") as empty:
        alone = {"HOME": empty, "XDG_CONFIG_HOME": empty, "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_CONFIG_GLOBAL": os.path.join(empty, "gitconfig"), **_config_env(network)}
        return _ls_remote(address, branch, timeout, STRANGER, alone)


# The settings of the person's Git that only say how to reach a host (a proxy, the certificates to trust), which a stranger's
# question takes along: never a credential helper, an askpass, a header, a cookie file or anything else of theirs
NETWORK = re.compile(r"https?\.proxy|http\.sslcainfo|http\.sslcapath|https?\..+\.proxy")


def network_options() -> List[Tuple[str, str]]:
    """[(key, value)] of the Git settings of the person and of the system that NETWORK names, as their configuration says them."""
    result = run(["config", "--null", "--get-regexp", r"^(https?\.|http\..*\.)"], timeout=10)
    if result.code != 0:
        return []
    settings = []
    for record in result.out.decode("utf-8", "replace").split("\0"):
        key, _, value = record.partition("\n")
        if NETWORK.fullmatch(key.lower()) and value and "\n" not in value:
            settings.append((key, value))
    return settings


def _config_env(settings) -> Dict[str, str]:
    """The settings as git reads them from its environment (Git 2.31 and later: an older one asks without them, and a doubt is
    private), where a key may hold an equals sign and a value, a proxy's password, is not in the command line."""
    env = {"GIT_CONFIG_COUNT": str(len(settings))}
    for n, (key, value) in enumerate(settings):
        env[f"GIT_CONFIG_KEY_{n}"], env[f"GIT_CONFIG_VALUE_{n}"] = key, value
    return env


def _ls_remote(address, branch, timeout, options, env):
    refs = ["HEAD"] + ([f"refs/heads/{branch}"] if branch else [])
    how = {**address_env(address), **env}
    result = run([*options, "ls-remote", "--symref", "--", address.url, *refs], timeout=timeout, env=how)
    require_ok(result, address)
    heads, symref = {}, None
    for line in result.out.decode("utf-8", "replace").splitlines():
        if line.startswith("ref: ") and line.endswith("\tHEAD"):
            symref = line[5:-5]
            continue
        sha, _, name = line.partition("\t")
        if _SHA.fullmatch(sha) and name:
            heads[name] = sha
    if branch is not None:
        if f"refs/heads/{branch}" not in heads:
            raise fail("branch_missing", address, branch=branch)
        return {"branch": branch, "head": heads[f"refs/heads/{branch}"]}
    if "HEAD" not in heads:
        raise fail("not_catalog", address)
    if symref is None or not symref.startswith("refs/heads/"):
        symref = _default_branch(address, heads["HEAD"], timeout, options, how)
    return {"branch": valid_branch(symref[len("refs/heads/"):], address), "head": heads["HEAD"]}


def _default_branch(address, head, timeout, options, env):
    """The branch of a remote that did not say which one HEAD is (a server without symref): the one at the same
    commit, `main` or `master` first. Asked the way the question before it was."""
    result = run([*options, "ls-remote", "--heads", "--", address.url], timeout=timeout, env=env)
    require_ok(result, address)
    names = []
    for line in result.out.decode("utf-8", "replace").splitlines():
        sha, _, name = line.partition("\t")
        if sha == head and name.startswith("refs/heads/"):
            names.append(name)
    for preferred in ("refs/heads/main", "refs/heads/master"):
        if preferred in names:
            return preferred
    if not names:
        raise fail("branch_missing", address, branch="HEAD")
    return sorted(names)[0]


def push_probe(address: Address, commit: str, store=None, timeout: float = 20) -> str:
    """Whether the person can push to the remote: "yes", "no" (the remote refused the sign-in or the permission) or
    "unknown" (the network, a certificate, a timeout: the question was not answered). A dry run of a push of `commit`
    to a branch that does not exist (nothing is sent). `store` is the bare repository that holds `commit`; without it,
    or when it lacks the commit, an empty commit made in a throwaway repository is pushed instead."""
    if address.kind != "git":
        return "unknown"
    checked(address)
    target = f"refs/heads/lab/probe-{secrets.token_hex(4)}"
    with tempfile.TemporaryDirectory(prefix="gravitee-lab-probe-") as scratch:
        git_dir, commit = _probe_commit(store, commit, scratch)
        result = run(["--git-dir", git_dir, "push", "--dry-run", "--", address.url, f"{commit}:{target}"],
                     timeout=timeout, env=address_env(address))
    if result.code == 0:
        return "yes"
    return "no" if classify(result.err, address).code in ("no_access", "ssh_denied", "not_found") else "unknown"


def _probe_commit(store, commit, scratch):
    """(repository, commit) a probe pushes: the given one when the store holds it, else an empty commit of a repository
    made in `scratch`."""
    if store is not None and _SHA.fullmatch(commit or "") and \
            run(["--git-dir", str(store), "cat-file", "-e", f"{commit}^{{commit}}"]).code == 0:
        return str(store), commit
    git_dir = os.path.join(scratch, "probe.git")
    require_ok(run(["init", "--bare", "--quiet", "--template=", "--", git_dir]))
    tree = require_ok(run(["--git-dir", git_dir, "hash-object", "-t", "tree", "-w", "--stdin"], input=b"")).out
    identity = {"GIT_AUTHOR_NAME": "lab", "GIT_AUTHOR_EMAIL": "lab@example.invalid",
                "GIT_COMMITTER_NAME": "lab", "GIT_COMMITTER_EMAIL": "lab@example.invalid"}
    made = require_ok(run(["--git-dir", git_dir, "commit-tree", tree.decode().strip(), "-m", "probe"], env=identity))
    return git_dir, made.out.decode().strip()
