"""What the Dashboard reads that the state does not hold: the Gravitee products here against the latest version of
their channel in the registry, the gateway a platform of the Cloud runs here against the latest stable one, Docker and
this machine. What runs and what is on is the state's (`/api/state`), which the page draws live. Docker's answers are
kept for a minute and the registry's for an hour; the load of the containers comes from one `docker stats` that every
page following it shares."""

import http.client
import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as Slow
from pathlib import Path

from ..docker import compose, images
from ..library import project as projects

DOCKER_TTL = 60       # seconds Docker's answers are kept: a quick back and forth does not read them again
DF_WAIT = 0.4         # seconds a read lasts at the most for what Docker holds, the slow one, before the page is drawn without it
LATEST_TTL = 3600     # seconds the versions a registry lists are kept
RETRY = 60            # seconds before a registry that did not answer is asked again
STALE = 3             # seconds without a sample of `docker stats` after which no container is taken to run
RESTART = 2           # seconds before `docker stats` is started again when it ended on its own
NAMES_TTL = 10        # seconds the names of the lab's containers are kept
MEMINFO = Path("/proc/meminfo")
TYPES = ("images", "containers", "volumes", "cache")   # what `docker system df` counts, in the order it lists them
# The system an image is built on, at the end of a tag: not part of its version (VARIANTS in js/core.js)
VARIANTS = ("chainguard-fips", "chainguard", "debian", "noble", "jammy", "alpine")
# A tag that names a build: three numbers, then a pre-release that ends with one (alpha.4); not 4.12, latest or alpha
VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]*\d))?")
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


def _info():
    """What `docker info` says of the engine: {engine, os, cpus, memory}. Cpus and memory are what Docker has, not the
    machine. Raises RuntimeError with Docker's words when it does not answer, which is not kept."""
    def ask():
        rc, out, err = compose.run(["docker", "info", "--format", "{{json .}}"], timeout=20)
        info = _object(out)
        if rc != 0 or not info.get("ServerVersion"):
            raise RuntimeError(next(iter(info.get("ServerErrors") or []), None)
                               or (err.strip().splitlines() or ["Docker is not reachable"])[-1])
        return {"engine": info["ServerVersion"], "os": info.get("OperatingSystem"), "cpus": info.get("NCPU"),
                "memory": info.get("MemTotal")}
    return compose.CACHE.get("local:dashboard:info", DOCKER_TTL, ask)


def _compose_version():
    """The version of Docker Compose; None when it does not answer."""
    def ask():
        rc, out, _ = compose.run(["docker", "compose", "version", "--short"], timeout=20)
        return out.strip().lstrip("v") if rc == 0 and out.strip() else None
    return compose.CACHE.get("local:dashboard:compose", DOCKER_TTL, ask)


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
    """parse_df of Docker now, with when it was read; about two seconds. None when Docker does not answer."""
    def ask():
        rc, out, _ = compose.run(["docker", "system", "df", "--format", "{{json .}}"], timeout=60)
        found = parse_df(out) if rc == 0 else None
        return {**found, "read_at": int(time.time())} if found else None
    return compose.CACHE.get("local:dashboard:df", DOCKER_TTL, ask)


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
    info, _ = _asked(_info)
    return (info or {}).get("cpus") or os.cpu_count() or 1


# ---------------------------------------------------------------------------------------------------------------------
# This machine
# ---------------------------------------------------------------------------------------------------------------------

def parse_meminfo(text):
    """(total, available) bytes from /proc/meminfo, which counts in kB; None for what it lacks."""
    found = {name: int(kb) * 1024 for name, kb in re.findall(r"^(MemTotal|MemAvailable):\s+(\d+) kB", text, re.M)}
    return found.get("MemTotal"), found.get("MemAvailable")


def _memory_mac():
    """macOS: the memory sysctl says, and what a program can have without swapping: the free, inactive and speculative
    pages vm_stat counts."""
    rc, out, _ = compose.run(["sysctl", "-n", "hw.memsize"], timeout=5)
    total = int(out) if rc == 0 else None
    rc, out, _ = compose.run(["vm_stat"], timeout=5)
    page = re.search(r"page size of (\d+) bytes", out)
    pages = re.findall(r"^Pages (?:free|inactive|speculative):\s+(\d+)\.", out, re.M)
    if rc != 0 or not page or len(pages) != 3:
        return total, None
    return total, sum(int(count) for count in pages) * int(page.group(1))


def _memory():
    """(total, available) bytes of this machine's memory: macOS from sysctl and vm_stat, Linux and WSL 2 from
    /proc/meminfo; None for what cannot be read."""
    try:
        if platform.system() == "Darwin":
            return _memory_mac()
        if platform.system() == "Linux":
            return parse_meminfo(MEMINFO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return None, None


def machine():
    """This machine, read in a few milliseconds: {cpus, memory_total, memory_available, disk_total, disk_free}. The
    disk is the one that holds the home folder."""
    total, available = _memory()
    disk = shutil.disk_usage(Path.home())
    return {"cpus": os.cpu_count(), "memory_total": total, "memory_available": available,
            "disk_total": disk.total, "disk_free": disk.free}


# ---------------------------------------------------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------------------------------------------------

def bare(tag):
    """(the tag without its system, the system or ""): "4.13.0-alpha.8-debian" gives ("4.13.0-alpha.8", "debian")."""
    for variant in VARIANTS:
        if tag.endswith(f"-{variant}"):
            return tag[:-len(variant) - 1], variant
    return tag, ""


def parse(tag):
    """(numbers, pre-release or None) of a tag that names a build, its system ignored ("4.13.0-alpha.8-debian" gives
    ((4, 13, 0), "alpha.8")); None for a tag that moves (latest, 4.12, 4.13.0-alpha)."""
    found = VERSION.fullmatch(bare(tag)[0])
    return (tuple(int(number) for number in found.groups()[:3]), found.group(4)) if found else None


def _order(version):
    """What orders versions, as semver does: a release above its pre-releases, the identifiers of a pre-release one by
    one, numbers before words."""
    numbers, pre = version
    if pre is None:
        return numbers, 1, ()
    return numbers, 0, tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in pre.split("."))


def _text(version):
    numbers, pre = version
    return ".".join(map(str, numbers)) + (f"-{pre}" if pre else "")


def older(saved, running):
    """Whether the tag saved names an older version than the one that runs, their systems ignored: making it again goes
    back. False when either names no version (a tag that moves, a local build)."""
    one, other = parse(saved or ""), parse(running or "")
    return bool(one and other) and _order(one) < _order(other)


def latest(tags):
    """(the latest stable, the latest pre-release) among tag names, each as a version without its system; None for
    none. A pre-release is listed only above the stable one: once 4.13.0 is out, 4.13.0-alpha.8 is not news."""
    found = [version for version in map(parse, tags) if version]
    stable = max((version for version in found if version[1] is None), key=_order, default=None)
    pre = max((version for version in found if version[1] is not None), key=_order, default=None)
    if pre and stable and _order(pre) < _order(stable):
        pre = None
    return _text(stable) if stable else None, _text(pre) if pre else None


def channel(deployed):
    """The channel a version is of: 'pre-release' for a build with a pre-release part (4.13.0-alpha.8), else 'stable',
    also for a tag that names no version."""
    found = parse(deployed or "")
    return "pre-release" if found and found[1] is not None else "stable"


def judge(deployed, stable, prerelease):
    """(status, latest): the newest version of the channel of the deployed one, and whether it is that one. 'latest'
    when nothing above the deployed version is known (the latest then is the highest version known, the deployed one
    when the registry lists none that high), 'newer' when the channel has a version above it, 'unknown' (and none)
    when a version is missing or moves. A stable version is compared with the latest stable, a pre-release with the
    latest pre-release, else with the stable one: a release is above its pre-releases."""
    here, other = parse(deployed or ""), parse(stable or "")
    ahead = parse(prerelease or "") if here and here[1] is not None and prerelease else other
    if not here or not ahead:
        return "unknown", None
    if _order(here) < _order(ahead):
        return "newer", _text(ahead)
    return "latest", _text(max(here, ahead, key=_order))


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
    known = compose.CACHE.get(key, LATEST_TTL, lambda: None)   # looks only: nothing is read here
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
    return min(tags, key=lambda tag: (parse(tag) is None, _order(parse(tag)) if parse(tag) else (), tag))


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
    """What the page reads besides the state: {docker, machine, data, products, pending}. Docker is asked at
    once for what it says of itself, its Compose, what it holds (the slow one, about two seconds) and what runs; each
    answer is kept DOCKER_TTL seconds, and `refresh` reads them again. Docker not answering is `docker.error`, with the
    rest: the machine and the products as configured. What Docker holds is waited for until the read has lasted
    DF_WAIT seconds, as long as it takes for a `refresh`: past that `data` is None and the answer is `pending`, and the
    next read has it. `pending`: versions or data are still being read, ask again."""
    began = time.monotonic()
    if refresh:
        compose.CACHE.drop("local:dashboard:")
    flight = _usage_flight(refresh)
    with ThreadPoolExecutor(max_workers=3) as pool:
        asked = [pool.submit(_asked, _info), pool.submit(_compose_version), pool.submit(compose.containers)]
    (info, why), version, (containers, down) = (future.result() for future in asked)
    try:
        data, slow = flight.result(timeout=None if refresh else max(0, DF_WAIT - (time.monotonic() - began))), False
    except Slow:
        data, slow = None, True
    docker = {"engine": None, "compose": version, "os": None, "cpus": None, "memory": None, **(info or {})}
    if down or why:
        docker["error"] = down or why
    rows, pending = products(project, containers)
    return {"docker": docker, "machine": machine(), "data": data, "products": rows, "pending": pending or slow}


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
    return subprocess.Popen(["docker", "stats", "--format", "{{json .}}"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8", errors="replace",
                            bufsize=1)


def lab_names():
    """The names of the containers of this project, which is the lab's: what Docker runs for something else is not its
    load. Kept a few seconds; None when they cannot be told, and the load is then every container's."""
    def ask():
        try:
            rc, out, _ = compose.run(["docker", "ps", "--filter", f"label=com.docker.compose.project={compose.project_name()}",
                                      "--format", "{{.Names}}"], timeout=10)
        except compose.ComposeError:
            return None
        return set(out.split()) if rc == 0 else None
    return compose.CACHE.get("dashboard:names", NAMES_TTL, ask)


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
                process = False
            if process is None:
                return
            if process:
                for line in process.stdout:
                    if closed.is_set():
                        break
                    self.feed(line)
                process.wait()   # reaped: it ended, or was killed
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
