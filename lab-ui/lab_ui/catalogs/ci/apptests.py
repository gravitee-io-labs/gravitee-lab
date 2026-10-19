"""The tests of an app, run in the app's own image: what the CI of a catalog runs (`catalog.py ci test`, standard 5.6).

    python3 -m lab_ui.catalogs.ci.apptests [--keep] [FOLDER ...]      (from lab-ui/; `catalog.py ci test` runs the same)

Each FOLDER is an app (it holds a Dockerfile) or a folder that holds apps in `apps/*/` (a playground, a catalog's
`playgrounds/`); with none, `playgrounds/` of the project. Each app is built (`docker build`, quietly), its tests are run in its
image, and the image is removed (`--keep` keeps it). Exit 0 when every app that has tests passed, 1 when one failed, 2 when
Docker cannot be used.

The tests of an app ship in its image (`COPY tests ./tests`), so they run with the versions the app runs on, offline and
hardened. The flag set below was run against the images of the engine's tree: every app that has tests passed. Two things it
found: the default `--tmpfs /tmp` is `noexec` (an app runs a script from /tmp), so `exec` is explicit, and `--read-only` alone
fails without a writable /tmp. So the contract for app authors is that **tests write under /tmp only**, and the run checks it.

    docker run --rm --pull never --network none --cap-drop ALL --security-opt no-new-privileges --pids-limit 512 --memory 2g
        --cpus 2 --read-only --tmpfs /tmp:rw,exec,nosuid,nodev,size=256m -e PYTHONDONTWRITEBYTECODE=1 [-v GUIDE:/guide.md:ro -e
        GUIDE=/guide.md] --entrypoint python IMAGE -m unittest discover -s tests -t .

The JavaScript tests of an app (`tests/js`) run in the Node image that the Makefile pins (`NODE_IMAGE`, read from there: one pin),
with the app mounted read-only at /app, no network and the same hardening, with `node --test`. The guide of the playground is
given to the tests that read it (GUIDE). An app in another language is not run.

A run is given a name of its own (`--name lab-test-<8 hexadecimal digits>`, the one flag added to the set of 5.6) so that a run
that passes its time limit can be ended whole: killing the `docker run` process does not stop the container, which would go on
with its two CPUs and its 2 GB, on a person's machine, until it ended by itself; `docker rm -f` of that name does. The Node image
is pulled again after 20 s and after 60 s when Docker Hub or the network refuses, as a build is (`ci build`, BD05), and when it
still cannot be had that is said as what it is, never as a test that failed.

Importable: `apps.py`, beside it, runs the same commands through the runner of the Lab. Standard library only, Python 3.9 or later."""

import argparse
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, NamedTuple, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[4]   # lab-ui/lab_ui/catalogs/ci/ is four folders under the repository
MAKEFILE = ROOT / "Makefile"

# The flags of a run of tests. `--pull never`: the image is the one built here, never another of the same name
HARDENING = ["--pull", "never", "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "512",
             "--memory", "2g", "--cpus", "2", "--read-only", "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=256m"]
PYTHON_TESTS = ["-m", "unittest", "discover", "-s", "tests", "-t", "."]
NODE_TESTS = ["node", "--test", "--test-timeout=60000", "--test-reporter=dot", "tests/js/*.test.mjs"]
PYTHON_TIMEOUT = 900   # seconds for the tests of one app; a test that never ends does not hold a job
NODE_TIMEOUT = 600
PULL_TIMEOUT = 600
BUILD_TIMEOUT = 900
NODE_PIN = re.compile(r"^NODE_IMAGE\s*\?=\s*(\S+)\s*$", re.M)
SUMMARY = re.compile(r"^FAILED \((.*)\)\s*$", re.M)
COUNT = re.compile(r"(failures|errors)=(\d+)")
RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)
# The last line of a run that never started a test: unittest cannot import the folder it was told to start from, or python cannot open it
NO_START = "ImportError: Start directory is not importable"
CANT_OPEN = "can't open file"
# What Docker Hub or the network says when the fault is theirs (the same words as `ci/build.py` INFRA reads in a build's log)
INFRA = re.compile(
    r"toomanyrequests|too many requests|rate limit|TLS handshake timeout|tls: |i/o timeout|dial tcp|no such host|"
    r"server misbehaving|temporary failure in name resolution|connection reset by peer|unexpected EOF|context deadline exceeded|"
    r"net/http: request canceled|(?:unexpected|received unexpected HTTP) status[^\n]*\b5\d\d\b|\b5\d\d (?:Internal Server Error|"
    r"Bad Gateway|Service Unavailable|Gateway Time-?out)", re.I)
PULL_RETRIES = (20, 60)   # seconds before the first and the second new try of a pull that Docker Hub or the network refused

Runner = Callable[[List[str], float, Optional[str]], Tuple[int, str, str]]


class Missing(Exception):
    """A pin or a file this needs is not where it is read."""


class Tests(NamedTuple):
    """What an app has to run, by its files alone."""
    python: bool   # tests/ holds a test*.py
    js: bool       # tests/js holds a *.test.mjs

    @property
    def any(self) -> bool:
        return self.python or self.js


class Outcome(NamedTuple):
    """How the tests of one app ended. `status`: passed, failed or none (no tests in the app, or none in its image).
    `failed` is how many tests failed; `output` is what they printed."""
    app: str
    status: str
    failed: int = 0
    python: str = "none"
    js: str = "none"
    output: str = ""


def node_image(environ=None, makefile: Optional[Path] = None) -> str:
    """The Node image of the JavaScript tests: `$NODE_IMAGE` when it is set (as `make NODE_IMAGE=...` does), else the
    pin in the Makefile of the Lab. Missing when neither says."""
    environ = os.environ if environ is None else environ
    if environ.get("NODE_IMAGE"):
        return environ["NODE_IMAGE"]
    try:
        text = (makefile or MAKEFILE).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise Missing("the Makefile of the Lab cannot be read, so the Node image of the tests is not known") from None
    found = NODE_PIN.search(text)
    if not found:
        raise Missing("the Makefile of the Lab pins no NODE_IMAGE")
    return found.group(1)


def has_tests(app) -> Tests:
    """Whether an app folder has Python tests (a `test*.py` under `tests/`) and JavaScript tests (`tests/js/*.test.mjs`), by looking
    at its files. A link is not followed, and nothing deeper than four folders is looked at."""
    folder = Path(app) / "tests"
    python = js = False
    try:
        if not folder.is_dir() or folder.is_symlink():
            return Tests(False, False)
        for top, folders, names in os.walk(folder):
            relative = os.path.relpath(top, folder)
            if relative.count(os.sep) >= 4:
                folders[:] = []
            folders[:] = [name for name in folders if not os.path.islink(os.path.join(top, name))]
            for name in names:
                if os.path.islink(os.path.join(top, name)):
                    continue
                if relative == "js" and name.endswith(".test.mjs"):
                    js = True
                elif name.startswith("test") and name.endswith(".py"):
                    python = True
    except OSError:
        pass
    return Tests(python, js)


def guide_of(app, unit_folder=None) -> Optional[str]:
    """The guide of the playground an app is part of, the real path of a regular file: `guide.md` of `unit_folder`, else two
    folders above the app (`playgrounds/bank/apps/bakery-web` is in `playgrounds/bank`). None when there is none (a link is none)."""
    folder = Path(unit_folder) if unit_folder else Path(app).parent.parent
    guide = folder / "guide.md"
    return str(guide.resolve()) if guide.is_file() and not guide.is_symlink() else None


def container_name() -> str:
    """A name for one run of tests, so that the container can be removed by name: `lab-test-` and 8 hexadecimal digits."""
    return "lab-test-" + secrets.token_hex(4)


def python_command(image: str, guide: Optional[str] = None, name: Optional[str] = None) -> List[str]:
    """The `docker run` of the Python tests of an image, as 5.6 words it (`name`: the container's, `--name` right after `--rm`)."""
    cmd = ["docker", "run", "--rm", *(["--name", name] if name else []), *HARDENING, "-e", "PYTHONDONTWRITEBYTECODE=1"]
    if guide:
        cmd += ["-v", f"{guide}:/guide.md:ro", "-e", "GUIDE=/guide.md"]
    return cmd + ["--entrypoint", "python", image, *PYTHON_TESTS]


def node_command(app: str, node: str, name: Optional[str] = None) -> List[str]:
    """The `docker run` of the JavaScript tests of an app in the Node image, the app mounted read-only."""
    return ["docker", "run", "--rm", *(["--name", name] if name else []), *HARDENING, "-v", f"{app}:/app:ro", "-w", "/app", node,
            *NODE_TESTS]


def failures(output: str, js: bool = False) -> int:
    """How many tests failed, from what the runner printed: the `FAILED (failures=2, errors=1)` of unittest (3), the `✖` lines
    that node's dot reporter lists under 'Failed tests:'. At least 1, since the caller knows the run failed."""
    count = 0
    if js:
        listed = output.split("Failed tests:", 1)
        count = len(re.findall(r"^✖ ", listed[1], re.M)) if len(listed) == 2 else 0
    else:
        for found in SUMMARY.finditer(output):
            count += sum(int(number) for _, number in COUNT.findall(found.group(1)))
    return max(count, 1)


def ran_none(code: int, output: str) -> bool:
    """Whether a run of unittest found no test at all: 'Ran 0 tests' (exit 5 from Python 3.12 on), or no `tests` folder in the
    image to start from. Read from the end of the output, where unittest says it: a test that prints such a line itself, or runs
    a unittest of its own, is not the run (the last `Ran N tests` is the run's, and the other cause is the last line)."""
    lines = [line for line in output.splitlines() if line.strip()]
    last = lines[-1] if lines else ""
    if last.startswith(NO_START) or ("python" in last[:100] and CANT_OPEN in last[:300]):
        return True
    found = RAN.findall(output)
    return bool(found and int(found[-1]) == 0)


def stopped(run: Runner, name: str) -> None:
    """Removes the container of a run that passed its time limit: the process of `docker run` was ended, the container was not."""
    run(["docker", "rm", "-f", name], 60, None)


def run_python(image: str, run: Runner, guide: Optional[str] = None, namer: Callable[[], str] = container_name) -> Tuple[str, int, str]:
    """(passed or failed or none, how many failed, the output) of the Python tests of an image."""
    name = namer()
    code, out, err = run(python_command(image, guide, name), PYTHON_TIMEOUT, None)
    if code == 124:
        stopped(run, name)
    text = (out or "") + (err or "")
    if code == 0 and not ran_none(code, text):
        return "passed", 0, text
    if ran_none(code, text):
        return "none", 0, text
    return "failed", failures(text), text


def run_js(app: str, node: str, run: Runner, namer: Callable[[], str] = container_name) -> Tuple[str, int, str]:
    """(passed or failed, how many failed, the output) of the JavaScript tests of an app, in the Node image."""
    name = namer()
    code, out, err = run(node_command(app, node, name), NODE_TIMEOUT, None)
    if code == 124:
        stopped(run, name)
    text = (out or "") + (err or "")
    return ("passed", 0, text) if code == 0 else ("failed", failures(text, True), text)


def ensure_node(node: str, run: Runner, sleep: Callable[[float], None] = time.sleep) -> Optional[str]:
    """The Node image is here, pulled quietly first when it is not, so that the log of a test run has no download progress. A pull
    that Docker Hub or the network refuses (a rate limit on a runner with no account, a TLS timeout, a 5xx) is tried again after
    20 s and after 60 s: it is not the author's change. None when the image is here, else why it could not be had (the last line
    Docker printed)."""
    code, _, _ = run(["docker", "image", "inspect", node], 60, None)
    if code == 0:
        return None
    for attempt in range(len(PULL_RETRIES) + 1):
        code, out, err = run(["docker", "pull", "-q", node], PULL_TIMEOUT, None)
        if code == 0:
            return None
        text = err or out or ""
        said = (text.strip().splitlines() or ["docker pull failed"])[-1][:300]
        if attempt == len(PULL_RETRIES) or not INFRA.search(text):
            return said
        sleep(PULL_RETRIES[attempt])
    return "docker pull failed"   # (not reached)


def run_app(app: str, image: str, run: Runner, node: Optional[str] = None, guide: Optional[str] = None,
             name: Optional[str] = None, tests: Optional[Tests] = None, namer: Callable[[], str] = container_name) -> Outcome:
    """Runs the tests of one app in its image: Python tests in the image, JavaScript tests in the Node image. An app with no
    tests is `none`. `node` is the Node image; None when it could not be had (the JavaScript tests then fail with that said)."""
    name = name or Path(app).name
    found = tests or has_tests(app)
    python = js = "none"
    failed, output = 0, ""
    if found.python:
        python, count, text = run_python(image, run, guide, namer)
        failed, output = failed + (count if python == "failed" else 0), output + text
    if found.js:
        if node is None:
            js, failed = "failed", failed + 1
            output += "\nThe Node image of the JavaScript tests could not be had.\n"
        else:
            js, count, text = run_js(app, node, run, namer)
            failed, output = failed + (count if js == "failed" else 0), output + text
    if "failed" in (python, js):
        return Outcome(name, "failed", failed, python, js, output)
    if python == "none" and js == "none":
        return Outcome(name, "none", 0, python, js, output)
    return Outcome(name, "passed", 0, python, js, output)


# ---------------------------------------------------------------------------------------------------------------------
# On this machine
# ---------------------------------------------------------------------------------------------------------------------

def local_run(argv: List[str], timeout: float, cwd: Optional[str]) -> Tuple[int, str, str]:
    """(exit code, output, errors) of a command, with no input and its own process group, ended with everything it started at
    `timeout` seconds: the runner of a person's terminal. 127 when it cannot be started, 124 at the time limit."""
    try:
        process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   encoding="utf-8", errors="replace", start_new_session=True)
    except OSError as error:
        return 127, "", str(error)
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()
        process.communicate()
        return 124, "", f"{argv[0]} did not answer within {int(timeout)} s"
    return process.returncode, out, err


def display(app: str) -> str:
    """The folder of an app as a person reads it in the log: from where the command runs, else from the project, else whole."""
    for base in (os.getcwd(), str(ROOT)):
        relative = os.path.relpath(app, base)
        if not relative.startswith(".."):
            return relative + "/"
    return app + "/"


def apps_in(folders: Sequence[str]) -> List[str]:
    """The apps (folders with a Dockerfile) that these folders are or hold in `apps/*/`, sorted, each once."""
    found: List[str] = []
    for each in folders:
        top = Path(each)
        if (top / "Dockerfile").is_file():
            found.append(str(top))
            continue
        found += sorted(str(item.parent) for item in top.glob("*/apps/*/Dockerfile")) if top.is_dir() else []
        found += sorted(str(item.parent) for item in top.glob("apps/*/Dockerfile")) if top.is_dir() else []
    return list(dict.fromkeys(found))


def main(argv: Optional[List[str]] = None, run: Runner = local_run, out=None, err=None, sleep: Callable[[float], None] = time.sleep) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    parser = argparse.ArgumentParser(prog="apptests", description="Build each app and run its tests in its image.")
    parser.add_argument("folders", nargs="*", help="an app, or a folder that holds apps (default: playgrounds/)")
    parser.add_argument("--keep", action="store_true", help="keep the images")
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2
    code, _, said = run(["docker", "info", "--format", "{{.ServerVersion}}"], 60, None)
    if code != 0:
        print("Docker is not running: start Docker Desktop (or the docker service), then try again" if code != 127 else
              "Docker is not installed or not on the PATH", file=err)
        return 2
    folders = args.folders or [str(ROOT / "playgrounds")]
    apps = apps_in(folders)
    if not apps:
        print("No app to test: no folder with a Dockerfile in " + ", ".join(folders), file=out)
        return 0
    status = 0
    try:
        node = node_image()
    except Missing as error:
        print(str(error), file=err)
        return 2
    node_error = ensure_node(node, run, sleep)
    for app in apps:
        shown = display(app)
        print(f"== {shown}", file=out)
        tag = "lab-test-" + re.sub(r"[^a-z0-9_.-]", "-", Path(app).name.lower())
        code, built, error = run(["docker", "build", "-q", "-t", tag, app], BUILD_TIMEOUT, None)
        if code != 0:
            print(error or built, file=err)
            status = 1
            continue
        outcome = run_app(app, tag, run, None if node_error else node, guide_of(app))
        if outcome.output:
            print(outcome.output.rstrip("\n"), file=err if outcome.status == "failed" else out)
        if outcome.status == "none":
            print(f"{Path(app).name} has no tests", file=out)
        if outcome.status == "failed":
            status = 1
        if not args.keep:
            run(["docker", "rmi", tag], 60, None)
    return status


if __name__ == "__main__":
    if sys.version_info < (3, 9):
        sys.exit("Python 3.9 or later is needed: this is " + sys.version.split()[0])
    sys.exit(main())
