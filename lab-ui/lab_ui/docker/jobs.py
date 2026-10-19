"""Jobs: the long-running actions, one at a time, with their output kept for the page and how far they are measured
from it and from what Docker says of their containers. The last failure on an item is remembered until the next job
that starts or stops it."""

import json
import re
import subprocess
import threading
import time
import uuid

from . import compose
from .. import paths
from .pulls import Pulls

# Per-layer pull progress ("3f1c0a7d9e2b Pull complete 0B"): one line per layer, too noisy to stream
LAYER_PROGRESS = re.compile(r"^\s*[0-9a-f]{12}\s")
# Compose's options that take a value: the word after one is not the command
VALUED = ("-p", "--project-name", "-f", "--file", "--env-file", "--profile", "--project-directory", "--ansi")
# How much of an image's bytes count in the job's progress while it downloads: it is pulled once Compose says so
DOWNLOAD_SHARE = 0.9
# What Compose prints about a container or an image: " Container gio-redis Started ", " Image redis:8 Pulled "
UNIT = re.compile(r"\s*(Container|Image)\s+(\S+)\s+(\w+)\s*")
# The steps of a container that starts, each a share of its way: made, running, ready
CREATED, RUNNING, READY = 0.2, 0.5, 1
# What Compose says of a container that starts. "Started" and "Running" (it ran already) say that it runs: whether it
# is ready is Docker's to say (observe), since a health check may come with its image, which Compose does not show.
# "Waiting": it runs and its health is awaited; "Healthy": ready. A one-shot is ready once it ended well (_share)
STARTS = {"Created": CREATED, "Recreated": CREATED, "Starting": CREATED, "Restarting": CREATED, "Started": RUNNING,
          "Running": RUNNING, "Waiting": RUNNING, "Healthy": READY}
# Of a container that stops: half way while it stops or is removed, there once stopped or removed
STOPS = {"Stopping": 0.5, "Stopped": 1, "Removing": 0.5, "Removed": 1}
# Of an image: pulled or built
IMAGES = {"Pulling": 0, "Pulled": 1, "Building": 0, "Built": 1}
# How a container that starts is waited for (awaited): until ready, or until it ended well for a one-shot
STARTING = ("start", "once")
# Where Docker says a container is (model.container_steps), as the share of the way of one that starts. One with an
# issue keeps what it had: it is not on its way
LIVE = {"created": CREATED, "running": RUNNING, "ready": READY}
# Where Docker says a container that stops is there: stopped, removed, or made and never started
GONE = ("stopped", "absent", "created")
# Where a container is still on its way to what the job waits for: made, or running and not ready yet
ON_ITS_WAY = ("created", "running")
NEVER = float("inf")   # when what has not happened yet happened: no answer of Docker comes after it
KEPT = 20
# What the catalog turns on and off, as a job's key names it ("addon:kafka"): a stop of one that failed is a failure of
# what it stopped with it too (Jobs.failed_job). The Stack's stop leaves what is on as it is
ITEMS = ("addon", "setup", "playground")
# An Azure registry refusing a pull for want of a login, as Docker says it in the last lines of a job: the registry
# and the refusal on one line, in either order ("pull access denied for x.azurecr.io/…", "…azurecr.io…: unauthorized")
AZURE_REGISTRY = re.compile(r"\b([a-z0-9]+\.azurecr\.io)\b", re.I)
REFUSED = re.compile(r"unauthorized|authentication required|access denied", re.I)
NOT_LOGGED_IN = "Not logged in to Azure"
# The image a build starts from, which BuildKit could not fetch: "failed to resolve source metadata for
# docker.io/library/node:20-alpine: …", or the line of the step that failed, "ERROR [internal] load metadata for …"
BASE_IMAGE = re.compile(r"(?:resolve source metadata|load metadata) for (\S+?)(?::\s|$)")


def _command(cmd):
    """Where the Compose command is in a command line of the Lab UI, and which: (index, "up"), None for anything else
    than Compose with its plain progress."""
    if cmd[:len(compose.COMPOSE)] != compose.COMPOSE:
        return None
    skip = False
    for index in range(len(compose.COMPOSE), len(cmd)):
        if skip:
            skip = False
        elif cmd[index] in VALUED:
            skip = True
        elif not cmd[index].startswith("-"):
            return index, cmd[index]
    return None


def _pull_before(cmd):
    """The pull that comes before an `up`: what is missing of the images it names, which Compose shows layer by layer
    here and not when it pulls them itself, as it starts. What it cannot pull is left to the `up`, which says why."""
    found = _command(cmd)
    if not found or found[1] != "up":
        return None
    index = found[0]
    options, names = [], []
    for word in cmd[index + 1:]:
        (options if word.startswith("-") else names).append(word)
    deps = ["--include-deps"] if names and "--no-deps" not in options else []
    return [*cmd[:index], "pull", "--policy", "missing", "--ignore-buildable", "--ignore-pull-failures", *deps, *names]


def _json(cmd):
    """A pull as Compose writes it in JSON."""
    return cmd[:2] + ["--progress", "json"] + cmd[4:] if cmd[2:4] == ["--progress", "plain"] else cmd


def _event(line):
    """A line of Compose's JSON progress as a dict, None for anything else (a warning, an error)."""
    if not line.startswith("{"):
        return None
    try:
        found = json.loads(line)
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def _image(name):
    """An image as Compose prints it and as a file writes it, alike: without Docker Hub's own prefixes."""
    return re.sub(r"^(docker\.io/)?(library/)?", "", name)


def awaited(services, start=(), stop=(), images=()):
    """What a command waits for, the units its stage counts: {unit: how}, a unit being ("Container", name) or
    ("Image", name). A container that starts ("start") is there once ready as the Lab UI judges it: healthy, or
    running when Docker checks no health; a one-shot ("once") once it ended well. One that stops ("stop") once stopped
    or removed; an image ("image") once pulled or built. A service both stopped and started is started: a restart."""
    found = {("Image", _image(name)): "image" for name in images if name}
    found.update({("Container", services[name]["container"]): "stop" for name in stop})
    found.update({("Container", services[name]["container"]): "once" if services[name]["oneshot"] else "start"
                  for name in start})
    return found


def _share(how, status):
    """How far a line of Compose says a unit is; None when it says nothing of it. A download says it itself."""
    if how == "stop":
        return STOPS.get(status)
    if how == "image":
        return IMAGES.get(status)
    if how == "download":
        return None
    if (how, status) == ("once", "Exited"):
        return READY
    return STARTS.get(status)


class JobFailed(Exception):
    """Stops a job, with the reason shown as its last line."""


class Stage:
    """A part of a job that its progress counts: what it waits for, known before it begins, and how far each is. It
    begins and, unless the job fails in it, ends (time.monotonic())."""

    def __init__(self, waits):
        self.how = dict(waits or {})
        self.done = dict.fromkeys(self.how, 0)
        # When the lines said that a container that starts runs: what Docker says of it counts from then on
        self.ran = {}
        self.began = self.ended = None

    def share(self):
        """How far it is; None before it begins, and while it runs waiting for nothing it can name. Once over, an image
        or a download counts in full, a container by what was seen of it: its health may still be awaited."""
        if self.began is None:
            return None
        over = self.ended is not None
        if not self.how:
            return 1 if over else None
        shares = [1 if over and unit[0] != "Container" else done for unit, done in self.done.items()]
        return sum(shares) / len(shares)


class Job:
    """One action: steps run in order, each a command or a function of the job. Its stages are what its progress
    counts: the phases declared as taking time, else the commands among its steps. `waits` says what each stage waits
    for (awaited()), in their order; one it leaves out waits for nothing it can name. `key` is what it acts on
    ("addon:kafka", "platform", "plugins:apim") and `verb` what it does ("start", "pull"): the button that started it
    shows it by them."""

    def __init__(self, title, steps, key=None, verb=None, changes=None, phases=None, waits=None):
        changes = changes or {}
        self.id, self.title, self.key, self.verb = uuid.uuid4().hex[:8], title, key, verb
        # What the job starts and stops, as [kind, id] pairs: their rows say so meanwhile
        self.changes = {"start": list(changes.get("start", [])), "stop": list(changes.get("stop", []))}
        self.steps, self.lines, self.done, self.ok = steps, [], False, None
        self.started, self.ended = time.time(), None
        self.phase, self.phases = None, []
        self._declared = phases
        count = sum(not callable(step) for step in steps) if phases is None else len(phases)
        waits = list(waits or [])
        if len(waits) > count:
            raise ValueError(f"{title}: what {len(waits)} stages wait for, for {count} stages")
        self._stages = [Stage(found) for found in waits + [None] * (count - len(waits))]
        # The stage that runs, and how many commands began one (a job without declared phases)
        self._stage, self._commands = None, 0
        # How far the job is, None until something is measured. It never goes down
        self.progress = None
        # Ended well, with nothing it waits for still on its way: what it did not bring where it goes (unhealthy,
        # restarting, exited) will not get there by itself. Once settled, it stays so. `_finished`: when it ended
        # well (time.monotonic()), what Docker said before is not where it left its containers
        self.settled, self._finished = False, None
        # The lines of the commands and what the state's refresh reads of Docker move the same stages
        self._lock = threading.RLock()
        self._process, self._canceled = None, False
        # The images it pulls, layer by layer (pulls.Pulls)
        self.pulls = Pulls()

    def step(self, name):
        """A named phase: a playground's page shows them as a progress list. One that was declared is a stage,
        until the next phase."""
        self._leave()
        self.phase = name
        self.phases.append({"name": name, "started": time.time()})
        self.lines.append(f"▸ {name}")
        if name in (self._declared or []):
            self._enter(self._stages[self._declared.index(name)])

    def _enter(self, stage):
        with self._lock:
            self._stage, stage.began = stage, time.monotonic()
            self._measure()

    def _leave(self):
        """The stage that runs, if any, is over: its command or its phase ended well."""
        with self._lock:
            if self._stage:
                self._stage.ended, self._stage = time.monotonic(), None
                self._measure()

    def unit(self, unit, fraction):
        """How far one thing the running stage waits for is, said by a step: a download by its bytes."""
        with self._lock:
            stage = self._stage
            if stage and unit in stage.how and fraction > stage.done[unit]:
                stage.done[unit] = fraction
                self._measure()

    def expect(self, waits):
        """What the running stage waits for and could not know before it began: a service that started since, which
        it stops to load plugins."""
        with self._lock:
            stage = self._stage
            if stage:
                for unit, how in waits.items():
                    if unit not in stage.how:
                        stage.how[unit], stage.done[unit] = how, 0
                self._measure()

    def _printed(self, line):
        """A line Compose printed about a container or an image moves it, when the running stage waits for it. An
        image it pulls or builds for a stage that waits for something is waited for too: what is created from it
        waits for it, and Compose names images before any container."""
        found = UNIT.fullmatch(line)
        if not found:
            return
        kind, name, status = found.groups()
        unit = (kind, _image(name) if kind == "Image" else name)
        with self._lock:
            stage = self._stage
            if stage is None:
                return
            if unit not in stage.how and kind == "Image" and status in IMAGES and stage.how:
                stage.how[unit], stage.done[unit] = "image", 0
            how = stage.how.get(unit)
            share = _share(how, status) if how else None
            if share is None:
                return
            if how in STARTING and share >= RUNNING:
                stage.ran.setdefault(unit, time.monotonic())
            if share > stage.done[unit]:
                stage.done[unit] = share
                self._measure()

    def observe(self, steps, asked):
        """What Docker says of the containers the job waits for, `steps` ({container: step}, model.container_steps)
        as asked at `asked` (time.monotonic()): whether they are ready, which Compose does not always print, and
        which comes after it returns. It counts for a container that starts once the lines said that it runs, or
        once its stage is over, and only when asked after that: until then Docker may still show the container it
        replaces, healthy. For one that stops, once its stage began. A job that failed stays where it was; one that
        ended well settles once nothing it waits for is still on its way."""
        with self._lock:
            if self.ok is False:
                return
            for stage in self._stages:
                if stage.began is None or stage.began > asked:
                    continue
                for unit, how in stage.how.items():
                    if unit[0] != "Container":
                        continue
                    step = steps.get(unit[1], "absent")
                    if how == "stop":
                        live = 1 if step in GONE else 0
                    elif min(stage.ran.get(unit, NEVER), stage.ended or NEVER) <= asked:
                        live = LIVE.get(step, 0)
                    else:
                        continue
                    stage.done[unit] = max(stage.done[unit], live)
            self._measure()
            if self.ok and not self.settled and asked >= self._finished:
                self.settled = not any(unit[0] == "Container" and done < 1 and steps.get(unit[1]) in ON_ITS_WAY
                                       for stage in self._stages for unit, done in stage.done.items())

    def _measure(self):
        """How far the job is: its stages alike, each by its share, one not begun counting 0. Nothing is measured
        until a stage begins with something to wait for, or ends."""
        shares = [stage.share() for stage in self._stages]
        if any(share is not None for share in shares):
            self.progress = max(self.progress or 0, sum(share or 0 for share in shares) / len(shares))

    def _phases(self):
        """The phases with how far each is: a declared one by its stage, any other 1 once over."""
        phases, declared = list(self.phases), self._declared or []
        found = []
        for number, phase in enumerate(phases):
            if phase["name"] in declared:
                progress = self._stages[declared.index(phase["name"])].share()
            else:
                progress = None if number == len(phases) - 1 and not self.ok else 1
            found.append(dict(phase, progress=progress))
        return found

    def _containers(self):
        """How far each container the job waits for is along it: its mean share in the stages that wait for it, one
        not begun counting 0. A container stopped then started again is half way once stopped."""
        shares = {}
        for stage in self._stages:
            for (kind, name), done in stage.done.items():
                if kind == "Container":
                    shares.setdefault(name, []).append(done)
        return {name: sum(found) / len(found) for name, found in shares.items()}

    def detail(self):
        """The latest line worth showing under the current phase (not build output)."""
        for line in reversed(self.lines[-200:]):
            text = line.strip()
            if text and not text.startswith(("#", "$", "▸")):
                return text[:160]
        return ""

    def last_line(self, start):
        """The last line printed from this one on, "" when there is none: what a command that failed said last."""
        return next((line.strip() for line in reversed(self.lines[start:]) if line.strip()), "")

    def login(self):
        """The Azure registry the job needed a login to: one refused its pull, in the last lines it printed; None
        otherwise."""
        for line in self.lines[-10:]:
            found = AZURE_REGISTRY.search(line)
            if found and REFUSED.search(line):
                return found.group(1).lower()
        return None

    def base_image(self):
        """The image a build starts from that could not be fetched, in the last lines the job printed; None
        otherwise. Docker's own words for why stay in its output."""
        for line in reversed(self.lines[-10:]):
            found = BASE_IMAGE.search(line.strip())
            if found:
                return _image(found.group(1))
        return None

    def error(self):
        """Why the job failed, in one line: its last line, where _fail() leaves the reason; "Not logged in to Azure"
        when an Azure registry refused a pull, the base image when a build could not fetch it."""
        if self._canceled:
            return "Canceled"
        if self.login():
            return NOT_LOGGED_IN
        base = self.base_image()
        return f"Base image {base} could not be fetched" if base else self.last_line(0)[:300]

    def summary(self):
        """What the state and the job's stream say of it. `containers` is how far each container it waits for is
        (_containers), for the progress of what they belong to; `settled`, whether it ended well with nothing it
        waits for still on its way."""
        with self._lock:
            return {"id": self.id, "title": self.title, "for": self.key, "verb": self.verb,
                    "changes": self.changes, "done": self.done, "ok": self.ok,
                    "started": self.started, "ended": self.ended,
                    "phases": self._phases(), "detail": self.detail(),
                    "progress": self.progress, "containers": self._containers(), "settled": self.settled,
                    "error": self.error() if self.done and not self.ok else None}

    @property
    def canceled(self):
        """Whether the job was canceled: a step that waits for something ends then."""
        return self._canceled

    def cancel(self):
        """Ends the job: the command that runs is terminated and the steps left are skipped."""
        if self.done:
            return
        self._canceled = True
        process = self._process
        if process and process.poll() is None:
            process.terminate()

    def exec(self, cmd, quiet=False, env=None):
        """Runs a command in the project root, streaming its output. When it fails, the job fails with the last line
        it printed: Compose's reason ("dependency failed to start: ..."). It gets no input: a question Compose asks
        would wait on the terminal the Lab UI was started from. `env`: variables set for this command alone, which a
        secret goes by, named in the command (`-e NAME`) and never written in it, since the command is a line of the
        job. What an `up` starts is pulled first, if missing, where its progress is told layer by layer (pull_view)."""
        if not quiet:
            self.lines.append(f"$ {' '.join(cmd)}")
        before = _pull_before(cmd)
        if before:
            self._spawn(before, env, lenient=True)
            if self._canceled:
                raise JobFailed("Canceled")
        self._spawn(cmd, env)

    def _spawn(self, cmd, env, lenient=False):
        """One command. A pull is read as JSON, its progress told as a plain one would; `lenient`: it does not fail
        the job, nor does it say what it could not pull."""
        pull = (_command(cmd) or (0, ""))[1] == "pull"
        start = len(self.lines)
        process = subprocess.Popen(_json(cmd) if pull else cmd, cwd=paths.ROOT,
                                   env={**compose.environment(), **(env or {})}, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                                   bufsize=1)
        self._process = process
        if self._canceled:   # canceled while the command was starting
            process.terminate()
        for line in process.stdout:
            event = _event(line) if pull else None
            if event is not None:
                self._pulled(event, lenient)
            elif not (lenient or LAYER_PROGRESS.match(line)):
                self.lines.append(line.rstrip("\n"))
                self._printed(line)
        if process.wait() != 0 and not lenient:
            raise JobFailed(self.last_line(start) or f"Failed with exit code {process.returncode}")

    def _pulled(self, event, lenient):
        """A line of a pull's progress: what a plain one would print, and how far the image is."""
        with self._lock:
            line = self.pulls.event(event, lenient)
            if line:
                self.lines.append(line)
                self._printed(line)
            name = (event.get("id") or "")[6:] if (event.get("id") or "").startswith("Image ") else \
                (event.get("parent_id") or "")[6:]
            fraction = self.pulls.fraction(name)
            if fraction is not None and event.get("text") != "Pulled":
                self.unit(("Image", _image(name)), fraction * DOWNLOAD_SHARE)

    def pull_view(self):
        """How far the images the job pulls are, and how fast: pulls.Pulls.view(); None when it pulls none."""
        with self._lock:
            return self.pulls.view()

    def run_steps(self, steps, stages=False):
        """Runs steps in order, until one fails or the job is canceled. `stages`: each command is a stage."""
        for step in steps:
            if self._canceled:
                break
            if callable(step):
                step(self)
            elif stages:
                self._enter(self._stages[self._commands])
                self._commands += 1
                self.exec(step)
                self._leave()
            else:
                self.exec(step)

    def run(self):
        try:
            self.run_steps(self.steps, stages=self._declared is None)
            if self._canceled:
                raise JobFailed("Canceled")
            self._finish()
        except JobFailed as e:
            self._fail(str(e))
        except Exception as e:  # noqa: BLE001 - reported to the page
            self._fail(f"Error: {e}")

    def _finish(self):
        """The job ended well: its last stage is over, and so is any declared phase it had no need to enter."""
        with self._lock:
            self._leave()
            now = time.monotonic()
            for stage in self._stages:
                if stage.began is None:
                    stage.began = stage.ended = now
            self.ok, self._finished = True, now
            self._measure()

    def _fail(self, reason):
        """The job ends on its reason, as its last line, said once: a command's own last line may be it already."""
        self.ok = False
        reason = "Canceled" if self._canceled else reason
        if self.last_line(0) != reason:
            self.lines.append(reason)


class Jobs:
    """The jobs of this run of the Lab UI: one at a time, the last ones kept for their output."""

    def __init__(self):
        self.jobs, self.current, self._lock = [], None, threading.Lock()

    def start(self, title, steps, *, key, verb, changes=None, phases=None, waits=None):
        """Starts a job. Every job says what it acts on and what it does (`key`, `verb`: see Job)."""
        with self._lock:
            if self.current and not self.current.done:
                raise RuntimeError(f"'{self.current.title}' is still running")
            job = Job(title, steps, key, verb, changes, phases, waits)
            self.jobs = (self.jobs + [job])[-KEPT:]
            self.current = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job):
        """A job changes what Docker runs: what was read from it is dropped before the job shows as done."""
        try:
            job.run()
            compose.CACHE.drop("local:")
        finally:
            job.ended, job.done = time.time(), True

    def get(self, jid):
        for job in self.jobs:
            if job.id == jid:
                return job
        raise paths.NotFound("No such job")

    def failed_job(self, kind, uid):
        """The last job on this item when it failed or was canceled: {id, verb, error, login}, its reason in one line
        ("Canceled" for a canceled one), and the registry it needed a login to (Job.login). So is the stop of
        another item that failed while it stopped this one too (a playground's of the add-ons it brings, a setup's of
        its add-ons, an add-on's of what needs it): the on file does not have it, and what of it still runs was not
        started outside the Lab UI. Its row says so until the next job on it, or the next one that starts or stops it
        with something else (a setup, the stack) and ends well."""
        for job in reversed(self.jobs):
            if job.key == f"{kind}:{uid}":
                if not job.done or job.ok:
                    return None
                login = None if job._canceled else job.login()
                return {"id": job.id, "verb": job.verb, "error": job.error(), "login": login}
            if job.done and job.ok is False and job.verb == "stop" and (job.key or "").split(":")[0] in ITEMS \
                    and [kind, uid] in job.changes["stop"]:
                return {"id": job.id, "verb": "stop", "error": job.error(), "login": None}
            if job.ok and [kind, uid] in job.changes["start"] + job.changes["stop"]:
                return None
        return None


JOBS = Jobs()
