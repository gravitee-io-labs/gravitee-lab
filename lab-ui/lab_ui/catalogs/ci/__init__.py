"""`catalog.py ci <stage>`: the stages that a catalog's workflows and `make ci` call, and what they share.

    needs       the catalogs this one requires, fetched and linked      (needs.py)
    plan        what a change touches                                  (plan.py)
    gitleaks    the secrets of every commit, with the Lab's own rules  (gitleaks.py)
    manifests   every image a touched unit pulls exists                (manifests.py)
    build       the apps of the touched units, on one architecture     (build.py)
    test        the tests of each app, in its image, offline           (apps.py)
    run         the local runner behind `make ci`                      (run.py)
    smoke, publish-plan, publish, release-notes, links, scan-published, e2e-plan, e2e-gate

A stage is `main(argv, stdout=None, stderr=None, **seams) -> int` of its module, `argv` being what follows the stage word;
`STAGES` says which module and which function. A stage whose module is not in this Lab yet answers "not available yet" and
exits 2, so that the command line does not wait for the packages that write them. Exit 0 it worked, 1 it found
something to fix, 2 the command or the machine cannot do it (a stage that cannot run is never a green one).

What every stage shares is here:

- **The output.** `--format text|github|json` for the findings (`report`), `--ci` for the Markdown summary in
  `$GITHUB_STEP_SUMMARY`; the outputs of a step in `$GITHUB_OUTPUT` are written by `write_outputs` and by nothing else, one
  line each, a flag or a `json.dumps(..., ensure_ascii=True)`: a name from the tree never reaches a line of its own.
- **The messages of the stages**: `problem(code, path, **fields)`.
- **The units a stage works on.** `--units` is a JSON list of `kind:name` (`parse_units` refuses anything else). `load` gives
  the resolved Compose JSON of each one the trust policy judges OK, and says why it left out the others. Compose is run only
  there, only for units the Lab's own screen let through, with the scrubbed environment. The structure rules come first:
  a unit that holds a file they call unreadable (ST01 to ST04) is refused before anything is read, and is left out of the
  temporary project the others are read in, so that no Compose call of the Lab's project names it either (`hidden`); with no
  unit left there is no reading at all. The default loader is `lab_loader`: the project as the Lab reads it, in the temporary
  project the check reads a folder in (`check.temporary_link`: nothing is linked into the Lab and nothing is written to it),
  then `docker compose config` of the Lab's core and the unit with `compose.scrubbed_environment()` and every profile on
  (`COMPOSE_PROFILES=*`: Compose leaves a service that has `profiles` out of what it prints, and a service nobody read is a
  service nobody judged, so a unit that has one is refused); `LOADER` replaces it (the tests).
- **Docker.** Everything that is not `config` runs with `compose.build_environment()`: the same allow-list with the real
  `HOME` and `DOCKER_CONFIG` (`run_docker`); no `ACTIONS_*`, no token, no agent socket reaches an app's build or its tests.

Python 3.9 and later, standard library only."""

import argparse
import collections
import contextlib
import functools
import importlib
import json
import os
import platform as hostplatform
import re
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

from ...library import check
from ...docker import compose
from ... import paths
from .. import explain, report, store, tree as trees, trust
from ..report import ERROR, WARNING, Problem
from ..rules import say

# The stage word, then the module of this package and the function that answers it. A package that writes a stage names its
# functions as this table does, or says which line to change
STAGES = {
    "needs": ("needs", "main"),
    "plan": ("plan", "main"),
    "gitleaks": ("gitleaks", "main"),
    "manifests": ("manifests", "main"),
    "build": ("build", "main"),
    "test": ("apps", "main"),
    "run": ("run", "main"),
    "smoke": ("smoke", "main"),
    "publish-plan": ("publish", "plan_main"),
    "publish": ("publish", "main"),
    "release-notes": ("release", "main"),
    "links": ("links", "main"),
    "scan-published": ("scan", "main"),
    "e2e-plan": ("e2e", "plan_main"),
    "e2e-gate": ("e2e", "gate_main"),
}
SUMMARY = {
    "needs": "fetch and link the catalogs this one requires",
    "plan": "what a change touches",
    "gitleaks": "the secrets of every commit, with the Lab's own rules",
    "manifests": "every image a touched unit pulls exists in its registry",
    "build": "build the apps of the touched units",
    "test": "run the tests of each app in its image",
    "run": "plan, manifests, build and test on this machine, as make ci",
    "smoke": "start what needs no platform",
    "publish-plan": "the gates and the images to publish",
    "publish": "build and push the images",
    "release-notes": "the notes of a release",
    "links": "the links of the guides answer",
    "scan-published": "scan the published images",
    "e2e-plan": "the matrix of the end-to-end run",
    "e2e-gate": "the end-to-end run's last gate",
}

UNIT = re.compile(r"(addon|playground|setup):([a-z0-9-]{1,100})")
MAX_UNITS = 1000   # the most --units may name; a catalog is limited to 500 (ST10)
FORMATS = report.FORMATS

# What an image reference may be made of (a registry host, a path, a tag, a digest): anything else is not given to a registry or to
# Docker. The first character is a letter or a digit, so that no name is an option
IMAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@+-]{0,254}")

# The platforms a catalog may build for, each with what a job needs to run it (the matrix). Anything else is SD04
PLATFORMS = {
    "linux/amd64": {"arch": "amd64", "platform": "linux/amd64", "runner": "ubuntu-24.04"},
    "linux/arm64": {"arch": "arm64", "platform": "linux/arm64", "runner": "ubuntu-24.04-arm"},
}
# The Lab's core file names the seeder; a service built from it is the Lab's own code
SEEDER_DIR = "seeder"

# The messages of the stages word for word: (severity, message). A test keeps them equal to the document and to
# the table of `explain`
MESSAGES = {
    "BD01": (ERROR, "{image}: the build failed at step {n}/{m}, {instruction} (exit {code}). Fix: run make ci to see it on your "
                    "machine; the last 15 lines are in the summary."),
    "BD02": (ERROR, "{service}: build.{key} is not accepted. Fix: use context, dockerfile, args, target and labels only."),
    "BD04": (ERROR, "{image}: the build took over 10 minutes and was stopped."),
    "BD05": (ERROR, "Docker Hub refused the pull (rate limit). This is not your change: run the job again in a few minutes."),
    "BD06": (ERROR, "Split this pull request: it changes {n} apps, the limit is 20."),
    "MF01": (ERROR, "{image}: the registry has no tag {tag}. Fix: use a tag that exists, or an earlier version."),
    "MF02": (WARNING, "{registry} did not answer: {image} was not checked."),
    "TS01": (ERROR, "{app}: {n} tests failed. Fix: run make ci APP={app} to see them."),
    "TS02": (WARNING, "{app} has no tests. Fix: COPY tests ./tests in the Dockerfile and add some."),
    "SD04": (ERROR, ".github/standard.json: {problem}."),
    "ND01": (ERROR, "{ids}: this catalog needs these catalogs and the secret NEEDS_DEPLOY_KEY is not set. Fix: give the repository a "
                    "read-only deploy key of theirs: make settings prints the commands."),
    "ND02": (ERROR, "{name} ({repository}) could not be fetched or linked: {why}. Fix: NEEDS_DEPLOY_KEY must be a deploy key of that "
                    "repository, and ref a commit of it."),
}
# The one case 6.11 has no sentence for: the build failed and Docker did not say at which step (a failure before the first, a
# frontend that answered otherwise). The same words, without the step
BD01_PLAIN = "{image}: the build failed (exit {code}). Fix: run make ci to see it on your machine; the last 15 lines are in the " \
             "summary."


# What a stage says when Docker is not here, and what the Lab's gitleaks image says before its digest is written: the words that
# `make ci` reads to skip the scan on a machine that cannot run it, and to show every other reason a stage could not run
NO_DOCKER = "Docker is not installed or not on the PATH"
DOCKER_DOWN = "Docker is not running"
NOT_PINNED_YET = "is not pinned yet"
SKIPPABLE = (NO_DOCKER, DOCKER_DOWN, NOT_PINNED_YET)

# The longest line of a stranger's program that a message or a summary holds (and the most a summary file is given)
LINE_WIDTH = 500
SUMMARY_LIMIT = 900_000   # bytes: GitHub keeps about 1 MiB of the summary of a step and drops the whole of it when it is over


class Usage(Exception):
    """The command cannot be run as asked (exit 2): the message says what to give."""


class Unavailable(Exception):
    """This machine cannot do what the stage needs (Docker is not there, the catalog cannot be read): exit 2, and the message
    says what to do. Never taken for a verdict on the catalog."""


def problem(which: str, path: str = "", line: int = 0, severity: Optional[str] = None, **fields) -> Problem:
    """The Problem of a stage code (`which`: BD01...), its message made from the table above with `fields` (a stranger's text in
    a field is never read for places of its own)."""
    wanted, message = MESSAGES[which]
    return Problem(which, severity or wanted, path, int(line or 0), say(message, **fields))


# ---------------------------------------------------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------------------------------------------------

class Help(Exception):
    """The help of a stage was asked for and printed: the stage ends with exit 0."""


class Parser(argparse.ArgumentParser):
    """argparse that raises Usage instead of printing and exiting, so that every stage ends with its exit code. `--help` prints
    the help and ends the stage with exit 0 (Help)."""

    def error(self, message):
        raise Usage(message)

    def exit(self, status=0, message=None):
        if status == 0 and not message:
            raise Help()
        raise Usage((message or "").strip() or "usage")


def parser(stage: str, text: str, units: bool = False, fmt: bool = True, ci: bool = True) -> Parser:
    """The parser of a stage: PATH (the catalog, `.` by default), and `--units`, `--format`, `--ci` when asked."""
    found = Parser(prog=f"catalog.py ci {stage}", description=text)
    found.add_argument("path", nargs="?", default=".", help="the catalog folder (default: the current folder)")
    if units:
        found.add_argument("--units", required=True, help='the units to work on, a JSON list: ["playground:bank"]')
    if fmt:
        found.add_argument("--format", choices=FORMATS, default=None, help="text (default), github or json")
    if ci:
        found.add_argument("--ci", action="store_true", help="the run is CI's: GitHub annotations and the step summary")
    return found


def parsed(found: Parser, argv: Sequence[str]) -> argparse.Namespace:
    """The arguments, with `--ci` meaning `--format github` unless a format was given."""
    args = found.parse_args(list(argv))
    if getattr(args, "format", "x") is None:
        args.format = "github" if getattr(args, "ci", False) else "text"
    return args


def parse_units(text: str) -> List[Tuple[str, str]]:
    """[(kind, name)] of a `--units` value: a JSON list of `kind:name`, each once, in the order given. Anything else is a Usage
    (the plan wrote it, but a workflow can be edited): a name is lowercase letters, digits and dashes, a kind is addon,
    playground or setup, and the list is at most MAX_UNITS long."""
    try:
        items = json.loads(text)
    except (TypeError, ValueError, RecursionError):
        raise Usage('--units is not JSON: give a list of kind:name, for example ["playground:bank"]') from None
    if not isinstance(items, list) or len(items) > MAX_UNITS:
        raise Usage('--units must be a JSON list of kind:name, for example ["playground:bank"]')
    found: List[Tuple[str, str]] = []
    for item in items:
        match = UNIT.fullmatch(item) if isinstance(item, str) else None
        if match is None:
            raise Usage('--units must be a JSON list of kind:name, for example ["playground:bank"]: '
                        f"{report.printable(str(item)[:40])} is not one")
        if match.groups() not in found:
            found.append(match.groups())
    return found


def entry(function: Callable) -> Callable:
    """The `main` of a stage: `function(argv, out, err, **seams) -> exit code`, with Usage and Unavailable answered as exit 2 on
    `err` (one line, nothing of a tree in it but what `printable` lets through) and an interrupt as 130."""
    @functools.wraps(function)
    def main(argv=None, stdout=None, stderr=None, **seams):
        out, err = stdout or sys.stdout, stderr or sys.stderr
        try:
            return function(list(argv or []), out, err, **seams)
        except Help:
            return 0
        except (Usage, Unavailable) as error:
            print(f"catalog.py ci: {report.printable(error)}", file=err)
            return 2
        except trees.TreeError as error:   # (a catalog that lists more files than any is read: the stage cannot say anything of it)
            print(f"catalog.py ci: {report.printable(error)}", file=err)
            return 2
        except KeyboardInterrupt:
            return 130
    return main


def main(argv=None, stdout=None, stderr=None, **seams) -> int:
    """`catalog.py ci <stage> ...`: the stage word, then the arguments of that stage. A stage whose module is not in this Lab
    exits 2 with "not available yet"."""
    out, err = stdout or sys.stdout, stderr or sys.stderr
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(usage(), file=out if argv else err)
        return 0 if argv else 2
    stage, rest = argv[0], argv[1:]
    if stage not in STAGES:
        print(f"catalog.py ci: {report.printable(stage[:40])} is not a stage.\n{usage()}", file=err)
        return 2
    name, function = STAGES[stage]
    target = f"{__name__}.{name}"
    try:
        module = importlib.import_module(target)
    except ModuleNotFoundError as error:
        if error.name != target:   # a module it needs is missing: that is a fault to show
            raise
        print(f"catalog.py ci: {stage} is not available yet in this Lab.", file=err)
        return 2
    if not hasattr(module, function):
        print(f"catalog.py ci: {stage} is not available yet in this Lab.", file=err)
        return 2
    return getattr(module, function)(rest, out, err, **seams)


def usage() -> str:
    width = max(len(stage) for stage in STAGES)
    return "usage: catalog.py ci <stage> [PATH] [options]\n\n" + "\n".join(
        f"  {stage.ljust(width)}  {SUMMARY[stage]}" for stage in STAGES) + "\n"


# ---------------------------------------------------------------------------------------------------------------------
# What a step writes for the workflow
# ---------------------------------------------------------------------------------------------------------------------

_KEY = re.compile(r"[a-z][a-z0-9_]*")


def json_line(value) -> str:
    """A value for a line of `$GITHUB_OUTPUT`: JSON, ASCII only, so one line whatever the value holds."""
    return json.dumps(value, ensure_ascii=True)


def write_outputs(items: Dict[str, str], target: Optional[str] = None) -> None:
    """The outputs of a step, appended to `$GITHUB_OUTPUT` (or `target`): a line `key=value` for each, and nothing else. A key
    is lowercase letters, digits and `_`; a value is one line (a flag, or what `json_line` made). Anything else is a mistake of
    the stage, refused before a byte is written, so that no name of the tree can add a key or start a workflow command."""
    where = target or os.environ.get("GITHUB_OUTPUT")
    if not where:
        raise Unavailable("--github-output needs $GITHUB_OUTPUT, which only a workflow step has")
    lines = []
    for key, value in items.items():
        if not _KEY.fullmatch(key) or not isinstance(value, str) or any(char in value for char in "\r\n\x00") \
                or value.startswith("::"):
            raise ValueError(f"not a line of an output: {key!r}")
        lines.append(f"{key}={value}\n")
    with open(where, "a", encoding="utf-8") as handle:
        handle.write("".join(lines))


def write_summary(text: str) -> None:
    """Markdown appended to `$GITHUB_STEP_SUMMARY` (nothing outside a workflow step). The stages call it with `--ci` only. A text
    over SUMMARY_LIMIT bytes is cut at a line and says so: GitHub drops the whole summary of a step that is over its limit, and
    with it the table of images."""
    where = os.environ.get("GITHUB_STEP_SUMMARY")
    if where and text:
        text = text if text.endswith("\n") else text + "\n"
        data = text.encode("utf-8")
        if len(data) > SUMMARY_LIMIT:
            text = data[:SUMMARY_LIMIT].decode("utf-8", "ignore")
            text = text[:text.rfind("\n") + 1] + "\nThe rest of this summary is left out: it is longer than GitHub shows.\n"
        with open(where, "a", encoding="utf-8") as handle:
            handle.write(text)


def fence(lines: Iterable[str]) -> str:
    """Lines of a stranger's program as a fenced block of a summary, with a fence longer than any run of backticks in them."""
    kept = [report.printable(line.rstrip("\r\n")) for line in lines]
    longest = max((len(run) for line in kept for run in re.findall(r"`+", line)), default=0)
    mark = "`" * max(3, longest + 1)
    return mark + "\n" + "\n".join(kept) + "\n" + mark + "\n"


def cell(text) -> str:
    """A name from a tree as a cell of a Markdown table: letters, digits and `. _ -` as they are, anything else a `?`, so that no
    name can add a column, a link or a line to the summary."""
    return re.sub(r"[^A-Za-z0-9._-]", "?", str(text))[:80]


def tail(text: str, count: int = 15, width: int = LINE_WIDTH) -> List[str]:
    """The last `count` lines of a log that have anything in them, each cut to `width` characters (a minified bundle or a program
    that prints without end has lines of megabytes: 15 of them are no summary, and no message)."""
    kept = [line for line in text.splitlines() if line.strip()][-count:]
    return [line if len(line) <= width else line[:width] + " [...]" for line in kept]


def conclude(stage: str, problems: List[Problem], args, out, command: str, skipped: Sequence[str] = (), extra: str = "",
             title: str = "") -> int:
    """The end of a stage: the findings in the format asked for, the summary with `--ci`, and the exit code (1 for an error).
    `extra` is Markdown that goes before the findings in the summary (a table of images)."""
    ordered = report.ordered(problems)
    rendered = report.render(ordered, args.format, skipped=list(skipped))
    out.write(rendered)
    if getattr(args, "ci", False):
        write_summary((extra + "\n" if extra else "") + report.summary(ordered, command, list(skipped), title or f"ci {stage}"))
    return 1 if any(each.severity == ERROR for each in ordered) else 0


# ---------------------------------------------------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------------------------------------------------

OUTPUT_LIMIT = 16 * 1024 * 1024   # what a command of a stage may say of itself: the first and the last half, whatever it prints


def run_limited(cmd: Sequence[str], timeout: float = 600, cwd=None, environ: Optional[dict] = None,
                limit: int = OUTPUT_LIMIT) -> Tuple[int, str, str]:
    """(exit code, output, errors) of a command, with no input and a process group of its own, ended with everything it started
    at `timeout` seconds (124) and kept to `limit` bytes of each output (the first half and the last half, and a line that says
    the middle is gone): a program that prints without end, which a build or a test of a stranger may, fills no memory of this
    run. 127 when it cannot be started."""
    try:
        process = subprocess.Popen(list(cmd), cwd=cwd, env=environ, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
    except (OSError, ValueError) as error:
        return 127, "", str(error)
    kept: Dict[str, bytes] = {}

    def drain(name: str, stream) -> None:
        half = max(limit // 2, 1)
        head, tail_parts, tail_size, clipped = bytearray(), collections.deque(), 0, False
        descriptor = stream.fileno()
        while True:
            try:
                chunk = os.read(descriptor, 65536)
            except OSError:
                break
            if not chunk:
                break
            if len(head) < half:
                take = chunk[:half - len(head)]
                head += take
                chunk = chunk[len(take):]
            if chunk:
                tail_parts.append(chunk)
                tail_size += len(chunk)
                while tail_size - len(tail_parts[0]) >= half:
                    tail_size -= len(tail_parts.popleft())
                    clipped = True
        text = b"".join(tail_parts)
        if len(text) > half:
            text, clipped = text[-half:], True
        kept[name] = bytes(head) + (b"\n[... the middle of the output is gone ...]\n" if clipped else b"") + text

    readers = [threading.Thread(target=drain, args=(name, stream), daemon=True)
               for name, stream in (("out", process.stdout), ("err", process.stderr))]
    for reader in readers:
        reader.start()
    code = None
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop(process)
        code = 124
    except BaseException:   # an interrupt too: nothing is left running
        _stop(process)
        raise
    finally:
        for reader in readers:
            reader.join(10)
        for stream in (process.stdout, process.stderr):
            stream.close()
    out, err = (kept.get(name, b"").decode("utf-8", "replace") for name in ("out", "err"))
    if code == 124 and not (err or "").strip():
        err = f"{cmd[0]} did not answer within {int(timeout)} s"
    return code, out, err


def _stop(process) -> None:
    """Ends a command and everything it started (it leads a process group of its own)."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        process.kill()
    try:
        process.wait(5)
    except subprocess.TimeoutExpired:
        pass


def run_docker(argv: Sequence[str], timeout: float = 600, cwd=None, runner: Optional[Callable] = None) -> Tuple[int, str, str]:
    """(exit code, output, errors) of a command that builds or runs what someone else wrote: `run_limited` with the build
    environment, so it ends with its process group at the time limit, says no more than OUTPUT_LIMIT, and gets no variable
    of the shell that is not on the list. 127 when `docker` cannot be run, 124 at the time limit."""
    if runner is not None:
        return runner(list(argv), timeout, cwd)
    return run_limited(argv, timeout=timeout, cwd=str(cwd) if cwd else None, environ=compose.build_environment())


def docker_ready(runner: Optional[Callable] = None) -> None:
    """Raises Unavailable, with what to do, when `docker` cannot be run here or the daemon does not answer."""
    code, _, said = run_docker(["docker", "version", "--format", "{{.Server.Version}}"], timeout=60, runner=runner)
    if code == 127:
        raise Unavailable(f"{NO_DOCKER}: this stage runs containers")
    if code != 0:
        if compose.unreachable(said):
            raise Unavailable(f"{DOCKER_DOWN}: start Docker Desktop (or the docker service), then run it again")
        raise Unavailable("Docker does not answer: " + (report.printable(tail(said, 1)[0]) if tail(said, 1) else f"exit {code}"))


def host_platform() -> str:
    """linux/amd64 or linux/arm64: the architecture of the containers this machine runs (a Mac with Apple silicon runs arm64)."""
    machine = hostplatform.machine().lower()
    return "linux/arm64" if machine in ("arm64", "aarch64") else "linux/amd64"


def platform_of(text: str) -> str:
    """`--platform` checked: one of PLATFORMS. A Usage otherwise."""
    if text not in PLATFORMS:
        raise Usage(f"--platform must be one of {', '.join(PLATFORMS)}")
    return text


# ---------------------------------------------------------------------------------------------------------------------
# The units a stage works on
# ---------------------------------------------------------------------------------------------------------------------

class Loaded(NamedTuple):
    """A unit the trust policy judged OK, as the stages that run Docker read it.

    kind, name   `playground`, `bank`
    uid          the Lab's id of it (`bank` in the default catalog, `@acme/bank` in another)
    file         its compose file, relative to the catalog: `playgrounds/bank/compose.yml`
    folder       the real path of its folder ("" for a setup, which has none)
    config       `docker compose config` of the Lab's core and the unit, read with the scrubbed environment: the JSON that
                 is judged and built
    own          the services the unit defines itself (neither the core nor an included unit defines them)
    tested       `x-gravitee.tested` of the unit: the sets of versions it was tried with
    variables    the variables the images of the project are written with (the only ones a set of versions may set), or None
    resolve      `f(variables) -> config`: the same JSON with these variables set for Compose (a set of `x-gravitee.tested`),
                 read the same way; None when the loader cannot
    """
    kind: str
    name: str
    uid: str
    file: str
    folder: str
    config: dict
    own: Tuple[str, ...]
    tested: Tuple[dict, ...]
    variables: Optional[frozenset]
    resolve: Optional[Callable] = None

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}"


class Refused(NamedTuple):
    """A unit asked for that was left out, and why (in the words of the trust policy for a Blocked one). `fix` is what to do when
    `make check` does not say it (a unit the Lab's project judged OK and this stage refuses all the same)."""
    kind: str
    name: str
    file: str
    reasons: Tuple[str, ...]
    code: str = "T27"
    fix: str = ""


class Loading(NamedTuple):
    units: List[Loaded]
    refused: List[Refused]
    notes: List[str]


def unit_path(kind: str, name: str) -> str:
    """The file of a unit in a catalog, relative to its top."""
    return f"setups/{name}.yml" if kind == "setup" else f"{kind}s/{name}/compose.yml"


# A callable (folder, names) -> Loading that takes the place of lab_loader: the tests, and a caller that loads a folder in its own way
LOADER: Optional[Callable] = None


# What makes a unit's files unreadable as a catalog: a link, a submodule, a name the Lab cannot install, a path too deep or too long.
# The structure rules (ST) say so for the whole catalog; here they decide whether a unit is given to Compose and to Docker
STRUCTURE = {"ST01": "is a symbolic link", "ST02": "is a submodule", "ST03": "has a name the Lab cannot install",
             "ST04": "is too deep or too long"}


def structure(root: str, names: Optional[Sequence[Tuple[str, str]]] = None) -> Dict[Tuple[str, str], Tuple[str, str]]:
    """{(kind, name): (code, path)} for the units among `names` (every unit of the catalog when None) that hold a file the
    structure rules refuse as unreadable (ST01 to ST04): the first such file of each. Read from the names of the files alone
    (git's listing, or a walk that never follows a link), nothing is opened."""
    from .plan import unit_of
    wanted = None if names is None else set(names)
    found: Dict[Tuple[str, str], Tuple[str, str]] = {}
    try:
        entries = trees.FolderTree(root).entries()
    except trees.TreeError as error:
        raise Unavailable(str(error)) from None
    for each in entries:
        code = store.refusal(each)
        unit = unit_of(each.path) if code in STRUCTURE else None
        if unit is not None and (wanted is None or (unit.kind, unit.name) in wanted):
            found.setdefault((unit.kind, unit.name), (code, each.path))
    return found


def load(folder, names: Sequence[Tuple[str, str]]) -> Loading:
    """The units of the catalog at `folder` that `names` asks for ([(kind, name)]), judged by the trust policy: the OK ones
    with their resolved JSON, the others as refused with their reasons.

    A unit that holds a file the structure rules call unreadable (ST01 to ST04) is never read: the rules are applied to the names
    of the files first, the unit is refused, and the Lab's project is read without it (`hidden`: the units of the catalog that
    hold such a file, asked for or not, are not in the temporary project the others are read in). With no unit left to read,
    nothing is read at all."""
    asked = list(names)
    unreadable = structure(str(folder)) if asked else {}
    refused: List[Refused] = []
    for kind, name in asked:
        if (kind, name) in unreadable:
            code, where = unreadable[(kind, name)]
            refused.append(Refused(kind, name, unit_path(kind, name), (f"{where} {STRUCTURE[code]}",), code))
    survivors = [each for each in asked if each not in unreadable]
    if not survivors:
        return Loading([], refused, [])
    loader = LOADER
    found = loader(str(folder), survivors) if loader is not None else lab_loader(str(folder), survivors, hidden=sorted(unreadable))
    refused = list(found.refused) + refused
    kept = [unit for unit in found.units if (unit.kind, unit.name) not in unreadable]
    return Loading(kept, refused, list(found.notes))


def blocked_reasons(unit: dict) -> Tuple[str, ...]:
    blocked = unit.get("blocked")
    reasons = (blocked or {}).get("reasons") if isinstance(blocked, dict) else None
    return tuple(str(reason) for reason in reasons) if reasons else ("the trust policy blocks it",)


def _realized(config: dict, linked) -> dict:
    """The JSON of a unit with its build contexts as the real places they lead to: Compose read the unit through the links of a
    temporary project, which is gone when the stage builds. A context below one of the folders the Lab's own project is made
    of (the seeder) is the Lab's, where it really is."""
    anchors = {os.path.realpath(str(linked.root / name)): os.path.realpath(str(linked.lab / name)) for name in check.ANCHORS}
    for service in (config.get("services") or {}).values():
        build = service.get("build") if isinstance(service, dict) else None
        context = build.get("context") if isinstance(build, dict) else None
        if isinstance(context, str) and os.path.isabs(context) and "\x00" not in context:
            real = os.path.realpath(context)
            for temporary, labs in anchors.items():
                if real == temporary or real.startswith(temporary + os.sep):
                    real = labs + real[len(temporary):]
                    break
            build["context"] = real
    return config


# Compose prints a service that has `profiles` only when one of them is on. `*` turns them all on, so that what is judged and built
# is every service the files define
ALL_PROFILES = {"COMPOSE_PROFILES": "*"}


def gated(config: dict) -> List[str]:
    """The services of a resolved configuration that have `profiles`, sorted: Compose reads them only when a profile is on (a
    line of a guide, a `COMPOSE_PROFILES` of an `.env`), which makes them a part of the unit that the policy did not judge."""
    services = config.get("services") if isinstance(config, dict) else None
    return sorted(str(name) for name, service in (services or {}).items() if isinstance(service, dict) and service.get("profiles"))


def gated_refusal(kind: str, name: str, file: str, services: Sequence[str]) -> Refused:
    """The refusal of a unit with a service that has `profiles`: the trust policy's own T01 words for it, with the services
    named. The policy reads the unit without a profile on, so it does not say this itself (the fix says what to do)."""
    named = ", ".join(report.printable(each) for each in services[:5]) + (", ..." if len(services) > 5 else "")
    reason = trust.RULES["T01"]["lines"][0].replace("{key}", "profiles") + f" ({named})"
    fix = (getattr(trust, "FIXES", None) or explain.T_FIXES).get("T01", "").replace("{key}", "profiles")
    return Refused(kind, name, file, (reason,), "T01", fix)


@contextlib.contextmanager
def hidden_units(ns: str, units: Iterable[Tuple[str, str]]):
    """For the length of the block the Lab's project does not list these units of catalog `ns` ([(kind, name)]): its one listing of a
    catalog's units (`paths.names`) leaves them out, so nothing that lists the units (the files it reads, the ones it asks Compose
    about) reaches them, and a unit that includes one is held by its closure as it is for any file that is not a clean unit. Put
    back at the end, as `check.scrubbed` does for the environment: a command line runs one command."""
    gone = {(kind, name) for kind, name in units}
    if not gone:
        yield
        return
    before = paths.names

    def names(where, kind):
        return [found for found in before(where, kind) if where != ns or (kind, found) not in gone]
    paths.names = names
    try:
        yield
    finally:
        paths.names = before


def _resolver(folder: str, ns: str, needs: Sequence[str], source: str, scrubbed: dict) -> Callable:
    """`resolve` of a unit: its JSON again, with the variables of a set of versions on top of the scrubbed environment, read in a
    temporary project of its own (the one the unit was read in is gone), every profile on."""
    def resolve(variables: dict) -> dict:
        with check.temporary_link(folder, ns, needs) as linked:
            return _realized(compose.config_json(compose.all_files([source]), timeout=90,
                                                 environ={**scrubbed, **variables, **ALL_PROFILES}), linked)
    return resolve


def lab_loader(folder: str, names: List[Tuple[str, str]], hidden: Iterable[Tuple[str, str]] = ()) -> Loading:
    """The default loader. The folder is read as the check reads it: in a temporary project of this process in which the catalog is
    linked (`check.temporary_link`), so that nothing is linked into the Lab and nothing of it is written, and with Compose in the
    scrubbed environment (`check.scrubbed`). The project is read as the Lab reads it (the text screen, the closure taint, the
    judgement of the trust policy: nothing of a unit reaches Compose that the screen refused); each unit that is OK is then
    resolved again with `docker compose config` and the scrubbed environment of 5.5, so that no value of the runner is in what is
    built. The catalogs it includes from have to be the Lab's own (installed or linked). `hidden`: units of the catalog that the
    project is not to read at all (`hidden_units`)."""
    from ... import e2e
    from .. import defaults, registry
    if not os.path.isdir(folder):
        raise Usage(f"{report.printable(folder)} is not a folder")
    summary = check.folder_summary(folder)
    ns = summary.get("id")
    if not isinstance(ns, str) or not re.fullmatch(paths.NS, ns):
        raise Unavailable("catalog.yml gives no id this machine can use, so its units were not read: make check says why")
    try:
        registry.check_namespace(ns, listed=[one["id"] for one in defaults.read()])
        needs = sorted(summary.get("requires_catalogs") or {})
        check.needs_here(ns, needs, registry.inputs())
    except (registry.CatalogError, check.Unavailable) as error:
        raise Unavailable(str(getattr(error, "message", None) or error)) from None
    scrubbed = compose.scrubbed_environment()
    found: List[Loaded] = []
    refused: List[Refused] = []
    real = os.path.realpath(folder)
    try:
        with check.temporary_link(folder, ns, needs) as linked, check.scrubbed(), hidden_units(ns, hidden):
            built, _ = check.loaded()
            for kind, name in names:
                file = unit_path(kind, name)
                item = paths.unit_of(paths.CATALOG_FILES[kind].format(ns=ns, name=name))
                unit = built[f"{kind}s"].get(item[1]) if item else None
                if unit is None or unit.get("catalog") != ns:
                    refused.append(Refused(kind, name, file, ("the Lab did not read it as a unit of this catalog",)))
                    continue
                if unit.get("blocked"):
                    refused.append(Refused(kind, name, file, blocked_reasons(unit), _code_of(unit)))
                    continue
                try:
                    config = compose.config_json(compose.all_files([unit["file"]]), timeout=90, environ={**scrubbed, **ALL_PROFILES})
                except (compose.ComposeError, ValueError) as error:
                    if compose.missing(str(error)):
                        raise Unavailable("Docker Compose 5 was not found: " + compose.NO_COMPOSE) from None
                    refused.append(Refused(kind, name, file, (f"Cannot be read: {report.printable(str(error))[:200]}",)))
                    continue
                profiled = gated(config)
                if profiled:   # (read with every profile on, so that nothing of the unit is left unjudged)
                    refused.append(gated_refusal(kind, name, file, profiled))
                    continue
                where = os.path.realpath(os.path.join(real, *file.split("/")))
                folder_here = "" if kind == "setup" else os.path.dirname(where)
                found.append(Loaded(kind, name, item[1], file, folder_here, _realized(config, linked),
                                    tuple(sorted(unit.get("services") or ())), tuple(unit.get("tested") or ()),
                                    _variables(e2e, built), _resolver(folder, ns, needs, unit["file"], scrubbed)))
    except check.ComposeGone:
        raise Unavailable("Docker Compose 5 was not found: " + compose.NO_COMPOSE) from None
    return Loading(found, refused, [])


def _variables(e2e, built) -> Optional[frozenset]:
    known = e2e.image_variables(built)
    return None if known is None else frozenset(known)


def _code_of(unit: dict) -> str:
    codes = ((unit.get("verdict") or {}).get("codes") or ())
    return str(codes[0]) if codes else "T27"


def refusals(refused: Iterable[Refused], what: str) -> List[Problem]:
    """A unit that was asked for and not read is a finding, never silence: its first reason, in the Lab's words, and what to
    do. It is the same unit that `check` fails, so the finding is the same code."""
    found = []
    for each in refused:
        reason = report.printable(each.reasons[0] if each.reasons else "the trust policy blocks it")
        found.append(Problem(each.code, ERROR, each.file, 0,
                             f"{each.kind}:{each.name} was not {what}: {reason}. Fix: {each.fix or 'make check says how.'}"))
    return found


def seeder_anchor(lab=None) -> Tuple[Tuple[str, ...], str]:
    """(the repositories of the Lab's seeder image, the real path of the Lab's seeder folder): what tells a service built from
    the Lab's own code, which the stages leave to the Lab's CI."""
    from .. import audit
    root = Path(lab) if lab else Path(paths.ROOT)
    return audit.lab_seeder_images(root), os.path.realpath(str(root / SEEDER_DIR))


def seconds(value: float) -> str:
    """A duration the way a person reads it in a table: `18 s`, `3 min 5 s`."""
    whole = int(round(value))
    return f"{whole} s" if whole < 60 else f"{whole // 60} min {whole % 60} s"


def megabytes(size: int) -> str:
    """A size in bytes as a person reads it: `41.2 MB`."""
    return f"{size / 1_000_000:.1f} MB" if size >= 100_000 else f"{size / 1000:.1f} kB"


__all__ = ["ALL_PROFILES", "BD01_PLAIN", "DOCKER_DOWN", "FORMATS", "Help", "IMAGE_NAME", "LINE_WIDTH", "LOADER", "Loaded", "Loading",
           "MAX_UNITS", "MESSAGES", "NOT_PINNED_YET", "NO_DOCKER", "PLATFORMS", "Parser", "Refused", "SKIPPABLE", "STAGES",
           "SUMMARY_LIMIT", "Unavailable", "Usage", "WARNING", "blocked_reasons", "cell", "conclude", "docker_ready", "entry", "fence",
           "gated", "gated_refusal", "hidden_units", "host_platform", "json_line", "lab_loader", "load", "main", "megabytes",
           "parse_units", "parsed", "parser", "platform_of", "problem", "refusals", "run_docker", "run_limited", "seconds",
           "seeder_anchor", "structure", "tail", "unit_path", "usage", "write_outputs", "write_summary"]
