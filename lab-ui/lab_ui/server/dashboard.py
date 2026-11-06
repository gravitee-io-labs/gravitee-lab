"""What the Dashboard reads that the state does not hold: the Gravitee products here against the latest version of
their channel in the registry, the gateway a platform of the Cloud runs here against the latest stable one, and Docker.
What runs and what is on is the state's (`/api/state`), which the page draws live. What Docker says of
itself and of its Compose is what the state reads (compose.docker_info, compose_version), what it holds is kept for a
minute and the registry's answers for an hour; the load of the containers comes from one `docker stats` that every page
following it shares."""

import http.client
import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as Slow

from ..docker import compose, images
from ..library import project as projects
from ..library.versions import judge, channel, latest, order, parse

DOCKER_TTL = 60       # seconds what Docker holds is kept: a quick back and forth does not read it again
DF_WAIT = 0.4         # seconds a read lasts at the most for what Docker holds, the slow one, before the page is drawn without it
LATEST_TTL = 3600     # seconds the versions a registry lists are kept
RETRY = 60            # seconds before a registry that did not answer is asked again
STALE = 3             # seconds without a sample of `docker stats` after which no container is taken to run
RESTART = 2           # seconds before `docker stats` is started again when it ended on its own
NAMES_TTL = 10        # seconds the names of the lab's containers are kept
TYPES = ("images", "containers", "volumes", "cache")   # what `docker system df` counts, in the order it lists them
# Docker writes sizes in powers of 1000 (`system df`: 58.89GB) or of 1024 (`stats`: 731.8MiB)
UNITS = {"B": 1,
         **{unit: 1000 ** power for power, unit in enumerate(["kB", "MB", "GB", "TB", "PB"], 1)},
         **{unit: 1024 ** power for power, unit in enumerate(["KiB", "MiB", "GiB", "TiB", "PiB"], 1)}}
SIZE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*([A-Za-z]+)")
# What `docker stats` writes besides the lines: escapes that move the cursor or clear the screen. A screen drawn again
# begins with the cursor sent home
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
REDRAW = re.compile(r"\x1b\[(?:2J|H)")


# ---------------------------------------------------------------------------------------------------------------------
# Sizes and Docker
# ---------------------------------------------------------------------------------------------------------------------

def size(text):
    """The bytes of a size as Docker writes it ("58.89GB", "731.8MiB", "42.7GB (72%)", "0B"); 0 for anything else."""
    found = SIZE.match(text or "")
    return int(round(float(found.group(1)) * UNITS[found.group(2)])) if found and found.group(2) in UNITS else 0


def _object(text):
    """A JSON object from text; empty when it is none."""
    try:
        found = json.loads(text)
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def _number(text):
    return int(text) if str(text).isdigit() else 0


def parse_df(out):
    """What Docker holds, from `docker system df --format '{{json .}}'` (an object a line): {images, containers, volumes,
    cache, total}, sizes in bytes. `size` is what a type takes and `reclaimable` what no container uses (Docker's word for
    it): unused images, the writable layer of stopped containers, unused local volumes (which can hold data) and unused
    build cache. `reclaimable` never exceeds `size` (Docker writes both rounded). `total` is the sum of the four types.
    None without a line."""
    rows = {}
    for line in out.splitlines():
        row = _object(line)
        if row.get("Type"):
            rows[row["Type"]] = row
    if not rows:
        return None

    def sizes(kind):
        row = rows.get(kind, {})
        taken = size(row.get("Size"))
        return {"size": taken, "reclaimable": min(size(row.get("Reclaimable")), taken)}

    def count(kind, field="TotalCount"):
        return _number(rows.get(kind, {}).get(field))
    found = {"images": {"count": count("Images"), **sizes("Images")},
             "containers": {"count": count("Containers"), "running": count("Containers", "Active"), **sizes("Containers")},
             "volumes": {"count": count("Local Volumes"), **sizes("Local Volumes")},
             "cache": {"count": count("Build Cache"), **sizes("Build Cache")}}
    found["total"] = {field: sum(found[kind][field] for kind in TYPES) for field in ("size", "reclaimable")}
    return found


def _usage():
    """parse_df of Docker now, with when it was read; about two seconds. None when Docker does not answer, which is not
    kept."""
    def ask():
        rc, out, err = compose.run(["docker", "system", "df", "--format", "{{json .}}"], timeout=60)
        found = parse_df(out) if rc == 0 else None
        if not found:
            raise compose.ComposeError(err.strip() or "Docker did not say what it holds")
        return {**found, "read_at": int(time.time())}
    try:
        return compose.CACHE.get("local:dashboard:df", DOCKER_TTL, ask)
    except compose.ComposeError:
        return None


_flight, _flying = None, threading.Lock()   # the one read of what Docker holds that is under way


def _usage_flight(fresh=False):
    """The future of `_usage`: the read under way, else a new one in a thread of its own, so that a read of the page
    can leave without it (`DF_WAIT`) and the next finds it done. `fresh`: read again, whatever is under way."""
    global _flight
    with _flying:
        if fresh or _flight is None or _flight.done():
            flight = _flight = Future()

            def run():
                try:
                    flight.set_result(_usage())
                except Exception as error:  # noqa: BLE001 - whatever it is, the read that waits for it says it
                    flight.set_exception(error)
            threading.Thread(target=run, daemon=True).start()
        return _flight


def _asked(ask):
    """(what `ask` gives, the words of the RuntimeError it raised)."""
    try:
        return ask(), None
    except RuntimeError as error:
        return None, str(error)


def cores():
    """The cores Docker has, which the load of the containers is a share of; this machine's when it does not say."""
    info, _ = _asked(compose.docker_info)
    return (info or {}).get("cpus") or os.cpu_count() or 1


# ---------------------------------------------------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------------------------------------------------

_reading, _failed, _lock = set(), {}, threading.Lock()   # the reads under way, when each failed last


def _read(project, component, key):
    """Asks the registry of a component (images.tags, which keeps its answer ten minutes) and keeps the latest
    versions for an hour. Offline, refused or not published, it is asked again after RETRY seconds."""
    default = component["registry_default"] or ""
    try:
        compose.CACHE.get(key, LATEST_TTL, lambda: latest(
            tag["name"] for tag in images.tags(project, component["id"], None if "$" in default else default)))
    except (OSError, ValueError, LookupError, RuntimeError, http.client.HTTPException):
        _failed[key] = time.monotonic()
    finally:
        with _lock:
            _reading.discard(key)


def _listed(project, component):
    """((the latest stable, the latest pre-release) or None, whether a read is under way). The read happens in the
    background, so that the page never waits for a registry: it fills in once they are known."""
    key = f"dashboard:latest:{component['id']}"
    known = compose.CACHE.kept(key, LATEST_TTL)   # looks only: nothing is read here, nor waited for
    if known is not None:
        return known, False
    with _lock:
        failed = _failed.get(key)
        if key not in _reading and (failed is None or time.monotonic() - failed >= RETRY):
            _reading.add(key)
            threading.Thread(target=_read, args=(project, component, key), daemon=True).start()
        return None, key in _reading


def _oldest(tags):
    """The tag with the lowest version: what a product runs when its services are not on the same one. A tag that
    names no version comes after."""
    return min(tags, key=lambda tag: (parse(tag) is None, order(parse(tag)) if parse(tag) else (), tag))


def deployed(project, component, containers):
    """(the tag a product's component runs, 'running'), else (the tag configured for it, 'configured')."""
    running = {projects.tag(containers[name]["image"]) for name in component["services"]
               if name in containers and containers[name]["state"] == "running" and containers[name].get("image")} - {""}
    if running:
        return _oldest(running), "running"
    return project["services"][component["services"][0]]["tag"], "configured"


def products(project, containers):
    """(the Gravitee products of the platform, whether versions are still being read). A product is a group of the
    platform with Gravitee images, described by its main component (the one most of its services share): {id, name,
    deployed, deployed_state, channel, stable, prerelease, latest, status}, in the order of the groups. `latest` is the
    newest version of the channel of `deployed` and `status` says whether it is that one (judge)."""
    rows, seen, pending = [], set(), False
    for component in images.components(project):
        owner = component["owner"]
        if component["kind"] != "gravitee" or owner["kind"] != "platform" or owner["id"] in seen:
            continue
        seen.add(owner["id"])
        tag, state = deployed(project, component, containers)
        found, reading = _listed(project, component)
        stable, prerelease = found or (None, None)
        status, newest = judge(tag, stable, prerelease)
        rows.append({"id": owner["id"], "name": owner["name"], "deployed": tag, "deployed_state": state,
                     "channel": channel(tag), "stable": stable, "prerelease": prerelease, "latest": newest,
                     "status": status})
        pending = pending or reading
    return rows, pending


# ---------------------------------------------------------------------------------------------------------------------
# The whole page
# ---------------------------------------------------------------------------------------------------------------------

def read(project, refresh=False):
    """What the page reads besides the state: {docker, data, products, pending}. Docker is asked at
    once for what it says of itself and its Compose (as the state keeps them: compose.docker_info, compose_version),
    what it holds (the slow one, about two seconds, kept DOCKER_TTL seconds) and what runs; `refresh` reads them again.
    Docker not answering is `docker.error`, with the rest: the products as configured. What Docker holds
    is waited for until the read has lasted DF_WAIT seconds, as long as it takes for a `refresh`: past that `data` is None
    and the answer is `pending`, and the next read has it. `pending`: versions or data are still being read, ask again."""
    began = time.monotonic()
    if refresh:
        compose.CACHE.drop("local:dashboard:")
        compose.CACHE.drop("docker:")
    flight = _usage_flight(refresh)
    with ThreadPoolExecutor(max_workers=3) as pool:
        asked = [pool.submit(_asked, compose.docker_info), pool.submit(_asked, compose.compose_version),
                 pool.submit(compose.containers)]
    (info, why), (version, _), (containers, down) = (future.result() for future in asked)
    try:
        data, slow = flight.result(timeout=None if refresh else max(0, DF_WAIT - (time.monotonic() - began))), False
    except Slow:
        data, slow = None, True
    docker = {"engine": None, "compose": version, "os": None, "cpus": None, "memory": None,
              **{key: value for key, value in (info or {}).items() if key != "root"}}
    if down or why:
        docker["error"] = down or why
    rows, pending = products(project, containers)
    return {"docker": docker, "data": data, "products": rows, "pending": pending or slow}


# ---------------------------------------------------------------------------------------------------------------------
# The load of the containers
# ---------------------------------------------------------------------------------------------------------------------

def _percent(text):
    """A percentage as `docker stats` writes it ("0.47%"); 0 for "--" and the like."""
    try:
        return float(str(text).rstrip("%"))
    except ValueError:
        return 0.0


def _stats_process():
    """The `docker stats` that reads the load, started as a job's command is (compose.spawn): the Lab UI ends it as it
    exits. None while it exits."""
    return compose.spawn(["docker", "stats", "--format", "{{json .}}"], subprocess.DEVNULL)


def lab_names():
    """The names of the containers of this project, which is the lab's: what Docker runs for something else is not its
    load. Kept a few seconds; None when they cannot be told, and the load is then every container's."""
    def ask():
        rc, out, err = compose.run(["docker", "ps", "--filter", f"label=com.docker.compose.project={compose.project_name()}",
                                    "--format", "{{.Names}}"], timeout=10)
        if rc != 0:
            raise compose.ComposeError(err.strip())
        return set(out.split())
    try:
        return compose.CACHE.get("dashboard:names", NAMES_TTL, ask)
    except compose.ComposeError:
        return None


class Stats:
    """The load of the containers, from one `docker stats` for every page that follows it: the first to join starts it,
    the last to leave kills it, and it does not run with nobody there. It draws its screen about twice a second, an
    object per container: a sample is what lies between two screens (told by the cursor sent home, a blank line, or a
    container that comes twice), and the latest complete one is kept. It is started again when it ends on its own,
    Docker having stopped. `names`: the containers that count (the lab's, `lab_names`), every one when None."""

    def __init__(self, spawn=_stats_process, clock=time.monotonic):
        self.spawn, self.clock, self.lock = spawn, clock, threading.Lock()
        self.clients, self.closed, self.process, self.thread = 0, None, None, None
        self.batch, self.last, self.since, self.names = {}, None, 0, None

    def join(self):
        with self.lock:
            self.clients += 1
            if self.clients == 1:
                self.closed, self.batch, self.last, self.since = threading.Event(), {}, None, self.clock()
                self.thread = threading.Thread(target=self._run, args=(self.closed,), daemon=True)
                self.thread.start()

    def leave(self):
        with self.lock:
            self.clients -= 1
            if self.clients == 0:
                self.closed.set()
                if self.process:
                    self.process.kill()

    def _start(self, closed):
        """A `docker stats` started, unless the last client left meanwhile: none outlives them."""
        with self.lock:
            if closed.is_set():
                return None
            self.process = self.spawn()
            return self.process

    def _run(self, closed):
        while True:
            try:
                process = self._start(closed)
            except OSError:   # no docker command yet: it is asked again, Docker can be installed while the page is open
                if closed.wait(RESTART):
                    return
                continue
            if process is None:
                return
            for line in process.stdout:
                if closed.is_set():
                    break
                self.feed(line)
            process.wait()   # reaped: it ended, or was killed
            compose.release(process)
            if closed.wait(RESTART):
                return

    def feed(self, line):
        """One line of `docker stats`."""
        text = ANSI.sub("", line).strip()
        if REDRAW.search(line) or not text:
            self._finish()
        row = _object(text)
        key = row.get("ID") or row.get("Name")
        if not key or (self.names is not None and row.get("Name") not in self.names):
            return
        if key in self.batch:
            self._finish()
        self.batch[key] = (_percent(row.get("CPUPerc")), size(str(row.get("MemUsage", "")).split("/")[0]), row.get("Name") or key)

    def _finish(self):
        if self.batch:
            self.last = (self.clock(), sum(cpu for cpu, _, _ in self.batch.values()),
                         sum(memory for _, memory, _ in self.batch.values()), len(self.batch), list(self.batch.values()))
            self.batch = {}

    def read(self, cores):
        """The load as the stream sends it, {cpu_percent, memory_used, containers, top, at}: the CPU of the containers as a
        share of the cores Docker has, 0 to 100, and `top`, each container's own, [{name, cpu, memory}] from the heaviest in
        memory. None while the first sample is on its way. No sample for STALE seconds means nothing runs (`docker stats`
        says nothing without a container) or Docker does not answer."""
        last = self.last
        if self.clock() - (last[0] if last else self.since) > STALE:
            cpu, memory, count, each = 0.0, 0, 0, []
        elif last:
            _, cpu, memory, count, each = last
        else:
            return None
        top = [{"name": name, "cpu": round(min(100.0, share / cores), 1), "memory": used}
               for share, used, name in sorted(each, key=lambda one: -one[1])]
        return {"cpu_percent": round(min(100.0, cpu / cores), 1), "memory_used": memory, "containers": count, "top": top,
                "at": int(time.time())}


STATS = Stats()
