"""What the Dashboard reads that the state does not hold: the Gravitee products here against the latest version of
their channel in the registry, and Docker. What runs and what is on is the state's (`/api/state`), which the page draws live. What Docker says of
itself and of its Compose is what the state reads (compose.docker_info, compose_version), what it holds is kept for a
minute and the registry's answers for an hour; the load of the containers comes from a `docker stats --no-stream` that the
pages following it share."""

import http.client
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..docker import compose, images
from ..library import project as projects
from ..library.versions import judge, channel, latest, order, parse

USAGE = "local:dashboard:df"   # what Docker holds, in the cache
DOCKER_TTL = 60       # seconds what Docker holds is kept: a quick back and forth does not read it again
DF_WAIT = 0.4         # seconds a read lasts at the most for what Docker holds, the slow one, before the page is drawn without it
LATEST_TTL = 3600     # seconds the versions a registry lists are kept
RETRY = 60            # seconds before a registry that did not answer is asked again
LOAD_TTL = 1          # seconds a reading of the load is kept
NAMES_TTL = 10        # seconds the names of the lab's containers are kept
TYPES = ("images", "containers", "volumes", "cache")   # what `docker system df` counts, in the order it lists them
# Docker writes sizes in powers of 1000 (`system df`: 58.89GB) or of 1024 (`stats`: 731.8MiB)
UNITS = {"B": 1,
         **{unit: 1000 ** power for power, unit in enumerate(["kB", "MB", "GB", "TB", "PB"], 1)},
         **{unit: 1024 ** power for power, unit in enumerate(["KiB", "MiB", "GiB", "TiB", "PiB"], 1)}}
SIZE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*([A-Za-z]+)")


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
    found = {"images": sizes("Images"), "containers": sizes("Containers"), "volumes": sizes("Local Volumes"),
             "cache": sizes("Build Cache")}
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
        return compose.CACHE.get(USAGE, DOCKER_TTL, ask)
    except compose.ComposeError:
        return None


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
    deployed, deployed_state, channel, latest, status}, in the order of the groups. `latest` is the
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
                     "channel": channel(tag), "latest": newest, "status": status})
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
    reading = threading.Thread(target=_usage, daemon=True)   # a read under way is waited for, not made again (compose.Cache)
    reading.start()
    with ThreadPoolExecutor(max_workers=3) as pool:
        asked = [pool.submit(_asked, compose.docker_info), pool.submit(_asked, compose.compose_version),
                 pool.submit(compose.containers)]
    (info, why), (version, _), (containers, down) = (future.result() for future in asked)
    reading.join(None if refresh else max(0, DF_WAIT - (time.monotonic() - began)))
    docker = {"engine": None, "compose": version, "os": None, "cpus": None, "memory": None,
              **{key: value for key, value in (info or {}).items() if key != "root"}}
    if down or why:
        docker["error"] = down or why
    rows, pending = products(project, containers)
    return {"docker": docker, "data": compose.CACHE.kept(USAGE, DOCKER_TTL), "products": rows,
            "pending": pending or reading.is_alive()}


# ---------------------------------------------------------------------------------------------------------------------
# The load of the containers
# ---------------------------------------------------------------------------------------------------------------------

def _percent(text):
    """A percentage as `docker stats` writes it ("0.47%"); 0 for "--" and the like."""
    try:
        return float(str(text).rstrip("%"))
    except ValueError:
        return 0.0


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


def load(cores):
    """The load of the lab's containers as the stream sends it, {cpu_percent, memory_used, containers, top, at}: the CPU of the
    containers as a share of the cores Docker has, 0 to 100, and `top`, each container's own, [{name, cpu, memory}] from the
    heaviest in memory. One `docker stats --no-stream`, about two seconds, kept LOAD_TTL seconds so that the pages that follow
    it share it. Nothing runs, or Docker does not answer: no load. The containers that count are the lab's (`lab_names`), every
    one when they cannot be told."""
    def ask():
        _, out, _ = compose.run(["docker", "stats", "--no-stream", "--format", "{{json .}}"], timeout=10)
        names, found = lab_names(), []
        for row in map(_object, out.splitlines()):
            key = row.get("ID") or row.get("Name")
            if key and (names is None or row.get("Name") in names):
                found.append((_percent(row.get("CPUPerc")), size(str(row.get("MemUsage", "")).split("/")[0]), row.get("Name") or key))
        return found
    each = compose.CACHE.get("dashboard:load", LOAD_TTL, ask)
    top = [{"name": name, "cpu": round(min(100.0, share / cores), 1), "memory": used}
           for share, used, name in sorted(each, key=lambda one: -one[1])]
    return {"cpu_percent": round(min(100.0, sum(one[0] for one in each) / cores), 1), "memory_used": sum(one[1] for one in each),
            "containers": len(each), "top": top, "at": int(time.time())}
