"""`catalog.py ci smoke PATH --units JSON [--ci]` (SM01 to SM03): the cheapest check that means something. The services of a
touched add-on or playground that do not depend on the platform start, stay up and answer their health check. Level 2 of the
standard: `compile` of `ci.yml` calls it after `ci build`, and `make ci` calls it from `ci run` (`run.py` finds this module
by `STAGES["smoke"]` and gives it `[PATH, "--units", JSON]`).

For each unit the trust policy judged OK (`ci.load`, the same reading the build has):

1. Its own services are the services it defines that neither the core nor an included unit defines (`Loaded.own`, which is
   `project._own`). Left out: the seed service (`io.gravitee.role: seed`), a service that exits without a license
   (`io.gravitee.license: required`) and every service whose `depends_on` reaches, through own services, a service of the core or
   of another unit (also `links`, `volumes_from` and `service:` modes). That is standard Compose and no new label: in a bank, the MCP
   server with no dependency is started and the agent that needs both gateways is left to the end-to-end run.
2. A unit that needs settings (Compose cannot read it without a setting that has no default) or a license
   (`x-gravitee.requires.license`) is **not tried**: said (SM03, a warning) and never taken for a pass, the convention of the
   engine's `e2e.py`, whose exit code 2 is "not tried".
3. The bind directories the unit keeps its state in (`.state/` inside its own folder) are made, mode 777, as the Lab's `make init`
   did, then `docker compose -p catalog-smoke-<unit> -f docker-compose.yml -f on.yml up -d --wait --wait-timeout 240 --no-build
   --pull missing --no-deps <services>` runs in a temporary project of this process in which the catalog is linked (the way `check`
   reads a folder: nothing is linked into the Lab and nothing of it is written), with the build environment of 5.5. The services
   start in waves, one for each level of `depends_on` among them, so that a service never starts before what it waits for
   (rehearsal item V13 asks whether one call for all of them would do; the waves are right whatever the answer). A unit with no
   dependency among its services is one wave, and one command.
4. `--wait` ends when the services are healthy (or running, when they have none); any exit is a failure. They are looked at again
   after a few seconds, so a service that dies right after it started is found too. On failure the last 60 lines of the log of each
   container that is not up are printed (between `::stop-commands::` and its token: they are a stranger's program's words).
5. Always `down -v --remove-orphans`, whatever happened.

    SM01  {service} was not healthy after 240 s        an error
    SM02  {service} exited with {code}                 an error
    SM03  {unit} was not tried: it needs settings or a license.      a warning

An image that is not here and cannot be pulled is no verdict on the unit: the stage exits 2 and says to build it first. Exit 0 every
unit that was tried worked (or none could be tried), 1 a service did not, 2 this machine cannot do it.

Python 3.9 and later, standard library only."""

import contextlib
import hashlib
import json
import os
import re
import time
from typing import Callable, Dict, FrozenSet, List, NamedTuple, Optional, Sequence, Set, Tuple

from ...library import check
from ...docker import compose
from ... import paths
from .. import report, tree as trees
from ..report import ERROR, WARNING, Problem
from ..rules import outline, say
from . import (Loaded, Refused, Unavailable, conclude, docker_ready, entry, fence, load, parse_units, parsed, parser,
               refusals, run_docker, seconds, tail)

MESSAGES = {
    "SM01": (ERROR, "{service} was not healthy after 240 s"),
    "SM02": (ERROR, "{service} exited with {code}"),
    "SM03": (WARNING, "{unit} was not tried: it needs settings or a license."),
}
WAIT = 240          # seconds `up --wait` gives the services of a wave to become healthy
SETTLE = 5          # seconds a started wave is given before it is looked at again
UP_TIMEOUT = 900    # seconds for one `up`: the wait, and what is pulled before it
LOG_LINES = 60      # lines of the log of a container that is not up
LOG_WIDTH = 2000    # characters of such a line that are kept
SEED = "seed"
ROLE = "io.gravitee.role"
LICENSE_LABEL = "io.gravitee.license"
STATE = ".state"
STATE_FOLDERS = 100   # the most state folders a unit has made for it: a handful is what a unit names
PROJECT = "catalog-smoke"
NAME_LIMIT = 63
SERVICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
# What `ci.load` says of a unit whose Compose file needs a setting that has no default: the words of Compose (measured with 5.6.0:
# `error while interpolating services.web.environment.TOKEN: required variable TOKEN is missing a value: set TOKEN`) after its own
# `Cannot be read: `. Anchored at the start and with the variable a plain name, so that a name in a stranger's file that happens to
# spell the words inside another error cannot make a unit that does not load look like one that only lacks a setting
NEEDS_SETTING = re.compile(r"Cannot be read: error while interpolating \S{1,300}: required variable [A-Za-z_][A-Za-z0-9_]* is missing a value")
# What `up` says when an image is neither here nor to be had: the machine's registry access, not the unit's health
NO_IMAGE = re.compile(r"pull access denied|manifest unknown|no matching manifest|toomanyrequests|failed to resolve reference|"
                      r"repository does not exist|image .{1,200} not found|No such image|failed to pull|error pulling image", re.I)


def finding(which: str, path: str = "", **fields) -> Problem:
    """The Problem of a code of this stage (`which`: SM01...), its message the words of 6.11 with the fields put in."""
    severity, message = MESSAGES[which]
    return Problem(which, severity, path, 0, say(message, **fields))


# ---------------------------------------------------------------------------------------------------------------------
# What to start
# ---------------------------------------------------------------------------------------------------------------------

def needs_settings(refused: Refused) -> bool:
    """Whether a unit that `ci.load` left out was left out only for want of a setting that has no default: Compose cannot resolve
    it without one, which the Lab shows as "Needs settings". That is not a fault of the unit (`check` does not fail it), so a stage
    that starts it says it was not tried; the others (a Blocked unit, a file that does not load) stay errors."""
    return bool(refused.reasons) and bool(NEEDS_SETTING.match(refused.reasons[0] if isinstance(refused.reasons[0], str) else ""))


def requires_license(text: Optional[str]) -> bool:
    """Whether the `x-gravitee` block of a unit's file says `requires: license:` with something in it. Read as the text it is
    (the merged JSON of Compose has the core's `x-gravitee`, not the unit's)."""
    if not text:
        return False
    found = next((node for node in outline(text).root.children if node.key == "x-gravitee"), None)
    if found is None:
        return False
    if found.value.startswith("{"):   # the block in flow style, on one line
        return bool(re.search(r"\blicense\s*:\s*(?:\[\s*[^\]\s]|\{\s*[^}\s]|[^\s,}\]\[{])", found.value))
    requires = found.get("requires")
    license_ = requires.get("license") if requires is not None else None
    if license_ is None:
        return False
    if license_.children:
        return True
    return license_.value.strip() not in ("", "[]", "{}", "null", "~", '""', "''")


def labels_of(service: dict) -> Dict[str, str]:
    """The labels of a service in the shape of the resolved JSON (a mapping), or of a list of `key=value`."""
    labels = service.get("labels")
    if isinstance(labels, dict):
        return {str(key): "" if value is None else str(value) for key, value in labels.items()}
    if isinstance(labels, list):
        return {str(each).partition("=")[0]: str(each).partition("=")[2] for each in labels}
    return {}


def dependencies(service: dict) -> Set[str]:
    """The names a service waits for or shares with: `depends_on`, and the other ways Compose ties one service to another (`links`,
    `volumes_from`, and the `service:` form of `network_mode`, `pid` and `ipc`). A `container:` reference is not a service of the
    unit: it is named as it is, which no own service is."""
    found: Set[str] = set()
    needs = service.get("depends_on")
    if isinstance(needs, dict):
        found |= {str(name) for name in needs}
    elif isinstance(needs, list):
        found |= {str(name) for name in needs}
    for key in ("links", "volumes_from"):
        for each in service.get(key) or ():
            found.add(str(each).split(":", 1)[0] if not str(each).startswith("container:") else str(each))
    for key in ("network_mode", "pid", "ipc"):
        value = service.get(key)
        if isinstance(value, str) and value.startswith(("service:", "container:")):
            found.add(value.split(":", 1)[1] if value.startswith("service:") else value)
    return found


def completed(service: dict) -> Set[str]:
    """The services this one waits for to finish well: `depends_on` with the condition `service_completed_successfully`. A service
    that is waited for so is made to run once and exit, and exiting with 0 is how it comes up well."""
    needs = service.get("depends_on")
    if not isinstance(needs, dict):
        return set()
    return {str(name) for name, how in needs.items() if isinstance(how, dict) and how.get("condition") == "service_completed_successfully"}


class Selection(NamedTuple):
    """What of a unit starts: the services in waves, the ones left out with the reason, and the ones that run once (`done`: another
    service waits for them to exit with 0)."""
    waves: List[List[str]]
    left: List[Tuple[str, str]]
    done: FrozenSet[str] = frozenset()


def select(unit: Loaded) -> Selection:
    """The own services that need no platform, in the order they can start. A service is left out when it is the seed, when it exits
    without a license, or when what it depends on is not a service that is started here: the core, another unit, or an own service
    that is itself left out. What is left out because of another is left out in turn, so that `--no-deps` never starts a service
    whose dependency is missing. One pass over the services and their ties, whatever their number: a unit of ten thousand services
    in a chain costs what ten thousand cost."""
    services = unit.config.get("services") if isinstance(unit.config, dict) else None
    services = services if isinstance(services, dict) else {}
    left: Dict[str, str] = {}
    kept: Dict[str, Set[str]] = {}
    for name in unit.own:
        service = services.get(name)
        if not isinstance(service, dict) or not SERVICE.fullmatch(str(name)):
            left[name] = "it cannot be read"
            continue
        labels = labels_of(service)
        if labels.get(ROLE) == SEED:
            left[name] = "it is the seed, which needs the platform"
        elif labels.get(LICENSE_LABEL) == "required":
            left[name] = "it exits without a license"
        else:
            kept[name] = dependencies(service)
    own = set(unit.own)
    needed_by: Dict[str, List[str]] = {}
    for name, ties in kept.items():
        for tie in ties:
            needed_by.setdefault(tie, []).append(name)
    every = set(kept)
    pending = [name for name, ties in kept.items() if not ties <= every]
    while pending:
        name = pending.pop()
        if name not in kept:
            continue
        ties = kept.pop(name)
        outside = sorted(tie for tie in ties if tie not in own)
        missing = outside or sorted(tie for tie in ties if tie not in kept)
        left[name] = ("it depends on " + ", ".join(report.printable(each) for each in missing[:5]) +
                      (", which this unit does not run" if outside else ", which is left out"))
        pending.extend(needed_by.get(name, ()))
    waves: List[List[str]] = []
    degree = {name: len(ties) for name, ties in kept.items()}
    ready = [name for name in kept if degree[name] == 0]
    while ready:
        waves.append(ready)
        following: List[str] = []
        for name in ready:
            for other in needed_by.get(name, ()):
                if other in degree:
                    degree[other] -= 1
                    if degree[other] == 0:
                        following.append(other)
        ready = following
    placed = {name for wave in waves for name in wave}
    for name in kept:
        if name not in placed:   # a cycle: Compose would refuse it, and so is it not started
            left[name] = "its dependencies form a cycle"
    once = {tie for name in kept for tie in completed(services[name]) if tie in kept}
    # `up --wait` of a service that runs once and exits answers no ("exited (0)") when it is alone in its call, and well when
    # the service that waits for it is in the same call: it goes to the wave of the first one that does
    wave_of = {name: at for at, wave in enumerate(waves) for name in wave}
    for name in sorted(once & set(wave_of)):
        first = min((wave_of[each] for each in kept if each in wave_of and name in completed(services[each])), default=None)
        if first is not None and wave_of[name] < first:
            waves[wave_of[name]].remove(name)
            waves[first].append(name)
            wave_of[name] = first
    waves = [wave for wave in waves if wave]
    return Selection(waves, [(name, left[name]) for name in unit.own if name in left], frozenset(once))


def project_name(unit: Loaded) -> str:
    """The Compose project of the smoke of a unit: `catalog-smoke-<kind>-<name>`, or, for a name too long for a container name,
    the first twelve characters of its hash."""
    name = f"{PROJECT}-{unit.kind}-{unit.name}"
    return name if len(name) <= NAME_LIMIT else f"{PROJECT}-{hashlib.sha256(unit.key.encode()).hexdigest()[:12]}"


def state_folders(unit: Loaded, services: Sequence[str]) -> List[str]:
    """The folders below the `.state` of the unit that its started services bind, relative to the unit's folder (`.state`,
    `.state/data`): the only place a unit may write in its own folder (T12). The resolved JSON names them as they were when it was
    read, in a project of that moment, so what is taken is the part after the unit's own folder. At most STATE_FOLDERS, each once."""
    marker = "/" + os.path.dirname(unit.file) + "/"
    found: Dict[str, None] = {}
    config = unit.config.get("services") if isinstance(unit.config, dict) else None
    for name in services:
        service = (config or {}).get(name)
        for volume in (service.get("volumes") if isinstance(service, dict) else None) or ():
            source = volume.get("source") if isinstance(volume, dict) and volume.get("type") == "bind" else None
            if not isinstance(source, str) or marker not in source:
                continue
            rest = source.split(marker, 1)[1].rstrip("/")
            parts = rest.split("/")
            if parts[0] == STATE and all(re.fullmatch(r"[A-Za-z0-9._+-]+", part) and part not in (".", "..") for part in parts):
                found[rest] = None
                if len(found) >= STATE_FOLDERS:
                    return sorted(found)
    return sorted(found)


def make_state(unit: Loaded, wanted: Sequence[str]) -> List[str]:
    """Makes (mode 777, as `make init` did, so that a container that does not run as root can write) the folders of `wanted` below
    the unit's real folder, and answers the ones it made. It never follows a link and never leaves the folder: a name that is a
    link, or a file, is left as it is (Compose will say what it makes of it)."""
    made: List[str] = []
    base = unit.folder
    if not base or not os.path.isdir(base):
        return made
    for rest in wanted:
        here = base
        for part in rest.split("/"):
            here = os.path.join(here, part)
            try:
                info = os.lstat(here)
            except FileNotFoundError:
                try:
                    os.mkdir(here, 0o777)
                    os.chmod(here, 0o777)
                    made.append(os.path.relpath(here, base))
                except OSError:
                    break
                continue
            except OSError:
                break
            if not os.path.isdir(here) or os.path.islink(here) or info.st_mode & 0o170000 == 0o120000:
                break
    return made


# ---------------------------------------------------------------------------------------------------------------------
# The project the services start in
# ---------------------------------------------------------------------------------------------------------------------

class Session(NamedTuple):
    """The place Compose runs in: a project folder of this process, and `files(unit)`, the `-f` arguments that read the core and the
    unit as the Lab does."""
    root: str
    files: Callable


@contextlib.contextmanager
def project_session(folder: str):
    """The default session: the folder is linked into a temporary copy of the Lab's project (`check.temporary_link`) for the length
    of the block, as `check` reads it, so the units' relative paths (the seeder, a sibling's folder) are what the Lab sees and
    nothing is linked into the Lab or written to it. The catalogs the unit includes from have to be the Lab's own."""
    from .. import defaults, registry
    summary = check.folder_summary(folder)
    ns = summary.get("id")
    if not isinstance(ns, str) or not re.fullmatch(paths.NS, ns):
        raise Unavailable("catalog.yml gives no id this machine can use, so its units were not started: make check says why")
    try:
        registry.check_namespace(ns, listed=[one["id"] for one in defaults.read()])
        needs = sorted(summary.get("requires_catalogs") or {})
        check.needs_here(ns, needs, registry.inputs())
    except (registry.CatalogError, check.Unavailable) as error:
        raise Unavailable(str(getattr(error, "message", None) or error)) from None
    with check.temporary_link(folder, ns, needs) as linked:
        def files(unit: Loaded) -> List[str]:
            return compose.all_files([paths.CATALOG_FILES[unit.kind].format(ns=ns, name=unit.name)])
        yield Session(str(linked.root), files)


# ---------------------------------------------------------------------------------------------------------------------
# Starting, looking, tearing down
# ---------------------------------------------------------------------------------------------------------------------

class Outcome(NamedTuple):
    """How a unit went: `state` is started, failed, not_tried or nothing (no service of it can start without the platform)."""
    unit: str
    path: str
    state: str
    started: Tuple[str, ...] = ()
    seconds: float = 0.0
    note: str = ""


def up_words(services: Sequence[str]) -> List[str]:
    """The words of the `docker compose up` of 5.6 for one wave, after the project and the files."""
    return ["up", "-d", "--wait", "--wait-timeout", str(WAIT), "--no-build", "--pull", "missing", "--no-deps", *services]


def up_command(project: str, files: Sequence[str], services: Sequence[str]) -> List[str]:
    """The whole `docker compose up` of 5.6 for one wave, as an argument list."""
    return [*compose.COMPOSE, "-p", project, *files, *up_words(services)]


def ps_rows(text: str) -> List[dict]:
    """The containers `docker compose ps --all --format json` lists: one JSON object a line, or one array (Compose has done both)."""
    text = (text or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        rows = data if isinstance(data, list) else [data]
    except (ValueError, RecursionError):
        rows = []
        for line in text.splitlines():
            try:
                rows.append(json.loads(line))
            except (ValueError, RecursionError):
                continue
    return [row for row in rows if isinstance(row, dict)]


def state_of(rows: Sequence[dict], services: Sequence[str]) -> Dict[str, Tuple[str, str, int]]:
    """{service: (state, health, exit code)} of the containers of these services; a service with no container is `missing`."""
    found: Dict[str, Tuple[str, str, int]] = {}
    for row in rows:
        name = row.get("Service")
        if isinstance(name, str) and name in services:
            code = row.get("ExitCode")
            found[name] = (str(row.get("State") or "").lower(), str(row.get("Health") or "").lower(),
                           code if isinstance(code, int) and not isinstance(code, bool) else 0)
    return {name: found.get(name, ("missing", "", 0)) for name in services}


def healthy(state: Tuple[str, str, int], once: bool = False) -> bool:
    """Up and, when it has a health check, healthy; a service that runs once is also well when it exited with 0."""
    return (state[0] == "running" and state[1] in ("", "healthy")) or (once and state[0] == "exited" and state[2] == 0)


class Docker:
    """The calls of one unit to Compose, in its project."""

    def __init__(self, project: str, session: Session, runner: Optional[Callable]):
        self.project, self.session, self.runner = project, session, runner

    def compose(self, unit: Loaded, *words: str, timeout: float = 120) -> Tuple[int, str, str]:
        return run_docker([*compose.COMPOSE, "-p", self.project, *self.session.files(unit), *words], timeout=timeout,
                          cwd=self.session.root, runner=self.runner)

    def ps(self, unit: Loaded) -> List[dict]:
        code, out, _ = self.compose(unit, "ps", "--all", "--format", "json", timeout=60)
        return ps_rows(out) if code == 0 else []

    def logs(self, unit: Loaded, service: str) -> List[str]:
        code, out, said = self.compose(unit, "logs", "--no-color", "--tail", str(LOG_LINES), service, timeout=60)
        return [line[:LOG_WIDTH] for line in tail(out + ("\n" + said if said and code != 0 else ""), LOG_LINES)]

    def down(self, unit: Loaded) -> str:
        """Removes the project with its volumes and orphans. Answers "" or why it could not."""
        code, out, said = self.compose(unit, "down", "-v", "--remove-orphans", timeout=300)
        return "" if code == 0 else " ".join(report.printable(line) for line in tail(said or out, 2))


class Found(NamedTuple):
    problems: List[Problem]
    logs: Dict[str, List[str]]
    started: List[str]


def start_unit(unit: Loaded, selection: Selection, docker: Docker, sleep: Callable) -> Found:
    """Starts the waves of a unit and looks at them: the first wave that does not come up well ends it (what follows would wait for
    it). Raises Unavailable for an image that is not here and cannot be had, and for a Docker that does not answer."""
    problems: List[Problem] = []
    logs: Dict[str, List[str]] = {}
    started: List[str] = []
    for wave in selection.waves:
        code, out, said = docker.compose(unit, *up_words(wave), timeout=UP_TIMEOUT)
        text = said + "\n" + out
        if code == 127 or compose.missing(text):
            raise Unavailable("Docker Compose 5 was not found: " + compose.NO_COMPOSE)
        if compose.unreachable(text):
            raise Unavailable("Docker is not running: start Docker Desktop (or the docker service), then run it again")
        if code == 0:
            sleep(SETTLE)   # (a service with no health check is "running" the moment it starts: it may still die)
        states = state_of(docker.ps(unit), wave)
        bad = [name for name in wave if not healthy(states[name], name in selection.done)]
        if code != 0:
            if NO_IMAGE.search(text) and all(states[name][0] == "missing" for name in bad):
                raise Unavailable("an image of " + report.printable(unit.key) + " is not here and cannot be pulled (" +
                                  " ".join(report.printable(line) for line in tail(text, 1)) + "): build it first "
                                  "(catalog.py ci build)")
            bad = bad or list(wave)   # (up said no and everything looks fine: the wave is not shown to have worked)
        if not bad:
            started += wave
            continue
        for name in bad:
            state, _, exit_code = states[name]
            if state in ("exited", "dead"):
                problems.append(finding("SM02", unit.file, service=report.printable(name), code=exit_code))
            else:
                problems.append(finding("SM01", unit.file, service=report.printable(name)))
            logs[name] = docker.logs(unit, name)
        return Found(problems, logs, started + [name for name in wave if name not in bad])
    return Found(problems, logs, started)


def lines_of(outcomes: Sequence[Outcome]) -> List[str]:
    """What a person reads: a count, then one line for each unit."""
    count = {state: sum(1 for each in outcomes if each.state == state) for state in ("started", "failed", "not_tried", "nothing")}
    out = [f"Smoke: {count['started']} started, {count['failed']} failed, {count['not_tried']} not tried, "
           f"{count['nothing']} with nothing to start without the platform" if outcomes else "Smoke: no unit to start"]
    for each in outcomes:
        shown = report.printable(each.unit)
        if each.state == "started":
            out.append(f"✓ {shown}  {', '.join(report.printable(name) for name in each.started)} started and answered "
                       f"({seconds(each.seconds)})")
        elif each.state == "failed":
            out.append(f"✗ {shown}  {report.printable(each.note)}")
        else:
            out.append(f"- {shown}  {report.printable(each.note)}")
    return out


def table(outcomes: Sequence[Outcome]) -> str:
    """The Markdown table of the summary: each unit, what started, how it ended."""
    lines = ["| Unit | Services | Result | Time |", "|---|---|---|---|"]
    for each in outcomes:
        result = {"started": "started and answered", "failed": "failed", "not_tried": "not tried",
                  "nothing": "left to the end-to-end run"}[each.state]
        if each.note and each.state in ("not_tried", "nothing"):
            result += f": {each.note}"
        services = ", ".join(f"`{name}`" for name in each.started)
        lines.append(f"| {each.unit} | {services} | {result} | "
                     f"{seconds(each.seconds) if each.state in ('started', 'failed') else ''} |")
    return "\n".join(lines) + "\n"


@entry
def main(argv, out, err, **seams) -> int:
    """`ci smoke`. Exit 1 when a service did not start well, 2 when this machine cannot start them."""
    found = parser("smoke", "Start what does not need the platform, and check that it answers.", units=True)
    args = parsed(found, argv)
    names = parse_units(args.units)
    if not os.path.isdir(args.path):
        raise Unavailable(f"{report.printable(args.path)} is not a folder")
    root = os.path.abspath(args.path)
    runner, sleep, session = seams.get("runner"), seams.get("sleep") or time.sleep, seams.get("session") or project_session
    loading = load(root, names)
    problems: List[Problem] = []
    outcomes: List[Outcome] = []
    for each in loading.refused:
        if needs_settings(each):
            problems.append(finding("SM03", each.file, unit=f"{each.kind}:{each.name}"))
            outcomes.append(Outcome(f"{each.kind}:{each.name}", each.file, "not_tried", (), 0.0, "it needs settings or a license"))
        else:
            problems += refusals([each], "tried")
    tree = trees.FolderTree(root)
    logs: Dict[str, List[str]] = {}
    ready = False
    for unit in loading.units:
        if unit.kind == "setup":   # (it only includes: its units are the ones that start, when they are touched)
            outcomes.append(Outcome(unit.key, unit.file, "nothing", (), 0.0, "it only includes other units"))
            continue
        selection = select(unit)
        if requires_license(tree.text(unit.file)):
            problems.append(finding("SM03", unit.file, unit=unit.key))
            outcomes.append(Outcome(unit.key, unit.file, "not_tried", (), 0.0, "it needs settings or a license"))
            continue
        if not selection.waves:
            why = "; ".join(f"{report.printable(name)}: {why}" for name, why in selection.left[:3]) or "it has no service of its own"
            outcomes.append(Outcome(unit.key, unit.file, "nothing", (), 0.0, why))
            continue
        if not ready:
            docker_ready(runner)
            ready = True
        started_at = time.monotonic()
        project = project_name(unit)
        try:
            with session(root) as opened:
                docker = Docker(project, opened, runner)
                try:
                    make_state(unit, state_folders(unit, [name for wave in selection.waves for name in wave]))
                    result = start_unit(unit, selection, docker, sleep)
                finally:
                    stuck = docker.down(unit)   # whatever happened, nothing is left running
                    if stuck:
                        print(f"The smoke of {report.printable(unit.key)} could not remove its project {project}: {stuck}", file=err)
        except check.ComposeGone:
            raise Unavailable("Docker Compose 5 was not found: " + compose.NO_COMPOSE) from None
        spent = time.monotonic() - started_at
        problems += result.problems
        logs.update({f"{unit.key} {name}": lines for name, lines in result.logs.items()})
        if result.problems:
            bad = [each.message for each in result.problems]
            outcomes.append(Outcome(unit.key, unit.file, "failed", tuple(result.started), spent, "; ".join(bad)))
        else:
            outcomes.append(Outcome(unit.key, unit.file, "started", tuple(result.started), spent))
    skipped = list(loading.notes)
    if args.format != "json":
        out.write("\n".join(lines_of(outcomes)) + "\n")
        for label, lines in logs.items():
            out.write(f"The last {len(lines)} lines of the log of {report.printable(label)}:\n")
            out.write(report.untrusted_text("\n".join(lines) + "\n"))
    extra = ""
    if args.ci:
        extra = "### Units started\n\n" + (table(outcomes) if outcomes else "No unit to start.\n")
        for label, lines in logs.items():
            extra += f"\nThe last {len(lines)} lines of the log of `{label.replace('`', '')}`:\n\n" + fence(lines)
    return conclude("smoke", problems, args, out, "make ci", skipped, title="Start what does not need the platform", extra=extra)


__all__ = ["MESSAGES", "Outcome", "SETTLE", "Selection", "Session", "WAIT", "dependencies", "finding", "labels_of", "main", "make_state",
           "needs_settings", "project_name", "project_session", "ps_rows", "requires_license", "select", "state_folders",
           "state_of", "up_command"]
