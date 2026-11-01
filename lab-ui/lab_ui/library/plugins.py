"""Plugins of the Gravitee products: the catalog of download.gravitee.io, what each product's containers load,
adding or removing plugins in the folder the host shares with them, and starting the services that share one."""

import hashlib
import http.client
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..docker import compose
from ..docker.compose import CACHE, COMPOSE
from ..docker.images import web_error
from ..docker.jobs import JOBS, JobFailed, awaited
from . import layers
from ..paths import NotFound, ROOT

DOWNLOADS = "https://download.gravitee.io"
SITE = "download.gravitee.io"   # the download site, as its errors name it
ZIP = re.compile(r"^(?P<name>.+?)-(?P<version>\d+\.\d+[^/]*)\.zip$")
PLUGIN_PATH = re.compile(r"(graviteeio-[a-z]+/|graviteeio-ee/[a-z]+/|)plugins/[a-z-]+/[A-Za-z0-9._-]+/")
CHUNK = 64 * 1024   # a download is read by pieces, so that the job says how far it is, and a cancel ends it
DOWNLOAD_TRIES = 3   # a plugin download cut short or refused by a server error is tried again, then fails
READ_TIMEOUT = 30   # seconds a download waits for its next piece before it is tried again
WAIT = 120   # seconds a service that unpacks plugins is waited for before the next one that shares its folder starts

# Each plugin zip of a folder, a line each: its modification time, its file name, and the id and the license feature it
# declares with "id=" and "feature=" in the plugin.properties of its main jar, <name>-<version>.jar at the root of the
# zip (BusyBox unzip in some images: -p only). Nothing, and no error, when the folder has none
BUNDLED_SCRIPT = r"""t=$(mktemp -d)
for f in "$0"/*.zip; do
  [ -e "$f" ] || continue
  : > "$t/p"
  unzip -p "$f" "$(basename "$f" .zip).jar" > "$t/p.jar" 2>/dev/null &&
    unzip -p "$t/p.jar" plugin.properties > "$t/p" 2>/dev/null
  echo "$(stat -c %Y "$f") $(basename "$f") $(sed -n 's/^id=//p' "$t/p" | tr -d '\r')" \
    "$(sed -n 's/^feature=//p' "$t/p" | tr -d '\r')"
done
rm -rf "$t"
true
"""


def _target(project, product):
    """A product that loads plugins: its name, the folder shared with its services, their bundled plugins folders."""
    if product not in project["products"]:
        raise NotFound(f"No such product: {product}")
    return project["products"][product]


def _sources(product):
    """Where a product's plugins are published: Community and Enterprise, its own and the shared ones."""
    return [f"graviteeio-{product}/plugins/", f"graviteeio-ee/{product}/plugins/", "graviteeio-ee/plugins/", "plugins/"]


# ---------------------------------------------------------------------------------------------------------------------
# The catalog: download.gravitee.io, an S3 bucket listed folder by folder
# ---------------------------------------------------------------------------------------------------------------------

def _fetch(url):
    """One page of a listing. The bucket answers 502 when hit too fast: asked again, slower. What fails says why in
    plain words (web_error)."""
    request = urllib.request.Request(url, headers={"Accept": "text/xml"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode()
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt == 3:
                raise RuntimeError(web_error(error, SITE)) from error
            time.sleep(0.5 * 2 ** attempt)
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            raise RuntimeError(web_error(error, SITE)) from error


def _bucket_list(prefix):
    """A folder of the download bucket: (sub-folders, [(key, last modified, size)])."""
    folders, files, token = [], [], None
    while True:
        url = f"{DOWNLOADS}?list-type=2&max-keys=1000&delimiter=/&prefix={prefix}"
        if token:
            url += "&continuation-token=" + urllib.parse.quote(token)
        xml = _fetch(url)
        folders += [p for p in re.findall(r"<Prefix>([^<]*)</Prefix>", xml) if p and p != prefix]
        for block in re.findall(r"<Contents>(.*?)</Contents>", xml, re.S):
            key = re.search(r"<Key>([^<]*)</Key>", block).group(1)
            modified = re.search(r"<LastModified>([^<]*)</LastModified>", block)
            size = re.search(r"<Size>([^<]*)</Size>", block)
            files.append((key, modified.group(1) if modified else None, int(size.group(1)) if size else 0))
        more = re.search(r"<NextContinuationToken>([^<]*)</NextContinuationToken>", xml)
        if not more:
            return folders, files
        token = more.group(1)


def _catalog(product):
    categories = [folder for source in _sources(product) for folder in _bucket_list(source)[0]]
    with ThreadPoolExecutor(max_workers=4) as pool:
        listings = list(pool.map(lambda category: _bucket_list(category)[0], categories))
    plugins = {}
    for category, folders in zip(categories, listings):
        for folder in folders:
            name = folder.rstrip("/").split("/")[-1]
            if ZIP.match(name + ".zip") or "citest" in name:
                continue
            # Published twice: the first source, the most specific, wins
            plugins.setdefault(name, {"name": name, "category": category.rstrip("/").split("/")[-1], "path": folder,
                                      "edition": "enterprise" if folder.startswith("graviteeio-ee/") else "community"})
    return sorted(plugins.values(), key=lambda p: (p["category"], p["name"]))


def catalog(project, product, refresh=False):
    """The plugins published for a product: [{name, category, edition, path}], by category then name. `refresh`
    reads the download site again: the catalog, and the version lists asked for next."""
    _target(project, product)
    key = f"plugin-catalog:{product}"
    if refresh:
        CACHE.drop(key)
        CACHE.drop("plugin-versions:")
    return CACHE.get(key, 6 * 3600, lambda: _catalog(product))


def _versions(path):
    found = []
    for key, modified, size in _bucket_list(path)[1]:
        file = key.split("/")[-1]
        m = ZIP.match(file)
        if m:
            found.append({"version": m.group("version"), "file": file, "updated": modified, "size": size})
    return found


def versions(path):
    """The versions published for a plugin of the catalog: [{version, file, updated, size}]."""
    if not PLUGIN_PATH.fullmatch(path or ""):
        raise ValueError("Invalid plugin path")
    return CACHE.get(f"plugin-versions:{path}", 1800, lambda: _versions(path))


# ---------------------------------------------------------------------------------------------------------------------
# Installed: bundled in each service's image, or added in the folder shared with the host
# ---------------------------------------------------------------------------------------------------------------------

def _zips(files):
    """{plugin name: {version, file, mtime}} from (file name, modification time) pairs."""
    found = {}
    for name, mtime in files:
        m = ZIP.match(name)
        if m:
            found[m.group("name")] = {"version": m.group("version"), "file": name, "mtime": mtime}
    return found


def _zip_manifest(path):
    """(id, license feature) of a plugin zip on disk, or (None, None)."""
    try:
        with zipfile.ZipFile(path) as z:
            with z.open(Path(path).stem + ".jar") as jar, zipfile.ZipFile(jar) as inner:
                properties = inner.read("plugin.properties").decode()
    except (zipfile.BadZipFile, KeyError, OSError):
        return None, None
    found = dict(re.findall(r"^(id|feature)=(.+)$", properties, re.M))
    return found.get("id", "").strip() or None, found.get("feature", "").strip() or None


def _image_plugins(service, folder):
    """The plugins a service's image comes with in `folder`: ({plugin name: {version, file, mtime, id, feature}},
    None), kept by the image they were read from (compose.image_key): read again once its container runs another one,
    or its tag names another image. (None, (exit code, what Docker said)) when they cannot be read: such an answer is
    not kept."""
    def read():
        rc, out, err = compose.shell(service, ["-c", BUNDLED_SCRIPT, folder], 60)
        if rc != 0:
            raise compose.ComposeError(rc, err)
        found = {}
        for line in out.splitlines():
            mtime, file, plugin_id, feature = (line.split(" ") + ["", "", ""])[:4]
            m = ZIP.match(file)
            if m and mtime.isdigit():
                found[m.group("name")] = {"version": m.group("version"), "file": file, "mtime": int(mtime),
                                          "id": plugin_id or None, "feature": feature.strip() or None}
        return found
    try:
        return CACHE.get(f"plugin-bundled:{compose.image_key(service)}:{folder}", 24 * 3600, read), None
    except compose.ComposeError as failed:
        return None, failed.args


def _bundled(project, target):
    """The plugins each service's image comes with, {plugin name: {service: {version, file, mtime, id, feature}}},
    why some could not be read, None when all were, and the image to pull when one is not here. The services are read
    at once. Why is said in plain words when Docker does not answer ("Docker is not reachable") or an image is not here
    ("<image> is not pulled"): the first service's that could not be read, so that the reason and the image to pull
    name the same one."""
    services = project["services"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        answers = list(pool.map(lambda item: _image_plugins(services[item[0]], item[1]), target["services"].items()))
    bundled, error, pull = {}, None, None
    for service, (found, failed) in zip(target["services"], answers):
        if found is None:
            if error is None:
                (rc, err), image = failed, services[service]["image"]
                lines = [line.removeprefix("docker: ").strip() for line in err.splitlines()
                         if line.strip() and not line.lstrip().startswith(("Run 'docker", "See 'docker"))]   # not hints
                plain = compose.unread(image, err)
                error = plain or "Bundled plugins unknown: " + (lines[-1] if lines else f"exit code {rc}")
                pull = image if plain == f"{image} is not pulled" else None
            continue
        for name, info in found.items():
            bundled.setdefault(name, {})[service] = info
    return bundled, error, pull


def _installed(project, target):
    folder = ROOT / target["folder"]
    bundled, bundled_error, pull = _bundled(project, target)
    ext = _zips((f.name, int(f.stat().st_mtime)) for f in sorted(folder.glob("*.zip")))
    manifests = {name: (info["id"], info["feature"]) for name, by in bundled.items() for info in by.values()}
    for name, info in ext.items():
        manifests[name] = _zip_manifest(folder / info["file"])
        # Gravitee keeps the most recent file when a plugin is found twice: is the added one used?
        info["active"] = info["mtime"] >= max((b["mtime"] for b in bundled.get(name, {}).values()), default=0)
    return {"bundled": {name: {s: i["version"] for s, i in by.items()} for name, by in bundled.items()},
            "bundled_error": bundled_error, "pull": pull, "ext": ext, "services": list(target["services"]),
            "features": {name: feature for name, (_, feature) in manifests.items() if feature},
            "feature_by_id": {plugin_id: feature for plugin_id, feature in manifests.values() if plugin_id and feature}}


def _here(target, only):
    """The target with the services of the product that Compose reads now (`only`, compose.loaded()): a service of an
    add-on that is off is neither pulled nor read, since nothing starts it. All of them when Compose cannot say."""
    if only is None:
        return target
    return {**target, "services": {service: folder for service, folder in target["services"].items() if service in only}}


def installed(project, product, only=None):
    """What a product loads: {bundled: {plugin: {service: version}}, bundled_error, pull, ext: {plugin: {version,
    file, mtime, active}}, services, features: {plugin: license feature}, feature_by_id: {plugin id: license
    feature}}. `bundled_error` says why what its images come with is not known, `pull` the image to pull when that is
    what it takes; such an answer is not kept, but asked again. `only`: the services Compose reads now
    (compose.loaded(), asked when it is not given): what an add-on that is off brings does not count, as nothing starts
    it."""
    target = _here(_target(project, product), compose.loaded() if only is None else only)
    key = f"local:plugins-installed:{product}:" + ",".join(target["services"])
    found = CACHE.get(key, 30, lambda: _installed(project, target))
    if found["bundled_error"]:
        CACHE.drop(key)
    return found


# ---------------------------------------------------------------------------------------------------------------------
# Changing: download everything, then change the shared folder at once, then have the product load its plugins again
# ---------------------------------------------------------------------------------------------------------------------

def _published_sha1(url):
    """The SHA-1 published next to a file of the download site, None when there is none (404). Any other refusal fails
    the try: a server error is tried again (_transient), and a download is never placed unchecked for want of an
    answer."""
    try:
        with urllib.request.urlopen(url + ".sha1", timeout=READ_TIMEOUT) as response:
            return response.read().decode().split()[0].strip().lower()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def _downloads(names):
    """What downloading these plugins waits for, a unit each: known by its name before its version is chosen."""
    return {("Download", name): "download" for name in names}


def _canceled(job):
    if job.canceled:
        raise JobFailed("Canceled")


def _download(job, folder, plugin, version):
    """Downloads a plugin version into the shared folder as a hidden .part file, which no product loads, its size and
    its SHA-1 checked: answers that file, for _place(). A download cut short, or that the site fails to serve (a 5xx,
    the network), is tried again, up to DOWNLOAD_TRIES times: a nightly test must not fail on a transfer. A cancel ends
    it at the next piece read, or before the next try. Nothing is left of a download that failed, and why is said in
    plain words."""
    folder.mkdir(parents=True, exist_ok=True)
    url, unit = f"{DOWNLOADS}/{plugin['path']}{version['file']}", ("Download", plugin["name"])
    job.lines.append(f"Downloading {version['file']} ({max(1, version['size'] // 1024)} KB)")
    part = folder / f".{version['file']}.part"
    for attempt in range(1, DOWNLOAD_TRIES + 1):
        _canceled(job)
        try:
            expected = _fetch_part(job, url, part, unit, version["size"])
            break
        except BaseException as error:
            part.unlink(missing_ok=True)
            said = error if isinstance(error, JobFailed) or not isinstance(error, Exception) else \
                JobFailed(f"{version['file']}: {web_error(error, SITE)}")
            if job.canceled or attempt == DOWNLOAD_TRIES or not _transient(error):
                if said is error:
                    raise
                raise said from error
            job.lines.append(f"  Downloading again: {said}")
            time.sleep(2 ** attempt)
    job.lines.append("  SHA-1 verified" if expected else "  No published checksum")
    return part


def _fetch_part(job, url, part, unit, size):
    """One try of _download(): the file in `part`, of the size the listing gives (when it gives one), checked against
    the SHA-1 the site publishes, which it answers."""
    digest = hashlib.sha1()
    with urllib.request.urlopen(url, timeout=READ_TIMEOUT) as response, open(part, "wb") as f:
        read = 0
        while True:
            _canceled(job)   # before the next piece, which may take READ_TIMEOUT to come
            chunk = response.read(CHUNK)
            if not chunk:
                break
            f.write(chunk)
            digest.update(chunk)
            read += len(chunk)
            job.unit(unit, read / max(read, size))   # the listing may give no size
    name = part.name[1:-len(".part")]
    if size and read != size:   # a connection closed early ends the read as the end of the file does
        raise Mismatch(f"{name}: download cut short, discarded")
    expected = _published_sha1(url)
    if expected and digest.hexdigest() != expected:
        raise Mismatch(f"{name}: checksum mismatch, download discarded")
    return expected


class Mismatch(JobFailed):
    """A download that is not the file published: shorter than its size, or not of its SHA-1. Cut short, most of the
    time."""


def _transient(error):
    """Whether a failed download may work when tried again: cut short, a server error, the network. Not a 4xx."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code >= 500
    return isinstance(error, (Mismatch, urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError,
                              socket.timeout))


def _place(job, folder, plugin, version, part):
    """Puts a plugin _download() fetched in the shared folder, in place of another version of it. The new file is
    there before the old one goes: a plugin found twice is loaded from its most recent file."""
    old = _zips((f.name, int(f.stat().st_mtime)) for f in folder.glob("*.zip")).get(plugin["name"])
    part.replace(folder / version["file"])
    if old and old["file"] != version["file"]:
        (folder / old["file"]).unlink(missing_ok=True)
        job.lines.append(f"Added {version['file']} in place of {old['file']}")
    else:
        job.lines.append(f"Added {version['file']}")


def _change(job, folder, picked, remove=(), then=()):
    """Changes the plugins of the shared folder, all at once and only once everything is downloaded: each plugin of
    `picked` ([(phase or None, plugin, version)]) is downloaded as a .part file first; then the added files of
    `remove` go and the downloads take their places, right before `then` (the stop of the product, which loads them
    as it starts again). A download that fails, or a cancel, leaves the folder as it was: what was downloaded goes."""
    parts = []   # (plugin, version, .part file) downloaded, until it is in place

    def fetch(phase, plugin, version):
        def step(job):
            if phase:
                job.step(phase)
            parts.append((plugin, version, _download(job, folder, plugin, version)))
        return step

    def place(job):
        for name in remove:
            (folder / name).unlink(missing_ok=True)
            job.lines.append(f"Removed {name}")
        while parts:
            _place(job, folder, *parts[0])
            parts.pop(0)

    try:
        job.run_steps([*(fetch(*item) for item in picked), place, *then])
    finally:
        for _, _, part in parts:
            part.unlink(missing_ok=True)


def _running(target):
    """The services of a product that run now: none when Docker does not answer, and the files still change."""
    containers, _ = compose.containers()
    return [s for s in target["services"] if containers.get(s, {}).get("state") == "running"]


def _unload_steps(running):
    """Plugins are loaded on start: the product's running services stop, and whatever starts them next loads the
    plugins. Each service unpacks every plugin again as it starts (.work, in the shared folder): nothing of the last
    start is kept, and nothing is cleared here, which on Linux the files of the containers' user would refuse."""
    if not running:
        return [lambda job: job.lines.append("Not running: loaded on next start")]
    return [[*COMPOSE, "stop", *running]]


def up(job, project, names):
    """Starts these services, `up -d --no-deps`. The services that load a product's plugins unpack them in the same
    folder as they start (.work), and two at once break each other's: a plugin of one does not load. Compose starts them
    one after the other when one depends on the other, as the core's do. Any other one starts first and alone, with what
    it depends on among these, in the order Compose would start them, and the next one once it is ready (healthy, or
    running without a health check). It is started, then waited for: a start that fails (an image it could not pull,
    a container not made) fails the job; a wait that ends otherwise, unhealthy or after WAIT seconds, keeps nothing
    else from starting: its container says it."""
    names = list(names)
    depends = {name: layers.depends(project, names, [name]) for name in names}
    order = {name: (len(depends[name]), name) for name in names}
    alone = []
    for product in project["products"].values():
        together = []   # what one `up` may start: each depends on the ones before it
        for name in sorted((name for name in names if name in product["services"]), key=order.get, reverse=True):
            (together if all(name in depends[other] for other in together) else alone).append(name)
    started = set()
    for name in sorted(alone, key=order.get):
        group = sorted(({name} | depends[name]) - started)
        started.update(group)
        job.exec([*COMPOSE, "up", "-d", "--no-deps", *group])
        try:   # the containers are made: this `up` only waits
            job.exec([*COMPOSE, "up", "-d", "--no-deps", "--wait", f"--wait-timeout={WAIT}", *group], quiet=True)
        except JobFailed:
            if job.canceled:
                raise
    rest = [name for name in names if name not in started]
    if rest:
        job.exec([*COMPOSE, "up", "-d", "--no-deps", *rest])


def _pick(project, product, item):
    """The catalog entry and the published version an install asks for with {path, version}."""
    plugin = next((p for p in catalog(project, product) if p["path"] == item.get("path")), None)
    published = versions(plugin["path"]) if plugin else []
    version = next((v for v in published if v["version"] == item.get("version")), None)
    if not version:
        raise ValueError(f"Unknown plugin or version: {item.get('path')} {item.get('version')}")
    return plugin, version


def apply(project, product, install, remove):
    """Adds or updates plugins ([{path, version}]) and removes added ones (file names), then restarts the product
    once. Each download, then the stop and the start, are the phases its progress counts: a download by the bytes
    read, the stop and the start by the steps of the product's containers that ran. The folder changes only once
    every download is there, right before the stop (_change): a download that fails changes nothing."""
    target = _target(project, product)
    folder = ROOT / target["folder"]
    if not set(remove) <= {f.name for f in folder.glob("*.zip")}:
        raise ValueError("Unknown plugin file")
    picked = [_pick(project, product, item) for item in install]
    if not picked and not remove:
        raise ValueError("Nothing to apply")
    running = _running(target)
    downloads = [(f"Download {version['file']}", plugin, version) for plugin, version in picked]

    def unload(job):
        if running:
            job.step("Stop")
        job.run_steps(_unload_steps(running))

    def change(job):
        _change(job, folder, downloads, remove, then=[unload])

    def start(job):
        job.step("Start")
        # Only what was running starts again: a service it depends on that was stopped stays stopped
        up(job, project, running)

    steps = [change] + ([start] if running else [])
    phases = [phase for phase, _, _ in downloads] + (["Stop", "Start"] if running else [])
    services = project["services"]
    waits = [_downloads([plugin["name"]]) for _, plugin, _ in downloads]
    waits += [awaited(services, stop=running), awaited(services, start=running)] if running else []
    # A restart shows as starting: the services stop on the way, which is not an issue
    return JOBS.start("Apply plugins", steps, key=f"plugins:{product}", verb="apply",
                      changes={"start": [["service", name] for name in running]}, phases=phases, waits=waits)


def _latest(name, published):
    releases = [v for v in published if "-" not in v["version"]]
    if not releases:
        raise JobFailed(f"Plugin {name}: no release to install")
    return max(releases, key=lambda v: [int(x) for x in re.findall(r"\d+", v["version"])])


def _missing(present, names):
    return [name for name in names if name not in present["bundled"] and name not in present["ext"]]


def install_steps(project, product, names):
    """The steps of a job that needs these plugins, the services they may stop, and what they wait for (the units of
    jobs.awaited): none when the plugins are all there. The latest release of the ones that are missing is
    downloaded, put in the folder once all of them are there (_change), and the product stops so that the `up` that
    follows loads them (up()). When what its images come with cannot be read yet, the step pulls the images that are not
    here first and decides then. What it waits for is known before it begins all the same: any plugin not added may be
    missing then, and what the images turn out to bring counts as done, as does the stop when nothing is missing."""
    here = compose.loaded()
    target = _here(_target(project, product), here)
    present = installed(project, product, here)
    if not present["bundled_error"] and not _missing(present, names):
        return [], [], {}
    services, stopped = project["services"], _running(target)
    unknown = present["bundled_error"]
    held = compose.image_ids() if unknown else {}   # None when Docker does not answer: every image is pulled
    pulls = [name for name in target["services"]
             if held is None or compose.reference(services[name]["image"]) not in held] if unknown else []
    maybe = [name for name in names if name not in present["ext"]] if unknown else _missing(present, names)
    waits = {**_downloads(maybe), **awaited(services, stop=stopped)}
    waits.update(awaited(services, images=[services[name]["image"] for name in pulls]))

    def install(job):
        found = present
        if found["bundled_error"]:   # an answer without them was not kept: read again once pulled
            if pulls:
                job.exec([*COMPOSE, "pull", *pulls])
            found = installed(project, product, here)
            if found["bundled_error"]:
                raise JobFailed(found["bundled_error"])
        missing = _missing(found, names)
        for name in sorted(set(maybe) - set(missing)):   # the images bring it
            job.unit(("Download", name), 1)
        if not missing:   # nothing to load: nothing stops
            for name in stopped:
                job.unit(("Container", services[name]["container"]), 1)
            return
        try:
            published = {p["name"]: p for p in catalog(project, product)}
            for name in missing:
                if name not in published:
                    raise JobFailed(f"Plugin {name} is not on {SITE}")
            picked = [(None, published[name], _latest(name, versions(published[name]["path"]))) for name in missing]
        except RuntimeError as error:   # the site said why in plain words: the reason of the job
            raise JobFailed(str(error)) from error

        def unload(job):
            # What runs now stops, also a service that started since the job began
            running = _running(target)
            job.expect(awaited(services, stop=running))
            job.run_steps(_unload_steps(running))
        _change(job, ROOT / target["folder"], picked, then=[unload])

    return [install], stopped, waits
